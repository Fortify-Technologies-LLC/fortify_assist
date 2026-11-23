"""Compare an agent's Outcome with the expected one.

The metric that matters most is WRONG_ACTION: the agent *did something* it should not
have (wrong device, wrong value, acted when it should have asked or refused). A missed
action or an unneeded question is annoying; a wrong action is a broken product.
"""
from __future__ import annotations

from enum import StrEnum

from .types import Action, Kind, Outcome


class Verdict(StrEnum):
    PASS = "pass"
    WRONG_ACTION = "wrong_action"  # executed something unexpected
    MISSED = "missed"  # expected an action, agent did not act
    WRONG_RESPONSE = "wrong_response"  # no action either way, but wrong kind/answer


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.01
    return a == b


def _actions_match(expected: list[Action], actual: list[Action]) -> bool:
    exp = {(a.service, a.entity_id): a for a in expected}
    act = {(a.service, a.entity_id): a for a in actual}
    if exp.keys() != act.keys() or len(expected) != len(exp) or len(actual) != len(act):
        return False
    for key, e in exp.items():
        got = act[key].data_dict()
        for field, value in e.data_dict().items():
            if field not in got or not _same(got[field], value):
                return False
    return True


def score(expect: Outcome, actual: Outcome) -> Verdict:
    if actual.kind is Kind.ACTION:
        if expect.kind is Kind.ACTION and _actions_match(expect.actions, actual.actions):
            return Verdict.PASS
        return Verdict.WRONG_ACTION
    if expect.kind is Kind.ACTION:
        return Verdict.MISSED
    if actual.kind is not expect.kind:
        return Verdict.WRONG_RESPONSE
    if expect.kind is Kind.ANSWER and set(actual.entities) != set(expect.entities):
        return Verdict.WRONG_RESPONSE
    return Verdict.PASS
