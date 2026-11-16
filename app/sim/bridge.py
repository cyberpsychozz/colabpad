"""Main <-> simulation messaging.

Main is the only database writer: the simulation sends deltas, events, snapshots,
births, deaths and explosions, and main stores and broadcasts them. Two transports:
a real process (two multiprocessing queues, a reader thread handing batches to the
event loop, restart if it dies) and an inline one that tests drive tick by tick.
"""

import asyncio
import json
import logging
import multiprocessing
import threading
from collections.abc import Callable
from typing import Any

from app.config import GameConfig, Settings
from app.realtime.gateway import Realtime
from app.realtime.rooms import room
from app.repo import Repository
from app.repo.canvases import now_ms
from app.sim.process import SimCore, run

logger = logging.getLogger(__name__)


class InlineTransport:
    """The simulation in this process, stepped explicitly. For tests."""

    def __init__(self, config: GameConfig) -> None:
        self.core = SimCore(config, seed=1)

    def start(self, deliver: Callable[[list[dict[str, Any]]], None]) -> None:
        pass

    def send(self, message: dict[str, Any]) -> None:
        self.core.handle(message)

    def alive(self) -> bool:
        return True

    def stop(self) -> list[dict[str, Any]]:
        return self.core.flush()


class ProcessTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.context = multiprocessing.get_context("spawn")
        self.process: Any = None
        self.inbox: Any = None
        self.outbox: Any = None

    def start(self, deliver: Callable[[list[dict[str, Any]]], None]) -> None:
        self.inbox, self.outbox = self.context.Queue(), self.context.Queue()
        self.process = self.context.Process(
            target=run, args=(self.inbox, self.outbox, str(self.settings.config_dir)), daemon=True
        )
        self.process.start()
        outbox = self.outbox

        def read() -> None:
            while True:
                try:
                    batch = outbox.get()
                except (EOFError, OSError, ValueError):
                    return
                if batch is None:
                    return
                deliver(batch)

        threading.Thread(target=read, daemon=True, name="sim-reader").start()

    def send(self, message: dict[str, Any]) -> None:
        if self.inbox is not None:
            self.inbox.put(message)

    def alive(self) -> bool:
        return self.process is not None and self.process.is_alive()

    def stop(self) -> list[dict[str, Any]]:
        if self.process is None:
            return []
        if self.process.is_alive():
            self.inbox.put({"op": "stop"})
            self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.terminate()
        self.outbox.put(None)  # release the reader thread
        return []


class SimBridge:
    def __init__(
        self,
        settings: Settings,
        config: GameConfig,
        get_repo: Callable[[], Repository],
        realtime: Realtime,
    ) -> None:
        self.settings, self.config, self.get_repo, self.realtime = settings, config, get_repo, realtime
        self.mode = settings.sim_mode
        self.transport: Any = None
        # Last known state of every living entity, in wire shape, for snapshots.
        self.live: dict[str, dict[str, Any]] = {}
        self.canvas_of: dict[str, str] = {}
        self.hz: int = config.balance["tick_hz"]
        self.restarts = 0
        self.watchdog: asyncio.Task[None] | None = None
        self.sweeper: asyncio.Task[None] | None = None
        self.events: list[tuple[Any, ...]] = []
        self.events_flushed = 0.0
        self.pending: set[asyncio.Future[Any]] = set()
        self.closed = False

    # ---- lifecycle

    async def start(self) -> None:
        if self.mode == "off":
            return
        self.realtime.rooms.on_change = self.chunk_changed
        self.realtime.sim = self
        loop = asyncio.get_running_loop()
        self.transport = (
            InlineTransport(self.config) if self.mode == "inline" else ProcessTransport(self.settings)
        )

        def deliver(batch: list[dict[str, Any]]) -> None:
            future = asyncio.run_coroutine_threadsafe(self.handle(batch), loop)
            self.pending.add(future)  # type: ignore[arg-type]
            future.add_done_callback(self.pending.discard)  # type: ignore[arg-type]

        self.deliver = deliver
        self.transport.start(deliver)
        self.load()
        if self.mode == "process":
            self.watchdog = asyncio.create_task(self.watch())
            self.sweeper = asyncio.create_task(self.sweep_leases())

    def load(self) -> None:
        """Tell a fresh simulation about every canvas, living entity and subscription."""
        repo = self.get_repo()
        self.live.clear()
        for canvas in repo.db.execute("SELECT id, bounds_w, bounds_h, frozen FROM canvas").fetchall():
            self.send({"op": "world", "canvas": canvas["id"], "bounds": [canvas["bounds_w"], canvas["bounds_h"]]})
            if canvas["frozen"]:
                self.send({"op": "freeze", "canvas": canvas["id"], "frozen": True})
        for row in repo.sim_rows():
            self.add(row)
        for (canvas_id, chunk), count in self.realtime.rooms.subscribers.items():
            if count:
                self.send({"op": "chunk", "canvas": canvas_id, "cx": chunk[0], "cy": chunk[1], "on": True})

    async def watch(self) -> None:
        """If the simulation process dies, start a new one from the database."""
        while True:
            await asyncio.sleep(0.5)
            if not self.transport.alive():
                logger.error("Simulation process died; restarting")
                self.restarts += 1
                self.transport.stop()
                self.transport = ProcessTransport(self.settings)
                self.transport.start(self.deliver)
                self.load()

    async def sweep_leases(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self.realtime.control.sweep()

    async def stop(self) -> None:
        if self.transport is None:
            return
        if self.watchdog:
            self.watchdog.cancel()
        if self.sweeper:
            self.sweeper.cancel()
        batch = self.transport.stop()
        if batch:
            await self.handle(batch)
        self.flush_events()
        # Batches still in flight from the reader thread arrive after the database is closed.
        self.closed = True

    # ---- main -> sim

    def send(self, message: dict[str, Any]) -> None:
        if self.transport is not None:
            self.transport.send(message)

    def ensure_world(self, canvas: dict[str, Any]) -> None:
        self.send({"op": "world", "canvas": canvas["id"], "bounds": [canvas["bounds_w"], canvas["bounds_h"]]})

    def add(self, row: dict[str, Any]) -> None:
        """Hand an entity to the simulation and start tracking its live state."""
        wire = self.get_repo().get_entity(row["id"])
        if wire is None:
            return
        health = (self.config.archetypes[row["type"]]["components"].get("Health") or {}).get("hp")
        wire["hp"] = round(row["hp"] / health * 100) if health and row.get("hp") is not None else 100
        wire["view"] = (row["params"].get("data") or {}).get("view") or {}
        wire["anim"] = "idle"
        self.live[row["id"]] = wire
        self.canvas_of[row["id"]] = row["canvas"]
        self.send({"op": "spawn", "canvas": row["canvas"], "entity": row})

    def spawn(self, entity_id: str) -> None:
        """An entity main has just created (ALIVE)."""
        for row in self.get_repo().sim_rows(entity_id=entity_id):
            self.add(row)

    def chunk_changed(self, canvas_id: str, chunk: tuple[int, int], on: bool) -> None:
        self.send({"op": "chunk", "canvas": canvas_id, "cx": chunk[0], "cy": chunk[1], "on": on})

    def entities_in(self, canvas_id: str, x0: float, y0: float, x1: float, y1: float) -> list[dict[str, Any]]:
        return [
            entity
            for entity_id, entity in self.live.items()
            if self.canvas_of.get(entity_id) == canvas_id
            and x0 <= entity["x"] < x1
            and y0 <= entity["y"] < y1
        ]

    async def step(self, dt: float = 0.1, ticks: int = 1) -> None:
        """Inline mode only: advance the simulation and process what it sent."""
        for _ in range(ticks):
            await self.handle(self.transport.core.step(dt))

    # ---- sim -> main

    async def handle(self, batch: list[dict[str, Any]]) -> None:
        if self.closed:
            return
        for message in batch:
            try:
                await getattr(self, f"on_{message['op']}")(message)
            except Exception:
                logger.exception("Failed to handle simulation message %s", message.get("op"))
        if self.events and now_ms() - self.events_flushed >= 1000:
            self.flush_events()

    async def on_delta(self, message: dict[str, Any]) -> None:
        for row in message["upd"]:
            entity = self.live.get(row[0])
            if entity is None:
                continue
            entity["x"], entity["y"], entity["state"], entity["anim"], entity["hp"] = row[1:6]
            if len(row) > 6:
                entity["view"] = row[6]
        await self.realtime.emit(
            "world:delta",
            {
                "cx": message["cx"], "cy": message["cy"], "tick": message["tick"],
                "upd": message["upd"], "gone": message["gone"],
                # Entities that walked into this chunk, in full, for those who did not have them.
                "enter": [self.live[i] for i in message["enter"] if i in self.live],
            },  # fmt: skip
            [room(message["canvas"], (message["cx"], message["cy"]))],
        )

    async def on_event(self, message: dict[str, Any]) -> None:
        payload = {k: v for k, v in message.items() if k not in ("op", "canvas")}
        self.events.append(
            (message["canvas"], message["tick"], now_ms(), message["type"], message.get("id"), payload)
        )
        await self.realtime.emit(
            "world:event", payload, [room(message["canvas"], (message["cx"], message["cy"]))]
        )

    def flush_events(self) -> None:
        events, self.events = self.events, []
        self.events_flushed = now_ms()
        if events:
            self.get_repo().db.executemany(
                "INSERT INTO world_event(canvas_id, tick, at, type, actors, payload) VALUES(?,?,?,?,?,?)",
                [(c, tick, at, kind, json.dumps([actor] if actor else []), json.dumps(payload, ensure_ascii=False))
                 for c, tick, at, kind, actor, payload in events],  # fmt: skip
            )

    async def on_snapshot(self, message: dict[str, Any]) -> None:
        self.get_repo().save_snapshot(message["entities"])

    async def on_born(self, message: dict[str, Any]) -> None:
        """The world made an entity (a calf, a fruit): store it and show it."""
        repo, row = self.get_repo(), message["entity"]
        repo.insert_entity(
            {
                "id": row["id"], "canvas_id": message["canvas"], "sprite_id": row.get("sprite_id"),
                "archetype": row["type"], "label": row.get("label") or {},
                "x": row["x"], "y": row["y"], "vx": 0.0, "vy": 0.0,
                "facing": row.get("facing") or "right", "state": row.get("state") or "idle",
                "hp": row.get("hp"), "hunger": row.get("hunger") or 0.0,
                "thirst": row.get("thirst") or 0.0, "energy": row.get("energy", 100.0),
                "params": {**(row.get("params") or {}), "data": row.get("data") or {}, "age": row.get("age", 0)},
                "origin_x": row["x"], "origin_y": row["y"], "parent_id": row.get("parent_id"),
            }  # fmt: skip
        )
        wire = repo.get_entity(row["id"])
        assert wire is not None
        wire.update(hp=100, anim="idle", view=(row.get("data") or {}).get("view") or {})
        self.live[row["id"]] = wire
        self.canvas_of[row["id"]] = message["canvas"]
        size = self.config.limits["world"]["chunk_size"]
        await self.realtime.emit(
            "world:event",
            {"type": "spawn", "entity": wire, "parent": row.get("parent_id")},
            [room(message["canvas"], (int(row["x"] // size), int(row["y"] // size)))],
        )

    async def on_death(self, message: dict[str, Any]) -> None:
        """Record the death; with `fossil`, its strokes become a grey fossil where it died."""
        repo = self.get_repo()
        self.live.pop(message["id"], None)
        self.canvas_of.pop(message["id"], None)
        dead = repo.mark_dead(message["id"], message["x"], message["y"])
        if not dead or not message["fossil"] or not dead["seqs"]:
            return
        repo.insert_fates(dead["canvas"], dead["seqs"], "fossil", message["id"])
        strokes = repo.strokes_by_seq(dead["canvas"], dead["seqs"])
        size = self.config.limits["world"]["chunk_size"]
        await self.realtime.emit(
            "stroke:fate",
            {"seqs": dead["seqs"], "fate": "fossil", "entityId": message["id"]},
            sorted({room(dead["canvas"], (s["cx"], s["cy"])) for s in strokes}),
        )
        fossil = {"entityId": message["id"], "x": message["x"], "y": message["y"],
                  "dx": dead["dx"], "dy": dead["dy"], "strokes": strokes}  # fmt: skip
        await self.realtime.emit(
            "world:event",
            {"type": "fossil", "fossil": fossil},
            [room(dead["canvas"], (int(message["x"] // size), int(message["y"] // size)))],
        )

    async def on_explosion(self, message: dict[str, Any]) -> None:
        """A blast destroys the static strokes it reaches (as `destroyed` fates)."""
        repo = self.get_repo()
        canvas, x, y, radius = message["canvas"], message["x"], message["y"], message["r"]
        size = self.config.limits["world"]["chunk_size"]
        hit: list[dict[str, Any]] = []
        for cy in range(max(0, int((y - radius) // size)), int((y + radius) // size) + 1):
            for cx in range(max(0, int((x - radius) // size)), int((x + radius) // size) + 1):
                strokes = repo.chunk_strokes(canvas, cx, cy)
                static = repo.static_seqs(canvas, [s["seq"] for s in strokes])
                hit += [s for s in strokes if s["seq"] in static and reaches(s["points"], x, y, radius)]
        if not hit:
            return
        seqs = [s["seq"] for s in hit]
        repo.db.execute("BEGIN IMMEDIATE")
        try:
            repo.insert_fates(canvas, seqs, "destroyed")
            repo.db.execute("COMMIT")
        except BaseException:
            repo.db.execute("ROLLBACK")
            raise
        await self.realtime.emit(
            "stroke:fate",
            {"seqs": seqs, "fate": "destroyed", "entityId": None},
            sorted({room(canvas, (s["cx"], s["cy"])) for s in hit}),
        )

    async def on_rate(self, message: dict[str, Any]) -> None:
        self.hz = message["hz"]
        logger.warning("Simulation ticks exceeded their budget; running at %s Hz", self.hz)


def reaches(points: list[list[float]], x: float, y: float, radius: float) -> bool:
    """Does a circle touch a polyline? (Distance from the centre to the nearest segment.)"""
    r2 = radius * radius
    for (ax, ay, *_), (bx, by, *_) in zip(points, points[1:] or points):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else min(1.0, max(0.0, ((x - ax) * dx + (y - ay) * dy) / length2))
        if (ax + t * dx - x) ** 2 + (ay + t * dy - y) ** 2 <= r2:
            return True
    return False

