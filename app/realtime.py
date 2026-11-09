"""Socket.IO rooms, ephemeral presence, limits and durable stroke submission."""

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import socketio
from pydantic import ValidationError

from app.config import Settings
from app.errors import AppError, too_large, validation_error
from app.models import JoinIn, StrokeIn
from app.repo import Repository

logger = logging.getLogger(__name__)


class SlidingWindow:
    def __init__(self, spec: str) -> None:
        self.limit, self.seconds = map(int, spec.split("/"))
        self.buckets: dict[str, deque[float]] = {}
        self.last_sweep = 0.0

    def consume(self, key: str) -> bool:
        now = time.monotonic()
        if now - self.last_sweep >= self.seconds:
            self.buckets = {
                key: bucket
                for key, bucket in self.buckets.items()
                if bucket and bucket[-1] > now - self.seconds
            }
            self.last_sweep = now
        bucket = self.buckets.setdefault(key, deque())
        while bucket and bucket[0] <= now - self.seconds:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


@dataclass
class Member:
    canvas_id: str
    name: str | None
    width: int
    height: int


class Realtime:
    def __init__(self, settings: Settings, get_repo: Callable[[], Repository]) -> None:
        self.settings = settings
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

    def names(self, canvas_id: str) -> list[str]:
        return [
            m.name or "Гость" for m in self.members.values() if m.canvas_id == canvas_id
        ]

    async def presence(self, canvas_id: str) -> None:
        await self.sio.emit(
            "presence:update", {"online": self.names(canvas_id)}, room=canvas_id
        )

    def check_size(self, data: Any) -> None:
        if (
            len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
            > self.settings.max_payload_bytes
        ):
            raise too_large()

    async def join(self, sid: str, data: Any) -> dict[str, Any]:
        try:
            self.check_size(data)
            payload = JoinIn.model_validate(data, context={"settings": self.settings})
            canvas = self.get_repo().get_canvas(payload.canvas_id)
            old = self.members.get(sid)
            if (not old or old.canvas_id != payload.canvas_id) and len(
                self.names(payload.canvas_id)
            ) >= self.settings.max_clients_per_canvas:
                raise AppError("ROOM_FULL", "На холсте сейчас слишком много людей.")
            # Reserve capacity before the first await.
            self.members[sid] = Member(
                payload.canvas_id, payload.name, canvas["width"], canvas["height"]
            )
            if old and old.canvas_id != payload.canvas_id:
                await self.sio.leave_room(sid, old.canvas_id)
                await self.presence(old.canvas_id)
            await self.sio.enter_room(sid, payload.canvas_id)
            await self.presence(payload.canvas_id)
            return {
                "ok": True,
                "online": self.names(payload.canvas_id),
                "lastSeq": canvas["stroke_count"],
            }
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
                    "width": member.width,
                    "height": member.height,
                },
            )
            async with self.write_lock:
                saved, inserted = self.get_repo().insert_stroke(stroke, member.name)
                # The transaction has committed before any network await.
                if inserted:
                    await self.sio.emit(
                        "stroke:new", saved, room=member.canvas_id, skip_sid=sid
                    )
                    if saved["seq"] == self.settings.max_strokes_per_canvas:
                        await self.sio.emit(
                            "canvas:full",
                            {"maxStrokes": self.settings.max_strokes_per_canvas},
                            room=member.canvas_id,
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
        member = self.members.pop(sid, None)
        self.stroke_limit.buckets.pop(sid, None)
        if member:
            await self.presence(member.canvas_id)
