"""Water: an area. Drinking, fish and fishing happen here; walkers wade at half speed;
cars cannot enter and boats cannot leave; it puts out fire nearby. Puddles dry under
a sun and grow in rain.
"""

from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register
from app.sim.types import base

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

DRY_PER_S = 0.01     # a puddle under a sun is gone in about 100 s
FILL_PER_S = 0.02
MAX_GROWTH = 1.5


@register("water")
class Water(Brain):
    def is_puddle(self, entity: "Entity") -> bool:
        return entity.params.get("sizeClass") == "S"

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        self.advance(world, entity, dt)

    def advance(self, world: "World", entity: "Entity", dt: float) -> None:
        if not self.is_puddle(entity):
            return
        x, y = world.pos(entity)
        scale = (entity.data.get("view") or {}).get("scale", 1.0)
        if base.raining_at(world, x, y):
            scale = min(MAX_GROWTH, scale + FILL_PER_S * dt)
        elif base.sun_near(world, x, y):
            scale -= DRY_PER_S * dt
        else:
            return
        if scale <= 0.2:
            world.kill(entity, fossil=True, cause="dried")
            return
        world.view(entity, scale=round(scale, 2))

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        self.advance(world, entity, elapsed)
