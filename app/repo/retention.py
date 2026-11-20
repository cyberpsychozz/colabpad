"""Daily retention (docs/brief.md §5). Strokes, fates and payments are never touched."""

import sqlite3
from typing import Any

DAY_MS = 86_400_000


class RetentionRepo:
    db: sqlite3.Connection

    def purge_old_rows(self, now: int, days: dict[str, int]) -> dict[str, int]:
        """Drop ALIVE jobs (with their raw VLM output), old world events and expired sign-in codes."""
        counts = {}
        counts["alive_job"] = self.db.execute(
            "DELETE FROM alive_job WHERE created_at < ?",
            (now - days["alive_job"] * DAY_MS,),
        ).rowcount
        counts["world_event"] = self.db.execute(
            "DELETE FROM world_event WHERE at < ?", (now - days["world_event"] * DAY_MS,)
        ).rowcount
        counts["login_code"] = self.db.execute(
            "DELETE FROM login_code WHERE expires_at <= ?", (now,)
        ).rowcount
        return counts

    def expired_sprites(self, now: int, days_after_death: int) -> list[dict[str, Any]]:
        """Sprites made by ALIVE whose every entity (calves share their parent's sprite) died
        more than `days_after_death` ago. Built-in sprites (no job) are never expired."""
        rows = self.db.execute(
            "SELECT s.id, s.cutout_path, s.sheet_path FROM sprite s JOIN entity e ON e.sprite_id = s.id"
            " WHERE s.job_id IS NOT NULL AND s.sheet_path IS NOT NULL"
            " GROUP BY s.id HAVING count(e.died_at) = count(*) AND max(e.died_at) < ?",
            (now - days_after_death * DAY_MS,),
        ).fetchall()
        return [dict(row) for row in rows]

    def forget_sprite_files(self, sprite_id: str) -> None:
        self.db.execute(
            "UPDATE sprite SET cutout_path = NULL, sheet_path = NULL WHERE id = ?", (sprite_id,)
        )
