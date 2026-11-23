"""Constants for the Fortify Assist integration."""
from __future__ import annotations

DOMAIN = "fortify_assist"

# persona
CONF_AGENT_NAME = "agent_name"
CONF_SPOKEN_NAME = "spoken_name"
CONF_ACRONYM = "acronym_expansion"
CONF_OWNER = "owner"
CONF_MAKER = "maker"
CONF_PURPOSE = "purpose"
CONF_PERSONALITY = "personality"
CONF_TRAITS = "traits"

# language model tiers: "primary" is asked first, "fallback" only if the primary fails
CONF_PRIMARY = "primary"
CONF_FALLBACK = "fallback"
CONF_PROVIDER = "provider"
CONF_URL = "url"
CONF_MODEL = "model"
CONF_API_KEY = "api_key"

# behaviour
CONF_CONFIRM_SENSITIVE = "confirm_sensitive"
CONF_STRIP_NAME = "strip_assistant_name"
CONF_HA_FALLBACK = "ha_fallback"  # let Home Assistant's own Assist try what Fortify does not understand
CONF_HOME_CHAT = "home_chat"  # let the language model answer questions about the home

# news and status feeds
CONF_FEEDS = "feeds"  # [{"name": ..., "url": ...}]
CONF_WEATHER_ENTITY = "weather_entity"
CONF_FEED_ENTITIES = "feed_entities"  # Feedparser sensors or Feed Reader events already in Home Assistant
CONF_FEED_SUMMARY = "feed_summary"
CONF_FEED_COUNT = "feed_count"
DEFAULT_FEED_COUNT = 3

PROVIDER_NONE = "none"
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
PROVIDERS = [PROVIDER_NONE, PROVIDER_OLLAMA, PROVIDER_OPENAI]

DEFAULT_AGENT_NAME = "Assistant"
DEFAULT_PERSONALITY = "neutral"
ASSISTANT = "conversation"  # the exposure namespace Home Assistant uses for Assist

# entity domains the assistant can see when exposed
DOMAINS = {
    "light", "switch", "fan", "cover", "lock", "climate", "media_player", "scene", "script", "sensor",
    "binary_sensor", "alarm_control_panel", "input_boolean", "humidifier", "valve", "siren", "vacuum", "remote",
}
# state attributes the core pipeline can answer questions about
ATTRIBUTES = ("brightness", "volume_level", "media_title", "media_artist", "temperature", "current_temperature",
              "supported_color_modes", "min_color_temp_kelvin", "max_color_temp_kelvin", "color_temp_kelvin",
              "hvac_modes", "percentage", "current_position", "is_volume_muted", "mass_player_type", "entity_id",
              "source_list", "source", "app_id", "color_mode", "hs_color", "supported_features")
SENSITIVE_COVER_CLASSES = {"garage", "gate", "door"}
