"""Shared pieces for the type modules: geometry, the environment, caps and children."""

import math
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.i18n import LOCALES, translate

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

LIVING = ("creature", "predator", "aquatic", "person")
def _label(key: str) -> dict[str, str]:
    return {locale: translate(locale, f"label.{key}") for locale in LOCALES}


# Things the world makes by itself have no drawing, so they use built-in sprites.
BUILTIN = {
    "apple": {"sprite_id": "builtin-apple", "w": 28, "h": 28, "label": _label("apple")},
    "fish": {"sprite_id": "builtin-fish", "w": 44, "h": 24, "label": _label("fish")},
    "flame": {"sprite_id": "builtin-flame", "w": 36, "h": 48, "label": _label("flame")},
}


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    px, py = polygon[-1]
    for qx, qy in polygon:
        if (qy > y) != (py > y) and x < (px - qx) * (y - qy) / (py - qy) + qx:
            inside = not inside
        px, py = qx, qy
    return inside


def polygon_of(world: "World", water: "Entity") -> list[list[float]]:
    """A water's outline where it is now (it may have been pushed) and at its current size."""
    cached = world.cache.get(water.id)
    x, y = world.pos(water)
    grow = (water.data.get("view") or {}).get("scale", 1.0)
    key = (round(x), round(y), round(grow, 2))
    if cached and cached[0] == key:
        return cached[1]
    base = water.params.get("polygon")
    if not base:
        r = water.radius
        base = [[x + r * math.cos(a / 16 * math.tau), y + r * math.sin(a / 16 * math.tau)] for a in range(16)]
        polygon = base
    else:
        ox = sum(p[0] for p in base) / len(base)
        oy = sum(p[1] for p in base) / len(base)
        # The stored outline is around the spawn position; follow the entity and its growth.
        polygon = [[x + (px - ox) * grow, y + (py - oy) * grow] for px, py in base]
    world.cache[water.id] = (key, polygon)
    return polygon


def water_at(world: "World", x: float, y: float) -> "Entity | None":
    for water in world.of_type("water"):
        wx, wy = world.pos(water)
        reach = water.radius * 2 + 50
        if abs(wx - x) <= reach and abs(wy - y) <= reach and point_in_polygon(x, y, polygon_of(world, water)):
            return water
    return None


def shore_point(world: "World", water: "Entity", x: float, y: float) -> tuple[float, float]:
    """The point of a water's outline nearest to (x, y)."""
    best = min(polygon_of(world, water), key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
    return best[0], best[1]


def nearest_water(world: "World", x: float, y: float, radius: float) -> "tuple[Entity, tuple[float, float]] | None":
    found = None
    for water in world.of_type("water"):
        px, py = shore_point(world, water, x, y)
        d = math.hypot(px - x, py - y)
        if d <= radius and (found is None or d < found[0]):
            found = (d, water, (px, py))
    return (found[1], found[2]) if found else None


def raining_at(world: "World", x: float, y: float) -> bool:
    for cloud in world.of_type("sky"):
        if (cloud.data.get("view") or {}).get("raining"):
            cx, cy = world.pos(cloud)
            if math.hypot(cx - x, cy - y) <= rain_radius(cloud):
                return True
    return False


def rain_radius(cloud: "Entity") -> float:
    return max(200.0, cloud.radius * 2.5)


def sun_near(world: "World", x: float, y: float) -> bool:
    radius = world.balance["sky"]["sun_radius"]
    return any(
        sky.get("Weather", "kind") == "sun" and math.hypot(world.pos(sky)[0] - x, world.pos(sky)[1] - y) <= radius
        for sky in world.of_type("sky")
    )


def growth_boost(world: "World", x: float, y: float) -> float:
    """Plants grow faster by water or in rain (x1.5 to x2) and under a sun (x1.5)."""
    low, high = world.balance["plant"]["wet_growth_mult"]
    boost = 1.0
    if raining_at(world, x, y):
        boost = high
    elif nearest_water(world, x, y, 150) is not None:
        boost = low
    if sun_near(world, x, y):
        boost *= world.balance["sky"]["sun_growth_mult"]
    return boost


def is_meat(world: "World", entity: "Entity") -> bool:
    words = world.balance["nutrition"]["meat_labels"]
    return bool(entity.data.get("meat")) or entity.label.get("en") in words or entity.label.get("ru") in words


def nutrition(world: "World", food: "Entity") -> float:
    table = world.balance["nutrition"]
    value = table["meat"] if is_meat(world, food) else table["apple"]
    return value * (table["cooked_mult"] if food.data.get("cooked") else 1.0)


def room_for(world: "World", x: float, y: float, label: str | None = None) -> bool:
    """Population caps: per label per chunk, living per chunk, living per world."""
    caps = world.config.limits["population"]
    if len(world.entities) >= caps["living_per_world"]:
        return False
    chunk = world.chunk_of(x, y)
    here = [e for e in world.entities.values() if e.chunk == chunk]
    if len(here) >= caps["living_per_chunk"]:
        return False
    if label is not None:
        same = sum(1 for e in here if e.label.get("en") == label)
        if same >= caps["per_label_per_chunk"]:
            return False
    return True


def child_row(type_name: str, x: float, y: float, parent: "Entity | None" = None, builtin: str | None = None, **params: Any) -> dict[str, Any]:
    """A row for an entity the world makes: a copy of its parent's sprite, or a built-in one."""
    if builtin:
        look = BUILTIN[builtin]
        base = {"sizeClass": "S", "w": look["w"], "h": look["h"]}
        sprite, label = look["sprite_id"], look["label"]
    else:
        assert parent is not None
        base = {key: parent.params.get(key) for key in ("sizeClass", "w", "h", "scale", "locomotion", "diet", "role", "variant", "parts")}
        sprite, label = parent.sprite_id, parent.label
    return {
        "id": str(uuid4()), "type": type_name, "label": params.pop("label", label), "x": x, "y": y,
        "facing": parent.facing if parent else "right", "state": "idle", "sprite_id": sprite,
        "parent_id": parent.id if parent else None, "params": {**base, **params},
    }  # fmt: skip
