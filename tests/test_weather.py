"""Weather questions: recognised narrowly, answered only from what the weather entity reports."""
import asyncio

import pytest

from fortify_core import Pipeline, weather
from fortify_core.chat import Chatter
from fortify_core.llm import ScriptedLLM
from fortify_core.persona import Persona
from fortify_core.responses import Responder
from fortify_core.types import Kind
from fortify_core.weather import WeatherDay, WeatherNow, WeatherQuery
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


@pytest.mark.parametrize("said, want", [
    ("what's the weather", WeatherQuery(None)),
    ("what's the weather like", WeatherQuery(None)),
    ("how's the weather today", WeatherQuery(0)),
    ("what's the forecast", WeatherQuery(0)),
    ("what's the weather tomorrow", WeatherQuery(1)),
    ("will it rain tomorrow", WeatherQuery(1)),
    ("is it going to snow", WeatherQuery(0)),
    ("do i need an umbrella", WeatherQuery(0)),
    ("how cold is it outside", WeatherQuery(None)),
    ("how hot is it", WeatherQuery(None)),
])
def test_weather_questions(said, want):
    assert weather.ask(said) == want


@pytest.mark.parametrize("said", ["how warm is it in the basement", "what's the temperature in the kitchen",
                                  "play the weather channel", "set the thermostat to 70", "tell me a joke",
                                  "turn on the rain sound"])
def test_other_requests_are_not_weather(said):
    assert weather.ask(said) is None


def test_reports_say_only_what_is_known():
    assert weather.now_report(WeatherNow("partlycloudy", 68.4, 40, 5, "mph")) == \
        "partly cloudy and 68 degrees, with humidity at 40 percent and wind at 5 miles per hour"
    assert weather.now_report(WeatherNow("sunny")) == "sunny"
    assert weather.now_report(WeatherNow()) == ""
    assert weather.day_report(WeatherDay("rainy", 72, 55, 80)) == \
        "rainy, with a high of 72 and a low of 55, and a 80 percent chance of rain"
    assert weather.day_report(WeatherDay("sunny", 60)) == "sunny, with a high of 60"


class FakeWeather:
    def __init__(self, now=None, days=None, fail=False):
        self._now, self._days, self.fail = now, days or {}, fail

    def available(self):
        return self._now is not None or bool(self._days)

    async def now(self):
        if self.fail:
            raise RuntimeError("down")
        return self._now

    async def day(self, offset):
        return self._days.get(offset)


def run(p, text):
    return asyncio.run(p.ahandle(text))


def pipeline(source):
    return Pipeline(load_house(DEFAULT_HOUSE), weather_source=source)


def test_the_weather_is_answered_from_the_entity():
    p = pipeline(FakeWeather(WeatherNow("sunny", 70), {1: WeatherDay("cloudy", 65, 50, 10)}))
    out = run(p, "what's the weather")
    assert out.kind is Kind.ANSWER and out.message == "Right now it's sunny and 70 degrees."
    assert run(p, "will it rain tomorrow").message == \
        "For tomorrow, expect cloudy, with a high of 65 and a low of 50, and a 10 percent chance of rain."


def test_no_weather_and_a_failing_weather_are_said_not_invented():
    assert "weather service" in run(pipeline(FakeWeather()), "what's the weather").message
    failing = pipeline(FakeWeather(WeatherNow("sunny"), fail=True))
    assert run(failing, "what's the weather").message == "I couldn't get the weather just now."
    assert run(pipeline(FakeWeather(WeatherNow("sunny"))), "what's the weather tomorrow").message == \
        "I couldn't get the weather just now."  # no forecast for tomorrow: not today's weather passed off


def test_device_commands_still_win():
    out = run(pipeline(FakeWeather(WeatherNow("sunny"))), "turn on the kitchen lights")
    assert out.kind is not Kind.ANSWER


@pytest.mark.parametrize("reply", ["Done.", "Done, played the music successfully.", "Success!", "All set."])
def test_chat_never_reports_success_for_a_question(reply):
    chatter = Chatter(Persona(), Responder(Persona()), [ScriptedLLM([], chat_replies=[reply])])
    from datetime import datetime
    said = asyncio.run(chatter.reply([], "are there launches today", datetime(2026, 10, 28, 9), []))
    assert said == "I'm not sure about that one. Could you ask another way?"
