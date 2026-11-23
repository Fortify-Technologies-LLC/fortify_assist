"""Regressions found by running the assistant against a real home (workshop and basement)."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.chat import system_prompt
from fortify_core.llm import ScriptedLLM
from fortify_core.persona import Persona
from fortify_core.types import Entity, Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


@pytest.fixture()
def pipe(house):
    return Pipeline(house)


def ids(out):
    return sorted(a.entity_id for a in out.actions)


def run(p, text, ctx=None, cid=None):
    return asyncio.run(p.ahandle(text, ctx, cid))


def act(**clause):
    return {"decision": "act", "clauses": [clause]}


# ---- inventory: what exists, not what is on ---------------------------------------------------

def test_what_lights_are_in_a_room(pipe):
    out = pipe.handle("what lights are in the workshop")
    assert out.kind is Kind.ANSWER and out.entities == ["light.workshop_a", "light.workshop_b"]
    assert out.message == "There are 2 lights in the workshop: Workshop Light A and Workshop Light B."


def test_how_many_lights_are_on_a_whole_floor(pipe):
    out = pipe.handle("how many lights are in the basement")
    assert sorted(out.entities) == ["light.bar_strip", "light.workshop_a", "light.workshop_b"]
    assert out.message == "There are 3 lights in the basement."


@pytest.mark.parametrize("text,count", [
    ("how many fans are there", 2), ("list the locks", 2), ("what lights are in the bedroom", 2),
    ("show me the fans", 2), ("what is in the garage", 3), ("how many fans do we have", 2),
])
def test_inventory_phrasings(pipe, text, count):
    out = pipe.handle(text)
    assert out.kind is Kind.ANSWER and len(out.entities) == count, out


def test_singular_grammar_and_empty_rooms(pipe):
    assert pipe.handle("how many lights are in the kitchen").message == "There are 2 lights in the kitchen."
    assert pipe.handle("what fans are in the garage").message == "There are no fans in the garage."
    assert pipe.handle("how many locks are in the garage").message == "There are no locks in the garage."


def test_unavailable_devices_are_counted_and_admitted(house):
    house.entities["light.broken_porch"].area = "bedroom"
    out = Pipeline(house).handle("how many lights are in the bedroom")
    assert "3 lights" in out.message and out.message.endswith("1 of them is unavailable right now.")


def test_inventory_never_includes_unexposed_devices(pipe):
    assert "light.hidden_closet" not in pipe.handle("what lights are in the bedroom").entities


def test_the_model_can_ask_for_an_inventory_and_cannot_bolt_on_a_state(house):
    reply = {"decision": "query", "clauses": [{"intent": "query_count", "target": "lights", "area": "workshop",
                                               "state": "off"}]}  # the model added "off"; nobody said it
    out = run(Pipeline(house, llm_tiers=[ScriptedLLM([reply])]), "gimme a headcount of lights over in the workshop")
    assert out.kind is Kind.ANSWER and out.message == "There are 2 lights in the workshop."


def test_idle_speakers_are_not_on(pipe):
    assert pipe.handle("is anything on in the bar").entities == ["light.bar_strip", "media_player.bar_speaker"]
    assert "media_player.kitchen_speaker" not in pipe.handle("is anything on in the kitchen").entities


# ---- several things in one breath --------------------------------------------------------------

def test_comma_separated_lists(pipe):
    out = pipe.handle("turn on the deck lights, turn off the bar strip and turn off the office lamp")
    assert ids(out) == ["light.bar_strip", "light.deck", "light.office_lamp"]
    assert {a.entity_id: a.service for a in out.actions}["light.bar_strip"] == "light.turn_off"


def test_a_verbless_second_part_inherits_the_verb(pipe):
    assert ids(pipe.handle("turn off the bedroom lamp and the office lamp")) == ["light.bedroom_lamp", "light.office_lamp"]
    out = pipe.handle("turn off the bedroom lamp, and the office lamp")
    assert ids(out) == ["light.bedroom_lamp", "light.office_lamp"]
    out = pipe.handle("turn on the deck lights then turn off the bar strip")
    assert [a.service for a in out.actions] == ["light.turn_on", "light.turn_off"]


def test_a_device_with_and_in_its_name_is_not_split(house):
    house.entities["light.sp"] = Entity("light.sp", "Salt and Pepper Lamp", "kitchen", "on")
    assert ids(Pipeline(house).handle("turn off the salt and pepper lamp")) == ["light.sp"]


def test_a_bare_unknown_thing_is_not_invented_into_a_command(pipe):
    out = pipe.handle("turn off the deck lights and the flux capacitor")
    assert not out.actions  # one part is not a device: nothing runs


def test_one_of_the_lights_asks_which_and_the_answer_finishes_the_job(pipe):
    out = pipe.handle("make one of the lights in the workshop red", conversation_id="c")
    assert out.kind is Kind.CLARIFY and "Workshop Light A" in out.message and not out.actions
    done = pipe.handle("workshop light b", conversation_id="c")
    assert ids(done) == ["light.workshop_b"] and done.actions[0].data_dict() == {"color_name": "red"}


def test_your_original_two_colour_request_never_guesses(pipe):
    out = pipe.handle("can you make one of the lights in the workshop red and another one green")
    assert out.kind is Kind.CLARIFY and not out.actions


# ---- the model must not widen what the user asked for -----------------------------------------------

def test_a_model_that_drops_distinguishing_words_cannot_hit_the_whole_group(house):
    # "workshop light" alone would be ambiguous (A or B); the user said A
    p = Pipeline(house, llm_tiers=[ScriptedLLM([act(intent="turn_off", target="workshop light")])])
    out = run(p, "do away with the workshop light a")
    assert ids(out) == ["light.workshop_a"]


def test_widening_does_not_invent_when_the_user_was_vague(house):
    p = Pipeline(house, llm_tiers=[ScriptedLLM([act(intent="turn_off", target="workshop light")])])
    out = run(p, "do away with the workshop light")
    assert out.kind is Kind.CLARIFY  # still asks: A or B?


# ---- open conversation stays honest about the home ---------------------------------------------------

def test_chat_is_given_current_facts_and_told_to_use_only_those(pipe):
    facts = pipe.home_facts("tell me about the workshop lamp", None)
    assert any("Workshop" in f for f in facts)
    prompt = system_prompt(Persona(), "plain", __import__("datetime").datetime(2026, 1, 1), ["Workshop"], facts)
    assert "ONLY facts" in prompt and "not in the facts" in prompt


def test_plain_chat_gets_no_home_facts(pipe):
    assert pipe.home_facts("who painted the Mona Lisa") == []


def test_the_assistants_own_name_does_not_make_chat_look_like_a_home_request(house):
    house.entities["light.n"] = Entity("light.n", "Nova Display", "office", "on")
    p = Pipeline(house, persona=Persona(agent_name="Nova"))
    assert p.home_facts("how are you doing Nova") == []


def test_the_room_part_of_a_name_can_be_left_out(house):
    house.entities["light.wd"] = Entity("light.wd", "Workshop Desk Lights", "workshop", "on")
    p = Pipeline(house)
    assert ids(p.handle("turn off the desk lights")) == ["light.wd"]
    house.entities["light.wl"] = Entity("light.wl", "Workshop Lights", "workshop", "on")
    assert p.handle("turn off the lights").kind is Kind.CLARIFY  # "lights" alone names nothing in particular


def test_dropping_the_room_still_asks_when_two_rooms_share_the_rest(house):
    house.entities["light.a"] = Entity("light.a", "Garage Work Light", "garage", "on")
    house.entities["light.b"] = Entity("light.b", "Bar Work Light", "bar", "on")
    out = Pipeline(house).handle("turn off the work light")
    assert out.kind is Kind.CLARIFY and not out.actions
