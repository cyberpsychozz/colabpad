"""Food: eaten in three bites (the sprite shrinks); near a fire it gets cooked."""

from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World


@register("food")
class Food(Brain):
    def on_add(self, world: "World", entity: "Entity") -> None:
        entity.data.setdefault("bites", world.balance["nutrition"]["bites"])

    # Eating is done by the eater (living.bite); cooking by the fire (fire.think).
