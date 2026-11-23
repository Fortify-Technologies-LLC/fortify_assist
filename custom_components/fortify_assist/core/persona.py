"""Who the assistant is. Everything here is configuration, nothing is hard-coded to one house."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Persona:
    agent_name: str = "Assistant"  # how it is written, e.g. "N.O.V.A."
    spoken_name: str = ""  # how it is said, e.g. "Nova"; defaults to agent_name
    acronym_expansion: str = ""  # "Neighborhood Operations and Voice Assistant"; empty if not an acronym
    owner: str = ""  # the person or household it serves
    maker: str = ""  # who built or configured it
    purpose: str = ""  # one sentence about what it is for
    personality: str = "neutral"  # id of a response pack (see personalities/)
    traits: str = ""  # free text steering the chat model's tone (used in the chat path)
    overrides: dict = field(default_factory=dict)  # response key -> list of variants, wins over the pack

    @property
    def spoken(self) -> str:
        return self.spoken_name or self.agent_name

    def names(self) -> list[str]:
        """Every way someone might address it, longest first (for stripping 'hey Nova')."""
        found = {n.strip().lower() for n in (self.agent_name, self.spoken_name) if n.strip()}
        return sorted(found, key=len, reverse=True)
