"""Core data types shared by the pipeline, the eval harness and the HA glue."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    ACTION = "action"  # execute one or more service calls
    CLARIFY = "clarify"  # ambiguous: ask the user which one
    CONFIRM = "confirm"  # sensitive: ask before acting
    REFUSE = "refuse"  # unknown / unavailable / impossible: do nothing, say so
    ANSWER = "answer"  # state question answered from live state
    CHAT = "chat"  # general conversation, no device involved


@dataclass(frozen=True)
class Action:
    service: str  # e.g. "light.turn_on"
    entity_id: str  # one entity per Action; multi-target calls are expanded
    data: tuple[tuple[str, Any], ...] = ()

    @staticmethod
    def make(service: str, entity_id: str, data: dict[str, Any] | None = None) -> "Action":
        return Action(service, entity_id, tuple(sorted((data or {}).items())))

    def data_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass
class Outcome:
    kind: Kind
    actions: list[Action] = field(default_factory=list)  # for ACTION
    entities: list[str] = field(default_factory=list)  # for ANSWER / CLARIFY candidates
    message: str = ""
    handoff: bool = False  # CHAT only: Home Assistant's own Assist may know this one (timers, lists); else say message


@dataclass
class Entity:
    id: str
    name: str
    area: str | None = None
    state: str = "unknown"
    aliases: list[str] = field(default_factory=list)
    exposed: bool = True
    sensitive: bool = False
    device_class: str | None = None
    unit: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)  # brightness, volume_level, media_title, ...

    @property
    def domain(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def available(self) -> bool:
        # "unknown" is not "offline": radio and infrared blinds, template switches and some locks never report a
        # state yet still work. Only a device Home Assistant cannot reach is refused.
        return self.state != "unavailable"


@dataclass
class House:
    floors: dict[str, str]  # id -> name
    areas: dict[str, tuple[str, str | None]]  # id -> (name, floor id)
    entities: dict[str, Entity]
    floor_info: dict[str, tuple[int | None, list[str]]] = field(default_factory=dict)  # id -> (level, aliases)
    area_aliases: dict[str, list[str]] = field(default_factory=dict)  # id -> other names people use for the room

    def area_name(self, area_id: str | None) -> str | None:
        return self.areas[area_id][0] if area_id else None

    def exposed(self) -> list[Entity]:
        return [e for e in self.entities.values() if e.exposed]
