"""A home with several TVs and speakers: which one is meant, whose volume, and answers to "which one?"."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.types import Entity, House, Kind


def E(id, name, area, state="off", aliases=(), device_class=None, sensitive=False, **attrs):
    return Entity(id, name, area, state, aliases=list(aliases), device_class=device_class, sensitive=sensitive,
                  attributes=attrs)


def house():
    ents = [
        E("media_player.living_room_tv", "Living Room TV", "living_room", device_class="tv"),
        E("media_player.living_room_tv_cast", "Living Room TV Cast", "living_room", "paused", media_title="Show"),
        E("media_player.living_room_speaker", "Living Room Speaker", "living_room", "idle", volume_level=0.3),
        E("light.living_room_lamp", "Living Room Lamp", "living_room"),
        E("light.living_room_ceiling", "Living Room Ceiling", "living_room", "on"),
        E("media_player.den_tv", "Den TV", "den", "on", device_class="tv"),
        E("media_player.den_soundbar", "Den Soundbar", "den", "on", volume_level=0.4),
        E("media_player.den_speaker", "Den Speaker", "den", "playing", media_title="Take Five", volume_level=0.4),
        E("light.den_light_a", "Den Light A", "den"), E("light.den_light_b", "Den Light B", "den"),
        E("light.tv_lights", "TV Lights", "den", "on"),
        E("media_player.bedroom_tv", "Bedroom TV", "bedroom", device_class="tv"),
        E("light.bedroom_lamp", "Bedroom Lamp", "bedroom"), E("fan.bedroom_fan", "Bedroom Fan", "bedroom"),
        E("light.workshop_a", "Workshop Overhead Light A", "workshop"),
        E("light.workshop_b", "Workshop Overhead Light B", "workshop"),
        E("light.workshop_lamp", "Workshop Lamp", "workshop"), E("switch.printer", "3D Printer", "workshop"),
        E("light.kitchen_lights", "Kitchen Lights", "kitchen"),
        E("cover.garage_door", "Garage Door", None, "closed", device_class="garage", sensitive=True),
        E("scene.movie_night", "Movie Night", None, "scening"),
        E("climate.thermostat", "Thermostat", None, "heat", temperature=70, current_temperature=69),
    ]
    areas = {"living_room": ("Living Room", "main"), "den": ("Den", "basement"), "bedroom": ("Bedroom", "main"),
             "workshop": ("Workshop", "basement"), "kitchen": ("Kitchen", "main")}
    return House({"main": "Main Floor", "basement": "Basement"}, areas, {e.id: e for e in ents},
                 {"main": (0, []), "basement": (-1, [])}, {"workshop": ["the lab"]})


def talk(*said, area=None):
    p = Pipeline(house())
    return [asyncio.run(p.ahandle(t, area, "c", "s")) for t in said]


def acts(out):
    return sorted(f"{a.service.split('.')[1]} {a.entity_id.split('.')[1]}" for a in out.actions)


@pytest.mark.parametrize("said, area, want", [
    ("turn on the tv", "living_room", ["turn_on living_room_tv"]),  # the set, not the stick plugged into it
    ("turn off the tv", "den", ["turn_off den_tv"]), ("tv off", "den", ["turn_off den_tv"]),
    ("turn off the television", "living_room", ["turn_off living_room_tv"]),
    ("switch on the telly", "den", ["turn_on den_tv"]),
    ("turn on the tv in the living room", "den", ["turn_on living_room_tv"]),
    ("turn off the tvs", "den", ["turn_off den_tv"]),
    ("turn off all the tvs", None, ["turn_off bedroom_tv", "turn_off den_tv", "turn_off living_room_tv"]),
    ("turn on the tv lights", "living_room", ["turn_on tv_lights"]),  # a light, never the TV
    ("turn off the tv and the tv lights", "bedroom", ["turn_off bedroom_tv", "turn_off tv_lights"]),
    ("turn on the bedroom light", "bedroom", ["turn_on bedroom_lamp"]),  # never the bedroom TV or fan
    ("turn on the fan", "living_room", ["turn_on bedroom_fan"]),  # the home's only fan
    ("turn on the three d printer", None, ["turn_on printer"]),
    ("turn on the overhead light", "workshop", ["turn_on workshop_a", "turn_on workshop_b"]),
    ("turn on light a", "workshop", ["turn_on workshop_a"]),  # this room's, not the den's Light A
    ("turn on the lights in the lab", None, ["turn_on workshop_a", "turn_on workshop_b", "turn_on workshop_lamp"]),
    ("lites on", "kitchen", ["turn_on kitchen_lights"]), ("trun on the lights", "kitchen", ["turn_on kitchen_lights"]),
    ("play movie night", None, ["turn_on movie_night"]),
])
def test_the_right_device(said, area, want):
    assert acts(talk(said, area=area)[0]) == want


@pytest.mark.parametrize("said, area, want", [
    ("turn it up", "den", ["volume_up den_speaker"]),  # music is playing here: not the lights
    ("turn it down", "den", ["volume_down den_speaker"]), ("turn it up a bit", "den", ["volume_up den_speaker"]),
    ("music louder", "den", ["volume_up den_speaker"]), ("turn up the volume", "den", ["volume_up den_speaker"]),
    ("volume up", None, ["volume_up den_speaker"]),
    ("set the volume to 30", "den", ["volume_set den_speaker"]), ("volume 40 percent", "den", ["volume_set den_speaker"]),
    ("tv volume up", "living_room", ["volume_up living_room_tv"]),
    ("turn the tv volume down", "den", ["volume_down den_tv"]),
    ("turn up the volume on the tv", "den", ["volume_up den_tv"]),
])
def test_whose_volume(said, area, want):
    assert acts(talk(said, area=area)[0]) == want


def test_nothing_playing_means_the_lights():
    out = talk("turn it up", area="living_room")[0]
    assert out.actions and all(a.service == "light.turn_on" for a in out.actions)


# ---- answers to a question are answers, never a new request ------------------------------------

@pytest.mark.parametrize("answer, want", [
    ("living room", ["turn_on living_room_lamp"]), ("the living room one", ["turn_on living_room_lamp"]),
    ("the first one", ["turn_on bedroom_lamp"]),
    ("both", ["turn_on bedroom_lamp", "turn_on living_room_lamp", "turn_on workshop_lamp"]),
])
def test_which_one_is_answered(answer, want):
    asked, done = talk("turn on the lamp", answer)
    assert asked.kind is Kind.CLARIFY and acts(done) == want


def test_a_room_as_the_answer_never_switches_that_rooms_lights():
    asked, done = talk("turn on the tv", "den")
    assert asked.message == "Which room's TVs?" and acts(done) == ["turn_on den_tv"]


def test_no_to_which_one_is_heard():
    assert talk("turn on the lamp", "no")[1].message == "Okay."


def test_a_room_without_one_asks_which_of_the_others():
    out = talk("turn on the tv", area="kitchen")[0]
    assert out.kind is Kind.CLARIFY and out.message == "Which one: Living Room TV, Den TV or Bedroom TV?"


def test_follow_up_with_another_kind_of_device():
    assert acts(talk("turn off the lights", "and the tv", area="den")[1]) == ["turn_off den_tv"]


# ---- questions -----------------------------------------------------------------------------------

def test_a_soundbar_is_not_a_tv():
    assert talk("are the tvs on")[0].message == "2 TVs are on: Living Room TV Cast and Den TV."


def test_the_thermostat_answers_for_the_temperature():
    assert talk("what's the temperature")[0].message == "Thermostat is reading 69 degrees."
    assert acts(talk("set the temperature to 68")[0]) == ["set_temperature thermostat"]


def test_a_room_that_was_named_is_not_answered_from_elsewhere():
    assert "69" not in talk("what's the temperature in the kitchen")[0].message


def test_one_confirmation_is_enough():
    asked, done = talk("open the garage", "yes")
    assert asked.kind is Kind.CONFIRM and acts(done) == ["open_cover garage_door"]
