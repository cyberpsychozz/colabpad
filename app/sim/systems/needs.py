"""Needs: hunger and thirst rise, energy falls; at the limit the body takes damage."""

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.sim.world import World


def run(world: "World", dt: float) -> None:
    needs = world.config.balance["needs"]
    hungry = world.active & world.has_needs
    if not hungry.any():
        return
    world.hunger[hungry] = np.minimum(100, world.hunger[hungry] + 100 / needs["hunger_full_s"] * dt)
    thirsty = hungry & world.has_thirst
    world.thirst[thirsty] = np.minimum(100, world.thirst[thirsty] + 100 / needs["thirst_full_s"] * dt)
    resting = hungry & world.resting
    awake = hungry & ~world.resting
    world.energy[awake] = np.maximum(0, world.energy[awake] - 100 / needs["energy_empty_s"] * dt)
    # Rest restores energy four times faster than being awake drains it.
    world.energy[resting] = np.minimum(100, world.energy[resting] + 400 / needs["energy_empty_s"] * dt)
    starving = hungry & ((world.hunger >= 100) | (world.thirst >= 100))
    world.hp[starving] -= needs["starve_damage_per_s"] * dt
