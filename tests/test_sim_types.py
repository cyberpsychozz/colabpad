"""One test per specific (●) cell of the interaction matrix in docs/spec.md §3.

Test names are `test_<who>_on_<whom>`.
"""

from typing import Any

from app.sim.types import base
from app.sim.types.explosive import blast_radius
from tests.simkit import events, make_world, ops, row, run

LAKE = [[600 + 150 * c, 400 + 100 * s] for c, s in [(1, 0), (0.7, 0.7), (0, 1), (-0.7, 0.7), (-1, 0), (-0.7, -0.7), (0, -1), (0.7, -0.7)]]


def lake(world: Any, size: str = "L") -> Any:
    return world.add(row("water", 600, 400, polygon=LAKE, sizeClass=size, w=300, h=200))


def cow(world: Any, x: float, y: float, **extra: Any) -> Any:
    return world.add(row("creature", x, y, **{"locomotion": "walk", "diet": "plants", "label": {"ru": "корова", "en": "cow"}, **extra}))


def wolf(world: Any, x: float, y: float, **extra: Any) -> Any:
    return world.add(row("predator", x, y, locomotion="walk", **{"label": {"ru": "волк", "en": "wolf"}, **extra}))


def person(world: Any, x: float, y: float, role: str = "villager", **extra: Any) -> Any:
    return world.add(row("person", x, y, locomotion="walk", role=role, **extra))


def hp(world: Any, entity: Any) -> float:
    return float(world.hp[entity.i])


def alive(world: Any, entity: Any) -> bool:
    return entity.id in world.entities


# ---- Creature

def test_creature_on_creature_two_fed_cows_have_a_calf() -> None:
    world = make_world()
    a, b = cow(world, 300, 300), cow(world, 330, 300)
    out = run(world, 20)
    births = events(out, "birth")
    assert len(births) == 1 and set(births[0]["parents"]) == {a.id, b.id}
    calf = ops(out, "born")[0]["entity"]
    # A smaller copy of the same drawing.
    assert calf["type"] == "creature" and calf["label"]["en"] == "cow" and calf["parent_id"] in (a.id, b.id)
    assert calf["data"]["view"]["scale"] == 0.6
    # A three-minute cooldown: no second calf right away, and the calf is too young.
    assert events(run(world, 60), "birth") == []
    # Hungry cows do not breed, nor do different animals.
    world = make_world()
    cow(world, 300, 300, hunger=60); cow(world, 330, 300)
    cow(world, 900, 300, label={"ru": "овца", "en": "sheep"}); cow(world, 930, 300)
    assert events(run(world, 20), "birth") == []


def test_creature_on_predator_flees() -> None:
    world = make_world()
    prey, hunter = cow(world, 300, 300), wolf(world, 400, 300, hunger=0)
    run(world, 1)
    assert prey.state == "flee" and world.pos(prey)[0] < 300
    run(world, 3)
    assert world.dist(prey, hunter) > 150


def test_creature_on_food_eats_it_in_three_bites() -> None:
    world = make_world()
    eater = cow(world, 200, 200, hunger=80)
    apple = world.add(row("food", 300, 200, label={"ru": "яблоко", "en": "apple"}))
    out = run(world, 4)
    assert eater.state == "eat" and apple.data["view"]["bites"] in (1, 2)  # the sprite shrinks
    out += run(world, 8)
    assert len(events(out, "eat")) == 3 and not alive(world, apple)
    assert 45 < world.hunger[eater.i] < 55  # 80, minus an apple's 30, plus a little time
    assert [d["fossil"] for d in ops(out, "death")] == [False]
    # A herbivore ignores meat.
    world = make_world()
    eater = cow(world, 200, 200, hunger=80)
    meat = world.add(row("food", 300, 200, label={"ru": "мясо", "en": "meat"}))
    run(world, 12)
    assert alive(world, meat)


def test_creature_on_plant_grazes_a_pasture_which_regrows() -> None:
    world = make_world()
    eater = cow(world, 200, 200, hunger=90)
    field = world.add(row("plant", 300, 200, variant="pasture", w=120, h=60))
    run(world, 8)
    assert field.data["stock"] < 100 and world.hunger[eater.i] < 75
    stock = field.data["stock"]
    world.hunger[eater.i] = 0
    run(world, 10)
    assert stock + 8 < field.data["stock"] <= 100  # +1 per second
    world.kill(eater, fossil=False)
    field.data["stock"] = 0
    run(world, 150)
    assert field.data["stock"] == 100


def test_creature_on_water_drinks() -> None:
    world = make_world()
    thirsty = cow(world, 300, 400, thirst=90)
    lake(world)
    out = run(world, 10)
    assert events(out, "drink") and world.thirst[thirsty.i] < 10
    # With no water it dies of thirst and leaves a fossil where it died.
    world = make_world()
    doomed = cow(world, 300, 400, thirst=100)
    out = run(world, 55)
    death = ops(out, "death")[0]
    assert death["id"] == doomed.id and death["fossil"] is True


def test_creature_on_house_goes_to_the_barn_at_night() -> None:
    world = make_world(day_phase=0.7)
    sleepy = cow(world, 300, 300)
    barn = world.add(row("house", 500, 300, w=120, h=100, label={"ru": "сарай", "en": "barn"}))
    run(world, 8)
    assert sleepy.state == "rest" and world.dist(sleepy, barn) < 60
    # Resting restores energy.
    world.energy[sleepy.i] = 30
    run(world, 30)
    assert world.energy[sleepy.i] > 35


def test_creature_on_explosive_flees_a_lit_fuse() -> None:
    world = make_world()
    scared = cow(world, 300, 300)
    world.add(row("explosive", 380, 300))
    run(world, 1)
    assert scared.state == "flee" and world.pos(scared)[0] < 290
    run(world, 3)
    assert world.pos(scared)[0] < 235  # out of the flee radius


def test_creature_on_fire_flees_wild_fire_but_not_a_campfire() -> None:
    world = make_world()
    scared = cow(world, 300, 300)
    world.add(row("fire", 360, 300))
    run(world, 0.5)
    assert scared.state == "flee" and world.pos(scared)[0] < 295
    world = make_world()
    calm = cow(world, 300, 300)
    world.add(row("fire", 360, 300, variant="campfire"))
    run(world, 1.5)
    assert calm.state != "flee"


# ---- Predator

def test_predator_on_creature_hunts_kills_and_eats() -> None:
    world = make_world()
    prey, hunter = cow(world, 300, 300, sizeClass="L"), wolf(world, 420, 300, hunger=60)
    out = run(world, 20)
    death = ops(out, "death")[0]
    assert death["id"] == prey.id and death["fossil"] is True and death["cause"] == "killed"
    assert events(out, "hit") and world.hunger[hunter.i] < 30  # it ate its kill
    # A fed predator leaves creatures alone.
    world = make_world()
    safe, _ = cow(world, 300, 300, sizeClass="L"), wolf(world, 600, 300, hunger=0)
    run(world, 5)
    assert hp(world, safe) == 100


def test_predator_on_predator_wolves_breed() -> None:
    world = make_world()
    wolf(world, 300, 300, hunger=0); wolf(world, 330, 300, hunger=0)
    assert len(events(run(world, 20), "birth")) == 1


def test_predator_on_aquatic_takes_fish_at_the_shore_only() -> None:
    world = make_world()
    lake(world)
    deep = world.add(row("aquatic", 600, 400, locomotion="swim"))
    hunter = wolf(world, 380, 400, hunger=70)
    brain = __import__("app.sim.types.predator", fromlist=["Predator"]).Predator()
    assert not brain.is_prey(world, deep)
    shore = world.add(row("aquatic", 470, 400, locomotion="swim"))
    assert brain.is_prey(world, shore)
    world.base_speed[shore.i] = world.base_speed[deep.i] = 0  # too slow to get away
    out = run(world, 12)
    hit = {e["id"] for e in events(out, "hit")}
    assert hit == {shore.id} and hp(world, deep) == 60
    assert world.hunger[hunter.i] < 30 or not alive(world, shore)


def test_predator_on_person_hunts_people_and_fights_warriors() -> None:
    world = make_world()
    victim, hunter = person(world, 300, 300, sizeClass="L"), wolf(world, 400, 300, hunger=60)
    run(world, 1)
    assert victim.state == "flee" and hunter.state in ("hunt", "attack")
    # A warrior does not run: he attacks, and the wolf turns on him.
    world = make_world()
    fighter, hunter = person(world, 300, 300, "warrior"), wolf(world, 420, 300, hunger=0)
    out = run(world, 6)
    hit = {e["id"] for e in events(out, "hit")}
    assert hunter.id in hit and fighter.id in hit
    # The warrior wins, narrowly.
    out += run(world, 6)
    assert not alive(world, hunter) and alive(world, fighter)
    assert any(d["id"] == hunter.id and d["fossil"] for d in ops(out, "death"))


def test_predator_on_food_eats_meat_not_apples() -> None:
    world = make_world()
    hunter = wolf(world, 200, 200, hunger=80)
    apple = world.add(row("food", 260, 200, label={"ru": "яблоко", "en": "apple"}))
    meat = world.add(row("food", 330, 200, label={"ru": "мясо", "en": "meat"}))
    run(world, 14)
    assert alive(world, apple) and not alive(world, meat)
    assert world.hunger[hunter.i] < 45  # meat is worth 50


def test_predator_on_water_drinks() -> None:
    world = make_world()
    thirsty = wolf(world, 300, 400, thirst=90, hunger=0)
    lake(world)
    assert events(run(world, 10), "drink") and world.thirst[thirsty.i] < 10


def test_predator_on_explosive_and_fire_flees_both() -> None:
    for threat in ("explosive", "fire"):
        world = make_world()
        hunter = wolf(world, 300, 300, hunger=0)
        world.add(row(threat, 370, 300))
        run(world, 0.5)
        assert hunter.state == "flee" and world.pos(hunter)[0] < 295, threat


# ---- Aquatic

def test_water_on_aquatic_fish_stay_in_water_and_die_outside_it() -> None:
    world = make_world()
    lake(world)
    fish = world.add(row("aquatic", 600, 400, locomotion="swim"))
    for _ in range(60):
        run(world, 1)
        assert base.water_at(world, *world.pos(fish)) is not None
    assert hp(world, fish) == 60
    # On dry land it loses 10 HP a second.
    world = make_world()
    stranded = world.add(row("aquatic", 1500, 1500, locomotion="swim"))
    run(world, 3)
    assert 25 <= hp(world, stranded) <= 35
    out = run(world, 4)
    assert ops(out, "death")[0]["cause"] == "dried"


def test_aquatic_on_water_a_stranded_fish_flops_back() -> None:
    world = make_world()
    lake(world)
    fish = world.add(row("aquatic", 430, 400, locomotion="swim"))  # just outside the shore
    run(world, 3)
    assert base.water_at(world, *world.pos(fish)) is not None and alive(world, fish)


def test_aquatic_on_predator_flees_into_the_water() -> None:
    world = make_world()
    lake(world)
    fish = world.add(row("aquatic", 480, 400, locomotion="swim"))
    wolf(world, 420, 400, hunger=0)
    run(world, 3)
    assert world.pos(fish)[0] > 500 and base.water_at(world, *world.pos(fish)) is not None


def test_aquatic_on_aquatic_fish_breed() -> None:
    world = make_world()
    lake(world)
    for x in (580, 610):
        world.add(row("aquatic", x, 400, locomotion="swim", label={"ru": "рыба", "en": "fish"}))
    assert len(events(run(world, 30), "birth")) == 1


def test_aquatic_on_food_eats_only_what_is_in_the_water() -> None:
    world = make_world()
    lake(world)
    fish = world.add(row("aquatic", 600, 400, locomotion="swim", hunger=80, diet="omni"))
    dry = world.add(row("food", 300, 400))
    wet = world.add(row("food", 640, 420))
    run(world, 14)
    assert alive(world, dry) and not alive(world, wet) and world.hunger[fish.i] < 60


# ---- Person

def test_person_on_creature_a_medic_heals_animals() -> None:
    world = make_world()
    medic = person(world, 300, 300, "medic")
    hurt = cow(world, 420, 300, hp=40)
    world.go(hurt, 420, 300)
    run(world, 12)
    assert medic.state == "heal" or hp(world, hurt) > 40
    assert hp(world, hurt) >= 55  # +5 HP a second once he is beside it


def test_person_on_predator_a_warrior_attacks_within_200px() -> None:
    world = make_world()
    fighter, far = person(world, 300, 300, "warrior"), wolf(world, 560, 300, hunger=0)
    run(world, 2)
    assert fighter.state not in ("hunt", "attack") and hp(world, far) == 150
    world = make_world()
    fighter, near = person(world, 300, 300, "warrior"), wolf(world, 460, 300, hunger=0)
    run(world, 6)
    assert hp(world, near) < 150


def test_person_on_aquatic_a_fisherman_catches_fish_and_wild_fish() -> None:
    world = make_world()
    lake(world)
    fisher = person(world, 380, 400, "fisherman")
    fish = world.add(row("aquatic", 520, 400, locomotion="swim", label={"ru": "карп", "en": "carp"}))
    out = run(world, 45)
    caught = events(out, "catch")[0]
    assert caught["fish"] == fish.id and not alive(world, fish)
    food = next(m["entity"] for m in ops(out, "born") if m["entity"]["id"] == caught["food"])
    # The caught fish becomes food "fish", keeping its own drawing.
    assert food["type"] == "food" and food["label"]["en"] == "fish" and food["parent_id"] == fish.id
    assert world.dist(fisher, world.entities[caught["food"]]) < 40 or not alive(world, world.entities.get(caught["food"]) or fisher)
    # With no fish in the lake, a wild fish bites once a minute.
    world = make_world()
    lake(world)
    person(world, 380, 400, "fisherman", hunger=0)
    out = run(world, 75)
    wild = [e for e in events(out, "catch") if e["fish"] is None]
    assert len(wild) == 1
    assert next(m["entity"] for m in ops(out, "born"))["sprite_id"] == "builtin-fish"


def test_person_on_person_people_breed_only_near_a_house_and_medics_heal() -> None:
    world = make_world()
    person(world, 300, 300); person(world, 330, 300)
    assert events(run(world, 30), "birth") == []
    world.add(row("house", 320, 380, w=120, h=100))
    assert len(events(run(world, 30), "birth")) == 1
    world = make_world()
    person(world, 300, 300, "medic")
    hurt = person(world, 340, 300, hp=30)
    run(world, 10)
    assert hp(world, hurt) > 50


def test_person_on_food_eats() -> None:
    world = make_world()
    eater = person(world, 200, 200, hunger=80)
    bread = world.add(row("food", 280, 200, label={"ru": "хлеб", "en": "bread"}))
    run(world, 12)
    assert not alive(world, bread) and world.hunger[eater.i] < 60


def test_person_on_plant_a_hungry_person_shakes_a_fruit_tree() -> None:
    world = make_world()
    hungry = person(world, 200, 300, hunger=80)
    tree = world.add(row("plant", 330, 300, variant="fruit_tree", w=100, h=160))
    tree.data["fruit_in"] = 500
    out = run(world, 25)
    assert events(out, "shake") and events(out, "fruit")
    assert events(out, "eat") and world.hunger[hungry.i] < 80


def test_person_on_water_drinks() -> None:
    world = make_world()
    thirsty = person(world, 300, 400, thirst=90)
    lake(world)
    assert events(run(world, 10), "drink") and world.thirst[thirsty.i] < 10


def test_person_on_house_sleeps_in_a_house_at_night() -> None:
    world = make_world(day_phase=0.8)
    villager = person(world, 300, 300)
    home = world.add(row("house", 520, 300, w=120, h=100))
    run(world, 10)
    assert villager.state == "rest" and world.dist(villager, home) < 60
    world._day_phase = 0.2
    world.energy[villager.i] = 100
    run(world, 3)
    assert villager.state != "rest"  # up in the morning


def test_person_on_explosive_and_fire_flees_them_but_warms_by_a_campfire() -> None:
    for threat in ("explosive", "fire"):
        world = make_world()
        villager = person(world, 300, 300)
        world.add(row(threat, 370, 300))
        run(world, 0.5)
        assert villager.state == "flee", threat
    world = make_world()
    villager = person(world, 300, 300)
    world.add(row("fire", 345, 300, variant="campfire"))
    run(world, 5)
    assert villager.state != "flee" and hp(world, villager) == 100


def test_person_on_sky_and_sky_on_person_rain_sends_people_indoors() -> None:
    world = make_world()
    villager = person(world, 300, 300)
    home = world.add(row("house", 480, 300, w=120, h=100))
    cloud = world.add(row("sky", 320, 150, variant="cloud", w=200, h=80))
    cloud.data["rain_in"] = 0.5
    out = run(world, 10)
    assert events(out, "rain") and base.raining_at(world, 300, 300)
    assert villager.state == "rest" and world.dist(villager, home) < 60
    # It rains for 30 seconds in every two minutes.
    out = run(world, 30)
    assert events(out, "rain_stop")
    run(world, 5)
    assert villager.state != "rest"
    assert not events(run(world, 70), "rain") and events(run(world, 25), "rain")


# ---- Plant, Water, House

def test_plant_on_food_a_fruit_tree_drops_fruit_up_to_three() -> None:
    world = make_world()
    tree = world.add(row("plant", 400, 300, variant="fruit_tree", w=100, h=160))
    out = run(world, 400)
    fruit = events(out, "fruit")
    # One every 60-90 s, but never more than three lying around it.
    assert len(fruit) == 3 and len(world.of_type("food")) == 3
    born = ops(out, "born")[0]["entity"]
    assert born["type"] == "food" and born["sprite_id"] == "builtin-apple" and born["label"]["en"] == "apple"
    assert all(world.dist(tree, apple) <= 110 for apple in world.of_type("food"))
    world.kill(world.of_type("food")[0], fossil=False)
    assert len(events(run(world, 100), "fruit")) == 1


def test_water_on_plant_and_sky_on_plant_growth_is_faster_when_wet_or_sunny() -> None:
    def regrown(setup: Any) -> float:
        world = make_world()
        field = world.add(row("plant", 300, 400, variant="pasture", w=120, h=60))
        field.data["stock"] = 0
        setup(world)
        run(world, 20)
        return field.data["stock"]

    dry = regrown(lambda world: None)
    by_water = regrown(lake)
    sunny = regrown(lambda world: world.add(row("sky", 300, 100, variant="sun")))

    def rainy(world: Any) -> None:
        cloud = world.add(row("sky", 300, 300, variant="cloud", w=200, h=80))
        cloud.data.update(rain_in=30)
        cloud.data["view"]["raining"] = True

    wet = regrown(rainy)
    assert 19 <= dry <= 21 and 28 <= by_water <= 31 and 28 <= sunny <= 31 and 38 <= wet <= 41


def test_water_on_vehicle_cars_stay_out_and_boats_stay_in() -> None:
    world = make_world()
    lake(world)
    car = world.add(row("vehicle", 380, 400, variant="car"))
    boat = world.add(row("vehicle", 600, 400, variant="boat"))
    plane = world.add(row("vehicle", 380, 300, variant="plane"))
    for vehicle in (car, boat):
        world.inputs[vehicle.id] = (1.0, 0.0)
    world.inputs[plane.id] = (1.0, 0.5)
    run(world, 5)
    assert world.pos(car)[0] < 460 and base.water_at(world, *world.pos(car)) is None
    assert base.water_at(world, *world.pos(boat)) is not None and 700 < world.pos(boat)[0] < 760
    assert world.pos(plane)[0] > 800  # a plane flies over everything
    # Without a driver a vehicle does not move.
    world.inputs.clear()
    before = world.pos(plane)
    run(world, 3)
    assert world.pos(plane) == before


def test_water_on_fire_and_sky_on_fire_water_and_rain_put_fire_out() -> None:
    world = make_world()
    lake(world)
    flame = world.add(row("fire", 430, 400, variant="campfire"))
    safe = world.add(row("fire", 300, 400, variant="campfire"))
    out = run(world, 2)
    assert not alive(world, flame) and alive(world, safe) and events(out, "douse")
    cloud = world.add(row("sky", 300, 300, variant="cloud", w=200, h=80))
    cloud.data["rain_in"] = 0.5
    run(world, 3)
    assert not alive(world, safe)


def test_house_on_creature_a_barn_doubles_the_chance_of_calves() -> None:
    def calves(with_barn: bool) -> int:
        total = 0
        for seed in range(80):
            world = make_world(seed=seed)
            cow(world, 300, 300); cow(world, 330, 300)
            if with_barn:
                world.add(row("house", 320, 380, w=120, h=100))
            total += len(events(run(world, 0.5), "birth"))
        return total

    plain, sheltered = calves(False), calves(True)
    assert sheltered > plain * 1.3 and plain > 0


def test_house_on_person_shelter_from_rain_is_a_roof() -> None:
    world = make_world()
    villager = person(world, 300, 300)
    cloud = world.add(row("sky", 320, 150, variant="cloud", w=200, h=80))
    cloud.data["rain_in"] = 0.5
    run(world, 6)
    assert villager.state != "rest"  # nowhere to hide
    world.add(row("house", 420, 300, w=120, h=100))
    run(world, 8)
    assert villager.state == "rest"


# ---- Explosive

def bomb(world: Any, x: float, y: float, fuse: float = 1.0, **extra: Any) -> Any:
    entity = world.add(row("explosive", x, y, **{"w": 60, "h": 60, **extra}))
    entity.data["fuse"] = fuse
    return entity


def test_explosive_fuse_counts_down_from_twenty_and_radius_is_clamped() -> None:
    world = make_world()
    grenade = world.add(row("explosive", 400, 400, w=60, h=60))
    assert grenade.data["view"]["fuse"] == 20
    out = run(world, 10)
    assert grenade.data["view"]["fuse"] in (10, 11) and not events(out, "explode")
    out = run(world, 11)
    blast = ops(out, "explosion")[0]
    assert (blast["x"], blast["y"], blast["r"]) == (400.0, 400.0, 90.0) and not alive(world, grenade)
    assert [blast_radius(world, world.add(row("explosive", 0, 0, w=w, h=10))) for w in (10, 100, 400)] == [80, 150, 200]


def test_explosive_on_living_things_damage_falls_from_100_to_30_at_the_edge() -> None:
    world = make_world()
    bomb(world, 400, 400)
    near = cow(world, 400, 415, sizeClass="L", hp=1000)
    edge = wolf(world, 480, 400, hunger=0, hp=1000)
    swimmer = world.add(row("aquatic", 400, 470, locomotion="swim", hp=1000))
    far = person(world, 400, 600, hp=1000)
    for entity in (near, edge, swimmer, far):
        world.max_hp[entity.i] = 1000
        world.base_speed[entity.i] = 0  # hold still for the measurement
    run(world, 2)
    lost = [round(1000 - hp(world, entity)) for entity in (near, edge, far)]
    assert 85 <= lost[0] <= 100 and 30 <= lost[1] <= 45 and lost[2] == 0
    assert hp(world, swimmer) < 1000


def test_explosive_on_food_fire_doodle_house_and_vehicle() -> None:
    world = make_world()
    bomb(world, 400, 400)
    apple = world.add(row("food", 430, 400))
    flame = world.add(row("fire", 400, 440, variant="campfire"))
    scribble = world.add(row("doodle", 370, 400, locomotion="hop"))
    home = world.add(row("house", 400, 340, w=120, h=100))
    car = world.add(row("vehicle", 460, 430, variant="car"))
    out = run(world, 2)
    deaths = {d["id"]: d for d in ops(out, "death")}
    assert not alive(world, apple) and not alive(world, flame)
    assert deaths[scribble.id]["fossil"] is True  # a scribble is blown to a fossil
    assert hp(world, home) < 200 and hp(world, car) < 200


def test_explosive_on_plant_ignites_plants_within_half_the_radius() -> None:
    world = make_world()
    bomb(world, 400, 400)
    close = world.add(row("plant", 430, 400, w=60, h=80))
    world.max_hp[close.i] = world.hp[close.i] = 1000
    distant = world.add(row("plant", 475, 400, w=60, h=80))
    world.max_hp[distant.i] = world.hp[distant.i] = 1000
    out = run(world, 2)
    lit = [e["id"] for e in events(out, "ignite")]
    assert lit == [close.id] and close.data["burning"] and not distant.data.get("burning")


def test_explosive_on_explosive_chain_reaction() -> None:
    world = make_world()
    bomb(world, 400, 400, fuse=1)
    second = bomb(world, 470, 400, fuse=15)
    third = bomb(world, 540, 400, fuse=15)
    out = run(world, 4)
    assert len(ops(out, "explosion")) == 3 and not alive(world, second) and not alive(world, third)


def test_explosive_on_remote_unbinds_it() -> None:
    world = make_world()
    car = world.add(row("vehicle", 300, 400, variant="car"))
    remote = world.add(row("remote", 440, 400))
    run(world, 1)
    assert remote.bound_to == car.id
    bomb(world, 470, 400, fuse=0.5)
    world.kill(car, fossil=True)  # nothing left to rebind to
    run(world, 2)
    assert alive(world, remote) and remote.bound_to is None and remote.data["view"]["bound"] is None


# ---- Vehicle, Remote

def test_remote_on_vehicle_binds_to_the_nearest_free_vehicle_or_the_next_one() -> None:
    world = make_world()
    remote = world.add(row("remote", 400, 400))
    out = run(world, 2)
    assert remote.bound_to is None and not events(out, "bind")
    far = world.add(row("vehicle", 900, 400, variant="car"))
    run(world, 2)
    assert remote.bound_to is None  # more than 400 px away
    near = world.add(row("vehicle", 600, 400, variant="car"))
    out = run(world, 2)
    assert remote.bound_to == near.id and events(out, "bind")[0]["vehicle"] == near.id
    # A second remote does not take a vehicle that is already bound.
    other = world.add(row("remote", 620, 400))
    run(world, 2)
    assert other.bound_to == far.id or other.bound_to is None
    assert other.bound_to != near.id


def test_vehicle_hits_walkers_at_speed() -> None:
    world = make_world()
    car = world.add(row("vehicle", 300, 400, variant="car"))
    victim = cow(world, 420, 400, sizeClass="L")
    world.base_speed[victim.i] = 0
    world.inputs[car.id] = (1.0, 0.0)
    out = run(world, 0.9)
    # 20 damage and a shove away from the car, at most once a second.
    assert hp(world, victim) == 80 and any(e["by"] == car.id for e in events(out, "hit"))
    assert world.pos(victim)[0] > 440
    # A slow nudge hurts nobody.
    world = make_world()
    car = world.add(row("vehicle", 300, 400, variant="car"))
    victim = cow(world, 360, 400, sizeClass="L")
    world.base_speed[victim.i] = 0
    world.inputs[car.id] = (0.4, 0.0)  # 48 px/s
    run(world, 2)
    assert hp(world, victim) == 100


# ---- Fire

def test_fire_on_living_things_burns_those_within_20px() -> None:
    world = make_world()
    world.add(row("fire", 400, 400, variant="campfire"))
    victims = [cow(world, 410, 400, sizeClass="S", w=20, h=20), wolf(world, 400, 412, sizeClass="S", w=20, h=20, hunger=0),
               person(world, 392, 400, sizeClass="S", w=20, h=20)]
    safe = cow(world, 470, 400, sizeClass="S", w=20, h=20)
    for entity in victims + [safe]:
        world.base_speed[entity.i] = 0
    run(world, 3)
    for victim, full in zip(victims, (100, 150, 100)):
        assert full - 35 <= hp(world, victim) <= full - 20  # 10 HP a second
    assert hp(world, safe) == 100


def test_fire_on_food_cooks_it() -> None:
    world = make_world()
    world.add(row("fire", 400, 400, variant="campfire"))
    meat = world.add(row("food", 425, 400, label={"ru": "мясо", "en": "meat"}))
    raw = world.add(row("food", 600, 400, label={"ru": "мясо", "en": "meat"}))
    out = run(world, 2)
    assert meat.data["cooked"] and meat.data["view"]["cooked"] and events(out, "cook")
    assert base.nutrition(world, meat) == 75 and base.nutrition(world, raw) == 50


def test_fire_on_plant_and_house_wildfire_spreads_and_burns_them_down() -> None:
    world = make_world(seed=3)
    world.add(row("fire", 400, 400))
    tree = world.add(row("plant", 430, 400, w=60, h=80))
    home = world.add(row("house", 370, 400, w=60, h=60))
    out = run(world, 20)
    lit = {e["id"] for e in events(out, "ignite")}
    assert lit == {tree.id, home.id}
    # The drawn fire burned for 20 s and is gone; what it lit burns for 20 s more.
    out += run(world, 25)
    deaths = {d["id"]: d for d in ops(out, "death")}
    assert deaths[tree.id]["fossil"] and deaths[tree.id]["cause"] == "burned"
    assert deaths[home.id]["fossil"] and not world.of_type("fire")
    # A campfire never spreads.
    world = make_world(seed=3)
    world.add(row("fire", 400, 400, variant="campfire"))
    bush = world.add(row("plant", 430, 400, w=60, h=80))
    out = run(world, 60)
    assert not events(out, "ignite") and alive(world, bush) and len(world.of_type("fire")) == 1


def test_fire_on_explosive_detonates_it_at_once() -> None:
    world = make_world()
    grenade = world.add(row("explosive", 400, 400, w=60, h=60))
    assert grenade.data["fuse"] == 20
    world.add(row("fire", 440, 400, variant="campfire"))
    out = run(world, 1.5)
    assert len(ops(out, "explosion")) == 1 and not alive(world, grenade)


# ---- Sky on water

def test_sky_on_water_puddles_dry_in_sun_and_grow_in_rain() -> None:
    world = make_world()
    puddle = lake(world, size="S")
    world.add(row("sky", 600, 200, variant="sun"))
    run(world, 30)
    assert 0.65 <= puddle.data["view"]["scale"] <= 0.75
    out = run(world, 60)
    death = ops(out, "death")[0]
    assert death["id"] == puddle.id and death["fossil"] is True and death["cause"] == "dried"
    # Rain fills a puddle; a lake is not affected by either.
    world = make_world()
    puddle, big = lake(world, size="S"), world.add(row("water", 3000, 3000, sizeClass="L", w=400, h=300))
    cloud = world.add(row("sky", 600, 300, variant="cloud", w=200, h=80))
    cloud.data.update(rain_in=30)
    cloud.data["view"]["raining"] = True
    world.base_speed[cloud.i] = 0
    run(world, 20)
    assert 1.3 <= puddle.data["view"]["scale"] <= 1.5 and "scale" not in (big.data.get("view") or {})
    assert len(base.polygon_of(world, puddle)) == 8


def test_doodle_hops_and_is_eaten_only_by_omnivores() -> None:
    world = make_world()
    scribble = world.add(row("doodle", 400, 400, locomotion="hop"))
    start = world.pos(scribble)
    run(world, 20)
    assert world.pos(scribble) != start and alive(world, scribble)
    herbivore = cow(world, *world.pos(scribble), hunger=90)
    run(world, 8)
    assert alive(world, scribble) and world.hunger[herbivore.i] > 85
    world.kill(herbivore, fossil=False)
    omnivore = cow(world, *world.pos(scribble), hunger=90, diet="omni")
    run(world, 10)
    assert world.hunger[omnivore.i] < 85
