"""Issue single-use login links without exposing tokens to API clients."""

import hashlib
import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from app.auth.sessions import COOKIE, ensure_session, set_session_cookie
from app.errors import AppError
from app.i18n import translate
from app.realtime.ratelimit import SlidingWindow
from app.repo.canvases import now_ms


class MagicLinkRequest(BaseModel):
    email: str = Field(max_length=254)
    # Where to land after signing in. Only a world path, so the link cannot redirect elsewhere.
    next: str | None = Field(default=None, max_length=64)


RETURN_PATH = re.compile(r"/w|/plans|/p/[A-Za-z0-9_-]{1,32}")


def return_path(path: str | None) -> str:
    return path if path and RETURN_PATH.fullmatch(path) else "/w"


def auth_router(rate: str) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    limiter = SlidingWindow(rate)

    @router.post("/magic-link", status_code=204, response_class=Response, response_model=None)
    async def magic_link(body: MagicLinkRequest, request: Request,
                         session: Annotated[dict[str, Any], Depends(ensure_session)]) -> None:
        email = body.email.strip().lower()
        if not re.fullmatch(r"[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", email):
            raise AppError("VALIDATION_FAILED", "email")
        if not limiter.consume(email):
            raise AppError("RATE_LIMITED")
        state = request.app.state
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        ttl = state.config.limits["auth"]["magic_link_ttl_min"]
        locale = session["locale"]
        state.repo.store_magic_token(digest, email, locale, now_ms() + ttl * 60_000)
        url = state.settings.public_url.rstrip("/") + "/api/auth/callback?token=" + token
        if return_path(body.next) != "/w":
            url += "&next=" + return_path(body.next)
        try:
            await state.mailer.send(email, translate(locale, "mail.login_subject"),
                                    translate(locale, "mail.login_body", {"url": url, "minutes": ttl}))
        except OSError:
            state.repo.discard_magic_token(digest)
            raise AppError("INTERNAL") from None

    async def disconnect_session(request: Request, sid: str) -> None:
        realtime = request.app.state.realtime
        for socket, session_id in list(realtime.sessions.items()):
            if session_id == sid:
                await realtime.sio.disconnect(socket)

    @router.get("/callback")
    async def callback(token: str, request: Request, next: str | None = None) -> RedirectResponse:
        repo = request.app.state.repo
        session = repo.cookie_session(request.cookies.get(COOKIE))
        credential = repo.redeem_magic_token(token, session)
        if session and not session["user_id"]:
            account = repo.get_session(session["id"])
            request.app.state.alive.link_subject(repo.subject_of(session), repo.subject_of(account))
        if session:
            await disconnect_session(request, session["id"])
        response = RedirectResponse(return_path(next), status_code=302)
        set_session_cookie(request, response, credential)
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        repo = request.app.state.repo
        session = repo.cookie_session(request.cookies.get(COOKIE))
        if not session or not session["user_id"]:
            raise AppError("UNAUTHENTICATED")
        repo.logout(session["id"])
        await disconnect_session(request, session["id"])
        response = Response(status_code=204)
        response.delete_cookie(COOKIE)
        return response

    return router
