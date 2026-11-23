"""Personality response packs.

Every user-facing sentence the deterministic pipeline produces is a (key, slots) pair. A
personality is a pack of phrasings for those keys. The *facts* (device names, states,
counts) always come from slots, so changing personality can change tone but never accuracy.
Packs are validated: each variant must mention every required slot and nothing unknown.
"""
from __future__ import annotations

import json
import random
import string
from pathlib import Path

from .persona import Persona

PACK_DIR = Path(__file__).parent / "personalities"
IMPLICIT = {"agent", "spoken"}  # always available to any variant
GERUNDS = {"turn_on": "Turning on", "turn_off": "Turning off", "open": "Opening", "close": "Closing",
           "lock": "Locking", "unlock": "Unlocking", "update": "Updating"}
KEYS: dict[str, set[str]] = {
    "done": {"verb", "names"},
    "answer": {"name", "state"},
    "answer_unavailable": {"name"},
    "clarify_options": {"options"},
    "clarify_one": {"name"},
    "clarify_room": {"domain"},
    "confirm": {"verb", "names"},
    "cancelled": set(),
    "doing_it": set(),
    "not_found": {"target"},
    "none_there": {"domain"},
    "no_sensor": {"sensor"},
    "exec_ok": set(),
    "exec_partial": {"done", "total", "detail"},
    "exec_failed": {"detail"},
    "identity_name": set(),
    "identity_acronym": {"expansion"},
    "identity_no_acronym": set(),
    "identity_maker": {"maker"},
    "identity_purpose": {"purpose"},
    "identity_owner": {"owner"},
    "identity_unknown": set(),
    "qa_none": {"what", "state"},
    "qa_count": {"count", "what", "be", "state"},
    "qa_list": {"count", "what", "be", "state", "names"},
    "qa_any_yes": {"count", "what", "be", "state", "names"},
    "qa_all_yes": {"what", "state"},
    "qa_all_no": {"what", "state", "names"},
    "qa_attr": {"name", "what", "value"},
    "qa_playing": {"name", "title"},
    "qa_no_data": {"name", "what"},
    "qa_set_to": {"name", "value"},
    "chat_off": set(),
    "cant_do": {"name", "what"},
    "qa_inv_list": {"count", "what", "where", "names"},
    "qa_inv_count": {"count", "what", "where"},
    "qa_inv_none": {"what", "where"},
    "no_action": set(),
    "ambience_done": {"theme", "where", "what"},
    "ambience_none": {"where"},
    "ambience_unknown": {"theme"},
    "now_playing": {"title", "name"},
    "music_not_found": {"query"},
    "music_unsupported": {"name"},
    "music_which": set(),
    "music_needs_library": set(),
    "skipped": {"names", "what"},
    "exclude_unclear": {"target"},
    "feed_headlines": {"source", "headlines"},
    "feed_status": {"source", "age", "title"},
    "feed_empty": {"source"},
    "feed_failed": {"source"},
    "feed_topic_none": {"topic"},
    "feeds_none": set(),
    "feed_stale": {"source", "age", "headlines"},
    "weather_now": {"report"},
    "weather_forecast": {"day", "report"},
    "weather_none": set(),
    "weather_failed": set(),
    "chat_unsure": set(),
    "capabilities": {"things"},
    "time_now": {"time"},
    "date_today": {"date"},
    "welcome": set(),
    "okay": set(),
    "rooms_list": {"names"},
    "scenes_list": {"names"},
    "no_history": set(),
    "no_schedule": set(),
    "home_unsure": set(),
    "error": set(),
    "secure_yes": set(),
    "secure_no": {"names"},
    "not_understood": set(),
}
OPTIONAL_SLOTS: dict[str, set[str]] = {"qa_inv_list": {"be"}, "qa_inv_count": {"be"}}


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def pack_errors(pack: dict, require_all: bool = False) -> list[str]:
    """Problems that would make a pack say something wrong. Empty list means safe to load."""
    errors = []
    responses = pack.get("responses", {})
    for key, variants in responses.items():
        if key not in KEYS:
            errors.append(f"unknown key {key!r}")
            continue
        if not variants or not all(isinstance(v, str) and v.strip() for v in variants):
            errors.append(f"{key}: needs at least one non-empty string")
            continue
        for v in variants:
            used = _fields(v)
            if KEYS[key] - used:
                errors.append(f"{key}: {v!r} is missing {sorted(KEYS[key] - used)}")
            if used - KEYS[key] - IMPLICIT - OPTIONAL_SLOTS.get(key, set()):
                errors.append(f"{key}: {v!r} uses unknown slot {sorted(used - KEYS[key] - IMPLICIT)}")
    if require_all:
        errors += [f"missing key {k!r}" for k in KEYS if k not in responses]
    return errors


_PACKS: dict[str, dict] = {}  # packs are read from disk once; Home Assistant must not do file I/O on its event loop


def preload() -> None:
    """Read and validate every pack. Blocking: call from an executor thread, once, before first use."""
    for path in sorted(PACK_DIR.glob("*.json")):
        load_pack(path.stem)


def list_personalities() -> list[dict]:
    if not _PACKS:
        preload()
    return [{"id": p["id"], "name": p["name"], "description": p.get("description", "")}
            for _, p in sorted(_PACKS.items())]


def load_pack(pack_id: str) -> dict:
    if pack_id in _PACKS:
        return _PACKS[pack_id]
    path = PACK_DIR / f"{pack_id}.json"
    if not path.exists():
        raise KeyError(f"unknown personality {pack_id!r}")
    pack = json.loads(path.read_text(encoding="utf-8"))
    if errors := pack_errors(pack, require_all=pack["id"] == "neutral"):
        raise ValueError(f"personality {pack_id!r} is invalid: {errors}")
    _PACKS[pack_id] = pack
    return pack


class Responder:
    """Picks a phrasing for a key and fills in the facts. Falls back to neutral per key."""

    def __init__(self, persona: Persona | None = None, rng: random.Random | None = None):
        self.persona = persona or Persona()
        self.rng = rng or random.Random()
        self._neutral = load_pack("neutral")
        try:
            self._pack = load_pack(self.persona.personality)
        except KeyError:
            self._pack = self._neutral
        overrides = {"responses": self.persona.overrides}
        if errors := pack_errors(overrides):
            raise ValueError(f"persona overrides are invalid: {errors}")
        self._overrides = self.persona.overrides
        self._last: dict[str, str] = {}

    @property
    def chat_style(self) -> str:
        return self._pack.get("chat_style") or self._neutral["chat_style"]

    def gerund(self, action: str) -> str:
        return self._pack.get("gerund", {}).get(action) or GERUNDS.get(action, GERUNDS["update"])

    def all(self, key: str) -> set[str]:
        """Every way this personality can say a slot-less line, to recognise one afterwards."""
        variants = self._overrides.get(key) or self._pack["responses"].get(key) or self._neutral["responses"][key]
        values = {"agent": self.persona.agent_name, "spoken": self.persona.spoken}
        return {(t := v.format_map(values))[:1].upper() + t[1:] for v in variants}

    def say(self, key: str, **slots) -> str:
        variants = (self._overrides.get(key) or self._pack["responses"].get(key)
                    or self._neutral["responses"][key])
        choices = [v for v in variants if v != self._last.get(key)] or variants  # avoid back-to-back repeats
        template = self.rng.choice(choices)
        self._last[key] = template
        values = {"agent": self.persona.agent_name, "spoken": self.persona.spoken, **slots}
        text = template.format_map(values)
        return text[:1].upper() + text[1:]
