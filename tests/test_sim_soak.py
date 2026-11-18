"""T13: thirty simulated minutes of a well-fed herd. Animals breed, and the caps hold."""

from collections import Counter

from tests.simkit import make_world, row
from tests.test_sim_types import cow, lake

CAPS = {"label": 8, "chunk": 60}


def test_population_caps_hold_in_a_thirty_minute_soak() -> None:
    world = make_world(seed=7)
    lake(world)
    for x, y in ((250, 250), (250, 600), (850, 700), (450, 800)):
        world.add(row("plant", x, y, variant="pasture", w=220, h=160, sizeClass="L"))
    world.add(row("house", 200, 800, variant="barn"))
    herd = [cow(world, 300 + 40 * n, 300) for n in range(4)]
    sheep = {"ru": "овца", "en": "sheep"}
    flock = [cow(world, 300 + 40 * n, 650, label=sheep) for n in range(4)]
    births, peak = 0, Counter()
    for tick in range(30 * 60 * 10):
        world.tick(0.1)
        for message in world.drain():
            if message["op"] != "born":
                continue
            # Checked in the very tick of the birth: it never takes its chunk over a cap.
            births += 1
            baby = message["entity"]
            chunk = world.chunk_of(baby["x"], baby["y"])
            here = [e for e in world.entities.values() if world.chunk_of(*world.pos(e)) == chunk]
            same = [e for e in here if e.label["en"] == baby["label"]["en"]]
            assert len(same) <= CAPS["label"] and len(here) <= CAPS["chunk"], (tick, baby["label"], len(same), len(here))
        if tick % 100 == 0:
            for label, count in Counter(e.label["en"] for e in world.entities.values() if e.type == "creature").items():
                peak[label] = max(peak[label], count)
    assert births >= 4, "a fed herd with water and a barn should breed"
    # Both herds grew, and neither ran away: nobody strays far from one pasture.
    assert peak["cow"] > len(herd) and peak["sheep"] > len(flock)
    assert peak["cow"] <= 2 * CAPS["label"] and peak["sheep"] <= 2 * CAPS["label"], peak
    assert len(world.entities) <= 2 * CAPS["chunk"]
