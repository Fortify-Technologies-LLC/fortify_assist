"""Text normalisation and similarity. Pure Python, no dependencies."""
from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_FILLER_PREFIX = sorted(
    ["hey", "ok", "okay", "uh", "um", "please", "can you", "could you", "would you",
     "will you", "i want you to", "i need you to", "go ahead and", "just", "maybe", "kindly", "quickly", "yo",
     "hey there", "alright", "all right", "so", "lets", "let's", "let us", "would you mind", "do you mind",
     "i'd like you to", "i would like you to", "would you please", "could you please", "can you please",
     "actually", "oh", "wait", "sorry", "well", "hmm", "you know what", "on second thought", "never mind",
     "nevermind", "no wait", "no actually", "oops", "ok so", "okay so"],
    key=len, reverse=True)
_FILLER_SUFFIX = sorted(["please", "for me", "now", "thanks", "thank you", "would you", "could you", "can you",
                         "will you", "right now", "if you would", "if you can", "real quick"], key=len, reverse=True)
_DROP = {"the", "my", "both"}
_HEARD = {"lites": "lights", "lite": "light", "lighs": "lights", "lihgts": "lights", "ligths": "lights",
          "ligts": "lights", "lightes": "lights", "teh": "the", "trun": "turn", "tern": "turn", "tunr": "turn",
          "turnn": "turn", "swtich": "switch", "telly": "television", "tellies": "tvs",
          "televisions": "tvs", "tv's": "tvs"}  # slips and synonyms, said the one way the rest of the code knows
STOP = {"a", "an", "of", "to", "in", "on", "at", "me", "my", "is", "are", "it", "the", "and", "or", "do", "you",
        "i", "this", "that", "so", "for", "with", "be", "can", "please"}  # words that prove nothing about a request
_VOWELS = "aeiouy"
_SKELETON_MAP = str.maketrans({"v": "f", "b": "p", "d": "t", "g": "k", "z": "s", "q": "k", "c": "k"})


def _numbers(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _TENS:
            n = _TENS[t]
            if i + 1 < len(tokens) and tokens[i + 1] in _UNITS and 0 < _UNITS[tokens[i + 1]] < 10:
                n += _UNITS[tokens[i + 1]]
                i += 1
            out.append(str(n))
        elif t in _UNITS:
            out.append(str(_UNITS[t]))
        elif t == "hundred" and out and out[-1] == "1":
            out[-1] = "100"
        else:
            out.append(t)
        i += 1
    return out


def normalize(text: str) -> str:
    """Lower-case, strip punctuation, politeness and articles, convert number words."""
    t = text.lower().replace("’", "'").replace("%", " percent ")
    t = re.sub(r"\b(what|who|where|how|that|there|it|here)'s\b", r"\1 is", t)
    t = re.sub(r"\b(what|where|how)s\b", r"\1 is", t)  # "whats the temperature" typed without an apostrophe
    t = re.sub(r"\bits (?=(?:way |a bit |a little |kind of |really |so |pretty |much |getting )?(?:too|dark|bright|"
               r"cold|hot|warm|chilly|freezing|boiling|loud|quiet|stuffy)\b)", "it is ", t)  # "its too dark"
    t = re.sub(r"\blet's\b", "lets", t)  # "let's have movie night", before possessives eat the s
    t = re.sub(r"(?<=\w)'s\b", "", t)  # possessive: alex's -> alex
    t = re.sub(r"\b(?:uh|um|umm|uhh|erm|er|ah)\b", " ", t)  # hesitations anywhere, not only at the start
    t = re.sub(r"\blike (?=(?:turn|switch|set|make|dim|open|close|play|start|stop|put|shut|lock)\b)", "", t)
    t = re.sub(r"\s+(?:or whatever|or something|and stuff)\s*[.!?]*$", "", t)  # "turn it off or whatever"
    t = re.sub(r"[^a-z0-9'. ]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # keep decimal points only
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
    tokens = [_HEARD.get(w, w) for w in t.split() if w not in _DROP]
    return re.sub(r"\b(\d) d\b", r"\1d", " ".join(_numbers(tokens)))  # "three d printer"

# Words for a light in the ceiling. People mix them freely: the "can lights" are also the "overhead lights".
CEILING = {"overhead", "ceiling", "can", "recessed", "pot", "downlight", "downlights"}


def ceiling_same(toks: tuple[str, ...]) -> tuple[str, ...]:
    """One spelling for every ceiling word, so names can be compared regardless of which one was used."""
    return tuple("overhead" if t in CEILING else t for t in toks)


def tokens(text: str) -> tuple[str, ...]:
    return tuple(normalize(text).split())


def skeleton(word: str) -> str:
    """Consonant skeleton that survives common ASR confusions (lamp/lamb, fan/van, desk/disk)."""
    w = re.sub(r"[^a-z0-9]", "", word.lower())
    if w.isdigit():
        return w
    w = w.replace("ph", "f").replace("ck", "k").translate(_SKELETON_MAP)
    w = "".join(c for c in w if c not in _VOWELS)
    return re.sub(r"(.)\1+", r"\1", w)


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# words that only restate what kind of device something is; "bar sign light" means "Bar Sign"
GENERIC = {"light", "lights", "lamp", "lamps", "bulb", "bulbs", "fan", "fans", "switch", "switches", "plug",
           "plugs", "outlet", "outlets", "speaker", "speakers"}


_SAME_START = {"f": "fv", "v": "fv", "b": "bp", "p": "bp", "d": "dt", "t": "dt", "g": "gk", "k": "kcgq", "c": "kcs",
               "s": "scz", "z": "sz", "q": "kq"}  # first sounds speech-to-text mixes up


def token_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a.isdigit() or b.isdigit():
        return 0.0
    if min(len(a), len(b)) >= 3 and (a + "s" == b or b + "s" == a):  # lamp/lamps, light/lights
        return 0.95
    if len(a) >= 3 and len(b) >= 3 and skeleton(a) == skeleton(b) \
            and levenshtein(a, b) <= max(1, min(len(a), len(b)) // 3) and a[0] in _SAME_START.get(b[0], b[0]):
        return 0.92  # lamp/lamb, fan/van, desk/disk; never attic/deck, dryer/door or garden/curtain
    if min(len(a), len(b)) >= 5 and levenshtein(a, b) == 1:
        return 0.88
    return 0.0


def phrase_sim(target: tuple[str, ...], phrase: tuple[str, ...]) -> float:
    """1.0 exact; ~0.9 same words with ASR-style slips; 0.6-0.8 target is part of the phrase."""
    if not target or not phrase:
        return 0.0
    if target == phrase:
        return 1.0
    if len(target) == len(phrase):
        sims = [token_sim(a, b) for a, b in zip(target, phrase)]
        if all(sims):
            return sum(sims) / len(sims)
    core = tuple(t for t in target if t not in GENERIC)
    if len(target) > len(phrase) and core and core != target:
        inner = phrase_sim(core, phrase)  # extra "light" / "lamp" in what was said
        if inner:
            return inner * 0.93
    if len(target) < len(phrase):
        used: set[int] = set()
        sims = []
        for a in target:
            best, best_j = 0.0, -1
            for j, b in enumerate(phrase):
                if j not in used and (s := token_sim(a, b)) > best:
                    best, best_j = s, j
            if not best:
                return 0.0
            used.add(best_j)
            sims.append(best)
        if all(phrase[j] in GENERIC for j in range(len(phrase)) if j not in used):
            return 0.93 * (sum(sims) / len(sims))  # only the type word is missing: "workshop overhead"
        return (0.6 + 0.2 * len(target) / len(phrase)) * (sum(sims) / len(sims))
    return 0.0
