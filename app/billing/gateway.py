"""The payment provider behind an interface: YooKassa over httpx, and a fake for tests."""

import logging
from typing import Any, Protocol
from uuid import uuid4

import httpx

from app.errors import AppError

logger = logging.getLogger(__name__)
API = "https://api.yookassa.ru/v3"


def rubles(amount_kop: int) -> str:
    return f"{amount_kop // 100}.{amount_kop % 100:02d}"


def kopecks(value: str) -> int:
    whole, _, cents = value.partition(".")
    return int(whole) * 100 + int((cents + "00")[:2])


class Payments(Protocol):
    async def create(self, order: dict[str, Any]) -> dict[str, Any]:
        """order: amount_kop, currency, description, metadata, idempotence_key, and either
        return_url (the user confirms on the provider's page) or payment_method_id (a renewal).
        Returns {id, status, confirmation_url?}."""

    async def get(self, payment_id: str) -> dict[str, Any] | None:
        """{id, status, amount_kop, currency, metadata, payment_method_id} or None."""


class UnconfiguredPayments:
    async def create(self, order: dict[str, Any]) -> dict[str, Any]:
        raise AppError("PAYMENT_FAILED")

    async def get(self, payment_id: str) -> dict[str, Any] | None:
        return None


class YooKassa:
    def __init__(self, http: httpx.AsyncClient, shop_id: str, secret_key: str) -> None:
        self.http, self.auth = http, (shop_id, secret_key)

    @staticmethod
    def parse(body: dict[str, Any]) -> dict[str, Any]:
        method = body.get("payment_method") or {}
        return {
            "id": body["id"],
            "status": body["status"],
            "amount_kop": kopecks(body["amount"]["value"]),
            "currency": body["amount"]["currency"],
            "metadata": body.get("metadata") or {},
            "payment_method_id": method.get("id") if method.get("saved") else None,
            "confirmation_url": (body.get("confirmation") or {}).get("confirmation_url"),
        }

    async def create(self, order: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "amount": {"value": rubles(order["amount_kop"]), "currency": order["currency"]},
            "capture": True,
            "description": order["description"],
            "metadata": order["metadata"],
        }
        if order.get("payment_method_id"):
            body["payment_method_id"] = order["payment_method_id"]
        else:
            body["confirmation"] = {"type": "redirect", "return_url": order["return_url"]}
            body["save_payment_method"] = True
        try:
            response = await self.http.post(
                f"{API}/payments",
                json=body,
                auth=self.auth,
                headers={"Idempotence-Key": order["idempotence_key"]},
                timeout=15,
            )
            response.raise_for_status()
            return self.parse(response.json())
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("Payment creation failed: %s", type(exc).__name__)
            raise AppError("PAYMENT_FAILED") from None

    async def get(self, payment_id: str) -> dict[str, Any] | None:
        try:
            response = await self.http.get(f"{API}/payments/{payment_id}", auth=self.auth, timeout=15)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self.parse(response.json())
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("Payment lookup failed: %s", type(exc).__name__)
            raise AppError("PAYMENT_FAILED") from None


class FakePayments:
    """Payments stay pending until a test settles them with pay() or decline()."""

    def __init__(self, renewals_succeed: bool = True) -> None:
        self.payments: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.renewals_succeed = renewals_succeed

    async def create(self, order: dict[str, Any]) -> dict[str, Any]:
        if order["idempotence_key"] in self.by_key:
            return self.payments[self.by_key[order["idempotence_key"]]]
        payment_id = f"fake-{uuid4().hex[:12]}"
        renewal = bool(order.get("payment_method_id"))
        payment = {
            "id": payment_id,
            "status": ("succeeded" if self.renewals_succeed else "canceled") if renewal else "pending",
            "amount_kop": order["amount_kop"],
            "currency": order["currency"],
            "metadata": order["metadata"],
            "payment_method_id": order.get("payment_method_id"),
            "confirmation_url": None if renewal else f"https://pay.example/confirm/{payment_id}",
        }
        self.payments[payment_id] = payment
        self.by_key[order["idempotence_key"]] = payment_id
        return payment

    async def get(self, payment_id: str) -> dict[str, Any] | None:
        return self.payments.get(payment_id)

    def pay(self, payment_id: str) -> None:
        self.payments[payment_id].update(status="succeeded", payment_method_id=f"method-{payment_id}")

    def decline(self, payment_id: str) -> None:
        self.payments[payment_id]["status"] = "canceled"


def build_payments(http: httpx.AsyncClient, shop_id: str, secret_key: str) -> Payments:
    if not shop_id or not secret_key:
        return UnconfiguredPayments()
    if not secret_key.startswith("test_"):
        # Ground rule: no real money. A live key is refused, not used.
        logger.error("YOOKASSA_SECRET_KEY is not a test key; billing is disabled")
        return UnconfiguredPayments()
    return YooKassa(http, shop_id, secret_key)
