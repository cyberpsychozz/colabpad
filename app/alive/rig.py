"""ALIVE step 7: a procedural rig made from the user's own lines.

The cutout is split into layers by the part boxes the VLM marked; each animation
moves those layers with plain affine transforms and strip shifts. The result is a
sprite sheet plus the manifest in app/alive/bake.py.
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from app.alive.postprocess import Classified
from app.alive.rasterize import Cutout

FRAMES = {"idle": 4, "move": 8, "act": 4, "death": 6}
FPS = {"idle": 4, "move": 10, "act": 8, "death": 8}
COLUMNS = 8
MAX_FRAME_W, MAX_FRAME_H = 256, 384  # 8 x 256 and 4 x 384 fit a 2048 x 2048 sheet
FRAME_PAD = 0.16
BODY_OVERLAP = 2
FOSSIL_GREY = 150
UNRIGGED = {"water", "sky"}

Matrix = np.ndarray


def translate(dx: float, dy: float) -> Matrix:
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], dtype=float)


def about(matrix: Matrix, x: float, y: float) -> Matrix:
    return translate(x, y) @ matrix @ translate(-x, -y)


def rotate(degrees: float, x: float, y: float) -> Matrix:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return about(np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float), x, y)


def scale(sx: float, sy: float, x: float, y: float) -> Matrix:
    return about(np.diag([sx, sy, 1.0]), x, y)


def warp(layer: Image.Image, matrix: Matrix) -> Image.Image:
    """Apply a forward affine transform (PIL wants the inverse mapping)."""
    inverse = np.linalg.inv(matrix)
    return layer.transform(
        layer.size, Image.Transform.AFFINE, tuple(inverse[:2].flatten()), Image.Resampling.BICUBIC
    )


def shift_rows(image: Image.Image, offsets: np.ndarray) -> Image.Image:
    """Shift each row sideways by a whole number of pixels (wind sway, flame wobble)."""
    pixels = np.asarray(image)
    out = np.zeros_like(pixels)
    width = pixels.shape[1]
    for y, dx in enumerate(np.rint(offsets).astype(int)):
        if dx > 0:
            out[y, dx:] = pixels[y, : width - dx]
        elif dx < 0:
            out[y, : width + dx] = pixels[y, -dx:]
        else:
            out[y] = pixels[y]
    return Image.fromarray(out, "RGBA")


def shift_columns(image: Image.Image, offsets: np.ndarray) -> Image.Image:
    """Shift each column up or down (the swimming wave)."""
    return shift_rows(image.transpose(Image.Transpose.TRANSPOSE), offsets).transpose(
        Image.Transpose.TRANSPOSE
    )


@dataclass
class Part:
    kind: str
    layer: Image.Image
    pivot: tuple[float, float]
    index: int = 0


@dataclass
class Rig:
    body: Image.Image
    parts: list[Part]
    width: int
    height: int
    # The drawing's box inside the frame canvas.
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2


def build_rig(cutout: Image.Image, parts: dict[str, Any]) -> Rig:
    """Place the cutout on a padded frame canvas and cut it into body and part layers."""
    pad = math.ceil(max(cutout.size) * FRAME_PAD)
    width, height = cutout.width + 2 * pad, cutout.height + 2 * pad
    canvas = Image.new("RGBA", (width, height))
    canvas.paste(cutout, (pad, pad))
    alpha = np.asarray(canvas)[:, :, 3]
    rows, columns = np.where(alpha.any(axis=1))[0], np.where(alpha.any(axis=0))[0]
    rig = Rig(canvas, [], width, height, columns[0], rows[0], columns[-1] + 1, rows[-1] + 1)
    center_x, center_y = rig.center

    def pixels(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        x, y, w, h = box
        return (
            pad + round(x * cutout.width),
            pad + round(y * cutout.height),
            pad + round((x + w) * cutout.width),
            pad + round((y + h) * cutout.height),
        )

    def nearest_edge(x0: int, y0: int, x1: int, y1: int) -> tuple[float, float]:
        """The middle of the box side that faces the body's centre."""
        sides = [
            ((x0, (y0 + y1) / 2), abs(center_x - x0)),
            ((x1, (y0 + y1) / 2), abs(center_x - x1)),
            (((x0 + x1) / 2, y0), abs(center_y - y0)),
            (((x0 + x1) / 2, y1), abs(center_y - y1)),
        ]
        return min(sides, key=lambda side: side[1])[0]

    listed = [(kind, i, box) for kind in ("legs", "wings", "wheels") for i, box in enumerate(parts.get(kind) or [])]
    listed += [(kind, 0, parts[kind]) for kind in ("head", "tail") if parts.get(kind)]
    body = np.asarray(canvas).copy()
    for kind, index, box in listed:
        x0, y0, x1, y1 = pixels(box)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        layer = Image.new("RGBA", (width, height))
        layer.paste(canvas.crop((x0, y0, x1, y1)), (x0, y0))
        if not np.asarray(layer)[:, :, 3].any():
            continue
        pivot = {
            "legs": ((x0 + x1) / 2, y0),
            "wheels": ((x0 + x1) / 2, (y0 + y1) / 2),
            "head": ((x0 + x1) / 2, y1),
        }.get(kind) or nearest_edge(x0, y0, x1, y1)
        rig.parts.append(Part(kind, layer, pivot, index))
        # The body keeps a thin overlap so no seam opens when the part moves.
        body[y0 + BODY_OVERLAP : y1 - BODY_OVERLAP, x0 + BODY_OVERLAP : x1 - BODY_OVERLAP] = 0
    rig.body = Image.fromarray(body, "RGBA")
    return rig


def compose(rig: Rig, body: Matrix | None = None, moves: dict[int, Matrix] | None = None) -> Image.Image:
    """Body plus parts, each part with its own transform on top of the body's."""
    identity = np.eye(3)
    body = identity if body is None else body
    frame = Image.new("RGBA", (rig.width, rig.height))
    for i, part in enumerate(rig.parts):
        frame.alpha_composite(warp(part.layer, body @ (moves or {}).get(i, identity)))
    frame.alpha_composite(rig.body if body is identity else warp(rig.body, body))
    return frame


def tint(image: Image.Image, color: tuple[int, int, int], amount: float) -> Image.Image:
    pixels = np.asarray(image).astype(float)
    pixels[:, :, :3] += (np.array(color) - pixels[:, :, :3]) * amount
    return Image.fromarray(pixels.astype(np.uint8), "RGBA")


def idle_frames(rig: Rig, kind: str) -> list[Image.Image]:
    whole = compose(rig)
    frames = []
    for i in range(FRAMES["idle"]):
        phase = math.sin(2 * math.pi * i / FRAMES["idle"])
        if kind == "plant":
            # Wind: rows lean sideways, more the higher they are (up to 4% of the width).
            height = np.clip((rig.bottom - np.arange(rig.height)) / (rig.bottom - rig.top), 0, 1)
            frames.append(shift_rows(whole, height * 0.04 * (rig.right - rig.left) * phase))
        elif kind == "vehicle":
            frames.append(warp(whole, translate(0, [0, -1, 0, 1][i])))  # engine shake
        elif kind == "fire":
            rows = np.arange(rig.height)
            wobble = np.sin(rows / 9 + i * math.pi / 2) * 0.03 * (rig.right - rig.left)
            frames.append(shift_rows(whole, wobble * np.clip((rig.bottom - rows) / (rig.bottom - rig.top), 0, 1)))
        else:
            # Breathing: 3% shorter and back, feet planted.
            breath = 1 - 0.03 * (1 - math.cos(2 * math.pi * i / FRAMES["idle"])) / 2
            frames.append(compose(rig, scale(1, breath, rig.center[0], rig.bottom)))
    return frames


def move_frames(rig: Rig, classified: Classified) -> list[Image.Image]:
    kinds = {part.kind for part in rig.parts}
    count = FRAMES["move"]
    height = rig.bottom - rig.top
    frames = []
    for i in range(count):
        turn = 2 * math.pi * i / count
        if classified.locomotion == "swim" and not kinds & {"legs", "wheels"}:
            columns = np.arange(rig.width)
            frames.append(shift_columns(compose(rig), np.sin(columns / (rig.right - rig.left) * 2 * math.pi + turn) * 0.05 * height))
        elif kinds & {"legs", "wings", "wheels"}:
            moves: dict[int, Matrix] = {}
            for n, part in enumerate(rig.parts):
                if part.kind == "legs":
                    # Neighbouring legs swing in counter-phase.
                    moves[n] = rotate(20 * math.sin(turn + part.index * math.pi), *part.pivot)
                elif part.kind == "wings":
                    flap = 0.7 + 0.3 * math.cos(turn)  # 1 -> 0.4 -> 1
                    moves[n] = scale(1, flap, *part.pivot)
                elif part.kind == "wheels":
                    moves[n] = rotate(45 * i, *part.pivot)
                elif part.kind == "tail":
                    moves[n] = rotate(8 * math.sin(turn), *part.pivot)
            bob = -0.03 * height * abs(math.sin(turn)) if "legs" in kinds else 0
            frames.append(compose(rig, translate(0, bob), moves))
        else:
            # No parts: a squash-and-stretch hop.
            stretch = 1 + 0.12 * math.sin(turn)
            hop = -0.10 * height * max(0.0, math.sin(turn))
            frames.append(compose(rig, translate(0, hop) @ scale(1 / stretch, stretch, rig.center[0], rig.bottom)))
    return frames


def act_frames(rig: Rig, classified: Classified) -> list[Image.Image]:
    forward = 1 if classified.facing == "right" else -1
    width = rig.right - rig.left
    frames = []
    for i in range(FRAMES["act"]):
        swing = math.sin(math.pi * i / (FRAMES["act"] - 1))  # 0 -> 1 -> 0
        if classified.type == "explosive":
            pulse = 1 + 0.08 * swing
            frames.append(tint(compose(rig, scale(pulse, pulse, *rig.center)), (229, 57, 53), 0.55 * swing))
        elif classified.type == "fire":
            rows = np.arange(rig.height)
            frames.append(shift_rows(compose(rig), np.sin(rows / 6 + i * 1.7) * 0.06 * width * np.clip((rig.bottom - rows) / (rig.bottom - rig.top), 0, 1)))
        elif classified.type == "predator":
            frames.append(compose(rig, translate(forward * 0.08 * width * swing, 0)))  # lunge
        else:
            frames.append(compose(rig, rotate(forward * 10 * swing, rig.center[0], rig.bottom)))  # lean in to eat
    return frames


def death_frames(rig: Rig) -> list[Image.Image]:
    whole = compose(rig)
    frames = []
    for i in range(FRAMES["death"]):
        progress = i / (FRAMES["death"] - 1)
        turned = warp(whole, rotate(180 * progress, *rig.center))
        frames.append(tint(turned, (FOSSIL_GREY,) * 3, progress))
    return frames


def area_polygon(image: Image.Image, vertices: int = 64) -> list[list[float]]:
    """The outline of a drawn area (a lake is usually a loop, so the inside counts).

    Rays from the centre of the painted pixels to the farthest painted pixel in each
    direction; in image pixels.
    """
    alpha = np.asarray(image)[:, :, 3] > 40
    ys, xs = np.nonzero(alpha)
    cx, cy = xs.mean(), ys.mean()
    angles = np.arctan2(ys - cy, xs - cx)
    distance = np.hypot(xs - cx, ys - cy)
    sector = ((angles + math.pi) / (2 * math.pi) * vertices).astype(int) % vertices
    polygon = []
    for index in range(vertices):
        inside = sector == index
        if inside.any():
            far = np.argmax(np.where(inside, distance, -1))
            polygon.append([float(xs[far]), float(ys[far])])
    return polygon


def rig_sprite(cutout: Cutout, classified: Classified) -> tuple[Image.Image, dict[str, Any], float]:
    """(sheet, manifest, pixels per cutout pixel). Water and sky keep the drawing as it is."""
    if classified.type in UNRIGGED:
        from app.alive.bake import single_frame_manifest

        return cutout.image, single_frame_manifest(cutout.image, classified.facing), 1.0
    rig = build_rig(cutout.image, classified.parts)
    rows = [
        idle_frames(rig, classified.type),
        move_frames(rig, classified) if classified.locomotion != "none" or rig.parts else idle_frames(rig, classified.type) * 2,
        act_frames(rig, classified),
        death_frames(rig),
    ]
    shrink = min(1.0, MAX_FRAME_W / rig.width, MAX_FRAME_H / rig.height)
    frame_w, frame_h = max(1, round(rig.width * shrink)), max(1, round(rig.height * shrink))
    sheet = Image.new("RGBA", (COLUMNS * frame_w, len(rows) * frame_h))
    for row, frames in enumerate(rows):
        for column, frame in enumerate(frames):
            if shrink < 1:
                frame = frame.resize((frame_w, frame_h), Image.Resampling.LANCZOS)
            sheet.paste(frame, (column * frame_w, row * frame_h))
    center_x, center_y = rig.center
    manifest = {
        "frameW": frame_w,
        "frameH": frame_h,
        "pivot": [round(center_x * shrink), round(center_y * shrink)],
        "facing": classified.facing,
        "anims": {
            name: {"row": row, "frames": FRAMES[name], "fps": FPS[name]}
            for row, name in enumerate(FRAMES)
        },
    }
    return sheet, manifest, shrink
