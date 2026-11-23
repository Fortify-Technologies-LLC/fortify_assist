"""Weather answers from a weather entity inside a real Home Assistant core."""
import pytest

from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.fortify_assist.const import CONF_WEATHER_ENTITY

from test_integration import say, setup_entry


@pytest.fixture(autouse=True)
async def base(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})


def forecasts(hass):
    async def handler(call) -> ServiceResponse:
        ids = call.data["entity_id"]
        ids = [ids] if isinstance(ids, str) else ids
        today = dt_util.now()
        tomorrow = today.replace(hour=12) + dt_util.dt.timedelta(days=1)
        return {e: {"forecast": [
            {"datetime": today.isoformat(), "condition": "sunny", "temperature": 71, "templow": 52},
            {"datetime": tomorrow.isoformat(), "condition": "rainy", "temperature": 60, "templow": 48,
             "precipitation_probability": 90}]} for e in ids}
    hass.services.async_register("weather", "get_forecasts", handler, supports_response=SupportsResponse.ONLY)


async def test_the_weather_comes_from_the_home_forecast(hass):
    hass.states.async_set("weather.station_two", "unavailable")
    hass.states.async_set("weather.airport", "cloudy", {"temperature": 50})
    hass.states.async_set("weather.forecast_home", "sunny", {"temperature": 68.2, "humidity": 40,
                                                             "wind_speed": 5.4, "wind_speed_unit": "mph"})
    forecasts(hass)
    await setup_entry(hass)
    speech, _ = await say(hass, "what's the weather")
    assert speech == "Right now it's sunny and 68 degrees, with humidity at 40 percent and wind at 5 miles per hour."
    speech, _ = await say(hass, "will it rain tomorrow")
    assert speech == "For tomorrow, expect rainy, with a high of 60 and a low of 48, and a 90 percent chance of rain."


async def test_the_weather_chosen_in_settings_is_used(hass):
    hass.states.async_set("weather.forecast_home", "sunny", {"temperature": 68})
    hass.states.async_set("weather.airport", "cloudy", {"temperature": 50})
    await setup_entry(hass, **{CONF_WEATHER_ENTITY: "weather.airport"})
    speech, _ = await say(hass, "what's the weather")
    assert speech == "Right now it's cloudy and 50 degrees."


async def test_without_a_weather_entity_it_says_how_to_add_one(hass):
    await setup_entry(hass)
    speech, _ = await say(hass, "what's the weather")
    assert "Met.no" in speech
