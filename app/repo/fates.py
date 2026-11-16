"""Stroke fates: append-only. The forward-only rule is enforced by a DB trigger."""

import sqlite3
from typing import Any

from app.repo.canvases import now_ms


class FateRepo:
    db: sqlite3.Connection

    def insert_fates(
        self, canvas_id: str, seqs: list[int], fate: str, entity_id: str | None = None
    ) -> None:
        """Insert inside the caller's transaction, or alone as one statement batch."""
        at = now_ms()
        self.db.executemany(
            "INSERT INTO stroke_fate(canvas_id, seq, fate, entity_id, at) VALUES(?,?,?,?,?)",
            [(canvas_id, seq, fate, entity_id, at) for seq in seqs],
        )

    def chunk_fates(self, canvas_id: str, cx: int, cy: int) -> list[dict[str, Any]]:
        """Current fate of every non-static stroke in a chunk."""
        rows = self.db.execute(
            "SELECT f.seq, f.fate, f.entity_id FROM stroke_fate f"
            " JOIN stroke s ON s.canvas_id = f.canvas_id AND s.seq = f.seq"
            " WHERE s.canvas_id=? AND s.chunk_x=? AND s.chunk_y=?"
            " ORDER BY f.seq, f.fate = 'fossil'",
            (canvas_id, cx, cy),
        )
        current: dict[int, dict[str, Any]] = {}
        for row in rows:  # fossil sorts after consumed, so the later state wins
            current[row["seq"]] = {
                "seq": row["seq"],
                "fate": row["fate"],
                "entityId": row["entity_id"],
            }
        return list(current.values())

    def static_seqs(self, canvas_id: str, seqs: list[int]) -> set[int]:
        if not seqs:
            return set()
        marks = ",".join("?" * len(seqs))
        fated = {
            row[0]
            for row in self.db.execute(
                f"SELECT DISTINCT seq FROM stroke_fate WHERE canvas_id=? AND seq IN ({marks})",
                (canvas_id, *seqs),
            )
        }
        return set(seqs) - fated
