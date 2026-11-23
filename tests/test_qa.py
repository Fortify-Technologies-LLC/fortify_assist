import asyncio

import pytest

from fortify_core import Config, Pipeline
from fortify_core.llm import ScriptedLLM
from fortify_core.persona import Persona
from fortify_core.responses import list_personalities
from fortify_core.types import Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house

PERSONALITIES = [p["id"] for p in list_personalities()]


@pytest.fixture(scope="module")
def house():
    return load_house(DEFAULT_HOUSE)


@pytest.fixture()
def pipe(house):
    return Pipeline(house)


def test_answers_are_plain_computed_facts(pipe):
    assert pipe.handle("how many lights are on").message == "7 lights are on."
    assert pipe.handle("which doors are locked").message == "2 doors are locked: Front Door Lock and Back Door Lock."
    assert pipe.handle("are any lights on in the bedroom").message == "No lights are on."
    assert pipe.handle("are all the fans off").message == "Yes, all fans are off."
    out = pipe.handle("are all the lights off")
    assert out.message.startswith("No. These lights aren't off: ") and "Bar Strip" in out.message


def test_singular_grammar(pipe):
    assert pipe.handle("what lights are on in the kitchen").message == "1 light is on: Counter Lights."
    assert pipe.handle("how many lights are on in the kitchen").message == "1 light is on."


def test_device_names_are_qualified_house_wide_but_not_inside_a_room(pipe):
    assert "Ceiling Light in the Living Room" in pipe.handle("which lights are on").message
    assert "in the" not in pipe.handle("which lights are on in the workshop").message


def test_the_room_of_the_speaker_answers_here(pipe):
    assert pipe.handle("what is on here", context_area="workshop").entities == ["light.workshop_a", "light.workshop_b"]
    out = pipe.handle("what is on here")
    assert out.kind is Kind.CLARIFY  # no idea which room


def test_attribute_answers(pipe):
    assert pipe.handle("how bright is the office overhead light").message == "The brightness of Overhead Light in the Office is 50 percent."
    assert pipe.handle("what is the volume of the bar speaker").message == "The volume of Bar Speaker is 40 percent."
    assert pipe.handle("what is the thermostat set to").message == "Upstairs Thermostat in the Bedroom is set to 70 degrees."
    assert pipe.handle("what is playing").message == "Bar Speaker is playing Take Five by Dave Brubeck."


def test_no_data_is_admitted(house):
    p = Pipeline(house)
    house.entities["media_player.kitchen_speaker"].attributes.pop("volume_level")
    try:
        assert "don't have volume" in p.handle("what is the volume of the kitchen speaker").message
    finally:
        house.entities["media_player.kitchen_speaker"].attributes["volume_level"] = 0.25


def test_off_light_does_not_report_a_brightness(pipe):
    assert pipe.handle("how bright is the bedroom lamp").message.endswith("is off.")


def test_unknown_things_go_to_chat_not_to_a_made_up_answer(pipe):
    for text in ("which robots are on", "are all the doors on", "which lights are on in the dungeon"):
        assert pipe.handle(text).kind is Kind.CHAT, text


def test_unavailable_and_unexposed_devices_never_appear(pipe):
    names = pipe.handle("which lights are off").message
    assert "Porch" not in names and "Closet" not in names


@pytest.mark.parametrize("pid", PERSONALITIES)
def test_facts_survive_every_personality(house, pid):
    p = Pipeline(house, persona=Persona(personality=pid))
    for _ in range(10):
        assert "7" in p.handle("how many lights are on").message
        assert "Take Five" in p.handle("what is playing").message
        assert "Front Door Lock" in p.handle("which doors are locked").message
        assert "Bar Strip" in p.handle("are all the lights off").message
        assert "50" in p.handle("how bright is the office overhead light").message
        assert "70" in p.handle("what is the thermostat set to").message


@pytest.mark.parametrize("pid", PERSONALITIES)
def test_personality_does_not_change_the_answer_entities(house, pid):
    base, other = Pipeline(house), Pipeline(house, persona=Persona(personality=pid))
    for text in ("which lights are on", "are all the doors locked", "is anything open", "what is playing"):
        assert base.handle(text).entities == other.handle(text).entities


# ---- the LLM can rephrase a question, but the facts still come from state ----------------

def run(pipe, text):
    return asyncio.run(pipe.ahandle(text))


def clause(intent, target, **kw):
    return {"intent": intent, "target": target, **kw}


def test_llm_rewrites_loose_group_questions(house):
    reply = {"decision": "query", "clauses": [clause("query_any", "anything", state="on", area="workshop")]}
    p = Pipeline(house, llm_tiers=[ScriptedLLM([reply])])
    out = run(p, "is there anything still running in the workshop")
    assert out.kind is Kind.ANSWER and out.entities == ["light.workshop_a", "light.workshop_b"]


def test_llm_cannot_invent_a_room_or_a_state(house):
    for bad in (clause("query_list", "lights", state="on", area="dungeon"),
                clause("query_list", "lights", area="kitchen"),
                clause("query_list", "lights", state="glowing", area="kitchen")):
        p = Pipeline(house, llm_tiers=[ScriptedLLM([{"decision": "query", "clauses": [bad]}])])
        out = run(p, "what is lit up in the kitchen please")
        assert out.kind is not Kind.ANSWER or "Counter" not in out.message or bad["area"] == "kitchen" and bad.get("state") == "on"


def test_assistant_name_stripping_can_be_turned_off(house):
    persona = Persona(agent_name="Lamp")
    assert Pipeline(house, persona=persona).handle("lamp turn on the deck lights").kind is Kind.ACTION
    off = Pipeline(house, Config(strip_assistant_name=False), persona=persona)
    assert off.handle("lamp turn on the deck lights").kind is not Kind.ACTION
