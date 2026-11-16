"""Creature: a peaceful animal. Wanders, eats food and grazes, drinks, flees, breeds."""

from app.sim.systems.decide import register
from app.sim.types.living import Living


@register("creature")
class Creature(Living):
    grazes = True
