"""Download diagnostics: what the assistant heard and decided, without secrets."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FortifyConfigEntry
from .const import CONF_API_KEY, CONF_FEEDS, CONF_OWNER, CONF_URL

TO_REDACT = {CONF_API_KEY, CONF_URL, CONF_FEEDS, CONF_OWNER}  # feed addresses can carry private tokens


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FortifyConfigEntry) -> dict[str, Any]:
    rt = entry.runtime_data
    house = rt.pipeline.house
    domains: dict[str, int] = {}
    for e in house.entities.values():
        domains[e.domain] = domains.get(e.domain, 0) + 1
    return {
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "exposed_to_assist": {"entities": len(house.entities), "by_domain": dict(sorted(domains.items())),
                              "areas": len(house.areas), "floors": len(house.floors)},
        "language_models": [client.name for client in rt.pipeline.llm_tiers],
        "recent_requests": list(rt.recent),  # newest last; each says what was heard, decided and answered
    }
