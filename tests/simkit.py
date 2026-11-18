"""Helpers for simulation tests: a world with a fixed seed and compact entity rows."""

from pathlib import Path
from typing import Any

from app.config import GameConfig
from app.sim import types  # noqa: F401
from app.sim.world import World

CONFIG_DIR = Path(__file__).parent.parent / "config"
BOUNDS = (19456, 11264)


def make_world(seed: int = 1, day_phase: float = 0.1, subscribe: tuple[int, int] | None = (0, 0)) -> World:
    world = World("world", BOUNDS, GameConfig(CONFIG_DIR), seed=seed, day_phase=day_phase)
    if subscribe is not None:
        world.set_subscribed(subscribe, True)
    return world


_counter = 0


def row(type_name: str, x: float, y: float, **extra: Any) -> dict[str, Any]:
    """An entity row as main sends it. Keyword extras override params or top-level fields."""
    global _counter
    _counter += 1
    top = {key: extra.pop(key) for key in list(extra) if key in ("id", "hp", "hunger", "thirst", "energy", "label", "state", "facing")}
    params = {"sizeClass": "M", "w": 60, "h": 40, **extra}
    return {"id": f"{type_name}-{_counter}", "type": type_name, "label": {"ru": type_name, "en": type_name},
            "x": x, "y": y, "params": params, **top}  # fmt: skip


def run(world: World, seconds: float, dt: float = 0.1) -> list[dict[str, Any]]:
    """Tick for a while; returns everything the world sent."""
    out: list[dict[str, Any]] = []
    for _ in range(round(seconds / dt)):
        world.tick(dt)
        out.extend(world.drain())
    return out


def events(out: list[dict[str, Any]], kind: str | None = None) -> list[dict[str, Any]]:
    return [m for m in out if m["op"] == "event" and (kind is None or m["type"] == kind)]


def ops(out: list[dict[str, Any]], op: str) -> list[dict[str, Any]]:
    return [m for m in out if m["op"] == op]
