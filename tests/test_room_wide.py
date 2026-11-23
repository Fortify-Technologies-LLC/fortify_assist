"""Several lights at once without naming them, everyday phrasings, exceptions and 'don't'."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.llm import ScriptedLLM
from fortify_core.types import Entity, Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house

WORKSHOP = ["light.workshop_a", "light.workshop_b"]
BASEMENT = ["light.bar_strip", "light.workshop_a", "light.workshop_b"]


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


def run(p, text, ctx=None, cid=None):
    return asyncio.run(p.ahandle(text, ctx, cid))


def ids(out):
    return sorted(a.entity_id for a in out.actions)


@pytest.mark.parametrize("text,expected,service", [
    ("kill everything in the basement", BASEMENT, "light.turn_off"),
    ("everything off in the bar", ["light.bar_strip"], "light.turn_off"),
    ("lights out downstairs", BASEMENT, "light.turn_off"),
    ("turn on the workshop", WORKSHOP, "light.turn_on"),
    ("turn on the workshop and the bar", BASEMENT, "light.turn_on"),
    ("turn the bar and workshop lights off", BASEMENT, "light.turn_off"),
    ("light up the basement", BASEMENT, "light.turn_on"),
    ("turn the lights off in the kitchen", ["light.kitchen_ceiling", "light.kitchen_counters"], "light.turn_off"),
    ("turn everything off in the garage", ["light.garage_overhead"], "light.turn_off"),
    ("shut off both workshop lights", WORKSHOP, "light.turn_off"),
])
def test_rooms_and_floors_mean_their_lights(house, text, expected, service):
    out = Pipeline(house).handle(text)
    assert out.kind is Kind.ACTION and ids(out) == sorted(expected), out
    assert {a.service for a in out.actions} == {service}


def test_everything_never_touches_doors_or_locks(house):
    out = Pipeline(house).handle("turn everything off in the garage")
    assert not any(a.entity_id.startswith(("cover.", "lock.")) for a in out.actions)


def test_a_device_named_like_its_room_still_wins(house):
    house.entities["light.bar_group"] = Entity("light.bar_group", "Bar", "bar", "on")
    assert ids(Pipeline(house).handle("turn off the bar")) == ["light.bar_group"]


def test_a_device_in_a_room_is_not_the_whole_room(house):
    assert Pipeline(house).handle("turn on the workshop light").kind is Kind.CLARIFY  # A or B?
    assert ids(Pipeline(house).handle("turn on the office fan")) == ["fan.office_fan"]


def test_lights_that_cannot_dim_are_skipped_and_named(house):
    house.entities["light.workshop_b"].attributes = {"supported_color_modes": ["onoff"]}
    out = Pipeline(house).handle("dim the workshop to 30 percent")
    assert ids(out) == ["light.workshop_a"] and "Workshop Light B" in out.message


def test_unavailable_lights_do_not_block_the_room(house):
    house.entities["light.bar_dead"] = Entity("light.bar_dead", "Bar Lights", "bar", "unavailable")
    assert ids(Pipeline(house).handle("turn on the workshop and the bar")) == BASEMENT


def test_group_helpers_are_not_switched_twice(house):
    house.entities["light.ws_group"] = Entity("light.ws_group", "Workshop Overhead", "workshop", "on",
                                              attributes={"entity_id": WORKSHOP})
    assert ids(Pipeline(house).handle("turn off the workshop")) == WORKSHOP


def test_many_devices_are_summarised(house):
    out = Pipeline(house).handle("switch everything off upstairs")
    assert len(out.actions) == 5 and "5 lights in 3 rooms" in out.message


@pytest.mark.parametrize("text,expected", [
    ("turn off every light downstairs except the bar", WORKSHOP),
    ("turn off the workshop lights but leave light a on", ["light.workshop_b"]),
    ("turn on the basement apart from the bar", WORKSHOP),
])
def test_exceptions_are_honoured(house, text, expected):
    assert ids(Pipeline(house).handle(text)) == expected


@pytest.mark.parametrize("text", [
    "turn off the basement except the flux capacitor",  # the exception names nothing: never ignore it
    "turn off the workshop except the kitchen",  # the exception is outside the request
])
def test_an_exception_that_does_not_apply_stops_everything(house, text):
    out = Pipeline(house).handle(text)
    assert not out.actions and out.kind is Kind.REFUSE


@pytest.mark.parametrize("text", ["don't turn off the bar strip", "never turn the workshop lights off",
                                  "turn off every light downstairs but not the bar please don't"])
def test_the_model_never_decides_a_negative_request(house, text):
    llm = ScriptedLLM([{"decision": "act", "clauses": [{"intent": "turn_off", "target": "bar strip"}]}] * 3)
    out = run(Pipeline(house, llm_tiers=[llm]), text)
    assert not (out.kind is Kind.ACTION and "light.bar_strip" in ids(out))


@pytest.mark.parametrize("text,expected", [
    ("i can't see anything in the workshop", WORKSHOP),
    ("i need the garage lights", ["light.garage_overhead"]),
    ("the lamp in the bedroom turn it off", ["light.bedroom_lamp"]),
    ("could you maybe turn off the bedroom lamp", ["light.bedroom_lamp"]),
    ("get the floor lamp going", ["light.living_room_floor"]),
    ("put the kitchen speaker on pause", ["media_player.kitchen_speaker"]),
    ("fire up the coffee maker", ["switch.coffee_maker"]),
    ("shut the blinds", ["cover.living_room_blinds"]),
    ("close up the garage", ["cover.garage_door"]),
    ("it's freezing in the bedroom", ["climate.upstairs"]),
])
def test_everyday_phrasings(house, text, expected):
    assert ids(Pipeline(house).handle(text)) == expected


def test_i_need_something_that_is_not_a_device_is_chat(house):
    assert Pipeline(house).handle("i need a vacation").kind is Kind.CHAT


def test_too_dark_without_a_room_asks(house):
    assert Pipeline(house).handle("it's too dark in here").kind is Kind.CLARIFY


def test_how_warm_reads_the_room_sensor(house):
    out = Pipeline(house).handle("how warm is it in the living room")
    assert out.entities == ["sensor.living_room_temperature"]


def test_floor_levels_and_aliases_from_home_assistant(house):
    house.floor_info = {"main": (0, ["ground floor"]), "upstairs": (1, []), "basement": (-1, [])}
    p = Pipeline(house)
    assert ids(p.handle("lights out downstairs")) == BASEMENT
    main = ["light.garage_overhead", "light.kitchen_ceiling", "light.kitchen_counters", "light.living_room_ceiling",
            "light.living_room_floor"]
    assert ids(p.handle("turn off the lights on the ground floor")) == main
