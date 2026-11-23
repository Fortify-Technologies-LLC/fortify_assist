"""The deterministic pipeline: normalise, split, parse, resolve, validate.

Stateless per call except for short-lived follow-ups (a pending question or confirmation),
keyed by conversation id. No LLM in Milestone 1; CHAT means "hand this to the chat model".
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace

from . import ambience, feeds, music, qa, weather
from .index import EntityIndex
from .llm import LLMClient
from .controls import ADJUST_WORDS, COLOR_MODES, COLORS, WHITES
from .intents import Intent, parse, prepare
from .persona import Persona
from .planner import Planner
from .retrieval import WORD_DOMAINS, retrieve
from .responses import Responder
from .semantic import TYPE_WORDS, SemanticParser
from .resolver import Resolution, Resolver, label
from .text import STOP, normalize, phrase_sim, tokens
from .types import Action, Entity, House, Kind, Outcome
from .validator import validate

PENDING_TTL = 60.0
CONTEXT_TTL = 300.0  # how long "turn them off" still means what was just talked about
# words that point back at what was just talked about; "this room" is a place, not a reference
POINTING = re.compile(r"\b(?:them|they|those ones|these ones|those|these|that one|this one|that|this(?! room)|both)\b")
NEGATIVE = re.compile(r"(?:^|[\s(])(?:-\s?|minus |negative )\d")  # a setting below zero is never meant
PLURAL_POINTING = re.compile(r"\b(?:them|they|those|these|both)\b")
POINTING_TARGETS = {"it", "them", "they", "those", "these", "that", "this", "both"}
# a later clause that names a setting but no device ("... and set brightness to 100") or points back
# ("... and make them 100 percent") means the device of the clause before it
SETTING_ONLY = re.compile(r"^(?:(?:make|set|turn|put) )?(?:the )?(?:it|them|they|those|these|both|"
                          r"brightness|brightness level|level|colou?r)(?:'?s? brightness)?$")
AMOUNT = re.compile(r"^(?:(?:even|a|little|bit|tad|touch|slightly|lot|much|way|tiny) )+")
# "change them back to white" is "change them to white"; "make them white again" is "make them white"
_SETTING_WORDS = "|".join(sorted((re.escape(w) for w in COLORS | set(WHITES)), key=len, reverse=True))
BACK_TO = re.compile(rf"\bback (?=to (?:{_SETTING_WORDS}|\d+|full|max|maximum|half)\b)", re.I)
AGAIN_END = re.compile(r"(?<=\w)[\s,]+again[\s.!?]*$", re.I)
NOT_AGAIN = re.compile(r"^\W*(?:play|do|say|repeat|try|ask)\b", re.I)
# "change them back", "put it back how it was", "undo that": the last change, undone
UNDO = re.compile(r"^(?:undo(?: that| it| last one| last change)?|"
                  r"(?:change|put|set|turn|switch|make|bring) (?:(?P<t>.+?) )?back"
                  r"(?: (?:to )?(?:how|way|what|like) (?:it|they|that|those|these) (?:was|were|had)(?: it| them| before)?"
                  r"| to (?:normal|before|previous|original|last)(?: colou?r| setting| one)?| again| how they were)?)$")
UNDO_DOMAINS = {"light", "switch", "fan", "input_boolean"}
AGAIN = {"again", "more", "little more", "bit more", "even more", "lot more", "do that again", "do it again",
         "same again", "keep going"}
ALSO = re.compile(r"^(?:(?:and|also|plus|then|now)(?: (?:do|in|for|with))? (?P<a>.+?)|"
                  r"(?:what|how) about (?:in )?(?P<b>.+?)|"
                  r"(?:do )?(?:same )?(?:same thing )?(?:for|in|with|to) (?P<c>.+?)|"
                  r"(?P<d>.+?) (?:too|as well|also))$")
CAPABILITIES = re.compile(r"^(?:what (?:can|could|do) you (?:do|help with|control)|what are you able to do|"
                          r"what can i (?:ask|say)(?: you)?|how can you help(?: me)?|help|"
                          r"(?:can|could|will) you help(?: me)?(?: out)?(?: with (?:the|my|this) (?:house|home|lights?|devices?)\b.*)?|"
                          r"(?:can|could|do) you (?:control|run|manage|operate|access)\b.*|"
                          r"are you able to (?:control|run|manage)\b.*|do you have (?:access|control)\b.*)$")
ABLE_TO = {"light": "lights", "switch": "switches", "fan": "fans", "cover": "blinds and garage doors",
           "climate": "thermostat", "media_player": "speakers and TVs", "lock": "locks"}
COMMAND_WORDS = {"turn", "switch", "open", "close", "shut", "lock", "unlock", "set", "dim", "brighten", "raise",
                 "lower", "lift", "kill", "cut", "start", "stop", "run", "activate", "play", "pause", "resume",
                 "make", "put", "get", "fire", "power", "disarm", "arm", "toggle", "enable", "disable", "silence",
                 "mute", "unmute", "increase", "decrease", "boost", "reduce", "brighter", "dimmer", "warmer",
                 "cooler", "louder", "quieter", "wake", "sleep", "water", "sprinkle"}
YES = {"yes", "yeah", "yep", "yup", "sure", "confirm", "correct", "affirmative", "ok", "okay", "absolutely",
       "definitely", "certainly"}
# A yes only counts when it is the whole reply: "ok, turn off the kitchen lights" must never unlock a door.
YES_FILL = {"please", "do", "it", "go", "ahead", "for", "that", "thanks", "thank", "you", "i", "am", "i'm", "im",
            "thing", "of", "course", "right", "then", "just", "positive"}
GO_AHEAD = {"do it", "go ahead", "go for it", "please do", "do it please", "go ahead please"}
TIME_Q = re.compile(r"^(?:what time is it(?: now| right now)?|what is time(?: now| right now)?|"
                    r"do you (?:have|know) time|tell me time|what time it is|time)$")
DATE_Q = re.compile(r"^(?:what is (?:today )?date(?: today)?|what day is it(?: today)?|what is today|"
                    r"what day is today|what is day today|what day of week is it|tell me date)$")
THANKS = {"thanks", "thank you", "thanks a lot", "thank you very much", "thank you so much", "thanks so much",
          "cheers", "appreciate it", "ok thanks", "okay thanks", "ok thank you", "okay thank you", "great thanks",
          "perfect thanks", "thanks " , "ty"}
NEVER_MIND = {"never mind", "nevermind", "forget it", "forget about it", "cancel", "cancel that", "nothing",
              "no", "nope", "no thanks", "no thank you", "that is all", "that's all", "that will be all"}
SPELL_Q = re.compile(r"^(?:how do (?:you|i) spell|can you spell|spell)(?: the word)? (?P<w>[a-z][a-z'-]{1,30})$")
ROOMS_Q = re.compile(r"^(?:(?:what|which) rooms (?:are there|do you (?:have|know|control|see|know about)|"
                     r"do (?:i|we) have|can you control)|list (?:all )?rooms|what are rooms)$")
SCENES_Q = re.compile(r"^(?:(?:what|which) (?:scenes|routines|scripts) (?:are there|do you (?:have|know)|"
                      r"do (?:i|we) have|can (?:i|you) (?:run|use|ask for|start))|list (?:all )?(?:scenes|routines|scripts))$")
HISTORY_Q = re.compile(r"^(?:why|when|how long|since when|who (?:turned|opened|closed|left|unlocked|locked))\b")
QUESTION = re.compile(r"^(?:what|which|who|whom|whose|why|when|where|how|is|are|was|were|do|does|did|can|could|"
                      r"will|would|should|has|have|am)\b")
LATER = re.compile(r"\b(?:in|after) (?:\d+|an?|half an?|couple(?: of)?|few) (?:seconds?|minutes?|mins?|hours?)\b"
                   r"|\bat (?:\d{1,2}:\d\d|\d{1,2} ?(?:am|pm|a\.m\.|p\.m\.|o'clock)|(?:[1-9]|1[0-2])$)"
                   r"|\b(?:tonight|tomorrow|later|every (?:day|morning|night|evening)|"
                   r"at (?:sunset|sunrise|noon|midnight|dusk|dawn))\b")
LIVE = re.compile(r"\b(?:flew over|flying over|fly over|overhead right now|last night|score|scores|who won|"
                  r"who is winning|price|prices|traffic|right now|currently|latest|breaking|"
                  r"weather|forecast|news|headlines|today|tonight|tomorrow|yesterday|this (?:morning|week|weekend))\b")
SPOT_VERBS = {"turn", "switch", "shut", "open", "close", "lock", "unlock", "set", "dim", "brighten", "pause", "mute",
              "kill", "get", "put", "make", "start", "stop"}
NOT_PREAMBLE = {"if", "when", "whenever", "unless", "before", "after", "until", "while", "to", "you", "i"}
ASKING = {"how", "why", "what", "who", "where", "which", "did", "do", "does", "should", "would", "could", "can",
          "will", "is", "are", "was"}  # a question, a condition or "I forgot to turn off...": not scene-setting
GREETINGS = {"good night", "goodnight", "good morning", "good evening", "good afternoon", "night night", "hello",
             "morning", "night", "good day"}
SCENE_LEAD = {"have", "do", "put", "on", "start", "run", "activate", "set", "go", "to", "into", "switch", "time",
              "play", "show", "scene", "turn",
              "for", "it", "is", "its", "begin", "launch", "enable", "trigger", "a", "an"}
CHANGE_WORDS = {"on", "off", "up", "down", "brighter", "dimmer", "louder", "quieter", "open", "closed", "shut",
                "locked", "unlocked", "back"}
HOME_WORDS = {"house", "home", "device", "devices", "secure", "security", "window", "windows", "doors", "locked",
              "unlocked", "upstairs", "downstairs", "room", "rooms"}
HANDOFF = re.compile(r"\b(?:timers?|remind(?:er)? me|reminders?|alarm (?:for|at)|wake me|shopping list|grocery list|"
                     r"to ?do list|my list|calendar)\b")
NO = {"no", "nope", "cancel", "stop", "nevermind", "never", "don't", "dont", "negative"}
FILLER = {"one", "in", "on", "it", "that", "please", "room", "the"}
ORDINALS = {"first": 0, "1": 0, "second": 1, "2": 1, "third": 2, "3": 2, "last": -1}
EXCEPT = re.compile(r"^(?P<main>.+?)(?:,)? (?:except(?: for)?|apart from|other than|but not|but leave|"
                    r"and leave|but keep|and keep|leaving|keeping)(?: the)? (?P<ex>.+?)(?: on| alone| as is| be)?$")
CAUTION = re.compile(r"\b(?:don't|dont|do not|never|not|except|but|unless|without|leave|keep|leaving|keeping|"
                     r"apart from|other than)\b")


SUM_Q = re.compile(r"^(?:what is |how much is |calculate |work out )?(?P<a>\d+(?:\.\d+)?) "
                   r"(?P<op>percent of|plus|minus|times|multiplied by|divided by|over|x|\+|-|\*|/) "
                   r"(?P<b>\d+(?:\.\d+)?)$")


def _sum(m: re.Match) -> str | None:
    """Arithmetic is worked out, never left to a model that says 15 percent of 80 is 12.4."""
    a, b, op = float(m["a"]), float(m["b"]), m["op"]
    if op in ("divided by", "over", "/") and b == 0:
        return None
    value = {"percent of": a * b / 100, "plus": a + b, "+": a + b, "minus": a - b, "-": a - b, "times": a * b,
             "multiplied by": a * b, "x": a * b, "*": a * b}.get(op, a / b if b else 0)
    shown = f"{value:.4f}".rstrip("0").rstrip(".") if not float(value).is_integer() else str(int(value))
    return f"{m['a']} {'divided by' if op in ('over', '/') else 'times' if op in ('x', '*') else 'plus' if op == '+' else 'minus' if op == '-' else op} {m['b']} is {shown}."


def _carry_targets(clauses: list[tuple[str, Intent | None]]) -> list[tuple[str, Intent | None]]:
    """'Make the workshop lights white and set brightness to 100': the second clause names no device,
    so it means the workshop lights, not a device called 'brightness'."""
    out: list[tuple[str, Intent | None]] = []
    last: Intent | None = None
    for text, intent in clauses:
        if intent is not None and last is not None and not intent.is_query and SETTING_ONLY.match(intent.target or ""):
            intent = replace(intent, target=last.target, area=intent.area or last.area, loose=False)
        if intent is not None and intent.target and not SETTING_ONLY.match(intent.target):
            last = intent
        out.append((text, intent))
    return out


@dataclass
class Config:
    confirm_sensitive: bool = True
    retrieval_k: int = 8
    strip_assistant_name: bool = True  # drop "hey Nova" typed or spoken into a phone app
    llm_mode: str = "intent"  # "intent": LLM rewrites to intents, resolver picks devices; "candidates": LLM picks from top-K


@dataclass
class _Pending:
    kind: str  # "clarify" | "confirm" | "llm_clarify"
    expires: float
    utterance: str = ""  # original request, for llm_clarify
    intent: Intent | None = None
    candidates: list[Entity] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    hit: music.MusicHit | None = None  # kind "music": what to play once the speaker is chosen
    resume: bool = False  # kind "music": "play some music", so carry on with the speaker's queue if it has one


@dataclass
class _Context:
    """What was last acted on or asked about, so the next request can say "them" or "and the kitchen"."""
    expires: float
    entities: list[str]
    intents: list[Intent]
    before: dict[str, tuple[str, dict]] = field(default_factory=dict)  # state and attributes before the last change


def _tidy(utterance: str) -> str:
    """Words that change nothing about what to do: "back to white" means "to white", a trailing "again" is noise."""
    said = BACK_TO.sub("", utterance)
    if not NOT_AGAIN.match(said) and len(said.split()) >= 3:
        said = AGAIN_END.sub("", said)
    return said


def _restore(entity_id: str, state: str, attrs: dict) -> Action | None:
    """The action that puts one device back the way it was, or None when that cannot be done exactly."""
    domain = entity_id.split(".", 1)[0]
    if domain not in UNDO_DOMAINS or state not in ("on", "off"):
        return None
    if state == "off" or domain != "light":
        return Action.make(f"{domain}.turn_{state}", entity_id)
    data: dict = {}
    if (b := attrs.get("brightness")) is not None:
        data["brightness_pct"] = max(1, min(100, round(float(b) / 255 * 100)))
    mode = attrs.get("color_mode")
    if mode == "color_temp" and (k := attrs.get("color_temp_kelvin")):
        data["color_temp_kelvin"] = int(k)
    elif mode in COLOR_MODES and (hs := attrs.get("hs_color")) and len(hs) == 2:
        data["hs_color"] = (round(float(hs[0]), 1), round(float(hs[1]), 1))
    return Action.make("light.turn_on", entity_id, data)


class Pipeline:
    def __init__(self, house: House, config: Config | None = None, clock=time.monotonic,
                 llm_tiers: list[LLMClient] | None = None, persona: Persona | None = None,
                 music_provider: music.MusicProvider | None = None, feed_source: feeds.FeedSource | None = None,
                 feed_count: int = 3, feed_summary: bool = False,
                 weather_source: weather.WeatherSource | None = None, now=datetime.now, home_chat: bool = False):
        self.now = now  # local wall-clock time; Home Assistant passes its own, which knows the home's time zone
        self.home_chat = home_chat  # may the language model answer questions about the home (needs a capable model)
        self.music = music_provider
        self.weather = weather_source
        self.feeds, self.feed_count, self.feed_summary = feed_source, feed_count, feed_summary
        self.config = config or Config()
        self.persona = persona or Persona()
        self.responder = Responder(self.persona)
        names = [re.escape(n) for n in self.persona.names()] if self.config.strip_assistant_name else []
        # "hey nova, turn on the light" / "turn on the light, nova"
        self._name_re = re.compile(
            rf"^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?(?:{'|'.join(names)})(?![\w])[\s,]*"
            rf"|[\s,]*(?<![\w])(?:{'|'.join(names)})[\s.!?]*$",
            re.I) if names else None
        self.clock = clock
        self.llm_tiers = list(llm_tiers or [])
        self._pending: dict[str, _Pending] = {}
        self._context: dict[str, _Context] = {}  # by speaker: a voice satellite starts a new conversation each wake
        self._last_intents: list[Intent] = []
        self._answering = False
        self.trace: list[str] = []  # what the language model said during the last request, for diagnostics
        self.update_house(house)

    def update_house(self, house: House) -> None:
        """Call when registries, exposure or aliases change."""
        self.house = house
        self.index = EntityIndex(house)
        self.resolver = Resolver(self.index, self.config.confirm_sensitive, self.responder)
        self.planner = (Planner(self.index, self.resolver, self.llm_tiers, k=self.config.retrieval_k)
                        if self.llm_tiers and self.config.llm_mode == "candidates" else None)
        self.parser = (SemanticParser(self.index, self.llm_tiers)
                       if self.llm_tiers and self.config.llm_mode == "intent" else None)

    # ---- entry point -----------------------------------------------------------

    def handle(self, utterance: str, context_area: str | None = None,
               conversation_id: str | None = None) -> Outcome:
        """Rules only: fast, deterministic, no network."""
        utterance = _tidy(self._strip_name(utterance))
        out, escalate = self._rules(utterance, context_area, conversation_id)
        return (self._unparsed(utterance, context_area, conversation_id) or out) if escalate else out

    def _unparsed(self, utterance: str, area: str | None, cid: str | None) -> Outcome | None:
        """What the grammar did not parse but still needs no model."""
        return (self._named_scene(utterance, cid)  # "movie night", "let's have movie night"
                or (None if self._answering else self._bare(utterance, area, cid))  # "lights", "the fan": switch it
                or self._spot(utterance, area, cid))  # "it's bedtime, turn off everything upstairs"

    def _strip_name(self, utterance: str) -> str:
        return self._name_re.sub("", utterance) if self._name_re else utterance

    async def ahandle(self, utterance: str, context_area: str | None = None,
                      conversation_id: str | None = None, speaker: str | None = None) -> Outcome:
        """Rules first; the LLM tiers only see what the rules could not place.
        `speaker` is the device that heard it, which outlives one conversation."""
        self._last_intents = []
        key = speaker or conversation_id
        self._forget_expired()
        out = await self._ahandle(utterance, context_area, conversation_id, key)
        self._note(key, out)
        return out

    async def _ahandle(self, utterance: str, context_area: str | None, conversation_id: str | None,
                       key: str | None) -> Outcome:
        utterance = _tidy(self._strip_name(utterance))
        self.trace = []
        pending = self._pending.get(conversation_id) if conversation_id else None
        if pending and pending.kind == "llm_clarify":
            del self._pending[conversation_id]
            if pending.expires >= self.clock() and self.planner:
                plan = await self.planner.plan(pending.utterance, context_area, clarification=utterance)
                if plan is not None:
                    return self._finish_llm(plan.resolution, pending.utterance, conversation_id)
        if CAPABILITIES.match(utterance.lower().strip(" ?.!,")):
            return Outcome(Kind.ANSWER, message=self.responder.say("capabilities", things=self._able_to()))
        if not (pending and pending.expires >= self.clock()) and (quick := self._quick(utterance)) is not None:
            return quick
        if not (pending and pending.expires >= self.clock()):
            said = tokens(utterance)
            if said and all(len(w) <= 1 for w in said):  # a stray "a" from speech-to-text is not a request
                return Outcome(Kind.CHAT, message=self.responder.say("no_action"))
            if NEGATIVE.search(utterance.lower()):  # "-5 percent": never quietly act on 5
                return Outcome(Kind.CHAT, message=self.responder.say("no_action"))
        if self.feeds is not None and (asked := feeds.ask(utterance, self.feeds.names())) is not None \
                and not self._resolvable(utterance, context_area):
            return await self._news(asked, utterance)
        if self.weather is not None and (wq := weather.ask(utterance)) is not None \
                and not self._resolvable(utterance, context_area):
            return await self._weather(wq)
        if self.music is not None and not (pending and pending.kind == "music"):
            heard = await self._music(utterance, context_area, conversation_id)
            if heard is not None:
                return heard
        if not (pending and pending.expires >= self.clock()):
            referred = self._refer(utterance, context_area, conversation_id, key)
            if referred is not None:
                return referred
            if (later := self._later(utterance, context_area)) is not None:
                return later
            if (own := self._named_scene(utterance, conversation_id, exact=True)) is not None:
                return own  # a scene or script the household named is theirs, whatever else the words could mean
        out, escalate = self._rules(utterance, context_area, conversation_id)
        if not escalate:
            return out
        if (plain := self._unparsed(utterance, context_area, conversation_id)) is not None:
            return plain
        if CAUTION.search(normalize(utterance)):
            return out  # "don't..." / "except...": a paraphraser that drops one word does the opposite
        if self.parser is not None and self._home_related(utterance, context_area):
            parsed = await self.parser.parse(utterance, context_area)
            self.trace = list(self.parser.last_trace)
            if parsed is None:
                return out
            if parsed[0].chat:
                return Outcome(Kind.CHAT)
            resolutions = [self._aimed(i, context_area, key) for i in parsed[0].intents]
            for n, r in enumerate(resolutions):  # a mood we have no look for: let the model design one
                if r.needs_design and self.llm_tiers and (palette := await ambience.design(self.llm_tiers, r.needs_design)):
                    resolutions[n] = self.resolver.ambience(r.intent, context_area, palette)
            return self._combine(resolutions, conversation_id)  # same policy as the rules
        if self.planner is None or self.parser is not None:
            return out  # intent mode found nothing home-related: plain conversation
        plan = await self.planner.plan(utterance, context_area)
        if plan is None:
            return out
        return self._finish_llm(plan.resolution, utterance, conversation_id)

    # ---- answers that never need a model ------------------------------------------

    def _quick(self, utterance: str) -> Outcome | None:
        """The time, the date, thanks, never mind, which rooms and scenes exist. A small model gets these wrong."""
        say, text = self.responder.say, normalize(utterance)
        raw = re.sub(r"[^a-z' ]", " ", utterance.lower()).strip()
        raw = re.sub(r"\s+", " ", raw)
        if TIME_Q.match(text):
            return Outcome(Kind.ANSWER, message=say("time_now", time=f"{self.now():%I:%M %p}".lstrip("0")))
        if DATE_Q.match(text):
            now = self.now()
            return Outcome(Kind.ANSWER, message=say("date_today", date=f"{now:%A, %B} {now.day}"))
        if (m := SUM_Q.match(text)) and (answer := _sum(m)) is not None:
            return Outcome(Kind.ANSWER, message=answer)
        if (m := SPELL_Q.match(raw)):
            word = m["w"]
            return Outcome(Kind.ANSWER, message=f"{word.capitalize()}: {', '.join(c.upper() for c in word if c.isalpha())}.")
        if raw in THANKS:
            return Outcome(Kind.CHAT, message=say("welcome"))  # small talk, not a fact about the home
        if raw in NEVER_MIND:
            return Outcome(Kind.ANSWER, message=say("okay"))
        if ROOMS_Q.match(text) and self.house.areas:
            return Outcome(Kind.ANSWER, message=say("rooms_list", names=qa.join_names(
                sorted(name for name, _ in self.house.areas.values()), limit=20)))
        if SCENES_Q.match(text):
            names = sorted({e.name for e in self.house.exposed() if e.domain in ("scene", "script") and e.available})
            if not names:
                return Outcome(Kind.ANSWER, message=say("qa_inv_none", what="scenes", where="the house"))
            return Outcome(Kind.ANSWER, message=say("scenes_list", names=qa.join_names(names, limit=12)))
        return None

    def _later(self, utterance: str, context_area: str | None) -> Outcome | None:
        """Timers, reminders, lists and "in 10 minutes": not ours. Home Assistant's own Assist may do them;
        if it cannot, say so plainly rather than hunting for a device called "lights in 10 minutes"."""
        text = normalize(utterance)
        if HANDOFF.search(text):
            return Outcome(Kind.CHAT, message=self.responder.say("no_schedule") if re.search(
                r"timer|remind|alarm|wake me", text) else "", handoff=True)
        words = text.split()
        if LATER.search(text) and words and words[0] in COMMAND_WORDS and not self._resolvable(utterance, context_area):
            return Outcome(Kind.CHAT, message=self.responder.say("no_schedule"), handoff=True)
        return None

    def _bare(self, utterance: str, area: str | None, cid: str | None) -> Outcome | None:
        """A light or fan named with no verb: on if it is off, off if it is on. Nothing else is switched this way,
        and a room alone is not enough unless a question was just asked."""
        text = normalize(utterance)
        words = text.split()
        if not words or len(words) > 5 or QUESTION.match(text) or words[0] in COMMAND_WORDS:
            return None
        if self.resolver.areas_in(text) and not self.index.find(text, {"light", "fan"}):
            return None
        res = self.resolver.resolve(Intent("toggle", text), area)
        out = res.outcome
        ids = [a.entity_id for a in out.actions] if out.kind is Kind.ACTION else out.entities
        if out.kind is Kind.CLARIFY and not ids and text in ("light", "lights", "lamp", "lamps", "fan", "fans"):
            return self._combine([res], cid)  # "lights" with no room to go by: ask which room, and remember
        if out.kind not in (Kind.ACTION, Kind.CLARIFY) or not ids or \
                any(i.split(".", 1)[0] not in ("light", "fan") for i in ids):
            return None
        return self._combine([res], cid)

    def _spot(self, utterance: str, area: str | None, cid: str | None) -> Outcome | None:
        """The command inside a longer sentence, when what comes before it is only scene-setting."""
        words = normalize(utterance).split()
        for i in range(2, min(len(words) - 1, 9)):
            if words[i] not in SPOT_VERBS:
                continue
            if words[0] in ASKING or set(words[:i]) & NOT_PREAMBLE or CAUTION.search(" ".join(words)):
                return None
            rest = " ".join(words[i:])
            intent = parse(rest)
            if intent is not None and not intent.is_query and not intent.loose and self.resolver.can_resolve(intent, area):
                named = [a for n in (2, 1) for j in range(i - n + 1) if (a := self.index.find_areas(" ".join(words[j:j + n])))]
                room = next(iter(named[0])) if named and len(named[0]) == 1 else area  # "the office is stuffy, ..."
                return self._rules(rest, room, cid)[0]
        return None

    def _named_scene(self, utterance: str, cid: str | None, exact: bool = False) -> Outcome | None:
        said = tokens(utterance)
        while not exact and said and said[0] in SCENE_LEAD:  # "let's have movie night", "put on the movie night scene"
            said = said[1:]
        while not exact and said and said[-1] in ("scene", "mode", "script", "time"):
            said = said[:-1]
        if not said:
            return None
        found = [e for e in self.house.exposed() if e.domain in ("scene", "script") and e.available
                 and any(t == said or "".join(t) == "".join(said) for t in map(tokens, [e.name, *e.aliases]) if t)]
        if len(found) != 1:
            return None
        res = self.resolver.finalize(Intent("activate", " ".join(said)), found)
        if " ".join(said) in GREETINGS and res.outcome.kind is Kind.ACTION:  # a greeting must not act unasked
            self._remember(cid, _Pending("confirm", self.clock() + PENDING_TTL, actions=res.outcome.actions))
            return Outcome(Kind.CONFIRM, entities=[found[0].id],
                           message=self.responder.say("confirm", verb="start", names=label(found[0], self.house)))
        return self._combine([res], cid)

    def chat_plan(self, utterance: str, context_area: str | None = None) -> str | None:
        """What to say when nothing was understood, without a model; None when the model may answer.
        Chat can never act, and a small model asked about the home makes things up, so talk that names the
        home's own rooms or devices only reaches a model when that has been allowed in settings."""
        text = normalize(utterance)
        if LIVE.search(text):  # no model knows what flew over, who won last night or what it is like outside
            return self.responder.say("chat_unsure")
        if not self.home_talk(utterance):
            return None
        if HISTORY_Q.match(text):
            return self.responder.say("no_history")
        if not QUESTION.match(text):
            return self.responder.say("no_action")
        return None if self.home_chat else self.responder.say("home_unsure")

    def home_talk(self, utterance: str) -> bool:
        """Stricter than _home_related: a command word first, a device-type word, or the full name of one of the
        home's rooms or devices. "Who won the game last night" is not about a scene called Movie Night."""
        own = {w for n in self.persona.names() for w in n.replace(".", "").split()}
        words = [w for w in tokens(utterance) if w not in own]
        if not words:
            return False
        if set(words) & POINTING_TARGETS and set(words) & CHANGE_WORDS:
            return True  # "I need them back on": about something in the home, whatever it points at
        if words[0] in COMMAND_WORDS or set(words) & (HOME_WORDS | WORD_DOMAINS.keys() - {"warm", "cold", "cool", "play", "bright"}):
            return True
        joined = " " + " ".join(words) + " "
        names = [toks for _id, toks in self.index._areas] + [toks for _e, toks, w in self.index._phrases if w == 1.0]
        return any(len(" ".join(t)) >= 3 and f" {' '.join(t)} " in joined for t in names)

    def _forget_expired(self) -> None:
        if len(self._pending) + len(self._context) < 64:
            return
        now = self.clock()
        self._pending = {k: v for k, v in self._pending.items() if v.expires >= now}
        self._context = {k: v for k, v in self._context.items() if v.expires >= now}

    # ---- what the last request was about -----------------------------------------

    def _note(self, key: str | None, out: Outcome) -> None:
        if key is None or out.kind not in (Kind.ACTION, Kind.ANSWER):
            return
        ids = [a.entity_id for a in out.actions] if out.kind is Kind.ACTION else out.entities
        ids = [i for i in dict.fromkeys(ids) if i.split(".", 1)[0] not in ("scene", "script")]
        if not ids:
            return
        old = self._recent(key)
        if out.kind is Kind.ACTION:
            before = self._snapshot(ids)
        else:  # a question in between does not forget what "change it back" would undo
            before = old.before if old else {}
        self._context[key] = _Context(self.clock() + CONTEXT_TTL, ids, list(self._last_intents), before)

    def _snapshot(self, ids: list[str]) -> dict[str, tuple[str, dict]]:
        """How the devices about to change are now. A group is remembered member by member, since putting a
        group back sets every member to one colour; members that are not exposed keep the group as it is."""
        out: dict[str, tuple[str, dict]] = {}
        for i in ids:
            e = self.house.entities.get(i)
            if e is None:
                continue
            members = [self.house.entities.get(m) for m in e.attributes.get("entity_id") or []]
            if members and all(m is not None and m.exposed for m in members):
                for m in members:
                    out[m.id] = (m.state, dict(m.attributes))
            else:
                out[i] = (e.state, dict(e.attributes))
        return out

    def _undo(self, text: str, area: str | None, ctx: _Context | None) -> Outcome | None:
        """ "Change them back": the devices of the last change, set the way they were before it."""
        m = UNDO.match(text)
        if m is None or ctx is None or not ctx.before:
            return None
        before = ctx.before
        target = (m["t"] or "").strip()
        if target and target not in POINTING_TARGETS:  # "change the den lights back": only what was changed
            intent = parse(f"turn on {target}")
            res = self.resolver.resolve(intent, area) if intent else None
            named = {a.entity_id for a in res.outcome.actions} if res else set()
            wanted = set()
            for i in named:
                e = self.house.entities.get(i)
                wanted |= {i} | set((e.attributes.get("entity_id") if e else None) or [])
            before = {i: v for i, v in before.items() if i in wanted}
            if not before:
                return None
        actions = [a for i, (state, attrs) in before.items() if (a := _restore(i, state, attrs)) is not None]
        if not actions:
            return Outcome(Kind.CHAT, message=self.responder.say("no_action"))
        for a in actions:
            if (problem := validate(a, self.house)):
                return Outcome(Kind.REFUSE, message=problem)
        names = [self.house.entities[i] for i in ctx.entities if i in self.house.entities]
        self._last_intents = list(ctx.intents)
        return Outcome(Kind.ACTION, actions, message=self.responder.say(
            "done", verb="Restoring", names=qa.join_names([label(e, self.house) for e in names])))

    def _recent(self, key: str | None) -> _Context | None:
        ctx = self._context.get(key) if key else None
        if ctx is None or ctx.expires < self.clock():
            return None
        return ctx

    def _aimed(self, intent: Intent, area: str | None, key: str | None) -> Resolution:
        """The language model's reading of a request: "it" and "them" mean the same as they do for the rules."""
        ctx = self._recent(key)
        ents = [self.house.entities[i] for i in ctx.entities if i in self.house.entities] if ctx else []
        if intent.target in POINTING_TARGETS and ents and not intent.is_query:
            return self.resolver.finalize(intent, ents)
        return self.resolver.resolve(intent, area)

    def _refer(self, utterance: str, area: str | None, cid: str | None, key: str | None) -> Outcome | None:
        """Requests that lean on the one before: "turn them off", "brighter", "and the kitchen", "do that again"."""
        ctx = self._recent(key)
        text = normalize(utterance)
        ents = [self.house.entities[i] for i in ctx.entities if i in self.house.entities] if ctx else []
        if (undone := self._undo(text, area, ctx)) is not None:
            return undone
        if ents and text in AGAIN and (acts := [i for i in ctx.intents if not i.is_query]):
            return self._combine([self.resolver.finalize(i, ents) for i in acts], cid)
        said = self._pointed(text)
        intent = parse(said) if said else None
        if intent is not None and intent.target == "it" and intent.area and not intent.is_query \
                and said == text:  # "make it louder in the bar": the room was named, so that room's, not the last
            room = intent.area.removeprefix("the ")
            return self._combine([self.resolver.resolve(replace(intent, target=f"it in {room}", area=None), area)], cid)
        if intent is not None and intent.target == "it":
            if intent.is_query:
                if ctx and ctx.intents:
                    return self._combine([self.resolver.resolve(replace(intent, target=ctx.intents[0].target), area)], cid)
            elif ents:
                target = ctx.intents[0].target if ctx.intents else "it"
                return self._combine([self.resolver.finalize(replace(intent, target=target), ents)], cid)
            if area is not None:
                if PLURAL_POINTING.search(text) and not intent.is_query:  # "turn these on": the lights in this room
                    return self._combine([self.resolver.resolve(replace(intent, target="lights"), area)], cid)
                return self._combine([self.resolver.resolve(intent, area)], cid) if said != text else None
            return Outcome(Kind.CHAT, message=self.responder.say("no_action"))
        return self._also(text, area, cid, ctx) if ctx and ctx.intents else None

    @staticmethod
    def _pointed(text: str) -> str:
        """The request with whatever it points at said as "it", or "" if it is not that kind of request."""
        bare = AMOUNT.sub("", text)
        if bare in ("on", "off", "back on", "back off"):
            return f"turn it {bare.removeprefix('back ')}"
        if bare in ("up", "down"):
            return f"turn it {bare}"
        if bare in ADJUST_WORDS:
            return f"make it {bare}"
        said = re.sub(r"\bback (on|off|up|down)\b", r"\1", POINTING.sub("it", text))
        return said if re.search(r"\bit\b", said) else ""

    def _also(self, text: str, area: str | None, cid: str | None, ctx: _Context) -> Outcome | None:
        """The last request again somewhere else: "and the kitchen", "what about the bedroom", "the garage too"."""
        m = ALSO.match(text)
        where = next((g for g in m.groups() if g), None) if m else None
        where = where.removeprefix("the ").strip() if where else None
        if not where or parse(where) is not None:  # "and turn off the fan" is a request of its own
            return None
        resolutions = []
        for last in ctx.intents:
            if last.area is not None:
                resolutions.append(self.resolver.resolve(replace(last, area=f"in {where}"), area))
                continue
            kind = next((w for w in last.target.split() if w in TYPE_WORDS), None)
            if set(where.split()) & TYPE_WORDS:
                kind = None  # "and the tv" names its own kind of thing: not "tv lights"
            named = replace(last, target=f"{where} {kind}" if kind and kind not in where.split() else where)
            if not self.resolver.can_resolve(named, area):
                named = replace(last, target=where)
            if not self.resolver.can_resolve(named, area):
                return None
            resolutions.append(self.resolver.resolve(named, area))
        return self._combine(resolutions, cid) if resolutions else None

    def _able_to(self) -> str:
        have = {e.domain for e in self.house.exposed()}
        return qa.join_names([word for domain, word in ABLE_TO.items() if domain in have] or ["devices"])

    def _resolvable(self, text: str, context_area: str | None) -> bool:
        intent = parse(text)
        return bool(intent) and self.resolver.can_resolve(intent, context_area)

    def home_facts(self, utterance: str, context_area: str | None = None, k: int = 8) -> list[str]:
        """Current facts about the devices a sentence seems to be about, for grounding open conversation."""
        if not self._home_related(utterance, context_area):
            return []
        facts = []
        for cand in retrieve(self.index, utterance, context_area, k):
            e = cand.entity
            bits = [f"{e.name} ({self.house.area_name(e.area) or 'no room'}) is {e.state}"]
            if (b := e.attributes.get("brightness")) is not None and e.state == "on":
                bits.append(f"brightness {round(float(b) / 255 * 100)} percent")
            if (v := e.attributes.get("volume_level")) is not None:
                bits.append(f"volume {round(float(v) * 100)} percent")
            if (t := e.attributes.get("temperature")) is not None:
                bits.append(f"set to {t}")
            if e.unit and e.state not in ("unavailable", "unknown"):
                bits[0] += f" {e.unit}"
            facts.append(", ".join(bits))
        return facts

    def home_related(self, utterance: str, context_area: str | None = None) -> bool:
        return self._home_related(utterance, context_area)

    def _home_related(self, utterance: str, context_area: str | None) -> bool:
        """Cheap test for 'could this be about the home?', so plain chat skips the parsing call.
        True if the sentence uses a command word, a device-type word, or any word from the
        home's own device, room or floor names. Deliberately exact: fuzzy matches would let
        'Mona' count as 'main'."""
        own = {w for n in self.persona.names() for w in n.replace(".", "").split()}  # its own name is not a device
        words = set(tokens(utterance)) - STOP - own
        return bool(words & (COMMAND_WORDS | WORD_DOMAINS.keys() | (self.index.vocabulary - own)))

    def _finish_llm(self, res: Resolution, utterance: str, conversation_id: str | None) -> Outcome:
        """LLM proposals get the same validator and confirmation memory as rule results."""
        o = res.outcome
        for a in (o.actions if o.kind is Kind.ACTION else res.pending_actions):
            if (problem := validate(a, self.house)):
                return Outcome(Kind.REFUSE, message=problem)
        if o.kind is Kind.CONFIRM:
            self._remember(conversation_id, _Pending("confirm", self.clock() + PENDING_TTL,
                                                     actions=res.pending_actions))
        elif o.kind is Kind.CLARIFY:
            self._remember(conversation_id, _Pending("llm_clarify", self.clock() + PENDING_TTL,
                                                     utterance=utterance))
        return o

    def _rules(self, utterance: str, context_area: str | None,
               conversation_id: str | None) -> tuple[Outcome, bool]:
        """Returns (outcome, worth_escalating_to_llm)."""
        self._answering = False  # is this the reply to a question we asked? Then a room alone is an answer
        if conversation_id:
            followup = self._followup(utterance, context_area, conversation_id)
            if followup is not None:
                return followup, False
        main, leave_out = self._exception(utterance)
        if leave_out is not None and not leave_out[0] and not leave_out[1]:
            return Outcome(Kind.REFUSE, message=self.responder.say("exclude_unclear", target=leave_out[2])), False
        clauses = _carry_targets([(c, parse(c)) for c in prepare(main, lambda t: self._resolvable(t, context_area))])
        resolutions = [self.resolver.resolve(i, context_area) for _, i in clauses if i]
        if leave_out is not None:
            resolutions = [self._without(r, leave_out) for r in resolutions]
        if all(r.outcome.kind in (Kind.CHAT, Kind.REFUSE) for r in resolutions):  # nothing real was understood
            plain = normalize(utterance)
            mood = None if re.search(r"\b(?:off|out)\b", plain) else ambience.detect(plain, self.index.find_areas)
            if mood is not None:  # "make the workshop halloween colors"
                theme, where = mood
                res = self.resolver.resolve(Intent("ambience", where, option=theme), context_area)
                return self._combine([res], conversation_id), False
        if not resolutions:
            return Outcome(Kind.CHAT), True
        out = self._combine(resolutions, conversation_id)
        live = [r for r in resolutions if r.outcome.kind is not Kind.CHAT]
        if len(resolutions) == 1:
            escalate = resolutions[0].escalate
        else:
            escalate = not live and any(r.escalate for r in resolutions)
        return out, escalate

    # ---- "all the lights except the bar" ----------------------------------------------

    def _exception(self, utterance: str):
        """Split off 'except the bar' / 'but leave light a on'. Returns (main request, None) when there is
        no exception, else (main, (areas, entities, words)); both empty means the exception was not understood."""
        text = normalize(utterance)
        m = EXCEPT.match(text)
        if not m:
            return utterance, None
        words = m.group("ex").strip()
        areas = self.resolver.areas_in(words)
        ents: set[str] = set()
        if not areas:
            found = self.index.find(words)
            top = [x for x in found if x.score >= 0.85]
            if top and (len(top) == 1 or top[1].score < top[0].score - 0.05):
                ents = {top[0].entity.id}
        return m.group("main"), (areas, ents, words)

    def _without(self, res: Resolution, leave_out) -> Resolution:
        """The same request minus what was excluded. Something the exception does not touch is left alone."""
        areas, ents, words = leave_out
        out = res.outcome
        if out.kind is not Kind.ACTION or res.intent is None:
            return res
        keep = [self.house.entities[a.entity_id] for a in out.actions]
        keep = [e for e in {e.id: e for e in keep}.values() if e.id not in ents and e.area not in areas]
        if len(keep) == len(set(a.entity_id for a in out.actions)):
            return Resolution(Outcome(Kind.REFUSE, message=self.responder.say("exclude_unclear", target=words)),
                              res.intent)  # the exception named nothing in this request: never ignore it
        if not keep:
            return Resolution(Outcome(Kind.REFUSE, message=self.responder.say("exclude_unclear", target=words)),
                              res.intent)
        return self.resolver.finalize(res.intent, keep)

    # ---- news and status feeds ---------------------------------------------------

    async def _news(self, asked: feeds.FeedQuery, utterance: str) -> Outcome:
        """Headlines read from the feeds, or a model summary that stays within them. Never an action."""
        say = self.responder.say
        if not self.feeds.names():
            return Outcome(Kind.ANSWER, message=say("feeds_none"))
        try:
            got = await self.feeds.fetch(list(asked.feeds) or (None if asked.topic else self._news_feeds()))
        except Exception:  # a failing feed must never read as "nothing new"
            got = []
        source = asked.feeds[0] if len(asked.feeds) == 1 else "your feeds"
        readable = [f for f in got if f.items]
        if not readable:
            failed = not got or not all(f.ok for f in got)
            return Outcome(Kind.ANSWER, message=say("feed_failed" if failed else "feed_empty", source=source))
        if asked.status:
            name, item = feeds.pick(readable, 1)[0]
            return Outcome(Kind.ANSWER, message=say("feed_status", source=name, title=item.title.rstrip("."),
                                                    age=feeds.age(item.published, datetime.now(timezone.utc))))
        picked = feeds.pick(readable, self.feed_count, asked.topic)
        if not picked:
            return Outcome(Kind.ANSWER, message=say("feed_topic_none", topic=asked.topic))
        if (self.feed_summary or asked.summarize) and self.llm_tiers:
            summary = await feeds.summarize(self.llm_tiers, utterance, picked, self.trace)
            if summary:
                return Outcome(Kind.ANSWER, message=summary)
        one = len({name for name, _ in picked}) == 1
        if all(not f.ok for f in readable):  # could not refresh: say so, and how old these are
            when = max((f.read_at for f in readable if f.read_at), default=None)
            return Outcome(Kind.ANSWER, message=say(
                "feed_stale", source=picked[0][0] if one else source, age=feeds.age(when, datetime.now(timezone.utc)),
                headlines=feeds.headline_text(picked, one)))
        return Outcome(Kind.ANSWER, message=say("feed_headlines", source=picked[0][0] if one else source,
                                                headlines=feeds.headline_text(picked, one)))

    async def _weather(self, wq: weather.WeatherQuery) -> Outcome:
        """What the home's weather entity reports, in a sentence. Never a model's guess."""
        say = self.responder.say
        if not self.weather.available():
            return Outcome(Kind.ANSWER, message=say("weather_none"))
        try:
            if wq.offset is None:
                report = weather.now_report(await self.weather.now())
            else:
                report = weather.day_report(await self.weather.day(wq.offset))
        except Exception:  # a failing weather service must not read as fine weather
            report = ""
        if not report:
            return Outcome(Kind.ANSWER, message=say("weather_failed"))
        if wq.offset is None:
            return Outcome(Kind.ANSWER, message=say("weather_now", report=report))
        return Outcome(Kind.ANSWER, message=say("weather_forecast", day="tomorrow" if wq.offset else "today",
                                                report=report))

    def _news_feeds(self) -> list[str] | None:
        """'What's the news' means the feeds with news in their name, not weather or sports, when there are any."""
        return [n for n in self.feeds.names() if "news" in tokens(n)] or None

    # ---- music -----------------------------------------------------------------

    async def _music(self, utterance: str, context_area: str | None, cid: str | None) -> Outcome | None:
        """'play some jazz in the workshop', 'read me <book>': search, pick one honest match, play it.
        None means this is not a music request (or a plain resume) and the usual rules should handle it."""
        split = music.verb_split(utterance)
        if split is None:
            return None
        rest, hint = split
        if rest in ("", "it", "again", "it again", "playback", "audio") or \
                (not music.is_generic(music.strip_here(rest)[0]) and self._resolvable(utterance, context_area)):
            return None  # "play it again", "play the kitchen speaker": the usual rules
        rest, here = music.strip_here(rest)  # "play some music down here": this room's speaker
        generic = music.is_generic(rest) and hint is None
        searchable = getattr(self.music, "available", lambda: True)()
        if generic and not searchable:
            return None  # no music library: "play some music" can still resume a speaker through the usual rules
        if not generic:
            request = music.interpret(rest, hint)
            if request is None or not request.query:  # "play an audiobook" names nothing to look for
                return None
            if not searchable:  # say what is missing, never "that room can't play music"
                return Outcome(Kind.REFUSE, message=self.responder.say("music_needs_library"))
        where, chosen, missed = rest, None, ""
        if not here:
            for what, tail in music.splits(rest):
                if not tail:
                    continue
                status, players = self.resolver.find_players(tail, context_area)
                if status != "none":
                    where, chosen = what, (status, players)
                    break
                if music.is_generic(what) and not missed:  # "play music on <something we don't know>"
                    missed, where = tail, what
                    chosen = self._loose_players(tail, context_area)
        if chosen is None:
            if missed:
                return Outcome(Kind.REFUSE, message=self.responder.say("not_found", target=missed))
            chosen = self.resolver.find_players("", context_area)
        status, players = chosen
        if status == "many" and context_area:  # "play music on the basement" from a basement speaker: that one
            local = [p for p in players if p.area == context_area]
            if len(local) == 1:
                status, players = "one", local
        say = self.responder.say
        if status == "unsupported":
            return Outcome(Kind.REFUSE, message=say("music_unsupported", name=label(players[0], self.house)))
        if status == "noplayers" or (status == "none" and not players):
            return Outcome(Kind.REFUSE, message=say("music_unsupported", name="that room"))
        if music.is_generic(where) and hint is None:
            hit, resume = await self._favourite(), True
        else:
            resume = False
            request = music.interpret(where, hint) or request
            try:
                hit = await self._find_music(request)
            except Exception:  # a failing music service must never read as "not found" or as success
                return Outcome(Kind.REFUSE, message=say("no_action"))
            if hit is None:
                return Outcome(Kind.REFUSE, message=say("music_not_found", query=request.query))
        if status == "many":
            names = [label(p, self.house) for p in players]
            self._remember(cid, _Pending("music", self.clock() + PENDING_TTL, candidates=list(players), hit=hit,
                                         resume=resume))
            return Outcome(Kind.CLARIFY, entities=[p.id for p in players],
                           message=say("clarify_options", options=", ".join(names[:-1]) + f" or {names[-1]}")
                           if len(names) > 1 else say("clarify_one", name=names[0]))
        return self._play_something(players[0], hit) if resume else self._play(hit, players[0])

    def _loose_players(self, tail: str, context_area: str | None) -> tuple[str, list[Entity]] | None:
        """A speaker phrase with a misheard word at the end ('the basement voice pd'): the longest start of it
        that names a speaker or room."""
        words = tail.split()
        for n in range(len(words) - 1, 0, -1):
            status, players = self.resolver.find_players(" ".join(words[:n]), context_area)
            if status != "none" and set(tokens(" ".join(words[:n]))) - STOP:
                return status, players
        return None

    async def _find_music(self, request: music.MusicRequest) -> music.MusicHit | None:
        """Search, then the same with a misheard number, then the household's own playlists."""
        for query in music.variants(request.query):
            asked = replace(request, query=query)
            if (hit := music.choose(asked, await self.music.search(query, request.artist))) is not None:
                return hit
        if request.media_type in (None, "playlist") and not request.artist:
            try:
                own = await self.music.library("playlist", 100)
            except Exception:
                own = []
            for query in music.variants(request.query):
                if (hit := music.choose(replace(request, query=query, media_type="playlist"),
                                        {"playlists": own})) is not None:
                    return hit
        return None

    async def _favourite(self) -> music.MusicHit | None:
        """What 'play some music' plays on a speaker with nothing queued: the most recently played playlist."""
        try:
            own = await self.music.library("playlist", 5)
        except Exception:
            return None
        return own[0] if own else None

    def _play_something(self, player: Entity, fallback: music.MusicHit | None) -> Outcome:
        """'Play some music': carry on with what the speaker had queued, else a recent playlist, else ask."""
        if player.state == "playing":  # music is already on: say what, rather than start something else over it
            return Outcome(Kind.ANSWER, entities=[player.id], message=self.responder.say(
                "qa_playing", title=player.attributes.get("media_title") or "music", name=label(player, self.house)))
        if player.state == "paused" or (player.state in ("idle", "on", "off", "standby")
                                        and player.attributes.get("media_title")) or fallback is None:
            if fallback is None and not player.attributes.get("media_title") and player.state != "paused":
                return Outcome(Kind.CHAT, message=self.responder.say("music_which"))
            action = Action.make("media_player.media_play", player.id)
            if (problem := validate(action, self.house)):
                return Outcome(Kind.REFUSE, message=problem)
            return Outcome(Kind.ACTION, [action], entities=[player.id],
                           message=self.responder.say("done", verb="Resuming", names=label(player, self.house)))
        return self._play(fallback, player)

    def _play(self, hit: music.MusicHit, player: Entity) -> Outcome:
        action = Action.make("music_assistant.play_media", player.id, {
            "media_id": hit.uri, "media_type": hit.media_type, "enqueue": "replace"})
        if (problem := validate(action, self.house)):
            return Outcome(Kind.REFUSE, message=problem)
        title = f"{hit.name} by {hit.artists[0]}" if hit.artists and hit.media_type == "track" else hit.name
        return Outcome(Kind.ACTION, [action], entities=[player.id],
                       message=self.responder.say("now_playing", title=title, name=label(player, self.house)))

    # ---- combining clauses -----------------------------------------------------

    def _combine(self, resolutions: list[Resolution], conversation_id: str | None) -> Outcome:
        live = [r for r in resolutions if r.outcome.kind is not Kind.CHAT]
        self._last_intents = [r.intent for r in live if r.intent is not None]
        if not live:
            return resolutions[0].outcome
        blocked = [r for r in live if r.outcome.kind in (Kind.REFUSE, Kind.CLARIFY)]
        if blocked:
            first = blocked[0]
            if first.outcome.kind is Kind.CLARIFY and len(live) == 1:
                self._remember(conversation_id, _Pending(
                    "clarify", self.clock() + PENDING_TTL, intent=first.intent, candidates=first.candidates))
            return first.outcome  # never run part of a request that has an unresolved part
        actions: list[Action] = []
        confirm_entities: list[str] = []
        answers: list[Outcome] = []
        for r in live:
            if r.outcome.kind is Kind.ANSWER:
                answers.append(r.outcome)
            elif r.outcome.kind is Kind.CONFIRM:
                confirm_entities += r.outcome.entities
                actions += r.pending_actions
            else:
                actions += r.outcome.actions
        actions = list(dict.fromkeys(actions))
        for a in actions:
            if (problem := validate(a, self.house)):
                return Outcome(Kind.REFUSE, message=problem)
        if confirm_entities:
            msg = next(r.outcome.message for r in live if r.outcome.kind is Kind.CONFIRM)
            self._remember(conversation_id, _Pending("confirm", self.clock() + PENDING_TTL, actions=actions))
            return Outcome(Kind.CONFIRM, entities=confirm_entities, message=msg)
        if actions:
            msg = " ".join(dict.fromkeys(r.outcome.message for r in live if r.outcome.message))
            return Outcome(Kind.ACTION, actions, message=msg)
        return Outcome(Kind.ANSWER, entities=[e for a in answers for e in a.entities],
                       message=" ".join(a.message for a in answers))

    # ---- follow-ups ------------------------------------------------------------

    def _remember(self, conversation_id: str | None, pending: _Pending) -> None:
        if conversation_id:
            self._pending[conversation_id] = pending

    def _followup(self, utterance: str, context_area: str | None, cid: str) -> Outcome | None:
        pending = self._pending.pop(cid, None)
        if pending is None or pending.expires < self.clock() or pending.kind == "llm_clarify":
            return None
        raw = re.findall(r"[a-z']+", utterance.lower())
        words = set(raw)
        self._answering = True
        if pending.kind == "confirm":
            if self._agrees(raw):
                for a in pending.actions:
                    if (problem := validate(a, self.house)):
                        return Outcome(Kind.REFUSE, message=problem)
                return Outcome(Kind.ACTION, pending.actions, message=self.responder.say("doing_it"))
            if words & NO and len(raw) <= 4:
                return Outcome(Kind.CHAT, message=self.responder.say("cancelled"))
            return None  # something new: the question is dropped, and this is treated as a fresh request
        if pending.kind == "clarify" and not pending.candidates and pending.intent is not None:
            where = re.sub(r"^(?:in |on |at )?(?:the )?", "", normalize(utterance))  # "Which room's lights?" "Kitchen."
            where = re.sub(r" (?:one|1|please)$", "", where)
            if where in ("all", "all of them", "everywhere", "every room", "all rooms", "all the rooms", "whole house",
                         "the whole house", "everything", "both", "all of it"):  # "Which room's lights?" "All of them."
                target = re.sub(r"^(?:all |every )?(?:the )?", "", pending.intent.target)
                return self._combine([self.resolver.resolve(replace(pending.intent, target=f"all {target}"),
                                                            context_area)], cid)
            if where and self.resolver.areas_in(where):
                target = pending.intent.target
                return self._combine([self.resolver.resolve(replace(pending.intent, target=f"{target} in {where}"),
                                                            context_area)], cid)
            return None
        if words & NO and len(raw) <= 3:
            return Outcome(Kind.ANSWER, message=self.responder.say("okay"))  # "Did you mean ...?" "No."
        said = " ".join(raw)
        if pending.kind == "clarify" and pending.intent is not None and said in ("both", "both of them", "all of them",
                                                                               "all", "all of those", "every one"):
            return self._combine([self.resolver.finalize(pending.intent, pending.candidates)], cid)
        chosen = self._choose(utterance, pending.candidates)
        where = re.sub(r"^(?:in |on |at )?(?:the )?", "", normalize(utterance))
        where = re.sub(r" (?:one|1)$", "", where)  # "the den one"
        if chosen is None and where and (rooms := self.resolver.areas_in(where)):
            inside = [e for e in pending.candidates if e.area in rooms]
            if len(inside) == 1:
                chosen = inside[0]
            elif len(inside) > 1:  # still more than one there: ask again, about those only
                names = [label(e, self.house) for e in inside]
                self._remember(cid, replace(pending, expires=self.clock() + PENDING_TTL, candidates=inside))
                return Outcome(Kind.CLARIFY, entities=[e.id for e in inside], message=self.responder.say(
                    "clarify_options", options=", ".join(names[:-1]) + f" or {names[-1]}"))
            elif pending.kind == "clarify" and pending.intent is not None:  # none of those: the same request there
                return self._combine([self.resolver.resolve(
                    replace(pending.intent, target=f"{pending.intent.target} in {where}"), context_area)], cid)
        if chosen is None:
            return None
        if pending.kind == "music" and pending.resume:
            return self._play_something(chosen, pending.hit)
        if pending.kind == "music" and pending.hit is not None:
            return self._play(pending.hit, chosen)
        resolution = self.resolver.finalize(pending.intent, [chosen])
        return self._combine([resolution], cid)

    @staticmethod
    def _agrees(raw: list[str]) -> bool:
        """Is this reply a yes and nothing else?"""
        words = set(raw)
        if not raw or len(raw) > 6 or words & NO:
            return False
        return (bool(words & YES) or " ".join(raw) in GO_AHEAD) and words <= YES | YES_FILL

    def _choose(self, reply: str, candidates: list[Entity]) -> Entity | None:
        # work on raw words: normalize() would turn the filler word "one" into the digit 1
        raw = re.findall(r"[a-z0-9']+", reply.lower())
        for w in raw:
            if w in ORDINALS and w not in FILLER and len(candidates) > abs(ORDINALS[w]):
                return candidates[ORDINALS[w]]
        if len(candidates) == 1 and self._agrees(re.findall(r"[a-z']+", reply.lower())):
            return candidates[0]
        letters = [w for w in raw if len(w) == 1 and w.isalpha()]
        if len(letters) == 1 and len(raw) <= 3:  # "Light A or Light B?" "A."
            lettered = [e for e in candidates if tokens(e.name)[-1:] == (letters[0],)]
            if len(lettered) == 1:
                return lettered[0]
        target = tokens(" ".join(w for w in raw if w not in FILLER and w not in ORDINALS))
        if not target:
            return None
        scored = []
        for e in candidates:
            area = tokens(self.house.area_name(e.area) or "")
            phrases = [tokens(e.name), area, area + tokens(e.name), *[tokens(a) for a in e.aliases]]
            scored.append((max(phrase_sim(target, p) for p in phrases if p), e))
        scored.sort(key=lambda s: -s[0])
        if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
            return scored[0][1]
        return None


__all__ = ["Config", "Pipeline", "label"]
