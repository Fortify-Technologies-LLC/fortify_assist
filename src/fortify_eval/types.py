"""Harness-only types. Shared domain types live in the core package."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fortify_core.types import Action, Entity, House, Kind, Outcome

__all__ = ["Action", "Agent", "Case", "Entity", "House", "Kind", "Outcome"]


class Agent(Protocol):
    """What every pipeline under test implements. The house is read-only state."""

    name: str

    def handle(self, utterance: str, house: House, context_area: str | None = None) -> Outcome:
        """context_area is the area id of the satellite that heard the request, if known."""
        ...


@dataclass
class Case:
    id: str
    category: str
    utterance: str
    expect: Outcome
    context_area: str | None = None
    notes: str = ""
