"""REST for ALIVE and the caller's own state."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.auth.access import require_role
from app.auth.sessions import ensure_session
from app.errors import AppError
from app.models import AliveIn, ChoiceIn, LocaleIn, check_id

Session = Annotated[dict[str, Any], Depends(ensure_session)]


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/me")
    async def me(request: Request, session: Session) -> dict[str, Any]:
        state = request.app.state
        quota = state.alive.quota(session)
        return {
            "user": state.repo.public_user(session["user_id"]),
            "plan": "free" if quota["plan"] == "anon" else quota["plan"],
            "ads": state.config.flags.ads.enabled and quota["plan"] != "plus",
            "adBlocks": {"side": state.settings.ads_block_side, "bottom": state.settings.ads_block_bottom},
            "locale": session["locale"],
            "subscription": state.billing.public(state.repo.get_subscription(session["user_id"])),
            "quota": {"used": quota["used"], "limit": quota["limit"]},
            "legal": state.config.legal.model_dump(),
        }

    @router.get("/api/me/events")
    async def my_events(
        request: Request, session: Session, since: Annotated[int, Query(ge=0)] = 0
    ) -> dict[str, Any]:
        """What happened to the things I brought to life, oldest first, 100 at a time.
        `since` is unix ms; pass the last event's `at` to get the next page."""
        events = request.app.state.repo.my_events(session, since)
        return {"events": events, "next": events[-1]["at"] if events else since}

    # Returning None keeps the session cookie that ensure_session may have just set.
    @router.put("/api/me/locale", status_code=204, response_class=Response, response_model=None)
    async def locale(body: LocaleIn, request: Request, session: Session) -> None:
        request.app.state.repo.set_locale(session, body.locale)

    @router.post("/api/canvases/{canvas_id}/alive", status_code=202)
    async def alive(
        canvas_id: str, body: AliveIn, request: Request, session: Session
    ) -> dict[str, Any]:
        state = request.app.state
        canvas = state.repo.get_canvas(check_id(canvas_id))
        require_role(request, canvas, session, "editor")
        job, quota = state.alive.submit(canvas, session, body.lasso)
        await state.alive.announce(job, session["nickname"])
        return {"jobId": job.id, "quota": quota}

    @router.get("/api/alive/{job_id}")
    async def job_status(job_id: str, request: Request, session: Session) -> dict[str, Any]:
        return request.app.state.alive.status(job_id, session)

    @router.post("/api/alive/{job_id}/choice")
    async def choice(
        job_id: str, body: ChoiceIn, request: Request, session: Session
    ) -> dict[str, Any]:
        request.app.state.alive.choose(job_id, session, body.type)
        return {"ok": True}

    return router
