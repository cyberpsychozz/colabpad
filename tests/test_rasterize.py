import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image

from app.alive.rasterize import rasterize, render

GOLDEN = Path(__file__).parent / "golden"
# strokes.browser.png is renderStroke from web/src/draw.js painted by Chromium onto
# a transparent 320x240 canvas with the strokes in strokes.json.
DATA = json.loads((GOLDEN / "strokes.json").read_text())


def difference(strokes: list[dict]) -> tuple[float, float, float]:
    browser = np.asarray(Image.open(GOLDEN / "strokes.browser.png").convert("RGBA")).astype(int)
    ours = render(strokes, 0, 0, DATA["width"], DATA["height"]).astype(int)
    alpha = np.abs(browser[:, :, 3] - ours[:, :, 3])
    a, b = browser[:, :, 3] > 127, ours[:, :, 3] > 127
    return alpha.mean(), (alpha > 32).mean(), (a & b).sum() / (a | b).sum()


def test_python_port_matches_the_browser_render_within_tolerance() -> None:
    mean, edge_fraction, overlap = difference(DATA["strokes"])
    # Only antialiased edge pixels may differ (Chromium's edges are a hair heavier).
    assert mean < 1.5 and edge_fraction < 0.015 and overlap > 0.965
    browser = np.asarray(Image.open(GOLDEN / "strokes.browser.png").convert("RGBA")).astype(int)
    ours = render(DATA["strokes"], 0, 0, DATA["width"], DATA["height"]).astype(int)
    solid = (browser[:, :, 3] == 255) & (ours[:, :, 3] == 255)
    assert np.abs(browser[:, :, :3] - ours[:, :, :3])[solid].mean() < 1


def test_the_tolerance_catches_a_wrong_port() -> None:
    # Ignoring pressure, or widths only 5% off, must not pass.
    flat = copy.deepcopy(DATA["strokes"])
    for stroke in flat:
        stroke["points"] = [[x, y, 0.5] for x, y, _ in stroke["points"]]
    assert difference(flat)[0] > 2
    wide = copy.deepcopy(DATA["strokes"])
    for stroke in wide:
        stroke["width"] *= 1.05
    assert difference(wide)[0] > 2


def test_rasterize_trims_pads_and_limits_the_long_side() -> None:
    small = [{"color": "#000000", "width": 4, "points": [[1000, 2000, 0.5], [1040, 2020, 0.5]]}]
    cut = rasterize(small)
    alpha = np.asarray(cut.image)[:, :, 3]
    assert cut.scale == 1.0 and cut.image.mode == "RGBA"
    # Exactly 8 transparent pixels on every side of the painted area.
    rows, cols = np.where(alpha.any(axis=1))[0], np.where(alpha.any(axis=0))[0]
    assert (rows[0], cols[0]) == (8, 8)
    assert (alpha.shape[0] - 1 - rows[-1], alpha.shape[1] - 1 - cols[-1]) == (8, 8)
    assert abs(cut.image.width - (40 + 4 + 16)) <= 2 and abs(cut.image.height - (20 + 4 + 16)) <= 2
    # Pixel (0, 0) maps back to the world: the line starts about a brush radius in.
    assert abs(cut.x + 8 + 2 - 1000) <= 1.5 and abs(cut.y + 8 + 2 - 2000) <= 1.5
    assert cut.on_white().mode == "RGB" and cut.on_white().getpixel((0, 0)) == (255, 255, 255)

    big = [{"color": "#E53935", "width": 10, "points": [[0, 0, 0.5], [1000, 600, 0.5]]}]
    cut = rasterize(big)
    assert max(cut.image.size) <= 512 and max(cut.image.size) >= 500
    assert 0.45 < cut.scale < 0.5
    custom = rasterize(big, long_side=128, pad=4)
    assert max(custom.image.size) <= 128
