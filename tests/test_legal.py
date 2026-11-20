"""Legal pages, the seller's details in /api/me, and /ads.txt (spec §9b)."""

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import GameConfig, Legal, Settings
from app.main import create_app, create_asgi

CONFIG = Path(__file__).parent.parent / "config"


def client_for(tmp_path: Path, config_dir: Path) -> TestClient:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", config_dir=config_dir, sim_mode="off")
    return TestClient(create_asgi(create_app(settings)))


def test_repo_ads_txt_lists_the_yandex_seller() -> None:
    config = GameConfig(CONFIG)
    assert config.ads_txt is not None
    assert "yandex.com, 108702699, DIRECT" in config.ads_txt.splitlines()


def test_ads_txt_is_served_as_plain_text(client: TestClient) -> None:
    response = client.get("/ads.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == (CONFIG / "ads.txt").read_text("utf-8")


def test_no_ads_txt_means_404(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    shutil.copytree(CONFIG, directory)
    (directory / "ads.txt").unlink()
    with client_for(tmp_path, directory) as client:
        assert client.get("/ads.txt").status_code == 404


def test_legal_pages_are_spa_routes(client: TestClient) -> None:
    for path in ("/about", "/offer", "/privacy"):
        assert client.get(path).status_code in (200, 503)


def test_me_carries_the_sellers_details(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    shutil.copytree(CONFIG, directory)
    details = {
        "seller": {"name": "Иванов Иван Иванович", "inn": "123456789012"},
        "email": "help@example.org",
        "phone": "+7 900 123-45-67",
    }
    (directory / "legal.yaml").write_text(yaml.safe_dump(details, allow_unicode=True), "utf-8")
    with client_for(tmp_path, directory) as client:
        assert client.get("/api/me").json()["legal"] == details


def test_blank_details_load_and_bad_ones_are_refused() -> None:
    assert Legal.model_validate(yaml.safe_load((CONFIG / "legal.yaml").read_text("utf-8"))).model_dump() == {
        "seller": {"name": "", "inn": ""},
        "email": "",
        "phone": "",
    }
    for bad in (
        {"seller": {"inn": "12345"}},
        {"email": "not-an-address"},
        {"phone": "call me"},
        {"site": "extra keys are refused"},
    ):
        with pytest.raises(ValidationError):
            Legal.model_validate(bad)
