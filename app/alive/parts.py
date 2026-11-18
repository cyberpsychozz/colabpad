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
SMALL_LOOP = 0.25      # of the drawing's height
WHOLE_LEG_STUB = 0.4   # a stroke at most this much outside its leg box, at one end, is all leg
TOUCH = 0.03           # of the drawing's size (plus the widest line): lines this close belong together
ATTACH = 0.08          # of the drawing's height (plus the line's width): how near the body a separate leg or tail starts
CHAIN_GAP = 1.5        # world px between one piece's end and the next one's start
CHUNK_SIZE = 1024      # world px, as config/limits.yaml world.chunk_size (checked by a test)
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


def on_border(point: list[float]) -> bool:
    return any(abs(value - round(value / CHUNK_SIZE) * CHUNK_SIZE) <= 0.01 for value in point[:2])


def chains(strokes: list[Stroke]) -> list[list[Stroke]]:
    """Strokes as the player drew them. The client cuts a line where it crosses a chunk
    border (web/src/draw.js): the pieces follow each other and share the cut point, which
    lies exactly on the border. Joined back, a dress crossing a border is one line again,
    not two short "legs". A torso that merely ends where a leg begins stays separate."""
    out: list[list[Stroke]] = []
    for stroke in strokes:
        if out and len(stroke["points"]) > 1:
            last = out[-1][-1]
            end, start = last["points"][-1], stroke["points"][0]
            if (len(last["points"]) > 1 and last["color"] == stroke["color"] and last["width"] == stroke["width"]
                    and math.hypot(end[0] - start[0], end[1] - start[1]) <= CHAIN_GAP and on_border(start)):  # fmt: skip
                out[-1].append(stroke)
                continue
        out.append([stroke])
    return out


def main_box(frames: list[list[tuple[float, float]]], reach: float) -> tuple[tuple[float, float, float, float], set[int]]:
    """The drawing itself: the largest group of lines that touch each other, as its box
    and the chains in it. A word written under a cow is ink too, but it is not where the
    cow's feet are, and it never moves."""
    parent = list(range(len(frames)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Points in a grid of reach-sized cells; two lines touch if any of their points in
    # the same or a neighbouring cell are within reach of each other.
    cells: dict[tuple[int, int], list[tuple[float, float, int]]] = {}
    for n, frame in enumerate(frames):
        for x, y in frame[::2]:
            cells.setdefault((int(x // reach), int(y // reach)), []).append((x, y, n))
    limit = reach * reach
    for (cx, cy), here in cells.items():
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1), (1, -1)):
            there = cells.get((cx + dx, cy + dy))
            if not there:
                continue
            for x, y, n in here:
                for u, v, m in there:
                    if m != n and root(m) != root(n) and (x - u) ** 2 + (y - v) ** 2 <= limit:
                        parent[root(m)] = root(n)
    # The biggest group by the area it spans: a written word has many dense little
    # strokes, the drawing covers more ground.
    spans: dict[int, list[float]] = {}
    for n, frame in enumerate(frames):
        if not frame:
            continue
        xs, ys = [x for x, _ in frame], [y for _, y in frame]
        box = spans.setdefault(root(n), [min(xs), min(ys), max(xs), max(ys)])
        box[:] = [min(box[0], min(xs)), min(box[1], min(ys)), max(box[2], max(xs)), max(box[3], max(ys))]
    biggest = max(spans, key=lambda r: (spans[r][2] - spans[r][0] + 1) * (spans[r][3] - spans[r][1] + 1))
    members = {n for n in range(len(frames)) if root(n) == biggest}
    ink = np.array([xy for n in members for xy in frames[n]])
    (x0, y0), (x1, y1) = ink.min(axis=0), ink.max(axis=0)
    # Lines inside the drawing belong to it even when they do not touch it (a wing drawn
    # on the chest, an eye); what lies outside its box (a caption) does not.
    for n, frame in enumerate(frames):
        if n not in members and frame:
            cx, cy = np.mean(np.array(frame), axis=0)
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                members.add(n)
    return (float(x0), float(y0), float(x1), float(y1)), members


def hanging_legs(frames: list[np.ndarray], drawing: tuple[float, float, float, float],
                 known: list[Region]) -> list[Region]:  # fmt: skip
    """Legs the VLM did not box. A walker's leg is usually a stroke of its own: narrow,
    hanging from the middle of the body down to the ground."""
    left, top, right, bottom = drawing
    height, width = bottom - top, right - left
    found: list[Region] = []
    for frame in frames:
        if len(frame) < 2:
            continue
        (x0, y0), (x1, y1) = frame.min(axis=0), frame.max(axis=0)
        if not (bottom + 2 >= y1 >= bottom - GROUND * height and y0 >= top + 0.3 * height
                and y1 - y0 >= 0.15 * height and x1 - x0 <= 0.6 * (y1 - y0) + 0.05 * width):  # fmt: skip
            continue
        middle = ((x0 + x1) / 2, (y0 + y1) / 2)
        if any(r.kind == "legs" and r.contains(*middle) for r in known + found):
            continue
        margin = 0.02 * width
        found.append(Region("legs", len(known) + len(found), x0 - margin, y0 - margin, x1 + margin, max(y1, bottom) + 2))
    return found


def split(cutout: Cutout, parts: dict[str, Any], pad: int, box_px: Any,
          drawing: tuple[float, float, float, float], walker: bool = False) -> Split:  # fmt: skip
    """Body strokes and part strokes, in world coordinates, with each part's pivot.
    A walker also gets the legs the VLM missed (hanging_legs)."""
    assert cutout.strokes is not None
    step = STEP_PX / cutout.scale  # world units
    prepared = []
    for chain in chains(cutout.strokes):
        points: list[list[float]] = []
        for k, piece in enumerate(chain):
            dense = curve(piece["points"], step)
            points += dense if k == 0 else dense[1:]
        prepared.append((chain, points, [to_frame(cutout, pad, p[0], p[1]) for p in points]))
    # Legs reach the ground of the drawing itself, not of a caption written under it.
    # Lines touch edge to edge, so their width counts.
    widest = max(stroke["width"] for stroke in cutout.strokes) * cutout.scale
    reach = TOUCH * max(drawing[2] - drawing[0], drawing[3] - drawing[1]) + widest + 2
    drawing, members = main_box([frame for _, _, frame in prepared], reach)
    found = regions(parts, box_px, drawing)
    if walker:
        found += hanging_legs([np.array(frame) for _, _, frame in prepared], drawing, found)
    wheels = [r for r in found if r.kind == "wheels"]
    others = [r for r in found if r.kind != "wheels"]
    owned: dict[int, SplitPart] = {i: SplitPart(r.kind, r.index) for i, r in enumerate(found)}
    body: list[Stroke] = []
    joints: dict[int, list[tuple[float, float]]] = {i: [] for i in owned}

    # First pass: who owns each sample of each stroke.
    labelled: list[tuple[list[Stroke], list[list[float]], list[tuple[float, float]], list[int]]] = []
    for n, (chain, points, frame) in enumerate(prepared):
        stroke = chain[0]
        # A wheel takes whole strokes only.
        wheel = next((found.index(w) for w in wheels
                      if sum(w.in_circle(x, y) for x, y in frame) >= WHEEL_INSIDE * len(frame)), None)  # fmt: skip
        if wheel is not None:
            owned[wheel].strokes.extend(chain)
            continue
        # A caption, or anything else apart from the drawing, never moves (wheels, which
        # are often drawn a little apart from the car, were handled above).
        if n not in members or small_loop(frame, stroke["width"] * cutout.scale, drawing):
            labelled.append((chain, points, frame, [-1] * len(points)))
            continue
        labels = []
        for x, y in frame:
            inside = [r for r in others if r.contains(x, y)]
            labels.append(found.index(min(inside, key=lambda r: r.closeness(x, y))) if inside else -1)
        labels = one_leg_per_stretch(labels, frame, found)
        labels = smooth(free_ends(upright_legs(labels, frame, found, drawing), found))
        labels = whole_leg_strokes(labels, frame, found)
        labelled.append((chain, points, frame, labels))

    # Second pass: a stroke that is a leg or a tail from end to end must hang from the
    # body (words written under a drawing sit in the leg boxes too, but apart from it).
    body_ink = [xy for _, _, frame, labels in labelled for xy, label in zip(frame, labels) if label == -1]
    ink_tree = np.array(body_ink) if body_ink else None
    for n, (chain, points, frame, labels) in enumerate(labelled):
        if ink_tree is None or -1 in labels or found[labels[0]].kind not in ("legs", "tail") or len(set(labels)) > 1:
            continue
        reach = ATTACH * (drawing[3] - drawing[1]) + chain[0]["width"] * cutout.scale
        ends = np.array([frame[0], frame[-1], min(frame, key=lambda xy: xy[1])])
        if ((ends[:, None, :] - ink_tree[None, :, :]) ** 2).sum(axis=2).min() > reach * reach:
            labelled[n] = (chain, points, frame, [-1] * len(labels))

    for chain, points, frame, labels in labelled:
        if all(label == -1 for label in labels):
            body.extend(chain)  # untouched: drawn exactly as stored
            continue
        for label, start, end in runs(labels):
            # Drawn as polylines along the curve (see render_stroke's "dense" case).
            piece = {**chain[0], "points": points[start:end + 1], "dense": True}
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


def small_loop(frame: list[tuple[float, float]], width: float, drawing: tuple[float, float, float, float]) -> bool:
    """A small closed loop (a letter o, a spot, an eye) is never a moving part."""
    if len(frame) < 3:
        return False
    xs, ys = [x for x, _ in frame], [y for _, y in frame]
    closed = math.hypot(frame[0][0] - frame[-1][0], frame[0][1] - frame[-1][1]) <= 3 * width
    return closed and max(max(xs) - min(xs), max(ys) - min(ys)) < SMALL_LOOP * (drawing[3] - drawing[1])


def whole_leg_strokes(labels: list[int], frame: list[tuple[float, float]], found: list[Region]) -> list[int]:
    """A leg drawn as its own stroke is a leg from end to end, even where its box starts
    below the hip or the foot slants out of it: otherwise it would bend at the box's edge."""
    pieces = runs(labels)
    if len(pieces) != 2:
        return labels
    (first, a0, a1), (second, b0, b1) = pieces
    if first == -1 and second != -1 and found[second].kind == "legs":
        stub, leg = (a0, a1), (b0, b1)
    elif second == -1 and first != -1 and found[first].kind == "legs":
        stub, leg = (b0, b1), (a0, a1)
    else:
        return labels
    # By length along the line: samples are not evenly spaced along a curve.
    def length(span: tuple[int, int]) -> float:
        return sum(math.dist(frame[i], frame[i + 1]) for i in range(span[0], min(span[1] + 1, len(frame) - 1)))

    if length(stub) <= WHOLE_LEG_STUB * (length(stub) + length(leg)):
        return [labels[leg[0]]] * len(labels)
    return labels


def one_leg_per_stretch(labels: list[int], frame: list[tuple[float, float]], found: list[Region]) -> list[int]:
    """Where leg boxes overlap, a stretch of line inside them is one leg, not two halves
    swinging opposite ways."""
    out = list(labels)
    is_leg = [label != -1 and found[label].kind == "legs" for label in out]
    start = 0
    while start < len(out):
        if not is_leg[start]:
            start += 1
            continue
        end = start
        while end + 1 < len(out) and is_leg[end + 1]:
            end += 1
        # The leg whose box is centred nearest the middle of the stretch.
        xs = [x for x, _ in frame[start:end + 1]]
        ys = [y for _, y in frame[start:end + 1]]
        middle = (sum(xs) / len(xs), sum(ys) / len(ys))
        winner = min(set(out[start:end + 1]), key=lambda label: found[label].closeness(*middle))
        out[start:end + 1] = [winner] * (end - start + 1)
        start = end + 1
    return out


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
