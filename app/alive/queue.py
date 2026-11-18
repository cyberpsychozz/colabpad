"""The ALIVE pipeline: request -> goo -> rasterize -> classify -> (choose) -> rig -> spawn.

Jobs run on an in-process asyncio queue; their state is also persisted in alive_job.
Heavy steps run off the event loop. Quota is spent only on success or UNSAFE_CONTENT.
"""

import asyncio
import io
import logging
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from PIL import Image

from app.alive.bake import save_sprite, single_frame_manifest
from app.alive.postprocess import Classified, postprocess, size_class
from app.alive.rasterize import Cutout, rasterize
from app.alive.rig import area_polygon
from app.alive.select import SelectionLocks, bbox, select_strokes
from app.alive.vlm import TYPES, Vlm, VlmError
from app.config import GameConfig, Settings
from app.errors import AppError
from app.realtime.gateway import Realtime
from app.realtime.rooms import room, session_room
from app.repo import Repository
from app.repo.canvases import now_ms

logger = logging.getLogger(__name__)

# (sheet, manifest, sheet pixels per cutout pixel)
Rig = Callable[[Cutout, Classified], tuple[Image.Image, dict[str, Any], float]]


def plain_rig(
    cutout: Cutout, classified: Classified
) -> tuple[Image.Image, dict[str, Any], float]:
    """No animation: the drawing itself is the only frame."""
    return cutout.image, single_frame_manifest(cutout.image, classified.facing), 1.0


@dataclass
class Job:
    id: str
    canvas_id: str
    session_id: str
    subject: str
    polygon: list[tuple[float, float]]
    strokes: list[dict[str, Any]]
    rooms: list[str]
    choice: asyncio.Future[str] | None = None
    timings: dict[str, int] = field(default_factory=dict)

    @property
    def seqs(self) -> list[int]:
        return [stroke["seq"] for stroke in self.strokes]


class AliveService:
    def __init__(
        self,
        settings: Settings,
        config: GameConfig,
        get_repo: Callable[[], Repository],
        realtime: Realtime,
        vlm: Vlm,
        rig: Rig = plain_rig,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        on_spawn: Callable[[dict[str, Any]], None] = lambda entity: None,
    ) -> None:
        self.settings, self.config, self.get_repo, self.realtime = settings, config, get_repo, realtime
        self.vlm, self.rig, self.clock, self.monotonic, self.on_spawn = vlm, rig, clock, monotonic, on_spawn
        self.limits = config.limits["alive"]
        self.chunk_size: int = config.limits["world"]["chunk_size"]
        self.locks = SelectionLocks()
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.inflight: Counter[str] = Counter()
        self.last_request: dict[str, float] = {}
        self.workers: list[asyncio.Task[None]] = []

    # ---- lifecycle

    def start(self) -> None:
        self.workers = [
            asyncio.create_task(self.worker())
            for _ in range(self.limits["queue"]["concurrency"])
        ]

    async def stop(self) -> None:
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []

    # ---- quota

    def day(self) -> str:
        offset = timedelta(hours=self.limits["quota_utc_offset_hours"])
        return (datetime.fromtimestamp(self.clock(), UTC) + offset).strftime("%Y-%m-%d")

    def link_subject(self, anonymous: str, account: str) -> None:
        for job in self.jobs.values():
            if job.subject == anonymous:
                job.subject = account
        self.inflight[account] += self.inflight.pop(anonymous, 0)
        last = self.last_request.pop(anonymous, None)
        if last is not None:
            self.last_request[account] = max(last, self.last_request.get(account, last))

    def quota(self, session: dict[str, Any]) -> dict[str, Any]:
        repo = self.get_repo()
        plan = repo.plan_of(session, int(self.clock() * 1000))
        used = repo.quota_used(repo.subject_of(session), self.day())
        return {"used": used, "limit": self.limits["quota_per_day"][plan], "plan": plan}

    def upgrade_hint(self, plan: str) -> dict[str, int]:
        """What the next plan would give, for the "out of ✨" hint: signing in adds `more`
        a day; Plus allows `plusLimit` a day."""
        limits = self.limits["quota_per_day"]
        if plan == "anon":
            return {"more": limits["free"] - limits["anon"]}
        if plan == "free":
            return {"plusLimit": limits["plus"]}
        return {}

    # ---- step 1-3: request

    def submit(
        self, canvas: dict[str, Any], session: dict[str, Any], polygon: list[tuple[float, float]]
    ) -> tuple[Job, dict[str, int]]:
        """Validate, select and lock strokes, and queue the job. Synchronous on purpose."""
        repo = self.get_repo()
        if canvas["frozen"]:
            raise AppError("CANVAS_FROZEN")
        subject = repo.subject_of(session)
        quota = self.quota(session)
        # Jobs still running count, so a burst cannot overspend the day.
        if quota["used"] + self.inflight[subject] >= quota["limit"]:
            raise AppError("QUOTA_EXCEEDED", details={**quota, **self.upgrade_hint(quota["plan"])})
        last = self.last_request.get(subject)
        if last is not None and self.monotonic() - last < self.limits["min_interval_s"]:
            raise AppError("RATE_LIMITED")
        if self.queue.qsize() >= self.limits["queue"]["max_queued"]:
            raise AppError("RATE_LIMITED")
        strokes = select_strokes(repo, canvas, polygon, self.config.limits, self.chunk_size)
        x0, y0, x1, y1 = bbox(polygon)
        caps = self.config.limits["population"]
        size = self.chunk_size
        cx, cy = int((x0 + x1) / 2 // size), int((y0 + y1) / 2 // size)
        here = repo.living_in_rect(canvas["id"], cx * size, cy * size, (cx + 1) * size, (cy + 1) * size)
        if len(here) >= caps["living_per_chunk"] or repo.count_living(canvas["id"]) >= caps["living_per_world"]:
            raise AppError("ENTITY_LIMIT")
        job = Job(
            id=str(uuid4()),
            canvas_id=canvas["id"],
            session_id=session["id"],
            subject=subject,
            polygon=polygon,
            strokes=strokes,
            rooms=sorted(
                {room(canvas["id"], (s["cx"], s["cy"])) for s in strokes}
                | {
                    room(canvas["id"], (x, y))
                    for x in range(int(x0 // size), int(x1 // size) + 1)
                    for y in range(int(y0 // size), int(y1 // size) + 1)
                }
            ),
        )
        self.locks.acquire(canvas["id"], job.seqs, job.id)
        repo.create_job(job.id, job.canvas_id, job.session_id, polygon, job.seqs)
        self.last_request[subject] = self.monotonic()
        self.inflight[subject] += 1
        self.jobs[job.id] = job
        self.queue.put_nowait(job)
        return job, {"used": quota["used"], "limit": quota["limit"]}

    async def announce(self, job: Job, by: str | None) -> None:
        """The goo: everyone near the lasso sees it before the request returns."""
        await self.realtime.emit(
            "alive:goo", {"jobId": job.id, "polygon": job.polygon, "by": by}, job.rooms
        )

    # ---- step 4-8: the worker

    async def worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.run(job)
            except AppError as error:
                await self.fail(job, error.code)
            except Exception:
                logger.exception("ALIVE job %s failed", job.id)
                await self.fail(job, "INTERNAL")
            finally:
                self.locks.release(job.id)
                self.inflight[job.subject] -= 1
                self.jobs.pop(job.id, None)

    async def run(self, job: Job) -> None:
        repo, started = self.get_repo(), self.monotonic()
        hard = self.limits["hard_timeout_s"]

        def lap(name: str) -> None:
            job.timings[name] = round((self.monotonic() - started) * 1000)

        async with asyncio.timeout(hard):
            repo.update_job(job.id, status="raster")
            cutout = await asyncio.to_thread(
                rasterize, job.strokes, self.limits["raster_long_side"], self.limits["raster_pad"]
            )
            lap("raster")
            long_side = (max(cutout.image.size) - 2 * self.limits["raster_pad"]) / cutout.scale
            sizes = self.config.balance["size_class_px"]
            repo.update_job(job.id, status="classify")
            await self.progress(job, "classify")
            classified: Classified | None = None
            if self.config.flags.vlm.enabled:
                try:
                    png = await asyncio.to_thread(self.png, cutout.on_white())
                    result = await self.vlm.classify(png)
                    repo.update_job(
                        job.id,
                        vlm_result={"prompt": self.vlm.prompt_version, **result.model_dump()},
                    )
                    classified = postprocess(
                        result, self.config.labels, long_side, sizes, self.limits["confidence_min"]
                    )
                except VlmError as error:
                    # Not shown as an error: the author gets the manual grid instead.
                    repo.update_job(job.id, error_code=error.code)
            lap("classify")

        # Asking the author is not part of the time budget.
        if classified is None or classified.choices:
            chosen = await self.ask(job, classified.choices if classified else None)
            classified = self.retype(classified, chosen, size_class(long_side, sizes))
            lap("choose")

        async with asyncio.timeout(hard):
            repo.update_job(job.id, status="rig", chosen_type=classified.type)
            await self.progress(job, "rig")
            sheet, manifest, shrink = await asyncio.to_thread(self.rig, cutout, classified)
            polygon = (
                await asyncio.to_thread(area_polygon, cutout.image)
                if classified.type == "water"
                else None
            )
            sprite_id = str(uuid4())
            paths = await asyncio.to_thread(
                save_sprite, self.settings.sprites_dir, sprite_id, cutout.image, sheet
            )
            lap("rig")
        entity = self.spawn(job, classified, cutout, sprite_id, paths, manifest, shrink, polygon)
        await self.realtime.emit(
            "stroke:fate", {"seqs": job.seqs, "fate": "consumed", "entityId": entity["id"]}, job.rooms
        )
        await self.realtime.emit(
            "world:event", {"type": "spawn", "jobId": job.id, "entity": entity}, job.rooms
        )
        self.on_spawn(entity)

    @staticmethod
    def png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    async def progress(self, job: Job, stage: str) -> None:
        await self.realtime.emit("alive:progress", {"jobId": job.id, "stage": stage}, job.rooms)

    async def ask(self, job: Job, choices: list[str] | None) -> str:
        """Ask the author: three guesses, or the whole grid when the VLM gave nothing.

        Never a dead end: after the deadline the first guess (or a living scribble) wins.
        """
        timeout = self.limits["choose_timeout_s"]
        job.choice = asyncio.get_running_loop().create_future()
        self.get_repo().update_job(job.id, status="choose")
        await self.realtime.emit(
            "alive:choose",
            {
                "jobId": job.id,
                "top3": choices,
                "deadline": int(self.clock() * 1000) + timeout * 1000,
            },
            [session_room(job.session_id)],
        )
        try:
            return await asyncio.wait_for(job.choice, timeout)
        except TimeoutError:
            return choices[0] if choices else "doodle"
        finally:
            job.choice = None

    def choose(self, job_id: str, session: dict[str, Any], type_name: str) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.session_id != session["id"]:
            # Finished, unknown, or someone else's: the author's window has closed.
            stored = self.get_repo().get_job(job_id)
            if stored is None or stored["session_id"] != session["id"]:
                raise AppError("NOT_FOUND")
            raise AppError("CHOICE_EXPIRED")
        if type_name not in TYPES:
            raise AppError("VALIDATION_FAILED", "type")
        if job.choice is None or job.choice.done():
            raise AppError("CHOICE_EXPIRED")
        job.choice.set_result(type_name)

    def retype(self, classified: Classified | None, type_name: str, size: str) -> Classified:
        """Apply the author's choice. A different type starts from the archetype's defaults."""
        if classified is not None and classified.type == type_name:
            classified.choices = []
            return classified
        archetype = self.config.archetypes[type_name]
        components = archetype["components"]
        default = lambda name, key: (components.get(name) or {}).get("default") or (  # noqa: E731
            None if (components.get(name) or {}).get(key) == "from_vlm" else (components.get(name) or {}).get(key)
        )
        empty = {"legs": [], "wings": [], "wheels": [], "head": None, "tail": None}
        return Classified(
            type=type_name,
            label={lang: archetype["title"][lang].lower() for lang in ("ru", "en")},
            size_class=size,
            locomotion=default("Locomotion", "mode") or "none",
            diet=default("Diet", "kind"),
            role=default("Role", "kind"),
            variant=None,
            facing=classified.facing if classified else "right",
            parts=classified.parts if classified else empty,
            confidence=1.0,
        )

    def spawn(
        self,
        job: Job,
        classified: Classified,
        cutout: Cutout,
        sprite_id: str,
        paths: tuple[str, str],
        manifest: dict[str, Any],
        shrink: float = 1.0,
        polygon: list[list[float]] | None = None,
    ) -> dict[str, Any]:
        """One transaction: sprite, consumed fates, entity, job state and the quota."""
        repo = self.get_repo()
        pad = self.limits["raster_pad"]
        width = (cutout.image.width - 2 * pad) / cutout.scale
        height = (cutout.image.height - 2 * pad) / cutout.scale
        x = cutout.x + pad / cutout.scale + width / 2
        y = cutout.y + pad / cutout.scale + height / 2
        health = (self.config.archetypes[classified.type]["components"].get("Health") or {}).get("hp", 100)
        entity_id = str(uuid4())
        repo.db.execute("BEGIN IMMEDIATE")
        try:
            repo.insert_sprite(sprite_id, job.id, paths[0], paths[1], manifest)
            repo.insert_fates(job.canvas_id, job.seqs, "consumed", entity_id)
            repo.insert_entity(
                {
                    "id": entity_id,
                    "canvas_id": job.canvas_id,
                    "sprite_id": sprite_id,
                    "archetype": classified.type,
                    "label": classified.label,
                    "x": x, "y": y, "vx": 0.0, "vy": 0.0,
                    "facing": classified.facing,
                    "state": "idle",
                    "hp": health, "hunger": 0.0, "thirst": 0.0, "energy": 100.0,
                    "params": {
                        "w": round(width, 1),
                        "h": round(height, 1),
                        "scale": cutout.scale * shrink,
                        # A drawn area (water) in world coordinates.
                        "polygon": polygon
                        and [
                            [round(cutout.x + px / cutout.scale, 1), round(cutout.y + py / cutout.scale, 1)]
                            for px, py in polygon
                        ],
                        "sizeClass": classified.size_class,
                        "locomotion": classified.locomotion,
                        "diet": classified.diet,
                        "role": classified.role,
                        "variant": classified.variant,
                        "parts": classified.parts,
                    },
                    "origin_x": x, "origin_y": y,
                    "summoner_session_id": job.session_id,
                    "born_at": now_ms(),
                }  # fmt: skip
            )
            repo.update_job(job.id, status="spawned", chosen_type=classified.type, timings=job.timings)
            repo.spend_quota(job.subject, self.day())
            repo.db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            repo.db.execute("ROLLBACK")
            # A stroke got a fate while we worked (an explosion, usually).
            raise AppError("STROKES_ALREADY_ALIVE") from exc
        except BaseException:
            if repo.db.in_transaction:
                repo.db.execute("ROLLBACK")
            raise
        entity = repo.get_entity(entity_id)
        assert entity is not None
        return entity

    async def fail(self, job: Job, code: str) -> None:
        """The goo fizzles and the strokes stay. Only UNSAFE_CONTENT costs quota."""
        repo = self.get_repo()
        repo.update_job(job.id, status="failed", error_code=code, timings=job.timings)
        if code == "UNSAFE_CONTENT":
            repo.spend_quota(job.subject, self.day())
        await self.realtime.emit("alive:failed", {"jobId": job.id, "code": code}, job.rooms)

    # ---- status for the author

    def status(self, job_id: str, session: dict[str, Any]) -> dict[str, Any]:
        repo = self.get_repo()
        job = repo.get_job(job_id)
        if job is None or job["session_id"] != session["id"]:
            raise AppError("NOT_FOUND")
        row = repo.db.execute(
            "SELECT e.id FROM entity e JOIN sprite s ON s.id = e.sprite_id WHERE s.job_id=?",
            (job_id,),
        ).fetchone()
        return {
            "status": job["status"],
            "type": job["chosen_type"],
            "entityId": row[0] if row else None,
            "error": job["error_code"] if job["status"] == "failed" else None,
        }
