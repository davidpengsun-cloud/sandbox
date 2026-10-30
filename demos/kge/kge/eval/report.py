"""The A/B report: each arm side by side, per set and kind, with the mean and spread across repeats.

    python3 -m kge.eval.report <run>                               # rebuild a run's report
    python3 -m kge.eval.report <run> --baseline <baseline.json>    # and put it beside a baseline
    python3 -m kge.eval.report <run> --save-baseline <baseline.json>  # record the run as a baseline

<run> is a run's folder, or its id in the profile's runs (kge/pipeline/profile.py:
--profile, else PROFILE, else the registry's default; runs/ in its run state).

kge/eval/harness.py writes it at the end of a run; this rebuilds it from the run's
manifest.json and records.jsonl alone, and the baseline.json the run keeps if
it was put beside one, so a report never says more than its files do. Its
header names each set's version and sha256, the estate's seed, the graph's
hashes and store, and the model's seed. It writes, beside them:

  summary.json  per set, arm and kind: passes and every measure, each as its
                mean, lowest, highest and standard deviation across repeats;
                the margin; and per set and category, each arm's passes and
                the margin of the pass rate
  report.md     the same as tables, with the questions whose result changed
                across repeats, any errors, and each question's result per arm
  report.html   the same page, self-contained

Per category, the baseline sets beside the expansion, graph off and graph on:
an item's category is its own where its set names one, else its kind's; each
arm's passes, labelled graph on or off by its run config's context; and the
margin of the pass rate, the agent's treatment less its control. The baseline
sets are the golden and held-out sets the harness names (SETS); any further
set a client's build holds is the expansion.

The margin is what the graph adds: the reference agent's treatment (graph on)
less its control (graph off) on each score in MARGIN (the golden scorer's pass
rate, then the measures beside it), per set and kind and overall, taken repeat
by repeat so it has a spread across repeats like any measure. It is the A/B's
own measure, not a threshold anyone sets; make margin (kge/eval/margin.py) checks
it after a graph load.

A measure that is unknown in any repeat (a cost with no price, a token count
the provider did not report) is unknown, never averaged over the repeats that
know it. One that does not apply (no query gated, no model wording to check)
is n/a. Each arm is labelled with what ran it: rules, mock or model. A mock
run is said, on the page, to prove the seam and not model quality.

A baseline (--save-baseline) is a run's record kept to put later runs beside:
what ran, each set's version and sha256, the estate's seed, the graph's hashes,
each arm's passes per set and kind and per question, and the margin with its
spread. Beside a baseline, a report gives the passes then and now; a set whose
file is unchanged is put beside it question by question, and a changed set by
its counts alone, so before and after stay comparable when the sets grow
(issue #39). A set that declares what it changed since the very file the
baseline ran (changes_from, kge/eval/sets.py: golden v2, since v1) is put beside
it question by question on every other item, with the changed ones listed
apart, and so is the margin of the pass rate on those other items, read from
the baseline's passes per question. Only like with like is put beside the
baseline: an arm on the mode the baseline's same arm ran on (rules beside
rules), in a run asked as the baseline's principal, on the baseline's profile
(another profile is another client's graph and data). Any other arm reads - in
the baseline's place and none of its questions is listed as moved, and the
report says why. The margin is put beside the baseline's on the same terms,
and only on an unchanged set, or on a declared change's other items. There it
names each margin that fell by more than its spread, and each the baseline
measured that is n/a now (no longer measured).
"""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import math
import re
import shutil
import statistics
import sys
from collections import Counter
from pathlib import Path

from kge.pipeline.trace import read_jsonl

SETS = {"golden": "Golden set", "heldout": "Held-out paraphrases"}
ABOUT = {"golden": "The gated set.", "heldout": "Paraphrases of golden questions, reported apart and never gated."}
BASELINE_SETS = tuple(SETS)
FURTHER = "A further set, scored by the same scorer and reported apart."
KINDS = {"within": "Within a unit", "cross": "Across units", "shelf": "Shelf", "abstain": "Abstain"}
MEASURES = {
    "pass_rate": ("Passed", "rate", False, "quality"),
    "figure_correct": ("Figure correct", "rate", False, "quality"),
    "behaviour_exact": ("Behaviour, exact", "rate", False, "quality"),
    "behaviour_class": ("Behaviour, class", "rate", False, "quality"),
    "gate_first_try": ("Gate, first try", "rate", False, "quality"),
    "retries": ("Retries per gated question", "number", False, "quality"),
    "tier_correct": ("Tier-correct citations", "rate", False, "quality"),
    "numbers_caught": ("Unsupported numbers, caught", "count", False, "quality"),
    "numbers_shown": ("Unsupported numbers, shown", "count", False, "quality"),
    "ms_mean": ("ms per question", "ms", False, "cost"),
    "ms_p95": ("ms, p95", "ms", False, "cost"),
    "llm_calls": ("Model calls per question", "number", False, "cost"),
    "tokens_in": ("Tokens in per question", "number", True, "cost"),
    "tokens_out": ("Tokens out per question", "number", True, "cost"),
    "cost_usd": ("Cost per repeat, USD", "usd", True, "cost"),
}
TABLES = {"quality": "quality", "cost": "latency, tokens and cost"}
BASIS = ("Basis: measured in this run, from each answer's run trace; cost at the prices the manifest records "
         "(`MODEL_PRICE_IN`, `MODEL_PRICE_OUT`), unknown where a model was called with none.")


def spread(values: list, strict: bool = False) -> dict | None:
    """{mean, min, max, sd, n} across repeats. None when no repeat has a value, or, if strict, when any lacks one."""
    if strict and any(v is None for v in values):
        return None
    known = [v for v in values if v is not None]
    if not known:
        return None
    return {"mean": statistics.fmean(known), "min": min(known), "max": max(known),
            "sd": statistics.stdev(known) if len(known) > 1 else 0.0, "n": len(known)}


def ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def p95(xs: list[float]) -> float | None:
    """Nearest rank."""
    if not xs:
        return None
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def one_repeat(recs: list[dict]) -> dict:
    """Every measure over one repeat's records."""
    m = [r["measures"] for r in recs]
    figs = [x["figure_ok"] for x in m if x["figure_ok"] is not None]
    gated = [x["gate"] for x in m if x["gate"]]
    checked = [x["numbers"] for x in m if x["numbers"]["checked"]]
    ms = [x["ms"] for x in m if x["ms"] is not None]
    tin, tout = [x["tokens"]["in"] for x in m], [x["tokens"]["out"] for x in m]
    costs = [x["cost_usd"] for x in m]
    n = len(recs)
    passed = sum(1 for r in recs if r["ok"])
    return {"passed": passed, "pass_rate": ratio(passed, n),
            "figure_correct": ratio(sum(figs), len(figs)),
            "behaviour_exact": ratio(sum(x["behaviour"]["exact"] for x in m), n),
            "behaviour_class": ratio(sum(x["behaviour"]["class"] for x in m), n),
            "gate_first_try": ratio(sum(g["first_try"] for g in gated), len(gated)),
            "retries": ratio(sum(g["tries"] - 1 for g in gated), len(gated)),
            "tier_correct": ratio(sum(x["citations"]["tier_correct"] for x in m), sum(x["citations"]["cited"] for x in m)),
            "numbers_caught": sum(c["caught"] for c in checked) if checked else None,
            "numbers_shown": sum(c["shown"] for c in checked) if checked else None,
            "ms_mean": ratio(sum(ms), len(ms)), "ms_p95": p95(ms),
            "tokens_in": None if None in tin else ratio(sum(tin), n), "tokens_out": None if None in tout else ratio(sum(tout), n),
            "llm_calls": ratio(sum(x["llm_calls"] for x in m), n),
            "cost_usd": None if None in costs else sum(costs),
            "errors": sum(1 for r in recs if r.get("error"))}


def group(recs: list[dict]) -> dict:
    """Across repeats: the number of questions, and each measure's spread."""
    repeats = sorted({r["repeat"] for r in recs})
    per = [one_repeat([r for r in recs if r["repeat"] == n]) for n in repeats]
    out = {"of": len({r["id"] for r in recs}), "passed": spread([p["passed"] for p in per]), "errors": sum(p["errors"] for p in per)}
    for name, (_, _, strict, _) in MEASURES.items():
        out[name] = spread([p[name] for p in per], strict)
    return out


def set_order(records: list[dict]) -> list[str]:
    """The sets the records hold: the known ones first, then any further one in the order it ran."""
    ran = list(dict.fromkeys(r["set"] for r in records))
    return [s for s in SETS if s in ran] + [s for s in ran if s not in SETS]


def summarise(records: list[dict]) -> dict:
    """{set: {arm: {"modes", "kinds": {kind or "all": group}, "all": group}}}"""
    out: dict = {}
    for s in set_order(records):
        out[s] = {}
        for arm in dict.fromkeys(r["arm"] for r in records if r["set"] == s):
            recs = [r for r in records if r["set"] == s and r["arm"] == arm]
            kinds = {k: group([r for r in recs if r["kind"] == k]) for k in KINDS if any(r["kind"] == k for r in recs)}
            everything = group(recs)
            out[s][arm] = {"modes": sorted({r["mode"] for r in recs}), "kinds": {**kinds, "all": everything}, "all": everything}
    return out


def category(r: dict) -> str:
    """A record's category: its item's, where its set names one, else its kind's."""
    return r.get("category") or KINDS.get(r["kind"], r["kind"])


def graph_on(a: dict) -> bool:
    """Whether an arm ran on the graph's context: its run config's, or, in a manifest that does not record it, its name's."""
    stages = a.get("stages")
    return stages.get("context") == "graph" if stages else a["arm"] in ("graph", "treatment")


def by_category(records: list[dict]) -> dict:
    """{set: {category: {"of", "arms": {arm: passed}, "margin": the pass rate's}}}: each arm's passes across repeats,
    and the margin of the pass rate, the treatment's less the control's, repeat by repeat; None without both."""
    out: dict = {}
    for s in set_order(records):
        recs = [r for r in records if r["set"] == s]
        out[s] = {}
        for c in dict.fromkeys(category(r) for r in recs):
            mine = [r for r in recs if category(r) == c]
            of = len({r["id"] for r in mine})
            ran = {arm: sorted({r["repeat"] for r in mine if r["arm"] == arm}) for arm in dict.fromkeys(r["arm"] for r in mine)}
            passed = lambda arm, n: sum(r["ok"] for r in mine if r["arm"] == arm and r["repeat"] == n)  # noqa: E731
            row: dict = {"of": of, "arms": {arm: spread([passed(arm, n) for n in ns]) for arm, ns in ran.items()}, "margin": None}
            if all(a in ran for a in MARGIN_ARMS):
                control, treatment = MARGIN_ARMS
                both = sorted(set(ran[control]) & set(ran[treatment]))
                row["margin"] = spread([(passed(treatment, n) - passed(control, n)) / of for n in both])
            out[s][c] = row
    return out


def category_table(manifest: dict, records: list[dict]) -> tuple[list[str], list[list[str]]]:
    """The per-category table's head and rows: set (baseline or expansion), category, questions, each arm's passes, and
    the margin."""
    arms = [a for a in manifest["arms"] if any(r["arm"] == a["arm"] for r in records)]
    head = ["Set", "Category", "Questions"] + [f"{a['arm']} ({'graph on' if graph_on(a) else 'graph off'})" for a in arms] + \
        ["Margin, treatment less control"]
    rows = []
    for s, cats in by_category(records).items():
        part = f"{SETS.get(s, s)}, {'baseline' if s in BASELINE_SETS else 'expansion'}"
        for c, row in cats.items():
            rows.append([part, c, str(row["of"])] + [show(row["arms"][a["arm"]], "number") if a["arm"] in row["arms"] else "-" for a in arms]
                        + [show_gap(row["margin"], "rate")])
    return head, rows


def category_lines(manifest: dict, records: list[dict]) -> list[str]:
    """The per-category table as the harness prints it."""
    head, rows = category_table(manifest, records)
    widths = [max(len(x) for x in col) for col in zip(head, *rows)]
    line = lambda cells: "  ".join(c.ljust(w) if i < 2 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths))).rstrip()  # noqa: E731
    return [f"by category, passed (mean of {manifest['repeats']}): graph off and graph on", line(head)] + [line(r) for r in rows]


MARGIN_ARMS = ("control", "treatment")
MARGIN = {"pass_rate": "higher", "figure_correct": "higher", "behaviour_class": "higher", "gate_first_try": "higher",
          "tier_correct": "higher", "numbers_caught": "lower", "numbers_shown": "lower"}
EPS = 1e-9


def margins(records: list[dict]) -> dict:
    """{set: {kind or "all": {score: spread}}}: the treatment's score less the control's, repeat by repeat, and its
    mean, lowest, highest and standard deviation across the repeats. A score either arm has no value for in a repeat
    has no margin in it. Only the sets both arms ran."""
    out: dict = {}
    for s in set_order(records):
        recs = {arm: [r for r in records if r["set"] == s and r["arm"] == arm] for arm in MARGIN_ARMS}
        if not all(recs.values()):
            continue
        repeats = sorted({r["repeat"] for r in recs["control"]} & {r["repeat"] for r in recs["treatment"]})
        ran = {r["kind"] for rs in recs.values() for r in rs}
        out[s] = {}
        for k in [k for k in KINDS if k in ran] + ["all"]:
            per = []
            for n in repeats:
                c, t = (one_repeat([r for r in recs[arm] if r["repeat"] == n and k in ("all", r["kind"])]) for arm in MARGIN_ARMS)
                per.append({m: None if None in (t[m], c[m]) else t[m] - c[m] for m in MARGIN})
            out[s][k] = {m: spread([p[m] for p in per]) for m in MARGIN}
    return out


def same_set(then: dict | None, now: dict | None) -> bool:
    """The set the baseline ran: the same version, the same items run, and the same file by sha256."""
    if not isinstance(then, dict) or not isinstance(now, dict):
        return False
    return all(then.get(k) is not None and then.get(k) == now.get(k) for k in ("version", "items", "sha256"))


def set_line(v: dict | None) -> str:
    v = v if isinstance(v, dict) else {}
    return f"{v.get('version') or '(no version)'}, {v.get('items')} of {v.get('size')} items, sha256 `{(v.get('sha256') or '')[:12]}`"


def agent_modes(arms: list[dict] | None) -> str:
    """What ran the reference agent's two arms: rules, mock or model, or each arm's where they differ."""
    modes = {a["arm"]: a.get("mode") for a in arms or []}
    ran = [modes.get(arm) or "nothing" for arm in MARGIN_ARMS]
    return ran[0] if len(set(ran)) == 1 else " and ".join(f"{m} ({arm})" for m, arm in zip(ran, MARGIN_ARMS))


def store_apart(b: dict, engine: str | None) -> str | None:
    """Why the baseline b cannot be put beside a run on the graph store engine: it was recorded on another (its
    graph.store.engine, as the run's manifest records it), whose answers this one's need not match. None where both
    name the same, or either names none (a baseline recorded before the store was recorded is checked as before)."""
    then = ((b.get("graph") or {}).get("store") or {}).get("engine")
    if not then or not engine or then == engine:
        return None
    return (f"the baseline was recorded on {then} and this run is on {engine}: record a baseline for this setup "
            "(setup-client ... BASELINE=<file>)")


def margin_beside(manifest: dict, now: dict, b: dict, records: list[dict] | None = None) -> dict:
    """This run's margin beside the baseline's, set by set. Each kind and score is a row: the margin then and now,
    the tolerance (its spread across repeats, highest less lowest, in this run or the baseline's, whichever is
    wider) and how far it moved the wrong way; it fell if that is more than the tolerance, or if it was measured at
    the baseline and is n/a now (no longer measured). Only like with like is compared: a set the baseline ran as it
    is now (same_set), with the agent's arms on the same mode as the baseline's (rules beside rules), asked as the
    same principal (what a principal may read moves the scores, not the graph), on the same profile (each profile is
    its own client's graph and data). A set that declares what it changed
    since the very file the baseline ran (changed_items), given this run's records, is compared on its other items
    alone, on the one score the baseline keeps per question, the pass rate (on_unchanged); "changed_from" says what
    changed. A set that has changed otherwise, that the baseline has no margin for, or that ran on another mode, as
    another principal or on another profile is not, and "changed" says why. The scores are MARGIN's, each with the way
    it is better, the golden scorer's pass rate first, so a question that fails for a reason no other measure sees still
    counts; a kind the baseline had is never skipped, and a fall smaller than EPS is float noise, rates being ratios of
    counts.
    {"compared"
: {set: rows}, "changed": {set: why}, "changed_from": {set: {version, items, unchanged, why}},
     "new": [sets], "gone": [sets], "fell": rows}"""
    then, sets_then, sets_now = b.get("margin") or {}, b.get("sets") or {}, manifest.get("sets") or {}
    mode_now, mode_then = agent_modes(manifest.get("arms")), agent_modes(b.get("arms"))
    who_now, who_then = manifest.get("principal"), b.get("principal")
    prof_now, prof_then = manifest.get("profile"), b.get("profile")
    apart = "; ".join(why for differs, why in (
        (prof_now != prof_then, f"this run is profile {prof_now}'s, the baseline profile {prof_then}'s"),
        (mode_now != mode_then, f"this run's agent arms ran on {mode_now}, the baseline's on {mode_then}"),
        (who_now != who_then, f"this run was asked as {who_now}, the baseline as {who_then}")) if differs)
    out: dict = {"compared": {}, "changed": {}, "changed_from": {}, "new": [], "gone": [s for s in then if s not in now], "fell": []}
    for s, kinds in now.items():
        if s not in then and s not in sets_then:
            out["new"].append(s)
        elif apart:
            out["changed"][s] = apart
        elif s not in then:
            out["changed"][s] = "the baseline holds no margin for it"
        elif same_set(sets_then.get(s), sets_now.get(s)):
            rows = []
            for k in [k for k in [*KINDS, "all"] if k in kinds or k in then[s]]:
                for m, way in MARGIN.items():
                    rows.append(margin_row(s, k, m, (then[s].get(k) or {}).get(m), (kinds.get(k) or {}).get(m)))
            out["compared"][s] = rows
            out["fell"] += [r for r in rows if r["fell"]]
        else:
            changed = changed_items(sets_then.get(s), sets_now.get(s))
            unchanged = on_unchanged(records, b, s, changed) if changed is not None and records is not None else None
            if unchanged is None:
                out["changed"][s] = (f"it has changed since the baseline ({set_line(sets_then.get(s))} then; "
                                     f"{set_line(sets_now.get(s))} now)")
                continue
            was_by, now_by, ids = unchanged
            c = sets_now[s]["changes_from"]
            on = f"the {len(ids)} items unchanged since {c['version']}"
            rows = [{**margin_row(s, k, "pass_rate", was_by.get(k), now_by.get(k)), "on": on}
                    for k in [*KINDS, "all"] if k in was_by or k in now_by]
            out["compared"][s] = rows
            out["changed_from"][s] = {"version": c["version"], "items": changed, "unchanged": len(ids), "why": c.get("why")}
            out["fell"] += [r for r in rows if r["fell"]]
    return out


def margin_row(s: str, k: str, m: str, was: dict | None, is_: dict | None) -> dict:
    """One score's margin on one kind, then and now: the tolerance, how far it moved the wrong way, and whether it fell."""
    row = {"set": s, "kind": k, "score": m, "then": was, "now": is_, "tolerance": None, "worse_by": None, "fell": False}
    if was is not None and is_ is not None:
        row["tolerance"] = max(was["max"] - was["min"], is_["max"] - is_["min"])
        row["worse_by"] = was["mean"] - is_["mean"] if MARGIN[m] == "higher" else is_["mean"] - was["mean"]
        row["fell"] = row["worse_by"] > row["tolerance"] + EPS
    elif was is not None:
        row["fell"] = True
    return row


def changed_items(then: dict | None, now: dict | None) -> list[str] | None:
    """The items a set declares it changed since the baseline ran it (changes_from, kge/eval/sets.py), when it changed from
    the very file the baseline ran (its version and sha256) and both runs ran the whole set: golden v2 beside a
    baseline on golden v1. None otherwise: a change is never taken on trust from another file."""
    if not isinstance(then, dict) or not isinstance(now, dict) or not isinstance(now.get("changes_from"), dict):
        return None
    c = now["changes_from"]
    if not (c.get("version") and c["version"] == then.get("version") and c.get("sha256") and c["sha256"] == then.get("sha256")):
        return None
    if then.get("items") != then.get("size") or now.get("items") != now.get("size") or not isinstance(c.get("items"), list):
        return None
    return list(c["items"])


def on_unchanged(records: list[dict], b: dict, s: str, changed: list[str]) -> tuple[dict, dict, list[str]] | None:
    """The pass rate's margin on a changed set's other items, at the baseline and in this run: ({kind: spread} then,
    {kind: spread} now, the items). None when the baseline has no passes per question for them, or this run did not
    run each of them."""
    items = (b.get("items") or {}).get(s) or {}
    ids = [i for i in items if i not in changed]
    ran = {r["id"] for r in records if r["set"] == s}
    if not ids or not set(ids) <= ran or not all(isinstance(items[i], dict) and all(a in items[i] for a in MARGIN_ARMS) for i in ids):
        return None
    now = (margins([r for r in records if r["set"] == s and r["id"] in ids]).get(s)) or {}
    return passes_margin(items, ids), {k: g["pass_rate"] for k, g in now.items() if g.get("pass_rate") is not None}, ids


def passes_margin(items: dict, ids: list[str]) -> dict:
    """The pass rate's margin, treatment less control, on these items of a baseline's set, read from its passes per
    question (each arm's share of the repeats it passed): {kind or "all": spread}. The mean is exact. The lowest and
    highest across the baseline's repeats are not kept, so they are bounds: a question passed in some repeats and not
    others could have moved the margin by its share either way, and one passed in all or none could not."""
    out = {}
    for k in [*KINDS, "all"]:
        its = [items[i] for i in ids if k in ("all", items[i].get("kind"))]
        if not its:
            continue
        n, c, t = len(its), [x["control"] for x in its], [x["treatment"] for x in its]
        out[k] = {"mean": (sum(t) - sum(c)) / n, "min": (sum(x == 1 for x in t) - sum(x > 0 for x in c)) / n,
                  "max": (sum(x > 0 for x in t) - sum(x == 1 for x in c)) / n, "sd": None, "n": None}
    return out


def gap(v: float, how: str, unit: bool = True) -> str:
    """A margin, signed: a rate's in percentage points (pts), a count as it is."""
    if how == "rate":
        return signed(round(v * 100, 1)) + (" pts" if unit else "")
    return signed(v)


def size(v: float, how: str) -> str:
    """How far a margin moved, unsigned."""
    return f"{num(round(v * 100, 1))} pts" if how == "rate" else num(v)


def show_gap(s: dict | None, how: str) -> str:
    """A margin's mean across repeats, and its lowest and highest in brackets where they differ; n/a when there is none."""
    if s is None:
        return "n/a"
    return gap(s["mean"], how) if s["min"] == s["max"] else f"{gap(s['mean'], how)} ({gap(s['min'], how, False)}–{gap(s['max'], how, False)})"


def fell_line(row: dict) -> str:
    """One margin that fell, in a sentence: its set, kind and score, then and now, and by how much against its spread,
    or that it is no longer measured."""
    how = MEASURES[row["score"]][1]
    head = f"{row['set']}, {KINDS.get(row['kind'], 'All')}, {MEASURES[row['score']][0]}: {gap(row['then']['mean'], how)} at the baseline"
    on = f"; on {row['on']}" if row.get("on") else ""
    if row["now"] is None:
        return f"{head}, n/a now: no longer measured{on}"
    moved = "fell" if MARGIN[row["score"]] == "higher" else "rose"
    return (f"{head}, {gap(row['now']['mean'], how)} now: {moved} {size(row['worse_by'], how)}, more than its spread across repeats "
            f"({size(row['tolerance'], how)}){on}")


def fell_head(rows: list[dict]) -> str:
    """What the margins named below it did: fell, or are no longer measured."""
    lost = any(r["now"] is None for r in rows)
    return "these margins fell by more than their spread across repeats" + (", or are no longer measured" if lost else "")


def num(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def show(s: dict | None, how: str, strict: bool = False) -> str:
    """The mean across repeats, and the lowest and highest in brackets where they differ; unknown or n/a when there is none."""
    if s is None:
        return "unknown" if strict else "n/a"
    if how == "rate":
        f = lambda v: f"{v * 100:.0f}%"  # noqa: E731
    elif how == "usd":
        f = lambda v: "0" if v == 0 else f"{v:.4f}"  # noqa: E731
    elif how == "ms":
        f = lambda v: f"{v:,.1f}"  # noqa: E731
    else:
        f = num
    return f(s["mean"]) if s["min"] == s["max"] else f"{f(s['mean'])} ({f(s['min'])}–{f(s['max'])})"


def label(arm: str, modes: list[str]) -> str:
    return f"{arm} ({'/'.join(modes)})"


def statement(manifest: dict) -> str:
    modes = {a["mode"] for a in manifest["arms"]}
    llm = manifest.get("llm") or {}
    if "mock" in modes:
        return ("**Mock run.** The arms marked mock ran the reference agent's model path on the mock model "
                "(`MODEL_PROVIDER=mock`, `kge/adapters/model/mock.py`" + (f", mode `{llm['mode']}`" if llm.get("mode") else "") + "): offline, "
                "deterministic, answering the contract from the prompt alone. Their results prove the seam, not model quality. "
                "No real model was run. The arms marked rules ran on rules.")
    if "model" in modes:
        heat = (f"at temperature {llm.get('temperature')}" if llm.get("temperature") is not None else
                "at its own sampling defaults (it takes no temperature)")
        return (f"**Model run.** The arms marked model ran on `{llm.get('name')}` {heat}, seed "
                f"{llm.get('seed')}, with the same prompts in both agent arms. The arms marked rules ran on rules.")
    return ("**No model was run.** Every arm ran on rules: the presets, and the reference agent's rule-based fallback "
            "(the planner and the template on its arm's run config).")


STAND_IN = ("**The tiers are a stand-in's.** Each document's AI readiness and tier, which the treatment's tier rule cites by and "
            "tier-correct citations are scored on, is what `kge/estate/certify.py` landed in the release the graph was loaded from "
            "(`build/landing/<client>/releases/<release_id>/certification.json`): a stand-in for upstream AI certification, run "
            "on the invented estate, not a real certification pipeline. The results on tiers rest on it.")


def sets_ran(manifest: dict) -> str:
    """Each set, its version, how many of its items ran and its file's sha256; an older manifest has counts alone."""
    out = []
    for s, v in (manifest.get("sets") or {}).items():
        if isinstance(v, dict):
            c = v.get("changes_from")
            changed = f"; changed from {c.get('version')} in {words(c['items'])}" if isinstance(c, dict) and c.get("items") else ""
            out.append(f"{s} {v.get('version') or '(no version)'} ({v['items']} of {v['size']} items, sha256 "
                       f"`{(v.get('sha256') or '')[:12]}`{changed})")
        else:
            out.append(f"{s} ({v} items)")
    return ", ".join(out) or "none"


def store(graph: dict | None) -> str:
    """The graph store a run read, in words. Runs before the store was recorded all ran on Kuzu 0.11.3."""
    s = (graph or {}).get("store")
    if not s:
        return "not recorded (Kuzu 0.11.3)"
    return f"{s.get('engine')} {s.get('version')} (storage version {s.get('storage_version')})"


def legacy(graph: dict | None) -> str:
    """The legacy model digest (the DDL and layers as before #39), the digest the #38 baseline recorded as its graph model.
    Runs before #39 recorded none."""
    d = (graph or {}).get("legacy_model")
    return f"`{d[:12]}`" if d else "not recorded"


def compiled(graph: dict | None) -> str:
    """The compiled artifact the graph was loaded from, by its model hash. Runs before #39 loaded none."""
    d = (graph or {}).get("artifact")
    return f"`{d[:12]}`" if d else "not recorded"


def landed(manifest: dict) -> str:
    """The landed release the graph was loaded from, in words. Runs before #39 recorded none."""
    r = manifest.get("release")
    if not r:
        return "landed release not recorded"
    return f"landed release `{r.get('id')}` ({r.get('client')}, policy {r.get('policy')}, as of {r.get('as_of')})"


def model_seed(manifest: dict) -> str:
    llm = manifest.get("llm")
    if not llm:
        return "none, no model ran"
    return str(llm["seed"]) if llm.get("seed") is not None else f"none, {llm.get('provider')} takes none"


def blocks(manifest: dict, records: list[dict], base: dict | None = None) -> list[tuple]:
    summary, gaps = summarise(records), margins(records)
    arms = [a["arm"] for a in manifest["arms"]]
    g = manifest.get("graph") or {}
    seed = (manifest.get("estate") or {}).get("seed")
    out: list[tuple] = [("h1", f"A/B run {manifest['run_id']}"),
                        ("p", f"Run from {manifest['started_at']} to {manifest.get('finished_at') or '(unfinished)'}, asked as "
                              f"`{manifest['principal']}`, {manifest['repeats']} repeat{'s' if manifest['repeats'] != 1 else ''} of each set. "
                              f"Sets: {sets_ran(manifest)}. The estate from estate seed {seed if seed is not None else 'unknown'}; "
                              f"graph model `{(g.get('model') or '')[:12]}`, legacy model {legacy(g)}, artifact {compiled(g)}, "
                              f"overlay `{(g.get('overlay') or '')[:12]}`, "
                              f"store {store(g)}; {landed(manifest)}; profile "
                              f"`{manifest.get('profile')}`; model seed: {model_seed(manifest)}. Pass or fail is the golden scorer's "
                              "(`kge/eval/run_golden.py`), unchanged; the measures beside it are `kge/eval/metrics.py`'s."),
                        ("note", statement(manifest)),
                        ("note", STAND_IN),
                        ("table", ["Arm", "Side", "Runs on", "Config"], "llll",
                         [[a["arm"], a["side"], a["mode"], f"`{a['config']}`"] for a in manifest["arms"]])]
    for s in summary:
        title = SETS.get(s, f"Held-out paraphrases ({s})" if s.startswith("heldout_") else s)
        by_arm = summary[s]
        first = next(iter(by_arm.values()))
        kinds = [k for k in KINDS if k in first["kinds"]]
        about = ABOUT["heldout"] if s.startswith("heldout_") else ABOUT.get(s, FURTHER)
        out += [("h2", f"{title}: passed, by kind"),
                ("p", about + " Questions passed: the mean across the repeats, with the lowest and highest in brackets where they differ."),
                ("table", ["Arm"] + [f"{KINDS[k]} (of {first['kinds'][k]['of']})" for k in kinds] + [f"All (of {first['all']['of']})"],
                 "l" + "r" * (len(kinds) + 1),
                 [[label(a, by_arm[a]["modes"])] + [show(by_arm[a]["kinds"][k]["passed"], "number") for k in kinds]
                  + [show(by_arm[a]["all"]["passed"], "number")] for a in arms if a in by_arm])]
        for t, name in TABLES.items():
            cols = [(m, how, strict) for m, (_, how, strict, table) in MEASURES.items() if table == t]
            heads = [MEASURES[m][0] for m, _, _ in cols]
            basis = [("p", BASIS)] if t == "cost" else []
            out += [("h2", f"{title}: {name}"), *basis,
                    ("table", ["Arm"] + heads, "l" + "r" * len(cols),
                     [[label(a, by_arm[a]["modes"])] + [show(by_arm[a]["all"][m], how, strict) for m, how, strict in cols]
                      for a in arms if a in by_arm]),
                    ("h3", f"{title}: {name}, by kind"), *basis,
                    ("table", ["Arm", "Kind"] + heads, "ll" + "r" * len(cols),
                     [[label(a, by_arm[a]["modes"]), KINDS[k]] + [show(by_arm[a]["kinds"][k][m], how, strict) for m, how, strict in cols]
                      for a in arms if a in by_arm for k in kinds if k in by_arm[a]["kinds"]])]
        if s in gaps:
            out += [("h2", f"{title}: the margin, treatment less control"),
                    ("p", "What the graph adds: the reference agent's treatment (graph on) less its control (graph off), on each "
                          "score, repeat by repeat; the mean across the repeats, with the lowest and highest in brackets where they "
                          "differ. Rates in percentage points (pts); unsupported numbers in counts, where fewer is better, so a "
                          "margin below 0 is the treatment ahead. n/a where either arm has no value."),
                    ("table", ["Kind"] + [MEASURES[m][0] for m in MARGIN], "l" + "r" * len(MARGIN),
                     [[KINDS.get(k, "All")] + [show_gap(g.get(m), MEASURES[m][1]) for m in MARGIN] for k, g in gaps[s].items()])]
    head, rows = category_table(manifest, records)
    out += [("h2", "By category: graph off and graph on"),
            ("p", "Each set's questions by category, the baseline sets beside the expansion: a set whose items name their "
                  "category, by those, and any other by its kinds. The baseline sets are the golden and held-out sets the "
                  "harness names; any further set is the expansion. Questions passed per arm, graph off or on as its run "
                  "config's context is, the mean across the repeats with the lowest and highest in brackets where they "
                  "differ; and the margin, the agent's treatment (graph on) less its control (graph off), in percentage "
                  "points of the pass rate, n/a without both."),
            ("table", head, "llr" + "r" * (len(head) - 3), rows)]
    if base is not None:
        out += against(manifest, summary, records, base)
    repeats = manifest["repeats"]
    runs = Counter((r["set"], r["id"], r["arm"]) for r in records)
    passes = Counter((r["set"], r["id"], r["arm"]) for r in records if r["ok"])
    questions = list(dict.fromkeys((r["set"], r["id"], r["kind"], r["question"]) for r in records))
    changed = [[s, i, KINDS.get(k, k), q, a, f"{passes[s, i, a]} of {runs[s, i, a]}"] for s, i, k, q in questions for a in arms
               if 0 < passes[s, i, a] < runs[s, i, a]]
    out += [("h2", "Questions whose result changed across repeats")]
    out += [("table", ["Set", "Id", "Kind", "Question", "Arm", "Passed"], "lllllr", changed)] if changed else \
        [("p", "None: each arm gave each question the same result in every repeat." if repeats > 1 else "One repeat: nothing to compare.")]
    errors = [[r["set"], r["id"], r["arm"], str(r["repeat"]), r["error"]] for r in records if r.get("error")]
    if errors:
        out += [("h2", "Errors"), ("p", "A question an arm could not answer is recorded as a fail, with the error."),
                ("table", ["Set", "Id", "Arm", "Repeat", "Error"], "lllrl", errors)]
    out += [("h2", "Each question"),
            ("p", f"Passed, of {repeats} repeat{'s' if repeats != 1 else ''}, per arm."),
            ("table", ["Set", "Id", "Kind", "Question"] + arms, "llll" + "r" * len(arms),
             [[s, i, KINDS.get(k, k), q] + [f"{passes[s, i, a]} of {runs[s, i, a]}" if runs[s, i, a] else "-" for a in arms]
              for s, i, k, q in questions])]
    out += [("h2", "How to read it"), ("list", [
        "Passed: the golden scorer, strict on what each item states (a figure's unit, column, value and join path; a cross answer's "
        "parts and verdict; no uncertified document cited; the exact way of declining). In the quality tables, as a rate.",
        "Figure correct: the figure, or each part, equals the agreed one whatever its path or column; over the items that agree a figure.",
        "Behaviour, exact and class: the behaviour the item expects, matched exactly, and its class, one of four: answer (a figure, "
        "a sum, a citation), parts (reported apart, not added), refuse (refused, or blocked by access) or abstain (abstained, or "
        "routed to the steward). A sum of parts that must not be added is the wrong class against parts. An abstain item takes "
        "refuse or abstain as its class.",
        "Gate, first try: every query the answer ran passed the SQL gate on its first try; retries: the agent's tries after the "
        "first, with the gate's reasons. The planner does not retry. Over the questions that ran a query.",
        "Tier-correct citations: of the documents cited, those the certification stand-in (`kge/estate/certify.py`, in place of upstream "
        "AI certification) landed AI-ready as gold or silver; bronze and not AI-ready documents are never cited under the tier rule.",
        "Unsupported numbers: caught, the numbers in a model's wording the number check threw away; shown, any that reached the "
        "reader all the same (none should). n/a where no model wording was checked: a template has none.",
        "ms: the run trace's milliseconds per question. Tokens and model calls per question, from the run trace. Cost: tokens at "
        "`MODEL_PRICE_IN` and `MODEL_PRICE_OUT` (US dollars per million tokens), unknown when a model was called without a price or "
        "a token count; a run that called no model costs nothing.",
        "The margin: the agent's treatment less its control on each score, the golden scorer's pass rate first, repeat by repeat, "
        "in percentage points for a rate. It is put beside a baseline's only where the agent's arms ran on the same mode (rules "
        "beside rules) and were asked as the same principal: a margin that fell by more than its spread across repeats (highest less lowest, in this run or the "
        "baseline's, whichever is wider) is named, and so is one the baseline measured that is n/a now (no longer measured). "
        "`make margin` runs on rules, as the baseline's principal, and fails on either. On rules the repeats agree, so any fall is named. "
        "On a set that declares what it changed since the file the baseline ran (golden v2, since v1), the margin "
        "compared is the pass rate's on its other questions, read from the baseline's passes per question.",
        "Each cell is the mean across repeats, with the lowest and highest in brackets where the repeats differ; no brackets, "
        "no spread. summary.json has the standard deviation too."])]
    return out


QUALITY = [m for m, (_, _, _, table) in MEASURES.items() if table == "quality"]
BESIDE = ("figure_correct", "behaviour_exact", "behaviour_class", "tier_correct")


def tallies(records: list[dict]) -> tuple[Counter, Counter]:
    runs = Counter((r["set"], r["id"], r["arm"]) for r in records)
    return runs, Counter((r["set"], r["id"], r["arm"]) for r in records if r["ok"])


def baseline(manifest: dict, records: list[dict], name: str) -> dict:
    """A run's record, kept to put later runs beside: what ran, and each arm's passes per set and kind and per question."""
    summary = summarise(records)
    runs, passes = tallies(records)
    items: dict = {}
    for r in records:
        key = (r["set"], r["id"], r["arm"])
        items.setdefault(r["set"], {}).setdefault(r["id"], {"kind": r["kind"]})[r["arm"]] = passes[key] / runs[key]
    mean = lambda x: x["mean"] if x else None  # noqa: E731
    return {"baseline": name, "run_id": manifest["run_id"], "recorded_at": manifest.get("finished_at"), "principal": manifest["principal"],
            "repeats": manifest["repeats"], "profile": manifest.get("profile"), "estate": manifest.get("estate"), "graph": manifest.get("graph"),
            "llm": manifest.get("llm"), "sets": manifest.get("sets"),
            "arms": [{k: a.get(k) for k in ("arm", "side", "mode", "config")} for a in manifest["arms"]],
            "passed": {s: {arm: {k: g["passed"]["mean"] for k, g in a["kinds"].items()} for arm, a in by.items()} for s, by in summary.items()},
            "measures": {s: {arm: {m: mean(a["all"][m]) for m in QUALITY} for arm, a in by.items()} for s, by in summary.items()},
            "margin": margins(records), "items": items}


def signed(x: float) -> str:
    return "0" if x == 0 else ("+" if x > 0 else "-") + num(abs(x))


def words(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def ran_on(arms: list[dict] | None) -> str:
    """What ran a run's arms, in words: every arm on rules, or each mode's arms."""
    by: dict = {}
    for a in arms or []:
        by.setdefault(a.get("mode") or "nothing recorded", []).append(a["arm"])
    if len(by) == 1:
        return f"every arm on {next(iter(by))}"
    return ", ".join(f"{words(names)} on {mode}" for mode, names in by.items()) or "no arm recorded"


def apart(manifest: dict, b: dict) -> tuple[set[str], str]:
    """The arms of this run that are not put beside the baseline's, and why, in words. Only like with like is, as
    for the margin (margin_beside): a run on the baseline's profile (each profile is its own client's graph and
    data), asked as the baseline's principal (what a principal may read moves the scores, not the graph), and an arm
    on the mode the baseline's same arm ran on (rules beside rules). An arm the baseline did not run has nothing to be
    put beside, and is not named."""
    then = {a["arm"]: a.get("mode") for a in b.get("arms") or []}
    ran = {arm for by in (b.get("passed") or {}).values() for arm in by}
    arms = [a for a in manifest.get("arms") or [] if a["arm"] in ran]
    if manifest.get("profile") != b.get("profile"):
        return {a["arm"] for a in arms}, (f"this run is profile `{manifest.get('profile')}`'s, the baseline profile "
                                          f"`{b.get('profile')}`'s (each profile is its own client's graph and data)")
    who_now, who_then = manifest.get("principal"), b.get("principal")
    if who_now != who_then:
        return {a["arm"] for a in arms}, (f"this run was asked as `{who_now}`, the baseline as `{who_then}` (what a principal "
                                          "may read moves the scores, not the graph)")
    modes: dict = {}
    for a in arms:
        if a.get("mode") != then.get(a["arm"]):
            modes.setdefault((a.get("mode"), then.get(a["arm"]) or "nothing recorded"), []).append(a["arm"])
    return ({arm for names in modes.values() for arm in names},
            "; ".join(f"{words(names)} ran on {now} here and on {was} at the baseline" for (now, was), names in modes.items()))


def against(manifest: dict, summary: dict, records: list[dict], b: dict) -> list[tuple]:
    """This run beside the baseline: passes per set, arm and kind, then and now; the quality measures; and, on a set
    whose file is the one the baseline ran, each question whose result moved. On a set that declares what it changed
    since that very file (changed_items), each other question whose result moved, and the changed ones apart, never
    as moved. Only like with like (apart): an arm on another mode than at the baseline reads - in the baseline's
    place, and none of its questions is listed as moved; a run asked as another principal is put beside the baseline
    on none of them."""
    then, now = b.get("graph") or {}, manifest.get("graph") or {}
    seed = (b.get("estate") or {}).get("seed")
    if then.get("model") == now.get("model"):
        graph = "This run's graph model is the same."
    elif not now.get("legacy_model"):
        graph = f"This run's graph model is `{(now.get('model') or '')[:12]}`: what the graph holds has changed since."
    elif now["legacy_model"] == then.get("model"):
        graph = (f"This run's graph model is `{(now.get('model') or '')[:12]}`, which differs by what issue #39 adds (the landing "
                 f"release, and the meta-model's newer types, which the compiled pack fills). Its legacy model digest, "
                 f"`{now['legacy_model'][:12]}`, is the baseline's graph model: the curated layers it held then, it holds now.")
    else:
        graph = (f"This run's graph model is `{(now.get('model') or '')[:12]}`, and its legacy model digest, "
                 f"`{now['legacy_model'][:12]}`, is not the baseline's graph model: what the graph holds has changed since.")
    out: list[tuple] = [("h2", f"Against the baseline {b['baseline']}"),
                        ("p", f"The baseline is run `{b['run_id']}`, recorded {b.get('recorded_at')}, asked as `{b.get('principal')}`, "
                              f"{ran_on(b.get('arms'))}, {b['repeats']} repeat{'s' if b['repeats'] != 1 else ''}, from estate seed {seed} "
                              f"and graph model `{(then.get('model') or '')[:12]}`, store {store(then)}. {graph} A set is put beside the baseline question by "
                              "question only if its file is the one the baseline ran, by sha256, or declares what it changed since "
                              "that very file, and then on every other question; a set that has changed otherwise is compared by "
                              "its counts alone.")]
    far, why = apart(manifest, b)
    if far and (manifest.get("principal") != b.get("principal") or manifest.get("profile") != b.get("profile")):
        out.append(("p", f"Only like with like is put beside the baseline, as for the margin: {why}, so no arm's passes, measures "
                         "or questions are put beside the baseline's."))
    elif far:
        its = "Its" if len(far) == 1 else "Their"
        out.append(("p", f"Only like with like is put beside the baseline, as for the margin: {why}. {its} passes and measures "
                         f"read - in the baseline's place, and none of {its.lower()} questions is listed as moved."))
    runs, passes = tallies(records)
    sets_now = manifest.get("sets") or {}
    gaps = margin_beside(manifest, margins(records), b, records)
    for s, by_arm in summary.items():
        title = SETS.get(s, s)
        if s not in (b.get("sets") or {}):
            out.append(("p", f"{title}: {s} is new since the baseline, with nothing to put beside it."))
            continue
        old_sha, new_sha = b["sets"][s].get("sha256"), (sets_now.get(s) or {}).get("sha256") if isinstance(sets_now.get(s), dict) else None
        same = old_sha is not None and old_sha == new_sha
        changed = None if same else changed_items(b["sets"][s], sets_now.get(s))
        like = [arm for arm in by_arm if arm not in far]
        out.append(("h3", f"{title} beside the baseline"))
        if same:
            out.append(("p", f"The same {s} set as the baseline's (sha256 `{old_sha[:12]}`)."))
        elif changed is not None:
            c, n = sets_now[s]["changes_from"], sum(i not in changed for i in (b.get("items") or {}).get(s) or {})
            out.append(("p", f"{s} {sets_now[s].get('version')} changed from the baseline's {c['version']} in {words(changed)} alone, as "
                             f"its file declares ({c.get('why')}); sha256 `{old_sha[:12]}` then, `{(new_sha or '')[:12]}` now. "
                             + ("Its other question is" if n == 1 else f"Its other {n} questions are") + " put beside the baseline's "
                             f"question by question, and {words(changed)} {'is' if len(changed) == 1 else 'are'} listed apart."))
        else:
            out.append(("p", f"{s} has changed since the baseline (sha256 `{(old_sha or '')[:12]}` then, `{(new_sha or '')[:12]}` now): "
                             "its counts are compared, never its questions."))
        rows, quality = [], []
        for arm, a in by_arm.items():
            was = None if arm in far else b["passed"].get(s, {}).get(arm)
            for k in [k for k in KINDS if k in a["kinds"]] + ["all"]:
                is_ = a["kinds"][k]["passed"]["mean"]
                old = (was or {}).get(k)
                rows.append([label(arm, a["modes"]), KINDS.get(k, "All"), "-" if old is None else num(old), num(is_),
                             "-" if old is None else signed(is_ - old)])
            m = b.get("measures", {}).get(s, {}).get(arm) or {}
            quality.append([label(arm, a["modes"])] + [f"{'-' if arm in far else show_rate(m.get(x))} → "
                                                       f"{show_rate((a['all'][x] or {}).get('mean'))}" for x in BESIDE])
        if like:
            out.append(("table", ["Arm", "Kind", "Baseline", "This run", "Change"], "llrrr", rows))
            out.append(("table", ["Arm"] + [f"{MEASURES[x][0]}, baseline → this run" for x in BESIDE], "l" + "r" * len(BESIDE), quality))
        if s in gaps["compared"]:
            cells: dict = {}
            for r in gaps["compared"][s]:
                how = MEASURES[r["score"]][1]
                cells.setdefault(r["kind"], []).append(f"{show_gap(r['then'], how)} → {show_gap(r['now'], how)}")
            fell = [r for r in gaps["compared"][s] if r["fell"]]
            head = fell_head(fell)
            fails = ", and `make margin` fails on them:" if agent_modes(manifest.get("arms")) == "rules" else ":"
            if s in gaps["changed_from"]:
                on = gaps["compared"][s][0]["on"]
                out += [("p", f"The margin of the pass rate, treatment less control, on {on}, beside the baseline's, which is taken "
                              "from its passes per question: the other scores are not kept per question, so they are not compared. "
                              "Each as its mean across repeats, with the lowest and highest in brackets where they differ (at the "
                              "baseline, the bounds its passes per question allow)."),
                        ("table", ["Kind", f"Passed, on {on}, baseline → this run"], "lr", [[KINDS.get(k, "All")] + c for k, c in cells.items()])]
            else:
                out += [("p", "The margin, treatment less control, beside the baseline's: each as its mean across repeats, with the "
                              "lowest and highest in brackets where they differ."),
                        ("table", ["Kind"] + [f"{MEASURES[m][0]}, baseline → this run" for m in MARGIN], "l" + "r" * len(MARGIN),
                         [[KINDS.get(k, "All")] + c for k, c in cells.items()])]
            out += [("p", head[:1].upper() + head[1:] + fails) if fell else ("p", "No margin fell by more than its spread across repeats.")]
            out += [("list", [fell_line(r) for r in fell])] if fell else []
        elif s in gaps["changed"]:
            out.append(("p", f"Its margin is not compared: {gaps['changed'][s]}."))
        if (same or changed is not None) and like:
            moved = []
            for i, item in b["items"].get(s, {}).items():
                for arm in like:
                    if i not in (changed or []) and arm in item and runs[s, i, arm] and passes[s, i, arm] / runs[s, i, arm] != item[arm]:
                        moved.append([s, i, KINDS.get(item["kind"], item["kind"]), arm, f"{round(item[arm] * b['repeats'])} of {b['repeats']}",
                                      f"{passes[s, i, arm]} of {runs[s, i, arm]}"])
            out += [("table", ["Set", "Id", "Kind", "Arm", "Baseline", "This run"], "llllrr", moved)] if moved else \
                [("p", "No question's result moved from the baseline's." if same else "No unchanged question's result moved from the baseline's.")]
        if changed and like:
            was, now_v = sets_now[s]["changes_from"]["version"], sets_now[s].get("version")
            items = b["items"].get(s, {})
            rows = [[s, i, KINDS.get(items[i]["kind"], items[i]["kind"]), arm, f"{round(items[i][arm] * b['repeats'])} of {b['repeats']}",
                     f"{passes[s, i, arm]} of {runs[s, i, arm]}"] for i in changed if i in items for arm in like if arm in items[i] and runs[s, i, arm]]
            out += [("p", f"Changed since the baseline's {was}, so listed apart and never counted as moved: each arm's passes on "
                          f"{words(changed)}, as {was} expected at the baseline and as {now_v} expects in this run."),
                    ("table", ["Set", "Id", "Kind", "Arm", f"Baseline ({was})", f"This run ({now_v})"], "llllrr", rows)]
    gone = [s for s in (b.get("sets") or {}) if s not in summary]
    if gone:
        out.append(("p", "In the baseline and not run here: " + ", ".join(gone) + "."))
    return out


def show_rate(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown(manifest: dict, records: list[dict], base: dict | None = None) -> str:
    lines: list[str] = []
    for b in blocks(manifest, records, base):
        kind = b[0]
        if kind in ("h1", "h2", "h3"):
            lines += ["#" * int(kind[1]) + " " + b[1], ""]
        elif kind == "p":
            lines += [b[1], ""]
        elif kind == "note":
            lines += ["> " + b[1], ""]
        elif kind == "list":
            lines += [f"- {x}" for x in b[1]] + [""]
        elif kind == "table":
            head, align, rows = b[1], b[2], b[3]
            lines.append("| " + " | ".join(md_cell(h) for h in head) + " |")
            lines.append("|" + "|".join("---:" if a == "r" else "---" for a in align) + "|")
            lines += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def inline(text: str) -> str:
    """Escape, then the two inline marks the blocks use: `code` and **bold**."""
    t = htmlmod.escape(str(text))
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)


CSS = """
:root { --bg: #ffffff; --fg: #1d2330; --muted: #5b6475; --line: #d9dee7; --head: #f3f5f9; --note: #fff7e0; --note-line: #e8c55a;
        --ok: #e5f4ea; --mixed: #fff1d6; --bad: #fbe4e4; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: #14171d; --fg: #e3e7ee; --muted: #9aa3b2; --line: #2c323d; --head: #1d222b; --note: #2b2616;
        --note-line: #8a7224; --ok: #183323; --mixed: #3a2f14; --bad: #3d1d1f; }
}
:root[data-theme="dark"] { --bg: #14171d; --fg: #e3e7ee; --muted: #9aa3b2; --line: #2c323d; --head: #1d222b; --note: #2b2616;
        --note-line: #8a7224; --ok: #183323; --mixed: #3a2f14; --bad: #3d1d1f; }
body { background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 24px 16px; }
main { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 8px; } h2 { font-size: 17px; margin: 28px 0 8px; } h3 { font-size: 15px; margin: 20px 0 8px; }
p, li { color: var(--fg); } .muted { color: var(--muted); }
code { font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--head); padding: 1px 4px; border-radius: 3px; }
.note { background: var(--note); border-left: 4px solid var(--note-line); padding: 10px 14px; margin: 12px 0; }
.scroll { overflow-x: auto; margin: 8px 0 4px; }
table { border-collapse: collapse; font-size: 13px; }
th, td { border: 1px solid var(--line); padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: var(--head); font-weight: 600; }
td.r, th.r { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
td.ok { background: var(--ok); } td.mixed { background: var(--mixed); } td.bad { background: var(--bad); }
"""
TALLY = re.compile(r"^(\d+) of (\d+)$")


def tally_class(text: str) -> str:
    m = TALLY.match(str(text))
    if not m:
        return ""
    p, n = int(m.group(1)), int(m.group(2))
    return " ok" if p == n else " bad" if p == 0 else " mixed"


def html(manifest: dict, records: list[dict], base: dict | None = None) -> str:
    body: list[str] = []
    for b in blocks(manifest, records, base):
        kind = b[0]
        if kind in ("h1", "h2", "h3"):
            body.append(f"<{kind}>{inline(b[1])}</{kind}>")
        elif kind == "p":
            body.append(f"<p>{inline(b[1])}</p>")
        elif kind == "note":
            body.append(f'<div class="note">{inline(b[1])}</div>')
        elif kind == "list":
            body.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ul>")
        elif kind == "table":
            head, align, rows = b[1], b[2], b[3]
            cls = lambda a: ' class="r"' if a == "r" else ""  # noqa: E731
            th = "".join(f"<th{cls(a)}>{inline(h)}</th>" for h, a in zip(head, align))
            trs = "".join("<tr>" + "".join(f'<td class="{a}{tally_class(c)}">{inline(c)}</td>' for c, a in zip(row, align)) + "</tr>"
                          for row in rows)
            body.append(f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
    title = f"A/B run {manifest['run_id']}"
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{htmlmod.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
            + "\n".join(body) + "\n</main>\n</body>\n</html>\n")


def load(run_dir: Path) -> tuple[dict, list[dict], dict | None]:
    """A run's manifest, its records, and the baseline it keeps beside them, if any."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    path, base = run_dir / "records.jsonl", run_dir / "baseline.json"
    return manifest, read_jsonl(path) if path.exists() else [], json.loads(base.read_text()) if base.exists() else None


def write(run_dir: Path) -> dict[str, Path]:
    """summary.json, report.md and report.html, from the run's manifest, records and baseline."""
    run_dir = Path(run_dir)
    manifest, records, base = load(run_dir)
    out = {"summary": run_dir / "summary.json", "markdown": run_dir / "report.md", "html": run_dir / "report.html"}
    out["summary"].write_text(json.dumps({"run_id": manifest["run_id"], "arms": manifest["arms"], "sets": summarise(records),
                                          "margin": margins(records), "categories": by_category(records),
                                          "baseline": base["baseline"] if base else None}, indent=2) + "\n")
    out["markdown"].write_text(markdown(manifest, records, base))
    out["html"].write_text(html(manifest, records, base))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="a run's folder, or its id in the profile's runs")
    ap.add_argument("--profile", help="whose runs a run id is in (default: PROFILE, else the registry's default)")
    ap.add_argument("--baseline", help="put the run beside this baseline; the run's folder keeps a copy as baseline.json")
    ap.add_argument("--save-baseline", metavar="PATH",
                    help="record the run as a baseline at PATH (as make baseline does, into the Makefile's BASELINE)")
    ap.add_argument("--name", help="the saved baseline's name (default: the file's name, less .json)")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir() and len(run_dir.parts) == 1:
        from kge.pipeline.profile import ProfileError, resolve
        try:
            run_dir = resolve(args.profile).run_dir() / "runs" / args.run_dir
        except ProfileError as exc:
            print(f"report: {exc}", file=sys.stderr)
            return 2
    if not (run_dir / "manifest.json").is_file():
        print(f"report: no manifest.json in {run_dir}", file=sys.stderr)
        return 2
    if args.baseline:
        if not Path(args.baseline).is_file():
            print(f"report: no baseline {args.baseline}", file=sys.stderr)
            return 2
        shutil.copyfile(args.baseline, run_dir / "baseline.json")
    for p in write(run_dir).values():
        print(p)
    if args.save_baseline:
        manifest, records, _ = load(run_dir)
        path = Path(args.save_baseline)
        path.write_text(json.dumps(baseline(manifest, records, args.name or path.stem), indent=1) + "\n")
        print(f"baseline: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
