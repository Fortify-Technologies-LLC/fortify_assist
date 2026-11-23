"""Generate a labelled test corpus from any House.

Expected outcomes are derived from the house itself (names, aliases, areas, exposure,
availability, sensitivity), so the same generator works on the synthetic fixture and on
an export of a real installation. Output is deterministic for a given (house, seed).
"""
from __future__ import annotations

import random
import re
from collections import defaultdict

from .types import Action, Case, Entity, House, Kind, Outcome

ONOFF_DOMAINS = ("light", "switch", "fan", "media_player")
ONOFF_FORMS = [
    ("turn on the {k}", "turn_on"),
    ("turn the {k} off", "turn_off"),
    ("switch on the {k}", "turn_on"),
    ("shut off the {k}", "turn_off"),
]
BRIGHTNESS = [25, 50, 80]
TEMPS = [68, 72]
VOLUMES = [30, 60]
ASR_CONFUSIONS = {"lamp": "lamb", "fan": "van", "desk": "disk", "deck": "dec", "strip": "stripe"}
CHAT = [
    "tell me a joke",
    "what is the capital of Italy",
    "how far is the moon",
    "who wrote Pride and Prejudice",
    "give me a fun fact about octopuses",
    "what's two plus two",
    "how do I boil an egg",
    "thank you",
]
REFUSE = [
    "turn on the flux capacitor",
    "lock the side door",
    "open the pool cover",
    "turn off the jacuzzi",
]


class _Gen:
    def __init__(self, house: House, seed: int):
        self.house = house
        self.rng = random.Random(seed)
        self.cases: list[Case] = []
        self.counts: dict[str, int] = defaultdict(int)
        self.area_names = {name.lower() for name, _ in house.areas.values()}
        # domain -> key -> exposed entities answering to that key
        self.index: dict[str, dict[str, list[Entity]]] = defaultdict(lambda: defaultdict(list))
        for e in house.exposed():
            for key in self.keys(e):
                self.index[e.domain][key].append(e)

    @staticmethod
    def keys(e: Entity) -> list[str]:
        return [e.name.lower(), *[a.lower() for a in e.aliases]]

    def add(self, category, utterance, expect, context_area=None, notes=""):
        self.counts[category] += 1
        self.cases.append(
            Case(f"{category}-{self.counts[category]:04d}", category, utterance, expect,
                 context_area=context_area, notes=notes)
        )

    def act(self, service, entity_id, data=None):
        return Action.make(service, entity_id, data)

    def usable_keys(self, e: Entity):
        """Keys that are safe to emit positive cases for: unique within the domain and
        not equal to an area name (a 'Living Room' group is ambiguous with the area)."""
        for i, key in enumerate(self.keys(e)):
            if key in self.area_names or len(self.index[e.domain][key]) != 1:
                continue
            yield i, key

    def area_entities(self, domain, area_id):
        return [e for e in self.house.exposed()
                if e.domain == domain and e.area == area_id and e.available]

    # ---- generators -------------------------------------------------------------

    def onoff(self):
        for e in self.house.entities.values():
            if e.domain not in ONOFF_DOMAINS:
                continue
            if not e.exposed:
                self.add("refuse", f"turn on the {e.name.lower()}", Outcome(Kind.REFUSE),
                         notes="entity exists but is not exposed")
                continue
            for i, key in self.usable_keys(e):
                if not e.available:
                    self.add("refuse", f"turn on the {key}", Outcome(Kind.REFUSE),
                             notes="entity unavailable")
                    continue
                for tmpl, svc in ONOFF_FORMS[: 4 if i == 0 else 2]:
                    self.add("exact" if i == 0 else "alias", tmpl.format(k=key),
                             Outcome(Kind.ACTION, [self.act(f"{e.domain}.{svc}", e.id)]))

    def ambiguity(self):
        done = set()
        for domain in ("light", "switch", "fan", "media_player"):
            for key, members in self.index[domain].items():
                if len(members) < 2 or key in self.area_names or (domain, key) in done:
                    continue
                done.add((domain, key))
                self.add("ambiguous", f"turn on the {key}",
                         Outcome(Kind.CLARIFY, entities=[m.id for m in members]),
                         notes="same name in several areas, no room context")
                for m in members:
                    if not m.area or not m.available:
                        continue
                    a = self.house.area_name(m.area).lower()
                    peers = [x for x in members if x.area == m.area]
                    if len(peers) == 1:
                        self.add("context", f"turn on the {key}",
                                 Outcome(Kind.ACTION, [self.act(f"{domain}.turn_on", m.id)]),
                                 context_area=m.area, notes="resolved by satellite area")
                        self.add("qualified", f"turn on the {key} in the {a}",
                                 Outcome(Kind.ACTION, [self.act(f"{domain}.turn_on", m.id)]))

    def brightness(self):
        for n, e in enumerate(x for x in self.house.exposed() if x.domain == "light" and x.available):
            for _, key in self.usable_keys(e):
                p = BRIGHTNESS[n % len(BRIGHTNESS)]
                if e.attributes.get("supported_color_modes") == ["onoff"]:  # cannot dim: must say so, not try
                    self.add("value", f"set the {key} to {p} percent", Outcome(Kind.REFUSE),
                             notes="light only switches on and off")
                else:
                    self.add("value", f"set the {key} to {p} percent",
                             Outcome(Kind.ACTION, [self.act("light.turn_on", e.id, {"brightness_pct": p})]))
                break

    def area_scoped(self):
        for area_id, (name, _) in self.house.areas.items():
            lights = self.area_entities("light", area_id)
            if not lights:
                continue
            a = name.lower()
            for tmpl, svc in (("turn on all the lights in the {a}", "turn_on"),
                              ("turn off the lights in the {a}", "turn_off")):
                self.add("area", tmpl.format(a=a),
                         Outcome(Kind.ACTION, [self.act(f"light.{svc}", e.id) for e in lights]))
            self.add("context", "turn off the lights",
                     Outcome(Kind.ACTION, [self.act("light.turn_off", e.id) for e in lights]),
                     context_area=area_id, notes="bare 'the lights' means this room")
        self.add("ambiguous", "turn off the lights", Outcome(Kind.CLARIFY),
                 notes="no room context")

    def covers_locks(self):
        for e in self.house.exposed():
            if not e.available:
                continue
            for _, key in self.usable_keys(e):
                if e.domain == "cover":
                    self.add("exact", f"close the {key}",
                             Outcome(Kind.ACTION, [self.act("cover.close_cover", e.id)]))
                    opened = (Outcome(Kind.CONFIRM) if e.sensitive else
                              Outcome(Kind.ACTION, [self.act("cover.open_cover", e.id)]))
                    self.add("sensitive" if e.sensitive else "exact", f"open the {key}", opened)
                elif e.domain == "lock":
                    self.add("exact", f"lock the {key}",
                             Outcome(Kind.ACTION, [self.act("lock.lock", e.id)]))
                    self.add("sensitive", f"unlock the {key}", Outcome(Kind.CONFIRM))
                elif e.domain == "alarm_control_panel":
                    self.add("sensitive", f"disarm the {key}", Outcome(Kind.CONFIRM))

    def climate_media_scenes(self):
        for e in self.house.exposed():
            if not e.available:
                continue
            for n, (_, key) in enumerate(self.usable_keys(e)):
                if e.domain == "climate":
                    for t in TEMPS:
                        self.add("value", f"set the {key} to {t} degrees",
                                 Outcome(Kind.ACTION, [self.act("climate.set_temperature", e.id,
                                                                {"temperature": t})]))
                elif e.domain == "media_player":
                    self.add("exact", f"pause the {key}",
                             Outcome(Kind.ACTION, [self.act("media_player.media_pause", e.id)]))
                    v = VOLUMES[n % len(VOLUMES)]
                    self.add("value", f"set the volume on the {key} to {v} percent",
                             Outcome(Kind.ACTION, [self.act("media_player.volume_set", e.id,
                                                            {"volume_level": v / 100})]))
                elif e.domain == "scene":
                    self.add("exact", f"activate the {key} scene",
                             Outcome(Kind.ACTION, [self.act("scene.turn_on", e.id)]))
                elif e.domain == "script":
                    self.add("exact", f"run the {key} script",
                             Outcome(Kind.ACTION, [self.act("script.turn_on", e.id)]))

    def queries(self):
        for e in self.house.exposed():
            if not e.available:
                continue
            for _, key in self.usable_keys(e):
                q = {"light": "on", "switch": "on", "fan": "on", "lock": "locked",
                     "cover": "open"}.get(e.domain)
                if q:
                    self.add("query", f"is the {key} {q}", Outcome(Kind.ANSWER, entities=[e.id]))
                elif e.domain == "sensor":
                    self.add("query", f"what is the {key}", Outcome(Kind.ANSWER, entities=[e.id]))
        seen = defaultdict(list)
        for e in self.house.exposed():
            if e.domain == "sensor" and e.device_class and e.area and e.available:
                seen[(e.device_class, e.area)].append(e)
        for (dc, area), es in seen.items():
            if len(es) == 1:
                self.add("query", f"what's the {dc} in the {self.house.area_name(area).lower()}",
                         Outcome(Kind.ANSWER, entities=[es[0].id]))

    def negatives(self):
        for u in REFUSE:
            self.add("refuse", u, Outcome(Kind.REFUSE), notes="no such device")
        for u in CHAT:
            self.add("chat", u, Outcome(Kind.CHAT), notes="must not touch devices")

    def multi(self, n=25):
        pool = [c for c in self.cases
                if c.category in ("exact", "alias") and c.expect.kind is Kind.ACTION
                and len(c.expect.actions) == 1]
        for _ in range(n):
            a, b = self.rng.sample(pool, 2)
            ea, eb = a.expect.actions[0], b.expect.actions[0]
            if ea.entity_id == eb.entity_id:
                continue
            self.add("multi", f"{a.utterance} and {b.utterance}",
                     Outcome(Kind.ACTION, [ea, eb]))

    def noisy(self, n=70):
        pool = [c for c in self.cases
                if c.category in ("exact", "alias", "value", "area")
                and c.expect.kind is Kind.ACTION and not c.context_area]
        perturb = [
            lambda u: self.rng.choice(["please ", "hey could you ", "can you ", "i want you to "]) + u,
            lambda u: u + self.rng.choice([" please", " for me", " now"]),
            lambda u: "uh " + u,
            lambda u: u.replace(" the ", " ", 1),
            lambda u: u.capitalize() + ".",
        ]
        for c in self.rng.sample(pool, min(n, len(pool))):
            u = self.rng.choice(perturb)(c.utterance)
            if u != c.utterance:
                self.add("noisy", u, c.expect)
        for c in pool:
            u = c.utterance
            for good, bad in ASR_CONFUSIONS.items():
                u = re.sub(rf"\b{good}\b", bad, u)
            if u != c.utterance:
                self.add("asr", u, c.expect, notes=f"mis-transcription of: {c.utterance}")


def generate(house: House, seed: int = 7) -> list[Case]:
    g = _Gen(house, seed)
    g.onoff()
    g.ambiguity()
    g.brightness()
    g.area_scoped()
    g.covers_locks()
    g.climate_media_scenes()
    g.queries()
    g.negatives()
    g.multi()
    g.noisy()
    return g.cases
