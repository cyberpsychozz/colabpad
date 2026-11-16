"""Remote: binds to the nearest unbound vehicle within 400 px, or to the next one
spawned there. Who may drive, and for how long, is decided by main (the control lease).
"""

from typing import TYPE_CHECKING

from app.sim.systems.decide import Brain, register

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World


@register("remote")
class Remote(Brain):
    def on_add(self, world: "World", entity: "Entity") -> None:
        entity.data.setdefault("view", {}).setdefault("bound", entity.bound_to)

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        bound = world.entities.get(entity.bound_to or "")
        if bound is not None:
            return
        if entity.bound_to:
            # Its vehicle is gone.
            entity.bound_to = None
            world.view(entity, bound=None)
        taken = {other.bound_to for other in world.of_type("remote") if other.bound_to}
        radius = world.config.limits["control"]["bind_radius"]
        vehicle = world.nearest(entity, radius, lambda other: other.type == "vehicle" and other.id not in taken)
        if vehicle is not None:
            entity.bound_to = vehicle.id
            entity.dirty = True
            world.view(entity, bound=vehicle.id)
            world.event("bind", entity, vehicle=vehicle.id)
