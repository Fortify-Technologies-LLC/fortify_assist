"""Opening an app on a TV."""
import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.types import Entity, House, Kind

PLEX = {"media_content_id": "com.plexapp.android", "media_content_type": "app"}


def house():
    tv = lambda i, n, a, **attrs: Entity(f"media_player.{i}", n, a, "on", device_class="tv", attributes=attrs)
    ents = [tv("den_tv", "Den TV", "den"), tv("bar_tv", "Bar TV Remote", "bar", app_id="launcher"),
            tv("lounge_tv", "Lounge TV", "lounge", source_list=["HDMI 1", "Netflix", "Plex", "Disney+"]),
            Entity("media_player.den_speaker", "Den Speaker", "den", "idle"),
            Entity("cover.garage_door", "Garage Door", None, "closed", device_class="garage", sensitive=True)]
    ents[1].aliases = ["Bar TV"]
    return House({}, {"den": ("Den", None), "bar": ("Bar", None), "lounge": ("Lounge", None)}, {e.id: e for e in ents})


def say(text, area=None):
    return asyncio.run(Pipeline(house()).ahandle(text, area, "c", "s"))


def act(out):
    return [(a.service, a.entity_id, a.data_dict()) for a in out.actions]


@pytest.mark.parametrize("said, area, tv", [
    ("open plex on the bar tv", None, "media_player.bar_tv"), ("Open Plex on the bar TV.", "den", "media_player.bar_tv"),
    ("launch plex on the den tv", None, "media_player.den_tv"), ("open plex", "den", "media_player.den_tv"),
    ("put plex on the bar tv", None, "media_player.bar_tv"), ("pull up plex on the tv", "bar", "media_player.bar_tv"),
])
def test_an_app_is_opened_by_package(said, area, tv):
    out = say(said, area)
    assert act(out) == [("media_player.play_media", tv, PLEX)] and out.message.startswith("Opening Plex on")


def test_a_tv_that_lists_the_app_is_asked_for_it_by_name():
    assert act(say("open netflix on the lounge tv")) == [("media_player.select_source", "media_player.lounge_tv",
                                                          {"source": "Netflix"})]
    assert act(say("open disney plus", "lounge")) == [("media_player.select_source", "media_player.lounge_tv",
                                                       {"source": "Disney+"})]


def test_other_names_for_an_app():
    assert act(say("open hbo on the den tv"))[0][2]["media_content_id"] == "com.wbd.stream"
    assert act(say("watch you tube on the den tv"))[0][2]["media_content_id"] == "com.google.android.youtube.tv"


def test_opening_other_things_is_unchanged():
    assert say("open the garage door").kind is Kind.CONFIRM
    assert not say("open the pod bay doors").actions


def test_a_tv_that_lists_only_inputs_is_asked_by_title():
    out = say("open hulu on the lounge tv")
    assert act(out) == [("media_player.play_media", "media_player.lounge_tv",
                         {"media_content_id": "Hulu", "media_content_type": "app"})]
    assert out.message == "Opening Hulu on Lounge TV."
