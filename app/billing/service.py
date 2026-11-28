"""Plus subscriptions: checkout, Robokassa's ResultURL, renewal, and freezing lapsed worlds."""

import asyncio
import ipaddress
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.billing.gateway import Payments
from app.config import GameConfig, Settings
from app.errors import AppError
from app.i18n import pick_locale, translate
from app.repo import Repository

logger = logging.getLogger(__name__)
DAY_MS = 86_400_000


class Billing:
    def __init__(
        self,
        settings: Settings,
        config: GameConfig,
        get_repo: Callable[[], Repository],
        payments: Payments,
        realtime: Any,
        sim: Any,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings, self.config, self.get_repo = settings, config, get_repo
        self.payments, self.realtime, self.sim, self.clock = payments, realtime, sim, clock
        self.plans: dict[str, dict[str, int]] = config.billing["plans"]
        self.task: asyncio.Task[None] | None = None
        self.renewed_on = ""

    def now(self) -> int:
        return int(self.clock() * 1000)

    # ---- checkout and ResultURL

    def receipt(self, plan: str, amount_kop: int) -> dict[str, Any] | None:
        """The fiscal receipt (54-FZ) Robokassa registers for the payment, in Russian."""
        settings = self.config.billing.get("receipt") or {}
        if not settings.get("enabled"):
            return None
        item = {
            "name": translate("ru", f"billing.description.{plan}"),
            "quantity": 1,
            "sum": amount_kop / 100,
            "payment_method": settings["payment_method"],
            "payment_object": settings["payment_object"],
            "tax": settings["tax"],
        }
        return {**({"sno": settings["sno"]} if settings.get("sno") else {}), "items": [item]}

    async def checkout(self, session: dict[str, Any], plan: str) -> str:
        if not session.get("user_id"):
            raise AppError("UNAUTHENTICATED")
        price, repo = self.plans[plan], self.get_repo()
        locale = pick_locale(None, session["locale"])
        recurring = self.config.flags.billing.recurring
        # No await from choosing the number to saving it: invoice numbers never repeat.
        inv_id = repo.next_inv_id(self.now())
        url = self.payments.payment_url({
            "inv_id": inv_id,
            "amount_kop": price["amount_kop"],
            "description": translate(locale, f"billing.description.{plan}"),
            "culture": locale,
            "receipt": self.receipt(plan, price["amount_kop"]),
            # Only an invoice paid with Recurring=true can be charged again later.
            "recurring": recurring,
        })  # fmt: skip
        repo.insert_payment(
            session["user_id"], inv_id, plan, price["amount_kop"], self.config.billing["currency"], recurring
        )
        return url

    def ip_allowed(self, host: str | None) -> bool:
        for allowed in self.config.billing["result_ips"]:
            if host == allowed:
                return True
            try:
                if ipaddress.ip_address(host or "") in ipaddress.ip_network(allowed):
                    return True
            except ValueError:
                continue
        return False

    async def result(self, fields: dict[str, str]) -> str:
        """Robokassa's ResultURL: only a notification signed with password #2 counts. The
        answer OK<InvId> tells Robokassa to stop repeating it."""
        paid = self.payments.verify(fields)
        if paid is None:
            raise AppError("VALIDATION_FAILED")
        await self.settle(paid)
        return f"OK{paid['inv_id']}"

    async def settle(self, paid: dict[str, Any]) -> bool:
        """paid: {inv_id, amount_kop} from a verified notification. True if it activated Plus."""
        repo = self.get_repo()
        row = repo.get_payment(paid["inv_id"])
        if row is None:
            return False
        plan = self.plans.get(row["plan"])
        if plan is None or paid["amount_kop"] != row["amount_kop"]:
            logger.warning("Payment %s does not match its invoice", paid["inv_id"])
            repo.settle_payment(paid["inv_id"], "canceled")
            return False
        now = self.now()
        repo.db.execute("BEGIN IMMEDIATE")
        try:
            # A repeated notification finds the payment already settled and changes nothing.
            fresh = repo.settle_payment(paid["inv_id"], "succeeded")
            if fresh:
                current = repo.get_subscription(row["user_id"])
                start = max(now, (current or {}).get("current_period_end") or 0)
                repo.activate_subscription(
                    row["user_id"], start + plan["period_days"] * DAY_MS,
                    paid["inv_id"] if row["recurring"] else None,
                )  # fmt: skip
            repo.db.execute("COMMIT")
        except BaseException:
            repo.db.execute("ROLLBACK")
            raise
        if fresh:
            await self.sweep()  # paying unfreezes immediately
        return fresh

    def payment(self, session: dict[str, Any], inv_id: int) -> dict[str, Any]:
        """The buyer's own invoice, for /billing/return. Anyone else's is not found."""
        row = self.get_repo().get_payment(inv_id)
        if row is None or not session.get("user_id") or row["user_id"] != session["user_id"]:
            raise AppError("NOT_FOUND")
        amount = row["amount_kop"] / 100
        return {
            "invId": row["inv_id"], "plan": row["plan"], "amount": int(amount) if amount.is_integer() else amount,
            "currency": row["currency"], "status": row["status"],
        }  # fmt: skip

    def cancel(self, session: dict[str, Any]) -> dict[str, Any]:
        repo = self.get_repo()
        subscription = repo.get_subscription(session.get("user_id"))
        if subscription is None:
            raise AppError("UNAUTHENTICATED" if not session.get("user_id") else "NOT_FOUND")
        if subscription["status"] == "active":
            repo.set_subscription_status(session["user_id"], "canceled")
        return self.public(repo.get_subscription(session["user_id"]))

    @staticmethod
    def public(subscription: dict[str, Any] | None) -> dict[str, Any] | None:
        if subscription is None:
            return None
        return {"status": subscription["status"], "periodEnd": subscription["current_period_end"]}

    # ---- time passing

    def lapsed(self, subscription: dict[str, Any] | None, now: int) -> bool:
        if subscription is None:
            return True
        end = subscription["current_period_end"] or 0
        if subscription["status"] == "past_due":
            return now > end + self.config.billing["past_due_grace_days"] * DAY_MS
        return now > end

    async def sweep(self) -> None:
        """Expire periods and freeze or unfreeze every private world to match its owner."""
        repo, now = self.get_repo(), self.now()
        for subscription in repo.subscriptions():
            if subscription["status"] == "active" and now > (subscription["current_period_end"] or 0):
                # past_due first: a period is charged at most once, and a renewal that Robokassa
                # confirms at ResultURL makes the subscription active again.
                repo.set_subscription_status(subscription["user_id"], "past_due")
                await self.renew(subscription)
        for canvas in repo.db.execute("SELECT * FROM canvas WHERE kind='private' AND deleted_at IS NULL").fetchall():
            frozen = self.lapsed(repo.get_subscription(canvas["owner_user_id"]), now)
            if frozen != bool(canvas["frozen"]):
                repo.set_frozen(canvas["id"], frozen)
                self.sim.send({"op": "freeze", "canvas": canvas["id"], "frozen": frozen})
                await self.realtime.frozen(canvas["id"], frozen)

    async def renew(self, subscription: dict[str, Any]) -> bool:
        """Charge another month by repeating the first paid invoice. Off unless billing.recurring.
        True means Robokassa accepted the charge; the money arrives later at ResultURL."""
        if not self.config.flags.billing.recurring or not subscription["parent_inv_id"]:
            return False
        repo, price = self.get_repo(), self.plans["plus_month"]
        inv_id = repo.next_inv_id(self.now())
        repo.insert_payment(
            subscription["user_id"], inv_id, "plus_month", price["amount_kop"], self.config.billing["currency"], False
        )
        accepted = await self.payments.charge({
            "inv_id": inv_id,
            "previous_inv_id": subscription["parent_inv_id"],
            "amount_kop": price["amount_kop"],
            "description": translate("ru", "billing.description.plus_month"),
            "receipt": self.receipt("plus_month", price["amount_kop"]),
        })  # fmt: skip
        if not accepted:
            repo.settle_payment(inv_id, "canceled")
        return accepted

    async def run(self) -> None:
        """Freeze checks every minute; the 03:00 MSK pass is the same sweep, logged once a day."""
        hour = self.config.billing["renewal_hour_msk"]
        while True:
            try:
                msk = datetime.fromtimestamp(self.clock(), UTC) + timedelta(hours=3)
                await self.sweep()
                if msk.hour == hour and self.renewed_on != f"{msk:%Y-%m-%d}":
                    self.renewed_on = f"{msk:%Y-%m-%d}"
                    logger.warning("Daily renewal pass done for %s", self.renewed_on)
            except Exception:
                logger.exception("Billing sweep failed")
            await asyncio.sleep(60)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
