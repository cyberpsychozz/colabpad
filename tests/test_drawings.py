"""The T7 drawing set itself (scripts/drawings.py); the VLM run is scripts/vlm_accuracy.py."""

from app.alive.rasterize import rasterize
from app.alive.vlm import TYPES
from scripts.drawings import DRAWINGS


def test_the_set_has_sixty_plus_drawings_covering_every_type_and_both_languages() -> None:
    assert len(DRAWINGS) >= 60
    assert {d.expected for d in DRAWINGS} == set(TYPES)
    assert len({d.id for d in DRAWINGS}) == len(DRAWINGS)
    hints = [d.hint for d in DRAWINGS if d.hint]
    assert any(any("а" <= ch <= "я" for ch in h) for h in hints) and any(h.isascii() for h in hints)
    for drawing in DRAWINGS:
        assert set(drawing.also) <= set(TYPES)
        assert rasterize(drawing.strokes).image.size[0] > 20  # it draws
