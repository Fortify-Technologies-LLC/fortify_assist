"""Mood and theme lighting: "make the workshop halloween colors", "make the bar feel cozy".

A theme is a short palette of (colour or white tone, brightness). The assistant spreads the palette
across the lights in the named room, but only changes what each light can really do, so a bulb that
only switches on and off is left alone and a tunable-white bulb gets a white tone instead of purple.
The language model may *design* a palette for a theme it has not been taught, but it can only choose
from the known colours and a brightness range; it never picks devices.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .controls import COLORS, WHITES, _kelvin_bounds, can_color, can_dim, can_white
from .llm import LLMClient, LLMError
from .text import tokens
from .types import Action, Entity

Spec = str | int  # a colour name, or a colour temperature in kelvin
Palette = tuple[tuple[Spec, int], ...]  # (spec, brightness percent)

WARM = 2200
THEMES: dict[str, Palette] = {
    "halloween": (("orange", 90), ("purple", 75), ("green", 60), ("orange", 100), ("purple", 90)),
    "spooky": (("purple", 35), ("green", 30), ("orange", 25)),
    "christmas": (("red", 90), ("green", 80), (2700, 90)),
    "valentines": (("red", 70), ("pink", 70), (WARM, 50)),
    "sunset": (("orange", 75), ("red", 60), ("pink", 60), (WARM, 60)),
    "sunrise": (("yellow", 70), ("orange", 65), (2700, 80)),
    "ocean": (("blue", 70), ("cyan", 60), ("teal", 65), (6000, 50)),
    "forest": (("green", 60), ("lime", 50), ("teal", 45)),
    "fire": (("orange", 70), ("red", 60), (2000, 60)),
    "cozy": ((WARM, 35),),
    "movie night": ((WARM, 12),),
    "focus": ((5500, 100),),
    "reading": ((3000, 85),),
    "bright": ((6000, 100),),
    "party": (("red", 100), ("blue", 100), ("green", 100), ("purple", 100), ("pink", 100), ("cyan", 100)),
    "neon": (("magenta", 85), ("cyan", 85), ("purple", 80)),
    "gaming": (("purple", 80), ("blue", 80), ("cyan", 80)),
    "patriotic": (("red", 90), (6000, 100), ("blue", 90)),
    "st patricks": (("green", 80), ("lime", 70), ("gold", 70)),
    "spring": (("pink", 70), ("yellow", 70), ("lavender", 70), ("turquoise", 60)),
    "winter": ((6500, 80), ("blue", 60), ("cyan", 55)),
    "space": (("indigo", 50), ("purple", 45), ("blue", 50)),
    "bedtime": (("orange", 8),),
    "rainbow": (("red", 100), ("orange", 100), ("yellow", 100), ("green", 100), ("blue", 100), ("purple", 100)),
}
ALIASES: dict[str, str] = {
    "halloween": "halloween", "spooky": "spooky", "scary": "spooky", "creepy": "spooky", "haunted": "spooky",
    "haunted house": "spooky", "horror": "spooky", "scary movie": "spooky", "horror movie": "spooky",
    "christmas": "christmas", "xmas": "christmas", "holiday": "christmas", "valentines": "valentines",
    "valentine": "valentines", "romantic": "valentines", "romance": "valentines", "date night": "valentines",
    "sunset": "sunset", "sundown": "sunset", "dusk": "sunset", "golden hour": "sunset", "sunrise": "sunrise",
    "dawn": "sunrise", "ocean": "ocean", "beach": "ocean", "underwater": "ocean", "sea": "ocean",
    "forest": "forest", "jungle": "forest", "woods": "forest", "nature": "forest", "fire": "fire",
    "campfire": "fire", "bonfire": "fire", "cozy cabin": "fire", "cabin": "fire", "lava": "fire",
    "cozy": "cozy", "cosy": "cozy", "warm": "cozy", "relaxing": "cozy", "relax": "cozy", "relaxed": "cozy",
    "chill": "cozy", "calm": "cozy", "snug": "cozy", "comfy": "cozy", "wind down": "cozy", "cozier": "cozy",
    "cosier": "cozy", "comfier": "cozy", "snugger": "cozy",
    "movie night": "movie night", "movie": "movie night", "cinema": "movie night", "theater": "movie night",
    "theatre": "movie night", "watching a movie": "movie night", "film": "movie night",
    "focus": "focus", "work": "focus", "working": "focus", "study": "focus", "studying": "focus",
    "concentrate": "focus", "concentration": "focus", "productive": "focus", "reading": "reading",
    "read": "reading", "bright": "bright", "energize": "bright", "energetic": "bright", "wake up": "bright",
    "party": "party", "disco": "party", "rave": "party", "club": "party", "dance": "party", "celebration": "party",
    "neon": "neon", "cyberpunk": "neon", "synthwave": "neon", "vaporwave": "neon", "retro": "neon",
    "arcade": "neon", "gaming": "gaming", "gamer": "gaming", "patriotic": "patriotic", "fourth of july": "patriotic",
    "4th of july": "patriotic", "st patricks": "st patricks", "st patrick": "st patricks", "irish": "st patricks",
    "spring": "spring", "easter": "spring", "pastel": "spring", "winter": "winter", "snow": "winter",
    "snowy": "winter", "ice": "winter", "icy": "winter", "arctic": "winter", "frozen": "winter",
    "space": "space", "galaxy": "space", "spaceship": "space", "bedtime": "bedtime", "sleep": "bedtime",
    "nighttime": "bedtime", "nightlight": "bedtime", "rainbow": "rainbow", "pride": "rainbow",
}
LOOKS = {"valentines": "valentine's", "st patricks": "St Patrick's Day", "cozy": "cozy"}
CUE_WORDS = {"make", "set", "turn", "give", "change", "put", "mood", "vibe", "vibes", "colors", "colours",
             "lighting", "ambiance", "ambience", "atmosphere", "feel", "look", "theme", "into", "scene",
             "cozier", "cosier", "comfier", "snugger"}  # "get the den a bit cozier" asks for a change by itself
CUE_PHRASES = ("i need to", "i want to", "i'm going to", "i am going to", "let's", "lets", "time for",
               "getting ready to", "about to", "i'm about to")
MUSIC_WORDS = {"play", "song", "songs", "music", "playlist", "album", "audiobook", "podcast", "radio",
               "artist", "track", "listen"}
HERE = {"here", "this room", "the room", "in here"}
_ALIAS_TOKENS = sorted(((tuple(a.split()), t) for a, t in ALIASES.items()), key=lambda p: -len(p[0]))


def label(theme: str) -> str:
    return LOOKS.get(theme, theme)


def canonical(text: str) -> str | None:
    """The theme a phrase asks for ('scary movie' -> 'spooky'), or None if it is not one we know."""
    found = _scan(tokens(text))
    return found[0] if found else None


def _scan(words: tuple[str, ...], blocked: set[int] = frozenset()) -> tuple[str, int, int] | None:
    """Longest known theme phrase in `words`, as (theme, start, end), avoiding blocked positions."""
    for alias, theme in _ALIAS_TOKENS:
        n = len(alias)
        for i in range(len(words) - n + 1):
            if words[i:i + n] == alias and not any(j in blocked for j in range(i, i + n)):
                return theme, i, i + n
    return None


def detect(text: str, find_areas: Callable[[str], set[str]]) -> tuple[str, str] | None:
    """(theme, area words) if the sentence asks for a mood in a room, else None. `text` is normalised."""
    words = tokens(text)
    if not words or set(words) & MUSIC_WORDS:
        return None
    if not (set(words) & CUE_WORDS or any(p in text for p in CUE_PHRASES)):
        return None
    area_text, blocked = None, set()
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            window = " ".join(words[i:i + size])
            if window in ("lights", "light", "lamps", "lamp") or not find_areas(window):
                continue
            area_text, blocked = window, set(range(i, i + size))
            break
        if area_text:
            break
    if area_text is None:
        area_text = "here" if (set(words) & {"here", "it"} or "this room" in text) else None
    found = _scan(words, blocked)
    if not found or area_text is None:
        return None
    return found[0], area_text


def _specs(light: Entity, spec: Spec, brightness: int) -> dict | None:
    """What this particular light can do for one palette entry, or None if it should be left alone."""
    if not can_dim(light):
        return None
    if isinstance(spec, int):
        if not can_white(light):
            return None
        lo, hi = _kelvin_bounds(light)
        return {"color_temp_kelvin": int(min(max(spec, lo), hi)), "brightness_pct": brightness}
    if can_color(light):
        return {"color_name": spec, "brightness_pct": brightness}
    if can_white(light) and spec in {"orange", "red", "yellow", "gold", "coral", "pink", "magenta"}:
        lo, hi = _kelvin_bounds(light)
        return {"color_temp_kelvin": int(min(max(WARM, lo), hi)), "brightness_pct": brightness}
    return None


def lights_in(lights: list[Entity]) -> list[Entity]:
    """Lights worth styling: drop group helpers whose members are also in the list."""
    ids = {e.id for e in lights}
    keep = []
    for e in lights:
        members = e.attributes.get("entity_id")
        if isinstance(members, (list, tuple)) and any(m in ids for m in members):
            continue
        keep.append(e)
    return sorted(keep, key=lambda e: e.name.lower())


def assign(lights: list[Entity], palette: Palette) -> list[Action]:
    """Spread a palette over the lights, in a stable order, keeping each light's own limits."""
    actions: list[Action] = []
    slot = 0
    for light in lights_in(lights):
        data = None
        for step in range(len(palette)):  # try the next palette entries until one fits this light
            spec, bright = palette[(slot + step) % len(palette)]
            if (data := _specs(light, spec, bright)) is not None:
                slot += step + 1
                break
        if data is not None:
            actions.append(Action.make("light.turn_on", light.id, data))
    return actions


# ---- a language model may design a palette for a theme we do not know --------------------------------

ALLOWED = sorted(COLORS | set(WHITES))
SCHEMA = {
    "type": "object",
    "properties": {"palette": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
        "type": "object",
        "properties": {"color": {"enum": ALLOWED}, "brightness": {"type": "integer", "minimum": 5, "maximum": 100}},
        "required": ["color", "brightness"], "additionalProperties": False}}},
    "required": ["palette"], "additionalProperties": False,
}
SYSTEM = ("You design lighting looks. Given a theme, choose 2 to 6 colours that evoke it, each with a brightness "
          "percentage from 5 to 100 (use low brightness for moody or dark themes). Only use the allowed colours: "
          + ", ".join(ALLOWED) + ".")


async def design(tiers: list[LLMClient], theme: str) -> Palette | None:
    for client in tiers:
        try:
            raw = await client.generate(SYSTEM, f"Theme: {theme}", SCHEMA)
            items = json.loads(raw)["palette"]
        except (LLMError, ValueError, KeyError, TypeError):
            continue
        palette: list[tuple[Spec, int]] = []
        for item in items if isinstance(items, list) else []:
            color, level = item.get("color") if isinstance(item, dict) else None, item.get("brightness") if isinstance(item, dict) else None
            if color not in ALLOWED or isinstance(level, bool) or not isinstance(level, int) or not 5 <= level <= 100:
                palette = []
                break
            palette.append((WHITES[color] if color in WHITES else color, level))
        if palette:
            return tuple(palette)
    return None
