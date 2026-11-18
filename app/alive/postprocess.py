"""ALIVE step 6: turn the VLM's answer into a decision the pipeline can act on."""

from dataclasses import dataclass, field
from typing import Any

from app.alive.vlm import TYPES, Box, VlmResult
from app.errors import AppError

MIN_PART_AREA = 0.02


@dataclass
class Classified:
    type: str
    label: dict[str, str]
    size_class: str
    locomotion: str
    diet: str | None
    role: str | None
    variant: str | None
    facing: str
    parts: dict[str, Any]
    confidence: float
    # Non-empty when the author must choose: the three most likely types.
    choices: list[str] = field(default_factory=list)


def clamp_box(box: Box) -> Box | None:
    """Clamp to the unit square; None if under 2% of the image is left."""
    x, y, w, h = box
    x0, y0 = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    x1, y1 = min(max(x + w, 0.0), 1.0), min(max(y + h, 0.0), 1.0)
    if (x1 - x0) * (y1 - y0) < MIN_PART_AREA:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def size_class(long_side: float, thresholds: dict[str, float]) -> str:
    if long_side < thresholds["S"]:
        return "S"
    return "M" if long_side < thresholds["M"] else "L"


def top_choices(result: VlmResult) -> list[str]:
    """The chosen type first, then the model's other guesses, padded to three."""
    ordered = [result.type] + [
        guess.type for guess in sorted(result.top3, key=lambda guess: -guess.p)
    ]
    ordered += ["doodle", "creature", "food"]
    unique = list(dict.fromkeys(ordered))
    assert all(name in TYPES for name in unique)
    return unique[:3]


def postprocess(
    result: VlmResult,
    labels: dict[str, dict[str, str]],
    long_side: float,
    size_thresholds: dict[str, float],
    confidence_min: float,
) -> Classified:
    """long_side is the drawing's bbox long side in world px."""
    if result.unsafe:
        raise AppError("UNSAFE_CONTENT")
    if result.multiple:
        raise AppError("SELECTION_MULTIPLE")
    label = {"ru": result.label.ru.strip().lower(), "en": result.label.en.strip().lower()}
    fields = result.model_dump(include={"type", "locomotion", "diet", "role", "variant"})
    # A known label, in either language, decides the type and may fix other fields.
    override = labels.get(label["ru"]) or labels.get(label["en"])
    overridden = bool(override) and override["type"] != result.type
    if override:
        fields.update(override)
    parts = result.parts
    boxes = lambda items: [box for box in map(clamp_box, items) if box]  # noqa: E731
    classified = Classified(
        **fields,
        label=label,
        size_class=size_class(long_side, size_thresholds),
        facing=result.facing,
        parts={
            "legs": boxes(parts.legs),
            "wings": boxes(parts.wings),
            "wheels": boxes(parts.wheels),
            "head": clamp_box(parts.head) if parts.head else None,
            "tail": clamp_box(parts.tail) if parts.tail else None,
        },
        confidence=result.confidence,
    )
    # A label override is a rule, not a guess, so it never asks the author.
    if result.confidence < confidence_min and not overridden:
        classified.choices = top_choices(result)
    return classified
