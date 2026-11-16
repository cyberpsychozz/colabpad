"""Stroke SQL. There are deliberately no stroke mutation methods."""

import json
import sqlite3
from typing import Any

from app.models import StrokeIn
from app.repo.canvases import now_ms


class StrokeRepo:
    db: sqlite3.Connection

    @staticmethod
    def _stroke(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "seq": row["seq"],
            "color": row["color"],
            "width": row["width"],
            "points": json.loads(row["points"]),
            "cx": row["chunk_x"],
            "cy": row["chunk_y"],
            "createdAt": row["created_at"],
        }

    def chunk_strokes(
        self, canvas_id: str, cx: int, cy: int, after_seq: int = 0
    ) -> list[dict[str, Any]]:
        return [
            self._stroke(row)
            for row in self.db.execute(
                "SELECT * FROM stroke WHERE canvas_id=? AND chunk_x=? AND chunk_y=?"
                " AND seq>? ORDER BY seq",
                (canvas_id, cx, cy, after_seq),
            )
        ]

    def insert_stroke(
        self, stroke: StrokeIn, chunk: tuple[int, int], session_id: str | None = None
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
            canvas = self.get_canvas(stroke.canvas_id)  # type: ignore[attr-defined]
            seq = canvas["stroke_count"] + 1
            created_at = now_ms()
            self.db.execute(
                "INSERT INTO stroke(canvas_id,seq,client_id,session_id,color,width,points,"
                "chunk_x,chunk_y,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    stroke.canvas_id,
                    seq,
                    stroke.client_id,
                    session_id,
                    stroke.color,
                    stroke.width,
                    json.dumps(stroke.points, separators=(",", ":")),
                    chunk[0],
                    chunk[1],
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
            "cx": chunk[0],
            "cy": chunk[1],
            "createdAt": created_at,
        }, True
