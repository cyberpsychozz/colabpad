"""The daily retention pass: old rows out of the database, dead sprites off the disk."""

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.repo import Repository

logger = logging.getLogger(__name__)
DAY_S = 86_400


class Retention:
    def __init__(
        self,
        get_repo: Callable[[], Repository],
        sprites_dir: Path,
        days: dict[str, int],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.get_repo, self.sprites_dir, self.days, self.clock = get_repo, sprites_dir, days, clock
        self.task: asyncio.Task[None] | None = None

    async def run_once(self) -> dict[str, Any]:
        # SQL stays on the loop (one shared connection); only file removal goes to a thread.
        repo, now = self.get_repo(), int(self.clock() * 1000)
        counts = repo.purge_old_rows(now, self.days)
        expired = repo.expired_sprites(now, self.days["sprite_after_death"])
        for sprite in expired:
            repo.forget_sprite_files(sprite["id"])
        await asyncio.to_thread(self.remove_folders, [sprite["id"] for sprite in expired])
        counts["sprites"] = len(expired)
        return counts

    def remove_folders(self, sprite_ids: list[str]) -> None:
        root = self.sprites_dir.resolve()
        for sprite_id in sprite_ids:
            folder = (root / sprite_id).resolve()
            if folder.parent == root:
                shutil.rmtree(folder, ignore_errors=True)

    async def run(self) -> None:
        while True:
            try:
                counts = await self.run_once()
                logger.warning("Retention pass done: %s", counts)
            except Exception:
                logger.exception("Retention pass failed")
            await asyncio.sleep(DAY_S)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
