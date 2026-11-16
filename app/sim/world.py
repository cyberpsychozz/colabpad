"""One canvas's world: storage, the tick, chunk activity, deltas and snapshots.

The world is plain Python and numpy with no I/O: it takes messages in, and collects
messages for the main process in `self.out`. That keeps it deterministic and testable;
app/sim/process.py wraps it in a process.
"""

import math
import random
from typing import Any

import numpy as np

from app.config import GameConfig
from app.sim.ecs import Entity, resolve_components
from app.sim.systems import decide, interact, life, needs, steer
from app.sim.systems.perception import SpatialHash

ACT_STATES = {"eat", "drink", "attack", "mate", "act", "fish", "heal"}
ARRAYS = ("x", "y", "vx", "vy", "gx", "gy", "speed", "base_speed", "slow", "hp", "max_hp",
          "hunger", "thirst", "energy", "sent_x", "sent_y", "sent_hp")  # fmt: skip
MASKS = ("alive", "active", "has_goal", "arrived", "has_needs", "has_thirst", "resting", "touched")


class World:
    def __init__(
        self,
        canvas_id: str,
        bounds: tuple[int, int],
        config: GameConfig,
        seed: int | None = None,
        day_phase: Any = None,
    ) -> None:
        self.canvas_id, self.bounds, self.config = canvas_id, bounds, config
        self.balance = config.balance
        self.chunk_size: int = config.limits["world"]["chunk_size"]
        self.columns = math.ceil(bounds[0] / self.chunk_size)
        self.rows = math.ceil(bounds[1] / self.chunk_size)
        self.rng = random.Random(seed)
        self.t = 0.0
        self.tick_n = 0
        self.frozen = False
        # 0..1 through the 20-minute day; the second half is night. Injected for tests.
        self._day_phase = day_phase
        self.capacity = 0
        for name in ARRAYS:
            setattr(self, name, np.zeros(0))
        for name in MASKS:
            setattr(self, name, np.zeros(0, dtype=bool))
        self._grow(256)
        self.slots: list[Entity | None] = [None] * self.capacity
        self.free: list[int] = list(range(self.capacity - 1, -1, -1))
        self.entities: dict[str, Entity] = {}
        self.by_type: dict[str, set[str]] = {}
        self.hash = SpatialHash()
        self.subscribed: set[tuple[int, int]] = set()
        self.hot: set[str] = set()  # lit fuses and fires keep their chunk awake
        self.active_grid = np.zeros((self.rows, self.columns), dtype=bool)
        self.slept_at: dict[tuple[int, int], float] = {}
        self.inputs: dict[str, tuple[float, float]] = {}
        self.out: list[dict[str, Any]] = []
        self.gone: dict[tuple[int, int], list[str]] = {}
        self.last_snapshot = 0.0
        self.stats: dict[str, int] = {}

    # ---- storage

    def _grow(self, capacity: int) -> None:
        for name in ARRAYS:
            old = getattr(self, name)
            new = np.zeros(capacity)
            new[: len(old)] = old
            setattr(self, name, new)
        for name in MASKS:
            old = getattr(self, name)
            new = np.zeros(capacity, dtype=bool)
            new[: len(old)] = old
            setattr(self, name, new)
        if self.capacity:
            self.slots.extend([None] * (capacity - self.capacity))
            self.free = list(range(capacity - 1, self.capacity - 1, -1)) + self.free
        self.capacity = capacity

    def add(self, row: dict[str, Any]) -> Entity:
        """row: id, type, label, x, y, facing, state, hp, hunger, thirst, energy, params, ..."""
        if row["id"] in self.entities:
            return self.entities[row["id"]]
        if not self.free:
            self._grow(self.capacity * 2)
        i = self.free.pop()
        params = row.get("params") or {}
        comps = resolve_components(self.config.archetypes[row["type"]], params)
        entity = Entity(i, row, comps, self.t)
        self.slots[i] = entity
        self.entities[entity.id] = entity
        self.by_type.setdefault(entity.type, set()).add(entity.id)
        max_hp = float(entity.get("Health", "hp", 100))
        speeds = self.balance["speed"]
        mode = entity.get("Locomotion", "mode", "none")
        base = 0.0 if mode in (None, "none") else float(speeds[params.get("sizeClass") or "M"])
        if mode == "fly":
            base *= speeds["fly_mult"]
        values = {
            "x": row["x"], "y": row["y"], "vx": 0, "vy": 0, "gx": row["x"], "gy": row["y"],
            "speed": base, "base_speed": base, "slow": 1, "max_hp": max_hp,
            "hp": max_hp if row.get("hp") is None else row["hp"],
            "hunger": row.get("hunger") or 0, "thirst": row.get("thirst") or 0,
            "energy": 100 if row.get("energy") is None else row["energy"],
            "sent_x": -1e9, "sent_y": -1e9, "sent_hp": -1,
        }  # fmt: skip
        for name, value in values.items():
            getattr(self, name)[i] = value
        for name in MASKS:
            getattr(self, name)[i] = False
        self.alive[i] = True
        self.has_needs[i] = entity.has("Needs")
        self.has_thirst[i] = entity.has("Needs") and entity.get("Needs", "thirst", True)
        entity.chunk = self.chunk_of(row["x"], row["y"])
        # Spread first decisions over the stagger window.
        entity.next_think = self.tick_n + self.rng.randrange(self.think_every)
        decide.brain(entity).on_add(self, entity)
        self._refresh_active()
        return entity

    def remove(self, entity: Entity) -> None:
        if self.entities.pop(entity.id, None) is None:
            return
        self.by_type[entity.type].discard(entity.id)
        self.hot.discard(entity.id)
        self.inputs.pop(entity.id, None)
        self.gone.setdefault(entity.chunk, []).append(entity.id)
        for name in MASKS:
            getattr(self, name)[entity.i] = False
        self.slots[entity.i] = None
        self.free.append(entity.i)

    # ---- helpers for systems and types

    @property
    def think_every(self) -> int:
        return max(1, round(self.balance["tick_hz"] / self.balance["decide_hz"]))

    def chunk_of(self, x: float, y: float) -> tuple[int, int]:
        size = self.chunk_size
        return min(self.columns - 1, max(0, int(x // size))), min(self.rows - 1, max(0, int(y // size)))

    def pos(self, entity: Entity) -> tuple[float, float]:
        return float(self.x[entity.i]), float(self.y[entity.i])

    def dist(self, a: Entity, b: Entity) -> float:
        return math.hypot(self.x[a.i] - self.x[b.i], self.y[a.i] - self.y[b.i])

    def near(self, x: float, y: float, radius: float, skip: Entity | None = None) -> list[Entity]:
        """Living, active entities within `radius` of a point."""
        slots, xs, ys, r2 = self.slots, self.x, self.y, radius * radius
        found = []
        for i in self.hash.near(x, y, radius):
            other = slots[i]
            if other is None or other is skip:
                continue
            dx, dy = xs[i] - x, ys[i] - y
            if dx * dx + dy * dy <= r2:
                found.append(other)
        return found

    def nearest(self, entity: Entity, radius: float, wanted: Any) -> Entity | None:
        x, y = self.pos(entity)
        best, best_d = None, radius * radius + 1
        for other in self.near(x, y, radius, entity):
            if not wanted(other):
                continue
            d = (self.x[other.i] - x) ** 2 + (self.y[other.i] - y) ** 2
            if d < best_d:
                best, best_d = other, d
        return best

    def go(self, entity: Entity, x: float, y: float, speed: float = 1.0) -> None:
        i = entity.i
        self.gx[i] = min(max(x, 0), self.bounds[0])
        self.gy[i] = min(max(y, 0), self.bounds[1])
        self.speed[i] = self.base_speed[i] * speed
        self.has_goal[i] = self.base_speed[i] > 0
        if self.gx[i] != self.x[i]:
            entity.facing = "right" if self.gx[i] > self.x[i] else "left"

    def stop(self, entity: Entity) -> None:
        self.has_goal[entity.i] = False
        self.touched[entity.i] = True

    def set_state(self, entity: Entity, state: str) -> None:
        if entity.state != state:
            entity.state = state
            self.resting[entity.i] = state == "rest"
            self.touched[entity.i] = True
            entity.dirty = True

    def view(self, entity: Entity, **changes: Any) -> None:
        """Client-visible extras (growth, bites left, fuse time...), sent when they change."""
        view = entity.data.setdefault("view", {})
        if any(view.get(key) != value for key, value in changes.items()):
            view.update(changes)
            self.touched[entity.i] = True
            entity.dirty = True

    def think_now(self, entity: Entity) -> None:
        entity.next_think = self.tick_n

    def event(self, kind: str, entity: Entity | None = None, **payload: Any) -> None:
        """Something worth showing (VFX, the feed). Routed to the chunk it happened in."""
        if entity is not None:
            payload.setdefault("x", round(float(self.x[entity.i]), 1))
            payload.setdefault("y", round(float(self.y[entity.i]), 1))
            payload["id"] = entity.id
        cx, cy = self.chunk_of(payload.get("x", 0), payload.get("y", 0))
        self.out.append({"op": "event", "canvas": self.canvas_id, "cx": cx, "cy": cy,
                         "tick": self.tick_n, "type": kind, **payload})  # fmt: skip

    def damage(self, entity: Entity, amount: float, cause: str) -> None:
        if entity.has("Health") and self.alive[entity.i]:
            self.hp[entity.i] -= amount
            entity.data["cause"] = cause
            entity.dirty = True

    def kill(self, entity: Entity, fossil: bool, cause: str = "") -> None:
        """Remove from the world. fossil: its strokes turn into a grey fossil where it died."""
        if entity.id not in self.entities:
            return
        x, y = self.pos(entity)
        decide.brain(entity).on_remove(self, entity)
        self.out.append({"op": "death", "canvas": self.canvas_id, "id": entity.id, "x": round(x, 1),
                         "y": round(y, 1), "fossil": fossil, "cause": cause or entity.data.get("cause", "")})  # fmt: skip
        self.event("death", entity, fossil=fossil, cause=cause or entity.data.get("cause", ""))
        self.remove(entity)

    def spawn_child(self, row: dict[str, Any]) -> Entity:
        """A new entity made by the world itself (a calf, a fruit). Main stores it."""
        entity = self.add(row)
        self.out.append({"op": "born", "canvas": self.canvas_id, "entity": {**row, **self.snapshot_row(entity)}})
        return entity

    def of_type(self, *types: str) -> list[Entity]:
        return [self.entities[i] for name in types for i in self.by_type.get(name, ())]

    def day_phase(self) -> float:
        if self._day_phase is not None:
            return self._day_phase() if callable(self._day_phase) else self._day_phase
        return (self.t % self.balance["day_night_cycle_s"]) / self.balance["day_night_cycle_s"]

    def is_night(self) -> bool:
        return self.day_phase() >= 0.5

    # ---- chunk activity

    def set_subscribed(self, chunk: tuple[int, int], on: bool) -> None:
        (self.subscribed.add if on else self.subscribed.discard)(chunk)
        self._refresh_active()

    def _refresh_active(self) -> None:
        """Active = subscribed or next to a subscribed chunk, or holding a lit fuse or fire."""
        grid = np.zeros_like(self.active_grid)
        for cx, cy in self.subscribed:
            grid[max(0, cy - 1) : cy + 2, max(0, cx - 1) : cx + 2] = True
        for entity_id in self.hot:
            cx, cy = self.chunk_of(*self.pos(self.entities[entity_id]))
            grid[cy, cx] = True
        woke = np.argwhere(grid & ~self.active_grid)
        slept = np.argwhere(~grid & self.active_grid)
        self.active_grid = grid
        for cy, cx in slept.tolist():
            self.slept_at[(cx, cy)] = self.t
            self._sleeping((cx, cy))
        for cy, cx in woke.tolist():
            since = self.slept_at.pop((cx, cy), None)
            if since is not None and self.config.flags.sleep_catchup:
                self._catch_up((cx, cy), min(self.t - since, self.balance["sleep_catchup_max_s"]))

    def _in_chunk(self, chunk: tuple[int, int]) -> list[Entity]:
        return [e for e in self.entities.values() if self.chunk_of(*self.pos(e)) == chunk]

    def _sleeping(self, chunk: tuple[int, int]) -> None:
        for entity in self._in_chunk(chunk):
            entity.dirty = True  # goes out with the next snapshot

    def _catch_up(self, chunk: tuple[int, int], elapsed: float) -> None:
        """A sleeping chunk wakes: apply what would have happened, without moving anything."""
        if elapsed <= 0:
            return
        for entity in self._in_chunk(chunk):
            life.catch_up(self, entity, elapsed)

    # ---- the tick

    def tick(self, dt: float) -> None:
        if self.frozen:
            return
        self.tick_n += 1
        self.t += dt
        size = self.chunk_size
        cx = np.clip((self.x // size).astype(int), 0, self.columns - 1)
        cy = np.clip((self.y // size).astype(int), 0, self.rows - 1)
        self.active = self.alive & self.active_grid[cy, cx]
        indices = np.nonzero(self.active)[0]
        interact.apply_inputs(self, dt)       # 1. inputs
        needs.run(self, dt)                   # 2. needs
        self.hash.rebuild(indices, self.x, self.y)  # 3. perception
        decide.run(self, indices)             # 4. decision (staggered) + 6. interactions
        steer.run(self, dt)                   # 5. steering
        life.run(self, indices, dt)           # 7. births, deaths
        self._deltas()                        # 8. deltas and events
        if self.t - self.last_snapshot >= 5:  # 9. dirty entities go to main
            self.snapshot()

    # ---- output

    def anim_of(self, entity: Entity) -> str:
        if self.has_goal[entity.i] or entity.id in self.inputs:
            return "move"
        return "act" if entity.state in ACT_STATES else "idle"

    def _deltas(self) -> None:
        hp_pct = np.rint(self.hp / np.maximum(self.max_hp, 1) * 100)
        changed = self.active & (
            (np.abs(self.x - self.sent_x) > 0.05)
            | (np.abs(self.y - self.sent_y) > 0.05)
            | (hp_pct != self.sent_hp)
            | self.touched
        )
        per_chunk: dict[tuple[int, int], dict[str, list[Any]]] = {}
        for i in np.nonzero(changed)[0].tolist():
            entity = self.slots[i]
            if entity is None:
                continue
            x, y = round(float(self.x[i]), 1), round(float(self.y[i]), 1)
            chunk = self.chunk_of(x, y)
            row: list[Any] = [entity.id, x, y, entity.state, self.anim_of(entity), int(hp_pct[i])]
            view = entity.data.get("view")
            state = (entity.state, row[4], view and dict(view))
            entry = per_chunk.setdefault(chunk, {"upd": [], "enter": []})
            if chunk != entity.chunk:
                self.gone.setdefault(entity.chunk, []).append(entity.id)
                entity.chunk = chunk
                entry["enter"].append(entity.id)
                entity.dirty = True
            if view and (entity.sent is None or entity.sent[2] != state[2]):
                row.append(view)
            entity.sent = state
            entry["upd"].append(row)
            self.sent_x[i], self.sent_y[i], self.sent_hp[i] = self.x[i], self.y[i], hp_pct[i]
            if abs(self.vx[i]) + abs(self.vy[i]) > 0:
                entity.dirty = True
        self.touched[:] = False
        for chunk in set(per_chunk) | set(self.gone):
            entry = per_chunk.get(chunk, {"upd": [], "enter": []})
            self.out.append({"op": "delta", "canvas": self.canvas_id, "cx": chunk[0], "cy": chunk[1],
                             "tick": self.tick_n, "upd": entry["upd"], "enter": entry["enter"],
                             "gone": self.gone.get(chunk, [])})  # fmt: skip
        self.gone = {}

    def snapshot_row(self, entity: Entity) -> dict[str, Any]:
        i = entity.i
        return {
            "id": entity.id, "x": round(float(self.x[i]), 1), "y": round(float(self.y[i]), 1),
            "vx": round(float(self.vx[i]), 1), "vy": round(float(self.vy[i]), 1),
            "facing": entity.facing, "state": entity.state,
            "hp": round(float(self.hp[i]), 1), "hunger": round(float(self.hunger[i]), 1),
            "thirst": round(float(self.thirst[i]), 1), "energy": round(float(self.energy[i]), 1),
            "data": entity.data, "age": round(self.t - entity.born, 1), "bound_to": entity.bound_to,
        }  # fmt: skip

    def snapshot(self, everything: bool = False) -> None:
        self.last_snapshot = self.t
        rows = []
        for entity in self.entities.values():
            if entity.dirty or everything:
                rows.append(self.snapshot_row(entity))
                entity.dirty = False
        if rows:
            self.out.append({"op": "snapshot", "canvas": self.canvas_id, "entities": rows})

    def drain(self) -> list[dict[str, Any]]:
        out, self.out = self.out, []
        return out
