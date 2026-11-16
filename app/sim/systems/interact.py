"""Player input: driving a vehicle, and petting, pushing or dragging things."""

import math
from typing import TYPE_CHECKING, Any

from app.sim.systems import decide

if TYPE_CHECKING:
    from app.sim.world import World

PUSH_DISTANCE = 40.0


def apply_inputs(world: "World", dt: float) -> None:
    """Controlled entities move by their driver's stick, not by a goal."""
    for entity_id, (ax, ay) in list(world.inputs.items()):
        entity = world.entities.get(entity_id)
        if entity is None:
            del world.inputs[entity_id]
            continue
        length = math.hypot(ax, ay)
        i = entity.i
        if length < 0.05:
            world.vx[i] = world.vy[i] = 0
            continue
        scale = min(1.0, length) / length
        speed = world.balance["vehicle"]["speed"] if entity.type == "vehicle" else world.base_speed[i]
        vx, vy = ax * scale * speed, ay * scale * speed
        nx = min(max(world.x[i] + vx * dt, 0), world.bounds[0])
        ny = min(max(world.y[i] + vy * dt, 0), world.bounds[1])
        if not decide.brain(entity).can_enter(world, entity, nx, ny):
            world.vx[i] = world.vy[i] = 0
            continue
        world.x[i], world.y[i], world.vx[i], world.vy[i] = nx, ny, vx, vy
        world.has_goal[i] = False
        if abs(vx) > 1:
            entity.facing = "right" if vx > 0 else "left"


def interact(world: "World", entity_id: str, action: str, to: Any = None) -> None:
    """pet: a small reaction. push: a shove away from the cursor. drag: move it there."""
    entity = world.entities.get(entity_id)
    if entity is None:
        return
    draggable = world.config.limits["interact"]["draggable"]
    x, y = world.pos(entity)
    if action == "pet":
        world.event("pet", entity)
    elif action == "push" and to and world.base_speed[entity.i] > 0 or action == "push" and to and entity.type in draggable:
        dx, dy = x - to[0], y - to[1]
        length = math.hypot(dx, dy) or 1.0
        world.x[entity.i] = min(max(x + dx / length * PUSH_DISTANCE, 0), world.bounds[0])
        world.y[entity.i] = min(max(y + dy / length * PUSH_DISTANCE, 0), world.bounds[1])
        world.touched[entity.i] = True
        world.think_now(entity)
    elif action == "drag" and to and entity.type in draggable:
        world.x[entity.i] = min(max(to[0], 0), world.bounds[0])
        world.y[entity.i] = min(max(to[1], 0), world.bounds[1])
        world.has_goal[entity.i] = False
        world.touched[entity.i] = True
        world.think_now(entity)
    entity.dirty = True
