from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, create_asgi


def test_canvas_and_chunk_contract(
    client: TestClient, application: FastAPI, add_stroke: Callable[..., dict[str, Any]]
) -> None:
    assert client.get("/api/health").json() == {"ok": True}
    world = client.get("/api/canvases/world").json()
    assert set(world) == {
        "id", "kind", "title", "role", "frozen", "bounds", "spawn", "limits", "palette",
    }  # fmt: skip
    assert world["kind"] == "world" and world["role"] == "editor" and not world["frozen"]
    assert world["bounds"] == {"w": 19456, "h": 11264}
    assert world["spawn"] == {"x": 9728, "y": 5632}
    assert world["limits"]["chunkSize"] == 1024 and world["limits"]["maxChunks"] == 12
    assert world["limits"]["brushWidthMin"] == 1 and len(world["palette"]) == 8

    empty = client.get("/api/canvases/world/chunks/0/0").json()
    assert empty == {
        "strokes": [], "fates": [], "entities": [], "fossils": [], "lastSeq": 0, "tick": 0,
    }  # fmt: skip
    for _ in range(3):
        add_stroke()
    add_stroke(points=[[1500, 30, 0.5], [1600, 40, 0.5]])
    application.state.repo.insert_fates("world", [2], "destroyed")
    chunk = client.get("/api/canvases/world/chunks/0/0?afterSeq=1").json()
    assert [s["seq"] for s in chunk["strokes"]] == [2, 3]
    assert set(chunk["strokes"][0]) == {
        "seq", "color", "width", "points", "cx", "cy", "createdAt",
    }  # fmt: skip
    assert chunk["fates"] == [{"seq": 2, "fate": "destroyed", "entityId": None}]
    # lastSeq is the canvas-wide watermark, not the chunk's own maximum.
    assert chunk["lastSeq"] == 4
    other = client.get("/api/canvases/world/chunks/1/0").json()
    assert [s["seq"] for s in other["strokes"]] == [4] and other["fates"] == []
    assert client.get("/docs").status_code == 200
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert client.post("/api/canvases", json={"title": "x"}).status_code == 401


@pytest.mark.parametrize(
    "path,code,status,field",
    [
        ("/api/canvases/no", "VALIDATION_FAILED", 400, "canvasId"),
        ("/api/canvases/0123456789", "NOT_FOUND", 404, None),
        ("/api/canvases/world/chunks/19/0", "NOT_FOUND", 404, None),
        ("/api/canvases/world/chunks/0/11", "NOT_FOUND", 404, None),
        ("/api/canvases/world/chunks/-1/0", "VALIDATION_FAILED", 400, "cx"),
        ("/api/canvases/world/chunks/0/0?afterSeq=-1", "VALIDATION_FAILED", 400, "afterSeq"),
        ("/api/canvases/world/chunks/0/0?afterSeq=abc", "VALIDATION_FAILED", 400, "afterSeq"),
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
    for key in ["plus_clicked", "plus_clicked", "adblock_gate_shown"]:
        assert client.post(f"/api/metrics/{key}").status_code == 204
    assert client.post("/api/metrics/arbitrary").status_code == 404
    assert dict(application.state.repo.db.execute("SELECT key,value FROM metric")) == {
        "plus_clicked": 2,
        "adblock_gate_shown": 1,
    }


def test_limits_and_config_reach_browser(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            brush_width_min=2,
            brush_width_max=8,
            max_points_per_stroke=50,
            simplify_epsilon=0.7,
            palette="#123456,#ffffff",
        )
    )
    with TestClient(create_asgi(app)) as client:
        data = client.get("/api/canvases/world").json()
        assert data["palette"] == ["#123456", "#ffffff"]
        assert data["limits"]["brushWidthMax"] == 8
        assert data["limits"]["maxPointsPerStroke"] == 50
        assert data["limits"]["simplifyEpsilon"] == 0.7


def test_static_spa_and_legacy_redirect(client: TestClient) -> None:
    assert client.get("/").status_code in (200, 503)
    assert client.get("/w").status_code in (200, 503)
    legacy = client.get("/c/0123456789", follow_redirects=False)
    assert legacy.status_code == 302 and legacy.headers["location"] == "/w"
    assert client.get("/api/missing").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/../app/schema.sql").status_code == 404
