"""ALIVE jobs and daily quotas."""

import json
import sqlite3
from typing import Any

from app.repo.canvases import now_ms


class JobRepo:
    db: sqlite3.Connection

    def create_job(
        self, job_id: str, canvas_id: str, session_id: str, lasso: list[Any], seqs: list[int]
    ) -> None:
        self.db.execute(
            "INSERT INTO alive_job(id, canvas_id, session_id, lasso, stroke_seqs, status,"
            " timings, created_at) VALUES(?,?,?,?,?,'queued','{}',?)",
            (job_id, canvas_id, session_id, json.dumps(lasso), json.dumps(seqs), now_ms()),
        )

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "vlm_result", "chosen_type", "error_code", "timings"}
        assert fields and set(fields) <= allowed
        values = [
            json.dumps(value) if isinstance(value, dict | list) else value
            for value in fields.values()
        ]
        self.db.execute(
            f"UPDATE alive_job SET {', '.join(f'{name}=?' for name in fields)} WHERE id=?",
            (*values, job_id),
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM alive_job WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def quota_used(self, subject: str, day: str) -> int:
        row = self.db.execute(
            "SELECT alive_used FROM quota WHERE subject=? AND day=?", (subject, day)
        ).fetchone()
        return row[0] if row else 0

    def spend_quota(self, subject: str, day: str) -> None:
        self.db.execute(
            "INSERT INTO quota(subject, day, alive_used) VALUES(?,?,1)"
            " ON CONFLICT(subject, day) DO UPDATE SET alive_used=alive_used+1",
            (subject, day),
        )
