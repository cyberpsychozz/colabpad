"""Version 1 choreography: strict model data -> bounded, finite pixel tracks.

Transforms in y-down coordinates: T(translation) T(pivot) R(clockwise) S T(-pivot).
Body transforms parent every layer; each part transforms about its own pivot first.
Easing belongs to the outgoing key. Values hold before/after the first/last key.
"""

import json
import math
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class Key(Strict):
    t: int = Field(ge=0, le=30000, strict=True)
    v: Number
    ease: Literal["linear", "smooth", "in", "out", "hold"]


class Track(Strict):
    target: str = Field(min_length=1, max_length=40)
    property: Literal["x", "y", "rotation", "scaleX", "scaleY"]
    keys: list[Key] = Field(min_length=2, max_length=256)


class Part(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    # Normalized x,y,width,height in the supplied image.
    box: tuple[Number, Number, Number, Number]
    pivot: tuple[Number, Number]  # normalized coordinates in that same image
    order: int = Field(ge=-16, le=16, strict=True)


class Repeat(Strict):
    start: int = Field(ge=0, strict=True)
    # Inclusive bounds are supported by Gemini's structured-output schema.
    # Times are strict integers, so minimum 1 is exactly equivalent to > 0.
    end: int = Field(ge=1, strict=True)
    count: int = Field(ge=2, le=8, strict=True)  # total plays, including the original


class Labels(Strict):
    ru: str = Field(min_length=1, max_length=40)
    en: str = Field(min_length=1, max_length=40)


class Outcome(Strict):
    status: Literal["ok", "unsupported", "unsafe", "multiple"]
    reason: (
        Literal["other_actors", "gameplay", "missing_parts", "cannot_satisfy"] | None
    )


class IdentifiedPart(Part):
    label: Labels
    pivot_type: Literal["joint", "center"]
    kind: Literal["part", "wheel"]


class DrawingParts(Outcome):
    version: Literal[2]
    label: Labels
    parts: list[IdentifiedPart] = Field(max_length=16)


class Motion(Outcome):
    version: Literal[1]
    duration: int = Field(ge=1, le=30000, strict=True)
    tracks: list[Track] = Field(max_length=90)
    repeats: list[Repeat] = Field(max_length=8)


class Generation(Motion):
    """Internal composition; geometry never comes from the motion planner."""

    label: Labels
    parts: list[Part] = Field(max_length=16)


def combine(drawing: DrawingParts, motion: Motion) -> Generation:
    return Generation(
        **motion.model_dump(), label=drawing.label,
        parts=[Part(**p.model_dump(exclude={"label", "pivot_type", "kind"})) for p in drawing.parts],
    )


class PartsError(ValueError):
    """Safe geometry diagnostics: field names and bounded part IDs, no model text."""


def validate_parts(parts: list[Part], maximum: int = 16) -> None:
    ids = [p.id for p in parts]
    if len(ids) != len(set(ids)) or set(ids) & {"root", "body"} or len(ids) > maximum:
        raise PartsError("invalid part ids or count")
    for p in parts:
        if getattr(p, 'kind', None) == 'wheel' and p.pivot_type != 'center':
            raise PartsError(f'part={p.id}: wheel must rotate around its center')
        x, y, w, h = p.box
        if not (w > 0 and h > 0 and x < x + w and y < y + h):
            raise PartsError(f'part={p.id}: box width and height must be positive')
        for field, coordinate in (
            ('box.x', x), ('box.y', y),
            ('box.right (x + width)', x + w), ('box.bottom (y + height)', y + h),
            ('pivot.x', p.pivot[0]), ('pivot.y', p.pivot[1]),
        ):
            # Atlas world/raster round trips can put an edge one ULP outside.
            # This tolerance is far below a pixel, not a geometry repair heuristic.
            if not math.isfinite(coordinate) or not -1e-9 <= coordinate <= 1 + 1e-9:
                raise PartsError(f'part={p.id}: {field} must be within [0,1]')


DEFAULT = {"x": 0.0, "y": 0.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0}


def value(track: dict[str, Any], t: float) -> float:
    keys = track["keys"]
    if t <= keys[0]["t"]:
        return keys[0]["v"]
    for a, b in pairwise(keys):
        if t < b["t"]:
            u = (t - a["t"]) / (b["t"] - a["t"])
            ease = a["ease"]
            if ease == "smooth":
                u = u * u * (3 - 2 * u)
            elif ease == "in":
                u *= u
            elif ease == "out":
                u = 1 - (1 - u) ** 2
            elif ease == "hold":
                u = 0
            return a["v"] + (b["v"] - a["v"]) * u
    return keys[-1]["v"]


def pose(plan: dict[str, Any], t: float, target: str) -> dict[str, float]:
    result = dict(DEFAULT)
    for track in plan["tracks"]:
        if track["target"] == target:
            result[track["property"]] = value(track, t)
    return result


def compile_plan(
    result: Generation,
    width: float,
    height: float,
    *,
    limits=None,
    layers=None,
    origin=None,
    bounds=None,
) -> dict[str, Any]:
    limits = limits or {}

    def limit(name, default):
        return limits.get(name, default)

    if result.status != "ok" or result.reason is not None or not result.tracks:
        raise ValueError("not a performance")
    ids = [p.id for p in result.parts]
    validate_parts(result.parts, limit("parts", 16))
    tracks, seen = [], set()
    for source in result.tracks:
        target, prop = source.target, source.property
        identity = (target, prop)
        if target not in {"root", "body", *ids} or identity in seen:
            raise ValueError("unknown target or conflicting tracks")
        if target == "root" and prop not in ("x", "y"):
            raise ValueError("root only translates")
        seen.add(identity)
        keys = [k.model_dump() for k in source.keys]
        if (
            keys[0]["t"] != 0
            or keys[-1]["t"] > result.duration
            or any(a["t"] >= b["t"] for a, b in pairwise(keys))
        ):
            raise ValueError("invalid key times")
        for key in keys:
            v = key["v"]
            if prop.startswith("scale") and not 0.25 <= v <= 4:
                raise ValueError("scale out of range")
            if prop == "rotation" and abs(v) > 1440:
                raise ValueError("rotation out of range")
            if prop in ("x", "y"):
                if target != "root" and abs(v) > 2:
                    raise ValueError("translation out of range")
                key["v"] *= width if prop == "x" else height
        tracks.append({"target": target, "property": prop, "keys": keys})
    repeats = sorted(result.repeats, key=lambda r: r.start)
    for i, r in enumerate(repeats):
        if (
            r.start >= r.end
            or r.end > result.duration
            or (i and r.start < repeats[i - 1].end)
            or r.count > limit("repetitions", 8)
        ):
            raise ValueError("invalid repeat")
        # Closed segments avoid teleporting a part at a repetition boundary.
        if any(abs(value(t, r.start) - value(t, r.end)) > 1e-6 for t in tracks):
            raise ValueError("repeat must return to its starting pose")
        # A repeat boundary inside an eased interval would alter its interpolation.
        if any(
            not all(any(k["t"] == b for k in t["keys"]) for b in (r.start, r.end))
            for t in tracks
        ):
            raise ValueError("repeat boundaries must be explicit keys on every track")
    shift = 0
    for r in repeats:
        start, end, span = r.start + shift, r.end + shift, r.end - r.start
        for track in tracks:
            before = [k for k in track["keys"] if k["t"] < start]
            segment = [k for k in track["keys"] if start <= k["t"] <= end]
            after = [
                {**k, "t": k["t"] + span * (r.count - 1)}
                for k in track["keys"]
                if k["t"] > end
            ]
            expanded = {}
            for n in range(r.count):
                for k in segment:
                    expanded[k["t"] + n * span] = {**k, "t": k["t"] + n * span}
            track["keys"] = (
                before + sorted(expanded.values(), key=lambda k: k["t"]) + after
            )
        shift += span * (r.count - 1)
    duration = result.duration + shift
    if duration > limit("duration_ms", 30000) or sum(
        len(t["keys"]) for t in tracks
    ) > limit("keyframes", 256):
        raise ValueError("performance too large")
    plan = {"version": 1, "duration": duration, "tracks": tracks}
    if len(json.dumps(plan, separators=(",", ":")).encode()) > limit(
        "plan_bytes", 65536
    ):
        raise ValueError("plan too large")
    times = sorted({0, duration, *(k["t"] for tr in tracks for k in tr["keys"])})
    for a, b in pairwise(times):
        root = _ranges(plan, "root", a, b)
        if math.hypot(max(map(abs, root["x"])), max(map(abs, root["y"]))) > limit(
            "root_displacement_px", 512
        ):
            raise ValueError("root displacement too large")
    if layers is not None:
        times = sorted({0, duration, *(k["t"] for tr in tracks for k in tr["keys"])})
        for a, b in pairwise(times):
            x0, y0, x1, y1 = visual_bounds(plan, layers, a, b)
            if (
                origin[0] + x0 < 0
                or origin[1] + y0 < 0
                or origin[0] + x1 > bounds[0]
                or origin[1] + y1 > bounds[1]
            ):
                raise ValueError("visual trajectory leaves the world")
    return plan


# Interval arithmetic includes every intermediate pose (not just sampled frames).
def _add(a, b):
    return a[0] + b[0], a[1] + b[1]


def _mul(a, b):
    values = [x * y for x in a for y in b]
    return min(values), max(values)


def _trig(lo, hi, cos=False):
    a, b = math.radians(lo), math.radians(hi)
    f = math.cos if cos else math.sin
    values = [f(a), f(b)]
    offset = 0 if cos else math.pi / 2
    values += [
        f(offset + k * math.pi)
        for k in range(
            math.ceil((a - offset) / math.pi), math.floor((b - offset) / math.pi) + 1
        )
    ]
    return min(values), max(values)


def _ranges(plan, target, start, end):
    out = {k: (v, v) for k, v in DEFAULT.items()}
    for t in plan["tracks"]:
        if t["target"] != target:
            continue
        vs = [value(t, start), value(t, end)] + [
            k["v"] for k in t["keys"] if start <= k["t"] <= end
        ]
        out[t["property"]] = min(vs), max(vs)
    return out


def _transform(x, y, pivot, p):
    x = _mul(_add(x, (-pivot[0], -pivot[0])), p["scaleX"])
    y = _mul(_add(y, (-pivot[1], -pivot[1])), p["scaleY"])
    c, s = _trig(*p["rotation"], cos=True), _trig(*p["rotation"])
    rx = _add(_mul(x, c), _mul(y, (-s[1], -s[0])))
    ry = _add(_mul(x, s), _mul(y, c))
    return _add(rx, _add(p["x"], (pivot[0], pivot[0]))), _add(
        ry, _add(p["y"], (pivot[1], pivot[1]))
    )


def visual_bounds(plan, layers, start, end):
    root, body = _ranges(plan, "root", start, end), _ranges(plan, "body", start, end)
    xs, ys = [], []
    for layer in layers:
        x, y = layer["offset"]
        w, h = layer["size"]
        px, py = (x, x + w), (y, y + h)
        if layer["id"] != "body":
            px, py = _transform(
                px, py, layer["pivot"], _ranges(plan, layer["id"], start, end)
            )
        px, py = _transform(px, py, (0, 0), body)
        xs.extend(_add(px, root["x"]))
        ys.extend(_add(py, root["y"]))
    return min(xs), min(ys), max(xs), max(ys)


def playback_time(plan: dict[str, Any], elapsed: float, pause_ms: int) -> float:
    """One performance, final-pose pause, then another play from the fixed origin."""
    return min(plan["duration"], max(0, elapsed) % (plan["duration"] + pause_ms))
