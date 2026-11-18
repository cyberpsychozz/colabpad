"""Sky: a cloud drifts and rains for 30 s every 2 minutes; a sun makes plants grow and
puddles dry. Rain fills water, grows plants, puts out fire and sends people indoors
(those effects live with the things they affect; see base.raining_at). Never dies.
"""

import math
from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

DRIFT_SPEED = 8.0


@register("sky")
class Sky(Brain):
    def is_cloud(self, entity: "Entity") -> bool:
        return entity.get("Weather", "kind") != "sun"

    def on_add(self, world: "World", entity: "Entity") -> None:
        if self.is_cloud(entity):
            world.base_speed[entity.i] = DRIFT_SPEED
            entity.data.setdefault("rain_in", world.balance["sky"]["rain_period_s"] - world.balance["sky"]["rain_s"])
            entity.data.setdefault("view", {}).setdefault("raining", False)

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        if not self.is_cloud(entity):
            return
        self.weather(world, entity, dt)
        if not world.has_goal[entity.i]:
            x, y = world.pos(entity)
            angle = world.rng.uniform(-0.4, 0.4) + (0 if entity.facing == "right" else math.pi)
            gx = x + math.cos(angle) * 300
            if not 0 < gx < world.bounds[0]:
                gx = x - math.cos(angle) * 300  # turn back at the edge of the world
            world.go(entity, gx, min(max(y + math.sin(angle) * 300, 0), world.bounds[1]))

    def weather(self, world: "World", entity: "Entity", dt: float) -> None:
        sky = world.balance["sky"]
        raining = entity.data["view"].get("raining", False)
        entity.data["rain_in"] -= dt
        if entity.data["rain_in"] <= 0:
            # Rain for 30 s, then dry for the rest of the 2-minute cycle.
            entity.data["rain_in"] = sky["rain_period_s"] - sky["rain_s"] if raining else sky["rain_s"]
            world.view(entity, raining=not raining)
            world.event("rain" if not raining else "rain_stop", entity)

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        if self.is_cloud(entity):
            cycle = world.balance["sky"]["rain_period_s"]
            self.weather(world, entity, elapsed % cycle)
