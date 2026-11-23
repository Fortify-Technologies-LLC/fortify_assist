"""Build the core's picture of the home from Home Assistant's registries and states.

Only entities exposed to Assist are included, exactly as for the built-in agent, so exposure
settings, areas, floors and aliases the user already maintains are all respected.
"""
from __future__ import annotations

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

from .const import ASSISTANT, ATTRIBUTES, DOMAINS, SENSITIVE_COVER_CLASSES
from .core.types import Entity, House


def _attributes(state) -> dict:
    return {k: state.attributes[k] for k in ATTRIBUTES if k in state.attributes}


def build_house(hass: HomeAssistant) -> House:
    """Full rebuild. Call when registries or exposure change."""
    floor_list = list(fr.async_get(hass).async_list_floors())
    floors = {f.floor_id: f.name for f in floor_list}
    floor_info = {f.floor_id: (f.level, sorted(a for a in f.aliases if isinstance(a, str))) for f in floor_list}
    area_list = list(ar.async_get(hass).async_list_areas())
    areas = {a.id: (a.name, a.floor_id) for a in area_list}
    area_aliases = {a.id: sorted(x for x in a.aliases if isinstance(x, str)) for a in area_list if a.aliases}
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    entities: dict[str, Entity] = {}
    for state in hass.states.async_all():
        domain = state.entity_id.split(".", 1)[0]
        if domain not in DOMAINS or not async_should_expose(hass, ASSISTANT, state.entity_id):
            continue
        entry = ent_reg.async_get(state.entity_id)
        area_id = entry.area_id if entry else None
        if area_id is None and entry and entry.device_id and (device := dev_reg.async_get(entry.device_id)):
            area_id = device.area_id
        if area_id not in areas:
            area_id = None
        device_class = state.attributes.get("device_class")
        entities[state.entity_id] = Entity(
            id=state.entity_id,
            name=state.name,
            area=area_id,
            state=state.state,
            aliases=sorted(a for a in entry.aliases if isinstance(a, str)) if entry else [],  # may hold non-string markers
            sensitive=domain == "cover" and device_class in SENSITIVE_COVER_CLASSES,
            device_class=device_class,
            unit=state.attributes.get("unit_of_measurement"),
            attributes=_attributes(state),
        )
    return House(floors, areas, entities, floor_info, area_aliases)


def refresh_states(hass: HomeAssistant, house: House) -> None:
    """Cheap per-request update of state and attributes on an already built house."""
    for entity in house.entities.values():
        state = hass.states.get(entity.id)
        if state is None:
            entity.state, entity.attributes = "unavailable", {}
        else:
            entity.state, entity.attributes = state.state, _attributes(state)
