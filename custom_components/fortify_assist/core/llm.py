"""LLM clients. The core needs two things: structured JSON for intent rewriting, and free text for chat.

`generate` must return raw JSON text. Clients ask the server to constrain output to the schema,
but nothing downstream trusts that: the semantic parser re-validates every field.

The HTTP layer is injectable (`transport`) so Home Assistant can use its own web client while
the core stays dependency-free.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Awaitable, Callable, Protocol

Transport = Callable[[str, str, "dict | None", "dict[str, str]", float], Awaitable[Any]]
"""transport(method, url, json_payload_or_None, headers, timeout_seconds) -> decoded JSON"""


CHAT_TEMPERATURE = 0.4  # spoken answers want to be right more than they want to be inventive
CHAT_TOKENS = 220  # a reply is one to three sentences; a cap stops a small model rambling for ten seconds


class LLMError(Exception):
    """Transport or server failure; the pipeline moves on to the next tier."""


class LLMClient(Protocol):
    name: str

    async def generate(self, system: str, user: str, schema: dict[str, Any]) -> str: ...

    async def chat(self, messages: list[dict[str, str]]) -> str: ...

    async def list_models(self) -> list[str]: ...


async def urllib_transport(method: str, url: str, payload: dict | None, headers: dict[str, str],
                           timeout: float) -> Any:
    def call() -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, body, {"Content-Type": "application/json", **headers}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    try:
        return await asyncio.to_thread(call)
    except Exception as exc:  # urllib raises many unrelated types
        raise LLMError(f"model server unreachable ({type(exc).__name__})") from exc  # never the address: it lands in diagnostics


class OllamaClient:
    """Native Ollama API: schema-constrained JSON, thinking disabled."""

    def __init__(self, base_url: str, model: str, timeout: float = 60.0, num_ctx: int = 2048,
                 transport: Transport = urllib_transport):
        self.name = f"ollama:{model}"
        self.base_url, self.model, self.timeout, self.num_ctx = base_url.rstrip("/"), model, timeout, num_ctx
        self._transport = transport

    async def _chat(self, messages, temperature: float, fmt=None) -> str:
        payload = {"model": self.model, "stream": False, "think": False, "messages": messages,
                   "options": {"temperature": temperature, "num_ctx": self.num_ctx, "num_predict": CHAT_TOKENS}}
        if fmt is not None:
            payload["format"] = fmt
        data = await self._transport("POST", f"{self.base_url}/api/chat", payload, {}, self.timeout)
        try:
            return data["message"]["content"] or ""
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {exc}") from exc

    async def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        return await self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 0, schema)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        return await self._chat(messages, CHAT_TEMPERATURE)

    async def warm_up(self) -> None:
        """Load the model into memory without generating anything."""
        await self._transport("POST", f"{self.base_url}/api/generate", {"model": self.model}, {}, self.timeout)

    async def list_models(self) -> list[str]:
        data = await self._transport("GET", f"{self.base_url}/api/tags", None, {}, min(self.timeout, 15))
        try:
            return sorted(m["name"] for m in data["models"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {exc}") from exc


class OpenAICompatClient:
    """Any /v1/chat/completions server (llama.cpp, vLLM, LM Studio, OpenAI, ...)."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60.0,
                 transport: Transport = urllib_transport):
        self.name = f"openai:{model}"
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout
        self._transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _complete(self, messages, temperature: float, response_format=None) -> str:
        payload = {"model": self.model, "temperature": temperature, "messages": messages, "max_tokens": CHAT_TOKENS}
        if response_format is not None:
            payload["response_format"] = response_format
        data = await self._transport("POST", f"{self.base_url}/chat/completions", payload, self._headers, self.timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {exc}") from exc

    async def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        fmt = {"type": "json_schema", "json_schema": {"name": "plan", "schema": schema, "strict": True}}
        return await self._complete([{"role": "system", "content": system}, {"role": "user", "content": user}], 0, fmt)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        return await self._complete(messages, CHAT_TEMPERATURE)

    async def list_models(self) -> list[str]:
        data = await self._transport("GET", f"{self.base_url}/models", None, self._headers, min(self.timeout, 15))
        try:
            return sorted(m["id"] for m in data["data"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {exc}") from exc


class ScriptedLLM:
    """Test double: canned replies in order, or computed from the prompt."""

    def __init__(self, replies, name: str = "scripted", chat_replies=None, models=None):
        self.name = name
        self._replies = replies
        self._chat = chat_replies if chat_replies is not None else ["scripted chat"]
        self._models = models or ["scripted-model"]
        self.prompts: list[tuple[str, str]] = []
        self.chats: list[list[dict[str, str]]] = []

    async def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        self.prompts.append((system, user))
        reply = self._replies(system, user) if callable(self._replies) else self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.chats.append(messages)
        reply = self._chat(messages) if callable(self._chat) else self._chat.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def list_models(self) -> list[str]:
        return list(self._models)
