"""Hand-written corpus files (YAML) plus the generated corpus."""
from __future__ import annotations

from pathlib import Path

import yaml

from .generate import generate
from .types import Action, Case, House, Kind, Outcome


def _outcome(raw: dict) -> Outcome:
    return Outcome(
        Kind(raw["kind"]),
        actions=[Action.make(a["service"], a["entity_id"], a.get("data")) for a in raw.get("actions", [])],
        entities=list(raw.get("entities", [])),
    )


def load_cases(path: str | Path) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [
        Case(c["id"], c.get("category", "hard"), c["utterance"], _outcome(c["expect"]),
             context_area=c.get("context_area"), notes=c.get("notes", ""))
        for c in raw
    ]


def build_corpus(house: House, hand_written: list[str | Path] = (), seed: int = 7) -> list[Case]:
    cases = generate(house, seed)
    for p in hand_written:
        cases.extend(load_cases(p))
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in corpus")
    return cases
