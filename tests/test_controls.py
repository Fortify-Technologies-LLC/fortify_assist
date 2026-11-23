import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core import controls
from fortify_core.executor import StateInfo, expectation
from fortify_core.llm import ScriptedLLM
from fortify_core.types import Action, Entity, Kind
from fortify_core.validator import validate
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


@pytest.fixture()
def pipe(house):
    return Pipeline(house)


def one(out):
    assert out.kind is Kind.ACTION and len(out.actions) == 1, out
    return out.actions[0].service, out.actions[0].data_dict()


# ---- colours and white tones ------------------------------------------------------------

@pytest.mark.parametrize("text,color", [
    ("make the kitchen counter lights blue", "blue"),
    ("set the kitchen counter lights to red", "red"),
    ("change the kitchen counter lights to purple", "purple"),
    ("turn the kitchen counter lights green", "green"),
    ("set the kitchen counter lights color to pink", "pink"),
])
def test_colours(pipe, text, color):
    assert one(pipe.handle(text)) == ("light.turn_on", {"color_name": color})


@pytest.mark.parametrize("text,kelvin", [
    ("set the bedroom lights to warm white", 2700), ("make the bedroom lights cool white", 5000),
    ("set the bedroom lights to daylight", 6500), ("set the bedroom lights to soft white", 2700),
])
def test_white_tones(pipe, text, kelvin):
    assert one(pipe.handle(text)) == ("light.turn_on", {"color_temp_kelvin": kelvin})


def test_a_light_that_cannot_do_it_says_so_instead_of_pretending(pipe):
    out = pipe.handle("make the deck lights blue")  # on/off only
    assert out.kind is Kind.REFUSE and "Deck Lights doesn't support color" in out.message
    assert pipe.handle("make the bedroom lights blue").kind is Kind.REFUSE  # tunable white only
    assert pipe.handle("set the deck lights to warm white").kind is Kind.REFUSE
    assert pipe.handle("dim the deck lights to 30 percent").kind is Kind.REFUSE  # cannot dim


def test_unknown_capabilities_are_given_the_benefit_of_the_doubt(house):
    house.entities["light.bar_strip"].attributes = {}
    assert one(Pipeline(house).handle("make the bar strip blue"))[1] == {"color_name": "blue"}


def test_groups_skip_members_that_cannot_but_still_act_for_the_rest(house):
    house.entities["light.workshop_b"].attributes = {"supported_color_modes": ["onoff"]}
    out = Pipeline(house).handle("make the workshop lights red")
    assert [a.entity_id for a in out.actions] == ["light.workshop_a"]


def test_white_on_a_tunable_white_lamp_means_a_white_tone(pipe):
    assert one(pipe.handle("make the bedroom lights white"))[1] == {"color_temp_kelvin": 4000}


# ---- relative changes --------------------------------------------------------------------

@pytest.mark.parametrize("text,step", [
    ("make the bar strip brighter", 20), ("make the bar strip dimmer", -20),
    ("make the bar strip a little brighter", 10), ("make the bar strip a lot dimmer", -40),
    ("brighten the bar strip", 20), ("dim the bar strip", -20), ("dim the bar strip by 30 percent", -30),
    ("turn up the bar strip", 20), ("turn down the bar strip", -20),
    ("increase the bar strip by 15 percent", 15), ("lower the bar strip", -20),
])
def test_brightness_steps(pipe, text, step):
    assert one(pipe.handle(text)) == ("light.turn_on", {"brightness_step_pct": step})


def test_make_it_brighter_means_the_room_the_speaker_is_in(pipe):
    out = pipe.handle("make it brighter", context_area="workshop")
    assert sorted(a.entity_id for a in out.actions) == ["light.workshop_a", "light.workshop_b"]
    assert pipe.handle("make it brighter").kind is Kind.CLARIFY  # which room?


def test_warmer_and_cooler_depend_on_the_device(house):
    p = Pipeline(house)
    assert one(p.handle("make it warmer", context_area="bedroom")) == ("climate.set_temperature", {"temperature": 72})
    assert one(p.handle("make it cooler", context_area="bedroom")) == ("climate.set_temperature", {"temperature": 68})
    house.entities["light.bar_strip"].attributes = {"supported_color_modes": ["color_temp"], "color_temp_kelvin": 4000,
                                                    "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500}
    assert one(p.handle("make the bar strip warmer"))[1] == {"color_temp_kelvin": 3400}
    house.entities["light.bar_strip"].attributes["color_temp_kelvin"] = 2100
    assert one(p.handle("make the bar strip warmer"))[1] == {"color_temp_kelvin": 2000}  # clamped to the lamp's range


def test_a_thermostat_with_no_known_setpoint_is_not_guessed(pipe):
    out = pipe.handle("make it warmer", context_area="living_room")  # main floor thermostat reports no setting
    assert out.kind is Kind.REFUSE and "temperature setting" in out.message


# ---- levels, covers, fans, climate, media -----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("set the office fan to high", ("fan.set_percentage", {"percentage": 100})),
    ("set the bedroom fan to medium", ("fan.set_percentage", {"percentage": 66})),
    ("set the bedroom fan to 40 percent", ("fan.set_percentage", {"percentage": 40})),
    ("open the living room blinds halfway", ("cover.set_cover_position", {"position": 50})),
    ("open the living room blinds to 30 percent", ("cover.set_cover_position", {"position": 30})),
    ("set the bar strip to 25", ("light.turn_on", {"brightness_pct": 25})),
    ("set the bar strip to half", ("light.turn_on", {"brightness_pct": 50})),
    ("set the bar strip to max", ("light.turn_on", {"brightness_pct": 100})),
    ("set the thermostat to cool", ("climate.set_hvac_mode", {"hvac_mode": "cool"})),
    ("make the thermostat 72", ("climate.set_temperature", {"temperature": 72})),
    ("set the thermostat to 70 degrees", ("climate.set_temperature", {"temperature": 70})),
    ("set the bar speaker to 30", ("media_player.volume_set", {"volume_level": 0.3})),
    ("turn up the bar speaker", ("media_player.volume_up", {})),
    ("turn the kitchen speaker down", ("media_player.volume_down", {})),
    ("mute the bar speaker", ("media_player.volume_mute", {"is_volume_muted": True})),
    ("unmute the bar speaker", ("media_player.volume_mute", {"is_volume_muted": False})),
    ("raise the blinds", ("cover.open_cover", {})),
    ("lower the curtain", ("cover.close_cover", {})),
])
def test_levels_and_modes(pipe, text, expected):
    assert one(pipe.handle(text)) == expected


@pytest.mark.parametrize("text,colour,level", [
    ("make the bar strip white and set brightness to 100 percent", "white", 100),
    ("make the bar strip red and set the brightness to 100", "red", 100),
    ("turn the bar strip blue and make it 40 percent", "blue", 40),
    ("turn the bar strip green and make them 30 percent", "green", 30),
])
def test_a_second_clause_with_no_device_means_the_device_before_it(pipe, text, colour, level):
    out = pipe.handle(text)
    assert out.kind is Kind.ACTION, out
    assert [(a.service, a.entity_id, a.data_dict()) for a in out.actions] == [
        ("light.turn_on", "light.bar_strip", {"color_name": colour}),
        ("light.turn_on", "light.bar_strip", {"brightness_pct": level})]


def test_a_level_word_means_different_amounts_for_different_devices(pipe):
    assert one(pipe.handle("set the bedroom fan to low"))[1] == {"percentage": 33}
    assert one(pipe.handle("set the bar strip to low"))[1] == {"brightness_pct": 20}
    assert one(pipe.handle("set the bar speaker to low"))[1] == {"volume_level": 0.2}


def test_an_unsupported_thermostat_mode_is_refused(house):
    house.entities["climate.upstairs"].attributes["hvac_modes"] = ["heat", "off"]
    out = Pipeline(house).handle("set the thermostat to cool")
    assert out.kind is Kind.REFUSE and "cool mode" in out.message


def test_vacuums(house):
    house.entities["vacuum.dusty"] = Entity("vacuum.dusty", "Dusty", "living_room", "docked")
    p = Pipeline(house)
    assert one(p.handle("send dusty home"))[0] == "vacuum.return_to_base"
    assert one(p.handle("start dusty"))[0] == "vacuum.start"
    assert one(p.handle("stop dusty"))[0] == "vacuum.stop"
    assert one(p.handle("pause dusty"))[0] == "vacuum.pause"


# ---- safety ---------------------------------------------------------------------------------

def test_these_phrases_are_not_commands(pipe):
    for text in ("make me a sandwich", "make it snappy", "set me free", "what is the best color"):
        assert not pipe.handle(text).actions, text


def test_sensitive_devices_never_run_from_a_relative_or_level_command(house):
    house.entities["cover.garage_door"].state = "closed"
    p = Pipeline(house)
    out = p.handle("raise the garage door")
    assert out.kind is Kind.CONFIRM and not out.actions  # opening a garage door still asks first


def test_validator_checks_the_new_settings(house):
    ok = Action.make("light.turn_on", "light.bar_strip", {"color_name": "blue"})
    assert validate(ok, house) is None
    assert "not a valid" in validate(Action.make("light.turn_on", "light.bar_strip", {"color_name": "plaid"}), house)
    assert "not a valid" in validate(Action.make("climate.set_hvac_mode", "climate.upstairs", {"hvac_mode": "explode"}), house)
    assert validate(Action.make("light.turn_on", "light.bar_strip", {"brightness_step_pct": 500}), house)
    assert validate(Action.make("light.turn_on", "light.bar_strip", {"color_temp_kelvin": 3}), house)
    assert validate(Action.make("media_player.volume_mute", "media_player.bar_speaker", {"is_volume_muted": "yes"}), house)
    assert validate(Action.make("fan.set_percentage", "fan.office_fan", {"percentage": 100}), house) is None
    assert validate(Action.make("vacuum.start", "light.bar_strip"), house)  # wrong domain


def test_the_executor_knows_how_to_check_the_new_actions():
    check = expectation(Action.make("fan.set_percentage", "fan.a", {"percentage": 66}))
    assert check(StateInfo("on", {"percentage": 66})) and not check(StateInfo("on", {"percentage": 20}))
    check = expectation(Action.make("cover.set_cover_position", "cover.a", {"position": 50}))
    assert check(StateInfo("open", {"current_position": 52})) and not check(StateInfo("open", {"current_position": 100}))
    assert expectation(Action.make("climate.set_hvac_mode", "climate.a", {"hvac_mode": "cool"}))(StateInfo("cool"))
    assert expectation(Action.make("light.turn_on", "light.a", {"color_name": "red"}))(StateInfo("on"))
    assert not expectation(Action.make("light.turn_on", "light.a", {"color_name": "red"}))(StateInfo("off"))
    assert expectation(Action.make("media_player.volume_up", "media_player.a")) is None  # cannot be verified


def test_controls_module_is_pure(house):
    e = house.entities["light.kitchen_counters"]
    assert controls.set_option(e, "blue").actions[0].data_dict() == {"color_name": "blue"}
    assert controls.set_option(e, "nonsense").problem == "nonsense"


# ---- the language model path ---------------------------------------------------------------------

def run(pipe, text, ctx=None):
    return asyncio.run(pipe.ahandle(text, ctx))


def act(**clause):
    return {"decision": "act", "clauses": [clause]}


def test_the_model_can_rephrase_a_colour_request(house):
    llm = ScriptedLLM([act(intent="set_option", target="kitchen counter lights", option="purple")])
    out = run(Pipeline(house, llm_tiers=[llm]), "give the kitchen counter lights a purple glow")
    assert one(out) == ("light.turn_on", {"color_name": "purple"})


def test_the_model_cannot_invent_a_colour_or_level(house):
    for option in ("plaid", "orange"):  # not a colour / not something the user said
        llm = ScriptedLLM([act(intent="set_option", target="kitchen counter lights", option=option)])
        out = run(Pipeline(house, llm_tiers=[llm]), "give the kitchen counter lights a purple glow")
        assert not out.actions, option


def test_the_model_cannot_flip_a_direction(house):
    llm = ScriptedLLM([act(intent="adjust", target="bar strip", option="brighter")])
    assert not run(Pipeline(house, llm_tiers=[llm]), "bring the bar strip down a bit").actions
    llm = ScriptedLLM([act(intent="adjust", target="bar strip", option="down")])
    assert one(run(Pipeline(house, llm_tiers=[llm]), "bring the bar strip down a bit"))[1] == {"brightness_step_pct": -20}


def test_the_model_can_target_this_room_with_it(house):
    llm = ScriptedLLM([act(intent="adjust", target="it", option="up")])
    out = run(Pipeline(house, llm_tiers=[llm]), "a touch higher in here, it is dim", "workshop")
    assert sorted(a.entity_id for a in out.actions) == ["light.workshop_a", "light.workshop_b"]


def test_invented_adjust_values_are_rejected(house):
    llm = ScriptedLLM([act(intent="adjust", target="bar strip", option="up", value=60)])
    assert not run(Pipeline(house, llm_tiers=[llm]), "bring the bar strip up a bit").actions


@pytest.mark.parametrize("text,expected", [
    ("bar strip to 75 percent", ("light.turn_on", {"brightness_pct": 75})),
    ("kitchen counter lights blue", ("light.turn_on", {"color_name": "blue"})),
    ("bedroom lights to warm white", ("light.turn_on", {"color_temp_kelvin": 2700})),
])
def test_commands_without_a_leading_verb(pipe, text, expected):
    assert one(pipe.handle(text)) == expected


def test_verbless_phrases_that_are_not_about_a_device_stay_chat(pipe):
    for text in ("the sky is blue", "my favourite colour is green", "battery at 20 percent", "is the lamp blue"):
        out = pipe.handle(text)
        assert not out.actions, text
