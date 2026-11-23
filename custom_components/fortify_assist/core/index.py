"""Entity index: every way a person might refer to an exposed entity or area."""
from __future__ import annotations

from dataclasses import dataclass

from .text import CEILING, GENERIC, STOP, phrase_sim, tokens
from .types import Entity, House

AREA_QUALIFIED_WEIGHT = 0.98  # "bedroom lamp" is slightly less certain than the name itself
AREA_DROPPED_WEIGHT = 0.97  # "desk lights" for "Workshop Desk Lights" (the room is implied)
CEILING_WEIGHT = 0.97  # "bar overhead lights" for "Bar Recessed Lights"


@dataclass(frozen=True)
class Match:
    entity: Entity
    score: float


class EntityIndex:
    """Built from a House snapshot. Rebuild whenever registries or exposure change."""

    def __init__(self, house: House):
        self.house = house
        self._phrases: list[tuple[Entity, tuple[str, ...], float]] = []
        self._areas: list[tuple[str, tuple[str, ...]]] = []
        for area_id, (name, _floor) in house.areas.items():
            self._areas.append((area_id, tokens(name)))
            self._areas += [(area_id, tokens(a)) for a in house.area_aliases.get(area_id, ()) if tokens(a)]
        self._floors = [(fid, tokens(name)) for fid, name in house.floors.items()]
        for fid, (_level, aliases) in house.floor_info.items():
            self._floors += [(fid, tokens(a)) for a in aliases if tokens(a)]
        self.vocabulary: set[str] = set()  # every word the home itself uses, for 'is this about the home?'
        for _id, toks in [*self._areas, *self._floors]:
            self.vocabulary.update(toks)
        for e in house.exposed():
            names = [tokens(e.name), *[tokens(a) for a in e.aliases]]
            for toks in names:
                if toks:
                    self._phrases.append((e, toks, 1.0))
            area = tokens(house.area_name(e.area) or "")
            if e.domain == "light":  # the same light by its other ceiling words, with or without its room
                for toks in names:
                    for word in {t for t in toks if t in CEILING}:
                        for other in sorted(CEILING - {word}):
                            said = tuple(other if t == word else t for t in toks)
                            self._phrases.append((e, said, CEILING_WEIGHT))
                            if area and len(said) > len(area) and said[: len(area)] == area:
                                self._phrases.append((e, said[len(area):], CEILING_WEIGHT * AREA_DROPPED_WEIGHT))
            for toks in names:
                self.vocabulary.update(toks)
            primary = names[0]
            if area and primary and primary[: len(area)] != area:
                self._phrases.append((e, area + primary, AREA_QUALIFIED_WEIGHT))
            elif area and len(primary) > len(area):  # "Workshop Desk Lights": people also say "desk lights"
                rest = primary[len(area):]
                if any(t not in GENERIC for t in rest):  # but not just "lights", which names nothing
                    self._phrases.append((e, rest, AREA_DROPPED_WEIGHT))
        self.vocabulary -= STOP

    def find(self, text: str, domains: set[str] | None = None, areas: set[str] | None = None,
             floor_text: bool = False) -> list[Match]:
        """Entities matching `text`, best first. Scores below 0.6 are dropped."""
        target = tokens(text)
        best: dict[str, Match] = {}
        for entity, phrase, weight in self._phrases:
            if domains is not None and entity.domain not in domains:
                continue
            if areas is not None and entity.area not in areas:
                continue
            score = phrase_sim(target, phrase) * weight
            if score >= 0.6 and (entity.id not in best or score > best[entity.id].score):
                best[entity.id] = Match(entity, score)
        return sorted(best.values(), key=lambda m: (-m.score, m.entity.id))

    def find_areas(self, text: str) -> set[str]:
        """Area ids for a spoken area or floor name ('bedroom', 'upstairs'); empty if none."""
        target = tokens(text)
        if not target:
            return set()
        scored = [(phrase_sim(target, t), aid) for aid, t in self._areas]
        scored = [s for s in scored if s[0] >= 0.85]
        if scored:
            top = max(s[0] for s in scored)
            return {aid for s, aid in scored if s >= top - 0.01}
        for fid, ftoks in self._floors:
            if phrase_sim(target, ftoks) >= 0.85:
                return self._on_floor(fid)
        if target in (("downstairs",), ("upstairs",)) and (fid := self._relative_floor(target[0])):
            return self._on_floor(fid)
        if target in (("house",), ("home",), ("everywhere",), ("whole", "house"), ("entire", "house")):
            return set(self.house.areas)
        return set()

    def _on_floor(self, fid: str) -> set[str]:
        return {aid for aid, (_n, floor) in self.house.areas.items() if floor == fid}

    def _relative_floor(self, word: str) -> str | None:
        """'downstairs' is the lowest floor and 'upstairs' the highest, by HA floor level when set,
        otherwise by name (basement / lower, upstairs / upper / second)."""
        levels = {fid: lvl for fid, (lvl, _a) in self.house.floor_info.items() if lvl is not None}
        if len(levels) >= 2:
            pick = min if word == "downstairs" else max
            return pick(levels, key=levels.get)
        hints = {"downstairs": ("basement", "lower", "downstairs", "cellar"),
                 "upstairs": ("upstairs", "upper", "second", "2nd", "top", "attic")}[word]
        found = [fid for fid, name in self.house.floors.items() if any(h in name.lower() for h in hints)]
        return found[0] if len(found) == 1 else None

    def candidates_in(self, domain: str, areas: set[str]) -> list[Entity]:
        return [e for e in self.house.exposed()
                if e.domain == domain and e.area in areas]
