"""Asking for music: songs, artists, albums, playlists, genres and moods, audiobooks, podcasts.

Parsing keeps what the user said (numbers, "the", capital-free but otherwise intact) because titles
matter: "Seven Nation Army" must not become "7 nation army". Choosing among search results is
deterministic and honest: if nothing matches well enough the answer is "I couldn't find that", never a
guess that plays something unrelated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from .text import _FILLER_PREFIX, _FILLER_SUFFIX, phrase_sim, tokens

MEDIA_TYPES = ("track", "album", "artist", "playlist", "radio", "audiobook", "podcast")
GENRES = {
    "jazz", "rock", "pop", "hip hop", "hiphop", "rap", "country", "classical", "blues", "metal", "punk", "edm",
    "electronic", "techno", "house", "lofi", "lo-fi", "chill", "relaxing", "relaxation", "calm", "ambient",
    "workout", "gym", "running", "study", "focus", "sleep", "party", "dinner", "cooking", "christmas",
    "holiday", "halloween", "summer", "beach", "road trip", "roadtrip", "karaoke", "indie", "folk", "soul",
    "r&b", "rnb", "reggae", "latin", "disco", "funk", "80s", "90s", "70s", "60s", "2000s", "oldies", "classic rock",
    "love songs", "sad songs", "happy", "upbeat", "acoustic", "instrumental", "movie soundtracks", "soundtracks",
    "background music", "dance", "romantic", "spooky", "scary",
}
RESUME = {"", "music", "some music", "the music", "it", "again", "something", "some", "songs", "audio", "playback",
          "my music", "tunes", "some tunes", "something on", "a song", "some songs"}
_PLAY = re.compile(
    r"^(?:play|put on|queue up|queue|start|listen to|i want to hear|i want to listen to|let s listen to|let s hear|"
    r"i would like to hear|i would like to listen to)\s+(?P<rest>.*)$")
_READ = re.compile(r"^(?:read me|read|narrate)\s+(?P<rest>.+)$")
_BARE = {"music", "some music", "music please", "tunes", "some tunes", "a song", "songs"}  # said with no verb at all
_HERE = re.compile(r"\s+(?:(?:right|down|up|out|over|back)\s+)?(?:in\s+)?here$|\s+in (?:this|the) room$")
_HERE_LOOSE = re.compile(r"\s+(?:(?:right|down|up|out|over|back)\s+here|in here|in (?:this|the) room)$")
# what speech-to-text writes for a number at the end of a title: "road trip mix to" is "road trip mix 2"
_SAID_NUMBER = {"to": "2", "too": "2", "two": "2", "for": "4", "four": "4", "won": "1", "one": "1", "tree": "3",
                "three": "3", "ate": "8", "eight": "8"}
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def light_normalize(text: str) -> str:
    """Lower-case and strip punctuation and politeness, but keep every word of a possible title."""
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9'&\- ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    changed = True
    while changed:
        changed = False
        for f in _FILLER_PREFIX:
            if t.startswith(f + " "):
                t, changed = t[len(f) + 1:], True
        for f in _FILLER_SUFFIX:
            if t.endswith(" " + f):
                t, changed = t[: -len(f) - 1], True
    return t.replace("let's", "let s")


@dataclass(frozen=True)
class MusicRequest:
    query: str  # what to play, in the user's words ("" means: resume or ask which)
    media_type: str | None = None  # one of MEDIA_TYPES, or None to let the search decide
    artist: str | None = None
    genre: bool = False  # a style or mood ("jazz", "something relaxing"), so prefer playlists


@dataclass(frozen=True)
class MusicHit:
    uri: str
    name: str
    media_type: str
    artists: tuple[str, ...] = ()
    library: bool = False  # already in the user's library (preferred on a tie)


class MusicProvider(Protocol):
    async def search(self, query: str, artist: str | None = None, limit: int = 6) -> dict[str, list[MusicHit]]: ...

    async def library(self, media_type: str, limit: int = 25) -> list[MusicHit]: ...


def _strip_lead(rest: str) -> tuple[str, bool]:
    """Drop 'some', 'a few', 'the', 'my' etc. and report whether 'some/something' was said."""
    some = bool(re.match(r"^(?:some|something|a few|any|a bit of)\b", rest))
    rest = re.sub(r"^(?:some|something like|something|a few|any|a bit of|the|my|a|an)\s+", "", rest)
    return rest.strip(), some


def verb_split(text: str) -> tuple[str, str | None] | None:
    """('what follows the verb', media hint) for a play/read sentence, else None."""
    t = light_normalize(text)
    if t in _BARE:  # "Music." on its own: play something here
        return t, None
    t = re.sub(r"^put (.+?) on$", r"put on \1", t)  # "put some music on"
    t = re.sub(r"^(?:turn|switch) on (?=(?:some |the |my )?(?:music|tunes)\b)", "play ", t)
    if (m := _READ.match(t)):
        return m.group("rest").strip(), "audiobook"
    if (m := _PLAY.match(t)):
        return m.group("rest").strip(), None
    return None


def interpret(rest: str, media_hint: str | None = None) -> MusicRequest | None:
    """What to play, from the words after the verb with any speaker phrase already removed.
    None means 'just play': resume what was queued."""
    rest = rest.strip()
    if rest in RESUME:
        return None
    core, some = _strip_lead(rest)
    artist = None
    if (m := re.match(r"^(?:music|songs|tunes|tracks|stuff) by (?P<a>.+)$", core)):
        return MusicRequest(m.group("a").strip(), "artist")
    if (m := re.match(r"^(?P<song>.+?) by (?P<a>.+)$", core)):
        artist, core = m.group("a").strip(), m.group("song").strip()
    for pattern, kind in (
        (r"^(?:song|track|tune)\s+(?P<n>.+)$", "track"), (r"^(?P<n>.+?)\s+(?:song|track)$", "track"),
        (r"^(?P<n>.+?)\s+playlist$", "playlist"), (r"^playlist\s+(?P<n>.+)$", "playlist"),
        (r"^(?P<n>.+?)\s+album$", "album"), (r"^album\s+(?P<n>.+)$", "album"),
        (r"^(?:audio ?books?|an audio ?book|audio ?book)$", "audiobook"),
        (r"^(?:audio ?book|book)\s+(?P<n>.+)$", "audiobook"), (r"^(?P<n>.+?)\s+audio ?book$", "audiobook"),
        (r"^podcasts?$", "podcast"), (r"^podcast\s+(?P<n>.+)$", "podcast"), (r"^(?P<n>.+?)\s+podcast$", "podcast"),
        (r"^(?P<n>.+?)\s+(?:radio|station)$", "radio"), (r"^(?:radio|station)\s+(?P<n>.+)$", "radio"),
    ):
        if (m := re.match(pattern, core)):
            return MusicRequest((m.groupdict().get("n") or "").strip(), kind, artist)
    if media_hint == "audiobook":
        return MusicRequest(core, "audiobook", artist)
    genre = some or core in GENRES or " ".join(core.split()[:2]) in GENRES
    core = re.sub(r"\b(?:music|songs|tunes|tracks|stuff)$", "", core).strip() or core
    if core in ("to relax to", "to listen to", "to work to"):
        core = "relaxing"
    return MusicRequest(core, None, artist, genre=genre)


def is_generic(rest: str) -> bool:
    """'music', 'some music', 'something': no title, just play something."""
    return interpret(rest) is None


def strip_here(rest: str) -> tuple[str, bool]:
    """('what to play', True) when the request ends in 'down here', 'in here', 'in this room'.
    A bare trailing 'here' only counts after generic words, so 'wish you were here' stays a title."""
    if (m := _HERE_LOOSE.search(rest)):
        return rest[:m.start()].strip(), True
    if (m := _HERE.search(rest)):
        before = rest[:m.start()].strip()
        request = interpret(before)
        if request is None or request.genre:  # "play music here", "play some jazz here"
            return before, True
    return rest, False


def variants(query: str) -> list[str]:
    """The query, then the same with a misheard number at the end: 'type shit to' -> 'type shit 2'."""
    words = query.split()
    out = [query]
    if len(words) > 1 and words[-1] in _SAID_NUMBER:
        out.append(" ".join(words[:-1] + [_SAID_NUMBER[words[-1]]]))
    return out


SPEAKER_JOINERS = ("on", "in", "at", "through", "over", "from", "downstairs in", "upstairs in")


def splits(text: str) -> list[tuple[str, str]]:
    """Every way to cut a request into (what, where), longest 'what' first: titles contain 'in' and 'on'."""
    out = [(text, "")]
    words = text.split()
    for i in range(len(words) - 1, 0, -1):
        if words[i] in ("on", "in", "at", "through", "over", "from"):
            out.append((" ".join(words[:i]), " ".join(words[i + 1:])))
    return out


# ---- choosing among search results -----------------------------------------------------------------------

ORDER = {"track": 5, "album": 4, "artist": 3, "playlist": 2, "radio": 1, "audiobook": 0, "podcast": 0}
GENRE_ORDER = ("playlist", "radio", "artist", "album", "track")


def _score(query: tuple[str, ...], name: str) -> float:
    n = tuple(_ROMAN.get(t, t) for t in tokens(re.sub(r"\(.*?\)|\[.*?\]", " ", name)))
    query = tuple(_ROMAN.get(t, t) if i else t for i, t in enumerate(query))  # "Part II" is "part 2"
    if not query or not n:
        return 0.0
    return phrase_sim(query, n)


def choose(request: MusicRequest, found: dict[str, list[MusicHit]]) -> MusicHit | None:
    """The best hit, or None if nothing is a good enough match. Never returns a loose guess."""
    q = tokens(request.query)
    want_artist = tokens(request.artist) if request.artist else None

    def artist_ok(h: MusicHit) -> bool:
        return want_artist is None or any(phrase_sim(want_artist, tokens(a)) >= 0.85 for a in h.artists)

    best: tuple[float, int, MusicHit] | None = None
    types = [request.media_type] if request.media_type else list(GENRE_ORDER if request.genre else ORDER)
    for rank, kind in enumerate(types):
        for hit in found.get(kind + "s", found.get(kind, [])):
            if not artist_ok(hit):
                continue
            s = _score(q, hit.name) if q else 0.5
            if s < 0.6:
                continue
            # exact titles beat everything; otherwise follow the preferred order, library first
            key = s + (0.02 if hit.library else 0) - rank * (0.15 if request.genre or request.media_type else 0.01)
            if best is None or key > best[0]:
                best = (key, rank, hit)
    return best[2] if best else None


@dataclass
class Plan:
    """What the resolver found: a request and either a decided speaker or a question to ask."""
    request: MusicRequest
    players: list = field(default_factory=list)  # entities that could play it
