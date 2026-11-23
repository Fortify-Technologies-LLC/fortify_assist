"""Run an agent over a corpus and summarise the results."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .scoring import Verdict, score
from .types import Agent, Case, House, Kind, Outcome


@dataclass
class Result:
    case: Case
    actual: Outcome
    verdict: Verdict
    seconds: float


@dataclass
class Report:
    agent: str
    results: list[Result] = field(default_factory=list)

    def rate(self, verdict: Verdict, results=None) -> float:
        rs = self.results if results is None else results
        return sum(r.verdict is verdict for r in rs) / len(rs) if rs else 0.0

    @property
    def accuracy(self) -> float:
        return self.rate(Verdict.PASS)

    @property
    def wrong_action_rate(self) -> float:
        return self.rate(Verdict.WRONG_ACTION)

    def by_category(self) -> dict[str, list[Result]]:
        groups: dict[str, list[Result]] = defaultdict(list)
        for r in self.results:
            groups[r.case.category].append(r)
        return dict(sorted(groups.items()))

    def format(self) -> str:
        lines = [f"agent: {self.agent}   cases: {len(self.results)}", ""]
        lines.append(f"{'category':<12}{'n':>5}{'pass':>8}{'wrong-act':>11}{'missed':>8}{'wrong-resp':>12}")
        for cat, rs in self.by_category().items():
            lines.append(
                f"{cat:<12}{len(rs):>5}{self.rate(Verdict.PASS, rs):>8.1%}"
                f"{self.rate(Verdict.WRONG_ACTION, rs):>11.1%}{self.rate(Verdict.MISSED, rs):>8.1%}"
                f"{self.rate(Verdict.WRONG_RESPONSE, rs):>12.1%}"
            )
        lines.append("")
        lines.append(f"{'OVERALL':<12}{len(self.results):>5}{self.accuracy:>8.1%}"
                     f"{self.wrong_action_rate:>11.1%}{self.rate(Verdict.MISSED):>8.1%}"
                     f"{self.rate(Verdict.WRONG_RESPONSE):>12.1%}")
        times = sorted(r.seconds for r in self.results)
        if times:
            lines.append(f"latency p50 {times[len(times) // 2] * 1000:.1f} ms, "
                         f"p95 {times[int(len(times) * 0.95)] * 1000:.1f} ms")
        return "\n".join(lines)

    def failures(self, limit: int = 15) -> str:
        bad = [r for r in self.results if r.verdict is not Verdict.PASS]
        bad.sort(key=lambda r: r.verdict is not Verdict.WRONG_ACTION)  # wrong actions first
        out = [f"\nfirst {min(limit, len(bad))} of {len(bad)} failures (wrong actions first):"]
        for r in bad[:limit]:
            ctx = f" [room={r.case.context_area}]" if r.case.context_area else ""
            out.append(f"  {r.verdict.value:<14}{r.case.id:<14}{r.case.utterance!r}{ctx}"
                       f"  expected {r.case.expect.kind.value}, got {r.actual.kind.value}")
        return "\n".join(out)

    def to_json(self) -> dict:
        return {
            "agent": self.agent,
            "total": len(self.results),
            "accuracy": self.accuracy,
            "wrong_action_rate": self.wrong_action_rate,
            "verdicts": Counter(r.verdict.value for r in self.results),
            "cases": [
                {"id": r.case.id, "category": r.case.category, "utterance": r.case.utterance,
                 "context_area": r.case.context_area, "verdict": r.verdict.value,
                 "expected": r.case.expect.kind.value, "got": r.actual.kind.value}
                for r in self.results
            ],
        }


def run(agent: Agent, house: House, cases: list[Case]) -> Report:
    report = Report(agent.name)
    for case in cases:
        start = time.perf_counter()
        try:
            actual = agent.handle(case.utterance, house, case.context_area)
        except Exception as exc:  # a crash is a failure, not a harness error
            actual = Outcome(Kind.REFUSE, message=f"agent crashed: {exc!r}")
        elapsed = time.perf_counter() - start
        report.results.append(Result(case, actual, score(case.expect, actual), elapsed))
    return report


def write_json(report: Report, directory: str | Path = "reports") -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"{report.agent}.json"
    out.write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")
    return out
