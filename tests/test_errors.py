import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.errors import RETRYABLE, STATUS, AppError
from app.i18n import CATALOGS, pick_locale, translate

APP = Path(__file__).parent.parent / "app"


def test_catalogs_have_identical_keys_and_cover_every_error_code() -> None:
    assert set(CATALOGS["ru"]) == set(CATALOGS["en"])
    for code in STATUS:
        assert f"err.{code}" in CATALOGS["en"]
    assert RETRYABLE <= set(STATUS)
    for key in CATALOGS["en"]:
        placeholders = [set(re.findall(r"\{(\w+)\}", CATALOGS[loc][key])) for loc in ("ru", "en")]
        assert placeholders[0] == placeholders[1], key


def test_no_cyrillic_literals_in_server_code_and_only_known_error_codes() -> None:
    for path in APP.rglob("*.py"):
        code = path.read_text("utf-8")
        assert not re.search("[А-Яа-яЁё]", code), path
        for used in re.findall(r'AppError\(\s*"([A-Z_]+)"', code):
            assert used in STATUS, (path, used)


def test_locale_choice_and_envelope_shape() -> None:
    assert pick_locale("ru-RU,ru;q=0.9,en;q=0.8") == "ru"
    assert pick_locale("de-DE,en;q=0.8,ru;q=0.5") == "en"
    assert pick_locale("fr, ru") == "ru"
    assert pick_locale(None) == "en" and pick_locale("ru", "en") == "en"
    assert translate("xx", "err.NOT_FOUND") == "This world doesn't exist"
    assert AppError("SELECTION_BUSY").body("ru") == {
        "ok": False,
        "error": {
            "code": "SELECTION_BUSY",
            "message": "Этот рисунок уже оживляют",
            "field": None,
            "retryable": True,
            "details": {},
        },
    }


def test_rest_errors_are_localized(client: TestClient) -> None:
    russian = client.get("/api/canvases/0123456789", headers={"Accept-Language": "ru"})
    assert russian.status_code == 404
    assert russian.json()["error"]["message"] == "Такого мира нет"
    english = client.get("/api/canvases/no").json()["error"]
    assert english == {
        "code": "VALIDATION_FAILED",
        "message": "Check the format and the allowed values",
        "field": "canvasId",
        "retryable": False,
        "details": {},
    }
