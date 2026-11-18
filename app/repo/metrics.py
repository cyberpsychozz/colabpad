"""Counter SQL for whitelisted product metrics."""

import sqlite3


class MetricRepo:
    db: sqlite3.Connection

    def increment_metric(self, key: str) -> None:
        self.db.execute(
            "INSERT INTO metric(key,value) VALUES(?,1) ON CONFLICT(key) DO UPDATE SET value=value+1",
            (key,),
        )

    def add_metric(self, key: str, amount: int) -> None:
        self.db.execute(
            "INSERT INTO metric(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value",
            (key, amount),
        )

    def get_metric(self, key: str) -> int:
        row = self.db.execute("SELECT value FROM metric WHERE key=?", (key,)).fetchone()
        return row[0] if row else 0
