"""The simulation process: one loop ticking every canvas's world at 10 Hz.

Messages in (from main): world, spawn, chunk, input, release, interact, freeze, stop.
Messages out (to main), batched per tick: delta, event, snapshot, born, death,
explosion, rate. `SimCore` holds the logic and has no I/O, so tests can drive it
directly; `run` is the process entry point around it.
"""

import time
from collections import deque
from pathlib import Path
from typing import Any

from app.config import GameConfig
from app.sim import types  # noqa: F401  (registers every type's brain)
from app.sim.systems import decide, interact
from app.sim.world import World

TICK_BUDGET_S = 0.060
FALLBACK_HZ = 5


class SimCore:
    def __init__(self, config: GameConfig, seed: int | None = None) -> None:
        self.config = config
        self.seed = seed
        self.worlds: dict[str, World] = {}

    def handle(self, message: dict[str, Any]) -> None:
        op = message["op"]
        if op == "world":
            if message["canvas"] not in self.worlds:
                self.worlds[message["canvas"]] = World(
                    message["canvas"], tuple(message["bounds"]), self.config, self.seed
                )
            return
        world = self.worlds.get(message.get("canvas", ""))
        if world is None:
            return
        if op == "spawn":
            world.add(message["entity"])
        elif op == "chunk":
            world.set_subscribed((message["cx"], message["cy"]), message["on"])
        elif op == "input":
            if message["id"] in world.entities:
                world.inputs[message["id"]] = (message["ax"], message["ay"])
        elif op == "release":
            world.inputs.pop(message["id"], None)
        elif op == "interact":
            interact.interact(world, message["id"], message["action"], message.get("to"))
        elif op == "freeze":
            world.frozen = message["frozen"]
        elif op == "command":
            # A type-specific request from main (claim a remote, light a fuse...).
            entity = world.entities.get(message["id"])
            if entity is not None:
                decide.brain(entity).command(world, entity, message)

    def step(self, dt: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for world in self.worlds.values():
            world.tick(dt)
            out.extend(world.drain())
        return out

    def flush(self) -> list[dict[str, Any]]:
        """Everything that is dirty, for a clean shutdown."""
        out: list[dict[str, Any]] = []
        for world in self.worlds.values():
            world.snapshot()
            out.extend(world.drain())
        return out


def run(inbox: Any, outbox: Any, config_dir: str) -> None:
    """Process entry point. Falls back to 5 Hz if ticks do not fit their budget."""
    core = SimCore(GameConfig(Path(config_dir)))
    hz = core.config.balance["tick_hz"]
    recent: deque[float] = deque(maxlen=100)
    next_tick = time.monotonic()
    while True:
        while True:
            try:
                message = inbox.get_nowait()
            except Exception:  # queue.Empty; the queue type depends on the start method
                break
            if message["op"] == "stop":
                outbox.put(core.flush())
                return
            core.handle(message)
        started = time.monotonic()
        batch = core.step(1 / hz)
        recent.append(time.monotonic() - started)
        if len(recent) == recent.maxlen and hz > FALLBACK_HZ:
            if sorted(recent)[94] > TICK_BUDGET_S:
                hz = FALLBACK_HZ
                recent.clear()
                batch.append({"op": "rate", "hz": hz})
        if batch:
            outbox.put(batch)
        next_tick += 1 / hz
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.monotonic()  # we are behind; do not try to catch up
