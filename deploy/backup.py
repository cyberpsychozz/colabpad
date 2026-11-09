"""SQLite online backup, retaining the latest fourteen successful daily copies."""

import argparse
import os
import sqlite3
from datetime import date
from pathlib import Path


def backup(source: Path, directory: Path, keep: int = 14) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"collabpad-{date.today().isoformat()}.db"
    temporary = destination.with_suffix(".tmp")
    with (
        sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as incoming,
        sqlite3.connect(temporary) as outgoing,
    ):
        incoming.backup(outgoing)
        if outgoing.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    for old in sorted(directory.glob("collabpad-????-??-??.db"), reverse=True)[keep:]:
        old.unlink()
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, default=Path("/var/lib/collabpad/collabpad.db")
    )
    parser.add_argument(
        "--directory", type=Path, default=Path("/var/lib/collabpad/backups")
    )
    args = parser.parse_args()
    print(backup(args.db, args.directory))
