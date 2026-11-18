"""House, nest or barn: where people and animals rest, shelter and breed. Flammable."""

from app.sim.systems.decide import Brain, register


@register("house")
class House(Brain):
    """A house does nothing by itself: living.go_rest, court and person shelter use it."""
