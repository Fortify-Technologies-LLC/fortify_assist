"""Deliberately simple reference agents. They prove the harness works and set the floor
that the real pipeline has to beat."""
from __future__ import annotations

import re

from .types import Action, House, Kind, Outcome

_VERBS = [
    (re.compile(r"^(?:please )?turn on (?:the )?(.+)$"), "turn_on"),
    (re.compile(r"^(?:please )?turn (?:the )?(.+) on$"), "turn_on"),
    (re.compile(r"^(?:please )?turn off (?:the )?(.+)$"), "turn_off"),
    (re.compile(r"^(?:please )?turn (?:the )?(.+) off$"), "turn_off"),
]


class RefuseAgent:
    """Never does anything. Scores 0 wrong actions and shows the cost of doing nothing."""

    name = "refuse-all"

    def handle(self, utterance, house, context_area=None):
        return Outcome(Kind.REFUSE)


class ExactNameAgent:
    """Handles only 'turn on/off <exact entity name>'. Asks when a name is shared."""

    name = "exact-name"

    def handle(self, utterance: str, house: House, context_area: str | None = None) -> Outcome:
        text = utterance.lower().strip(" .?!")
        for pattern, service in _VERBS:
            m = pattern.match(text)
            if not m:
                continue
            key = m.group(1).strip()
            hits = [e for e in house.exposed()
                    if e.available and key in (e.name.lower(), *[a.lower() for a in e.aliases])
                    and e.domain in ("light", "switch", "fan", "media_player")]
            if len(hits) == 1:
                return Outcome(Kind.ACTION, [Action.make(f"{hits[0].domain}.{service}", hits[0].id)])
            if len(hits) > 1:
                return Outcome(Kind.CLARIFY, entities=[h.id for h in hits])
        return Outcome(Kind.REFUSE)


BASELINES = {a.name: a for a in (RefuseAgent(), ExactNameAgent())}


class FortifyAgent:
    """The real pipeline under test (core package)."""

    name = "fortify"

    def __init__(self):
        self._pipeline = None

    def handle(self, utterance, house, context_area=None):
        from fortify_core import Pipeline

        if self._pipeline is None or self._pipeline.house is not house:
            self._pipeline = Pipeline(house)
        return self._pipeline.handle(utterance, context_area)


BASELINES["fortify"] = FortifyAgent()


class FortifyLLMAgent:
    """Rules first, then an Ollama model. Set FORTIFY_OLLAMA_URL and FORTIFY_OLLAMA_MODEL."""

    name = "fortify-llm"

    def __init__(self):
        self._pipeline = None

    def handle(self, utterance, house, context_area=None):
        import asyncio
        import os

        from fortify_core import Config, Pipeline
        from fortify_core.llm import OllamaClient

        if self._pipeline is None or self._pipeline.house is not house:
            client = OllamaClient(os.environ["FORTIFY_OLLAMA_URL"], os.environ["FORTIFY_OLLAMA_MODEL"])
            mode = os.environ.get("FORTIFY_LLM_MODE", "intent")
            self._pipeline = Pipeline(house, Config(llm_mode=mode), llm_tiers=[client])
        return asyncio.run(self._pipeline.ahandle(utterance, context_area))


BASELINES["fortify-llm"] = FortifyLLMAgent()
