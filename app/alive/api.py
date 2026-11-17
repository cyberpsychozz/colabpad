"""REST for ALIVE and the caller's own state."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.sessions import ensure_session
from app.errors import AppError
from app.models import AliveIn, ChoiceIn, check_id

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
            "locale": session["locale"],
            "quota": {"used": quota["used"], "limit": quota["limit"]},
        }

    @router.post("/api/canvases/{canvas_id}/alive", status_code=202)
    async def alive(
        canvas_id: str, body: AliveIn, request: Request, session: Session
    ) -> dict[str, Any]:
        state = request.app.state
        canvas = state.repo.get_canvas(check_id(canvas_id))
        # Roles arrive with accounts; until then only the shared world can be edited.
        if canvas["kind"] != "world":
            raise AppError("FORBIDDEN_ROLE")
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
