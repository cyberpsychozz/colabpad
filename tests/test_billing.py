"""T21: Robokassa in test mode: checkout link, ResultURL, cancel, renewal, freeze, migration."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.billing.gateway import (
    PAY_URL, RECURRING_URL, Robokassa, UnconfiguredPayments, build_payments, kopecks, rubles,
)  # fmt: skip
from app.db import connect
from app.i18n import translate
from tests.accounts import DAY, browser, set_clock, sign_in

RESULT = "/api/billing/result"
LOGIN, PASSWORD1, PASSWORD2 = "collabpad-test", "test-one", "test-two"
RECEIPT_MONTH = {"items": [{
    "name": "CollabPad «Плюс», 1 месяц", "quantity": 1, "sum": 299.0,
    "payment_method": "full_payment", "payment_object": "service", "tax": "none",
}]}  # fmt: skip


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def notification(inv_id: Any, out_sum: str = "299.00", password: str = PASSWORD2, **shp: str) -> dict[str, str]:
    """What Robokassa posts to ResultURL for a paid invoice (its hex comes in upper case)."""
    signed = "".join(f":{key}={value}" for key, value in sorted(shp.items()))
    return {
        "OutSum": out_sum, "InvId": str(inv_id), "Fee": "0.00", "EMail": "host@example.org",
        "PaymentMethod": "BankCard", "IncCurrLabel": "BankCardPSR", **shp,
        "SignatureValue": md5(f"{out_sum}:{inv_id}:{password}{signed}").upper(),
    }  # fmt: skip


class RecurringEndpoint:
    """Robokassa's /Merchant/Recurring: answers OK<InvoiceID> while `accepts` is true."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.accepts = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == RECURRING_URL and request.method == "POST"
        fields = dict(parse_qsl(request.content.decode()))
        self.requests.append(fields)
        return httpx.Response(200, text=f"OK{fields['InvoiceID']}" if self.accepts else "ERROR: recurring is off")


@pytest.fixture
def shop(application: FastAPI, client: TestClient) -> dict[str, Any]:
    """A signed-in host, Robokassa with test passwords, and a clock the test moves."""
    now = {"t": 1_800_000_000.0}
    set_clock(application, now)
    recurring = RecurringEndpoint()
    http = httpx.AsyncClient(transport=httpx.MockTransport(recurring))
    application.state.billing.payments = Robokassa(http, LOGIN, PASSWORD1, PASSWORD2)
    application.state.config.billing["result_ips"] = ["testclient"]
    application.state.realtime.frozen = AsyncMock()
    user = sign_in(application, client, "host@example.org")
    return {"now": now, "recurring": recurring, "user": user}


def checkout(client: TestClient, plan: str = "plus_month") -> dict[str, str]:
    """The payment link's parameters, decoded once (as Robokassa reads them)."""
    response = client.post("/api/billing/checkout", json={"plan": plan})
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["paymentUrl"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == PAY_URL
    return dict(parse_qsl(url.query))


def buy(client: TestClient, plan: str = "plus_month") -> int:
    link = checkout(client, plan)
    response = client.post(RESULT, data=notification(link["InvId"], link["OutSum"]))
    assert (response.status_code, response.text) == (200, f"OK{link['InvId']}")
    return int(link["InvId"])


def sweep(application: FastAPI) -> None:
    asyncio.run(application.state.billing.sweep())


def test_checkout_signs_a_test_mode_payment_link(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo = application.state.repo
    assert browser(application).post("/api/billing/checkout", json={"plan": "plus_month"}).status_code == 401
    assert client.post("/api/billing/checkout", json={"plan": "plus_decade"}).status_code == 400
    link = checkout(client)
    inv_id = int(link["InvId"])
    assert inv_id >= shop["now"]["t"]  # unix seconds are the floor: numbers survive a fresh DB
    assert (link["MerchantLogin"], link["OutSum"], link["IsTest"], link["Encoding"]) == (LOGIN, "299.00", "1", "utf-8")
    assert link["Culture"] in ("ru", "en")
    assert link["Description"] == translate(link["Culture"], "billing.description.plus_month")
    assert "Recurring" not in link  # billing.recurring is off
    # The receipt is URL-encoded JSON inside the signature, and encoded again in the link.
    assert json.loads(unquote(link["Receipt"])) == RECEIPT_MONTH
    assert link["SignatureValue"] == md5(f"{LOGIN}:299.00:{inv_id}:{link['Receipt']}:{PASSWORD1}")
    row = repo.get_payment(inv_id)
    assert (row["user_id"], row["plan"], row["amount_kop"], row["status"], row["recurring"]) == (
        shop["user"]["id"], "plus_month", 29900, "pending", 0)  # fmt: skip

    year = checkout(client, "plus_year")
    assert int(year["InvId"]) == inv_id + 1 and year["OutSum"] == "2490.00"

    # Without the fiscal receipt the signature has no Receipt slot.
    application.state.config.billing["receipt"]["enabled"] = False
    plain = checkout(client)
    assert "Receipt" not in plain
    assert plain["SignatureValue"] == md5(f"{LOGIN}:299.00:{plain['InvId']}:{PASSWORD1}")


def test_result_activates_plus_once(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo, now = application.state.repo, shop["now"]
    inv_id = int(checkout(client)["InvId"])

    # Only a notification signed with password #2 counts.
    for forged in (notification(inv_id, password=PASSWORD1), {**notification(inv_id), "SignatureValue": "0" * 32},
                   {"OutSum": "299.00", "InvId": str(inv_id)}, {}):  # fmt: skip
        assert client.post(RESULT, data=forged).status_code == 400
    assert client.get("/api/me").json()["plan"] == "free"
    assert repo.get_payment(inv_id)["status"] == "pending"
    # A signed notice for an invoice we never issued is answered (so it stops) and ignored.
    assert client.post(RESULT, data=notification(999)).text == "OK999"

    for _ in range(3):  # Robokassa repeats notifications; only the first one counts
        response = client.post(RESULT, data=notification(inv_id))
        assert (response.status_code, response.text) == (200, f"OK{inv_id}")
        assert response.headers["content-type"].startswith("text/plain")
    me = client.get("/api/me").json()
    month_end = int(now["t"] * 1000) + 30 * DAY * 1000
    assert me["plan"] == "plus" and me["ads"] is False and me["quota"]["limit"] == 50
    assert me["subscription"] == {"status": "active", "periodEnd": month_end}
    assert repo.get_payment(inv_id)["status"] == "succeeded"
    assert repo.get_subscription(shop["user"]["id"])["parent_inv_id"] is None  # not a recurring parent

    # A year on top extends from the current period end; ResultURL may also come as GET with
    # production-style six decimals.
    year = checkout(client, "plus_year")
    response = client.get(RESULT, params=notification(year["InvId"], "2490.000000"))
    assert response.text == f"OK{year['InvId']}"
    assert client.get("/api/me").json()["subscription"]["periodEnd"] == month_end + 365 * DAY * 1000
    assert repo.db.execute("SELECT count(*) FROM payment WHERE status='succeeded'").fetchone()[0] == 2


def test_result_only_from_robokassas_addresses(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    billing = application.state.billing
    billing.config.billing["result_ips"] = ["185.59.216.65", "185.59.217.65", "10.0.0.0/8"]
    assert client.post(RESULT, data=notification(1)).status_code == 404
    assert billing.ip_allowed("185.59.216.65") and billing.ip_allowed("185.59.217.65") and billing.ip_allowed("10.1.2.3")
    assert not billing.ip_allowed("185.59.216.66") and not billing.ip_allowed(None)


def test_a_wrong_amount_cancels_the_payment(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    inv_id = int(checkout(client)["InvId"])
    assert client.post(RESULT, data=notification(inv_id, "1.00")).text == f"OK{inv_id}"
    assert application.state.repo.get_payment(inv_id)["status"] == "canceled"
    assert client.get("/api/me").json()["plan"] == "free"
    # And it stays canceled when the right amount is claimed later.
    client.post(RESULT, data=notification(inv_id))
    assert client.get("/api/me").json()["plan"] == "free"


def test_lapsed_subscription_freezes_worlds_and_paying_unfreezes(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo, now = application.state.repo, shop["now"]
    sent: list[dict[str, Any]] = []
    application.state.sim.send = sent.append
    buy(client)
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
    assert shop["recurring"].requests == []  # billing.recurring is off: nothing is charged
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
    buy(client)
    assert state() == ("active", False, "editor", "editor")
    assert sent[-1] == {"op": "freeze", "canvas": world, "frozen": False}

    # Cancelling keeps Plus until the period ends, then the world freezes.
    assert client.post("/api/billing/cancel").json()["subscription"]["status"] == "canceled"
    sweep(application)
    assert state()[:2] == ("canceled", False) and client.get("/api/me").json()["plan"] == "plus"
    now["t"] += 31 * DAY
    sweep(application)
    assert state() == ("canceled", True, "viewer", "viewer")


def test_recurring_renewal_repeats_the_first_invoice(application: FastAPI, client: TestClient, shop: dict[str, Any]) -> None:
    repo, now, endpoint = application.state.repo, shop["now"], shop["recurring"]
    user_id = shop["user"]["id"]
    application.state.config.flags.billing.recurring = True
    link = checkout(client)
    assert link["Recurring"] == "true"
    assert link["SignatureValue"] == md5(f"{LOGIN}:299.00:{link['InvId']}:{link['Receipt']}:{PASSWORD1}")
    parent = int(link["InvId"])
    client.post(RESULT, data=notification(parent))
    first_end = repo.get_subscription(user_id)["current_period_end"]
    assert repo.get_subscription(user_id)["parent_inv_id"] == parent

    now["t"] += 30 * DAY + 60
    sweep(application)
    sweep(application)  # the second pass in the same period charges nothing more
    assert len(endpoint.requests) == 1
    charge = endpoint.requests[0]
    renewal = int(charge["InvoiceID"])
    assert renewal > parent and int(charge["PreviousInvoiceID"]) == parent
    assert (charge["MerchantLogin"], charge["OutSum"], charge["IsTest"]) == (LOGIN, "299.00", "1")
    assert json.loads(unquote(charge["Receipt"])) == RECEIPT_MONTH
    assert charge["SignatureValue"] == md5(f"{LOGIN}:299.00:{renewal}:{charge['Receipt']}:{PASSWORD1}")
    # Until Robokassa confirms the charge the subscription waits in its grace period.
    assert repo.get_subscription(user_id)["status"] == "past_due"
    assert repo.get_payment(renewal)["status"] == "pending"

    assert client.post(RESULT, data=notification(renewal)).text == f"OK{renewal}"
    renewed = repo.get_subscription(user_id)
    assert renewed["status"] == "active" and renewed["current_period_end"] > first_end
    assert renewed["parent_inv_id"] == parent  # a renewal never replaces the parent invoice
    assert repo.db.execute("SELECT count(*) FROM payment WHERE status='succeeded'").fetchone()[0] == 2

    # A refused charge leaves the subscription past due and its invoice canceled.
    endpoint.accepts = False
    now["t"] += 31 * DAY
    sweep(application)
    assert repo.get_subscription(user_id)["status"] == "past_due"
    assert repo.get_payment(int(endpoint.requests[-1]["InvoiceID"]))["status"] == "canceled"
    assert len(endpoint.requests) == 2


def test_unconfigured_billing_fails_cleanly(application: FastAPI, client: TestClient) -> None:
    sign_in(application, client, "host@example.org")
    assert isinstance(application.state.billing.payments, UnconfiguredPayments)
    response = client.post("/api/billing/checkout", json={"plan": "plus_month"})
    assert (response.status_code, response.json()["error"]["code"]) == (402, "PAYMENT_FAILED")
    assert application.state.repo.db.execute("SELECT count(*) FROM payment").fetchone()[0] == 0


def test_gateway_settings_signatures_and_amounts() -> None:
    http = httpx.AsyncClient()
    assert isinstance(build_payments(http, "", "", ""), UnconfiguredPayments)
    assert isinstance(build_payments(http, "shop", "p1", ""), UnconfiguredPayments)
    assert isinstance(build_payments(http, "shop", "p1", "p2", "sha256"), Robokassa)
    with pytest.raises(ValueError):
        Robokassa(http, "shop", "p1", "p2", "crc32")
    sha = Robokassa(http, "shop", "p1", "p2", "sha256")
    signed = {"OutSum": "5.00", "InvId": "7", "SignatureValue": hashlib.sha256(b"5.00:7:p2:Shp_a=1:Shp_b=x").hexdigest(),
              "Shp_b": "x", "Shp_a": "1"}  # fmt: skip
    assert sha.verify(signed) == {"inv_id": 7, "amount_kop": 500, "shp": {"Shp_a": "1", "Shp_b": "x"}}
    assert sha.verify({**signed, "Shp_b": "y"}) is None  # Shp_ values are signed too
    zero = {"OutSum": "5.00", "InvId": "0", "SignatureValue": hashlib.sha256(b"5.00:0:p2").hexdigest()}
    assert sha.verify(zero) is None
    assert (rubles(29900), rubles(249000), rubles(5)) == ("299.00", "2490.00", "0.05")
    assert (kopecks("299.00"), kopecks("2490.5"), kopecks("7"), kopecks("299.000000")) == (29900, 249050, 700, 29900)
    for bad in ("", "abc", "-1.00", "1.x", "1e3"):
        with pytest.raises(ValueError):
            kopecks(bad)


V2_BILLING = """
DROP TABLE payment;
CREATE TABLE payment (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_user(id),
  yk_payment_id TEXT UNIQUE NOT NULL, amount_kop INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT CHECK (status IN ('pending','succeeded','canceled')), created_at INTEGER NOT NULL);
ALTER TABLE subscription DROP COLUMN parent_inv_id;
ALTER TABLE subscription ADD COLUMN yk_payment_method_id TEXT;
INSERT INTO app_user(id,email,locale,created_at) VALUES('u','a@b.c','ru',0);
INSERT INTO payment VALUES('p','u','2f00-yk','29900','RUB','succeeded',1);
INSERT INTO subscription(id,user_id,plan,status,current_period_end,yk_payment_method_id)
  VALUES('s','u','plus','active',99,'2f00-method');
INSERT INTO canvas(id,kind,link_role,bounds_w,bounds_h,created_at) VALUES('c','world','editor',1,1,0);
INSERT INTO stroke(canvas_id,seq,client_id,color,width,points,chunk_x,chunk_y,created_at)
  VALUES('c',1,'x','#000000',1,'[]',0,0,0);
PRAGMA user_version=2;
"""


def test_a_version_2_database_moves_to_robokassa_keeping_everything(tmp_path: Path) -> None:
    path = str(tmp_path / "v2.db")
    connect(path).close()
    with sqlite3.connect(path) as old:
        old.executescript(V2_BILLING)
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    # YooKassa's payments are kept (payment rows are never deleted), strokes untouched.
    assert dict(db.execute("SELECT yk_payment_id, status FROM payment_yookassa").fetchone()) == {
        "yk_payment_id": "2f00-yk", "status": "succeeded"}  # fmt: skip
    assert db.execute("SELECT count(*) FROM stroke").fetchone()[0] == 1
    # Plus stays active; a YooKassa card can't be charged by Robokassa, so no parent invoice.
    subscription = dict(db.execute("SELECT * FROM subscription").fetchone())
    assert (subscription["status"], subscription["current_period_end"], subscription["parent_inv_id"]) == ("active", 99, None)
    assert "yk_payment_method_id" not in subscription
    db.execute("INSERT INTO payment(id,user_id,inv_id,plan,amount_kop,currency,status,created_at)"
               " VALUES('n','u',1,'plus_month',29900,'RUB','pending',0)")  # fmt: skip
    db.close()
    connect(path).close()  # opening a version 3 database again changes nothing
