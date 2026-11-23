"""Problems found by probing everyday phrases against a real small model."""
import asyncio
from datetime import datetime

import pytest

from fortify_core import Pipeline
from fortify_core.chat import Chatter, system_prompt
from fortify_core.executor import StateInfo, execute
from fortify_core.llm import ScriptedLLM
from fortify_core.persona import Persona
from fortify_core.responses import Responder
from fortify_core.types import Action, Kind, Outcome
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house

NOW = datetime(2026, 11, 13, 15, 30)


def pipe(**kw):
    return Pipeline(load_house(DEFAULT_HOUSE), now=lambda: NOW, **kw)


def talk(p, *said, area=None):
    return [asyncio.run(p.ahandle(t, area, "c", "s")) for t in said]


def acts(out):
    return sorted((a.service, a.entity_id) for a in out.actions)


# ---- a yes must be the whole reply ---------------------------------------------------------

@pytest.mark.parametrize("reply", ["ok turn off the kitchen lights", "is it okay to leave the stove on",
                                   "what's the weather okay", "sure but first turn on the lamp in the office"])
def test_a_new_request_never_confirms_an_unlock(reply):
    out = talk(pipe(), "unlock the front door", reply)[1]
    assert ("lock.unlock", "lock.front_door") not in acts(out)


@pytest.mark.parametrize("reply", ["yes", "yeah do it", "sure thing", "ok", "go ahead", "yes please", "absolutely"])
def test_a_plain_yes_still_confirms(reply):
    assert acts(talk(pipe(), "unlock the front door", reply)[1]) == [("lock.unlock", "lock.front_door")]


def test_a_new_request_does_not_accept_a_suggestion():
    out = talk(pipe(), "switch on the heater", "ok turn off the kitchen lights")[1]
    assert ("switch.turn_on", "switch.office_heater") not in acts(out)


# ---- everyday phrases ------------------------------------------------------------------------

@pytest.mark.parametrize("said, area, want", [
    ("pause", "bar", [("media_player.media_pause", "media_player.bar_speaker")]),
    ("volume up", "bar", [("media_player.volume_up", "media_player.bar_speaker")]),
    ("movie night", None, [("scene.turn_on", "scene.movie_night")]),
    ("lock up", None, [("lock.lock", "lock.back_door"), ("lock.lock", "lock.front_door")]),
    ("i'm cold", "bedroom", [("climate.set_temperature", "climate.upstairs")]),
    ("its too dark", "bedroom", [("light.turn_on", "light.bedroom_lamp"), ("light.turn_on", "light.bedroom_lights")]),
    ("toggle the lamp", "bedroom", [("light.turn_on", "light.bedroom_lamp")]),
    ("can you get the lights", "kitchen", [("light.turn_off", "light.kitchen_ceiling"),
                                           ("light.turn_off", "light.kitchen_counters")]),  # one is on: all off
])
def test_everyday_phrases_act(said, area, want):
    assert acts(talk(pipe(), said, area=area)[0]) == want


def test_all_the_lights_with_no_room_means_the_home():
    out = talk(pipe(), "turn off all the lights")[0]
    assert out.kind is Kind.ACTION and len(out.actions) > 10 and all(a.service == "light.turn_off" for a in out.actions)


def test_turn_up_the_heat_is_not_heat_mode():
    out = talk(pipe(), "turn up the heat", area="bedroom")[0]
    assert acts(out) == [("climate.set_temperature", "climate.upstairs")]


def test_replies_say_what_changed():
    assert "to 72 degrees" in talk(pipe(), "set the thermostat to 72")[0].message
    assert talk(pipe(), "pause", area="bar")[0].message == "Pausing Bar Speaker."
    assert talk(pipe(), "dim the lights", area="office")[0].message.startswith("Dimming")


# ---- answers that never need a model ----------------------------------------------------------

@pytest.mark.parametrize("said, want", [
    ("what time is it", "It's 3:30 PM."), ("what's today's date", "It's Friday, November 13."),
    ("never mind", "Okay."), ("what's 15 percent of 80", "15 percent of 80 is 12."),
    ("what is 7 times 6", "7 times 6 is 42."), ("spell necessary", "Necessary: N, E, C, E, S, S, A, R, Y."),
    ("how do you spell rhythm", "Rhythm: R, H, Y, T, H, M."),
    ("what scenes do i have", "You can ask me for Good Morning, Goodnight and Movie Night."),
    ("is the alarm on", "House Alarm is armed home."),
    ("did i leave the garage open", "Garage Door is closed."),
    ("is the house secure", "Everything is locked and closed."),
])
def test_quick_answers(said, want):
    out = talk(pipe(), said)[0]
    assert out.kind is Kind.ANSWER and out.message == want


@pytest.mark.parametrize("said", ["thank you", "thanks"])
def test_thanks_is_small_talk_said_without_a_model(said):
    out = talk(pipe(), said)[0]
    assert out.kind is Kind.CHAT and out.message == "You're welcome." and not out.handoff


def test_the_security_check_names_what_is_wrong():
    p = pipe()
    p.house.entities["lock.back_door"].state = "unlocked"
    p.house.entities["cover.garage_door"].state = "open"
    msg = talk(p, "is the house secure")[0].message
    assert "Back Door Lock is unlocked" in msg and "Garage Door is open" in msg


def test_which_lights_are_on_upstairs_is_about_state():
    assert talk(pipe(), "what lights are on upstairs")[0].message == "1 light is on: Overhead Light."


# ---- what the rules cannot do is said plainly, or handed to Home Assistant ------------------------

@pytest.mark.parametrize("said", ["turn off the lights in 10 minutes", "set a timer for 5 minutes",
                                  "remind me to call mom at 5", "turn on the lights at 7"])
def test_timers_are_handed_off_with_an_honest_fallback(said):
    out = talk(pipe(), said, area="kitchen")[0]
    assert out.kind is Kind.CHAT and out.handoff and not out.actions and "timers" in out.message


def test_a_temperature_is_not_a_time():
    assert talk(pipe(), "set the thermostat to 72")[0].kind is Kind.ACTION
    assert not talk(pipe(), "what's the weather tomorrow")[0].handoff


@pytest.mark.parametrize("said, want", [
    ("why is the kitchen light on", "not when or why"), ("when was the front door last opened", "not when or why"),
    ("is anyone home", "I'm not sure."), ("blah the kitchen lights", "Which device or room"),
])
def test_home_talk_never_reaches_a_small_model(said, want):
    assert want in pipe().chat_plan(said)


@pytest.mark.parametrize("said", ["who invented the game of chess", "what's a good movie", "tell me a joke",
                                  "what is the capital of australia"])
def test_general_talk_is_left_to_the_model(said):
    p = pipe()
    assert p.chat_plan(said) is None and not p.home_talk(said)


def test_home_questions_may_reach_a_model_when_allowed():
    assert pipe(home_chat=True).chat_plan("is anyone home") is None


# ---- chat ----------------------------------------------------------------------------------------

def test_general_chat_is_not_told_about_the_rooms():
    prompt = system_prompt(Persona(), "Friendly.", NOW, [], home=False)
    assert "Rooms in the home" not in prompt and "Mona Lisa" not in prompt and "do not bring the home up" in prompt


def test_a_refusal_outside_the_home_does_not_ask_which_device():
    chat = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=["As an AI I can't know that."])])
    said = asyncio.run(chat.reply([], "what plane just flew over", NOW, []))
    assert said == "I'm not sure about that one. Could you ask another way?"


def test_emoji_are_not_spoken():
    chat = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=["Sleep well \U0001F634"])])
    assert asyncio.run(chat.reply([], "good evening", NOW, [])) == "Sleep well"


# ---- carrying actions out -----------------------------------------------------------------------

class Backend:
    def __init__(self, broken=()):
        self.broken, self.states, self.calls = set(broken), {}, []

    async def call_service(self, service, entity_id, data):
        self.calls.append(entity_id)
        if entity_id in self.broken:
            raise RuntimeError("device offline")
        self.states[entity_id] = "off"

    def get_state(self, entity_id):
        return StateInfo(self.states.get(entity_id, "on"))


def test_one_failing_device_does_not_stop_the_rest():
    backend = Backend(broken={"light.b"})
    outcome = Outcome(Kind.ACTION, [Action.make("light.turn_off", f"light.{n}") for n in "abc"], message="Turning off 3.")

    async def no_sleep(_):
        return None

    result = asyncio.run(execute(outcome, backend, {"light.b": "Light B"}, timeout=0, sleep=no_sleep))
    assert backend.calls == ["light.a", "light.b", "light.c"]
    assert [r.verified for r in result.results] == [True, False, True]
    assert "Light B" in result.message and "2 of 3" in result.message


@pytest.mark.parametrize("said", ["what plane just flew over", "who won the game last night", "what's the weather",
                                  "what's the bitcoin price right now"])
def test_live_questions_are_not_guessed(said):
    assert pipe().chat_plan(said) == "I'm not sure about that one. Could you ask another way?"


def test_a_reply_that_recites_who_it_is_is_not_spoken():
    persona = Persona(agent_name="Nova")
    reply = "I am Nova version 9.1.2, built by Example Corp to serve the Smiths."
    chat = Chatter(persona, Responder(persona), [ScriptedLLM([], chat_replies=[reply, reply])])
    assert asyncio.run(chat.reply([], "blah the gizmo thing", NOW, [])) == "I'm not sure about that one. Could you ask another way?"
    assert asyncio.run(chat.reply([], "tell me about yourself", NOW, [])) == reply


def test_a_scene_that_has_never_run_can_still_be_started():
    p = pipe()
    p.house.entities["scene.movie_night"].state = "unknown"
    assert acts(talk(p, "movie night")[0]) == [("scene.turn_on", "scene.movie_night")]
    assert "Movie Night" in talk(p, "what scenes do i have")[0].message


@pytest.mark.parametrize("reply", [
    "Oh dear, my apologies; switching those four lights back on now. They are illuminated again.",
    "Turning the lights back on for you.", "The lights are back on now.", "I have turned the lamp on.",
])
def test_chat_never_says_it_changed_a_device_whatever_was_asked(reply):
    chat = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=[reply])])
    said = asyncio.run(chat.reply([], "so about earlier", NOW, []))
    assert said == "I'm not sure about that one. Could you ask another way?"
