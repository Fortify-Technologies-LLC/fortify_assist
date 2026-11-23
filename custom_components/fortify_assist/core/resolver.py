"""Turn an Intent into an Outcome by resolving its target against the entity index.

Policy: act only when exactly one entity clearly matches. Anything else becomes a
question (CLARIFY), a confirmation (CONFIRM) or an honest refusal (REFUSE). A command
that matches nothing is REFUSE; a question that matches nothing is CHAT, so a language
model can take it later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .index import EntityIndex, Match
from .intents import Intent
from . import ambience, controls, qa
from .persona import Persona
from .responses import Responder
from .text import GENERIC as GENERIC_WORDS, ceiling_same, phrase_sim, tokens
from .types import Action, Entity, Kind, Outcome

ONOFF = {"light", "switch", "fan", "media_player", "input_boolean", "humidifier", "siren", "remote"}
VERB_DOMAINS: dict[str, set[str]] = {
    "turn_on": ONOFF | {"scene", "script", "vacuum", "climate"},  # "turn on the roomba" starts it
    "turn_off": ONOFF | {"vacuum", "climate"},  # and "turn it off" sends it home; "turn off the thermostat"
    "toggle": ONOFF,
    "open": {"cover", "valve"},
    "close": {"cover", "valve"},
    "lock": {"lock"},
    "unlock": {"lock"},
    "disarm": {"alarm_control_panel"},
    "volume_up": {"media_player"},
    "volume_down": {"media_player"},
    "pause": {"media_player", "vacuum"},
    "play": {"media_player", "vacuum"},
    "stop": {"media_player", "vacuum"},
    "set_brightness": {"light", "media_player", "fan", "cover"},
    "set_number": {"climate", "light", "media_player", "fan", "cover"},
    "set_option": {"light", "fan", "cover", "climate", "media_player"},
    "adjust": {"light", "fan", "cover", "climate", "media_player"},
    "mute": {"media_player"},
    "unmute": {"media_player"},
    "next_track": {"media_player"},
    "previous_track": {"media_player"},
    "dock": {"vacuum"},
    "set_volume": {"media_player"},
    "set_temperature": {"climate"},
    "activate": {"scene", "script", "vacuum"},
    "launch": {"media_player"},
}
ONE_OF = re.compile(r"^(?:1|2|3|any|some|a few|a couple) of (?P<rest>.+)$")
HERE = {"here", "it", "this room", "the room", "in here", "room", ""}
ACTIVE_WORDS = {"music", "song", "songs", "track", "tracks", "audio", "audiobook", "book", "podcast", "playback",
                "tunes", "the music"}  # "pause the music": whatever is playing
TRANSPORT = ("pause", "play", "stop", "next_track", "previous_track", "mute", "unmute")
CLIMATE_WORDS = {"heat", "heating", "temperature", "thermostat", "ac", "air conditioning", "air conditioner"}
STATE_DOMAINS = {
    "on": ONOFF - {"remote"} | {"binary_sensor"},
    "off": ONOFF - {"remote"} | {"binary_sensor"},
    "open": {"cover", "binary_sensor", "valve"},
    "closed": {"cover", "binary_sensor", "valve"},
    "locked": {"lock"},
    "unlocked": {"lock"},
}
ALWAYS_CONFIRM = {"lock.unlock", "alarm_control_panel.alarm_disarm"}
CONFIRM_IF_SENSITIVE = {"cover.open_cover", "cover.set_cover_position", "valve.open_valve",
                        "switch.turn_on", "switch.turn_off", "switch.toggle"}  # a garage or gate opener wired as a switch
SENSITIVE_WORDS = {"cover": re.compile(r"\b(?:garage|gate|door|doors)\b", re.I),
                   "switch": re.compile(r"\b(?:garage|gate)\b", re.I)}
SENSITIVE_CLASSES = {"garage", "gate", "door"}
CONFIRM_VERBS = {"lock.unlock": "unlock", "alarm_control_panel.alarm_disarm": "disarm", "cover.open_cover": "open",
                 "cover.set_cover_position": "move", "valve.open_valve": "open", "switch.turn_on": "switch on",
                 "switch.turn_off": "switch off", "switch.toggle": "switch"}
GENERIC = re.compile(r"^(?:all |every )?(light|lights|fan|fans|switch|switches|tv|tvs)$")
GENERIC_DOMAIN = {"light": "light", "lights": "light", "fan": "fan", "fans": "fan",
                  "switch": "switch", "switches": "switch", "tv": "tv", "tvs": "tv"}  # "tv": media players that are TVs
PLURAL_CLASSES = {"lights", "fans", "switches", "tvs"}
# the last word of a name says what kind of thing is meant: "tv lights" is a light, never the TV
TYPE_DOMAIN = {"light": {"light"}, "lights": {"light"}, "lamp": {"light"}, "lamps": {"light"}, "bulb": {"light"},
               "tv": {"media_player"}, "tvs": {"media_player"}, "speaker": {"media_player"},
               "speakers": {"media_player"}, "soundbar": {"media_player"}, "fan": {"fan"}, "fans": {"fan"},
               "lock": {"lock"}, "thermostat": {"climate"}, "blinds": {"cover"}, "curtains": {"cover"},
               "shades": {"cover"}}
SENSOR_CLASSES = {"temperature", "humidity"}
EVERYTHING = re.compile(r"^(?:everything|all|every light|all lights|all of lights|every lights)$")
ROOM_VERBS = {"turn_on", "turn_off", "toggle", "adjust", "set_option", "set_brightness", "set_number"}  # "dim the workshop"
SERVICE_VERBS = {"media_player.media_pause": "Pausing", "media_player.media_play": "Resuming",
                 "media_player.media_stop": "Stopping", "media_player.media_next_track": "Skipping ahead on",
                 "media_player.media_previous_track": "Going back on", "media_player.volume_up": "Turning up",
                 "media_player.volume_down": "Turning down", "scene.turn_on": "Starting", "script.turn_on": "Starting",
                 "vacuum.start": "Starting", "vacuum.pause": "Pausing", "vacuum.stop": "Stopping",
                 "vacuum.return_to_base": "Sending home", "alarm_control_panel.alarm_disarm": "Disarming",
                 "cover.stop_cover": "Stopping"}
ALL_LOCKS = {"all doors", "all locks", "doors", "locks", "every door", "everything", "house"}
NOT_ON = ("off", "unavailable", "unknown", "standby", "idle")
SPLIT_LIST = re.compile(r"\s*,\s*|\s+and\s+")


@dataclass
class Resolution:
    outcome: Outcome
    intent: Intent | None = None
    candidates: list[Entity] = field(default_factory=list)  # for CLARIFY follow-ups
    pending_actions: list[Action] = field(default_factory=list)  # for CONFIRM follow-ups
    escalate: bool = False  # rules found no device; worth asking the LLM tier
    needs_design: str | None = None  # a theme we have no palette for; a language model may design one


def label(e: Entity, house) -> str:
    area = house.area_name(e.area)
    if area and area.lower() not in e.name.lower():
        return f"{e.name} in the {area}"
    return e.name


def sensitive(e: Entity) -> bool:
    """Opening this lets someone in, or could hurt someone: a garage, gate or door, whatever the home called its
    device class, a garage or gate opener wired as a switch, or a valve."""
    if e.sensitive or e.domain == "valve":
        return True
    if e.domain == "cover" and e.device_class in SENSITIVE_CLASSES:
        return True
    words = SENSITIVE_WORDS.get(e.domain)
    return bool(words and any(words.search(n) for n in [e.name, *e.aliases]))


def _num(v: float) -> int | float:
    return int(v) if float(v).is_integer() else v


def _service(intent: Intent, e: Entity) -> tuple[str, dict]:
    d, v = e.domain, intent.verb
    if d == "vacuum":
        return {"activate": "vacuum.start", "play": "vacuum.start", "pause": "vacuum.pause", "turn_on": "vacuum.start",
                "stop": "vacuum.stop", "dock": "vacuum.return_to_base",
                "turn_off": "vacuum.return_to_base"}.get(v, "vacuum.start"), {}
    if v in ("mute", "unmute"):
        return "media_player.volume_mute", {"is_volume_muted": v == "mute"}
    if v in ("next_track", "previous_track"):
        return f"media_player.media_{v}", {}
    if v in ("turn_on", "turn_off", "activate"):
        return f"{d}.{'turn_off' if v == 'turn_off' else 'turn_on'}", {}
    if v in ("open", "close"):
        return (f"{d}.{v}_{d}" if d == "valve" else f"{d}.{v}_cover"), {}
    if v in ("lock", "unlock"):
        return f"lock.{v}", {}
    if v == "disarm":
        return "alarm_control_panel.alarm_disarm", {}
    if v in ("volume_up", "volume_down"):
        return f"media_player.{v}", {}
    if v in ("pause", "play", "stop"):
        return f"media_player.media_{v}", {}
    if v == "set_temperature":
        return "climate.set_temperature", {"temperature": _num(intent.value)}
    if v == "set_volume" or (v == "set_brightness" and d == "media_player"):
        return "media_player.volume_set", {"volume_level": round(intent.value / 100, 4)}
    return "light.turn_on", {"brightness_pct": _num(intent.value)}


class Resolver:
    def __init__(self, index: EntityIndex, confirm_sensitive: bool = True, responder: Responder | None = None):
        self.index = index
        self.house = index.house
        self.confirm_sensitive = confirm_sensitive
        self.responder = responder or Responder()
        self.say = self.responder.say

    # ---- public ----------------------------------------------------------------

    def resolve(self, intent: Intent, context_area: str | None = None) -> Resolution:
        if intent.verb == "ambience":
            return self.ambience(intent, context_area)
        if intent.verb.startswith("identity_"):
            return self._identity(intent)
        if intent.verb in ("query_list", "query_count", "query_any", "query_all"):
            return self._group_query(intent, context_area)
        if intent.verb in ("query_attr", "query_playing"):
            return self._attr_query(intent, context_area)
        if intent.verb in ("query_secure", "query_status"):
            return self._secure(intent)
        if intent.verb == "query_state":
            return self._query_state(intent, context_area)
        if intent.verb == "query_value":
            return self._query_value(intent, context_area)
        if intent.verb == "clean_area":
            return self._clean_area(intent, context_area)
        return self._command(intent, context_area)

    def _clean_area(self, intent: Intent, context_area: str | None) -> Resolution:
        """'vacuum the kitchen': a robot that can clean single rooms goes to those rooms. Rooms that are not rooms
        ('clean the coffee maker') are not this, and a robot that can only clean everything is never sent off
        to do the whole house when one room was asked for."""
        where = intent.area or ""
        rooms = (({context_area} if context_area else set()) if where in HERE else self.areas_in(where))
        if not rooms:
            return Resolution(Outcome(Kind.CHAT), intent)
        bots = [e for e in self.house.exposed() if e.domain == "vacuum"]
        if intent.target:
            named = [m.entity for m in self.index.find(intent.target, {"vacuum"}) if m.score >= 0.85]
            if not named:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("not_found", target=intent.target)), intent)
            bots = named[:1]
        if not bots:
            return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="vacuum")), intent)
        names = qa.join_names([self.house.area_name(a) for a in sorted(rooms)])

        def floor(area: str | None) -> str | None:
            return self.house.areas.get(area, (None, None))[1] if area else None

        floors = {floor(a) for a in rooms} - {None}
        if floors and not intent.target:  # a robot cannot climb stairs: only one on that floor (or of unknown place)
            bots = [e for e in bots if floor(e.area) in floors or floor(e.area) is None]
            if not bots:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="vacuum")), intent)
        able = [e for e in bots if controls.can_clean_area(e)]
        live = [e for e in able if e.available]
        if len(live) > 1:
            return self._ask(intent, [Match(e, 1.0) for e in live])
        if live:
            bot = live[0]
            action = Action.make("vacuum.clean_area", bot.id, {"cleaning_area_id": tuple(sorted(rooms))})
            return Resolution(Outcome(Kind.ACTION, [action], entities=[bot.id], message=self.say(
                "done", verb="Sending", names=f"{bot.name} to clean the {names}")), intent)
        if len(bots) == 1 and not bots[0].available:
            return Resolution(Outcome(Kind.REFUSE, message=self.say("answer_unavailable",
                                                                   name=label(bots[0], self.house))), intent)
        whole = [e for e in bots if e.available and not controls.can_clean_area(e)]
        if len(whole) == 1 and floors:  # the only robot on that floor, and it only knows "clean everything"
            bot = whole[0]
            return Resolution(Outcome(Kind.ACTION, [Action.make("vacuum.start", bot.id)], entities=[bot.id],
                                      message=self.say("done", verb="Starting",
                                                       names=f"{bot.name}, which cleans its whole floor")), intent)
        if able:  # a robot that could do it is offline
            return Resolution(Outcome(Kind.REFUSE, message=self.say("answer_unavailable",
                                                                   name=label(able[0], self.house))), intent)
        what = label(bots[0], self.house) if len(bots) == 1 else "your vacuums"
        return Resolution(Outcome(Kind.REFUSE, message=self.say("cant_do", name=what,
                                                               what="clean a single room")), intent)

    def finalize(self, intent: Intent, entities: list[Entity]) -> Resolution:
        """Build the outcome once the target entities are decided (also used after a clarification)."""
        if intent.verb == "toggle":  # off if any of them is on, otherwise on: never half the room each way
            lit = any(e.state not in NOT_ON for e in entities)
            intent = replace(intent, verb="turn_off" if lit else "turn_on")
        actions, problems = [], []
        for e in entities:
            planned = self._plan(intent, e)
            if planned.problem:
                problems.append((e, planned.problem))
            actions += planned.actions
        if not actions:  # nothing could do it: say what is missing instead of failing silently
            e, what = problems[0]
            who = label(e, self.house) if len(entities) == 1 else "None of those"
            return Resolution(Outcome(Kind.REFUSE, message=self.say("cant_do", name=who, what=what)), intent)
        res = self.gate(actions, intent.verb, intent.verb.replace("_", " "))
        res.intent = intent
        if problems and res.outcome.kind is Kind.ACTION:
            left = [label(e, self.house) for e, _ in problems]
            res.outcome.message += " " + self.say("skipped", names=qa.join_names(left), what=problems[0][1])
        return res

    @staticmethod
    def _plan(intent: Intent, e: Entity) -> controls.Result:
        v = intent.verb
        if v == "launch":
            return controls.launch(e, intent.option)
        if v == "set_option":
            return controls.set_option(e, intent.option)
        if v == "adjust":
            word = intent.option if intent.option not in ("up", "down") else None
            up = intent.option in ("up", "brighter", "warmer", "hotter", "louder", "faster")
            amount = intent.value
            if intent.state_word == "scale" and amount:  # "a little" / "a lot": scale the default step
                amount = {"light": 20, "climate": 2, "fan": 20, "media_player": 10, "cover": 10}.get(e.domain, 20) * amount
                if word in ("warmer", "cooler", "colder") and e.domain == "light":
                    amount = 600 * intent.value
            return controls.adjust(e, "up" if up else "down", amount, word)
        if v == "set_brightness":
            return controls.set_level(e, intent.value)
        if v == "set_number":
            if e.domain == "climate":
                return controls.Result([Action.make("climate.set_temperature", e.id, {"temperature": _num(intent.value)})])
            return controls.set_level(e, intent.value)
        service, data = _service(intent, e)
        return controls.Result([Action.make(service, e.id, data)])

    def gate(self, actions: list[Action], action: str = "update", verb: str = "do that to") -> Resolution:
        """Apply the sensitive-action policy to any list of actions, whoever proposed them."""
        ents = [self.house.entities[a.entity_id] for a in actions]
        confirm = [a for a, e in zip(actions, ents) if self._needs_confirm(a, e)]
        if confirm and self.confirm_sensitive:
            names = ", ".join(label(self.house.entities[a.entity_id], self.house) for a in confirm)
            verb = CONFIRM_VERBS.get(confirm[0].service, verb)  # what will really happen, not the words of the request
            return Resolution(
                Outcome(Kind.CONFIRM, entities=[a.entity_id for a in confirm],
                        message=self.say("confirm", verb=verb, names=names)),
                pending_actions=actions)
        said, names = self._wording(actions), self.names(ents)
        if said:
            verb_text, tail = said
            return Resolution(Outcome(Kind.ACTION, actions, message=self.say("done", verb=verb_text, names=names + tail)))
        return Resolution(Outcome(Kind.ACTION, actions,
                                  message=self.say("done", verb=self.responder.gerund(action), names=names)))

    @staticmethod
    def _wording(actions: list[Action]) -> tuple[str, str] | None:
        """Say what is changing when every device gets the same change: "Setting X to 72 degrees", "Pausing X"."""
        if len({(a.service, a.data) for a in actions}) != 1:
            return None
        service, data = actions[0].service, actions[0].data_dict()
        if service == "media_player.select_source":
            return f"Opening {data.get('source')} on", ""
        if service == "media_player.play_media":
            return f"Opening {controls.APP_NAMES.get(data.get('media_content_id'), 'that')} on", ""
        if not data:
            verb = SERVICE_VERBS.get(service) or SERVICE_VERBS.get(service.split(".", 1)[1])
            return (verb, "") if verb else None
        if len(data) != 1:
            return None
        key, v = next(iter(data.items()))
        if key == "is_volume_muted":
            return ("Muting" if v else "Unmuting"), ""
        if key == "brightness_step_pct":
            return ("Brightening" if v > 0 else "Dimming"), ""
        tail = {"temperature": f"{_num(v)} degrees" if isinstance(v, (int, float)) else None,
                "brightness_pct": f"{v} percent", "percentage": f"{v} percent", "position": f"{v} percent",
                "volume_level": f"{round(v * 100)} percent" if isinstance(v, (int, float)) else None,
                "color_name": str(v), "hvac_mode": str(v)}.get(key)
        return ("Setting", f" to {tail}") if tail else None

    def names(self, ents: list[Entity]) -> str:
        """Up to three devices by name; more as a count: '5 lights in the workshop', '7 lights in 3 rooms'."""
        ents = list({e.id: e for e in ents}.values())
        if len(ents) <= 3:
            return ", ".join(label(e, self.house) for e in ents)
        domains = {e.domain for e in ents}
        noun = {"light": "lights", "switch": "switches", "fan": "fans", "cover": "covers",
                "media_player": "speakers"}.get(next(iter(domains)), "devices") if len(domains) == 1 else "devices"
        if all(qa.is_tv(e) for e in ents):
            noun = "TVs"
        rooms = {e.area for e in ents}
        if len(rooms) == 1 and None not in rooms:
            return f"{len(ents)} {noun} in the {self.house.area_name(next(iter(rooms)))}"
        if None not in rooms:
            return f"{len(ents)} {noun} in {len(rooms)} rooms"
        return f"{len(ents)} {noun}"

    # ---- helpers ---------------------------------------------------------------

    @staticmethod
    def _needs_confirm(action: Action, e: Entity) -> bool:
        return action.service in ALWAYS_CONFIRM or (sensitive(e) and action.service in CONFIRM_IF_SENSITIVE)

    def _split_area(self, target: str, context_area: str | None) -> tuple[str, set[str] | None]:
        """'lamp in the office' -> ('lamp', {office}). Leaves target alone if the tail is not an area."""
        for word in (" downstairs", " upstairs", " everywhere"):  # "lights downstairs": no "in" needed
            if target.endswith(word) and (areas := self.index.find_areas(word.strip())):
                return target[: -len(word)].strip(), areas
        if " on " in target and " in " not in target:  # "lights on the ground floor"
            name, _, tail = target.rpartition(" on ")
            if name.strip() and (areas := self.areas_in(tail)):
                return name.strip(), areas
        if " in " in target:
            name, _, tail = target.rpartition(" in ")
            if tail in HERE:  # no known room: the name alone, so the caller can ask which room
                return name.strip(), ({context_area} if context_area else None)
            areas = self.areas_in(tail)
            if areas and name.strip():
                return name.strip(), areas
        return target, None

    def _generic(self, name: str, loose: bool):
        """'all lights' / 'lights' -> (domain, None). With loose=True also 'bedroom lights'
        -> (domain, {bedroom}); only used when no entity has that exact name."""
        m = GENERIC.match(name)
        if m:
            return GENERIC_DOMAIN[m.group(1)], None
        if EVERYTHING.match(name):  # "everything" in a room means its lights, never its locks or doors
            return "light", None
        parts = name.split()
        if loose and len(parts) >= 2 and parts[-1] in PLURAL_CLASSES:  # "kitchen light" is one device: ask
            found = self.areas_in(" ".join(parts[:-1]))
            if found:
                return GENERIC_DOMAIN[parts[-1]], found
        return None

    def areas_in(self, text: str) -> set[str]:
        """'bar and workshop' / 'bedroom, office': every part must be a room or floor, else nothing."""
        parts = [p for p in SPLIT_LIST.split(text) if p]
        found: set[str] = set()
        for p in parts:
            if set(tokens(p)) & GENERIC_WORDS:  # "office fan" is a device in a room, not the room
                return set()
            areas = self.index.find_areas(p)
            if not areas:
                return set()
            found |= areas
        return found

    def _room_wide(self, intent: Intent, name: str, areas: set[str] | None, domains: set[str]) -> set[str] | None:
        """'turn off the workshop', 'dim the bar and the workshop': a room named where a device should be
        means its lights, unless a device really has that name."""
        if intent.verb not in ROOM_VERBS or not name:
            return None
        rooms = self.areas_in(name)
        if not rooms:
            return None
        if any(m.score >= 0.99 and m.entity.available for m in self.index.find(name, domains or None, areas)):
            return None  # a light group called "Living Room" is meant as itself, while it works
        return rooms & areas if areas else rooms

    def _roomless(self, domain: str) -> bool:
        """True when there are devices of this kind and none of them has a room set."""
        kind = [e for e in self.house.exposed() if self._is_kind(e, domain) and e.available]
        return bool(kind) and not any(e.area for e in kind)

    def _room_instead(self, name: str):
        """'bar lights' when a dead device has that name: the room's working lights, if it has any."""
        generic = self._generic(name, loose=True) or self._generic(name + "s", loose=True)
        return generic if generic and generic[1] and self._lights(*generic) else None

    def _here(self, name: str, domains: set[str], context_area: str) -> list[Entity]:
        """A device named in the speaker's own room, when the name clearly means one there (or a lettered set)."""
        local = [m for m in self.index.find(name, domains or None, {context_area}) if m.entity.available]
        if local and local[0].score >= 0.9 and (len(local) == 1 or local[1].score < local[0].score - 0.02):
            return [local[0].entity]
        if "light" not in (domains or {"light"}):
            return []
        if (family := self._family(name, {context_area}, context_area, singular=True)):
            return family  # "the overhead light" where the overhead is two bulbs
        said = set(tokens(name))
        if len(said) >= 2 and not any(m.score >= 1.0 for m in self.index.find(name, domains or None)):
            holds = [m.entity for m in local if said <= set(tokens(m.entity.name))]
            if len(holds) == 1:
                return holds  # "light a" in the workshop is the workshop's Light A, not another room's
        return []

    def _contains(self, name: str, domains: set[str], areas: set[str] | None) -> list[Entity]:
        """Devices whose name holds every word that was said, when that is at most three of them."""
        said = set(tokens(name)) - GENERIC_WORDS
        if not said:
            return []
        found = [e for e in self.house.exposed()
                 if e.available and (not domains or e.domain in domains) and (areas is None or e.area in areas)
                 and any(said <= set(t) for t in map(tokens, [e.name, *e.aliases]))]
        return found if 1 <= len(found) <= 3 else []

    def _family(self, name: str, areas: set[str] | None, context_area: str | None,
                singular: bool = False) -> list[Entity]:
        """'game room overhead lights' for Game Room Overhead Light A and B: every device named that plus a letter or
        number. The room may be left out of what is said, and the ceiling words count as one."""
        parts = ceiling_same(tokens(name))
        if len(parts) < 2 or parts[-1] not in (GENERIC_DOMAIN if singular else PLURAL_CLASSES) or parts[-1] in ("tv", "tvs"):
            return []
        domain = GENERIC_DOMAIN[parts[-1]]
        one = parts[-1] if parts[-1] not in PLURAL_CLASSES else (
            parts[-1].removesuffix("es") if domain == "switch" else parts[-1][:-1])
        stem = parts[:-1] + (one,)
        found = []
        for e in self.house.exposed():
            if e.domain != domain or not e.available or (areas is not None and e.area not in areas):
                continue
            words = ceiling_same(tokens(e.name))
            if len(words) < 2 or not (len(words[-1]) == 1 or words[-1].isdigit()):
                continue
            room = ceiling_same(tokens(self.house.area_name(e.area) or ""))
            if words[:-1] == stem or (room and words[:-1] == room + stem):
                found.append(e)
        if len({e.area for e in found}) > 1 and context_area:  # "the can lights" means the ones in this room
            found = [e for e in found if e.area == context_area] or found
        return found if len(found) >= 2 else []

    def _lights(self, domain: str, scope: set[str]) -> list[Entity]:
        """Every available device of a kind in some rooms, without a group helper that repeats its members."""
        found = [e for e in self.house.exposed() if self._is_kind(e, domain) and e.area in scope and e.available]
        if domain == "tv":  # a TV and the streaming stick plugged into it: the set itself, when the home says which
            found = [e for e in found if e.device_class == "tv"] or found
        return ambience.lights_in(found)

    def _active(self, areas: set[str] | None, context_area: str | None) -> list[Entity]:
        scope = areas or ({context_area} if context_area else None)
        playing = [e for e in self.house.exposed()
                   if e.domain == "media_player" and e.available and e.state in ("playing", "paused", "buffering")]
        return [e for e in playing if scope and e.area in scope] or playing

    def _lit(self, areas: set[str] | None, context_area: str | None) -> bool:
        """Is a light on here? Then 'turn it up' more likely means the light than the music."""
        scope = areas or ({context_area} if context_area else None)
        return any(e.domain == "light" and e.state == "on" and (not scope or e.area in scope)
                   for e in self.house.exposed())

    def _active_player(self, intent: Intent, context_area: str | None) -> Resolution:
        """'pause the music', 'turn it up', 'skip this song': whatever is playing, preferring this room."""
        pick = self._active(None, context_area)
        local = [e for e in pick if context_area and e.area == context_area]
        chosen = local or pick
        if intent.verb == "play" and not chosen:  # "play the music": resume on the speaker in this room
            chosen = [e for e in self._music_players() if context_area and e.area == context_area]
        if len(chosen) == 1:
            return self.finalize(intent, chosen)
        if not chosen:
            return Resolution(Outcome(Kind.ANSWER, entities=[], message=self.say(
                "qa_none", what="media players", state="playing")), intent)
        return self._ask(intent, [Match(e, 1.0) for e in chosen[:6]])

    # ---- speakers that can play music (Music Assistant players) --------------------------------

    def _music_players(self) -> list[Entity]:
        return [e for e in self.house.exposed()
                if e.domain == "media_player" and e.available and e.attributes.get("mass_player_type")]

    def find_players(self, text: str, context_area: str | None) -> tuple[str, list[Entity]]:
        """Which speaker did the words name? ('one'|'many'|'unsupported'|'noplayers'|'none', entities).
        'none' means the words are not a speaker or room at all (they belong to a song title)."""
        players = self._music_players()
        words = tokens(text)
        if not words:  # no speaker named: this room, else the only speaker; never a guess among several
            local = [p for p in players if context_area and p.area == context_area]
            if local:
                return ("one", local) if len(local) == 1 else ("many", local)
            if len(players) == 1:
                return "one", players
            return ("many", players) if players else ("none", [])
        named = [m for m in self.index.find(" ".join(words), {"media_player"}) if m.score >= 0.85]
        if named:
            top = [m.entity for m in named if m.score >= named[0].score - 0.02]
            ma = [e for e in top if e.attributes.get("mass_player_type")]
            if len(top) == 1:
                return ("one", ma) if ma else ("unsupported", top)
            return ("many", ma) if len(ma) > 1 else (("one", ma) if ma else ("unsupported", top))
        if (areas := self.index.find_areas(" ".join(words))):
            there = [p for p in players if p.area in areas]
            if not there:
                return "noplayers", []
            return ("one", there) if len(there) == 1 else ("many", there)
        return "none", []

    # ---- mood lighting ------------------------------------------------------------------------------

    def ambience(self, intent: Intent, context_area: str | None = None, palette=None) -> Resolution:
        """'make the workshop halloween colors': a theme spread across the lights in a room."""
        theme = ambience.canonical(intent.option or "")
        palette = palette or (ambience.THEMES.get(theme) if theme else None)
        if palette is None:
            return Resolution(Outcome(Kind.REFUSE, message=self.say("ambience_unknown", theme=intent.option or "that")),
                              intent, needs_design=intent.option or "that")
        target = intent.target
        if target in HERE:
            areas = {context_area} if context_area else None
        else:
            areas = self.index.find_areas(target)
        if not areas:
            return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain="light")), intent)
        where = "this room" if target in HERE else f"the {target}"
        lights = [e for a in areas for e in self.index.candidates_in("light", {a}) if e.available]
        actions = ambience.assign(lights, palette)
        if not actions:
            return Resolution(Outcome(Kind.REFUSE, message=self.say("ambience_none", where=where)), intent)
        n = len(actions)
        what = f"{n} light{'s' if n != 1 else ''}"
        return Resolution(Outcome(Kind.ACTION, actions, message=self.say(
            "ambience_done", theme=ambience.label(theme or (intent.option or "that")), where=where, what=what)), intent)

    def _which(self, intent: Intent, rest: str, areas: set[str] | None, context_area: str | None) -> Resolution:
        """'make one of the workshop lights red': which one? Never guess among several."""
        generic = self._generic(rest, loose=True)
        scope = areas or (generic[1] if generic else None) or ({context_area} if context_area else None)
        if generic is None or not scope:
            return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain="light")), intent)
        ents = [e for e in self.index.candidates_in(generic[0], scope) if e.available][:8]
        if not ents:
            return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain=generic[0])), intent)
        return self._ask(intent, [Match(e, 1.0) for e in ents])

    def can_resolve(self, intent: Intent, context_area: str | None = None) -> bool:
        """Would this command's target be understood? Used to decide whether a bare 'and the lamp' is a device."""
        if intent.is_query or intent.verb.startswith("identity_"):
            return True
        name, areas = self._split_area(intent.target, context_area)
        if not name:
            return False
        if name in ("it", "this room", "the room", "things", "volume", "sound") or name in CLIMATE_WORDS:
            return True
        if self._generic(name, loose=True) is not None:
            return True
        if self._room_wide(intent, name, areas, set(VERB_DOMAINS.get(intent.verb, ()))):
            return True
        matches, confident = self._pick(name, set(VERB_DOMAINS.get(intent.verb, ())), areas, context_area)
        return bool(matches) and confident

    def _single_in_area(self, name: str) -> Entity | None:
        """'office fan' / 'kitchen light': a room plus a device type, and exactly one such device there."""
        parts = name.split()
        if len(parts) < 2 or parts[-1] not in GENERIC_DOMAIN:
            return None
        areas = self.index.find_areas(" ".join(parts[:-1]))
        if not areas:
            return None
        found = self._lights(GENERIC_DOMAIN[parts[-1]], areas)
        return found[0] if len(found) == 1 else None

    def _pick(self, name: str, domains: set[str], areas: set[str] | None,
              context_area: str | None) -> tuple[list[Match], bool]:
        """Returns (decisive matches, is_confident). Several matches means 'ask'."""
        matches = self.index.find(name, domains, areas)
        if not matches and "television" in name.split():  # no device is called that: it is a TV by another word
            matches = self.index.find(" ".join("tv" if w == "television" else w for w in name.split()), domains, areas)
        if not matches:
            return [], False
        top = matches[0].score
        # partial matches ("tv" inside "Living Room TV") are scored coarsely, so group them loosely
        tied = [m for m in matches if m.score >= top - (0.02 if top >= 0.85 else 0.1)]
        working = [m for m in tied if m.entity.available]
        if working and len(working) < len(tied):  # "the vacuum" when one of two is offline: the one that works
            if len(working) == 1:
                return working, True
            tied = working
        if len(tied) > 1 and context_area:
            local = [m for m in tied if m.entity.area == context_area]
            if len(local) == 1:
                return local, True
        if len(matches) == 1 and (said := set(tokens(name)) - {"a", "an"}) and any(
                said <= set(t) for t in map(tokens, [matches[0].entity.name, *matches[0].entity.aliases])):
            room = self._generic(name, loose=True)  # "living room lights" is the room's lights, not "Living Room TV
            if not (room and room[1] and len(self._lights(*room)) > 1):  # Lights", when the room has several
                return matches, True  # "the printer" when the only thing it could be is the 3D Printer
        if len(tied) == 1:
            runner_up = matches[1].score if len(matches) > 1 else 0.0
            if top >= 0.85 and (top >= 1.0 or runner_up < top - 0.1):
                return tied, True
            if areas is not None:  # the user named the room, so one partial match in it is enough
                return tied, True
        return [m for m in matches if m.score >= top - 0.1][:4], False

    def _ask(self, intent: Intent, matches: list[Match]) -> Resolution:
        ents = [m.entity for m in matches]
        names = [label(e, self.house) for e in ents]
        if len(names) == 1:
            msg = self.say("clarify_one", name=names[0])
        else:
            msg = self.say("clarify_options", options=", ".join(names[:-1]) + f" or {names[-1]}")
        return Resolution(Outcome(Kind.CLARIFY, entities=[e.id for e in ents], message=msg),
                          intent, candidates=ents)

    # ---- commands --------------------------------------------------------------

    def _command(self, intent: Intent, context_area: str | None) -> Resolution:
        domains = set(VERB_DOMAINS.get(intent.verb, ()))
        if intent.domain_hint:
            domains &= {intent.domain_hint}
        name, areas = self._split_area(intent.target, context_area)
        if intent.verb == "adjust" and (defaults := self._default_targets(intent, name, areas, context_area)):
            return defaults
        if intent.verb == "lock" and name in ALL_LOCKS:  # "lock up": every lock, which is always the safe direction
            locks = [e for e in self.house.exposed() if e.domain == "lock" and e.available
                     and (areas is None or e.area in areas)]
            if not locks:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="lock")), intent)
            return self.finalize(intent, locks)
        if intent.verb in ("set_number", "set_temperature") and name in CLIMATE_WORDS:  # "set the temperature to 68"
            pool = [e for e in self.house.exposed() if e.domain == "climate" and e.available]
            scope = areas or ({context_area} if context_area else None)
            chosen = [e for e in pool if scope and e.area in scope] or (pool if len(pool) == 1 else [])
            if len(chosen) == 1:
                return self.finalize(replace(intent, verb="set_temperature"), chosen)
        if intent.verb == "set_number" and not self.index.find(name, {"climate"}, areas):
            domains = domains - {"climate"}  # "set the lamp to 50": a level, not a temperature
        if intent.verb == "set_volume" and name in ACTIVE_WORDS | {"volume", "sound"}:  # "set the volume to 30"
            return self._volume(intent, areas or ({context_area} if context_area else None))
        kind = TYPE_DOMAIN.get(name.split()[-1]) if name else None
        if kind and domains & kind and self.index.find(name, domains & kind, areas):
            domains = domains & kind  # "bedroom light" never means the bedroom TV or fan
        elif kind and domains & kind:  # nothing of that kind by that name: still never a different kind of thing
            domains = domains & (kind | ({"switch", "input_boolean"} if kind & {"light", "fan"} else set()))

        if (name in ACTIVE_WORDS or (not name and not areas)) and (intent.verb in TRANSPORT or intent.verb == "adjust"):
            return self._active_player(intent, context_area)
        if (some := ONE_OF.match(name)) and intent.verb not in ("query_state", "query_value"):
            return self._which(intent, some.group("rest"), areas, context_area)
        if name in ("here", "this room", "the room", "in here") and intent.verb in ROOM_VERBS:
            scope = areas or ({context_area} if context_area else None)  # "set the lights in here to red"
            if not scope:
                return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain="light")), intent)
            lights = self._lights("light", scope)
            if not lights:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="light")), intent)
            return self.finalize(intent, lights)
        if intent.verb in ("open", "close") and (rooms := self.areas_in(name)):  # "close the garage"
            covers = [e for e in self.index.candidates_in("cover", rooms) if e.available]
            if len(covers) == 1 and not any(m.score >= 0.99 for m in self.index.find(name, domains)):
                return self.finalize(intent, covers)
        if (rooms := self._room_wide(intent, name, areas, domains)) is not None:
            lights = self._lights("light", rooms)
            if not lights:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="light")), intent)
            return self.finalize(intent, lights)
        on_off = intent.verb in ("turn_on", "turn_off", "toggle", "adjust", "set_option", "set_brightness", "set_number",
                                 "launch")
        generic = self._generic(name, loose=False) if on_off else None
        if generic is None and areas is None and context_area and (here := self._here(name, domains, context_area)):
            return self.finalize(intent, here)  # "the overhead lights" said in the bar: the bar's, not another room's
        if generic is None:
            matches, confident = self._pick(name, domains, areas, context_area)
            if matches and confident:
                if matches[0].entity.available or not on_off or not (generic := self._room_instead(name)):
                    return self.finalize(intent, [matches[0].entity])
            elif on_off:  # "kitchen lights" beats weak partial matches like "Kitchen Ceiling Light"
                generic = self._generic(name, loose=True)
            if on_off and generic is None and (family := self._family(name, areas, context_area)):
                return self.finalize(intent, family)
            if generic is None and on_off and (one := self._single_in_area(name)):
                return self.finalize(intent, [one])  # "office fan" when the office has exactly one fan
            if matches and generic is None:
                return self._ask(intent, matches)
        if generic is not None:
            domain, g_areas = generic
            g_areas = g_areas or areas or ({context_area} if context_area else None)
            whole = not g_areas and intent.verb in ("turn_on", "turn_off") and name.startswith(("all ", "every "))
            if not g_areas and intent.verb in ROOM_VERBS and self._roomless(domain):
                whole = True  # no room is set for any of them, so "which room?" could never be answered
            if not g_areas and not whole:
                return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain=domain.replace("tv", "TV"))), intent)
            if whole:  # "turn off all the lights" with no room to go by: the home, rooms or not
                ents = [e for e in self.house.exposed() if self._is_kind(e, domain) and e.available]
                if domain == "tv":
                    ents = [e for e in ents if e.device_class == "tv"] or ents
                ents = ambience.lights_in(ents)
            else:
                ents = self._lights(domain, g_areas)
                everywhere = [e for e in self.house.exposed() if self._is_kind(e, domain) and e.available]
                if domain == "tv":
                    everywhere = [e for e in everywhere if e.device_class == "tv"] or everywhere
                if not ents and domain != "light" and areas is None and len(everywhere) == 1:
                    ents = everywhere  # "turn on the fan" from a room without one, in a home with exactly one
                elif not ents and domain != "light" and areas is None and 1 < len(everywhere) <= 4:
                    return self._ask(intent, [Match(e, 1.0) for e in everywhere])  # none here: which of the others?
            if not ents:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain=domain)), intent)
            return self.finalize(intent, ents)
        if intent.verb in ("turn_on", "turn_off") and name and not (kind and not kind & {"cover", "valve"}):
            # "shut off the garage door": a door closes. Never for "the curtain lights": lights were asked for
            doors, sure = self._pick(name, {"cover", "valve"}, areas, context_area)
            if doors and sure:
                way = "close" if intent.verb == "turn_off" else "open"
                return self.finalize(replace(intent, verb=way), [doors[0].entity])
        if intent.verb == "activate" and name and not intent.domain_hint:  # "start the coffee maker"
            things, sure = self._pick(name, ONOFF, areas, context_area)
            if things and sure:
                return self.finalize(replace(intent, verb="turn_on"), [things[0].entity])
        if intent.loose:  # "so what's going on": not a command after all
            return Resolution(Outcome(Kind.CHAT), intent, escalate=True)
        if name and (near := self._contains(name, domains, areas)):  # "the coffee" for Coffee Maker: ask, never guess
            return self._ask(intent, [Match(e, 0.8) for e in near])
        return Resolution(Outcome(Kind.REFUSE, message=self.say("not_found", target=intent.target)),
                          intent, escalate=True)

    def _volume(self, intent: Intent, scope: set[str] | None) -> Resolution:
        """Whose volume? What is playing in this room, else what is on here, else the one thing playing anywhere."""
        players = [e for e in self.house.exposed() if e.domain == "media_player" and e.available]
        local = [e for e in players if scope and e.area in scope]
        pick = ([e for e in local if e.state == "playing"]
                or [e for e in local if e.state in ("paused", "buffering", "on")]
                or local or [e for e in players if e.state == "playing"])
        if len(pick) == 1:
            return self.finalize(intent, pick)
        if pick:
            return self._ask(intent, [Match(e, 1.0) for e in pick[:4]])
        return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain="speaker")), intent)

    def _is_kind(self, e: Entity, kind: str) -> bool:
        return qa.is_tv(e) if kind == "tv" else e.domain == kind

    def _default_targets(self, intent: Intent, name: str, areas: set[str] | None,
                         context_area: str | None) -> Resolution | None:
        """'make it brighter' / 'turn up the heat': decide which devices from the room or the home."""
        here = areas or ({context_area} if context_area else None)
        playing_here = any(e.domain == "media_player" and e.state == "playing" and here and e.area in here
                           for e in self.house.exposed())  # music on in this room: "turn it up" is the volume
        if intent.option in ("louder", "quieter") or (
                intent.option in ("up", "down") and name == "it" and self._active(areas, context_area)
                and (playing_here or not self._lit(areas, context_area))):
            return self._active_player(intent, context_area)
        thermal = intent.option in ("warmer", "cooler", "hotter", "colder") or name in CLIMATE_WORDS
        if name not in ("it", "this room", "the room", "things", "volume", "sound") and name not in CLIMATE_WORDS:
            return None
        scope = areas or ({context_area} if context_area else None)
        if name in ("volume", "sound"):  # "turn the volume down": the speaker here, or the one playing
            return self._volume(intent, scope)
        if thermal:
            pool = [e for e in self.house.exposed() if e.domain == "climate" and e.available]
            if not pool:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="thermostat")), intent)
            local = [e for e in pool if scope and e.area in scope]
            chosen = local or (pool if len(pool) == 1 else [])
            if not chosen:
                return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain="thermostat")), intent)
            return self.finalize(intent, chosen[:1])
        if not scope and self._roomless("light"):  # "brighter" in a home with no rooms set: all of its lights
            scope = {None}
        if not scope:
            return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain="light")), intent)
        lights = self._lights("light", scope)
        if not lights:
            return Resolution(Outcome(Kind.REFUSE, message=self.say("none_there", domain="light")), intent)
        return self.finalize(intent, lights)

    # ---- questions -------------------------------------------------------------

    def _query_state(self, intent: Intent, context_area: str | None) -> Resolution:
        name, areas = self._split_area(intent.target, context_area)
        matches, confident = self._pick(name, STATE_DOMAINS[intent.state_word], areas, context_area)
        if matches and confident:
            return Resolution(self._answer(matches[0].entity), intent)
        real = [m for m in matches if m.entity.domain != "binary_sensor"]
        if len(real) == 1 and len(matches) > 1:  # "Garage Door" and its contact sensor: the door itself answers
            return Resolution(self._answer(real[0].entity), intent)
        if matches:
            return self._ask(intent, matches)
        return Resolution(Outcome(Kind.CHAT), intent, escalate=True)

    def _secure(self, intent: Intent) -> Resolution:
        """'is the house secure': every lock, door, garage door and window, and the alarm, from live state."""
        checked, wrong = 0, []
        for spec, state in ((qa.SPECS["lock"], "unlocked"), (qa.SPECS["door"], "open"), (qa.SPECS["window"], "open")):
            picked = qa.select(self.house, spec, state, None)
            if picked:
                checked += len(picked[1])
                wrong += [f"{label(e, self.house)} is {state}" for e in picked[0]]
        for e in self.house.exposed():
            if e.domain == "alarm_control_panel" and e.available:
                checked += 1
                if e.state == "disarmed":
                    wrong.append(f"{label(e, self.house)} is disarmed")
        wrong = list(dict.fromkeys(wrong))
        if not checked:
            return Resolution(Outcome(Kind.ANSWER, message=self.say("home_unsure")), intent)
        msg = self.say("secure_no", names=qa.join_names(wrong)) if wrong else self.say("secure_yes")
        if intent.verb == "query_status":
            lit = qa.select(self.house, qa.SPECS["light"], "on", None)
            n = len(ambience.lights_in(lit[0])) if lit else 0
            lights = (self.say("qa_count", count=n, what="light" if n == 1 else "lights", be="is" if n == 1 else "are",
                               state="on") if n else self.say("qa_none", what="lights", state="on"))
            msg = f"{lights} {msg}"
        return Resolution(Outcome(Kind.ANSWER, message=msg), intent)

    def _query_value(self, intent: Intent, context_area: str | None) -> Resolution:
        name, areas = self._split_area(intent.target, context_area)
        if name in SENSOR_CLASSES:
            named = areas is not None  # a room that was said out loud is only answered from that room
            areas = areas or ({context_area} if context_area else None)
            stats = [e for e in self.house.exposed() if e.domain == "climate" and e.available
                     and e.attributes.get("current_temperature") is not None] if name == "temperature" else []
            sensors = [e for e in self.house.exposed()
                       if e.domain == "sensor" and e.device_class == name
                       and areas and e.area in areas and e.available]
            if len(sensors) == 1:
                return Resolution(self._answer(sensors[0]), intent)
            if sensors:
                return self._ask(intent, [Match(s, 1.0) for s in sensors])
            near = [e for e in stats if areas and e.area in areas] or (stats if len(stats) == 1 and not named else [])
            if len(near) == 1:  # no sensor to read, but the thermostat knows what it measures
                reading = _num(float(near[0].attributes["current_temperature"]))
                return Resolution(Outcome(Kind.ANSWER, entities=[near[0].id], message=self.say(
                    "answer", name=label(near[0], self.house), state=f"reading {reading} degrees")), intent)
            if areas:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("no_sensor", sensor=name)), intent)
        matches, confident = self._pick(name, {"sensor", "alarm_control_panel"}, areas, context_area)
        if matches and confident:
            return Resolution(self._answer(matches[0].entity), intent)
        if matches:
            return self._ask(intent, matches)
        return Resolution(Outcome(Kind.CHAT), intent, escalate=True)

    def _answer(self, e: Entity) -> Outcome:
        n = label(e, self.house)
        if not e.available:
            return Outcome(Kind.ANSWER, entities=[e.id], message=self.say("answer_unavailable", name=n))
        unit = f" {e.unit}" if e.unit else ""
        return Outcome(Kind.ANSWER, entities=[e.id],
                       message=self.say("answer", name=n, state=f"{e.state.replace('_', ' ')}{unit}"))

    # ---- questions about groups of devices ------------------------------------------

    def _group_query(self, intent: Intent, context_area: str | None) -> Resolution:
        phrase, area_text = intent.target, intent.area
        spec = qa.lookup(phrase)
        if spec is None and " " in phrase:  # "kitchen lights" -> class "lights", area "kitchen"
            head, _, tail = phrase.rpartition(" ")
            if qa.lookup(tail) and self.index.find_areas(head):
                spec, area_text = qa.lookup(tail), area_text or head
        away = Resolution(Outcome(Kind.CHAT), intent, escalate=True)
        if spec is None:
            return away
        areas, scoped = None, False
        if area_text in ("here", "this room", "room"):
            if not context_area:
                return Resolution(Outcome(Kind.CLARIFY, message=self.say("clarify_room", domain=spec.singular)), intent)
            areas, scoped = {context_area}, True
        elif area_text and area_text not in qa.WHOLE_HOUSE:
            areas, scoped = self.index.find_areas(area_text), True
            if not areas:
                return away
        if intent.state_word is None:
            return self._inventory(intent, spec, areas, scoped, area_text)
        picked = qa.select(self.house, spec, intent.state_word, areas)
        if picked is None:
            return away
        matching, scope = picked
        state = intent.state_word
        n = len(matching)

        def names(ents):
            return qa.join_names([e.name if scoped else label(e, self.house) for e in ents])

        kw = dict(what=spec.plural if n != 1 else spec.singular, be="is" if n == 1 else "are", state=state)
        if intent.verb == "query_all":
            if not scope:
                return Resolution(Outcome(Kind.REFUSE, message=self.say("not_found", target=phrase)), intent)
            rest = [e for e in scope if e not in matching]
            if not rest:
                return Resolution(Outcome(Kind.ANSWER, entities=[e.id for e in scope],
                                          message=self.say("qa_all_yes", what=spec.plural, state=state)), intent)
            return Resolution(Outcome(Kind.ANSWER, entities=[e.id for e in rest],
                                      message=self.say("qa_all_no", what=spec.plural, state=state, names=names(rest))), intent)
        ids = [e.id for e in matching]
        if not matching:
            return Resolution(Outcome(Kind.ANSWER, entities=[], message=self.say("qa_none", what=spec.plural, state=state)), intent)
        if intent.verb == "query_count":
            msg = self.say("qa_count", count=n, **kw)
        elif intent.verb == "query_any":
            msg = self.say("qa_any_yes", count=n, names=names(matching), **kw)
        else:
            msg = self.say("qa_list", count=n, names=names(matching), **kw)
        return Resolution(Outcome(Kind.ANSWER, entities=ids, message=msg), intent)

    def _inventory(self, intent: Intent, spec, areas, scoped: bool, area_text: str | None) -> Resolution:
        """'what lights are in the workshop', 'how many lights are in the basement': what exists."""
        ents = qa.inventory(self.house, spec, areas)
        n = len(ents)
        where = ("this room" if area_text in ("here", "this room", "room") else f"the {area_text}") \
            if scoped and area_text else "the house"
        if not ents:
            return Resolution(Outcome(Kind.ANSWER, entities=[],
                                      message=self.say("qa_inv_none", what=spec.plural, where=where)), intent)
        kw = dict(count=n, what=spec.plural if n != 1 else spec.singular, be="is" if n == 1 else "are", where=where)
        if intent.verb == "query_count":
            msg = self.say("qa_inv_count", **kw)
        else:
            names = qa.join_names([e.name if len(areas or ()) == 1 else label(e, self.house) for e in ents])
            msg = self.say("qa_inv_list", names=names, **kw)
        away = sum(not e.available for e in ents)
        if away:
            msg += f" {away} of them {'is' if away == 1 else 'are'} unavailable right now."
        return Resolution(Outcome(Kind.ANSWER, entities=[e.id for e in ents], message=msg), intent)

    # ---- questions about one device's attributes ------------------------------------

    def _attr_query(self, intent: Intent, context_area: str | None) -> Resolution:
        if intent.verb == "query_playing":
            return self._playing(intent, context_area)
        what, domain = {"brightness": ("brightness", {"light"}), "volume": ("volume", {"media_player"}),
                        "setpoint": ("temperature setting", {"climate"})}[intent.state_word]
        name, areas = self._split_area(intent.target, context_area)
        matches, confident = self._pick(name, domain, areas, context_area)
        if not matches:
            return Resolution(Outcome(Kind.CHAT), intent, escalate=True)
        if not confident:
            return self._ask(intent, matches)
        e = matches[0].entity
        n = label(e, self.house)
        if not e.available:
            return Resolution(self._answer(e), intent)
        if intent.state_word == "brightness":
            if e.state == "off":
                return Resolution(self._answer(e), intent)
            value = e.attributes.get("brightness")
            value = None if value is None else f"{round(float(value) / 255 * 100)} percent"
        elif intent.state_word == "volume":
            value = e.attributes.get("volume_level")
            value = None if value is None else f"{round(float(value) * 100)} percent"
        else:
            value = e.attributes.get("temperature")
            value = None if value is None else f"{value:g} degrees"
        if value is None:
            return Resolution(Outcome(Kind.ANSWER, entities=[e.id], message=self.say("qa_no_data", name=n, what=what)), intent)
        key = "qa_set_to" if intent.state_word == "setpoint" else "qa_attr"
        slots = {"name": n, "value": value} if key == "qa_set_to" else {"name": n, "what": what, "value": value}
        return Resolution(Outcome(Kind.ANSWER, entities=[e.id], message=self.say(key, **slots)), intent)

    def _playing(self, intent: Intent, context_area: str | None) -> Resolution:
        if intent.target:
            name, areas = self._split_area(intent.target, context_area)
            matches, confident = self._pick(name, {"media_player"}, areas, context_area)
            if not matches:
                return Resolution(Outcome(Kind.CHAT), intent, escalate=True)
            if not confident:
                return self._ask(intent, matches)
            players = [matches[0].entity]
        else:
            players = [e for e in self.house.exposed() if e.domain == "media_player" and e.state == "playing"]
        playing = [e for e in players if e.state == "playing"]
        if not playing:
            if intent.target:
                return Resolution(self._answer(players[0]), intent)
            return Resolution(Outcome(Kind.ANSWER, entities=[],
                                      message=self.say("qa_none", what="media players", state="playing")), intent)
        parts = []
        for e in playing:
            title = e.attributes.get("media_title")
            artist = e.attributes.get("media_artist")
            if not title:
                parts.append(self.say("qa_no_data", name=label(e, self.house), what="track information"))
            else:
                parts.append(self.say("qa_playing", name=label(e, self.house),
                                      title=f"{title} by {artist}" if artist else title))
        return Resolution(Outcome(Kind.ANSWER, entities=[e.id for e in playing], message=" ".join(parts)), intent)

    # ---- who the assistant is -----------------------------------------------------

    def _identity(self, intent: Intent) -> Resolution:
        p: Persona = self.responder.persona
        ask = intent.verb
        if ask == "identity_acronym" and intent.target:
            mine = [tokens(n) for n in (p.agent_name, p.spoken, p.acronym_expansion) if n]
            if not any(phrase_sim(tokens(intent.target), m) >= 0.85 for m in mine):
                return Resolution(Outcome(Kind.CHAT), intent, escalate=True)  # "what does NASA stand for"
        facts = {
            "identity_name": ("identity_name", {}, True),
            "identity_acronym": ("identity_acronym", {"expansion": p.acronym_expansion}, bool(p.acronym_expansion)),
            "identity_maker": ("identity_maker", {"maker": p.maker}, bool(p.maker)),
            "identity_purpose": ("identity_purpose", {"purpose": p.purpose}, bool(p.purpose)),
            "identity_owner": ("identity_owner", {"owner": p.owner}, bool(p.owner)),
        }
        key, slots, known = facts[ask]
        if not known:
            key = "identity_no_acronym" if ask == "identity_acronym" else "identity_unknown"
            slots = {}
        return Resolution(Outcome(Kind.ANSWER, message=self.say(key, **slots)), intent)
