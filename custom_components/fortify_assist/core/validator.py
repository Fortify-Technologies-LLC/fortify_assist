"""Last line of defence: every action is checked against the live house before it runs.

Whatever produced the action (rules today, an LLM later), it cannot reach a service call
unless the entity exists, is exposed and available, the service belongs to its domain,
and the values are in range.
"""
from __future__ import annotations

from .controls import COLORS, HVAC_MODES, can_clean_area
from .music import MEDIA_TYPES
from .types import Action, House

_ONOFF = {"turn_on", "turn_off", "toggle"}
SERVICES: dict[str, set[str]] = {
    "light": _ONOFF, "switch": _ONOFF, "fan": _ONOFF | {"set_percentage", "increase_speed", "decrease_speed"},
    "input_boolean": _ONOFF, "humidifier": _ONOFF, "siren": _ONOFF, "remote": _ONOFF,
    "media_player": _ONOFF | {"media_play", "media_pause", "media_stop", "volume_set",
                              "volume_up", "volume_down", "media_next_track",
                              "media_previous_track", "volume_mute", "select_source", "play_media"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position", "toggle"},
    "valve": {"open_valve", "close_valve", "stop_valve"},
    "lock": {"lock", "unlock"},
    "alarm_control_panel": {"alarm_disarm"},
    "climate": {"set_temperature", "set_hvac_mode", "turn_on", "turn_off"},
    "scene": {"turn_on"},
    "script": {"turn_on", "turn_off"},
    "vacuum": {"start", "pause", "stop", "return_to_base", "clean_area"},
}
RANGES = {"brightness_pct": (0, 100), "volume_level": (0.0, 1.0), "temperature": (5, 95),
          "percentage": (0, 100), "position": (0, 100), "brightness_step_pct": (-100, 100),
          "color_temp_kelvin": (1000, 10000)}
ENUMS = {"color_name": COLORS, "hvac_mode": HVAC_MODES, "media_type": set(MEDIA_TYPES),
         "enqueue": {"replace", "play", "next", "add", "replace_next"}, "media_content_type": {"app"}}
BOOLS = {"is_volume_muted", "radio_mode"}
STRINGS = {"media_id": 400, "source": 120, "media_content_id": 200}  # a music item, a TV source, an app package
EXTERNAL = {"music_assistant.play_media": "media_player"}  # services of another integration, and who they target


def validate(action: Action, house: House) -> str | None:
    """None if the action may run, otherwise a short reason suitable for the user."""
    entity = house.entities.get(action.entity_id)
    if entity is None:
        return f"{action.entity_id} does not exist."
    if not entity.exposed:
        return f"{entity.name} is not available to the assistant."
    if not entity.available:
        return f"{entity.name} is unavailable right now."
    domain, _, service = action.service.partition(".")
    if action.service in EXTERNAL:
        if entity.domain != EXTERNAL[action.service]:
            return f"{action.service} cannot control {entity.name}."
    elif domain != entity.domain:
        return f"{action.service} cannot control {entity.name}."
    elif service not in SERVICES.get(domain, ()):
        return f"I can't do {service.replace('_', ' ')} on {entity.name}."
    for key, value in action.data_dict().items():
        if key in ENUMS:
            if value not in ENUMS[key]:
                return f"{value} is not a valid {key.replace('_', ' ')}."
            continue
        if key in STRINGS:
            if not isinstance(value, str) or not value.strip() or len(value) > STRINGS[key]:
                return f"{key.replace('_', ' ')} is not usable."
            continue
        if key in BOOLS:
            if not isinstance(value, bool):
                return f"{key.replace('_', ' ')} must be yes or no."
            continue
        if key == "cleaning_area_id":  # rooms of this home, for a robot that can be sent to rooms
            if not (isinstance(value, (list, tuple)) and value and all(a in house.areas for a in value)):
                return "Those are not rooms in this home."
            if not can_clean_area(entity):
                return f"{entity.name} can't clean a single room."
            continue
        if key == "hs_color":  # hue 0-360, saturation 0-100: only ever a colour the light already had
            if not (isinstance(value, (tuple, list)) and len(value) == 2
                    and all(isinstance(v, (int, float)) for v in value)
                    and 0 <= value[0] <= 360 and 0 <= value[1] <= 100):
                return f"{value} is not a usable colour."
            continue
        bounds = RANGES.get(key)
        if bounds is None:
            return f"Unsupported setting {key}."
        if not isinstance(value, (int, float)) or not bounds[0] <= value <= bounds[1]:
            return f"{value} is out of range for {key.replace('_', ' ')}."
    return None
