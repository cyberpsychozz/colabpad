"""Sprites and entities. The simulation owns live state; main is the only writer."""

import json
import sqlite3
from typing import Any

from app.repo.canvases import now_ms


class EntityRepo:
    db: sqlite3.Connection

    def insert_sprite(
        self, sprite_id: str, job_id: str | None, cutout: str, sheet: str, manifest: dict[str, Any]
    ) -> None:
        self.db.execute(
            "INSERT INTO sprite(id, job_id, cutout_path, sheet_path, manifest) VALUES(?,?,?,?,?)",
            (sprite_id, job_id, cutout, sheet, json.dumps(manifest)),
        )

    def insert_entity(self, entity: dict[str, Any]) -> None:
        row = {**entity, "born_at": entity.get("born_at") or now_ms()}
        for name in ("label", "params"):
            row[name] = json.dumps(row[name], ensure_ascii=False)
        columns = ", ".join(row)
        self.db.execute(
            f"INSERT INTO entity({columns}) VALUES({', '.join('?' * len(row))})",
            tuple(row.values()),
        )

    @staticmethod
    def _entity(row: sqlite3.Row) -> dict[str, Any]:
        """The wire shape of an entity, shared by REST and world events."""
        params = json.loads(row["params"] or "{}")
        return {
            "id": row["id"],
            "type": row["archetype"],
            "label": json.loads(row["label"] or "{}"),
            "x": row["x"],
            "y": row["y"],
            "facing": row["facing"],
            "state": row["state"],
            "hp": row["hp"],
            "w": params.get("w"),
            "h": params.get("h"),
            "variant": params.get("variant"),
            "scale": params.get("scale", 1),
            "sprite": {
                "sheet": f"/sprites/{row['sheet_path']}",
                "manifest": json.loads(row["manifest"] or "{}"),
            },
        }

    _SELECT = (
        "SELECT e.*, s.sheet_path, s.manifest FROM entity e"
        " LEFT JOIN sprite s ON s.id = e.sprite_id"
    )

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        row = self.db.execute(f"{self._SELECT} WHERE e.id=?", (entity_id,)).fetchone()
        return self._entity(row) if row else None

    def living_in_rect(
        self, canvas_id: str, x0: float, y0: float, x1: float, y1: float
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            f"{self._SELECT} WHERE e.canvas_id=? AND e.died_at IS NULL"
            " AND e.x>=? AND e.x<? AND e.y>=? AND e.y<? ORDER BY e.born_at, e.id",
            (canvas_id, x0, x1, y0, y1),
        )
        return [self._entity(row) for row in rows]

    def count_living(self, canvas_id: str) -> int:
        return self.db.execute(
            "SELECT count(*) FROM entity WHERE canvas_id=? AND died_at IS NULL", (canvas_id,)
        ).fetchone()[0]
