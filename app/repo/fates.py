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

    def chunk_fossils(self, canvas_id: str, x0: float, y0: float, x1: float, y1: float) -> list[dict[str, Any]]:
        """Fossils lying in a rectangle: where the entity died, with its strokes and shift."""
        rows = self.db.execute(
            "SELECT id, death_x - origin_x AS dx, death_y - origin_y AS dy FROM entity"
            " WHERE canvas_id=? AND died_at IS NOT NULL AND death_x>=? AND death_x<? AND death_y>=? AND death_y<?",
            (canvas_id, x0, x1, y0, y1),
        ).fetchall()
        fossils = []
        for row in rows:
            seqs = [
                r[0]
                for r in self.db.execute(
                    "SELECT seq FROM stroke_fate WHERE canvas_id=? AND entity_id=? AND fate='fossil'",
                    (canvas_id, row["id"]),
                )
            ]
            if seqs:
                fossils.append({
                    "entityId": row["id"], "dx": row["dx"] or 0, "dy": row["dy"] or 0,
                    "strokes": self.strokes_by_seq(canvas_id, seqs),  # type: ignore[attr-defined]
                })  # fmt: skip
        return fossils
