"""LLM as a paraphrase normaliser: spoken request -> the same Intent the rules produce.

The model never chooses a device. It rewrites "shut the blinds" as (close, "blinds") using
words from the request, and the deterministic resolver does everything else: entity match,
ambiguity, room context, sensitive-action confirmation, validation. That removes the
largest class of LLM control errors (wrong device) and keeps the prompt independent of how
many devices the house has.

What is left to go wrong is the verb, the value and the quoted target, and each is checked
here: the target must be words the user actually said, a value must be derivable from the
request, and the direction (on/off, open/close) must not contradict an explicit word.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import ambience
from .index import EntityIndex
from .controls import ADJUST_WORDS, OPTION_WORDS
from .intents import Intent
from .llm import LLMClient, LLMError
from .resolver import VERB_DOMAINS
from .text import token_sim, tokens

COMMANDS = ["turn_on", "turn_off", "open", "close", "lock", "unlock", "disarm", "pause", "play",
            "stop", "set_brightness", "set_temperature", "set_volume", "volume_up", "volume_down", "activate",
            "set_option", "adjust", "mute", "unmute", "next_track", "previous_track", "dock", "ambience"]
QUERIES = ["query_state", "query_value", "query_list", "query_count", "query_any", "query_all"]
GROUP_QUERIES = {"query_list", "query_count", "query_any", "query_all"}
GENERIC_CLASSES = {"anything", "everything", "something", "devices", "things"}
STATES = ["on", "off", "open", "closed", "locked", "unlocked"]
NEEDS_VALUE = {"set_brightness", "set_temperature", "set_volume"}
QUANTITIES = {"half": 50, "halfway": 50, "quarter": 25, "full": 100, "max": 100, "maximum": 100,
              "min": 0, "minimum": 0}
UP = {"on": {"on", "start", "enable", "fire", "power", "wake"},
      "off": {"off", "kill", "stop", "disable", "cut", "sleep"},
      "open": {"open", "raise", "lift", "up", "unroll"},
      "close": {"close", "shut", "lower", "down", "draw"}}
OPPOSITE = {"turn_on": "off", "turn_off": "on", "open": "close", "close": "open"}
# a request whose verb is one of these asks for a job, not a switch: "clean the kitchen" must never become
# "kitchen lights off", whatever a small model makes of it
OTHER_JOBS = {"clean", "vacuum", "hoover", "mop", "sweep", "wash", "cook", "bake", "feed", "water", "charge", "order",
              "buy", "call", "text", "email", "remind", "book", "pee", "poop", "walk", "spell", "read"}
SWITCH_VERBS = {"turn_on", "turn_off", "open", "close", "set_brightness", "set_option", "adjust", "toggle"}
OWN = {"turn_on": "on", "turn_off": "off", "open": "open", "close": "close"}
GLUE = {"in", "all", "of", "and", "on", "off", "to"}
SENSOR_WORDS = {"temperature", "humidity"}  # may stand in for "how warm / humid is it"
HOUSE_WORDS = {"house", "home"}
TYPE_WORDS = {"light", "lights", "lamp", "lamps", "bulb", "bulbs", "speaker", "speakers", "fan", "fans", "switch",
              "switches", "plug", "plugs", "tv", "television", "heater", "thermostat", "blinds", "shades", "curtain",
              "curtains", "door", "doors", "lock", "locks", "speakers"}
STATE_WORDS = {"on": {"on", "lit", "running", "active", "powered"}, "off": {"off"}, "open": {"open", "opened"},
               "closed": {"closed", "shut"}, "locked": {"locked"}, "unlocked": {"unlocked"}}
UP_WORDS = {"raise", "increase", "up", "brighter", "louder", "faster", "warmer", "hotter", "higher", "brighten",
            "lift", "boost", "more"}
DOWN_WORDS = {"lower", "decrease", "reduce", "down", "dim", "dimmer", "quieter", "slower", "cooler", "colder",
              "softer", "less", "dark", "darker"}

SYSTEM = """You convert a spoken smart-home request into JSON. You never choose a device; you copy the words the user used for it.

decision: "act" (a command), "query" (a question about a device's state or a sensor reading), "chat" (anything else: general knowledge, small talk, thanks), "unsure".
clauses: one per separate command or question. Each has:
- intent: turn_on, turn_off, open, close, lock, unlock, disarm, pause, play, stop, set_brightness, set_temperature, set_volume, volume_up, volume_down, activate, set_option, adjust, mute, unmute, next_track, previous_track, dock, ambience, query_state, query_value, query_list, query_count, query_any, query_all
- target: the device or place words copied from the request, without articles ("kitchen lights", "lamp in the bedroom", "blinds")
- value: a number, only for set_brightness (0-100), set_temperature or set_volume (0-100)
- state: for query_state and the group questions: on, off, open, closed, locked, unlocked
- option: for set_option one word or phrase copied from the request: a colour (red, blue...), a white tone (warm white, cool white, daylight), a level (low, medium, high, half, max) or a thermostat mode (heat, cool, auto). For adjust: brighter, dimmer, warmer, cooler, louder, quieter, faster, slower, up or down
- option for ambience: the mood or theme words copied from the request ("halloween", "cozy", "movie night"); its target is the room words, or "here" for this room
- area: only for group questions, a room name copied from the request
Group questions ask about many devices: query_list (which/what is...), query_count (how many...), query_any (is anything / are any...), query_all (are all / is every...). Their target is the kind of device ("lights", "doors", "windows") or "anything" for everything. "running/lit/active" = on, "shut" = closed.

Always keep the room words with the device in target. Examples (request -> result):
"den lights on" -> act: turn_on "den lights"
"cut the lights in the attic" -> act: turn_off "lights in the attic"
"make the heater 68" -> act: set_temperature "heater" value 68
"lift the shades" -> act: open "shades"
"the fan in the loft, switch it on" -> act: turn_on "fan in the loft"
"turn the stereo up" -> act: volume_up "stereo"
"how cold is it in the den" -> query: query_value "temperature in den"
"is the back gate shut" -> query: query_state "back gate" state closed
"which fans are running upstairs" -> query: query_list target "fans" area "upstairs" state on
"anything left on in the den" -> query: query_any target "anything" area "den" state on
"is every window shut" -> query: query_all target "windows" state closed
"give the den lamp a purple glow" -> act: set_option target "den lamp" option "purple"
"switch the attic fan to low" -> act: set_option target "attic fan" option "low"
"a touch brighter in the loft please" -> act: adjust target "it" option "brighter" (target "it" means this room)
"bring the porch lamp down a bit" -> act: adjust target "porch lamp" option "down"
"make the den feel like a campfire" -> act: ambience target "den" option "campfire"
"set a spooky mood in here" -> act: ambience target "here" option "spooky"
"who painted the Mona Lisa" -> chat

set_option is only for words (a colour, a white tone, low/medium/high/half/max, a mode). Any number goes in value with set_brightness (or set_temperature / set_volume), never in option.
"the hall lamp at 30 percent" -> act: set_brightness target "hall lamp" value 30

Map synonyms: kill/cut/shut off = turn_off; fire up/start/put on = turn_on; shut/lower = close; raise/lift = open; "make the thermostat 70" = set_temperature 70; half = 50. "how warm/cold/humid is it in the den" = query_value target "temperature in den" (or "humidity in den"). "turn it off" refers to the device named earlier in the sentence. Never invent a target. If the request names no device and is not a question about the home, use "chat". If a command lacks a needed value or you cannot tell, use "unsure"."""


@dataclass
class Parsed:
    intents: list[Intent]
    chat: bool = False


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "decision": {"enum": ["act", "query", "chat", "unsure"]},
            "clauses": {"type": "array", "maxItems": 4, "items": {
                "type": "object",
                "properties": {
                    "intent": {"enum": COMMANDS + QUERIES},
                    "target": {"type": "string"},
                    "value": {"type": "number"},
                    "state": {"enum": STATES},
                    "option": {"type": "string"},
                    "area": {"type": "string"},
                },
                "required": ["intent", "target"], "additionalProperties": False}},
        },
        "required": ["decision"],
        "additionalProperties": False,
    }


class SemanticParser:
    def __init__(self, index: EntityIndex, tiers: list[LLMClient]):
        self.index, self.house, self.tiers = index, index.house, tiers
        self.last_trace: list[str] = []

    async def parse(self, utterance: str, context_area: str | None = None) -> tuple[Parsed, str] | None:
        areas = ", ".join(name for name, _ in self.house.areas.values())
        user = (f"Room of the speaker: {self.house.area_name(context_area) or 'unknown'}\n"
                f"Rooms in the home: {areas}\nRequest: {utterance}")
        self.last_trace = []
        for client in self.tiers:
            try:
                raw = await client.generate(SYSTEM, user, _schema())
            except LLMError as exc:
                self.last_trace.append(f"{client.name}: error {exc}")
                continue
            self.last_trace.append(f"{client.name}: {' '.join(raw.split())[:240]}")
            parsed = self._validate(raw, utterance)
            if parsed is not None:
                return parsed, client.name
        return None

    # ---- checks on the model's output -------------------------------------------------

    def _validate(self, raw: str, utterance: str) -> Parsed | None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        decision = data.get("decision")
        if decision == "chat":
            return Parsed([], chat=True)
        if decision not in ("act", "query"):
            return None
        clauses = data.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            return None
        said = tokens(utterance)
        words = set(re.findall(r"[a-z]+", utterance.lower()))
        intents = []
        for c in clauses:
            intent = self._clause(c, said, words, decision)
            if intent is None:
                return None  # one bad clause voids the whole plan: never run part of it
            intents.append(intent)
        return Parsed(intents)

    def _clause(self, c, said: tuple[str, ...], words: set[str], decision: str) -> Intent | None:
        if not isinstance(c, dict):
            return None
        verb, target = c.get("intent"), c.get("target")
        allowed = COMMANDS if decision == "act" else QUERIES
        if verb not in allowed or not isinstance(target, str):
            return None
        if verb in SWITCH_VERBS and words & OTHER_JOBS and not words & (UP["on"] | UP["off"] | UP["open"] | UP["close"]):
            return None  # "have the robot clean the kitchen" read as "kitchen lights on": a different job entirely
        target_tokens = tokens(target.replace("_", " "))  # small models write "front_door"
        if not target_tokens or len(target_tokens) > 8:
            return None
        if verb in COMMANDS:  # "workshop lights" for "turn the workshop off": keep what the user said, drop the rest
            heard = [t for t in target_tokens if t in GLUE or any(token_sim(t, s) for s in said)]
            unsaid = [t for t in target_tokens if t not in heard]
            if unsaid and all(t in TYPE_WORDS for t in unsaid):
                said_types = {s for s in said if s in TYPE_WORDS}
                if said_types and not said_types & set(heard):
                    return None  # the user named a kind of device the model replaced: never swap fan for lights
                while heard and heard[-1] in GLUE:
                    heard.pop()
                while heard and heard[0] in GLUE:
                    heard.pop(0)
                target_tokens = tuple(heard)
                if not target_tokens:
                    return None
        for t in target_tokens:  # every word must come from the user, not the model
            if t in GLUE or (verb == "query_value" and t in SENSOR_WORDS):
                continue
            if verb in GROUP_QUERIES and t in GENERIC_CLASSES:
                continue
            if not any(token_sim(t, s) for s in said):
                return None
        if verb in COMMANDS and not target_tokens:
            return None
        raw_option = c.get("option") if isinstance(c.get("option"), str) else ""
        if verb == "set_option" and raw_option.strip().lower() not in OPTION_WORDS and ambience.canonical(raw_option):
            verb = "ambience"  # "cozy" is a mood, not a device option: small models often file it under set_option
        if verb == "ambience":  # a mood for a room: the theme words must be the user's, and no device is named
            theme = tokens(raw_option)
            said_theme = ambience.canonical(" ".join(said))
            if not theme or len(theme) > 4:
                return None
            if not all(any(token_sim(w, s) for s in said) for w in theme) and \
                    not (said_theme and ambience.canonical(raw_option) == said_theme):
                return None  # "cozy" for "a bit cozier" is fine; a theme the user never asked for is not
            return Intent("ambience", " ".join(target_tokens), option=" ".join(theme))
        area = None
        if verb in GROUP_QUERIES:
            raw_state = c.get("state")
            if raw_state is not None and raw_state not in STATES:
                return None
            if raw_state in STATES and not (STATE_WORDS[raw_state] & words):
                raw_state = None  # a state the user never mentioned: the model added it, so drop it
            if raw_state is None and verb in ("query_any", "query_all"):
                return None
            c = {**c, "state": raw_state}
            raw_area = c.get("area")
            if isinstance(raw_area, str) and raw_area.strip():
                area_tokens = tokens(raw_area)
                if not area_tokens or len(area_tokens) > 4:
                    return None
                for t in area_tokens:
                    if t not in HOUSE_WORDS and not any(token_sim(t, s) for s in said):
                        return None
                area = " ".join(area_tokens)
        option = None
        if verb == "set_option" and isinstance(c.get("option"), str) and re.fullmatch(
                r"\s*\d+(?:\.\d+)?\s*(?:%|percent)?\s*", c["option"]):
            verb = "set_brightness"  # a number is a level to set, not an option word
            c = {**c, "value": float(re.sub(r"[^\d.]", "", c["option"]))}
        if verb == "set_option":
            raw = c.get("option")
            if not isinstance(raw, str) or raw.strip().lower() not in OPTION_WORDS:
                return None
            option = raw.strip().lower()
            if not all(any(token_sim(w, s) for s in said) for w in option.split()):
                return None  # the colour or level must be something the user actually said
        elif verb == "adjust":
            raw = c.get("option")
            if not isinstance(raw, str) or raw.strip().lower() not in ADJUST_WORDS:
                return None
            option = raw.strip().lower()
            if option not in ("up", "down") and option not in words:
                return None
            wants_up = option in UP_WORDS
            clash = (words & (DOWN_WORDS if wants_up else UP_WORDS)) and not (words & (UP_WORDS if wants_up else DOWN_WORDS))
            if clash:
                return None  # direction contradicts what was said
        value = c.get("value")
        if verb in ("turn_on", "open") and value is not None and self._derive_value(value, said, words) is not None:
            verb = "set_brightness"  # "put the lights on 20 percent" read as turn_on 20: the number is the point
        elif any(re.fullmatch(r"\d+(\.\d+)?", s) for s in said) and verb in COMMANDS \
                and verb not in NEEDS_VALUE | {"adjust", "activate"}:
            return None  # the user said a number and the model's command has no place for it: never drop it
        if verb == "adjust" and value is not None:
            value = self._derive_value(value, said, words)
            if value is None:
                return None
        elif verb in NEEDS_VALUE:
            value = self._derive_value(value, said, words)
            if value is None:
                return None
        else:
            value = None
        state = c.get("state") if verb == "query_state" or verb in GROUP_QUERIES else None
        if verb == "query_state" and state not in STATES:
            return None
        if verb in OPPOSITE and self._contradicts(verb, words):
            return None
        hint = None
        text = " ".join(target_tokens)
        if verb == "activate":
            for w in ("scene", "script"):
                if text.endswith(" " + w):
                    text, hint = text[: -len(w) - 1], w
        if verb not in GROUP_QUERIES and verb != "query_value" and text not in ("it", "this room", "the room"):
            text = self._widen(text, said, verb)
        return Intent(verb, text, value, state, hint, area, option=option)

    def _widen(self, text: str, said: tuple[str, ...], verb: str) -> str:
        """The model may drop the words that tell two devices apart ("workshop overhead light A" became
        "workshop lights", which is the whole group). If the user's own sentence contains a longer,
        exact device name that includes everything the model kept, use that name instead."""
        target = tuple(text.split())
        domains = set(VERB_DOMAINS.get(verb, ())) or None
        for size in range(min(len(said), 7), len(target), -1):
            for start in range(len(said) - size + 1):
                window = said[start:start + size]
                if not all(any(token_sim(t, w) for w in window) for t in target):
                    continue
                found = self.index.find(" ".join(window), domains)
                if found and found[0].score >= 0.95:
                    return " ".join(window)
        return text

    @staticmethod
    def _derive_value(value, said: tuple[str, ...], words: set[str]):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if any(re.fullmatch(r"\d+(\.\d+)?", t) and abs(float(t) - value) < 0.01 for t in said):
            return value
        if any(QUANTITIES.get(w) == value for w in words):
            return value
        return None

    @staticmethod
    def _contradicts(verb: str, words: set[str]) -> bool:
        """True if the user said an explicit opposite word and none supporting the verb."""
        opposite = UP[OPPOSITE[verb]]
        own = UP[OWN[verb]]
        return bool(words & opposite) and not words & own


__all__ = ["Parsed", "SemanticParser"]
