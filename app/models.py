"""Strict wire models; all public properties use camelCase."""

import math
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)
from pydantic.alias_generators import to_camel

from app.config import Settings
from app.errors import AppError

# The shared world has a fixed id; private worlds use 10-character nanoid slugs.
CANVAS_ID = r"^(world|[A-Za-z0-9_-]{10})$"
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Point = tuple[Number, Number, Number]


def check_id(canvas_id: str) -> str:
    if not re.fullmatch(CANVAS_ID, canvas_id):
        raise AppError("VALIDATION_FAILED", "canvasId")
    return canvas_id


def chunk_of(
    points: list[Point], size: int, bounds: tuple[int, int]
) -> tuple[int, int] | None:
    """The one chunk that contains every point (borders included), or None."""
    result = []
    for axis, limit in ((0, bounds[0]), (1, bounds[1])):
        low = min(point[axis] for point in points)
        high = max(point[axis] for point in points)
        index = min(max(math.floor((low + high) / 2 / size), 0), limit // size - 1)
        if low < index * size or high > (index + 1) * size:
            return None
        result.append(index)
    return result[0], result[1]


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


class ViewportIn(WireModel):
    """Validation context: max_chunks."""

    canvas_id: str = Field(pattern=CANVAS_ID)
    chunks: list[tuple[int, int]]

    @field_validator("chunks")
    @classmethod
    def chunk_list(
        cls, chunks: list[tuple[int, int]], info: ValidationInfo
    ) -> list[tuple[int, int]]:
        if len(chunks) > (info.context or {}).get("max_chunks", 12):
            raise ValueError("Too many chunks")
        if any(cx < 0 or cy < 0 for cx, cy in chunks):
            raise ValueError("Negative chunk index")
        return chunks


class StrokeIn(WireModel):
    """Validation context: settings, bounds (w, h) and chunk (size) of the canvas."""

    canvas_id: str = Field(pattern=CANVAS_ID)
    client_id: str
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    width: Number
    points: list[Point] = Field(min_length=2)

    @field_validator("client_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("width")
    @classmethod
    def width_bounds(cls, value: float, info: ValidationInfo) -> float:
        settings: Settings = (info.context or {}).get("settings") or Settings()
        if not settings.brush_width_min <= value <= settings.brush_width_max:
            raise ValueError("Brush width out of range")
        return value

    @field_validator("points")
    @classmethod
    def point_bounds(cls, points: list[Point], info: ValidationInfo) -> list[Point]:
        context = info.context or {}
        settings: Settings = context.get("settings") or Settings()
        width, height = context["bounds"]
        if len(points) > settings.max_points_per_stroke:
            raise ValueError("Too many points")
        if any(
            not (0 <= x <= width and 0 <= y <= height and 0 <= p <= 1)
            for x, y, p in points
        ):
            raise ValueError("Point out of bounds")
        if chunk_of(points, context["chunk"], (width, height)) is None:
            raise ValueError("Stroke crosses a chunk border")
        return points


class AliveIn(WireModel):
    lasso: list[tuple[Number, Number]] = Field(max_length=2000)


class ChoiceIn(WireModel):
    type: str = Field(max_length=20)


class InteractIn(WireModel):
    entity_id: str = Field(max_length=64)
    action: Literal["pet", "push", "drag"]
    to: tuple[Number, Number] | None = None


class ClaimIn(WireModel):
    controller_id: str = Field(max_length=64)


class InputIn(WireModel):
    ax: float = Field(ge=-1, le=1, allow_inf_nan=False)
    ay: float = Field(ge=-1, le=1, allow_inf_nan=False)


class StrokeOut(WireModel):
    seq: int
    color: str
    width: float
    points: list[Point]
    cx: int
    cy: int
    created_at: int


class FateOut(WireModel):
    seq: int
    fate: str
    entity_id: str | None


class ChunkOut(WireModel):
    strokes: list[StrokeOut]
    fates: list[FateOut]
    entities: list[dict[str, Any]]
    fossils: list[dict[str, Any]] = []
    last_seq: int
    tick: int


class BoundsOut(WireModel):
    w: int
    h: int


class SpawnOut(WireModel):
    x: float
    y: float


class LimitsOut(WireModel):
    brush_width_min: float
    brush_width_max: float
    max_points_per_stroke: int
    max_payload_bytes: int
    simplify_epsilon: float
    chunk_size: int
    max_chunks: int


class CanvasOut(WireModel):
    id: str
    kind: str
    title: str | None
    role: str
    frozen: bool
    bounds: BoundsOut
    spawn: SpawnOut
    limits: LimitsOut
    palette: list[str]


class ErrorDetail(WireModel):
    code: str
    message: str
    field: str | None = None


class ErrorOut(WireModel):
    ok: bool = False
    error: ErrorDetail
