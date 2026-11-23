"""Intent mode: the model rewrites, the rules resolve. It must never name a device itself."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.llm import LLMError, ScriptedLLM
from fortify_core.types import Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


def run(pipe, text, ctx=None, cid=None):
    return asyncio.run(pipe.ahandle(text, ctx, cid))


def act(*clauses):
    return {"decision": "act", "clauses": list(clauses)}


def clause(intent, target, **kw):
    return {"intent": intent, "target": target, **kw}


def ids(o):
    return sorted(a.entity_id for a in o.actions)


def pipe_for(house, reply):
    llm = ScriptedLLM(reply if callable(reply) else [reply])
    return Pipeline(house, llm_tiers=[llm]), llm


def test_paraphrase_is_resolved_by_the_rules(house):
    p, _ = pipe_for(house, act(clause("open", "blinds")))
    out = run(p, "lift the blinds")
    assert out.kind is Kind.ACTION and out.actions[0].service == "cover.open_cover"


def test_prompt_contains_no_device_list(house):
    p, llm = pipe_for(house, act(clause("close", "blinds")))
    run(p, "yank the blinds")
    prompt = llm.prompts[0][1]
    assert "Blinds" not in prompt and "light." not in prompt and "Request: yank the blinds" in prompt


def test_model_cannot_invent_a_target(house):
    p, _ = pipe_for(house, act(clause("turn_on", "deck lights")))
    assert run(p, "make it cosy in here").kind is not Kind.ACTION


def test_value_must_come_from_the_request(house):
    p, _ = pipe_for(house, act(clause("set_brightness", "bedroom lamp", value=80)))
    assert run(p, "get the bedroom lamp glowing nicely").kind is not Kind.ACTION
    p, _ = pipe_for(house, act(clause("set_brightness", "bedroom lamp", value=50)))
    out = run(p, "get the bedroom lamp glowing at half")
    assert out.actions[0].data_dict() == {"brightness_pct": 50}


def test_direction_must_not_contradict_the_request(house):
    p, _ = pipe_for(house, act(clause("close", "blinds")))
    assert run(p, "lift the blinds").kind is not Kind.ACTION
    p, _ = pipe_for(house, act(clause("turn_on", "bedroom lamp")))
    assert run(p, "send the bedroom lamp to sleep").kind is not Kind.ACTION
    p, _ = pipe_for(house, act(clause("close", "garage door")))
    assert run(p, "close up the garage door").kind is Kind.ACTION  # "up" is fine next to "close"


def test_ambiguity_and_sensitivity_still_come_from_the_rules(house):
    p, _ = pipe_for(house, act(clause("turn_off", "lamp")))
    assert run(p, "kill the lamp").kind is Kind.CLARIFY
    p, _ = pipe_for(house, act(clause("turn_off", "lamp")))
    assert ids(run(p, "kill the lamp", ctx="office")) == ["light.office_lamp"]
    p, _ = pipe_for(house, act(clause("unlock", "front door")))
    assert run(p, "let me in the front door", cid="c").kind is Kind.CONFIRM


def test_clarification_after_an_llm_parse_uses_the_normal_followup(house):
    p, _ = pipe_for(house, act(clause("turn_off", "lamp")))
    assert run(p, "kill the lamp", cid="c").kind is Kind.CLARIFY
    assert ids(run(p, "the office one", cid="c")) == ["light.office_lamp"]


def test_one_bad_clause_voids_everything(house):
    p, _ = pipe_for(house, act(clause("turn_on", "deck lights"), clause("turn_on", "teleporter")))
    out = run(p, "turn on the deck lights and fire up the teleporter")
    assert out.kind is not Kind.ACTION


def test_multi_clause_runs_when_all_resolve(house):
    p, _ = pipe_for(house, act(clause("turn_off", "bedroom lamp"), clause("turn_off", "office lamp")))
    out = run(p, "turn off the bedroom lamp, and the office lamp")
    assert ids(out) == ["light.bedroom_lamp", "light.office_lamp"]


def test_query_uses_real_state(house):
    p, _ = pipe_for(house, {"decision": "query", "clauses": [clause("query_value", "living room temperature")]})
    out = run(p, "how warm is it in the living room")
    assert out.kind is Kind.ANSWER and "71.5" in out.message


def test_chat_and_junk_fall_back_safely(house):
    p, _ = pipe_for(house, {"decision": "chat"})
    assert run(p, "tell me a joke").kind is Kind.CHAT
    for junk in ("nope", "[]", {"decision": "act"}, {"decision": "act", "clauses": [{"intent": "explode", "target": "x"}]},
                 {"decision": "query", "clauses": [clause("turn_on", "deck lights")]}):
        p, _ = pipe_for(house, junk)
        assert run(p, "turn on the christmas tree").kind is Kind.REFUSE


def test_cloud_tier_only_after_local_fails(house):
    local = ScriptedLLM([LLMError("down")], name="local")
    cloud = ScriptedLLM([act(clause("open", "blinds"))], name="cloud")
    p = Pipeline(house, llm_tiers=[local, cloud])
    assert run(p, "lift the blinds").kind is Kind.ACTION
    assert p.parser.last_trace[-1].startswith("cloud:")
    local = ScriptedLLM([act(clause("open", "blinds"))], name="local")
    cloud = ScriptedLLM(lambda s, u: pytest.fail("cloud must not be called"), name="cloud")
    assert run(Pipeline(house, llm_tiers=[local, cloud]), "lift the blinds").kind is Kind.ACTION


def test_plain_chat_skips_the_intent_parser(house):
    llm = ScriptedLLM(lambda s, u: pytest.fail("no parsing call for plain conversation"))
    p = Pipeline(house, llm_tiers=[llm])
    for text in ("tell me a joke", "who painted the Mona Lisa", "what is the capital of Italy"):
        assert run(p, text).kind is Kind.CHAT, text
    assert llm.prompts == []


def test_anything_that_mentions_the_home_still_reaches_the_parser(house):
    reply = act(clause("open", "blinds"))
    for text in ("lift the blinds", "could you yank the blinds"):  # a command word, or a device name
        llm = ScriptedLLM([reply])
        run(Pipeline(house, llm_tiers=[llm]), text)
        assert llm.prompts, text
