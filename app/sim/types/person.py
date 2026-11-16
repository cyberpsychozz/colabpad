"""Person, with a role: villager, warrior, fisherman or medic."""

import math
from typing import TYPE_CHECKING, Any

from app.sim.systems.decide import register
from app.sim.types import base
from app.sim.types.living import WATER_SEARCH, Living

if TYPE_CHECKING:
    from app.sim.ecs import Entity
    from app.sim.world import World

WARRIOR_DAMAGE = 20.0  # per think: a warrior beats a lone wolf, narrowly
CATCH_RADIUS = 250.0


@register("person")
class Person(Living):
    needs_house_to_mate = True

    def role(self, entity: "Entity") -> str:
        return entity.get("Role", "kind") or "villager"

    def is_threat(self, world: "World", entity: "Entity", other: "Entity") -> bool:
        if other.type == "predator" and self.role(entity) == "warrior":
            return False
        return super().is_threat(world, entity, other)

    def extra_options(self, world: "World", entity: "Entity", seen: list["Entity"]) -> list[tuple[float, Any]]:
        options: list[tuple[float, Any]] = []
        person = world.balance["person"]
        x, y = world.pos(entity)
        # Everyone hides from rain under a roof, if there is one.
        if base.raining_at(world, x, y) and world.of_type("house"):
            options.append((70.0, lambda: self.go_rest(world, entity)))
        # Hungry with nothing to eat in sight: shake a fruit tree.
        if world.hunger[entity.i] > world.balance["needs"]["seek_food_above"] and self.find_food(world, entity, seen) is None:
            tree = world.nearest(entity, 300, lambda other: other.type == "plant" and other.params.get("variant") == "fruit_tree")
            if tree is not None:
                options.append((float(world.hunger[entity.i]) - 1, lambda: self.shake(world, entity, tree)))
        role = self.role(entity)
        if role == "warrior":
            foe = world.nearest(entity, person["warrior_radius"], lambda other: other.type == "predator")
            if foe is not None:
                options.append((95.0, lambda: self.strike(world, entity, foe, WARRIOR_DAMAGE)))
        elif role == "medic":
            hurt = world.nearest(
                entity, 300, lambda other: other.type in ("person", "creature") and world.hp[other.i] < world.max_hp[other.i]
            )
            if hurt is not None:
                options.append((50.0, lambda: self.heal(world, entity, hurt)))
        elif role == "fisherman":
            found = base.nearest_water(world, x, y, WATER_SEARCH)
            if found is not None:
                options.append((20.0, lambda: self.fish(world, entity, found)))
        return options

    def shake(self, world: "World", entity: "Entity", tree: "Entity") -> None:
        if world.dist(entity, tree) > (entity.radius + tree.radius) * 0.5 + 10:
            world.set_state(entity, "seek_food")
            world.go(entity, *world.pos(tree))
            return
        world.stop(entity)
        if world.t >= tree.data.get("shaken_after", 0):
            tree.data["shaken_after"] = world.t + 10
            tree.data["fruit_in"] = 0  # the next fruit falls now (if there is room for it)
            world.think_now(tree)
            world.event("shake", tree, by=entity.id)

    def heal(self, world: "World", entity: "Entity", hurt: "Entity") -> None:
        person = world.balance["person"]
        if world.dist(entity, hurt) > person["medic_radius"] - 5:
            world.set_state(entity, "wander")
            tx, ty = world.pos(hurt)
            x, y = world.pos(entity)
            length = math.hypot(tx - x, ty - y) or 1.0
            # Stop a little short, inside healing range.
            back = person["medic_radius"] - 10
            world.go(entity, tx - (tx - x) / length * back, ty - (ty - y) / length * back)
            return
        world.stop(entity)
        world.set_state(entity, "heal")
        dt = world.think_every / world.balance["tick_hz"]
        x, y = world.pos(entity)
        for other in world.near(x, y, person["medic_radius"], entity):
            if other.has("Health") and other.type in ("person", "creature"):
                world.hp[other.i] = min(world.max_hp[other.i], world.hp[other.i] + person["medic_heal_per_s"] * dt)
                other.dirty = True

    def fish(self, world: "World", entity: "Entity", found: "tuple[Entity, tuple[float, float]]") -> None:
        water, (px, py) = found
        x, y = world.pos(entity)
        if math.hypot(px - x, py - y) > 14:
            world.set_state(entity, "wander")
            world.go(entity, px, py)
            return
        world.stop(entity)
        world.set_state(entity, "fish")
        person = world.balance["person"]
        dt = world.think_every / world.balance["tick_hz"]
        entity.timers["catch"] = entity.timers.get("catch", person["fisherman_catch_s"]) - dt
        entity.timers["wild"] = entity.timers.get("wild", person["fisherman_wild_s"]) - dt
        if not base.room_for(world, x, y):
            return
        if entity.timers["catch"] <= 0:
            entity.timers["catch"] = person["fisherman_catch_s"]
            fish = world.nearest(entity, CATCH_RADIUS, lambda other: other.type == "aquatic")
            if fish is not None:
                # The caught aquatic becomes food "fish", with its own drawing.
                entity.timers["wild"] = person["fisherman_wild_s"]
                food = world.spawn_child(base.child_row(
                    "food", x + 18, y + 8, parent=fish, label=base.BUILTIN["fish"]["label"], data={"meat": True},
                ))  # fmt: skip
                world.event("catch", entity, fish=fish.id, food=food.id)
                world.kill(fish, fossil=False, cause="caught")
                return
        if entity.timers["wild"] <= 0:
            entity.timers["wild"] = person["fisherman_wild_s"]
            food = world.spawn_child(base.child_row("food", x + 18, y + 8, builtin="fish", data={"meat": True}))
            world.event("catch", entity, fish=None, food=food.id)
