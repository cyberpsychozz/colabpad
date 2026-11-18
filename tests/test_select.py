from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.alive.select import SelectionLocks, inside, select_strokes
from app.errors import AppError

SQUARE = [(100, 100), (300, 100), (300, 300), (100, 300)]


def line(*points: tuple[float, float]) -> list[list[float]]:
    return [[x, y, 0.5] for x, y in points]


@pytest.fixture
def select(application: FastAPI, client: TestClient) -> Callable[..., list[int]]:
    def run(polygon: list[tuple[float, float]]) -> list[int]:
        repo = application.state.repo
        limits = application.state.config.limits
        strokes = select_strokes(repo, repo.get_canvas("world"), polygon, limits, 1024)
        return [stroke["seq"] for stroke in strokes]

    return run


def code(run: Callable[[], Any]) -> str:
    with pytest.raises(AppError) as caught:
        run()
    return caught.value.code


def test_even_odd_point_in_polygon() -> None:
    assert inside(200, 200, SQUARE) and not inside(50, 200, SQUARE)
    assert not inside(200, 301, SQUARE)
    # A self-intersecting bow-tie: both lobes are inside, the gaps between are outside.
    bow = [(0, 0), (100, 100), (100, 0), (0, 100)]
    assert inside(10, 50, bow) and inside(90, 50, bow)
    assert not inside(50, 10, bow) and not inside(50, 90, bow)
    # A loop drawn twice around encloses the middle an even number of times.
    twice = SQUARE + [(100, 100)] + SQUARE
    assert not inside(200, 200, twice)


def test_seventy_percent_rule(
    select: Callable[..., list[int]], add_stroke: Callable[..., dict[str, Any]]
) -> None:
    fully = add_stroke(points=line((150, 150), (250, 250)))["seq"]
    # 7 of 10 points inside: selected. 6 of 10: not.
    seven = [(110 + i * 20, 200) for i in range(7)] + [(400, 200), (420, 200), (440, 200)]
    six = [(110 + i * 20, 220) for i in range(6)] + [(400 + i * 20, 220) for i in range(4)]
    edge = add_stroke(points=line(*seven))["seq"]
    add_stroke(points=line(*six))
    add_stroke(points=line((500, 500), (600, 600)))  # outside
    assert select(SQUARE) == [fully, edge]
    # The rule counts points, so a long simplified line with one end outside is half in.
    add_stroke(points=line((200, 120), (900, 120)))
    assert select(SQUARE) == [fully, edge]


def test_selection_spans_chunks_and_ignores_fated_strokes(
    application: FastAPI,
    select: Callable[..., list[int]],
    add_stroke: Callable[..., dict[str, Any]],
) -> None:
    repo = application.state.repo
    left = add_stroke(points=line((1000, 500), (1024, 500)))["seq"]
    right = add_stroke(points=line((1024, 500), (1050, 500)))["seq"]
    below = add_stroke(points=line((1010, 1030), (1015, 1040)))["seq"]
    loop = [(990, 480), (1060, 480), (1060, 1060), (990, 1060)]
    assert select(loop) == [left, right, below]
    repo.insert_fates("world", [left], "destroyed")
    assert select(loop) == [right, below]
    repo.insert_fates("world", [right, below], "consumed", "entity-1")
    assert code(lambda: select(loop)) == "STROKES_ALREADY_ALIVE"
    repo.insert_fates("world", [right, below], "fossil", "entity-1")
    assert code(lambda: select(loop)) == "SELECTION_EMPTY"


def test_limits(select: Callable[..., list[int]], add_stroke: Callable[..., dict[str, Any]]) -> None:
    assert code(lambda: select(SQUARE)) == "SELECTION_EMPTY"
    assert code(lambda: select(SQUARE[:2])) == "VALIDATION_FAILED"
    assert code(lambda: select([(i, i % 7) for i in range(501)])) == "VALIDATION_FAILED"
    assert code(lambda: select([(-1, 0), (10, 0), (10, 10)])) == "VALIDATION_FAILED"
    assert code(lambda: select([(0, 0), (19457, 0), (10, 10)])) == "VALIDATION_FAILED"
    assert code(lambda: select([(0, 0), (1025, 0), (10, 10)])) == "SELECTION_TOO_LARGE"
    assert code(lambda: select([(0, 0), (10, 1024.5), (10, 10)])) == "SELECTION_TOO_LARGE"
    for i in range(400):
        add_stroke(points=line((150 + i % 100, 150), (150 + i % 100, 250)))
    assert len(select(SQUARE)) == 400
    add_stroke(points=line((200, 200), (201, 201)))
    assert code(lambda: select(SQUARE)) == "SELECTION_TOO_LARGE"


def test_locks_allow_one_job_per_stroke() -> None:
    locks = SelectionLocks()
    locks.acquire("world", [1, 2, 3], "job-a")
    with pytest.raises(AppError) as busy:
        locks.acquire("world", [3, 4], "job-b")
    assert busy.value.code == "SELECTION_BUSY"
    # A failed acquire locks nothing; other canvases are independent.
    locks.acquire("world", [4], "job-c")
    locks.acquire("other", [1, 2, 3], "job-d")
    locks.release("job-a")
    locks.acquire("world", [1, 2, 3], "job-b")
    locks.release("job-b"); locks.release("job-c"); locks.release("job-d"); locks.release("unknown")
    assert not locks.owner
