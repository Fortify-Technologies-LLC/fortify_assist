"""How people really talk to a speaker: one word, a room, a wish, a sentence with the command inside it.
All of it by rule, with no language model."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.llm import ScriptedLLM
from fortify_core.types import Entity, Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


def pipe(**kw):
    return Pipeline(load_house(DEFAULT_HOUSE), **kw)


def talk(p, *said, area=None):
    return [asyncio.run(p.ahandle(t, area, "c", "s")) for t in said]


def acts(out):
    return sorted(f"{a.service.split('.')[1]} {a.entity_id.split('.')[1]}" for a in out.actions)


OFFICE_OFF = ["turn_off office_lamp", "turn_off office_overhead"]  # one of them is on, so "lights" means off
KITCHEN_ON = ["turn_on kitchen_ceiling", "turn_on kitchen_counters"]


# ---- a device or a room with no verb -----------------------------------------------------------

@pytest.mark.parametrize("said, area, want", [
    ("Lights.", "office", OFFICE_OFF), ("Office lights.", None, OFFICE_OFF), ("the lamp please", "bedroom", ["turn_on bedroom_lamp"]),
    ("the fan", "office", ["turn_on office_fan"]), ("Office.", None, OFFICE_OFF),
])
def test_a_light_or_fan_named_alone_is_switched(said, area, want):
    assert acts(talk(pipe(), said, area=area)[0]) == want


@pytest.mark.parametrize("said", ["coffee maker", "garage door", "front door", "blinds", "living room tv", "thermostat"])
def test_nothing_else_is_switched_by_its_name_alone(said):
    assert not talk(pipe(), said)[0].actions


def test_lights_with_no_room_asks_and_takes_the_room_as_the_answer():
    asked, done = talk(pipe(), "Lights.", "Office.")
    assert asked.kind is Kind.CLARIFY and asked.message == "Which room's lights?" and acts(done) == OFFICE_OFF


@pytest.mark.parametrize("first, answer, want", [
    ("turn on the lights", "kitchen", KITCHEN_ON), ("turn on the lights", "in the kitchen", KITCHEN_ON),
    ("dim the lights", "the office", ["turn_on office_lamp", "turn_on office_overhead"]),
])
def test_which_room_is_answered_with_a_room(first, answer, want):
    assert acts(talk(pipe(), first, answer)[1]) == want


# ---- wishes, politeness and slips -------------------------------------------------------------

@pytest.mark.parametrize("said, area, want", [
    ("i'd like the bedroom lamp on please", None, ["turn_on bedroom_lamp"]),
    ("we need the 3d printer on", None, ["turn_on printer_3d"]),
    ("can we have the bar strip off", None, ["turn_off bar_strip"]),
    ("i want the desk fan running", None, ["turn_on office_fan"]),
    ("the vanity light needs to be on", None, ["turn_on bathroom_vanity"]),
    ("would you mind turning off the lamp in the office", None, ["turn_off office_lamp"]),
    ("shut the garage door would you", None, ["close_cover garage_door"]),
    ("make sure the front door is locked", None, ["lock front_door"]),
    ("draw the curtains in the bedroom", None, ["close_cover bedroom_curtain"]),
    ("pop the blinds open in the living room", None, ["open_cover living_room_blinds"]),
    ("crank the thermostat to 74", None, ["set_temperature upstairs"]),
    ("put the kitchen speaker at 30", None, ["volume_set kitchen_speaker"]),
    ("set the bedroom tv volume to 25", None, ["volume_set bedroom_tv"]),
    ("silence the bar speaker", None, ["volume_mute bar_speaker"]),
    ("start the coffee maker", None, ["turn_on coffee_maker"]),
    ("turn the kitchen lights of", None, ["turn_off kitchen_ceiling", "turn_off kitchen_counters"]),
    ("turn of the kitchen lights", None, ["turn_off kitchen_ceiling", "turn_off kitchen_counters"]),
    ("lets have movie night", None, ["turn_on movie_night"]),
    ("put on the movie night scene", None, ["turn_on movie_night"]),
])
def test_plain_speech_acts(said, area, want):
    assert acts(talk(pipe(), said, area=area)[0]) == want


@pytest.mark.parametrize("said, want", [
    ("is the back door locked up", "Back Door Lock is locked."),
    ("check if the garage door is closed", "Garage Door is closed."),
    ("tell me if the coffee maker is running", "Coffee Maker in the Kitchen is off."),
    ("how hot is the bedroom", "Bedroom Temperature is 68.0 °F."),
])
def test_plain_questions_are_answered(said, want):
    assert talk(pipe(), said)[0].message == want


def test_lights_left_on_upstairs():
    assert talk(pipe(), "are there any lights left on upstairs")[0].message.startswith("Yes, 1 light is on")


def test_the_only_device_a_word_could_mean_is_used():
    assert acts(talk(pipe(), "lets get the coffee going")[0]) == ["turn_on coffee_maker"]
    assert acts(talk(pipe(), "turn on the heater")[0]) == ["turn_on office_heater"]
    assert acts(talk(pipe(), "turn off the printer")[0]) == ["turn_off printer_3d"]


def test_a_shared_word_still_asks():
    out = talk(pipe(), "turn on the lamp")[0]
    assert out.kind is Kind.CLARIFY and not out.actions


# ---- the command inside a sentence -------------------------------------------------------------

def test_the_command_inside_a_sentence_is_found_and_never_turned_into_a_mood():
    out = talk(pipe(), "its bedtime turn off everything upstairs")[0]
    assert out.actions and all(a.service == "light.turn_off" for a in out.actions)


def test_a_room_named_before_the_command_is_used():
    assert acts(talk(pipe(), "the office is stuffy, get the fan going")[0]) == ["turn_on office_fan"]


@pytest.mark.parametrize("said", ["how do i turn off the kitchen lights", "i forgot to turn off the kitchen lights",
                                  "when i get home turn on the kitchen lights", "should i turn off the kitchen lights",
                                  "whatever you do don't turn off the kitchen lights"])
def test_questions_and_conditions_are_not_commands(said):
    assert not talk(pipe(), said)[0].actions


# ---- scenes: the household's names win, greetings ask first -------------------------------------

def test_a_greeting_that_names_a_script_asks_first():
    asked, done = talk(pipe(), "good night", "yes", area="bedroom")
    assert asked.kind is Kind.CONFIRM and "Goodnight" in asked.message and not asked.actions
    assert acts(done) == ["turn_on goodnight"]


def test_a_script_named_like_a_built_in_question_is_run():
    p = pipe()
    p.house.entities["script.security_check"] = Entity("script.security_check", "Is The House Secure", None, "off")
    p.update_house(p.house)
    assert acts(talk(p, "is the house secure")[0]) == ["turn_on security_check"]


# ---- the model path ----------------------------------------------------------------------------

def test_underscores_from_a_small_model_are_read_as_spaces():
    llm = ScriptedLLM([{"decision": "act", "clauses": [{"intent": "lock", "target": "front_door"}]}])
    out = talk(pipe(llm_tiers=[llm]), "yo secure that front door for me")[0]
    assert acts(out) == ["lock front_door"]


def test_a_model_cannot_open_what_was_drawn():
    llm = ScriptedLLM([{"decision": "act", "clauses": [{"intent": "open", "target": "curtain"}]}])
    out = talk(pipe(llm_tiers=[llm]), "kindly draw that curtain thing")[0]
    assert "open_cover bedroom_curtain" not in acts(out)


# ---- follow-ups said the way people say them ------------------------------------------------------

@pytest.mark.parametrize("second", ["actually i need them back on", "oh wait turn them back on", "sorry, turn them back on",
                                    "never mind turn them back on", "i want them back on", "actually, back on"])
def test_a_casual_follow_up_uses_what_was_just_done(second):
    off, on = talk(pipe(), "turn off the lights", second, area="office")
    assert sorted(a.entity_id for a in on.actions) == sorted(a.entity_id for a in off.actions)
    assert all(a.service == "light.turn_on" for a in on.actions)


def test_a_follow_up_nobody_understood_never_reaches_a_model():
    p = pipe()
    assert p.home_talk("i need them back on") and "Which device or room" in p.chat_plan("blah them back on")
