"""Strict wire models; all public properties use camelCase."""

import re
from typing import Annotated
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

CANVAS_ID = r"^[A-Za-z0-9_-]{10}$"
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Point = tuple[Number, Number, Number]


def check_id(canvas_id: str) -> str:
    if not re.fullmatch(CANVAS_ID, canvas_id):
        raise AppError("INVALID_ID", "Некорректная ссылка на холст.", "canvasId")
    return canvas_id


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


class JoinIn(WireModel):
    canvas_id: str = Field(pattern=CANVAS_ID)
    name: str | None = None

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None, info: ValidationInfo) -> str | None:
        limit = (info.context or {}).get("settings", Settings()).name_max_len
        return (value.strip()[:limit] or None) if value else None


class StrokeIn(WireModel):
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
        width = context.get("width", settings.canvas_width)
        height = context.get("height", settings.canvas_height)
        if len(points) > settings.max_points_per_stroke:
            raise ValueError("Too many points")
        if any(
            not (0 <= x <= width and 0 <= y <= height and 0 <= p <= 1)
            for x, y, p in points
        ):
            raise ValueError("Point out of bounds")
        return points


class StrokeOut(WireModel):
    seq: int
    author_name: str | None
    color: str
    width: float
    points: list[Point]
    created_at: int


class CanvasCreated(WireModel):
    id: str
    url: str
    width: int
    height: int


class CanvasOut(WireModel):
    id: str
    width: int
    height: int
    background: str
    stroke_count: int
    max_strokes: int
    created_at: int
    palette: list[str]
    brush_width_min: float
    brush_width_max: float
    max_points_per_stroke: int
    max_payload_bytes: int
    simplify_epsilon: float
    name_max_len: int
    strokes: list[StrokeOut]


class StrokesOut(WireModel):
    strokes: list[StrokeOut]


class ErrorDetail(WireModel):
    code: str
    message: str
    field: str | None = None


class ErrorOut(WireModel):
    ok: bool = False
    error: ErrorDetail
