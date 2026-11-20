"""Sign in with a six-digit code sent by e-mail, and sign out.

A code works only in the browser session that asked for it, for a few minutes and a few guesses."""

import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.mail import login_mail
from app.auth.sessions import COOKIE, ensure_session, set_session_cookie
from app.errors import AppError
from app.realtime.ratelimit import SlidingWindow
from app.repo.canvases import now_ms

CODE_DIGITS = 6
EMAIL = re.compile(r"[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")


class CodeRequest(BaseModel):
    email: str = Field(max_length=254)


class CodeCheck(BaseModel):
    code: str = Field(max_length=32)


def new_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def auth_router(rate: str) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    limiter = SlidingWindow(rate)

    @router.post("/code", status_code=204, response_class=Response, response_model=None)
    async def send_code(body: CodeRequest, request: Request,
                        session: Annotated[dict[str, Any], Depends(ensure_session)]) -> None:
        email = body.email.strip().lower()
        if not EMAIL.fullmatch(email):
            raise AppError("VALIDATION_FAILED", "email")
        if not limiter.consume(email):
            raise AppError("RATE_LIMITED")
        state = request.app.state
        code = new_code()
        ttl = state.config.limits["auth"]["code_ttl_min"]
        locale = session["locale"]
        state.repo.store_login_code(session["id"], email, locale, code, now_ms() + ttl * 60_000)
        subject, text, html = login_mail(locale, code, ttl, state.settings.public_url)
        try:
            await state.mailer.send(email, subject, text, html)
        except OSError:
            state.repo.discard_login_code(session["id"], code)
            raise AppError("INTERNAL") from None

    async def disconnect_session(request: Request, sid: str) -> None:
        realtime = request.app.state.realtime
        for socket, session_id in list(realtime.sessions.items()):
            if session_id == sid:
                await realtime.sio.disconnect(socket)

    @router.post("/verify", status_code=204, response_class=Response, response_model=None)
    async def verify(body: CodeCheck, request: Request) -> Response:
        state = request.app.state
        repo = state.repo
        code = re.sub(r"\D", "", body.code)  # people paste "123 456" or "123-456"
        if len(code) != CODE_DIGITS:
            raise AppError("VALIDATION_FAILED", "code")
        session = repo.cookie_session(request.cookies.get(COOKIE))
        if session is None:
            raise AppError("CODE_EXPIRED", "code")
        credential = repo.redeem_login_code(session, code, state.config.limits["auth"]["code_attempts"])
        if not session["user_id"]:
            account = repo.get_session(session["id"])
            state.alive.link_subject(repo.subject_of(session), repo.subject_of(account))
        await disconnect_session(request, session["id"])
        response = Response(status_code=204)
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
