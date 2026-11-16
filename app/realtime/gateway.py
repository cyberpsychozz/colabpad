"""Socket.IO events: canvas rooms, limits and durable stroke submission."""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import socketio
from pydantic import ValidationError

from app.config import GameConfig, Settings
from app.errors import AppError, too_large, validation_error
from app.models import JoinIn, StrokeIn, chunk_of
from app.realtime.ratelimit import SlidingWindow
from app.repo import Repository

logger = logging.getLogger(__name__)


@dataclass
class Member:
    canvas_id: str
    bounds: tuple[int, int]


class Realtime:
    def __init__(
        self, settings: Settings, config: GameConfig, get_repo: Callable[[], Repository]
    ) -> None:
        self.settings = settings
        self.chunk_size: int = config.limits["world"]["chunk_size"]
        self.get_repo = get_repo
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=settings.origins,
            max_http_buffer_size=settings.max_payload_bytes,
            async_handlers=False,
            logger=False,
            engineio_logger=False,
        )
        self.members: dict[str, Member] = {}
        self.stroke_limit = SlidingWindow(settings.stroke_rate_limit)
        self.write_lock = asyncio.Lock()
        self.sio.on("canvas:join", self.join)
        self.sio.on("stroke:add", self.add)
        self.sio.on("disconnect", self.disconnect)

    def check_size(self, data: Any) -> None:
        if (
            len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
            > self.settings.max_payload_bytes
        ):
            raise too_large()

    async def join(self, sid: str, data: Any) -> dict[str, Any]:
        try:
            self.check_size(data)
            payload = JoinIn.model_validate(data)
            canvas = self.get_repo().get_canvas(payload.canvas_id)
            old = self.members.get(sid)
            self.members[sid] = Member(
                payload.canvas_id, (canvas["bounds_w"], canvas["bounds_h"])
            )
            if old and old.canvas_id != payload.canvas_id:
                await self.sio.leave_room(sid, old.canvas_id)
            await self.sio.enter_room(sid, payload.canvas_id)
            return {"ok": True, "lastSeq": canvas["stroke_count"]}
        except ValidationError as exc:
            return validation_error(exc).body()
        except AppError as exc:
            return exc.body()
        except Exception:
            logger.exception("Canvas join failed")
            return AppError("INTERNAL", "Ошибка сервера.", http_status=500).body()

    async def add(self, sid: str, data: Any) -> dict[str, Any]:
        try:
            self.check_size(data)
            member = self.members.get(sid)
            if (
                not member
                or not isinstance(data, dict)
                or data.get("canvasId") != member.canvas_id
            ):
                raise AppError("NOT_JOINED", "Сначала подключись к холсту.")
            if not self.stroke_limit.consume(sid):
                raise AppError(
                    "RATE_LIMITED",
                    "Слишком много штрихов. Подожди немного.",
                    http_status=429,
                )
            stroke = StrokeIn.model_validate(
                data,
                context={
                    "settings": self.settings,
                    "bounds": member.bounds,
                    "chunk": self.chunk_size,
                },
            )
            async with self.write_lock:
                chunk = chunk_of(stroke.points, self.chunk_size, member.bounds)
                assert chunk is not None  # checked by StrokeIn
                saved, inserted = self.get_repo().insert_stroke(stroke, chunk)
                # The transaction has committed before any network await.
                if inserted:
                    await self.sio.emit(
                        "stroke:new", saved, room=member.canvas_id, skip_sid=sid
                    )
            return {"ok": True, "seq": saved["seq"]}
        except ValidationError as exc:
            return validation_error(exc).body()
        except AppError as exc:
            return exc.body()
        except Exception:
            logger.exception("Stroke submission failed")
            return AppError("INTERNAL", "Ошибка сервера.", http_status=500).body()

    async def disconnect(self, sid: str, reason: str | None = None) -> None:
        self.members.pop(sid, None)
        self.stroke_limit.buckets.pop(sid, None)
