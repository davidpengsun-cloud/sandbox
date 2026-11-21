"""The quality service's logic: the profile's golden sets, the A/B harness's runs, and the golden set's eval."""
from __future__ import annotations

import json
import re
from pathlib import Path

from kge.adapters.deadline import request_remaining
from kge.adapters.objstore import StoreError
from kge.ports import REFERENCE
from kge.pipeline.objstore import the_store
from kge.pipeline.profile import Profile
from kge.pipeline.settings import past_questions_retention_days, settings
from kge.services import onboard
from kge.services.ask import AGENT_ARMS, PRESETS, Agent, Engine, parse
from kge.services.onboard import JobRefused

RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]*$")


def golden(profile: Profile, engine: Engine, default_arm: str) -> list[dict]:
    """The profile's golden sets, the page's sample questions, beside its A/B baseline where it has one
    (kge/eval/sets.py golden); each set's golden20 names its questions among the owner's golden 20, the items in the
    golden-20 category that make golden20 asks (kge/estate/golden_sage.py GOLDEN_20), empty where it has none."""
    from kge.estate.golden_sage import GOLDEN_20
    from kge.eval.sets import golden as golden_sets
    path = profile.baseline
    try:
        baseline = json.loads(path.read_text()) if path and path.is_file() else None
    except ValueError:
        baseline = None
    shown = golden_sets(profile.estate(), baseline, default_arm, profile.id, units=engine.UNIT_ORDER, category=GOLDEN_20)
    return [{**{k: v for k, v in s.items() if k != "category_questions"}, "golden20": s["category_questions"]} for s in shown]


def suggestions_meta(
    profile: Profile,
    engine: Engine,
    readable_units: list[str] | set[str] | None = None,
) -> dict:
    """Unit tags for golden questions and certified glossary terms, plus per-person history retention days."""
    from kge.eval.sets import FURTHER, is_heldout, read

    readable = set(readable_units) if readable_units is not None else None
    known_units = set(getattr(engine, "UNIT_ORDER", None) or [])
    term_impl_units: dict[str, list[str]] = {}
    try:
        impl_rows = engine.g.q(
            "MATCH (c:Col)-[:MAPS_TO {certified:true}]->(t:Term) RETURN t.name AS name, c.unit AS unit"
        )
    except Exception:
        impl_rows = []
    for row in impl_rows:
        t_name = str(row.get("name") or "").strip()
        u_name = row.get("unit")
        if t_name and u_name in known_units:
            bucket = term_impl_units.setdefault(t_name, [])
            if u_name not in bucket:
                bucket.append(u_name)

    question_units: dict[str, list[str]] = {}
    unreadable_questions: set[str] = set()
    estate_dir = profile.estate()
    if estate_dir.is_dir():
        paths = [estate_dir / "golden.json"] if (estate_dir / "golden.json").is_file() else []
        paths += [p for p in sorted(estate_dir.glob("*_v*.json")) if FURTHER.match(p.stem) and not is_heldout(p.stem)]
        for p in paths:
            try:
                data = read(p)
            except Exception:
                continue
            stem_m = FURTHER.match(p.stem)
            set_name = stem_m.group(1) if stem_m else p.stem
            for item in data.get("items") or []:
                q = item.get("question")
                if not isinstance(q, str) or not q.strip():
                    continue
                units: list[str] = []
                exp = item.get("expect") if isinstance(item.get("expect"), dict) else {}
                if isinstance(exp.get("unit"), str) and exp["unit"] in known_units:
                    units.append(exp["unit"])
                if isinstance(exp.get("units"), list):
                    units.extend(u for u in exp["units"] if isinstance(u, str) and u in known_units)
                req = parse(q, engine.term_names, engine)
                for u in req.units:
                    if u in known_units and u not in units:
                        units.append(u)
                terms_for_q = req.terms_found or ([req.term] if req.term else [])
                if not units:
                    for t in terms_for_q:
                        for c in engine.semantics.contexts_of(t):
                            if getattr(c, "unit", None) in known_units and c.unit not in units:
                                units.append(c.unit)
                        for u_name in term_impl_units.get(t, []):
                            if u_name not in units:
                                units.append(u_name)
                elif item.get("kind") == "cross":
                    for t in terms_for_q:
                        t_u: list[str] = []
                        for c in engine.semantics.contexts_of(t):
                            if getattr(c, "unit", None) in known_units and c.unit not in t_u:
                                t_u.append(c.unit)
                        for u_name in term_impl_units.get(t, []):
                            if u_name not in t_u:
                                t_u.append(u_name)
                        if len(t_u) == 1 and t_u[0] not in units:
                            units.append(t_u[0])
                set_unit = set_name.removeprefix("golden_")
                if not units and set_unit in known_units:
                    units.append(set_unit)
                prev = question_units.get(q) or []
                merged = list(dict.fromkeys([*prev, *units]))
                numeric = any(key in exp for key in ("figure", "figures", "rows", "parts")) or any(
                    isinstance(value, (int, float)) and not isinstance(value, bool) for value in exp.values()
                ) or re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", str(exp.get("answer", "")).strip())
                definition = item.get("kind") in ("definition", "shelf") and not numeric and exp.get("behaviour") != "figure"
                accessible = bool(set(merged) & readable) if readable is not None and definition else (
                    readable is None or set(merged).issubset(readable)
                )
                if readable is not None and merged and not accessible:
                    unreadable_questions.add(q)
                    question_units.pop(q, None)
                    continue
                if q in unreadable_questions:
                    continue
                question_units[q] = merged

    glossary: list[dict] = []
    try:
        rows = engine.g.q("MATCH (t:Term) RETURN t.name AS name ORDER BY t.name")
    except Exception:
        rows = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        t_units: list[str] = []
        for c in engine.semantics.contexts_of(name):
            if getattr(c, "unit", None) in known_units and c.unit not in t_units:
                t_units.append(c.unit)
        for u_name in term_impl_units.get(name, []):
            if u_name not in t_units:
                t_units.append(u_name)
        if readable is not None and t_units and not set(t_units) & readable:
            continue
        glossary.append({"term": name, "units": t_units})

    return {
        "question_units": question_units,
        "unreadable_questions": unreadable_questions,
        "glossary": glossary,
        "retention_days": past_questions_retention_days(),
    }


def ab_runs(profile: Profile) -> list[Path]:
    """The A/B harness's runs that have a report, newest first (a run id starts with its UTC time): the profile's."""
    root = profile.run_dir() / "runs"
    if not root.is_dir():
        return []
    return sorted((d for d in root.iterdir() if RUN_ID.match(d.name) and (d / "manifest.json").is_file() and (d / "report.html").is_file()),
                  key=lambda d: d.name, reverse=True)


def ab(profile: Profile) -> dict:
    """The A/B harness's runs, newest first: what ran each arm, and where its report is; and the profile's A/B baseline,
    by its file's name, or None for a profile with none."""
    out = []
    for d in ab_runs(profile):
        m = json.loads((d / "manifest.json").read_text())
        out.append({"run_id": d.name, "started_at": m.get("started_at"), "finished_at": m.get("finished_at"), "principal": m.get("principal"),
                    "repeats": m.get("repeats"), "sets": m.get("sets"), "arms": [{"arm": a["arm"], "mode": a["mode"]} for a in m.get("arms") or []],
                    "report": f"/ab/{d.name}/report.html"})
    has = profile.baseline is not None and profile.baseline.is_file()
    return {"runs": out, "baseline": profile.baseline.name if has else None}


def report(profile: Profile, run_id: str) -> Path | None:
    """One run's report, as kge/eval/report.py wrote it from the run's records; None for a run id that names none."""
    runs = profile.run_dir() / "runs"
    if not RUN_ID.match(run_id) or not (runs / run_id / "report.html").is_file():
        return None
    return runs / run_id / "report.html"


def evaluate(
    profile: Profile,
    engine: Engine,
    who: str,
    *,
    arm: str | None = None,
    runtimes: list[str] | None = None,
    check_deadline: bool = True,
) -> dict:
    """The golden set answered on each requested arm as who, each answer judged against its truth on the runtime it
    ran on (kge/eval/run_golden.py judge), with the runtime list it was asked on its truth's; with divergence report
    across runtimes where more than the reference one is named. A kind counts the items scored; an item set apart,
    a draft or an expectation not available on its runtime, is listed apart and counts in neither passed nor of.
    The approved items of the estate's further golden sets (the golden 20 among them) are reported beside it, under
    approved, on the default arm alone (graph where it runs), so the golden set's own counts do not move and the
    request stays within its time; a set naming a unit who may not read is left out, as the page leaves it out."""
    from kge.eval import sets
    from kge.eval.run_golden import (
        APART,
        approved_items,
        asking_runtimes,
        choose_arms,
        compare_runtimes,
        judge,
        parse_runtimes,
        uncertified_ids,
    )

    items = [dict(it, expect=dict(it.get("expect") or {})) for it in sets.items(profile.estate() / "golden.json")]
    eff_runtimes = parse_runtimes(runtimes, "runtimes") if runtimes is not None else None
    arms = choose_arms(arm or "all", PRESETS, AGENT_ARMS)
    readable_units = engine.g.readable_units(who)
    approved = approved_items(profile.estate(), [u for u in engine.UNIT_ORDER or [] if u in readable_units])
    agent = Agent(engine) if any(a in AGENT_ARMS for a in arms) else None

    def ask(item: dict, chosen_arm: str, item_runtimes: list[str] | None) -> dict:
        if chosen_arm in AGENT_ARMS:
            return agent.ask(item["question"], who, arm=chosen_arm, runtimes=item_runtimes)
        return engine.answer(item["question"], principal=who, arm_name=chosen_arm, runtimes=item_runtimes)

    default_unit = engine.UNIT_ORDER[0] if engine.UNIT_ORDER else None
    unc = uncertified_ids(profile.estate())
    truth_cache: dict[str, dict] = {}
    refused: list[dict] = []

    def judged(it: dict, chosen_arm: str) -> dict:
        if check_deadline:
            request_remaining()
        item_rt = asking_runtimes(it, eff_runtimes)
        ans = ask(it, chosen_arm, item_rt)
        verdict = judge(it, ans, unc, w=engine.w, runtimes=item_rt, default_unit=default_unit,
                        readable_units=readable_units, cache=truth_cache, refused=refused)
        row = {"id": it["id"], "kind": it["kind"], "question": it["question"], "ok": verdict["ok"],
               "status": verdict["status"], "why": verdict["why"]}
        if ans.get("runtime"):
            row["runtime"] = ans["runtime"]
        return {"row": row, "verdict": verdict}

    def arm_report(chosen: list[dict], chosen_arm: str) -> dict:
        rows, apart = [], []
        for it in chosen:
            got = judged(it, chosen_arm)
            rows.append(got["row"])
            if got["verdict"]["status"] in APART:
                apart.append({"id": it["id"], "kind": it["kind"], "status": got["verdict"]["status"],
                              "why": got["verdict"]["why"], "runtime": got["verdict"]["runtime"]})
        scored = [r for r in rows if r["status"] not in APART]
        base_kinds = ("within", "cross", "shelf", "abstain")
        extra_kinds = tuple(dict.fromkeys(r["kind"] for r in scored if r["kind"] not in base_kinds))
        return {
            "rows": rows,
            "by_kind": {
                k: {
                    "passed": sum(1 for r in scored if r["kind"] == k and r["ok"]),
                    "of": sum(1 for r in scored if r["kind"] == k),
                }
                for k in (*base_kinds, *extra_kinds)
            },
            "apart": apart,
        }

    out: dict = {a: arm_report(items, a) for a in arms}
    if eff_runtimes and (len(eff_runtimes) > 1 or eff_runtimes[0] != REFERENCE):
        cmp_arm = "graph" if "graph" in arms else arms[0]
        cmp = compare_runtimes(
            items,
            lambda it, rts: (
                request_remaining() if check_deadline else None,
                ask(it, cmp_arm, rts),
            )[1],
            eff_runtimes,
            uncertified=unc,
            w=engine.w,
            default_unit=default_unit,
            readable_units=readable_units,
            cache=truth_cache,
            refused=refused,
        )
        out["runtimes"] = cmp["runtimes"]
        out["by_runtime"] = cmp["by_runtime"]
        out["divergences"] = cmp["divergences"]
    if approved:
        chosen = [it for a in approved for it in a["items"]]
        out["approved"] = {"sets": [{"name": a["name"], "version": a["version"], "items": len(a["items"])} for a in approved],
                           **{a: arm_report(chosen, a) for a in [default_arm(arms)]}}
    out["refused"] = refused
    run_dir = profile.run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eval.json").write_text(json.dumps(out, indent=2))
    return out


def default_arm(arms: list[str]) -> str:
    """The arm the approved items are asked on: graph, the page's default, where the run takes it, else its first."""
    return "graph" if "graph" in arms else arms[0]


def start_eval(
    served_app,
    who: str,
    *,
    arm: str | None = None,
    runtimes: list[str] | None = None,
) -> dict:
    """Validate evaluation parameters and start the evaluation job."""
    from kge.eval.run_golden import choose_arms, parse_runtimes

    try:
        choose_arms(arm or "all", PRESETS, AGENT_ARMS)
    except SystemExit as exc:
        raise ValueError(str(exc)) from None
    eff_runtimes = parse_runtimes(runtimes, "runtimes") if runtimes is not None else None
    return onboard.start_eval(
        served_app,
        who,
        lambda: evaluate(
            served_app.PROFILE,
            served_app.engine(),
            who,
            arm=arm,
            runtimes=eff_runtimes,
            check_deadline=False,
        ),
        arm=arm,
        runtimes=eff_runtimes,
    )


def eval_results(served_app) -> dict | None:
    """Read the running evaluation job state or the latest stored evaluation run results, or None."""
    job = onboard.load_job(served_app)
    if job and job.get("step") == "eval" and job.get("state") == "running":
        return {"state": "running", "id": job["id"], "job": job}
    profile = served_app.PROFILE
    if (settings().STORE_URL or "").strip():
        try:
            raw = the_store().get(f"clients/{profile.client}/onboard/eval.json")
        except StoreError as exc:
            raise JobRefused(503, "the onboarding store could not read the evaluation results") from exc
        if raw:
            return json.loads(raw)
    path = profile.run_dir() / "eval.json"
    if path.is_file():
        return json.loads(path.read_text())
    return None
