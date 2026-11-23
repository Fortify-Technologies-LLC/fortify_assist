"""Answering questions about the home from live state: lists, counts, any/all, attributes.

Everything here is computed from entity states, never generated. The language model may
phrase an answer later, but the facts (which devices, how many) come from this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import Entity, House

STATES = ("on", "off", "open", "closed", "locked", "unlocked")
GROUP = {"on": "power", "off": "power", "open": "opening", "closed": "opening",
         "locked": "lock", "unlocked": "lock"}
OPENING_SENSORS = {"door", "garage_door", "window", "opening", "gate"}
POWER_DOMAINS = [("light", None), ("switch", None), ("fan", None), ("media_player", None),
                 ("input_boolean", None)]
WHOLE_HOUSE = {"house", "whole house", "entire house", "home", "everywhere", "anywhere", "the house"}


@dataclass(frozen=True)
class Spec:
    plural: str
    singular: str
    kinds: dict  # state group -> list of (domain, device_classes or None)


def _spec(plural: str, singular: str, **kinds) -> Spec:
    return Spec(plural, singular, kinds)


_DOORS = [("cover", {"door", "garage", "gate"}), ("binary_sensor", {"door", "garage_door", "opening", "gate"})]
SPECS = {
    "light": _spec("lights", "light", power=[("light", None)]),
    "fan": _spec("fans", "fan", power=[("fan", None)]),
    "switch": _spec("switches", "switch", power=[("switch", None)]),
    "door": _spec("doors", "door", opening=_DOORS, lock=[("lock", None)]),
    "garage door": _spec("garage doors", "garage door", opening=[("cover", {"garage"}), ("binary_sensor", {"garage_door"})]),
    "window": _spec("windows", "window", opening=[("binary_sensor", {"window"}), ("cover", {"window"})]),
    "lock": _spec("locks", "lock", lock=[("lock", None)]),
    "blind": _spec("blinds", "blind", opening=[("cover", {"blind", "shade", "curtain", "shutter", "awning"})]),
    "cover": _spec("covers", "cover", opening=[("cover", None)]),
    "tv": _spec("TVs", "TV", power=[("media_player", "tv")]),
    "speaker": _spec("speakers", "speaker", power=[("media_player", None)]),
    "thing": _spec("devices", "device", power=POWER_DOMAINS,
                   opening=[("cover", None), ("binary_sensor", OPENING_SENSORS)], lock=[("lock", None)]),
}
ALIASES = {
    "lights": "light", "lamp": "light", "lamps": "light", "fans": "fan", "switches": "switch",
    "plug": "switch", "plugs": "switch", "outlet": "switch", "outlets": "switch",
    "doors": "door", "garage doors": "garage door", "locks": "lock", "windows": "window",
    "blinds": "blind", "curtain": "blind", "curtains": "blind", "shade": "blind", "shades": "blind",
    "covers": "cover", "tvs": "tv", "television": "tv", "televisions": "tv", "speakers": "speaker",
    "media player": "speaker", "media players": "speaker",
    "things": "thing", "device": "thing", "devices": "thing", "anything": "thing",
    "everything": "thing", "something": "thing", "stuff": "thing",
}


def is_tv(e: Entity) -> bool:
    """A television, by what Home Assistant says it is or by its name. A soundbar or speaker is not one."""
    return e.domain == "media_player" and (
        e.device_class == "tv" or bool({"tv", "television"} & set(e.name.lower().replace("-", " ").split())))


def _of_class(e: Entity, classes) -> bool:
    return is_tv(e) if classes == "tv" else classes is None or e.device_class in classes


def lookup(phrase: str) -> Spec | None:
    phrase = phrase.strip()
    return SPECS.get(phrase) or SPECS.get(ALIASES.get(phrase, ""))


MEDIA_ON = {"on", "playing", "paused", "buffering"}
MEDIA_OFF = {"off", "standby", "idle"}  # an idle speaker is powered but is not "on" in anyone's sense


def _matches(e: Entity, state: str) -> bool:
    s = e.state
    if e.domain == "media_player":
        return s in MEDIA_ON if state == "on" else s in MEDIA_OFF if state == "off" else False
    if e.domain == "binary_sensor":  # door/window sensors: on means open
        return (s == "on") if state == "open" else (s == "off") if state == "closed" else False
    table = {"on": {"on"}, "off": {"off"}, "open": {"open", "opening"}, "closed": {"closed", "closing"},
             "locked": {"locked"}, "unlocked": {"unlocked"}}
    return s in table[state]


def select(house: House, spec: Spec, state: str, areas: set[str] | None) -> tuple[list[Entity], list[Entity]] | None:
    """(matching, everything in scope). None if this class cannot have that state ('doors on')."""
    kinds = spec.kinds.get(GROUP[state])
    if not kinds:
        return None
    scope = []
    for e in house.exposed():
        if not e.available or (areas is not None and e.area not in areas):
            continue
        for domain, classes in kinds:
            if e.domain == domain and _of_class(e, classes):
                scope.append(e)
                break
    return [e for e in scope if _matches(e, state)], scope


def inventory(house: House, spec: Spec, areas: set[str] | None) -> list[Entity]:
    """Every exposed device of this kind, whatever its state: 'what lights are in the workshop'."""
    seen: set[str] = set()
    found: list[Entity] = []
    for kinds in spec.kinds.values():
        for e in house.exposed():
            if e.id in seen or (areas is not None and e.area not in areas):
                continue
            if any(e.domain == d and _of_class(e, c) for d, c in kinds):
                seen.add(e.id)
                found.append(e)
    return sorted(found, key=lambda e: e.name.lower())


def join_names(names: list[str], limit: int = 8) -> str:
    shown = names[:limit]
    text = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + f" and {shown[-1]}"
    return text + (f", and {len(names) - limit} more" if len(names) > limit else "")
