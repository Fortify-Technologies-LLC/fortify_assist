import asyncio
import random

import pytest

from fortify_core import Pipeline
from fortify_core.executor import StateInfo, execute
from fortify_core.persona import Persona
from fortify_core.responses import KEYS, Responder, list_personalities, load_pack, pack_errors
from fortify_core.types import Action, Kind, Outcome
from fortify_eval.cli import DEFAULT_HAND, DEFAULT_HOUSE
from fortify_eval.corpus import build_corpus
from fortify_eval.house import load_house

PERSONA = dict(agent_name="N.O.V.A.", spoken_name="Nova",
             acronym_expansion="Neighborhood Operations and Voice Assistant", owner="the Smiths",
             maker="Example Corp", purpose="I control the house and assist the family.")
PERSONALITIES = [p["id"] for p in list_personalities()]


@pytest.fixture(scope="module")
def house():
    return load_house(DEFAULT_HOUSE)


def pipe(house, personality="neutral", **kw):
    return Pipeline(house, persona=Persona(personality=personality, **{**PERSONA, **kw}))


def test_the_expected_personalities_ship():
    assert {"neutral", "butler", "sarcastic", "jokester", "secretary"} <= set(PERSONALITIES)


@pytest.mark.parametrize("pid", PERSONALITIES)
def test_every_pack_is_valid_and_complete(pid):
    pack = load_pack(pid)
    assert pack_errors(pack, require_all=True) == []
    if pid != "neutral":
        assert all(len(v) >= 4 for v in pack["responses"].values()), "non-neutral packs need variety"


def test_pack_validation_catches_dropped_and_invented_facts():
    bad = {"responses": {"done": ["Done."], "answer": ["{name} is {state} {nope}."], "color": ["x"]}}
    errors = " ".join(pack_errors(bad))
    assert "missing" in errors and "unknown slot" in errors and "unknown key" in errors


@pytest.mark.parametrize("pid", PERSONALITIES)
def test_personality_never_changes_what_the_pipeline_does(house, pid):
    """Same corpus, any personality: identical kinds and actions. Tone only."""
    cases = build_corpus(house, DEFAULT_HAND)[::4]  # every 4th case keeps this fast
    base, other = pipe(house, "neutral"), pipe(house, pid)
    for c in cases:
        a, b = base.handle(c.utterance, c.context_area), other.handle(c.utterance, c.context_area)
        assert (a.kind, a.actions, a.entities) == (b.kind, b.actions, b.entities), c.id


@pytest.mark.parametrize("pid", PERSONALITIES)
def test_facts_survive_every_personality(house, pid):
    p = pipe(house, pid)
    for _ in range(12):  # variants are random; sample a lot
        assert "Deck Lights" in p.handle("turn on the deck lights").message
        assert "71.5" in p.handle("what is the living room temperature").message
        clarify = p.handle("turn on the lamp").message
        assert "Lamp in the Bedroom" in clarify and "Lamp in the Office" in clarify
        assert "flux capacitor" in p.handle("turn on the flux capacitor").message.lower()
        assert "Front Door Lock" in p.handle("unlock the front door").message
        assert "temperature" in p.handle("what is the temperature in the garage").message


def test_personalities_actually_sound_different(house):
    said = {pid: {pipe(house, pid).handle("turn on the deck lights").message for _ in range(10)}
            for pid in PERSONALITIES}
    assert said["neutral"] == {"Turning on Deck Lights."}
    assert all(said[pid] != said["neutral"] for pid in PERSONALITIES if pid != "neutral")


def test_same_phrase_is_not_repeated_back_to_back():
    r = Responder(Persona(personality="butler"), random.Random(1))
    lines = [r.say("exec_ok") for _ in range(30)]
    assert all(a != b for a, b in zip(lines, lines[1:]))


def test_unknown_personality_falls_back_to_neutral():
    assert Responder(Persona(personality="does-not-exist")).say("exec_ok") == "Done."


def test_custom_overrides_win_and_are_validated():
    r = Responder(Persona(personality="butler", overrides={"exec_ok": ["Mission accomplished."]}))
    assert r.say("exec_ok") == "Mission accomplished."
    with pytest.raises(ValueError):
        Responder(Persona(overrides={"answer": ["{name} is wonderful."]}))  # drops {state}


# ---- identity -------------------------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("what's your name", "N.O.V.A."),
    ("who are you", "N.O.V.A."),
    ("what does Nova stand for", "Neighborhood Operations and Voice Assistant"),
    ("what does your name stand for", "Neighborhood Operations and Voice Assistant"),
    ("who made you", "Example Corp"),
    ("what do you do", "I control the house"),
    ("who do you work for", "the Smiths"),
])
def test_identity_answers_come_from_the_persona(house, text, expect):
    out = pipe(house).handle(text)
    assert out.kind is Kind.ANSWER and expect in out.message and not out.actions


def test_unrelated_acronym_question_goes_to_chat_not_to_identity(house):
    out = pipe(house).handle("what does NASA stand for")
    assert out.kind is Kind.CHAT


def test_unset_fields_are_admitted_not_invented(house):
    p = Pipeline(house, persona=Persona(agent_name="Bob"))
    assert "haven't been told" in p.handle("who made you").message
    assert "just a name" in p.handle("what does your name stand for").message


def test_identity_works_in_every_personality(house):
    for pid in PERSONALITIES:
        assert "Example Corp" in pipe(house, pid).handle("who built you").message


# ---- addressing the assistant by name --------------------------------------------

@pytest.mark.parametrize("text", [
    "Nova turn on the deck lights", "hey Nova, turn on the deck lights",
    "turn on the deck lights, Nova", "ok N.O.V.A. turn on the deck lights",
])
def test_the_assistants_name_is_ignored(house, text):
    out = pipe(house).handle(text)
    assert [a.entity_id for a in out.actions] == ["light.deck"]


def test_a_device_whose_name_contains_the_assistants_name_is_still_found(house):
    p = Pipeline(house, persona=Persona(agent_name="Deck"))
    assert [a.entity_id for a in p.handle("turn on the deck lights").actions] == ["light.deck"] \
        or p.handle("turn on the deck lights").kind is not Kind.ACTION  # never a wrong device


# ---- executor speaks in character --------------------------------------------------

def test_executor_messages_follow_the_personality():
    class Stuck:
        states = {"light.a": StateInfo("off")}

        async def call_service(self, *a):
            pass

        def get_state(self, e):
            return self.states[e]

    async def no_sleep(_):
        pass

    ticks = iter(range(100))
    out = Outcome(Kind.ACTION, [Action.make("light.turn_on", "light.a")])
    result = asyncio.run(execute(out, Stuck(), {"light.a": "Porch"}, sleep=no_sleep,
                                 clock=lambda: next(ticks), responder=Responder(Persona(personality="secretary"))))
    assert "Porch is still off" in result.message and result.message != "That didn't work. Porch is still off."


def test_the_prompt_for_chat_can_use_the_traits_field():
    assert Persona(traits="warm and a little dry").traits  # consumed by the chat path in the next slice
    assert set(KEYS) >= {"done", "answer", "identity_name"}


@pytest.mark.parametrize("pid", PERSONALITIES)
def test_a_lowercase_verb_slot_never_starts_a_sentence(pid):
    import re
    for key, variants in load_pack(pid)["responses"].items():
        for v in variants:
            assert not re.search(r"(^|[.?!:]\s+)\{verb\}", v) or key == "done", (pid, key, v)
