"""The A/B harness: each arm on each set, N times, one record per question.

    python3 -m kge.eval.harness [--profile <id>] [--arms all|<arm>,...] [--sets golden,heldout] [--repeats 3] [--as <principal>]
    make ab REPEATS=3 ARMS=control,treatment

The arms are the three run-config presets, the control of kge/eval/run_golden.py
(schema, catalog and graph, rule-based), and the reference agent's two
(kge/services/ask/engine/agent.py: control, graph off; treatment, graph on). All five run offline.
The agent runs on rules unless a model is configured (MODEL_PROVIDER), and then
both its arms use it, with the same prompts and seed. MODEL_PROVIDER=mock runs
its model path on the offline mock, and every record and report says mock.

It runs on the profile's graph (kge/pipeline/profile.py): --profile, else PROFILE,
else the registry's default. The sets are the gated golden set and the held-out
paraphrases, reported apart, each named and versioned (SETS below), in the
profile's estate (Profile.estate, build/ in the demo); a further set drops in
by name, <name>_v<n>.json, and all runs every further set whose file the
estate holds, after the named ones. A client's own set is its estate's to hold:
the platform names none. A set file the code does not run, such as a golden.json
written before golden v2, stops the run, naming make golden. Each answer is
scored by the golden scorer, unchanged, and measured beside it
(kge/eval/metrics.py). An item that names its principal (a refusal by the
join-time access rule, say) is asked as that principal, and its record says
so; every other item is asked as the run's. A run writes runs/<run_id>/ in the
profile's run state (Profile.run_dir, build/stores/<client>/run/ in the demo):

  manifest.json  what ran: each arm and what ran it (rules, mock or model); each
                 set's version and the sha256 of its file, and what it declares
                 it changed since the version before (golden v2, since v1);
                 the estate's seed; the profile;
                 the repeats and principal; the graph's hashes and its store
                 (engine, version, storage version); the landed release the
                 graph was loaded from, whose tiers the citations are measured
                 by; the model's identity and seed; the prices
  records.jsonl  one record per repeat, arm, set and question: pass or fail and
                 why, who it was asked as, its category where the item names
                 one, the measures, the answer's structured result and its run
                 trace (ids, never chunk text or prompts)
  baseline.json  with --baseline, the baseline the run is put beside (a copy of
                 the client's, as make ab passes the Makefile's BASELINE), so
                 its report needs nothing else
  summary.json, report.md, report.html
                 kge/eval/report.py, from the run's own files alone

Two runs are put item by item beside each other only on the same set, or on a
set that declares what it changed since the other's: golden v2 (issue #39)
changes two of v1's items, as the owner stated, and declares it, so its other
items are still put beside a baseline on v1.

Each repeat, arm and set is asked as one batch through kge/eval/asking.py each: EVAL_CONCURRENCY questions at a time
(8 unset), each with a run trace of its own, its records written in the set's order once the batch is done, so the
records, the scores and the report are a serial run's (EVAL_CONCURRENCY=1 asks one at a time, as before). A question
that meets a rate limit or a 5xx from BigQuery or Vertex AI is asked again after a back-off, never scored on it; one
that meets it on every try stops the run (Unanswered).

Opens the graph: stop the API first (the graph store lets one process hold it).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, Engine
from kge.eval import metrics, report, sets as setfiles
from kge.eval.asking import Unanswered, again, each
from kge.eval.run_golden import asked_as, score, uncertified_ids, unknown_principal
from kge.landing.zone import LandingError
from kge.pipeline.envfile import load_env
from kge.adapters.model import ModelSettingError
from kge.pipeline.settings import eval_concurrency
from kge.pipeline.profile import ProfileError, resolve
from kge.adapters.graphstore import GraphLocked

ARM_NAMES = list(PRESETS) + list(AGENT_ARMS)
SIDES = {**{p: "preset" for p in PRESETS}, "control": "agent, graph off", "treatment": "agent, graph on"}
SETS = {"golden": "v2", "heldout": "v1"}
FURTHER = re.compile(r"^[a-z][a-z0-9_]*_(v\d+)$")
FURTHER_BASE = re.compile(r"_v\d+$")
KEEP = ("behaviour", "figure", "unit", "column", "join_path", "sql", "queries", "parts", "commensurable", "missing_parts",
        "joins_refused", "cite", "citations_rejected", "denied_warehouses", "reason", "phrased_by", "answer_text")


def choose_arms(choice: str) -> list[str]:
    """all, or a comma list of arms; an unknown name stops the run."""
    names = ARM_NAMES if choice.strip() == "all" else [a.strip() for a in choice.split(",") if a.strip()]
    for a in names:
        if a not in ARM_NAMES:
            raise SystemExit(f"harness: no arm {a!r}; the arms are {', '.join(ARM_NAMES)}")
    return list(dict.fromkeys(names))


def set_version(name: str, declared: dict | None = None) -> str | None:
    """The version a run expects of a set: the one the profile's pack names for it (declared: kge/eval/sets.py declared), else
    the harness's own (SETS), else a further set's, from its name."""
    if declared and name in declared:
        return declared[name]
    if name in SETS:
        return SETS[name]
    m = FURTHER.match(name)
    return m.group(1) if m else None


def base_sets(declared: dict | None = None) -> list[str]:
    """The sets SETS names that a run on all takes: those the profile's pack names, where it names any."""
    return [s for s in SETS if not declared or s in declared]


def further(build: Path) -> list[str]:
    """The further sets whose file the build holds, build/<name>_v<n>.json, by name."""
    return sorted(p.stem for p in Path(build).glob("*.json") if p.is_file() and p.stem not in SETS and FURTHER.match(p.stem))


def choose_sets(choice: str, build: Path, declared: dict | None = None) -> list[str]:
    """all (the sets SETS names that the profile's pack names, every one where it names none, then each further set
    whose file is in the build), or a comma list of them and of further sets whose file is in the build. A set the
    pack does not name, where it names its sets, stops the run, as does a set whose file is not the version the run
    expects, or one that cannot be read."""
    names = base_sets(declared) + further(build) if choice.strip() == "all" else [s.strip() for s in choice.split(",") if s.strip()]
    for s in names:
        if s not in SETS and not (FURTHER.match(s) and (Path(build) / f"{s}.json").is_file()):
            raise SystemExit(f"harness: no set {s!r}; the sets are {', '.join(base_sets(declared))}, or a further one named "
                             f"<name>_v<n> with its file in {build}")
        if s in SETS and s not in base_sets(declared):
            raise SystemExit(f"harness: no set {s!r} in this profile's pack (tests.sets): its sets are "
                             f"{', '.join(base_sets(declared))}, or a further one named <name>_v<n> with its file in {build}")
        why = stale(build, s, declared)
        if why:
            raise SystemExit(f"harness: {why}")
    return list(dict.fromkeys(names))


def stale(build: Path, name: str, declared: dict | None = None) -> str | None:
    """Why the set's file in the build is not the version the run expects (set_version), or cannot be read; None if it
    is, or if there is no file yet (what the run needs and the build lacks is said elsewhere)."""
    path = Path(build) / f"{name}.json"
    if not path.is_file():
        return None
    try:
        got = setfiles.read(path)["version"]
    except ValueError as exc:
        return str(exc)
    want = set_version(name, declared)
    if got != want:
        base = name if name in SETS else FURTHER_BASE.sub("", name)
        return (f"{path} holds {base} {got}, and this code runs {base} {want}: make golden writes the sets again from the "
                "warehouses on disk")
    return None


def describe_sets(build: Path, questions: dict[str, list[dict]], declared: dict | None = None) -> dict:
    """Each set run: how many of its items ran, how many it holds, its version, its file and the file's sha256, and
    what it declares it changed from the version before, if it does."""
    out = {}
    for s, qs in questions.items():
        path = Path(build) / f"{s}.json"
        data = setfiles.read(path)
        out[s] = {"items": len(qs), "size": len(data["items"]), "version": set_version(s, declared), "file": path.name,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if data["changes_from"]:
            out[s]["changes_from"] = data["changes_from"]
    return out


def estate(build: Path) -> dict:
    """What generated the estate: its seed, as kge/estate/generate.py recorded it in build/summary.json (None if it did not)."""
    path = Path(build) / "summary.json"
    return {"seed": (json.loads(path.read_text()) if path.exists() else {}).get("seed"), "generator": "kge/estate/generate.py"}


class Arms:
    """Each arm as one call, (question, principal) -> the answer with its run trace."""

    def __init__(self, engine: Engine, llm="auto"):
        self.e = engine
        self.agent = Agent(engine, llm=llm)

    def describe(self, arm: str) -> dict:
        """The arm, which side of the A/B it is on, the endpoint it stands for, and what runs it."""
        if arm in PRESETS:
            cfg, mode, path = PRESETS[arm], "rules", "answer"
        else:
            cfg, mode, path = self.agent.config(arm), self.agent.mode, "agent"
        return {"arm": arm, "side": SIDES[arm], "path": path, "mode": mode, "config": cfg.label, "stages": cfg.stages()}

    def about(self) -> dict:
        return {"llm": runtrace.identity(self.agent.llm), "graph": runtrace.fingerprint(self.e.g), "release": self.e.g.release()}

    def ask(self, arm: str, question: str, principal: str) -> dict:
        if arm in PRESETS:
            return runtrace.run("answer", lambda: self.e.answer(question, principal, config=PRESETS[arm]), graph=self.e.g)
        return runtrace.run("agent", lambda: self.agent.ask(question, principal, arm=arm), graph=self.e.g)


def compact(ans: dict) -> dict:
    out = {k: ans[k] for k in KEEP if k in ans}
    if not isinstance(out.get("parts"), dict):
        out.pop("parts", None)
    if isinstance(ans.get("citations"), list):
        out["citations"] = [{k: c.get(k) for k in ("n", "doc", "tier", "cited_as", "version", "attribution")} for c in ans["citations"]]
    if ans.get("references"):
        out["references"] = [r["doc"] if isinstance(r, dict) else r for r in ans["references"]]
    return out


def load(build: Path, sets: list[str], items: list[str] | None) -> dict[str, list[dict]]:
    """Each set's questions, or only the ones items names; an id in no set stops the run."""
    out = {s: setfiles.items(build / f"{s}.json") for s in sets}
    if items:
        unknown = set(items) - {i["id"] for qs in out.values() for i in qs}
        if unknown:
            raise SystemExit(f"harness: no question {', '.join(sorted(unknown))} in {', '.join(sets)}")
        out = {s: [i for i in qs if i["id"] in items] for s, qs in out.items()}
    return {s: qs for s, qs in out.items() if qs}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(profile, arms, names: list[str], sets: list[str], repeats: int, principal: str | None = None,
        items: list[str] | None = None, out: Path | None = None, run_id: str | None = None, echo=print,
        baseline: Path | None = None) -> Path:
    """Every arm on every set of the profile's estate, repeats times, asked as principal, else as the querying identity
    the profile's deployment names (Profile.asking); the records, then the report, beside the baseline if one is given, in
    out (default: the profile's runs). Returns the run's folder. The run pins the prices of the one model its arms use,
    so its records cannot mix tiers, and each repeat runs every arm, so drift over the run falls on all of them."""
    build = profile.estate()
    principal = profile.asking(principal)
    eval_concurrency()
    if repeats < 1:
        raise ValueError("repeats must be 1 or more")
    if baseline is not None and not Path(baseline).is_file():
        raise SystemExit(f"harness: no baseline {baseline}; record one with make baseline RUN=<run_id>")
    questions = load(build, sets, items)
    about = arms.about()
    pinned_llm = getattr(getattr(arms, "agent", None), "llm", None)
    landing, price, unc = metrics.load_landing(profile.landing, about.get("release")), metrics.prices(pinned_llm), uncertified_ids(build)
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    run_dir = Path(out or profile.run_dir() / "runs") / run_id
    run_dir.mkdir(parents=True)
    if baseline is not None:
        shutil.copyfile(baseline, run_dir / "baseline.json")
    described = {a: arms.describe(a) for a in names}
    manifest = {"run_id": run_id, "started_at": now(), "finished_at": None, "principal": principal, "repeats": repeats,
                "sets": describe_sets(build, questions, setfiles.declared(profile.paths()["pack"])), "estate": estate(build), "items": items or None, "arms": list(described.values()),
                **about, "prices": price, "profile": profile.id,
                "scorer": "kge/eval/run_golden.py score", "measures": "kge/eval/metrics.py",
                "baseline": str(baseline) if baseline is not None else None}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    records = run_dir / "records.jsonl"
    for n in range(1, repeats + 1):
        for arm in names:
            for s, qs in questions.items():
                started, passed = time.perf_counter(), 0
                asked = each(lambda item: one(arms, arm, item, s, n, run_id, principal, described[arm], landing, price, unc), qs,
                             lambda item: f"{item['id']} on {arm}, repeat {n}")
                for rec in asked:
                    passed += rec["ok"]
                    runtrace.append_jsonl(records, rec)
                echo(f"repeat {n} of {repeats}  {arm:<9} {s:<8} {passed:>3} of {len(qs):<3} {time.perf_counter() - started:6.1f}s")
    manifest["finished_at"] = now()
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    report.write(run_dir)
    return run_dir


def one(arms, arm: str, item: dict, set_name: str, repeat: int, run_id: str, principal: str, described: dict,
        landing: dict, price: dict, unc: set[str]) -> dict:
    """One question on one arm, asked as the item's principal if it names one, else the run's: scored, measured and
    recorded. An arm that fails on it is a fail, with the error, and the run goes on; a failure that may pass if asked
    again is raised, for kge/eval/asking.py to ask again, and is never a fail."""

    error, principal = None, asked_as(item, principal)
    try:
        ans = arms.ask(arm, item["question"], principal)
        ok, why = score(item, ans, unc)
    except Exception as exc:  # noqa: BLE001
        if again(exc):
            raise
        error = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:200]}"
        ans, ok, why = {"question": item["question"]}, False, error
    rt = ans.get("run_trace") or {}
    return {"run_id": run_id, "repeat": repeat, "arm": arm, "side": described["side"],
            "mode": (ans.get("agent") or {}).get("mode") or described["mode"], "config": (rt.get("config") or {}).get("label") or described["config"],
            "set": set_name, "id": item["id"], "kind": item["kind"], **({"category": item["category"]} if item.get("category") else {}),
            "question": item["question"], "principal": principal,
            "ok": ok, "why": why, **({"error": error} if error else {}), "measures": metrics.measure(item, ans, landing, price),
            "answer": compact(ans), "run_trace": rt or None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile whose graph and sets to run (default: PROFILE, else the registry's default)")
    ap.add_argument("--arms", default="all", help=f"all, or a comma list of {', '.join(ARM_NAMES)}")
    ap.add_argument("--sets", default="all", help="all (golden and heldout, those the profile's pack names, then each further set "
                                                  "the estate holds), or a comma list of them and of further sets named <name>_v<n>")
    ap.add_argument("--repeats", type=int, default=3, help="how many times each arm runs each set")
    ap.add_argument("--as", dest="principal", help="the querying identity to ask as (default: the one the profile's deployment "
                                                   "names, asker in its identities)")
    ap.add_argument("--items", help="only these question ids, comma separated")
    ap.add_argument("--out", help="the folder runs land in (default: runs/ in the profile's run state)")
    ap.add_argument("--baseline", help="a baseline the report puts this run beside (the client's, as make ab passes the "
                                       "Makefile's BASELINE)")
    args = ap.parse_args(argv)
    if args.repeats < 1:
        ap.error("--repeats must be 1 or more")
    load_env()
    try:
        prof = resolve(args.profile)
        principal = prof.asking(args.principal)
    except ProfileError as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2
    why = unknown_principal(prof, principal)
    if why:
        print(f"harness: {why}", file=sys.stderr)
        return 2
    build = prof.estate()
    names, sets = choose_arms(args.arms), choose_sets(args.sets, build, setfiles.declared(prof.paths()["pack"]))
    lacking = [f"{s}.json" for s in sets if not (build / f"{s}.json").is_file()]
    if lacking:
        print(f"harness: no {', '.join(lacking)} in {build}: make golden writes the question sets from the warehouses on disk "
              "(make estate, if it has none)", file=sys.stderr)
        return 2
    items = [i.strip() for i in args.items.split(",") if i.strip()] if args.items else None
    try:
        eval_concurrency()
    except ValueError as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2
    try:
        eng = Engine.serving(prof)
    except GraphLocked as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2
    try:
        run_dir = run(prof, Arms(eng), names, sets, args.repeats, principal, items, Path(args.out) if args.out else None,
                      baseline=Path(args.baseline) if args.baseline else None)
    except (LandingError, ModelSettingError, Unanswered) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2
    finally:
        eng.close()
    summary = json.loads((run_dir / "summary.json").read_text())["sets"]
    for s, by_arm in summary.items():
        print(f"\n{report.SETS.get(s, s)}, passed (mean of {args.repeats})\narm                    within   cross    shelf    abstain  all")
        for arm, a in by_arm.items():
            cells = [a["kinds"].get(k, {}).get("passed") for k in ("within", "cross", "shelf", "abstain")] + [a["all"]["passed"]]
            print(f"{report.label(arm, a['modes']):<22} " + " ".join(f"{(c['mean'] if c else 0):>7.1f} " for c in cells))
    manifest, records, _ = report.load(run_dir)
    print("\n" + "\n".join(report.category_lines(manifest, records)))
    if args.baseline:
        print(f"\nbaseline: {args.baseline}, beside this run in the report")
    print(f"\nreport: {run_dir / 'report.md'}\n        {run_dir / 'report.html'}\nrecords: {run_dir / 'records.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
