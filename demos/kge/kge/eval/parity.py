"""Preset parity: the three rule presets, result by result, beside a snapshot frozen before the change.

    python3 -m kge.eval.parity [--profile <id>] --snapshot clients/<client>/baselines/parity-<base>.json
    make parity

The three presets of the control (schema, catalog and graph; PRESETS in
kge/services/ask/engine/config.py) answer every golden and held-out item, on rules, asked as the
snapshot's principal: 45 and 16 items on three arms, 183 results. Each result
keeps five things: whether the golden scorer passed it (kge/eval/run_golden.py,
unchanged), its behaviour, its figure, its parts across units and the joins it
refused. Each is put beside the snapshot's, and every one that moved is
printed. The A/B report cannot show this: its baseline records passes per
question, not figures or refused joins.

The snapshot holds figures and ids only. It is recorded once, with --record,
which never overwrites one, and is never edited. A move the owner asked for is
named in a file of stated changes (--stated): each names the result, the field,
the value it moves to and why. Any other move fails, and so does a stated move
that did not happen.

Exit 0: every result unchanged, or moved as stated. 1: a result moved. 2: it
cannot check: no snapshot, no estate or no graph, a set file that is not the
version the code runs (a golden.json written before golden v2: make golden), a
graph the pipeline has not filled, or the API holding the graph.

The check runs on the profile's graph and sets (kge/pipeline/profile.py: --profile,
else PROFILE, else the registry's default), on Ladybug, and names the store:
the snapshot was taken on Kuzu 0.11.3, the store before Ladybug, and the 183
hold on Ladybug.

Opens the graph: stop the API first (the graph store lets one process hold it).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kge.pipeline.settings import ROOT, eval_concurrency
from kge.services.ask.engine.core import PRESETS, Engine
from kge.eval import sets as setfiles
from kge.eval.asking import Unanswered, each
from kge.eval.harness import stale
from kge.eval.margin import chunks, missing
from kge.eval.run_golden import score, uncertified_ids
from kge.pipeline.envfile import load_env
from kge.pipeline.profile import ProfileError, resolve
from kge.adapters.graphstore import GraphLocked, store_info

DEMO = ROOT
SETS = ("golden", "heldout")
FIELDS = ("ok", "behaviour", "figure", "parts", "joins_refused")


def results(eng: Engine, build: Path, principal: str) -> dict[str, dict]:
    """Every preset on every golden and held-out item: "<set>:<id>:<arm>" -> its kind and FIELDS, as JSON holds them. Asked
    through kge/eval/asking.py each, several at once, in the serial order."""
    unc = uncertified_ids(build)
    asks = [(s, item, arm, cfg) for s in SETS for item in setfiles.items(Path(build) / f"{s}.json") for arm, cfg in PRESETS.items()]

    def result(ask: tuple) -> tuple[str, dict]:
        s, item, arm, cfg = ask
        a = eng.answer(item["question"], principal=principal, config=cfg)
        ok, _ = score(item, a, unc)
        return f"{s}:{item['id']}:{arm}", {"kind": item["kind"], "ok": ok, "behaviour": a.get("behaviour"), "figure": a.get("figure"),
                                           "parts": a.get("parts") if isinstance(a.get("parts"), dict) else None,
                                           "joins_refused": a.get("joins_refused")}
    out = dict(each(result, asks, lambda ask: f"{ask[0]}:{ask[1]['id']}:{ask[2]}"))
    return json.loads(json.dumps(out))


def read_stated(path: Path | None) -> dict[tuple[str, str], dict]:
    """The owner's stated changes, (result, field) -> {to, why}; none without a file."""
    if path is None:
        return {}
    out = {}
    for c in json.loads(Path(path).read_text())["changes"]:
        if not (c.get("result") and c.get("field") in FIELDS and "to" in c and c.get("why")):
            raise ValueError(f"{path}: a stated change names its result, one of the fields {', '.join(FIELDS)}, the value it moves to "
                             f"and why: {c}")
        out[(c["result"], c["field"])] = {"to": c["to"], "why": c["why"]}
    return out


def compare(then: dict[str, dict], now: dict[str, dict], stated: dict[tuple[str, str], dict] | None = None) -> tuple[list[str], list[str]]:
    """(moved, as_stated): a line for each result field that moved and is not a stated change, or was stated and did not
    move so; and a line for each that moved as stated."""
    stated = stated or {}
    moved, as_stated = [], []
    for key in sorted(set(then) | set(now)):
        if key not in now:
            moved.append(f"{key}: in the snapshot and not run now")
            continue
        if key not in then:
            moved.append(f"{key}: run now and not in the snapshot")
            continue
        for f in FIELDS:
            was, got, want = then[key].get(f), now[key].get(f), stated.get((key, f))
            if want is not None:
                if got == want["to"]:
                    as_stated.append(f"{key} {f}: {shown(was)} → {shown(got)}, as stated: {want['why']}")
                else:
                    moved.append(f"{key} {f}: {shown(was)} → {shown(got)}; the stated change is to {shown(want['to'])}")
            elif got != was:
                moved.append(f"{key} {f}: {shown(was)} → {shown(got)}")
    return moved, as_stated


def shown(v) -> str:
    return json.dumps(v, sort_keys=True)


def table(then: dict[str, dict], now: dict[str, dict]) -> list[str]:
    """Passed per set and arm, in the snapshot and now."""
    lines = []
    for s in SETS:
        for arm in PRESETS:
            keys = [k for k in then if k.startswith(f"{s}:") and k.endswith(f":{arm}")]
            was = sum(then[k]["ok"] for k in keys)
            got = sum(bool(now.get(k, {}).get("ok")) for k in keys)
            lines.append(f"  {s:<8} {arm:<8} passed {was:>2} of {len(keys)} in the snapshot, {got:>2} now")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile whose graph and sets to check (default: PROFILE, else the registry's default)")
    ap.add_argument("--snapshot", required=True, help="the frozen snapshot, clients/<client>/baselines/parity-<base>.json")
    ap.add_argument("--stated", help="the owner's stated changes: {\"changes\": [{result, field, to, why}]}")
    ap.add_argument("--record", metavar="TAKEN_ON", help="record the snapshot instead, naming what it was taken on (the commit and "
                                                         "the graph store); never overwrites one")
    ap.add_argument("--as", dest="principal", help="with --record, the querying identity to ask as (default: the one the "
                                                   "profile's deployment names, asker in its identities)")
    args = ap.parse_args(argv)
    load_env()
    try:
        prof = resolve(args.profile)
        asking = prof.asking(args.principal) if args.record else None
        eval_concurrency()
    except (ProfileError, ValueError) as exc:
        print(f"parity: {exc}", file=sys.stderr)
        return 2
    build, path = prof.estate(), Path(args.snapshot)
    if args.record and path.exists():
        print(f"parity: {path} exists, and a snapshot is frozen: record a new one under its own name", file=sys.stderr)
        return 2
    if not args.record and not path.is_file():
        print(f"parity: no snapshot {path}", file=sys.stderr)
        return 2
    snap = None if args.record else json.loads(path.read_text())
    try:
        stated = read_stated(Path(args.stated) if args.stated else None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"parity: cannot read the stated changes: {exc}", file=sys.stderr)
        return 2
    why = missing(prof, list(SETS))
    if why:
        print(f"parity: {why}, then make parity", file=sys.stderr)
        return 2
    why = next(filter(None, (stale(build, s) for s in SETS)), None)
    if why:
        print(f"parity: {why}, then make parity", file=sys.stderr)
        return 2
    try:
        eng = Engine.serving(prof)
    except (GraphLocked, FileNotFoundError) as exc:
        print(f"parity: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        if "does not exist" in str(exc):
            print(f"parity: the graph {prof.graph_path()} was never loaded: make graph, then make pipeline, then make parity",
                  file=sys.stderr)
        elif "no client domain" in str(exc):
            print(f"parity: {exc}, then make pipeline, then make parity", file=sys.stderr)
        else:
            raise
        return 2
    try:
        if not chunks(eng):
            print("parity: the graph holds no chunks: make pipeline fills it after make graph, then make parity", file=sys.stderr)
            return 2
        principal = asking if snap is None else snap["principal"]
        with eng.g.reader() as conn:
            store = store_info(conn)
        now = results(eng, build, principal)
    except Unanswered as exc:
        print(f"parity: cannot check: {exc}", file=sys.stderr)
        return 2
    finally:
        eng.close()
    if args.record:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "about": "Preset parity (kge/eval/parity.py): the three rule presets on the golden and held-out items, figures and ids only. "
                     "Frozen: recorded once, never edited.",
            "taken_on": args.record, "principal": principal, "arms": list(PRESETS), "fields": list(FIELDS),
            "sets": {s: sum(k.startswith(f"{s}:") for k in now) // len(PRESETS) for s in SETS}, "results": now}, indent=1, sort_keys=True) + "\n")
        print(f"parity: recorded {len(now)} results in {path}, taken on {args.record}")
        return 0
    then = snap["results"]
    moved, as_stated = compare(then, now, stated)
    print(f"parity: the presets ({', '.join(PRESETS)}) on {', '.join(SETS)}, asked as {principal}, on {store['engine']} "
          f"{store['version']}, beside {path} (taken on {snap.get('taken_on')})")
    print("\n".join(table(then, now)))
    for line in as_stated:
        print("  stated: " + line)
    if moved:
        keys = {m.split(" ", 1)[0].rstrip(":") for m in moved}
        print(f"\nFAILED: {len(keys)} of {len(then)} results moved:")
        for line in moved:
            print("  " + line)
        print("\nOnly a change the owner stated may move a result, and the snapshot is never edited to absorb one.")
        return 1
    unchanged = len(then) - len({s.split(" ", 1)[0] for s in as_stated})
    print(f"\npassed: {unchanged} of {len(then)} results unchanged" + (f", {len(then) - unchanged} moved as stated" if as_stated else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
