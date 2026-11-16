"""Predator: hunts creatures, people and aquatics at the shore; fights back; eats meat."""

from typing import TYPE_CHECKING, Any

from app.sim.systems.decide import register
from app.sim.types import base
from app.sim.types.living import Living

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

SHORE = 60.0


@register("predator")
class Predator(Living):
    flees = ("fire", "explosive")

    def is_prey(self, world: "World", other: "Entity") -> bool:
        if other.type in ("creature", "person"):
            return True
        if other.type == "aquatic":
            water = base.water_at(world, *world.pos(other))
            if water is None:
                return True
            x, y = world.pos(other)
            px, py = base.shore_point(world, water, x, y)
            return (px - x) ** 2 + (py - y) ** 2 <= SHORE * SHORE
        return False

    def extra_options(self, world: "World", entity: "Entity", seen: list["Entity"]) -> list[tuple[float, Any]]:
        options: list[tuple[float, Any]] = []
        # Whoever is hitting it comes first.
        attacker = world.entities.get(entity.data.get("attacker") or "")
        if attacker is not None and world.dist(entity, attacker) < 250:
            options.append((90.0, lambda: self.strike(world, entity, attacker)))
        else:
            entity.data.pop("attacker", None)
        if world.hunger[entity.i] > 30:
            prey = None
            for other in seen:
                if self.is_prey(world, other):
                    d = world.dist(entity, other)
                    if prey is None or d < prey[0]:
                        prey = (d, other)
            if prey is not None:
                options.append((40.0 + float(world.hunger[entity.i]), lambda: self.hunt(world, entity, prey[1])))
        return options

    def hunt(self, world: "World", entity: "Entity", prey: "Entity") -> None:
        if self.strike(world, entity, prey):
            # It eats what it killed.
            world.hunger[entity.i] = max(0.0, world.hunger[entity.i] - world.balance["nutrition"]["meat"])
            world.event("eat", entity, target=prey.id)
            world.set_state(entity, "wander")
