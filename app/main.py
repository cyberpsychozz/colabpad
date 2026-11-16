"""REST, lifespan, static frontend and a single-process Socket.IO ASGI entrypoint."""

import json
import logging

import httpx
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any

import socketio
from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app.alive.api import create_router
from app.alive.builtin import ensure_builtin_sprites
from app.alive.queue import AliveService, Rig
from app.alive.rig import rig_sprite
from app.alive.vlm import Budget, OpenRouterVlm, UnavailableVlm, Vlm, load_prompt
from app.auth.sessions import ensure_session
from app.config import GameConfig, Settings
from app.db import connect
from app.errors import AppError, validation_error
from app.i18n import pick_locale, translate
from app.models import CanvasOut, ChunkOut, ErrorOut, check_id
from app.realtime.gateway import Realtime
from app.repo import Repository
from app.sim.bridge import SimBridge

logger = logging.getLogger(__name__)

METRIC_KEYS = {"adblock_gate_shown", "adblock_gate_passed", "plus_clicked"}


def scope_locale(scope: Scope) -> str:
    headers = dict(scope.get("headers") or ())
    return pick_locale(headers.get(b"accept-language", b"").decode("latin-1"))


def request_locale(request: Request) -> str:
    return pick_locale(request.headers.get("accept-language"))


class PayloadLimit:
    """Bound REST/polling bodies and reject oversized WS packets before Engine.IO.

    Default-namespace Socket.IO events with an ack ID receive the standard error
    ack; unacknowledged or binary oversized messages close with code 1009.
    """

    def __init__(self, application: ASGIApp, limit: int) -> None:
        self.application = application
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in ("POST", "PUT", "PATCH"):
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.limit:
                    await JSONResponse(
                        AppError("PAYLOAD_TOO_LARGE").body(scope_locale(scope)),
                        status_code=413,
                    )(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay() -> dict[str, Any]:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            await self.application(scope, replay, send)
            return
        if scope["type"] == "websocket":

            async def limited_receive() -> dict[str, Any]:
                while True:
                    message = await receive()
                    if message["type"] != "websocket.receive":
                        return message
                    packet = message.get("text")
                    size = (
                        len(packet.encode())
                        if packet is not None
                        else len(message.get("bytes") or b"")
                    )
                    if size <= self.limit:
                        return message
                    ack = re.match(r"^42(\d+)\[", packet or "")
                    if ack:
                        body = AppError("PAYLOAD_TOO_LARGE").body(scope_locale(scope))
                        error = json.dumps([body], ensure_ascii=False)
                        await send(
                            {"type": "websocket.send", "text": f"43{ack[1]}{error}"}
                        )
                    else:
                        await send({"type": "websocket.close", "code": 1009})
                        return {"type": "websocket.disconnect", "code": 1009}

            await self.application(scope, limited_receive, send)
            return
        await self.application(scope, receive, send)


def create_app(
    settings: Settings | None = None, vlm: Vlm | None = None, rig: Rig = rig_sprite
) -> FastAPI:
    """vlm and rig can be replaced by fakes in tests."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings.sprites_dir.mkdir(parents=True, exist_ok=True)
        connection = connect(settings.db_path)
        application.state.repo = Repository(connection)
        world = config.limits["world"]
        application.state.repo.ensure_world(world["bounds_w"], world["bounds_h"])
        ensure_builtin_sprites(application.state.repo, settings.sprites_dir)
        http = httpx.AsyncClient()
        sim = application.state.sim = SimBridge(
            settings, config, lambda: application.state.repo, application.state.realtime
        )
        await sim.start()
        application.state.alive = AliveService(
            settings,
            config,
            lambda: application.state.repo,
            application.state.realtime,
            vlm or build_vlm(http),
            rig,
            on_spawn=lambda entity: sim.spawn(entity["id"]),
        )
        application.state.alive.start()
        try:
            yield
        finally:
            await application.state.alive.stop()
            await sim.stop()
            await http.aclose()
            await application.state.realtime.sio.shutdown()
            connection.close()

    def build_vlm(http: httpx.AsyncClient) -> Vlm:
        if not (config.flags.vlm.enabled and settings.vlm_api_key):
            return UnavailableVlm()
        repo = lambda: application.state.repo  # noqa: E731
        budget = Budget(
            config.vlm["daily_budget_usd"],
            lambda key: repo().get_metric(key),
            lambda key, amount: repo().add_metric(key, amount),
        )
        prompt = load_prompt(settings.config_dir.parent / "prompts" / "classify.md")
        return OpenRouterVlm(http, config.vlm, settings.vlm_api_key, prompt, budget)

    application = FastAPI(
        title="CollabPad API",
        version="1.0.0",
        lifespan=lifespan,
        responses={400: {"model": ErrorOut}, 404: {"model": ErrorOut}},
    )
    application.state.settings = settings
    application.state.config = config = GameConfig(settings.config_dir)
    application.state.realtime = Realtime(
        settings, config, lambda: application.state.repo
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/sprites/") and response.status_code == 200:
            # Sprite files never change once written.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @application.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.body(request_locale(request)), status_code=exc.http_status)

    @application.exception_handler(RequestValidationError)
    async def request_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            validation_error(exc).body(request_locale(request)), status_code=400
        )

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled application error", exc_info=(type(exc), exc, exc.__traceback__)
        )
        return JSONResponse(
            AppError("INTERNAL").body(request_locale(request)), status_code=500
        )

    @application.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/api/canvases/{canvas_id}", response_model=CanvasOut)
    async def get_canvas(
        canvas_id: str, session: Annotated[dict[str, Any], Depends(ensure_session)]
    ) -> dict[str, Any]:
        canvas = application.state.repo.get_canvas(check_id(canvas_id))
        return {
            "id": canvas["id"],
            "kind": canvas["kind"],
            "title": canvas["title"],
            # Roles arrive with accounts (T20); the shared world gives editor to all.
            "role": "viewer" if canvas["frozen"] else "editor",
            "frozen": bool(canvas["frozen"]),
            "bounds": {"w": canvas["bounds_w"], "h": canvas["bounds_h"]},
            "spawn": {"x": canvas["bounds_w"] / 2, "y": canvas["bounds_h"] / 2},
            "limits": {
                "brush_width_min": settings.brush_width_min,
                "brush_width_max": settings.brush_width_max,
                "max_points_per_stroke": settings.max_points_per_stroke,
                "max_payload_bytes": settings.max_payload_bytes,
                "simplify_epsilon": settings.simplify_epsilon,
                "chunk_size": config.limits["world"]["chunk_size"],
                "max_chunks": config.limits["viewport"]["max_chunks"],
            },
            "palette": settings.colors,
        }

    @application.get(
        "/api/canvases/{canvas_id}/chunks/{cx}/{cy}", response_model=ChunkOut
    )
    async def get_chunk(
        canvas_id: str,
        cx: Annotated[int, Path(ge=0)],
        cy: Annotated[int, Path(ge=0)],
        after_seq: Annotated[int, Query(alias="afterSeq", ge=0)] = 0,
    ) -> dict[str, Any]:
        repo = application.state.repo
        canvas = repo.get_canvas(check_id(canvas_id))
        size = config.limits["world"]["chunk_size"]
        if cx * size >= canvas["bounds_w"] or cy * size >= canvas["bounds_h"]:
            raise AppError("NOT_FOUND")
        # No await between these reads: lastSeq is a consistent watermark.
        return {
            "strokes": repo.chunk_strokes(canvas_id, cx, cy, after_seq),
            "fates": repo.chunk_fates(canvas_id, cx, cy),
            "entities": (
                repo.living_in_rect if settings.sim_mode == "off" else application.state.sim.entities_in
            )(canvas_id, cx * size, cy * size, (cx + 1) * size, (cy + 1) * size),
            "fossils": repo.chunk_fossils(
                canvas_id, cx * size, cy * size, (cx + 1) * size, (cy + 1) * size
            ),
            "last_seq": canvas["stroke_count"],
            "tick": 0,
        }

    @application.post("/api/metrics/{key}", status_code=204)
    async def metric(key: str) -> Response:
        if key not in METRIC_KEYS:
            raise AppError("NOT_FOUND")
        application.state.repo.increment_metric(key)
        return Response(status_code=204)

    application.include_router(create_router())

    # API routes precede static mounting and explicit SPA routes.
    application.mount(
        "/sprites", StaticFiles(directory=settings.sprites_dir, check_dir=False), name="sprites"
    )
    assets = settings.static_dir / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/c/{canvas_id}", include_in_schema=False)
    async def legacy_canvas_link(canvas_id: str) -> Response:
        return RedirectResponse("/w", status_code=302)

    @application.get("/", include_in_schema=False)
    @application.get("/w", include_in_schema=False)
    async def index(request: Request) -> Response:
        path = settings.static_dir / "index.html"
        if not path.is_file():
            return Response(
                translate(request_locale(request), "page.frontend_missing"),
                status_code=503,
            )
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    return application


def create_asgi(application: FastAPI) -> PayloadLimit:
    return PayloadLimit(
        socketio.ASGIApp(application.state.realtime.sio, other_asgi_app=application),
        application.state.settings.max_payload_bytes,
    )


app = create_app()
asgi = create_asgi(app)
