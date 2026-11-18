"""SQLite online backup, retaining the latest fourteen successful daily copies, plus a
mirror of the baked sprites (DATA_DIR/sprites -> backups/sprites).

Sprite files never change once written, so the mirror only copies new folders and drops
the ones the retention job has removed; it is cheap to run every day.
"""

import argparse
import os
import shutil
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


def mirror_sprites(source: Path, destination: Path) -> tuple[int, int]:
    """Make `destination` hold the same sprite folders as `source`. Returns (copied, removed)."""
    if not source.is_dir():
        return 0, 0
    destination.mkdir(parents=True, exist_ok=True)
    present = {folder.name for folder in source.iterdir() if folder.is_dir()}
    copied = removed = 0
    for name in sorted(present):
        target = destination / name
        if not target.is_dir() or {f.name for f in (source / name).iterdir()} - {f.name for f in target.iterdir()}:
            partial = destination / f".{name}.tmp"
            shutil.rmtree(partial, ignore_errors=True)
            shutil.copytree(source / name, partial)
            shutil.rmtree(target, ignore_errors=True)
            partial.replace(target)
            copied += 1
    for folder in destination.iterdir():
        if folder.is_dir() and folder.name not in present:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return copied, removed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, default=Path("/var/lib/collabpad/collabpad.db")
    )
    parser.add_argument(
        "--directory", type=Path, default=Path("/var/lib/collabpad/backups")
    )
    parser.add_argument(
        "--sprites", type=Path, default=Path("/var/lib/collabpad/sprites")
    )
    args = parser.parse_args()
    print(backup(args.db, args.directory))
    copied, removed = mirror_sprites(args.sprites, args.directory / "sprites")
    print(f"sprites: {copied} copied, {removed} removed")
