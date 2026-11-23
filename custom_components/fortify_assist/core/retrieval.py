"""Pick the few entities an LLM is allowed to see.

The prompt size must not grow with the house, so the model never receives the whole entity
list. It gets the top-K candidates for this utterance, labelled with short refs (e1, e2...)
so it can only choose among real, retrieved devices.
"""
from __future__ import annotations

from dataclasses import dataclass

from .index import EntityIndex
from .text import phrase_sim, token_sim, tokens
from .types import Entity

CONTROLLABLE = {
    "light", "switch", "fan", "cover", "lock", "climate", "media_player", "scene", "script",
    "sensor", "binary_sensor", "alarm_control_panel", "input_boolean", "humidifier", "valve",
    "siren", "vacuum", "remote",
}
# loose word -> domain hints, so "shut the blinds" still surfaces covers
WORD_DOMAINS = {
    "light": "light", "lights": "light", "lamp": "light", "lamps": "light", "bulb": "light",
    "dim": "light", "bright": "light", "brighter": "light", "brightness": "light",
    "fan": "fan", "blinds": "cover", "blind": "cover", "curtain": "cover", "curtains": "cover",
    "shade": "cover", "shades": "cover", "garage": "cover", "door": "lock", "lock": "lock",
    "unlock": "lock", "thermostat": "climate", "heat": "climate", "heating": "climate",
    "cool": "climate", "degrees": "climate", "warm": "sensor", "cold": "sensor",
    "temperature": "sensor", "humidity": "sensor", "tv": "media_player", "television": "media_player",
    "speaker": "media_player", "volume": "media_player", "music": "media_player",
    "play": "media_player", "pause": "media_player", "alarm": "alarm_control_panel",
    "arm": "alarm_control_panel", "disarm": "alarm_control_panel", "scene": "scene",
    "script": "script", "vacuum": "vacuum", "heater": "switch", "outlet": "switch", "plug": "switch",
    "siren": "siren", "camera": "camera", "robot": "vacuum", "sprinkler": "switch", "irrigation": "switch",
    "humidifier": "humidifier", "fireplace": "switch", "ac": "climate", "conditioner": "climate",
}
AREA_BOOST = 0.20
CONTEXT_BOOST = 0.10
DOMAIN_BOOST = 0.10
MAX_WINDOW = 4


@dataclass(frozen=True)
class Candidate:
    ref: str
    entity: Entity
    score: float


def _windows(words: tuple[str, ...]):
    for n in range(1, MAX_WINDOW + 1):
        for i in range(len(words) - n + 1):
            yield words[i:i + n]


def _overlap(words: tuple[str, ...], phrase: tuple[str, ...]) -> float:
    """Fraction of the phrase's words that appear somewhere in the utterance."""
    if not phrase:
        return 0.0
    hit = sum(any(token_sim(w, p) for w in words) for p in phrase)
    return hit / len(phrase)


def retrieve(index: EntityIndex, utterance: str, context_area: str | None = None,
             k: int = 8) -> list[Candidate]:
    words = tokens(utterance)
    windows = list(_windows(words))
    mentioned: set[str] = set()
    for w in windows:
        mentioned |= index.find_areas(" ".join(w))
    hinted = {WORD_DOMAINS[w] for w in words if w in WORD_DOMAINS}

    best: dict[str, float] = {}
    for entity, phrase, weight in index._phrases:  # the index owns the phrase table
        if entity.domain not in CONTROLLABLE:
            continue
        direct = max((phrase_sim(w, phrase) for w in windows), default=0.0) * weight
        lexical = 0.5 * _overlap(words, phrase)
        best[entity.id] = max(best.get(entity.id, 0.0), direct, lexical)

    scored: list[tuple[float, Entity]] = []
    for e in index.house.exposed():
        if e.id not in best and e.domain in CONTROLLABLE:
            best[e.id] = 0.0
        if e.id not in best:
            continue
        s = best[e.id]
        if e.area and e.area in mentioned:
            s += AREA_BOOST
        elif e.area and e.area == context_area:
            s += CONTEXT_BOOST
        if e.domain in hinted:
            s += DOMAIN_BOOST
        scored.append((s, e))
    scored.sort(key=lambda t: (-t[0], t[1].id))
    return [Candidate(f"e{i}", e, round(s, 3)) for i, (s, e) in enumerate(scored[:k], 1)]
