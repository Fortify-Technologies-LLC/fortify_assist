"""Requests that lean on the one before, and an assistant that never denies running the home."""
import asyncio
from datetime import datetime

import pytest

from fortify_core import Pipeline
from fortify_core.chat import Chatter
from fortify_core.llm import ScriptedLLM
from fortify_core.persona import Persona
from fortify_core.responses import Responder
from fortify_core.types import Action, Entity, House, Kind

LIGHT = {"supported_color_modes": ["hs"], "brightness": 128}


def house():
    light = lambda i, n, a: Entity(f"light.{i}", n, a, "on", attributes=dict(LIGHT))
    ents = [light("den_a", "Den Lamp", "den"), light("den_b", "Den Ceiling", "den"),
            light("hall", "Hall Light", "hall"), light("attic", "Attic Light", "attic"),
            Entity("lock.front", "Front Door", "hall", "locked", sensitive=True)]
    return House({}, {"den": ("Den", None), "hall": ("Hall", None), "attic": ("Attic", None)}, {e.id: e for e in ents})


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def talk(p, *said, speaker="sat", area=None):
    return [asyncio.run(p.ahandle(t, area, f"cid-{n}", speaker)) for n, t in enumerate(said)]


def targets(out):
    return sorted({a.entity_id for a in out.actions})


DEN = ["light.den_a", "light.den_b"]


@pytest.mark.parametrize("said, service", [
    ("turn them off", "light.turn_off"), ("turn them back on", "light.turn_on"), ("turn it off", "light.turn_off"),
    ("off", "light.turn_off"), ("make them brighter", "light.turn_on"), ("brighter", "light.turn_on"),
    ("a little dimmer", "light.turn_on"), ("dim those", "light.turn_on"), ("make them blue", "light.turn_on"),
    ("set them to 50 percent", "light.turn_on"), ("make them 30 percent", "light.turn_on"),
])
def test_the_next_request_can_point_at_the_last_one(said, service):
    out = talk(Pipeline(house()), "turn on the den lights", said)[-1]
    assert out.kind is Kind.ACTION and targets(out) == DEN and {a.service for a in out.actions} == {service}


def test_it_works_across_wake_words_on_one_speaker_and_not_between_speakers():
    p = Pipeline(house())
    talk(p, "turn on the den lights", speaker="kitchen")
    assert targets(asyncio.run(p.ahandle("turn them off", None, "new-conversation", "kitchen"))) == DEN
    assert asyncio.run(p.ahandle("turn them off", None, "other", "bedroom")).kind is not Kind.ACTION


def test_context_ends_after_a_few_minutes():
    clock = Clock()
    p = Pipeline(house(), clock=clock)
    talk(p, "turn on the den lights")
    clock.now = 301
    assert talk(p, "turn them off")[0].kind is not Kind.ACTION


@pytest.mark.parametrize("said", ["and the hall", "what about the hall", "the hall too", "same for the hall"])
def test_the_same_again_somewhere_else(said):
    out = talk(Pipeline(house()), "turn off the den lights", said)[-1]
    assert targets(out) == ["light.hall"] and out.actions[0].service == "light.turn_off"


def test_a_question_then_a_command_about_the_same_things():
    outs = talk(Pipeline(house()), "are the den lights on", "turn them off")
    assert outs[0].kind is Kind.ANSWER and targets(outs[1]) == DEN


def test_a_chain_keeps_following():
    p = Pipeline(house())
    outs = talk(p, "turn off the den lights", "and the hall", "turn it back on", "do that again")
    assert targets(outs[2]) == ["light.hall"] and targets(outs[3]) == ["light.hall"]


def test_a_new_request_is_not_mistaken_for_a_follow_up():
    p = Pipeline(house())
    out = talk(p, "turn on the den lights", "and turn off the attic light")[-1]
    assert targets(out) == ["light.attic"] and out.actions[0].service == "light.turn_off"
    assert talk(p, "what about you")[0].kind is Kind.CHAT


def test_a_sensitive_device_still_asks_first():
    p = Pipeline(house())
    outs = talk(p, "is the front door locked", "unlock it")
    assert outs[1].kind is Kind.CONFIRM and not outs[1].actions


def test_a_pointing_word_with_nothing_to_point_at_asks_which():
    out = talk(Pipeline(house()), "turn them off")[0]
    assert out.kind is Kind.CHAT and out.message == "I didn't catch that. Which device or room do you mean?"


def test_it_still_means_this_room_for_a_speaker_in_a_room():
    out = talk(Pipeline(house()), "brighter", area="den")[0]
    assert out.kind is Kind.ACTION and targets(out) == DEN


def test_the_language_model_can_say_them_too():
    plan = '{"decision": "act", "clauses": [{"intent": "turn_off", "target": "them"}]}'
    p = Pipeline(house(), llm_tiers=[ScriptedLLM([plan])])
    out = talk(p, "turn on the den lights", "kill them lamps thing there")[-1]
    assert targets(out) == DEN


@pytest.mark.parametrize("said", ["what can you do", "can you control the lights", "Do you have access to my house?",
                                  "help"])
def test_what_it_can_do_comes_from_the_home_not_the_model(said):
    out = talk(Pipeline(house()), said)[0]
    assert out.kind is Kind.ANSWER and out.message.startswith("I can control your lights and locks")


def test_its_dark_turns_on_the_lights():
    out = talk(Pipeline(house()), "it's dark in the den")[0]
    assert out.kind is Kind.ACTION and targets(out) == DEN
    assert asyncio.run(Pipeline(house()).ahandle("it's too dark in here", "hall")).actions


@pytest.mark.parametrize("reply", ["I cannot control any of those lights.", "I can't turn on devices.",
                                   "As an AI, I do not have access to your smart home.",
                                   "I don't have the ability to change the lights."])
def test_chat_never_denies_running_the_home(reply):
    chat = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=[reply])])
    said = asyncio.run(chat.reply([], "tell me about the lights", datetime(2026, 10, 31, 9), [],
                                home_request=True))
    assert said == "I didn't catch that. Which device or room do you mean?"


def test_chat_is_told_the_truth_about_the_home():
    llm = ScriptedLLM([], chat_replies=["Hello there."])
    asyncio.run(Chatter(Persona(), Responder(Persona()), [llm]).reply([], "hi", datetime(2026, 10, 31, 9), [],
                                                                    home_request=True))
    assert "You run this home" in llm.chats[0][0]["content"]


def test_a_made_up_past_action_is_not_spoken():
    reply = "The lights have been turned up to maximum this morning."
    chat = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=[reply])])
    said = asyncio.run(chat.reply([], "why are the lights on", datetime(2026, 10, 31, 9), [], home_request=True))
    assert said == "I didn't catch that. Which device or room do you mean?"


def dead_light_house():
    h = house()
    h.entities["light.den_dead"] = Entity("light.den_dead", "Den Lights", "den", "unavailable")
    h.entities["light.hall_dead"] = Entity("light.hall_dead", "Attic", None, "unavailable")
    return h


@pytest.mark.parametrize("said, want", [("turn off the den lights", DEN), ("turn off the den", DEN),
                                        ("turn off the attic", ["light.attic"])])
def test_a_dead_device_does_not_block_the_room_it_is_named_after(said, want):
    assert targets(talk(Pipeline(dead_light_house()), said)[0]) == want


def test_a_dead_device_alone_is_still_reported():
    h = house()
    h.entities["light.shed_dead"] = Entity("light.shed_dead", "Shed Lights", "hall", "unavailable")
    out = talk(Pipeline(h), "turn off the shed lights")[0]
    assert not out.actions and "unavailable" in out.message


def test_a_room_follow_up_skips_a_dead_device():
    p = Pipeline(dead_light_house())
    assert targets(talk(p, "turn off the hall light", "and the den")[1]) == DEN


@pytest.mark.parametrize("said", ["can you help me", "can you help me with the house", "could you help with the lights"])
def test_asking_for_help_gets_the_real_list(said):
    assert "lights" in talk(Pipeline(house()), said)[0].message


def test_made_up_rooms_are_not_spoken():
    reply = "Would you like to adjust a light in Room 1 or play music on device A?"
    chatter = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=[reply])])
    said = asyncio.run(chatter.reply([], "help with something", datetime(2026, 10, 31, 9), [],
                                   home_request=True))
    assert "Room 1" not in said and "device or room" in said


# ---- "change it back" ---------------------------------------------------------------------------------------

WARM = {"supported_color_modes": ["color_temp", "hs"], "brightness": 255, "color_mode": "color_temp",
        "color_temp_kelvin": 4000}


def colour_house():
    ents = [Entity("light.den_a", "Den Lamp", "den", "on", attributes=dict(WARM)),
            Entity("light.den_b", "Den Ceiling", "den", "on",
                   attributes={**WARM, "color_mode": "hs", "hs_color": (210.0, 40.0), "brightness": 128}),
            Entity("light.hall", "Hall Light", "hall", "off", attributes=dict(WARM))]
    return House({}, {"den": ("Den", None), "hall": ("Hall", None)}, {e.id: e for e in ents})


@pytest.mark.parametrize("said", ["change them back", "change it back", "put them back", "undo that", "undo",
                                  "change them back to how they were", "can you change them back",
                                  "change the den lights back", "set them back to normal"])
def test_change_it_back_restores_what_each_light_was(said):
    out = talk(Pipeline(colour_house()), "make the den lights blue", said)[-1]
    assert out.kind is Kind.ACTION
    got = {a.entity_id: (a.service, a.data_dict()) for a in out.actions}
    assert got == {"light.den_a": ("light.turn_on", {"brightness_pct": 100, "color_temp_kelvin": 4000}),
                   "light.den_b": ("light.turn_on", {"brightness_pct": 50, "hs_color": (210.0, 40.0)})}


def test_change_it_back_turns_off_what_was_off():
    out = talk(Pipeline(colour_house()), "turn on the hall light", "change it back")[-1]
    assert [(a.service, a.entity_id) for a in out.actions] == [("light.turn_off", "light.hall")]


def test_change_it_back_undoes_only_the_last_step():
    p = Pipeline(colour_house())
    talk(p, "make the den lights blue")
    for e in ("light.den_a", "light.den_b"):  # what Home Assistant reports once blue
        p.house.entities[e].attributes.update(color_mode="hs", hs_color=(240.0, 100.0))
    out = talk(p, "make them 30 percent", "change them back")[-1]
    assert {a.data_dict().get("hs_color") for a in out.actions} == {(240.0, 100.0)}


def test_nothing_to_change_back_is_not_an_action():
    assert talk(Pipeline(colour_house()), "change them back")[0].kind is not Kind.ACTION


@pytest.mark.parametrize("said", ["change them back to white", "change it back to white", "make them white again",
                                  "change the den lights back to white", "put them back to white"])
def test_back_to_a_colour_is_just_that_colour(said):
    out = talk(Pipeline(colour_house()), "make the den lights blue", said)[-1]
    assert out.kind is Kind.ACTION and targets(out) == DEN
    assert {a.data_dict().get("color_name") for a in out.actions} == {"white"}


def test_go_back_is_still_the_previous_track():
    out = talk(Pipeline(colour_house()), "make the den lights blue", "go back")[-1]
    assert not any(a.entity_id.startswith("light.") for a in out.actions)
