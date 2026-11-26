"""A generated wheel owns its strokes, not everything in its bounding rectangle."""

import math
from types import SimpleNamespace

import pytest
from PIL import Image

from app.alive.parts import split_named
from app.alive.choreography import DrawingParts
from app.alive.preparation import prepare_atlas
from app.alive.rasterize import Cutout, render


def stroke(points, width=3):
    return {'color': '#000000', 'width': width, 'points': [[x, y, .5] for x, y in points]}


def wheel_scene(scale=1, origin=(0, 0)):
    local = [
        stroke([(5, 45), (190, 45)]),
        stroke([(60 + 20 * math.cos(i * math.tau / 80),
                 45 + 20 * math.sin(i * math.tau / 80)) for i in range(81)]),
        stroke([(60, 45), (77, 45)]),
        stroke([(40, 25), (41, 26)], width=1),
    ]
    image = Image.fromarray(render(local, 0, 0, 200, 100))
    world = [{**s, 'width': s['width'] / scale,
              'points': [[x / scale + origin[0], y / scale + origin[1], p]
                         for x, y, p in s['points']]} for s in local]
    cutout = Cutout(image, origin[0], origin[1], scale, world)
    # Deliberately use an arbitrary ID; ownership must not depend on its name.
    part = SimpleNamespace(id='roller', kind='wheel', box=(.2, .25, .2, .4),
                           pivot=(.3, .45), pivot_type='center', order=1)
    return cutout, part


@pytest.mark.parametrize('scale,origin', [(1, (0, 0)), (.5, (3000, 500)), (2, (150, 150))])
def test_wheel_keeps_crossing_body_line_and_rectangle_corner_outside_its_layer(scale, origin):
    cutout, part = wheel_scene(scale, origin)
    groups = split_named(cutout, [part])
    assert groups['roller'] == [cutout.strokes[1], cutout.strokes[2]]
    assert groups['body'] == [cutout.strokes[0], cutout.strokes[3]]


def test_wheel_ownership_uses_arc_length_not_nonuniform_sample_counts():
    cutout, part = wheel_scene()
    # The renderer samples this quadratic densely near its start. Most samples
    # lie inside the wheel, while a substantial length is an external axle.
    axle = stroke([(60, 45), (95, 45)])
    cutout.strokes = [axle, cutout.strokes[1]]
    groups = split_named(cutout, [part])
    assert groups['body'] == [axle]
    assert groups['roller'] == [cutout.strokes[1]]


def test_chunk_cut_does_not_make_a_short_body_piece_into_a_wheel_spoke():
    body = [stroke([(1015, 45), (1024, 45)]), stroke([(1024, 45), (1110, 45)])]
    circle = stroke([(1024 + 20 * math.cos(i * math.tau / 80),
                      45 + 20 * math.sin(i * math.tau / 80)) for i in range(81)])
    strokes = [*body, circle]
    cutout = Cutout(Image.fromarray(render(strokes, 950, 0, 200, 100)), 950, 0, 1, strokes)
    part = SimpleNamespace(id='roller', kind='wheel', box=(.27, .25, .2, .4),
                           pivot=(.37, .45), pivot_type='center', order=1)
    groups = split_named(cutout, [part])
    assert groups['body'] == body
    assert groups['roller'] == [circle]


def test_a_non_wheel_center_part_can_keep_rectangular_corner_lines():
    cutout, part = wheel_scene()
    part.kind = 'part'
    cutout.strokes = [cutout.strokes[3]]
    assert split_named(cutout, [part])['roller'] == cutout.strokes


def identified(part):
    return DrawingParts.model_validate({
        'version': 2, 'status': 'ok', 'reason': None,
        'label': {'ru': 'машина', 'en': 'car'},
        'parts': [{**vars(part), 'label': {'ru': 'колесо', 'en': 'wheel'}}],
    })


def test_prepared_wheel_atlas_has_no_crossing_body_line_or_corner_fragment():
    cutout, part = wheel_scene()
    drawing, atlas, manifest = prepare_atlas(cutout, identified(part))
    assert drawing.parts[0].kind == 'wheel'

    def alpha(name, x, y):
        layer = next(l for l in manifest['layers'] if l['id'] == name)
        sx, sy, w, h = layer['src']
        x -= layer['offset'][0] + cutout.image.width / 2
        y -= layer['offset'][1] + cutout.image.height / 2
        return atlas.getpixel((sx + round(x), sy + round(y)))[3] if 0 <= x < w and 0 <= y < h else 0

    assert alpha('body', 55, 45) > 200
    assert alpha('roller', 55, 45) == 0
    assert alpha('body', 40, 25) > 100
    assert alpha('roller', 40, 25) == 0


def test_identified_wheel_requires_center_pivot():
    cutout, part = wheel_scene()
    drawing = identified(part)
    drawing.parts[0].pivot_type = 'joint'
    with pytest.raises(ValueError, match='wheel must rotate around its center'):
        prepare_atlas(cutout, drawing)
