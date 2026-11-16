"""Stroke SQL. There are deliberately no stroke mutation methods."""

import json
import sqlite3
import time
from typing import Any

from app.models import StrokeIn


class StrokeRepo:
    db: sqlite3.Connection

    @staticmethod
    def _stroke(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "seq": row["seq"],
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

    def insert_stroke(self, stroke: StrokeIn) -> tuple[dict[str, Any], bool]:
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
            canvas = self.get_canvas(stroke.canvas_id)  # type: ignore[attr-defined]
            seq = canvas["stroke_count"] + 1
            created_at = time.time_ns() // 1_000_000
            self.db.execute(
                "INSERT INTO stroke(canvas_id,seq,client_id,color,width,points,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    stroke.canvas_id,
                    seq,
                    stroke.client_id,
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
            "color": stroke.color,
            "width": stroke.width,
            "points": stroke.points,
            "createdAt": created_at,
        }, True
