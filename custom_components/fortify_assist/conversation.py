"""The Fortify Assist conversation agent."""
from __future__ import annotations

import logging
import re
import time
from typing import Literal

from homeassistant.components import conversation
from homeassistant.components.conversation import AssistantContent, UserContent
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent as ha_intent
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import FortifyConfigEntry
from .const import DOMAIN
from .core.executor import execute
from .core.resolver import label
from .core.types import Kind
from .ha_io import HaBackend


_LOGGER = logging.getLogger(__name__)
# what Home Assistant's own agent might do without asking first
RISKY = re.compile(r"\b(?:unlock|disarm|unarm)|\bopen\b.*\b(?:garage|gate|door|doors|lock)\b", re.I)
SMALL_TALK_INTENTS = {"HassRespond", "HassNevermind"}


async def async_setup_entry(hass: HomeAssistant, entry: FortifyConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([FortifyConversationEntity(entry)])


class FortifyConversationEntity(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """Deterministic-first agent: rules decide and act, the language model only helps rephrase and chat."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: FortifyConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Fortify Technologies",
            model="Fortify Assist", entry_type=DeviceEntryType.SERVICE)

    @property
    def runtime(self):
        return self.entry.runtime_data

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return ["en"]  # the rule grammar is English; more languages need their own grammar

    # ---- helpers -------------------------------------------------------------------------

    def _area_of(self, user_input: conversation.ConversationInput) -> str | None:
        """The room of the satellite or device that heard the request, if it has one."""
        ent_reg, dev_reg = er.async_get(self.hass), dr.async_get(self.hass)
        if user_input.satellite_id and (entry := ent_reg.async_get(user_input.satellite_id)):
            if entry.area_id:
                return entry.area_id
            if entry.device_id and (device := dev_reg.async_get(entry.device_id)):
                return device.area_id
        if user_input.device_id and (device := dev_reg.async_get(user_input.device_id)):
            return device.area_id
        return None

    @staticmethod
    def _history(chat_log: conversation.ChatLog) -> list[tuple[str, str]]:
        """Earlier turns of this conversation, excluding the message being answered."""
        turns = [("user" if isinstance(c, UserContent) else "assistant", c.content)
                 for c in chat_log.content if isinstance(c, (UserContent, AssistantContent)) and c.content]
        return turns[:-1] if turns and turns[-1][0] == "user" else turns

    async def _respond(self, outcome, user_input, chat_log, area, speaker) -> str | None:
        """What the rules decided, carried out. None when nothing was understood and someone else may try."""
        rt = self.runtime
        if outcome.kind is Kind.ACTION:
            names = {e.id: label(e, rt.pipeline.house) for e in rt.pipeline.house.entities.values()}
            result = await execute(outcome, HaBackend(self.hass, user_input.context), names, responder=rt.responder)
            return result.message
        if outcome.kind is Kind.CHAT and (outcome.handoff or not outcome.message):
            return None
        return outcome.message

    async def _ask_home_assistant(self, user_input: conversation.ConversationInput) -> tuple[str | None, bool]:
        """Timers, shopping lists, sentence triggers and custom sentences belong to Home Assistant's own Assist.
        It is only asked about what the rules did not understand, and only its own matches count."""
        if not self.runtime.ha_fallback or user_input.agent_id == conversation.HOME_ASSISTANT_AGENT:
            return None, False
        if RISKY.search(user_input.text):  # never let another agent unlock or open something without our confirmation
            return None, False
        try:
            result = await conversation.async_converse(
                self.hass, user_input.text, None, user_input.context, user_input.language,
                conversation.HOME_ASSISTANT_AGENT, user_input.device_id, user_input.satellite_id)
        except Exception:  # its failure must not become ours
            _LOGGER.debug("Home Assistant's Assist could not take %r", user_input.text, exc_info=True)
            return None, False
        response = result.response
        if response.response_type is ha_intent.IntentResponseType.ERROR:
            return None, False
        if response.intent is not None and response.intent.intent_type in SMALL_TALK_INTENTS:
            return None, False  # greetings and the like are this assistant's own voice, not Home Assistant's
        speech = ((response.speech or {}).get("plain") or {}).get("speech") or None
        return speech, bool(speech) and result.continue_conversation

    async def _chat(self, outcome, user_input, chat_log, area, speaker) -> str:
        rt = self.runtime
        if outcome.message:  # a hand-off nobody took: the honest fallback the rules prepared
            return outcome.message
        if (planned := rt.pipeline.chat_plan(user_input.text, area)) is not None:
            return planned
        home = rt.pipeline.home_talk(user_input.text)
        rooms = [name for name, _ in rt.pipeline.house.areas.values()] if home else []
        facts = rt.pipeline.home_facts(user_input.text, area) if home else []  # keeps chat honest about the home
        return await rt.chatter.reply(
            self._history(chat_log) or rt.earlier_turns(speaker), user_input.text, dt_util.now(), rooms, facts,
            home_request=home)

    # ---- the conversation ----------------------------------------------------------------

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        rt = self.runtime
        started = time.monotonic()
        area = speaker = None
        decision, actions = "error", []
        rt.chatter.last_trace = []
        listen = False  # keep the microphone open for the answer?
        try:
            rt.refresh()
            area = self._area_of(user_input)
            speaker = user_input.satellite_id or user_input.device_id
            outcome = await rt.pipeline.ahandle(user_input.text, area, chat_log.conversation_id, speaker)
            decision = outcome.kind.value
            actions = [f"{a.service} {a.entity_id} {a.data_dict() or ''}".strip() for a in outcome.actions]
            message = await self._respond(outcome, user_input, chat_log, area, speaker)
            listen = outcome.kind in (Kind.CLARIFY, Kind.CONFIRM)
            if message is None:
                decision = "home_assistant"
                message, listen = await self._ask_home_assistant(user_input)
            if message is None:
                decision, message = "chat", await self._chat(outcome, user_input, chat_log, area, speaker)
                listen = message in rt.pipeline.responder.all("no_action")  # "which device or room?" is a real question
            message, listen = rt.fluent(speaker, message, listen, understood=decision in ("action", "answer", "home_assistant"))
        except Exception:  # whatever went wrong, the person at the speaker gets an answer, and the log gets the cause
            _LOGGER.exception("Could not handle %r", user_input.text)
            message = rt.responder.say("error")

        rt.add_turn(speaker, user_input.text, message)
        elapsed_ms = (time.monotonic() - started) * 1000
        record = {
            "heard": user_input.text, "room": rt.pipeline.house.area_name(area), "decision": decision,
            "actions": actions,
            "said": message, "llm": list(rt.pipeline.trace) + list(rt.chatter.last_trace if decision == "chat" else []),
            "milliseconds": round(elapsed_ms),
        }
        rt.recent.append(record)
        _LOGGER.debug("heard %r in %s -> %s %s | said %r | llm=%s | %d ms", record["heard"], record["room"],
                      record["decision"], record["actions"], message, record["llm"], elapsed_ms)
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=user_input.agent_id, content=message))
        result = conversation.async_get_result_from_chat_log(user_input, chat_log)
        result.continue_conversation = listen  # Home Assistant would reopen the microphone after any "?"
        rt.save_recent()
        return result
