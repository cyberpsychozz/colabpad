import json
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deploy.backup import backup
from tests.conftest import private_canvas, stroke_in


def ack(
    ws: Any,
    number: int,
    event: str,
    payload: dict[str, Any],
    seen: list[int] | None = None,
) -> dict[str, Any]:
    """Emit with an ack id and wait for the reply, noting stroke:new seqs on the way."""
    ws.send_text(f"42{number}" + json.dumps([event, payload]))
    while True:
        message = ws.receive_text()
        if message == "2":
            ws.send_text("3")
        elif message.startswith(f"43{number}["):
            return json.loads(message[len(f"43{number}") :])[0]
        elif message.startswith('42["stroke:new"') and seen is not None:
            seen.append(json.loads(message[2:])[1]["seq"])


def test_fifty_rooms_and_ten_collaborators(
    client: TestClient, application: FastAPI
) -> None:
    repo = application.state.repo
    ids = [private_canvas(repo) for _ in range(50)]
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
            # Half of the collaborators on the busy canvas watch another chunk.
            chunks = [[5, 5]] if i >= 55 else [[0, 0], [1, 0]]
            joined = ack(ws, 1, "viewport:set", {"canvasId": canvas_id, "chunks": chunks})
            assert joined["ok"]
            sockets.append(ws)
        seen: dict[int, list[int]] = {50: [], 58: []}
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
                seen.get(i),
            )
            assert saved["ok"]
        # A subscriber of the chunk receives everyone else's strokes; a watcher of
        # a distant chunk of the same canvas receives none.
        for i in seen:
            view = {"canvasId": ids[0], "chunks": [[7, 7]]}
            assert ack(sockets[i], 3, "viewport:set", view, seen[i])["ok"]
        assert seen == {50: [1, *range(3, 11)], 58: []}
        strokes = client.get(f"/api/canvases/{ids[0]}/chunks/0/0").json()["strokes"]
        assert [stroke["seq"] for stroke in strokes] == list(range(1, 11))
        assert client.get(f"/api/canvases/{ids[1]}/chunks/0/0").json()["strokes"] == []


def test_five_thousand_strokes_in_a_chunk_and_online_backup(
    client: TestClient, application: FastAPI, tmp_path: Path
) -> None:
    repo = application.state.repo
    for i in range(5000):
        points = [[(i + j) % 1000, (i * 7 + j) % 1000, 0.5] for j in range(12)]
        repo.insert_stroke(stroke_in(width=3, points=points), (0, 0))
    started = time.perf_counter()
    data = client.get("/api/canvases/world/chunks/0/0")
    elapsed = time.perf_counter() - started
    assert data.status_code == 200 and len(data.json()["strokes"]) == 5000
    print(f"\n5000 strokes / 12 points: REST {elapsed:.3f}s, {len(data.content)} bytes")
    copied = backup(Path(application.state.settings.db_path), tmp_path / "backups")
    with sqlite3.connect(copied) as db:
        assert db.execute("SELECT count(*) FROM stroke").fetchone()[0] == 5000
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
