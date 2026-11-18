"""Server-side message catalogs: error fallbacks and (later) e-mails, in RU and EN."""

import json
from pathlib import Path
from typing import Any

LOCALES = ("ru", "en")
CATALOGS: dict[str, dict[str, str]] = {
    locale: json.loads(Path(__file__).with_name(f"{locale}.json").read_text("utf-8"))
    for locale in LOCALES
}


def pick_locale(accept_language: str | None, preferred: str | None = None) -> str:
    """The session/user locale if known, else the first supported Accept-Language."""
    if preferred in LOCALES:
        return preferred
    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip().lower()[:2]
        if tag in LOCALES:
            return tag
    return "en"


def translate(locale: str, key: str, params: dict[str, Any] | None = None) -> str:
    text = CATALOGS.get(locale, CATALOGS["en"]).get(key) or CATALOGS["en"].get(key, key)
    for name, value in (params or {}).items():
        text = text.replace("{" + name + "}", str(value))
    return text
