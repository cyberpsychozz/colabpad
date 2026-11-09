"""One connection, one process, explicit synchronous transactions."""

import sqlite3
from pathlib import Path


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(Path(path).expanduser()) if path != ":memory:" else path,
        check_same_thread=False,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(Path(__file__).with_name("schema.sql").read_text())
    return connection
