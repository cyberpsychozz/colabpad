"""One connection, one process, explicit synchronous transactions."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

# The only migration: 2 → 3 moved payments from YooKassa to Robokassa (docs/spec.md §5).
# YooKassa's payment rows are kept, renamed, because payment rows are never deleted.
MIGRATIONS = {
    2: """
        ALTER TABLE payment RENAME TO payment_yookassa;
        ALTER TABLE subscription DROP COLUMN yk_payment_method_id;
        ALTER TABLE subscription ADD COLUMN parent_inv_id INTEGER;
    """,
}


class LegacyDatabase(RuntimeError):
    pass


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
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    has_tables = connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    if version in MIGRATIONS and has_tables:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in filter(str.strip, MIGRATIONS[version].split(";")):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            connection.close()
            raise
        version = SCHEMA_VERSION
    if version != SCHEMA_VERSION and has_tables:
        connection.close()
        # Anything else has no migration: the old file is discarded by a human, never by us.
        raise LegacyDatabase(
            f"{path} was created by another version of CollabPad. "
            "Move or delete it and start again (see docs/deploy.md)."
        )
    connection.executescript(Path(__file__).with_name("schema.sql").read_text())
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return connection
