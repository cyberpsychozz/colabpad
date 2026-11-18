"""Living scribble: hops about, can be pushed and dragged, edible only by omnivores."""

import math
from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World


@register("doodle")
class Doodle(Brain):
    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        if world.has_goal[entity.i] or world.rng.random() < 0.6:
            return
        x, y = world.pos(entity)
        angle, distance = world.rng.uniform(0, math.tau), world.rng.uniform(20, 80)
        world.set_state(entity, "wander")
        world.go(entity, x + math.cos(angle) * distance, y + math.sin(angle) * distance, 0.5)
