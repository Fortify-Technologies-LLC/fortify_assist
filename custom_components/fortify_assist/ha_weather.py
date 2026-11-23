"""Weather for the core, from a weather entity already in Home Assistant (Met.no is set up by default).

The entity chosen in settings is used while it is available; otherwise the first available one,
preferring a "home" forecast. Nothing is fetched by Fortify itself.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .core.weather import WeatherDay, WeatherNow

_LOGGER = logging.getLogger(__name__)
NOT_THERE = ("unavailable", "unknown")


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HaWeather:
    """Implements core.weather.WeatherSource."""

    def __init__(self, hass: HomeAssistant, entity_id: str | None = None):
        self.hass, self.entity_id = hass, entity_id

    def _state(self) -> State | None:
        if self.entity_id and (s := self.hass.states.get(self.entity_id)) and s.state not in NOT_THERE:
            return s
        found = [s for s in self.hass.states.async_all("weather") if s.state not in NOT_THERE]
        found.sort(key=lambda s: ("home" not in s.entity_id, s.entity_id))
        return found[0] if found else None

    def available(self) -> bool:
        return self._state() is not None

    async def now(self) -> WeatherNow | None:
        if (s := self._state()) is None:
            return None
        a = s.attributes
        return WeatherNow(s.state, _num(a.get("temperature")), _num(a.get("humidity")), _num(a.get("wind_speed")),
                          str(a.get("wind_speed_unit") or ""))

    async def day(self, offset: int) -> WeatherDay | None:
        if (s := self._state()) is None:
            return None
        want = (dt_util.now() + timedelta(days=offset)).date()
        for kind in ("daily", "twice_daily"):
            try:
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": s.entity_id, "type": kind},
                    blocking=True, return_response=True)
            except HomeAssistantError as exc:  # this entity does not offer that kind of forecast
                _LOGGER.debug("weather %s %s forecast: %s", s.entity_id, kind, exc)
                continue
            rows = [f for f in ((resp or {}).get(s.entity_id) or {}).get("forecast") or []
                    if (when := dt_util.parse_datetime(str(f.get("datetime", "")))) and dt_util.as_local(when).date() == want]
            if not rows:
                continue
            days = [f for f in rows if f.get("is_daytime", True)]
            nights = [f for f in rows if f.get("is_daytime") is False]
            main = (days or rows)[0]
            low = _num(main.get("templow"))
            if low is None and nights:
                low = _num(nights[0].get("temperature"))
            high = _num(main.get("temperature")) if days or kind == "daily" else None
            return WeatherDay(str(main.get("condition") or ""), high, low, _num(main.get("precipitation_probability")))
        return None
