# Alternatives and add-ons to Home Assistant Assist for accurate voice/NL device control

Research date: 2026-10-24. Sources are web search/fetch results; where a fact could not be confirmed it is marked **(unverified)**. GitHub fetches often did not expose last-commit dates, so "activity" is given as commit count/stars/issues/release info instead. Vendor and author accuracy claims are self-reported unless stated otherwise.

## 1. Baseline: how built-in Assist works (and why it "fails")

- Default agent = **hassil** template matcher: user text is matched against per-language sentence templates (alternatives, optional words, slot lists filled with exposed entity/area/floor names and aliases). Deterministic, fast, local. ([developer docs](https://developers.home-assistant.io/docs/voice/intent-recognition/), [hassil](https://github.com/home-assistant/hassil))
- Speech-to-Phrase (STT) restricts recognition to known templates plus exposed entity names. Fast on weak hardware but only recognizes supported patterns. ([OHF-Voice/speech-to-phrase](https://github.com/OHF-Voice/speech-to-phrase), [popularai guide](https://www.popularai.org/p/local-alexa-alternative-home-assistant-ollama))
- Failure mode: anything outside the templates (paraphrase, filler words, STT errors, unaliased names) returns "no match." One benchmark (below, author-run) measured only 49.6% for direct HassIL on a deliberately hard corpus.
- LLM agents (OpenAI/Anthropic/Google/Ollama) use HA's `llm.py` API: exposed entities, an area/device overview and intent tools are put into the prompt; the model emits tool calls (HassTurnOn etc.) that HA executes. Docs say: expose the minimum, give good aliases, get domains/device classes right, write script descriptions (scripts become tools; hard cap of 128 scripts/tools). ([best practices](https://www.home-assistant.io/voice_control/best_practices), [exposing scripts](https://www.home-assistant.io/voice_control/exposing_scripts_to_llms/))
- Ollama integration: entity names/states and tool definitions are sent on every request; small context windows silently truncate the prompt. Community reports: ~30 entities is ~1,300 tokens; ~40 devices reached ~6,000 tokens vs Ollama's default 2048 window; recommended 25-50 exposed entities for 7-8B models (heuristics, sources disagree). ([HA Ollama docs](https://home-assistant.io/integrations/ollama), [community thread](https://community.home-assistant.io/t/ollama-function-calling/757999), [bestllmfor](https://bestllmfor.com/guide/llm-local-domotique-vie-privee-smart-home/), [NAS test](https://book.st-hakky.com/en/news/nas-local-llm-smart-home-1-of-7))
- Known pipeline bug: with "Prefer handling commands locally", the pipeline can stick in follow-up mode after a misheard command (core issue [#162768](https://github.com/home-assistant/core/issues/162768), core 2026.2.1).

## 2. Sophia NLU and similar NLU engines

### Sophia NLU ("Sophia: Home Assistant Edition") - exists publicly
- Commercial, closed-source **Rust, non-LLM NLU engine** run as a separate local service (24 MB binary, ~160-180 MB RAM, ~15 ms latency claimed). Claims: multiple intents per message, clarification requests, state persistence, custom vocabulary (106k-word vocabulary), 12 "personalities", fully deterministic, "zero hallucinations." ([balena forum post, 2026-06-08](https://forums.balena.io/t/on-device-low-compute-nlu-engine-for-voice-control/378092), [nlu.to/ha](https://nlu.to/ha/))
- Pricing: 14-day trial, then $79.95 lifetime (12 months free upgrades, then optional $19.95/yr). Author contact in the post: matt@aquila-labs.ca.
- Self-reported accuracy: **99.0%** on its own open-source test suite comparing Sophia vs LLMs vs built-in Assist (suite said to be runnable by users; I did not locate/run it).
- Not documented anywhere I could find: how it discovers/maps HA entities, how it handles unknown phrasings, license terms beyond commercial.
- Plausible reasons it could fail for a user (inference, not documented): (a) NLU/grammar systems are closed-world: unknown device names, nicknames, or phrasing outside its lexicon get rejected or mis-slotted; (b) entity mapping depends on HA names/aliases/areas being clean; (c) "deterministic" is not "correct" - a deterministic mis-parse repeats every time; (d) young product (first public post June 2026), single developer, no independent evaluation; (e) fallback behavior when it declines is unknown.

### "Clara NLU" - not found
- No public project, repo, forum post or docs found under that name for Home Assistant, so it is excluded from the comparison. The closest similarly named public project is **Klar! NLU** (below). General failure modes of classical intent+slot NLU engines (closed-world vocabulary, entity-slot resolution, no fallback, no eval set) are listed in section 5.

### Klar! NLU (similar-sounding, public)
- Free, MIT-licensed local conversation engine for Assist: HACS integration (agent) + separate app/add-on (engine, "Mapping/Lab"); multi-intent, follow-ups, clarification when ambiguous, 60+ languages. Covers lights, climate, covers, locks, fans, media, timers, lists, scenes. ([HA forum post](https://community.home-assistant.io/t/klar-nlu-assist-alternative-with-multi-intent-follow-ups-60-languages/1021843); page returned 403 to direct fetch, details from search snippet.) No benchmarks seen.

## 3. Comparison table

| Project | Type | Entity resolution | Execution | Reported accuracy / latency | Local/Cloud | License | Maturity |
|---|---|---|---|---|---|---|---|
| **HA Assist (hassil)** | Template NLU | Exact/alias match over exposed names, areas, floors | HA intent handlers | 49.6% on Canonicalizer's hard corpus (author-run); instant | Local | Apache-2.0 | Core, very active |
| **HA Assist + LLM (OpenAI/Anthropic/Google/Ollama)** | LLM tool-calling over HA llm API | Model reads exposed entity list in prompt | Intent tools / scripts | No official benchmark. Anecdotes: 3B ~80%, qwen3:4b ~70%, frontier near 100% (informal) | Either | Apache-2.0 | Core; Ollama control flagged experimental |
| [**Home-LLM** (acon96)](https://github.com/acon96/home-llm) | Fine-tuned small LLMs (Home-Llama-3.2-3B, Home-FunctionGemma-270m) + integration; also works with any LLM backend | Entities in prompt; trained on Home-Assistant-Requests-V2 | HA chat/tool API; llama.cpp, Ollama, OpenAI-compatible | No benchmarks on page; 1.5k stars, 741 commits, v0.5.0 (needs HA 2026.5+), 41 open issues | Local (cloud optional) | Not stated on page (unverified) | Active, mature community project |
| [**Extended OpenAI Conversation** (jekalmin)](https://github.com/jekalmin/extended_openai_conversation) | LLM + user-defined functions (execute_service, script, template, REST, SQLite, composite) | Exposed entities in prompt; model composes service calls | Arbitrary HA services (model picks domain/service/entity_id) | None; 1.5k stars, 267 commits, 106 open issues; tools-not-passed reports ([#174](https://github.com/jekalmin/extended_openai_conversation/issues/174)) | OpenAI-compatible (local possible) | MIT | Older, largely superseded by core LLM API; SQLite queries not secured |
| [**Custom Conversation** (michelle-avery)](https://github.com/michelle-avery/custom-conversation) | Hybrid: Assist first, then LLM via LiteLLM | Same as Assist / LLM | HA | None; 98 stars, 261 commits; Langfuse tracing + thumbs up/down scoring | Either | MIT | Small but well-engineered; e2e tests present |
| [**Assist Canonicalizer**](https://github.com/luuquangvu/assist-canonicalizer) | Deterministic-first + fuzzy lexical rewrite (RapidFuzz + char n-grams + BM25 + intent context) + confidence/margin gate + fallback agent | Index of built-in intents, custom sentences, entity names/aliases, areas, floors | Rewrites to canonical sentence, executes via HA flow; preflight check; one retry | **93.3% vs 49.6% HassIL** on own corpus (about half challenge cases); P50 85 ms / P95 252 ms cold; accuracy gated for DE/EN/FR/NL/VI | Local, no LLM | MIT | New; 56 stars, 127 commits; [forum](https://community.home-assistant.io/t/assist-canonicalizer-improve-voice-assistant-accuracy-for-natural-device-control-with-instant-local-feedback/1014170) |
| **Sophia NLU** | Closed Rust NLU service | Undocumented | Via HA agent integration | 99.0% self-reported on own suite; ~15 ms | Local | Commercial $79.95 | New (June 2026), single dev |
| **Klar! NLU** | Local NLU engine + HACS agent | Mapping UI | Assist intents | None seen | Local | MIT | New |
| [**ha-mcp**](https://github.com/homeassistant-ai/ha-mcp) | MCP server exposing 87 HA tools (control, search, config, history, logs) | Search/registry tools let the agent look entities up instead of relying on prompt | Direct service calls, bulk control | None (tool server, not NLU); 5.2k stars, 3,161 commits, v9.x | Local server; client LLM anywhere | MIT | Very active, mature. Large blast radius (can edit automations etc.) |
| **HA built-in MCP server** | Official MCP server exposing Assist API to external agents | Exposed entities only | Intent tools | n/a | Local | Apache-2.0 | Core (not researched in depth) |
| [**Hermes Agent** (Nous) + hermes-voice-ha-integration](https://github.com/rusty4444/hermes-voice-ha-integration) | General autonomous agent; HA tools: entity search, state lookup, service call, scene discovery ([tool source mirror](https://gitcode.com/brxiz/hermes-agent/blob/main/tools/homeassistant_tool.py)) | Search tool then call | Service calls with blocked-domain list, optional allow-list, JSONL audit log | None | Local or cloud model | MIT | Hermes launched Feb 2026; HA bridge v0.0.16, 81 stars; "room-grade voice UX still needs work" |
| **OpenCode** | Coding agent; could use HA via MCP (ha-mcp) | Same as ha-mcp | Same | No HA-specific project found | - | - | Not a voice-control framework; unverified |
| [**Willow**](https://community.home-assistant.io/t/willow-voice-assistant/572004) | ESP32-S3-BOX voice front-end with self-hosted inference server | Delegates to HA | - | Latency-focused | Local | Open | Reportedly discontinued (single forum comment; unverified) |
| [**OVOS / OpenVoiceOS**](https://blog.openvoiceos.org/posts/2025-09-17-ovos_ha_dream_team) | Full voice OS; Wyoming STT/TTS/wake-word bridge to HA | HA skill | - | n/a | Local | Open | Community/grant-funded, "not plug-and-play" |
| **Rhasspy 2.x/3** | Older offline voice toolkit (template NLU); Piper/Wyoming survive under OHF | - | - | - | Local | MIT | Superseded by Assist; Rhasspy 3 was a dev preview |
| **n8n / Node-RED agents** | Workflow glue around LLM+HA | Custom | Custom | Nothing HA-specific found | - | - | Not researched deeply |
| **LLM Vision** | Image/video analysis, not device control | n/a | n/a | n/a | Either | - | Out of scope |

## 4. Published evaluation data (thin)

- **Only real benchmark artifacts found:**
  - [Drizzt321/ha-voiceagent-llm-benchmark](https://github.com/Drizzt321/ha-voiceagent-llm-benchmark): Inspect AI harness; NDJSON test cases + YAML entity inventory; sends an HA-style system prompt + tool defs to a llama.cpp server and scores whether the correct intent tool call with correct arguments was emitted (tier-1 scorer, C/I/N scoring). Does not run the full HA pipeline. 53 commits, no published results seen. Good template for our own harness.
  - Assist Canonicalizer's built-in corpus (93.3% vs 49.6%), author-run, about half adversarial.
  - Sophia's "open-source test suite" (99.0%), vendor-run; not located.
- Informal blog tests: 24 spoken commands with recommended models at 71-96% ([privacysmarthome](https://www.privacysmarthome.com/guides/home-assistant-2026-8-local-ai-voice-ready-replace-cloud-2026/)); an older cloud-LLM vs local-voice test of 19 prompts (11/19 vs 7/19); other blog numbers (e.g. "94% for Qwen3 8B") have no methodology and should be ignored. Others: [XDA](https://www.xda-developers.com/local-llms-changed-how-i-use-home-assistant/), [techfuelhq](https://techfuelhq.com/tutorials/home-assistant-local-llm-voice-2026/).
- **No standard public HA voice-control accuracy dataset exists.** Home-Assistant-Requests-V2 (Home-LLM's training set) is the closest public data but is a training set, not a held-out eval. HA's intents repo (sentence tests per language) is a good seed for deterministic cases.

## 5. Failure modes (observed or well-supported)

1. **Closed-world matching** (hassil, any grammar/NLU): paraphrase, filler words, STT errors, unaliased names give no match.
2. **Name ambiguity / duplicates**: several lights in one room, similar names; models guess instead of asking.
3. **Context overflow**: entity list + tools exceed the context window (Ollama default 2048-8k) so the model never sees the target entity or tool schema; small models confuse similar devices beyond ~30-50 entities.
4. **Tool-call format errors**: small models choose the right function but emit malformed JSON/tool syntax (Home-LLM releases keep fixing tool-call parsing; small-model Ollama tests report the same). Tools not passed at all in some Extended OpenAI reports.
5. **Hallucinated or wrong-entity calls** (nonexistent IDs, wrong domain/service) when the model composes raw service calls rather than picking from constrained intents.
6. **Say-it-did-it**: model replies "done" without a tool call or after a failed call; nothing verifies state.
7. **Wrong STT upstream**: Whisper mangling entity names; Speech-to-Phrase avoids this but restricts phrases.
8. **Bad HA hygiene**: wrong domains/device classes, missing aliases, scripts without descriptions, over-exposure.
9. **Fallback design**: using the built-in agent as the fallback after it already rejected the text is pointless (Canonicalizer author); local-first can get stuck in follow-up state (core #162768).
10. **Unevaluated systems**: none of the NLU projects ship an independent regression suite, so "didn't work well" cannot be diagnosed. Plausibly why some NLU-only engines disappoint in practice (inference).
11. **Blast radius** of agent frameworks: service calls to shell_command/hassio/python_script (Hermes blocks these by default); SQLite queries in Extended OpenAI not constrained to exposed entities.

## 6. Design lessons for our own pipeline

1. **Deterministic-first, LLM-second, with an explicit confidence gate.** Order: (a) exact/alias/template match (hassil or our own) -> (b) fuzzy lexical rewrite with score and margin thresholds (Canonicalizer pattern; require the full margin when candidates differ in polarity, e.g. on vs off) -> (c) LLM only for the residue. Never run the LLM on commands the deterministic layer already resolved.
2. **Build our own entity index and retrieve, don't dump.** Index names, aliases, area/floor, domain, device class and synonyms from the registry. For each utterance retrieve top-k (BM25 + fuzzy + optionally embeddings) and give the LLM only 5-15 candidates, not the whole home. Fixes context overflow and similar-name confusion, and scales past 50 entities. Re-index on registry-change events.
3. **Constrain the output.** Small fixed tool schema (e.g. `control(entity_id in {retrieved ids}, action enum, params)`), enum the entity IDs from retrieval, use JSON-schema/grammar-constrained decoding for local models. Do not let the model write raw service calls or free-form entity IDs.
4. **Validate before executing.** Check entity exists and is exposed, domain supports the action, parameter ranges, polarity sanity, and a domain allow/deny list with an audit log (Hermes pattern). Reject and re-prompt once on schema failure, then ask the user.
5. **Verify after acting.** Read back state (subscribe to state change with a timeout) and only then confirm; report failures honestly.
6. **Disambiguate, don't guess.** If the top-2 candidates are close, or the action is sensitive (locks, garage, alarm), ask a short clarifying question and keep follow-up context (test it; Assist's follow-up has bugs).
7. **Split multi-intent utterances** into atomic actions before resolution and evaluate each separately.
8. **Fix upstream data cheaply.** Bias STT toward entity names (Speech-to-Phrase, or Whisper prompt with entity names), auto-suggest aliases from registry/usage, lint HA config (domains, device classes, missing areas, duplicate names).
9. **Build the eval harness first.** Gold set of (utterance, expected entity IDs + service + params) over our real inventory: paraphrases, typos/STT-style errors, duplicate/ambiguous names, multi-intent, negations, state queries, should-ask cases, and hard negatives that must NOT act. Score per stage (retrieval recall@k, tool-call exact match, end-to-end state-change success, false-action rate, clarification rate, latency P50/P95). Seed from the HA intents repo tests, Home-Assistant-Requests-V2 and the Inspect AI harness structure. Run in CI against a test HA instance so Sophia/Klar/Assist/LLM candidates (and Clara) can be compared on identical data, which also tests vendor claims of 99%/93%.
10. **Log and close the loop.** Trace each stage (Langfuse-style as in Custom Conversation), capture corrections, and promote corrected phrasings to aliases/templates (deterministic cache) so accuracy and latency improve over time.
11. **Model choice**: frontier models are near ceiling when given retrieved candidates; small local models (3-8B, Home-LLM fine-tunes, Qwen 2.5/3) are viable only with retrieval + constrained decoding + adequate context. Measure rather than trust blogs.
12. **Agent frameworks (Hermes, OpenCode + ha-mcp) are poor primary voice paths**: general agent loops are slow and broadly permissioned. Use them for authoring/diagnostics and long-tail tasks behind the same validation layer; keep the voice hot path narrow and typed.
13. **Targets**: deterministic path under 100 ms; retrieval+LLM path only on misses; cache resolved utterances.

## 7. Gaps / unverified

- Last-commit dates were not retrievable via page fetches for most repos; check git log / GitHub before depending on any of them.
- Not researched in depth: HA's native MCP server, Willow's current status, OVOS-HA skill quality, n8n/Node-RED LLM agents, Rhasspy 3 status, OpenCode+HA, LLM Vision (not control).
- Sophia's test suite and entity-resolution method; Klar's forum thread (403).
