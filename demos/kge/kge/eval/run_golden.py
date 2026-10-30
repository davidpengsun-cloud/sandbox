"""Run the golden set on every preset arm and score each kind apart.

    python3 -m kge.eval.run_golden [--profile <id>] [--arm <preset>|all]
    python3 -m kge.eval.run_golden [--profile <id>] --api http://127.0.0.1:8000   # while the API holds the graph

The arms are the run-config presets (PRESETS in kge/services/ask/engine/config.py, or GET /presets
from the API): schema, catalog and graph. The graph, the sets and the tokens
are the profile's (kge/pipeline/profile.py: --profile, else PROFILE, else the
registry's default), and the report, eval.json, goes in its run state.

Scoring, per kind:
  within   figure equals the agreed figure; join path equals the agreed path
  cross    behaviour matches; commensurability verdict matches; every agreed
           refused join is refused (only the graph arm can refuse)
  shelf    every agreed citation is cited; nothing on the must-not list is
  abstain  the engine abstained or refused rather than answering

and, on any kind, what an item states beyond those: a grouped figure's rows,
each group's value, scored like parts; a ratio beside the parts; and, for a
refusal, no partial figure (no figure, parts or rows). An item that names its
principal is asked as that principal (asked_as). The held-out paraphrases,
where the estate holds them, are scored the same way and reported apart, never
as part of the gate.

The questions are asked through kge/eval/asking.py each, EVAL_CONCURRENCY at a time, the scores in the set's order; with
--api, API_IN_FLIGHT at most, the requests the API lets one person have in flight (kge/services/http.py MAX_IN_FLIGHT).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from kge.services.ask.engine.core import PRESETS, Engine
from kge.services.identity.service import token_for
from kge.eval import sets
from kge.eval.asking import Unanswered, each
from kge.pipeline.settings import eval_concurrency
from kge.pipeline.profile import ProfileError, resolve
from kge.adapters.graphstore import GraphLocked


def api_asker(base: str, token: str):
    def ask(question: str, arm: str) -> dict:
        req = urllib.request.Request(f"{base.rstrip('/')}/answer", data=json.dumps({"question": question, "arm": arm}).encode(),
                                     headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    return ask


def api_presets(base: str) -> list[str]:
    with urllib.request.urlopen(f"{base.rstrip('/')}/presets", timeout=60) as r:
        return list(json.loads(r.read())["presets"])


def choose_arms(choice: str, presets) -> list[str]:
    """Every preset for all, else the one named; an unknown name stops the run."""
    if choice == "all":
        return list(presets)
    if choice not in presets:
        raise SystemExit(f"run_golden: no preset {choice!r}; the presets are {', '.join(presets)}")
    return [choice]


def close(a, b, tol=0.01) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


RATIO = 0.0001
API_IN_FLIGHT = 4


def same(got, want: dict) -> bool:
    """Every key of want, and no other, in got, each value within the tolerance: an answer's parts, or a figure's rows."""
    got = got if isinstance(got, dict) else {}
    return set(got) == set(want) and all(close(got[k], v) for k, v in want.items())


def partial(ans: dict) -> list[str]:
    """What of a figure an answer gave: a figure, parts or rows. A plan's parts (a list) are no figure."""
    return [k for k in ("figure", "parts", "rows") if (ans.get(k) is not None if k == "figure" else isinstance(ans.get(k), dict) and ans[k])]


def asked_as(item: dict, principal: str) -> str:
    """Who an item is asked as: its own principal, where it names one (a join-time access refusal), else the run's."""
    return item.get("principal") or principal


def uncertified_ids(build: Path) -> set[str]:
    return {d["doc_id"] for d in json.loads((build / "documents.json").read_text()) if d["shelf"] != "certified"}


def score(item: dict, ans: dict, uncertified: set[str] | frozenset = frozenset()) -> tuple[bool, str]:
    """Strict on what the golden set states: a figure's unit and measure column as
    well as its value and path; a cross answer's parts as well as its verdict; no
    uncertified document among the citations; the exact abstaining behaviour. Then,
    on any kind, what else the item states: a ratio, or that a refusal gives no
    partial figure."""
    ok, why = by_kind(item, ans, uncertified)
    exp = item["expect"]
    if ok and "ratio" in exp and not close(ans.get("ratio"), exp["ratio"], RATIO):
        return False, f"{why} ratio {ans.get('ratio')} != {exp['ratio']}".strip()
    if ok and exp.get("no_figure") and partial(ans):
        return False, f"{why} a partial figure given: {', '.join(partial(ans))}".strip()
    return ok, why


def by_kind(item: dict, ans: dict, uncertified: set[str] | frozenset) -> tuple[bool, str]:
    kind, exp = item["kind"], item["expect"]
    if kind == "within":
        if ans.get("behaviour") != "figure":
            return False, f"{ans.get('behaviour')}: {ans.get('reason', '')[:80]}"
        if "rows" in exp:
            fig_ok = same(ans.get("rows"), exp["rows"])
            fig_why = f"rows {ans.get('rows')} != {exp['rows']} "
        else:
            fig_ok = close(ans.get("figure"), exp["figure"])
            fig_why = f"figure {ans.get('figure')} != {exp['figure']} "
        path_ok = "join_path" not in exp or \
            [p.replace(" -> ", "->") for p in ans.get("join_path", [])] == [p.replace(" -> ", "->") for p in exp["join_path"]]
        unit_ok = ("unit" not in exp) or ans.get("unit") == exp["unit"]
        col_ok = not exp.get("columns") or ans.get("column") == exp["columns"][0]
        why = ("" if fig_ok else fig_why) + ("" if path_ok else f"path {ans.get('join_path')} ") + \
              ("" if unit_ok else f"unit {ans.get('unit')} ") + ("" if col_ok else f"column {ans.get('column')} != {exp['columns'][0]}")
        return fig_ok and path_ok and unit_ok and col_ok, why.strip()
    if kind == "cross":
        b_ok = ans.get("behaviour") == exp["behaviour"]
        v_ok = ("commensurable" not in exp) or (ans.get("commensurable") == exp["commensurable"])
        r_ok = set(exp.get("joins_refused", [])) <= set(ans.get("joins_refused", []))
        f_ok = ("figure" not in exp) or close(ans.get("figure"), exp["figure"])
        got = ans.get("parts") if isinstance(ans.get("parts"), dict) else {}
        p_ok = ("parts" not in exp) or same(got, exp["parts"])
        w_ok = ("rows" not in exp) or same(ans.get("rows"), exp["rows"])
        return b_ok and v_ok and r_ok and f_ok and p_ok and w_ok, f"behaviour={ans.get('behaviour')} verdict={ans.get('commensurable')} refused={ans.get('joins_refused')}" + \
            ("" if p_ok else f" parts={got}") + ("" if w_ok else f" rows={ans.get('rows')}")
    if kind == "shelf":
        cited = set(ans.get("cite", []))
        b_ok = ("behaviour" not in exp) or ans.get("behaviour") == exp["behaviour"]
        c_ok = set(exp["cite"]) <= cited
        n_ok = not (set(exp.get("must_not_cite", [])) & cited)
        u_ok = not (cited & set(uncertified))
        return b_ok and c_ok and n_ok and u_ok, f"behaviour={ans.get('behaviour')} cited={sorted(cited)}"
    if kind == "abstain":
        want = exp.get("behaviour")
        ok = ans.get("behaviour") == want if want else ans.get("behaviour") in ("abstain", "refuse", "abstain_route_steward")
        return ok, f"behaviour={ans.get('behaviour')} expected={want}"
    return False, "unknown kind"


def querying_identities(prof) -> list[str] | None:
    """The profile's querying identities, from the access mirror its graph load wrote (principals.json, in its run
    state); None before a graph is loaded, when the engine says what is missing."""
    path = prof.run_dir() / "principals.json"
    if not path.is_file():
        return None
    return [p["principal"] for p in json.loads(path.read_text()) if "querying" in (p.get("processes") or [])]


def unknown_principal(prof, principal: str) -> str | None:
    """Why principal cannot be asked as on the profile, or None: one that is not a querying identity of its own is refused
    every question, and would score every arm nought."""
    who = querying_identities(prof)
    if who is None or principal in who:
        return None
    return (f"{principal} is not a querying identity of profile {prof.id}: its querying identities are {', '.join(who)} "
            f"(PRINCIPAL=<one of them>)")


def main() -> int:
    """make eval; a question BigQuery or Vertex AI turned away on every try stops it with one line and exit 2."""
    try:
        return evaluate()
    except Unanswered as exc:
        print(f"run_golden: cannot check: {exc}", file=sys.stderr)
        return 2


def evaluate() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile whose graph and sets to run (default: PROFILE, else the registry's default)")
    ap.add_argument("--arm", default="all", help="a preset arm, or all")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--api", help="ask the running API instead of opening the graph (the graph store allows one process at a time)")
    ap.add_argument("--as", dest="principal", help="the querying identity to ask as (default: the one the profile's deployment "
                                                   "names, asker in its identities)")
    args = ap.parse_args()
    try:
        prof = resolve(args.profile).serve()
        principal = prof.asking(args.principal)
    except ProfileError as exc:
        print(f"run_golden: {exc}", file=sys.stderr)
        return 2
    why = unknown_principal(prof, principal)
    if why:
        print(f"run_golden: {why}", file=sys.stderr)
        return 2
    build = prof.estate()
    golden = sets.items(build / "golden.json")
    try:
        workers = eval_concurrency()
    except ValueError as exc:
        print(f"run_golden: {exc}", file=sys.stderr)
        return 2
    if args.api:
        ask = api_asker(args.api, token_for(prof.run_dir(), principal))
        arms = choose_arms(args.arm, api_presets(args.api))
        workers = min(workers, API_IN_FLIGHT)
    else:
        arms = choose_arms(args.arm, PRESETS)
        try:
            eng = Engine.serving(prof)
        except GraphLocked as exc:
            print(f"run_golden: {exc}", file=sys.stderr)
            return 2

        def ask(question: str, arm: str) -> dict:
            return eng.answer(question, principal=principal, arm_name=arm)
    unc = uncertified_ids(build)
    report = {}
    for arm in arms:
        by_kind: dict[str, list] = {}
        scored = each(lambda item: score(item, ask(item["question"], arm), unc), golden, lambda item: f"{item['id']} on {arm}",
                      workers)
        for item, (ok, why) in zip(golden, scored):
            by_kind.setdefault(item["kind"], []).append((item["id"], ok, why, item["question"]))
            if args.verbose and not ok:
                print(f"[{arm}] {item['id']} {item['kind']:8} FAIL {item['question']}\n        {why}")
        report[arm] = {k: {"passed": sum(1 for _, ok, _, _ in v if ok), "of": len(v)} for k, v in by_kind.items()}
    heldout_path = build / "heldout.json"
    heldout_report = {}
    if heldout_path.exists():
        heldout = sets.items(heldout_path)
        for arm in arms:
            by_kind = {}
            scored = each(lambda item: score(item, ask(item["question"], arm), unc), heldout,
                          lambda item: f"{item['id']} on {arm}, held out", workers)
            for item, (ok, why) in zip(heldout, scored):
                by_kind.setdefault(item["kind"], []).append(ok)
                if args.verbose and not ok:
                    print(f"[{arm} held-out] {item['id']} {item['kind']:8} FAIL {item['question']}\n        {why}")
            heldout_report[arm] = {k: {"passed": sum(v), "of": len(v)} for k, v in by_kind.items()}
    prof.run_dir().mkdir(parents=True, exist_ok=True)
    (prof.run_dir() / "eval.json").write_text(json.dumps({**report, "heldout": heldout_report}, indent=2))
    print_table(f"gated golden set, asked as {principal}", report)
    if heldout_report:
        print_table("held-out paraphrases (reported apart, not gated)", heldout_report)
    return 0


def print_table(title: str, report: dict) -> None:
    print(f"\n{title}\narm       within     cross      shelf      abstain")
    for arm, r in report.items():
        print(f"{arm:8}  " + "  ".join(f"{r.get(k, {}).get('passed', 0):>3}/{r.get(k, {}).get('of', 0):<5}" for k in ["within", "cross", "shelf", "abstain"]))


if __name__ == "__main__":
    raise SystemExit(main())
