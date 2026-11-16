"""ALIVE step 2: which strokes does a lasso select?

A stroke is selected when at least `inside_ratio` of its points lie inside the
lasso polygon (even-odd rule). Only static strokes (no fate) can be selected,
and a stroke can be under one ALIVE job at a time.
"""

from typing import Any

from app.errors import AppError
from app.repo import Repository

Polygon = list[tuple[float, float]]


def inside(x: float, y: float, polygon: Polygon) -> bool:
    """Even-odd point-in-polygon test."""
    result = False
    px, py = polygon[-1]
    for qx, qy in polygon:
        if (qy > y) != (py > y) and x < (px - qx) * (y - qy) / (py - qy) + qx:
            result = not result
        px, py = qx, qy
    return result


def bbox(polygon: Polygon) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def select_strokes(
    repo: Repository,
    canvas: dict[str, Any],
    polygon: Polygon,
    limits: dict[str, Any],
    chunk_size: int,
) -> list[dict[str, Any]]:
    """The static strokes a lasso selects, in seq order, or an AppError."""
    alive = limits["alive"]
    if not alive["lasso_points"]["min"] <= len(polygon) <= alive["lasso_points"]["max"]:
        raise AppError("VALIDATION_FAILED", "lasso")
    x0, y0, x1, y1 = bbox(polygon)
    if x0 < 0 or y0 < 0 or x1 > canvas["bounds_w"] or y1 > canvas["bounds_h"]:
        raise AppError("VALIDATION_FAILED", "lasso")
    if x1 - x0 > alive["bbox_max"] or y1 - y0 > alive["bbox_max"]:
        raise AppError("SELECTION_TOO_LARGE")

    selected: list[dict[str, Any]] = []
    already_alive = False
    columns = range(int(x0 // chunk_size), int(x1 // chunk_size) + 1)
    rows = range(int(y0 // chunk_size), int(y1 // chunk_size) + 1)
    for cy in rows:
        for cx in columns:
            fates = {f["seq"]: f["fate"] for f in repo.chunk_fates(canvas["id"], cx, cy)}
            for stroke in repo.chunk_strokes(canvas["id"], cx, cy):
                points = stroke["points"]
                hits = sum(inside(x, y, polygon) for x, y, _ in points)
                if hits < alive["inside_ratio"] * len(points):
                    continue
                fate = fates.get(stroke["seq"])
                if fate is None:
                    selected.append(stroke)
                elif fate == "consumed":
                    already_alive = True
    if not selected:
        raise AppError("STROKES_ALREADY_ALIVE" if already_alive else "SELECTION_EMPTY")
    if len(selected) > alive["strokes"]["max"]:
        raise AppError("SELECTION_TOO_LARGE")
    selected.sort(key=lambda stroke: stroke["seq"])
    return selected


class SelectionLocks:
    """In-memory locks keyed by (canvas, seq): one ALIVE job per stroke at a time."""

    def __init__(self) -> None:
        self.owner: dict[tuple[str, int], str] = {}

    def acquire(self, canvas_id: str, seqs: list[int], job_id: str) -> None:
        if any((canvas_id, seq) in self.owner for seq in seqs):
            raise AppError("SELECTION_BUSY")
        for seq in seqs:
            self.owner[(canvas_id, seq)] = job_id

    def release(self, job_id: str) -> None:
        for key in [key for key, owner in self.owner.items() if owner == job_id]:
            del self.owner[key]
