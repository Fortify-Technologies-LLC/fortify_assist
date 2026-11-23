"""Weather questions, answered from the home's own weather entity: what it reports, put in a sentence, never invented."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .text import normalize

# Home Assistant's weather conditions, as they are said
CONDITIONS = {"clear-night": "clear", "cloudy": "cloudy", "exceptional": "unusual", "fog": "foggy", "hail": "hail",
              "lightning": "stormy", "lightning-rainy": "stormy with rain", "partlycloudy": "partly cloudy",
              "pouring": "pouring rain", "rainy": "rainy", "snowy": "snowy", "snowy-rainy": "sleety",
              "sunny": "sunny", "windy": "windy", "windy-variant": "windy and cloudy"}
SPEED_UNITS = {"mph": "miles per hour", "km/h": "kilometers per hour", "m/s": "meters per second", "kn": "knots",
               "ft/s": "feet per second"}
COMMAND_START = {"turn", "switch", "set", "dim", "brighten", "open", "close", "lock", "unlock", "start", "stop",
                 "play", "put", "pause", "resume", "activate", "update", "launch"}
WEATHER = re.compile(r"\b(?:weather|forecast)\b")
RAIN = re.compile(r"\b(?:will|is|does|gonna|going to|chance of)\b.*\b(?:rain|raining|snow|snowing|storm|storming)\b"
                  r"|\b(?:need|bring|take) (?:an? )?(?:umbrella|jacket|coat)\b")
OUTSIDE = re.compile(r"\b(?:how (?:hot|cold|warm|chilly)|temperature|degrees)\b.*\b(?:outside|out there|outdoors)\b"
                     r"|^(?:what s |whats )?how (?:hot|cold|warm) is it(?: today| out)?$")
TOMORROW = re.compile(r"\btomorrow\b")
TODAY = re.compile(r"\b(?:today|tonight|later|this (?:morning|afternoon|evening))\b")


@dataclass(frozen=True)
class WeatherNow:
    condition: str = ""
    temperature: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_unit: str = ""


@dataclass(frozen=True)
class WeatherDay:
    condition: str = ""
    high: float | None = None
    low: float | None = None
    rain_chance: float | None = None


class WeatherSource(Protocol):
    def available(self) -> bool: ...

    async def now(self) -> WeatherNow | None: ...

    async def day(self, offset: int) -> WeatherDay | None: ...  # 0 today, 1 tomorrow


@dataclass(frozen=True)
class WeatherQuery:
    offset: int | None  # None: right now; 0: today's forecast; 1: tomorrow's


def ask(utterance: str) -> WeatherQuery | None:
    """A weather question, or None. Indoor temperatures ("how warm is the basement") are left to the sensors."""
    text = normalize(utterance)
    said = text.split()
    if not said or said[0] in COMMAND_START:
        return None
    rain = bool(RAIN.search(text))
    if not (WEATHER.search(text) or rain or OUTSIDE.search(text)):
        return None
    if TOMORROW.search(text):
        return WeatherQuery(1)
    if TODAY.search(text) or rain or "forecast" in said:
        return WeatherQuery(0)
    return WeatherQuery(None)


def _n(value: float | None) -> str:
    return str(round(value))


def condition(code: str) -> str:
    return CONDITIONS.get(code, code.replace("-", " ").replace("_", " "))


def now_report(w: WeatherNow | None) -> str:
    """'sunny and 68 degrees, with humidity at 40 percent and wind at 5 miles per hour', or '' with nothing to say."""
    if w is None:
        return ""
    lead = " and ".join(p for p in (condition(w.condition) if w.condition else "",
                                    f"{_n(w.temperature)} degrees" if w.temperature is not None else "") if p)
    extra = []
    if w.humidity is not None:
        extra.append(f"humidity at {_n(w.humidity)} percent")
    if w.wind_speed is not None:
        extra.append(f"wind at {_n(w.wind_speed)} {SPEED_UNITS.get(w.wind_unit, w.wind_unit)}".rstrip())
    if not lead:
        return " and ".join(extra)
    return lead + (", with " + " and ".join(extra) if extra else "")


def day_report(d: WeatherDay | None) -> str:
    """'partly cloudy, with a high of 72 and a low of 55, and a 20 percent chance of rain'."""
    if d is None:
        return ""
    temps = " and ".join(p for p in (f"a high of {_n(d.high)}" if d.high is not None else "",
                                     f"a low of {_n(d.low)}" if d.low is not None else "") if p)
    parts = [condition(d.condition) if d.condition else "", f"with {temps}" if temps else "",
             f"a {_n(d.rain_chance)} percent chance of rain" if d.rain_chance is not None else ""]
    parts = [p for p in parts if p]
    if len(parts) == 3:
        return f"{parts[0]}, {parts[1]}, and {parts[2]}"
    return ", ".join(parts)
