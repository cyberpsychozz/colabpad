"""T14: what a sleeping chunk missed is applied when it wakes, without moving anything."""

import pytest

from tests.simkit import events, make_world, row, run

CHUNK = (5, 5)


def asleep(seed: int, seconds: float, catchup: bool = True, **cow: float):
    """A cow and a grazed pasture left alone for a while, then looked at again."""
    world = make_world(seed=seed, subscribe=None)
    world.config.flags.sleep_catchup = catchup
    world.set_subscribed(CHUNK, True)
    animal = world.add(row("creature", 5600, 5600, **cow))
    pasture = world.add(row("plant", 5800, 5800, variant="pasture"))
    pasture.data["stock"] = 20
    world.set_subscribed(CHUNK, False)
    quiet = run(world, seconds, dt=1.0)
    assert [m for m in quiet if m["op"] in ("delta", "event")] == []  # frozen while asleep
    world.set_subscribed(CHUNK, True)
    return world, animal, pasture, world.drain()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_needs_and_regrowth_catch_up_the_same_for_any_seed(seed: int) -> None:
    world, cow, pasture, _ = asleep(seed, 120)
    assert world.pos(cow) == (5600.0, 5600.0)
    assert world.hunger[cow.i] == pytest.approx(20.0)        # 120 s of a 10-minute scale
    assert world.thirst[cow.i] == pytest.approx(120 / 420 * 100)
    assert world.energy[cow.i] == pytest.approx(100 - 120 / 900 * 100)
    assert world.hp[cow.i] == 100.0
    assert pasture.data["stock"] == 100                      # +1/s from 20, capped
    assert cow.dirty and pasture.dirty


def test_catch_up_is_capped_at_ten_minutes() -> None:
    world, cow, _, _ = asleep(1, 3600, hp=1000)
    # 600 s, not 3600: hunger just reaches 100, thirst was full for the last 180 s.
    assert world.hunger[cow.i] == pytest.approx(100.0)
    assert world.energy[cow.i] == pytest.approx(100 - 600 / 900 * 100)
    assert world.hp[cow.i] == pytest.approx(1000 - 2 * 180)


def test_an_animal_left_too_long_becomes_a_fossil_where_it_stood() -> None:
    world, cow, _, out = asleep(1, 3600)
    assert cow.id not in world.entities
    death = [m for m in out if m["op"] == "death"]
    assert len(death) == 1 and death[0]["fossil"] and death[0]["cause"] == "neglect"
    assert (death[0]["id"], death[0]["x"], death[0]["y"]) == (cow.id, 5600.0, 5600.0)
    assert len(events(out, "death")) == 1


def test_no_catch_up_when_the_flag_is_off() -> None:
    world, cow, pasture, _ = asleep(1, 120, catchup=False)
    assert (world.hunger[cow.i], world.thirst[cow.i], world.energy[cow.i]) == (0.0, 0.0, 100.0)
    assert pasture.data["stock"] == 20
