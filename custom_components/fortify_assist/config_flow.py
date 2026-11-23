"""Config and options flow: persona, language model endpoints, behaviour."""
from __future__ import annotations

from typing import Any

import asyncio

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (BooleanSelector, EntitySelector, EntitySelectorConfig, NumberSelector,
                                            NumberSelectorConfig, NumberSelectorMode, SelectOptionDict, SelectSelector,
                                            SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig,
                                            TextSelectorType)

from .const import (CONF_ACRONYM, CONF_AGENT_NAME, CONF_API_KEY, CONF_CONFIRM_SENSITIVE, CONF_FALLBACK,
                    CONF_HA_FALLBACK, CONF_HOME_CHAT,
                    CONF_FEED_COUNT, CONF_FEED_ENTITIES, CONF_WEATHER_ENTITY, CONF_FEED_SUMMARY, CONF_FEEDS, DEFAULT_FEED_COUNT, CONF_MAKER,
                    CONF_MODEL, CONF_OWNER, CONF_PERSONALITY, CONF_PRIMARY, CONF_PROVIDER, CONF_PURPOSE,
                    CONF_SPOKEN_NAME, CONF_STRIP_NAME, CONF_TRAITS, CONF_URL, DEFAULT_AGENT_NAME,
                    DEFAULT_PERSONALITY, DOMAIN, PROVIDER_NONE, PROVIDER_OLLAMA, PROVIDER_OPENAI)
from .core.llm import LLMError, OllamaClient, OpenAICompatClient
from .core.responses import list_personalities
from .ha_feeds import feed_list, read_url
from .ha_io import make_transport

DEFAULT_URLS = {PROVIDER_OLLAMA: "http://localhost:11434", PROVIDER_OPENAI: "http://localhost:8080/v1"}
ORDER = ["persona", "primary", "fallback", "feeds"]  # the order of a first-time setup
MAX_FEEDS = 20  # each box needs a label written in advance (strings.json); raising it means adding labels
SPARE_FEEDS = 2  # empty pairs always shown, so there is room to add more


def _text(**kw) -> TextSelector:
    return TextSelector(TextSelectorConfig(**kw))


def _persona_schema(available: list[dict]) -> vol.Schema:
    personalities = [SelectOptionDict(value=p["id"], label=f"{p['name']}: {p['description']}") for p in available]
    return vol.Schema({
        vol.Required(CONF_AGENT_NAME, default=DEFAULT_AGENT_NAME): _text(),
        vol.Optional(CONF_SPOKEN_NAME): _text(),
        vol.Optional(CONF_ACRONYM): _text(),
        vol.Optional(CONF_OWNER): _text(),
        vol.Optional(CONF_MAKER): _text(),
        vol.Optional(CONF_PURPOSE): _text(multiline=True),
        vol.Required(CONF_PERSONALITY, default=DEFAULT_PERSONALITY):
            SelectSelector(SelectSelectorConfig(options=personalities, mode=SelectSelectorMode.DROPDOWN)),
        vol.Optional(CONF_TRAITS): _text(multiline=True),
    })


def _tier_schema() -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_PROVIDER, default=PROVIDER_NONE): SelectSelector(SelectSelectorConfig(
            options=[PROVIDER_NONE, PROVIDER_OLLAMA, PROVIDER_OPENAI], translation_key="provider",
            mode=SelectSelectorMode.DROPDOWN)),
        vol.Optional(CONF_URL): _text(type=TextSelectorType.URL),
        vol.Optional(CONF_API_KEY): _text(type=TextSelectorType.PASSWORD),
    })


def _behaviour_schema() -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_CONFIRM_SENSITIVE, default=True): BooleanSelector(),
        vol.Required(CONF_STRIP_NAME, default=True): BooleanSelector(),
        vol.Required(CONF_HA_FALLBACK, default=True): BooleanSelector(),
        vol.Required(CONF_HOME_CHAT, default=False): BooleanSelector(),
        vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(EntitySelectorConfig(domain="weather")),
    })


def _feed_rows(saved: int) -> int:
    return min(MAX_FEEDS, max(4, saved + SPARE_FEEDS))


def _feeds_schema(rows: int) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for n in range(1, rows + 1):
        fields[vol.Optional(f"name_{n}")] = _text()
        fields[vol.Optional(f"url_{n}")] = _text(type=TextSelectorType.URL)
    fields[vol.Required(CONF_FEED_SUMMARY, default=False)] = BooleanSelector()
    fields[vol.Required(CONF_FEED_COUNT, default=DEFAULT_FEED_COUNT)] = NumberSelector(NumberSelectorConfig(
        min=1, max=5, step=1, mode=NumberSelectorMode.SLIDER))
    fields[vol.Optional(CONF_FEED_ENTITIES)] = EntitySelector(
        EntitySelectorConfig(domain=["sensor", "event"], multiple=True))
    return vol.Schema(fields)


async def _list_models(hass, provider: str, url: str, api_key: str) -> list[str]:
    transport = make_transport(hass)
    if provider == PROVIDER_OLLAMA:
        client = OllamaClient(url, "-", transport=transport)
    else:
        client = OpenAICompatClient(url, "-", api_key, transport=transport)
    return await client.list_models()


class _Steps:
    """Steps shared by the setup flow and the options flow."""

    _options: dict[str, Any]
    _models: list[str]
    _tier_cfg: dict[str, Any]

    async def _done(self, section: str) -> ConfigFlowResult:  # pragma: no cover - overridden
        raise NotImplementedError

    async def async_step_persona(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update({k: v for k, v in user_input.items() if v not in (None, "")})
            for key in (CONF_SPOKEN_NAME, CONF_ACRONYM, CONF_OWNER, CONF_MAKER, CONF_PURPOSE, CONF_TRAITS):
                if key not in user_input or user_input[key] in (None, ""):
                    self._options.pop(key, None)  # clearing a field really clears it
            return await self._done("persona")
        available = await self.hass.async_add_executor_job(list_personalities)  # reads files: not on the loop
        return self.async_show_form(step_id="persona", data_schema=self.add_suggested_values_to_schema(
            _persona_schema(available), self._options))

    async def async_step_behavior(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update(user_input)
            return await self._done("behavior")
        return self.async_show_form(step_id="behavior", data_schema=self.add_suggested_values_to_schema(
            _behaviour_schema(), self._options))

    async def async_step_feeds(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        saved = feed_list(self._options.get(CONF_FEEDS))
        rows = _feed_rows(len(saved))
        if user_input is not None:
            pairs = [(n, (user_input.get(f"name_{n}") or "").strip(), (user_input.get(f"url_{n}") or "").strip())
                     for n in range(1, rows + 1)]
            for n, name, url in pairs:
                if name and not url:
                    errors[f"url_{n}"] = "feed_missing"
            filled = [(n, name, url) for n, name, url in pairs if url]
            checks = await asyncio.gather(*(read_url(self.hass, url) for _, _, url in filled), return_exceptions=True)
            feeds: list[dict[str, str]] = []
            for (n, name, url), check in zip(filled, checks):  # each address must really be a feed
                if isinstance(check, Exception):
                    errors[f"url_{n}"] = "feed_failed"
                    continue
                name = name or check[0] or url  # no name given: use the feed's own title
                if name.lower() in {f["name"].lower() for f in feeds}:
                    errors[f"name_{n}"] = "feed_duplicate"
                    continue
                feeds.append({"name": name, "url": url})
            if not errors:
                self._options[CONF_FEEDS] = feeds
                self._options[CONF_FEED_ENTITIES] = user_input.get(CONF_FEED_ENTITIES) or []
                self._options[CONF_FEED_SUMMARY] = user_input[CONF_FEED_SUMMARY]
                self._options[CONF_FEED_COUNT] = int(user_input[CONF_FEED_COUNT])
                return await self._done("feeds")
            suggested = user_input
        else:
            suggested = {k: v for k, v in self._options.items() if k != CONF_FEEDS}
            for n, (name, url) in enumerate(saved, 1):
                suggested[f"name_{n}"], suggested[f"url_{n}"] = name, url
        return self.async_show_form(step_id="feeds", errors=errors, data_schema=self.add_suggested_values_to_schema(
            _feeds_schema(rows), suggested))

    # ---- language model tiers ------------------------------------------------------------

    async def _tier(self, tier: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            provider = user_input[CONF_PROVIDER]
            if provider == PROVIDER_NONE:
                self._options[tier] = {CONF_PROVIDER: PROVIDER_NONE}
                return await self._done(tier)
            url = (user_input.get(CONF_URL) or DEFAULT_URLS[provider]).strip()
            key = user_input.get(CONF_API_KEY, "")
            try:
                self._models = await _list_models(self.hass, provider, url, key)
            except LLMError:
                errors["base"] = "cannot_connect"
            else:
                self._tier_cfg = {CONF_PROVIDER: provider, CONF_URL: url, CONF_API_KEY: key}
                return await self.async_step_model(None, tier)
        return self.async_show_form(
            step_id=tier, errors=errors, data_schema=self.add_suggested_values_to_schema(
                _tier_schema(), user_input or self._options.get(tier, {})))

    async def async_step_model(self, user_input: dict[str, Any] | None, tier: str) -> ConfigFlowResult:
        if user_input is not None:
            self._options[tier] = {**self._tier_cfg, CONF_MODEL: user_input[CONF_MODEL]}
            return await self._done(tier)
        current = self._options.get(tier, {}).get(CONF_MODEL)
        default = current if current in self._models else (self._models[0] if self._models else "")
        return self.async_show_form(step_id=f"{tier}_model", data_schema=vol.Schema({
            vol.Required(CONF_MODEL, default=default): SelectSelector(SelectSelectorConfig(
                options=self._models, custom_value=True, mode=SelectSelectorMode.DROPDOWN))}))

    async def async_step_primary(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._tier(CONF_PRIMARY, user_input)

    async def async_step_primary_model(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_model(user_input, CONF_PRIMARY)

    async def async_step_fallback(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._tier(CONF_FALLBACK, user_input)

    async def async_step_fallback_model(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_model(user_input, CONF_FALLBACK)


class FortifyConfigFlow(_Steps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._models: list[str] = []
        self._tier_cfg: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FortifyOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_persona()

    async def _done(self, section: str) -> ConfigFlowResult:
        index = ORDER.index(section) + 1
        if index < len(ORDER):
            return await getattr(self, f"async_step_{ORDER[index]}")()
        return self.async_create_entry(title=self._options.get(CONF_AGENT_NAME, DEFAULT_AGENT_NAME), data={},
                                       options=self._options)


class FortifyOptionsFlow(_Steps, OptionsFlow):
    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._models: list[str] = []
        self._tier_cfg: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._options = dict(self.config_entry.options)
        return self.async_show_menu(step_id="init", menu_options=["persona", "primary", "fallback", "feeds", "behavior"])

    async def _done(self, section: str) -> ConfigFlowResult:
        return self.async_create_entry(data=self._options)
