"""Private worlds: creating them, the link role, members and invitations."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.sessions import ensure_session
from app.errors import AppError
from app.i18n import pick_locale, translate
from app.models import AccessIn, CanvasIn, MemberIn, RoleIn, check_id

Session = Annotated[dict[str, Any], Depends(ensure_session)]
ROLES = ("none", "viewer", "player", "editor")


def require_role(request: Request, canvas: dict[str, Any], session: dict[str, Any], needed: str) -> str:
    role = request.app.state.repo.role_of(canvas, session)
    if ROLES.index(role) < ROLES.index(needed):
        raise AppError("FORBIDDEN_ROLE")
    return role


def access_router() -> APIRouter:
    router = APIRouter(prefix="/api/canvases")

    def owned(request: Request, canvas_id: str, session: dict[str, Any]) -> dict[str, Any]:
        canvas = request.app.state.repo.get_canvas(check_id(canvas_id))
        if not session["user_id"] or canvas["owner_user_id"] != session["user_id"]:
            raise AppError("FORBIDDEN_ROLE")
        return canvas

    def summary(canvas: dict[str, Any], role: str) -> dict[str, Any]:
        return {"id": canvas["id"], "title": canvas["title"], "role": role,
                "frozen": bool(canvas["frozen"]), "linkRole": canvas["link_role"]}  # fmt: skip

    def access(request: Request, canvas_id: str) -> dict[str, Any]:
        repo = request.app.state.repo
        return {"linkRole": repo.get_canvas(canvas_id)["link_role"], "members": repo.members(canvas_id)}

    @router.get("")
    async def my_canvases(request: Request, session: Session) -> dict[str, Any]:
        repo = request.app.state.repo
        user_id = session["user_id"]
        if not user_id:
            return {"owned": [], "shared": [], "max": 0}
        return {
            "owned": [summary(c, repo.role_of(c, session)) for c in repo.owned_canvases(user_id)],
            "shared": [summary(c, repo.role_of(c, session)) for c in repo.shared_canvases(user_id)],
            "max": request.app.state.config.limits["worlds"]["max_private"],
        }

    @router.post("", status_code=201)
    async def create(body: CanvasIn, request: Request, session: Session) -> dict[str, Any]:
        state = request.app.state
        if not session["user_id"]:
            raise AppError("UNAUTHENTICATED")
        if state.repo.plan_of(session, state.billing.now()) != "plus":
            raise AppError("PAYMENT_REQUIRED")
        limit = state.config.limits["worlds"]["max_private"]
        if len(state.repo.owned_canvases(session["user_id"])) >= limit:
            raise AppError("WORLD_LIMIT", details={"limit": limit})
        world = state.config.limits["world"]
        canvas = state.repo.create_private_canvas(
            session["user_id"], body.title.strip() or None, world["bounds_w"], world["bounds_h"]
        )
        state.sim.ensure_world(canvas)
        return {"id": canvas["id"], "kind": "private"}

    @router.patch("/{canvas_id}")
    async def rename(
        canvas_id: str, body: CanvasIn, request: Request, session: Session
    ) -> dict[str, Any]:
        canvas = owned(request, canvas_id, session)
        if canvas["kind"] != "private":
            raise AppError("FORBIDDEN_ROLE")
        title = body.title.strip()
        if not title:
            raise AppError("VALIDATION_FAILED", "title")
        updated = request.app.state.repo.rename_canvas(canvas_id, title)
        return summary(updated, request.app.state.repo.role_of(updated, session))

    @router.delete("/{canvas_id}", status_code=204)
    async def delete(canvas_id: str, request: Request, session: Session) -> Response:
        canvas = owned(request, canvas_id, session)
        if canvas["kind"] != "private":
            raise AppError("FORBIDDEN_ROLE")
        state = request.app.state
        state.repo.delete_canvas(canvas_id)
        await state.realtime.recheck(canvas_id)
        state.sim.drop_world(canvas_id)
        return Response(status_code=204)

    @router.get("/{canvas_id}/access")
    async def get_access(canvas_id: str, request: Request, session: Session) -> dict[str, Any]:
        owned(request, canvas_id, session)
        return access(request, canvas_id)

    @router.put("/{canvas_id}/access")
    async def put_access(
        canvas_id: str, body: AccessIn, request: Request, session: Session
    ) -> dict[str, Any]:
        owned(request, canvas_id, session)
        request.app.state.repo.set_link_role(canvas_id, body.link_role)
        await request.app.state.realtime.recheck(canvas_id)
        return access(request, canvas_id)

    @router.post("/{canvas_id}/members", status_code=201)
    async def invite(
        canvas_id: str, body: MemberIn, request: Request, session: Session
    ) -> dict[str, Any]:
        state = request.app.state
        canvas = owned(request, canvas_id, session)
        repo = state.repo
        email = body.email.strip().lower()
        user = repo.user_by_email(email, session["locale"])
        if user["id"] == session["user_id"]:
            raise AppError("VALIDATION_FAILED", "email")
        members = repo.members(canvas_id)
        if len(members) >= state.config.limits["worlds"]["max_members"] and all(
            member["userId"] != user["id"] for member in members
        ):
            raise AppError("WORLD_LIMIT", details={"limit": len(members)})
        repo.set_member(canvas_id, user["id"], body.role, session["user_id"])
        await state.realtime.recheck(canvas_id)
        locale = pick_locale(None, user["locale"] or session["locale"])
        params = {
            "title": canvas["title"] or canvas_id,
            "role": translate(locale, f"roles.{body.role}"),
            "url": f"{state.settings.public_url.rstrip('/')}/p/{canvas_id}",
        }
        try:
            await state.mailer.send(
                email,
                translate(locale, "mail.invite_subject", params),
                translate(locale, "mail.invite_body", params),
            )
            mailed = True
        except OSError:
            mailed = False  # access is granted either way; the owner can share the link
        return {"userId": user["id"], "email": email, "role": body.role, "mailed": mailed}

    @router.patch("/{canvas_id}/members/{user_id}")
    async def change(
        canvas_id: str, user_id: str, body: RoleIn, request: Request, session: Session
    ) -> dict[str, Any]:
        owned(request, canvas_id, session)
        if not request.app.state.repo.update_member(canvas_id, user_id, body.role):
            raise AppError("NOT_FOUND")
        await request.app.state.realtime.recheck(canvas_id)
        return access(request, canvas_id)

    @router.delete("/{canvas_id}/members/me", status_code=204)
    async def leave(canvas_id: str, request: Request, session: Session) -> Response:
        if not session["user_id"]:
            raise AppError("UNAUTHENTICATED")
        canvas = request.app.state.repo.get_canvas(check_id(canvas_id))
        if canvas["owner_user_id"] == session["user_id"]:
            raise AppError("FORBIDDEN_ROLE")
        if not request.app.state.repo.remove_member(canvas_id, session["user_id"]):
            raise AppError("NOT_FOUND")
        await request.app.state.realtime.kick(canvas_id, session["user_id"])
        return Response(status_code=204)

    @router.delete("/{canvas_id}/members/{user_id}", status_code=204)
    async def remove(canvas_id: str, user_id: str, request: Request, session: Session) -> Response:
        owned(request, canvas_id, session)
        if not request.app.state.repo.remove_member(canvas_id, user_id):
            raise AppError("NOT_FOUND")
        await request.app.state.realtime.kick(canvas_id, user_id)
        return Response(status_code=204)

    return router
