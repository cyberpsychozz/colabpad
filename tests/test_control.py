from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def setup(application: FastAPI) -> tuple[Any, Any, Any, dict[str, float]]:
    """A car, a remote bound to it, an apple and a cow, all live in chunk (0,0)."""
    realtime, sim, repo = application.state.realtime, application.state.sim, application.state.repo
    realtime.sio.emit = AsyncMock()
    realtime.sio.enter_room = AsyncMock()
    realtime.sio.leave_room = AsyncMock()
    now = {"t": 1000.0}
    realtime.control.clock = lambda: now["t"]
    repo.insert_sprite("s", None, "s/cutout.png", "s/sheet.png", {})
    for entity_id, kind, x, params in [
        ("car", "vehicle", 300.0, {"variant": "car"}), ("remote", "remote", 500.0, {}),
        ("apple", "food", 700.0, {}), ("cow", "creature", 900.0, {"locomotion": "walk", "diet": "plants"}),
    ]:
        repo.insert_entity({
            "id": entity_id, "canvas_id": "world", "sprite_id": "s", "archetype": kind, "label": {"en": kind},
            "x": x, "y": 300.0, "facing": "right", "state": "idle", "hp": 100.0, "hunger": 0.0, "thirst": 0.0,
            "energy": 100.0, "params": {"sizeClass": "M", "w": 60, "h": 40, **params}, "origin_x": x, "origin_y": 300.0,
        })  # fmt: skip
        sim.spawn(entity_id)
    return realtime, sim, sim.transport.core.worlds["world"], now


def emitted(realtime: Any, name: str) -> list[tuple[Any, Any]]:
    return [(call.args[1], call.kwargs.get("to")) for call in realtime.sio.emit.call_args_list if call.args[0] == name]


def test_pet_push_and_drag(client: TestClient, application: FastAPI) -> None:
    realtime, sim, world, _ = setup(application)

    async def scenario() -> None:
        interact = realtime.acked("entity:interact", realtime.control.interact)
        assert (await interact("a", {"entityId": "apple", "action": "pet"}))["error"]["code"] == "NOT_JOINED"
        await realtime.viewport("a", {"canvasId": "world", "chunks": [[0, 0]]})
        await sim.step(0.1, 3)
        assert (await interact("a", {"entityId": "nope", "action": "pet"}))["error"]["code"] == "NOT_FOUND"
        assert (await interact("a", {"entityId": "apple", "action": "fly"}))["error"]["field"] == "action"
        assert await interact("a", {"entityId": "cow", "action": "pet"}) == {"ok": True}
        realtime.control.interact_limit.buckets.clear()
        # Dragging: only draggable things, and only with the cursor on them.
        assert (await interact("a", {"entityId": "cow", "action": "drag", "to": [910, 300]}))["error"]["field"] == "action"
        assert (await interact("a", {"entityId": "apple", "action": "drag", "to": [900, 300]}))["error"]["field"] == "to"
        assert await interact("a", {"entityId": "apple", "action": "drag", "to": [760, 320]}) == {"ok": True}
        await sim.step(0.1, 2)
        assert world.pos(world.entities["apple"]) == (760.0, 320.0) and sim.live["apple"]["x"] == 760.0
        # A push shoves it 40 px away from the cursor.
        assert await interact("a", {"entityId": "apple", "action": "push", "to": [750, 320]}) == {"ok": True}
        await sim.step(0.1, 2)
        assert world.pos(world.entities["apple"]) == (800.0, 320.0)
        for _ in range(3):
            limited = await interact("a", {"entityId": "apple", "action": "pet"})
        assert limited["error"]["code"] == "RATE_LIMITED"
        # In a frozen world everyone is a viewer.
        application.state.repo.db.execute("UPDATE canvas SET frozen=1 WHERE id='world'")
        realtime.control.interact_limit.buckets.clear()
        assert (await interact("a", {"entityId": "apple", "action": "pet"}))["error"]["code"] == "FORBIDDEN_ROLE"
        assert (await realtime.add("a", {"canvasId": "world"}))["error"]["code"] == "FORBIDDEN_ROLE"

    client.portal.call(scenario)  # type: ignore[union-attr]


def test_driving_lease_grant_extend_release_expire_and_disconnect(client: TestClient, application: FastAPI) -> None:
    realtime, sim, world, now = setup(application)
    control = realtime.control
    claim = realtime.acked("control:claim", control.claim)

    async def scenario() -> None:
        for sid in ("a", "b"):
            await realtime.viewport(sid, {"canvasId": "world", "chunks": [[0, 0]]})
        # Before the simulation has bound the remote, it is not linked to anything.
        assert await claim("a", {"controllerId": "remote"}) == {"ok": False, "unbound": True}
        assert emitted(realtime, "control:revoked")[-1] == ({"reason": "unbound"}, "a")
        await sim.step(0.1, 10)
        assert sim.live["remote"]["view"]["bound"] == "car"
        assert (await claim("a", {"controllerId": "car"}))["error"]["field"] == "controllerId"

        granted = await claim("a", {"controllerId": "remote"})
        assert granted == {"ok": True, "controllerId": "remote", "vehicleId": "car", "until": 1_060_000}
        assert emitted(realtime, "control:granted")[-1][1] == "a"
        # One driver at a time: the other client is told who and for how long.
        now["t"] += 20
        taken = await claim("b", {"controllerId": "remote"})
        assert taken["error"]["code"] == "CONTROL_TAKEN" and taken["error"]["details"] == {"name": None, "seconds": 40}

        # Input drives the car and extends the lease.
        await control.input("a", {"ax": 1, "ay": 0})
        assert control.leases["remote"].until == now["t"] + 60
        before = world.pos(world.entities["car"])[0]
        await sim.step(0.1, 10)
        assert abs(world.pos(world.entities["car"])[0] - before - 120) < 1  # 120 px/s
        await control.input("b", {"ax": -1, "ay": 0})   # not the driver: ignored
        await realtime.quiet(control.input)("a", {"ax": 5, "ay": 0})  # out of range: dropped
        assert world.inputs["car"] == (1.0, 0.0)

        # Letting go stops it and frees the remote.
        assert await realtime.acked("control:release", control.release)("a", None) == {"ok": True}
        await sim.step(0.1, 2)
        assert "car" not in world.inputs and not control.leases
        assert emitted(realtime, "control:revoked")[-1] == ({"reason": "released", "controllerId": "remote"}, "a")
        assert (await claim("b", {"controllerId": "remote"}))["ok"]

        # With no input for 60 s the lease runs out.
        now["t"] += 61
        await control.sweep()
        assert not control.leases
        assert emitted(realtime, "control:revoked")[-1] == ({"reason": "expired", "controllerId": "remote"}, "b")

        # A disconnect releases at once.
        assert (await claim("a", {"controllerId": "remote"}))["ok"]
        await control.input("a", {"ax": 0, "ay": 1})
        await realtime.disconnect("a")
        await sim.step(0.1, 1)
        assert not control.leases and "car" not in world.inputs

        # If the remote loses its vehicle (a blast unbinds it), the lease ends.
        assert (await claim("b", {"controllerId": "remote"}))["ok"]
        sim.live["remote"]["view"] = {"bound": None}
        await control.sweep()
        assert emitted(realtime, "control:revoked")[-1] == ({"reason": "unbound", "controllerId": "remote"}, "b")

    client.portal.call(scenario)  # type: ignore[union-attr]
