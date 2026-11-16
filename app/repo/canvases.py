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
            "SELECT * FROM canvas WHERE id=?", (canvas_id,)
        ).fetchone()
        if row is None:
            raise AppError("NOT_FOUND")
        return dict(row)
