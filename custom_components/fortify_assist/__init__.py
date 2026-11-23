"""Fortify Assist: a high-accuracy conversation agent for Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .core.responses import preload
from .runtime import Runtime

PLATFORMS = [Platform.CONVERSATION]

type FortifyConfigEntry = ConfigEntry[Runtime]


async def async_setup_entry(hass: HomeAssistant, entry: FortifyConfigEntry) -> bool:
    await hass.async_add_executor_job(preload)  # read personality files off the event loop
    entry.runtime_data = Runtime.create(hass, entry)
    await entry.runtime_data.load_recent()  # what was heard and decided before the restart, for diagnostics
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FortifyConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removing the integration also removes what it remembered hearing."""
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, f"fortify_assist.{entry.entry_id}.recent").async_remove()


async def _options_updated(hass: HomeAssistant, entry: FortifyConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
