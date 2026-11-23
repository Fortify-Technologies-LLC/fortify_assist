"""The standing benchmark: the same phrases on every version, kept as a history you can look back on.

    python -m fortify_eval.bench run --label 0.1.4                 # rules only
    python -m fortify_eval.bench run --label 0.1.4 --llm           # with FORTIFY_OLLAMA_URL / FORTIFY_OLLAMA_MODEL
    python -m fortify_eval.bench run --label 0.1.0 --core <old checkout>/custom_components/fortify_assist/core
    python -m fortify_eval.bench report                            # rewrite benchmark/REPORT.md from the history

Each run is saved as benchmark/history/<label>__<mode>.json. `--core` loads an older copy of the core, so
earlier versions can be scored on today's phrases. The house is the synthetic one, built with the
loaded core's own types so old and new versions see exactly the same home.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"
PHRASES = BENCH / "phrases.yaml"
HISTORY = BENCH / "history"
HOUSE = ROOT / "eval" / "houses" / "synthetic.yaml"
MUSIC_PLAYERS = ("media_player.bar_speaker", "media_player.kitchen_speaker")


def load_core(path: Path | None):
    """Import fortify_core from `path` (an older checkout) or the installed one."""
    if path is not None:
        for name in [m for m in sys.modules if m == "fortify_core" or m.startswith("fortify_core.")]:
            del sys.modules[name]
        spec = importlib.util.spec_from_file_location("fortify_core", path / "__init__.py",
                                                      submodule_search_locations=[str(path)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["fortify_core"] = module
        spec.loader.exec_module(module)
    import fortify_core
    return fortify_core


def build_house(types):
    raw = yaml.safe_load(HOUSE.read_text(encoding="utf-8"))
    floors = {f["id"]: f["name"] for f in raw.get("floors", [])}
    areas = {a["id"]: (a["name"], a.get("floor")) for a in raw.get("areas", [])}
    entities = {}
    for e in raw["entities"]:
        attrs = dict(e.get("attributes", {}))
        if e["id"] in MUSIC_PLAYERS:
            attrs["mass_player_type"] = "player"
        entities[e["id"]] = types.Entity(
            id=e["id"], name=e["name"], area=e.get("area"), state=str(e.get("state", "unknown")),
            aliases=list(e.get("aliases", [])), exposed=bool(e.get("exposed", True)),
            sensitive=bool(e.get("sensitive", False)), device_class=e.get("device_class"), unit=e.get("unit"),
            attributes=attrs)
    return types.House(floors, areas, entities)


class FakeMusic:
    """A tiny Music Assistant library, so music requests are scored the same way every time."""

    def __init__(self, hit_type):
        self.lib = {
            "tracks": [hit_type("library://track/1", "Seven Nation Army", "track", ("The White Stripes",), True)],
            "playlists": [hit_type("library://playlist/2", "Jazz Classics", "playlist", (), True)],
            "audiobooks": [hit_type("library://audiobook/3", "Dune", "audiobook", ("Frank Herbert",), True)],
        }

    async def search(self, query, artist=None, limit=6):
        return self.lib

    async def library(self, media_type, limit=25):
        return []


def make_pipeline(core, house, tiers):
    params = inspect.signature(core.Pipeline).parameters
    kwargs = {}
    if "llm_tiers" in params:
        kwargs["llm_tiers"] = tiers
    if "music_provider" in params:
        try:
            from fortify_core.music import MusicHit
            kwargs["music_provider"] = FakeMusic(MusicHit)
        except ImportError:
            pass
    return core.Pipeline(house, **kwargs)


def make_chatter(core, pipeline, tiers):
    try:
        from fortify_core.chat import Chatter
    except ImportError:
        return None
    return Chatter(pipeline.persona, pipeline.responder, tiers) if tiers else None


async def run_case(core, case: dict, tiers, chatter_wanted: bool) -> dict:
    house = build_house(importlib.import_module("fortify_core.types"))
    pipeline = make_pipeline(core, house, tiers)
    chatter = make_chatter(core, pipeline, tiers) if chatter_wanted else None
    turns = case.get("turns") or [case["text"]]
    room = case.get("room")
    started = time.monotonic()
    out, trace = None, []
    for text in turns:
        out = await pipeline.ahandle(text, room, "bench")
        trace += list(getattr(pipeline, "trace", []))
    message = out.message
    if out.kind.value == "chat" and not message and chatter is not None:
        params = inspect.signature(chatter.reply).parameters
        extra = {"home_request": pipeline.home_related(turns[-1], room)} if "home_request" in params else {}
        facts = pipeline.home_facts(turns[-1], room) if hasattr(pipeline, "home_facts") else []
        rooms = [name for name, _ in house.areas.values()]
        try:
            message = await chatter.reply([], turns[-1], dt.datetime.now(), rooms, facts, **extra)
        except Exception as exc:  # a chat failure is recorded, not fatal
            message = f"(chat failed: {exc})"
    ms = (time.monotonic() - started) * 1000
    actions = [{"service": a.service, "entity_id": a.entity_id, "data": a.data_dict()} for a in out.actions]
    return {"id": case["id"], "group": case["group"], "text": " / ".join(turns), "room": room,
            "kind": out.kind.value, "actions": actions, "entities": list(out.entities), "message": message,
            "ms": round(ms), "llm": trace, "verdict": verdict(case.get("expect", {}), out.kind.value, actions,
                                                              list(out.entities))}


def verdict(expect: dict, kind: str, actions: list[dict], entities: list[str]) -> str:
    want = expect.get("kind", "any")
    if want == "any":
        return "recorded"
    if kind == "action":
        if want != "action":
            return "wrong_action"
        if "targets" in expect and {a["entity_id"] for a in actions} != set(expect["targets"]):
            return "wrong_action"
        if "service" in expect and any(a["service"] != expect["service"] for a in actions):
            return "wrong_action"
        for field, value in (expect.get("data") or {}).items():
            if any(a["data"].get(field) != value for a in actions):
                return "wrong_action"
        return "pass"
    if want == "action":
        return "missed"
    if want == "no_action":
        return "pass"
    if kind != want:
        return "wrong_response"
    if want == "answer" and "targets" in expect and set(entities) != set(expect["targets"]):
        return "wrong_response"
    return "pass"


def _llm_tiers(core):
    url, model = os.environ.get("FORTIFY_OLLAMA_URL"), os.environ.get("FORTIFY_OLLAMA_MODEL")
    if not url or not model:
        raise SystemExit("--llm needs FORTIFY_OLLAMA_URL and FORTIFY_OLLAMA_MODEL")
    from fortify_core.llm import OllamaClient
    return [OllamaClient(url, model)], model


def _version(core_dir: Path) -> str:
    manifest = core_dir.parent / "manifest.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version", "?")
    except OSError:
        return "?"


def _commit(core_dir: Path) -> str:
    if (stamp := core_dir.parent / "COMMIT").exists():  # an exported older version records its commit here
        return stamp.read_text(encoding="utf-8").strip()
    try:
        head = subprocess.run(["git", "-C", str(core_dir), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(core_dir), "status", "--porcelain", "."], capture_output=True,
                               text=True, check=True).stdout.strip()
        return f"{head}+changes" if dirty else head
    except (OSError, subprocess.CalledProcessError):
        return "?"


def cmd_run(args) -> int:
    core = load_core(args.core)
    core_dir = Path(core.__file__).parent
    tiers, model = _llm_tiers(core) if args.llm else ([], None)
    cases = [c for c in yaml.safe_load(PHRASES.read_text(encoding="utf-8")) if not c.get("retired")]
    if args.only:
        cases = [c for c in cases if c["group"] in args.only.split(",") or c["id"] in args.only.split(",")]
    results = []
    for case in cases:
        r = asyncio.run(run_case(core, case, tiers, args.llm))
        results.append(r)
        mark = {"pass": "ok ", "recorded": "-- "}.get(r["verdict"], "XX ")
        acts = ", ".join(f"{a['service']} {a['entity_id']}" for a in r["actions"])
        print(f"{mark}{r['id']:<12} {r['kind']:<8} {r['text'][:60]!r} {acts[:90]} | {r['message'][:80]!r}")
    run = {"label": args.label, "version": _version(core_dir), "commit": _commit(core_dir),
           "date": dt.datetime.now().isoformat(timespec="minutes"), "mode": "llm" if args.llm else "rules",
           "model": model, "results": results}
    HISTORY.mkdir(parents=True, exist_ok=True)
    path = HISTORY / f"{args.label}__{run['mode']}.json"
    path.write_text(json.dumps(run, indent=1, ensure_ascii=False), encoding="utf-8")
    scored = [r for r in results if r["verdict"] != "recorded"]
    passed = sum(r["verdict"] == "pass" for r in scored)
    wrong = sum(r["verdict"] == "wrong_action" for r in scored)
    print(f"\n{passed}/{len(scored)} passed, {wrong} wrong actions -> {path.relative_to(ROOT)}")
    write_report()
    return 0


# ---- the report ---------------------------------------------------------------------------------------

def _runs() -> list[dict]:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(HISTORY.glob("*.json"))]
    def order(run):  # by version, then rules before llm: 0.1.3 < 0.1.3-fix < 0.1.4
        parts = re.split(r"[.\-]", run["label"])
        return [(0, int(x)) if x.isdigit() else (1, x) for x in parts], run["mode"] != "rules"
    return sorted(runs, key=order)


def _rate(results) -> str:
    scored = [r for r in results if r["verdict"] != "recorded"]
    if not scored:
        return "-"
    return f"{sum(r['verdict'] == 'pass' for r in scored)}/{len(scored)}"


def write_report() -> Path:
    runs = _runs()
    groups = list(dict.fromkeys(r["group"] for run in runs for r in run["results"]))
    lines = ["# Fortify Assist benchmark history", "",
             "Generated by `python -m fortify_eval.bench report` from `benchmark/history/`. The phrases are in",
             "`benchmark/phrases.yaml`; every run uses the same synthetic house. Do not edit this file by hand.", "",
             "## Runs", "",
             "| run | version | commit | date | mode | model | passed | wrong actions | " + " | ".join(groups) + " |",
             "|---|---|---|---|---|---|---|---|" + "---|" * len(groups)]
    for run in runs:
        res = run["results"]
        wrong = sum(r["verdict"] == "wrong_action" for r in res)
        by = [_rate([r for r in res if r["group"] == g]) for g in groups]
        lines.append(f"| {run['label']} | {run['version']} | {run['commit']} | {run['date'][:10]} | {run['mode']} | "
                     f"{run.get('model') or '-'} | {_rate(res)} | {wrong} | " + " | ".join(by) + " |")
    lines += ["", "## Every phrase", "", "`ok` passed, `miss` did not act when it should, `WRONG` acted wrongly, "
              "`resp` wrong kind of reply, `-` recorded only.", ""]
    cols = runs[-8:]
    lines.append("| id | phrase | " + " | ".join(f"{r['label']} {r['mode']}" for r in cols) + " |")
    lines.append("|---|---|" + "---|" * len(cols))
    short = {"pass": "ok", "missed": "miss", "wrong_action": "**WRONG**", "wrong_response": "resp", "recorded": "-"}
    ids = list(dict.fromkeys(r["id"] for run in runs for r in run["results"]))
    texts = {r["id"]: r["text"] for run in runs for r in run["results"]}
    for cid in ids:
        cells = []
        for run in cols:
            hit = next((r for r in run["results"] if r["id"] == cid), None)
            cells.append(short.get(hit["verdict"], "?") if hit else "")
        lines.append(f"| {cid} | {texts[cid]} | " + " | ".join(cells) + " |")
    if runs:
        latest = runs[-1]
        lines += ["", f"## What {latest['label']} ({latest['mode']}) said", "", "| id | decision | reply | ms |",
                  "|---|---|---|---|"]
        for r in latest["results"]:
            reply = (r["message"] or "").replace("|", "/").replace("\n", " ")
            lines.append(f"| {r['id']} | {r['kind']} | {reply[:160]} | {r['ms']} |")
    path = BENCH / "REPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fortify-bench")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--llm", action="store_true")
    r.add_argument("--core", type=Path)
    r.add_argument("--only", help="comma-separated groups or ids")
    sub.add_parser("report")
    args = p.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    print(f"wrote {write_report().relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
