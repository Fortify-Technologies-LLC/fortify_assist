# Home Assistant voice / Assist internals (for a replacement conversation agent or pipeline)

Research date: 2026-10-24. Sources are official docs plus the `dev` branch of home-assistant/core as fetched on that date. Where a fetch tool summarized a file rather than quoting it, details are marked "(summarized)". Items I could not confirm are marked UNVERIFIED. The dev branch is ahead of the 2026.9 release, so some details may not yet be in a stable release.

Note: developer docs moved from developers.home-assistant.io/docs/... to **contributors.home-assistant.io/core/...** (301 redirects).

---

## 1. Assist pipeline architecture

Source: https://contributors.home-assistant.io/core/internals/assist-pipelines/

- A pipeline chains four stages: wake word, STT, intent/conversation, TTS. Each stage is a separate integration with its own entity platform: `wake_word`, `stt`, `conversation`, `tts`. The `assist_pipeline` integration runs the chain. A device may skip wake word if it detects it locally.
- Pipelines are configured under Settings > Voice assistants. A pipeline stores a language and an engine and language per stage. Fields in `PIPELINE_FIELDS` (core `assist_pipeline/pipeline.py`):
  - Required: `conversation_engine`, `conversation_language`, `language`, `name`.
  - Nullable: `stt_engine`, `stt_language`, `tts_engine`, `tts_language`, `tts_voice`, `wake_word_entity`, `wake_word_id`.
  - Optional: `prefer_local_intents` (bool), `acknowledge_media_id`.
  - `conversation_engine` defaults to `conversation.home_assistant` (the built-in agent).
- Special case: a pipeline ID starting with `conversation.` is synthesized on the fly from that conversation entity (`_async_get_pipeline_from_conversation_entity`). The entity ID is the pipeline ID. This is a cheap way to target your agent without a stored pipeline.
- Files in `assist_pipeline/`: `pipeline.py`, `run.py`, `runtime.py`, `default_pipeline.py`, `models.py`, `vad.py`, `audio_enhancer.py`, `websocket_api.py`, `select.py`, `error.py`, and others.

### Websocket API (non-satellite clients)
`assist_pipeline/run`:
- `start_stage` and `end_stage` are required (`wake_word`, `stt`, `intent`, `tts`).
- `input`: `sample_rate` is required when starting at wake_word or stt. `text` is required when starting at intent or tts. Wake-word starts also accept `noise_suppression_level` (0-4), `auto_gain_dbfs` (0-31) and `volume_multiplier`.
- Optional: `pipeline` (ID), `conversation_id`, `device_id`, `timeout` (default 300 s).
- Related commands: `assist_pipeline/pipeline/list`, plus `conversation/agent/list`, `conversation/prepare` and `conversation/agent/homeassistant/language_scores`.

Events (each carries the run `id`):
- `run-start`: includes `stt_binary_handler_id` and the TTS output URL and token. `run-end` and `error` are also always possible.
- `wake_word-start` / `wake_word-end`
- `stt-start`, `stt-vad-start`, `stt-vad-end`, `stt-end`
- `intent-start`, `intent-progress` (streaming agents only), `intent-end`
- `tts-start`, `tts-end`

Error codes: `wake-engine-missing`, `stt-provider-missing`, `tts-not-supported`, `wake-stream-failed`, `stt-stream-failed`, `intent-failed`, `stt-no-text-recognized`, `intent-agent-not-found`, `intent-not-supported`, `cloud-auth-failed`, `duplicate_wake_up_detected`, `wake-word-timeout`, `timeout`. On timeout, `run-end` arrives before `error`.

Audio transport: binary websocket frames. The first byte is `stt_binary_handler_id`, followed by 16-bit mono PCM. HA resamples to 16 kHz. A frame with only the ID byte marks end of audio.

### Intent stage run-time flow (`default_pipeline.py`, `_DefaultPipelineProcessor.recognize_intent`, summarized)
This is the most important code path for a replacement agent. Source: https://github.com/home-assistant/core/blob/dev/homeassistant/components/assist_pipeline/default_pipeline.py
1. If the previous turn returned `continue_conversation`, the same agent is reused for the next turn (`continue_conversation_agent`). Local-first handling is skipped for that follow-up.
2. `INTENT_START` is emitted with engine, language, text, conversation_id, device_id, satellite_id and the `prefer_local_intents` flag.
3. If the pipeline agent is NOT the built-in HA agent (and this is not a follow-up):
   - Sentence triggers run first and can override the agent.
   - If the agent has the `CONTROL` feature, the local filter only lets through state queries and media search-and-play intents (so local handling of "turn on X" is not used when an LLM with control is selected, apart from these).
   - If `prefer_local_intents` is on, `conversation.async_handle_intents` runs the built-in agent in strict mode. A match is executed locally and the agent is swapped to the HA agent. `processed_locally=True` is reported in `INTENT_END`.
4. Otherwise `conversation.async_converse(...)` is called with text, conversation/device/satellite IDs, context, language, agent_id and `extra_system_prompt`.
5. Streaming: if the TTS stream accepts streaming input, each chat-log delta emits `INTENT_PROGRESS` (`chat_log_delta`). Assistant text is buffered until about 60 chars (`STREAM_RESPONSE_CHARS`) or a tool call arrives, then `tts_start_streaming` is emitted and TTS consumes the text stream.
6. `INTENT_END` carries `processed_locally` and `intent_output`. If `continue_conversation` is true, the agent is remembered for the next turn. For HA-agent responses where all targets lie in the satellite's area, the satellite plays the acknowledge sound instead of full TTS.
7. Any unexpected exception becomes `IntentRecognitionError` (`intent-failed`).

### Satellites and Wyoming
- Satellites are `assist_satellite` entities (ESPHome voice devices, Voice PE, Wyoming satellites). The device only captures and plays audio; HA runs the pipeline on its behalf. Custom clients that are not satellite entities use the websocket API. Satellites also supply `satellite_id` and `device_id`, which flow into `ConversationInput` and are used for area-aware resolution.
- Wyoming (https://github.com/OHF-Voice/wyoming): peer-to-peer TCP protocol. Each message is a single JSON header line (`type`, optional `data`, `data_length`, `payload_length`), optional extra JSON, optional binary payload (PCM). No authentication or encryption by design. Event families: `describe`/`info`, `audio-start/chunk/stop`, `transcribe`/`transcript` (+ streaming `transcript-*`), `synthesize` (+ streaming `synthesize-*`), `detect`/`detection`/`not-detected`, `voice-started/stopped`, `recognize`/`intent`/`not-recognized`, `handled`/`not-handled` (+ streaming), `played`, satellite events (`run-satellite`, `pause-satellite`, `run-pipeline`, `streaming-started/stopped`, ...), timers, `user-event`.
- HA's Wyoming integration (https://www.home-assistant.io/integrations/wyoming/) connects to Whisper, Piper, Speech-to-Phrase, openWakeWord, either as apps on the HA host or on another machine. Services are discovered via zeroconf or entered as host:port. Wyoming defines `intent` and `handle` service types, but HA's pipeline uses the `conversation` entity platform for the intent stage, not Wyoming. A replacement agent therefore belongs on the HA side as a `ConversationEntity`, not as a Wyoming service. Wyoming remains the right place for custom STT/TTS/wake engines.

---

## 2. Conversation agent API

Sources: core `conversation/entity.py`, `models.py`, `chat_log.py`, `agent_manager.py`, `openai_conversation/conversation.py` (all on `dev`). REST and websocket docs: https://contributors.home-assistant.io/core/internals/conversation-api/. Entity docs: https://contributors.home-assistant.io/core/entity/conversation/ returned 404; the code is the reference.

### Data classes (`conversation/models.py`)
```python
@dataclass(slots=True)
class ConversationInput:
    text: str; context: Context; conversation_id: str | None
    device_id: str | None; satellite_id: str | None
    language: str; agent_id: str
    extra_system_prompt: str | None = None

@dataclass(slots=True)
class ConversationResult:
    response: intent.IntentResponse
    conversation_id: str | None = None
    continue_conversation: bool = False
```
`AbstractConversationAgent` has abstract `supported_languages` and `async_process`, plus no-op `async_reload(language)` and `async_prepare(language)`.

### ConversationEntity (`conversation/entity.py`)
- `class ConversationEntity(RestoreEntity)`. State is the timestamp of the last activity (the `state` property is `@final`).
- Class attrs: `_attr_supported_features = ConversationEntityFeature(0)`, `_attr_supports_streaming = False`.
- Abstract: `supported_languages -> list[str] | Literal["*"]`.
- Call chain: `internal_async_process` (final, sets last activity) -> `async_process` -> opens `async_get_chat_session(hass, conversation_id)` and `async_get_chat_log(hass, session, user_input)` -> `_async_handle_message(user_input, chat_log) -> ConversationResult` (the method you implement).
- `async_prepare(language)` is a hook to warm intents or models.
- Feature flag: `ConversationEntityFeature.CONTROL` marks agents that can control HA. The pipeline uses it for the local-intent filter above.

### Minimal agent shape
```python
class MyAgent(conversation.ConversationEntity):
    _attr_supports_streaming = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL
    @property
    def supported_languages(self): return conversation.MATCH_ALL   # "*"
    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)    # as in openai_conversation
    async def async_will_remove_from_hass(self):
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()
    async def _async_handle_message(self, user_input, chat_log):
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN), opts.get(CONF_LLM_HASS_API),
                opts.get(CONF_PROMPT), user_input.extra_system_prompt)
        except conversation.ConverseError as err:
            return err.as_conversation_result()
        await self._run_model_loop(chat_log)
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
```
The `async_set_agent` call matches the OpenAI integration (summarized). Agents are looked up by ID in `agent_manager.async_get_agent`: `None` or the built-in ID gives the default agent; an ID containing "." is looked up as a conversation entity.

### ChatLog (`conversation/chat_log.py`)
- Content types: `SystemContent`, `UserContent` (with attachments), `AssistantContent` (`agent_id`, `content`, `thinking_content`, `tool_calls: list[llm.ToolInput]`, opaque `native`), `ToolResultContent` (`tool_call_id`, `tool_name`, `result: llm.ToolResult`).
- `async_provide_llm_data(llm_context, user_llm_hass_api, user_llm_prompt, user_extra_system_prompt)`: resolves the LLM API(s) and builds the system prompt: instructions prompt (Jinja template with `ha_name`, `user_name`, `llm_context`; default `llm.DEFAULT_INSTRUCTIONS_PROMPT`), then `llm_api.api_prompt`, then `DATE_TIME_PROMPT` (unless a `GetDateTime` tool exists), then the extra system prompt. Raises `ConverseError` on template or API failure.
- `async_add_assistant_content(content)`: async generator that runs internal tool calls through `llm_api.async_call_tool` and yields `ToolResultContent`. `HomeAssistantError` and schema errors become `ToolResult(error=True)` data given back to the model; other exceptions propagate. Tool calls are awaited in order.
- `async_add_delta_content_stream(agent_id, stream)`: consumes provider deltas, starts tool tasks as soon as a call appears, emits to the delta listener (used by the pipeline for `intent-progress`).
- `continue_conversation` property: true when the last assistant text ends in `?`, `;` (Greek) or fullwidth `?`. `async_get_result_from_chat_log` copies this into `ConversationResult.continue_conversation`. Continue-conversation is therefore heuristic: end your reply with a question mark to have the satellite re-open the mic.
- Conversation ownership: the first user to send input owns the `conversation_id`; another user continuing it raises `Unauthorized`. Chat sessions expire; logs are only saved if an assistant message was added.
- `async_update_llm_data` is deprecated (flagged for removal in 2026.1); use `async_provide_llm_data`.
- Reference implementations to copy: `openai_conversation`, `anthropic`, `google_generative_ai_conversation`, `ollama`, `openrouter`, all using a shared base entity with `_async_handle_chat_log`.

### Config flows and subentries for LLM agents
- Modern LLM integrations keep credentials/connection in the config entry and each agent as a **config subentry** of type `conversation` (also `ai_task_data`). `async_setup_entry` iterates `config_entry.subentries.values()`, filters `subentry.subentry_type == "conversation"`, creates one entity per subentry, and calls `async_add_entities([...], config_subentry_id=subentry.subentry_id)`.
- Per-agent options (model, prompt, `CONF_LLM_HASS_API` list, temperature, etc.) live in `subentry.data`. `_attr_supported_features` gets `CONTROL` only if an LLM API is selected.
- A `ConfigSubentryFlow` subclass is registered through `async_get_supported_subentry_types` on the main ConfigFlow. The user can then add several agents, each with different prompt/model/API, under one account. This is useful for a "fast" and a "smart" agent.
- `llm_hass_api` options are chosen from `llm.async_get_apis(hass)` (a selector with multiple selection). The value is a list of API IDs.

---

## 3. Built-in intent system

Sources: https://contributors.home-assistant.io/translate/voice/how-assist-understands/, https://github.com/OHF-Voice/intents, core `conversation/default_agent.py`, https://www.home-assistant.io/voice_control/custom_sentences/

- Matching engine: **hassil** (template matcher). Syntax: `(a | b)` alternatives, `[optional]`, `{slot}` list references, `<expansion_rule>`, numeric ranges, wildcards (`{x}` as wildcard in triggers), permutations.
- Data: OHF-Voice/intents repo, shipped as the `home-assistant-intents` pip package. Layout: `sentences/<lang>/<domain>_<intent>.yaml`, `responses/<lang>/`, `tests/<lang>/`, `lists/`, `rules/`, `shared/`, `intents.yaml` (the language-independent intent list), `languages.yaml`. The `intentfest` CLI validates data and counts permutations.
- Intents have a `Hass` prefix: `HassTurnOn`, `HassTurnOff`, `HassToggle`, `HassLightSet` (brightness, color), `HassClimateSetTemperature`, `HassClimateGetTemperature`, `HassGetState`, `HassSetPosition`, `HassOpenCover`/`HassCloseCover`, media intents (`HassMediaPause`, `HassSetVolume`, `HassMediaSearchAndPlay`), `HassBroadcast`, timers (`HassStartTimer`, ...), shopping list/todo, `HassRespond`, etc. Slots include `name`, `area`, `floor`, `domain`, `device_class`, `state`, `color`, `brightness`, `temperature`.
- Handlers are registered with `intent.async_register(hass, handler)` in the `intent` helper (`homeassistant/helpers/intent.py`) and in integrations (e.g. light, climate, cover, timers in `components/intent/`). `intent.async_handle(hass, platform, intent_type, slots, text_input, context, language, assistant, device_id)` is the programmatic entry. Slot values are `{"value": ...}` (and `text` for the original text).
- **Target resolution** is in `intent.async_match_targets`/`MatchTargetsConstraints`: name (with aliases), area (+ aliases), floor (+ aliases), domain, device_class, state, `assistant` (exposure). Preferences `preferred_area_id` and `preferred_floor_id` (from the satellite's area/floor) break ties. Failure raises `MatchFailedError` with a reason (`NAME`, `AREA`, `FLOOR`, `DOMAIN`, `DEVICE_CLASS`, `STATE`, `ASSISTANT`, `DUPLICATE_NAME`, `INVALID_AREA` ...), which the default agent maps to a spoken error (`no_valid_targets`, duplicates handled by `_get_duplicate_response`).
- **Recognition stages in `DefaultAgent._recognize`** (summarized): LRU cache (128) keyed on text, language, satellite, device -> strict hassil match over exposed entity names -> strict match over all entity names (including unexposed, so it can say "that entity isn't exposed") -> `_recognize_unknown_names` ranking partial matches (most literal text matched, then most matched entities, fewer unmatched entities). The last step is heuristic scoring, not similarity matching.
- **New in dev: `gazetteer.py` / `GazetteerFallback`** (summarized): when hassil returns nothing, a second recognizer (external package `gazetteer_matcher`) tags spans of the sentence against a gazetteer built from floors, areas and exposed entities with aliases, tolerates "near-miss names", and remembers per-conversation targets for anaphora ("turn it off", "them"). It only applies to English dialects, only when the built-in agent answers on its own, and is NOT used on the `prefer_local_intents` path (`async_handle_intents`). I could not find an official release note for it (UNVERIFIED; may be unreleased or recent). No official docs for "fuzzy matching" in Assist were found. The community project **hass-closest-intent** (https://community.home-assistant.io/t/hass-closest-intent-garbled-stt-output-in-actual-intent-out/1009765) is a third-party conversation agent that fuzzy-matches against expanded hassil intents.
- **Custom sentences**: YAML under `config/custom_sentences/<language>/*.yaml` is merged with built-ins (`_load_intents`, loaded in executor, metadata `METADATA_CUSTOM_SENTENCE`/`FILE`). They can add sentences to existing intents (lists `{name}`, `{area}` are available) or define custom intents; `conversation.reload` reloads. Custom intents are handled by `intent_script:` in configuration.yaml (action + `speech` template). Custom sentences are preferred over built-in matches in `_recognize_strict`. `response` blocks/templates can read slots and state.
- **Sentence triggers**: automation trigger `conversation` with `command:` sentences, supports `{slot}` wildcards exposed as `trigger.slots.*` and a "Set conversation response" action. They run before the intent matcher in the default agent and are checked first on the non-default-agent path in the pipeline (they can override the selected agent). Docs: https://www.home-assistant.io/voice_control/custom_sentences/ (UI only; YAML is in a separate tutorial).
- **Prefer handling commands locally**: a pipeline option (`prefer_local_intents`). With a non-default agent selected, the pipeline first tries `async_handle_intents` (strict hassil, `discard_match_errors` so match failures fall through to the LLM rather than speaking an error). Caveat from the code: when the selected agent has `CONTROL`, local handling is limited to state queries and media search-and-play per the pipeline filter (see section 1), so check on the stable release you target (UNVERIFIED which releases include this filter).
- Language support: `DefaultAgent.supported_languages` = `get_languages()`; per-language quality in `conversation/agent/homeassistant/language_scores`.

---

## 4. Entity exposure

Sources: https://www.home-assistant.io/voice_control/voice_remote_expose_devices/, https://www.home-assistant.io/voice_control/aliases/
- Exposure is per assistant (`conversation`, `cloud.alexa`, `cloud.google_assistant`), stored by the `homeassistant.exposed_entities` helper (`async_should_expose(hass, "conversation", entity_id)`, `async_expose_entity`, `async_get_assistant_settings`). Settings > Voice assistants > Expose manages it, with bulk expose.
- Defaults: entities in sensitive domains (locks, covers of type garage door, alarm panels, etc.) are not exposed by default; some domains are auto-exposed on first add. Exact rules are in `exposed_entities.py` (not fetched; UNVERIFIED).
- The built-in agent builds slot lists from exposed entities: `name` list (entity name + entity aliases), `area` list (name + aliases), `floor` list (name + aliases mapping to canonical name). Lists are rebuilt when the entity/area/floor/device registries or states change (`_listen_clear_slot_list`). Entity name tuples carry domain and device_class context.
- Aliases: entity aliases (exposure tab or entity settings), area aliases, floor aliases. Docs recommend full names including the area ("Living room lamp") plus short aliases ("Lamp"); with duplicate aliases Assist disambiguates by the spoken area. Labels: no Assist-specific doc found; labels are not part of the name/area/floor slot lists in the code reviewed (UNVERIFIED for other places).
- LLM side: `LLMContext.assistant` selects whose exposed set applies, so a custom API must respect it by calling `async_should_expose(hass, llm_context.assistant, entity_id)`.
- Satellite area: a satellite's area becomes intent context (`preferred_area_id`, `_make_intent_context`) so "turn on the light" resolves in the room.

---

## 5. LLM API (`homeassistant.helpers.llm`)

Sources: https://contributors.home-assistant.io/core/internals/llm-api/, core `helpers/llm.py`, `components/llm/`, `components/mcp_server/`, https://www.home-assistant.io/voice_control/exposing_scripts_to_llms/
- Registry: `llm.async_register_api(hass, api)` (returns an unregister callable), `async_get_api(hass, api_id | list, llm_context)`, `async_get_authorized_api_instance` (checks `requires_admin`), `async_get_apis` (discovery). Multiple IDs are combined by `MergedAPI` with `NamespacedTool` prefixes and per-API instruction paragraphs.
- Built-in IDs: `assist` ("Device control", scoped to exposed entities, no admin actions) and `homeassistant` (admin only: registries, logs, automations, config entries).
- Types: `API` (dataclass, `id`, `name`, `requires_admin`, `async_get_api_instance(llm_context) -> APIInstance`), `APIInstance(api, api_prompt, llm_context, tools, custom_serializer)`, `LLMContext(platform, context, language, assistant, device_id)`, `ToolInput(tool_name, tool_args, id, external)`, `ToolResult(data, error)`, `Tool` (`name`, `description`, `parameters` voluptuous schema, `annotations`, `integration`, `async_call(hass, tool_input, llm_context)`).
- `ToolAnnotations` mirrors MCP: `read_only`, `destructive`, `idempotent`, `open_world` (worst-case defaults). Tool names from platforms must be `<domain>__<name>`; non-prefixed names are deprecated (the code mentions removal around 2027.10).
- Tool sources for the Assist API:
  - `IntentTool` wraps registered intent handlers. It strips `preferred_area_id` and `preferred_floor_id` from the schema and injects them from the calling device's area/floor, so the model never sees them. Args become `{key: {"value": v}}` slots and call `intent.async_handle(... assistant=...)`. The result is the intent response dict with `language` and `card` removed.
  - Script tools: exposed scripts become tools (via `ActionTool`, named `script__<name>`, the script description and fields become the tool schema, `blocking=True, return_response=True`). Docs say without a description the LLM will not know when to use a script.
  - `llm.py` platform hook in integrations: `async_get_tools(hass, llm_context, api_id) -> llm.LLMTools(tools, prompt)`, evaluated per request. `components/llm/llm.py` contributes `llm__GetDateTime`.
  - `GetLiveContext` (read tool that returns current state of exposed entities) exists in the Assist API, according to the MCP integration and third-party agents; I could not locate the AssistAPI class source in `helpers/llm.py` in my fetch (it appears to live elsewhere in this version), so the exact prompt text and entity-context format are UNVERIFIED. Historically the Assist API prompt included a YAML list of exposed entities (names, area, aliases) in the system prompt, with state moved to a tool call (`GetLiveContext`) to keep the prompt cacheable.
- The MCP Server integration exposes any registered API at `/api/mcp/<API ID>` (Streamable HTTP; non-Assist APIs require an admin token) and the default at `/api/mcp`. Its `StatelessAssistAPI` subclass omits entity state from the prompt. A newer websocket command `llm/api/list` lists registered APIs (admin only). HA can also be an MCP client via the `mcp` integration, whose remote tools appear as an LLM API.
- Custom API sketch:
```python
class MyAPI(llm.API):
    async def async_get_api_instance(self, ctx: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(api=self, api_prompt="...", llm_context=ctx, tools=[MyTool(), ...])
unreg = llm.async_register_api(hass, MyAPI(hass=hass, id="my_api", name="My API"))
```
  Build the tool list per request so entities/areas can be constrained to exposed ones and to the request's area.

### Known failure modes for LLM control (from docs/community, mostly non-official)
- Too many exposed entities: the Ollama docs recommend fewer than 25 entities when experimenting with local models and note smaller models are more error-prone. Context default 8k in HA vs 2k in Ollama server (https://www.home-assistant.io/integrations/ollama/).
- Small local models: weaker tool calling, break conversations when control is on; only tool-capable models work. Workaround in docs: two agents, one with control, one without.
- Wrong or hallucinated entity: models passing invented names/IDs; mitigation is constraining tool schemas to the real current entity list (third-party advice, https://www.promptquorum.com/smart-home/reducing-llm-hallucinations-home-automation).
- Ambiguity: duplicate names ("light") across areas; mitigated by unique names/aliases and area context.
- Poisoned history: earlier assistant turns that claimed actions without tool calls teach small models to do the same (https://newreleases.io/project/github/goruck/home-generative-agent/release/v3.33.3).
- Scripts without descriptions are invisible to the model.
- Tool errors are fed back as `ToolResult(error=True)` so the model can retry, but the loop has no built-in validation of whether the action actually changed state.

---

## 6. Hooks for reliability

All of these are patterns derived from the code above, not an official guide.
- **Deterministic first, LLM second** is already supported: set the LLM agent as the pipeline's conversation agent and enable `prefer_local_intents`. Strict hassil handles whatever it can, discarding match errors so failures fall through. Pitfalls: local handling is strict (STT noise breaks it), a CONTROL-capable agent narrows what is tried locally (check your release), and non-pipeline callers (`conversation.process`, REST) do not get this behavior; it lives in the pipeline, not the agent.
- **Do it inside your own agent** for full control. In `_async_handle_message`, call `conversation.async_handle_intents(hass, user_input)` yourself (or `intent.async_handle`) or `async_converse(agent_id="conversation.home_assistant")` first, inspect `response.response_type` and `error_code` (`no_intent_match`, `no_valid_targets`, `failed_to_handle`), then decide whether to fall back to the LLM. The default agent also supports sentence triggers and custom sentences, so this reuses all user config.
- **Validate tool calls**: wrap or replace `IntentTool`/`ActionTool` with your own `llm.Tool` subclasses. Check `entity_id`/name against `async_should_expose` and the registries, resolve names with `intent.async_match_targets` or area/floor helpers (rather than trusting model-supplied IDs), reject or return a `ToolResult(error=True, data={"error": ..., "candidates": [...]})` listing valid options. After the call, read the state back (`hass.states.get`) and report real state to the model/user.
- **Disambiguation**: return candidate lists from a tool error and let the model ask; set a final assistant message ending in `?` so `continue_conversation` re-opens the mic; keep `conversation_id` stable so the follow-up lands in the same `ChatLog`. The pipeline reuses the same agent for the follow-up turn.
- **Confirmation**: use `ToolAnnotations(destructive=True)` plus an in-agent policy (locks, garage, alarm, bulk actions) that returns a question instead of executing, storing pending action in the chat log or your own per-`conversation_id` state; execute on "yes".
- Use `extra_system_prompt` (set by the satellite/pipeline caller) and `LLMContext.device_id`/`satellite_id` to supply room context.

---

## 7. Recent changes (2025-2026) that matter

- **AI Task** integration (2025.8): `ai_task` platform with `async_generate_data` style entities, structured output, attachments, later image generation (2025.10); LLM integrations expose these as `ai_task_data` subentries. Useful for non-conversational one-shot LLM calls (e.g. classification or intent repair) without a chat session. Dev docs page for it returned 404 so API specifics are UNVERIFIED. Sources: https://www.notebookcheck.net/Home-Assistant-2025-10-arrives-with-new-AI-image-generation-tool.1129869.0.html
- **Conversation subentries**: one config entry, multiple agents (see section 2).
- **Streaming**: `_attr_supports_streaming`, `ChatLog.async_add_delta_content_stream`, `intent-progress` events and TTS streaming (text streamed to TTS after about 60 chars). Android companion 2026.1.x added streaming TTS.
- **Satellite**: multiple wake words per device (late 2025), `acknowledge_media_id`, `satellite_id` in `ConversationInput`.
- `prefer_local_intents`, `llm/api/list` websocket (2026.9 changelog: https://www.home-assistant.io/changelogs/core-2026.9/), `ToolResult`/`ToolAnnotations` requirements, namespaced tool names, and deprecations (`async_update_llm_data`, dict tool returns, `ToolResultContent.tool_result`) all tighten the contract for custom integrations.
- Gazetteer fallback in the default agent (English only, dev branch; see section 3). Timeline unknown.
- Release list seen: 2026.1 to 2026.8 on https://home-assistant.io/blog/categories/release-notes/ ; I did not read individual 2026 notes, so other Assist changes may exist.

---

## Implications for building a replacement pipeline

**Key extension points (in order of least to most invasive)**
1. **Custom `ConversationEntity` in a HACS integration** (recommended). Implement `_async_handle_message`; register with `async_set_agent`; set `CONTROL` and `supports_streaming` as needed; select it in any pipeline or satellite. Gives you `ChatLog`, tool execution, streaming, `continue_conversation`, and conversation ownership for free.
2. **Custom `llm.API`** registered with `llm.async_register_api`, selectable by other agents (OpenAI, Ollama, etc.) and exposed over MCP at `/api/mcp/<id>`. Put the reliability layer (validation, area-aware resolution, confirmation) here and every LLM agent benefits.
3. **`llm.py` platform hook** (`async_get_tools`) to add tools to the existing Assist API.
4. **Calling the built-in agent from yours** (`async_handle_intents`, `async_converse` with the HA agent, `intent.async_handle`) for the deterministic-first layer.
5. **Custom sentences / `intent_script` / sentence triggers** to extend deterministic coverage without code.
6. **Config subentries** for multiple agent profiles (fast/local vs strong/cloud).
7. **Wyoming services** (custom STT/TTS/wake) if you need a replacement for the audio stages, and the `assist_pipeline/run` websocket for non-satellite clients. The pipeline stage order is fixed; you cannot insert extra stages between STT and the agent except by doing it inside your agent.

**Pitfalls**
- Only the pipeline implements `prefer_local_intents` and sentence-trigger override for non-default agents; direct `conversation.process`/REST callers bypass it. Replicate the logic inside your agent if you need it everywhere.
- A `CONTROL` agent narrows what the pipeline tries locally; behavior varies by release (verify on the HA version you target).
- `continue_conversation` is inferred from a trailing question mark; the follow-up turn skips local-first handling and goes straight to the same agent.
- Respect exposure: pass `llm_context.assistant` to every lookup; never expose all entities (context size, safety, small-model accuracy).
- Do not trust model-supplied entity IDs or names; resolve via registries and exposure, and return actionable errors as `ToolResult(error=True)`.
- Tool errors that are only `HomeAssistantError` are converted to results; other exceptions abort the turn.
- Tool naming and annotation contract is tightening (domain prefix `<domain>__name`, `integration`, `ToolResult`, annotations default to destructive/open-world). Return `ToolResult`, not dicts.
- Chat sessions are per-user (ownership) and expire; history poisoning in small models is real; keep tool-calling turns in the log consistent.
- Hassil is strict, STT noise defeats it; consider Speech-to-Phrase for Wyoming STT biased to your sentences, or your own fuzzy layer (e.g. hass-closest-intent approach) before escalating to an LLM.
- Dev-branch internals (gazetteer, `llm` integration layout, pipeline split into `default_pipeline.py`/`run.py`/`runtime.py`) are moving; pin your integration to documented public APIs (`ConversationEntity`, `ChatLog`, `llm` helpers, `intent` helpers) and test against each monthly release.
- UNVERIFIED items to confirm in source before building: exact AssistAPI prompt/entity-context format and `GetLiveContext` behavior, default exposure rules, label handling, exact release in which each behavior landed, AI Task entity API surface.

## Source list
- https://contributors.home-assistant.io/core/internals/assist-pipelines/
- https://contributors.home-assistant.io/core/internals/conversation-api/
- https://contributors.home-assistant.io/core/internals/llm-api/
- https://contributors.home-assistant.io/translate/voice/how-assist-understands/
- https://www.home-assistant.io/voice_control/aliases/
- https://www.home-assistant.io/voice_control/custom_sentences/
- https://www.home-assistant.io/voice_control/voice_remote_expose_devices/
- https://www.home-assistant.io/voice_control/exposing_scripts_to_llms/
- https://www.home-assistant.io/integrations/conversation/
- https://www.home-assistant.io/integrations/wyoming/
- https://www.home-assistant.io/integrations/ollama/
- https://github.com/OHF-Voice/wyoming
- https://github.com/OHF-Voice/intents
- https://github.com/home-assistant/core/tree/dev/homeassistant/components/conversation (entity.py, models.py, chat_log.py, agent_manager.py, default_agent.py, gazetteer.py)
- https://github.com/home-assistant/core/tree/dev/homeassistant/components/assist_pipeline (pipeline.py, run.py, default_pipeline.py)
- https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/llm.py
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/openai_conversation/conversation.py
- https://community.home-assistant.io/t/hass-closest-intent-garbled-stt-output-in-actual-intent-out/1009765
- https://www.home-assistant.io/changelogs/core-2026.9/
