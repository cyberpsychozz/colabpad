"""ALIVE step 7 (storage half): write the sprite files and their manifest.

The manifest is the contract between whatever makes sprites and the client:
{frameW, frameH, pivot, facing, anims: {name: {row, frames, fps}}}.
"""

from pathlib import Path
from typing import Any

from PIL import Image

ANIMS = ("idle", "move", "act", "death")


def single_frame_manifest(image: Image.Image, facing: str) -> dict[str, Any]:
    """A sprite that never changes: every animation is the one drawing."""
    return {
        "frameW": image.width,
        "frameH": image.height,
        "pivot": [image.width // 2, image.height // 2],
        "facing": facing,
        "anims": {name: {"row": 0, "frames": 1, "fps": 1} for name in ANIMS},
    }


def save_sprite(
    directory: Path, sprite_id: str, cutout: Image.Image, sheet: Image.Image
) -> tuple[str, str]:
    """Write cutout.png and sheet.png; returns their paths relative to the sprites dir."""
    folder = directory / sprite_id
    folder.mkdir(parents=True, exist_ok=True)
    cutout.save(folder / "cutout.png", optimize=True)
    sheet.save(folder / "sheet.png", optimize=True)
    return f"{sprite_id}/cutout.png", f"{sprite_id}/sheet.png"
