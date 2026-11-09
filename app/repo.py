"""All application SQL. There are deliberately no stroke mutation methods."""

import json
import sqlite3
import time
from typing import Any

from nanoid import generate

from app.config import Settings
from app.errors import AppError, not_found
from app.models import StrokeIn


class Repository:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.db = connection
        self.settings = settings

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

    @staticmethod
    def _stroke(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "seq": row["seq"],
            "authorName": row["author_name"],
            "color": row["color"],
            "width": row["width"],
            "points": json.loads(row["points"]),
            "createdAt": row["created_at"],
        }

    def list_strokes(self, canvas_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        return [
            self._stroke(row)
            for row in self.db.execute(
                "SELECT * FROM stroke WHERE canvas_id=? AND seq>? ORDER BY seq",
                (canvas_id, after_seq),
            )
        ]

    def insert_stroke(
        self, stroke: StrokeIn, author: str | None
    ) -> tuple[dict[str, Any], bool]:
        # This function is synchronous: never yield while assigning seq.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT * FROM stroke WHERE canvas_id=? AND client_id=?",
                (stroke.canvas_id, stroke.client_id),
            ).fetchone()
            if existing is not None:
                self.db.execute("COMMIT")
                return self._stroke(existing), False
            canvas = self.get_canvas(stroke.canvas_id)
            if canvas["stroke_count"] >= self.settings.max_strokes_per_canvas:
                raise AppError("CANVAS_FULL", "Холст заполнен.")
            seq = canvas["stroke_count"] + 1
            created_at = time.time_ns() // 1_000_000
            self.db.execute(
                "INSERT INTO stroke(canvas_id,seq,client_id,author_name,color,width,points,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    stroke.canvas_id,
                    seq,
                    stroke.client_id,
                    author,
                    stroke.color,
                    stroke.width,
                    json.dumps(stroke.points, separators=(",", ":")),
                    created_at,
                ),
            )
            self.db.execute(
                "UPDATE canvas SET stroke_count=stroke_count+1 WHERE id=?",
                (stroke.canvas_id,),
            )
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {
            "seq": seq,
            "authorName": author,
            "color": stroke.color,
            "width": stroke.width,
            "points": stroke.points,
            "createdAt": created_at,
        }, True

    def increment_metric(self, key: str) -> None:
        self.db.execute(
            "INSERT INTO metric(key,value) VALUES(?,1) ON CONFLICT(key) DO UPDATE SET value=value+1",
            (key,),
        )
