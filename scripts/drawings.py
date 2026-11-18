"""The T7 test set: drawings like players make, as strokes, with the type each should get.

Shapes are built from wobbly lines and loops (a seeded random hand, so the set is the
same every run); words are the outlines of real glyphs, so the Russian and English
handwriting hints read like text a player wrote. Used by scripts/vlm_accuracy.py.

    DRAWINGS: list[Drawing]   # id, expected type, accepted alternatives, strokes
"""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import skia

Stroke = dict[str, Any]
Point = tuple[float, float]

BLACK, RED, ORANGE, YELLOW, GREEN, BLUE, PURPLE, GREY = (
    "#000000", "#E53935", "#FB8C00", "#FDD835", "#43A047", "#1E88E5", "#8E24AA", "#000000",
)


class Hand:
    """Draws like a person with a mouse: slightly wobbly, never quite closing a loop."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.strokes: list[Stroke] = []

    def _jitter(self, points: list[Point], amount: float) -> list[list[float]]:
        drift_x = drift_y = 0.0
        out = []
        for x, y in points:
            drift_x = drift_x * 0.7 + self.rng.uniform(-amount, amount)
            drift_y = drift_y * 0.7 + self.rng.uniform(-amount, amount)
            out.append([round(x + drift_x, 1), round(y + drift_y, 1), round(self.rng.uniform(0.4, 0.6), 2)])
        return out

    def path(self, points: list[Point], color: str = BLACK, width: float = 5, wobble: float = 1.2) -> None:
        dense: list[Point] = [points[0]]
        for a, b in zip(points, points[1:]):
            steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 8))
            dense += [(a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps) for k in range(1, steps + 1)]
        self.strokes.append({"color": color, "width": width, "points": self._jitter(dense, wobble)})

    def line(self, a: Point, b: Point, color: str = BLACK, width: float = 5) -> None:
        self.path([a, b], color, width)

    def loop(self, cx: float, cy: float, rx: float, ry: float, color: str = BLACK, width: float = 5,
             start: float | None = None, sweep: float = 2.0) -> None:  # fmt: skip
        """An ellipse (sweep in turns of pi: 2 is a full loop, slightly overshot)."""
        start = self.rng.uniform(0, 2 * math.pi) if start is None else start
        n = max(12, int((rx + ry) * sweep / 4))
        end = sweep * math.pi * (1.04 if sweep >= 2 else 1)
        self.path([(cx + rx * math.cos(start + end * k / n), cy + ry * math.sin(start + end * k / n))
                   for k in range(n + 1)], color, width)  # fmt: skip

    def dot(self, x: float, y: float, color: str = BLACK, width: float = 7) -> None:
        self.strokes.append({"color": color, "width": width, "points": [[x, y, 0.5]]})

    def zigzag(self, points: list[Point], color: str = BLACK, width: float = 5) -> None:
        self.path(points, color, width, wobble=0.6)

    def text(self, word: str, x: float, y: float, size: float = 34, color: str = BLACK) -> None:
        """Handwriting: each glyph contour becomes a stroke."""
        font = skia.Font(skia.Typeface("Helvetica"), size)
        glyphs = font.textToGlyphs(word)
        positions = font.getXPos(glyphs)
        for glyph, offset in zip(glyphs, positions):
            measure = skia.PathMeasure(font.getPath(glyph), False)
            while True:
                length = measure.getLength()
                if length > 0:
                    steps = max(2, int(length / 3))
                    pts = []
                    for k in range(steps + 1):
                        position, _ = measure.getPosTan(length * k / steps)
                        pts.append((x + offset + position.x(), y + position.y()))
                    self.strokes.append({"color": color, "width": 2.5, "points": self._jitter(pts, 0.3)})
                if not measure.nextContour():
                    break


@dataclass
class Drawing:
    id: str
    expected: str
    strokes: list[Stroke]
    also: tuple[str, ...] = ()  # other types that are a fair reading
    hint: str = ""              # the handwritten word, if any
    meta: dict[str, Any] = field(default_factory=dict)


Builder = Callable[[Hand], None]
_BUILDERS: list[tuple[str, str, Builder, tuple[str, ...], str]] = []


def drawing(name: str, expected: str, also: tuple[str, ...] = (), hint: str = "") -> Callable[[Builder], Builder]:
    def register(build: Builder) -> Builder:
        _BUILDERS.append((name, expected, build, also, hint))
        return build

    return register


# ---- shared pieces

def legs4(h: Hand, x0: float, x1: float, top: float, bottom: float, color: str = BLACK) -> None:
    for x in (x0, x0 + (x1 - x0) * 0.3, x0 + (x1 - x0) * 0.7, x1):
        h.line((x, top), (x + h.rng.uniform(-6, 6), bottom), color)


def eye(h: Hand, x: float, y: float) -> None:
    h.dot(x, y, BLACK, 8)


# ---- creature

@drawing("cow", "creature")
def cow(h: Hand) -> None:
    h.loop(150, 120, 90, 50)
    legs4(h, 85, 215, 160, 230)
    h.loop(255, 95, 32, 26)
    h.line((240, 72), (232, 50)); h.line((268, 72), (278, 50))
    eye(h, 262, 92)
    h.loop(130, 110, 18, 12, BLACK, 8); h.loop(180, 135, 14, 10, BLACK, 8)
    h.path([(60, 110), (40, 140), (45, 160)])


@drawing("dog", "creature", also=("predator",))
def dog(h: Hand) -> None:
    h.loop(140, 130, 75, 38, ORANGE)
    legs4(h, 85, 195, 160, 215, ORANGE)
    h.loop(225, 95, 30, 26, ORANGE)
    h.path([(205, 75), (195, 110)], ORANGE); h.path([(245, 75), (258, 108)], ORANGE)
    eye(h, 232, 92); h.dot(252, 102, BLACK, 9)
    h.path([(65, 120), (40, 95), (35, 80)], ORANGE)


@drawing("cat", "creature")
def cat(h: Hand) -> None:
    h.loop(150, 140, 70, 35, GREY)
    legs4(h, 100, 200, 168, 215, GREY)
    h.loop(220, 105, 28, 25, GREY)
    h.zigzag([(198, 88), (204, 62), (215, 82), (228, 82), (240, 60), (244, 88)], GREY)
    eye(h, 212, 102); eye(h, 230, 102)
    h.line((222, 115), (250, 110)); h.line((222, 115), (250, 122))
    h.path([(80, 140), (55, 110), (60, 75), (75, 70)], GREY)


@drawing("bird", "creature")
def bird(h: Hand) -> None:
    h.loop(150, 140, 55, 40, BLUE)
    h.loop(205, 100, 25, 23, BLUE)
    h.zigzag([(228, 98), (255, 105), (228, 110)], ORANGE)
    eye(h, 210, 95)
    h.path([(130, 125), (95, 90), (160, 115)], BLUE)
    h.line((140, 178), (135, 210), ORANGE); h.line((165, 178), (170, 210), ORANGE)
    h.zigzag([(98, 140), (75, 130), (80, 150), (98, 148)], BLUE)


@drawing("bee", "creature")
def bee(h: Hand) -> None:
    h.loop(150, 150, 60, 38, YELLOW, 8)
    for x in (125, 150, 175):
        h.path([(x, 115), (x + 4, 185)], BLACK, 8)
    h.loop(130, 95, 25, 32, BLUE, 3); h.loop(170, 95, 25, 32, BLUE, 3)
    h.loop(215, 145, 18, 18)
    eye(h, 220, 140)
    h.zigzag([(90, 150), (72, 150)], BLACK)


@drawing("sheep", "creature")
def sheep(h: Hand) -> None:
    for k in range(9):
        a = k / 9 * 2 * math.pi
        h.loop(150 + 70 * math.cos(a), 130 + 40 * math.sin(a), 22, 20, BLACK, 4, start=a - 1.5, sweep=1.3)
    h.loop(235, 115, 20, 28, BLACK, 5)
    eye(h, 238, 108)
    legs4(h, 100, 200, 165, 215)


@drawing("pig", "creature")
def pig(h: Hand) -> None:
    h.loop(150, 140, 80, 50, "#F48FB1", 6)
    h.loop(230, 135, 18, 14, "#F48FB1", 6)
    h.dot(226, 135, BLACK, 5); h.dot(234, 135, BLACK, 5)
    eye(h, 205, 115)
    legs4(h, 95, 205, 182, 220, "#F48FB1")
    h.path([(70, 130), (55, 120), (60, 108), (50, 100)], "#F48FB1", 4)


# ---- predator

@drawing("wolf", "predator", also=("creature",))
def wolf(h: Hand) -> None:
    h.loop(140, 140, 80, 35, GREY)
    legs4(h, 80, 200, 168, 220, GREY)
    h.path([(200, 120), (215, 80), (230, 100), (245, 78), (255, 110), (300, 125), (255, 140), (210, 140)], GREY)
    eye(h, 240, 108)
    h.zigzag([(262, 130), (268, 140), (274, 130), (280, 140), (286, 130)], RED)
    h.path([(62, 130), (25, 110), (20, 95)], GREY)


@drawing("spider", "predator")
def spider(h: Hand) -> None:
    h.loop(150, 150, 40, 32)
    h.loop(150, 105, 22, 18)
    eye(h, 143, 102); eye(h, 157, 102)
    for side in (-1, 1):
        for k in range(4):
            y = 130 + k * 14
            h.path([(150 + side * 35, y), (150 + side * 75, y - 30 + k * 6), (150 + side * 100, y + 20 + k * 8)])


@drawing("dragon", "predator")
def dragon(h: Hand) -> None:
    h.loop(150, 160, 75, 40, GREEN)
    h.path([(210, 140), (235, 95), (270, 90), (285, 105), (250, 115), (225, 150)], GREEN)
    eye(h, 262, 98)
    h.path([(150, 125), (120, 50), (175, 80), (200, 40), (190, 130)], GREEN)
    h.zigzag([(100, 125), (110, 110), (120, 125), (130, 110), (140, 125)], GREEN)
    legs4(h, 100, 200, 195, 230, GREEN)
    h.path([(75, 165), (40, 175), (20, 150)], GREEN)
    h.zigzag([(287, 100), (305, 95), (300, 110), (318, 108)], ORANGE)


@drawing("monster", "predator", also=("doodle",))
def monster(h: Hand) -> None:
    h.loop(150, 150, 70, 85, PURPLE, 6)
    h.line((110, 75), (95, 35), PURPLE); h.line((190, 75), (205, 35), PURPLE)
    h.loop(150, 115, 16, 16)
    h.dot(150, 115, BLACK, 10)
    h.zigzag([(110, 175), (120, 160), (130, 175), (140, 160), (150, 175), (160, 160), (170, 175), (180, 160), (190, 175)], BLACK)
    h.line((80, 140), (40, 110), PURPLE); h.line((220, 140), (260, 110), PURPLE)


@drawing("bear", "predator", also=("creature",))
def bear(h: Hand) -> None:
    h.loop(150, 160, 70, 60, "#6D4C41", 7)
    h.loop(150, 85, 40, 35, "#6D4C41", 7)
    h.loop(120, 55, 12, 12, "#6D4C41", 6); h.loop(180, 55, 12, 12, "#6D4C41", 6)
    eye(h, 136, 80); eye(h, 164, 80); h.dot(150, 95, BLACK, 10)
    legs4(h, 105, 195, 210, 245, "#6D4C41")


# ---- aquatic

@drawing("fish", "aquatic")
def fish(h: Hand) -> None:
    h.path([(60, 120), (110, 85), (170, 90), (210, 120), (170, 150), (110, 155), (60, 120)], ORANGE)
    h.path([(60, 120), (25, 95), (30, 145), (60, 120)], ORANGE)
    eye(h, 180, 112)
    h.path([(130, 95), (120, 120), (130, 145)], ORANGE, 3)


@drawing("whale", "aquatic")
def whale(h: Hand) -> None:
    h.path([(40, 150), (60, 100), (140, 80), (230, 110), (250, 150), (40, 150)], BLUE, 6)
    h.path([(40, 150), (15, 120), (25, 160), (45, 150)], BLUE, 6)
    eye(h, 215, 125)
    h.path([(150, 78), (150, 50), (135, 35)], BLUE, 3); h.path([(150, 50), (165, 35)], BLUE, 3)


@drawing("jellyfish", "aquatic")
def jellyfish(h: Hand) -> None:
    h.loop(150, 100, 60, 45, PURPLE, 6, start=math.pi, sweep=1.0)
    h.line((90, 100), (210, 100), PURPLE, 6)
    for x in (105, 130, 155, 180, 200):
        h.path([(x, 102), (x - 8, 140), (x + 6, 175), (x - 4, 210)], PURPLE, 4)


@drawing("goldfish in a bowl", "aquatic", also=("water",))
def fishbowl(h: Hand) -> None:
    h.loop(150, 150, 75, 70, BLUE, 4, start=-0.8, sweep=1.5)
    h.path([(120, 150), (145, 135), (170, 150), (145, 165), (120, 150), (105, 140), (105, 160), (120, 150)], ORANGE)
    eye(h, 158, 147)


@drawing("octopus", "aquatic", also=("predator",))
def octopus(h: Hand) -> None:
    h.loop(150, 95, 55, 50, RED, 6)
    eye(h, 135, 90); eye(h, 165, 90)
    for k in range(6):
        x = 105 + k * 18
        h.path([(x, 135), (x - 10 + k * 4, 175), (x + 8, 210), (x - 6, 230)], RED, 5)


# ---- person

@drawing("villager", "person")
def villager(h: Hand) -> None:
    h.loop(150, 60, 25, 28)
    h.line((150, 88), (150, 170))
    h.line((150, 110), (110, 140)); h.line((150, 110), (190, 140))
    h.line((150, 170), (120, 230)); h.line((150, 170), (180, 230))
    eye(h, 142, 56); eye(h, 158, 56)


@drawing("warrior", "person", also=("predator",))
def warrior(h: Hand) -> None:
    villager(h)
    h.line((190, 140), (240, 60), GREY); h.line((180, 120), (205, 135), GREY)
    h.loop(105, 140, 25, 32, RED, 6)
    h.path([(125, 40), (150, 25), (175, 40)], GREY)


@drawing("fisherman", "person")
def fisherman(h: Hand) -> None:
    villager(h)
    h.line((190, 140), (270, 40), "#6D4C41", 5)
    h.path([(270, 40), (285, 120), (282, 170)], GREY, 2)
    h.path([(276, 170), (286, 182), (292, 170)], GREY, 2)


@drawing("medic", "person")
def medic(h: Hand) -> None:
    villager(h)
    h.line((150, 115), (150, 145), RED, 8); h.line((135, 130), (165, 130), RED, 8)


@drawing("girl", "person")
def girl(h: Hand) -> None:
    h.loop(150, 60, 25, 28)
    h.path([(125, 50), (110, 110)], ORANGE, 4); h.path([(175, 50), (190, 110)], ORANGE, 4)
    h.path([(150, 88), (110, 190), (190, 190), (150, 88)], PURPLE, 5)
    h.line((135, 110), (100, 140)); h.line((165, 110), (200, 140))
    h.line((135, 190), (130, 235)); h.line((165, 190), (170, 235))
    eye(h, 142, 56); eye(h, 158, 56)


# ---- food

@drawing("apple", "food", also=("plant",))
def apple(h: Hand) -> None:
    h.loop(150, 150, 50, 45, RED, 8)
    h.line((150, 105), (155, 80), "#6D4C41", 5)
    h.loop(170, 90, 15, 7, GREEN, 4)


@drawing("bread", "food")
def bread(h: Hand) -> None:
    h.path([(70, 170), (70, 120), (100, 95), (200, 95), (230, 120), (230, 170), (70, 170)], "#A1887F", 6)
    for x in (110, 150, 190):
        h.line((x - 10, 115), (x + 10, 140), "#6D4C41", 4)


@drawing("drumstick", "food")
def drumstick(h: Hand) -> None:
    h.loop(130, 130, 55, 40, "#8D6E63", 7)
    h.line((180, 150), (240, 190), GREY, 6)
    h.loop(248, 195, 9, 9, GREY, 5); h.loop(238, 205, 9, 9, GREY, 5)


@drawing("cheese", "food")
def cheese(h: Hand) -> None:
    h.path([(60, 180), (240, 180), (240, 120), (60, 180)], YELLOW, 6)
    h.path([(60, 180), (60, 200), (240, 200), (240, 180)], YELLOW, 6)
    h.loop(180, 165, 8, 6, ORANGE, 4); h.loop(215, 150, 6, 6, ORANGE, 4)


@drawing("banana", "food")
def banana(h: Hand) -> None:
    h.path([(60, 100), (90, 170), (160, 195), (240, 170), (250, 150), (170, 165), (110, 140), (75, 95), (60, 100)], YELLOW, 7)
    h.line((60, 100), (52, 88), "#6D4C41", 5)


# ---- plant

@drawing("apple tree", "plant")
def apple_tree(h: Hand) -> None:
    h.line((140, 250), (145, 150), "#6D4C41", 10); h.line((165, 250), (160, 150), "#6D4C41", 10)
    h.loop(152, 100, 85, 65, GREEN, 6)
    for x, y in ((110, 90), (150, 70), (190, 100), (130, 125), (175, 130)):
        h.loop(x, y, 9, 9, RED, 7)


@drawing("bush", "plant")
def bush(h: Hand) -> None:
    for x, y, r in ((110, 160, 40), (160, 140, 50), (210, 165, 38)):
        h.loop(x, y, r, r * 0.8, GREEN, 6, start=math.pi * 0.9, sweep=1.3)
    h.line((70, 195), (250, 195), GREEN, 5)


@drawing("flower", "plant")
def flower(h: Hand) -> None:
    h.line((150, 240), (150, 120), GREEN, 5)
    h.loop(130, 190, 18, 9, GREEN, 4)
    for k in range(6):
        a = k / 6 * 2 * math.pi
        h.loop(150 + 25 * math.cos(a), 95 + 25 * math.sin(a), 15, 15, RED, 5)
    h.loop(150, 95, 11, 11, YELLOW, 8)


@drawing("grass", "plant")
def grass(h: Hand) -> None:
    for k in range(12):
        x = 50 + k * 18
        h.zigzag([(x, 200), (x + 4, 160 + h.rng.uniform(-15, 10)), (x + 9, 200)], GREEN)
    h.line((40, 202), (270, 202), GREEN, 4)


@drawing("cactus", "plant")
def cactus(h: Hand) -> None:
    h.path([(130, 240), (130, 70), (170, 70), (170, 240)], GREEN, 6)
    h.path([(130, 150), (95, 150), (95, 110)], GREEN, 6)
    h.path([(170, 130), (205, 130), (205, 95)], GREEN, 6)
    h.path([(100, 245), (200, 245)], ORANGE, 6)


# ---- water

@drawing("lake", "water")
def lake(h: Hand) -> None:
    h.loop(160, 130, 120, 60, BLUE, 6)
    h.zigzag([(110, 120), (125, 112), (140, 120), (155, 112)], BLUE)
    h.zigzag([(170, 145), (185, 137), (200, 145), (215, 137)], BLUE)


@drawing("river", "water")
def river(h: Hand) -> None:
    h.path([(20, 60), (90, 100), (150, 80), (220, 130), (290, 120)], BLUE, 6)
    h.path([(20, 110), (90, 150), (150, 130), (220, 180), (290, 170)], BLUE, 6)
    h.zigzag([(100, 125), (115, 118), (130, 125)], BLUE)


@drawing("puddle", "water")
def puddle(h: Hand) -> None:
    h.path([(60, 150), (90, 115), (150, 120), (200, 105), (250, 140), (220, 175), (140, 180), (80, 175), (60, 150)], BLUE, 6)


@drawing("pond with lily", "water", also=("plant",))
def pond(h: Hand) -> None:
    h.loop(150, 140, 110, 55, BLUE, 6)
    h.loop(170, 135, 20, 12, GREEN, 5)
    h.loop(120, 150, 14, 9, GREEN, 5)


# ---- house

@drawing("house", "house")
def house(h: Hand) -> None:
    h.path([(80, 230), (80, 130), (220, 130), (220, 230), (80, 230)], RED, 6)
    h.path([(65, 135), (150, 60), (235, 135)], "#6D4C41", 6)
    h.path([(130, 230), (130, 180), (165, 180), (165, 230)], "#6D4C41", 5)
    h.path([(185, 155), (205, 155), (205, 175), (185, 175), (185, 155)], BLUE, 4)


@drawing("hut", "house")
def hut(h: Hand) -> None:
    h.path([(90, 220), (90, 150), (210, 150), (210, 220), (90, 220)], "#8D6E63", 6)
    h.path([(70, 155), (150, 90), (230, 155), (70, 155)], YELLOW, 6)
    h.loop(150, 195, 14, 25, "#6D4C41", 5, start=math.pi, sweep=1.0)


@drawing("nest", "house", also=("food",))
def nest(h: Hand) -> None:
    for k in range(5):
        h.path([(60, 140 + k * 6), (150, 190 + k * 4), (240, 140 + k * 6)], "#8D6E63", 4)
    h.loop(125, 140, 18, 24, GREY, 4); h.loop(160, 135, 18, 24, GREY, 4); h.loop(195, 142, 17, 22, GREY, 4)


@drawing("barn", "house")
def barn(h: Hand) -> None:
    h.path([(70, 230), (70, 130), (110, 85), (190, 85), (230, 130), (230, 230), (70, 230)], RED, 7)
    h.path([(120, 230), (120, 160), (180, 160), (180, 230)], GREY, 5)
    h.line((120, 160), (180, 230), GREY, 4); h.line((180, 160), (120, 230), GREY, 4)


@drawing("tent", "house")
def tent(h: Hand) -> None:
    h.path([(60, 220), (150, 70), (240, 220), (60, 220)], ORANGE, 6)
    h.path([(150, 70), (130, 220)], ORANGE, 4); h.path([(150, 70), (170, 220)], ORANGE, 4)
    h.line((150, 70), (150, 45), GREY, 4)


# ---- explosive

@drawing("bomb", "explosive")
def bomb(h: Hand) -> None:
    h.loop(140, 150, 60, 60, BLACK, 8)
    h.path([(170, 100), (180, 80)], GREY, 8)
    h.path([(180, 80), (200, 60), (215, 70)], "#8D6E63", 4)
    h.zigzag([(215, 70), (225, 55), (230, 75), (240, 62)], ORANGE)


@drawing("dynamite", "explosive")
def dynamite(h: Hand) -> None:
    for x in (110, 150, 190):
        h.path([(x - 15, 220), (x - 15, 100), (x + 15, 100), (x + 15, 220), (x - 15, 220)], RED, 6)
    h.line((95, 160), (205, 160), BLACK, 4)
    h.path([(150, 100), (155, 70), (175, 55)], GREY, 3)
    h.zigzag([(175, 55), (185, 45), (182, 62), (195, 50)], ORANGE)


@drawing("grenade", "explosive")
def grenade(h: Hand) -> None:
    h.loop(150, 160, 45, 55, GREEN, 7)
    h.path([(130, 105), (130, 85), (170, 85), (170, 105)], GREY, 6)
    h.loop(185, 80, 14, 14, GREY, 4)
    for y in (140, 170):
        h.line((112, y), (188, y), GREEN, 3)


@drawing("TNT box", "explosive", hint="TNT")
def tnt(h: Hand) -> None:
    h.path([(70, 210), (70, 110), (230, 110), (230, 210), (70, 210)], RED, 6)
    h.text("TNT", 95, 175, 50, RED)


@drawing("mine", "explosive")
def mine(h: Hand) -> None:
    h.loop(150, 170, 70, 30, GREY, 7)
    for x in (110, 150, 190):
        h.line((x, 140), (x, 120), GREY, 6)
    h.dot(150, 165, RED, 12)


# ---- vehicle

@drawing("car", "vehicle")
def car(h: Hand) -> None:
    h.path([(50, 180), (50, 140), (90, 140), (115, 100), (195, 100), (225, 140), (260, 140), (260, 180), (50, 180)], RED, 6)
    h.loop(95, 185, 22, 22, BLACK, 7); h.loop(215, 185, 22, 22, BLACK, 7)
    h.path([(125, 110), (150, 110), (150, 138), (110, 138)], BLUE, 4)


@drawing("boat", "vehicle", also=("water",))
def boat(h: Hand) -> None:
    h.path([(50, 160), (250, 160), (220, 200), (80, 200), (50, 160)], "#6D4C41", 6)
    h.line((150, 160), (150, 50), "#6D4C41", 5)
    h.path([(150, 55), (220, 140), (150, 140)], GREY, 4)
    h.zigzag([(30, 215), (60, 208), (90, 215), (120, 208), (150, 215), (180, 208), (210, 215), (240, 208), (270, 215)], BLUE)


@drawing("plane", "vehicle")
def plane(h: Hand) -> None:
    h.path([(40, 140), (60, 120), (260, 120), (280, 140), (260, 160), (60, 160), (40, 140)], GREY, 6)
    h.path([(130, 120), (170, 50), (195, 50), (180, 120)], GREY, 6)
    h.path([(130, 160), (170, 230), (195, 230), (180, 160)], GREY, 6)
    h.path([(60, 120), (45, 85), (70, 85), (85, 120)], GREY, 5)
    for x in (210, 230, 250):
        h.dot(x, 138, BLUE, 8)


@drawing("truck", "vehicle")
def truck(h: Hand) -> None:
    h.path([(40, 190), (40, 90), (180, 90), (180, 190)], BLUE, 6)
    h.path([(180, 120), (240, 120), (265, 150), (265, 190), (40, 190)], BLUE, 6)
    h.loop(85, 195, 20, 20, BLACK, 7); h.loop(220, 195, 20, 20, BLACK, 7)
    h.path([(195, 128), (235, 128), (250, 148), (195, 148), (195, 128)], GREY, 3)


@drawing("bicycle", "vehicle")
def bicycle(h: Hand) -> None:
    h.loop(80, 180, 40, 40); h.loop(220, 180, 40, 40)
    h.path([(80, 180), (130, 120), (200, 120), (220, 180)])
    h.path([(130, 120), (150, 180), (80, 180)])
    h.path([(200, 120), (195, 95), (215, 95)])
    h.path([(130, 120), (125, 100), (110, 100), (145, 100)])


# ---- remote

@drawing("remote control", "remote")
def remote(h: Hand) -> None:
    h.path([(120, 240), (120, 90), (180, 90), (180, 240), (120, 240)], GREY, 6)
    h.line((170, 90), (185, 40), GREY, 4)
    h.dot(150, 115, RED, 12)
    for y in (150, 180, 210):
        h.dot(138, y, BLACK, 9); h.dot(162, y, BLACK, 9)


@drawing("joystick", "remote")
def joystick(h: Hand) -> None:
    h.path([(80, 220), (80, 180), (220, 180), (220, 220), (80, 220)], GREY, 6)
    h.line((150, 180), (150, 100), GREY, 7)
    h.loop(150, 90, 18, 18, RED, 8)
    h.dot(195, 198, RED, 12)


@drawing("gamepad", "remote")
def gamepad(h: Hand) -> None:
    h.path([(70, 130), (230, 130), (260, 200), (220, 205), (195, 175), (105, 175), (80, 205), (40, 200), (70, 130)], PURPLE, 6)
    h.line((95, 150), (125, 150), BLACK, 6); h.line((110, 137), (110, 163), BLACK, 6)
    h.dot(190, 145, RED, 10); h.dot(210, 160, BLUE, 10)


@drawing("steering wheel", "remote", also=("vehicle",))
def steering(h: Hand) -> None:
    h.loop(150, 150, 80, 80, BLACK, 9)
    h.loop(150, 150, 18, 18, BLACK, 6)
    h.line((132, 150), (72, 150), BLACK, 6); h.line((168, 150), (228, 150), BLACK, 6)
    h.line((150, 168), (150, 228), BLACK, 6)


# ---- fire

@drawing("campfire", "fire")
def campfire(h: Hand) -> None:
    h.line((80, 220), (220, 190), "#6D4C41", 10); h.line((80, 190), (220, 220), "#6D4C41", 10)
    h.path([(110, 195), (100, 140), (130, 160), (145, 90), (165, 150), (185, 120), (190, 195)], ORANGE, 6)
    h.path([(135, 195), (140, 160), (155, 175), (165, 195)], YELLOW, 5)


@drawing("torch", "fire")
def torch(h: Hand) -> None:
    h.path([(140, 250), (130, 130), (170, 130), (160, 250)], "#6D4C41", 6)
    h.path([(130, 130), (120, 90), (140, 105), (150, 55), (162, 105), (178, 85), (170, 130)], ORANGE, 6)
    h.path([(145, 125), (150, 95), (157, 125)], YELLOW, 4)


@drawing("wildfire", "fire")
def wildfire(h: Hand) -> None:
    h.zigzag([(40, 210), (60, 140), (80, 180), (100, 100), (125, 170), (150, 80), (175, 160), (200, 110),
              (220, 170), (245, 130), (260, 210)], RED)  # fmt: skip
    h.zigzag([(70, 210), (90, 170), (110, 200), (140, 140), (165, 200), (195, 160), (225, 210)], ORANGE)
    h.zigzag([(110, 210), (140, 185), (170, 210)], YELLOW)


@drawing("candle", "fire", also=("house", "doodle"))
def candle(h: Hand) -> None:
    h.path([(130, 240), (130, 140), (170, 140), (170, 240)], GREY, 6)
    h.line((150, 140), (150, 125), BLACK, 3)
    h.path([(150, 125), (138, 105), (150, 75), (162, 105), (150, 125)], ORANGE, 5)


# ---- sky

@drawing("cloud", "sky")
def cloud(h: Hand) -> None:
    for x, y, r in ((100, 140, 35), (150, 115, 45), (205, 135, 38), (150, 160, 40)):
        h.loop(x, y, r, r * 0.8, BLUE, 5, start=math.pi * 0.8, sweep=1.4)


@drawing("sun", "sky")
def sun(h: Hand) -> None:
    h.loop(150, 150, 45, 45, YELLOW, 8)
    for k in range(10):
        a = k / 10 * 2 * math.pi
        h.line((150 + 58 * math.cos(a), 150 + 58 * math.sin(a)), (150 + 90 * math.cos(a), 150 + 90 * math.sin(a)), ORANGE, 5)


@drawing("rain cloud", "sky", also=("water",))
def rain_cloud(h: Hand) -> None:
    cloud(h)
    for x in (110, 140, 170, 200):
        h.line((x, 200), (x - 8, 230), BLUE, 4)


@drawing("sun behind cloud", "sky")
def sun_cloud(h: Hand) -> None:
    h.loop(190, 110, 40, 40, YELLOW, 7)
    for k in range(6):
        a = -math.pi * 0.9 + k * 0.35
        h.line((190 + 50 * math.cos(a), 110 + 50 * math.sin(a)), (190 + 75 * math.cos(a), 110 + 75 * math.sin(a)), ORANGE, 4)
    h.loop(120, 160, 60, 35, GREY, 6)


# ---- doodle

@drawing("heart", "doodle")
def heart(h: Hand) -> None:
    h.path([(150, 230), (70, 150), (70, 100), (110, 75), (150, 110), (190, 75), (230, 100), (230, 150), (150, 230)], RED, 7)


@drawing("star", "doodle", also=("sky",))
def star(h: Hand) -> None:
    points = []
    for k in range(11):
        a = -math.pi / 2 + k * math.pi / 5
        r = 90 if k % 2 == 0 else 38
        points.append((150 + r * math.cos(a), 150 + r * math.sin(a)))
    h.path(points, YELLOW, 7)


@drawing("smiley", "doodle", also=("sky", "person"))
def smiley(h: Hand) -> None:
    h.loop(150, 150, 70, 70, BLACK, 6)
    eye(h, 125, 130); eye(h, 175, 130)
    h.loop(150, 160, 40, 30, BLACK, 5, start=0.3, sweep=0.8)


@drawing("spiral", "doodle")
def spiral(h: Hand) -> None:
    h.path([(150 + 4 * t * math.cos(t), 150 + 4 * t * math.sin(t)) for t in [k / 4 for k in range(0, 90)]], PURPLE, 5)


@drawing("scribble", "doodle")
def scribble(h: Hand) -> None:
    h.zigzag([(60 + h.rng.uniform(0, 180), 60 + h.rng.uniform(0, 160)) for _ in range(14)], GREEN)


@drawing("letters ABC", "doodle", hint="ABC")
def letters(h: Hand) -> None:
    h.text("ABC", 70, 170, 70, BLUE)


@drawing("word privet", "doodle", hint="привет")
def privet(h: Hand) -> None:
    h.text("привет", 40, 160, 48, RED)


# ---- handwriting hints on rough shapes: the word decides

def blob(h: Hand, color: str = BLACK) -> None:
    h.loop(150, 150, 60, 45, color, 6)
    h.line((110, 190), (105, 220), color); h.line((190, 190), (195, 220), color)


@drawing("blob + волк", "predator", hint="волк")
def hint_wolf_ru(h: Hand) -> None:
    blob(h, GREY); h.text("волк", 100, 270, 34)


@drawing("blob + корова", "creature", hint="корова")
def hint_cow_ru(h: Hand) -> None:
    blob(h); h.text("корова", 80, 270, 34)


@drawing("circle + бомба", "explosive", hint="бомба")
def hint_bomb_ru(h: Hand) -> None:
    h.loop(150, 150, 55, 55, BLACK, 6); h.text("бомба", 85, 260, 34)


@drawing("square + дом", "house", hint="дом")
def hint_house_ru(h: Hand) -> None:
    h.path([(90, 210), (90, 110), (210, 110), (210, 210), (90, 210)], BLACK, 6); h.text("дом", 115, 260, 38)


@drawing("oval + рыба", "aquatic", hint="рыба")
def hint_fish_ru(h: Hand) -> None:
    h.loop(150, 150, 70, 35, ORANGE, 6); h.text("рыба", 105, 240, 34)


@drawing("flames + огонь", "fire", hint="огонь")
def hint_fire_ru(h: Hand) -> None:
    h.zigzag([(100, 200), (115, 140), (130, 180), (150, 110), (170, 180), (185, 140), (200, 200)], ORANGE)
    h.text("огонь", 95, 255, 34)


@drawing("blob + wolf", "predator", hint="wolf")
def hint_wolf_en(h: Hand) -> None:
    blob(h, GREY); h.text("wolf", 110, 270, 34)


@drawing("circle + bomb", "explosive", hint="bomb")
def hint_bomb_en(h: Hand) -> None:
    h.loop(150, 150, 55, 55, BLACK, 6); h.text("bomb", 105, 260, 34)


@drawing("rectangle + car", "vehicle", hint="car")
def hint_car_en(h: Hand) -> None:
    h.path([(60, 190), (60, 130), (240, 130), (240, 190), (60, 190)], RED, 6); h.text("car", 125, 250, 36)


@drawing("puddle + lake", "water", hint="lake")
def hint_lake_en(h: Hand) -> None:
    h.loop(150, 150, 90, 40, BLUE, 6); h.text("lake", 115, 240, 34)


@drawing("sticks + campfire", "fire", hint="campfire")
def hint_campfire_en(h: Hand) -> None:
    h.line((90, 190), (210, 170), "#6D4C41", 8); h.line((90, 170), (210, 190), "#6D4C41", 8)
    h.text("campfire", 70, 240, 32)


def build() -> list[Drawing]:
    out = []
    for n, (name, expected, builder, also, hint) in enumerate(_BUILDERS):
        hand = Hand(seed=1000 + n)
        builder(hand)
        out.append(Drawing(name, expected, hand.strokes, also, hint))
    return out


DRAWINGS = build()
