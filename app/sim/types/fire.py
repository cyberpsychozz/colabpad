"""Fire: ignites flammable things nearby, burns those close to it, is put out by water or rain.

A wild fire burns for 20 s and spreads; a campfire stays until doused and does not
spread. Burning a plant or a house is a child fire sitting on it: when that fire
burns out, its host is gone. Fire never touches static strokes.
"""

from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register
from app.sim.types import base

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World


def ignite(world: "World", target: "Entity") -> "Entity | None":
    """Set a flammable entity alight (once)."""
    if not target.has("Flammable") or target.get("Flammable", "cooks") or target.data.get("burning"):
        return None
    x, y = world.pos(target)
    if base.water_at(world, x, y) or base.raining_at(world, x, y):
        return None
    flame = world.spawn_child(base.child_row("fire", x, y, builtin="flame", variant=None, data={"host": target.id}))
    target.data["burning"] = flame.id
    world.view(target, burning=True)
    world.event("ignite", target, by=flame.id)
    return flame


@register("fire")
class Fire(Brain):
    def on_add(self, world: "World", entity: "Entity") -> None:
        world.hot.add(entity.id)  # a fire keeps its chunk awake
        entity.data.setdefault("left", world.balance["fire"]["burn_s"])

    def campfire(self, entity: "Entity") -> bool:
        return entity.get("Fire", "kind") == "campfire"

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        rules = world.balance["fire"]
        x, y = world.pos(entity)
        host = world.entities.get(entity.data.get("host") or "")
        if entity.data.get("host") and host is None:
            world.kill(entity, fossil=False, cause="burned out")
            return
        # Water within 40 px, or rain, puts it out; the host survives.
        doused = base.raining_at(world, x, y) or base.nearest_water(world, x, y, world.balance["water"]["douse_radius"]) is not None
        if doused:
            if host is not None:
                host.data.pop("burning", None)
                world.view(host, burning=False)
            world.event("douse", entity)
            world.kill(entity, fossil=False, cause="doused")
            return
        near = world.near(x, y, max(rules["spread_radius"], rules["damage_radius"]) + 60, entity)
        for other in near:
            d = world.dist(entity, other)
            if other.type == "explosive" and d <= rules["spread_radius"] + other.radius:
                other.data["fuse"] = 0  # fire next to a bomb: it goes off now
                world.think_now(other)
            elif other.type in base.LIVING and d <= rules["damage_radius"] + other.radius * 0.5:
                world.damage(other, rules["damage_per_s"] * dt, "burned")
                world.think_now(other)
            elif other.type == "food" and d <= rules["spread_radius"] and not other.data.get("cooked"):
                other.data["cooked"] = True
                world.view(other, cooked=True)
                world.event("cook", other)
        if not self.campfire(entity):
            entity.timers["spread"] = entity.timers.get("spread", rules["spread_period_s"]) - dt
            if entity.timers["spread"] <= 0:
                entity.timers["spread"] = rules["spread_period_s"]
                for other in near:
                    if other is host or world.dist(entity, other) > rules["spread_radius"] + other.radius * 0.5:
                        continue
                    if world.rng.random() < rules["spread_chance"] and base.room_for(world, x, y):
                        ignite(world, other)
            entity.data["left"] -= dt
            if entity.data["left"] <= 0:
                if host is not None:
                    # What it sat on has burned down.
                    world.kill(host, fossil=True, cause="burned")
                world.kill(entity, fossil=False, cause="burned out")

    def on_remove(self, world: "World", entity: "Entity") -> None:
        world.hot.discard(entity.id)

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        if not self.campfire(entity):
            entity.data["left"] = max(0.0, entity.data.get("left", 0) - elapsed)
