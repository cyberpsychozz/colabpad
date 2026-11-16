import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import GameConfig, Settings
from app.main import create_app, create_asgi

CONFIG = Path(__file__).parent.parent / "config"


def test_repo_config_loads_with_brief_defaults() -> None:
    config = GameConfig(CONFIG)
    assert config.flags.model_dump() == {
        "ads": {"enabled": False},
        "billing": {"recurring": False},
        "vlm": {"enabled": True},
        "sleep_catchup": True,
    }
    assert len(config.archetypes) == 14 and "doodle" in config.archetypes
    assert config.limits["alive"]["quota_per_day"] == {"anon": 10, "free": 20, "plus": 50}
    assert config.billing["plans"]["plus_month"]["amount_kop"] == 29900
    for override in config.labels.values():
        assert override["type"] in config.archetypes


def test_boots_with_all_flags_off_and_creates_sprites_dir(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    shutil.copytree(CONFIG, directory)
    (directory / "flags.yaml").write_text(
        yaml.safe_dump(
            {
                "ads": {"enabled": False},
                "billing": {"recurring": False},
                "vlm": {"enabled": False},
                "sleep_catchup": False,
            }
        )
    )
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", config_dir=directory)
    app = create_app(settings)
    with TestClient(create_asgi(app)) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/assets/missing.js").status_code == 404
    assert settings.sprites_dir.is_dir()
    assert settings.db_path == str(tmp_path / "data" / "collabpad.db")


def test_bad_flags_and_bounds_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    shutil.copytree(CONFIG, directory)
    limits = yaml.safe_load((directory / "limits.yaml").read_text())
    limits["world"]["bounds_w"] = 1000
    (directory / "limits.yaml").write_text(yaml.safe_dump(limits))
    with pytest.raises(ValueError, match="multiple"):
        GameConfig(directory)
    shutil.copy(CONFIG / "limits.yaml", directory / "limits.yaml")
    (directory / "flags.yaml").write_text("ads: {enabled: maybe}\n")
    with pytest.raises(ValueError):
        GameConfig(directory)
