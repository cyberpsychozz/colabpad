"""Counter SQL for whitelisted product metrics."""

import sqlite3


class MetricRepo:
    db: sqlite3.Connection

    def increment_metric(self, key: str) -> None:
        self.db.execute(
            "INSERT INTO metric(key,value) VALUES(?,1) ON CONFLICT(key) DO UPDATE SET value=value+1",
            (key,),
        )
