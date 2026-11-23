import asyncio

import pytest

from fortify_core import Pipeline
from fortify_core.executor import StateInfo, execute
from fortify_core.text import normalize, skeleton
from fortify_core.types import Action, Kind, Outcome
from fortify_core.validator import validate
from fortify_eval.cli import DEFAULT_HOUSE
from fortify_eval.house import load_house


@pytest.fixture()
def house():
    return load_house(DEFAULT_HOUSE)


@pytest.fixture()
def pipe(house):
    return Pipeline(house)


def ids(outcome):
    return sorted(a.entity_id for a in outcome.actions)


# ---- text ------------------------------------------------------------------------

def test_normalize_strips_politeness_articles_and_numbers():
    assert normalize("Hey, could you please set the lamp to fifty five percent?") == \
        "set lamp to 55 percent"
    assert normalize("Turn on the lights at 50%") == "turn on lights at 50 percent"


def test_skeleton_collapses_asr_confusions():
    assert skeleton("lamp") == skeleton("lamb")
    assert skeleton("fan") == skeleton("van")
    assert skeleton("desk") == skeleton("disk")


# ---- pipeline safety -------------------------------------------------------------

def test_never_acts_on_a_shared_name_without_context(pipe):
    out = pipe.handle("turn on the lamp")
    assert out.kind is Kind.CLARIFY and not out.actions
    assert out.message.endswith("?")  # HA continues the conversation on a trailing question mark


def test_room_context_resolves_shared_name(pipe):
    assert ids(pipe.handle("turn on the lamp", context_area="office")) == ["light.office_lamp"]


def test_partial_failure_in_multi_command_runs_nothing(pipe):
    out = pipe.handle("turn on the deck lights and turn on the flux capacitor")
    assert out.kind is Kind.REFUSE and not out.actions


def test_chat_clause_does_not_block_command(pipe):
    out = pipe.handle("what's the capital of France and turn on the deck lights")
    assert ids(out) == ["light.deck"]


def test_sensitive_actions_need_confirmation(pipe):
    assert pipe.handle("unlock the front door").kind is Kind.CONFIRM
    assert pipe.handle("open the garage door").kind is Kind.CONFIRM
    assert pipe.handle("close the garage door").kind is Kind.ACTION


def test_confirmation_can_be_disabled(house):
    from fortify_core import Config

    out = Pipeline(house, Config(confirm_sensitive=False)).handle("unlock the front door")
    assert out.kind is Kind.ACTION


# ---- follow-ups ------------------------------------------------------------------

def test_confirm_then_yes_executes(pipe):
    assert pipe.handle("unlock the front door", conversation_id="c").kind is Kind.CONFIRM
    out = pipe.handle("yes", conversation_id="c")
    assert [a.service for a in out.actions] == ["lock.unlock"]


def test_confirm_then_no_cancels(pipe):
    pipe.handle("unlock the front door", conversation_id="c")
    out = pipe.handle("no", conversation_id="c")
    assert out.kind is Kind.CHAT and not out.actions


def test_unrelated_reply_drops_pending_confirmation(pipe):
    pipe.handle("unlock the front door", conversation_id="c")
    assert pipe.handle("tell me a joke", conversation_id="c").kind is Kind.CHAT
    assert pipe.handle("yes", conversation_id="c").kind is Kind.CHAT  # nothing pending any more


def test_confirmation_expires(house):
    now = [0.0]
    p = Pipeline(house, clock=lambda: now[0])
    p.handle("unlock the front door", conversation_id="c")
    now[0] = 120.0
    assert p.handle("yes", conversation_id="c").kind is not Kind.ACTION


@pytest.mark.parametrize("reply,expected", [
    ("the office one", "light.office_lamp"),
    ("bedroom", "light.bedroom_lamp"),
    ("the second one", "light.office_lamp"),
])
def test_clarification_answers(pipe, reply, expected):
    out = pipe.handle("turn on the lamp", conversation_id="c")
    assert out.kind is Kind.CLARIFY
    assert ids(pipe.handle(reply, conversation_id="c")) == [expected]


# ---- validator -------------------------------------------------------------------

def test_validator_rejects_bad_actions(house):
    assert validate(Action.make("light.turn_on", "light.deck"), house) is None
    assert "does not exist" in validate(Action.make("light.turn_on", "light.nope"), house)
    assert "unavailable" in validate(Action.make("light.turn_on", "light.broken_porch"), house)
    assert "not available" in validate(Action.make("light.turn_on", "light.hidden_closet"), house)
    assert validate(Action.make("lock.unlock", "light.deck"), house)  # wrong domain
    assert "out of range" in validate(Action.make("light.turn_on", "light.deck", {"brightness_pct": 150}), house)
    assert validate(Action.make("light.explode", "light.deck"), house)


# ---- executor --------------------------------------------------------------------

class FakeBackend:
    def __init__(self, states, broken=()):
        self.states = {k: StateInfo(*v) if isinstance(v, tuple) else StateInfo(v) for k, v in states.items()}
        self.broken = set(broken)
        self.calls = []

    async def call_service(self, service, entity_id, data):
        self.calls.append((service, entity_id, data))
        if entity_id in self.broken:
            return
        s = self.states[entity_id]
        if service.endswith("turn_on"):
            s.state = "on"
            if "brightness_pct" in data:
                s.attributes["brightness"] = round(data["brightness_pct"] * 2.55)
        elif service.endswith("turn_off"):
            s.state = "off"
        elif service == "lock.unlock":
            s.state = "unlocked"

    def get_state(self, entity_id):
        return self.states.get(entity_id)


def run(coro):
    return asyncio.run(coro)


async def _no_sleep(_):
    pass


def _time():
    t = [0.0]

    def tick():
        t[0] += 1.0
        return t[0]

    return tick


def test_executor_verifies_state():
    backend = FakeBackend({"light.a": "off", "light.b": ("off", {})})
    out = Outcome(Kind.ACTION, [Action.make("light.turn_on", "light.a"),
                                Action.make("light.turn_on", "light.b", {"brightness_pct": 40})])
    result = run(execute(out, backend, sleep=_no_sleep, clock=_time()))
    assert result.ok and result.message == "Done."
    assert [c[1] for c in backend.calls] == ["light.a", "light.b"]


def test_executor_reports_a_device_that_ignored_the_command():
    backend = FakeBackend({"light.a": "off", "light.b": "off"}, broken={"light.b"})
    out = Outcome(Kind.ACTION, [Action.make("light.turn_on", "light.a"), Action.make("light.turn_on", "light.b")])
    result = run(execute(out, backend, {"light.b": "Porch Light"}, sleep=_no_sleep, clock=_time()))
    assert not result.ok
    assert result.message == "1 of 2 worked. Porch Light is still off."


def test_executor_will_not_run_non_actions():
    with pytest.raises(ValueError):
        run(execute(Outcome(Kind.CLARIFY), FakeBackend({})))


def test_room_plus_plural_class_means_the_whole_room_but_singular_asks(pipe):
    assert ids(pipe.handle("turn on the kitchen lights")) == ["light.kitchen_ceiling", "light.kitchen_counters"]
    out = pipe.handle("turn on the workshop light")
    assert out.kind is Kind.CLARIFY and not out.actions


def test_entity_named_like_a_room_class_beats_the_generic_reading(pipe):
    assert ids(pipe.handle("turn on the bedroom lights")) == ["light.bedroom_lights"]


@pytest.mark.parametrize("text,service", [
    ("turn down the bar speaker", "media_player.volume_down"),
    ("turn the bar speaker up", "media_player.volume_up"),
    ("turn up the kitchen speaker", "media_player.volume_up"),
    ("shut down the living room tv", "media_player.turn_off"),
    ("turn the living room tv off", "media_player.turn_off"),
])
def test_down_never_means_off_after_turn(pipe, text, service):
    out = pipe.handle(text)
    assert out.kind is Kind.ACTION and out.actions[0].service == service


def test_turn_the_volume_down_without_a_device_never_turns_anything_off(pipe):
    out = pipe.handle("turn the volume down")  # the one speaker that is playing gets quieter
    assert [a.service for a in out.actions] == ["media_player.volume_down"]
    assert [a.entity_id for a in out.actions] == ["media_player.bar_speaker"]
