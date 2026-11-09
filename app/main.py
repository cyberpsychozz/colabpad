"""REST, lifespan, static frontend and a single-process Socket.IO ASGI entrypoint."""

import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any

import socketio
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.db import connect
from app.errors import AppError, too_large, validation_error
from app.models import CanvasCreated, CanvasOut, ErrorOut, StrokesOut, check_id
from app.realtime import Realtime, SlidingWindow
from app.repo import Repository

logger = logging.getLogger(__name__)


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
                    await JSONResponse(too_large().body(), status_code=413)(
                        scope, receive, send
                    )
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
                        error = json.dumps([too_large().body()], ensure_ascii=False)
                        await send(
                            {"type": "websocket.send", "text": f"43{ack[1]}{error}"}
                        )
                    else:
                        await send({"type": "websocket.close", "code": 1009})
                        return {"type": "websocket.disconnect", "code": 1009}

            await self.application(scope, limited_receive, send)
            return
        await self.application(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        connection = connect(settings.db_path)
        application.state.repo = Repository(connection, settings)
        try:
            yield
        finally:
            await application.state.realtime.sio.shutdown()
            connection.close()

    application = FastAPI(
        title="CollabPad API",
        version="1.0.0",
        lifespan=lifespan,
        responses={400: {"model": ErrorOut}, 404: {"model": ErrorOut}},
    )
    application.state.settings = settings
    application.state.realtime = Realtime(settings, lambda: application.state.repo)
    create_limit = SlidingWindow(settings.canvas_create_limit)
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
        return response

    @application.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.http_status)

    @application.exception_handler(RequestValidationError)
    async def request_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(validation_error(exc).body(), status_code=400)

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled application error", exc_info=(type(exc), exc, exc.__traceback__)
        )
        return JSONResponse(
            AppError("INTERNAL", "Ошибка сервера.").body(), status_code=500
        )

    @application.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @application.post("/api/canvases", status_code=201, response_model=CanvasCreated)
    async def create_canvas(request: Request) -> dict[str, Any]:
        # Uvicorn trusts forwarding headers from loopback only. No IP persistence.
        key = request.client.host if request.client else "local"
        if not create_limit.consume(key):
            raise AppError(
                "RATE_LIMITED",
                "Подожди немного перед созданием нового холста.",
                http_status=429,
            )
        canvas = application.state.repo.create_canvas()
        return {
            "id": canvas["id"],
            "url": f"/c/{canvas['id']}",
            "width": canvas["width"],
            "height": canvas["height"],
        }

    @application.get("/api/canvases/{canvas_id}", response_model=CanvasOut)
    async def get_canvas(canvas_id: str) -> dict[str, Any]:
        canvas = application.state.repo.get_canvas(check_id(canvas_id))
        return {
            **canvas,
            "max_strokes": settings.max_strokes_per_canvas,
            "palette": settings.colors,
            "brush_width_min": settings.brush_width_min,
            "brush_width_max": settings.brush_width_max,
            "max_points_per_stroke": settings.max_points_per_stroke,
            "max_payload_bytes": settings.max_payload_bytes,
            "simplify_epsilon": settings.simplify_epsilon,
            "name_max_len": settings.name_max_len,
            "strokes": application.state.repo.list_strokes(canvas_id),
        }

    @application.get("/api/canvases/{canvas_id}/strokes", response_model=StrokesOut)
    async def get_strokes(
        canvas_id: str, after_seq: Annotated[int, Query(alias="afterSeq", ge=0)] = 0
    ) -> dict[str, Any]:
        application.state.repo.get_canvas(check_id(canvas_id))
        return {"strokes": application.state.repo.list_strokes(canvas_id, after_seq)}

    @application.post("/api/metrics/{key}", status_code=204)
    async def metric(key: str) -> Response:
        if key not in {"pro-click", "stroke-buy-click"}:
            raise AppError("NOT_FOUND", "Такой метрики нет.", http_status=404)
        application.state.repo.increment_metric(key)
        return Response(status_code=204)

    # API routes precede static mounting and explicit SPA routes.
    assets = settings.static_dir / "assets"
    application.mount(
        "/assets", StaticFiles(directory=assets, check_dir=False), name="assets"
    )

    @application.get("/", include_in_schema=False)
    @application.get("/c/{canvas_id}", include_in_schema=False)
    async def index() -> Response:
        path = settings.static_dir / "index.html"
        if not path.is_file():
            return Response(
                "Фронтенд ещё не собран. Выполни npm run build в папке web.",
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
