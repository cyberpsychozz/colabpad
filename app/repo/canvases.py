"""Canvas SQL."""

import sqlite3
import time
from typing import Any

from nanoid import generate

from app.config import Settings
from app.errors import not_found


class CanvasRepo:
    db: sqlite3.Connection
    settings: Settings

    def create_canvas(self) -> dict[str, Any]:
        for _ in range(5):
            canvas_id = generate(size=10)
            try:
                self.db.execute(
                    "INSERT INTO canvas(id, width, height, created_at) VALUES(?,?,?,?)",
                    (
                        canvas_id,
                        self.settings.canvas_width,
                        self.settings.canvas_height,
                        time.time_ns() // 1_000_000,
                    ),
                )
                return self.get_canvas(canvas_id)
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Could not allocate canvas ID")

    def get_canvas(self, canvas_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM canvas WHERE id=?", (canvas_id,)
        ).fetchone()
        if row is None:
            raise not_found()
        return dict(row)
