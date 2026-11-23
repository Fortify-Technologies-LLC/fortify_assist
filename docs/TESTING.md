# How Fortify Assist was built and tested

Fortify Assist operates real things in a home: lights, locks, garage doors, alarms, heating. A voice
assistant that is "usually right" is not good enough there, so the project is built around one question:
**how often does it do the wrong thing, and can it ever do something dangerous without being asked?**
This page explains the design that follows from that question, every test suite, how to run them, how
accuracy is tracked over time, and how to add a test when you find a bug.

All examples use the synthetic test house in `eval/houses/synthetic.yaml`. No real household data is, or ever
should be, committed to this repository.

## 1. Design in one page

```
speech/text -> normalise -> rules (parse -> resolve -> validate) -> answer / action / question
                               |  can't place it
                               v
                       language model rewrites it as the same kind of request
                       (quotes your words, picks no device)
                               |
                               v
                       the same resolver, validator and confirmation policy
```

1. **Rules first.** A deterministic grammar and resolver handle commands and questions. They are fast
   (about a millisecond), repeatable and testable. The same sentence in the same house gives the same
   result every time.
2. **The resolver acts only on one clear match.** If two devices fit ("the lamp" exists in two rooms), it asks
   which one, using the room of the speaker when known. If nothing fits, it says so. It never picks "the
   closest" device when the choice is a guess.
3. **The validator is the last line of defence.** Every action is checked against live state before a service
   call is made: the entity must exist and be exposed, be available, support the service, and the value must be
   in range. A made-up entity, a wrong service or an impossible value never reaches Home Assistant, no matter
   which part of the system proposed it.
4. **Sensitive actions ask first.** Unlocking, disarming an alarm, opening or moving a garage door, gate or door
   (by device class, or by those words in its name or alias), switching a garage or gate switch, and opening any
   valve need a confirmation, worded with the real action ("unlock", "open", "move", "switch on"). The
   confirmation only counts when "yes" (or a plain equivalent such as "yes please" or "go ahead") is the whole
   reply. A new request in place of "yes" cancels the pending action. Sentences about unlocking, disarming or
   opening a garage, gate, door or lock are never handed to Home Assistant's built-in agent, so they cannot
   bypass the confirmation through the fallback.
5. **The language model is a fallback, never an authority.** It is only consulted for what the rules could not
   place. Its only job is to rewrite the sentence in simpler words, or to chat. It is never shown a device list,
   never names a device, never executes anything, and its output goes back through the same resolver, validator
   and confirmation policy. Requests with a "don't" or "except" are never sent to it, because a paraphrase that
   drops one word would do the opposite. Questions about device states are computed from live state, not
   generated.
6. **Honest replies, verified against state.** After an action the executor re-reads the device and says so
   when the device ignored the command, was unavailable or failed. One failing device does not stop the others.
   Answers such as counts, "which lights are on", brightness, volume and the security check are computed from
   state, not written by a model.
7. **Personalities only change wording.** Every phrasing in a personality pack must keep every fact (device
   names, states, numbers). Tests assert that decisions are identical under every personality.

## 2. Test layout

| Folder or file | What it is |
|---|---|
| `tests/` | The core engine and the accuracy harness. Pure Python (needs only `pyyaml` and `pytest`). No Home Assistant, no network. |
| `tests_ha/` | The Home Assistant integration, run inside a real Home Assistant core (config flow, services, diagnostics). |
| `eval/houses/synthetic.yaml` | The test house, full of traps: duplicate names in different rooms, near-identical names, aliases, unavailable and unexposed entities, locks, an alarm, a garage door. |
| `eval/corpus/*.yaml` | Hand-written labelled cases and held-out sets. |
| `src/fortify_eval/` | The harness: house loader, case generator, runner, scoring, baselines, the standing benchmark. |
| `benchmark/phrases.yaml`, `benchmark/history/`, `benchmark/REPORT.md` | The standing benchmark: fixed phrases, a saved result per version, and a side-by-side report. |

### 2.1 Core tests (`tests/`)

749 tests at the time of writing, in about 12 seconds.

**Core behaviour**

- `test_core.py`: text normalisation, shared names never acted on without context, room context, a partly failing multi-command runs nothing, confirmation (ask, yes, no, unrelated reply, expiry), clarifications, validator rejections.
- `test_room_wide.py`: several lights at once with no device names, whole rooms and floors, everyday phrasing, "except" and "don't".
- `test_light_sets.py`: lettered sets of lights ("Overhead Light A and B"), ceiling-word synonyms, names said in the speaker's own room.
- `test_controls.py`: colours and white tones, brightness steps, warmer/cooler, thermostat modes and setpoints, a light that cannot do the change says so, groups skip members that cannot.
- `test_plain_speech.py`: how people really talk: one word, a room alone, wishes and politeness, the command inside a longer sentence, comfort phrases.
- `test_followups.py`: "turn them off", "change them back", "and the kitchen", "do that again", with nothing to refer back to, expiry, and the assistant never denying it runs the home.
- `test_audit_fixes.py`: problems found by auditing everyday phrases (a new request never confirms an unlock, replies say what changed, time/date/sums are computed, hand-off for timers).
- `test_live_fixes.py`: regressions found in a run against a real home, reproduced in the synthetic house.
- `test_release_review.py`: findings of the pre-release review: a home with no rooms set ("the lights" means all
  of them; "all of them" and "everywhere" answer "which room?"), `unknown` state still controllable and
  `unavailable` refused, garage, gate and door covers, garage switches and valves ask first whatever their device
  class, confirmation wording, words that only sound like a device are not matched to it, stray letters and
  negative settings do nothing, and "playing songs by name needs Music Assistant".

**Devices and rooms**

- `test_media_rooms.py`: several TVs and speakers: which one is meant, whose volume, answers to "which one?".
- `test_apps.py`: opening an app on a TV, by package name or by title.
- `test_vacuums.py`: robot vacuums: "vacuum the kitchen" uses `vacuum.clean_area` on a robot with the clean-area
  feature on the same floor, a robot that can only clean a whole floor is started instead, a robot is never sent
  to another floor, an offline robot is said rather than swapped, "turn on/off the vacuum" starts it or sends it
  home, and a model can never turn a cleaning job into something else.
- `test_music_everyday.py`: music asked the way people say it at a speaker: no title, "down here", misheard numbers, which speaker.
- `test_music_mood.py`: mood and theme lighting, music requests, the chat honesty guard.

**Questions, chat and answers**

- `test_qa.py`: answers are computed from live state, singular and plural grammar, "no data" is admitted, unavailable and unexposed devices never appear.
- `test_chat.py`: chat prompt carries the persona and guardrails, history is bounded, graceful failure, Ollama and OpenAI-compatible client payloads, malformed server replies.
- `test_weather.py`: weather questions recognised narrowly and answered only from what the entity reports.
- `test_feeds.py`: RSS and Atom reading, question recognition, turn-taking between feeds, grounding of summaries (an invented name or number discards the summary).
- `test_persona.py`: shipped personalities are valid and complete, facts survive every personality, decisions do not change, identity answers.

**Language model tier**

- `test_llm.py`: pins down what the model can never do (invent devices, act, skip confirmation, change a "don't").
- `test_semantic.py`: intent mode: the model rewrites, the rules resolve; it must never name a device itself.

**Harness**

- `test_harness.py`: the corpus is large and deterministic, every referenced entity exists, expected actions never target unavailable or unexposed devices, scoring is right (acting when it should ask counts as wrong), baselines behave.

### 2.2 Home Assistant tests (`tests_ha/`)

- `test_integration.py`: setup flow with and without a language model, model picked from a server list, unreachable server shows an error, options flow reload, commands run real service calls and are verified, devices that ignore a command are reported, failing services are reported not raised, unexposed entities cannot be controlled, sensitive actions ask then run on yes, speaker room resolves shared names, new devices are picked up without restart, chat and persona, confirmation toggle, unload/reload, colour capabilities, logo, diagnostics hide secrets, Home Assistant's own Assist gets what the rules do not understand, crash is answered not raised, microphone is reopened only for real questions, recent requests survive a restart, room aliases.
- `test_feeds.py`: feed settings, fetching, feed entities and diagnostics inside real Home Assistant.
- `test_music_provider.py`: the Music Assistant provider maps service responses to hits and degrades to "nothing found".
- `test_weather.py`: weather answers from a weather entity inside real Home Assistant.
- `test_safety_fallback.py`: sentences that could unlock, disarm or open something are never handed to Home
  Assistant's built-in agent while everyday ones still are, and a model error never carries the server address.
- `conftest.py`, `pytest.ini`: shared setup (on Windows it lets the event loop start despite the socket block; nothing touches a network).

### 2.3 Evaluation corpora (`eval/corpus/`)

- `controls.yaml`, `qa.yaml`, `hard.yaml`: hand-written cases for traps the generator cannot derive (same words as an alias but a different device, near-identical names, shared names with and without room context).
- `heldout.yaml`: phrasings written independently of the parser.
- `heldout2.yaml`: a second held-out set, run once before any tuning, then **retired**. Never tune against it.
- Generated cases: `src/fortify_eval/generate.py` derives labelled cases (exact names, aliases, areas, noisy speech-to-text variants, queries, values, refusals, sensitive devices) from any house file, so a new house gets a new corpus for free.

## 3. How to run the tests

### Core tests and accuracy harness

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"      # .venv/bin/python on Linux and macOS
.venv/Scripts/python.exe -m pytest                                # 749 tests, about 12 s
.venv/Scripts/python.exe -m fortify_eval.cli stats                # what is in the corpus
.venv/Scripts/python.exe -m fortify_eval.cli run fortify          # score the rules on the corpus
.venv/Scripts/python.exe -m fortify_eval.cli run fortify --min-accuracy 0.95 --max-wrong-action 0.01
```

The last command exits non-zero below the thresholds, so CI can gate on it. With a language model as the
fallback tier:

```bash
FORTIFY_OLLAMA_URL=http://your-server:11434 FORTIFY_OLLAMA_MODEL=qwen3.5:2b \
  .venv/Scripts/python.exe -m fortify_eval.cli run fortify-llm
```

### Home Assistant tests

These need their own virtual environment, because they install Home Assistant itself and must not mix with
the light-weight core environment:

```bash
uv venv --python 3.14 .venv-ha
uv pip install --python .venv-ha/Scripts/python.exe -r requirements_test_ha.txt
cd tests_ha
../.venv-ha/Scripts/python.exe -m pytest
```

`tests_ha/pytest.ini` puts the repository root on the path and turns on asyncio auto mode.
`requirements_test_ha.txt` pins the versions the suite was last run against
(`pytest-homeassistant-custom-component==0.13.374`, `homeassistant==2026.10.3`); 60 tests pass on Python 3.14
at the time of writing. The Python version must be one that pinned Home Assistant supports.

### Continuous integration

`.github/workflows/tests.yml` runs both suites and the accuracy gate on every push and pull request
(`fortify_eval.cli run fortify --min-accuracy 0.90 --max-wrong-action 0.005`).
`.github/workflows/validate.yml` runs Home Assistant's `hassfest` and the HACS validation action on every push,
pull request and weekly.

## 4. The standing benchmark

Unit tests say "this specific thing works". The benchmark says "is the product getting better or worse".

- `benchmark/phrases.yaml` is a fixed list of 130 phrases, questions and chat prompts, grouped by what they
  test. 126 are scored; the 4 with kind `any` are recorded only (`-` in the report). **Ids never change**: a phrase is retired with `retired: true`, or replaced by a new id.
  Expectations describe what a great assistant would do in the synthetic house, not what the current version
  does, so a phrase that fails today stays until it passes.
- Groups:
  - `multi`: several lights at once, no device names ("kill everything in the basement").
  - `single`: one device in everyday wording ("get the floor lamp going").
  - `indirect`: statements of need ("it's too dark in the workshop").
  - `mood`: themes and moods ("make the workshop feel like a campfire").
  - `music`: songs, playlists, speakers.
  - `ask`: questions answered from state.
  - `safety`: things that must ask first or refuse (unlocking, garage, unknown devices, "don't").
  - `turns`: short conversations (follow-ups, confirmations).
  - `chat`: chit-chat and open questions; scored on not acting and not lying.
  - `random`: phrases written blind, in bulk, before tuning, to keep the other groups honest.
- Each phrase has an expected kind (action, answer, clarify, confirm, refuse, chat, no_action or any),
  and where relevant the exact set of entities acted on, the service and the data. Acting on a wrong target
  or acting when it should have asked is a **wrong action** (`WRONG` in the report); failing to act is a
  **miss** (`miss`); the wrong kind of reply is `resp`.
- Running it:

  ```bash
  .venv/Scripts/python.exe -m fortify_eval.bench run --label 0.1.8             # rules only
  FORTIFY_OLLAMA_URL=... FORTIFY_OLLAMA_MODEL=... \
    .venv/Scripts/python.exe -m fortify_eval.bench run --label 0.1.8 --llm     # with a model
  .venv/Scripts/python.exe -m fortify_eval.bench run --label 0.1.0 --core <old checkout>/custom_components/fortify_assist/core
  .venv/Scripts/python.exe -m fortify_eval.bench report                        # rewrite the report
  ```

  `--core` runs an old copy of the engine against today's phrases, so earlier versions can be scored
  retroactively.
- Each run is saved as `benchmark/history/<label>__<mode>.json` (version, commit, date, mode, model,
  every phrase's verdict and the reply). `benchmark/REPORT.md` is generated from the history: a summary row
  per run and a phrase-by-phrase table across versions. Do not edit it by hand.
- **Current score (version 0.1.8, rules only, no language model): 125 of 126 phrases pass, 0 wrong
  actions.** The one miss is "open the curtain a bit" (random group). The first batch of random phrases,
  scored before any tuning on 0.1.0, passed 7 of 27; history shows the climb version by version. With a small local model (2B
  parameters) as the fallback, version 0.1.4 scored 126 of 126.
- The harness corpus (469 generated and hand-written cases on the synthetic house) currently scores 99.8% with
  0.0% wrong actions and a median latency under a millisecond.

Caveat, stated plainly: the synthetic house and most cases were written by the same people as the code. The
held-out sets and the random group exist to limit that bias, but the numbers are a floor to improve on, not a
guarantee for your home. Real-home testing (section 6) fills the gap.

## 5. Held-out data rules

- A held-out set is written independently of the parser, run once, and only then are failures studied.
- After it has been used it is retired and a new one (`heldout3`) is written. Tuning on a used set hides
  overfitting.
- Random-group phrases in the benchmark are written blind, in bulk, before changes are made.

## 6. Live testing in a real home (practice, not data)

The synthetic house cannot find everything. Before a release the maintainers also test against a real
installation, following these rules:

1. Copy the integration to a test Home Assistant and exercise it there first; only commit after it is verified.
2. **Replay real voice logs.** Take the requests that were actually heard (diagnostics lists them) and run
   them through the engine to see what it would decide now. A synthetic benchmark can hide failures that
   voice transcription produces (mis-heard words, extra words, no punctuation).
3. **Generate phrases for every exposed device** in the real house ("turn on X", "turn off X", "what is X",
   plus the room and alias variants) and check each one resolves to that device and nothing else. This finds
   devices with duplicate or unusual names, misnamed devices and devices that ignore commands.
4. Audit the real devices from scratch: unavailable ones, duplicates, names that sound alike.
5. Test with a voice satellite in several rooms, with a satellite that has no room, and with typed text.
6. Check the voice pipeline, not only text: a text test through the conversation service does not exercise
   speech-to-text mistakes or the "prefer local handling" step.
7. Restore device states afterwards (snapshot first).
8. **Never commit household data.** Real device names, room names, people, addresses, server addresses and
   raw logs stay out of the repository. A bug found at home is reproduced in the synthetic house (or a small
   invented one) before it becomes a test. Live-run records are kept in `benchmark/live/`, which is git-ignored.

## 7. Independent stress test (user-experience audit)

Before the first public release the product was attacked the way real users and real microphones do, by someone
who had not written the engine.

- **Method:** a script (kept outside the repository) fed the engine more than 2,400 requests in total and judged each
  against what a reasonable person expects: **WRONG** (acted on the wrong thing, or acted when it should have
  asked or refused), **MISS** (should have acted and did not), **ODD** (right outcome, confusing wording) or OK.
  "Asked which one" is never counted as wrong when the request really was ambiguous.
  - About 540 scripted requests across 4 houses: plain commands, sloppy speech and fillers, speech-to-text
    mistakes, numbers as words, several commands in one sentence, follow-ups, questions, music, scenes, locks,
    garage doors, thermostats, covers, vacuums, media, things the home does not have, chit-chat, empty or huge
    input, emoji, other languages, prompt-injection text. About 45 multi-step conversations (confirmations
    answered correctly, wrongly and late; follow-ups with nothing to refer to).
  - Four houses: the synthetic house, a flat with no rooms assigned, a house with accented and unusual room names
    ("Küche", "Room 4B", "Loft #2", the same device name in two rooms), and a house with more than 300 lights.
  - About 1,700 generated requests: every device with random typos, 100+ rooms and devices the house does not
    have, and about 80 statements (said in two places) that look like commands but are not.
  - About 230 hand probes and every confirmation reply variant ("yes please", "sure", "uh yes", "yes but not...").
- **Results:** no crash and no unsafe action on any input. No unlock, garage opening or disarm ever ran without a
  confirmation, and a reply that was not a plain yes never confirmed. Emoji, other languages, very long text and
  injection strings were all ignored. Fewer than 1% of requests needed attention; the findings (a few wrong
  guesses on words similar to a device name, missed phrasings, wording problems) were handed to the engineering team
  and become regression tests as they are fixed. The count of findings at the time of this audit:
  6 wrong, about 19 missed phrasings, about 12 oddly worded replies.
- **Lesson kept as a rule:** rooms and devices that the home does *not* have are as important to test as ones it
  has. A wrong guess on an unknown word is worse than "I couldn't find that".

## 8. Test categories worth having

Status: **exists** means a test or suite covers it today; **planned** means not yet built.

| Category | What it checks | Status |
|---|---|---|
| Unit: text normalisation | Politeness, articles, number words, speech-to-text confusions | exists (`test_core.py`) |
| Unit: resolver | One clear match only, shared names, room context, aliases | exists |
| Unit: validator | Rejects unknown, unexposed, unavailable, wrong service, out-of-range | exists |
| Unit: executor | Re-reads state, reports ignored or failed commands | exists |
| Command accuracy | Generated and hand-written corpus, wrong-action rate | exists (`eval/`) |
| Sensitive actions | Confirm before unlock, disarm, garage/gate/door covers, garage switches, valves; yes must be the whole reply; expiry; risky sentences never handed to the built-in agent | exists |
| Follow-ups and conversation | Referring back, clarifications, expiry, no endless questions | exists |
| Questions | Answers computed from state, honest "no data" | exists |
| Personalities | Facts preserved, decisions identical | exists |
| Language model safety | Cannot invent devices, act, drop "don't", skip confirmation | exists |
| Model client protocol | Ollama and OpenAI-compatible payloads, malformed replies, fallback chain | exists |
| Integration (Home Assistant) | Config flow, options, services, diagnostics, reload | exists (`tests_ha/`) |
| Music, weather, feeds | Provider mapping, honest "nothing found", feed text never treated as commands | exists |
| Standing benchmark | Same phrases every version, history | exists |
| Held-out phrasings | Independent of the parser, run once | exists (new set needed each cycle) |
| Real-home replay | Voice logs and every-device phrase generation | manual practice today; **planned**: a scripted runner that takes an exported house file |
| Stress and fuzz | Typos, noise, unknown words, huge or empty input, other languages, injection text | ad hoc (section 7); **planned**: a committed fuzz suite using the synthetic and the extra houses |
| Unknown-device refusal | A word that is only *similar* to a device name must not select it | partly (`test_release_review.py`, found in section 7); **planned**: a broader generated suite |
| Alternate-house suite | Flat with no areas, non-English room names, 300+ lights, runs the whole benchmark | partly (a home with no rooms in `test_release_review.py`); **planned**: the whole benchmark on each house |
| Speech-to-text realism | Run the benchmark through a real speech-to-text engine on recorded or synthesised audio | **planned** |
| Voice satellite / wake-word end to end | Room detection from the satellite's area, microphone re-open rules, conversation continuation | **planned** (partly in `tests_ha/`) |
| Latency budget | Rules answer in well under a second in a 300-light house; language model path measured separately | partly (harness reports p50/p95); **planned**: a failing threshold |
| Language model model-quality matrix | The same benchmark with several small models, recorded per model | exists for one model; **planned**: a standard matrix |
| Concurrency | Two satellites speaking at once; one speaker's confirmation cannot be answered from another | **planned** |
| Long-running soak | Days of simulated traffic, memory, stale follow-up state | **planned** |
| Upgrade and migration | Options from older versions load; recent requests survive restart | partly (`recent requests survive a restart`); **planned**: option migration |
| Multi-language | Non-English grammars | **planned** (English only today) |
| Accessibility of wording | Replies are short, speakable, no symbols or entity ids in speech | partly (`clean makes text speakable`); **planned**: automatic scan of all reply templates |
| Privacy | Diagnostics redact keys, addresses and the owner name; model errors carry no server address; nothing about devices sent to a model by default | partly (diagnostics test, `test_safety_fallback.py`); **planned**: a test that fails if a model request contains a device name |
| Security | Injection text in feeds, device names and chat cannot cause actions | partly; **planned**: a dedicated suite |
| Release check | Version in manifest, changelog entry, README claims match the behaviour | partly (`hassfest` and HACS validation in `validate.yml`); **planned**: version and claim checks (the README has already drifted once: examples that did not parse) |

## 9. How to add a test for a bug

Every bug found in the real world should become a test so it stays fixed.

1. **Reproduce it in the synthetic house.** Use `eval/houses/synthetic.yaml`, or add the minimum extra
   entities in the test itself (see `tests/test_vacuums.py`, which adds two vacuums in a fixture). Do not copy
   real device or room names; invent equivalents with the same shape of problem (for example two devices
   whose names differ by one letter).
2. **Write the failing test first.** Put it in the file that matches the area (see section 2.1), or a
   new file with a docstring that states what it covers. The usual shape:

   ```python
   import asyncio
   from fortify_core import Pipeline
   from fortify_eval.cli import DEFAULT_HOUSE
   from fortify_eval.house import load_house

   def test_a_word_that_only_sounds_like_a_device_is_refused():
       p = Pipeline(load_house(DEFAULT_HOUSE))
       out = asyncio.run(p.ahandle("turn on the hot tub", None, "c", "speaker"))
       assert not out.actions
   ```

   `ahandle(text, speaker_room_id, conversation_id, speaker_id)`: use the same speaker id across calls to test
   follow-ups. Music needs `Pipeline(h, music_provider=...)` with a fake provider (see `tests/test_music_everyday.py`).
3. **State what a reasonable user expects.** Assert on the entities and service (`out.actions`), the kind
   of outcome (`out.kind`), and where wording matters, a fragment of `out.message`.
4. **Fix the engine**, run `python -m pytest`, then the harness (`fortify_eval.cli run fortify`). The
   wrong-action rate must stay at 0.0%.
5. **Add the phrase to the benchmark** if it is a type of request that should always keep working: append a
   new id to `benchmark/phrases.yaml` (never edit existing ones), then run a new labelled benchmark and
   regenerate the report.
6. **Add a changelog line** under the next version.
7. For Home Assistant behaviour (config flow, service calls, diagnostics), add the test to `tests_ha/` and run it
   in the separate Home Assistant environment.
8. Do not put real names, addresses or logs in the test, the commit message or the issue.
