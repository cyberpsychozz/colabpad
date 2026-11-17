"""Payments and subscriptions. Payment rows are kept forever."""

import sqlite3
from typing import Any
from uuid import uuid4

from app.repo.canvases import now_ms


class BillingRepo:
    db: sqlite3.Connection

    def insert_payment(self, user_id: str, yk_payment_id: str, amount_kop: int, currency: str) -> None:
        self.db.execute(
            "INSERT INTO payment(id,user_id,yk_payment_id,amount_kop,currency,status,created_at)"
            " VALUES(?,?,?,?,?,'pending',?)",
            (str(uuid4()), user_id, yk_payment_id, amount_kop, currency, now_ms()),
        )

    def get_payment(self, yk_payment_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM payment WHERE yk_payment_id=?", (yk_payment_id,)
        ).fetchone()
        return dict(row) if row else None

    def settle_payment(self, yk_payment_id: str, status: str) -> bool:
        """Move a pending payment to its final status. False if it was already final."""
        return bool(
            self.db.execute(
                "UPDATE payment SET status=? WHERE yk_payment_id=? AND status='pending'",
                (status, yk_payment_id),
            ).rowcount
        )

    def get_subscription(self, user_id: str | None) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM subscription WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def subscriptions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM subscription")]

    def activate_subscription(
        self, user_id: str, period_end: int, payment_method_id: str | None
    ) -> None:
        self.db.execute(
            "INSERT INTO subscription(id,user_id,plan,status,current_period_end,yk_payment_method_id)"
            " VALUES(?,?,'plus','active',?,?) ON CONFLICT(user_id) DO UPDATE SET plan='plus',"
            " status='active', current_period_end=excluded.current_period_end,"
            " yk_payment_method_id=COALESCE(excluded.yk_payment_method_id, yk_payment_method_id)",
            (str(uuid4()), user_id, period_end, payment_method_id),
        )

    def set_subscription_status(self, user_id: str, status: str) -> None:
        self.db.execute("UPDATE subscription SET status=? WHERE user_id=?", (status, user_id))
