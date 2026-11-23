"""Rule-based parsing of one clause into an Intent. No entity knowledge lives here."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .controls import ADJUST_WORDS, APP_ALIASES, APPS, COLORS, OPTION_WORDS, WHITES
from .text import normalize

NUM = r"(\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class Intent:
    verb: str  # turn_on turn_off open close lock unlock disarm pause play stop
    #           set_brightness set_temperature set_volume volume_up volume_down
    #           activate                                              (commands)
    #           query_state query_value                              (questions)
    target: str
    value: float | None = None
    state_word: str | None = None  # for query_state: on off open closed locked unlocked
    domain_hint: str | None = None  # "scene" / "script" when the user said so
    area: str | None = None  # for list/count/any/all questions: "in the kitchen"
    loose: bool = False  # "kitchen lights on": a command only if the target turns out to be a real device
    option: str | None = None  # set_option: a colour, white tone, level or mode word; adjust: brighter/dimmer/up/down

    @property
    def is_query(self) -> bool:
        return self.verb.startswith("query_")


_OPT = "|".join(sorted((re.escape(w) for w in OPTION_WORDS), key=len, reverse=True))
_ADJ = "|".join(sorted(ADJUST_WORDS - {"up", "down"}, key=len, reverse=True))
_AMOUNT = r"(?: (?P<m>a little|a bit|slightly|a lot|much|way))?"
_COL = "|".join(sorted((re.escape(w) for w in COLORS | set(WHITES)), key=len, reverse=True))
_APP = "|".join(sorted((re.escape(a) for a in [*APPS, *APP_ALIASES]), key=len, reverse=True))
_NOT_QUESTION = r"(?!what|who|how|why|when|where|which|is|are|do|does|can)"
_S = r"(?P<state>on|off|open|closed|locked|unlocked)"
_A = r"(?: (?P<area>(?:(?:in|on) .+)|here|upstairs|downstairs|everywhere))?"
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"^(?:which|what) (?P<cls>.+?) (?:are|is) {_S}{_A}$"), "qa_list"),
    (re.compile(rf"^what is {_S}{_A}$"), "qa_list"),
    (re.compile(rf"^how many (?P<cls>.+?) (?:are|is) {_S}{_A}$"), "qa_count"),
    (re.compile(rf"^(?:is|are) (?:there )?(?:(?P<cls>any .+?)|(?P<any>anything|something)) {_S}{_A}$"), "qa_any"),
    (re.compile(rf"^(?:is|are) (?:all|every) (?P<cls>.+?) {_S}{_A}$"), "qa_all"),
    (re.compile(rf"^is everything {_S}{_A}$"), "qa_all"),
    (re.compile(r"^(?:what|which) (?P<cls>.+?) (?:are there|are|do (?:i|we) have|have i got) (?:in|on|at) (?P<area>.+)$"),
     "inv_list"),
    (re.compile(r"^what (?:is|are) (?:in|inside) (?P<area>.+)$"), "inv_things"),
    (re.compile(r"^(?:list|show me|name|give me) (?:all |every )?(?P<cls>.+?)(?: (?:in|on|at) (?P<area>.+))?$"), "inv_list"),
    (re.compile(r"^how many (?P<cls>.+?) (?:are there|are|do (?:i|we) have|have i got)(?: (?:in|on|at) (?P<area>.+))?$"),
     "inv_count"),
    (re.compile(r"^(?:how bright is|what is brightness (?:of|on|for)) (?P<t>.+)$"), "attr_brightness"),
    (re.compile(r"^(?:how loud is|what is volume (?:of|on|for)) (?P<t>.+)$"), "attr_volume"),
    (re.compile(r"^what is (?P<t>.+) set to$"), "attr_setpoint"),
    (re.compile(r"^what is playing(?: (?:on|in) (?P<t>.+))?$"), "playing"),
    (re.compile(r"^(?:what|which) (?:song|track|music|tune) is (?:this|that|playing)(?: right now)?$"), "playing"),
    (re.compile(r"^what is this (?:song|track|tune)$"), "playing"),
    (re.compile(r"^who (?:sings|is singing|plays|is playing|is this by|is that by) (?:this|that)(?: song)?$"), "playing"),
    (re.compile(r"^it is (?:way |a bit |a little |kind of |really |so |pretty |much |a tad )?too "
                r"(?P<w>bright|dark|dim|harsh|hot|warm|cold|cool|chilly|loud|quiet)(?: (?:in|at) (?P<a>.+))?$"), "too"),
    (re.compile(r"^it is (?:so |really |absolutely |pretty |kind of |getting |a bit |a little )?(?P<w>freezing|boiling|"
                r"roasting|sweltering|pitch black|pitch dark|hot|cold|chilly|stuffy)(?: (?:in|at) (?P<a>.+)| (?P<h>here))?$"),
     "too"),
    (re.compile(r"^i (?:need|want) (?:some |a )?(?P<t>.+?)(?: on)?$"), "need"),
    (re.compile(r"^how (?P<w>warm|cold|hot|cool|chilly|humid|muggy) is it(?: (?:in|at) (?P<a>.+)| in here)?$"), "how_warm"),
    (re.compile(r"^(?:is (?:house|home|everything|place|whole house) (?:secure|secured|safe|locked up|locked|all locked up)|"
                r"(?:are we|am i) (?:secure|locked up|all locked up)|"
                r"is everything (?:closed and locked|locked and closed|shut and locked))$"), "secure"),
    (re.compile(r"^(?:what is (?:status of (?:house|home)|house status|going on (?:in|with|at) (?:house|home))|"
                r"how is (?:house|home)|house status|status report|give me (?:a )?(?:status|house) report)$"), "status"),
    (re.compile(r"^(?:what is your name|who are you|what are you called|what should i call you|"
                r"what do they call you|tell me your name)$"), "identity_name"),
    (re.compile(r"^(?:what is your acronym|what does your name stand for)$"), "identity_acronym"),
    (re.compile(r"^what does (.+) stand for$"), "identity_acronym"),
    (re.compile(r"^(?:who (?:made|built|created|programmed|developed|designed) you|"
                r"who is your (?:maker|creator|developer|designer))$"), "identity_maker"),
    (re.compile(r"^(?:what (?:do you do|is your purpose|are you for|is your job)|why do you exist)$"),
     "identity_purpose"),
    (re.compile(r"^(?:who (?:do you work for|owns you|is your owner|do you belong to)|"
                r"who is your (?:boss|master))$"), "identity_owner"),
    (re.compile(r"^set volume (?:on|of|for) (.+) to " + NUM + r"(?: percent)?$"), "set_volume"),
    (re.compile(r"^(?:set|dim|brighten|change|turn|put) (.+?) to " + NUM + r" percent$"), "set_brightness"),
    # "make them 30 percent", "put the lamp at 40 percent"
    (re.compile(r"^(?:make|put|get|have|turn) (.+?) (?:at |to )?" + NUM + r" percent(?: brightness| bright)?$"),
     "set_brightness"),
    (re.compile(r"^turn volume (up|down) (?:on|of|for) (.+)$"), "volume_prefix"),
    (re.compile(r"^(?:set|make|change|turn|put|switch) (?P<t>.+?) (?:to |into )?(?P<o>" + _OPT + r")(?: mode| colou?r)?$"),
     "set_option"),
    (re.compile(r"^(?:set|change) (?P<t>.+?) (?:colou?r )?to (?P<o>" + _OPT + r")$"), "set_option"),
    (re.compile(r"^make (?P<t>it|this room|the room|things)" + _AMOUNT + r" (?P<o>" + _ADJ + r")(?: in (?P<a>.+))?$"),
     "adjust_word"),
    (re.compile(r"^make (?P<t>.+?)" + _AMOUNT + r" (?P<o>" + _ADJ + r")$"), "adjust_word"),
    (re.compile(r"^(?P<o>brighten|dim) (?P<t>.+?)" + _AMOUNT + r"(?: by (?P<n>\d+(?:\.\d+)?)(?: percent)?)?$"),
     "adjust_verb"),
    (re.compile(r"^(?P<o>raise|lower|increase|decrease|reduce|turn up|turn down|bump up|bring down) "
                r"(?P<t>.+?)(?: by (?P<n>\d+(?:\.\d+)?)(?: percent| degrees?)?)?$"), "adjust_verb"),
    (re.compile(r"^(?:turn|bump) (?P<t>.+?) (?P<o>up|down)(?: by (?P<n>\d+(?:\.\d+)?)(?: percent| degrees?)?)?$"),
     "adjust_verb"),
    (re.compile(r"^(?:set|make|change) (?P<t>.+?) (?:to )?(?P<n>\d+(?:\.\d+)?)(?: (?P<u>degrees?))?$"), "set_number"),
    (re.compile(r"^(?:open|close|raise|lower) (?P<t>.+?) (?:half ?way|halfway|half)$"), "cover_half"),
    (re.compile(r"^(?:open|close) (?P<t>.+?) to (?P<n>\d+(?:\.\d+)?) percent$"), "cover_to"),
    (re.compile(r"^mute (?P<t>.+)$"), "mute"),
    (re.compile(r"^unmute (?P<t>.+)$"), "unmute"),
    (re.compile(r"^(?:skip|next track|next song|next)(?: (?:this|that|the) (?:song|track|tune))?"
                r"(?: (?:on|in) (?P<t>.+))?$"), "next_track"),
    (re.compile(r"^(?:skip|play the next) (?:this|that|the) (?:song|track|tune)$"), "next_track"),
    (re.compile(r"^(?:previous track|previous song|previous|go back)(?: (?:on|in) (?P<t>.+))?$"), "previous_track"),
    (re.compile(r"^(?:send|tell|have) (?P<t>.+?) (?:to )?(?:go )?(?:home|dock|return to base|back to base)$"), "dock"),
    (re.compile(r"^(?:dock|recall) (?P<t>.+)$"), "dock"),
    # "vacuum the kitchen", "have dusty clean the living room and the kitchen": a robot cleaning rooms
    (re.compile(r"^(?:have|get|tell|send|let|can) (?P<t>.+?) (?:to )?(?:clean|vacuum|hoover|mop)(?: up)? "
                r"(?:in )?(?:the )?(?P<a>.+)$"), "clean_area"),
    (re.compile(r"^(?:vacuum|hoover|mop|clean)(?: up)? (?:in )?(?:the )?(?P<a>.+)$"), "clean_area"),
    (re.compile(r"^(?:turn|switch|power|shut) (on|off) (.+)$"), "onoff_prefix"),
    (re.compile(r"^(?:power|shut) (down) (.+)$"), "onoff_prefix"),
    (re.compile(r"^(?:turn|switch|power|shut) (.+) (on|off)$"), "onoff_suffix"),
    (re.compile(r"^(?:power|shut) (.+) (down)$"), "onoff_suffix"),
    (re.compile(r"^(?:open|launch|start|pull up|bring up|put on|switch to|go to|load|watch)(?: up)? (?P<app>" + _APP +
                r")(?: app)?(?: (?:on|in) (?P<t>.+))?$"), "launch"),
    (re.compile(r"^(?:put|get|pull up|bring up) (?P<app>" + _APP + r")(?: app)? (?:on|up on) (?P<t>.+)$"), "launch"),
    (re.compile(r"^(open|close|shut|lock|unlock|disarm|pause|resume|stop) (.+)$"), "simple"),
    (re.compile(r"^play (.+)$"), "play"),
    (re.compile(r"^(?:activate|run|start|trigger) (.+)$"), "activate"),
    (re.compile(r"^(?:set|put on|switch to|go to|use|load|apply) (?:the )?(.+ scene)$"), "activate"),  # "set the X scene"
    (re.compile(r"^toggle (.+)$"), "toggle"),
    (re.compile(r"^is (.+) (on|off|open|closed|locked|unlocked)$"), "query_state"),
    (re.compile(rf"^are (?P<cls>.+?) {_S}{_A}$"), "are"),
    (re.compile(r"^" + _NOT_QUESTION + r"(?P<t>.+?) (?:to |at )?(?P<n>\d+(?:\.\d+)?) percent$"), "implicit_percent"),
    (re.compile(r"^" + _NOT_QUESTION + r"(?P<t>.+?) (?:to )?(?P<o>" + _COL + r")$"), "implicit_color"),
    (re.compile(r"^(?!what|who|how|why|when|where|which)(.+) (on|off)$"), "onoff_bare"),
    (re.compile(r"^(?:what is|what are|how is) (.+)$"), "query_value"),
]
_SIMPLE = {"resume": "play", "stop": "stop", "shut": "close"}


def _area(raw: str | None) -> str | None:
    text = re.sub(r"^(?:in|on) ", "", (raw or "").strip())
    return text or None


_WHERE = r"(?P<w> (?:in|on|at) .+| downstairs| upstairs| everywhere| here)?"
_WANT = r"(?:i|we)(?: want| need| would like|'d like| d like| wanna have)"
_BASE = {"turning": "turn", "switching": "switch", "shutting": "shut", "opening": "open", "closing": "close",
         "locking": "lock", "unlocking": "unlock", "dimming": "dim", "setting": "set", "putting": "put",
         "pausing": "pause", "starting": "start", "stopping": "stop", "killing": "kill", "muting": "mute",
         "raising": "raise", "lowering": "lower", "brightening": "brighten", "getting": "get", "making": "make"}
_GERUND = re.compile(r"^(" + "|".join(_BASE) + r") (.+)$")
_REWRITES: list[tuple[re.Pattern, str]] = [  # say it the plain way first; the patterns below do the rest
    (re.compile(r"^(?P<v>turn|switch|shut|power) (?P<t>.+) of$"), r"\g<v> \g<t> off"),  # "turn the lights of"
    (re.compile(r"^(?:(?:set|make|turn|put|change) )?(?:volume|sound)(?: level)? (?:to |at )?(?P<n>\d+(?:\.\d+)?)"
                r"(?: percent)?$"), r"set volume on music to \g<n>"),  # "volume 40 percent", "set the volume to 30"
    (re.compile(r"^(?:(?:turn|bring|put) )?(?P<d>up|down) (?:volume|sound) (?:on|of|for|in) (?P<t>.+)$"), r"turn \g<d> \g<t>"),
    (re.compile(r"^(?:volume|sound) (?P<d>up|down) (?:on|of|for|in) (?P<t>.+)$"), r"turn \g<d> \g<t>"),
    (re.compile(r"^(?:(?:turn|bring|put) )?(?P<t>(?!turn\b|bring\b|put\b).+?) (?:volume|sound) (?P<d>up|down)$"),
     r"turn \g<d> \g<t>"),  # "tv volume up", "turn the tv volume up"
    (re.compile(r"^(?:turn|bring|put) (?P<d>up|down) (?P<t>.+?) (?:volume|sound)$"), r"turn \g<d> \g<t>"),
    (re.compile(r"^(?P<h>.+ (?:up|down)) (?:a bit|a little bit|a little|slightly|a tad|a touch|some|a lot|more)$"), r"\g<h>"),
    (re.compile(r"^(?:(?:make|turn) )?(?P<t>music|sound|volume|tv|it) (?:a bit |a little |much |way )?"
                r"(?P<o>louder|quieter)$"), r"make \g<t> \g<o>"),
    (re.compile(r"^(?P<v>turn|switch|shut|power) of (?P<t>.+)$"), r"\g<v> off \g<t>"),
    (re.compile(r"^(?:crank|bump|kick|push|bring|take|raise|lower|drop|move)(?: up| down)? (?P<t>.+?)(?: up| down)? "
                r"to (?P<n>\d+(?:\.\d+)?)(?P<u> degrees?| percent)?$"), r"set \g<t> to \g<n>\g<u>"),
    (re.compile(r"^put (?P<t>.+?) (?:at|to|on) (?P<n>\d+(?:\.\d+)?)$"), r"set \g<t> to \g<n>"),
    (re.compile(r"^(?:set|turn|put|change) (?P<t>.+?) volume (?:to|at) (?P<n>\d+(?:\.\d+)?)(?: percent)?$"),
     r"set volume on \g<t> to \g<n>"),
    (re.compile(r"^(?:turn off|shut off|switch off|deactivate|disable) (?P<t>(?:.+ )?alarm(?: system)?)$"),
     r"disarm \g<t>"),
    (re.compile(r"^(?:make sure|ensure|see that|make certain)(?: that)? (?P<t>.+?) (?:is|are|gets?) locked(?: up)?$"),
     r"lock \g<t>"),
    (re.compile(r"^(?:make sure|ensure|see that|make certain)(?: that)? (?P<t>.+?) (?:is|are|gets?) (?:closed|shut)$"),
     r"close \g<t>"),
    (re.compile(r"^(?:make sure|ensure|see that|make certain)(?: that)? (?P<t>.+?) (?:is|are|gets?) "
                r"(?:turned |switched )?(?P<s>on|off)$"), r"turn \g<s> \g<t>"),
    (re.compile(r"^(?P<t>.+?) (?:needs?|has|have|should|ought)(?: to)? be (?:turned |switched )?(?P<s>on|off)$"),
     r"turn \g<s> \g<t>"),
    (re.compile(r"^(?P<t>.+?) (?:needs?|has|have|should|ought)(?: to)? be (?:closed|shut)$"), r"close \g<t>"),
    (re.compile(r"^(?P<t>.+?) (?:needs?|has|have|should|ought)(?: to)? be locked$"), r"lock \g<t>"),
    (re.compile(r"^" + _WANT + r" (?P<t>.+?) (?:to be |turned |switched |to go )?(?P<s>on|off)$"), r"turn \g<s> \g<t>"),
    (re.compile(r"^" + _WANT + r" (?P<t>.+?) (?:running|going|started)$"), r"turn on \g<t>"),
    (re.compile(r"^" + _WANT + r" (?P<t>.+?) to be (?P<o>.+)$"), r"set \g<t> to \g<o>"),
    (re.compile(r"^(?:can|could|may) (?:we|i) (?:have|get) (?P<t>.+?) (?P<s>on|off)$"), r"turn \g<s> \g<t>"),
    (re.compile(r"^(?:pop|throw|pull|swing|crack|get|prop) (?P<t>.+?) open(?P<w> (?:in|on|at) .+)?$"), r"open \g<t>\g<w>"),
    (re.compile(r"^(?:pop|throw|pull|swing|get) (?P<t>.+?) (?:closed|shut)(?P<w> (?:in|on|at) .+)?$"), r"close \g<t>\g<w>"),
    (re.compile(r"^draw (?P<t>.*(?:curtains?|blinds?|shades?|drapes?)\b.*)$"), r"close \g<t>"),
    (re.compile(r"^(?:silence|shush|hush|quiet|quieten) (?P<t>.+)$"), r"mute \g<t>"),
    (re.compile(r"^(?:check|tell me|do you know|can you tell me|let me know|see|find out|check to see) (?:if|whether) "
                r"(?P<t>.+?) (?P<b>is|are) (?P<s>.+)$"), r"\g<b> \g<t> \g<s>"),
    (re.compile(r"^(?P<b>is|are) (?P<t>.+) (?:running|going|lit|turned on|switched on)$"), r"\g<b> \g<t> on"),
    (re.compile(r"^(?P<b>is|are) (?P<t>.+) (?:shut|closed up)$"), r"\g<b> \g<t> closed"),
    (re.compile(r"^(?P<h>.+) locked up$"), r"\g<h> locked"),
    (re.compile(r"^(?P<h>.+) (?:left|still|currently) (?P<s>on|off|open|closed|locked|unlocked)(?P<w> .+)?$"),
     r"\g<h> \g<s>\g<w>"),
    (re.compile(r"^how (?P<q>warm|cold|hot|cool|chilly) is (?!it\b)(?P<a>.+)$"), r"how \g<q> is it in \g<a>"),
    (re.compile(r"^(?:kill|cut|douse|put out|knock out|turn out|shut off|switch off|power off|shut down) (?P<t>.+)$"),
     r"turn off \g<t>"),
    (re.compile(r"^(?:fire up|light up|power up|start up) (?P<t>.+)$"), r"turn on \g<t>"),
    (re.compile(r"^(?:(?:can|could) (?:we|i|you) )?(?:get|have|give me) (?:some |a little |more )?light"
                r"(?P<w> (?:in|on|at) .+| here| in here)$"), r"turn on lights\g<w>"),
    (re.compile(r"^(?:get|have) (?P<t>.+?) (?:going|started|running|on)$"), r"turn on \g<t>"),
    (re.compile(r"^lights? (?P<s>out|off)" + _WHERE + r"$"), r"turn off lights\g<w>"),
    (re.compile(r"^lights? (?P<s>on|up)" + _WHERE + r"$"), r"turn on lights\g<w>"),
    (re.compile(r"^(?P<t>everything|all lights|all|lights) (?P<s>on|off)" + _WHERE + r"$"), r"turn \g<s> \g<t>\g<w>"),
    (re.compile(r"^(?P<v>turn|switch|shut|power) (?P<t>.+?) (?P<s>on|off) (?P<w>(?:in|on|at) .+|downstairs|upstairs|everywhere)$"),
     r"turn \g<s> \g<t> \g<w>"),
    (re.compile(r"^(?:shut|power) (?P<t>.+?) down(?P<w> (?:in|on|at) .+| downstairs| upstairs)?$"), r"turn off \g<t>\g<w>"),
    (re.compile(r"^(?:kill|stop|cut|turn off|shut off) (?P<t>(?:the )?(?:music|song|tunes|audio|podcast|audiobook))"
                r"(?P<w> (?:in|on) .+)?$"), r"pause \g<t>\g<w>"),
    (re.compile(r"^(?:crank up|crank|pump up|blast) (?P<t>.+?)$"), r"turn up \g<t>"),
    (re.compile(r"^(?P<v>turn|bump) (?P<t>.+?) (?P<d>up|down) (?P<w>(?:in|at) .+|here)$"), r"\g<v> \g<t> \g<w> \g<d>"),
    (re.compile(r"^(?:give me|i want|set|put) (?:full|max|maximum) (?:brightness|light)(?: (?P<w>(?:in|on) .+|here))?$"),
     r"set lights \g<w> to 100 percent"),
    (re.compile(r"^put (?P<t>.+?) (?:on|at) (?P<n>\d+(?:\.\d+)?) percent$"), r"set \g<t> to \g<n> percent"),
    (re.compile(r"^put (?P<t>.+?) on pause$"), r"pause \g<t>"),
    (re.compile(r"^put (?P<t>.+?) (?P<s>on|off)$"), r"turn \g<s> \g<t>"),
    (re.compile(r"^(?P<t>.+?) (?:turn|switch) (?:it|them) (?P<s>on|off)$"), r"turn \g<s> \g<t>"),
    (re.compile(r"^(?P<v>open|close|shut) up (?P<t>.+)$"), r"\g<v> \g<t>"),
    (re.compile(r"^(?P<v>brighten|dim) (?P<t>it|things|this room) (?:up|down)(?P<w>.*)$"), r"\g<v> \g<t>\g<w>"),
    (re.compile(r"^(?P<v>set|put|turn) (?P<t>.+?) at (?P<o>half|halfway|full|max|maximum)$"), r"\g<v> \g<t> to \g<o>"),
    (re.compile(r"^i (?:can't|cannot|cant|can not) see(?: anything| a thing)?" + _WHERE + r"$"), r"turn on lights\g<w>"),
    (re.compile(r"^it is (?:(?:really|so|too|pretty|very|kind of|getting) )*dark" + _WHERE + r"$"), r"turn on lights\g<w>"),
    (re.compile(r"^(?:i am|i'm|im|we are|we're) (?:(?:so|really|a bit|a little|kind of|too|very|getting) )*"
                r"(?:cold|freezing|chilly)(?P<w> (?:in|at) .+| here)?$"), r"it is too cold\g<w>"),
    (re.compile(r"^(?:i am|i'm|im|we are|we're) (?:(?:so|really|a bit|a little|kind of|too|very|getting) )*"
                r"(?:hot|warm|boiling|roasting|sweating|burning up)(?P<w> (?:in|at) .+| here)?$"), r"it is too hot\g<w>"),
    (re.compile(r"^(?:get|hit|flip|flick|grab) (?P<t>lights?)" + _WHERE + r"$"), r"toggle lights\g<w>"),
    (re.compile(r"^(?P<v>pause|resume|stop|mute|unmute|play)$"), r"\g<v> music"),
    (re.compile(r"^(?:turn )?(?P<t>volume|sound) (?P<d>up|down)$"), r"turn \g<t> \g<d>"),
    (re.compile(r"^lock (?:up|up house|all doors|doors|every door|everything|house|all locks|locks)$"), r"lock all doors"),
    (re.compile(r"^did (?:i|we|you|someone|anyone|somebody) leave (?P<t>(?:any |all )?.+s) (?P<s>on|open|unlocked)$"),
     r"are \g<t> \g<s>"),
    (re.compile(r"^did (?:i|we|you|someone|anyone|somebody) leave (?P<t>.+?) (?P<s>on|open|unlocked)$"),
     r"is \g<t> \g<s>"),
    (re.compile(r"^is (?P<t>(?:.+ )?alarm(?: system)?) (?:on|armed|set|off|disarmed|active)$"), r"what is \g<t>"),
]
_REWRITES += [  # a colour said with only a place: "make it purple in here", "warm white in the bedroom"
    (re.compile(r"^(?:make|turn|set|paint) (?:it|everything|things|lights) (?P<o>" + _COL + r") (?P<w>(?:in|on|at) .+)$"),
     r"set lights \g<w> to \g<o>"),
    (re.compile(r"^(?:make|turn|set|paint) (?:it|everything|things|lights) (?P<o>" + _COL + r") here$"),
     r"set lights in here to \g<o>"),
    (re.compile(r"^(?P<o>" + _COL + r") (?P<w>(?:in|on|at) .+)$"), r"set lights \g<w> to \g<o>"),
]


def rewrite(text: str) -> str:
    if (m := _GERUND.match(text)):  # "(would you mind) turning off the lamp"
        text = f"{_BASE[m.group(1)]} {m.group(2)}"
    for _ in range(4):  # "kill the music" -> "turn off music" -> "pause music"
        for pattern, plain in _REWRITES:
            if pattern.match(text):
                text = pattern.sub(plain, text, count=1).strip()
                break
        else:
            return text
    return text


def parse(text: str) -> Intent | None:
    """`text` must already be normalised."""
    text = rewrite(text)
    for pattern, kind in _PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        g = m.groups()
        gd = m.groupdict()
        if kind.startswith("qa_"):
            cls = (gd.get("cls") or "").strip()
            if cls.startswith("any "):
                cls = cls[4:]
            if kind == "qa_all" and not cls:
                cls = "everything"
            if kind != "qa_count" and not cls:
                cls = "anything"
            return Intent(f"query_{kind[3:]}", cls, state_word=gd["state"], area=_area(gd.get("area")))
        if kind in ("inv_list", "inv_things", "inv_count"):
            verb = "query_count" if kind == "inv_count" else "query_list"
            return Intent(verb, (gd.get("cls") or "things").strip(), area=_area(gd.get("area")))
        if kind == "launch":  # "open plex on the bar tv"; with no TV named, the one in this room
            return Intent("launch", (gd.get("t") or "tv").strip(), option=APP_ALIASES.get(gd["app"], gd["app"]))
        if kind in ("secure", "status"):
            return Intent(f"query_{kind}", "")
        if kind == "too":  # "it's too bright in the workshop"
            option = {"bright": "dimmer", "harsh": "dimmer", "dark": "brighter", "dim": "brighter", "hot": "cooler",
                      "warm": "cooler", "cold": "warmer", "cool": "warmer", "chilly": "warmer", "loud": "quieter",
                      "quiet": "louder", "freezing": "warmer", "boiling": "cooler", "roasting": "cooler",
                      "sweltering": "cooler", "pitch black": "brighter", "pitch dark": "brighter",
                      "stuffy": "cooler"}[gd["w"]]
            area = _area(gd.get("a")) or gd.get("h")
            return Intent("adjust", f"it in {area}" if area else "it", option=option)
        if kind == "are":  # "are the workshop lights on": which of them are
            return Intent("query_list", gd["cls"].strip(), state_word=gd["state"], area=_area(gd.get("area")))
        if kind == "how_warm":  # "how warm is it in the living room": the room's sensor
            sensor = "humidity" if gd["w"] in ("humid", "muggy") else "temperature"
            return Intent("query_value", f"{sensor} in {gd['a']}" if gd.get("a") else f"{sensor} in here")
        if kind == "need":  # "i need the garage lights": a command only if that is a real device
            return Intent("turn_on", gd["t"].strip(), loose=True)
        if kind == "implicit_percent":
            return Intent("set_brightness", gd["t"].strip(), float(gd["n"]), loose=True)
        if kind == "implicit_color":
            return Intent("set_option", gd["t"].strip(), option=gd["o"], loose=True)
        if kind == "set_option":
            if gd["t"].strip() in ("up", "down"):  # "turn up the heat" is a change, not the heat mode
                continue
            return Intent("set_option", re.sub(r" colou?r$", "", gd["t"].strip()), option=gd["o"])
        if kind == "toggle":
            return Intent("toggle", g[0].strip())
        if kind == "adjust_word":
            amount = {"a little": 0.5, "a bit": 0.5, "slightly": 0.5, "a lot": 2, "much": 2, "way": 2}.get(gd.get("m"), 1)
            return Intent("adjust", gd["t"].strip(), value=None if amount == 1 else amount, option=gd["o"],
                          area=_area(gd.get("a")), state_word="scale" if amount != 1 else None)
        if kind == "adjust_verb":
            up = gd["o"] in ("raise", "increase", "brighten", "turn up", "bump up", "up")
            amount = float(gd["n"]) if gd.get("n") else None
            scale = {"a little": 0.5, "a bit": 0.5, "slightly": 0.5, "a lot": 2, "much": 2, "way": 2}.get(gd.get("m"))
            return Intent("adjust", gd["t"].strip(), value=amount if amount is not None else scale,
                          option="up" if up else "down", state_word="scale" if (amount is None and scale) else None)
        if kind == "set_number":
            return Intent("set_temperature" if gd.get("u") else "set_number", gd["t"].strip(), float(gd["n"]),
                          option="degrees" if gd.get("u") else None)
        if kind == "cover_half":
            return Intent("set_brightness", gd["t"].strip(), 50.0)
        if kind == "cover_to":
            return Intent("set_brightness", gd["t"].strip(), float(gd["n"]))
        if kind == "clean_area":  # target: the robot if one was named; area: the rooms
            return Intent("clean_area", (gd.get("t") or "").strip(), area=gd["a"].strip())
        if kind in ("mute", "unmute", "next_track", "previous_track", "dock"):
            return Intent(kind, (gd.get("t") or "").strip())
        if kind.startswith("attr_"):
            return Intent("query_attr", gd["t"].strip(), state_word=kind[5:])
        if kind == "playing":
            return Intent("query_playing", (gd.get("t") or "").strip())
        if kind.startswith("identity_"):
            return Intent(kind, (g[0] if g else "").strip())
        if kind in ("set_volume", "set_brightness", "set_temperature"):
            return Intent(kind, g[0].strip(), float(g[1]))
        if kind == "volume_prefix":
            return Intent(f"volume_{g[0]}", g[1].strip())
        if kind == "volume_suffix":
            return Intent(f"volume_{g[1]}", g[0].strip())
        if kind == "onoff_prefix":
            return Intent("turn_off" if g[0] in ("off", "down") else "turn_on", g[1].strip())
        if kind == "onoff_bare":
            return Intent("turn_off" if g[1] == "off" else "turn_on", g[0].strip(), loose=True)
        if kind == "onoff_suffix":
            return Intent("turn_off" if g[1] in ("off", "down") else "turn_on", g[0].strip())
        if kind == "simple":
            return Intent(_SIMPLE.get(g[0], g[0]), g[1].strip())
        if kind == "play":
            return Intent("play", g[0].strip())
        if kind == "activate":
            target, hint = g[0].strip(), None
            for word in ("scene", "script"):
                if target.endswith(" " + word):
                    target, hint = target[: -len(word) - 1].strip(), word
            return Intent("activate", target, domain_hint=hint)
        if kind == "query_state":
            return Intent("query_state", g[0].strip(), state_word=g[1])
        if kind == "query_value":
            return Intent("query_value", g[0].strip())
    return None


_BREAKS = re.compile(r"\s*(?:,|;)\s*|\s+(?:and then|and also|then|also|after that)\s+|\s+and\s+", re.I)
_INHERIT = {"turn_on": "turn on", "turn_off": "turn off", "open": "open", "close": "close", "lock": "lock",
            "unlock": "unlock", "mute": "mute", "unmute": "unmute", "pause": "pause"}


_NAMED_FIRST = re.compile(r"^(?P<t>.+?) (?:can you |could you |please |would you )?(?:turn|switch) (?:it|them) "
                          r"(?P<s>on|off)$")


def split_clauses(pieces: list[str], can_resolve=None) -> list[str]:
    """Join normalised pieces into clauses. Split only where it is safe: both halves are complete
    commands, or the second is a bare device ('turn off the lamp and the fan') that really exists.
    Names containing 'and' ('salt and pepper lamp') and questions ('pride and prejudice') stay whole."""
    pieces = [p for p in pieces if p]
    if len(pieces) < 2:
        return pieces
    whole = " and ".join(pieces)
    if can_resolve and (intent := parse(whole)) and not intent.is_query and can_resolve(whole):
        return [whole]  # one device whose name contains 'and'
    clauses: list[str] = []
    current = pieces[0]
    for nxt in pieces[1:]:
        here, there = parse(current), parse(nxt)
        if here and there:
            clauses.append(current)
            current = nxt
        elif here and not there and here.verb in _INHERIT and can_resolve:
            inherited = f"{_INHERIT[here.verb]} {nxt}"
            if can_resolve(inherited):
                clauses.append(current)
                current = inherited
                continue
            current = current + " and " + nxt
        else:
            current = current + " and " + nxt
    clauses.append(current)
    return clauses


def prepare(utterance: str, can_resolve=None) -> list[str]:
    """Normalise and split a spoken request into clauses. `can_resolve(clause_text)` says whether the
    target of a (possibly verb-less) clause is a real device; without it only complete clauses split."""
    whole = normalize(utterance)
    if (m := _NAMED_FIRST.match(whole)):  # "the lamp in the bedroom, turn it off": one command, not two
        return [f"turn {m.group('s')} {m.group('t')}"]
    pieces = [re.sub(r"^(?:and|then|also)\s+", "", p.strip(), flags=re.I) for p in _BREAKS.split(utterance)]
    return split_clauses([normalize(p) for p in pieces], can_resolve)
