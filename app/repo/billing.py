"""Payments and subscriptions. Payment rows are kept forever."""

import sqlite3
from typing import Any
from uuid import uuid4

from app.repo.canvases import now_ms


class BillingRepo:
    db: sqlite3.Connection

    def next_inv_id(self, now: int) -> int:
        """Robokassa invoice numbers must never repeat for the shop. Unix seconds as a floor
        keeps them unique even across a fresh database."""
        last = self.db.execute("SELECT MAX(inv_id) FROM payment").fetchone()[0] or 0
        return max(last + 1, now // 1000)

    def insert_payment(
        self, user_id: str, inv_id: int, plan: str, amount_kop: int, currency: str, recurring: bool
    ) -> None:
        self.db.execute(
            "INSERT INTO payment(id,user_id,inv_id,plan,amount_kop,currency,recurring,status,created_at)"
            " VALUES(?,?,?,?,?,?,?,'pending',?)",
            (str(uuid4()), user_id, inv_id, plan, amount_kop, currency, int(recurring), now_ms()),
        )

    def get_payment(self, inv_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM payment WHERE inv_id=?", (inv_id,)).fetchone()
        return dict(row) if row else None

    def settle_payment(self, inv_id: int, status: str) -> bool:
        """Move a pending payment to its final status. False if it was already final."""
        return bool(
            self.db.execute(
                "UPDATE payment SET status=? WHERE inv_id=? AND status='pending'", (status, inv_id)
            ).rowcount
        )

    def get_subscription(self, user_id: str | None) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM subscription WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def subscriptions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM subscription")]

    def activate_subscription(self, user_id: str, period_end: int, parent_inv_id: int | None) -> None:
        """parent_inv_id: a paid invoice that recurring charges can repeat (kept if None)."""
        self.db.execute(
            "INSERT INTO subscription(id,user_id,plan,status,current_period_end,parent_inv_id)"
            " VALUES(?,?,'plus','active',?,?) ON CONFLICT(user_id) DO UPDATE SET plan='plus',"
            " status='active', current_period_end=excluded.current_period_end,"
            " parent_inv_id=COALESCE(excluded.parent_inv_id, parent_inv_id)",
            (str(uuid4()), user_id, period_end, parent_inv_id),
        )

    def set_subscription_status(self, user_id: str, status: str) -> None:
        self.db.execute("UPDATE subscription SET status=? WHERE user_id=?", (status, user_id))
