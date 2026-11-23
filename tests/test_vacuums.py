"""Robot vacuums: rooms, floors, the one that works, and the words people use."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.types import Action, Entity, Kind
from fortify_core.validator import validate
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house

ROOMS_OK = 16384 | 8192 | 4096 | 16 | 8 | 4  # can be sent to chosen rooms
WHOLE_ONLY = 8192 | 4096 | 16 | 8 | 4  # starts, stops and docks, but only cleans everything


@pytest.fixture()
def house():
    h = load_house(DEFAULT_HOUSE)
    h.entities["vacuum.dusty"] = Entity("vacuum.dusty", "Dusty", "living_room", "docked", aliases=["the vacuum"],
                                        attributes={"supported_features": ROOMS_OK})
    h.entities["vacuum.sweepy"] = Entity("vacuum.sweepy", "Sweepy", "bar", "docked", aliases=["roomba", "vacuum"],
                                         attributes={"supported_features": WHOLE_ONLY})
    return h


def run(h, text, room=None):
    return asyncio.run(Pipeline(h).ahandle(text, room, "c", "sat"))


def acts(out):
    return [(a.service, a.entity_id, a.data_dict()) for a in out.actions]


@pytest.mark.parametrize("said, rooms", [
    ("vacuum the kitchen", ("kitchen",)), ("clean the living room", ("living_room",)),
    ("have dusty clean the kitchen", ("kitchen",)), ("mop the kitchen", ("kitchen",)),
    ("vacuum the kitchen and the living room", ("kitchen", "living_room")), ("clean up the kitchen", ("kitchen",)),
])
def test_a_room_is_cleaned_by_the_robot_that_can_do_rooms(house, said, rooms):
    out = run(house, said)
    assert acts(out) == [("vacuum.clean_area", "vacuum.dusty", {"cleaning_area_id": rooms})]
    assert not validate(out.actions[0], house)


def test_vacuum_here_means_the_speakers_room(house):
    assert acts(run(house, "vacuum in here", "kitchen")) == [
        ("vacuum.clean_area", "vacuum.dusty", {"cleaning_area_id": ("kitchen",)})]


def test_a_robot_is_never_sent_to_another_floor(house):
    out = run(house, "vacuum the bar")  # Dusty lives on the main floor; Sweepy is the basement's robot
    assert acts(out) == [("vacuum.start", "vacuum.sweepy", {})] and "whole floor" in out.message


def test_the_floors_robot_being_offline_is_said_not_swapped_for_another(house):
    house.entities["vacuum.sweepy"].state = "unavailable"
    out = run(house, "vacuum the workshop")
    assert not out.actions and "Sweepy" in out.message and "unavailable" in out.message


def test_not_a_room_is_not_a_cleaning_job(house):
    out = run(house, "clean the coffee maker")
    assert not any(a.service.startswith("vacuum.") for a in out.actions)


@pytest.mark.parametrize("said, service", [("turn on the roomba", "vacuum.start"),
                                           ("turn off the roomba", "vacuum.return_to_base")])
def test_turn_on_and_off_a_vacuum(house, said, service):
    assert [(a.service, a.entity_id) for a in run(house, said).actions] == [(service, "vacuum.sweepy")]


def test_the_working_one_of_two_is_used(house):
    house.entities["vacuum.sweepy"].state = "unavailable"
    house.entities["vacuum.sweepy"].aliases.append("the vacuum")
    assert [(a.service, a.entity_id) for a in run(house, "start the vacuum").actions] == [
        ("vacuum.start", "vacuum.dusty")]


def test_both_working_still_asks(house):
    house.entities["vacuum.sweepy"].aliases.append("the vacuum")
    assert run(house, "start the vacuum").kind is Kind.CLARIFY


def test_clean_area_is_checked(house):
    ok = Action.make("vacuum.clean_area", "vacuum.dusty", {"cleaning_area_id": ("kitchen",)})
    assert validate(ok, house) is None
    assert validate(Action.make("vacuum.clean_area", "vacuum.dusty", {"cleaning_area_id": ("moon",)}), house)
    assert validate(Action.make("vacuum.clean_area", "vacuum.sweepy", {"cleaning_area_id": ("bar",)}), house)


@pytest.mark.parametrize("said, plan", [
    ("have dusty clean the kitchen please now", '{"decision": "act", "clauses": [{"intent": "turn_on", "target": "kitchen"}]}'),
    ("could the kitchen get a clean", '{"decision": "act", "clauses": [{"intent": "turn_off", "target": "kitchen lights"}]}'),
    ("so just switch it on there", '{"decision": "act", "clauses": [{"intent": "turn_on", "target": "it"}]}'),
])
def test_a_model_never_turns_a_cleaning_job_into_a_light_switch(house, said, plan):
    from fortify_core.llm import ScriptedLLM
    out = asyncio.run(Pipeline(house, llm_tiers=[ScriptedLLM([plan, plan])]).ahandle(said, "kitchen", "c", "sat"))
    assert not any(a.service.startswith("light.") for a in out.actions)
