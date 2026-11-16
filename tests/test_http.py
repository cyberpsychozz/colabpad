from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_rest_contract(client: TestClient, application: FastAPI) -> None:
    assert client.get("/api/health").json() == {"ok": True}
    repo = application.state.repo
    created = repo.create_canvas()
    assert len(created["id"]) == 10
    canvas = client.get(f"/api/canvases/{created['id']}").json()
    assert canvas["strokes"] == [] and canvas["strokeCount"] == 0
    assert canvas["brushWidthMin"] == 1 and len(canvas["palette"]) == 8
    for _ in range(3):
        repo.insert_stroke(StrokeIn.model_validate(payload(created["id"])))
    strokes = client.get(f"/api/canvases/{created['id']}/strokes?afterSeq=1").json()[
        "strokes"
    ]
    assert [s["seq"] for s in strokes] == [2, 3]
    assert set(strokes[0]) == {"seq", "color", "width", "points", "createdAt"}
    assert client.get("/docs").status_code == 200
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert client.post("/api/canvases").status_code == 404


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
        created = app.state.repo.create_canvas()
        data = client.get(f"/api/canvases/{created['id']}").json()
        assert data["width"] == 400 and data["height"] == 300
        assert data["palette"] == ["#123456", "#ffffff"]
        assert (
            data["brushWidthMax"] == 8
            and data["maxPointsPerStroke"] == 50
            and data["simplifyEpsilon"] == 0.7
        )


def test_static_spa_and_legacy_redirect(client: TestClient) -> None:
    assert client.get("/").status_code in (200, 503)
    assert client.get("/w").status_code in (200, 503)
    legacy = client.get("/c/0123456789", follow_redirects=False)
    assert legacy.status_code == 302 and legacy.headers["location"] == "/w"
    assert client.get("/api/missing").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/../app/schema.sql").status_code == 404
