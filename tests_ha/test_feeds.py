"""News feeds inside a real Home Assistant core: settings, fetching, feed entities, diagnostics."""
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.fortify_assist.const import (CONF_FEED_COUNT, CONF_FEED_ENTITIES, CONF_FEED_SUMMARY,
                                                    CONF_FEEDS)
from custom_components.fortify_assist.diagnostics import async_get_config_entry_diagnostics

from test_integration import say, setup_entry

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example Tech News</title>
<item><title>Rocket lands on barge</title><pubDate>Tue, 27 Oct 2026 14:00:00 GMT</pubDate></item>
<item><title>New phone announced</title><pubDate>Tue, 27 Oct 2026 13:00:00 GMT</pubDate></item>
</channel></rss>"""
URL = "https://news.example.com/rss"


@pytest.fixture(autouse=True)
async def base(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})


async def test_a_feed_address_from_settings_is_read_and_cached(hass, aioclient_mock):
    aioclient_mock.get(URL, text=RSS)
    await setup_entry(hass, **{CONF_FEEDS: [{"name": "", "url": URL}], CONF_FEED_SUMMARY: False})
    await hass.async_block_till_done()  # the feed's own title is learned in the background
    speech, _ = await say(hass, "what's new on example tech")
    assert speech == "Here's the latest from Example Tech News. Rocket lands on barge. New phone announced."
    await say(hass, "what's the news")
    assert aioclient_mock.call_count == 1  # cached, not fetched for every question


async def test_an_unreachable_feed_is_reported(hass, aioclient_mock):
    aioclient_mock.get(URL, status=500)
    await setup_entry(hass, **{CONF_FEEDS: [{"name": "Tech News", "url": URL}]})
    speech, _ = await say(hass, "anything new on tech news")
    assert speech == "I couldn't read Tech News just now."


async def test_feedparser_sensors_and_feed_reader_events_work_without_any_address(hass):
    hass.states.async_set("sensor.space_news", "2", {"friendly_name": "Space News", "entries": [
        {"title": "Probe reaches orbit", "summary": "<p>It worked.</p>", "published": "2026-10-27T10:00:00+00:00"},
        {"title": "Launch delayed", "published": "2026-10-27T09:00:00+00:00"}]})
    hass.states.async_set("event.example_status", "2026-10-27T11:00:00+00:00",
                          {"friendly_name": "Example Status", "title": "Incident resolved", "link": "x"})
    await setup_entry(hass, **{CONF_FEED_ENTITIES: ["sensor.space_news", "event.example_status"]})
    speech, _ = await say(hass, "what's new on space news")
    assert speech == "Here's the latest from Space News. Probe reaches orbit. Launch delayed."
    speech, _ = await say(hass, "is example status down")
    assert speech.startswith("The latest from Example Status,") and speech.endswith(": Incident resolved.")


async def test_without_feeds_news_questions_say_how_to_add_them(hass):
    await setup_entry(hass)
    speech, _ = await say(hass, "what's the news")
    assert speech == "No news feeds are set up yet. You can add them in my settings."


def fields(result) -> list[str]:
    return [str(k) for k in result["data_schema"].schema]


async def open_feeds(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "feeds"})


async def test_feed_settings_are_name_and_address_pairs_with_room_to_add_more(hass, aioclient_mock):
    aioclient_mock.get(URL, text=RSS)
    aioclient_mock.get("https://bad.example.com/page", text="<html><body>not a feed</body></html>")
    entry = await setup_entry(hass)
    result = await open_feeds(hass, entry)
    assert result["step_id"] == "feeds"
    assert fields(result)[:8] == ["name_1", "url_1", "name_2", "url_2", "name_3", "url_3", "name_4", "url_4"]
    assert "name_5" not in fields(result)  # four pairs to start with
    bad = {"name_1": "Weather", "url_1": "https://bad.example.com/page", "name_2": "Sports",
           "name_3": "Tech", "url_3": URL, CONF_FEED_SUMMARY: False, CONF_FEED_COUNT: 3}
    result = await hass.config_entries.options.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"url_1": "feed_failed", "url_2": "feed_missing"}
    good = {"name_1": "Local news", "url_1": URL, "url_2": URL, CONF_FEED_SUMMARY: False, CONF_FEED_COUNT: 2}
    result = await hass.config_entries.options.async_configure(result["flow_id"], good)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_FEEDS] == [{"name": "Local news", "url": URL},  # no name: the feed's own title
                                         {"name": "Example Tech News", "url": URL}]
    assert entry.options[CONF_FEED_COUNT] == 2
    await hass.async_block_till_done()
    result = await open_feeds(hass, entry)
    assert "name_4" in fields(result) and "name_5" not in fields(result)  # two saved plus two spare
    suggested = {str(k): k.description.get("suggested_value") for k in result["data_schema"].schema
                 if getattr(k, "description", None)}
    assert suggested["name_1"] == "Local news" and suggested["url_2"] == URL


async def test_names_must_be_unique(hass, aioclient_mock):
    aioclient_mock.get(URL, text=RSS)
    result = await open_feeds(hass, await setup_entry(hass))
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "name_1": "Tech", "url_1": URL, "name_2": "tech", "url_2": URL, CONF_FEED_SUMMARY: False,
        CONF_FEED_COUNT: 3})
    assert result["errors"] == {"name_2": "feed_duplicate"}


async def test_feeds_saved_as_text_by_an_early_build_still_work(hass, aioclient_mock):
    aioclient_mock.get(URL, text=RSS)
    await setup_entry(hass, **{CONF_FEEDS: f"Tech News {URL}"})
    speech, _ = await say(hass, "anything new on tech news")
    assert speech.startswith("Here's the latest from Tech News.")


async def test_diagnostics_hide_feed_addresses(hass, aioclient_mock):
    aioclient_mock.get(URL, text=RSS)
    entry = await setup_entry(hass, **{CONF_FEEDS: [{"name": "Tech", "url": f"{URL}?token=secret"}]})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "secret" not in str(diag)


class _Chunks:
    """A response body that arrives in pieces, as a real network delivers it."""

    def __init__(self, body: bytes, size: int = 50):
        self._parts = [body[i:i + size] for i in range(0, len(body), size)]

    async def read(self, n=-1):
        return self._parts.pop(0) if self._parts else b""


class _Resp:
    def __init__(self, body):
        self.content = _Chunks(body)

    def raise_for_status(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kw):
        return _Resp(self.body)


async def test_a_feed_that_arrives_in_pieces_is_read_whole(hass, monkeypatch):
    from custom_components.fortify_assist import ha_feeds
    monkeypatch.setattr(ha_feeds, "async_get_clientsession", lambda h: _Session(RSS.encode()))
    title, items = await ha_feeds.read_url(hass, URL)
    assert title == "Example Tech News" and [i.title for i in items] == ["Rocket lands on barge", "New phone announced"]


async def test_old_items_stand_in_for_a_failing_feed_only_for_a_while(hass, aioclient_mock, monkeypatch):
    from custom_components.fortify_assist import ha_feeds
    aioclient_mock.get(URL, text=RSS)
    feeds = ha_feeds.HaFeeds(hass, [("Tech News", URL)], [])
    clock = [1000.0]
    monkeypatch.setattr(ha_feeds.time, "monotonic", lambda: clock[0])
    assert (await feeds.fetch())[0].ok
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, status=500)
    clock[0] += ha_feeds.CACHE_SECONDS + 1
    stale = (await feeds.fetch())[0]
    assert not stale.ok and stale.items and stale.read_at is not None  # said as "couldn't refresh", with its age
    clock[0] += ha_feeds.STALE_SECONDS
    assert (await feeds.fetch())[0].items == []  # too old to pass off as the news
