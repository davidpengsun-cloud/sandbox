"""The post-load check: does the loaded graph meet its pack's gates, and does it still add what it did at the baseline?

    python3 -m kge.eval.margin [--profile <id>] [--baseline <the client's baseline>] [--repeats 3] [--sets all] [--hash <model_hash>]
    make margin                      # after make graph and make pipeline, beside the Makefile's BASELINE
    make margin HASH=<model_hash>    # a candidate make load loaded, before make release serves it

It is the onboarding's step 7 (issue #40): one verdict on the graph, from one
run of the reference agent's control (graph off) and treatment (graph on).
Against the thresholds the artifact the graph was loaded from holds, gates.json,
the pack's manifest.gates as compiled (so a candidate is held to its own pack's,
never the working pack's and never code's):

  V01  graph on's pass rate on the golden set, per kind, is at least
       golden_min_pass[kind]
  V02  graph on's pass rate less graph off's on the golden set, per kind, is at
       least control_margin[kind]
  V03  the held-out set's passes per kind, graph on and off: reported, never a
       failure
  V04  no graph-on answer, on any set, held-out included, cites a document the
       tier rule may not cite (kge/eval/metrics.py citations: not landed AI-ready,
       gold or silver; an uncertified document never is), each named with its
       question

and, where the profile names a baseline, the no-fall check, below, as #38 built
it. With no baseline (or --baseline '', none given) the gates are checked alone,
and it says the no-fall check was skipped; with neither gates nor a baseline
there is nothing to check against (2). It asks as the baseline's principal, else
the deployment's asker. Its verdict, passed or not, with its run and the hash,
is the GATE marker in the graph's folder, beside LOADED, for the store it ran on
(kge/graph/release.py); a load that starts again on the hash takes it out. A check
that cannot check (2) writes none.

The margin is the A/B's own measure of what the graph adds: the reference
agent's treatment (graph on) less its control (graph off) on each score
(kge/eval/report.py, MARGIN): the golden scorer's pass rate, figure correct,
behaviour class, gate first try, tier-correct citations and unsupported numbers,
per set and kind and overall. After a graph load, this runs the two arms through
the harness (kge/eval/harness.py) offline on rules, as the baseline was recorded,
whatever model is configured, and asked as the baseline's principal, on the
profile's graph and sets (kge/pipeline/profile.py: --profile, else PROFILE, else the
registry's default): the graph the client's current pointer names, or with
--hash the graph of that artifact, a candidate make load has loaded and make
release has not served yet, which is checked beside the same baseline while the
API serves the other. The run and its report, with the margin beside the
baseline's, land in the profile's runs (Profile.run_dir) like any other. The
baseline is the client's, a file of the client's own: platform code names none,
so the check takes the one it is given (make margin gives it the Makefile's
BASELINE), else the one the profile points at; given none, and no gates, it says
so and cannot check. A baseline recorded on another profile is another client's
graph and data, and the check will not put this one beside it.

The no-fall check fails, exit 1, naming each set, kind and score whose margin
fell by more than its spread across repeats: the margin's highest less its
lowest across the repeats, in this run or the baseline's, whichever is wider. A
margin the baseline measured that is n/a now (no longer measured: no citation
left to judge, say) fails too. No threshold is set by hand: the tolerance is the
runs' own variance, and on rules the repeats agree, so any fall fails. The pass
rate's margin catches a question that now fails for a reason no other score
sees, such as a certified definition no longer AI-ready. A set is compared only
if it is the one the baseline ran (its version, items and sha256), or if it
declares what it changed since that very file (changes_from, kge/eval/sets.py:
golden v2, since v1): then its other items are put beside the baseline's passes
per question, and the margin of the pass rate on them is checked, per kind and
overall, the one score the baseline keeps per question. A set that has changed
otherwise is put beside the baseline by its counts alone and its margin is not
checked; a set the baseline did not run is run and reported, new, with nothing
to check it against. It cannot check, exit 2, given neither a baseline nor
gates, or a baseline that it cannot read, or that records no margin on rules, or
that is another profile's or was recorded on another graph store (the baseline's graph.store.engine; record one on
this setup), with gates and a run that leaves out the golden set
they are on, on a profile with no estate or no graph (it says which make target
to run, and never opens a graph that is not there), with a set file that is not
the version the code runs (a golden.json written before golden v2: make golden),
on a graph the pipeline has not filled, while the API holds the graph, or when
no set is the baseline's. Any other error is a 2 as well, named in one line with
where it arose, never a traceback: a check that broke has not found a margin
that fell.

Opens the graph: stop the API first (the graph store lets one process hold it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path
from typing import NamedTuple

from kge.pipeline.settings import ROOT
from kge.services.ask.engine.core import Engine
from kge.eval import harness, report, sets as setfiles
from kge.graph.release import mark_gate
from kge.pipeline.envfile import load_env
from kge.pipeline.profile import ProfileError, resolve
from kge.adapters.graphstore import GraphLocked, store_info
from kge.adapters.warehouse import estate_of

DEMO = ROOT
RECORD = "a new baseline is recorded on rules with make ab, then make baseline RUN=<run_id>"
GATES = "gates.json"
GOLDEN, HELDOUT = "golden", "heldout"
CONTROL, TREATMENT = report.MARGIN_ARMS


NEEDS = ("baseline", "run_id", "principal", "repeats", "sets", "passed", "items")
SHAPE = {"baseline": str, "run_id": str, "principal": str, "repeats": int, "arms": list,
         **dict.fromkeys(("sets", "passed", "items", "margin", "measures", "graph", "estate"), dict)}
WORDS = {str: "a string", int: "a whole number", list: "a list", dict: "an object"}


def unusable(b) -> str | None:
    """Why this baseline cannot be checked against; None if it can. Its top level is checked here, before any run;
    anything deeper the check cannot read is a 2 all the same (main)."""
    if not isinstance(b, dict):
        return f"it is not a baseline, which is a JSON object; {RECORD}"
    lacks = [k for k in NEEDS if b.get(k) in (None, "")]
    if lacks:
        return f"it is not a baseline: it has no {', '.join(lacks)}; {RECORD}"
    wrong = [f"{k} is not {WORDS[t]}" for k, t in SHAPE.items() if b.get(k) is not None and not isinstance(b[k], t)]
    if not wrong and not all(isinstance(a, dict) and isinstance(a.get("arm"), str) for a in b.get("arms") or []):
        wrong = ["arms holds an entry that is not an object naming its arm"]
    if wrong:
        return f"it is not a baseline: {', '.join(wrong)}; {RECORD}"
    if not b.get("margin"):
        return f"the baseline records no margin; {RECORD}"
    modes = {a["arm"]: a.get("mode") for a in b.get("arms") or []}
    off = [f"{arm} on {modes.get(arm) or 'nothing'}" for arm in report.MARGIN_ARMS if modes.get(arm) != "rules"]
    if off:
        return (f"the baseline's agent arms did not both run on rules ({', '.join(off)}); the check runs on rules and "
                f"compares like with like; {RECORD}")
    return None


def missing(profile, sets: list[str]) -> str | None:
    """What the check needs of the profile and it lacks, and what makes it; None if nothing. Checked before the graph is
    opened: opening a graph that is not there makes an empty one, which make margin would then take for a loaded graph.
    Which warehouses the estate must hold is the graph's to say (its units, the client's); the engine names any it lacks
    when it opens them. An estate with its warehouses and not a set file, one made before the set was, needs only make
    golden."""
    estate = profile.estate()
    held = [] if estate_of(profile).present() else [f"{profile.warehouses.name}/"]
    files = [f"{s}.json" for s in sets if not (estate / f"{s}.json").is_file()]
    if held:
        return f"no estate in {estate} ({', '.join(held + files)}): make estate, then make graph and make pipeline"
    if files:
        return f"no {', '.join(files)} in {estate}: make golden writes the question sets from the warehouses on disk"
    try:
        graph = profile.graph_path()
    except ProfileError as exc:
        return str(exc)
    if not graph.exists():
        if profile.pinned is not None:
            return f"no graph {graph}: make load HASH={profile.pinned} loads it"
        return f"no graph {graph}: make graph, then make pipeline"
    return None


class CannotCheck(Exception):
    """Something the check needs that it cannot read, and why: a 2, never the 1 of a check that failed."""


class Check(NamedTuple):
    """One post-load check's result: its rule (V01 to V04), the kind of question it is on (None where it is not per kind),
    whether it passed (None: reported, or not measured, never gated), its line and the set it checks (None: all sets)."""
    rule: str
    kind: str | None
    ok: bool | None
    line: str
    set: str | None = None


def thresholds(prof) -> dict | None:
    """The post-load gates of the artifact whose graph prof names: its gates.json, the pack's manifest.gates as compiled.
    None if it holds none (a pack that declares none, an artifact compiled before there were any, or nothing released).
    CannotCheck if its manifest lists them and the file is not the one it hashed."""
    h = prof.model_hash()
    if h is None:
        return None
    folder = prof.artifacts / prof.client / h
    try:
        listed = json.loads((folder / "manifest.json").read_text()).get("files") or {}
    except (OSError, ValueError, AttributeError):
        return None
    if GATES not in listed:
        return None
    try:
        data = (folder / GATES).read_bytes()
    except OSError:
        raise CannotCheck(f"the artifact {folder} lists {GATES} and does not hold it: make compile writes it again") from None
    if hashlib.sha256(data).hexdigest() != listed[GATES]:
        raise CannotCheck(f"{folder / GATES} is not the file its manifest hashed: make compile writes it again")
    return json.loads(data)


def drafted(prof) -> list[dict] | None:
    """The artifact's review metadata, or None for artifacts made before onboarding drafts existed."""
    h = prof.model_hash()
    if h is None:
        return None
    path = prof.artifacts / prof.client / h / "drafts.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CannotCheck(f"cannot read {path}: {exc}") from None
    if not isinstance(value, dict) or value.get("format") != 1 or not isinstance(value.get("items"), list):
        raise CannotCheck(f"{path} is not drafts format 1")
    return value["items"]


def percent(x: float) -> str:
    return f"{report.num(round(x * 100, 1))}%"


def v05(draft_items: list[dict], records: list[dict], manifest: dict, gates: dict | None = None) -> tuple[list[dict], list[Check]]:
    """V05(a) unfinished review items; then (b) question coverage and (c) graph-on floor for each drafted unit."""
    waiting = [x for x in draft_items if x.get("state") != "certified"]
    if waiting:
        return waiting, []
    found = []
    units = sorted({x["unit"] for x in draft_items if x.get("kind") == "term"})
    for unit in units:
        terms = {x["id"] for x in draft_items if x.get("unit") == unit and x.get("kind") == "term"}
        questions = [x for x in draft_items if x.get("unit") == unit and x.get("kind") == "question"]
        set_name = f"golden_{unit}_v1"
        covered = {term for q in questions if q.get("set") == f"golden_{unit}" for term in q.get("terms", [])}
        ok = set_name in manifest.get("sets", {}) and terms <= covered
        found.append(Check("V05", None, ok, f"V05 {unit}: {set_name} has at least one question per drafted term: "
                           + ("passed" if ok else "failed"), set_name))
        by_kind = sorted({r["kind"] for r in records if r["set"] == set_name and r["arm"] == TREATMENT})
        floor_ok, details = bool(by_kind), []
        for kind in by_kind:
            rows = [r for r in records if r["set"] == set_name and r["arm"] == TREATMENT and r["kind"] == kind]
            rate = sum(bool(r["ok"]) for r in rows) / len(rows)
            least = max(0.8, (gates or {}).get("golden_min_pass", {}).get(kind, 0))
            floor_ok &= rate >= least - report.EPS
            details.append(f"{kind} {percent(rate)} (at least {percent(least)})")
        found.append(Check("V05", None, floor_ok, f"V05 {set_name}: graph on {'; '.join(details) or 'ran no questions'}: "
                           + ("passed" if floor_ok else "failed"), set_name))
    return [], found


def v05_proposal(prof) -> list[Check]:
    """V05(v) for a candidate hash a proposal names: fails while any proposal item lacks accepted_by or certified_by."""
    h = prof.model_hash()
    if h is None:
        return []
    import yaml
    from kge.compiler import pack as packs
    from kge import packstore

    store = packstore.for_profile(prof)
    prop_names = [n for n in store.list("proposals") if n.endswith("/proposal.yaml")]
    local_root = prof.clients / prof.client / "proposals"
    if not prop_names and local_root.is_dir():
        prop_names = [f"proposals/{p.parent.name}/proposal.yaml" for p in sorted(local_root.glob("*/proposal.yaml"))]
    if not prop_names:
        return []

    p = packs.read(prof.clients, prof.client, lock=False, identities=prof.identities)
    roles = p.principal_roles or {}

    out: list[Check] = []
    for name in sorted(prop_names):
        try:
            raw = store.read(name) if store.exists(name) else (prof.clients / prof.client / name).read_bytes()
            prop = yaml.safe_load(raw)
        except Exception:
            continue
        if not isinstance(prop, dict) or prop.get("candidate_hash") != h:
            continue
        pid = prop.get("id") or name.split("/")[1]
        items = prop.get("items") or []
        waiting: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            accepted, certified = item.get("accepted_by"), item.get("certified_by")
            if accepted and roles and not packs.accepts_drafts(roles.get(accepted, {})):
                accepted = None
            if certified and roles and "certification" not in roles.get(certified, {}).get("processes", []):
                certified = None
            if certified and (not accepted or certified == accepted):
                certified = None
            state = "certified" if certified else "accepted" if accepted else "drafted"
            if state != "certified":
                waiting.append(f"{item.get('id')} ({state})")
        if waiting:
            out.append(Check("V05", None, False, f"V05 proposal {pid}: {', '.join(waiting)} not certified: failed"))
        elif items:
            out.append(Check("V05", None, True, f"V05 proposal {pid}: {len(items)} item(s) accepted and certified: passed"))
    return out


def post_load(gates: dict | None, records: list[dict], manifest: dict) -> list[Check]:
    """V01 and V02 on the golden set, per kind the gates name (none without gates), then V03 and V04: each a Check."""
    gates, out = gates or {}, []
    summary, margins = report.summarise(records), report.margins(records)
    described = manifest.get("sets") or {}

    def named(s: str) -> str:
        v = (described.get(s) or {}).get("version")
        return f"{s} {v}" if v else s

    def ids(arm: str, kind: str, ok: bool) -> list[str]:
        return list(dict.fromkeys(r["id"] for r in records if (r["set"], r["arm"], r["kind"]) == (GOLDEN, arm, kind) and bool(r["ok"]) == ok))

    on = (summary.get(GOLDEN) or {}).get(TREATMENT) or {"kinds": {}}
    for kind in [k for k in report.KINDS if k in (gates.get("golden_min_pass") or {})]:
        least, g = gates["golden_min_pass"][kind], on["kinds"].get(kind)
        head = f"V01 {named(GOLDEN)}, {report.KINDS[kind]}"
        if g is None:
            out.append(Check("V01", kind, None, f"{head}: no item of this kind, not measured", GOLDEN))
            continue
        rate = g["passed"]["mean"] / g["of"]
        ok = rate >= least - report.EPS
        line = f"{head}: graph on passed {report.num(g['passed']['mean'])} of {g['of']} ({percent(rate)}), at least {percent(least)}: "
        out.append(Check("V01", kind, ok, line + ("passed" if ok else f"failed, graph on failed {', '.join(ids(TREATMENT, kind, False))}"), GOLDEN))
    for kind in [k for k in report.KINDS if k in (gates.get("control_margin") or {})]:
        least, m = gates["control_margin"][kind], ((margins.get(GOLDEN) or {}).get(kind) or {}).get("pass_rate")
        head = f"V02 {named(GOLDEN)}, {report.KINDS[kind]}"
        if m is None:
            out.append(Check("V02", kind, None, f"{head}: no item of this kind, not measured", GOLDEN))
            continue
        ok = m["mean"] >= least - report.EPS
        line = f"{head}: graph on less graph off, {report.gap(m['mean'], 'rate')}, at least {report.gap(least, 'rate')}: "
        why = [f"graph on failed {', '.join(x)}" for x in [ids(TREATMENT, kind, False)] if x] + \
              [f"graph off passed {', '.join(x)}" for x in [ids(CONTROL, kind, True)] if x]
        out.append(Check("V02", kind, ok, line + ("passed" if ok else ", ".join(["failed", *(["; ".join(why)] if why else [])])), GOLDEN))
    held_sets = [s for s in summary if setfiles.is_heldout(s)]
    if held_sets:
        def cells(a: dict) -> str:
            return ", ".join(f"{report.KINDS.get(k, k)} {report.num(g['passed']['mean'])} of {g['of']}" for k, g in a["kinds"].items()
                             if k != "all")
        for hs in held_sets:
            held = summary[hs]
            arms = [f"{label} {cells(held[arm])}" for arm, label in ((TREATMENT, "graph on passed"), (CONTROL, "graph off")) if arm in held]
            out.append(Check("V03", None, None, f"V03 {named(hs)}, held-out: {'; '.join(arms)}: reported, never gated", hs))
    else:
        out.append(Check("V03", None, None, "V03 no held-out set ran: nothing to report"))
    cited: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for r in records:
        off = ((r.get("measures") or {}).get("citations") or {}).get("off_tier") or []
        if r["arm"] == TREATMENT and off:
            docs = cited.setdefault((r["set"], r["id"]), (r.get("question") or "", []))[1]
            docs.extend(d for d in off if d not in docs)
    if not cited:
        out.append(Check("V04", None, True, "V04 every set, held-out included: no graph-on answer cites an uncertified or off-tier "
                                            "document: passed"))
    for (s, qid), (question, docs) in cited.items():
        out.append(Check("V04", None, False, f"V04 {s} {qid}, {question}: graph on cites {report.words(docs)}, which certification "
                                             "did not land AI-ready gold or silver: failed", s))
    return out


def chunks(eng: Engine) -> int:
    return int(eng.g.q("MATCH (c:Chunk) RETURN count(c) AS n")[0]["n"])


def counts(summary: dict, b: dict, s: str) -> list[str]:
    """A changed set's counts: each agent arm's passes per kind, at the baseline and now."""
    out = []
    for arm in report.MARGIN_ARMS:
        now, then = (summary.get(s) or {}).get(arm), (b.get("passed") or {}).get(s, {}).get(arm) or {}
        if now:
            cells = [f"{k} {report.num(then[k]) if k in then else '-'} → {report.num(g['passed']['mean'])}" for k, g in now["kinds"].items()]
            out.append(f"  {arm:<10} " + ", ".join(cells))
    return out


def set_outcomes(manifest: dict, gaps: dict, checks: list[Check]) -> dict[str, str]:
    outcomes = {name: "passed" for name in manifest.get("sets", {})}
    for row in gaps.get("fell", []):
        if row.get("set") in outcomes:
            outcomes[row["set"]] = "fell"
    for check in checks:
        if check.ok is not False:
            continue
        if check.set in outcomes:
            outcomes[check.set] = "fell"
        elif check.rule in ("V01", "V02") and GOLDEN in outcomes:
            outcomes[GOLDEN] = "fell"
        elif check.rule in ("V04", "V05"):
            for name in outcomes:
                outcomes[name] = "fell"
    return outcomes


def main(argv: list[str] | None = None) -> int:
    """The check, with any error it did not foresee a 2, named with where it arose and no traceback: a check that broke
    has not found a margin that fell, and a CI step must not read it as one. argparse's own exits stand (--help, 0)."""
    try:
        return check(argv)
    except Exception as exc:
        tb, at = traceback.extract_tb(exc.__traceback__), ""
        if tb:
            f = Path(tb[-1].filename)
            at = f" (at {f.relative_to(DEMO) if f.is_relative_to(DEMO) else f}:{tb[-1].lineno})"
        print(f"margin: cannot check: {type(exc).__name__}: {exc}{at}; if the baseline is at fault, {RECORD}", file=sys.stderr)
        return 2


def check(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile whose graph to check (default: PROFILE, else the registry's default)")
    ap.add_argument("--baseline", help="the client's baseline to check against (default: the profile's; '' for none; make margin "
                                       "passes the Makefile's BASELINE)")
    ap.add_argument("--repeats", type=int, default=3, help="how many times each arm runs each set")
    ap.add_argument("--sets", default="all", help="all (golden, heldout and each further set the estate holds), or a comma list of them")
    ap.add_argument("--out", help="the folder the run lands in (default: runs/ in the profile's run state)")
    ap.add_argument("--hash", help="the graph of this artifact's model hash, a candidate make load loaded (default: the one the "
                                   "client's current pointer names)")
    args = ap.parse_args(argv)
    if args.repeats < 1:
        ap.error("--repeats must be 1 or more")
    load_env()
    try:
        prof = resolve(args.profile)
        if args.hash:
            prof = prof.at(args.hash)
    except ProfileError as exc:
        print(f"margin: {exc}", file=sys.stderr)
        return 2
    try:
        gates = thresholds(prof.pin())
        draft_items = drafted(prof.pin())
    except CannotCheck as exc:
        print(f"margin: cannot check: {exc}", file=sys.stderr)
        return 2
    if draft_items is not None:
        waiting, _ = v05(draft_items, [], {}, gates)
        if waiting:
            words = ", ".join(f"{x.get('id')} ({x.get('state')})" for x in waiting)
            line = f"V05 drafted items: {len(waiting)} not certified: {words}: failed"
            print(f"  {line}")
            graph, model_hash = prof.graph_path(), prof.model_hash()
            mark_gate(graph, {"passed": False, "run_id": None, "model_hash": model_hash, "lines": [line]})
            return 1
    given = args.baseline if args.baseline is not None else prof.baseline
    if not given and gates is None and draft_items is None:
        print(f"margin: no baseline given: --baseline <file>, the client's A/B baseline, as make margin passes the Makefile's "
              f"BASELINE; profile {prof.id} points at none, and the artifact it checks holds no gates (the pack's manifest.gates): "
              "nothing to check against", file=sys.stderr)
        return 2
    build, b, path = prof.estate(), None, None
    if given:
        path = Path(given)
        if not path.is_file():
            print(f"margin: no baseline {path}; {RECORD}", file=sys.stderr)
            return 2
        try:
            b = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"margin: cannot read the baseline {path}: {exc}; {RECORD}", file=sys.stderr)
            return 2
        why = unusable(b)
        if why:
            print(f"margin: cannot check against {path}: {why}", file=sys.stderr)
            return 2
        if b.get("profile") != prof.id:
            whose = f"it is profile {b['profile']}'s baseline" if b.get("profile") else "it records no profile"
            print(f"margin: cannot check against {path}: {whose}, and this process serves profile {prof.id}; each profile is put "
                  "beside its own", file=sys.stderr)
            return 2
        apart = report.store_apart(b, store_info().get("engine"))
        if apart:
            print(f"margin: cannot check against {path}: {apart}; {RECORD}, on this setup", file=sys.stderr)
            return 2
    try:
        principal = b["principal"] if b is not None else prof.asking()
    except ProfileError as exc:
        print(f"margin: {exc}", file=sys.stderr)
        return 2
    try:
        sets = harness.choose_sets(args.sets, build, setfiles.declared(prof.paths()["pack"]))
    except SystemExit as exc:
        print(f"margin: {str(exc).removeprefix('harness: ')}", file=sys.stderr)
        return 2
    if gates and (gates.get("golden_min_pass") or gates.get("control_margin")) and GOLDEN not in sets:
        print(f"margin: cannot check the pack's gates: they are on the golden set, which --sets {args.sets} leaves out; run it "
              "with all the sets, or with golden among them", file=sys.stderr)
        return 2
    why = missing(prof, sets)
    if why:
        print(f"margin: {why}, then make margin", file=sys.stderr)
        return 2
    try:
        eng = Engine.candidate(prof)
    except (GraphLocked, FileNotFoundError) as exc:
        print(f"margin: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        if "does not exist" in str(exc):
            print(f"margin: the graph {prof.graph_path()} was never loaded: make graph, then make pipeline, then make margin",
                  file=sys.stderr)
        elif "no client domain" in str(exc):
            print(f"margin: {exc}, then make pipeline, then make margin", file=sys.stderr)
        else:
            raise
        return 2
    try:
        if not chunks(eng):
            print("margin: the graph holds no chunks, so the treatment has nothing to cite: make pipeline fills it after "
                  "make graph, then make margin", file=sys.stderr)
            return 2
        cur, graph, model_hash = prof.current(), eng.profile.graph_path(), eng.profile.model_hash()
        which = ("the candidate" if cur is None or cur.name != model_hash else "current") + f", the graph of {model_hash}"
        none = "none given (BASELINE=)" if prof.baseline is not None else f"profile {prof.id} names none"
        beside = f"beside {path} ({b['baseline']}, run {b['run_id']})" if b is not None else f"with no baseline: {none}"
        print(f"margin: the agent's control (graph off) and treatment (graph on), on rules, {args.repeats} repeat"
              f"{'s' if args.repeats != 1 else ''} of {', '.join(sets)}, asked as {principal}, on {which}, {beside}")
        run_dir = harness.run(prof, harness.Arms(eng, llm=None), list(report.MARGIN_ARMS), sets, args.repeats, principal,
                              out=Path(args.out) if args.out else None, baseline=path)
    finally:
        eng.close()
    manifest, records, _ = report.load(run_dir)
    found = post_load(gates, records, manifest)
    if draft_items is not None:
        _, draft_checks = v05(draft_items, records, manifest, gates)
        found.extend(draft_checks)
    found.extend(v05_proposal(prof.pin()))
    print()
    if gates:
        print(f"The post-load checks, against the thresholds the artifact holds ({GATES}, the pack's manifest.gates):")
    else:
        print(f"The post-load checks: the artifact holds no gates ({GATES}, the pack's manifest.gates), so V01 and V02 have no "
              "thresholds to check:")
    for c in found:
        print(f"  {c.line}")
    print()
    if b is None:
        why = "no baseline given (BASELINE=)" if prof.baseline is not None else f"profile {prof.id} names no baseline"
        print(f"no-fall check: skipped, {why} to put the margin beside ({RECORD}); the pack's gates are checked alone")
        got = {"compared": {}, "changed_from": {}, "changed": {}, "new": [], "gone": [], "fell": []}
    else:
        got = report.margin_beside(manifest, report.margins(records), b, records)
    summary = report.summarise(records)
    for s in got["compared"]:
        c = got["changed_from"].get(s)
        if c:
            print(f"{s} {manifest['sets'][s]['version']}: changed from the baseline's {c['version']} in {report.words(c['items'])} alone, "
                  f"as its file declares; its {c['unchanged']} other items are put beside the baseline's per-question passes, and "
                  f"the margin of the pass rate on them is checked, per kind and overall ({report.words(c['items'])} "
                  f"{'is' if len(c['items']) == 1 else 'are'} listed apart in the report)")
            continue
        print(f"{s} {manifest['sets'][s]['version']}: the baseline's set; its margin, {len(report.MARGIN)} scores per kind and "
              "overall, is put beside the baseline's")
    for s, reason in got["changed"].items():
        print(f"{s}: not compared, {reason.replace('`', '')}. Its counts, passed at the baseline → now:")
        print("\n".join(counts(summary, b, s)))
    for s in got["new"]:
        print(f"{s}: new since the baseline, with nothing to put beside it")
    for s in got["gone"]:
        print(f"{s}: in the baseline and not run here, so not checked")
    report_md = run_dir / "report.md"
    failed = [c for c in found if c.ok is False]
    if b is not None and not got["fell"] and not failed and not got["compared"]:
        print(f"\nmargin: nothing to check: no set that ran is the one the baseline ran; once the change is accepted, {RECORD}."
              f"\nreport: {report_md}", file=sys.stderr)
        return 2
    passed = not got["fell"] and not failed
    mark_gate(graph, {"passed": passed, "run_id": manifest["run_id"], "model_hash": model_hash,
                      "sets": set_outcomes(manifest, got, found)})
    said = f"GATE: {graph.parent / 'GATE'}: {'passed' if passed else 'failed'}, run {manifest['run_id']}\nreport: {report_md}"
    if got["fell"]:
        print(f"\nFAILED: {report.fell_head(got['fell'])}:")
        for row in got["fell"]:
            print("  " + report.fell_line(row))
        lost = ", or an arm no longer has the score at all" if any(r["now"] is None for r in got["fell"]) else ""
        print("\nOn each score and kind above, the treatment (graph on) adds less over the control (graph off) than it did at "
              f"the baseline{lost}. Look at what this load, or the code that reads it, changed: a certified mapping, a join, a "
              "no-join fact, a definition, or a document's tier or AI-ready flag. Look too for a steward decision the runtime "
              "overlay keeps: the sets expect a graph with none, so once the walkthrough accepts the Digital Engagement Score, "
              "G041 and H014 no longer route it to the steward, through every make graph and make pipeline, until make "
              "reset-overlay sets the decisions aside (then make graph and make pipeline). The report shows which questions "
              f"moved. If the change was meant, {RECORD}.")
    if failed:
        print("\nFAILED: these post-load checks against the pack's gates:")
        for c in failed:
            print(f"  {c.line}")
        print("\nThe graph just loaded does not meet the thresholds its own pack sets: look at what this load changed (a "
              "certified mapping, a join, a no-join fact, a definition, or a document's tier or AI-ready flag); the report shows "
              "each question.")
    if not passed:
        print(said)
        return 1
    if b is not None:
        print(f"\npassed: no margin fell by more than its spread across repeats ({', '.join(got['compared'])})")
    else:
        print()
    checked = [r for r in ("V01", "V02", "V04", "V05") if any(c.rule == r and c.ok for c in found)]
    if gates:
        print(f"passed: every post-load check against the pack's gates ({report.words(checked)}; V03 reported)\n{said}")
    else:
        print(f"passed: {report.words(checked)}, and V03 reported; the artifact holds no gates for V01 and V02\n{said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
