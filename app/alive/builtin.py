"""Built-in sprites for things the world makes itself (fruit, wild fish, flames).

They go through the same rasterizer and rig as a player's drawing, once, at startup.
"""

import math
from pathlib import Path
from typing import Any

from app.alive.bake import save_sprite
from app.alive.postprocess import Classified
from app.alive.rasterize import rasterize
from app.alive.rig import rig_sprite
from app.repo import Repository


def _line(color: str, width: float, points: list[tuple[float, float]]) -> dict[str, Any]:
    return {"color": color, "width": width, "points": [[x, y, 0.5] for x, y in points]}


def _ring(cx: float, cy: float, rx: float, ry: float, steps: int = 14) -> list[tuple[float, float]]:
    return [(cx + rx * math.cos(i / steps * math.tau), cy + ry * math.sin(i / steps * math.tau)) for i in range(steps + 1)]


DRAWINGS: dict[str, tuple[str, list[dict[str, Any]]]] = {
    "builtin-apple": ("food", [
        _line("#E53935", 12, _ring(14, 16, 5, 5)),
        _line("#E53935", 5, _ring(14, 16, 9, 9)),
        _line("#43A047", 3, [(14, 7), (16, 1)]),
    ]),
    "builtin-fish": ("food", [
        _line("#1E88E5", 4, [(4, 12), (16, 4), (32, 12), (16, 20), (4, 12)]),
        _line("#1E88E5", 4, [(32, 12), (42, 4), (42, 20), (32, 12)]),
        _line("#000000", 3, [(11, 10), (11, 10)]),
    ]),
    "builtin-flame": ("fire", [
        _line("#FB8C00", 7, [(18, 46), (6, 34), (10, 20), (18, 4), (26, 20), (30, 34), (18, 46)]),
        _line("#FDD835", 6, [(18, 40), (13, 32), (18, 20), (23, 32), (18, 40)]),
    ]),
}  # fmt: skip


def ensure_builtin_sprites(repo: Repository, sprites_dir: Path) -> None:
    for sprite_id, (type_name, strokes) in DRAWINGS.items():
        exists = repo.db.execute("SELECT 1 FROM sprite WHERE id=?", (sprite_id,)).fetchone()
        if exists and (sprites_dir / sprite_id / "sheet.png").is_file():
            continue
        cutout = rasterize(strokes)
        look = Classified(
            type=type_name, label={}, size_class="S", locomotion="none", diet=None, role=None,
            variant=None, facing="right",
            parts={"legs": [], "wings": [], "wheels": [], "head": None, "tail": None}, confidence=1.0,
        )  # fmt: skip
        sheet, manifest, _ = rig_sprite(cutout, look)
        paths = save_sprite(sprites_dir, sprite_id, cutout.image, sheet)
        if not exists:
            repo.insert_sprite(sprite_id, None, paths[0], paths[1], manifest)
