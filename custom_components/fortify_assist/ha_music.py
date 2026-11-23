"""Music Assistant as a music source: search and library lookups through its Home Assistant services."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .core.music import MusicHit

DOMAIN = "music_assistant"
KINDS = {"artists": "artist", "albums": "album", "tracks": "track", "playlists": "playlist", "radio": "radio",
         "audiobooks": "audiobook", "podcasts": "podcast"}


class MassMusic:
    """Implements core.music.MusicProvider. Without a loaded Music Assistant it finds nothing."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    def available(self) -> bool:
        return self._entry_id() is not None

    def _entry_id(self) -> str | None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                return entry.entry_id
        return None

    async def _call(self, service: str, data: dict[str, Any]) -> dict | None:
        entry_id = self._entry_id()
        if entry_id is None:
            return None
        result = await self.hass.services.async_call(
            DOMAIN, service, {"config_entry_id": entry_id, **data}, blocking=True, return_response=True)
        return result if isinstance(result, dict) else None

    async def search(self, query: str, artist: str | None = None, limit: int = 6) -> dict[str, list[MusicHit]]:
        data: dict[str, Any] = {"name": query}
        if artist:
            data["artist"] = artist
        result = await self._call("search", data)
        return {kind: [h for h in (_hit(i, kind) for i in (result or {}).get(plural, [])[:limit]) if h]
                for plural, kind in KINDS.items()}

    async def library(self, media_type: str, limit: int = 25) -> list[MusicHit]:
        result = await self._call("get_library", {"media_type": media_type, "order_by": "last_played_desc"})
        return [h for h in (_hit(i, media_type) for i in (result or {}).get("items", [])[:limit]) if h]


def _hit(item: Any, kind: str) -> MusicHit | None:
    if not isinstance(item, dict) or not item.get("uri") or not item.get("name"):
        return None
    artists = item.get("artists") or ([item["artist"]] if item.get("artist") else [])
    names = tuple(a.get("name", "") if isinstance(a, dict) else str(a) for a in artists)
    return MusicHit(str(item["uri"]), str(item["name"]), item.get("media_type") or kind,
                    tuple(n for n in names if n), str(item["uri"]).startswith("library://"))
