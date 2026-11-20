import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import GameConfig, Settings
from app.main import create_app, create_asgi

CONFIG = Path(__file__).parent.parent / "config"


def test_repo_config_loads_with_spec_defaults() -> None:
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


def test_only_the_vlm_goes_through_vlm_proxy_url(tmp_path: Path) -> None:
    """VLM calls use VLM_PROXY_URL; the payments client never does."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[str] = []
    answer = {
        "type": "doodle", "confidence": 0.9, "top3": [{"type": "doodle", "p": 0.9}],
        "label": {"ru": "звезда", "en": "star"}, "locomotion": "hop", "diet": None,
        "role": None, "variant": None, "facing": "right", "multiple": False, "unsafe": False,
        "parts": {"legs": [], "wings": [], "wheels": [], "head": None, "tail": None},
    }  # fmt: skip

    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append(self.path)  # a proxy gets the absolute URL
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {"choices": [{"message": {"content": json.dumps(answer)}}], "usage": {"cost": 0}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    directory = tmp_path / "config"
    shutil.copytree(CONFIG, directory)
    shutil.copytree(CONFIG.parent / "prompts", tmp_path / "prompts")
    vlm = yaml.safe_load((directory / "vlm.yaml").read_text())
    vlm["base_url"] = "http://vlm.invalid/api/v1"  # unresolvable: only the proxy can answer
    (directory / "vlm.yaml").write_text(yaml.safe_dump(vlm))
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", config_dir=directory,
        vlm_api_key="k", vlm_proxy_url=f"http://127.0.0.1:{server.server_port}",
        yookassa_shop_id="1", yookassa_secret_key="test_x",
    )  # fmt: skip
    app = create_app(settings)
    try:
        with TestClient(create_asgi(app)) as client:
            adapter = app.state.alive.vlm
            result = client.portal.call(adapter.classify, b"png")
            assert result.type == "doodle"
            assert seen == ["http://vlm.invalid/api/v1/chat/completions"]
            assert app.state.billing.payments.http is not adapter.client
            assert not app.state.billing.payments.http._mounts  # no proxy for payments
    finally:
        server.shutdown()
    with pytest.raises(ValueError):
        Settings(_env_file=None, vlm_proxy_url="socks5://127.0.0.1:1055")
