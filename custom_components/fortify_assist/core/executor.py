"""Run validated actions and check the device actually did what was asked.

The backend is a small protocol so Home Assistant, a test double or anything else can
supply it. Verification polls state until it matches or the timeout passes, then reports
honestly instead of claiming success.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .responses import Responder
from .types import Action, Kind, Outcome


@dataclass
class StateInfo:
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    async def call_service(self, service: str, entity_id: str, data: dict[str, Any]) -> None: ...

    def get_state(self, entity_id: str) -> StateInfo | None: ...


@dataclass
class ActionResult:
    action: Action
    verified: bool | None  # None: no way to verify this kind of action
    detail: str = ""


@dataclass
class ExecResult:
    results: list[ActionResult]
    message: str

    @property
    def ok(self) -> bool:
        return all(r.verified is not False for r in self.results)


def _close(a, b, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def expectation(action: Action) -> Callable[[StateInfo], bool] | None:
    """Predicate that is true once the device reflects the action, or None if unverifiable."""
    service, data = action.service, action.data_dict()
    if service.endswith(".turn_on") and not service.startswith(("scene", "script")):
        if "brightness_pct" in data:
            want = round(data["brightness_pct"] * 255 / 100)
            return lambda s: s.state == "on" and _close(s.attributes.get("brightness"), want, 3)
        return lambda s: s.state not in ("off", "unavailable", "unknown", "standby")
    if service.endswith(".turn_off") and not service.startswith("script"):
        return lambda s: s.state in ("off", "standby")
    simple = {
        "lock.lock": {"locked", "locking"}, "lock.unlock": {"unlocked", "unlocking"},
        "cover.open_cover": {"open", "opening"}, "cover.close_cover": {"closed", "closing"},
        "valve.open_valve": {"open", "opening"}, "valve.close_valve": {"closed", "closing"},
        "media_player.media_pause": {"paused", "idle"},  # some players go idle when paused
        "media_player.media_stop": {"idle", "off", "stopped"},
        "media_player.media_play": {"playing", "buffering"},
        "alarm_control_panel.alarm_disarm": {"disarmed"},
    }
    if service in simple:
        return lambda s: s.state in simple[service]
    if service == "music_assistant.play_media":
        return lambda s: s.state in ("playing", "buffering")
    if service == "fan.set_percentage":
        return lambda s: _close(s.attributes.get("percentage"), data["percentage"], 8)
    if service == "cover.set_cover_position":
        return lambda s: (_close(s.attributes.get("current_position"), data["position"], 6)
                          or s.state in ("opening", "closing"))
    if service == "climate.set_hvac_mode":
        return lambda s: s.state == data["hvac_mode"]
    if service == "vacuum.start":
        return lambda s: s.state in ("cleaning", "returning")
    if service == "vacuum.return_to_base":
        return lambda s: s.state in ("returning", "docked")
    if service == "vacuum.pause":
        return lambda s: s.state == "paused"
    if service == "media_player.volume_set":
        return lambda s: _close(s.attributes.get("volume_level"), data["volume_level"], 0.02)
    if service == "climate.set_temperature":
        return lambda s: _close(s.attributes.get("temperature"), data["temperature"], 0.5)
    return None


async def execute(outcome: Outcome, backend: Backend, names: dict[str, str] | None = None, *,
                  timeout: float = 3.0, interval: float = 0.25,
                  sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                  clock: Callable[[], float] = time.monotonic,
                  responder: Responder | None = None) -> ExecResult:
    """Call every action, then verify each. `names` maps entity id to a spoken name."""
    if outcome.kind is not Kind.ACTION:
        raise ValueError("execute() needs an ACTION outcome")
    names = names or {}
    if any(a.service == "music_assistant.play_media" for a in outcome.actions):
        timeout = max(timeout, 15.0)  # a streamed track can take a while to start
    failed: dict[Action, str] = {}

    async def call(a: Action) -> None:
        try:
            await backend.call_service(a.service, a.entity_id, a.data_dict())
        except Exception as exc:  # one device refusing must not stop the others, or go unreported
            failed[a] = " ".join(str(exc).split())[:120] or type(exc).__name__

    for batch in _batches(outcome.actions):  # ten lights at once, not one after another
        await asyncio.gather(*(call(a) for a in batch))
    results: list[ActionResult] = []
    deadline = clock() + timeout
    for a in outcome.actions:
        if a in failed:
            results.append(ActionResult(a, False, f"not responding ({failed[a]})"))
            continue
        check = expectation(a)
        if check is None:
            results.append(ActionResult(a, None))
            continue
        while True:
            state = backend.get_state(a.entity_id)
            if state is not None and check(state):
                results.append(ActionResult(a, True))
                break
            if clock() >= deadline:
                seen = state.state if state else "missing"
                results.append(ActionResult(a, False, f"still {seen}"))
                break
            await sleep(interval)
    message = _summary(results, names, outcome.message, responder or Responder())
    rich = len(outcome.actions) > 1 or any(a.service in ("music_assistant.play_media", "media_player.media_play")
                                           for a in outcome.actions)  # music: say which speaker it is on
    if outcome.message and rich and all(r.verified is not False for r in results):
        message = outcome.message  # "Turning off 5 lights in the workshop" says more than "Done"
    return ExecResult(results, message)


def _batches(actions: list[Action]) -> list[list[Action]]:
    """Neighbouring calls to the same service go out together; different services keep their order."""
    batches: list[list[Action]] = []
    for a in actions:
        if batches and batches[-1][0].service == a.service and a not in batches[-1]:
            batches[-1].append(a)
        else:
            batches.append([a])
    return batches


def _summary(results: list[ActionResult], names: dict[str, str], planned: str, say: Responder) -> str:
    failed = [r for r in results if r.verified is False]
    if not failed:
        return say.say("exec_ok") if all(r.verified for r in results) else (planned or say.say("exec_ok"))
    detail = "; ".join(f"{names.get(r.action.entity_id, r.action.entity_id)} is {r.detail}" for r in failed)
    done = len(results) - len(failed)
    if done:
        return say.say("exec_partial", done=done, total=len(results), detail=detail)
    return say.say("exec_failed", detail=detail)
