"""Per-config-entry runtime: the pipeline, the chat module and the house snapshot."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.homeassistant.exposed_entities import async_listen_entity_updates
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (ASSISTANT, CONF_ACRONYM, CONF_AGENT_NAME, CONF_API_KEY, CONF_CONFIRM_SENSITIVE, CONF_FALLBACK,
                    CONF_HA_FALLBACK, CONF_HOME_CHAT,
                    CONF_FEED_COUNT, CONF_FEED_ENTITIES, CONF_WEATHER_ENTITY, CONF_FEED_SUMMARY, CONF_FEEDS, DEFAULT_FEED_COUNT,
                    CONF_MAKER, CONF_MODEL, CONF_OWNER, CONF_PERSONALITY, CONF_PRIMARY, CONF_PROVIDER, CONF_PURPOSE,
                    CONF_SPOKEN_NAME, CONF_STRIP_NAME, CONF_TRAITS, CONF_URL, DEFAULT_AGENT_NAME,
                    DEFAULT_PERSONALITY, PROVIDER_NONE, PROVIDER_OLLAMA, PROVIDER_OPENAI)
from .core import Config, Pipeline
from .core.chat import Chatter
from .core.llm import LLMClient, OllamaClient, OpenAICompatClient, Transport
from .core.persona import Persona
from .core.responses import Responder
from .core.types import House
from .house import build_house, refresh_states
from .ha_io import make_transport
from .ha_feeds import HaFeeds, feed_list
from .ha_weather import HaWeather
from .ha_music import MassMusic


TURN_SECONDS = 300
RECENT_KEPT = 60  # requests kept for diagnostics, across restarts
MAX_OPEN_TURNS = 3  # the microphone is never reopened more than this many times in a row
LLM_TIMEOUT = 30.0  # someone is standing at a speaker waiting; past this, say so and move on


def build_client(cfg: dict, transport: Transport) -> LLMClient | None:
    provider = cfg.get(CONF_PROVIDER, PROVIDER_NONE)
    if provider == PROVIDER_NONE or not cfg.get(CONF_URL) or not cfg.get(CONF_MODEL):
        return None
    if provider == PROVIDER_OLLAMA:
        return OllamaClient(cfg[CONF_URL], cfg[CONF_MODEL], timeout=LLM_TIMEOUT, transport=transport)
    if provider == PROVIDER_OPENAI:
        return OpenAICompatClient(cfg[CONF_URL], cfg[CONF_MODEL], cfg.get(CONF_API_KEY, ""), timeout=LLM_TIMEOUT,
                                  transport=transport)
    return None


def persona_from(options: dict) -> Persona:
    return Persona(
        agent_name=options.get(CONF_AGENT_NAME) or DEFAULT_AGENT_NAME,
        spoken_name=options.get(CONF_SPOKEN_NAME, ""),
        acronym_expansion=options.get(CONF_ACRONYM, ""),
        owner=options.get(CONF_OWNER, ""),
        maker=options.get(CONF_MAKER, ""),
        purpose=options.get(CONF_PURPOSE, ""),
        personality=options.get(CONF_PERSONALITY) or DEFAULT_PERSONALITY,
        traits=options.get(CONF_TRAITS, ""),
    )


@dataclass
class Runtime:
    hass: HomeAssistant
    persona: Persona
    responder: Responder
    pipeline: Pipeline
    chatter: Chatter
    feeds: HaFeeds | None = None
    dirty: bool = True
    ha_fallback: bool = True
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_KEPT))  # last requests, for diagnostics
    store: Store | None = None
    streak: dict = field(default_factory=dict)  # speaker -> (misses in a row, open-microphone turns in a row)
    turns: dict = field(default_factory=dict)  # speaker -> recent (time, role, text): one wake word ends a conversation
    _unsubs: list[Callable[[], None]] = field(default_factory=list)

    def earlier_turns(self, speaker: str | None) -> list[tuple[str, str]]:
        """What was said on this speaker in the last few minutes, for chat."""
        now = time.monotonic()
        return [(role, text) for when, role, text in self.turns.get(speaker, ()) if now - when < TURN_SECONDS]

    def fluent(self, speaker: str | None, message: str, listen: bool, understood: bool) -> tuple[str, bool]:
        """Never ask the same thing twice, and never hold the microphone open in a loop. After one question
        that went nowhere the reply becomes a statement and the microphone closes until the wake word."""
        misses, opened = self.streak.get(speaker, (0, 0))
        if understood:
            misses = 0
        elif message in self.responder.all("no_action"):
            misses += 1
            if misses >= 2:
                message, listen = self.responder.say("not_understood"), False
        opened = opened + 1 if listen else 0
        if opened > MAX_OPEN_TURNS:
            listen, opened = False, 0
        self.streak[speaker] = (misses, opened)
        return message, listen

    async def load_recent(self) -> None:
        if self.store is not None and isinstance(saved := await self.store.async_load(), list):
            self.recent.extend(r for r in saved if isinstance(r, dict))

    def save_recent(self) -> None:
        if self.store is not None:
            self.store.async_delay_save(lambda: list(self.recent), 15)

    def add_turn(self, speaker: str | None, heard: str, said: str) -> None:
        if speaker:
            turns = self.turns.setdefault(speaker, deque(maxlen=12))
            now = time.monotonic()
            turns.extend([(now, "user", heard), (now, "assistant", said)])

    @classmethod
    def create(cls, hass: HomeAssistant, entry: ConfigEntry) -> "Runtime":
        options = entry.options
        transport = make_transport(hass)
        tiers = [c for c in (build_client(options.get(CONF_PRIMARY, {}), transport),
                             build_client(options.get(CONF_FALLBACK, {}), transport)) if c]
        persona = persona_from(options)
        config = Config(confirm_sensitive=options.get(CONF_CONFIRM_SENSITIVE, True),
                        strip_assistant_name=options.get(CONF_STRIP_NAME, True))
        feeds = HaFeeds(hass, feed_list(options.get(CONF_FEEDS)), options.get(CONF_FEED_ENTITIES, []))
        pipeline = Pipeline(House({}, {}, {}), config, llm_tiers=tiers, persona=persona, music_provider=MassMusic(hass),
                            feed_source=feeds, feed_count=int(options.get(CONF_FEED_COUNT, DEFAULT_FEED_COUNT)),
                            feed_summary=options.get(CONF_FEED_SUMMARY, False),
                            weather_source=HaWeather(hass, options.get(CONF_WEATHER_ENTITY)), now=dt_util.now,
                            home_chat=options.get(CONF_HOME_CHAT, False))
        rt = cls(hass, persona, pipeline.responder, pipeline, Chatter(persona, pipeline.responder, tiers), feeds,
                 ha_fallback=options.get(CONF_HA_FALLBACK, True),
                 store=Store(hass, 1, f"fortify_assist.{entry.entry_id}.recent"))
        rt._listen()
        feeds.start()
        if tiers:
            hass.async_create_background_task(rt._warm_up(tiers[0]), "fortify_assist model warm-up")
        return rt

    @staticmethod
    async def _warm_up(client: LLMClient) -> None:
        """Have the model loaded before anyone speaks; a cold model makes the first request take many seconds."""
        warm = getattr(client, "warm_up", None)  # only servers that load models on demand have one
        try:
            if warm is not None:
                await warm()
        except Exception:  # not there yet: the first real request will say so
            pass

    # ---- keeping the snapshot current ---------------------------------------------------

    @callback
    def _mark_dirty(self, *_args) -> None:
        self.dirty = True

    def _listen(self) -> None:
        hass = self.hass
        for event in (er.EVENT_ENTITY_REGISTRY_UPDATED, ar.EVENT_AREA_REGISTRY_UPDATED,
                      dr.EVENT_DEVICE_REGISTRY_UPDATED, fr.EVENT_FLOOR_REGISTRY_UPDATED):
            self._unsubs.append(hass.bus.async_listen(event, self._mark_dirty))
        self._unsubs.append(async_listen_entity_updates(hass, ASSISTANT, self._mark_dirty))

        @callback
        def added_or_removed(event: Event) -> None:
            if event.data.get("old_state") is None or event.data.get("new_state") is None:
                self.dirty = True

        self._unsubs.append(hass.bus.async_listen(EVENT_STATE_CHANGED, added_or_removed))

    def refresh(self) -> None:
        """Rebuild if registries or exposure changed, otherwise just refresh states."""
        if self.dirty:
            self.dirty = False
            self.pipeline.update_house(build_house(self.hass))
        else:
            refresh_states(self.hass, self.pipeline.house)

    def stop(self) -> None:
        if self.feeds:
            self.feeds.stop()
        while self._unsubs:
            self._unsubs.pop()()
