from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, create_asgi


@pytest.fixture
def application(tmp_path: Path) -> FastAPI:
    return create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "db.sqlite"),
            canvas_create_limit="100/60",
        )
    )


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(create_asgi(application)) as test_client:
        yield test_client
