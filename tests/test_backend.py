import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.db import connect
from app.errors import AppError
from app.main import create_app, create_asgi
from app.models import JoinIn, StrokeIn
from app.repo import Repository


def payload(canvas_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "canvasId": canvas_id,
        "clientId": str(uuid4()),
        "color": "#1E88E5",
        "width": 6,
        "points": [[10, 20, 0.5], [30, 40, 0.8]],
        **changes,
    }


def test_rest_contract(client: TestClient, application: FastAPI) -> None:
    assert client.get("/api/health").json() == {"ok": True}
    response = client.post("/api/canvases")
    assert response.status_code == 201
    created = response.json()
    assert set(created) == {"id", "url", "width", "height"}
    assert len(created["id"]) == 10
    assert created["url"] == f"/c/{created['id']}"
    canvas = client.get(f"/api/canvases/{created['id']}").json()
    assert canvas["strokes"] == [] and canvas["strokeCount"] == 0
    assert canvas["maxStrokes"] == 5000
    assert canvas["brushWidthMin"] == 1 and len(canvas["palette"]) == 8
    repo = application.state.repo
    for _ in range(3):
        repo.insert_stroke(StrokeIn.model_validate(payload(created["id"])), None)
    strokes = client.get(f"/api/canvases/{created['id']}/strokes?afterSeq=1").json()[
        "strokes"
    ]
    assert [s["seq"] for s in strokes] == [2, 3]
    assert set(strokes[0]) == {
        "seq",
        "authorName",
        "color",
        "width",
        "points",
        "createdAt",
    }
    assert client.get("/docs").status_code == 200
    assert client.get("/api/health").headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path,code,status,field",
    [
        ("/api/canvases/no", "INVALID_ID", 400, "canvasId"),
        ("/api/canvases/0123456789", "CANVAS_NOT_FOUND", 404, None),
        (
            "/api/canvases/0123456789/strokes?afterSeq=-1",
            "VALIDATION_ERROR",
            400,
            "afterSeq",
        ),
        (
            "/api/canvases/0123456789/strokes?afterSeq=abc",
            "VALIDATION_ERROR",
            400,
            "afterSeq",
        ),
    ],
)
def test_errors(
    client: TestClient, path: str, code: str, status: int, field: str | None
) -> None:
    response = client.get(path)
    assert response.status_code == status
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["field"] == field


def test_metrics_whitelist(client: TestClient, application: FastAPI) -> None:
    for key in ["pro-click", "pro-click", "stroke-buy-click"]:
        assert client.post(f"/api/metrics/{key}").status_code == 204
    assert client.post("/api/metrics/arbitrary").status_code == 404
    assert dict(application.state.repo.db.execute("SELECT key,value FROM metric")) == {
        "pro-click": 2,
        "stroke-buy-click": 1,
    }


def test_creation_limit_does_not_trust_arbitrary_forwarding_header(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            _env_file=None, db_path=str(tmp_path / "db"), canvas_create_limit="1/60"
        )
    )
    with TestClient(create_asgi(app)) as client:
        assert client.post("/api/canvases").status_code == 201
        assert (
            client.post(
                "/api/canvases", headers={"X-Forwarded-For": "fake"}
            ).status_code
            == 429
        )


def test_limits_and_config_reach_browser(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "db"),
            canvas_width=400,
            canvas_height=300,
            brush_width_min=2,
            brush_width_max=8,
            max_points_per_stroke=50,
            simplify_epsilon=0.7,
            palette="#123456,#ffffff",
        )
    )
    with TestClient(create_asgi(app)) as client:
        created = client.post("/api/canvases").json()
        data = client.get(f"/api/canvases/{created['id']}").json()
        assert data["width"] == 400 and data["height"] == 300
        assert data["palette"] == ["#123456", "#ffffff"]
        assert (
            data["brushWidthMax"] == 8
            and data["maxPointsPerStroke"] == 50
            and data["simplifyEpsilon"] == 0.7
        )


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
    assert (
        JoinIn.model_validate(
            {"canvasId": "0123456789", "name": "  А" + "я" * 40 + "  "}
        ).name
        == "А" + "я" * 29
    )
    assert JoinIn.model_validate({"canvasId": "0123456789", "name": "  "}).name is None


def test_immutable_deduplicated_and_transactional(
    client: TestClient, application: FastAPI
) -> None:
    repo = application.state.repo
    canvas = repo.create_canvas()
    stroke = StrokeIn.model_validate(payload(canvas["id"]))
    first, inserted = repo.insert_stroke(stroke, "Аня")
    assert inserted and first["seq"] == 1
    duplicate, inserted = repo.insert_stroke(stroke, "Другой")
    assert not inserted and duplicate["seq"] == 1 and duplicate["authorName"] == "Аня"
    for sql in ["UPDATE stroke SET width=5", "DELETE FROM stroke"]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.db.execute(sql)
    assert repo.get_canvas(canvas["id"])["stroke_count"] == 1
    repo.db.execute(
        "CREATE TRIGGER fail_counter BEFORE UPDATE ON canvas BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_stroke(StrokeIn.model_validate(payload(canvas["id"])), None)
    assert not repo.db.in_transaction and len(repo.list_strokes(canvas["id"])) == 1


def test_full_canvas_still_accepts_duplicate(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_strokes_per_canvas=1)
    connection = connect(str(tmp_path / "db"))
    try:
        repo = Repository(connection, settings)
        canvas = repo.create_canvas()
        stroke = StrokeIn.model_validate(payload(canvas["id"]))
        repo.insert_stroke(stroke, None)
        assert repo.insert_stroke(stroke, None)[0]["seq"] == 1
        with pytest.raises(AppError, match="Холст заполнен"):
            repo.insert_stroke(StrokeIn.model_validate(payload(canvas["id"])), None)
    finally:
        connection.close()


def test_database_survives_restart(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, db_path=str(tmp_path / "persistent" / "db"))
    first = create_app(settings)
    with TestClient(create_asgi(first)):
        canvas = first.state.repo.create_canvas()
        first.state.repo.insert_stroke(
            StrokeIn.model_validate(payload(canvas["id"])), None
        )
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


def test_realtime_limits_presence_and_errors(
    client: TestClient, application: FastAPI
) -> None:
    realtime = application.state.realtime
    realtime.sio.emit = AsyncMock()
    realtime.sio.enter_room = AsyncMock()
    realtime.sio.leave_room = AsyncMock()
    canvas = application.state.repo.create_canvas()["id"]
    realtime.settings.max_clients_per_canvas = 1
    realtime.settings.max_strokes_per_canvas = 1

    async def scenario() -> None:
        assert (await realtime.add("a", payload(canvas)))["error"][
            "code"
        ] == "NOT_JOINED"
        assert (await realtime.join("a", {"canvasId": canvas, "name": " <img> "}))[
            "online"
        ] == ["<img>"]
        assert (await realtime.join("a", {"canvasId": canvas}))["ok"]
        assert (await realtime.join("b", {"canvasId": canvas}))["error"][
            "code"
        ] == "ROOM_FULL"
        assert (await realtime.add("a", payload("0123456789")))["error"][
            "code"
        ] == "NOT_JOINED"
        assert (await realtime.add("a", payload(canvas, color="blue")))["error"][
            "field"
        ] == "color"
        stroke = payload(canvas)
        assert (await realtime.add("a", stroke)) == {"ok": True, "seq": 1}
        assert (await realtime.add("a", stroke)) == {"ok": True, "seq": 1}
        assert (await realtime.add("a", payload(canvas)))["error"][
            "code"
        ] == "CANVAS_FULL"
        emitted = [call.args[0] for call in realtime.sio.emit.call_args_list]
        assert emitted.count("stroke:new") == 1 and emitted.count("canvas:full") == 1
        new_call = next(
            call
            for call in realtime.sio.emit.call_args_list
            if call.args[0] == "stroke:new"
        )
        assert new_call.kwargs["skip_sid"] == "a"
        for _ in range(30):
            reply = await realtime.add("a", stroke)
        assert reply["error"]["code"] == "RATE_LIMITED"
        await realtime.disconnect("a")
        assert realtime.names(canvas) == [] and "a" not in realtime.stroke_limit.buckets

    asyncio.run(scenario())


def test_real_socketio_protocol_and_payload_limit(client: TestClient) -> None:
    canvas = client.post("/api/canvases").json()["id"]
    with client.websocket_connect(
        "/socket.io/?EIO=4&transport=websocket", headers={"Upgrade": "websocket"}
    ) as ws:
        assert ws.receive_text().startswith("0{")
        ws.send_text("40")
        assert ws.receive_text().startswith("40{")
        ws.send_text("421" + json.dumps(["canvas:join", {"canvasId": canvas}]))
        messages = [ws.receive_text(), ws.receive_text()]
        assert any(m.startswith("431") and json.loads(m[3:])[0]["ok"] for m in messages)
        ws.send_text("422" + json.dumps(["stroke:add", payload(canvas)]))
        assert json.loads(ws.receive_text()[3:])[0] == {"ok": True, "seq": 1}
        ws.send_text("423" + json.dumps(["stroke:add", {"large": "x" * 66000}]))
        ack = ws.receive_text()
        assert (
            ack.startswith("433")
            and json.loads(ack[3:])[0]["error"]["code"] == "PAYLOAD_TOO_LARGE"
        )
    assert client.post("/api/canvases", content=b"x" * 65537).status_code == 413


def test_static_spa_is_explicit(client: TestClient) -> None:
    assert client.get("/").status_code in (200, 503)
    assert client.get("/c/0123456789").status_code in (200, 503)
    assert client.get("/api/missing").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/../app/schema.sql").status_code == 404
