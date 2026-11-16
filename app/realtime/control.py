"""Playing with the world over the socket: pet, push and drag, and the driving lease.

A remote is bound to a vehicle by the simulation. Clicking the remote claims a 60 s
lease on that vehicle; input extends it; one driver at a time. Leases live in main.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.errors import AppError
from app.models import ClaimIn, InputIn, InteractIn
from app.realtime.ratelimit import SlidingWindow

if TYPE_CHECKING:
    from app.realtime.gateway import Realtime


@dataclass
class Lease:
    sid: str
    session_id: str | None
    name: str | None
    canvas_id: str
    vehicle_id: str
    until: float


class Control:
    def __init__(self, realtime: "Realtime", limits: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.realtime, self.limits, self.clock = realtime, limits, clock
        self.leases: dict[str, Lease] = {}  # by remote (controller) id
        self.interact_limit = SlidingWindow(limits["interact"]["rate"])
        self.input_limit = SlidingWindow(limits["control"]["input_rate"])

    @property
    def sim(self) -> Any:
        return self.realtime.sim

    def entity(self, sid: str, entity_id: str) -> tuple[str, dict[str, Any]]:
        """The live entity, which must be on the canvas this socket is watching."""
        canvas_id = self.realtime.rooms.canvas_of(sid)
        entity = self.sim.live.get(entity_id) if self.sim is not None else None
        if canvas_id is None:
            raise AppError("NOT_JOINED")
        if entity is None or self.sim.canvas_of.get(entity_id) != canvas_id:
            raise AppError("NOT_FOUND")
        self.realtime.require_role(sid, canvas_id, "player")
        return canvas_id, entity

    # ---- pet, push, drag

    async def interact(self, sid: str, data: Any) -> dict[str, Any]:
        if not self.interact_limit.consume(sid):
            raise AppError("RATE_LIMITED")
        payload = InteractIn.model_validate(data)
        canvas_id, entity = self.entity(sid, payload.entity_id)
        reach = self.limits["interact"]["cursor_radius"] + max(entity.get("w") or 0, entity.get("h") or 0) / 2
        if payload.action != "pet":
            # The cursor must be on or right next to the thing it acts on.
            if payload.to is None or math.hypot(payload.to[0] - entity["x"], payload.to[1] - entity["y"]) > reach:
                raise AppError("VALIDATION_FAILED", "to")
        if payload.action == "drag" and entity["type"] not in self.limits["interact"]["draggable"]:
            raise AppError("VALIDATION_FAILED", "action")
        self.sim.send(
            {"op": "interact", "canvas": canvas_id, "id": payload.entity_id, "action": payload.action,
             "to": list(payload.to) if payload.to else None}  # fmt: skip
        )
        return {"ok": True}

    # ---- the driving lease

    def bound_vehicle(self, remote: dict[str, Any]) -> str | None:
        vehicle_id = (remote.get("view") or {}).get("bound")
        return vehicle_id if vehicle_id in self.sim.live else None

    async def claim(self, sid: str, data: Any) -> dict[str, Any]:
        payload = ClaimIn.model_validate(data)
        canvas_id, remote = self.entity(sid, payload.controller_id)
        if remote["type"] != "remote":
            raise AppError("VALIDATION_FAILED", "controllerId")
        await self.sweep()
        vehicle_id = self.bound_vehicle(remote)
        if vehicle_id is None:
            await self.realtime.sio.emit("control:revoked", {"reason": "unbound"}, to=sid)
            return {"ok": False, "unbound": True}
        lease = self.leases.get(payload.controller_id)
        now = self.clock()
        if lease is not None and lease.sid != sid:
            raise AppError("CONTROL_TAKEN", details={"name": lease.name, "seconds": max(1, round(lease.until - now))})
        # One vehicle per driver: taking a new one lets go of the old one.
        for other_id, other in list(self.leases.items()):
            if other.sid == sid and other_id != payload.controller_id:
                await self.revoke(other_id, "released")
        until = now + self.limits["control"]["lease_s"]
        session = self.realtime.get_repo().get_session(self.realtime.sessions.get(sid))
        self.leases[payload.controller_id] = Lease(
            sid, session and session["id"], session and session["nickname"], canvas_id, vehicle_id, until
        )
        granted = {"controllerId": payload.controller_id, "vehicleId": vehicle_id, "until": round(until * 1000)}
        await self.realtime.sio.emit("control:granted", granted, to=sid)
        return {"ok": True, **granted}

    def lease_of(self, sid: str) -> tuple[str, Lease] | None:
        return next(((remote_id, lease) for remote_id, lease in self.leases.items() if lease.sid == sid), None)

    async def input(self, sid: str, data: Any) -> None:
        """Stick position while driving. Extra messages beyond 20 a second are dropped."""
        held = self.lease_of(sid)
        if held is None or not self.input_limit.consume(sid):
            return
        payload = InputIn.model_validate(data)
        _, lease = held
        lease.until = self.clock() + self.limits["control"]["lease_s"]  # input extends the lease
        self.sim.send({"op": "input", "canvas": lease.canvas_id, "id": lease.vehicle_id, "ax": payload.ax, "ay": payload.ay})

    async def release(self, sid: str, data: Any = None) -> dict[str, Any]:
        held = self.lease_of(sid)
        if held is not None:
            await self.revoke(held[0], "released")
        return {"ok": True}

    async def revoke(self, remote_id: str, reason: str) -> None:
        lease = self.leases.pop(remote_id, None)
        if lease is None:
            return
        if self.sim is not None:
            self.sim.send({"op": "release", "canvas": lease.canvas_id, "id": lease.vehicle_id})
        await self.realtime.sio.emit("control:revoked", {"reason": reason, "controllerId": remote_id}, to=lease.sid)

    async def sweep(self) -> None:
        """End leases that ran out, or whose remote lost its vehicle (a blast unbinds it)."""
        now = self.clock()
        for remote_id, lease in list(self.leases.items()):
            remote = self.sim.live.get(remote_id) if self.sim is not None else None
            if remote is None or self.bound_vehicle(remote) != lease.vehicle_id:
                await self.revoke(remote_id, "unbound")
            elif lease.until <= now:
                await self.revoke(remote_id, "expired")

    async def disconnect(self, sid: str) -> None:
        held = self.lease_of(sid)
        if held is not None:
            await self.revoke(held[0], "disconnected")
        self.interact_limit.buckets.pop(sid, None)
        self.input_limit.buckets.pop(sid, None)
