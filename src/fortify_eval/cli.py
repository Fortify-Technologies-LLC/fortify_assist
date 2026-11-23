"""fortify-eval: generate corpora and score agents.

    fortify-eval run exact-name
    fortify-eval run mypackage.mymodule:MyAgent --min-accuracy 0.95 --max-wrong-action 0.01
    fortify-eval stats
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections import Counter
from pathlib import Path

from .baselines import BASELINES
from .corpus import build_corpus
from .house import load_house
from .runner import run, write_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOUSE = ROOT / "eval" / "houses" / "synthetic.yaml"
DEFAULT_HAND = [ROOT / "eval" / "corpus" / "hard.yaml", ROOT / "eval" / "corpus" / "heldout.yaml", ROOT / "eval" / "corpus" / "heldout2.yaml", ROOT / "eval" / "corpus" / "qa.yaml", ROOT / "eval" / "corpus" / "controls.yaml"]


def _load_agent(spec: str):
    if spec in BASELINES:
        return BASELINES[spec]
    module, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit(f"unknown agent {spec!r}; use a baseline name or 'module:Class'")
    return getattr(importlib.import_module(module), attr)()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fortify-eval")
    p.add_argument("--house", type=Path, default=DEFAULT_HOUSE)
    p.add_argument("--seed", type=int, default=7)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="score an agent")
    r.add_argument("agent")
    r.add_argument("--min-accuracy", type=float, default=0.0)
    r.add_argument("--max-wrong-action", type=float, default=1.0)
    r.add_argument("--failures", type=int, default=15)
    r.add_argument("--json-dir", default="reports")
    sub.add_parser("stats", help="describe the corpus")
    args = p.parse_args(argv)

    house = load_house(args.house)
    hand = DEFAULT_HAND if args.house == DEFAULT_HOUSE else []
    cases = build_corpus(house, hand, args.seed)

    if args.cmd == "stats":
        counts = Counter(c.category for c in cases)
        print(f"{len(cases)} cases")
        for cat, n in sorted(counts.items()):
            print(f"  {cat:<12}{n:>5}")
        return 0

    report = run(_load_agent(args.agent), house, cases)
    print(report.format())
    print(report.failures(args.failures))
    print(f"\nwrote {write_json(report, args.json_dir)}")
    ok = report.accuracy >= args.min_accuracy and report.wrong_action_rate <= args.max_wrong_action
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
