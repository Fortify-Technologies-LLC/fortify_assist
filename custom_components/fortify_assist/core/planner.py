"""LLM planner: turns a request the rules could not handle into a *validated* plan.

The model sees only retrieved candidates and answers in a fixed JSON schema. Its output is
a proposal, never a command: refs must be among the candidates, services must belong to the
entity's domain, values must be in range, the entity needs evidence in the utterance, and
the result still passes the same sensitive-action gate and validator as the rules.
Any violation discards that tier's answer. Nothing the model says can skip those checks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .index import EntityIndex
from .llm import LLMClient, LLMError
from .resolver import Resolution, Resolver
from .retrieval import Candidate, retrieve
from .types import Action, Kind, Outcome
from .validator import RANGES, SERVICES

DATA_KEYS = {"brightness_pct": "integer", "volume_level": "number", "temperature": "number"}
SYSTEM = """You turn a spoken smart-home request into JSON. You may only use the devices listed.

decision:
- "act": the request clearly asks to change one or more listed devices. Put one entry per device in calls (ref, service, data).
- "answer": the request asks about the state of listed devices. Put their refs in refs.
- "ask": it could mean several listed devices. Put the candidate refs in refs and a short question.
- "chat": it is general conversation, or about something not in the list. Never invent a device.
- "unsure": you cannot tell.

Rules: use each device's own services only. data keys: brightness_pct 0-100 (light turn_on), volume_level 0.0-1.0 (media_player volume_set), temperature (climate set_temperature). When the request names a room, prefer devices in that room. Never act on a device the request does not point to. When in doubt choose "ask" or "unsure", not "act"."""


@dataclass
class Plan:
    resolution: Resolution
    tier: str


def _schema(cands: list[Candidate]) -> dict:
    services = sorted({s for c in cands for s in SERVICES.get(c.entity.domain, ())})
    refs = [c.ref for c in cands]
    return {
        "type": "object",
        "properties": {
            "decision": {"enum": ["act", "answer", "ask", "chat", "unsure"]},
            "calls": {"type": "array", "maxItems": 6, "items": {
                "type": "object",
                "properties": {
                    "ref": {"enum": refs},
                    "service": {"enum": services},
                    "data": {"type": "object", "properties": {
                        k: {"type": t} for k, t in DATA_KEYS.items()}, "additionalProperties": False},
                },
                "required": ["ref", "service"], "additionalProperties": False}},
            "refs": {"type": "array", "maxItems": 6, "items": {"enum": refs}},
            "question": {"type": "string"},
        },
        "required": ["decision"],
        "additionalProperties": False,
    }


def _describe(cands: list[Candidate], house) -> str:
    lines = []
    for c in cands:
        e = c.entity
        services = " ".join(sorted(SERVICES.get(e.domain, ()))) or "(read only)"
        lines.append(f"{c.ref} | {e.name} | {e.domain} | {house.area_name(e.area) or '-'} | {e.state} | {services}")
    return "\n".join(lines)


class Planner:
    def __init__(self, index: EntityIndex, resolver: Resolver, tiers: list[LLMClient],
                 k: int = 8, min_evidence: float = 0.25):
        self.index, self.resolver, self.house = index, resolver, index.house
        self.tiers, self.k, self.min_evidence = tiers, k, min_evidence
        self.last_trace: list[str] = []

    async def plan(self, utterance: str, context_area: str | None = None,
                   clarification: str | None = None) -> Plan | None:
        """First tier that returns a valid, decisive plan wins. None means: no usable plan."""
        text = f"{utterance}. {clarification}" if clarification else utterance
        cands = retrieve(self.index, text, context_area, self.k)
        if not cands:
            return None
        user = (f"Room of the speaker: {self.house.area_name(context_area) or 'unknown'}\n"
                f"Devices (ref | name | type | room | state | services):\n{_describe(cands, self.house)}\n\n"
                f"Request: {utterance}"
                + (f"\nThe user then clarified: {clarification}" if clarification else ""))
        schema = _schema(cands)
        self.last_trace = []
        for client in self.tiers:
            try:
                raw = await client.generate(SYSTEM, user, schema)
            except LLMError as exc:
                self.last_trace.append(f"{client.name}: error {exc}")
                continue
            res = self._interpret(raw, cands, utterance)
            self.last_trace.append(f"{client.name}: {raw.strip()[:200]}")
            if res is not None:
                return Plan(res, client.name)
        return None

    # ---- validation of the model's proposal --------------------------------------

    def _interpret(self, raw: str, cands: list[Candidate], utterance: str) -> Resolution | None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        by_ref = {c.ref: c for c in cands}
        decision = data.get("decision")
        if decision == "chat":
            return Resolution(Outcome(Kind.CHAT))
        if decision == "act":
            return self._act(data.get("calls"), by_ref)
        if decision in ("answer", "ask"):
            refs = data.get("refs")
            if not isinstance(refs, list) or not refs or not all(r in by_ref for r in refs):
                return None
            ents = [by_ref[r].entity for r in dict.fromkeys(refs)]
            if decision == "answer":
                outs = [self.resolver._answer(e) for e in ents]
                return Resolution(Outcome(Kind.ANSWER, entities=[e.id for e in ents],
                                          message=" ".join(o.message for o in outs)))
            question = data.get("question") if isinstance(data.get("question"), str) else ""
            question = question.strip() or "Which one do you mean?"
            if not question.endswith("?"):
                question += "?"  # a trailing question mark keeps the conversation open in HA
            return Resolution(Outcome(Kind.CLARIFY, entities=[e.id for e in ents], message=question),
                              candidates=ents)
        return None  # "unsure" or anything unexpected

    def _act(self, calls, by_ref) -> Resolution | None:
        if not isinstance(calls, list) or not calls:
            return None
        actions: list[Action] = []
        for call in calls:
            if not isinstance(call, dict) or call.get("ref") not in by_ref:
                return None
            cand = by_ref[call["ref"]]
            service = call.get("service")
            domain = cand.entity.domain
            if cand.score < self.min_evidence or service not in SERVICES.get(domain, ()):
                return None
            data = call.get("data") or {}
            if not isinstance(data, dict) or not set(data) <= set(DATA_KEYS):
                return None
            for key, value in data.items():
                lo, hi = RANGES[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                    return None
            actions.append(Action.make(f"{domain}.{service}", cand.entity.id, data))
        return self.resolver.gate(list(dict.fromkeys(actions)))


__all__ = ["Plan", "Planner"]
