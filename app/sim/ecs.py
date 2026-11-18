"""Entities as presets of components.

A type is a set of components from config/archetypes.yaml; the VLM's answer fills in
the fields marked `from_vlm`. Hot numeric state (position, velocity, needs, hp) lives
in the world's numpy arrays, indexed by `Entity.i`; everything else is on the object.
"""

from typing import Any


class Entity:
    __slots__ = (
        "i", "id", "type", "label", "params", "comps", "state", "anim", "facing",
        "target", "timers", "data", "born", "radius", "parent", "bound_to",
        "next_think", "last_think", "sent", "chunk", "dirty", "in_water", "sprite_id",
    )  # fmt: skip

    def __init__(self, i: int, row: dict[str, Any], comps: dict[str, dict[str, Any]], now: float) -> None:
        params = row.get("params") or {}
        self.i = i
        self.id: str = row["id"]
        self.type: str = row["type"]
        self.label: dict[str, str] = row.get("label") or {}
        self.params: dict[str, Any] = params
        self.comps = comps
        self.state: str = row.get("state") or "idle"
        self.anim = "idle"
        self.facing: str = row.get("facing") or "right"
        self.target: str | None = None
        self.timers: dict[str, float] = {}
        # Type-specific scratch state that is persisted with the entity (stock, fuse, ...).
        self.data: dict[str, Any] = dict(params.get("data") or {})
        self.born: float = now - float(params.get("age", 1e9))
        self.radius: float = max(8.0, max(params.get("w") or 32, params.get("h") or 32) / 2)
        self.parent: str | None = row.get("parent_id")
        self.bound_to: str | None = row.get("bound_to")
        self.next_think = 0
        self.last_think = now
        self.sent: tuple[Any, ...] | None = None
        self.chunk: tuple[int, int] = (0, 0)
        self.dirty = True
        self.in_water = False
        self.sprite_id: str | None = row.get("sprite_id")

    def has(self, name: str) -> bool:
        return name in self.comps

    def get(self, name: str, key: str, default: Any = None) -> Any:
        return (self.comps.get(name) or {}).get(key, default)


# Which field of the VLM result fills a `from_vlm` component value.
VLM_FIELDS = {
    ("Locomotion", "mode"): "locomotion",
    ("Diet", "kind"): "diet",
    ("Role", "kind"): "role",
    ("Controllable", "medium"): "variant",
    ("Fire", "kind"): "variant",
    ("Weather", "kind"): "variant",
}
VARIANT_VALUES = {
    "Controllable": {"car": "land", "boat": "water", "plane": "air"},
    "Fire": {"campfire": "campfire"},
    "Weather": {"cloud": "rain", "sun": "sun"},
}


def resolve_components(
    archetype: dict[str, Any], params: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The archetype's components with `from_vlm` values taken from the entity's params."""
    resolved: dict[str, dict[str, Any]] = {}
    variant = params.get("variant")
    for name, spec in archetype["components"].items():
        spec = dict(spec or {})
        # A component tied to a variant (a pasture's Edible, a fruit tree's Spawner).
        if "variant" in spec and name in ("Spawner", "Edible") and spec["variant"] != variant:
            if not (name == "Spawner" and variant is None):
                continue
        default = spec.pop("default", None)
        for key, value in list(spec.items()):
            if value != "from_vlm":
                continue
            given = params.get(VLM_FIELDS.get((name, key), ""))
            given = VARIANT_VALUES.get(name, {}).get(given, given if name not in VARIANT_VALUES else None)
            spec[key] = given or default
        resolved[name] = spec
    return resolved
