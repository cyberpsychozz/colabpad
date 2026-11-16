import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app, create_asgi
from app.models import StrokeIn


def payload(canvas_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "canvasId": canvas_id,
        "clientId": str(uuid4()),
        "color": "#1E88E5",
        "width": 6,
        "points": [[10, 20, 0.5], [30, 40, 0.8]],
        **changes,
    }


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
        ({"points": [[10, 10, 0.5]]}, "points"),
        ({"points": [[10, 10, 0.5]] * 2001}, "points"),
        ({"points": [[2000, 50, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, -1, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 1001, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 0, 1.1], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 0, -0.1], [1, 1, 0.5]]}, "points"),
        ({"points": [[float("nan"), 0, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, float("inf"), 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [["1", 0, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[True, 0, 0.5], [1, 1, 0.5]]}, "points"),
        ({"points": [[0, 0], [1, 1]]}, "points"),
    ],
)
def test_invalid_strokes(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        StrokeIn.model_validate(payload("0123456789", **changes))
    assert caught.value.errors()[0]["loc"][0] == field


def test_boundary_points_and_click() -> None:
    stroke = StrokeIn.model_validate(
        payload("0123456789", width=40, points=[[1600, 1000, 1], [0, 0, 0]])
    )
    assert len(stroke.points) == 2


def test_immutable_deduplicated_and_transactional(
    client: TestClient, application: FastAPI
) -> None:
    repo = application.state.repo
    canvas = repo.create_canvas()
    stroke = StrokeIn.model_validate(payload(canvas["id"]))
    first, inserted = repo.insert_stroke(stroke)
    assert inserted and first["seq"] == 1
    duplicate, inserted = repo.insert_stroke(stroke)
    assert not inserted and duplicate["seq"] == 1
    for sql in ["UPDATE stroke SET width=5", "DELETE FROM stroke"]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.db.execute(sql)
    assert repo.get_canvas(canvas["id"])["stroke_count"] == 1
    repo.db.execute(
        "CREATE TRIGGER fail_counter BEFORE UPDATE ON canvas BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_stroke(StrokeIn.model_validate(payload(canvas["id"])))
    assert not repo.db.in_transaction and len(repo.list_strokes(canvas["id"])) == 1


def test_database_survives_restart(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, db_path=str(tmp_path / "persistent" / "db"))
    first = create_app(settings)
    with TestClient(create_asgi(first)):
        canvas = first.state.repo.create_canvas()
        first.state.repo.insert_stroke(StrokeIn.model_validate(payload(canvas["id"])))
    with TestClient(create_asgi(create_app(settings))) as client:
        assert client.get(f"/api/canvases/{canvas['id']}").json()["strokeCount"] == 1


def test_sqlite_privacy_and_pragmas(client: TestClient, application: FastAPI) -> None:
    db = application.state.repo.db
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    columns = [
        row[1]
        for table in ("stroke", "canvas", "metric")
        for row in db.execute(f"PRAGMA table_info({table})")
    ]
    assert not {"ip", "email", "user_agent", "name"} & set(columns)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO stroke(canvas_id,seq,client_id,color,width,points,created_at) VALUES('absent',1,'x','#000000',1,'[]',0)"
        )
