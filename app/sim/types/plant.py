"""Plant: sways in the wind. A fruit tree drops fruit; a pasture is grazed and regrows.

Plants grow faster by water or in rain (x1.5 to x2) and near a sun (x1.5). Flammable.
"""

import math
from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register
from app.sim.types import base

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

FRUIT_RADIUS = 110.0


@register("plant")
class Plant(Brain):
    def variant(self, entity: "Entity") -> str | None:
        return entity.params.get("variant")

    def on_add(self, world: "World", entity: "Entity") -> None:
        rules = world.balance["plant"]
        if self.variant(entity) == "pasture":
            entity.data.setdefault("stock", rules["pasture_max"])
        elif self.variant(entity) == "fruit_tree":
            entity.data.setdefault("fruit_in", world.rng.uniform(*rules["fruit_period_s"]))

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        self.advance(world, entity, dt, drop=True)

    def advance(self, world: "World", entity: "Entity", dt: float, drop: bool) -> None:
        rules = world.balance["plant"]
        x, y = world.pos(entity)
        boost = base.growth_boost(world, x, y)
        if self.variant(entity) == "pasture":
            stock = min(rules["pasture_max"], entity.data.get("stock", 0) + rules["pasture_regrow_per_s"] * boost * dt)
            entity.data["stock"] = stock
            world.view(entity, stock=round(stock / 10) * 10)
        elif self.variant(entity) == "fruit_tree":
            entity.data["fruit_in"] = entity.data.get("fruit_in", 60) - dt * boost
            if entity.data["fruit_in"] > 0:
                return
            entity.data["fruit_in"] = world.rng.uniform(*rules["fruit_period_s"])
            if not drop:
                return
            around = sum(
                1 for other in world.of_type("food")
                if other.parent == entity.id and world.dist(entity, other) <= FRUIT_RADIUS
            )  # fmt: skip
            if around >= rules["fruit_cap"] or not base.room_for(world, x, y):
                return
            angle = world.rng.uniform(0.15 * math.pi, 0.85 * math.pi)  # falls below the crown
            reach = entity.radius * 0.6 + 20
            fruit = world.spawn_child(
                {**base.child_row("food", x + math.cos(angle) * reach, y + math.sin(angle) * reach, builtin="apple"),
                 "parent_id": entity.id}  # fmt: skip
            )
            world.event("fruit", fruit, tree=entity.id)

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        # Regrowth happens while asleep; fruit does not pile up unseen.
        self.advance(world, entity, elapsed, drop=False)
