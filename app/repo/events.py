"""World events involving the caller's own entities (GET /api/me/events)."""

import json
import sqlite3
from typing import Any


class EventRepo:
    db: sqlite3.Connection

    def my_events(self, session: dict[str, Any], since: int, limit: int = 100) -> list[dict[str, Any]]:
        """Events after `since` (unix ms) whose actors include an entity this person brought
        to life, from this session or any session of the same account, or a child of one."""
        rows = self.db.execute(
            "WITH mine AS ("
            "  SELECT e.id, e.label, e.archetype FROM entity e JOIN session s ON s.id = e.summoner_session_id"
            "  WHERE s.id = ? OR (? IS NOT NULL AND s.user_id = ?)"
            "  UNION"
            "  SELECT c.id, c.label, c.archetype FROM entity c JOIN entity p ON p.id = c.parent_id"
            "  JOIN session s ON s.id = p.summoner_session_id"
            "  WHERE s.id = ? OR (? IS NOT NULL AND s.user_id = ?)"
            ")"
            " SELECT w.id, w.canvas_id, w.at, w.type, w.payload, min(m.id) AS entity_id, m.label, m.archetype"
            " FROM world_event w JOIN json_each(w.actors) a JOIN mine m ON m.id = a.value"
            " WHERE w.at > ? GROUP BY w.id ORDER BY w.at, w.id LIMIT ?",
            (session["id"], session["user_id"], session["user_id"],
             session["id"], session["user_id"], session["user_id"], since, limit),
        ).fetchall()  # fmt: skip
        return [
            {
                "id": row["id"],
                "canvasId": row["canvas_id"],
                "at": row["at"],
                "type": row["type"],
                "entity": {
                    "id": row["entity_id"],
                    "type": row["archetype"],
                    "label": json.loads(row["label"] or "{}"),
                },
                "payload": json.loads(row["payload"] or "{}"),
            }
            for row in rows
        ]
