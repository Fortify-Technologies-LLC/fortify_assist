"""Found by the pre-release review: a fresh home, safety confirmations, and words that only sound alike."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.text import token_sim
from fortify_core.types import Entity, House, Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


def run(h, text, room=None, cid="c", p=None):
    return asyncio.run((p or Pipeline(h)).ahandle(text, room, cid, "sat"))


def ids(out):
    return sorted(a.entity_id for a in out.actions)


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


# ---- a home with no rooms set ------------------------------------------------------------------------------

def flat():
    ents = [Entity(f"light.l{i}", n, None, "on", attributes={"supported_color_modes": ["hs"], "brightness": 100})
            for i, n in enumerate(["Desk Lamp", "Ceiling", "Strip"])]
    ents.append(Entity("media_player.tv", "TV", None, "off", device_class="tv"))
    return House({}, {}, {e.id: e for e in ents})


@pytest.mark.parametrize("said", ["turn on the lights", "lights off", "make the lights brighter", "dim the lights",
                                  "turn off everything"])
def test_no_rooms_set_means_all_of_them_not_which_room(said):
    out = run(flat(), said)
    assert out.kind is Kind.ACTION and ids(out) == ["light.l0", "light.l1", "light.l2"]


def test_the_only_tv_in_a_home_without_rooms(house):
    assert ids(run(flat(), "turn on the tv")) == ["media_player.tv"]


def test_all_of_them_answers_which_room(house):
    p = Pipeline(house)
    assert run(house, "turn off the lights", p=p).kind is Kind.CLARIFY
    out = run(house, "all of them", p=p)
    assert out.kind is Kind.ACTION and len(out.actions) > 5


# ---- devices that never report a state ---------------------------------------------------------------------

def test_unknown_state_can_still_be_controlled(house):
    house.entities["light.bar_strip"].state = "unknown"  # a radio or template light that never reports
    assert ids(run(house, "turn on the bar strip")) == ["light.bar_strip"]


def test_unavailable_is_still_refused(house):
    house.entities["light.bar_strip"].state = "unavailable"
    out = run(house, "turn on the bar strip")
    assert not out.actions and "unavailable" in out.message


# ---- openers always ask -------------------------------------------------------------------------------------

@pytest.mark.parametrize("entity, said, verb", [
    (Entity("cover.shed", "Shed Garage Door", "garage", "closed"), "open the shed garage door", "open"),
    (Entity("cover.side", "Side Gate", None, "closed"), "open the side gate", "open"),
    (Entity("switch.gate", "Driveway Gate Opener", None, "off"), "turn on the driveway gate opener", "switch on"),
    (Entity("switch.gd", "Garage Opener Relay", None, "off"), "toggle the garage opener relay", "switch"),
    (Entity("valve.main", "Main Water", None, "closed"), "open the main water", "open"),
])
def test_doors_gates_and_valves_ask_first_whatever_their_class(house, entity, said, verb):
    house.entities[entity.id] = entity
    out = run(house, said)
    assert out.kind is Kind.CONFIRM and not out.actions and f"to {verb} " in out.message


def test_a_garage_position_is_asked_in_plain_words(house):
    house.entities["cover.garage_door"].attributes["current_position"] = 0
    out = run(house, "set the garage door to 50 percent")
    assert out.kind is Kind.CONFIRM and "brightness" not in out.message and "move" in out.message


def test_ordinary_switches_and_blinds_do_not_ask(house):
    house.entities["switch.fan"] = Entity("switch.fan", "Porch Fan", None, "off")
    assert run(house, "turn on the porch fan").kind is Kind.ACTION


# ---- words that only sound alike --------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [("attic", "deck"), ("dryer", "door"), ("garden", "curtain")])
def test_different_words_with_the_same_consonants_are_not_the_same(a, b):
    assert token_sim(a, b) == 0.0


@pytest.mark.parametrize("a, b", [("lamp", "lamb"), ("fan", "van"), ("desk", "disk"), ("dock", "deck")])
def test_real_speech_slips_still_match(a, b):
    assert token_sim(a, b) > 0.9


@pytest.mark.parametrize("said", ["turn on the attic light", "turn on the garden lights", "turn off the dryer",
                                  "close the dryer", "turn on the curtain lights"])
def test_a_device_the_home_does_not_have_is_not_swapped_for_another(house, said):
    out = run(house, said)
    assert not out.actions and out.kind is not Kind.CONFIRM


def test_lights_never_mean_a_curtain_but_a_door_still_closes(house):
    assert ids(run(house, "turn off the garage door")) == ["cover.garage_door"]


# ---- stray words --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("said", ["a", "b", "set the bar strip to -5 percent", "set the bar strip to minus 5 percent"])
def test_stray_letters_and_negative_settings_do_nothing(house, said):
    assert not run(house, said, "workshop").actions


@pytest.mark.parametrize("reply, want", [("a", "light.workshop_a"), ("the b one", "light.workshop_b")])
def test_a_letter_answers_which_one(house, reply, want):
    p = Pipeline(house)
    assert run(house, "turn on the workshop light", p=p, cid="q").kind is Kind.CLARIFY
    assert ids(run(house, reply, p=p, cid="q")) == [want]


@pytest.mark.parametrize("said, want", [
    ("let's have movie night", ["scene.movie_night"]), ("let’s have movie night", ["scene.movie_night"]),
    ("uh can you like turn the uh lamp off", ["light.office_lamp"]),
    ("turn the kitchen lights off or whatever", ["light.kitchen_ceiling", "light.kitchen_counters"]),
    ("turn off the thermostat", ["climate.upstairs"]),
])
def test_everyday_phrasing(house, said, want):
    assert ids(run(house, said, "office")) == want


def test_liking_something_is_not_a_command(house):
    assert not run(house, "I like the kitchen lights", "office").actions


# ---- no music library ---------------------------------------------------------------------------------------

class NoLibrary:
    def available(self):
        return False

    async def search(self, *a, **k):
        raise AssertionError("never searched")

    async def library(self, *a, **k):
        return []


def test_without_a_music_library_it_says_what_is_missing(house):
    house.entities["media_player.kitchen_speaker"].attributes["mass_player_type"] = "player"
    out = asyncio.run(Pipeline(house, music_provider=NoLibrary()).ahandle("play some jazz in the kitchen", None, "c", "s"))
    assert not out.actions and "Music Assistant" in out.message
