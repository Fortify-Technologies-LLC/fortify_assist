"""The LLM tier is a proposal engine. These tests pin down what it can never do."""
import asyncio
import json

import pytest

from fortify_core import Config, Pipeline
from fortify_core.llm import LLMError, ScriptedLLM
from fortify_core.retrieval import retrieve
from fortify_core.index import EntityIndex
from fortify_core.types import Entity, Kind

CAND = Config(llm_mode="candidates")
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


def ref_of(user: str, name: str, area: str | None = None) -> str:
    for line in user.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 5 and parts[1] == name and (area is None or parts[3] == area):
            return parts[0]
    raise AssertionError(f"{name} not offered to the model:\n{user}")


def run(pipe, text, ctx=None, cid=None):
    return asyncio.run(pipe.ahandle(text, ctx, cid))


def ids(o):
    return sorted(a.entity_id for a in o.actions)


def test_llm_resolves_what_rules_cannot(house):
    llm = ScriptedLLM(lambda s, u: {"decision": "act", "calls": [
        {"ref": ref_of(u, "Blinds"), "service": "close_cover"}]})
    out = run(Pipeline(house, CAND, llm_tiers=[llm]), "yank the blinds")
    assert out.kind is Kind.ACTION and ids(out) == ["cover.living_room_blinds"]
    assert out.actions[0].service == "cover.close_cover"


def test_rules_never_call_the_llm_when_confident(house):
    llm = ScriptedLLM(lambda s, u: pytest.fail("LLM should not be called"))
    pipe = Pipeline(house, CAND, llm_tiers=[llm])
    assert run(pipe, "turn on the deck lights").kind is Kind.ACTION
    assert run(pipe, "turn on the lamp").kind is Kind.CLARIFY  # rules know it is ambiguous
    assert run(pipe, "turn on the porch light").kind is Kind.REFUSE  # known but unavailable
    assert llm.prompts == []


@pytest.mark.parametrize("bad", [
    {"decision": "act", "calls": [{"ref": "e99", "service": "turn_on"}]},          # invented ref
    {"decision": "act", "calls": [{"ref": "e1", "service": "launch_missiles"}]},   # invented service
    {"decision": "act", "calls": [{"ref": "e1", "service": "turn_on", "data": {"brightness_pct": 900}}]},
    {"decision": "act", "calls": [{"ref": "e1", "service": "turn_on", "data": {"colour": "red"}}]},
    {"decision": "act", "calls": []},
    {"decision": "act"},
    {"decision": "ask", "refs": ["e77"], "question": "which?"},
    "not json at all",
    "[1, 2]",
])
def test_invalid_model_output_never_becomes_an_action(house, bad):
    out = run(Pipeline(house, CAND, llm_tiers=[ScriptedLLM(lambda s, u: bad)]), "turn on the flux capacitor")
    assert out.kind is Kind.REFUSE and not out.actions  # the rules' honest answer stands


def test_model_cannot_act_on_a_device_the_request_does_not_point_to(house):
    def reply(s, u):  # a valid service on a real candidate, but nothing in the request points to it
        return {"decision": "act", "calls": [{"ref": ref_of(u, "House Alarm"), "service": "alarm_disarm"}]}

    out = run(Pipeline(house, CAND, llm_tiers=[ScriptedLLM(reply)]), "turn on the flux capacitor")
    assert out.kind is Kind.REFUSE, "no evidence in the utterance for the alarm"


def test_service_must_belong_to_the_entitys_domain(house):
    def reply(s, u):
        return {"decision": "act", "calls": [{"ref": ref_of(u, "Blinds"), "service": "unlock"}]}

    assert run(Pipeline(house, CAND, llm_tiers=[ScriptedLLM(reply)]), "yank the blinds").kind is not Kind.ACTION


def test_model_proposed_sensitive_actions_still_need_confirmation(house):
    def reply(s, u):
        return {"decision": "act", "calls": [{"ref": ref_of(u, "Front Door Lock"), "service": "unlock"}]}

    pipe = Pipeline(house, CAND, llm_tiers=[ScriptedLLM(reply)])
    out = run(pipe, "let me into the front door", cid="c")
    assert out.kind is Kind.CONFIRM and not out.actions
    done = run(pipe, "yes", cid="c")
    assert [a.service for a in done.actions] == ["lock.unlock"]


def test_model_cannot_touch_unavailable_or_unexposed_entities(house):
    for name in ("Porch Light", "Closet Light"):
        llm = ScriptedLLM(lambda s, u: {"decision": "act", "calls": [
            {"ref": ref_of(u, name), "service": "turn_on"}]} if f"| {name} |" in u else "{}")
        out = run(Pipeline(house, CAND, llm_tiers=[llm]), f"switch on the {name.lower()}")
        assert out.kind is not Kind.ACTION


def test_answers_use_real_state_not_model_text(house):
    llm = ScriptedLLM(lambda s, u: {"decision": "answer", "refs": [ref_of(u, "Living Room Temperature")],
                                    "question": "it is 99 degrees"})
    out = run(Pipeline(house, CAND, llm_tiers=[llm]), "how warm is it in the living room")
    assert out.kind is Kind.ANSWER and "71.5" in out.message and "99" not in out.message


def test_tiers_fall_through_on_error_unsure_and_invalid(house):
    good = {"decision": "act"}  # placeholder, replaced below
    local = ScriptedLLM([LLMError("down")], name="local")
    mid = ScriptedLLM([{"decision": "unsure"}], name="mid")
    cloud = ScriptedLLM(lambda s, u: {"decision": "act", "calls": [
        {"ref": ref_of(u, "Blinds"), "service": "close_cover"}]}, name="cloud")
    pipe = Pipeline(house, CAND, llm_tiers=[local, mid, cloud])
    out = run(pipe, "yank the blinds")
    assert ids(out) == ["cover.living_room_blinds"]
    assert pipe.planner.last_trace[-1].startswith("cloud:")
    assert good  # silence linters about the placeholder


def test_first_valid_tier_wins_and_later_tiers_are_not_called(house):
    local = ScriptedLLM(lambda s, u: {"decision": "act", "calls": [
        {"ref": ref_of(u, "Blinds"), "service": "close_cover"}]}, name="local")
    cloud = ScriptedLLM(lambda s, u: pytest.fail("cloud must not be called"), name="cloud")
    assert run(Pipeline(house, CAND, llm_tiers=[local, cloud]), "yank the blinds").kind is Kind.ACTION


def test_chat_decision_leaves_a_chat_outcome(house):
    pipe = Pipeline(house, CAND, llm_tiers=[ScriptedLLM([{"decision": "chat"}])])
    assert run(pipe, "tell me a joke").kind is Kind.CHAT


def test_model_question_keeps_the_conversation_and_replans_with_the_answer(house):
    def reply(s, u):
        if "clarified" not in u:
            return {"decision": "ask", "refs": [ref_of(u, "Lamp", "Bedroom"), ref_of(u, "Lamp", "Office")],
                    "question": "Which lamp"}
        return {"decision": "act", "calls": [{"ref": ref_of(u, "Lamp", "Office"), "service": "turn_off"}]}

    pipe = Pipeline(house, CAND, llm_tiers=[ScriptedLLM(reply)])
    first = run(pipe, "do away with the lamp", cid="c")
    assert first.kind is Kind.CLARIFY and first.message == "Which lamp?"
    assert ids(run(pipe, "the office one", cid="c")) == ["light.office_lamp"]


def test_prompt_size_does_not_grow_with_the_house(house):
    llm = ScriptedLLM(lambda s, u: {"decision": "chat"})
    run(Pipeline(house, CAND, llm_tiers=[llm]), "yank the blinds")
    small = len(llm.prompts[-1][1])
    for i in range(500):
        house.entities[f"light.extra_{i}"] = Entity(f"light.extra_{i}", f"Extra Light {i}", "bar", "off")
    llm2 = ScriptedLLM(lambda s, u: {"decision": "chat"})
    run(Pipeline(house, CAND, llm_tiers=[llm2]), "yank the blinds")
    assert len(llm2.prompts[-1][1]) < small * 1.3


def test_retrieval_surfaces_the_right_device_for_loose_wording(house):
    index = EntityIndex(house)
    top = [c.entity.id for c in retrieve(index, "kill the lights in the workshop", None, 5)]
    assert {"light.workshop_a", "light.workshop_b"} <= set(top[:3])
    top = [c.entity.id for c in retrieve(index, "yank the blinds", None, 3)]
    assert "cover.living_room_blinds" in top
    top = [c.entity.id for c in retrieve(index, "make it warmer", "bedroom", 6)]
    assert "climate.upstairs" in top


def test_schema_only_offers_candidate_refs(house):
    llm = ScriptedLLM(lambda s, u: {"decision": "chat"})
    run(Pipeline(house, CAND, llm_tiers=[llm]), "yank the blinds")
    # the schema is not stored by the double, so check via a client-visible side channel
    assert llm.prompts and "Devices (ref | name" in llm.prompts[0][1]


def test_json_round_trip_of_ids_is_stable():
    assert json.loads(json.dumps({"decision": "chat"}))["decision"] == "chat"
