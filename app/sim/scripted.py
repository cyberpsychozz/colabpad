"""Simulation-owned repeated playback. No game systems run for visual entities."""

import time

from app.alive.choreography import playback_time, pose


def checkpoint(world, entity, advance=0):
    playback = entity.data.setdefault("playback", {"elapsed": 0, "revision": 0})
    plan = entity.params["plan"]
    pause_ms = world.config.vlm.get("generation", {}).get("loop_pause_ms", 2000)
    playback["pauseMs"] = pause_ms
    period = plan["duration"] + pause_ms
    before = playback["elapsed"]
    previous_phase = playback_time(plan, before, pause_ms)
    cx, cy = world.chunk_of(*world.pos(entity))
    running = not world.frozen and bool(world.active_grid[cy, cx])
    if advance and running:
        playback["elapsed"] = round(before + advance * 1000, 6)
        phase = playback_time(plan, playback["elapsed"], pause_ms)
        p = pose(plan, phase, "root")
        ox, oy = entity.params["origin"]
        world.x[entity.i], world.y[entity.i] = ox + p["x"], oy + p["y"]
        cx, cy = world.chunk_of(*world.pos(entity))
        running = bool(world.active_grid[cy, cx])
        entity.dirty = True
    else:
        phase = previous_phase
    boundary = (
        before // period != playback["elapsed"] // period
        or previous_phase < plan["duration"] <= phase
    )
    previous = entity.data.get("view", {}).get("playback", {})
    if (
        previous.get("running") != running
        or previous.get("pauseMs") != pause_ms
        or previous.get("elapsed") != playback["elapsed"]
        and (boundary or world.tick_n % 2 == 0)
    ):
        playback["revision"] += 1
        world.view(
            entity,
            playback={
                **playback,
                "running": running,
                "serverTime": round(time.time() * 1000),
            },
        )
