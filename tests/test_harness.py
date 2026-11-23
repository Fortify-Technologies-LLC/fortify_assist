from pathlib import Path

import pytest

from fortify_eval.baselines import BASELINES
from fortify_eval.cli import DEFAULT_HAND, DEFAULT_HOUSE
from fortify_eval.corpus import build_corpus
from fortify_eval.house import load_house
from fortify_eval.runner import run
from fortify_eval.scoring import Verdict, score
from fortify_eval.types import Action, Kind, Outcome


@pytest.fixture(scope="module")
def house():
    return load_house(DEFAULT_HOUSE)


@pytest.fixture(scope="module")
def cases(house):
    return build_corpus(house, DEFAULT_HAND)


def act(service, entity, **data):
    return Outcome(Kind.ACTION, [Action.make(service, entity, data)])


def test_corpus_is_large_and_deterministic(house, cases):
    assert len(cases) >= 300
    again = build_corpus(house, DEFAULT_HAND)
    assert [(c.id, c.utterance) for c in again] == [(c.id, c.utterance) for c in cases]


def test_every_referenced_entity_exists(house, cases):
    for c in cases:
        for a in c.expect.actions:
            assert a.entity_id in house.entities, c.id
        for e in c.expect.entities:
            assert e in house.entities, c.id
        if c.context_area:
            assert c.context_area in house.areas, c.id


def test_expected_actions_never_target_unavailable_or_unexposed(house, cases):
    for c in cases:
        for a in c.expect.actions:
            e = house.entities[a.entity_id]
            assert e.exposed and e.available, c.id


def test_score_pass_and_wrong_action():
    want = act("light.turn_on", "light.a", brightness_pct=50)
    assert score(want, act("light.turn_on", "light.a", brightness_pct=50)) is Verdict.PASS
    assert score(want, act("light.turn_on", "light.b", brightness_pct=50)) is Verdict.WRONG_ACTION
    assert score(want, act("light.turn_on", "light.a", brightness_pct=40)) is Verdict.WRONG_ACTION
    assert score(want, act("light.turn_off", "light.a")) is Verdict.WRONG_ACTION


def test_acting_when_it_should_ask_is_a_wrong_action():
    assert score(Outcome(Kind.CONFIRM), act("lock.unlock", "lock.x")) is Verdict.WRONG_ACTION
    assert score(Outcome(Kind.CLARIFY), act("light.turn_on", "light.x")) is Verdict.WRONG_ACTION
    assert score(Outcome(Kind.CHAT), act("light.turn_on", "light.x")) is Verdict.WRONG_ACTION


def test_not_acting_is_missed_or_wrong_response():
    want = act("light.turn_on", "light.a")
    assert score(want, Outcome(Kind.REFUSE)) is Verdict.MISSED
    assert score(Outcome(Kind.CLARIFY), Outcome(Kind.REFUSE)) is Verdict.WRONG_RESPONSE
    assert score(Outcome(Kind.ANSWER, entities=["a"]), Outcome(Kind.ANSWER, entities=["b"])) \
        is Verdict.WRONG_RESPONSE


def test_multi_target_must_match_exactly():
    want = Outcome(Kind.ACTION, [Action.make("light.turn_on", "light.a"), Action.make("light.turn_on", "light.b")])
    assert score(want, act("light.turn_on", "light.a")) is Verdict.WRONG_ACTION


def test_refuse_all_never_acts(house, cases):
    report = run(BASELINES["refuse-all"], house, cases)
    assert report.wrong_action_rate == 0


def test_exact_name_baseline_is_safe_but_limited(house, cases):
    report = run(BASELINES["exact-name"], house, cases)
    assert 0.05 < report.accuracy < 0.6
    assert report.wrong_action_rate < 0.05


def test_cli_stats(capsys):
    from fortify_eval.cli import main
    assert main(["stats"]) == 0
    assert "cases" in capsys.readouterr().out
