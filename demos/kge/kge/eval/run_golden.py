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
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from kge.adapters import http
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS, Engine
from kge.services.identity.service import token_for
from kge.eval import sets
from kge.eval.asking import Unanswered, each
from kge.pipeline.settings import eval_concurrency, settings
from kge.pipeline.profile import ProfileError, resolve
from kge.ports import QueryRefused, WarehouseSet
from kge.adapters.graphstore import GraphLocked
from kge.adapters.model import ModelSettingError


def api_asker(base: str, token: str, default_runtimes: list[str] | None = None):
    def ask(question: str, arm: str, who: str | None = None, runtimes: list[str] | None = None) -> dict:
        eff_rt = runtimes if runtimes is not None else default_runtimes
        payload: dict = {"question": question, "arm": arm}
        if eff_rt:
            payload["runtimes"] = list(eff_rt)
        req = urllib.request.Request(f"{base.rstrip('/')}/answer", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
        with http.open_http(req, timeout=60) as r:
            return json.loads(r.read())
    return ask


def parse_runtimes(raw: str | list[str] | None, label: str = "eval") -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            return None
        return sets.check_runtimes(label, parts)
    return sets.check_runtimes(label, list(raw))


def api_presets(base: str) -> list[str]:
    with http.open_http(f"{base.rstrip('/')}/presets", timeout=60) as r:
        return list(json.loads(r.read())["presets"])


def choose_arms(choice: str, presets, extra=()) -> list[str]:
    """Every preset for all, else the one(s) named; an unknown name stops the run."""
    if not isinstance(choice, str) or not choice.strip():
        raise SystemExit(f"run_golden: no preset {choice!r}; the presets are {', '.join(presets)}")
    if choice == "all":
        return list(presets)
    parts = [c.strip() for c in choice.split(",") if c.strip()] if "," in choice else [choice]
    if not parts:
        raise SystemExit(f"run_golden: no preset {choice!r}; the presets are {', '.join(presets)}")
    allowed = set(presets) | set(extra)
    for c in parts:
        if c not in allowed:
            raise SystemExit(f"run_golden: no preset {choice!r}; the presets are {', '.join(presets)}")
    return list(dict.fromkeys(parts))


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
    if exp.get("ground_truth_error") and item["kind"] not in ("within", "figure"):
        why = f"ground_truth_refused: {exp['ground_truth_error']}; {why}"
    if ok and "ratio" in exp and not close(ans.get("ratio"), exp["ratio"], RATIO):
        return False, f"{why} ratio {ans.get('ratio')} != {exp['ratio']}".strip()
    if ok and exp.get("no_figure") and partial(ans):
        return False, f"{why} a partial figure given: {', '.join(partial(ans))}".strip()
    return ok, why


def _check_signals(ans: dict, exp: dict) -> tuple[bool, str]:
    blob = " ".join(str(ans.get(k) or "") for k in ("answer_text", "reason", "sql", "column", "unit"))
    blob += " " + " ".join(str(c) for c in (ans.get("cite") or []))
    low = blob.lower()
    missing = [str(s) for s in (exp.get("required_signals") or []) if str(s).lower() not in low]
    forbidden = [str(s) for s in (exp.get("forbidden_signals") or []) if str(s).lower() in low]
    if missing:
        return False, f"missing signals: {', '.join(missing)}"
    if forbidden:
        return False, f"forbidden signals: {', '.join(forbidden)}"
    return True, ""


def _tolerance(exp: dict, figure: object = None) -> float:
    tol = exp.get("tolerance")
    if isinstance(tol, (int, float)) and not isinstance(tol, bool):
        return float(tol)
    if isinstance(tol, dict):
        abs_tol = float(tol.get("abs", 0.01))
        rel_tol = float(tol.get("rel", 0.0))
        try:
            base = abs(float(figure)) if figure is not None else 0.0
        except (TypeError, ValueError):
            base = 0.0
        return max(abs_tol, rel_tol * base)
    return 0.01


def by_kind(item: dict, ans: dict, uncertified: set[str] | frozenset) -> tuple[bool, str]:
    kind, exp = item["kind"], item["expect"]
    tol = _tolerance(exp, exp.get("figure"))
    if exp.get("ground_truth_error") and kind in ("within", "figure"):
        return False, f"ground_truth_refused: {exp['ground_truth_error']}"
    if kind in ("within", "figure"):
        if ans.get("behaviour") != "figure":
            return False, f"{ans.get('behaviour')}: {ans.get('reason', '')[:80]}"
        if "rows" in exp and isinstance(exp["rows"], dict):
            fig_ok = same(ans.get("rows"), exp["rows"])
            fig_why = f"rows {ans.get('rows')} != {exp['rows']} "
        elif "figure" in exp:
            fig_ok = close(ans.get("figure"), exp["figure"], tol)
            fig_why = f"figure {ans.get('figure')} != {exp['figure']} "
        else:
            fig_ok = ans.get("figure") is not None
            fig_why = "figure is None "
        path_ok = "join_path" not in exp or \
            [p.replace(" -> ", "->") for p in ans.get("join_path", [])] == [p.replace(" -> ", "->") for p in exp["join_path"]]
        unit_ok = ("unit" not in exp) or ans.get("unit") == exp["unit"]
        col_ok = not exp.get("columns") or ans.get("column") == exp["columns"][0]
        sig_ok, sig_why = _check_signals(ans, exp)
        why = ("" if fig_ok else fig_why) + ("" if path_ok else f"path {ans.get('join_path')} ") + \
              ("" if unit_ok else f"unit {ans.get('unit')} ") + ("" if col_ok else f"column {ans.get('column')} != {exp['columns'][0]} ") + \
              ("" if sig_ok else sig_why)
        return fig_ok and path_ok and unit_ok and col_ok and sig_ok, why.strip()
    if kind == "cross":
        b_ok = ans.get("behaviour") == exp["behaviour"]
        v_ok = ("commensurable" not in exp) or (ans.get("commensurable") == exp["commensurable"])
        r_ok = set(exp.get("joins_refused", [])) <= set(ans.get("joins_refused", []))
        f_ok = ("figure" not in exp) or close(ans.get("figure"), exp["figure"], tol)
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
        ok = ans.get("behaviour") == want if want else ans.get("behaviour") in ("abstain", "refuse", "abstain_route_steward", "abstain_with_reason")
        reason_ok = not str(item.get("id") or "").startswith("C") or ("reason" not in exp) or (ans.get("reason") == exp["reason"])
        sig_ok, sig_why = _check_signals(ans, exp)
        return ok and reason_ok and sig_ok, (f"behaviour={ans.get('behaviour')} expected={want}" +
                                             ("" if reason_ok else f" reason={ans.get('reason')!r}") +
                                             ("" if sig_ok else f" {sig_why}")).strip()
    if kind == "refusal":
        want = exp.get("behaviour")
        ok = ans.get("behaviour") == want if want else ans.get("behaviour") in ("refuse", "blocked_access", "abstain", "abstain_route_steward")
        sig_ok, sig_why = _check_signals(ans, exp)
        return ok and sig_ok, (f"behaviour={ans.get('behaviour')}" + ("" if sig_ok else f" {sig_why}")).strip()
    if kind in ("list", "point"):
        b_ok = ans.get("behaviour") not in (None, "error")
        f_ok = ("figure" not in exp) or close(ans.get("figure"), exp["figure"], tol)
        sig_ok, sig_why = _check_signals(ans, exp)
        return b_ok and f_ok and sig_ok, (f"behaviour={ans.get('behaviour')}" +
                                          ("" if f_ok else f" figure={ans.get('figure')}!={exp.get('figure')}") +
                                          ("" if sig_ok else f" {sig_why}")).strip()
    return False, "unknown kind"


def _resolve_item_truth_entry(
    sql: str,
    w: WarehouseSet,
    *,
    unit: str | None = None,
    runtimes: list[str] | None = None,
    runtime_id: str | None = None,
    readable_units: set[str] | None = None,
    cache: dict[str, dict] | None = None,
) -> tuple[dict, bool]:
    ver = json.dumps(getattr(w, "versions", None) or {}, sort_keys=True, default=str)
    rt_tag = runtime_id or (",".join(runtimes) if runtimes else "served")
    access = json.dumps(sorted(readable_units) if readable_units is not None else None)
    key = f"{rt_tag}::{ver}::{unit or ''}::{access}::{sql}"
    if cache is not None and key in cache:
        return cache[key], False
    try:
        res = w.resolve_reference_sql(sql, unit=unit, runtimes=runtimes, runtime_id=runtime_id,
                                      readable_units=readable_units)
        rt_info = res.get("runtime") if isinstance(res, dict) else None
        entry = {
            "figure": res.get("figure"),
            "sql": res.get("sql"),
            "runtime_id": (rt_info or {}).get("id") if isinstance(rt_info, dict) else (runtime_id or "reference"),
        }
    except QueryRefused as exc:
        entry = {"error": str(exc), "refused": True, "runtime_id": runtime_id}
    except Exception as exc:
        entry = {"error": str(exc), "refused": False, "runtime_id": runtime_id}
    if cache is not None:
        cache[key] = entry
    return entry, True


def _record_refusal(refused: list[dict] | None, item: dict, entry: dict, runtime: str | None = None) -> None:
    if refused is not None and entry.get("refused"):
        refusal = {"id": item["id"], "runtime": entry.get("runtime_id") or runtime or "reference", "reason": entry["error"]}
        if refusal not in refused:
            refused.append(refusal)


def resolve_suite_truth(
    items: list[dict],
    w: WarehouseSet,
    *,
    default_unit: str | None = None,
    cache_path: Path | None = None,
    runtimes: list[str] | None = None,
    runtime_id: str | None = None,
    readable_units: set[str] | Callable[[dict], set[str]] | None = None,
    refused: list[dict] | None = None,
) -> dict[str, dict]:
    """Execute unique reference_sql queries against the resolved runtime warehouse and cache results."""
    cache: dict[str, dict] = {}
    if cache_path is not None and cache_path.is_file():
        try:
            loaded = json.loads(cache_path.read_text())
            if isinstance(loaded, dict):
                cache = loaded
        except ValueError:
            cache = {}
    updated = False
    for item in items:
        exp = item.setdefault("expect", {})
        sql = item.get("reference_sql") or exp.get("reference_sql")
        if not sql:
            continue
        has_static = ("figure" in exp and not item.get("_resolved_truth")) or ("rows" in exp and isinstance(exp["rows"], dict))
        if has_static and runtime_id is None and runtimes is None and not item.get("runtimes"):
            continue
        unit = item.get("unit") or exp.get("unit") or default_unit
        item_runtimes = runtimes if runtimes is not None else item.get("runtimes")
        readable = readable_units(item) if callable(readable_units) else readable_units
        entry, changed = _resolve_item_truth_entry(
            sql,
            w,
            unit=unit,
            runtimes=item_runtimes,
            runtime_id=runtime_id,
            readable_units=readable,
            cache=cache,
        )
        updated = updated or changed
        _record_refusal(refused, item, entry, item_runtimes[0] if item_runtimes and len(item_runtimes) == 1 else None)
        item["_resolved_truth"] = True
        item["_resolved_runtime_id"] = entry.get("runtime_id")
        item["_resolved_readable_units"] = sorted(readable) if readable is not None else None
        exp.pop("ground_truth_error", None)
        if entry.get("error"):
            exp["ground_truth_error"] = entry["error"]
            exp.pop("figure", None)
        elif entry.get("figure") is not None:
            exp["figure"] = entry["figure"]
    if updated and cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2, default=str))
    return cache


def score_with_runtime_truth(
    item: dict,
    ans: dict,
    uncertified: set[str] | frozenset = frozenset(),
    *,
    w: WarehouseSet | None = None,
    default_unit: str | None = None,
    readable_units: set[str] | None = None,
    cache: dict[str, dict] | None = None,
    refused: list[dict] | None = None,
) -> tuple[bool, str]:
    """Route reference_sql to the same runtime that answered the question before scoring."""
    exp = item.get("expect") or {}
    sql = item.get("reference_sql") or exp.get("reference_sql")
    ans_rt = (ans.get("runtime") or {}).get("id") if isinstance(ans.get("runtime"), dict) else None
    access = sorted(readable_units) if readable_units is not None else None
    if w is not None and sql and ans_rt and (ans_rt != item.get("_resolved_runtime_id")
                                            or access != item.get("_resolved_readable_units")):
        unit = item.get("unit") or exp.get("unit") or default_unit
        entry, _ = _resolve_item_truth_entry(sql, w, unit=unit, runtime_id=ans_rt, runtimes=item.get("runtimes"),
                                             readable_units=readable_units, cache=cache)
        _record_refusal(refused, item, entry, ans_rt)
        cloned = dict(item)
        cloned_exp = dict(exp)
        cloned_exp.pop("ground_truth_error", None)
        if entry.get("error"):
            cloned_exp["ground_truth_error"] = entry["error"]
            cloned_exp.pop("figure", None)
        elif entry.get("figure") is not None:
            cloned_exp["figure"] = entry["figure"]
        cloned["expect"] = cloned_exp
        return score(cloned, ans, uncertified)
    return score(item, ans, uncertified)


def find_divergences(items: list[dict], answers_by_runtime: dict[str, dict[str, dict]]) -> list[dict]:
    """Identify questions where answers across runtimes diverge in behaviour, figure, rows, or certification."""
    rt_ids = list(answers_by_runtime.keys())
    if len(rt_ids) < 2:
        return []
    base_rt = "reference" if "reference" in answers_by_runtime else rt_ids[0]
    divergences: list[dict] = []
    for item in items:
        iid = item["id"]
        per_rt = {rt: answers_by_runtime[rt].get(iid, {}) for rt in rt_ids if iid in answers_by_runtime[rt]}
        if len(per_rt) < 2:
            continue
        base_ans = per_rt.get(base_rt) or next(iter(per_rt.values()))
        exp = item.get("expect") or {}
        tol = _tolerance(exp, base_ans.get("figure") if base_ans.get("figure") is not None else exp.get("figure"))
        reasons: list[str] = []
        for rt, ans in per_rt.items():
            if rt == base_rt:
                continue
            if ans.get("behaviour") != base_ans.get("behaviour"):
                if "behaviour" not in reasons:
                    reasons.append("behaviour")
            elif isinstance(base_ans.get("rows"), dict) or isinstance(ans.get("rows"), dict):
                if not same(ans.get("rows"), base_ans.get("rows") if isinstance(base_ans.get("rows"), dict) else {}):
                    if "rows" not in reasons:
                        reasons.append("rows")
            elif base_ans.get("figure") is not None or ans.get("figure") is not None:
                if not close(ans.get("figure"), base_ans.get("figure"), tol):
                    if "figure" not in reasons:
                        reasons.append("figure")
            if ans.get("runtime_certified") is False and base_ans.get("runtime_certified") is True:
                if "certification" not in reasons:
                    reasons.append("certification")
        if reasons:
            divergences.append({
                "id": iid,
                "question": item["question"],
                "kind": item["kind"],
                "reason": reasons[0],
                "reasons": reasons,
                "by_runtime": {
                    rt: {
                        "behaviour": a.get("behaviour"),
                        "figure": a.get("figure"),
                        "rows": a.get("rows"),
                        "runtime_certified": a.get("runtime_certified"),
                        "certified_in": a.get("certified_in"),
                        "reason": a.get("reason"),
                    }
                    for rt, a in per_rt.items()
                },
            })
    return divergences


def compare_runtimes(
    items: list[dict],
    ask_fn,
    runtimes: list[str],
    *,
    uncertified: set[str] | frozenset = frozenset(),
    w: WarehouseSet | None = None,
    default_unit: str | None = None,
    readable_units: set[str] | Callable[[dict], set[str]] | None = None,
    cache: dict[str, dict] | None = None,
    refused: list[dict] | None = None,
) -> dict:
    """Evaluate items across each named runtime (comparing against reference when a single external runtime is named) and report divergences."""
    compared = list(dict.fromkeys(runtimes))
    if len(compared) == 1 and compared[0] != "reference":
        compared = ["reference", compared[0]]
    answers_by_rt: dict[str, dict[str, dict]] = {}
    by_runtime: dict[str, dict] = {}
    truth_cache = cache if cache is not None else {}
    for rt_id in compared:
        answers_by_rt[rt_id] = {}
        rows = []
        for item in items:
            ans = ask_fn(item, [rt_id])
            answers_by_rt[rt_id][item["id"]] = ans
            ok, why = score_with_runtime_truth(
                item,
                ans,
                uncertified,
                w=w,
                default_unit=default_unit,
                readable_units=readable_units(item) if callable(readable_units) else readable_units,
                cache=truth_cache,
                refused=refused,
            )
            rows.append({
                "id": item["id"],
                "kind": item["kind"],
                "question": item["question"],
                "ok": ok,
                "why": why,
                "runtime": ans.get("runtime"),
                "figure": ans.get("figure"),
                "behaviour": ans.get("behaviour"),
                "runtime_certified": ans.get("runtime_certified"),
                "certified_in": ans.get("certified_in"),
            })
        kinds = list(dict.fromkeys(r["kind"] for r in rows))
        by_runtime[rt_id] = {
            "rows": rows,
            "by_kind": {
                k: {
                    "passed": sum(1 for r in rows if r["kind"] == k and r["ok"]),
                    "of": sum(1 for r in rows if r["kind"] == k),
                }
                for k in kinds
            },
        }
    return {
        "runtimes": compared,
        "by_runtime": by_runtime,
        "divergences": find_divergences(items, answers_by_rt),
    }


def querying_roles(prof) -> list[str] | None:
    """The profile's querying roles, from the access mirror its graph load wrote (principals.json, in its run
    state); None before a graph is loaded, when the engine says what is missing."""
    path = prof.run_dir() / "principals.json"
    if not path.is_file():
        return None
    return [p["principal"] for p in json.loads(path.read_text()) if "querying" in (p.get("processes") or [])]


def unknown_principal(prof, principal: str) -> str | None:
    """Why principal cannot be asked as on the profile, or None: one that is not a querying role of its own is refused
    every question, and would score every arm nought."""
    who = querying_roles(prof)
    if who is None or principal in who:
        return None
    return (f"{principal} is not a querying role of profile {prof.id}: its querying roles are {', '.join(who)} "
            f"(PRINCIPAL=<one of them>)")


def main(argv: list[str] | None = None) -> int:
    """make eval; a question BigQuery or Vertex AI turned away on every try stops it with one line and exit 2."""
    try:
        return evaluate(argv)
    except ModelSettingError as exc:
        print(f"run_golden: {exc}", file=sys.stderr)
        return 2
    except Unanswered as exc:
        print(f"run_golden: cannot check: {exc}", file=sys.stderr)
        return 2


def evaluate(argv: list[str] | None = None) -> int:
    s = settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile whose graph and sets to run (default: PROFILE, else the registry's default)")
    ap.add_argument("--arm", default=s.ARM or "all", help="a preset or agent arm, comma list, or all")
    ap.add_argument("--suite", default=s.SUITE or None, help="an external UAT question suite file or object-store URI")
    ap.add_argument("--runtimes", default=s.RUNTIMES or None, help="ordered runtime ids (comma-separated) to evaluate against")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--api", help="ask the running API instead of opening the graph (the graph store allows one process at a time)")
    ap.add_argument("--as", dest="principal", default=s.PRINCIPAL or None,
                    help="the querying role to ask as (default: the one the profile's deployment names, asker in its registry)")
    args = ap.parse_args(argv)
    try:
        cli_runtimes = parse_runtimes(args.runtimes, "--runtimes")
    except ValueError as exc:
        print(f"run_golden: {exc}", file=sys.stderr)
        return 2
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
    suite_data = None
    if args.suite:
        try:
            suite_data = sets.read(args.suite)
        except ValueError as exc:
            print(f"run_golden: {exc}", file=sys.stderr)
            return 2
        golden = suite_data["items"]
        catalogue = suite_data.get("catalogue") or []
    else:
        golden = sets.items(build / "golden.json")
        catalogue = sets.catalogue(build / "golden.json")
    suite_runtimes = cli_runtimes or (suite_data.get("runtimes") if suite_data else None)
    try:
        workers = eval_concurrency()
    except ValueError as exc:
        print(f"run_golden: {exc}", file=sys.stderr)
        return 2
    default_unit = None
    readable_units = None
    truth_cache: dict[str, dict] = {}
    refused: list[dict] = []
    if args.api:
        ask_api = api_asker(args.api, token_for(prof.run_dir(), principal), default_runtimes=suite_runtimes)
        arms = choose_arms(args.arm, api_presets(args.api))
        workers = min(workers, API_IN_FLIGHT)

        def ask(question: str, arm: str, who: str | None = None, runtimes: list[str] | None = None) -> dict:
            return ask_api(question, arm, who, runtimes=runtimes if runtimes is not None else suite_runtimes)
    else:
        arms = choose_arms(args.arm, PRESETS, AGENT_ARMS)
        try:
            eng = Engine.serving(prof)
        except GraphLocked as exc:
            print(f"run_golden: {exc}", file=sys.stderr)
            return 2
        default_unit = eng.UNIT_ORDER[0] if eng.UNIT_ORDER else None
        def readable_units(item):
            return eng.g.readable_units(asked_as(item, principal))
        agent = Agent(eng) if any(a in AGENT_ARMS for a in arms) else None

        def ask(question: str, arm: str, who: str | None = None, runtimes: list[str] | None = None) -> dict:
            effective = who or principal
            eff_rt = runtimes if runtimes is not None else suite_runtimes
            if arm in AGENT_ARMS and agent is not None:
                return agent.ask(question, effective, arm=arm, runtimes=eff_rt)
            return eng.answer(question, principal=effective, arm_name=arm, runtimes=eff_rt)
    unc = uncertified_ids(build)
    report = {}
    heldout_report = {}
    catalogue_report = {}
    runtime_comparison = None
    try:
        with nullcontext() if args.api else eng.g.as_roles():
            if not args.api and suite_data is not None and not suite_runtimes:
                truth_cache = resolve_suite_truth(golden, eng.w, default_unit=default_unit, readable_units=readable_units,
                                                  refused=refused)
            for arm in arms:
                by_kind = {}
                scored = each(
                    lambda item: score_with_runtime_truth(
                        item,
                        ask(item["question"], arm, asked_as(item, principal), runtimes=cli_runtimes or item.get("runtimes") or suite_runtimes),
                        unc,
                        w=None if args.api else eng.w,
                        default_unit=default_unit,
                        readable_units=readable_units(item) if readable_units else None,
                        cache=truth_cache,
                        refused=refused,
                    ),
                    golden,
                    lambda item: f"{item['id']} on {arm}",
                    workers,
                )
                for item, (ok, why) in zip(golden, scored):
                    by_kind.setdefault(item["kind"], []).append((item["id"], ok, why, item["question"]))
                    if args.verbose and not ok:
                        print(f"[{arm}] {item['id']} {item['kind']:8} FAIL {item['question']}\n        {why}")
                report[arm] = {k: {"passed": sum(1 for _, ok, _, _ in v if ok), "of": len(v)} for k, v in by_kind.items()}
            if suite_runtimes and (len(suite_runtimes) > 1 or suite_runtimes[0] != "reference"):
                cmp_arm = "graph" if "graph" in arms else arms[0]
                runtime_comparison = compare_runtimes(
                    golden,
                    lambda item, rts: ask(item["question"], cmp_arm, asked_as(item, principal), runtimes=rts),
                    suite_runtimes,
                    uncertified=unc,
                    w=None if args.api else eng.w,
                    default_unit=default_unit,
                    readable_units=readable_units,
                    cache=truth_cache,
                    refused=refused,
                )
            if catalogue:
                for arm in arms:
                    scored = each(lambda item: score(item, ask(item["question"], arm, asked_as(item, principal)), unc), catalogue,
                                  lambda item: f"{item['id']} on {arm}, catalogue", workers)
                    catalogue_report[arm] = {"abstain": {"passed": sum(ok for ok, _ in scored), "of": len(catalogue)}}
            heldout_path = build / "heldout.json"
            if not args.suite and heldout_path.exists():
                heldout = sets.items(heldout_path)
                for arm in arms:
                    by_kind = {}
                    scored = each(lambda item: score(item, ask(item["question"], arm, asked_as(item, principal)), unc), heldout,
                                  lambda item: f"{item['id']} on {arm}, held out", workers)
                    for item, (ok, why) in zip(heldout, scored):
                        by_kind.setdefault(item["kind"], []).append(ok)
                        if args.verbose and not ok:
                            print(f"[{arm} held-out] {item['id']} {item['kind']:8} FAIL {item['question']}\n        {why}")
                    heldout_report[arm] = {k: {"passed": sum(v), "of": len(v)} for k, v in by_kind.items()}
    finally:
        if not args.api:
            eng.close()
    prof.run_dir().mkdir(parents=True, exist_ok=True)
    output = {
        **report,
        "refused": refused,
        "heldout": heldout_report,
        **({"catalogue": catalogue_report} if catalogue else {}),
        **({"suite": args.suite} if args.suite else {}),
        **({"runtimes": suite_runtimes} if suite_runtimes else {}),
        **({"divergences": runtime_comparison["divergences"], "by_runtime": runtime_comparison["by_runtime"]}
           if runtime_comparison is not None else {}),
    }
    (prof.run_dir() / "eval.json").write_text(json.dumps(output, indent=2))
    title = f"UAT suite {args.suite}, asked as {principal}" if args.suite else f"gated golden set, asked as {principal}"
    print_table(title, report)
    for refusal in refused:
        print(f"  {refusal['id']} [{refusal['runtime']}] refused: {refusal['reason']}")
    if runtime_comparison is not None and runtime_comparison["divergences"]:
        print_divergences(runtime_comparison["divergences"])
    if heldout_report:
        print_table("held-out paraphrases (reported apart, not gated)", heldout_report)
    if catalogue_report:
        print_catalogue_table("catalogue abstentions", catalogue_report)
    return 0


def print_divergences(divergences: list[dict]) -> None:
    print(f"\nruntime divergences ({len(divergences)})")
    for d in divergences:
        parts = [f"{rt}={info.get('figure') if info.get('figure') is not None else info.get('behaviour')}"
                 for rt, info in d.get("by_runtime", {}).items()]
        print(f"  {d['id']:8}  {d['reason']:13}  {', '.join(parts)}")


def print_catalogue_table(title: str, report: dict) -> None:
    print(f"\n{title}\narm       abstain")
    for arm, result in report.items():
        counts = result["abstain"]
        print(f"{arm:8}  {counts['passed']:>2}/{counts['of']}")


def print_table(title: str, report: dict) -> None:
    base = ["within", "cross", "shelf", "abstain"]
    extra = [k for r in report.values() for k in r if k not in base]
    kinds = base + list(dict.fromkeys(extra))
    header = "arm       " + "  ".join(f"{k:<9}" for k in kinds).rstrip()
    print(f"\n{title}\n{header}")
    for arm, r in report.items():
        print(f"{arm:8}  " + "  ".join(f"{r.get(k, {}).get('passed', 0):>3}/{r.get(k, {}).get('of', 0):<5}" for k in kinds))


if __name__ == "__main__":
    raise SystemExit(main())
