"""Music asked for the way people say it at a voice speaker: no title, a room word, a misheard number."""
import asyncio

import pytest

from fortify_core import Pipeline, music
from fortify_core.music import MusicHit
from fortify_core.types import Entity, Kind
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house

ROAD_TRIP = MusicHit("library://playlist/2", "Road Trip II", "playlist", (), True)
JAZZ = MusicHit("library://playlist/3", "Sunday Jazz", "playlist", (), True)


class FakeMusic:
    """Search finds a playlist only when every word of its name was said; the library is the household's own."""

    def __init__(self, library=(ROAD_TRIP, JAZZ)):
        self.lib, self.queries = list(library), []

    async def search(self, query, artist=None, limit=6):
        self.queries.append(query)
        said = set(query.lower().split())
        return {"playlists": [h for h in self.lib if set(h.name.lower().split()) <= said]}

    async def library(self, media_type, limit=25):
        return list(self.lib) if media_type == "playlist" else []


@pytest.fixture()
def house():
    h = load_house(DEFAULT_HOUSE)
    for pid in ("media_player.bar_speaker", "media_player.kitchen_speaker"):
        h.entities[pid].attributes["mass_player_type"] = "player"
    h.entities["media_player.bar_speaker"].state = "idle"
    h.entities["media_player.bar_speaker"].attributes.pop("media_title", None)
    return h


def run(p, text, room=None, cid="c"):
    return asyncio.run(p.ahandle(text, room, cid, "sat"))


def played(out):
    return [(a.service, a.entity_id, a.data_dict().get("media_id")) for a in out.actions]


@pytest.mark.parametrize("said", ["can you play some music down here", "play music in here", "Music.",
                                  "put some music on", "play something", "turn on some music", "play some music",
                                  "play music"])
def test_no_title_plays_something_on_this_rooms_speaker(house, said):
    fake = FakeMusic()
    out = run(Pipeline(house, music_provider=fake), said, "bar")
    assert played(out) == [("music_assistant.play_media", "media_player.bar_speaker", ROAD_TRIP.uri)]
    assert not fake.queries  # nothing was searched for: "music down here" is not a title


@pytest.mark.parametrize("said", ["play music in the kitchen", "play some music on the kitchen speaker",
                                  "play music in the kitchen please"])
def test_a_room_after_music_is_where_not_what(house, said):
    fake = FakeMusic()
    out = run(Pipeline(house, music_provider=fake), said, "bar")
    assert [a.entity_id for a in out.actions] == ["media_player.kitchen_speaker"] and not fake.queries


def test_a_misheard_word_after_the_speaker_still_finds_the_room(house):
    out = run(Pipeline(house, music_provider=FakeMusic()), "play music on the kitchen speaker pd", "bar")
    assert [a.entity_id for a in out.actions] == ["media_player.kitchen_speaker"]


def test_a_place_nobody_has_is_not_searched_for_as_a_song(house):
    fake = FakeMusic()
    out = run(Pipeline(house, music_provider=fake), "play music on the zeppelin", "bar")
    assert not out.actions and not fake.queries and "zeppelin" in out.message


def test_paused_music_is_resumed(house):
    house.entities["media_player.bar_speaker"].state = "paused"
    out = run(Pipeline(house, music_provider=FakeMusic()), "play some music", "bar")
    assert played(out) == [("media_player.media_play", "media_player.bar_speaker", None)]


def test_queued_music_carries_on(house):
    house.entities["media_player.bar_speaker"].attributes["media_title"] = "Take Five"
    out = run(Pipeline(house, music_provider=FakeMusic()), "play some music down here", "bar")
    assert played(out) == [("media_player.media_play", "media_player.bar_speaker", None)]


def test_music_already_playing_is_not_restarted(house):
    house.entities["media_player.bar_speaker"].state = "playing"
    house.entities["media_player.bar_speaker"].attributes["media_title"] = "Take Five"
    out = run(Pipeline(house, music_provider=FakeMusic()), "play some music down here", "bar")
    assert out.kind is Kind.ANSWER and not out.actions and "Take Five" in out.message


def test_with_nothing_queued_and_no_playlists_it_asks_what_to_play(house):
    out = run(Pipeline(house, music_provider=FakeMusic(library=())), "play some music", "bar")
    assert out.kind is Kind.CHAT and not out.actions and out.message == "What would you like to hear?"


@pytest.mark.parametrize("said", ["play my road trip to playlist", "play my road trip 2 playlist",
                                  "play road trip two"])
def test_a_misheard_number_still_finds_the_playlist(house, said):
    out = run(Pipeline(house, music_provider=FakeMusic()), said, "bar")
    assert played(out) == [("music_assistant.play_media", "media_player.bar_speaker", ROAD_TRIP.uri)]


def test_the_households_own_playlists_are_checked_when_search_misses():
    class SearchFindsNothing(FakeMusic):
        async def search(self, query, artist=None, limit=6):
            self.queries.append(query)
            return {}

    h = load_house(DEFAULT_HOUSE)
    h.entities["media_player.bar_speaker"].attributes["mass_player_type"] = "player"
    out = run(Pipeline(h, music_provider=SearchFindsNothing()), "play my sunday jazz playlist", "bar")
    assert played(out) == [("music_assistant.play_media", "media_player.bar_speaker", JAZZ.uri)]


def test_a_title_ending_in_here_is_still_a_title():
    assert music.strip_here("wish you were here") == ("wish you were here", False)
    assert music.strip_here("some jazz down here") == ("some jazz", True)
    assert music.strip_here("music here") == ("music", True)


def test_no_room_and_several_speakers_asks_which_then_plays(house):
    p = Pipeline(house, music_provider=FakeMusic())
    ask = run(p, "play some music", None, "c1")
    assert ask.kind is Kind.CLARIFY and not ask.actions
    out = run(p, "the kitchen speaker", None, "c1")
    assert played(out) == [("music_assistant.play_media", "media_player.kitchen_speaker", ROAD_TRIP.uri)]


def test_play_it_again_is_still_the_usual_resume(house):
    out = run(Pipeline(house, music_provider=FakeMusic()), "play it again", "bar")
    assert all(a.service != "music_assistant.play_media" for a in out.actions)


# ---- found by running every exposed device of a real home through the rules ---------------------------------

def test_room_lights_are_the_rooms_lights_not_one_device_with_a_longer_name(house):
    house.entities["light.kitchen_tv_lights"] = Entity("light.kitchen_tv_lights", "Kitchen TV Lights", "kitchen", "off")
    out = run(Pipeline(house), "turn on the kitchen lights")
    assert {a.entity_id for a in out.actions} == {"light.kitchen_ceiling", "light.kitchen_counters",
                                                  "light.kitchen_tv_lights"}


def test_the_only_device_by_a_shorter_name_is_still_found(house):
    out = run(Pipeline(house), "turn on the vanity")
    assert [a.entity_id for a in out.actions] == ["light.bathroom_vanity"]


@pytest.mark.parametrize("said, want", [("make it louder in the bar", ["media_player.bar_speaker"]),
                                        ("make it brighter in the workshop", ["light.workshop_a", "light.workshop_b"])])
def test_it_with_a_named_room_means_that_room(said, want):
    p = Pipeline(load_house(DEFAULT_HOUSE))  # the bar speaker is playing there
    run(p, "turn on the kitchen lights")  # something else to point at: the named room still wins
    assert sorted(a.entity_id for a in run(p, said).actions) == want


@pytest.mark.parametrize("said", ["set the movie night scene", "put on the movie night scene",
                                  "switch to the movie night scene"])
def test_set_the_scene(house, said):
    out = run(Pipeline(house), said)
    assert [(a.service, a.entity_id) for a in out.actions] == [("scene.turn_on", "scene.movie_night")]
