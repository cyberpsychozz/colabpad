"""ALIVE step 4: rasterize the selected strokes with Skia.

`render_stroke` is a port of renderStroke in web/src/draw.js: the same quadratic
curves through segment midpoints, round caps and joins, and pressure-based width.
A golden test compares it against a real browser render.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import skia
from PIL import Image

Stroke = dict[str, Any]


def _color(value: str) -> int:
    return skia.Color(int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def render_stroke(canvas: skia.Canvas, stroke: Stroke) -> None:
    points = stroke["points"]
    if not points:
        return
    paint = skia.Paint(AntiAlias=True, Color=_color(stroke["color"]))
    first_x, first_y, first_pressure = points[0]
    if all(p[0] == first_x and p[1] == first_y for p in points):
        paint.setStyle(skia.Paint.kFill_Style)
        canvas.drawCircle(first_x, first_y, stroke["width"] * (0.5 + first_pressure) / 2, paint)
        return
    paint.setStyle(skia.Paint.kStroke_Style)
    paint.setStrokeCap(skia.Paint.kRound_Cap)
    paint.setStrokeJoin(skia.Paint.kRound_Join)
    if stroke.get("dense"):
        _render_dense(canvas, points, stroke["width"], paint)
        return
    start = points[0]
    for i in range(1, len(points)):
        control, point = points[i - 1], points[i]
        end = (
            point
            if i == len(points) - 1
            else ((control[0] + point[0]) / 2, (control[1] + point[1]) / 2)
        )
        path = skia.Path()
        path.moveTo(start[0], start[1])
        path.quadTo(control[0], control[1], end[0], end[1])
        paint.setStrokeWidth(stroke["width"] * (0.5 + (control[2] + point[2]) / 2))
        canvas.drawPath(path, paint)
        start = end


def _render_dense(canvas: skia.Canvas, points: list[list[float]], width: float, paint: skia.Paint) -> None:
    """A piece of a stroke the rig split, already sampled along the curve render_stroke
    draws (app/alive/parts.py), with one pressure per original segment. Each stretch of
    equal pressure is one path, so the round caps of many tiny segments do not pile up
    alpha on the edges and the piece looks like the same stretch of the whole stroke."""
    group = [points[0]]
    for point in points[1:]:
        group.append(point)
        if point is points[-1] or point[2] != group[1][2]:
            path = skia.Path()
            path.moveTo(group[0][0], group[0][1])
            for x, y, _ in group[1:]:
                path.lineTo(x, y)
            paint.setStrokeWidth(width * (0.5 + group[-1][2]))
            canvas.drawPath(path, paint)
            group = [point]


def render(strokes: list[Stroke], x: float, y: float, width: int, height: int, scale: float = 1.0) -> np.ndarray:
    """Unpremultiplied RGBA pixels of a world rectangle starting at (x, y)."""
    surface = skia.Surface(width, height)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorTRANSPARENT)
    canvas.scale(scale, scale)
    canvas.translate(-x, -y)
    for stroke in strokes:
        render_stroke(canvas, stroke)
    image = surface.makeImageSnapshot()
    return image.toarray(
        colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType
    )


@dataclass
class Cutout:
    """A drawing cut out of the world: pixel (0, 0) is world (x, y); 1 px = 1/scale world px."""

    image: Image.Image  # RGBA, transparent background
    x: float
    y: float
    scale: float
    # The strokes it was drawn from (world coordinates), so the rig can split them by part.
    strokes: list[Stroke] | None = None

    def on_white(self) -> Image.Image:
        """The copy shown to the VLM."""
        background = Image.new("RGB", self.image.size, "white")
        background.paste(self.image, mask=self.image.getchannel("A"))
        return background


def rasterize(strokes: list[Stroke], long_side: int = 512, pad: int = 8) -> Cutout:
    """Transparent, alpha-trimmed with `pad` px around, long side at most `long_side`."""
    reach = [s["width"] * 0.75 + 1 for s in strokes for _ in s["points"]]
    xs = [p[0] for s in strokes for p in s["points"]]
    ys = [p[1] for s in strokes for p in s["points"]]
    x0 = min(x - r for x, r in zip(xs, reach))
    y0 = min(y - r for y, r in zip(ys, reach))
    x1 = max(x + r for x, r in zip(xs, reach))
    y1 = max(y + r for y, r in zip(ys, reach))
    scale = min(1.0, (long_side - 2 * pad) / max(x1 - x0, y1 - y0))
    pixels = render(
        strokes, x0, y0, int(np.ceil((x1 - x0) * scale)), int(np.ceil((y1 - y0) * scale)), scale
    )
    # Trim to the painted pixels, then put the pad back.
    painted = np.argwhere(pixels[:, :, 3] > 0)
    (top, left), (bottom, right) = painted.min(axis=0), painted.max(axis=0) + 1
    trimmed = pixels[top:bottom, left:right]
    out = np.zeros((trimmed.shape[0] + 2 * pad, trimmed.shape[1] + 2 * pad, 4), dtype=np.uint8)
    out[pad:-pad, pad:-pad] = trimmed
    return Cutout(
        Image.fromarray(out, "RGBA"),
        x0 + (left - pad) / scale,
        y0 + (top - pad) / scale,
        scale,
        strokes,
    )
