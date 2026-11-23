# Changelog

## 0.1.8

Safety and honesty:
- A confirmation only counts when "yes" is the whole reply. Before, "ok, turn off the kitchen lights" said after
  "Are you sure you want me to unlock the front door?" unlocked the door.
- Talk about the home that the rules do not understand no longer goes to the language model by default, because
  small models invent device states. It gets a plain "I'm not sure" or "which device or room". A new setting
  allows it for larger models.
- The time, the date and simple sums are worked out, never generated.
- An error inside the assistant is answered ("Something went wrong on my end") and logged, never raised.
- One device failing no longer stops the others in the same request, and calls to the same service go out together.

Understands more, with no language model:
- A light or fan named alone is switched: "lights", "kitchen lights", "the lamp", or just the room ("office").
  Nothing else is switched by its name alone.
- "Which room's lights?" now takes the room as the answer.
- Wishes and politeness: "I'd like the lamp on", "we need the printer on", "can we have the strip off",
  "the vanity light needs to be on", "would you mind turning off the lamp", "... would you", "let's ...".
- "Make sure the front door is locked", "draw the curtains", "pop the blinds open", "crank the thermostat to 74",
  "put the kitchen speaker at 30", "silence the speaker", "start the coffee maker".
- Slips: "turn the lights of", "turn of the lights".
- The command inside a sentence: "it's bedtime, turn off everything upstairs", "the office is stuffy, get the fan
  going". Questions and conditions ("how do I turn off...", "when I get home...") are never treated as commands.
- A word from a device's name asks instead of failing: "get the coffee going" asks "Did you mean Coffee Maker?".
- Questions: "check if the garage is closed", "is the back door locked up", "any lights left on upstairs",
  "how hot is the bedroom". Spelling: "spell necessary".
- On their own: "pause", "stop", "volume up", a scene or script name ("movie night", "let's have movie night").
  A scene or script you named always wins over a built-in meaning. A greeting that is also a script's name
  ("good night") asks before it runs.
- "I'm cold", "it's hot in here", "turn up the heat", "its too dark", "lock up", "toggle the lamp",
  "get the lights", "turn off all the lights" (the whole home when no room is known).
- "Is the house secure", "what's the status of the house", "is the alarm on", "did I leave the garage open",
  "what lights are on upstairs", "what rooms are there", "what scenes do I have".
- Replies say what changed: "Setting Thermostat to 72 degrees", "Pausing Kitchen Speaker", "Dimming ...".

Works with Home Assistant's own Assist:
- Timers, shopping lists, sentence triggers and custom sentences are handed to Home Assistant's built-in Assist
  when Fortify does not understand a request (new setting, on by default). If it cannot help either, timers and
  schedules are declined plainly.

Conversation flow:
- The microphone stays open only for a real question (which one, which room, are you sure). Before, Home
  Assistant reopened it after any reply ending in "?", including chat, so a room with a TV on could get asked
  "which device or room?" again and again.
- The same question is never asked twice in a row: after one "which device or room?" that goes nowhere, the
  reply is a statement and the microphone closes until the wake word. It is never reopened more than three
  times in a row.
- An answer to "which one?" is treated as an answer: a room ("living room", "the den one"), "both", "the
  first one" or "no". Before, a room name given as the answer switched that room's lights.
- Recent requests (what was heard, decided and said) are kept across restarts for diagnostics.

TVs, speakers and rooms:
- "The TV" means the television in the speaker's room, not the streaming stick plugged into it, a soundbar
  or a light called "TV Lights". "Television" and "telly" work; "all the TVs" works.
- "TV lights" is always a light, and "bedroom light" is never the bedroom TV or fan.
- Volume: "set the volume to 30", "volume 40 percent", "tv volume up", "turn up the volume on the tv",
  "music louder", "turn it up a bit". With music playing in the room, "turn it up" is the volume.
- Room aliases from Home Assistant are understood ("the lab").
- A device type the room does not have: the home's only one is used ("turn on the fan"), or you are asked
  which of the others.
- The only device a word could mean is used without asking ("the printer", "the heater").
- "The overhead light" in a room whose overhead is two bulbs switches both; "light a" means this room's.
- "What's the temperature" is answered by the thermostat when the room has no sensor; "set the temperature
  to 68" works.
- What speech-to-text gets wrong: "lites", "trun on", "three d printer".
- A scene that has never been run is no longer treated as unavailable.
- Apps on a TV: "open Plex on the bar TV", "put Netflix on the TV", "open YouTube" (the TV in the room).
  Android TV and Google TV are asked by package, a TV whose source list has the app by that source, and a
  TV that lists only inputs by the app's title. About 18 common apps are known.
- Casual follow-ups: "actually I need them back on", "oh wait, turn them back on", "never mind, turn it
  off".
- Chat can never say it changed a device, whatever was asked. Before, "actually I need them back on" got
  "switching those four lights back on now" from the model with nothing done.
- A chat reply that only recites who the assistant is, to a question that was not about it, is not spoken.

Language model path:
- Measured on 35 harder phrasings: the rules alone now get 30 right (7 before) and 33 with a 2B model
  (22 to 25 before), with no wrong actions. Before, "draw the curtains" opened them and "turn the lights of"
  turned them on.
- A model's "front_door" is read as "front door"; "draw" can never become open.
- A request with "off" in it is never read as a mood. Before, "it's bedtime, turn off everything upstairs"
  turned the upstairs lights on as a "bedtime" mood.
- The model is loaded when Home Assistant starts, so the first request is not the slow one.

Chat:
- A much shorter prompt for general questions, a lower temperature and a length cap. Replies are shorter,
  faster and no longer bring up the home or the date unasked. Emoji are never spoken.
- Language model requests give up after 30 seconds.
- Questions that need live information (scores, prices, what flew over) get "I'm not sure" instead of a guess,
  unless a feed, the weather or one of your Home Assistant sentences answers them.

Follow-ups and undo:
- "Change them back", "put it back", "undo that" restore what the last change altered: each light's own colour,
  brightness and on/off state. "Change them back to white" and "make them white again" just set white.
- "Make it louder in the bar" means the bar, even right after talking about something else.

Music:
- "Play some music down here", "play music in the kitchen", "put some music on", or just "music": plays on the
  speaker in that room. It carries on with what the speaker had queued, else the most recently played playlist,
  else asks what to play. Room words are never searched for as a song title.
- A misheard number at the end of a title ("road trip to" for "road trip 2", "II") still finds it, and your own
  playlists are checked when a search finds nothing. Music already playing is not restarted.
- Without Music Assistant, song searches say it is needed.

Vacuums:
- "Vacuum the kitchen", "clean the living room and the kitchen", "have the robot mop the kitchen": a robot that
  can clean chosen rooms is sent to them. A robot is never sent to another floor; a robot that only cleans
  everything is started when it is the only one on that floor. "Turn on the vacuum" starts it.
- "The vacuum" means the one that is working when another with the same name is offline.

Safety:
- Opening a garage, gate or door asks first whatever device class it has, and so does a garage or gate opener
  wired as a switch, and opening a valve.
- Sentences about unlocking, disarming or opening a door are never handed to Home Assistant's built-in agent,
  which would not ask first.
- Words that only share consonants no longer match ("dryer" is not "door", "attic" is not "deck"), and a type
  of device you name ("the curtain lights") is never swapped for another kind of device.
- A stray letter ("a") or a negative setting ("-5 percent") does nothing. A language model can no longer turn a
  cleaning request into a light switch.
- Language model errors no longer contain the server address, and the owner name is hidden in diagnostics.

Fresh homes:
- With no rooms set for any light, "turn on the lights" switches all of them instead of asking which room, and
  "all of them" answers "which room?".
- Devices that never report a state (state "unknown") can be controlled; only "unavailable" ones are refused.
- "Living room lights" are the room's lights, not one device named "Living Room TV Lights".
- "Set the X scene", "turn off the thermostat", "let's have movie night", "uh can you like turn the lamp off",
  "... or whatever".

## 0.1.7

- Lettered sets: "make the game room overhead lights blue" changes Game Room Overhead Light A and B together
  instead of asking which one.
- Ceiling words mean the same light: overhead, ceiling, can, recessed and pot. "The den can lights" finds
  "Den Recessed Lights".
- A name said without a room means the one in the speaker's own room, when that room has it.
- "Turn these on" with nothing said before means the lights in the speaker's room.

## 0.1.6

- Follow-ups: each speaker remembers what it just did for 5 minutes, even across wake words. "Turn them off",
  "make them blue", "brighter", "do that again", "and the kitchen", "what about the bedroom".
- Chat remembers what was just said on that speaker.
- The assistant no longer says it can't control the home. "What can you do" and "can you control the lights" are
  answered from your own devices, and a reply that denies control or makes up rooms is replaced by a question.
- A dead device no longer blocks its room: "turn off the bar lights" uses the room's working lights when a device
  called Bar Lights is unavailable. If nothing there works, it says so.
- "It's dark in the workshop" turns the lights on.

## 0.1.5

- News and status feeds: give each feed a name and an RSS or Atom address in settings (up to 20). Ask by name
  ("any launches today", "what's new in sports"), for the news, for a topic, or whether a service is down.
  Headlines are read by default; summaries by a language model are optional and experimental.
- Weather from your weather entity: now, today and tomorrow, rain and umbrella questions.
- Feeds are read whole, and a feed that will not refresh is said to be out of date (and dropped after 30
  minutes) instead of repeating old headlines. Failures are logged as warnings.
- Chat never answers a question with a bare "Done." or "Success".
