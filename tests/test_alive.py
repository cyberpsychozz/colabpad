import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.alive.vlm import FakeVlm, VlmError
from app.config import Settings
from app.main import create_app, create_asgi
from tests.conftest import stroke_in
from tests.test_vlm import COW

LASSO = [[100, 100], [320, 100], [320, 320], [100, 320]]


class World:
    """An app with a scripted VLM, a recorder on socket emits and helpers to drive ALIVE."""

    def __init__(self, tmp_path: Path, vlm: Any, rig: Any = None) -> None:
        settings = Settings(_env_file=None, data_dir=tmp_path)
        self.app: FastAPI = create_app(settings, vlm=vlm, **({"rig": rig} if rig else {}))
        self.limits = self.app.state.config.limits["alive"]
        self.limits["min_interval_s"] = 0
        self.events: list[tuple[str, dict[str, Any], list[str]]] = []

        async def record(event: str, data: dict[str, Any], rooms: list[str]) -> None:
            self.events.append((event, data, rooms))

        async def record_deltas(canvas_id: str, tick: int, chunks: list[dict[str, Any]]) -> None:
            rooms = [f"{canvas_id}:{chunk['cx']}:{chunk['cy']}" for chunk in chunks]
            self.events.append(("world:delta", {"tick": tick, "chunks": chunks}, rooms))

        self.app.state.realtime.emit = record
        self.app.state.realtime.emit_deltas = record_deltas

    def draw(self, offset: float = 0) -> list[int]:
        """A little drawing of three strokes inside LASSO (shifted right by offset)."""
        repo = self.app.state.repo
        lines = [[(150, 150), (250, 150)], [(150, 150), (150, 250)], [(250, 150), (250, 250)]]
        return [
            repo.insert_stroke(
                stroke_in(points=[[x + offset, y, 0.5] for x, y in line]), (int((150 + offset) // 1024), 0)
            )[0]["seq"]
            for line in lines
        ]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.events]

    def last(self, name: str) -> dict[str, Any]:
        return next(data for event, data, _ in reversed(self.events) if event == name)


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    clients: list[TestClient] = []

    def build(vlm: Any = None, rig: Any = None) -> tuple[World, TestClient]:
        world = World(tmp_path / str(len(clients)), vlm or FakeVlm(COW), rig)
        client = TestClient(create_asgi(world.app))
        client.__enter__()
        clients.append(client)
        return world, client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def alive(client: TestClient, lasso: list[list[float]] = LASSO, **kwargs: Any) -> Any:
    return client.post("/api/canvases/world/alive", json={"lasso": lasso}, **kwargs)


def wait(client: TestClient, job_id: str, *until: str) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get(f"/api/alive/{job_id}").json()
        if status["status"] in (until or ("spawned", "failed")):
            return status
        time.sleep(0.01)
    raise AssertionError(f"job stuck in {status}")


def test_a_drawing_comes_alive(make: Any) -> None:
    world, client = make()
    seqs = world.draw()
    assert client.get("/api/me").json()["quota"] == {"used": 0, "limit": 10}
    started = time.perf_counter()
    response = alive(client)
    assert response.status_code == 202
    body = response.json()
    assert body["quota"] == {"used": 0, "limit": 10}
    # The goo was broadcast before the request returned, well inside 300 ms.
    assert time.perf_counter() - started < 0.3
    assert world.events[0] == (
        "alive:goo",
        {"jobId": body["jobId"], "polygon": [tuple(p) for p in LASSO], "by": None},
        ["world:0:0"],
    )
    status = wait(client, body["jobId"])
    assert status == {"status": "spawned", "type": "creature", "entityId": status["entityId"], "error": None}
    assert world.names() == ["alive:goo", "alive:progress", "alive:progress", "stroke:fate", "world:event"]
    assert [data["stage"] for name, data, _ in world.events if name == "alive:progress"] == ["classify", "rig"]
    assert world.last("stroke:fate") == {"seqs": seqs, "fate": "consumed", "entityId": status["entityId"]}

    entity = world.last("world:event")["entity"]
    assert world.last("world:event")["type"] == "spawn" and entity["id"] == status["entityId"]
    assert entity["type"] == "creature" and entity["label"] == {"ru": "корова", "en": "cow"}
    # It appears where it was drawn: the middle of the strokes' box.
    assert abs(entity["x"] - 200) < 3 and abs(entity["y"] - 200) < 3
    assert 100 < entity["w"] < 112 and entity["facing"] == "left"
    manifest = entity["sprite"]["manifest"]
    assert set(manifest) == {"frameW", "frameH", "pivot", "facing", "anims"}
    assert set(manifest["anims"]) == {"idle", "move", "act", "death"}
    sheet = client.get(entity["sprite"]["sheet"])
    assert sheet.status_code == 200 and sheet.headers["content-type"] == "image/png"
    assert "immutable" in sheet.headers["cache-control"]

    # Stroke rows are untouched; only fates were added. The chunk shows the entity.
    repo = world.app.state.repo
    assert repo.db.execute("SELECT count(*) FROM stroke").fetchone()[0] == 3
    chunk = client.get("/api/canvases/world/chunks/0/0").json()
    assert [fate["fate"] for fate in chunk["fates"]] == ["consumed"] * 3
    assert [e["id"] for e in chunk["entities"]] == [entity["id"]]
    job = repo.get_job(body["jobId"])
    assert '"prompt": "fake"' in job["vlm_result"] and '"raster"' in job["timings"]
    assert client.get("/api/me").json()["quota"] == {"used": 1, "limit": 10}
    # The same strokes cannot come alive twice, and the locks are free again.
    assert alive(client).json()["error"]["code"] == "STROKES_ALREADY_ALIVE"
    assert not world.app.state.alive.locks.owner and not world.app.state.alive.jobs


def test_low_confidence_asks_the_author(make: Any) -> None:
    unsure = {**COW, "confidence": 0.4, "top3": [{"type": "creature", "p": 0.4}, {"type": "predator", "p": 0.35}]}
    world, client = make(FakeVlm(unsure))
    world.draw()
    job_id = alive(client).json()["jobId"]
    wait(client, job_id, "choose")
    name, ask, rooms = world.events[-1]
    session_id = world.app.state.repo.get_job(job_id)["session_id"]
    assert name == "alive:choose" and rooms == [f"session:{session_id}"]
    assert ask["top3"] == ["creature", "predator", "doodle"] and ask["deadline"] > time.time() * 1000
    # Someone else cannot answer, and the answer must be a real type.
    stranger = TestClient(create_asgi(world.app))
    assert stranger.post(f"/api/alive/{job_id}/choice", json={"type": "food"}).status_code == 404
    assert client.post(f"/api/alive/{job_id}/choice", json={"type": "unicorn"}).status_code == 400
    assert client.post(f"/api/alive/{job_id}/choice", json={"type": "predator"}).json() == {"ok": True}
    status = wait(client, job_id)
    assert status["status"] == "spawned" and status["type"] == "predator"
    entity = world.last("world:event")["entity"]
    # A different type than the model's starts from the archetype's own label.
    assert entity["type"] == "predator" and entity["label"] == {"ru": "хищник", "en": "predator"}
    late = client.post(f"/api/alive/{job_id}/choice", json={"type": "food"})
    assert late.status_code == 410 and late.json()["error"]["code"] == "CHOICE_EXPIRED"


def test_no_answer_picks_the_first_guess(make: Any) -> None:
    world, client = make(FakeVlm({**COW, "confidence": 0.3}))
    world.limits["choose_timeout_s"] = 0.05
    world.draw()
    status = wait(client, alive(client).json()["jobId"])
    assert status["status"] == "spawned" and status["type"] == "creature"
    assert world.last("world:event")["entity"]["label"]["en"] == "cow"


@pytest.mark.parametrize("mode", ["timeout", "unavailable", "flag_off"])
def test_without_the_vlm_the_manual_grid_still_brings_it_to_life(make: Any, mode: str) -> None:
    code = "VLM_TIMEOUT" if mode == "timeout" else "VLM_UNAVAILABLE"
    vlm = FakeVlm(VlmError(code))
    world, client = make(vlm)
    if mode == "flag_off":
        world.app.state.config.flags.vlm.enabled = False
    world.draw()
    job_id = alive(client).json()["jobId"]
    wait(client, job_id, "choose")
    assert world.events[-1][0] == "alive:choose" and world.events[-1][1]["top3"] is None
    assert "alive:failed" not in world.names()
    assert vlm.calls == (0 if mode == "flag_off" else 1)
    assert client.post(f"/api/alive/{job_id}/choice", json={"type": "vehicle"}).status_code == 200
    status = wait(client, job_id)
    assert status == {"status": "spawned", "type": "vehicle", "entityId": status["entityId"], "error": None}
    assert world.last("world:event")["entity"]["label"] == {"ru": "транспорт", "en": "vehicle"}

    # And with nobody answering, it becomes a living scribble.
    world.limits["choose_timeout_s"] = 0.05
    world.draw(offset=400)
    shifted = [[x + 400, y] for x, y in LASSO]
    status = wait(client, alive(client, shifted).json()["jobId"])
    assert status["status"] == "spawned" and status["type"] == "doodle"


def test_unsafe_spends_quota_and_multiple_does_not(make: Any) -> None:
    world, client = make(FakeVlm({**COW, "unsafe": True}, {**COW, "multiple": True}))
    seqs = world.draw()
    status = wait(client, alive(client).json()["jobId"])
    assert status["status"] == "failed" and status["error"] == "UNSAFE_CONTENT"
    assert world.events[-1][0] == "alive:failed" and world.events[-1][1]["code"] == "UNSAFE_CONTENT"
    assert client.get("/api/me").json()["quota"]["used"] == 1
    # The goo fizzles and the strokes stay as they were.
    assert world.app.state.repo.static_seqs("world", seqs) == set(seqs)
    status = wait(client, alive(client).json()["jobId"])
    assert status["error"] == "SELECTION_MULTIPLE"
    assert client.get("/api/me").json()["quota"]["used"] == 1


def test_our_own_failure_refunds_and_unlocks(make: Any) -> None:
    def broken_rig(cutout: Any, classified: Any) -> Any:
        raise RuntimeError("rig exploded")

    world, client = make(rig=broken_rig)
    seqs = world.draw()
    status = wait(client, alive(client).json()["jobId"])
    assert status["status"] == "failed" and status["error"] == "INTERNAL"
    assert world.last("alive:failed")["code"] == "INTERNAL"
    assert client.get("/api/me").json()["quota"]["used"] == 0
    assert world.app.state.repo.static_seqs("world", seqs) == set(seqs)
    assert not world.app.state.alive.locks.owner
    assert world.app.state.repo.db.execute("SELECT count(*) FROM entity").fetchone()[0] == 0


def test_request_validation_busy_and_rate_limit(make: Any) -> None:
    world, client = make(FakeVlm({**COW, "confidence": 0.1}))
    assert alive(client).json()["error"]["code"] == "SELECTION_EMPTY"
    assert alive(client).status_code == 422
    assert alive(client, LASSO[:2]).json()["error"] == {
        "code": "VALIDATION_FAILED", "message": "Check the format and the allowed values",
        "field": "lasso", "retryable": False, "details": {},
    }  # fmt: skip
    assert alive(client, [[0, 0], [2000, 0], [0, 50]]).json()["error"]["code"] == "SELECTION_TOO_LARGE"
    assert client.post("/api/canvases/0123456789/alive", json={"lasso": LASSO}).status_code == 404
    world.draw()
    first = alive(client).json()["jobId"]
    wait(client, first, "choose")
    # While the author is choosing, the same strokes are busy for everyone.
    busy = alive(TestClient(create_asgi(world.app)))
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "SELECTION_BUSY"
    assert busy.json()["error"]["retryable"] is True
    client.post(f"/api/alive/{first}/choice", json={"type": "food"})
    wait(client, first)
    world.limits["min_interval_s"] = 5
    world.app.state.alive.last_request.clear()
    world.draw(offset=400)
    shifted = [[x + 400, y] for x, y in LASSO]
    assert alive(client, shifted).status_code == 202
    limited = alive(client, shifted)
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"


def test_daily_quota_and_moscow_midnight(make: Any) -> None:
    world, client = make()
    world.limits["quota_per_day"]["anon"] = 2
    service = world.app.state.alive
    now = {"t": 1_794_000_000.0}  # some instant; the day is computed in UTC+3
    service.clock = lambda: now["t"]
    day = service.day()
    for i in range(2):
        world.draw(offset=400 * i)
        job = alive(client, [[x + 400 * i, y] for x, y in LASSO])
        assert wait(client, job.json()["jobId"])["status"] == "spawned"
    world.draw(offset=1200)
    third = [[x + 1200, y] for x, y in LASSO]
    denied = alive(client, third, headers={"Accept-Language": "ru"})
    assert denied.status_code == 429
    assert denied.json()["error"] == {
        "code": "QUOTA_EXCEEDED", "message": "✨ на сегодня закончились", "field": None,
        "retryable": False, "details": {"used": 2, "limit": 2, "plan": "anon", "more": 18},  # free (20) minus anon (2)
    }  # fmt: skip
    # Another visitor has their own quota.
    other = TestClient(create_asgi(world.app))
    assert other.get("/api/me").json()["quota"] == {"used": 0, "limit": 2}
    # Step to the next Moscow midnight: the quota is back.
    while service.day() == day:
        now["t"] += 600
    assert client.get("/api/me").json()["quota"] == {"used": 0, "limit": 2}
    assert alive(client, third).status_code == 202


def test_me_and_session_cookie(make: Any) -> None:
    world, client = make()
    first = client.get("/api/me", headers={"Accept-Language": "ru-RU"})
    cookie = first.headers["set-cookie"]
    assert "cp_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert first.json() == {
        "user": None, "plan": "free", "ads": False, "locale": "ru", "quota": {"used": 0, "limit": 10}, "subscription": None,
        "adBlocks": {"side": "", "bottom": ""},
    }  # fmt: skip
    again = client.get("/api/me")
    assert "set-cookie" not in again.headers and again.json()["locale"] == "ru"
    assert world.app.state.repo.db.execute("SELECT count(*) FROM session").fetchone()[0] == 1
