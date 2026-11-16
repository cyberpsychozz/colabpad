"""Decision: each entity thinks about twice a second, staggered across ticks.

A fifth of the entities think on any given tick; an entity that has just arrived or
has been told to (`world.think_now`) thinks at once. What "thinking" means is up to
the entity's type: app/sim/types has one module per type.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

    from app.sim.ecs import Entity
    from app.sim.world import World


class Brain:
    """What a type does. The default does nothing: a still object."""

    def on_add(self, world: "World", entity: "Entity") -> None:
        pass

    def on_remove(self, world: "World", entity: "Entity") -> None:
        pass

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        """dt is the time since this entity last thought."""

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        """The entity's chunk slept for `elapsed` seconds; nothing moves."""

    def command(self, world: "World", entity: "Entity", message: dict[str, Any]) -> None:
        """A type-specific request from the main process."""

    def can_enter(self, world: "World", entity: "Entity", x: float, y: float) -> bool:
        """May a driven entity move to (x, y)? Cars stay out of water, boats in it."""
        return True


REGISTRY: dict[str, Brain] = {}
_DEFAULT = Brain()


def register(name: str) -> Any:
    def wrap(cls: type[Brain]) -> type[Brain]:
        REGISTRY[name] = cls()
        return cls

    return wrap


def brain(entity: "Entity") -> Brain:
    return REGISTRY.get(entity.type, _DEFAULT)


def run(world: "World", indices: "np.ndarray") -> None:
    tick, every, slots = world.tick_n, world.think_every, world.slots
    arrived = world.arrived
    for i in indices.tolist():
        entity = slots[i]
        if entity is None:
            continue
        if entity.next_think > tick and not arrived[i]:
            continue
        arrived[i] = False
        entity.next_think = tick + every
        dt = world.t - entity.last_think
        entity.last_think = world.t
        REGISTRY.get(entity.type, _DEFAULT).think(world, entity, dt)
