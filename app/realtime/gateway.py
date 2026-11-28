"""Socket.IO events: chunk subscriptions, limits and durable stroke submission."""

import asyncio
import json
import logging
import math
import zlib
from collections.abc import Callable
from typing import Any

import socketio
from pydantic import ValidationError

from app.config import GameConfig, Settings
from app.errors import AppError, validation_error
from app.i18n import pick_locale
from app.models import StrokeIn, ViewportIn, chunk_of
from app.auth.sessions import session_id_from_cookie_header
from app.realtime.control import Control
from app.realtime.ratelimit import SlidingWindow
from app.realtime.rooms import Rooms, room, session_room
from app.repo import Repository

logger = logging.getLogger(__name__)


class TextPacket(socketio.packet.Packet):
    """We never send binary, so skip socketio's scan of every payload for bytes; on a busy
    world that recursive scan over each delta row was a large share of main's CPU."""

    uses_binary_events = False


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
            serializer=TextPacket,
            logger=False,
            engineio_logger=False,
        )
        self.rooms = Rooms()
        self.stroke_limit = SlidingWindow(config.limits["stroke"]["rate"])
        self.viewport_limit = SlidingWindow(config.limits["viewport"]["rate"])
        self.write_lock = asyncio.Lock()
        self.locales: dict[str, str] = {}
        self.sessions: dict[str, str] = {}
        self.cursors_meta: dict[str, tuple[str, str]] = {}
        self.sim: Any = None  # the SimBridge, once started
        self.sio.on("connect", self.connect)
        self.sio.on("viewport:set", self.viewport)
        self.sio.on("stroke:add", self.add)
        self.sio.on("disconnect", self.disconnect)
        self.sio.on("cursor:move", self.quiet(self.cursor))
        self.control = Control(self, config.limits)
        for event, handler in (
            ("entity:interact", self.control.interact),
            ("control:claim", self.control.claim),
            ("control:release", self.control.release),
        ):
            self.sio.on(event, self.acked(event, handler))
        self.sio.on("control:input", self.quiet(self.control.input))

    def acked(self, what: str, handler: Callable[[str, Any], Any]) -> Callable[[str, Any], Any]:
        async def wrapped(sid: str, data: Any = None) -> dict[str, Any]:
            return await self.guarded(sid, what, lambda: handler(sid, data))

        return wrapped

    def quiet(self, handler: Callable[[str, Any], Any]) -> Callable[[str, Any], Any]:
        """For high-rate events without an ack: bad input is dropped, not answered."""

        async def wrapped(sid: str, data: Any = None) -> None:
            try:
                await handler(sid, data)
            except (ValidationError, AppError):
                pass

        return wrapped

    ROLES = ("none", "viewer", "player", "editor")

    def role(self, sid: str, canvas_id: str) -> str:
        """The socket's effective role on a canvas (docs/spec.md §5)."""
        repo = self.get_repo()
        return repo.role_of(repo.get_canvas(canvas_id), repo.get_session(self.sessions.get(sid)))

    async def _revoke_if_held(self, sid: str, canvas_id: str) -> None:
        held = self.control.lease_of(sid)
        if held is not None and held[1].canvas_id == canvas_id:
            await self.control.revoke(held[0], "released")

    async def recheck(self, canvas_id: str) -> None:
        """Access changed: drop sockets that lost view access and revoke driving leases below player."""
        repo = self.get_repo()
        try:
            canvas = repo.get_canvas(canvas_id)
        except AppError:
            canvas = None
        for sid, (watched, _) in list(self.rooms.viewports.items()):
            if watched == canvas_id:
                role = repo.role_of(canvas, repo.get_session(self.sessions.get(sid))) if canvas else "none"
                if self.ROLES.index(role) < self.ROLES.index("player"):
                    await self._revoke_if_held(sid, canvas_id)
                    if self.cursors_meta.pop(sid, None) is not None:
                        for peer, (watched2, _) in list(self.rooms.viewports.items()):
                            if watched2 == canvas_id and peer != sid:
                                await self.sio.emit("cursor:leave", {"id": sid}, to=peer)
                if role == "none":
                    await self.sio.emit("canvas:kicked", {"canvasId": canvas_id}, to=sid)
                    await self.sio.disconnect(sid)
                else:
                    await self.sio.emit("canvas:role", {"role": role}, to=sid)

    async def kick(self, canvas_id: str, user_id: str) -> None:
        """Immediately eject a user's active sockets from a private canvas."""
        repo = self.get_repo()
        for sid, (watched, _) in list(self.rooms.viewports.items()):
            if watched != canvas_id:
                continue
            session = repo.get_session(self.sessions.get(sid))
            if session and session.get("user_id") == user_id:
                await self._revoke_if_held(sid, canvas_id)
                await self.sio.emit("canvas:kicked", {"canvasId": canvas_id}, to=sid)
                await self.sio.disconnect(sid)

    async def frozen(self, canvas_id: str, frozen: bool) -> None:
        for sid, (watched, _) in list(self.rooms.viewports.items()):
            if watched == canvas_id:
                await self.sio.emit("canvas:frozen", {"frozen": frozen}, to=sid)

    def require_role(self, sid: str, canvas_id: str, needed: str) -> None:
        if self.ROLES.index(self.role(sid, canvas_id)) < self.ROLES.index(needed):
            raise AppError("FORBIDDEN_ROLE")

    def check_size(self, data: Any) -> None:
        if (
            len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
            > self.settings.max_payload_bytes
        ):
            raise AppError("PAYLOAD_TOO_LARGE")

    async def connect(self, sid: str, environ: dict[str, Any]) -> None:
        self.locales[sid] = pick_locale(environ.get("HTTP_ACCEPT_LANGUAGE"))
        session = self.get_repo().cookie_session(
            session_id_from_cookie_header(environ.get("HTTP_COOKIE"))
        )
        if session:
            self.sessions[sid] = session["id"]
            await self.sio.enter_room(sid, session_room(session["id"]))

    async def emit(self, event: str, data: dict[str, Any], rooms: list[str]) -> None:
        if rooms:
            await self.sio.emit(event, data, room=rooms)

    async def emit_deltas(self, canvas_id: str, tick: int, chunks: list[dict[str, Any]]) -> None:
        """One world:delta per client per tick, holding every watched chunk that changed.

        Each chunk is encoded once and the pieces are joined per client, instead of one
        message per chunk per client: a client watching 12 chunks gets 1 message, not 12,
        which is what keeps compression and socket writes affordable with many clients.
        """
        encoded = {
            (chunk["cx"], chunk["cy"]): json.dumps(chunk, separators=(",", ":"), ensure_ascii=False)
            for chunk in chunks
        }
        for sid, (canvas, watched) in list(self.rooms.viewports.items()):
            if canvas != canvas_id:
                continue
            parts = [encoded[chunk] for chunk in watched if chunk in encoded]
            if not parts:
                continue
            eio_sid = self.sio.manager.eio_sid_from_sid(sid, "/")
            if eio_sid is None:
                continue
            # A Socket.IO EVENT packet on the default namespace: "2" + JSON [name, data].
            text = f'2["world:delta",{{"tick":{tick},"chunks":[{",".join(parts)}]}}]'
            await self.sio.eio.send(eio_sid, text)

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
            self.require_role(sid, payload.canvas_id, "viewer")
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
            if self.sim is not None:
                # world:snapshot for every chunk this client has just started watching.
                size = self.chunk_size
                for name in enter:
                    cx, cy = (int(part) for part in name.rsplit(":", 2)[1:])
                    entities = self.sim.entities_in(
                        payload.canvas_id, cx * size, cy * size, (cx + 1) * size, (cy + 1) * size
                    )
                    await self.sio.emit(
                        "world:snapshot", {"cx": cx, "cy": cy, "entities": entities}, to=sid
                    )
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
            self.require_role(sid, canvas_id, "editor")
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

    async def cursor(self, sid: str, data: Any) -> None:
        """Broadcast a user's pointer position to other viewers on the same canvas."""
        canvas_id = self.rooms.canvas_of(sid)
        if not canvas_id or not isinstance(data, dict):
            return
        repo = self.get_repo()
        canvas = repo.get_canvas(canvas_id)
        if canvas["kind"] != "private":
            return
        role = repo.role_of(canvas, repo.get_session(self.sessions.get(sid)))
        if self.ROLES.index(role) < self.ROLES.index("player"):
            return
        x, y = data.get("x"), data.get("y")
        if not (isinstance(x, (int, float)) and isinstance(y, (int, float)) and math.isfinite(x) and math.isfinite(y)):
            return
        meta = self.cursors_meta.get(sid)
        if meta is None:
            session = repo.get_session(self.sessions.get(sid))
            raw = None
            if session and session.get("user_id"):
                row = repo.db.execute("SELECT email FROM app_user WHERE id=?", (session["user_id"],)).fetchone()
                if row:
                    raw = row["email"].split("@")[0]
            if not raw:
                raw = (session.get("nickname") if session else None) or (
                    "Гость" if self.locales.get(sid) == "ru" else "Guest"
                )
            palette = ("#e11d48", "#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d")
            used = {
                m[1]
                for peer, (watched, _) in self.rooms.viewports.items()
                if watched == canvas_id and peer != sid and (m := self.cursors_meta.get(peer)) is not None
            }
            color = next((c for c in palette if c not in used), palette[zlib.crc32(sid.encode()) % len(palette)])
            meta = self.cursors_meta[sid] = (raw[:14], color)
        name, color = meta
        payload = {"id": sid, "x": float(x), "y": float(y), "name": name, "color": color, "role": role}
        for peer, (watched, _) in list(self.rooms.viewports.items()):
            if watched == canvas_id and peer != sid:
                await self.sio.emit("cursor:move", payload, to=peer)

    async def disconnect(self, sid: str, reason: str | None = None) -> None:
        canvas_id = self.rooms.canvas_of(sid)
        self.cursors_meta.pop(sid, None)
        if canvas_id:
            for peer, (watched, _) in list(self.rooms.viewports.items()):
                if watched == canvas_id and peer != sid:
                    await self.sio.emit("cursor:leave", {"id": sid}, to=peer)
        await self.control.disconnect(sid)
        self.rooms.drop(sid)
        self.locales.pop(sid, None)
        self.sessions.pop(sid, None)
        self.stroke_limit.buckets.pop(sid, None)
        self.viewport_limit.buckets.pop(sid, None)
