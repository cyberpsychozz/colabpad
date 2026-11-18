"""Split the user's strokes into the body and the moving parts the VLM marked.

The VLM's part boxes are rough: too short, overlapping each other, crossing body lines.
Cutting the picture by those rectangles tears the drawing (body lines rotate with a
leg, a leg's lower half stays behind). So instead the strokes themselves are split:

- every stroke is sampled finely along the curve it is drawn as, and each sample goes to the part whose region holds
  it (the nearest part when regions overlap), or to the body;
- a stroke is cut where its owner changes, and both pieces keep the cut point, so a
  leg stays joined to the body exactly where it rotates;
- legs reach down to the ground (their boxes usually stop short);
- a wheel takes only strokes that lie almost entirely inside its circle, so no body
  line ever spins;
- each part turns around its joint: where its lines meet the body's.

Coordinates here are frame pixels: the cutout placed at (pad, pad) on the frame.
"""

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.alive.rasterize import Cutout, Stroke

STEP_PX = 2.5          # resampling distance, in frame pixels
MIN_RUN = 5            # samples; a shorter detour into a part region stays with its neighbour
LEG_SIDE_PAD = 0.04    # of the drawing's width, on each side of a leg box
PART_PAD = 0.03        # of the drawing's size, around wing and tail boxes
WHEEL_GROW = 1.25      # the wheel's circle, relative to its box
WHEEL_INSIDE = 0.8     # share of a stroke that must be inside the circle
FLAT = 1.8             # a leg-region run wider than this times its height is a body line
GROUND = 0.2           # of the drawing's height: a leg comes down at least this close to the bottom
# Parts that move on their own. The head only ever moves with the whole body.
MOVING = ("legs", "wings", "wheels", "tail")


@dataclass
class SplitPart:
    kind: str
    index: int
    strokes: list[Stroke] = field(default_factory=list)
    pivot: tuple[float, float] = (0.0, 0.0)
    # Which way it reaches from its pivot: -1 left, 1 right (a wing lifts away from the body).
    side: int = 1


@dataclass
class Split:
    body: list[Stroke]
    parts: list[SplitPart]


def to_frame(cutout: Cutout, pad: int, x: float, y: float) -> tuple[float, float]:
    return pad + (x - cutout.x) * cutout.scale, pad + (y - cutout.y) * cutout.scale


def curve(points: list[list[float]], step: float) -> list[list[float]]:
    """The line render_stroke actually draws, as points no more than `step` apart.

    render_stroke draws quadratic curves through the midpoints of the stored points, so a
    stroke with few points is visibly rounder than its polyline. Pieces of a split stroke
    are drawn from these samples, so they follow the original curve; each sample carries
    the pressure render_stroke uses for its segment.
    """
    if len(points) < 2:
        return [list(p) for p in points]
    out = [list(points[0])]
    start = points[0]
    for i in range(1, len(points)):
        control, point = points[i - 1], points[i]
        end = point if i == len(points) - 1 else ((control[0] + point[0]) / 2, (control[1] + point[1]) / 2)
        pressure = (control[2] + point[2]) / 2
        length = math.hypot(control[0] - start[0], control[1] - start[1]) + math.hypot(end[0] - control[0], end[1] - control[1])
        pieces = max(1, math.ceil(length / step))
        for k in range(1, pieces + 1):
            t = k / pieces
            a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
            out.append([a * start[0] + b * control[0] + c * end[0], a * start[1] + b * control[1] + c * end[1], pressure])
        start = end
    out[0][2] = out[1][2] if len(out) > 1 else out[0][2]
    return out


@dataclass
class Region:
    kind: str
    index: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def closeness(self, x: float, y: float) -> float:
        """Distance from the centre in units of the region's half size (smaller is closer)."""
        cx, cy = self.center
        return max(abs(x - cx) / max(1.0, (self.x1 - self.x0) / 2), abs(y - cy) / max(1.0, (self.y1 - self.y0) / 2))

    def in_circle(self, x: float, y: float) -> bool:
        cx, cy = self.center
        rx, ry = (self.x1 - self.x0) / 2 * WHEEL_GROW, (self.y1 - self.y0) / 2 * WHEEL_GROW
        return ((x - cx) / max(rx, 1.0)) ** 2 + ((y - cy) / max(ry, 1.0)) ** 2 <= 1


def regions(parts: dict[str, Any], box_px: Any, drawing: tuple[float, float, float, float]) -> list[Region]:
    left, top, right, bottom = drawing
    width, height = right - left, bottom - top
    out = []
    for kind in MOVING:
        boxes = parts.get(kind) or []
        if kind == "tail":
            boxes = [boxes] if boxes else []
        for index, box in enumerate(boxes):
            x0, y0, x1, y1 = box_px(box)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            if kind == "legs":
                pad = LEG_SIDE_PAD * width
                x0, x1, y1 = x0 - pad, x1 + pad, max(y1, bottom + 2)
            elif kind != "wheels":
                pad = PART_PAD * max(width, height)
                x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
            out.append(Region(kind, index, x0, y0, x1, y1))
    return out


def split(cutout: Cutout, parts: dict[str, Any], pad: int, box_px: Any,
          drawing: tuple[float, float, float, float]) -> Split:  # fmt: skip
    """Body strokes and part strokes, in world coordinates, with each part's pivot."""
    assert cutout.strokes is not None
    found = regions(parts, box_px, drawing)
    wheels = [r for r in found if r.kind == "wheels"]
    others = [r for r in found if r.kind != "wheels"]
    owned: dict[int, SplitPart] = {i: SplitPart(r.kind, r.index) for i, r in enumerate(found)}
    body: list[Stroke] = []
    joints: dict[int, list[tuple[float, float]]] = {i: [] for i in owned}
    step = STEP_PX / cutout.scale  # world units

    for stroke in cutout.strokes:
        points = curve(stroke["points"], step)
        frame = [to_frame(cutout, pad, p[0], p[1]) for p in points]
        # A wheel takes whole strokes only.
        wheel = next((found.index(w) for w in wheels
                      if sum(w.in_circle(x, y) for x, y in frame) >= WHEEL_INSIDE * len(frame)), None)  # fmt: skip
        if wheel is not None:
            owned[wheel].strokes.append(stroke)
            continue
        labels = []
        for x, y in frame:
            inside = [r for r in others if r.contains(x, y)]
            labels.append(found.index(min(inside, key=lambda r: r.closeness(x, y))) if inside else -1)
        labels = smooth(free_ends(upright_legs(labels, frame, found, drawing), found))
        if all(label == -1 for label in labels):
            body.append(stroke)
            continue
        for label, start, end in runs(labels):
            # Drawn as polylines along the curve (see render_stroke's "dense" case).
            piece = {**stroke, "points": points[start:end + 1], "dense": True}
            (body if label == -1 else owned[label].strokes).append(piece)
            # The cut points: where this run meets its neighbours.
            if label != -1:
                if start > 0:
                    joints[label].append(frame[start])
                if end < len(points) - 1:
                    joints[label].append(frame[end])

    result = []
    body_points = np.array([to_frame(cutout, pad, p[0], p[1]) for s in body for p in s["points"]] or [[0.0, 0.0]])
    for i, part in owned.items():
        if not part.strokes:
            continue
        ink = np.array([to_frame(cutout, pad, p[0], p[1]) for s in part.strokes for p in s["points"]])
        part.pivot = pivot_of(found[i], ink, joints[i], body_points if body else None)
        part.side = 1 if ink[:, 0].mean() >= part.pivot[0] else -1
        result.append(part)
    # Legs swing in counter-phase by position, left to right.
    legs = sorted((p for p in result if p.kind == "legs"), key=lambda p: p.pivot[0])
    for n, leg in enumerate(legs):
        leg.index = n
    return Split(body, result)


def upright_legs(labels: list[int], frame: list[tuple[float, float]], found: list[Region],
                 drawing: tuple[float, float, float, float]) -> list[int]:  # fmt: skip
    """Legs hang down to the ground. A flat stretch inside a leg region is the belly line
    passing over it, and one that stops short of the ground is the body's outline; both
    stay with the body."""
    out = list(labels)
    _, top, _, bottom = drawing
    ground = bottom - GROUND * (bottom - top)
    for label, start, end in runs(out):
        if label == -1 or found[label].kind != "legs":
            continue
        xs = [x for x, _ in frame[start:end + 1]]
        ys = [y for _, y in frame[start:end + 1]]
        if max(xs) - min(xs) > FLAT * (max(ys) - min(ys)) or max(ys) < ground:
            out[start:end + 1] = [-1] * (end - start + 1)
    return out


def free_ends(labels: list[int], found: list[Region]) -> list[int]:
    """A wing or a tail ends somewhere: a stretch of a stroke that only passes through
    its region (the body's outline) is not it."""
    out = list(labels)
    for label, start, end in runs(out):
        if label != -1 and found[label].kind in ("wings", "tail") and start > 0 and end < len(out) - 1:
            out[start:end + 1] = [-1] * (end - start + 1)
    return out


def smooth(labels: list[int]) -> list[int]:
    """A short run (a body line grazing a part region) goes to the run before it."""
    out = list(labels)
    for label, start, end in runs(out):
        if end - start + 1 < MIN_RUN and len(out) > MIN_RUN:
            neighbour = out[start - 1] if start > 0 else (out[end + 1] if end + 1 < len(out) else label)
            out[start:end + 1] = [neighbour] * (end - start + 1)
    return out


def runs(labels: list[int]) -> list[tuple[int, int, int]]:
    """(label, first index, last index) of each stretch of equal labels."""
    out, start = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            out.append((labels[start], start, i - 1))
            start = i
    return out


def pivot_of(region: Region, ink: np.ndarray, joints: list[tuple[float, float]],
             body: np.ndarray | None) -> tuple[float, float]:  # fmt: skip
    """Where the part turns: its joint with the body, or the end of it nearest the body."""
    if region.kind == "wheels":
        (x0, y0), (x1, y1) = ink.min(axis=0), ink.max(axis=0)
        return float((x0 + x1) / 2), float((y0 + y1) / 2)
    if joints:
        x, y = np.mean(np.array(joints), axis=0)
        return float(x), float(y)
    if body is not None and len(body):
        # Drawn as a separate stroke: the part's point closest to the body's lines.
        sample = body[:: max(1, len(body) // 400)]
        distance = ((ink[:, None, :] - sample[None, :, :]) ** 2).sum(axis=2).min(axis=1)
        x, y = ink[int(np.argmin(distance))]
        return float(x), float(y)
    (x0, y0), (x1, _) = ink.min(axis=0), ink.max(axis=0)
    return float((x0 + x1) / 2), float(y0)
