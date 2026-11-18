"""Explosive: a 20 s fuse everyone can see, then a blast.

The blast damages everything with health (100 at the centre, 30 at the edge),
removes loose things, sets plants alight within half the radius, unbinds remotes,
sets off other explosives, and asks main to destroy the static strokes it reaches.
"""

import math
from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register
from app.sim.types import base
from app.sim.types.fire import ignite

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

KNOCKBACK = 30.0


def blast_radius(world: "World", entity: "Entity") -> float:
    rules = world.balance["explosive"]
    size = max(entity.params.get("w") or 0, entity.params.get("h") or 0)
    return min(rules["radius_max"], max(rules["radius_min"], rules["radius_mult"] * size))


@register("explosive")
class Explosive(Brain):
    def on_add(self, world: "World", entity: "Entity") -> None:
        world.hot.add(entity.id)  # a lit fuse keeps its chunk awake
        entity.data.setdefault("fuse", world.balance["explosive"]["fuse_s"])
        world.view(entity, fuse=math.ceil(entity.data["fuse"]))

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        entity.data["fuse"] -= dt
        if entity.data["fuse"] <= 0:
            self.explode(world, entity)
            return
        world.view(entity, fuse=math.ceil(entity.data["fuse"]))

    def explode(self, world: "World", entity: "Entity") -> None:
        rules = world.balance["explosive"]
        x, y = world.pos(entity)
        radius = blast_radius(world, entity)
        world.out.append({"op": "explosion", "canvas": world.canvas_id, "x": round(x, 1), "y": round(y, 1), "r": radius})
        world.event("explode", entity, r=radius)
        world.kill(entity, fossil=False, cause="exploded")
        for other in world.near(x, y, radius + 60):
            ox, oy = world.pos(other)
            d = math.hypot(ox - x, oy - y)
            if d > radius + (other.radius * 0.5 if other.type != "water" else 0):
                continue
            if other.type == "explosive":
                other.data["fuse"] = 0  # chain reaction on its next think
                world.think_now(other)
            elif other.type == "remote":
                other.bound_to = None
                other.data.pop("bound", None)
                world.view(other, bound=None)
            elif other.type in ("food", "fire"):
                world.kill(other, fossil=False, cause="blast")
            elif other.has("Health"):
                share = min(1.0, d / radius)
                world.damage(other, rules["damage_center"] + (rules["damage_edge"] - rules["damage_center"]) * share, "blast")
                if world.base_speed[other.i] > 0 and d > 0:
                    world.x[other.i] = min(max(ox + (ox - x) / d * KNOCKBACK, 0), world.bounds[0])
                    world.y[other.i] = min(max(oy + (oy - y) / d * KNOCKBACK, 0), world.bounds[1])
                world.think_now(other)
            if other.type == "plant" and d <= radius / 2 and other.id in world.entities and world.hp[other.i] > 0:
                if base.room_for(world, ox, oy):
                    ignite(world, other)

    def on_remove(self, world: "World", entity: "Entity") -> None:
        world.hot.discard(entity.id)

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        entity.data["fuse"] = max(0.0, entity.data.get("fuse", 0) - elapsed)
