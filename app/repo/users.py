"""Sessions and users. E-mail is the only personal data stored."""

import hashlib
import secrets
import sqlite3
from typing import Any
from uuid import uuid4

from app.errors import AppError

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

    def store_magic_token(self, digest: str, email: str, locale: str, expires: int) -> None:
        self.db.execute("DELETE FROM magic_token WHERE expires_at <= ?", (now_ms(),))
        self.db.execute(
            "INSERT INTO magic_token(token_hash,email,locale,expires_at) VALUES(?,?,?,?)",
            (digest, email, locale, expires),
        )

    def discard_magic_token(self, digest: str) -> None:
        self.db.execute("DELETE FROM magic_token WHERE token_hash=?", (digest,))

    def cookie_session(self, cookie: str | None) -> dict[str, Any] | None:
        if not cookie:
            return None
        digest = hashlib.sha256(cookie.encode()).hexdigest()
        row = self.db.execute(
            "SELECT s.* FROM session s JOIN login_cookie c ON c.session_id=s.id WHERE c.digest=?",
            (digest,),
        ).fetchone()
        if row:
            return dict(row)
        session = self.get_session(cookie)
        return session if session and not session["user_id"] else None

    def redeem_magic_token(self, token: str, session: dict[str, Any] | None) -> str:
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = now_ms()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            link = self.db.execute(
                "DELETE FROM magic_token WHERE token_hash=? RETURNING email,locale,expires_at",
                (digest,),
            ).fetchone()
            if not link or link["expires_at"] <= now:
                self.db.commit()
                raise AppError("UNAUTHENTICATED")
            self.db.execute(
                "INSERT INTO app_user(id,email,locale,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(email) DO NOTHING",
                (str(uuid4()), link["email"], link["locale"], now),
            )
            user_id = self.db.execute("SELECT id FROM app_user WHERE email=?", (link["email"],)).fetchone()[0]
            if session and session["user_id"]:
                self.logout(session["id"])
                session = None
            if session is None:
                session = self.create_session(link["locale"])
            sid = session["id"]
            self.db.execute("UPDATE session SET user_id=?,locale=? WHERE id=?", (user_id, link["locale"], sid))
            self.db.execute(
                "INSERT INTO quota(subject,day,alive_used) SELECT ?,day,alive_used FROM quota WHERE subject=? "
                "ON CONFLICT(subject,day) DO UPDATE SET alive_used=quota.alive_used+excluded.alive_used",
                ("u:" + user_id, "s:" + sid),
            )
            self.db.execute("DELETE FROM quota WHERE subject=?", ("s:" + sid,))
            cookie = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO login_cookie VALUES(?,?)", (hashlib.sha256(cookie.encode()).hexdigest(), sid))
            self.db.commit()
            return cookie
        except Exception:
            self.db.rollback()
            raise

    def logout(self, sid: str) -> None:
        self.db.execute("DELETE FROM login_cookie WHERE session_id=?", (sid,))

    def public_user(self, user_id: str | None) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id,email FROM app_user WHERE id=?", (user_id,)).fetchone()
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
