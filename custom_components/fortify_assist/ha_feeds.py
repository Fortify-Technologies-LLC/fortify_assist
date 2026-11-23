"""News feeds for the core: addresses from Fortify's own settings, plus optional feed entities already in HA.

Addresses are fetched only when someone asks, through Home Assistant's web session, and cached for a few
minutes. Feed entities need nothing fetched: a Feedparser sensor keeps its entries as an attribute, and a
Feed Reader event entity holds its newest entry (older ones are remembered while Home Assistant runs).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

from .core.feeds import (MAX_FEED_BYTES, Feed, FeedItem, parse_date, parse_feed, plain, split_feed_lines,
                         summary_text)

_LOGGER = logging.getLogger(__name__)
CACHE_SECONDS = 600
STALE_SECONDS = 1800  # how long old items may stand in for a feed that will not refresh
WARN_SECONDS = 3600  # one log warning per failing feed per hour
TIMEOUT = 8
ENTITY_HISTORY = 20


async def read_url(hass: HomeAssistant, url: str) -> tuple[str, list[FeedItem]]:
    """Download and parse one feed. Raises ValueError with a short reason."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                               headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, */*"}) as resp:
            resp.raise_for_status()
            stream, chunks, size = resp.content, [], 0
            while chunk := await stream.read(65536):  # one read() returns only what has arrived so far
                size += len(chunk)
                if size > MAX_FEED_BYTES:
                    raise ValueError("feed is too large")
                chunks.append(chunk)
            data = b"".join(chunks)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ValueError(f"could not download: {exc}") from exc
    return await hass.async_add_executor_job(parse_feed, data)


def feed_list(value) -> list[tuple[str, str]]:
    """Saved feeds as [(name, url)]: a list of {"name", "url"}, or text with one feed per line (early 0.1.5)."""
    if isinstance(value, str):
        return split_feed_lines(value)
    return [(str(f.get("name") or ""), str(f["url"])) for f in value or [] if isinstance(f, dict) and f.get("url")]


def _host_name(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.removeprefix("www.").removeprefix("feeds.").removeprefix("rss.")


class HaFeeds:
    """Implements core.feeds.FeedSource."""

    def __init__(self, hass: HomeAssistant, urls: list[tuple[str, str]], entity_ids: list[str]):
        self.hass = hass
        self._urls = [(name, url) for name, url in urls]
        self._titles: dict[str, str] = {}  # url -> the feed's own title, for feeds added without a name
        self._cache: dict[str, tuple[float, Feed]] = {}
        self._warned: dict[str, float] = {}
        self._entity_ids = list(entity_ids)
        self._seen: dict[str, deque] = {e: deque(maxlen=ENTITY_HISTORY) for e in self._entity_ids}
        self._unsub = None

    # ---- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        if self._entity_ids:
            self._unsub = async_track_state_change_event(self.hass, self._entity_ids, self._entity_changed)
        if any(not name for name, _ in self._urls):  # learn the real titles so "what's on X" can find them
            self.hass.async_create_background_task(self.fetch(), "fortify_assist feed titles")

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _entity_changed(self, event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if state is not None and state.entity_id.startswith("event.") and state.attributes.get("title"):
            self._seen[state.entity_id].appendleft(self._event_item(state.attributes, state.state))

    # ---- names -----------------------------------------------------------------------------

    def _url_name(self, name: str, url: str) -> str:
        return name or self._titles.get(url) or _host_name(url)

    def _entity_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return (state.attributes.get("friendly_name") if state else None) or entity_id.split(".", 1)[1].replace("_", " ")

    def names(self) -> list[str]:
        return [self._url_name(n, u) for n, u in self._urls] + [self._entity_name(e) for e in self._entity_ids]

    # ---- reading ---------------------------------------------------------------------------

    async def fetch(self, names: list[str] | None = None) -> list[Feed]:
        jobs = [self._from_url(n, u) for n, u in self._urls if names is None or self._url_name(n, u) in names]
        feeds = list(await asyncio.gather(*jobs))
        feeds += [self._from_entity(e) for e in self._entity_ids if names is None or self._entity_name(e) in names]
        return feeds

    async def _from_url(self, name: str, url: str) -> Feed:
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            title, items = await read_url(self.hass, url)
        except ValueError as exc:
            if now - self._warned.get(url, now - WARN_SECONDS) >= WARN_SECONDS:
                self._warned[url] = now
                _LOGGER.warning("Could not read the feed %s (%s): %s", self._url_name(name, url), _host_name(url), exc)
            stale = cached[1] if cached and now - cached[0] < STALE_SECONDS else None
            return Feed(self._url_name(name, url), list(stale.items) if stale else [], ok=False,
                        read_at=stale.read_at if stale else None)
        self._warned.pop(url, None)
        if title:
            self._titles[url] = title
        feed = Feed(self._url_name(name, url), items, read_at=datetime.now(timezone.utc))
        self._cache[url] = (time.monotonic(), feed)
        return feed

    def _from_entity(self, entity_id: str) -> Feed:
        name = self._entity_name(entity_id)
        state = self.hass.states.get(entity_id)
        if state is None or state.state == "unavailable":
            return Feed(name, ok=False)
        if isinstance(entries := state.attributes.get("entries"), list):  # Feedparser sensor
            items = [FeedItem(plain(str(e.get("title", "")), 200), summary_text(str(e.get("summary", ""))),
                              str(e.get("link", "")), parse_date(str(e.get("published") or e.get("updated") or "")))
                     for e in entries if isinstance(e, dict) and e.get("title")]
            return Feed(name, items)
        if entity_id.startswith("event."):  # Feed Reader: the newest entry, plus what arrived since start-up
            items = list(self._seen[entity_id])
            if state.attributes.get("title"):
                newest = self._event_item(state.attributes, state.state)
                if not items or items[0].title != newest.title:
                    items.insert(0, newest)
            return Feed(name, items)
        return Feed(name, ok=False)

    @staticmethod
    def _event_item(attrs, when: str) -> FeedItem:
        try:
            published = datetime.fromisoformat(when)
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        return FeedItem(plain(str(attrs.get("title", "")), 200),
                        summary_text(str(attrs.get("content") or attrs.get("description") or "")),
                        str(attrs.get("link", "")), published)
