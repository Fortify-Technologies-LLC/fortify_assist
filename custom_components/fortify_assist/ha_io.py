"""Home Assistant adapters for the pure-Python core: web transport and service backend."""
from __future__ import annotations

from typing import Any

import aiohttp

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .core.executor import StateInfo
from .core.llm import LLMError, Transport


def make_transport(hass: HomeAssistant) -> Transport:
    """LLM HTTP calls go through Home Assistant's shared web session."""
    session = async_get_clientsession(hass)

    async def transport(method: str, url: str, payload: dict | None, headers: dict[str, str], timeout: float) -> Any:
        try:
            async with session.request(method, url, json=payload, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise LLMError(f"model server unreachable ({type(exc).__name__})") from exc  # never the address: it lands in diagnostics

    return transport


class HaBackend:
    """Lets the core executor call services and read states."""

    def __init__(self, hass: HomeAssistant, context: Context | None = None):
        self.hass, self.context = hass, context

    async def call_service(self, service: str, entity_id: str, data: dict[str, Any]) -> None:
        domain, _, name = service.partition(".")
        data = {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}  # HA's lists, not our frozen tuples
        await self.hass.services.async_call(domain, name, {ATTR_ENTITY_ID: entity_id, **data},
                                            blocking=True, context=self.context)

    def get_state(self, entity_id: str) -> StateInfo | None:
        state = self.hass.states.get(entity_id)
        return StateInfo(state.state, dict(state.attributes)) if state else None
