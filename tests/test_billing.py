"""T21: ЮKassa in test mode behind a fake: checkout, webhook, cancel, renewal, freeze."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.billing.gateway import FakePayments, UnconfiguredPayments, YooKassa, build_payments, kopecks, rubles
from tests.accounts import DAY, browser, set_clock, sign_in

HOOK = "/api/billing/webhook"


@pytest.fixture
def shop(application: FastAPI, client: TestClient) -> dict[str, Any]:
    """A signed-in host, a fake payment provider and a clock the test moves."""
    now = {"t": 1_800_000_000.0}
    set_clock(application, now)
    payments = application.state.billing.payments = FakePayments()
    application.state.config.billing["webhook_ips"] = ["testclient"]
    application.state.realtime.frozen = AsyncMock()
    user = sign_in(application, client, "host@example.org")
    return {"now": now, "payments": payments, "user": user}


def buy(client: TestClient, payments: FakePayments, plan: str = "plus_month") -> str:
    url = client.post("/api/billing/checkout", json={"plan": plan}).json()["confirmationUrl"]
    payment_id = url.rsplit("/", 1)[1]
    payments.pay(payment_id)
    assert client.post(HOOK, json={"event": "payment.succeeded", "object": {"id": payment_id}}).status_code == 200
    return payment_id


def sweep(application: FastAPI) -> None:
    asyncio.run(application.state.billing.sweep())


def test_checkout_then_webhook_activates_plus_once(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo, payments, now = application.state.repo, shop["payments"], shop["now"]
    assert browser(application).post("/api/billing/checkout", json={"plan": "plus_month"}).status_code == 401
    assert client.post("/api/billing/checkout", json={"plan": "plus_decade"}).status_code == 400
    url = client.post("/api/billing/checkout", json={"plan": "plus_month"}).json()["confirmationUrl"]
    payment_id = url.rsplit("/", 1)[1]
    order = payments.payments[payment_id]
    assert (order["amount_kop"], order["currency"]) == (29900, "RUB")
    assert order["metadata"] == {"userId": shop["user"]["id"], "plan": "plus_month"}
    assert repo.get_payment(payment_id)["status"] == "pending"

    # A notification is not proof: while the provider says pending, nothing changes.
    body = {"event": "payment.succeeded", "object": {"id": payment_id, "status": "succeeded"}}
    assert client.post(HOOK, json=body).status_code == 200
    assert client.get("/api/me").json()["plan"] == "free"
    # Unknown payments and malformed bodies are harmless.
    assert client.post(HOOK, json={"object": {"id": "nope"}}).status_code == 200
    assert client.post(HOOK, json={"object": 5}).status_code == 400

    payments.pay(payment_id)
    for _ in range(3):  # ЮKassa repeats notifications; only the first one counts
        assert client.post(HOOK, json=body).status_code == 200
    me = client.get("/api/me").json()
    month_end = int(now["t"] * 1000) + 30 * DAY * 1000
    assert me["plan"] == "plus" and me["ads"] is False and me["quota"]["limit"] == 50
    assert me["subscription"] == {"status": "active", "periodEnd": month_end}
    assert repo.get_payment(payment_id)["status"] == "succeeded"
    assert repo.get_subscription(shop["user"]["id"])["yk_payment_method_id"] == f"method-{payment_id}"

    # Buying a year on top extends from the current period end.
    buy(client, payments, "plus_year")
    assert client.get("/api/me").json()["subscription"]["periodEnd"] == month_end + 365 * DAY * 1000
    assert repo.db.execute("SELECT count(*) FROM payment WHERE status='succeeded'").fetchone()[0] == 2


def test_webhook_only_from_the_providers_addresses(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    billing = application.state.billing
    application.state.config.billing["webhook_ips"] = ["185.71.76.0/27", "77.75.156.11", "2a02:5180::/32"]
    assert client.post(HOOK, json={"object": {"id": "x"}}).status_code == 404
    assert billing.ip_allowed("185.71.76.5") and billing.ip_allowed("77.75.156.11")
    assert billing.ip_allowed("2a02:5180:0:1::5")
    assert not billing.ip_allowed("185.71.76.40") and not billing.ip_allowed(None)


def test_declined_payment_changes_nothing(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    url = client.post("/api/billing/checkout", json={"plan": "plus_month"}).json()["confirmationUrl"]
    payment_id = url.rsplit("/", 1)[1]
    shop["payments"].decline(payment_id)
    client.post(HOOK, json={"object": {"id": payment_id}})
    assert application.state.repo.get_payment(payment_id)["status"] == "canceled"
    assert client.get("/api/me").json()["plan"] == "free"


def test_lapsed_subscription_freezes_worlds_and_paying_unfreezes(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo, payments, now = application.state.repo, shop["payments"], shop["now"]
    sent: list[dict[str, Any]] = []
    application.state.sim.send = sent.append
    buy(client, payments)
    world = client.post("/api/canvases", json={"title": "Farm"}).json()["id"]
    client.put(f"/api/canvases/{world}/access", json={"linkRole": "editor"})
    guest = browser(application)

    def state() -> tuple[str, bool, str, str]:
        canvas = client.get(f"/api/canvases/{world}").json()
        return (repo.get_subscription(shop["user"]["id"])["status"], canvas["frozen"], canvas["role"],
                guest.get(f"/api/canvases/{world}").json()["role"])  # fmt: skip

    sweep(application)
    assert state() == ("active", False, "editor", "editor")
    # The period ends with no renewal: past due, but three days of grace.
    now["t"] += 31 * DAY
    sweep(application)
    assert state() == ("past_due", False, "editor", "editor")
    assert client.get("/api/me").json()["plan"] == "free"
    now["t"] += 2 * DAY
    sweep(application)
    assert state()[1] is False
    now["t"] += 1.5 * DAY
    sweep(application)
    assert state() == ("past_due", True, "viewer", "viewer")
    assert sent[-1] == {"op": "freeze", "canvas": world, "frozen": True}
    application.state.realtime.frozen.assert_awaited_with(world, True)
    assert repo.get_canvas(world)["stroke_count"] == 0  # frozen, never deleted
    assert client.post("/api/canvases", json={"title": "more"}).status_code == 402

    # Paying brings it back at once, without waiting for a sweep.
    buy(client, payments)
    assert state() == ("active", False, "editor", "editor")
    assert sent[-1] == {"op": "freeze", "canvas": world, "frozen": False}

    # Cancelling keeps Plus until the period ends, then the world freezes.
    assert client.post("/api/billing/cancel").json()["subscription"]["status"] == "canceled"
    sweep(application)
    assert state()[:2] == ("canceled", False) and client.get("/api/me").json()["plan"] == "plus"
    now["t"] += 31 * DAY
    sweep(application)
    assert state() == ("canceled", True, "viewer", "viewer")


def test_recurring_renewal_charges_the_saved_method_or_goes_past_due(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo, payments, now = application.state.repo, shop["payments"], shop["now"]
    application.state.config.flags.billing.recurring = True
    buy(client, payments)
    first_end = repo.get_subscription(shop["user"]["id"])["current_period_end"]
    now["t"] += 30 * DAY + 60
    sweep(application)
    sweep(application)  # the second pass in the same period charges nothing more
    renewed = repo.get_subscription(shop["user"]["id"])
    assert renewed["status"] == "active" and renewed["current_period_end"] > first_end
    assert repo.db.execute("SELECT count(*) FROM payment WHERE status='succeeded'").fetchone()[0] == 2
    payments.renewals_succeed = False
    now["t"] += 31 * DAY
    sweep(application)
    assert repo.get_subscription(shop["user"]["id"])["status"] == "past_due"


def test_live_keys_are_refused_and_amounts_are_exact() -> None:
    http = httpx.AsyncClient()
    assert isinstance(build_payments(http, "", ""), UnconfiguredPayments)
    assert isinstance(build_payments(http, "123", "live_abc"), UnconfiguredPayments)
    assert isinstance(build_payments(http, "123", "test_abc"), YooKassa)
    assert (rubles(29900), rubles(249000), rubles(5)) == ("299.00", "2490.00", "0.05")
    assert (kopecks("299.00"), kopecks("2490.5"), kopecks("7")) == (29900, 249050, 700)


def test_yookassa_requests_follow_the_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "p1", "status": "succeeded", "amount": {"value": "299.00", "currency": "RUB"},
                                             "metadata": {"plan": "plus_month"}, "payment_method": {"id": "m1", "saved": True}})  # fmt: skip
        return httpx.Response(200, json={"id": "p1", "status": "pending", "amount": {"value": "299.00", "currency": "RUB"},
                                         "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.example/p1"}})  # fmt: skip

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            gateway = YooKassa(http, "shop", "test_key")
            created = await gateway.create({"amount_kop": 29900, "currency": "RUB", "description": "Plus", "metadata": {"plan": "plus_month"},
                                            "idempotence_key": "k1", "return_url": "http://localhost/billing/return"})  # fmt: skip
            assert created["confirmation_url"] == "https://yoomoney.example/p1"
            fetched = await gateway.get("p1")
            assert (fetched["status"], fetched["amount_kop"], fetched["payment_method_id"]) == ("succeeded", 29900, "m1")

    asyncio.run(scenario())
    import json

    post = seen[0]
    body = json.loads(post.content)
    assert post.url == "https://api.yookassa.ru/v3/payments" and post.headers["Idempotence-Key"] == "k1"
    assert post.headers["Authorization"].startswith("Basic ")
    assert body["amount"] == {"value": "299.00", "currency": "RUB"} and body["save_payment_method"] is True
    assert body["confirmation"] == {"type": "redirect", "return_url": "http://localhost/billing/return"}
    assert seen[1].url == "https://api.yookassa.ru/v3/payments/p1"
