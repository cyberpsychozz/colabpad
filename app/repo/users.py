"""Sessions and users. E-mail is the only personal data stored."""

import hashlib
import hmac
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

    @staticmethod
    def code_digest(session_id: str, code: str) -> str:
        return hashlib.sha256(f"{session_id}:{code}".encode()).hexdigest()

    def store_login_code(self, session_id: str, email: str, locale: str, code: str, expires: int) -> None:
        self.db.execute("DELETE FROM login_code WHERE expires_at <= ?", (now_ms(),))
        self.db.execute(
            "INSERT INTO login_code(session_id,email,locale,code_hash,expires_at,attempts) VALUES(?,?,?,?,?,0)"
            " ON CONFLICT(session_id) DO UPDATE SET email=excluded.email, locale=excluded.locale,"
            " code_hash=excluded.code_hash, expires_at=excluded.expires_at, attempts=0",
            (session_id, email, locale, self.code_digest(session_id, code), expires),
        )

    def discard_login_code(self, session_id: str, code: str) -> None:
        self.db.execute("DELETE FROM login_code WHERE session_id=? AND code_hash=?",
                        (session_id, self.code_digest(session_id, code)))

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

    def redeem_login_code(self, session: dict[str, Any], code: str, max_attempts: int) -> str:
        """Check the code this session asked for; on success sign the session in and return a new cookie.

        A wrong guess costs an attempt; the last one voids the code."""
        sid = session["id"]
        now = now_ms()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            pending = self.db.execute("SELECT * FROM login_code WHERE session_id=?", (sid,)).fetchone()
            failure: AppError | None = None
            if not pending or pending["expires_at"] <= now:
                failure = AppError("CODE_EXPIRED", "code")
            elif not hmac.compare_digest(pending["code_hash"], self.code_digest(sid, code)):
                attempts = pending["attempts"] + 1
                if attempts >= max_attempts:
                    failure = AppError("CODE_EXPIRED", "code")
                else:
                    self.db.execute("UPDATE login_code SET attempts=? WHERE session_id=?", (attempts, sid))
                    failure = AppError("CODE_INVALID", "code", {"left": max_attempts - attempts})
            if failure:
                if failure.code == "CODE_EXPIRED":
                    self.db.execute("DELETE FROM login_code WHERE session_id=?", (sid,))
                self.db.commit()
                raise failure
            self.db.execute("DELETE FROM login_code WHERE session_id=?", (sid,))
            self.db.execute(
                "INSERT INTO app_user(id,email,locale,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(email) DO NOTHING",
                (str(uuid4()), pending["email"], pending["locale"], now),
            )
            user_id = self.db.execute("SELECT id FROM app_user WHERE email=?", (pending["email"],)).fetchone()[0]
            if session["user_id"]:
                # Signing in to another account from a signed-in browser: start a fresh session.
                self.logout(sid)
                sid = self.create_session(pending["locale"])["id"]
            self.db.execute("UPDATE session SET user_id=?,locale=? WHERE id=?", (user_id, pending["locale"], sid))
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

    def user_by_email(self, email: str, locale: str | None = None) -> dict[str, Any]:
        """The account for an address, created if this is the first time we see it."""
        self.db.execute(
            "INSERT INTO app_user(id,email,locale,created_at) VALUES(?,?,?,?)"
            " ON CONFLICT(email) DO NOTHING",
            (str(uuid4()), email, locale, now_ms()),
        )
        return dict(self.db.execute("SELECT * FROM app_user WHERE email=?", (email,)).fetchone())

    def set_locale(self, session: dict[str, Any], locale: str) -> None:
        self.db.execute("UPDATE session SET locale=? WHERE id=?", (locale, session["id"]))
        if session.get("user_id"):
            self.db.execute("UPDATE app_user SET locale=? WHERE id=?", (locale, session["user_id"]))

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
