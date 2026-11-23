"""Home Assistant's own agent is never handed a sentence that could unlock or open something without asking."""
import urllib.error
import urllib.request

import aiohttp
import pytest

from custom_components.fortify_assist.conversation import RISKY
from custom_components.fortify_assist.core.llm import LLMError, urllib_transport
from custom_components.fortify_assist.ha_io import make_transport


@pytest.mark.parametrize("said", ["unlock the front door", "please unlock it", "disarm the alarm", "open the garage",
                                  "open the side gate", "open the front door lock"])
def test_risky_sentences_never_go_to_the_built_in_agent(said):
    assert RISKY.search(said)


@pytest.mark.parametrize("said", ["set a timer for 5 minutes", "add milk to the shopping list", "open the blinds",
                                  "what time is it", "lock the front door"])
def test_everyday_sentences_still_can(said):
    assert not RISKY.search(said)


URL = "http://user:secret@192.0.2.10:9/api/chat"  # what a misconfigured server address might hold


class RefusingSession:
    """Stands in for Home Assistant's web session; fails the way an unreachable server does, with the address."""

    def request(self, method, url, **kwargs):
        raise aiohttp.ClientConnectionError(f"Cannot connect to host {url}")


async def test_model_errors_never_carry_the_server_address(hass, monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.URLError(f"cannot reach {URL}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr("custom_components.fortify_assist.ha_io.async_get_clientsession", lambda h: RefusingSession())
    for transport in (urllib_transport, make_transport(hass)):
        with pytest.raises(LLMError) as err:
            await transport("POST", URL, {}, {}, 0.5)
        assert "192.0.2.10" not in str(err.value) and "secret" not in str(err.value)
