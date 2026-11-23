# Fortify Assist

A high-accuracy conversation agent for Home Assistant, by Fortify Technologies.

**Goal:** device control that is right, not just plausible. A deterministic resolver handles
commands and questions about the home; an LLM (local first, optional cloud) rewrites requests
the rules cannot parse and handles chat. The LLM never picks a device and never executes anything.

Status: **early preview (0.1.8).** The Home Assistant integration works and is tested against
Home Assistant 2026.10. It has not been used on many real installs yet; expect rough edges and
please file issues.

## Install

1. In HACS, add this repository as a custom repository (category: Integration), then install **Fortify Assist**.
   Or copy `custom_components/fortify_assist` into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.
3. Settings, Devices & services, Add integration, **Fortify Assist**.
4. Choose a name and personality. Optionally point it at a language model (below).
5. Settings, Voice assistants: edit a voice assistant and choose **Fortify Assist** as its
   conversation agent.

Requirements: Home Assistant 2026.10 or newer, English. Entities must be exposed to Assist
(Settings, Voice assistants, Expose); the assistant sees exactly what the built-in agent sees.

### What each feature needs

| Feature | Needs |
|---|---|
| Device control, questions, personalities | Nothing beyond Home Assistant. Entities must be exposed to Assist. |
| Open conversation, unusual phrasing | A language model (below). Optional. |
| Mood and theme lighting | Lights in an area. Color and white tones only where the light supports them. |
| Music, playlists, audiobooks, podcasts | The **Music Assistant** integration (Home Assistant add-on or server plus its integration) with a music provider, and speakers exposed as Music Assistant players. Without it, music requests are not offered. |
| Weather | A weather integration in Home Assistant (Met.no is set up by default). |
| Timers, shopping lists, your own custom sentences | Nothing: requests Fortify does not understand are offered to Home Assistant's built-in Assist first (can be turned off under Behavior). |
| News and status feeds | A name and an RSS or Atom address for each feed, in Fortify's settings (up to 20). No other integration needed. Feedparser sensors or Feed Reader events already in Home Assistant can be added too. Summaries need a language model; without one the headlines are read. |
| "Which room?" for voice requests | Voice satellites assigned to an area. |

Nothing is hard-coded to one home: rooms, devices, speakers and music all come from your own Home Assistant.

### Language model (optional)

Commands and questions about your home work with no language model. A model adds understanding
of unusual phrasing and open conversation. Each install chooses its own:

- **Ollama**: server address such as `http://your-server:11434`, then pick a model from the list.
- **OpenAI-compatible** (llama.cpp server, LM Studio, vLLM, OpenAI, and similar): the `/v1` address
  and an API key if needed.
- **Fallback**: an optional second model used only if the first one fails, for example a hosted
  service as a backup for a local model. Nothing is sent to it unless the first model fails.

Keep the model loaded, or the first request after a pause waits for it to load: on Ollama set
`OLLAMA_KEEP_ALIVE=-1` (or a long time) on the server. Pick a model that fits entirely in your GPU's memory;
one that spills onto the CPU takes 10 to 40 seconds per reply, which is too slow for voice.

A small model is enough for control: it only rewrites the sentence, it never picks a device. Models
around 2B parameters worked in testing (`qwen3.5:2b`). Open conversation is better with a larger model
if your hardware allows. Use a plain model: a custom Ollama model with its own built-in system prompt or
example conversations leaks those into replies (for example "I cannot control anything").

**What is sent to a language model** (only if you configure one; with none, nothing leaves Home Assistant):
- your sentence, the speaker's room name and the names of your rooms;
- only if you turn on answering questions about the home: up to eight relevant devices with their room and current state
  (for example "Kitchen Lights (Kitchen) is on, brightness 60%"), plus the recent conversation;
- the assistant's persona: its name, and the owner, maker and purpose you typed in settings;
- for news summaries, the text of the feed items being summarized.

The backup model receives the same thing when the first one fails, so choose a hosted service as backup
only if you are comfortable sending it this. A language model never chooses which device to control.

### Settings you can change later

Settings, Devices & services, Fortify Assist, Configure: name and personality, language models,
news and status feeds, and behavior:

- Ask before unlocking, disarming or opening a garage door (on by default). A yes only counts when it is the
  whole reply.
- Ignore the assistant's name at the start or end of a request.
- Let Home Assistant's own Assist handle what this assistant doesn't (on by default): timers, shopping lists,
  sentence triggers and custom sentences. It is only asked when the rules understood nothing.
- Let the language model answer questions about the home (off by default). Small models make up device
  states, so by default a question the rules cannot answer gets "I'm not sure". Turn this on only with a
  capable model; it is then given the current state of the devices the question seems to be about.

## How it works

```
speech/text -> normalise -> rules (parse -> resolve -> validate) -> answer / action / question
                               |  can't place it
                               v
                       LLM rewrites it as the same intent (quotes your words, picks no device)
                               |
                               v
                       the same resolver, validator and confirmation policy
```

- **Resolver:** acts only when exactly one entity clearly matches. Shared names ask a question
  (using the room of the satellite that heard you when known). Unknown devices are refused.
- **Sensitive actions** (unlock, disarm, open the garage) ask for confirmation first.
- **Validator:** every action is checked against live state before it can run. A made-up entity,
  unavailable device, wrong service or out-of-range value never reaches a service call.
- **Executor:** runs the action, re-reads the device state, and says so when a device ignored it.
- **LLM tier:** local model first, optional cloud fallback. The prompt contains no device list,
  so it is the same size in any house.
- **Questions:** lists, counts, any/all, brightness, volume, thermostat setpoint, now-playing and the
  security check are computed from live state, never generated. So are the time, the date and simple sums.
- **What nobody understood:** Home Assistant's built-in Assist is offered the request (timers, lists,
  custom sentences). Then, if it is not about the home, the language model answers as conversation.
  The model can never act, and by default never talks about device states.

## What you can say

Examples (names are whatever your devices are called; a room's name narrows a shared name):

- **On and off, levels:** "turn on the kitchen lights", "kitchen lights off", "set the bar strip to 40 percent",
  "set the bedroom fan to high", "open the living room blinds halfway".
- **Colors and whites** (only for lights that report support for them, otherwise it says so):
  "make the workshop lights blue", "set the bedroom lamp to warm white", "set the porch lights to daylight".
- **Relative changes:** "make it brighter", "a little dimmer", "turn down the lights in the bar",
  "make it warmer", "turn up the bar speaker", "raise the blinds".
- **Thermostat, media, vacuum:** "set the thermostat to cool", "make the thermostat 72", "mute the kitchen speaker",
  "send Dusty home".
- **Whole rooms and floors, no device names needed:** "turn off the workshop", "kill everything in the basement",
  "lights out downstairs", "dim the bar and the workshop to 30 percent", "turn the kitchen lights blue",
  "turn off every light downstairs except the bar", "turn off the workshop lights but leave light a on".
  If a dead device has the name you said ("bar lights"), the room's working lights are used instead.
- **Sets and ceiling lights:** "make the game room overhead lights blue" changes every light named Game Room
  Overhead Light plus a letter or number. Overhead, ceiling, can, recessed and pot are treated as one word, so
  "the can lights" finds "Recessed Lights". Without a room, a name means the one in the speaker's own room.
  "Everything" means the lights, never locks, doors or switches. Lights that cannot do the change (an on/off bulb
  asked to dim) are skipped and named; unavailable lights are skipped; a light group is not switched twice.
  "Downstairs" and "upstairs" use your Home Assistant floor levels (or floor names such as Basement); floor
  aliases work too. An exception that matches nothing in the request stops the whole request.
- **Everyday phrasing:** "I can't see anything in the workshop", "I need the garage lights", "it's freezing in
  the bedroom", "warm white in the bedroom", "give me full brightness in the workshop", "kill the music".
- **Follow-ups:** "turn them off", "make them blue", "brighter", "do that again", "and the kitchen", "what about
  the bedroom". Each speaker remembers its last request for 5 minutes, across wake words. With nothing to refer
  back to it asks which device or room. Sensitive actions still ask first.
- **Several things at once:** "turn on the deck lights, turn off the bar strip and the office lamp",
  "make the helmet light blue and the lamp red". If any part is not understood, none of it runs.
- **"Don't" and "except":** a request with a negative or an exception is only ever handled by the rules, never by
  the language model, because a rewrite that drops one word would do the opposite.
- **Questions:** "which lights are on", "are all the doors locked", "how bright is the office lamp",
  "what is playing", and what exists: "what lights are in the workshop", "how many lights are in the basement".
- **Sensitive actions** (unlock, disarm, open a garage door) ask first.
- **On their own:** "pause", "stop", "volume up", "louder", and the name of a scene or script
  ("movie night", "let's have movie night"). A greeting that is also a script's name ("good night") asks first.
- **One word:** a light or fan named alone is switched ("lights", "kitchen lights", "the lamp", "office"):
  off if any of them is on, otherwise on. Nothing else is switched by its name alone.
- **TVs and volume:** "turn on the TV" (the one in the room you are in), "turn off all the TVs", "tv volume
  up", "set the volume to 30", "turn it up" while music plays. "Open Plex on the bar TV", "put Netflix on the
  TV" for common apps, where the TV's integration can launch apps. A TV must be exposed to Assist like any other
  device; expose the television itself rather than its streaming stick or a duplicate.
- **Wishes and politeness:** "I'd like the lamp on", "we need the printer on", "would you mind turning off the
  lamp", "make sure the front door is locked", "it's bedtime, turn off everything upstairs".
- **Comfort:** "I'm cold", "it's hot in here", "turn up the heat", "it's too dark", "it's too bright in here".
- **The whole home:** "lock up", "turn off all the lights", "is the house secure", "what's the status of the
  house", "did I leave the garage open", "is the alarm on", "what rooms are there", "what scenes do I have".
- **Small things without a model:** "what time is it", "what's the date", "what's 15 percent of 80".
- **Moods and themes:** "make the workshop halloween colors", "set a cozy mood in here", "make the den feel like a campfire".
  The palette is spread over the lights in the room, and each light only gets what it can really do (a white-only
  bulb gets a warm tone, an on/off bulb is left alone). A theme it does not know can be designed by the language
  model, but only from known colors and a brightness range; it never picks devices.
- **Music** (needs the [Music Assistant](https://music-assistant.io) integration): "play seven nation army in the
  kitchen", "play the jazz classics playlist", "play some jazz", "read me dune". It searches, plays only a good
  match and says so when it finds nothing. With several speakers and no room it asks which one.
  "pause the music", "skip this song", "what song is this" and "it's too loud" act on whatever is playing.
- **Weather** from your own weather entity: "what's the weather", "will it rain tomorrow", "what's the forecast",
  "do I need an umbrella", "how cold is it outside". The answer is what the entity reports, put in a sentence,
  never a model's guess. The first available weather entity is used, or the one chosen under Behavior. Indoor
  questions ("how warm is the basement") are left to your sensors.
- **News and status feeds** (feeds added in settings, each with a name you would say, such as Sports, Launches or
  Spaceflight): "what's the news", "any launches today", "anything on spaceflight", "anything new on Hacker News",
  "any news about the launch", "summarize the tech news", "is GitHub down". It reads the newest few headlines,
  taking turns between feeds. Ask for a summary ("summarize the news", "give me a rundown"), or turn summaries on
  for every answer in settings, and a language model gives two or three sentences instead. A summary that names
  a person, place or number the items do not say is thrown away and the headlines are read instead. Small models
  can still change what a story means, which is why headlines are the default and summaries are marked experimental.
  "What's the news" reads the feeds with news in their name (so not Weather or Sports) when there are any, otherwise all. Status questions
  report the newest item and how old it is, never "it's up" or "it's down". Feed text is only ever spoken: it
  never reaches the command parser, so a feed cannot make the assistant do anything. Addresses are fetched
  only when you ask, at most every 10 minutes per feed, and hidden in diagnostics.

## Your own questions

Anything Home Assistant can answer, the assistant can say, because requests it does not understand are offered
to Home Assistant's built-in Assist. Make an automation with a **Sentence** trigger and a **Set conversation
response** action whose text is a template. For example, with a flight-tracking integration that provides a
sensor of nearby aircraft:

```yaml
triggers:
  - trigger: conversation
    command: ["what plane just flew over", "what plane is that"]
actions:
  - set_conversation_response: >-
      {{ state_attr('sensor.nearby_flights', 'flights')[0].callsign | default('I do not see one right now') }}
```

The same works for a bin-collection day, a car's charge level or anything else you have a sensor for. The
entity and attribute names above are placeholders: use the ones your integration provides.

## Troubleshooting

Settings, Devices & services, Fortify Assist, the three-dot menu, **Download diagnostics** gives a file with
the last 60 things the assistant heard, what it decided, what it said and what the language model returned
(API keys and server addresses are removed). For live logging add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.fortify_assist: debug
```

## Persona and personalities

The assistant's name, spoken name, acronym, owner, maker and purpose are configuration
(`core/persona.py`). A personality is a pack of phrasings (`core/personalities/*.json`);
shipped: `neutral`, `butler`, `sarcastic`, `jokester`, `secretary`. Packs only change wording:
device names, states and numbers always come from the facts, and tests enforce that every
phrasing keeps every fact and that decisions are identical under every personality.
To add one, copy `neutral.json`, keep every `{slot}`, and run the tests.

## Measuring accuracy

No standard accuracy benchmark exists for Home Assistant voice control (see
[docs/research/assist-alternatives.md](docs/research/assist-alternatives.md)), so we build one.
The key metric is the **wrong-action rate**: the agent executed something it should not have.
Missed commands and unneeded questions are counted separately because they are far less harmful.

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m fortify_eval.cli stats
.venv/Scripts/python.exe -m fortify_eval.cli run fortify
# with an Ollama model as the LLM tier:
FORTIFY_OLLAMA_URL=http://host:11434 FORTIFY_OLLAMA_MODEL=qwen3.5:2b \
  .venv/Scripts/python.exe -m fortify_eval.cli run fortify-llm
```

(`.venv/bin/python` on Linux and macOS.)

- `eval/houses/synthetic.yaml`: a test house full of traps (duplicate names, near-identical names,
  aliases, unavailable and unexposed entities, locks, a garage door).
- `src/fortify_eval/generate.py`: derives labelled cases from any house file.
- `eval/corpus/*.yaml`: hand-written sets. `heldout.yaml` and `heldout2.yaml` are phrasings written
  independently of the parser; `heldout2` was run once before any tuning and is retired.
- `fortify-eval run ... --min-accuracy 0.95 --max-wrong-action 0.01` exits non-zero below the
  thresholds, so CI can gate on it.

### Standing benchmark

`benchmark/phrases.yaml` is a fixed list of phrases, questions and chat prompts run on every version, so
results can be compared over time. Ids never change; new phrases are added, never edited. Each run is kept in
`benchmark/history/` and `benchmark/REPORT.md` shows every version side by side, phrase by phrase, with what
the assistant said.

```bash
.venv/Scripts/python.exe -m fortify_eval.bench run --label 0.1.8             # rules only
FORTIFY_OLLAMA_URL=http://host:11434 FORTIFY_OLLAMA_MODEL=qwen3.5:2b   .venv/Scripts/python.exe -m fortify_eval.bench run --label 0.1.8 --llm       # with a model
.venv/Scripts/python.exe -m fortify_eval.bench run --label 0.1.0 --core <old checkout>/custom_components/fortify_assist/core
```

### Current results (synthetic house, 469 cases)

| agent | overall | wrong actions |
|---|---|---|
| refuse-all | low | 0% |
| exact-name baseline | about 20% | 0% |
| rules only (0.1.8) | 99.8% | 0% |
| rules + qwen3.5:2b (0.1.4, Ollama over LAN) | 99.1% | 0% |

Caveats: the synthetic house and most test cases were written by the same author as the code,
and the held-out sets are small. The first batch of random benchmark phrases, scored before any tuning,
passed 7 of 27 with rules only (see benchmark/REPORT.md); model results come from a 2B model on a LAN server.
Treat these as a floor to improve on and replace with an export of real homes.

## Tests

```bash
python -m pytest                     # core and accuracy harness
cd tests_ha && python -m pytest      # integration, needs requirements_test_ha.txt
```

## Roadmap

- **M0**: repo, test house, corpus, scoring. Done.
- **M1**: entity index, resolver, validator, executor with state verification. Done.
- **M2**: LLM tier (intent rewrite, tiered local then cloud). Done.
- **M3**: persona and personalities, questions about devices. Done.
  Chat path and the Home Assistant integration (config flow, exposure, HACS package): done, early preview.
  Next: more languages, a diagnostics page, more real-world evaluation data.

## Research

[ha-voice-internals](docs/research/ha-voice-internals.md),
[assist-alternatives](docs/research/assist-alternatives.md) and
[small-llm-finetune](docs/research/small-llm-finetune.md). All three were produced by automated
research; claims marked UNVERIFIED there need checking before we rely on them.

## License

Apache-2.0
