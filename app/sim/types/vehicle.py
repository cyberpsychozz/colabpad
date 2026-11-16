"""Vehicle: shakes when idle, moves only when someone drives it (120 px/s), hits walkers.

A car cannot enter water, a boat cannot leave it, a plane goes anywhere.
"""

import math
from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register
from app.sim.types import base

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

KNOCKBACK = 30.0


@register("vehicle")
class Vehicle(Brain):
    def medium(self, entity: "Entity") -> str:
        return entity.get("Controllable", "medium") or "land"

    def can_enter(self, world: "World", entity: "Entity", x: float, y: float) -> bool:
        medium = self.medium(entity)
        if medium == "air":
            return True
        wet = base.water_at(world, x, y) is not None
        return wet if medium == "water" else not wet

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        i = entity.i
        speed = math.hypot(world.vx[i], world.vy[i])
        driven = entity.id in world.inputs
        world.set_state(entity, "driven" if driven else "idle")
        rules = world.balance["vehicle"]
        if not driven or speed <= rules["hit_speed"]:
            return
        x, y = world.pos(entity)
        for other in world.near(x, y, entity.radius + 20, entity):
            if other.type not in base.LIVING or world.t < other.data.get("hit_after", 0):
                continue
            other.data["hit_after"] = world.t + 1.0  # one hit per second per victim
            world.damage(other, rules["hit_damage"], "run over")
            ox, oy = world.pos(other)
            d = math.hypot(ox - x, oy - y) or 1.0
            world.x[other.i] = min(max(ox + (ox - x) / d * KNOCKBACK, 0), world.bounds[0])
            world.y[other.i] = min(max(oy + (oy - y) / d * KNOCKBACK, 0), world.bounds[1])
            world.touched[other.i] = True
            world.think_now(other)
            world.event("hit", other, by=entity.id)
