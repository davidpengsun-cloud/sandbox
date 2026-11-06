"""The quality service's logic: the profile's golden sets, the A/B harness's runs, and the golden set's eval."""
from __future__ import annotations

import json
import re
from pathlib import Path

from kge.adapters.deadline import request_remaining
from kge.pipeline.profile import Profile
from kge.services.ask import PRESETS, Engine

RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]*$")


def golden(profile: Profile, engine: Engine, default_arm: str) -> list[dict]:
    """The profile's golden sets, the page's sample questions, beside its A/B baseline where it has one
    (kge/eval/sets.py golden)."""
    from kge.eval.sets import golden as golden_sets
    path = profile.baseline
    try:
        baseline = json.loads(path.read_text()) if path and path.is_file() else None
    except ValueError:
        baseline = None
    return golden_sets(profile.estate(), baseline, default_arm, profile.id, units=engine.UNIT_ORDER)


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


def evaluate(profile: Profile, engine: Engine, who: str) -> dict:
    """The golden set answered on each preset as who, each answer scored, with the passes per kind."""
    from kge.eval.run_golden import score, uncertified_ids
    from kge.eval.sets import items as set_items
    items = set_items(profile.estate() / "golden.json")
    unc = uncertified_ids(profile.estate())
    out = {}
    for arm in PRESETS:
        rows = []
        for it in items:
            request_remaining()
            ans = engine.answer(it["question"], principal=who, arm_name=arm)
            ok, why = score(it, ans, unc)
            rows.append({"id": it["id"], "kind": it["kind"], "question": it["question"], "ok": ok, "why": why})
        out[arm] = {"rows": rows, "by_kind": {k: {"passed": sum(1 for r in rows if r["kind"] == k and r["ok"]), "of": sum(1 for r in rows if r["kind"] == k)}
                                           for k in ("within", "cross", "shelf", "abstain")}}
    return out
