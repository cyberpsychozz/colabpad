import json
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import StrokeIn
from deploy.backup import backup


def ack(ws: Any, number: int, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    ws.send_text(f"42{number}" + json.dumps([event, payload]))
    while True:
        message = ws.receive_text()
        if message == "2":
            ws.send_text("3")
        elif message.startswith(f"43{number}["):
            return json.loads(message[len(f"43{number}") :])[0]


def test_fifty_rooms_and_ten_collaborators(client: TestClient) -> None:
    ids = [client.post("/api/canvases").json()["id"] for _ in range(50)]
    with ExitStack() as stack:
        sockets = []
        for i in range(59):
            ws = stack.enter_context(
                client.websocket_connect(
                    "/socket.io/?EIO=4&transport=websocket",
                    headers={"Upgrade": "websocket"},
                )
            )
            assert ws.receive_text().startswith("0")
            ws.send_text("40")
            assert ws.receive_text().startswith("40")
            canvas_id = ids[i] if i < 50 else ids[0]
            joined = ack(ws, 1, "canvas:join", {"canvasId": canvas_id})
            assert joined["ok"]
            sockets.append(ws)
        assert len(joined["online"]) == 10
        for i in [0, *range(50, 59)]:
            saved = ack(
                sockets[i],
                2,
                "stroke:add",
                {
                    "canvasId": ids[0],
                    "clientId": str(uuid4()),
                    "color": "#123456",
                    "width": 5,
                    "points": [[0, 0, 0.5], [100, 100, 0.5]],
                },
            )
            assert saved["ok"]
        strokes = client.get(f"/api/canvases/{ids[0]}").json()["strokes"]
        assert [stroke["seq"] for stroke in strokes] == list(range(1, 11))
        assert client.get(f"/api/canvases/{ids[1]}").json()["strokes"] == []


def test_five_thousand_stroke_load_and_online_backup(
    client: TestClient, application: FastAPI, tmp_path: Path, record_property: Any
) -> None:
    repo = application.state.repo
    canvas = repo.create_canvas()
    for i in range(5000):
        stroke = StrokeIn.model_validate(
            {
                "canvasId": canvas["id"],
                "clientId": str(uuid4()),
                "color": "#123456",
                "width": 3,
                "points": [[(i + j) % 1600, (i + j) % 1000, 0.5] for j in range(12)],
            }
        )
        repo.insert_stroke(stroke, None)
    started = time.perf_counter()
    data = client.get(f"/api/canvases/{canvas['id']}")
    elapsed = time.perf_counter() - started
    assert data.status_code == 200 and len(data.json()["strokes"]) == 5000
    record_property("load_5000_seconds", round(elapsed, 4))
    print(f"\n5000 strokes / 12 points: REST {elapsed:.3f}s, {len(data.content)} bytes")
    copied = backup(Path(application.state.settings.db_path), tmp_path / "backups")
    with sqlite3.connect(copied) as db:
        assert db.execute("SELECT count(*) FROM stroke").fetchone()[0] == 5000
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
