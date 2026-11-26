"""Generated choreography is finite data, with shared interpolation fixtures."""

import copy
import json
from pathlib import Path

import pytest

from app.alive.choreography import Generation, compile_plan, pose, visual_bounds


def performance():
    return {
        "version": 1,
        "status": "ok",
        "reason": None,
        "label": {"ru": "звезда", "en": "star"},
        "duration": 2000,
        "parts": [],
        "repeats": [],
        "tracks": [
            {
                "target": "root",
                "property": "x",
                "keys": [
                    {"t": 0, "v": 0, "ease": "linear"},
                    {"t": 2000, "v": 1, "ease": "linear"},
                ],
            },
            {
                "target": "body",
                "property": "rotation",
                "keys": [
                    {"t": 0, "v": 0, "ease": "smooth"},
                    {"t": 2000, "v": 360, "ease": "linear"},
                ],
            },
        ],
    }


def compile(data=None, **kw):
    return compile_plan(Generation.model_validate(data or performance()), 100, 80, **kw)


def test_parallel_tracks_units_and_final_hold():
    plan = compile()
    assert pose(plan, 1000, "root")["x"] == 50
    assert pose(plan, 1000, "body")["rotation"] == 180
    assert pose(plan, 90000, "root")["x"] == 100
    assert pose(plan, 90000, "body")["rotation"] == 360


def test_repeat_expands_every_track_on_one_finite_timeline():
    data = performance()
    data["tracks"][0]["keys"] = [
        {"t": 0, "v": 0, "ease": "linear"},
        {"t": 500, "v": 1, "ease": "linear"},
        {"t": 1000, "v": 0, "ease": "linear"},
        {"t": 2000, "v": 1, "ease": "linear"},
    ]
    data["tracks"][1]["keys"] = [
        {"t": t, "v": 0, "ease": "linear"} for t in (0, 1000, 2000)
    ]
    data["repeats"] = [{"start": 0, "end": 1000, "count": 3}]
    plan = compile(data)
    assert plan["duration"] == 4000
    assert [pose(plan, t, "root")["x"] for t in (500, 1500, 2500, 3000, 4000)] == [
        100,
        100,
        100,
        0,
        100,
    ]
    assert "repeats" not in plan


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(duration=30001),
        lambda p: p["tracks"][0].update(target="missing_arm"),
        lambda p: p["tracks"].append(copy.deepcopy(p["tracks"][0])),
        lambda p: p["tracks"][0]["keys"][1].update(t=0),
        lambda p: p["tracks"][0]["keys"][1].update(v=float("nan")),
        lambda p: p["tracks"][0]["keys"][1].update(v=10),
        lambda p: p["tracks"][0].update(property="rotation"),
        lambda p: p.update(repeats=[{"start": 0, "end": 2000, "count": 2}]),
        lambda p: p.update(
            parts=[{"id": "body", "box": [0, 0, 1, 1], "pivot": [0.5, 0.5], "order": 1}]
        ),
    ],
)
def test_invalid_output_never_compiles(change):
    data = performance()
    change(data)
    with pytest.raises(ValueError):
        compile(data)


def test_continuous_bounds_include_rotation_extrema_between_keys():
    plan = compile()
    layers = [{"id": "body", "offset": [-50, -40], "size": [100, 80], "pivot": [0, 0]}]
    low_x, low_y, high_x, high_y = visual_bounds(plan, layers, 0, 2000)
    assert low_x <= -50 and low_y < -60 and high_x >= 150 and high_y > 60
    with pytest.raises(ValueError):
        compile_plan(
            Generation.model_validate(performance()),
            100,
            80,
            layers=layers,
            origin=(20, 20),
            bounds=(1000, 1000),
        )


def test_shared_python_javascript_interpolation_fixture():
    fixture = json.loads(
        (Path(__file__).parent / "golden/choreography.json").read_text()
    )
    for sample in fixture["samples"]:
        assert pose(fixture["plan"], sample["t"], sample["target"]) == pytest.approx(
            sample["pose"]
        )


def test_shared_transform_order_and_pivot_fixture():
    from app.alive.choreography import _transform

    fixture = json.loads(
        (Path(__file__).parent / "golden/choreography.json").read_text()
    )
    for sample in fixture["transforms"]:
        x, y = sample["point"]
        px, py = _transform(
            (x, x),
            (y, y),
            sample["pivot"],
            {k: (v, v) for k, v in sample["pose"].items()},
        )
        assert [px[0], py[0]] == pytest.approx(sample["expected"])


@pytest.mark.parametrize(
    "limits",
    [
        {"keyframes": 3},
        {"duration_ms": 1000},
        {"plan_bytes": 50},
        {"root_displacement_px": 50},
    ],
)
def test_configured_limits_are_applied_before_playback(limits):
    with pytest.raises(ValueError):
        compile(limits=limits)


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan")])
def test_every_transform_number_and_part_coordinate_is_finite(bad_value):
    data = performance()
    data["parts"] = [
        {"id": "arm", "box": [0, 0, 0.3, 1], "pivot": [bad_value, 0.5], "order": 1}
    ]
    with pytest.raises(ValueError):
        compile(data)


@pytest.mark.parametrize("value", ["1", True, None])
def test_transform_values_are_numbers_not_coerced_strings_or_booleans(value):
    data = performance()
    data["tracks"][0]["keys"][0]["v"] = value
    with pytest.raises(ValueError):
        compile(data)


def test_shared_loop_and_pause_fixture():
    from app.alive.choreography import playback_time

    fixture = json.loads(
        (Path(__file__).parent / "golden/choreography.json").read_text()
    )
    for sample in fixture["playback_samples"]:
        assert (
            playback_time(fixture["plan"], sample["elapsed"], sample["pauseMs"])
            == sample["phase"]
        )
