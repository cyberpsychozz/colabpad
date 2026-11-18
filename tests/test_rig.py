from typing import Any

import numpy as np
from PIL import Image

from app.alive.postprocess import Classified
from app.alive.rasterize import Cutout, rasterize
from app.alive.rig import COLUMNS, FRAMES, area_polygon, rig_sprite

BLACK = "#000000"


def stroke(points: list[tuple[float, float]], width: float = 6) -> dict[str, Any]:
    return {"color": BLACK, "width": width, "points": [[x, y, 0.5] for x, y in points]}


def classified(type_name: str = "creature", **changes: Any) -> Classified:
    base: dict[str, Any] = dict(
        type=type_name, label={"ru": "тест", "en": "test"}, size_class="M", locomotion="walk",
        diet=None, role=None, variant=None, facing="right",
        parts={"legs": [], "wings": [], "wheels": [], "head": None, "tail": None}, confidence=1.0,
    )  # fmt: skip
    return Classified(**{**base, **changes})


def table() -> Cutout:
    """A body bar with two legs: a recognisable shape whose legs sit in known boxes."""
    return rasterize([
        stroke([(0, 0), (200, 0)], 30),
        stroke([(30, 15), (30, 110)], 12),
        stroke([(170, 15), (170, 110)], 12),
    ])


LEGS = {"legs": [(0.08, 0.25, 0.2, 0.75), (0.72, 0.25, 0.2, 0.75)], "wings": [], "wheels": [], "head": None, "tail": None}


def frames(sheet: Image.Image, manifest: dict[str, Any], name: str) -> list[np.ndarray]:
    anim, w, h = manifest["anims"][name], manifest["frameW"], manifest["frameH"]
    return [
        np.asarray(sheet.crop((i * w, anim["row"] * h, (i + 1) * w, (anim["row"] + 1) * h))).astype(int)
        for i in range(anim["frames"])
    ]


def changed(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a - b).sum(axis=2) > 60).mean())


def test_sheet_layout_and_manifest_contract() -> None:
    sheet, manifest, shrink = rig_sprite(table(), classified(parts=LEGS))
    # 246 px of drawing plus padding is wider than a 256 px frame, so it is shrunk a little.
    assert 0.7 < shrink < 1.0
    assert set(manifest) == {"frameW", "frameH", "pivot", "facing", "anims"}
    assert manifest["anims"] == {
        "idle": {"row": 0, "frames": 4, "fps": 4},
        "move": {"row": 1, "frames": 8, "fps": 10},
        "act": {"row": 2, "frames": 4, "fps": 8},
        "death": {"row": 3, "frames": 6, "fps": 8},
    }
    assert sheet.size == (COLUMNS * manifest["frameW"], 4 * manifest["frameH"])
    # The frame is the drawing plus padding, and the pivot is the drawing's centre.
    cutout = table().image
    assert manifest["frameW"] == 256 and manifest["frameH"] > cutout.height * shrink
    assert abs(manifest["pivot"][0] - manifest["frameW"] / 2) <= 2
    for name, count in FRAMES.items():
        painted = [frame[:, :, 3].any() for frame in frames(sheet, manifest, name)]
        assert painted == [True] * count
    # Columns past an animation's last frame stay empty.
    idle_row = np.asarray(sheet.crop((4 * manifest["frameW"], 0, sheet.width, manifest["frameH"])))
    assert not idle_row.any()


def test_legs_swing_in_counter_phase_and_the_body_bobs() -> None:
    sheet, manifest, _ = rig_sprite(table(), classified(parts=LEGS))
    move = frames(sheet, manifest, "move")
    w, h = manifest["frameW"], manifest["frameH"]
    half = slice(h // 2, h)
    left, right = slice(0, w // 2), slice(w // 2, w)
    # Frame 2 is the far end of the swing, frame 6 the opposite end.
    assert changed(move[2][half, left], move[6][half, left]) > 0.02
    assert changed(move[2][half, right], move[6][half, right]) > 0.02
    # The legs lean opposite ways: the left leg's ink is further left when the right one is further right.
    centre = lambda frame, cols: np.average(np.arange(w)[cols], weights=frame[half, cols, 3].sum(axis=0) + 1e-9)  # noqa: E731
    assert (centre(move[2], left) - centre(move[6], left)) * (centre(move[2], right) - centre(move[6], right)) < 0
    # Idle only breathes: far less changes than in a step.
    idle = frames(sheet, manifest, "idle")
    assert 0 < changed(idle[0], idle[2]) < changed(move[2], move[6])


def test_no_parts_hops_wheels_turn_and_swimmers_wave() -> None:
    blob = rasterize([stroke([(0, 0), (80, 0), (80, 60), (0, 60), (0, 0)], 10)])
    sheet, manifest, _ = rig_sprite(blob, classified("doodle", locomotion="hop"))
    move = frames(sheet, manifest, "move")
    top = lambda frame: int(np.nonzero(frame[:, :, 3].any(axis=1))[0][0])  # noqa: E731
    assert top(move[2]) < top(move[0]) <= top(move[6])  # up at the peak of the hop

    car = rasterize([stroke([(0, 0), (160, 0)], 30), stroke([(30, 30), (50, 50), (30, 50), (50, 30)], 6), stroke([(120, 30), (140, 50)], 6)])
    wheels = {**LEGS, "legs": [], "wheels": [(0.1, 0.45, 0.25, 0.55), (0.62, 0.45, 0.25, 0.55)]}
    sheet, manifest, _ = rig_sprite(car, classified("vehicle", locomotion="none", parts=wheels))
    move = frames(sheet, manifest, "move")
    h = manifest["frameH"]
    assert changed(move[0][h // 2 :], move[1][h // 2 :]) > 0.005  # wheels turned 45 degrees
    assert changed(move[0][: h // 3], move[1][: h // 3]) == 0  # the body did not

    fish = rasterize([stroke([(0, 30), (60, 0), (120, 30), (60, 60), (0, 30)], 8)])
    sheet, manifest, _ = rig_sprite(fish, classified("aquatic", locomotion="swim"))
    move = frames(sheet, manifest, "move")
    assert changed(move[0], move[4]) > 0.01


def test_plants_sway_more_at_the_top_and_death_ends_as_a_grey_fossil() -> None:
    tree = rasterize([stroke([(50, 0), (50, 200)], 14), stroke([(10, 20), (90, 20)], 14)])
    sheet, manifest, _ = rig_sprite(tree, classified("plant", locomotion="none"))
    idle = frames(sheet, manifest, "idle")
    h = manifest["frameH"]
    assert changed(idle[1][: h // 3], idle[3][: h // 3]) > changed(idle[1][2 * h // 3 :], idle[3][2 * h // 3 :])

    red = rasterize([{"color": "#E53935", "width": 20, "points": [[0, 0, 0.5], [100, 40, 0.5]]}])
    sheet, manifest, _ = rig_sprite(red, classified())
    death = frames(sheet, manifest, "death")
    first, last = death[0], death[-1]
    solid = last[:, :, 3] > 200
    assert solid.any()
    assert np.abs(last[solid][:, 0] - last[solid][:, 1]).max() <= 2  # no colour left
    assert abs(int(last[solid][:, 0].mean()) - 150) <= 3
    assert (first[first[:, :, 3] > 200][:, 0] - first[first[:, :, 3] > 200][:, 1]).mean() > 100

    bomb = rasterize([stroke([(0, 0), (40, 0), (40, 40), (0, 40), (0, 0)], 10)])
    sheet, manifest, _ = rig_sprite(bomb, classified("explosive", locomotion="none"))
    act = frames(sheet, manifest, "act")
    ink = lambda frame: frame[frame[:, :, 3] > 200]  # noqa: E731
    assert ink(act[1])[:, 0].mean() > ink(act[0])[:, 0].mean() + 50  # red pulse


def test_big_drawings_are_shrunk_to_fit_the_sheet_and_areas_are_not_rigged() -> None:
    big = rasterize([stroke([(0, 0), (900, 0), (900, 300), (0, 300), (0, 0)], 20)])
    sheet, manifest, shrink = rig_sprite(big, classified())
    assert manifest["frameW"] <= 256 and manifest["frameH"] <= 384 and shrink < 0.5
    assert sheet.width <= 2048 and sheet.height <= 2048

    lake_strokes = [stroke([(100 + 80 * np.cos(a), 60 + 40 * np.sin(a)) for a in np.linspace(0, 6.3, 40)], 6)]
    lake = rasterize(lake_strokes)
    sheet, manifest, shrink = rig_sprite(lake, classified("water", locomotion="none"))
    assert sheet.size == lake.image.size and shrink == 1.0
    assert all(anim["frames"] == 1 for anim in manifest["anims"].values())
    polygon = area_polygon(lake.image)
    assert 30 <= len(polygon) <= 64
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    # The outline spans the whole loop, so its inside is the lake.
    assert max(xs) - min(xs) > 150 and max(ys) - min(ys) > 70


# ---- parts split from the user's strokes (app/alive/parts.py)

from app.alive.rig import build_rig, compose  # noqa: E402


def cat() -> Cutout:
    """A body outline whose belly line crosses both leg boxes, legs drawn as their own
    strokes hanging from it, and a tail."""
    return rasterize([
        stroke([(0, 0), (160, 0), (160, 50), (0, 50), (0, 0)], 6),   # body outline
        stroke([(25, 50), (20, 110)], 6),                            # front leg
        stroke([(135, 50), (140, 110)], 6),                          # back leg
        stroke([(160, 10), (200, -20)], 6),                          # tail
    ])


# Boxes as a VLM draws them: the leg boxes start above the belly and stop short of the feet.
CAT_PARTS = {"legs": [(0.05, 0.35, 0.2, 0.3), (0.62, 0.35, 0.2, 0.3)], "wings": [], "wheels": [],
             "head": None, "tail": (0.78, 0.0, 0.22, 0.25)}  # fmt: skip


def alpha(image: Image.Image) -> np.ndarray:
    return np.asarray(image)[:, :, 3].astype(int)


def test_a_rig_split_from_strokes_redraws_the_drawing_exactly_and_never_twice() -> None:
    cutout = cat()
    rig = build_rig(cutout, CAT_PARTS)
    assert sorted(part.kind for part in rig.parts) == ["legs", "legs", "tail"]
    # At rest, body plus parts is the drawing itself: nothing torn off, nothing lost.
    whole = alpha(compose(rig))
    plain = Image.new("RGBA", (rig.width, rig.height))
    pad = (rig.width - cutout.image.width) // 2
    plain.paste(cutout.image, (pad, pad))
    difference = np.abs(whole - alpha(plain))
    # Only antialiased edge pixels where two pieces' round caps meet differ, and barely.
    assert difference.max() <= 64 and (difference > 40).sum() < 0.01 * (alpha(plain) > 100).sum()
    # No line is in two layers, apart from the round cap where a part joins the body.
    layers = [rig.body] + [part.layer for part in rig.parts]
    for i, a in enumerate(layers):
        for b in layers[i + 1:]:
            assert ((alpha(a) > 100) & (alpha(b) > 100)).sum() < 60
    # The belly and the outline stay with the body: a leg has nothing above the belly.
    belly_row = round(pad + (50 - cutout.y) * cutout.scale)
    for leg in (p for p in rig.parts if p.kind == "legs"):
        rows = np.nonzero((alpha(leg.layer) > 100).any(axis=1))[0]
        assert rows[0] >= belly_row - 4
        # It reaches the ground (the box stopped short) and turns where it meets the belly.
        assert rows[-1] >= rig.bottom - 4
        assert abs(leg.pivot[1] - belly_row) <= 3
    left, right = sorted((p for p in rig.parts if p.kind == "legs"), key=lambda p: p.pivot[0])
    assert (left.index, right.index) == (0, 1)


def test_wheels_spin_only_their_own_strokes() -> None:
    # A body line runs straight through both wheel boxes; it must not turn with them.
    car = rasterize([
        stroke([(0, 30), (200, 30)], 6),
        stroke([(40 + 15 * np.cos(a), 45 + 15 * np.sin(a)) for a in np.linspace(0, 6.3, 24)] + [(40, 45)], 5),
        stroke([(160 + 15 * np.cos(a), 45 + 15 * np.sin(a)) for a in np.linspace(0, 6.3, 24)] + [(160, 45)], 5),
    ])
    box = lambda cx: ((cx - 18 - car.x) * car.scale / car.image.width, (27 - car.y) * car.scale / car.image.height,  # noqa: E731
                      36 * car.scale / car.image.width, 36 * car.scale / car.image.height)
    rig = build_rig(car, {"legs": [], "wings": [], "wheels": [box(40), box(160)], "head": None, "tail": None})
    wheels = [part for part in rig.parts if part.kind == "wheels"]
    assert len(wheels) == 2
    body = alpha(rig.body)
    line_row = round(rig.top + (30 - (car.y + 8 / car.scale)) * car.scale) + 8
    assert (body.max(axis=0) > 100).sum() >= 190 * car.scale  # the full line is body
    for wheel in wheels:
        # Spinning about its own centre: the drawn circle's middle.
        ys, xs = np.nonzero(alpha(wheel.layer) > 100)
        assert abs(wheel.pivot[0] - (xs.min() + xs.max()) / 2) <= 2
        assert abs(wheel.pivot[1] - (ys.min() + ys.max()) / 2) <= 2


def test_a_pair_of_wings_lifts_together_and_a_line_through_a_wing_box_stays_body() -> None:
    # A gull: one stroke, two wings meeting in the middle.
    gull = rasterize([stroke([(0, 20), (40, 0), (80, 30), (120, 0), (160, 20)], 6)])
    wings = {"legs": [], "wings": [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0)], "wheels": [], "head": None, "tail": None}
    sheet, manifest, _ = rig_sprite(gull, classified(locomotion="fly", parts=wings))
    move = frames(sheet, manifest, "move")
    def tips(frame: np.ndarray) -> tuple[float, float]:
        """Height of the ink in the leftmost and rightmost painted columns."""
        columns = np.nonzero(frame[:, :, 3].any(axis=0))[0]
        rows = lambda column: np.nonzero(frame[:, column, 3])[0].mean()  # noqa: E731
        return rows(columns[0]), rows(columns[-1])

    up, down = tips(move[0]), tips(move[4])
    assert up[0] < down[0] - 5 and up[1] < down[1] - 5  # both tips higher in frame 0 than in frame 4

    # A body outline passing through a wing box (and ending outside it) is not a wing.
    bird = rasterize([stroke([(0, 0), (100, 0), (100, 60), (0, 60), (0, 0)], 6), stroke([(30, 20), (60, 35)], 6)])
    wing_box = (0.2, 0.2, 0.45, 0.45)
    rig = build_rig(bird, {"legs": [], "wings": [wing_box], "wheels": [], "head": None, "tail": None})
    (wing,) = rig.parts
    ys, xs = np.nonzero(alpha(wing.layer) > 100)
    assert xs.max() - xs.min() < 45 * bird.scale  # only the short wing stroke
