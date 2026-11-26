import asyncio

import pytest

from app.alive.atlas import build_atlas
from app.alive.choreography import DrawingParts, Generation, Motion, compile_plan
from app.alive.rasterize import rasterize
from app.alive.vlm import FakeVlm, VlmError
from tests.animationkit import split_performance
from tests.simkit import make_world, row, run
from tests.test_alive import LASSO, wait
from tests.test_alive import make as make
from tests.test_choreography import performance
from tests.test_vlm import adapter, reply


def submit(client, description="spin and move"):
    response = client.post(
        "/api/canvases/world/alive", json={"lasso": LASSO, "description": description}
    )
    assert response.status_code == 202
    return wait(client, response.json()["jobId"])


def test_generated_job_stores_atlas_plan_origin_and_consumes_only_on_success(make):
    world, client = make(FakeVlm(*split_performance(performance())))
    seqs = world.draw()
    status = submit(client)
    assert status["status"] == "spawned"
    entity = world.app.state.repo.get_entity(status["entityId"])
    assert entity["mode"] == "scripted_visual"
    assert entity["hp"] == 100
    assert entity["plan"]["duration"] == 2000
    assert entity["sprite"]["manifest"]["format"] == "layered"
    assert entity["origin"] == [entity["x"], entity["y"]]
    assert world.app.state.repo.static_seqs("world", seqs) == set()
    assert client.get("/api/me").json()["quota"]["used"] == 1
    assert "alive:choose" not in world.names()


@pytest.mark.parametrize(
    "result,code",
    [
        (VlmError("VLM_UNAVAILABLE"), "VLM_UNAVAILABLE"),
        (VlmError("VLM_TIMEOUT"), "VLM_TIMEOUT"),
        ({**performance(), "duration": -1}, "ANIMATION_INVALID"),
        (
            {
                **performance(),
                "tracks": [{**performance()["tracks"][0], "target": "ghost"}],
            },
            "ANIMATION_INVALID",
        ),
        (
            {**performance(), "status": "unsupported", "reason": "other_actors"},
            "ANIMATION_OTHER_ACTORS",
        ),
        (
            {**performance(), "status": "unsupported", "reason": "gameplay"},
            "ANIMATION_GAMEPLAY",
        ),
        (
            {**performance(), "status": "unsupported", "reason": "missing_parts"},
            "ANIMATION_MISSING_PARTS",
        ),
        ({**performance(), "status": "unsafe"}, "UNSAFE_CONTENT"),
        ({**performance(), "status": "multiple"}, "SELECTION_MULTIPLE"),
    ],
)
def test_generation_failures_leave_original_strokes_unlock_and_refund(
    make, result, code
):
    world, client = make(FakeVlm(*split_performance(result)))
    seqs = world.draw()
    status = submit(client)
    assert status["status"] == "failed" and status["error"] == code
    assert world.app.state.repo.static_seqs("world", seqs) == set(seqs)
    assert not world.app.state.alive.locks.owner
    assert world.app.state.repo.count_living("world") == 0
    assert client.get("/api/me").json()["quota"]["used"] == (
        1 if code == "UNSAFE_CONTENT" else 0
    )
    assert "alive:choose" not in world.names()


def test_disabled_generation_does_not_offer_grid_but_blank_description_does(make):
    world, client = make(FakeVlm(*split_performance(performance())))
    world.draw()
    world.app.state.config.flags.vlm.enabled = False
    assert submit(client)["error"] == "VLM_UNAVAILABLE"
    world.limits["choose_timeout_s"] = 0.01
    assert submit(client, " \n ")["status"] == "spawned"


def test_generated_simulation_pauses_sleep_freeze_repeats_and_recovers():
    w = make_world()
    p = compile_plan(Generation.model_validate(performance()), 100, 80)
    e = w.add(
        row("explosive", 500, 500, mode="scripted_visual", plan=p, origin=[500, 500])
    )
    run(w, 0.5)
    assert e.data["playback"]["elapsed"] == 500 and w.pos(e) == (525, 500)
    w.set_frozen(True)
    run(w, 10)
    assert e.data["playback"]["elapsed"] == 500
    assert not e.data["view"]["playback"]["running"]
    w.set_frozen(False)
    run(w, 0.5)
    w.set_subscribed((0, 0), False)
    run(w, 20)
    assert e.data["playback"]["elapsed"] == 1000
    saved = w.snapshot_row(e)
    restored = make_world()
    recovered_row = row(
        "explosive",
        saved["x"],
        saved["y"],
        mode="scripted_visual",
        plan=p,
        origin=[500, 500],
        data=saved["data"],
    )
    recovered = restored.add(recovered_row)
    run(restored, 1)
    assert recovered.data["playback"]["elapsed"] == 2000
    assert restored.pos(recovered) == (600, 500)
    run(restored, 1.5)
    assert restored.pos(recovered) == (600, 500)
    run(restored, 0.7)
    assert recovered.data["playback"]["elapsed"] == 4200
    assert restored.pos(recovered) == (510, 500)
    run(restored, 8)
    assert recovered.id in restored.entities and restored.pos(recovered) == (510, 500)
    assert not recovered.comps and not restored.hot
    assert recovered.data["view"]["playback"]["running"]


def test_visual_is_excluded_from_every_type_and_nearby_target_queries():
    w = make_world()
    p = compile_plan(Generation.model_validate(performance()), 100, 80)
    visuals = [
        w.add(row(kind, 500, 500, mode="scripted_visual", plan=p, origin=[500, 500]))
        for kind in w.config.archetypes
    ]
    ordinary = [w.add(row(kind, 500, 500)) for kind in w.config.archetypes]
    run(w, 1)
    assert all(e not in w.of_type(*w.config.archetypes) for e in visuals)
    assert all(e not in w.near(500, 500, 2000) for e in visuals)
    before = [(e.id, w.pos(e)) for e in visuals]
    from app.sim.systems.interact import interact

    for e in visuals:
        w.damage(e, 99999, "blast")
        w.kill(e, True)
        interact(w, e.id, "drag", [100, 100])
        interact(w, e.id, "push", [499, 499])
    assert all(e.id in w.entities for e in visuals)
    assert [(e.id, w.pos(e)) for e in visuals] == before
    assert not any(e.target in {v.id for v in visuals} for e in ordinary)


def test_named_layers_retain_lines_and_missing_parts_fail(make):
    world, _client = make()
    world.draw()
    strokes = list(world.app.state.repo.chunk_strokes("world", 0, 0))
    cutout = rasterize(strokes)
    part = Generation.model_validate(
        {
            **performance(),
            "parts": [
                {
                    "id": "left_arm",
                    "box": [0, 0, 0.3, 1],
                    "pivot": [0.2, 0.2],
                    "order": 1,
                }
            ],
        }
    ).parts
    atlas, manifest = build_atlas(cutout, part)
    assert atlas.width > 0 and atlas.height > 0
    assert {l["id"] for l in manifest["layers"]} == {"body", "left_arm"}
    assert all(l["size"][0] > 0 for l in manifest["layers"])
    part[0].box = (0.4, 0.4, 0.1, 0.1)
    with pytest.raises(ValueError):
        build_atlas(cutout, part)


def test_job_deadline_covers_all_generation_attempts_and_refunds(make):
    class Slow(FakeVlm):
        async def identify_parts(self, png):
            await asyncio.sleep(1)
            return DrawingParts.model_validate(split_performance(performance())[0])

    world, client = make(Slow())
    world.draw()
    world.app.state.config.vlm["generation"]["hard_timeout_s"] = 0.1
    assert submit(client)["error"] == "VLM_TIMEOUT"
    assert client.get("/api/me").json()["quota"]["used"] == 0


def test_invalid_provider_attempts_spend_shared_budget_and_return_typed_failure():
    vlm, spent, models = adapter(lambda request, body, n: reply("bad json"))
    with pytest.raises(VlmError) as error:
        asyncio.run(vlm.identify_parts(b"png"))
    assert error.value.code == "ANIMATION_INVALID"
    assert len(models) == 3 and next(iter(spent.values())) == 6000


def test_snapshot_database_roundtrip_preserves_elapsed_plan_and_origin(make):
    world, client = make(FakeVlm(*split_performance(performance())))
    world.draw()
    status = submit(client)
    sim = world.app.state.sim

    async def exercise():
        sim.send({"op": "chunk", "canvas": "world", "cx": 0, "cy": 0, "on": True})
        await sim.step(0.1, 7)
        await sim.handle(sim.transport.core.flush())
        before = world.app.state.repo.sim_rows(entity_id=status["entityId"])[0]
        assert before["params"]["data"]["playback"]["elapsed"] == 700
        from app.sim.bridge import InlineTransport

        sim.transport = InlineTransport(world.app.state.config)
        sim.load()
        await sim.step(0.1, 1)
        e = sim.transport.core.worlds["world"].entities[status["entityId"]]
        # No connected socket after recovery: pause, rather than return to frame zero.
        assert e.data["playback"]["elapsed"] == 700
        assert e.params["origin"] == before["params"]["origin"]
        assert e.params["plan"] == before["params"]["plan"]

    client.portal.call(exercise)


def test_root_chunk_crossing_is_reported_and_sleep_stops_at_saved_origin():
    w = make_world()
    p = compile_plan(Generation.model_validate(performance()), 100, 80)
    e = w.add(
        row("water", 1000, 500, mode="scripted_visual", plan=p, origin=[1000, 500])
    )
    out = run(w, 1)
    assert e.chunk == (1, 0)
    assert any(e.id in delta["enter"] for delta in out if delta["op"] == "delta")
    w.set_subscribed((0, 0), False)
    elapsed = e.data["playback"]["elapsed"]
    run(w, 20)
    assert e.data["playback"]["elapsed"] == elapsed
    w.set_subscribed((1, 0), True)
    run(w, 1)
    assert w.pos(e) == (1100, 500)


def test_population_and_editor_access_rechecked_after_provider_returns(make):
    world_ref = []

    class Revoke(FakeVlm):
        async def animate(self, drawing, description):
            world_ref[0].app.state.repo.db.execute(
                "UPDATE canvas SET frozen=1 WHERE id='world'"
            )
            return Motion.model_validate(split_performance(performance())[1])

    world, client = make(Revoke(*split_performance(performance())))
    world_ref.append(world)
    seqs = world.draw()
    assert submit(client)["error"] == "CANVAS_FROZEN"
    assert world.app.state.repo.static_seqs("world", seqs) == set(seqs)


def test_world_edge_failure_keeps_the_drawing_static(make):
    world, client = make(FakeVlm(*split_performance(performance())))
    seqs = world.draw(offset=19100)
    r = client.post(
        "/api/canvases/world/alive",
        json={
            "lasso": [[x + 19100, y] for x, y in LASSO],
            "description": "spin and move",
        },
    )
    assert r.status_code == 202
    assert wait(client, r.json()["jobId"])["error"] == "ANIMATION_INVALID"
    assert world.app.state.repo.static_seqs("world", seqs) == set(seqs)
    assert client.get("/api/me").json()["quota"]["used"] == 0


def test_initial_root_transform_is_authoritative_at_spawn(make):
    result = performance()
    result["tracks"][0]["keys"][0]["v"] = 0.25
    world, client = make(FakeVlm(*split_performance(result)))
    world.draw()
    status = submit(client)
    entity = world.app.state.repo.get_entity(status["entityId"])
    assert entity["x"] == pytest.approx(
        entity["origin"][0] + entity["plan"]["tracks"][0]["keys"][0]["v"]
    )

    stored = world.app.state.repo.db.execute(
        "SELECT origin_x,origin_y FROM entity WHERE id=?", (entity["id"],)
    ).fetchone()
    assert list(stored) == entity["origin"]


def test_scripted_performance_repeats_after_pause_from_fixed_origin():
    w = make_world()
    w.config.vlm["generation"]["loop_pause_ms"] = 1000
    p = compile_plan(Generation.model_validate(performance()), 100, 80)
    e = w.add(
        row("doodle", 500, 500, mode="scripted_visual", plan=p, origin=[500, 500])
    )
    run(w, 2)
    assert w.pos(e) == (600, 500)
    run(w, 0.8)
    assert w.pos(e) == (600, 500)
    assert e.data["playback"]["elapsed"] == 2800
    assert e.data["view"]["playback"]["running"]
    assert e.data["view"]["playback"]["pauseMs"] == 1000
    run(w, 0.4)
    assert e.data["playback"]["elapsed"] == 3200
    assert w.pos(e) == (510, 500)
    run(w, 6)
    assert w.pos(e) == (510, 500)


def test_freeze_sleep_and_recovery_preserve_inter_cycle_pause():
    w = make_world()
    w.config.vlm["generation"]["loop_pause_ms"] = 1000
    p = compile_plan(Generation.model_validate(performance()), 100, 80)
    e = w.add(
        row("doodle", 500, 500, mode="scripted_visual", plan=p, origin=[500, 500])
    )
    run(w, 2.5)
    w.set_frozen(True)
    run(w, 10)
    assert e.data["playback"]["elapsed"] == 2500
    w.set_frozen(False)
    w.set_subscribed((0, 0), False)
    run(w, 10)
    assert e.data["playback"]["elapsed"] == 2500
    saved = w.snapshot_row(e)
    restored = make_world()
    restored.config.vlm["generation"]["loop_pause_ms"] = 1000
    recovered = restored.add(
        row(
            "doodle",
            saved["x"],
            saved["y"],
            mode="scripted_visual",
            plan=p,
            origin=[500, 500],
            data=saved["data"],
        )
    )
    run(restored, 0.4)
    assert restored.pos(recovered) == (600, 500)
    run(restored, 0.2)
    assert restored.pos(recovered) == (505, 500)


def test_previously_completed_visuals_resume_with_repetition():
    w = make_world()
    w.config.vlm["generation"]["loop_pause_ms"] = 1000
    p = compile_plan(Generation.model_validate(performance()), 100, 80)
    e = w.add(
        row(
            "doodle",
            600,
            500,
            mode="scripted_visual",
            plan=p,
            origin=[500, 500],
            data={"playback": {"elapsed": 2000, "revision": 12}},
        )
    )
    run(w, 1.2)
    assert e.data["playback"]["elapsed"] == 3200
    assert w.pos(e) == (510, 500)
