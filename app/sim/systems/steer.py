"""Steering: seek / arrive towards each entity's goal, vectorized over the world."""

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.sim.world import World

ARRIVE = 3.0


def run(world: "World", dt: float) -> None:
    moving = world.active & world.has_goal
    if not moving.any():
        world.vx[world.active] = 0
        world.vy[world.active] = 0
        return
    index = np.nonzero(moving)[0]
    dx, dy = world.gx[index] - world.x[index], world.gy[index] - world.y[index]
    distance = np.hypot(dx, dy)
    arrived = distance <= ARRIVE
    # Never overshoot: the last step lands exactly on the goal.
    step = np.minimum(world.speed[index] * world.slow[index] * dt, distance)
    safe = np.where(distance > 0, distance, 1)
    world.x[index] += dx / safe * step
    world.y[index] += dy / safe * step
    world.vx[index] = np.where(arrived, 0, dx / safe * step / dt)
    world.vy[index] = np.where(arrived, 0, dy / safe * step / dt)
    np.clip(world.x, 0, world.bounds[0], out=world.x)
    np.clip(world.y, 0, world.bounds[1], out=world.y)
    done = index[arrived | (step >= distance)]
    world.has_goal[done] = False
    world.arrived[done] = True
    still = world.active & ~world.has_goal
    world.vx[still] = 0
    world.vy[still] = 0
