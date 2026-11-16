from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, create_asgi
from app.models import StrokeIn
from app.repo import Repository

BOUNDS = (19456, 11264)
CHUNK = 1024


@pytest.fixture
def application(tmp_path: Path) -> FastAPI:
    return create_app(Settings(_env_file=None, data_dir=tmp_path))


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(create_asgi(application)) as test_client:
        yield test_client


def payload(canvas_id: str = "world", **changes: Any) -> dict[str, Any]:
    return {
        "canvasId": canvas_id,
        "clientId": str(uuid4()),
        "color": "#1E88E5",
        "width": 6,
        "points": [[10, 20, 0.5], [30, 40, 0.8]],
        **changes,
    }


def stroke_in(canvas_id: str = "world", **changes: Any) -> StrokeIn:
    return StrokeIn.model_validate(
        payload(canvas_id, **changes), context={"bounds": BOUNDS, "chunk": CHUNK}
    )


def private_canvas(repo: Repository) -> str:
    """A second canvas, owned by a throwaway user."""
    user_id = str(uuid4())
    repo.db.execute(
        "INSERT INTO app_user(id, email, created_at) VALUES(?,?,0)",
        (user_id, f"{user_id}@example.test"),
    )
    return repo.create_private_canvas(user_id, None, *BOUNDS)["id"]


@pytest.fixture
def add_stroke(application: FastAPI, client: TestClient) -> Callable[..., dict[str, Any]]:
    def add(canvas_id: str = "world", **changes: Any) -> dict[str, Any]:
        stroke = stroke_in(canvas_id, **changes)
        x, y, _ = stroke.points[0]
        return application.state.repo.insert_stroke(
            stroke, (min(int(x // CHUNK), 18), min(int(y // CHUNK), 10))
        )[0]

    return add
