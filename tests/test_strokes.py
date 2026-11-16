import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.db import LegacyDatabase, connect
from app.main import create_app, create_asgi
from app.models import chunk_of
from tests.conftest import BOUNDS, CHUNK, payload, stroke_in


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"color": "blue"}, "color"),
        ({"color": "#1234567"}, "color"),
        ({"width": 0}, "width"),
        ({"width": 100}, "width"),
        ({"width": True}, "width"),
        ({"width": "6"}, "width"),
        ({"width": float("nan")}, "width"),
        ({"width": float("inf")}, "width"),
        ({"clientId": "not-uuid"}, "clientId"),
        ({"canvasId": "no"}, "canvasId"),
        ({"points": [[10, 10, 0.5]]}, "points"),
        ({"points": [[10, 10, 0.5]] * 2001}, "points"),
        ({"points": [[19457, 50, 0.5], [19456, 50, 0.5]]}, "points"),
        ({"points": [[0, -1, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 11265, 0.5], [1, 11264, 0.5]]}, "points"),
        ({"points": [[0, 0, 1.1], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 0, -0.1], [1, 1, 0.5]]}, "points"),
        ({"points": [[float("nan"), 0, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, float("inf"), 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [["1", 0, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[True, 0, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 0], [1, 1]]}, "points"),
        # Crosses the border between chunks (0,0) and (1,0).
        ({"points": [[1000, 5, 0.5], [1100, 5, 0.5]]}, "points"),
    ],
)
def test_invalid_strokes(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        stroke_in(**changes)
    assert caught.value.errors()[0]["loc"][0] == field


def test_chunk_of_includes_borders_and_world_edges() -> None:
    assert chunk_of([(10, 20, 0.5), (30, 40, 0.5)], CHUNK, BOUNDS) == (0, 0)
    assert chunk_of([(1024, 0, 0.5), (2048, 1024, 0.5)], CHUNK, BOUNDS) == (1, 0)
    assert chunk_of([(900, 5, 0.5), (1024, 5, 0.5)], CHUNK, BOUNDS) == (0, 0)
    # A dot exactly on the far world edge belongs to the last chunk.
    assert chunk_of([(19456, 11264, 1), (19456, 11264, 1)], CHUNK, BOUNDS) == (18, 10)
    assert chunk_of([(1000, 5, 0.5), (1100, 5, 0.5)], CHUNK, BOUNDS) is None
    assert len(stroke_in(width=40, points=[[19456, 11264, 1], [19000, 11000, 0]]).points) == 2


def test_stroke_insert_is_immutable_idempotent_and_transactional(
    client: TestClient, application: FastAPI
) -> None:
    repo = application.state.repo
    stroke = stroke_in(points=[[1500, 2100, 0.5], [1600, 2200, 0.5]])
    first, inserted = repo.insert_stroke(stroke, (1, 2), "session-1")
    assert inserted and first["seq"] == 1 and (first["cx"], first["cy"]) == (1, 2)
    duplicate, inserted = repo.insert_stroke(stroke, (1, 2), "other")
    assert not inserted and duplicate["seq"] == 1 and duplicate["createdAt"] == first["createdAt"]
    row = repo.db.execute("SELECT * FROM stroke").fetchone()
    assert (row["chunk_x"], row["chunk_y"], row["session_id"]) == (1, 2, "session-1")
    for sql in ["UPDATE stroke SET width=5", "DELETE FROM stroke"]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.db.execute(sql)
    assert repo.get_canvas("world")["stroke_count"] == 1
    repo.db.execute(
        "CREATE TRIGGER fail_counter BEFORE UPDATE ON canvas BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_stroke(stroke_in(), (0, 0))
    assert not repo.db.in_transaction
    assert len(repo.chunk_strokes("world", 1, 2)) == 1
    assert repo.chunk_strokes("world", 0, 0) == []


def test_fates_are_append_only_and_forward_only(
    client: TestClient, application: FastAPI, add_stroke: Callable[..., dict[str, Any]]
) -> None:
    repo = application.state.repo
    seqs = [add_stroke()["seq"] for _ in range(4)]
    repo.insert_fates("world", seqs[:2], "consumed", "entity-1")
    repo.insert_fates("world", [seqs[2]], "destroyed")
    assert repo.static_seqs("world", seqs) == {seqs[3]}
    for sql in ["UPDATE stroke_fate SET fate='fossil'", "DELETE FROM stroke_fate"]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.db.execute(sql)
    for seq, fate in [
        (seqs[0], "consumed"),  # twice
        (seqs[0], "destroyed"),  # consumed -> destroyed
        (seqs[2], "fossil"),  # destroyed -> fossil
        (seqs[2], "consumed"),  # destroyed -> consumed
        (seqs[3], "fossil"),  # static -> fossil
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_fates("world", [seq], fate)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_fates("world", [999], "destroyed")  # no such stroke
    with pytest.raises(sqlite3.IntegrityError):
        repo.db.execute(
            "INSERT INTO stroke_fate(canvas_id,seq,fate,at) VALUES('world',?,'burnt',0)",
            (seqs[3],),
        )
    repo.insert_fates("world", [seqs[0]], "fossil", "entity-1")
    assert repo.chunk_fates("world", 0, 0) == [
        {"seq": seqs[0], "fate": "fossil", "entityId": "entity-1"},
        {"seq": seqs[1], "fate": "consumed", "entityId": "entity-1"},
        {"seq": seqs[2], "fate": "destroyed", "entityId": None},
    ]
    # Stroke rows are untouched by any of this.
    assert repo.db.execute("SELECT count(*) FROM stroke").fetchone()[0] == 4


def test_shared_world_is_created_once_and_follows_config(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "persistent")
    first = create_app(settings)
    with TestClient(create_asgi(first)):
        world = first.state.repo.get_canvas("world")
        assert (world["kind"], world["link_role"], world["owner_user_id"]) == (
            "world",
            "editor",
            None,
        )
        assert (world["bounds_w"], world["bounds_h"]) == BOUNDS
        first.state.repo.insert_stroke(stroke_in(), (0, 0))
    second = create_app(settings)
    second.state.config.limits["world"]["bounds_w"] = 20480
    with TestClient(create_asgi(second)):
        world = second.state.repo.get_canvas("world")
        # Same world and strokes after a restart; bounds follow the config.
        assert world["stroke_count"] == 1 and world["bounds_w"] == 20480
        assert second.state.repo.db.execute("SELECT count(*) FROM canvas").fetchone()[0] == 1


def test_sqlite_pragmas_constraints_and_legacy_guard(
    client: TestClient, application: FastAPI, tmp_path: Path
) -> None:
    db = application.state.repo.db
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # E-mail is the only personal data stored anywhere.
    columns = {
        row[1]
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for row in db.execute(f"PRAGMA table_info({table})")
    }
    assert not {"ip", "user_agent", "password", "phone"} & columns
    for sql in [
        "INSERT INTO stroke(canvas_id,seq,client_id,color,width,points,chunk_x,chunk_y,created_at) VALUES('absent',1,'x','#000000',1,'[]',0,0,0)",
        "INSERT INTO canvas(id,kind,link_role,bounds_w,bounds_h,created_at) VALUES('p','private','none',1,1,0)",
        "INSERT INTO canvas(id,kind,link_role,bounds_w,bounds_h,created_at) VALUES('q','world','owner',1,1,0)",
        "INSERT INTO app_user(id,email,locale,created_at) VALUES('u','a@b.c','de',0)",
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as old:
        old.execute("CREATE TABLE canvas (id TEXT PRIMARY KEY, width INTEGER)")
    with pytest.raises(LegacyDatabase):
        connect(str(legacy))
