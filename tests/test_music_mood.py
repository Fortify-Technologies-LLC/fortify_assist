"""Mood lighting, music requests and the chat honesty guard."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core import ambience, music
from fortify_core.chat import CLAIM, Chatter
from fortify_core.llm import ScriptedLLM
from fortify_core.music import MusicHit, MusicRequest
from fortify_core.persona import Persona
from fortify_core.responses import Responder
from fortify_core.types import Action, Kind
from fortify_core.validator import validate
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house
from datetime import datetime


def run(p, text, ctx=None, cid=None):
    return asyncio.run(p.ahandle(text, ctx, cid))


@pytest.fixture()
def house():
    h = load_house(DEFAULT_HOUSE)
    h.entities["light.workshop_a"].attributes = {"supported_color_modes": ["rgb"]}
    h.entities["light.workshop_b"].attributes = {"supported_color_modes": ["color_temp"],
                                                 "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500}
    h.entities["light.bar_strip"].attributes = {"supported_color_modes": ["onoff"]}
    for pid in ("media_player.bar_speaker", "media_player.kitchen_speaker"):
        h.entities[pid].attributes["mass_player_type"] = "player"
    return h


class FakeMusic:
    def __init__(self, found=None, fail=False):
        self.found, self.fail, self.queries = found or {}, fail, []

    async def search(self, query, artist=None, limit=6):
        self.queries.append((query, artist))
        if self.fail:
            raise RuntimeError("down")
        return self.found

    async def library(self, media_type, limit=25):
        return []


LIB = {
    "tracks": [MusicHit("library://track/1", "Seven Nation Army", "track", ("The White Stripes",), True),
               MusicHit("spotify://track/9", "Seven Nation Army (Cover)", "track", ("Someone Else",))],
    "playlists": [MusicHit("library://playlist/2", "Jazz Classics", "playlist", (), True)],
    "audiobooks": [MusicHit("library://audiobook/3", "Dune", "audiobook", ("Frank Herbert",), True)],
}


# ---- mood lighting ------------------------------------------------------------------------------

def test_halloween_workshop_changes_only_what_each_light_can_do(house):
    out = run(Pipeline(house), "make the workshop halloween colors")
    assert out.kind is Kind.ACTION
    by = {a.entity_id: a.data_dict() for a in out.actions}
    assert set(by) == {"light.workshop_a", "light.workshop_b"}
    assert "color_name" in by["light.workshop_a"]
    assert "color_temp_kelvin" in by["light.workshop_b"] and 2000 <= by["light.workshop_b"]["color_temp_kelvin"] <= 6500
    assert not any(validate(a, house) for a in out.actions)


def test_on_off_lights_are_left_alone(house):
    out = run(Pipeline(house), "give the bar a spooky mood")
    assert out.kind is Kind.REFUSE and not out.actions


def test_here_uses_the_speakers_room(house):
    out = run(Pipeline(house), "set a cozy mood in here", "workshop")
    assert sorted(a.entity_id for a in out.actions) == ["light.workshop_a", "light.workshop_b"]


def test_a_device_named_like_a_theme_is_not_a_mood(house):
    out = run(Pipeline(house), "turn on the workshop light a")
    assert [a.entity_id for a in out.actions] == ["light.workshop_a"]
    assert not out.actions[0].data_dict()


def test_plain_colours_still_go_through_the_normal_path(house):
    out = run(Pipeline(house), "make the workshop light a blue")
    assert [a.data_dict().get("color_name") for a in out.actions] == ["blue"]


def test_theme_detection_does_not_fire_on_music_or_chat():
    find = lambda t: {"workshop"} if t == "workshop" else set()
    assert ambience.detect("play halloween music in the workshop", find) is None
    assert ambience.detect("what is halloween", find) is None
    assert ambience.detect("make the workshop feel like a campfire", find) == ("fire", "workshop")


def test_designed_palette_is_checked(house):
    good = ScriptedLLM(['{"palette": [{"color": "blue", "brightness": 40}, {"color": "warm white", "brightness": 30}]}'])
    assert asyncio.run(ambience.design([good], "rainy day"))
    for bad in ('{"palette": [{"color": "plaid", "brightness": 40}]}',
                '{"palette": [{"color": "blue", "brightness": 400}]}', "nonsense"):
        assert asyncio.run(ambience.design([ScriptedLLM([bad])], "rainy day")) is None


def test_unknown_theme_is_designed_by_the_model_then_applied_by_rules(house):
    parse = {"decision": "act", "clauses": [{"intent": "ambience", "target": "workshop", "option": "rainy day"}]}
    design = '{"palette": [{"color": "blue", "brightness": 40}, {"color": "warm white", "brightness": 30}]}'
    out = run(Pipeline(house, llm_tiers=[ScriptedLLM([parse, design])]), "give the workshop a rainy day feel")
    assert out.kind is Kind.ACTION and {a.entity_id for a in out.actions} == {"light.workshop_a", "light.workshop_b"}


def test_a_bit_cozier_is_a_mood(house):
    out = run(Pipeline(house), "could you get the workshop a bit cozier for movie time")
    assert {a.entity_id for a in out.actions} == {"light.workshop_a", "light.workshop_b"}


def test_a_mood_filed_as_a_device_option_is_still_a_mood(house):
    parse = {"decision": "act", "clauses": [{"intent": "set_option", "target": "workshop", "option": "cozy"}]}
    out = run(Pipeline(house, llm_tiers=[ScriptedLLM([parse])]), "can the workshop be more snug feeling")
    assert out.kind is Kind.ACTION and {a.entity_id for a in out.actions} == {"light.workshop_a", "light.workshop_b"}
    parse = {"decision": "act", "clauses": [{"intent": "set_option", "target": "workshop", "option": "party"}]}
    assert not run(Pipeline(house, llm_tiers=[ScriptedLLM([parse])]), "can the workshop be more snug feeling").actions


def test_the_model_cannot_invent_the_theme_or_the_room(house):
    parse = {"decision": "act", "clauses": [{"intent": "ambience", "target": "workshop", "option": "volcano"}]}
    out = run(Pipeline(house, llm_tiers=[ScriptedLLM([parse])]), "give the workshop a rainy day feel")
    assert not out.actions


# ---- music --------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,query,kind", [
    ("play seven nation army", "seven nation army", None),
    ("play some jazz music", "jazz", None),  # "music" is not part of what to search for
    ("play the jazz classics playlist", "jazz classics", "playlist"),
    ("play the song joker and the thief", "joker and the thief", "track"),
    ("play the song joker and the thief by wolfmother", "joker and the thief", "track"),
    ("read me dune", "dune", "audiobook"),
    ("put on the dune audiobook", "dune", "audiobook"),
])
def test_requests_are_understood(text, query, kind):
    rest, hint = music.verb_split(text)
    req = music.interpret(rest, hint)
    assert (req.query, req.media_type) == (query, kind)


def test_resume_is_not_a_search():
    assert music.interpret(*music.verb_split("play some music")) is None
    assert music.verb_split("play") is None  # no object: the normal rules resume the player


def test_exact_titles_beat_partial_and_nothing_weak_is_chosen():
    req = MusicRequest("seven nation army")
    assert music.choose(req, LIB).uri == "library://track/1"
    assert music.choose(MusicRequest("completely different song"), LIB) is None


def test_play_a_song_on_a_named_speaker(house):
    fake = FakeMusic(LIB)
    out = run(Pipeline(house, music_provider=fake), "play seven nation army on the kitchen speaker")
    assert out.kind is Kind.ACTION
    (a,) = out.actions
    assert a.service == "music_assistant.play_media" and a.entity_id == "media_player.kitchen_speaker"
    assert a.data_dict() == {"media_id": "library://track/1", "media_type": "track", "enqueue": "replace"}
    assert "Seven Nation Army" in out.message and fake.queries[0][0] == "seven nation army"


def test_titles_with_in_and_on_are_not_cut_as_rooms(house):
    fake = FakeMusic({"tracks": [MusicHit("library://t/5", "Walking on Sunshine", "track", (), True)]})
    out = run(Pipeline(house, music_provider=fake), "play walking on sunshine in the kitchen")
    assert out.actions and out.actions[0].entity_id == "media_player.kitchen_speaker"
    assert fake.queries[0][0] == "walking on sunshine"


def test_room_context_picks_the_speaker(house):
    out = run(Pipeline(house, music_provider=FakeMusic(LIB)), "play seven nation army", "kitchen")
    assert out.actions[0].entity_id == "media_player.kitchen_speaker"


def test_unclear_speaker_asks_then_plays(house):
    p = Pipeline(house, music_provider=FakeMusic(LIB))
    ask = run(p, "play seven nation army", None, "c1")
    assert ask.kind is Kind.CLARIFY and not ask.actions
    out = run(p, "the bar speaker", None, "c1")
    assert [a.entity_id for a in out.actions] == ["media_player.bar_speaker"]


def test_not_found_is_said_honestly_and_nothing_plays(house):
    out = run(Pipeline(house, music_provider=FakeMusic({})), "play zzzz qqqq on the kitchen speaker")
    assert out.kind is Kind.REFUSE and not out.actions and "zzzz qqqq" in out.message


def test_a_broken_music_service_never_claims_success(house):
    out = run(Pipeline(house, music_provider=FakeMusic(fail=True)), "play seven nation army on the kitchen speaker")
    assert not out.actions and out.kind is Kind.REFUSE


def test_non_music_speakers_are_refused(house):
    out = run(Pipeline(house, music_provider=FakeMusic(LIB)), "play seven nation army on the living room tv")
    assert not out.actions and "living room tv" in out.message.lower()


def test_without_music_assistant_play_commands_still_work(house):
    out = run(Pipeline(house), "pause the bar speaker")
    assert [a.service for a in out.actions] == ["media_player.media_pause"]


def test_play_media_is_validated(house):
    ok = Action.make("music_assistant.play_media", "media_player.bar_speaker",
                     {"media_id": "library://track/1", "media_type": "track", "enqueue": "replace"})
    assert validate(ok, house) is None
    for data in ({"media_id": "", "media_type": "track"}, {"media_id": "x", "media_type": "weapon"},
                 {"media_id": "x", "enqueue": "yolo"}, {"media_id": "x" * 500}, {"volume": 1}):
        assert validate(Action.make("music_assistant.play_media", "media_player.bar_speaker", data), house), data
    assert validate(Action.make("music_assistant.play_media", "light.workshop_a", {"media_id": "x"}), house)


@pytest.mark.parametrize("text,service", [
    ("pause the music", "media_player.media_pause"),
    ("skip this song", "media_player.media_next_track"),
])
def test_controls_for_whatever_is_playing(house, text, service):
    out = run(Pipeline(house), text, "bar")
    assert [(a.service, a.entity_id) for a in out.actions] == [(service, "media_player.bar_speaker")]


def test_what_song_is_this(house):
    out = run(Pipeline(house), "what song is this")
    assert out.kind is Kind.ANSWER and "Take Five" in out.message


def test_too_loud_turns_the_speaker_down(house):
    out = run(Pipeline(house), "it is too loud", "bar")
    assert out.actions and out.actions[0].entity_id == "media_player.bar_speaker"


# ---- chat honesty ---------------------------------------------------------------------------------

NOW = datetime(2026, 10, 26, 12, 0)


@pytest.mark.parametrize("claim", [
    "Sure, I've turned the lights orange.", "Done! I'm setting the workshop to halloween.",
    "I changed the colors for you.", "Okay, I played some jazz.", "All set.",
])
def test_action_claims_are_detected(claim):
    assert CLAIM.search(claim)


@pytest.mark.parametrize("fine", [
    "Halloween is on October thirty first.", "I can't control devices in chat.", "The turn of the century was 1900.",
])
def test_normal_replies_are_not_flagged(fine):
    assert not CLAIM.search(fine)


def test_chat_never_claims_it_did_something_in_the_home():
    p = Persona()
    chat = Chatter(p, Responder(p), [ScriptedLLM([], chat_replies=["Sure, I've turned the workshop lights orange!"])])
    said = asyncio.run(chat.reply([], "make it spooky", NOW, [], None, home_request=True))
    assert "turned" not in said and "Which device" in said
    free = Chatter(p, Responder(p), [ScriptedLLM([], chat_replies=["Sure, I've turned the page."])])
    assert asyncio.run(free.reply([], "tell me a story", NOW, [], None)) == "Sure, I've turned the page."


def test_chat_never_invents_a_device_state_for_a_home_request():
    p = Persona()
    made_up = "The lights were off but they are on and warm white at 75 percent brightness right behind you."
    chat = Chatter(p, Responder(p), [ScriptedLLM([], chat_replies=[made_up])])
    said = asyncio.run(chat.reply([], "warm white in the bar", NOW, [], None, home_request=True))
    assert "75" not in said and "Which device" in said
    fine = Chatter(p, Responder(p), [ScriptedLLM([], chat_replies=["Halloween is on October thirty first."])])
    assert "Halloween" in asyncio.run(fine.reply([], "when is halloween", NOW, [], None))
