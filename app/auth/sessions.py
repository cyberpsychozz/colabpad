"""Anonymous sessions: an httpOnly cookie that every visitor gets on first API use."""

from http.cookies import SimpleCookie
from typing import Any

from fastapi import Request, Response

from app.i18n import pick_locale
from app.repo import Repository

COOKIE = "cp_session"
MAX_AGE = 400 * 24 * 3600


def session_id_from_cookie_header(header: str | None) -> str | None:
    if not header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(header)
    except Exception:  # a malformed Cookie header is just "no session"
        return None
    return cookie[COOKIE].value if COOKIE in cookie else None


def ensure_session(request: Request, response: Response) -> dict[str, Any]:
    """The caller's session, created (and its cookie set) on first use."""
    repo: Repository = request.app.state.repo
    session = repo.get_session(request.cookies.get(COOKIE))
    if session is None:
        session = repo.create_session(pick_locale(request.headers.get("accept-language")))
        response.set_cookie(
            COOKIE,
            session["id"],
            max_age=MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.app.state.settings.public_url.startswith("https://"),
            path="/",
        )
    return session
