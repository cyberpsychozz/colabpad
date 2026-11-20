import json
from pathlib import Path
from typing import Any

from app.repo.canvases import now_ms
from app.retention import DAY_S, Retention
from app.sim.bridge import actors_of
from tests.test_alive import alive, make, wait  # noqa: F401  (make is a fixture)
from tests.test_sim_bridge import on_loop

DAY_MS = DAY_S * 1000


def test_event_actors_name_every_entity_taking_part() -> None:
    assert actors_of({"id": "a", "by": "b", "target": "c"}) == ["a", "b", "c"]
    assert actors_of({"id": "baby", "parents": ["p1", "p2"]}) == ["baby", "p1", "p2"]
    assert actors_of({"id": "f", "fish": None, "food": "x"}) == ["f", "x"]


def test_my_events_lists_what_happened_to_my_entities(make: Any) -> None:
    world, client = make()
    world.draw()
    entity_id = wait(client, alive(client).json()["jobId"])["entityId"]
    sim = world.app.state.sim
    base = {"op": "event", "canvas": "world", "cx": 0, "cy": 0, "tick": 1}
    on_loop(client, sim.handle([
        {**base, "type": "hit", "id": entity_id, "by": "wolf-1", "x": 1, "y": 1},
        {**base, "type": "eat", "id": "wolf-2", "target": entity_id, "x": 1, "y": 1},
        {**base, "type": "drink", "id": "someone-else", "x": 1, "y": 1},
    ]))  # fmt: skip
    sim.flush_events()
    body = client.get("/api/me/events").json()
    assert [event["type"] for event in body["events"]] == ["hit", "eat"]
    assert body["events"][0]["entity"]["id"] == entity_id
    assert body["events"][1]["payload"]["target"] == entity_id
    # Paging: nothing newer than the last one.
    assert client.get(f"/api/me/events?since={body['next']}").json()["events"] == []
    # Another visitor sees none of it.
    client.cookies.clear()
    assert client.get("/api/me/events").json()["events"] == []


def test_retention_keeps_strokes_and_drops_old_jobs_events_tokens_and_dead_sprites(
    make: Any, tmp_path: Path
) -> None:
    world, client = make()
    seqs = world.draw()
    entity_id = wait(client, alive(client).json()["jobId"])["entityId"]
    app = world.app
    repo, sprites = app.state.repo, app.state.settings.sprites_dir
    sprite_id = repo.db.execute("SELECT sprite_id FROM entity WHERE id=?", (entity_id,)).fetchone()[0]
    assert (sprites / sprite_id / "sheet.png").is_file()
    now = now_ms()
    repo.db.execute(
        "INSERT INTO world_event(canvas_id,tick,at,type,actors,payload) VALUES"
        " ('world',1,?,'old','[]','{}'), ('world',2,?,'new','[]','{}')",
        (now - 91 * DAY_MS, now - 89 * DAY_MS),
    )
    repo.store_login_code("expired", "a@example.test", "ru", "123456", now - 1)
    repo.store_login_code("fresh", "b@example.test", "ru", "654321", now + 60_000)

    def retention(days_later: float) -> Retention:
        return Retention(lambda: repo, sprites, app.state.config.limits["retention_days"],
                         clock=lambda: now / 1000 + days_later * DAY_S)  # fmt: skip

    counts = on_loop(client, retention(0).run_once())
    assert counts["world_event"] == 1 and counts["sprites"] == 0 and counts["alive_job"] == 0
    codes = {row[0] for row in repo.db.execute("SELECT session_id FROM login_code")}
    assert codes == {"fresh"}

    # The ALIVE job goes after 30 days; a living entity keeps its sprite however old.
    counts = on_loop(client, retention(40).run_once())
    assert counts["alive_job"] == 1 and counts["sprites"] == 0
    # Once its entity has been dead for 7 days the sprite goes.
    repo.db.execute("UPDATE entity SET died_at=? WHERE id=?", (now, entity_id))
    assert on_loop(client, retention(6).run_once())["sprites"] == 0
    counts = on_loop(client, retention(8).run_once())
    assert counts["sprites"] == 1 and not (sprites / sprite_id).exists()
    assert repo.db.execute("SELECT sheet_path FROM sprite WHERE id=?", (sprite_id,)).fetchone()[0] is None
    # Built-in sprites (fruit, flames) are never expired.
    assert any(path.is_dir() for path in sprites.iterdir())
    # Strokes and their fates stay forever.
    stored = [row[0] for row in repo.db.execute("SELECT seq FROM stroke ORDER BY seq")]
    assert stored == seqs
    fates = repo.db.execute("SELECT count(*) FROM stroke_fate WHERE fate='consumed'").fetchone()[0]
    assert fates == len(seqs)
