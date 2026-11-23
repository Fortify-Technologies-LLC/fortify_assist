"""Load a House from YAML (synthetic fixture or an export of a real instance)."""
from __future__ import annotations

from pathlib import Path

import yaml

from .types import Entity, House


def load_house(path: str | Path) -> House:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    floors = {f["id"]: f["name"] for f in raw.get("floors", [])}
    floor_info = {f["id"]: (f.get("level"), list(f.get("aliases", []))) for f in raw.get("floors", [])}
    areas = {a["id"]: (a["name"], a.get("floor")) for a in raw.get("areas", [])}
    entities: dict[str, Entity] = {}
    for e in raw["entities"]:
        entity = Entity(
            id=e["id"],
            name=e["name"],
            area=e.get("area"),
            state=str(e.get("state", "unknown")),
            aliases=list(e.get("aliases", [])),
            exposed=bool(e.get("exposed", True)),
            sensitive=bool(e.get("sensitive", False)),
            device_class=e.get("device_class"),
            unit=e.get("unit"),
            attributes=dict(e.get("attributes", {})),
        )
        if entity.area and entity.area not in areas:
            raise ValueError(f"{entity.id}: unknown area {entity.area!r}")
        if entity.id in entities:
            raise ValueError(f"duplicate entity id {entity.id}")
        entities[entity.id] = entity
    return House(floors, areas, entities, floor_info)
