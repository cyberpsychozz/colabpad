"""Socket.IO events: chunk subscriptions, limits and durable stroke submission."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import socketio
from pydantic import ValidationError

from app.config import GameConfig, Settings
from app.errors import AppError, validation_error
from app.i18n import pick_locale
from app.models import StrokeIn, ViewportIn, chunk_of
from app.realtime.ratelimit import SlidingWindow
from app.auth.sessions import session_id_from_cookie_header
from app.realtime.rooms import Rooms, room, session_room
from app.repo import Repository

logger = logging.getLogger(__name__)


class Realtime:
    def __init__(
        self, settings: Settings, config: GameConfig, get_repo: Callable[[], Repository]
    ) -> None:
        self.settings = settings
        self.get_repo = get_repo
        self.chunk_size: int = config.limits["world"]["chunk_size"]
        self.max_chunks: int = config.limits["viewport"]["max_chunks"]
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=settings.origins,
            max_http_buffer_size=settings.max_payload_bytes,
            async_handlers=False,
            logger=False,
            engineio_logger=False,
        )
        self.rooms = Rooms()
        self.stroke_limit = SlidingWindow(config.limits["stroke"]["rate"])
        self.viewport_limit = SlidingWindow(config.limits["viewport"]["rate"])
        self.write_lock = asyncio.Lock()
        self.locales: dict[str, str] = {}
        self.sessions: dict[str, str] = {}
        self.sio.on("connect", self.connect)
        self.sio.on("viewport:set", self.viewport)
        self.sio.on("stroke:add", self.add)
        self.sio.on("disconnect", self.disconnect)

    def check_size(self, data: Any) -> None:
        if (
            len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
            > self.settings.max_payload_bytes
        ):
            raise AppError("PAYLOAD_TOO_LARGE")

    async def connect(self, sid: str, environ: dict[str, Any]) -> None:
        self.locales[sid] = pick_locale(environ.get("HTTP_ACCEPT_LANGUAGE"))
        session = self.get_repo().get_session(
            session_id_from_cookie_header(environ.get("HTTP_COOKIE"))
        )
        if session:
            self.sessions[sid] = session["id"]
            await self.sio.enter_room(sid, session_room(session["id"]))

    async def emit(self, event: str, data: dict[str, Any], rooms: list[str]) -> None:
        if rooms:
            await self.sio.emit(event, data, room=rooms)

    async def guarded(
        self, sid: str, what: str, handler: Callable[[], Any]
    ) -> dict[str, Any]:
        locale = self.locales.get(sid, "en")
        try:
            return await handler()
        except ValidationError as exc:
            return validation_error(exc).body(locale)
        except AppError as exc:
            return exc.body(locale)
        except Exception:
            logger.exception("%s failed", what)
            return AppError("INTERNAL").body(locale)

    async def viewport(self, sid: str, data: Any) -> dict[str, Any]:
        async def handle() -> dict[str, Any]:
            self.check_size(data)
            if not self.viewport_limit.consume(sid):
                raise AppError("RATE_LIMITED")
            payload = ViewportIn.model_validate(
                data, context={"max_chunks": self.max_chunks}
            )
            canvas = self.get_repo().get_canvas(payload.canvas_id)
            chunks = frozenset(
                (cx, cy)
                for cx, cy in payload.chunks
                if cx * self.chunk_size < canvas["bounds_w"]
                and cy * self.chunk_size < canvas["bounds_h"]
            )
            # Bookkeeping is synchronous; the awaits below only mirror it.
            enter, leave = self.rooms.set_viewport(sid, payload.canvas_id, chunks)
            for name in leave:
                await self.sio.leave_room(sid, name)
            for name in enter:
                await self.sio.enter_room(sid, name)
            return {"ok": True, "lastSeq": canvas["stroke_count"]}

        return await self.guarded(sid, "Viewport update", handle)

    async def add(self, sid: str, data: Any) -> dict[str, Any]:
        async def handle() -> dict[str, Any]:
            self.check_size(data)
            canvas_id = self.rooms.canvas_of(sid)
            if (
                canvas_id is None
                or not isinstance(data, dict)
                or data.get("canvasId") != canvas_id
            ):
                raise AppError("NOT_JOINED")
            if not self.stroke_limit.consume(sid):
                raise AppError("RATE_LIMITED")
            canvas = self.get_repo().get_canvas(canvas_id)
            bounds = (canvas["bounds_w"], canvas["bounds_h"])
            stroke = StrokeIn.model_validate(
                data,
                context={
                    "settings": self.settings,
                    "bounds": bounds,
                    "chunk": self.chunk_size,
                },
            )
            chunk = chunk_of(stroke.points, self.chunk_size, bounds)
            assert chunk is not None  # checked by StrokeIn
            async with self.write_lock:
                saved, inserted = self.get_repo().insert_stroke(
                    stroke, chunk, self.sessions.get(sid)
                )
                # The transaction has committed before any network await.
                if inserted:
                    await self.sio.emit(
                        "stroke:new",
                        saved,
                        room=room(canvas_id, (saved["cx"], saved["cy"])),
                        skip_sid=sid,
                    )
            return {"ok": True, "seq": saved["seq"], "cx": saved["cx"], "cy": saved["cy"]}

        return await self.guarded(sid, "Stroke submission", handle)

    async def disconnect(self, sid: str, reason: str | None = None) -> None:
        self.rooms.drop(sid)
        self.locales.pop(sid, None)
        self.sessions.pop(sid, None)
        self.stroke_limit.buckets.pop(sid, None)
        self.viewport_limit.buckets.pop(sid, None)
