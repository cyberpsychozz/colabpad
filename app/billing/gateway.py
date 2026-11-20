"""The payment provider behind an interface: Robokassa in test mode (docs/spec.md §9).

Robokassa has no API call that creates a payment: the shop signs a link to its payment page
with password #1, and Robokassa reports a paid invoice to ResultURL, signed with password #2.
Its status API (OpStateExt) does not answer for test payments, so the ResultURL signature is
the proof of payment.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import quote, urlencode

import httpx

from app.errors import AppError

logger = logging.getLogger(__name__)
PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
RECURRING_URL = "https://auth.robokassa.ru/Merchant/Recurring"
# The hash the shop's technical settings choose; the signature must use the same one.
ALGORITHMS = ("md5", "sha1", "sha256", "sha384", "sha512")


def rubles(amount_kop: int) -> str:
    return f"{amount_kop // 100}.{amount_kop % 100:02d}"


def kopecks(value: str) -> int:
    """Robokassa sends "299.00" in test mode and "299.000000" in production."""
    whole, _, cents = value.partition(".")
    if not whole.isdigit() or (cents and not cents.isdigit()):
        raise ValueError(value)
    return int(whole) * 100 + int((cents + "00")[:2])


class Payments(Protocol):
    def payment_url(self, order: dict[str, Any]) -> str:
        """order: inv_id, amount_kop, description, culture, receipt (dict or None), recurring.
        Returns the payment page the user is sent to."""

    def verify(self, fields: Mapping[str, str]) -> dict[str, Any] | None:
        """A ResultURL notification → {inv_id, amount_kop, shp}, or None if it is not signed
        by the shop's password #2."""

    async def charge(self, order: dict[str, Any]) -> bool:
        """A recurring charge: inv_id, previous_inv_id, amount_kop, description, receipt.
        True means Robokassa accepted it; the payment itself arrives later at ResultURL."""


class UnconfiguredPayments:
    def payment_url(self, order: dict[str, Any]) -> str:
        raise AppError("PAYMENT_FAILED")

    def verify(self, fields: Mapping[str, str]) -> dict[str, Any] | None:
        return None

    async def charge(self, order: dict[str, Any]) -> bool:
        return False


def receipt_value(receipt: dict[str, Any] | None) -> str | None:
    """The Receipt parameter: compact UTF-8 JSON, URL-encoded once. The signature uses this
    exact string, and the request encodes it again as an ordinary parameter."""
    if not receipt:
        return None
    return quote(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), safe="")


class Robokassa:
    """Test mode only: every request carries IsTest=1 and is signed with the test passwords.
    (Production passwords are rejected by Robokassa in test mode, and test passwords in
    production, so no setting here can move real money.)"""

    def __init__(
        self, http: httpx.AsyncClient, login: str, password1: str, password2: str, algorithm: str = "md5"
    ) -> None:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"Unsupported Robokassa hash: {algorithm}")
        self.http, self.login = http, login
        self.password1, self.password2, self.algorithm = password1, password2, algorithm

    def sign(self, *parts: str) -> str:
        return hashlib.new(self.algorithm, ":".join(parts).encode()).hexdigest()

    def payment_url(self, order: dict[str, Any]) -> str:
        out_sum, inv_id = rubles(order["amount_kop"]), str(order["inv_id"])
        receipt = receipt_value(order.get("receipt"))
        params = {
            "MerchantLogin": self.login,
            "OutSum": out_sum,
            "InvId": inv_id,
            "Description": order["description"][:100],
            "Culture": order["culture"],
            "Encoding": "utf-8",
            "IsTest": "1",
        }
        if receipt:
            params["Receipt"] = receipt
        if order.get("recurring"):
            params["Recurring"] = "true"
        modifiers = [receipt] if receipt else []
        params["SignatureValue"] = self.sign(self.login, out_sum, inv_id, *modifiers, self.password1)
        return f"{PAY_URL}?{urlencode(params)}"

    def verify(self, fields: Mapping[str, str]) -> dict[str, Any] | None:
        out_sum, inv_id, signature = (fields.get(k) for k in ("OutSum", "InvId", "SignatureValue"))
        if not (out_sum and inv_id and signature):
            return None
        # Shp_ parameters come back as they were sent, signed in alphabetical order.
        shp = {key: value for key, value in sorted(fields.items()) if key.startswith("Shp_")}
        expected = self.sign(out_sum, inv_id, self.password2, *(f"{k}={v}" for k, v in shp.items()))
        if not hmac.compare_digest(expected, signature.lower()):
            return None
        try:
            amount_kop, number = kopecks(out_sum), int(inv_id)
        except ValueError:
            return None
        return {"inv_id": number, "amount_kop": amount_kop, "shp": shp} if number > 0 else None

    async def charge(self, order: dict[str, Any]) -> bool:
        out_sum, inv_id = rubles(order["amount_kop"]), str(order["inv_id"])
        receipt = receipt_value(order.get("receipt"))
        data = {
            "MerchantLogin": self.login,
            "InvoiceID": inv_id,
            "PreviousInvoiceID": str(order["previous_inv_id"]),
            "OutSum": out_sum,
            "Description": order["description"][:100],
            "IsTest": "1",
        }
        if receipt:
            data["Receipt"] = receipt
        modifiers = [receipt] if receipt else []
        data["SignatureValue"] = self.sign(self.login, out_sum, inv_id, *modifiers, self.password1)
        try:
            response = await self.http.post(RECURRING_URL, data=data, timeout=15)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Recurring charge failed: %s", type(exc).__name__)
            return False
        accepted = response.text.strip().startswith("OK")
        if not accepted:
            logger.warning("Recurring charge refused: %s", response.text.strip()[:200])
        return accepted


def build_payments(
    http: httpx.AsyncClient, login: str, password1: str, password2: str, algorithm: str = "md5"
) -> Payments:
    if not (login and password1 and password2):
        return UnconfiguredPayments()
    return Robokassa(http, login, password1, password2, algorithm)
