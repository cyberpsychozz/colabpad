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
            **({"mode": "scripted_visual", "plan": params["plan"], "origin": params["origin"]}
               if params.get("mode") == "scripted_visual" else {}),
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

    def sim_rows(self, canvas_id: str | None = None, entity_id: str | None = None) -> list[dict[str, Any]]:
        """Living entities in the shape the simulation loads."""
        where, args = ["died_at IS NULL"], []
        if canvas_id:
            where.append("canvas_id=?"); args.append(canvas_id)
        if entity_id:
            where.append("id=?"); args.append(entity_id)
        rows = self.db.execute(
            f"SELECT * FROM entity WHERE {' AND '.join(where)} ORDER BY born_at, id", args
        )
        return [
            {
                "id": row["id"], "canvas": row["canvas_id"], "type": row["archetype"],
                "label": json.loads(row["label"] or "{}"), "x": row["x"], "y": row["y"],
                "facing": row["facing"], "state": row["state"], "hp": row["hp"],
                "hunger": row["hunger"], "thirst": row["thirst"], "energy": row["energy"],
                "params": json.loads(row["params"] or "{}"), "parent_id": row["parent_id"],
                "bound_to": row["bound_to"], "sprite_id": row["sprite_id"],
            }  # fmt: skip
            for row in rows
        ]

    def save_snapshot(self, rows: list[dict[str, Any]]) -> None:
        """Live state from the simulation, in one transaction."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.executemany(
                "UPDATE entity SET x=?, y=?, vx=?, vy=?, facing=?, state=?, hp=?, hunger=?,"
                " thirst=?, energy=?, bound_to=?,"
                " params=json_set(params, '$.data', json(?), '$.age', ?)"
                " WHERE id=? AND died_at IS NULL",
                [
                    (r["x"], r["y"], r["vx"], r["vy"], r["facing"], r["state"], r["hp"], r["hunger"],
                     r["thirst"], r["energy"], r["bound_to"],
                     json.dumps(r["data"], ensure_ascii=False), r["age"], r["id"])
                    for r in rows
                ],  # fmt: skip
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def mark_dead(self, entity_id: str, x: float, y: float) -> dict[str, Any] | None:
        """Record the death; returns canvas, origin and the strokes the entity was made of."""
        row = self.db.execute(
            "SELECT canvas_id, origin_x, origin_y FROM entity WHERE id=? AND died_at IS NULL",
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        self.db.execute(
            "UPDATE entity SET died_at=?, death_x=?, death_y=?, state='dead' WHERE id=?",
            (now_ms(), x, y, entity_id),
        )
        seqs = [
            r[0]
            for r in self.db.execute(
                "SELECT seq FROM stroke_fate WHERE canvas_id=? AND entity_id=? AND fate='consumed'"
                " ORDER BY seq",
                (row["canvas_id"], entity_id),
            )
        ]
        return {"canvas": row["canvas_id"], "seqs": seqs,
                "dx": x - (row["origin_x"] or x), "dy": y - (row["origin_y"] or y)}  # fmt: skip
