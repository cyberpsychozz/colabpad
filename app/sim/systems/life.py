"""Births and deaths: who has run out of health, and what a sleeping chunk missed."""

from typing import TYPE_CHECKING

import numpy as np

from app.sim.systems import decide

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

# These just vanish; everything else with strokes leaves a fossil.
VANISH = {"food", "explosive", "fire"}


def run(world: "World", indices: np.ndarray, dt: float) -> None:
    dead = indices[world.hp[indices] <= 0]
    for i in dead.tolist():
        entity = world.slots[i]
        if entity is not None and entity.has("Health"):
            world.kill(entity, fossil=entity.type not in VANISH)


def catch_up(world: "World", entity: "Entity", elapsed: float) -> None:
    """Needs keep running while a chunk sleeps; positions do not change."""
    i = entity.i
    if world.has_needs[i]:
        needs = world.balance["needs"]
        for name, full, applies in (
            ("hunger", needs["hunger_full_s"], True),
            ("thirst", needs["thirst_full_s"], bool(world.has_thirst[i])),
        ):
            if not applies:
                continue
            values = getattr(world, name)
            to_limit = (100 - values[i]) * full / 100
            values[i] = min(100.0, values[i] + 100 / full * elapsed)
            # Past the limit, the body takes damage for the rest of the time.
            if elapsed > to_limit:
                world.hp[i] -= needs["starve_damage_per_s"] * (elapsed - to_limit)
        world.energy[i] = max(0.0, world.energy[i] - 100 / needs["energy_empty_s"] * elapsed)
    decide.brain(entity).catch_up(world, entity, elapsed)
    entity.dirty = True
    if entity.has("Health") and world.hp[i] <= 0:
        world.kill(entity, fossil=entity.type not in VANISH, cause="neglect")
