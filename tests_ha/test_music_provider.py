"""The Music Assistant provider maps service responses to hits and degrades to 'nothing found'."""
import asyncio
from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState

from custom_components.fortify_assist.ha_music import MassMusic


class FakeHass:
    def __init__(self, state, response):
        entry = SimpleNamespace(state=state, entry_id="abc")
        self.config_entries = SimpleNamespace(async_entries=lambda domain: [entry] if domain == "music_assistant" else [])
        self.calls = []

        async def async_call(domain, service, data, blocking=False, return_response=False):
            self.calls.append((domain, service, data))
            return response

        self.services = SimpleNamespace(async_call=async_call)


RESPONSE = {
    "tracks": [{"uri": "library://track/1", "name": "Seven Nation Army", "media_type": "track",
                "artists": [{"name": "The White Stripes"}]}, {"name": "no uri"}, "junk"],
    "playlists": [{"uri": "spotify://playlist/2", "name": "Jazz Classics"}],
}


def test_search_maps_hits_and_marks_library_items():
    hass = FakeHass(ConfigEntryState.LOADED, RESPONSE)
    found = asyncio.run(MassMusic(hass).search("seven nation army", "the white stripes"))
    assert [h.name for h in found["track"]] == ["Seven Nation Army"]
    assert found["track"][0].library and found["track"][0].artists == ("The White Stripes",)
    assert not found["playlist"][0].library
    assert hass.calls[0][2] == {"config_entry_id": "abc", "name": "seven nation army", "artist": "the white stripes"}


def test_no_loaded_music_assistant_finds_nothing():
    hass = FakeHass(ConfigEntryState.NOT_LOADED, RESPONSE)
    provider = MassMusic(hass)
    assert not provider.available()
    assert not any(asyncio.run(provider.search("anything")).values())
    assert not hass.calls
