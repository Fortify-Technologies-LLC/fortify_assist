"""Run against a real Home Assistant core: `pytest tests_ha` inside an environment with HA installed."""
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.fortify_assist.const import (CONF_AGENT_NAME, CONF_CONFIRM_SENSITIVE, CONF_MODEL, CONF_OWNER,
                                                    CONF_PERSONALITY, CONF_PRIMARY, CONF_PROVIDER, CONF_PURPOSE,
                                                    CONF_URL, DOMAIN)
from custom_components.fortify_assist.core.llm import LLMError, ScriptedLLM

AGENT = "conversation.assistant"
OLLAMA = {CONF_PROVIDER: "ollama", CONF_URL: "http://llm.example:11434", CONF_MODEL: "m"}


@pytest.fixture(autouse=True)
async def base(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})


def expose(hass, entity_id, name, state="off", **attrs):
    hass.states.async_set(entity_id, state, {"friendly_name": name, **attrs})
    async_expose_entity(hass, "conversation", entity_id, True)


async def setup_entry(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Assistant", options={CONF_AGENT_NAME: "Assistant", **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def say(hass, text, conversation_id=None, device_id=None):
    result = await conversation.async_converse(hass, text, conversation_id, Context(), language="en",
                                               agent_id=AGENT, device_id=device_id)
    return result.response.speech["plain"]["speech"], result


def light_services(hass, stuck=()):
    calls = []

    def make(on):
        async def handler(call):
            ids = call.data["entity_id"]
            for entity_id in ids if isinstance(ids, list) else [ids]:
                calls.append((call.service, entity_id, dict(call.data)))
                if entity_id not in stuck:
                    hass.states.async_set(entity_id, "on" if on else "off", dict(hass.states.get(entity_id).attributes))
        return handler

    hass.services.async_register("light", "turn_on", make(True))
    hass.services.async_register("light", "turn_off", make(False))
    return calls


def with_llm(llm):
    """Make the integration use a scripted model for the primary tier."""
    return patch("custom_components.fortify_assist.runtime.build_client",
                 side_effect=lambda cfg, transport: llm if cfg.get(CONF_PROVIDER) == "ollama" else None)


# ---- setup flow -------------------------------------------------------------------------------

async def test_setup_flow_without_a_language_model(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "persona"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        CONF_AGENT_NAME: "N.O.V.A.", CONF_OWNER: "the Smiths", CONF_PERSONALITY: "butler"})
    assert result["step_id"] == "primary"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROVIDER: "none"})
    assert result["step_id"] == "fallback"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROVIDER: "none"})
    assert result["step_id"] == "feeds"  # optional: every box can stay empty
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "N.O.V.A."
    assert result["options"]["feeds"] == []
    options = result["options"]
    assert options[CONF_OWNER] == "the Smiths" and options[CONF_PERSONALITY] == "butler"
    assert options[CONF_PRIMARY] == {CONF_PROVIDER: "none"}


async def test_setup_flow_picks_a_model_from_the_servers_list(hass):
    with patch("custom_components.fortify_assist.config_flow._list_models", return_value=["small", "big"]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_NAME: "Nova", CONF_PERSONALITY: "neutral"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PROVIDER: "ollama", CONF_URL: "http://llm.example:11434"})
        assert result["step_id"] == "primary_model"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MODEL: "small"})
        assert result["step_id"] == "fallback"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROVIDER: "none"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    primary = result["options"][CONF_PRIMARY]
    assert primary == {CONF_PROVIDER: "ollama", CONF_URL: "http://llm.example:11434", "api_key": "", CONF_MODEL: "small"}


async def test_unreachable_server_shows_an_error_and_lets_you_retry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_AGENT_NAME: "Nova", CONF_PERSONALITY: "neutral"})
    with patch("custom_components.fortify_assist.config_flow._list_models", side_effect=LLMError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PROVIDER: "openai", CONF_URL: "http://bad.example/v1"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}
    assert result["step_id"] == "primary"


async def test_options_flow_edits_the_persona_and_reloads(hass):
    entry = await setup_entry(hass, **{CONF_PERSONALITY: "neutral"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "persona"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AGENT_NAME: "Assistant", CONF_PERSONALITY: "secretary", CONF_PURPOSE: "Run the house."})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_PERSONALITY] == "secretary" and entry.state is ConfigEntryState.LOADED
    expose(hass, "light.deck", "Deck Lights")
    light_services(hass)
    speech, _ = await say(hass, "turn on the deck lights")
    from custom_components.fortify_assist.core.responses import load_pack
    assert speech in load_pack("secretary")["responses"]["exec_ok"] and speech != "Done."  # secretary, not neutral


# ---- real conversations -------------------------------------------------------------------------

async def test_a_command_runs_a_service_and_is_verified(hass):
    await setup_entry(hass)
    expose(hass, "light.deck", "Deck Lights")
    calls = light_services(hass)
    speech, result = await say(hass, "turn on the deck lights")
    assert calls == [("turn_on", "light.deck", {"entity_id": "light.deck"})]
    assert speech == "Done." and hass.states.get("light.deck").state == "on"
    assert not result.continue_conversation


async def test_a_device_that_ignores_the_command_is_reported_honestly(hass):
    await setup_entry(hass)
    expose(hass, "light.deck", "Deck Lights")
    light_services(hass, stuck={"light.deck"})
    with patch("custom_components.fortify_assist.core.executor.asyncio.sleep"):
        speech, _ = await say(hass, "turn on the deck lights")
    assert "Deck Lights is still off" in speech


async def test_a_failing_service_is_reported_not_raised(hass):
    await setup_entry(hass)
    expose(hass, "light.deck", "Deck Lights")

    async def boom(call):
        raise HomeAssistantError("hub offline")

    hass.services.async_register("light", "turn_on", boom)
    speech, _ = await say(hass, "turn on the deck lights")
    assert "hub offline" in speech


async def test_unexposed_entities_cannot_be_controlled(hass):
    await setup_entry(hass)
    hass.states.async_set("light.secret", "off", {"friendly_name": "Secret Light"})
    async_expose_entity(hass, "conversation", "light.secret", False)  # lights are exposed by default
    calls = light_services(hass)
    speech, _ = await say(hass, "turn on the secret light")
    assert calls == [] and "couldn't find" in speech


async def test_questions_are_answered_from_live_state(hass):
    await setup_entry(hass)
    expose(hass, "light.a", "Hall Light", "on")
    expose(hass, "light.b", "Den Light", "off")
    speech, _ = await say(hass, "which lights are on")
    assert speech == "1 light is on: Hall Light."
    hass.states.async_set("light.b", "on", {"friendly_name": "Den Light"})
    speech, _ = await say(hass, "how many lights are on")
    assert speech == "2 lights are on."


async def test_sensitive_actions_ask_first_and_run_on_yes(hass):
    await setup_entry(hass)
    expose(hass, "lock.front", "Front Door Lock", "locked")
    calls = []

    async def unlock(call):
        calls.append(call.data["entity_id"])
        hass.states.async_set("lock.front", "unlocked", {"friendly_name": "Front Door Lock"})

    hass.services.async_register("lock", "unlock", unlock)
    speech, result = await say(hass, "unlock the front door lock")
    assert "Are you sure" in speech and calls == [] and result.continue_conversation
    speech, _ = await say(hass, "yes", conversation_id=result.conversation_id)
    assert calls == ["lock.front"] and speech == "Done."


async def test_the_room_of_the_speaker_resolves_shared_names(hass):
    await setup_entry(hass)
    areas, ent_reg, dev_reg = ar.async_get(hass), er.async_get(hass), dr.async_get(hass)
    bedroom, office = areas.async_create("Bedroom"), areas.async_create("Office")
    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    for slug, area in (("bedroom", bedroom), ("office", office)):
        ent = ent_reg.async_get_or_create("light", "test", f"lamp_{slug}", suggested_object_id=f"lamp_{slug}",
                                          original_name="Lamp", config_entry=cfg)
        ent_reg.async_update_entity(ent.entity_id, area_id=area.id)
        hass.states.async_set(ent.entity_id, "off", {"friendly_name": "Lamp"})
        async_expose_entity(hass, "conversation", ent.entity_id, True)
    satellite = dev_reg.async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("test", "sat")}, name="Satellite")
    dev_reg.async_update_device(satellite.id, area_id=office.id)
    calls = light_services(hass)
    await hass.async_block_till_done()

    speech, _ = await say(hass, "turn on the lamp")
    assert "Which one" in speech and calls == []
    await say(hass, "turn on the lamp", device_id=satellite.id)
    assert [c[1] for c in calls] == ["light.lamp_office"]


async def test_new_devices_are_picked_up_without_a_restart(hass):
    await setup_entry(hass)
    light_services(hass)
    speech, _ = await say(hass, "turn on the porch light")
    assert "couldn't find" in speech
    expose(hass, "light.porch", "Porch Light")
    await say(hass, "turn on the porch light")
    assert hass.states.get("light.porch").state == "on"


# ---- chat and language models --------------------------------------------------------------------

async def test_chat_without_a_language_model_says_so(hass):
    await setup_entry(hass)
    speech, _ = await say(hass, "tell me a joke")
    assert "Chat isn't set up" in speech


async def test_chat_uses_the_persona_and_remembers_the_conversation(hass):
    llm = ScriptedLLM(lambda s, u: pytest.fail("plain chat must not call the intent parser"),
                      chat_replies=["Why did the lamp go to school? To get brighter.", "You are welcome."])
    with with_llm(llm):
        await setup_entry(hass, **{CONF_OWNER: "the Smiths", CONF_PRIMARY: OLLAMA})
    speech, result = await say(hass, "tell me a joke")
    assert speech.startswith("Why did the lamp")
    await say(hass, "another one", conversation_id=result.conversation_id)
    assert "the Smiths" in llm.chats[0][0]["content"]
    assert any(m["content"] == "tell me a joke" for m in llm.chats[1])  # earlier turns are passed along


async def test_the_language_model_can_rephrase_a_command_but_the_rules_still_decide(hass):
    llm = ScriptedLLM([{"decision": "act", "clauses": [{"intent": "close", "target": "blinds"}]}])
    with with_llm(llm):
        await setup_entry(hass, **{CONF_PRIMARY: OLLAMA})
    expose(hass, "cover.blinds", "Blinds", "open")
    closed = []

    async def close(call):
        closed.append(call.data["entity_id"])
        hass.states.async_set("cover.blinds", "closed", {"friendly_name": "Blinds"})

    hass.services.async_register("cover", "close_cover", close)
    speech, _ = await say(hass, "shut the blinds")
    assert closed == ["cover.blinds"] and speech == "Done."


async def test_confirmation_can_be_turned_off(hass):
    await setup_entry(hass, **{CONF_CONFIRM_SENSITIVE: False})
    expose(hass, "lock.front", "Front Door Lock", "locked")
    calls = []

    async def unlock(call):
        calls.append(1)
        hass.states.async_set("lock.front", "unlocked", {"friendly_name": "Front Door Lock"})

    hass.services.async_register("lock", "unlock", unlock)
    await say(hass, "unlock the front door lock")
    assert calls == [1]


async def test_unload_and_reload_are_clean(hass):
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


# ---- richer control, branding, diagnostics --------------------------------------------------------

async def test_colour_and_brightness_commands_use_the_lights_real_capabilities(hass):
    await setup_entry(hass)
    expose(hass, "light.strip", "Kitchen Counter Lights", "on",
           supported_color_modes=["color_temp", "hs"], brightness=200)
    expose(hass, "light.sign", "Bar Sign", "on", supported_color_modes=["onoff"])
    calls = light_services(hass)
    speech, _ = await say(hass, "make the kitchen counter lights blue")
    assert calls[-1][2] == {"entity_id": "light.strip", "color_name": "blue"} and speech == "Done."
    await say(hass, "make the kitchen counter lights a little dimmer")
    assert calls[-1][2]["brightness_step_pct"] == -10
    n = len(calls)
    speech, _ = await say(hass, "make the bar sign blue")
    assert len(calls) == n and "doesn't support color" in speech  # nothing was sent to a light that cannot do it


async def test_the_logo_ships_with_the_integration(hass):
    from homeassistant.loader import async_get_custom_components
    integration = (await async_get_custom_components(hass))[DOMAIN]
    assert integration.has_branding
    from pathlib import Path
    brand = Path(integration.file_path) / "brand"
    for name in ("icon.png", "icon@2x.png", "dark_icon.png", "dark_icon@2x.png"):
        assert (brand / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", name


async def test_diagnostics_show_what_was_heard_and_hide_secrets(hass):
    from custom_components.fortify_assist.diagnostics import async_get_config_entry_diagnostics
    llm = ScriptedLLM([], chat_replies=["Hello there."])
    with with_llm(llm):
        entry = await setup_entry(hass, **{CONF_PRIMARY: {**OLLAMA, "api_key": "s3cret"}})
    expose(hass, "light.deck", "Deck Lights")
    light_services(hass)
    await say(hass, "turn on the deck lights")
    await say(hass, "hello")
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert "s3cret" not in str(data) and "llm.example" not in str(data)
    assert data["exposed_to_assist"]["entities"] >= 1
    first = data["recent_requests"][0]
    assert first["heard"] == "turn on the deck lights" and first["decision"] == "action" and first["milliseconds"] >= 0
    assert data["recent_requests"][1]["decision"] == "chat"


async def test_personality_files_are_not_read_on_the_event_loop(hass, caplog):
    await setup_entry(hass, **{CONF_PERSONALITY: "butler"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "persona"
    assert "Detected blocking call" not in caplog.text


async def test_a_speaker_remembers_across_wake_words(hass):
    llm = ScriptedLLM([], chat_replies=["Hello there.", "Still here."])
    with with_llm(llm):
        await setup_entry(hass, **{CONF_PRIMARY: OLLAMA})
    expose(hass, "light.deck", "Deck Lights")
    calls = light_services(hass)
    await say(hass, "turn on the deck lights", device_id="speaker-1")  # each say is a new conversation, as after a wake word
    await say(hass, "turn them off", device_id="speaker-1")
    assert [c[:2] for c in calls] == [("turn_on", "light.deck"), ("turn_off", "light.deck")]
    await say(hass, "tell me something nice", device_id="speaker-1")
    await say(hass, "and another", device_id="speaker-1")
    assert "tell me something nice" in str(llm.chats[-1])


# ---- what Fortify does not understand ------------------------------------------------------------

async def test_home_assistants_own_assist_gets_what_the_rules_do_not_understand(hass):
    from homeassistant.helpers import intent

    class Lab(intent.IntentHandler):
        intent_type = "BootTheLab"

        async def async_handle(self, intent_obj):
            response = intent_obj.create_response()
            response.async_set_speech("Lab is booting.")
            return response

    async def own_assist(hass_, text, conversation_id, context, language=None, agent_id=None, *args, **kw):
        if agent_id != conversation.HOME_ASSISTANT_AGENT:  # the request to Fortify itself
            return await real(hass_, text, conversation_id, context, language, agent_id, *args, **kw)
        response = await Lab().async_handle(intent.Intent(hass_, "test", "BootTheLab", {}, text, context, language or "en"))
        return conversation.ConversationResult(response=response, conversation_id=None)

    real = conversation.async_converse
    llm = ScriptedLLM(lambda s, u: pytest.fail("a custom sentence must not reach the model"), chat_replies=[])
    with with_llm(llm):
        entry = await setup_entry(hass, **{CONF_PRIMARY: OLLAMA})
    with patch("custom_components.fortify_assist.conversation.conversation.async_converse", side_effect=own_assist):
        speech, _ = await say(hass, "boot up the lab")
    assert speech == "Lab is booting."
    assert entry.runtime_data.recent[-1]["decision"] == "home_assistant"


async def test_the_fallback_can_be_turned_off_and_timers_are_then_declined_plainly(hass):
    from custom_components.fortify_assist.const import CONF_HA_FALLBACK
    await setup_entry(hass, **{CONF_HA_FALLBACK: False})
    speech, _ = await say(hass, "set a timer for 5 minutes")
    assert speech == "I can't set timers or schedule things yet."


async def test_home_assistants_greeting_does_not_replace_the_persona(hass):
    llm = ScriptedLLM([], chat_replies=["Good day to you."])
    with with_llm(llm):
        await setup_entry(hass, **{CONF_PRIMARY: OLLAMA})
    speech, _ = await say(hass, "hello")
    assert speech == "Good day to you."


async def test_a_crash_is_answered_and_logged_not_raised(hass, caplog):
    entry = await setup_entry(hass)
    with patch.object(entry.runtime_data.pipeline, "ahandle", side_effect=RuntimeError("boom")):
        speech, _ = await say(hass, "turn on the lights")
    assert speech == "Something went wrong on my end. Please try again."
    assert "Could not handle" in caplog.text


async def test_the_time_comes_from_the_clock_not_a_model(hass):
    llm = ScriptedLLM(lambda s, u: pytest.fail("no model for the time"), chat_replies=[])
    with with_llm(llm):
        await setup_entry(hass, **{CONF_PRIMARY: OLLAMA})
    speech, _ = await say(hass, "what time is it")
    assert speech.startswith("It's ") and speech.endswith(("AM.", "PM."))


# ---- the microphone: open for a real question, never in a loop ------------------------------------

async def test_a_question_that_goes_nowhere_is_not_asked_twice(hass):
    await setup_entry(hass)
    first, r1 = await say(hass, "blah the gizmo lights", device_id="dev1")
    second, r2 = await say(hass, "blah the gizmo lights", device_id="dev1")
    third, r3 = await say(hass, "blah the gizmo lights", device_id="dev1")
    assert first.endswith("?") and r1.continue_conversation
    assert not second.endswith("?") and not r2.continue_conversation and "device or room name" in second
    assert not r3.continue_conversation


async def test_understanding_something_resets_the_count(hass):
    await setup_entry(hass)
    expose(hass, "light.deck", "Deck Lights")
    light_services(hass)
    await say(hass, "blah the gizmo lights", device_id="dev1")
    await say(hass, "turn on the deck lights", device_id="dev1")
    again, result = await say(hass, "blah the gizmo lights", device_id="dev1")
    assert again.endswith("?") and result.continue_conversation


async def test_a_chat_reply_that_ends_in_a_question_does_not_reopen_the_microphone(hass):
    llm = ScriptedLLM([], chat_replies=["Canberra. Anything else you would like to know?"])
    with with_llm(llm):
        await setup_entry(hass, **{CONF_PRIMARY: OLLAMA})
    speech, result = await say(hass, "what is the capital of australia")
    assert speech.endswith("?") and not result.continue_conversation


async def test_a_real_question_keeps_the_microphone_open(hass):
    await setup_entry(hass)
    expose(hass, "lock.front", "Front Door", "locked")
    speech, result = await say(hass, "unlock the front door")
    assert speech.endswith("?") and result.continue_conversation


async def test_recent_requests_survive_a_restart(hass, hass_storage):
    from custom_components.fortify_assist.diagnostics import async_get_config_entry_diagnostics
    entry = await setup_entry(hass)
    await say(hass, "what time is it")
    entry.runtime_data.save_recent()
    from homeassistant.helpers.storage import Store
    await entry.runtime_data.store.async_save(list(entry.runtime_data.recent))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert [r["heard"] for r in data["recent_requests"]] == ["what time is it"]


async def test_a_room_is_found_by_its_alias(hass):
    area = ar.async_get(hass).async_create("Workshop", aliases={"the lab"})
    await setup_entry(hass)
    entry = er.async_get(hass).async_get_or_create("light", "test", "bench", suggested_object_id="bench")
    er.async_get(hass).async_update_entity(entry.entity_id, area_id=area.id)
    expose(hass, entry.entity_id, "Bench Light")
    calls = light_services(hass)
    await say(hass, "turn on the lights in the lab")
    assert [c[1] for c in calls] == ["light.bench"]
