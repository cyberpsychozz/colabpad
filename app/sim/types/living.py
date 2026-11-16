"""The shared brain of everything that has needs: a utility-scored state machine.

States: wander, seek_food, eat, seek_water, drink, rest, mate, flee, hunt, attack.
`flee` pre-empts everything within the flee radius. Each think scores the options
and takes the best; timed actions (eating, drinking) run to their end first.
"""

import math
from typing import TYPE_CHECKING, Any

from app.sim.systems.decide import Brain
from app.sim.types import base

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

PERCEPTION = 300.0
WATER_SEARCH = 800.0
SHELTER_SEARCH = 600.0
BITE_S = 1.0
DRINK_S = 2.0
ATTACK_DAMAGE = 12.0  # per think, about 24 HP/s
MATE_CHANCE = 0.25


def reach(a: "Entity", b: "Entity") -> float:
    return (a.radius + b.radius) * 0.5 + 10


class Living(Brain):
    flees: tuple[str, ...] = ("predator", "fire", "explosive")
    grazes = False          # eats pasture
    hunts: tuple[str, ...] = ()
    needs_house_to_mate = False

    # ---- hooks for subclasses

    def is_threat(self, world: "World", entity: "Entity", other: "Entity") -> bool:
        if other.type not in self.flees:
            return False
        if other.type == "fire":
            return other.get("Fire", "kind") != "campfire" and world.dist(entity, other) < 90
        return True

    def suffer(self, world: "World", entity: "Entity", dt: float) -> None:
        """Continuous effects of where the entity is."""
        water = base.water_at(world, *world.pos(entity))
        entity.in_water = water is not None
        flies = entity.get("Locomotion", "mode") == "fly"
        world.slow[entity.i] = world.balance["speed"]["water_walk_mult"] if water and not flies else 1.0

    def extra_options(self, world: "World", entity: "Entity", seen: list["Entity"]) -> list[tuple[float, Any]]:
        return []

    def can_go(self, world: "World", entity: "Entity", x: float, y: float) -> bool:
        return True

    # ---- the think

    def think(self, world: "World", entity: "Entity", dt: float) -> None:
        self.suffer(world, entity, dt)
        i = entity.i
        if world.hp[i] <= 0:
            return
        self.grow(world, entity)
        x, y = world.pos(entity)
        seen = world.near(x, y, PERCEPTION, entity)
        flee_radius = world.balance["flee_radius"]
        threat = None
        for other in seen:
            if self.is_threat(world, entity, other):
                d = world.dist(entity, other)
                if d <= flee_radius and (threat is None or d < threat[0]):
                    threat = (d, other)
        if threat:
            self.flee(world, entity, threat[1])
            return
        if self.busy(world, entity, dt):
            return
        options = [(10.0, lambda: self.wander(world, entity))]
        needs = world.balance["needs"]
        if world.has_needs[i]:
            if world.hunger[i] > needs["seek_food_above"]:
                food = self.find_food(world, entity, seen)
                if food is not None:
                    options.append((float(world.hunger[i]), lambda: self.seek_food(world, entity, food)))
            if world.has_thirst[i] and world.thirst[i] > needs["seek_water_above"] and not entity.in_water:
                water = base.nearest_water(world, x, y, WATER_SEARCH)
                if water is not None:
                    options.append((float(world.thirst[i]) * 1.1, lambda: self.seek_water(world, entity, water)))
            elif world.has_thirst[i] and world.thirst[i] > needs["seek_water_above"]:
                options.append((float(world.thirst[i]) * 1.1, lambda: self.drink(world, entity)))
            if world.energy[i] < needs["rest_below"] or (world.is_night() and entity.type != "aquatic"):
                options.append((60.0, lambda: self.go_rest(world, entity)))
            partner = self.find_partner(world, entity, seen)
            if partner is not None:
                options.append((30.0, lambda: self.court(world, entity, partner)))
        options.extend(self.extra_options(world, entity, seen))
        max(options, key=lambda option: option[0])[1]()

    def grow(self, world: "World", entity: "Entity") -> None:
        """Babies start at 0.6 of their parent's size and reach full size in five minutes."""
        if entity.parent is None or (entity.data.get("view") or {}).get("scale", 1.0) >= 1.0:
            return
        rules = world.balance["reproduction"]
        age = world.t - entity.born
        scale = min(1.0, rules["baby_scale"] + (1 - rules["baby_scale"]) * age / rules["grow_s"])
        world.view(entity, scale=round(scale, 2))

    def is_adult(self, world: "World", entity: "Entity") -> bool:
        return entity.parent is None or world.t - entity.born >= world.balance["reproduction"]["grow_s"]

    # ---- flee

    def flee(self, world: "World", entity: "Entity", threat: "Entity") -> None:
        x, y = world.pos(entity)
        tx, ty = world.pos(threat)
        dx, dy = x - tx, y - ty
        length = math.hypot(dx, dy) or 1.0
        distance = world.balance["flee_radius"]
        gx, gy = x + dx / length * distance, y + dy / length * distance
        if not self.can_go(world, entity, gx, gy):
            gx, gy = self.safe_point(world, entity)
        world.set_state(entity, "flee")
        entity.target = None
        world.go(entity, gx, gy, world.balance["speed"]["hunt_mult"])

    def safe_point(self, world: "World", entity: "Entity") -> tuple[float, float]:
        return world.pos(entity)

    # ---- timed actions

    def busy(self, world: "World", entity: "Entity", dt: float) -> bool:
        i, state = entity.i, entity.state
        if state == "eat":
            target = world.entities.get(entity.target or "")
            if target is None or world.hunger[i] <= 5:
                world.set_state(entity, "wander")
                return False
            entity.timers["bite"] = entity.timers.get("bite", BITE_S) - dt
            if entity.timers["bite"] <= 0:
                entity.timers["bite"] = BITE_S
                self.bite(world, entity, target)
            return True
        if state == "drink":
            entity.timers["drink"] = entity.timers.get("drink", DRINK_S) - dt
            if entity.timers["drink"] <= 0:
                world.thirst[i] = 0
                world.set_state(entity, "wander")
                world.event("drink", entity)
                return False
            return True
        if state == "rest":
            hungry = world.hunger[i] > 80 or (world.has_thirst[i] and world.thirst[i] > 80)
            raining = entity.type == "person" and base.raining_at(world, *world.pos(entity))
            if hungry or (world.energy[i] >= 95 and not world.is_night() and not raining):
                world.set_state(entity, "wander")
                return False
            return True
        return False

    # ---- food

    def edible(self, world: "World", entity: "Entity", other: "Entity") -> bool:
        diet = entity.get("Diet", "kind", "omni")
        if other.type == "food":
            if entity.type == "aquatic" and not base.water_at(world, *world.pos(other)):
                return False
            meat = base.is_meat(world, other)
            return diet == "omni" or (diet == "meat") == meat
        if other.type == "plant" and self.grazes and diet != "meat":
            return other.has("Edible") and other.data.get("stock", 100) >= world.balance["plant"]["pasture_bite"]
        if other.type == "doodle":
            return diet == "omni"
        return False

    def find_food(self, world: "World", entity: "Entity", seen: list["Entity"]) -> "Entity | None":
        best = None
        for other in seen:
            if self.edible(world, entity, other):
                d = world.dist(entity, other)
                if best is None or d < best[0]:
                    best = (d, other)
        return best[1] if best else None

    def seek_food(self, world: "World", entity: "Entity", food: "Entity") -> None:
        if world.dist(entity, food) <= reach(entity, food):
            world.stop(entity)
            entity.target = food.id
            entity.timers["bite"] = BITE_S
            world.set_state(entity, "eat")
            return
        world.set_state(entity, "seek_food")
        entity.target = food.id
        world.go(entity, *world.pos(food))

    def bite(self, world: "World", entity: "Entity", target: "Entity") -> None:
        i = entity.i
        if target.type == "food":
            bites = world.balance["nutrition"]["bites"]
            left = target.data.get("bites", bites) - 1
            world.hunger[i] = max(0.0, world.hunger[i] - base.nutrition(world, target) / bites)
            target.data["bites"] = left
            world.view(target, bites=left)
            world.event("eat", entity, target=target.id)
            if left <= 0:
                world.kill(target, fossil=False, cause="eaten")
        elif target.type == "plant":
            rules = world.balance["plant"]
            target.data["stock"] = max(0, target.data.get("stock", rules["pasture_max"]) - rules["pasture_bite"])
            world.view(target, stock=round(target.data["stock"]))
            world.hunger[i] = max(0.0, world.hunger[i] - rules["pasture_bite"])
            world.event("eat", entity, target=target.id)
            if target.data["stock"] < rules["pasture_bite"]:
                world.set_state(entity, "wander")
        elif target.type == "doodle":
            world.hunger[i] = max(0.0, world.hunger[i] - target.get("Edible", "nutrition", 10))
            world.event("eat", entity, target=target.id)
            world.hp[target.i] -= 25
            if world.hp[target.i] <= 0:
                world.kill(target, fossil=False, cause="eaten")

    # ---- water

    def seek_water(self, world: "World", entity: "Entity", found: "tuple[Entity, tuple[float, float]]") -> None:
        _, (px, py) = found
        x, y = world.pos(entity)
        if math.hypot(px - x, py - y) <= 12:
            self.drink(world, entity)
            return
        world.set_state(entity, "seek_water")
        world.go(entity, px, py)

    def drink(self, world: "World", entity: "Entity") -> None:
        world.stop(entity)
        entity.timers["drink"] = DRINK_S
        world.set_state(entity, "drink")

    # ---- rest

    def go_rest(self, world: "World", entity: "Entity") -> None:
        """Rest where you are, or at a house (people) or nest/barn (animals) if one is near."""
        x, y = world.pos(entity)
        shelter = None
        for house in world.of_type("house"):
            d = world.dist(entity, house)
            if d <= SHELTER_SEARCH and (shelter is None or d < shelter[0]):
                shelter = (d, house)
        if shelter and shelter[0] > shelter[1].radius * 0.6:
            world.set_state(entity, "wander")
            world.go(entity, *world.pos(shelter[1]))
            return
        world.stop(entity)
        world.set_state(entity, "rest")

    # ---- reproduction

    def ready(self, world: "World", entity: "Entity") -> bool:
        rules = world.balance["reproduction"]
        i = entity.i
        return bool(
            entity.has("Reproduce")
            and self.is_adult(world, entity)
            and world.hunger[i] < rules["hunger_below"]
            and (not world.has_thirst[i] or world.thirst[i] < rules["thirst_below"])
            and world.energy[i] > rules["energy_above"]
            and world.t >= entity.data.get("mate_after", 0)
        )

    def find_partner(self, world: "World", entity: "Entity", seen: list["Entity"]) -> "Entity | None":
        if not self.ready(world, entity):
            return None
        label = entity.label.get("en")
        for other in seen:
            if other.type == entity.type and other.label.get("en") == label and self.ready(world, other):
                return other
        return None

    def near_house(self, world: "World", entity: "Entity") -> bool:
        return any(world.dist(entity, house) <= 150 for house in world.of_type("house"))

    def court(self, world: "World", entity: "Entity", partner: "Entity") -> None:
        rules = world.balance["reproduction"]
        if world.dist(entity, partner) > rules["distance"]:
            world.set_state(entity, "mate")
            world.go(entity, *world.pos(partner))
            return
        home = self.near_house(world, entity)
        if self.needs_house_to_mate and not home:
            self.wander(world, entity)
            return
        chance = MATE_CHANCE * (rules["shelter_chance_mult"] if home and not self.needs_house_to_mate else 1)
        if world.rng.random() >= chance:
            return
        x, y = world.pos(entity)
        px, py = world.pos(partner)
        mx, my = (x + px) / 2, (y + py) / 2
        for parent in (entity, partner):
            parent.data["mate_after"] = world.t + rules["cooldown_s"]
            parent.dirty = True
        if not base.room_for(world, mx, my, entity.label.get("en")):
            return
        world.set_state(entity, "mate")
        baby = world.spawn_child(base.child_row(entity.type, mx, my, parent=entity, age=0, data={"view": {"scale": rules["baby_scale"]}}))
        world.event("birth", baby, parents=[entity.id, partner.id])

    # ---- wander

    def wander(self, world: "World", entity: "Entity") -> None:
        if entity.state not in ("wander", "idle"):
            world.set_state(entity, "wander")
        if world.has_goal[entity.i] or world.rng.random() < 0.4:
            return
        x, y = world.pos(entity)
        for _ in range(4):
            angle, distance = world.rng.uniform(0, math.tau), world.rng.uniform(40, 160)
            gx, gy = x + math.cos(angle) * distance, y + math.sin(angle) * distance
            if 0 <= gx <= world.bounds[0] and 0 <= gy <= world.bounds[1] and self.can_go(world, entity, gx, gy):
                world.go(entity, gx, gy, 0.6)
                return

    # ---- fighting (used by predators and warriors)

    def strike(self, world: "World", entity: "Entity", prey: "Entity", damage: float = ATTACK_DAMAGE) -> bool:
        """Close in and hit. Returns True when the prey has just died."""
        if world.dist(entity, prey) > reach(entity, prey):
            world.set_state(entity, "hunt")
            entity.target = prey.id
            world.go(entity, *world.pos(prey), world.balance["speed"]["hunt_mult"])
            return False
        world.stop(entity)
        world.set_state(entity, "attack")
        prey.data["attacker"] = entity.id
        world.damage(prey, damage, "killed")
        world.think_now(prey)
        world.event("hit", prey, by=entity.id)
        return bool(world.hp[prey.i] <= 0)

    def catch_up(self, world: "World", entity: "Entity", elapsed: float) -> None:
        self.grow(world, entity)
