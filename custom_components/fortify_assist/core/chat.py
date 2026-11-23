"""Open conversation in the assistant's persona.

Chat is the one place the model writes free text, so it is fenced in: it cannot control
devices or read states from here (the deterministic pipeline does both), it is told so, and
its reply is trimmed to something sensible to say out loud.
"""
from __future__ import annotations

import re
from datetime import datetime

from .llm import LLMClient, LLMError
from .persona import Persona
from .responses import Responder

MAX_REPLY_CHARS = 600
_DID = (r"turn(?:ed|ing)?|switch(?:ed|ing)?|set(?:ting)?|chang(?:ed|ing)|dimm(?:ed|ing)|brighten(?:ed|ing)|"
        r"play(?:ed|ing)?|lock(?:ed|ing)?|unlock(?:ed|ing)?|open(?:ed|ing)?|clos(?:ed|ing)|start(?:ed|ing)|"
        r"stopp(?:ed|ing)|paus(?:ed|ing)|adjust(?:ed|ing)|activat(?:ed|ing)|mak(?:e|ing)|made|queu(?:ed|ing)")
CLAIM = re.compile(
    rf"\bi(?:'ve|'m|'ll| have| am| will)?\s+(?:just |now |already |gone ahead and |also )?(?:{_DID})\b"
    r"|\b(?:done|all set|got it done|sorted)\b[.!]?\s*$|^\W*turning\b|\bby me\b|\b(?:have|has|had|were|was) (?:been )?turned\b"
    r"|\b(?:is|are) now (?:on|off|set|playing|locked|unlocked|open|closed)\b"
    r"|\b(?:switching|turning|setting|dimming|brightening|locking|unlocking|opening|closing|pausing|activating)\b"
    r"[^.!?]{0,60}\b(?:on|off|up|down|now|for you|as requested)\b"
    r"|\b(?:is|are) (?:illuminated|lit|back on|back off|switched (?:on|off)|turned (?:on|off)) (?:again|now)\b", re.I)
# For a request about the home, a reply that reports a device state is invented: chat is never given live states
# it could quote this way ("they are on and warm white at 75 percent"). Only checked for home-related requests.
STATE_CLAIM = re.compile(
    r"\b(?:is|are|were|was) (?:now |still |already )?(?:on|off|lit|dimmed|playing|locked|unlocked|open|closed|"
    r"running|set to)\b|\b\d+ ?(?:percent|%)(?: brightness)?\b|\bbrightness (?:of|at|to) \d+", re.I)
# Outside the home a reply that only reports success ("Done.", "Success, played the music") is just as invented.
DONE = re.compile(r"^\W*(?:done|all set|all done|sorted|success\w*|completed?)\b"
                  r"|\b(?:is|are) now (?:on|off|set|playing|locked|unlocked|open|closed)\b", re.I)
# The assistant runs the home. A model that says otherwise tells people to stop asking it for anything.
DENIAL = re.compile(r"\b(?:can(?:'t|not| not)|unable to|not able to|don't have|do not have|doesn't include|no (?:access|control|ability))"
                    r"\b[^.!?]{0,50}\b(?:control|access|change|turn|switch|adjust|interact|devices?|lights?|smart home)\b"
                    r"|\b(?:as|just|only|am|i'm) an ai\b", re.I)
# The small model sometimes fills an example with made-up names instead of the home's real ones.
PLACEHOLDER = re.compile(r"\b(?:Room \d|[Dd]evice [A-Z0-9])\b")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
HOME_THING = re.compile(r"\b(?:lights?|lamps?|bulbs?|devices?|tvs?|television|doors?|locks?|thermostat|speakers?|fans?|"
                        r"blinds|curtains?|garage|switch(?:es)?|scenes?|volume|temperature|heating|music)\b", re.I)
HISTORY_TURNS = 6


def system_prompt(persona: Persona, style: str, now: datetime, rooms: list[str], facts: list[str] | None = None,
                  home: bool = True) -> str:
    who = persona.agent_name + (f" ({persona.acronym_expansion})" if persona.acronym_expansion else "")
    lines = [f"You are {who}, a voice assistant for a smart home."]
    if persona.spoken_name and persona.spoken_name != persona.agent_name:
        lines.append(f"People say your name as '{persona.spoken_name}'.")
    if persona.owner:
        lines.append(f"You serve {persona.owner}.")
    if persona.maker:
        lines.append(f"You were made by {persona.maker}.")
    if persona.purpose:
        lines.append(f"Your purpose: {persona.purpose}")
    lines.append(f"Personality: {style}")
    if persona.traits:
        lines.append(f"Additional traits: {persona.traits}")
    lines.append(f"Today is {now:%A, %B} {now.day}, {now.year}.")
    lines.append(
        "Your replies are spoken aloud. Answer the question directly in one or two short sentences of plain "
        "text: no lists, no markdown, no emoji. Do not introduce yourself, do not mention the date or time "
        "unless asked, and do not offer more help or ask a question back. General knowledge, jokes and small "
        "talk are all welcome: answer them plainly from what you know, and stay in character while you do.")
    if home:
        if rooms:
            lines.append("Rooms in the home: " + ", ".join(rooms) + ".")
        if facts:
            lines.append("What is true in the home right now (the ONLY facts you may use about devices): "
                         + "; ".join(facts) + ".")
        lines.append(
            "You run this home: its lights, devices and music are yours to control, so never say you can't, that "
            "you lack access, or that you are only an AI. Commands are carried out before you are asked to speak, "
            "so when a message reaches you nothing has been done: never say you did, are doing or will do "
            "something in the home. If someone wants something done, ask which device or room they mean. You do "
            "NOT know any device states beyond the facts given: if asked about one that is not in the facts, say "
            "you are not sure rather than inventing it.")
    else:
        lines.append("You also run this home's lights, devices and music, but this message is not about the home, "
                     "so do not bring the home up.")
    return "\n".join(lines)


def clean(text: str) -> str:
    """Make model output safe to speak: no markdown, no stray think blocks, bounded length."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"[*_`#>]+", "", text)
    text = EMOJI.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_REPLY_CHARS:
        cut = text[:MAX_REPLY_CHARS]
        text = cut[: max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))].strip() or cut.strip()
        text = text if text[-1:] in ".!?" else text + "."
    return text


class Chatter:
    def __init__(self, persona: Persona, responder: Responder, tiers: list[LLMClient]):
        self.persona, self.responder, self.tiers = persona, responder, tiers
        self.last_trace: list[str] = []

    def _introduces_itself(self, text: str, user_text: str) -> bool:
        """A reply that opens with "I am <name>..." to a question that was not about the assistant is a small
        model reciting its own instructions (or a custom model its built-in ones), not an answer."""
        if re.search(r"\b(?:who|your name|yourself|you called|introduce|what are you)\b", user_text.lower()):
            return False
        names = "|".join(re.escape(n.strip().lower()) for n in self.persona.names() if n.strip())
        return bool(names) and bool(re.match(rf"\W*(?:i am|i'm|my name is|this is)\s+(?:{names})\b", text.lower()))

    async def reply(self, history: list[tuple[str, str]], user_text: str, now: datetime,
                    rooms: list[str], facts: list[str] | None = None, home_request: bool = False) -> str:
        """`history` is [(role, text)] with role 'user' or 'assistant', oldest first, without user_text."""
        self.last_trace = []
        if not self.tiers:
            return self.responder.say("chat_off")
        messages = [{"role": "system", "content": system_prompt(self.persona, self.responder.chat_style, now, rooms,
                                                                 facts, home=home_request)}]
        messages += [{"role": r, "content": t} for r, t in history[-HISTORY_TURNS * 2:]]
        messages.append({"role": "user", "content": user_text})
        self.last_trace = []
        for client in self.tiers:
            try:
                text = clean(await client.chat(messages))
            except LLMError as exc:
                self.last_trace.append(f"{client.name}: error {exc}")
                continue
            about_home = home_request or bool(HOME_THING.search(text))  # the reply itself talks about devices
            if text and ((about_home and CLAIM.search(text)) or (home_request and not facts and STATE_CLAIM.search(text))):
                self.last_trace.append(f"{client.name}: claimed an action; replaced")  # chat can never act
                return self.responder.say("no_action" if home_request else "chat_unsure")
            if text and (DENIAL.search(text) or PLACEHOLDER.search(text)):
                self.last_trace.append(f"{client.name}: denied running the home; replaced")
                return self.responder.say("no_action" if home_request else "chat_unsure")
            if text and self._introduces_itself(text, user_text):
                self.last_trace.append(f"{client.name}: introduced itself unasked; replaced")
                return self.responder.say("chat_unsure")
            if text and DONE.search(text):
                self.last_trace.append(f"{client.name}: claimed success; replaced")
                return self.responder.say("chat_unsure")
            if text:
                self.last_trace.append(f"{client.name}: ok")
                return text
        return self.responder.say("chat_off")
