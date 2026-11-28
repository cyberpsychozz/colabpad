"""Canvas SQL: the shared world and private worlds."""

import sqlite3
import time
from typing import Any

from nanoid import generate

from app.errors import AppError

WORLD_ID = "world"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class CanvasRepo:
    db: sqlite3.Connection

    def ensure_world(self, bounds_w: int, bounds_h: int) -> dict[str, Any]:
        """Create the shared world if missing and apply the configured bounds."""
        self.db.execute(
            "INSERT INTO canvas(id, kind, link_role, bounds_w, bounds_h, created_at)"
            " VALUES(?, 'world', 'editor', ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET bounds_w=excluded.bounds_w, bounds_h=excluded.bounds_h",
            (WORLD_ID, bounds_w, bounds_h, now_ms()),
        )
        return self.get_canvas(WORLD_ID)

    def create_private_canvas(
        self, owner_user_id: str, title: str | None, bounds_w: int, bounds_h: int
    ) -> dict[str, Any]:
        for _ in range(5):
            canvas_id = generate(size=10)
            try:
                self.db.execute(
                    "INSERT INTO canvas(id, kind, owner_user_id, title, link_role,"
                    " bounds_w, bounds_h, created_at) VALUES(?, 'private', ?, ?, 'none', ?, ?, ?)",
                    (canvas_id, owner_user_id, title, bounds_w, bounds_h, now_ms()),
                )
                return self.get_canvas(canvas_id)
            except sqlite3.IntegrityError as exc:
                if "canvas.id" not in str(exc):
                    raise
        raise RuntimeError("Could not allocate canvas ID")

    def get_canvas(self, canvas_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM canvas WHERE id=? AND deleted_at IS NULL", (canvas_id,)
        ).fetchone()
        if row is None:
            raise AppError("NOT_FOUND")
        return dict(row)

    # ---- private worlds, access and members

    ROLES = ("none", "viewer", "player", "editor")

    def role_of(self, canvas: dict[str, Any], session: dict[str, Any] | None) -> str:
        """Owner is editor; explicit member role overrides the link role.

        A frozen world lets everyone watch and nothing more."""
        user_id = session.get("user_id") if session else None
        if canvas["kind"] == "world" or (user_id and canvas["owner_user_id"] == user_id):
            role = "editor"
        else:
            row = (
                self.db.execute(
                    "SELECT role FROM canvas_member WHERE canvas_id=? AND user_id=?",
                    (canvas["id"], user_id),
                ).fetchone()
                if user_id
                else None
            )
            role = row["role"] if row else canvas["link_role"]
        if canvas["frozen"] and role != "none":
            return "viewer"
        return role

    def owned_canvases(self, user_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM canvas WHERE owner_user_id=? AND deleted_at IS NULL ORDER BY created_at",
                (user_id,),
            )
        ]

    def shared_canvases(self, user_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT c.*, m.role AS member_role FROM canvas c JOIN canvas_member m"
                " ON m.canvas_id=c.id WHERE m.user_id=? AND c.deleted_at IS NULL ORDER BY m.created_at",
                (user_id,),
            )
        ]

    def rename_canvas(self, canvas_id: str, title: str) -> dict[str, Any]:
        """Update a private canvas title."""
        self.db.execute("UPDATE canvas SET title=? WHERE id=? AND deleted_at IS NULL", (title, canvas_id))
        return self.get_canvas(canvas_id)

    def delete_canvas(self, canvas_id: str) -> None:
        """Soft-delete a private canvas and revoke its members and link access."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "UPDATE canvas SET deleted_at=?, link_role='none' WHERE id=? AND deleted_at IS NULL",
                (now_ms(), canvas_id),
            )
            self.db.execute("DELETE FROM canvas_member WHERE canvas_id=?", (canvas_id,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def set_link_role(self, canvas_id: str, role: str) -> None:
        self.db.execute("UPDATE canvas SET link_role=? WHERE id=?", (role, canvas_id))

    def set_frozen(self, canvas_id: str, frozen: bool) -> None:
        self.db.execute("UPDATE canvas SET frozen=? WHERE id=?", (int(frozen), canvas_id))

    def members(self, canvas_id: str) -> list[dict[str, Any]]:
        return [
            {"userId": row["user_id"], "email": row["email"], "role": row["role"]}
            for row in self.db.execute(
                "SELECT m.user_id, m.role, u.email FROM canvas_member m JOIN app_user u"
                " ON u.id=m.user_id WHERE m.canvas_id=? ORDER BY m.created_at",
                (canvas_id,),
            )
        ]

    def set_member(self, canvas_id: str, user_id: str, role: str, invited_by: str) -> None:
        self.db.execute(
            "INSERT INTO canvas_member(canvas_id,user_id,role,invited_by,created_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(canvas_id,user_id) DO UPDATE SET role=excluded.role",
            (canvas_id, user_id, role, invited_by, now_ms()),
        )

    def update_member(self, canvas_id: str, user_id: str, role: str) -> bool:
        return bool(
            self.db.execute(
                "UPDATE canvas_member SET role=? WHERE canvas_id=? AND user_id=?",
                (role, canvas_id, user_id),
            ).rowcount
        )

    def remove_member(self, canvas_id: str, user_id: str) -> bool:
        return bool(
            self.db.execute(
                "DELETE FROM canvas_member WHERE canvas_id=? AND user_id=?", (canvas_id, user_id)
            ).rowcount
        )
