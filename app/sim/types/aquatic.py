"""Aquatic: lives only in water, takes damage outside it, eats food dropped in water."""

import math
from typing import TYPE_CHECKING

from app.sim.systems.decide import register
from app.sim.types import base
from app.sim.types.living import Living

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World


@register("aquatic")
class Aquatic(Living):
    flees = ("predator",)

    def suffer(self, world: "World", entity: "Entity", dt: float) -> None:
        water = base.water_at(world, *world.pos(entity))
        entity.in_water = water is not None
        if water is None:
            world.damage(entity, world.balance["aquatic"]["dry_damage_per_s"] * dt, "dried")
            # Flop towards the nearest water.
            found = base.nearest_water(world, *world.pos(entity), 400)
            if found is not None:
                wx, wy = world.pos(found[0])
                world.set_state(entity, "flee")
                world.go(entity, wx, wy)
                entity.timers["flop"] = 1
            return
        entity.timers.pop("flop", None)

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        self.suffer(world, entity, dt)
        if not entity.in_water:
            self.grow(world, entity)
            return
        # Living.think calls suffer again; it is cheap and keeps one code path.
        super().think(world, entity, 0.0 if dt == 0 else dt)

    def can_go(self, world: "World", entity: "Entity", x: float, y: float) -> bool:
        return base.water_at(world, x, y) is not None

    def safe_point(self, world: "World", entity: "Entity") -> tuple[float, float]:
        water = base.water_at(world, *world.pos(entity))
        return world.pos(water) if water is not None else world.pos(entity)

    def seek_food(self, world: "World", entity: "Entity", food: "Entity") -> None:
        fx, fy = world.pos(food)
        if base.water_at(world, fx, fy) is None:
            self.wander(world, entity)
            return
        super().seek_food(world, entity, food)

    def wander(self, world: "World", entity: "Entity") -> None:
        if entity.state not in ("wander", "idle"):
            world.set_state(entity, "wander")
        if world.has_goal[entity.i] or world.rng.random() < 0.4:
            return
        water = base.water_at(world, *world.pos(entity))
        if water is None:
            return
        # A point between the middle of the water and a random spot of its outline.
        cx, cy = world.pos(water)
        px, py = world.rng.choice(base.polygon_of(world, water))
        t = world.rng.uniform(0.1, 0.8)
        gx, gy = cx + (px - cx) * t, cy + (py - cy) * t
        if math.hypot(gx - cx, gy - cy) >= 0 and self.can_go(world, entity, gx, gy):
            world.go(entity, gx, gy, 0.6)
