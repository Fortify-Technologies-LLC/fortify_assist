import asyncio
from datetime import datetime

import pytest

from fortify_core.chat import Chatter, clean, system_prompt
from fortify_core.llm import (LLMError, OllamaClient, OpenAICompatClient, ScriptedLLM)
from fortify_core.persona import Persona
from fortify_core.responses import Responder, list_personalities

NOW = datetime(2026, 3, 14, 9, 30)
PERSONA = Persona(agent_name="N.O.V.A.", spoken_name="Nova", acronym_expansion="Neighborhood Operations and Voice Assistant",
                  owner="the Smiths", maker="Example Corp", purpose="I run the house.", personality="butler",
                  traits="loves astronomy")


def run(coro):
    return asyncio.run(coro)


def test_prompt_carries_the_persona_and_the_guardrails():
    p = system_prompt(PERSONA, Responder(PERSONA).chat_style, NOW, ["Kitchen", "Garage"])
    for expected in ("N.O.V.A.", "Neighborhood Operations", "'Nova'", "the Smiths", "Example Corp", "I run the house.",
                     "butler", "loves astronomy", "Saturday, March 14, 2026", "Kitchen, Garage", "You run this home"):
        assert expected in p, expected


def test_every_personality_has_a_chat_style():
    for info in list_personalities():
        assert Responder(Persona(personality=info["id"])).chat_style


def test_clean_makes_text_speakable():
    assert clean("**Hello** there!\n\n- one\n- two") == "Hello there! - one - two"
    assert clean("<think>plan</think>Sure thing.") == "Sure thing."
    long = "Sentence one. " * 100
    out = clean(long)
    assert len(out) <= 600 and out.endswith(".")


def test_chat_uses_history_and_the_persona():
    llm = ScriptedLLM([], chat_replies=["It is a lovely morning."])
    chatter = Chatter(PERSONA, Responder(PERSONA), [llm])
    reply = run(chatter.reply([("user", "hi"), ("assistant", "Hello.")], "how is the weather", NOW, ["Kitchen"]))
    assert reply == "It is a lovely morning."
    roles = [m["role"] for m in llm.chats[0]]
    assert roles == ["system", "user", "assistant", "user"] and llm.chats[0][-1]["content"] == "how is the weather"


def test_history_is_bounded():
    llm = ScriptedLLM([], chat_replies=["ok"])
    history = [("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(60)]
    run(Chatter(PERSONA, Responder(PERSONA), [llm]).reply(history, "now", NOW, []))
    assert len(llm.chats[0]) <= 1 + 12 + 1


def test_without_an_llm_chat_says_so_in_character():
    reply = run(Chatter(Persona(), Responder(Persona()), []).reply([], "tell me a joke", NOW, []))
    assert "Chat isn't set up" in reply
    butler = run(Chatter(PERSONA, Responder(PERSONA), []).reply([], "tell me a joke", NOW, []))
    assert butler != reply


def test_tiers_fall_through_and_failure_is_graceful():
    local = ScriptedLLM([], chat_replies=[LLMError("down")], name="local")
    cloud = ScriptedLLM([], chat_replies=["From the cloud."], name="cloud")
    assert run(Chatter(PERSONA, Responder(PERSONA), [local, cloud]).reply([], "hi", NOW, [])) == "From the cloud."
    dead = ScriptedLLM([], chat_replies=[LLMError("down")])
    assert run(Chatter(Persona(), Responder(Persona()), [dead]).reply([], "hi", NOW, [])).startswith("Chat isn't set up")


# ---- clients with an injected transport ---------------------------------------------

class FakeTransport:
    def __init__(self, response):
        self.response, self.calls = response, []

    async def __call__(self, method, url, payload, headers, timeout):
        self.calls.append((method, url, payload, headers))
        return self.response


def test_ollama_client_payloads():
    t = FakeTransport({"message": {"content": "hi"}, "models": [{"name": "b"}, {"name": "a"}]})
    c = OllamaClient("http://host:11434/", "m", transport=t)
    assert run(c.chat([{"role": "user", "content": "x"}])) == "hi"
    assert run(c.generate("sys", "usr", {"type": "object"})) == "hi"
    chat_call, gen_call = t.calls
    assert chat_call[1] == "http://host:11434/api/chat" and chat_call[2]["think"] is False and "format" not in chat_call[2]
    assert gen_call[2]["format"] == {"type": "object"} and gen_call[2]["options"]["temperature"] == 0
    assert run(c.list_models()) == ["a", "b"] and t.calls[-1][:2] == ("GET", "http://host:11434/api/tags")


def test_openai_client_payloads_and_auth():
    t = FakeTransport({"choices": [{"message": {"content": "hey"}}], "data": [{"id": "z"}, {"id": "y"}]})
    c = OpenAICompatClient("http://host:8080/v1", "m", api_key="secret", transport=t)
    assert run(c.generate("s", "u", {"type": "object"})) == "hey"
    method, url, payload, headers = t.calls[0]
    assert url == "http://host:8080/v1/chat/completions" and headers == {"Authorization": "Bearer secret"}
    assert payload["response_format"]["type"] == "json_schema"
    assert run(c.list_models()) == ["y", "z"]
    no_key = OpenAICompatClient("http://host/v1", "m", transport=t)
    run(no_key.chat([{"role": "user", "content": "x"}]))
    assert t.calls[-1][3] == {}


@pytest.mark.parametrize("client_cls,bad", [(OllamaClient, {"oops": 1}), (OpenAICompatClient, {"choices": []})])
def test_malformed_server_replies_become_llm_errors(client_cls, bad):
    c = client_cls("http://h", "m", transport=FakeTransport(bad))
    with pytest.raises(LLMError):
        run(c.chat([{"role": "user", "content": "x"}]))
