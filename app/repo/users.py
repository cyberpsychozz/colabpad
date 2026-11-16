"""Sessions and users. E-mail is the only personal data stored."""

import secrets
import sqlite3
from typing import Any

from app.repo.canvases import now_ms


class UserRepo:
    db: sqlite3.Connection

    def create_session(self, locale: str | None = None) -> dict[str, Any]:
        session_id = secrets.token_urlsafe(24)
        self.db.execute(
            "INSERT INTO session(id, locale, created_at, last_seen_at) VALUES(?,?,?,?)",
            (session_id, locale, now_ms(), now_ms()),
        )
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        row = self.db.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def plan_of(self, session: dict[str, Any], at_ms: int | None = None) -> str:
        """anon (no account), free, or plus (an active subscription)."""
        if not session.get("user_id"):
            return "anon"
        row = self.db.execute(
            "SELECT plan, status, current_period_end FROM subscription WHERE user_id=?",
            (session["user_id"],),
        ).fetchone()
        active = (
            row is not None
            and row["plan"] == "plus"
            and row["status"] in ("active", "canceled")
            and (row["current_period_end"] or 0) > (at_ms or now_ms())
        )
        return "plus" if active else "free"

    @staticmethod
    def subject_of(session: dict[str, Any]) -> str:
        """Who a quota or rate limit belongs to: the account if any, else the session."""
        return f"u:{session['user_id']}" if session.get("user_id") else f"s:{session['id']}"
