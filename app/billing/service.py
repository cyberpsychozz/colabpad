"""Plus subscriptions: checkout, the webhook, renewal, and freezing lapsed worlds."""

import asyncio
import ipaddress
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

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

    # ---- checkout and webhook

    async def checkout(self, session: dict[str, Any], plan: str) -> str:
        if not session.get("user_id"):
            raise AppError("UNAUTHENTICATED")
        price = self.plans[plan]
        payment = await self.payments.create({
            "amount_kop": price["amount_kop"],
            "currency": self.config.billing["currency"],
            "description": translate(pick_locale(None, session["locale"]), f"billing.description.{plan}"),
            "metadata": {"userId": session["user_id"], "plan": plan},
            "idempotence_key": str(uuid4()),
            "return_url": f"{self.settings.public_url.rstrip('/')}/billing/return",
        })  # fmt: skip
        if not payment.get("confirmation_url"):
            raise AppError("PAYMENT_FAILED")
        self.get_repo().insert_payment(
            session["user_id"], payment["id"], price["amount_kop"], self.config.billing["currency"]
        )
        return payment["confirmation_url"]

    def ip_allowed(self, host: str | None) -> bool:
        for allowed in self.config.billing["webhook_ips"]:
            if host == allowed:
                return True
            try:
                if ipaddress.ip_address(host or "") in ipaddress.ip_network(allowed):
                    return True
            except ValueError:
                continue
        return False

    async def webhook(self, payment_id: str) -> None:
        """Never trust the notification body: ask the provider what really happened."""
        payment = await self.payments.get(payment_id)
        if payment is not None:
            await self.settle(payment)

    async def settle(self, payment: dict[str, Any]) -> bool:
        repo = self.get_repo()
        row = repo.get_payment(payment["id"])
        if row is None or payment["status"] not in ("succeeded", "canceled"):
            return False
        plan = self.plans.get(payment["metadata"].get("plan", ""))
        if payment["status"] == "canceled" or plan is None or payment["amount_kop"] != row["amount_kop"]:
            repo.settle_payment(payment["id"], "canceled")
            return False
        now = self.now()
        repo.db.execute("BEGIN IMMEDIATE")
        try:
            # A repeated webhook finds the payment already settled and changes nothing.
            fresh = repo.settle_payment(payment["id"], "succeeded")
            if fresh:
                current = repo.get_subscription(row["user_id"])
                start = max(now, (current or {}).get("current_period_end") or 0)
                repo.activate_subscription(
                    row["user_id"], start + plan["period_days"] * DAY_MS, payment["payment_method_id"]
                )
            repo.db.execute("COMMIT")
        except BaseException:
            repo.db.execute("ROLLBACK")
            raise
        if fresh:
            await self.sweep()  # paying unfreezes immediately
        return fresh

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
                if not await self.renew(subscription):
                    repo.set_subscription_status(subscription["user_id"], "past_due")
        for canvas in repo.db.execute("SELECT * FROM canvas WHERE kind='private'").fetchall():
            frozen = self.lapsed(repo.get_subscription(canvas["owner_user_id"]), now)
            if frozen != bool(canvas["frozen"]):
                repo.set_frozen(canvas["id"], frozen)
                self.sim.send({"op": "freeze", "canvas": canvas["id"], "frozen": frozen})
                await self.realtime.frozen(canvas["id"], frozen)

    async def renew(self, subscription: dict[str, Any]) -> bool:
        """Charge the saved payment method for another month. Off unless billing.recurring."""
        if not self.config.flags.billing.recurring or not subscription["yk_payment_method_id"]:
            return False
        period = datetime.fromtimestamp((subscription["current_period_end"] or 0) / 1000, UTC)
        price = self.plans["plus_month"]
        try:
            payment = await self.payments.create({
                "amount_kop": price["amount_kop"],
                "currency": self.config.billing["currency"],
                "description": translate("ru", "billing.description.plus_month"),
                "metadata": {"userId": subscription["user_id"], "plan": "plus_month"},
                # One attempt per period: a retry of the same period is the same payment.
                "idempotence_key": f"renew-{subscription['user_id']}-{period:%Y%m%d}",
                "payment_method_id": subscription["yk_payment_method_id"],
            })  # fmt: skip
        except AppError:
            return False
        repo = self.get_repo()
        if repo.get_payment(payment["id"]) is None:
            repo.insert_payment(subscription["user_id"], payment["id"], price["amount_kop"], self.config.billing["currency"])
        return await self.settle(payment)

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
