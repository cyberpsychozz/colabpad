import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, create_asgi
from app.sim.bridge import reaches
from tests.conftest import stroke_in
from tests.test_alive import World, alive, make, wait  # noqa: F401  (make is a fixture)


def on_loop(client: TestClient, coroutine: Any) -> Any:
    """Run a coroutine on the app's event loop."""
    return client.portal.call(lambda: coroutine)  # type: ignore[union-attr]


async def step(world: World, seconds: float) -> None:
    await world.app.state.sim.step(0.1, round(seconds / 0.1))


def test_alive_entities_enter_the_world_and_their_state_is_persisted(make: Any) -> None:
    world, client = make()
    world.draw()
    sim = world.app.state.sim
    world.app.state.realtime.rooms.set_viewport("viewer", "world", frozenset({(0, 0)}))
    status = wait(client, alive(client).json()["jobId"])
    entity_id = status["entityId"]
    core = sim.transport.core
    assert entity_id in core.worlds["world"].entities and entity_id in sim.live
    # Drag it (a doodle would allow it; a creature can be pushed) and let the world tick.
    sim.send({"op": "interact", "canvas": "world", "id": entity_id, "action": "push", "to": [100, 200]})
    world.events.clear()
    on_loop(client, step(world, 5.2))
    deltas = [data for name, data, rooms in world.events if name == "world:delta"]
    # The shove moved it 40 px away from the cursor; after that it lives its own life.
    assert deltas and deltas[0]["upd"][0][0] == entity_id and deltas[0]["upd"][0][1] > 235
    moved_x = sim.live[entity_id]["x"]
    assert moved_x == round(float(core.worlds["world"].x[core.worlds["world"].entities[entity_id].i]), 1)
    # Chunk snapshots come from live state, and the database got the 5-second snapshot.
    chunk = client.get("/api/canvases/world/chunks/0/0").json()
    assert chunk["entities"][0]["x"] == moved_x
    stored = world.app.state.repo.db.execute("SELECT x FROM entity WHERE id=?", (entity_id,)).fetchone()
    assert abs(stored["x"] - moved_x) < 12  # the snapshot is at most a few ticks old


def test_death_makes_a_fossil_and_explosions_destroy_static_strokes(make: Any) -> None:
    world, client = make()
    seqs = world.draw()
    repo, sim = world.app.state.repo, world.app.state.sim
    bystander = repo.insert_stroke(stroke_in(points=[[400, 400, 0.5], [450, 400, 0.5]]), (0, 0))[0]["seq"]
    far = repo.insert_stroke(stroke_in(points=[[900, 900, 0.5], [950, 900, 0.5]]), (0, 0))[0]["seq"]
    entity_id = wait(client, alive(client).json()["jobId"])["entityId"]
    world.events.clear()
    on_loop(client, sim.handle([
        {"op": "explosion", "canvas": "world", "x": 420, "y": 380, "r": 100},
        {"op": "death", "canvas": "world", "id": entity_id, "x": 260, "y": 230, "fossil": True, "cause": "test"},
    ]))
    fates = {(f["seq"], f["fate"]) for f in client.get("/api/canvases/world/chunks/0/0").json()["fates"]}
    # The blast destroyed the static stroke it reached; living strokes and distant ones are untouched.
    assert (bystander, "destroyed") in fates and not any(seq == far for seq, _ in fates)
    assert {(seq, "fossil") for seq in seqs} <= fates
    names = [name for name, _, _ in world.events]
    assert names.count("stroke:fate") == 2 and "world:event" in names
    fossil = next(data for name, data, _ in world.events if name == "world:event")["fossil"]
    assert fossil["entityId"] == entity_id and len(fossil["strokes"]) == 3
    assert abs(fossil["dx"] - 60) < 3 and abs(fossil["dy"] - 30) < 3
    chunk = client.get("/api/canvases/world/chunks/0/0").json()
    assert chunk["entities"] == [] and chunk["fossils"][0]["entityId"] == entity_id
    row = repo.db.execute("SELECT died_at, death_x FROM entity WHERE id=?", (entity_id,)).fetchone()
    assert row["died_at"] and row["death_x"] == 260
    # Stroke rows were never touched.
    assert repo.db.execute("SELECT count(*) FROM stroke").fetchone()[0] == 5
    assert reaches([[0, 0, 0.5], [100, 0, 0.5]], 50, 30, 40) and not reaches([[0, 0, 0.5], [100, 0, 0.5]], 50, 60, 40)


def test_a_killed_simulation_process_restarts_and_the_world_resumes(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, sim_mode="process")
    app = create_app(settings)
    with TestClient(create_asgi(app)) as client:
        repo, sim = app.state.repo, app.state.sim
        repo.insert_sprite("s1", None, "s1/cutout.png", "s1/sheet.png", {})
        repo.insert_entity({
            "id": "e1", "canvas_id": "world", "sprite_id": "s1", "archetype": "food",
            "label": {"ru": "каракуля", "en": "doodle"}, "x": 300.0, "y": 300.0, "facing": "right",
            "state": "idle", "hp": 50.0, "hunger": 0.0, "thirst": 0.0, "energy": 100.0,
            "params": {"sizeClass": "M", "w": 40, "h": 40}, "origin_x": 300.0, "origin_y": 300.0,
        })  # fmt: skip
        sim.spawn("e1")
        app.state.realtime.rooms.set_viewport("viewer", "world", frozenset({(0, 0)}))

        def pushed_to(x: float) -> bool:
            deadline = time.time() + 10
            while time.time() < deadline:
                if abs(sim.live["e1"]["x"] - x) < 1:
                    return True
                time.sleep(0.05)
            return False

        sim.send({"op": "interact", "canvas": "world", "id": "e1", "action": "drag", "to": [500, 300]})
        assert pushed_to(500)
        # Wait for a snapshot so the database has the new position, then kill the process.
        deadline = time.time() + 10
        while repo.db.execute("SELECT x FROM entity WHERE id='e1'").fetchone()[0] != 500 and time.time() < deadline:
            time.sleep(0.1)
        first = sim.transport.process
        first.kill()
        deadline = time.time() + 10
        while (sim.restarts == 0 or not sim.transport.alive()) and time.time() < deadline:
            time.sleep(0.05)
        assert sim.restarts == 1 and sim.transport.process is not first
        # The world came back from the database and still answers.
        assert sim.live["e1"]["x"] == 500
        sim.send({"op": "interact", "canvas": "world", "id": "e1", "action": "drag", "to": [700, 300]})
        assert pushed_to(700)
        assert client.get("/api/health").json() == {"ok": True}


def test_things_the_world_makes_are_stored_and_shown_with_builtin_sprites(make: Any) -> None:
    world, client = make()
    sim, repo = world.app.state.sim, world.app.state.repo
    core = sim.transport.core.worlds["world"]
    world.app.state.realtime.rooms.set_viewport("viewer", "world", frozenset({(0, 0)}))
    tree_id = "tree-1"
    repo.insert_sprite("s-tree", None, "s-tree/cutout.png", "s-tree/sheet.png", {})
    repo.insert_entity({
        "id": tree_id, "canvas_id": "world", "sprite_id": "s-tree", "archetype": "plant",
        "label": {"ru": "яблоня", "en": "apple tree"}, "x": 400.0, "y": 300.0, "facing": "right",
        "state": "idle", "hp": 100.0, "hunger": 0.0, "thirst": 0.0, "energy": 100.0,
        "params": {"sizeClass": "L", "variant": "fruit_tree", "w": 100, "h": 160}, "origin_x": 400.0, "origin_y": 300.0,
    })  # fmt: skip
    sim.spawn(tree_id)
    core.entities[tree_id].data["fruit_in"] = 0.5
    world.events.clear()
    on_loop(client, step(world, 2))
    spawned = [data for name, data, _ in world.events if name == "world:event" and data.get("type") == "spawn"]
    assert len(spawned) == 1 and spawned[0]["parent"] == tree_id
    apple = spawned[0]["entity"]
    assert apple["type"] == "food" and apple["label"] == {"ru": "яблоко", "en": "apple"}
    assert apple["sprite"]["sheet"] == "/sprites/builtin-apple/sheet.png"
    assert client.get(apple["sprite"]["sheet"]).status_code == 200
    assert apple["sprite"]["manifest"]["anims"]["idle"]["frames"] == 4
    stored = repo.db.execute("SELECT archetype, parent_id FROM entity WHERE id=?", (apple["id"],)).fetchone()
    assert tuple(stored) == ("food", tree_id)
    assert {e["id"] for e in client.get("/api/canvases/world/chunks/0/0").json()["entities"]} == {tree_id, apple["id"]}
    # The fruit event itself reached the room and was logged.
    assert any(data.get("type") == "fruit" for name, data, _ in world.events if name == "world:event")
    sim.flush_events()
    assert repo.db.execute("SELECT count(*) FROM world_event WHERE type='fruit'").fetchone()[0] == 1
