# Installing and setting up Fortify Assist

Fortify Assist is a conversation agent for Home Assistant's voice assistant (Assist). It understands
everyday requests for your lights, switches, fans, blinds, locks, thermostats, speakers, TVs, vacuums, scenes
and more. It works with no language model and no cloud account.

Status: early preview. It is English only. Please report problems at
<https://github.com/Fortify-Technologies-LLC/fortify_assist/issues>.

## 1. Requirements

| Need | Details |
|---|---|
| Home Assistant | **2026.10.0 or newer** (this is the minimum in `hacs.json`). |
| Language | English. Set your Assist pipeline language to English. |
| Entities exposed to Assist | Fortify Assist only sees what you expose to Assist, exactly like the built-in agent. |
| HACS | Optional but recommended, for installing and updating. |
| Voice hardware | Optional. Any voice satellite or the Home Assistant app's Assist button works. Typing in the Assist dialog works too. |
| Music Assistant | Optional. Only for "play some jazz in the kitchen" style requests (searching for a song, artist, playlist, audiobook or podcast). Needs the Music Assistant integration with a music provider, and your speakers added as Music Assistant players. Without it, such a request gets "Playing songs by name needs the Music Assistant integration"; "play", "pause", "skip" and volume still work on any media player. |
| A language model | Optional. Local with Ollama, or any OpenAI-compatible server (llama.cpp, LM Studio, vLLM, a hosted service). Helps with unusual phrasing and open conversation. Commands and questions about your home work without it. |
| Weather | Optional. A weather integration in Home Assistant (for "what's the weather"). |

Devices it can control or ask about: lights, switches, fans, covers (blinds, curtains, garage doors), locks,
climate, media players, scenes, scripts, sensors, binary sensors, alarm panels, input booleans, humidifiers,
valves, sirens, vacuums and remotes.

## 2. Install

### Option A: with HACS (recommended)

1. In Home Assistant, open **HACS**.
2. Open the three-dot menu (top right) and choose **Custom repositories**.
3. Repository: `https://github.com/Fortify-Technologies-LLC/fortify_assist`. Category: **Integration**. Click **Add**.
4. Find **Fortify Assist** in HACS and click **Download**. Choose the latest version.
5. **Restart Home Assistant** (Settings, System, the power icon, Restart Home Assistant).

### Option B: manual install

1. Download the repository (the **Code** button on GitHub, then **Download ZIP**, or `git clone`).
2. Copy the folder `custom_components/fortify_assist` into your Home Assistant configuration folder, so that you
   end up with `<config>/custom_components/fortify_assist/manifest.json`.
   The `<config>` folder is the one that contains `configuration.yaml`.
3. **Restart Home Assistant.**

## 3. Add the integration

Settings, Devices & services, **Add integration**, search for **Fortify Assist**. The setup asks for a few
things in turn. Everything can be changed later (section 9), so do not worry about getting it perfect.

**Step 1 of 4: "Who is your assistant?"**

| Field | What to put |
|---|---|
| Name | How the name is written. Default is "Assistant". Example: "Issac" or "N.O.V.A.". Required. |
| Spoken name | How it is said, if different (for example "Nova" for "N.O.V.A."). Optional. |
| What the name stands for | Only if the name is an acronym. Optional. |
| Who it works for | A person or household, for example "the Smiths". Optional. |
| Who made it | Optional. |
| Purpose | One sentence about what it is for. Optional. |
| Personality | Changes the wording of replies, never what it does. Default is Neutral. See section 8. |
| Extra personality traits | Free text that steers the tone of open conversation, for example "curious, loves astronomy". Only matters if you use a language model. |

**Step 2 of 4: "Language model"** (optional)

| Field | What to put |
|---|---|
| Provider | **None** (the default), **Ollama**, or **OpenAI-compatible**. Choose None to skip. |
| Server address | Left empty, the default is used (Ollama: `http://localhost:11434`; OpenAI-compatible: `http://localhost:8080/v1`). `localhost` means the machine or container Home Assistant itself runs in, so on Home Assistant OS, or with the model on another computer, type that server's address, for example `http://your-server:11434`. For OpenAI-compatible servers include the `/v1` part. |
| API key | Only for hosted services. |

If you chose a provider, the next screen is **"Choose the model"**: pick one from the list your server
reports (you can also type a model name). If the server cannot be reached you will see "Could not reach the server.
Check the address and that the server is running." and can try again.

**Step 3 of 4: "Fallback language model"** (optional)

The same fields, followed by **"Choose the fallback model"**. Used only if the first model fails (for example a hosted service as a backup for a local
model). Nothing is sent to it unless the first one fails. Choose **None** to skip.

**Step 4 of 4: "News and status feeds"** (optional, can be added later)

Up to 20 name and address pairs (RSS or Atom), for example a feed named "Spaceflight". Leave pairs empty to
skip. Also on this screen:

- **Summarize with the language model (experimental)**: off by default. Off means headlines are read, and you
  get a summary only when you ask ("summarize the news").
- **Items to read**: 1 to 5.
- **Feeds already in Home Assistant (optional)**: Feedparser sensors or Feed Reader events you already use.

Feed addresses are checked when you save. Errors you may see: "This address could not be read as an RSS or Atom
feed.", "Add the feed's address, or clear the name.", "Another feed already has this name."

Click **Submit**. A device called by your assistant's name appears with one conversation entity.

## 4. Expose your devices to Assist

Fortify Assist can only act on devices you have exposed.

1. Settings, **Voice assistants**, open the **Expose** tab.
2. Click **Expose entities** and tick the lights, switches, locks, covers and so on you want to control by voice.
   Do not expose what you do not want controlled by voice.
3. Use clear, spoken names. See section 10 for tips on names, aliases and areas.

New devices you expose are picked up without restarting.

## 5. Create or choose an Assist pipeline and voice satellite

1. Settings, **Voice assistants**. Open an existing assistant, or **Add assistant**.
2. Set the **Language** to **English**.
3. Under **Conversation agent**, choose your assistant (the name you entered in step 1).
4. The assistant's conversation settings also have an option that lets Home Assistant handle commands
   locally first (the "prefer local" option; in the assistant's stored settings it is `prefer_local_intents`,
   off unless you turned it on). Leave it **off** (recommended, see the next section).
5. Choose speech-to-text and text-to-speech engines as you normally would (Home Assistant Cloud, Whisper,
   Piper, and so on). Fortify Assist does not change these.
6. **Voice satellite**: in Settings, Devices & services, open the satellite's device and **assign it to an
   area** (for example the room it is in). On the same device page the satellite has an **Assistant**
   setting: choose the assistant you just set up, or leave it on **Preferred** if that assistant is your
   preferred one in Settings, Voice assistants.
7. Test it with the Assist button at the top right of Home Assistant by typing "turn on the kitchen lights" (use
   a room you have), then by voice.

Why the area matters: "turn off the lights" or "play some music down here" uses the room of the satellite
that heard you. If the satellite (or the phone app) has no area and your lights are spread over several rooms,
it asks "Which room's lights?"; answer with a room, or "all of them" / "everywhere". In a home where no light
has a room at all, "turn off the lights" simply means all of them.

### The "prefer local" switch: why off is recommended

With the switch **on**, Home Assistant's built-in sentence matching answers first and Fortify Assist only
gets what it cannot match. Because Fortify is an agent that can control devices, Home Assistant still passes
state questions ("is the front door locked") and media searches on to Fortify, but plain commands it can
match are carried out by Home Assistant itself.

- **Safety:** the built-in commands include "unlock the front door" and "open the garage door", and Home
  Assistant carries those out **without Fortify's confirmation**. With the switch on, the "Are you sure?" check
  described in this guide does not apply to sentences Home Assistant matches itself. This is the main reason
  to leave it off.
- **Follow-ups:** "turn them off", "change them back" or "and the bedroom" only work for things Fortify itself
  handled. If the built-in Assist handled "turn on the kitchen lights", Fortify cannot refer back to it.
- **Replies:** when the built-in Assist handles a command and every device is in the satellite's own room, the
  satellite may answer with a short tone instead of words.
- With the switch **off**, Fortify answers everything first. It offers what *it* does not understand to Home
  Assistant's built-in Assist (timers, shopping lists, your own custom sentences). That is a Fortify setting
  (section 9, "Behavior"), on by default. Sentences about unlocking, disarming, or opening a garage, gate, door
  or lock are never passed on this way, so they always get Fortify's confirmation.

## 6. Try it

Some things to say (use your own device and room names):

- "turn off the kitchen lights", "set the bedroom lamp to 40 percent", "make the workshop lights blue"
- "turn off everything downstairs", "lights out", "it's too dark in here"
- "is the front door locked", "which lights are on", "what's the temperature in the bedroom"
- "unlock the front door": it asks "Are you sure you want me to unlock ...?" and only a reply that is just a yes
  counts ("yes", "yes please", "sure", "go ahead"). Anything else, including a new request, cancels it.
- "play some jazz in the kitchen" (with Music Assistant), "pause", "what's playing"
- "what's the weather", "what's the news" (with a weather entity or feeds)
- Follow-ups: "turn them off", "and the bedroom" (see section 5 about the "prefer local" switch)

Say "what can you do" for a summary of what it can do in your home.

## 7. Check what it heard

Speech-to-text is the most common reason a request goes wrong, so look at what was actually heard.

1. Settings, Devices & services, **Fortify Assist**.
2. Click the three-dot menu, then **Download diagnostics**.
3. Open the file and find `recent_requests`: the last 60 requests, kept across restarts, newest last. Each
   entry shows what was **heard**, the room, what was **decided**, the device actions, what was **said** and, if
   a language model was used, what it returned.

The file also lists how many entities are exposed, how many rooms and floors it can see, and the language models
configured (provider and model name). API keys, server addresses, feed addresses and the "Who it works for"
name are removed, and model errors are recorded without the server's address. Even so, read it before posting
it publicly: it contains your spoken requests and the names of your devices and rooms.

For live logs, add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.fortify_assist: debug
```

## 8. Personalities

Pick one in the setup or later in the options. They change wording only: the device, the state and the numbers
in a reply are always the true ones.

| Personality | Sounds like |
|---|---|
| Neutral | Plain, short and factual (default). |
| Butler | Courteous, formal and a little apologetic. |
| Sarcastic | Dry, deadpan and slightly put-upon. Still gets it right. |
| Jokester | Cheerful, punny, always up for a quip. |
| Secretary | An energetic, peppy intern. Very eager. |

## 9. Changing settings later

Settings, Devices & services, Fortify Assist, **Configure**. A menu offers:

- **Name and personality**: the same fields as step 1.
- **Language model** and **Fallback language model**: provider, server address, API key, model.
- **News and status feeds**: names, addresses, summary on or off, number of items, feeds already in Home Assistant.
  Two empty rows are always added so you can add more, up to 20. Addresses are fetched only when you ask, at most every 10 minutes per feed.
- **Behavior**:
  - *Ask before unlocking, disarming or opening garage doors*: on by default. Leave it on. It covers unlocking a
    lock, disarming an alarm, opening or moving a cover that is a garage door, gate or door (by its device class,
    or by "garage", "gate" or "door" in its name or alias), switching a switch with "garage" or "gate" in its
    name or alias, and opening any valve.
  - *Ignore the assistant's name at the start or end of a request*: on by default ("Issac, turn on the lamp").
  - *Let Home Assistant's own Assist handle what this assistant doesn't (timers, lists, your custom sentences)*: on by default.
  - *Let the language model answer questions about the home (larger models only; small ones make things up)*: off by default. Turn on only with a capable model.
  - *Weather to report*: optional. Left empty, the first available weather entity is used.

Saving reloads the assistant; no restart needed.

### Using a language model well

- Models of about 2 billion parameters are enough for control (the model only rewrites the sentence). Larger
  models give better open conversation.
- Pick a model that fits entirely in your GPU's memory. One that spills to the CPU can take 10 to 40
  seconds per reply, which is too slow for voice.
- Keep the model loaded (on Ollama, set `OLLAMA_KEEP_ALIVE=-1` on the server) or the first request after
  a pause waits for it to load.
- Use a plain model. A custom model with its own built-in instructions or example conversations can leak those into
  replies.

## 10. Make it work better: names, aliases and areas

1. **Assign every device to an area** (Settings, Areas). "Turn off the workshop" and "turn on the lamp" (in the
   room you are in) depend on it. A device with no area is reached by its name, or by "all the lights" style
   requests; only when no device of that kind has an area does "turn off the lights" mean all of them.
2. **Assign areas to floors**, and give floors a level in Settings, Areas, Floors (for "downstairs",
   "upstairs"). Floor aliases work too.
3. **Use short, speakable device names**: "Floor Lamp" not "Lamp_01 (Zigbee)". Avoid symbols.
4. **Do not repeat the room in the name** if the device is in that room's area; either "Lamp" in the Bedroom
   area or "Bedroom Lamp" is fine. Having both a "Lamp" in the Bedroom area and in the Office area is fine: it
   asks which when you are not in either room.
5. **Add aliases** for what you really say: Settings, Voice assistants, Expose, click the entity, then its aliases
   (for example "reading lamp", "the strip"). Areas can have aliases too (for example "the lab"), and so can floors.
6. **Expose the television itself**, not the streaming stick or a duplicate entity, so "turn on the TV" works.
7. **Name scenes and scripts** with a phrase you would say ("Movie Night"): saying just the name runs it.
8. **Light groups**: expose the group or its members, not both under the same name.
9. **Unwanted devices**: un-expose anything unavailable or unused so that names do not collide.
10. **Speakers for music**: they must be Music Assistant players, and each in an area.
11. A **satellite in every room** where you speak, each assigned to its area.

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Fortify Assist is not in the integrations list | Home Assistant not restarted after install, or the folder is in the wrong place | Check `custom_components/fortify_assist/manifest.json` exists, restart, clear the browser cache. |
| "I couldn't find a device called X" | Not exposed to Assist, misheard, or named differently | Expose it (section 4), check the heard text (section 7), add an alias. |
| It asks "Which one: A or B?" | Two devices fit and it will not guess | Answer with the room ("the kitchen one"), or give the devices different names or aliases. Use a satellite with an area so the room is known. |
| It asks "Which room's lights?" | The satellite or app has no area and your lights are in several rooms, or you said "the lights" with no room | Answer with a room, or "all of them" / "everywhere". Assign the satellite to an area. |
| A command was carried out but it only played a short tone, or Fortify does not appear in `recent_requests` | The "prefer local" switch is on (section 5), so Home Assistant's built-in Assist handled it | Expected with that switch on; turn it off to let Fortify answer first. |
| Follow-ups ("turn them off") do not work | The "prefer local" switch is on and the built-in Assist handled the earlier request, or more than 5 minutes passed | Name the device again, or turn the switch off (section 5). |
| It heard the wrong words | Speech-to-text | Check the heard text; try a better speech-to-text engine, a closer microphone, or add an alias for the word it keeps hearing. |
| "Are you sure you want me to unlock ...?" and then nothing | Anything other than a reply that is just a yes ("yes", "yes please", "sure", "go ahead") cancels it; it also expires after a minute | Say just "yes". |
| Unlocking or opening the garage happened without "Are you sure?" | The "prefer local" switch is on and Home Assistant's built-in Assist matched the sentence, or *Ask before unlocking...* is off, or the device is a garage or gate opener that Fortify cannot recognise (a cover without a garage, gate or door class or name, or a switch without "garage" or "gate" in its name) | Turn the switch off (section 5), keep confirmation on (section 9), set the cover's device class or add an alias with "garage" or "gate". |
| The satellite says "Which room?" for everything | The satellite is not assigned to an area | Assign it in the device settings. |
| "X is unavailable right now" | The device is `unavailable` in Home Assistant (offline). A device whose state is only `unknown` can still be controlled. | Fix the device; Fortify will not pretend to control it. |
| "X doesn't support brightness / color" | The light cannot do that | Expected. It says so rather than pretending. |
| "Playing songs by name needs the Music Assistant integration" | Music Assistant is not installed or not loaded | Section 1. |
| Music requests say a room or speaker "can't play music from here" | The speaker is not a Music Assistant player, or it has no area | Section 10. |
| "I couldn't find anything for ..." (music) | The search found no good match | Say the song and artist, or the name of a playlist you own. |
| Language model: "Could not reach the server" | Wrong address, server down, firewall | Use an address Home Assistant can reach (not `localhost` if the server is another machine). |
| Replies take 10 or more seconds | Language model too large for the GPU or not kept loaded | Use a smaller model; keep it loaded (section 9). |
| Replies are odd or invented when chatting | Small model | Use a larger or plainer model; keep *Let the language model answer questions about the home* off. |
| "Something went wrong on my end" | An internal error; it is logged | Turn on debug logging (section 7) and open an issue with the diagnostics. |
| News: "I couldn't read ... just now", or the address is refused in settings | Not an RSS or Atom address, or the site is down or unreachable from Home Assistant | Open the address in a browser; it should show XML. |
| A word that is only *similar* to a device name selects a device you did not mean | Fuzzy name matching (known issue) | Say the device's exact name or add an alias; report it. |

## 12. Updating

- **HACS:** HACS shows an update when a new version is available. Click **Update**, then **restart Home
  Assistant**. Read `CHANGELOG.md` first for anything that changes behaviour.
- **Manual:** replace the `custom_components/fortify_assist` folder with the new one and restart.

Your settings are kept.

## 13. Uninstalling

1. Settings, Voice assistants: switch any assistant that uses Fortify Assist back to another conversation agent.
2. Settings, Devices & services, Fortify Assist, three-dot menu, **Delete**.
3. In HACS, open Fortify Assist, three-dot menu, **Remove** (or delete the `custom_components/fortify_assist`
   folder for a manual install).
4. Restart Home Assistant.

Deleting the integration in step 2 also deletes its log of recent requests (the
`fortify_assist.<id>.recent` file in Home Assistant's `.storage` folder).

## 14. Privacy

**Stays on your Home Assistant, always:** your devices, their names and states, the rooms, the exposure
list, the decisions about what to do, every device command, and the recent-request history used for
diagnostics. No account, no telemetry, no cloud service is required, and Fortify Technologies does not receive
anything.

**Goes to a language model, only if you configure one** (only for requests the rules could not place, for
chat, for news summaries and for mood themes it does not know):

- your sentence,
- the name of the speaker's room and the list of room names in your home,
- for chat, the recent conversation, the date and time, and the persona you typed in settings (name, "Who it
  works for", "Who made it", purpose, extra traits),
- if you switched on "Let the language model answer questions about the home": for such a question, up to eight
  devices it appears to be about, with their room and current state,
- for news summaries, the text of the feed items being summarized,
- for a mood or theme it does not know ("make the den feel like a campfire"), the theme words.

Device names and states are **not** sent otherwise. The model is never shown your device list and never
chooses what to control; it only rewrites a sentence, chats, summarizes or suggests colours.

**Where it goes depends on the server you choose.** A local Ollama or llama.cpp server on your own network
stays on your network. A hosted service (a fallback, for example) receives the above over the internet under
that service's own terms. The fallback model is used only if the primary fails.

**Other network use:** news and status feeds fetch the addresses you give (when asked, at most every 10 minutes
per feed). Feed text is only ever read out; it can never trigger an action. Music and weather go through the
Home Assistant integrations you already have.

**Diagnostics** hide API keys, server addresses, feed addresses and the "Who it works for" name, but include
what was said to the assistant and device and room names. Check before sharing.

**Security:** sensitive actions (unlock, disarm, opening a garage door, gate or door, a garage or gate switch,
opening a valve) ask for confirmation by default. Do not turn that off if the assistant is reachable by anyone
you do not trust (for example a speaker near a window), and leave the pipeline's "prefer local" switch off
(section 5), because sentences Home Assistant handles itself are not confirmed. To report a vulnerability, see
`SECURITY.md`.
