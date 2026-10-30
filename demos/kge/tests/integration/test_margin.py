"""The A/B margin and its post-load check (kge/eval/report.py, kge/eval/margin.py).

    .venv/bin/python -m pytest tests/integration/test_margin.py -q

The margin is what the graph adds: the reference agent's treatment (graph on)
less its control (graph off) on each score, per set and kind and overall, repeat
by repeat, with its spread across repeats. The report shows it beside the
baseline's. After a graph load, make margin runs the two arms on rules and
fails, naming each score and kind, when a margin fell by more than its spread.
It is the onboarding's post-load gate too (issue #40, step 7): the thresholds
the loaded artifact holds, the pack's manifest.gates, hold graph on's golden
pass rate (V01) and its lead over graph off (V02) per kind; the held-out set is
reported and never fails (V03); no graph-on answer may cite a document the
tier rule may not cite (V04). Its verdict is the GATE marker beside LOADED,
which a reload drops.

Offline, on rules: nothing here is a model result. The checks that run end to
end run on scratch copies of the build; stop the API first. #38's baseline, recorded on golden v1, is the before;
#39's is kept as it was; #40's is the one make margin checks against.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kge.eval import harness, margin, report
from kge.estate import onboard as unit_onboard
from kge.adapters.model import LLM
from kge.adapters.objstore import open_store
from tests.scratch import BUILD, graph_of, profile_of, run_of, running_as, scratch_build, write_registry
from tests.unit.test_image import kge_path

DEMO = Path(__file__).resolve().parents[2]
B38 = DEMO / "clients" / "meridian" / "baselines" / "issue-38.json"
B39 = DEMO / "clients" / "meridian" / "baselines" / "issue-39.json"
B40 = DEMO / "clients" / "meridian" / "baselines" / "issue-40.json"
SCORES = ["pass_rate", "figure_correct", "behaviour_class", "gate_first_try", "tier_correct", "numbers_caught", "numbers_shown"]
RATES = SCORES[:5]
KINDS = ["within", "cross", "shelf", "abstain", "all"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No model is called: any HTTP post fails the test."""
    def no_network(*a, **k):
        raise AssertionError("a model call left the process")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))


def rec(arm: str, repeat: int, id_: str, kind: str = "within", figure: bool | None = True, klass: bool = True, first_try: bool | None = True,
        cited: int = 1, tier: int = 1, caught: int | None = None, shown: int | None = None, set_name: str = "golden") -> dict:
    """One record with the measures the margin is taken on, set by hand."""
    checked = caught is not None
    m = {"figure_ok": figure, "behaviour": {"expected": "figure", "got": "figure", "exact": klass, "class": klass},
         "gate": None if first_try is None else {"queries": 1, "first_try": first_try, "tries": 1, "passed": first_try},
         "citations": {"cited": cited, "tier_correct": tier, "off_tier": []},
         "numbers": {"checked": checked, "caught": caught, "bad_markers": 0 if checked else None, "shown": shown},
         "ms": 1.0, "tokens": {"in": 0, "out": 0}, "llm_calls": 0, "cost_usd": 0.0}
    return {"run_id": "r", "repeat": repeat, "arm": arm, "mode": "rules", "config": arm, "set": set_name, "id": id_, "kind": kind,
            "question": f"Question {id_}", "ok": bool(figure) and klass, "why": "", "measures": m}


def manifest(repeats: int = 2, golden_sha: str = "g" * 64, version: str = "v1", items: int = 3) -> dict:
    return {"run_id": "r", "started_at": "2026-10-11T00:00:00+00:00", "finished_at": "2026-10-11T00:01:00+00:00",
            "principal": "analyst_enterprise", "repeats": repeats, "profile": "meridian", "llm": None,
            "graph": {"model": "a" * 64, "overlay": "b" * 64}, "estate": {"seed": 7, "generator": "kge/estate/generate.py"},
            "prices": {"in": None, "out": None, "currency": "USD", "per_tokens": 1_000_000},
            "sets": {"golden": {"items": items, "size": items, "version": version, "file": "golden.json", "sha256": golden_sha}},
            "arms": [{"arm": a, "side": s, "path": "agent", "mode": "rules", "config": a}
                     for a, s in (("control", "agent, graph off"), ("treatment", "agent, graph on"))]}


def two_repeats(weaker: bool = False) -> list[dict]:
    """Two within questions and one across, two repeats. The control gets one within figure right; the treatment both
    in repeat 1 and, in repeat 2, one. weaker: the treatment also gets the across question's class wrong in both."""
    out = []
    for n in (1, 2):
        out += [rec("control", n, "G001"), rec("control", n, "G002", figure=False, first_try=False, tier=0),
                rec("control", n, "G021", kind="cross", figure=False, klass=False, cited=0, tier=0)]
        out += [rec("treatment", n, "G001"), rec("treatment", n, "G002", figure=n == 1),
                rec("treatment", n, "G021", kind="cross", figure=not weaker, klass=not weaker, cited=0, tier=0)]
    return out


def spread(mean, lo=None, hi=None) -> dict:
    lo, hi = mean if lo is None else lo, mean if hi is None else hi
    return {"mean": mean, "min": lo, "max": hi, "sd": 0.0, "n": 3}


def test_the_margin_is_the_treatment_less_the_control_repeat_by_repeat():
    g = report.margins(two_repeats())["golden"]
    assert list(g) == ["within", "cross", "all"] and list(g["all"]) == SCORES == list(report.MARGIN)
    assert g["within"]["pass_rate"] == g["within"]["figure_correct"]
    assert g["cross"]["pass_rate"]["mean"] == 1.0 and g["all"]["pass_rate"]["mean"] == pytest.approx(0.5)
    assert g["within"]["figure_correct"] == {"mean": 0.25, "min": 0.0, "max": 0.5, "sd": pytest.approx(0.3536, abs=1e-4), "n": 2}
    assert g["within"]["behaviour_class"] == spread(0.0, 0.0, 0.0) | {"sd": 0.0, "n": 2}
    assert g["within"]["gate_first_try"]["mean"] == 0.5
    assert g["within"]["tier_correct"]["mean"] == 0.5
    assert g["cross"]["figure_correct"]["mean"] == g["cross"]["behaviour_class"]["mean"] == 1.0
    assert g["cross"]["tier_correct"] is None
    assert g["all"]["figure_correct"]["min"] == pytest.approx(1 / 3) and g["all"]["figure_correct"]["max"] == pytest.approx(2 / 3)
    assert g["all"]["numbers_caught"] is None and g["all"]["numbers_shown"] is None


def test_unsupported_numbers_are_a_margin_of_counts_where_fewer_is_better():
    recs = [rec("control", 1, "G001", caught=3, shown=0), rec("treatment", 1, "G001", caught=1, shown=0)]
    g = report.margins(recs)["golden"]["all"]
    assert g["numbers_caught"]["mean"] == -2 and g["numbers_shown"]["mean"] == 0
    assert report.MARGIN["numbers_caught"] == report.MARGIN["numbers_shown"] == "lower"
    assert all(report.MARGIN[s] == "higher" for s in RATES)


def test_there_is_no_margin_unless_both_agent_arms_ran_the_set():
    assert report.margins([rec("treatment", 1, "G001"), rec("graph", 1, "G001")]) == {}
    both = report.margins([rec("control", 1, "G001"), rec("treatment", 1, "G001"), rec("treatment", 1, "H001", set_name="heldout")])
    assert list(both) == ["golden"]


def test_a_baseline_records_the_margin_with_its_spread():
    b = report.baseline(manifest(), two_repeats(), "test")
    assert b["margin"] == json.loads(json.dumps(report.margins(two_repeats())))
    assert b["margin"]["golden"]["within"]["figure_correct"]["max"] == 0.5


def beside(now_records: list[dict], base_records: list[dict], **m) -> dict:
    b = report.baseline(manifest(), base_records, "test")
    return report.margin_beside(manifest(**m), report.margins(now_records), b)


def test_a_margin_that_fell_by_more_than_its_spread_is_named_by_set_kind_and_score():
    got = beside(two_repeats(weaker=True), two_repeats())
    fell = {(r["set"], r["kind"], r["score"]) for r in got["fell"]}
    assert fell == {("golden", "cross", "pass_rate"), ("golden", "cross", "figure_correct"), ("golden", "cross", "behaviour_class"),
                    ("golden", "all", "behaviour_class")}
    lines = [report.fell_line(r) for r in got["fell"]]
    assert "golden, Across units, Behaviour, class: +100 pts at the baseline, 0 pts now: fell 100 pts, more than its spread across repeats (0 pts)" in lines
    row = next(r for r in got["compared"]["golden"] if (r["kind"], r["score"]) == ("all", "figure_correct"))
    assert row["worse_by"] == pytest.approx(1 / 3) and row["tolerance"] == pytest.approx(1 / 3) and not row["fell"]
    assert not beside(two_repeats(), two_repeats())["fell"], "the same run is not a fall"
    assert not beside(two_repeats(), two_repeats(weaker=True))["fell"], "a margin that grew is not a fall"


def test_the_tolerance_is_the_spread_across_repeats_of_either_run_whichever_is_wider():
    """Fewer unsupported numbers is better, so a margin of them that rose fell; a score measured at the baseline and n/a
    now fell too, since an arm no longer has it at all."""
    def one(then: dict, now: dict, score: str = "figure_correct") -> dict:
        b = {"margin": {"golden": {"all": {score: then}}}, "sets": manifest()["sets"], "arms": manifest()["arms"],
             "principal": manifest()["principal"], "profile": manifest()["profile"]}
        rows = report.margin_beside(manifest(), {"golden": {"all": {score: now}}}, b)["compared"]["golden"]
        return next(r for r in rows if r["score"] == score)
    row = one(spread(0.5, 0.4, 0.6), spread(0.35))
    assert row["tolerance"] == pytest.approx(0.2) and row["worse_by"] == pytest.approx(0.15) and not row["fell"]
    assert one(spread(0.5), spread(0.35, 0.25, 0.45))["tolerance"] == pytest.approx(0.2)
    assert one(spread(0.5, 0.45, 0.55), spread(0.35))["fell"] and one(spread(0.5), spread(0.4999999999999))["fell"] is False
    row = one(spread(-2), spread(0), score="numbers_caught")
    assert row["fell"] and row["worse_by"] == 2
    assert "rose 2, more than its spread across repeats (0)" in report.fell_line({**row, "set": "golden", "kind": "all", "score": "numbers_caught"})
    assert not one(spread(0), spread(-2), score="numbers_caught")["fell"]
    assert one(None, spread(0.5))["fell"] is False
    row = one(spread(0.5), None)
    assert row["fell"] and row["worse_by"] is None
    assert report.fell_line({**row, "set": "golden", "kind": "shelf", "score": "tier_correct"}) == \
        "golden, Shelf, Tier-correct citations: +50 pts at the baseline, n/a now: no longer measured"
    assert one(None, None)["fell"] is False
    rose = one(spread(-2), spread(0), score="numbers_caught")
    assert report.fell_head([rose, row]) == "these margins fell by more than their spread across repeats, or are no longer measured"
    assert report.fell_head([rose]) == "these margins fell by more than their spread across repeats"


def test_a_kind_the_baseline_measured_and_this_run_does_not_have_fell():
    b = {"margin": {"golden": {"shelf": {"tier_correct": spread(0.4)}, "all": {"tier_correct": spread(0.5)}}},
         "sets": manifest()["sets"], "arms": manifest()["arms"], "principal": manifest()["principal"], "profile": manifest()["profile"]}
    got = report.margin_beside(manifest(), {"golden": {"all": {"tier_correct": spread(0.5)}}}, b)
    assert [(r["kind"], r["score"]) for r in got["fell"]] == [("shelf", "tier_correct")]


def on_store(engine: str | None) -> dict:
    return {"graph": {"store": {"engine": engine}}} if engine else {}


@pytest.mark.parametrize("then, now", [("ladybug", "ladybug"), ("bigquery", "bigquery"), (None, "ladybug"), (None, "bigquery")])
def test_a_treatment_that_falls_is_caught_on_the_same_store_or_beside_a_store_less_baseline(then, now):
    """The margin gates as it always has where the baseline was recorded on this run's graph store, or names none: a
    treatment that cites at a lower tier than the baseline's falls, whatever the store."""
    assert report.store_apart(on_store(then), now) is None
    m = {**manifest(repeats=1, items=1), **on_store(now)}
    b = report.baseline({**manifest(repeats=1, items=1), **on_store(then)},
                        [rec("control", 1, "G001", tier=0), rec("treatment", 1, "G001", tier=1)], "b")
    weaker = [rec("control", 1, "G001", tier=0), rec("treatment", 1, "G001", tier=0)]
    assert [(r["kind"], r["score"]) for r in report.margin_beside(m, report.margins(weaker), b, weaker)["fell"]] == \
        [("within", "tier_correct"), ("all", "tier_correct")]


def test_a_baseline_recorded_on_another_graph_store_is_named_as_apart():
    said = report.store_apart(on_store("ladybug"), "bigquery")
    assert said == ("the baseline was recorded on ladybug and this run is on bigquery: record a baseline for this setup "
                    "(setup-client ... BASELINE=<file>)")
    assert report.store_apart(on_store("bigquery"), None) is None


def test_the_check_does_not_pass_beside_a_baseline_recorded_on_another_graph_store(tmp_path, capsys):
    """Fail closed: a baseline recorded on another graph store is not put beside this run, nor skipped. The check
    cannot check, exit 2, as for another profile's baseline, writing no GATE, so the candidate cannot be released."""
    b = json.loads(B40.read_text())
    other = tmp_path / "other-store.json"
    other.write_text(json.dumps({**b, "graph": {**b["graph"], "store": {**b["graph"]["store"], "engine": "bigquery"}}}))
    gate = graph_of(BUILD).parent / "GATE"
    before = gate.read_text() if gate.exists() else None
    code, out, err = run_check(BUILD, ["--repeats", "1", "--baseline", str(other)], capsys)
    assert code == 2, out + err
    assert "the baseline was recorded on bigquery and this run is on ladybug: record a baseline for this setup" in err
    assert "make baseline RUN=" in err and "the agent's control" not in out
    assert (gate.read_text() if gate.exists() else None) == before


def test_a_set_that_has_changed_is_put_beside_the_baseline_by_its_counts_alone():
    for changed in ({"golden_sha": "h" * 64}, {"version": "v2"}, {"items": 4}):
        got = beside(two_repeats(weaker=True), two_repeats(), **changed)
        assert got["compared"] == {} and got["fell"] == [] and "golden" in got["changed"], changed
        assert "has changed since the baseline" in got["changed"]["golden"]
    b = report.baseline(manifest(), two_repeats(), "test")
    m = manifest()
    m["sets"]["golden_wealth_v1"] = {"items": 1, "size": 1, "version": "v1", "file": "golden_wealth_v1.json", "sha256": "w" * 64}
    later = report.margin_beside(m, report.margins(two_repeats() + [rec(a, 1, "W001", set_name="golden_wealth_v1") for a in ("control", "treatment")]), b)
    assert later["new"] == ["golden_wealth_v1"] and list(later["compared"]) == ["golden"]
    assert report.margin_beside(manifest(), {}, b)["gone"] == ["golden"]
    for held in (None, {"heldout": b["margin"]["golden"]}):
        old = {k: v for k, v in b.items() if k != "margin"} | ({"margin": held} if held else {})
        got = report.margin_beside(manifest(), report.margins(two_repeats(weaker=True)), old)
        assert got["changed"] == {"golden": "the baseline holds no margin for it"} and got["compared"] == {} and got["fell"] == [], held
        against = report.markdown(manifest(), two_repeats(weaker=True), old).split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
        assert "Its margin is not compared: the baseline holds no margin for it." in against and "fell by more than" not in against


def steady(lost: tuple = ()) -> list[dict]:
    """Two within questions and one across, two repeats that agree. The control passes G001 alone; the treatment passes
    all three, but for each (arm, id) in lost, which fails in both repeats."""
    out = []
    for n in (1, 2):
        for arm in ("control", "treatment"):
            for qid, kind in (("G001", "within"), ("G002", "within"), ("G021", "cross")):
                ok = (arm == "treatment" or qid == "G001") and (arm, qid) not in lost
                out.append(rec(arm, n, qid, kind=kind, figure=ok, klass=ok))
    return out


def declared(m: dict, items: list[str], sha: str = "g" * 64, frm: str = "v1", ran: int | None = None) -> dict:
    """The manifest with its golden set at v2, declaring that it changed from frm (the file whose sha256 is sha) in items;
    ran: how many of its items this run ran (all of them by default)."""
    g = m["sets"]["golden"]
    v2 = {**g, "version": "v2", "sha256": "v" * 64, "items": g["size"] if ran is None else ran,
          "changes_from": {"version": frm, "items": items, "sha256": sha, "why": "the owner's stated change"}}
    return {**m, "sets": {**m["sets"], "golden": v2}}


def test_a_set_that_declares_its_change_puts_its_other_items_beside_the_baselines_passes():
    b = report.baseline(manifest(), steady(), "v1")
    m = declared(manifest(), ["G021"])
    recs = steady(lost=(("treatment", "G021"),))
    got = report.margin_beside(m, report.margins(recs), b, recs)
    assert got["changed"] == {} and got["fell"] == [] and got["new"] == []
    assert got["changed_from"]["golden"] == {"version": "v1", "items": ["G021"], "unchanged": 2, "why": "the owner's stated change"}
    rows = got["compared"]["golden"]
    assert [(r["kind"], r["score"]) for r in rows] == [("within", "pass_rate"), ("all", "pass_rate")], \
        "the pass rate alone, the one score the baseline keeps per question, on the kinds the unchanged items have"
    within = rows[0]
    assert within["then"]["mean"] == within["now"]["mean"] == 0.5 and within["tolerance"] == 0 and within["on"] == "the 2 items unchanged since v1"
    recs = steady(lost=(("treatment", "G001"),))
    got = report.margin_beside(m, report.margins(recs), b, recs)
    assert [(r["kind"], r["score"]) for r in got["fell"]] == [("within", "pass_rate"), ("all", "pass_rate")]
    assert report.fell_line(got["fell"][0]) == ("golden, Within a unit, Passed: +50 pts at the baseline, 0 pts now: fell 50 pts, more "
                                                "than its spread across repeats (0 pts); on the 2 items unchanged since v1")
    same = report.margin_beside(manifest(), report.margins(recs), b, recs)
    assert "golden" not in same["changed_from"] and {r["score"] for r in same["compared"]["golden"]} == set(SCORES)


def test_a_declared_change_is_put_beside_only_the_very_set_the_baseline_ran_whole():
    b = report.baseline(manifest(), steady(), "v1")
    recs = steady()
    for m, why in ((declared(manifest(), ["G021"], sha="h" * 64), "it changed from another file than the one the baseline ran"),
                   (declared(manifest(), ["G021"], frm="v0"), "it changed from another version"),
                   (declared(manifest(), ["G021"], ran=2), "this run ran part of it")):
        got = report.margin_beside(m, report.margins(recs), b, recs)
        assert got["compared"] == {} and "golden" not in got["changed_from"], why
        assert "has changed since the baseline" in got["changed"]["golden"], why
    part = report.baseline(manifest(items=3) | {"sets": {"golden": {**manifest()["sets"]["golden"], "size": 4}}}, steady(), "part")
    got = report.margin_beside(declared(manifest(), ["G021"]), report.margins(recs), part, recs)
    assert got["compared"] == {} and "has changed since the baseline" in got["changed"]["golden"], "the baseline ran part of it"
    got = report.margin_beside(declared(manifest(), ["G021"]), report.margins(recs), b)
    assert got["compared"] == {} and "has changed since the baseline" in got["changed"]["golden"]


def test_the_baselines_margin_on_the_unchanged_items_is_read_from_its_per_question_passes():
    """The baseline keeps each arm's share of repeats passed per question, not its repeats, so its margin on the
    unchanged items has the exact mean and its lowest and highest as bounds: a question passed in some repeats could
    have moved it."""
    b = report.baseline(manifest(), two_repeats(), "v1")
    recs = two_repeats()
    got = report.margin_beside(declared(manifest(), ["G021"]), report.margins(recs), b, recs)
    within = next(r for r in got["compared"]["golden"] if r["kind"] == "within")
    assert (within["then"]["mean"], within["then"]["min"], within["then"]["max"]) == (0.25, 0.0, 0.5)
    assert within["tolerance"] == 0.5 and not within["fell"]
    steady_b = report.baseline(manifest(), steady(), "v1")
    got = report.margin_beside(declared(manifest(), ["G021"]), report.margins(steady()), steady_b, steady())
    assert all(r["then"]["min"] == r["then"]["max"] == r["then"]["mean"] for r in got["compared"]["golden"])


def test_the_report_puts_the_margin_on_the_unchanged_items_beside_the_baselines():
    b = report.baseline(manifest(), steady(), "v1")
    m = declared(manifest(), ["G021"])
    recs = steady(lost=(("treatment", "G001"),))
    against = report.markdown(m, recs, b).split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(against.split())
    assert ("The margin of the pass rate, treatment less control, on the 2 items unchanged since v1, beside the baseline's, which "
            "is taken from its passes per question: the other scores are not kept per question, so they are not compared.") in flat
    assert "| Kind | Passed, on the 2 items unchanged since v1, baseline → this run |" in against
    assert "| Within a unit | +50 pts → 0 pts |" in against and "| All | +50 pts → 0 pts |" in against
    assert "and `make margin` fails on them:" in against
    assert "golden, Within a unit, Passed: +50 pts at the baseline, 0 pts now: fell 50 pts" in against


def test_the_report_shows_the_margin_per_kind_and_beside_the_baselines():
    md = report.markdown(manifest(), two_repeats())
    section = md.split("## Golden set: the margin, treatment less control", 1)[1].split("\n## ", 1)[0]
    assert ("| Kind | Passed | Figure correct | Behaviour, class | Gate, first try | Tier-correct citations | Unsupported numbers, caught "
            "| Unsupported numbers, shown |") in section
    assert "| Within a unit | +25 pts (0–+50) | +25 pts (0–+50) | 0 pts | +50 pts | +50 pts | n/a | n/a |" in section
    assert "| Across units | +100 pts | +100 pts | +100 pts |" in section and "| All |" in section
    b = report.baseline(manifest(), two_repeats(), "pre")
    later = report.markdown(manifest(), two_repeats(weaker=True), b)
    against = later.split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "| Across units | +100 pts → 0 pts | +100 pts → 0 pts | +100 pts → 0 pts |" in against
    assert "| All | +50 pts (+33.3–+66.7) → +16.7 pts (0–+33.3) | +50 pts (+33.3–+66.7) → +16.7 pts (0–+33.3) |" in against, \
        "each with its spread"
    assert "These margins fell by more than their spread across repeats, and `make margin` fails on them:" in against
    assert "golden, Across units, Figure correct: +100 pts at the baseline" in against
    same = report.markdown(manifest(), two_repeats(), b).split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "No margin fell by more than its spread across repeats." in same
    moved = report.markdown(manifest(golden_sha="h" * 64), two_repeats(), b).split("## Against the baseline", 1)[1]
    assert "Its margin is not compared" in moved.split("\n## ", 1)[0]
    assert "the margin, treatment less control" in report.html(manifest(), two_repeats())
    presets = [{**r, "arm": {"control": "catalog", "treatment": "graph"}[r["arm"]]} for r in two_repeats()]
    assert "the margin, treatment less control" not in report.markdown({**manifest(), "arms": [{"arm": a, "side": "preset", "path": "answer", "mode": "rules", "config": a}
                                                                       for a in ("catalog", "graph")]}, presets)


def mocked(records: list[dict], m: dict) -> tuple[list[dict], dict]:
    """The same records and manifest, with the agent's arms run on the mock model."""
    return [{**r, "mode": "mock"} for r in records], {**m, "arms": [{**a, "mode": "mock"} for a in m["arms"]],
                                                       "llm": {"provider": "mock", "name": "mock", "seed": 7, "temperature": 0}}


def test_a_margin_is_compared_only_with_a_baseline_run_on_the_same_mode():
    b = report.baseline(manifest(), two_repeats(), "rules")
    recs, m = mocked(two_repeats(weaker=True), manifest())
    got = report.margin_beside(m, report.margins(recs), b)
    assert got["compared"] == {} and got["fell"] == []
    assert got["changed"]["golden"] == "this run's agent arms ran on mock, the baseline's on rules"
    against = report.markdown(m, recs, b).split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "Its margin is not compared: this run's agent arms ran on mock, the baseline's on rules." in against
    assert "fell by more than" not in against and "make margin" not in against
    assert "asked as `analyst_enterprise`, every arm on rules," in against, "the baseline says what ran it"
    assert ("Only like with like is put beside the baseline, as for the margin: control and treatment ran on mock here and on "
            "rules at the baseline. Their passes and measures read - in the baseline's place, and none of their questions is "
            "listed as moved.") in against
    assert "| Baseline | This run | Change |" not in against and "baseline → this run" not in against, \
        "no arm is like with like, so no table puts one beside the baseline's"
    assert "| golden | G021 |" not in against and "No question's result moved" not in against
    mb = report.baseline(m, mocked(two_repeats(), manifest())[0], "mock")
    assert {(r["kind"], r["score"]) for r in report.margin_beside(m, report.margins(recs), mb)["fell"]} >= {("cross", "pass_rate")}
    against = report.markdown(m, recs, mb).split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "These margins fell by more than their spread across repeats:" in against and "make margin" not in against
    half = {**manifest(), "arms": [manifest()["arms"][0], {**manifest()["arms"][1], "mode": "model"}]}
    assert report.margin_beside(half, report.margins(two_repeats()), b)["changed"]["golden"] == \
        "this run's agent arms ran on rules (control) and model (treatment), the baseline's on rules"


def test_a_margin_is_compared_only_with_a_baseline_asked_as_the_same_principal():
    b = report.baseline(manifest(), two_repeats(), "rules")
    m = {**manifest(), "principal": "analyst_retail"}
    got = report.margin_beside(m, report.margins(two_repeats(weaker=True)), b)
    assert got["compared"] == {} and got["fell"] == []
    assert got["changed"]["golden"] == "this run was asked as analyst_retail, the baseline as analyst_enterprise"
    against = report.markdown(m, two_repeats(weaker=True), b).split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "Its margin is not compared: this run was asked as analyst_retail, the baseline as analyst_enterprise." in against
    assert "fell by more than" not in against and "make margin" not in against
    assert ("Only like with like is put beside the baseline, as for the margin: this run was asked as `analyst_retail`, the "
            "baseline as `analyst_enterprise` (what a principal may read moves the scores, not the graph), so no arm's passes, "
            "measures or questions are put beside the baseline's.") in against
    assert "| Baseline | This run | Change |" not in against and "baseline → this run" not in against
    assert "| golden | G021 |" not in against and "No question's result moved" not in against
    recs, mm = mocked(two_repeats(weaker=True), m)
    assert report.margin_beside(mm, report.margins(recs), b)["changed"]["golden"] == \
        "this run's agent arms ran on mock, the baseline's on rules; this run was asked as analyst_retail, the baseline as analyst_enterprise"
    assert "were asked as the same principal" in report.markdown(m, two_repeats())


def test_the_summary_holds_the_margin(tmp_path):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps(manifest()))
    (run_dir / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in two_repeats()))
    report.write(run_dir)
    s = json.loads((run_dir / "summary.json").read_text())
    assert s["margin"]["golden"]["cross"]["behaviour_class"]["mean"] == 1.0


def test_v05a_requires_every_draft_item_to_be_certified():
    waiting, checks = margin.v05([{"unit": "leasing", "id": "t", "kind": "term", "state": "accepted"}], [], {})
    assert [x["id"] for x in waiting] == ["t"] and checks == []
    waiting, _ = margin.v05([{"unit": "leasing", "id": "t", "kind": "term", "state": "certified"}], [], {})
    assert waiting == []


@pytest.mark.parametrize("covered, expected", [(False, False), (True, True)])
def test_v05b_requires_a_question_for_each_drafted_term(covered, expected):
    items = [{"unit": "leasing", "id": "t", "kind": "term", "state": "certified"},
             {"unit": "leasing", "id": "q", "kind": "question", "state": "certified",
              "set": "golden_leasing", "terms": ["t"] if covered else []}]
    records = [rec("treatment", 1, "q", set_name="golden_leasing_v1")]
    _, checks = margin.v05(items, records, {"sets": {"golden_leasing_v1": {"version": "v1"}}})
    assert checks[0].ok is expected


@pytest.mark.parametrize("passes, expected", [(3, False), (4, True)])
def test_v05c_requires_an_eighty_percent_graph_on_floor(passes, expected):
    items = [{"unit": "leasing", "id": "t", "kind": "term", "state": "certified"},
             {"unit": "leasing", "id": "q", "kind": "question", "state": "certified",
              "set": "golden_leasing", "terms": ["t"]}]
    records = [rec("treatment", 1, f"q{i}", figure=i < passes, set_name="golden_leasing_v1") for i in range(5)]
    _, checks = margin.v05(items, records, {"sets": {"golden_leasing_v1": {"version": "v1"}}})
    assert checks[1].ok is expected


@pytest.mark.parametrize("path, sets_run", [(B38, ("golden", "heldout")), (B39, ("golden", "heldout", "golden_wealth_v1")),
                                            (B40, ("golden", "heldout", "golden_wealth_v1"))], ids=["38", "39", "40"])
def test_the_committed_baseline_records_the_margin_on_rules(path, sets_run):
    b = json.loads(path.read_text())
    assert {a["arm"]: a["mode"] for a in b["arms"]}["treatment"] == "rules" and {a["arm"]: a["mode"] for a in b["arms"]}["control"] == "rules"
    assert tuple(b["margin"]) == sets_run
    for s in ("golden", "heldout"):
        assert list(b["margin"][s]) == KINDS and all(set(v) == set(SCORES) for v in b["margin"][s].values())
        for k, scores in b["margin"][s].items():
            for name, x in scores.items():
                assert x is None or (x["min"] == x["max"] and x["n"] == b["repeats"]), (s, k, name, "on rules the repeats agree")
        for name in RATES:
            t, c = b["measures"][s]["treatment"][name], b["measures"][s]["control"][name]
            assert b["margin"][s]["all"][name]["mean"] == pytest.approx(t - c), (s, name)
    assert all(b["margin"]["golden"]["all"][x]["mean"] > 0 for x in ("pass_rate", "figure_correct", "behaviour_class", "tier_correct"))


def run_check(build: Path, args: list[str], capsys) -> tuple[int, str, str]:
    """The check as make margin runs it, for build's profile: beside the client's baseline, BASELINE, which is #40's unless
    args name another."""
    with running_as(build):
        code = margin.main(args if "--baseline" in args else [*args, "--baseline", str(B40)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_check_passes_beside_the_40_baseline_checking_every_score_on_every_set(tmp_path, capsys, monkeypatch):
    """The check runs on rules whatever model is configured, as the baseline did."""
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    build = scratch_build(tmp_path / "build")
    code, out, err = run_check(build, ["--repeats", "1"], capsys)
    assert code == 0, out + err
    assert f"beside {B40} (of #40, " in out
    for s, v in (("golden", "v2"), ("heldout", "v1"), ("golden_wealth_v1", "v1")):
        assert (f"{s} {v}: the baseline's set; its margin, {len(report.MARGIN)} scores per kind and overall, is put beside the "
                "baseline's") in out, s
    assert "passed: no margin fell by more than its spread across repeats (golden, heldout, golden_wealth_v1)" in out
    assert "sage_v1: new since the baseline, with nothing to put beside it" in out, "the SAGE set (#176), which #40's did not run"
    assert out.count("new since the baseline") == 1 and "changed from the baseline's" not in out
    run = Path(re.search(r"^report: (\S+)$", out, re.M).group(1)).parent
    md = (run / "report.md").read_text()
    against = md.split("## Against the baseline", 1)[1]
    assert "This run's graph model is the same." in against, "#40's baseline was recorded again on the SAGE data (#176)"
    now, then = json.loads((run / "manifest.json").read_text())["graph"], json.loads(B40.read_text())["graph"]
    assert all(now[k] == then[k] for k in ("model", "legacy_model", "artifact", "overlay")), \
        "the baseline was recorded on the graph this build loads"
    for kind, n in (("Within a unit", 20), ("Across units", 10), ("Shelf", 10), ("Abstain", 5)):
        assert f"  V01 golden v2, {kind}: graph on passed {n} of {n} (100%), at least 100%: passed" in out, kind
    for kind in ("Across units", "Shelf"):
        assert f"  V02 golden v2, {kind}: graph on less graph off, +100 pts, at least +50 pts: passed" in out, kind
    assert re.search(r"^  V03 heldout v1, held-out: graph on passed Within a unit \d+ of 6, .*: reported, never gated$", out, re.M), out
    assert "  V04 every set, held-out included: no graph-on answer cites an uncertified or off-tier document: passed" in out
    assert "passed: every post-load check against the pack's gates (V01, V02 and V04; V03 reported)" in out
    prof = profile_of(build)
    gate = prof.store / prof.client / prof.model_hash() / "GATE"
    assert json.loads(gate.read_text()) == {graph_of(build).name.removeprefix("graph."): {"passed": True, "run_id": run.name,
                                                                                         "model_hash": prof.model_hash(),
                                                                                         "sets": {"golden": "passed", "heldout": "passed",
                                                                                                  "golden_wealth_v1": "passed", "sage_v1": "passed"}}}
    assert f"GATE: {gate}: passed, run {run.name}" in out
    for title in ("Golden set", "Held-out paraphrases", "golden_wealth_v1"):
        beside = against.split(f"### {title} beside the baseline", 1)[1].split("\n### ", 1)[0]
        assert "No question's result moved from the baseline's." in beside and "No margin fell by more than its spread" in beside, title


def test_the_check_passes_on_todays_build_on_rules_whatever_model_is_configured(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    build = scratch_build(tmp_path / "build")
    code, out, err = run_check(build, ["--repeats", "1", "--baseline", str(B38)], capsys)
    assert code == 0, out + err
    assert "passed: no margin fell by more than its spread across repeats" in out
    assert ("golden v2: changed from the baseline's v1 in G021 and G030 alone, as its file declares; its 43 other items are put "
            "beside the baseline's per-question passes, and the margin of the pass rate on them is checked, per kind and overall") in out
    assert "heldout v1: the baseline's set" in out
    assert "golden_wealth_v1: new since the baseline, with nothing to put beside it" in out
    runs = list((run_of(build) / "runs").iterdir())
    assert len(runs) == 1 and str(runs[0] / "report.md") in out
    m = json.loads((runs[0] / "manifest.json").read_text())
    assert [(a["arm"], a["mode"]) for a in m["arms"]] == [("control", "rules"), ("treatment", "rules")] and m["llm"] is None
    assert m["principal"] == json.loads(B38.read_text())["principal"]
    md = (runs[0] / "report.md").read_text()
    assert "## Golden set: the margin, treatment less control" in md and "No margin fell by more than its spread across repeats." in md
    against = md.split("## Against the baseline", 1)[1]
    golden = against.split("### Golden set beside the baseline", 1)[1].split("\n### ", 1)[0]
    assert "| Kind | Passed, on the 43 items unchanged since v1, baseline → this run |" in golden
    moved = golden.split("listed apart and never counted as moved", 1)[0]
    assert ("| golden | G017 | Within a unit | control | 3 of 3 | 0 of 1 |\n| golden | G020 | Within a unit | control | 3 of 3 | 0 of 1 |"
            in moved and "treatment |" not in moved.split("| Set | Id |", 1)[1]), \
        "since pack 1.5.0 the control, which finds a column by its name's words, no longer finds Wealth's physical columns"
    apart = golden.split("listed apart and never counted as moved", 1)[1]
    assert "| golden | G021 | Across units | treatment | 3 of 3 | 1 of 1 |" in apart and "| golden | G030 |" in apart, \
        "the baseline's three repeats, this run's one"
    assert not (run_of(BUILD) / "runs" / runs[0].name).exists(), "a scratch check never writes into the demo's own build"


def weaken(build: Path, model_hash: str | None = None) -> None:
    """Remove one curated fact from a scratch graph: the certified mapping of retail revenue to its column."""
    from kge.services.ask.engine.core import Graph
    prof = profile_of(build)
    g = Graph(prof.at(model_hash) if model_hash else prof)
    try:
        g.q("MATCH (c:Col {id: 'retail.monthly_revenue.total_revenue_usd'})-[m:MAPS_TO]->(t:Term {name: 'Revenue'}) DELETE m")
        assert not g.q("MATCH (c:Col {id: 'retail.monthly_revenue.total_revenue_usd'})-[m:MAPS_TO]->(:Term) RETURN c.id")
    finally:
        g.close()


def test_the_check_fails_on_a_weakened_treatment_naming_each_score_and_kind(tmp_path, capsys):
    build = scratch_build(tmp_path / "build")
    weaken(build)
    code, out, err = run_check(build, ["--repeats", "1", "--sets", "golden"], capsys)
    assert code == 1, out + err
    assert "FAILED: these margins fell by more than their spread across repeats" in out
    named = set(re.findall(r"^  golden, ([^,]+), (.+?): ", out, re.M))
    assert {("Within a unit", "Figure correct"), ("Within a unit", "Behaviour, class"), ("Across units", "Figure correct"),
            ("All", "Figure correct")} <= named, out
    assert "heldout: in the baseline and not run here" in out and "golden_wealth_v1: in the baseline and not run here" in out, \
        "a set of the baseline's that did not run is said, not passed"
    assert "make baseline RUN=" in out
    v01 = re.search(r"^  V01 golden v2, Within a unit: graph on passed (\d+) of 20 \(\d+%\), at least 100%: failed, graph on "
                    r"failed (G\d{3}(?:, G\d{3})*)$", out, re.M)
    assert v01 and int(v01.group(1)) < 20 and "G001" in v01.group(2), out
    assert "FAILED: these post-load checks against the pack's gates:" in out
    prof = profile_of(build)
    gate = json.loads((prof.store / prof.client / prof.model_hash() / "GATE").read_text())
    assert [g["passed"] for g in gate.values()] == [False]


@pytest.mark.parametrize("expected", [0, 1, 2], ids=["passed", "margin-fell", "not-checked"])
def test_entrypoint_onboard_preserves_the_real_make_margin_exit_code_in_a_local_store(tmp_path, expected):
    app = tmp_path / f"app-{expected}"
    shutil.copytree(DEMO, app, ignore=shutil.ignore_patterns("build", ".venv", ".coverage*", "__pycache__"))
    build = scratch_build(app / "build")
    estate = app / "estate"
    shutil.copytree(profile_of(build).estate(), estate,
                    ignore=shutil.ignore_patterns("artifacts", "landing", "stores", "profiles.yaml", "review"))
    write_registry(build, clients=app / "clients", warehouses=estate / "warehouses",
                   identities=app / "deploy" / "meridian" / "identities.yaml")
    prof = profile_of(build)
    env = {k: v for k, v in os.environ.items() if k not in ("PROFILE", "PROFILES", "STORE_URL", "MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    profile_env = {**env, "PROFILES": str(build / "profiles.yaml"), "PROFILE": ""}
    unit_onboard.create(prof, "leasing", app / "examples" / "leasing" / "schema.sql", "Asset Finance", rows="csv",
                        csv_dir=app / "examples" / "leasing" / "data", as_="knowledge_engineer")
    unit_onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-22")
    unit_onboard.accept(prof, "leasing", "all", "business_team", "2026-10-22")
    unit_onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-22")
    unit_onboard.build(prof)
    (build / "landing").unlink()
    (build / "landing").mkdir()
    subprocess.run(["make", "certify", f"PY={sys.executable}"], cwd=app, check=True, capture_output=True, text=True,
                   env=profile_env)
    subprocess.run([sys.executable, "-m", "kge.compiler.lock", "--add-new"], cwd=app, check=True,
                   capture_output=True, text=True, env=profile_env)
    subprocess.run(["make", "check", "compile", f"PY={sys.executable}"], cwd=app, check=True,
                   capture_output=True, text=True, env=profile_env)
    artifact = subprocess.run([sys.executable, "-m", "kge.compiler.compile", "--check"], cwd=app, check=True,
                              capture_output=True, text=True, env=profile_env).stdout.strip()
    candidate = Path(artifact).name
    subprocess.run(["make", "load", f"HASH={candidate}", f"PY={sys.executable}"], cwd=app, check=True,
                   capture_output=True, text=True, env=profile_env)
    if expected == 1:
        weaken(build, candidate)
    (build / "artifacts" / "meridian" / "current").unlink()
    store_path = tmp_path / f"store-{expected}"
    store = open_store(str(store_path))
    for root in (app / "clients", app / "deploy"):
        for path in root.rglob("*"):
            if path.is_file():
                store.put(path.relative_to(app).as_posix(), path.read_bytes())
    landing = open_store(str(store_path / "landing" / "meridian"))
    landing_root = build / "landing" / "meridian"
    for path in landing_root.rglob("*"):
        if path.is_file():
            landing.put(path.relative_to(landing_root).as_posix(), path.read_bytes())
    landing.put("current", (os.readlink(landing_root / "current") + "\n").encode())
    command = ["sh", "entrypoint.sh", "onboard", "REPEATS=1"]
    if expected == 2:
        command.append("BASELINE=clients/meridian/baselines/not-recorded.json")
    run_env = {**env, "STORE_URL": str(store_path), "PY": sys.executable,
               "PATH": kge_path(tmp_path / "bin"),
               "MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules",
               "PROFILES": str(build / "profiles.yaml"), "PROFILE": ""}
    result = subprocess.run(command, cwd=app, capture_output=True, text=True,
                            env=run_env)
    assert result.returncode == expected, result.stdout + result.stderr
    assert store.get("work/meridian.tar.gz")
    assert not (build / "stores" / "meridian" / "run" / "margin.status").exists()
    if expected == 1:
        rerun_env = {k: v for k, v in run_env.items() if k != "STORE_URL"}
        rerun = subprocess.run(command, cwd=app, capture_output=True, text=True, env=rerun_env)
        assert rerun.returncode == 1, rerun.stdout + rerun.stderr
        assert "onboard: candidate already loaded=" in rerun.stdout
        assert "onboard: [6/7] load" not in rerun.stdout


def test_the_check_fails_on_a_weakened_treatment_beside_the_v1_baseline_on_golden_v2s_unchanged_items(tmp_path, capsys):
    build = scratch_build(tmp_path / "build")
    weaken(build)
    code, out, err = run_check(build, ["--repeats", "1", "--sets", "golden", "--baseline", str(B38)], capsys)
    assert code == 1, out + err
    named = set(re.findall(r"^  golden, ([^,]+), (.+?): .*; on the 43 items unchanged since v1$", out, re.M))
    assert {("Across units", "Passed"), ("All", "Passed")} <= named, out
    assert {score for _, score in re.findall(r"^  golden, ([^,]+), (.+?): ", out, re.M)} == {"Passed"}, \
        "the pass rate alone: the baseline keeps no other score per question"
    report_md = Path(re.search(r"^report: (\S+)$", out, re.M).group(1)).read_text()
    beside = report_md.split("### Golden set beside the baseline", 1)[1].split("\n### ", 1)[0]
    moved = beside.split("listed apart and never counted as moved", 1)[0]
    assert "| golden | G001 | Within a unit | treatment | 3 of 3 | 0 of 1 |" in moved and "| golden | G021 |" not in moved


def not_ai_ready(build: Path, docs: list[str] | None) -> None:
    """Land documents in a scratch graph as not AI-ready, as kge/graph/load.py does when certification rows are missing:
    every document (docs None), or the given ones."""
    from kge.services.ask.engine.core import Graph
    g = Graph(profile_of(build))
    try:
        ids = docs or [r["id"] for r in g.q("MATCH (d:Document) RETURN d.id AS id")]
        g.q("MATCH (d:Document) WHERE d.id IN $ids SET d.ai_ready = false", {"ids": ids})
        assert ids and not g.q("MATCH (d:Document) WHERE d.ai_ready AND d.id IN $ids RETURN d.id", {"ids": ids})
    finally:
        g.close()


@pytest.mark.parametrize("docs, named", [
    (None, {("Shelf", "Passed"), ("All", "Passed"), ("Shelf", "Tier-correct citations"), ("All", "Tier-correct citations")}),
    (["MD-ACTIVE-CUSTOMER", "MD-REVENUE", "MD-FEE-INCOME"], {("Shelf", "Passed"), ("All", "Passed")}),
], ids=["no-document-ai-ready", "three-definitions-not-ai-ready"])
def test_the_check_fails_when_certified_definitions_load_not_ai_ready(tmp_path, capsys, docs, named):
    build = scratch_build(tmp_path / "build")
    not_ai_ready(build, docs)
    code, out, err = run_check(build, ["--repeats", "1", "--sets", "golden"], capsys)
    assert code == 1, out + err
    got = set(re.findall(r"^  golden, ([^,]+), (.+?): ", out, re.M))
    assert named <= got, out
    if docs is None:
        assert "  golden, Shelf, Tier-correct citations: +46.7 pts at the baseline, n/a now: no longer measured" in out
        assert "FAILED: these margins fell by more than their spread across repeats, or are no longer measured:\n" in out
    else:
        assert "FAILED: these margins fell by more than their spread across repeats:\n" in out


def test_the_check_fails_after_the_walkthroughs_accept_and_names_a_kept_steward_decision(tmp_path, capsys):
    """The runtime overlay keeps the README walkthrough's accept until make reset-overlay, so G041 abstains where the
    golden set, made for a fresh graph, expects the steward; the message names the kept decision as a cause and how to
    set it aside."""
    from kge.services.ask.engine.core import Engine
    build = scratch_build(tmp_path / "build")
    e = Engine(profile_of(build))
    try:
        cand = next(r for r in e.g.steward_queue() if r["family"] == "candidate_term" and r["doc"] == "EM-004")
        e.g.steward_decide(cand["tag"], "steward_glossary", "accept", definition="logins per active customer per month")
    finally:
        e.close()
    code, out, err = run_check(build, ["--repeats", "1", "--sets", "golden"], capsys)
    assert code == 1, out + err
    assert ("Abstain", "Passed") in set(re.findall(r"^  golden, ([^,]+), (.+?): ", out, re.M)), out
    assert "a steward decision the runtime overlay keeps" in out and "make reset-overlay" in out and "G041" in out
    assert "discards" not in out, "make graph keeps the decisions now: it replays them"
    assert "G041" in Path(re.search(r"^report: (\S+)$", out, re.M).group(1)).read_text(), "the report shows the question that moved"

def test_the_check_cannot_check_without_a_margin_on_rules_or_a_filled_graph(tmp_path, capsys):
    b = json.loads(B38.read_text())
    old = tmp_path / "old.json"
    old.write_text(json.dumps({k: v for k, v in b.items() if k != "margin"}))
    code, out, err = run_check(BUILD, ["--baseline", str(old)], capsys)
    assert code == 2 and "records no margin" in err and "make baseline RUN=" in err
    mocked = tmp_path / "mock.json"
    mocked.write_text(json.dumps({**b, "arms": [{**a, "mode": "mock"} for a in b["arms"]]}))
    code, out, err = run_check(BUILD, ["--baseline", str(mocked)], capsys)
    assert code == 2 and "on rules" in err
    code, out, err = run_check(BUILD, ["--baseline", str(tmp_path / "none.json")], capsys)
    assert code == 2 and "no baseline" in err
    build = scratch_build(tmp_path / "build")
    from kge.services.ask.engine.core import Graph
    g = Graph(profile_of(build))
    g.q("MATCH (c:Chunk) DETACH DELETE c")
    g.close()
    code, out, err = run_check(build, ["--repeats", "1"], capsys)
    assert code == 2 and "make pipeline" in err and not (run_of(build) / "runs").exists()


def test_a_baseline_the_check_cannot_read_is_a_2_never_the_1_of_a_margin_that_fell(tmp_path, capsys):
    """A CI step acts on the exit code, so a broken baseline must not read as a regression, nor end in a traceback."""
    b = json.loads(B38.read_text())
    path = tmp_path / "baseline.json"
    for text, said in (('{"margin": ', "cannot read"), (b"\xff\xfe{}", "cannot read"), ("[1, 2]", "not a baseline"),
                       (json.dumps({k: v for k, v in b.items() if k not in ("run_id", "principal")}), "run_id, principal"),
                       (json.dumps({**b, "arms": "z"}), "arms is not a list"),
                       (json.dumps({**b, "arms": [1, 2]}), "arms holds an entry that is not an object naming its arm"),
                       (json.dumps({**b, "sets": "x"}), "sets is not an object"),
                       (json.dumps({**b, "margin": "x"}), "margin is not an object"),
                       (json.dumps({**b, "repeats": "3", "items": []}), "repeats is not a whole number, items is not an object")):
        (path.write_bytes if isinstance(text, bytes) else path.write_text)(text)
        code, out, err = run_check(BUILD, ["--baseline", str(path), "--repeats", "1", "--sets", "heldout",
                                    "--out", str(tmp_path / "runs")], capsys)
        assert code == 2 and said in err and str(path) in err and "make baseline RUN=" in err, (text, err)
        assert "Traceback" not in err and not out and not (tmp_path / "runs").exists(), "nothing ran"


def test_an_error_the_check_did_not_foresee_is_a_2_with_no_traceback(tmp_path, capsys, monkeypatch):
    b = json.loads(B38.read_text())
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({**b, "sets": {**b["sets"], "golden": "x"}}))
    monkeypatch.setattr(margin.harness, "Arms", Quick)
    build = scratch_build(tmp_path / "build")
    code, out, err = run_check(build, ["--baseline", str(path), "--repeats", "1", "--sets", "golden"], capsys)
    assert code == 2, out + err
    assert re.search(r"^margin: cannot check: AttributeError: 'str' object has no attribute 'get' \(at kge/eval/report\.py:\d+\); "
                     r"if the baseline is at fault, a new baseline is recorded on rules", err, re.M), err
    assert "Traceback" not in err and "FAILED" not in out and "passed:" not in out
    with pytest.raises(SystemExit) as exc:
        margin.main(["--help"])
    assert exc.value.code == 0
    capsys.readouterr()


def no_graph(dest: Path) -> Path:
    """A copy of the build with its estate and sets and no graph: make estate has run, make graph has not."""
    return scratch_build(dest, graph=False)


def test_a_build_with_no_graph_or_no_estate_is_a_2_and_the_check_leaves_no_graph_behind(tmp_path, capsys):
    """Opening a graph that is not there makes an empty one, which make margin's own prerequisite would then take for a
    loaded graph, so the check says what to run and never opens it."""
    build = no_graph(tmp_path / "build")
    code, out, err = run_check(build, ["--repeats", "1"], capsys)
    assert code == 2 and f"no graph {graph_of(build)}: make graph, then make pipeline, then make margin" in err, err
    assert not graph_of(build).exists() and not (run_of(build) / "runs").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    write_registry(empty)
    code, out, err = run_check(empty, ["--repeats", "1"], capsys)
    assert code == 2 and f"no estate in {empty}" in err and "warehouses/" in err and "golden.json" in err, err
    assert "make estate, then make graph and make pipeline, then make margin" in err
    assert [p.name for p in empty.iterdir()] == ["profiles.yaml"], "the check wrote nothing"


def test_a_unit_of_the_graph_with_no_warehouse_is_a_2_naming_it(tmp_path, capsys):
    build = scratch_build(tmp_path / "build")
    (build / "warehouses").unlink()
    (build / "warehouses").mkdir()
    for w in sorted((BUILD / "warehouses").glob("*.duckdb")):
        if w.stem != "wealth":
            (build / "warehouses" / w.name).symlink_to(w)
    code, out, err = run_check(build, ["--repeats", "1"], capsys)
    assert code == 2 and "wealth" in err and "make estate" in err and "Traceback" not in err, err
    assert not (run_of(build) / "runs").exists()
    from kge.services.ask.engine.core import Graph
    Graph(profile_of(build)).close()


def test_a_graph_that_was_never_loaded_is_a_2(tmp_path, capsys):
    from kge.adapters.graphstore import open_db
    build = no_graph(tmp_path / "build")
    graph_of(build).parent.mkdir(parents=True, exist_ok=True)
    open_db(graph_of(build)).close()
    code, out, err = run_check(build, ["--repeats", "1"], capsys)
    assert code == 2 and f"the graph {graph_of(build)} was never loaded: make graph, then make pipeline, then make margin" in err, err
    assert not (run_of(build) / "runs").exists()


class Quick:
    """The two arms abstaining at once, so the check's own flow runs with no planner behind it. Each principal it is
    asked as is kept in asked_as."""
    asked_as: set[str] = set()

    def __init__(self, eng, llm=None):
        assert llm is None, "the check runs on rules"

    def describe(self, arm: str) -> dict:
        return {"arm": arm, "side": harness.SIDES[arm], "path": "agent", "mode": "rules", "config": arm}

    def about(self) -> dict:
        return {"llm": None, "graph": {"model": "m" * 64, "overlay": "o" * 64}}

    def ask(self, arm: str, question: str, principal: str) -> dict:
        Quick.asked_as.add(principal)
        return {"behaviour": "abstain", "run_trace": {"total_ms": 1.0, "tokens": {"in": 0, "out": 0}, "llm_calls": [], "number_check": None}}


def test_a_set_that_has_changed_is_not_checked_and_a_check_of_nothing_does_not_pass(tmp_path, capsys, monkeypatch):
    b = json.loads(B38.read_text())
    b["sets"]["golden"]["sha256"] = "ab" * 32
    b["principal"] = "analyst_retail"
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(b))
    monkeypatch.setattr(margin.harness, "Arms", Quick)
    monkeypatch.setattr(Quick, "asked_as", set())
    monkeypatch.setattr(margin, "thresholds", lambda prof: None)
    build = scratch_build(tmp_path / "build")
    code, out, err = run_check(build, ["--baseline", str(path), "--repeats", "1", "--sets", "golden"], capsys)
    assert code == 2 and "nothing to check" in err and "make baseline RUN=" in err
    prof = profile_of(build)
    assert not (prof.store / prof.client / prof.model_hash() / "GATE").exists(), "a check that could not check writes no GATE"
    assert Quick.asked_as == {"analyst_retail"} and "asked as analyst_retail" in out
    runs = list((run_of(build) / "runs").iterdir())
    assert len(runs) == 1 and json.loads((runs[0] / "manifest.json").read_text())["principal"] == "analyst_retail"
    assert "golden: not compared, it has changed since the baseline (v1, 45 of 45 items, sha256 abababababab then;" in out
    assert "  control    within 9 → 0, cross 0 → 0," in out and "  treatment  within 20 → 0," in out, "its counts, never its margin"
    assert "FAILED" not in out
    code, out, err = run_check(build, ["--sets", "train"], capsys)
    assert code == 2 and "no set 'train'" in err


def test_the_check_says_so_when_the_graph_is_locked(capsys, monkeypatch):
    from kge.adapters.graphstore import GraphLocked

    def locked(build):
        raise GraphLocked("held by the API (pid 1)")
    monkeypatch.setattr(margin.Engine, "candidate", staticmethod(locked))
    code, out, err = run_check(BUILD, ["--repeats", "1"], capsys)
    assert code == 2 and "held by the API" in err


GATES = {"golden_min_pass": {"within": 1.0, "cross": 1.0}, "control_margin": {"cross": 0.5}}


def checks(records: list[dict], gates: dict | None = GATES) -> dict[tuple[str, str | None], margin.Check]:
    """The post-load checks on records, by rule and kind (None for a check that is not per kind)."""
    m = manifest()
    m["sets"]["heldout"] = {"items": 1, "size": 1, "version": "v1", "file": "heldout.json", "sha256": "h" * 64}
    got = margin.post_load(gates, records, m)
    assert len({(c.rule, c.kind) for c in got if c.rule != "V04"}) == len([c for c in got if c.rule != "V04"]), "one a rule and kind"
    return {(c.rule, c.kind): c for c in got}


def held_out(on: bool = True, off: bool = False, off_tier: list[str] = ()) -> list[dict]:
    """H001, a held-out within question, one repeat: the treatment passes it if on, the control if off; the treatment
    cites off_tier, documents the tier rule may not cite."""
    out = [rec("treatment", 1, "H001", figure=on, klass=on, set_name="heldout"), rec("control", 1, "H001", figure=off, klass=off, set_name="heldout")]
    out[0]["measures"]["citations"]["off_tier"] = list(off_tier)
    return out


def test_v01_holds_graph_on_to_the_pack_s_golden_min_pass_per_kind_naming_the_items_it_failed():
    got = checks(steady())
    assert got[("V01", "within")].ok and got[("V01", "cross")].ok
    assert got[("V01", "within")].line == "V01 golden v1, Within a unit: graph on passed 2 of 2 (100%), at least 100%: passed"
    got = checks(steady(lost=(("treatment", "G002"),)))
    assert got[("V01", "within")].ok is False and got[("V01", "cross")].ok
    assert got[("V01", "within")].line == ("V01 golden v1, Within a unit: graph on passed 1 of 2 (50%), at least 100%: failed, "
                                           "graph on failed G002")
    assert ("V01", "shelf") not in got, "only the kinds the gates name"


def test_v02_holds_graph_on_less_graph_off_to_the_pack_s_control_margin_per_kind():
    got = checks(steady())
    assert got[("V02", "cross")].ok and got[("V02", "cross")].line == \
        "V02 golden v1, Across units: graph on less graph off, +100 pts, at least +50 pts: passed"
    recs = [r if not (r["arm"] == "control" and r["id"] == "G021") else rec("control", r["repeat"], "G021", kind="cross") for r in steady()]
    got = checks(recs)
    assert got[("V01", "cross")].ok and got[("V02", "cross")].ok is False
    assert got[("V02", "cross")].line == ("V02 golden v1, Across units: graph on less graph off, 0 pts, at least +50 pts: failed, "
                                          "graph off passed G021")


def test_v03_reports_held_out_graph_on_and_off_and_never_fails():
    for on in (True, False):
        got = checks(steady() + held_out(on=on))
        c = got[("V03", None)]
        assert c.ok is None, "reported, never gated"
        assert c.line == (f"V03 heldout v1, held-out: graph on passed Within a unit {int(on)} of 1; graph off Within a unit 0 of 1: "
                          "reported, never gated")
        assert not [x for x in got.values() if x.ok is False], "a held-out fall fails nothing"
    assert checks(steady())[("V03", None)].line == "V03 no held-out set ran: nothing to report"


def test_v04_names_each_graph_on_answer_that_cites_an_uncertified_or_off_tier_document_held_out_included():
    got = checks(steady() + held_out())
    assert got[("V04", None)].ok and got[("V04", None)].line == \
        "V04 every set, held-out included: no graph-on answer cites an uncertified or off-tier document: passed"
    got = margin.post_load(GATES, steady() + held_out(off_tier=["EM-002", "WIKI-001"]), manifest())
    bad = [c for c in got if c.rule == "V04"]
    assert len(bad) == 1 and bad[0].ok is False
    assert bad[0].line == ("V04 heldout H001, Question H001: graph on cites EM-002 and WIKI-001, which certification did not land "
                           "AI-ready gold or silver: failed")
    control = steady() + held_out()
    control[-1]["measures"]["citations"]["off_tier"] = ["EM-002"]
    assert checks(control)[("V04", None)].ok


def test_without_gates_the_thresholds_are_not_checked_and_a_kind_the_set_lacks_is_not_measured():
    got = checks(steady(), gates=None)
    assert {k for k in got} == {("V03", None), ("V04", None)}
    got = checks(steady(), gates={"golden_min_pass": {"shelf": 1.0}, "control_margin": {"abstain": 0.5}})
    assert got[("V01", "shelf")].ok is None and got[("V01", "shelf")].line == "V01 golden v1, Shelf: no item of this kind, not measured"
    assert got[("V02", "abstain")].ok is None


def test_the_gate_is_a_marker_beside_loaded_per_store_and_a_reload_drops_it(tmp_path):
    from kge.graph import release
    folder = tmp_path / ("c" * 64)
    folder.mkdir()
    graph, other = folder / "graph.ladybug", folder / "graph.retired"
    release.write_marker(folder, {"ladybug": {"model_hash": "c" * 64}, "retired": {"model_hash": "c" * 64}})
    release.mark_gate(graph, {"passed": True, "run_id": "r1", "model_hash": "c" * 64})
    release.mark_gate(other, {"passed": False, "run_id": "r2", "model_hash": "c" * 64})
    assert release.gate(graph) == {"passed": True, "run_id": "r1", "model_hash": "c" * 64}
    assert json.loads((folder / "GATE").read_text()) == {"ladybug": release.gate(graph), "retired": release.gate(other)}
    release.unmark(graph)
    assert release.loaded(graph) is None and release.gate(graph) is None
    assert release.loaded(other) and release.gate(other) == {"passed": False, "run_id": "r2", "model_hash": "c" * 64}
    release.unmark(other)
    assert not (folder / "LOADED").exists() and not (folder / "GATE").exists()


def test_the_gates_are_read_from_the_artifact_whose_graph_is_checked_never_the_working_pack(tmp_path):
    from tests.scratch import stand_in
    build = scratch_build(tmp_path / "build", graph=False)
    prof = profile_of(build)
    pack = __import__("yaml").safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())
    assert margin.thresholds(prof) == pack["manifest"]["gates"], "the current artifact's, compiled from the pack"
    stand_in(build, "c0ffee")
    prof = profile_of(build)
    assert margin.thresholds(prof) is None
    folder = prof.artifacts / prof.client / "c0ffee"
    body = json.dumps({"golden_min_pass": {"within": 0.5}}, indent=1, sort_keys=True) + "\n"
    (folder / "gates.json").write_text(body)
    man = json.loads((folder / "manifest.json").read_text())
    (folder / "manifest.json").write_text(json.dumps({**man, "files": {"gates.json": __import__("hashlib").sha256(body.encode()).hexdigest()}}))
    assert margin.thresholds(prof) == {"golden_min_pass": {"within": 0.5}}
    (folder / "gates.json").write_text(body.replace("0.5", "0.1"))
    with pytest.raises(margin.CannotCheck, match="is not the file its manifest hashed"):
        margin.thresholds(prof)


def test_with_no_baseline_the_gates_are_checked_alone_and_the_check_says_the_no_fall_check_was_skipped(tmp_path, capsys):
    build = scratch_build(tmp_path / "build")
    write_registry(build, baseline=None)
    with running_as(build):
        code = margin.main(["--repeats", "1", "--sets", "golden"])
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert "asked as analyst_enterprise" in out, "the deployment's asker"
    assert "with no baseline" in out and "no-fall check: skipped, profile meridian names no baseline" in out
    for kind in ("Within a unit", "Across units", "Shelf", "Abstain"):
        assert f"V01 golden v2, {kind}: graph on passed" in out, kind
    assert "passed: every post-load check against the pack's gates" in out and "passed: no margin fell" not in out
    prof = profile_of(build)
    gate = json.loads((prof.store / prof.client / prof.model_hash() / "GATE").read_text())
    run = Path(re.search(r"^report: (\S+)$", out, re.M).group(1)).parent
    assert gate == {graph_of(build).name.removeprefix("graph."): {"passed": True, "run_id": run.name,
                                                                  "model_hash": prof.model_hash(),
                                                                  "sets": {"golden": "passed"}}}


def test_an_empty_baseline_is_none_so_the_gates_are_checked_alone_though_the_profile_names_one(tmp_path, capsys):
    """BASELINE= is none, as make ab BASELINE= puts a run beside none: make margin passes it on as --baseline '', and the
    check puts the margin beside no baseline, not the profile's, and says so."""
    build = scratch_build(tmp_path / "build")
    assert profile_of(build).baseline is not None, "the demo's profile names a baseline"
    with running_as(build):
        code = margin.main(["--repeats", "1", "--sets", "golden", "--baseline", ""])
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert "with no baseline: none given (BASELINE=)" in out and "no-fall check: skipped, no baseline given (BASELINE=)" in out
    assert "passed: every post-load check against the pack's gates" in out and "passed: no margin fell" not in out


def test_with_neither_gates_nor_a_baseline_there_is_nothing_to_check_against(tmp_path, capsys, monkeypatch):
    build = scratch_build(tmp_path / "build", graph=False)
    write_registry(build, baseline=None)
    monkeypatch.setattr(margin, "thresholds", lambda prof: None)
    with running_as(build):
        code = margin.main(["--repeats", "1"])
    err = capsys.readouterr().err
    assert code == 2 and "--baseline" in err and "manifest.gates" in err and "nothing to check against" in err, err
    assert not (run_of(build) / "runs").exists()


def test_the_gates_are_on_the_golden_set_so_a_run_that_leaves_it_out_cannot_check_them(tmp_path, capsys):
    build = scratch_build(tmp_path / "build", graph=False)
    code, out, err = run_check(build, ["--repeats", "1", "--sets", "heldout"], capsys)
    assert code == 2 and "the golden set" in err and "--sets" in err, err


def test_make_margin_runs_the_check():
    mk = (DEMO / "Makefile").read_text()
    assert re.search(r"^\.PHONY:.*\bmargin\b", mk, re.M)
    rule = mk.split("\nmargin:", 1)[1].split("\n\n", 1)[0]
    assert rule.split() == ["$(KGE)", "margin", "$(KGE_ARGS)"], rule
    from tests.unit.test_cli import planned
    assert f"kge.eval.margin --repeats 3 --baseline  --hash {'ab' * 32}" in planned("margin", "BASELINE=", "HASH=" + "ab" * 32)
    assert "margin" in mk.split("\n\n", 1)[0], "the header names it among what needs the API stopped"
    said = mk.split("\nmargin:", 1)[0].rsplit("\n\n", 1)[1]
    for needed in ("gates", "manifest.gates", "GATE", "held-out", "off-tier"):
        assert needed in said, needed
    recipe = [line for line in rule.splitlines()[1:] if line.startswith("\t")]
    assert recipe and not any(line.lstrip("\t@+").startswith("-") or "||" in line or "; true" in line for line in recipe), rule
    assert not re.search(r"^\.IGNORE\b|--ignore-errors|^MAKEFLAGS.*\s-\w*i", mk, re.M)


def test_the_readme_says_when_to_run_the_check_and_what_a_failure_means():
    readme = (DEMO / "README.md").read_text()
    section = readme.split("## The A/B harness", 1)[1].split("\n## ", 1)[0]
    for needed in ("make margin", "make graph", "make pipeline", "treatment less control", "spread across repeats",
                   "fell by more than", "no longer measured", "pass rate", "make baseline RUN=", "exits", "on rules", "sha256",
                   "rules beside rules", "asked as the same principal", "a steward decision the runtime overlay keeps",
                   "`make reset-overlay`"):
        assert needed in section, needed
    assert ("Only like with like is put beside it: an arm on the mode the baseline's same arm ran on (rules beside rules), in "
            "a run asked as the baseline's principal, on the baseline's profile.") in section
    assert "`MODEL_PROVIDER=mock make ab` puts the presets, on rules, beside the baseline, and the agent's arms, on mock, not" in section
    walk = readme.split("## The steward walkthrough", 1)[1].split("\n## ", 1)[0]
    for needed in ("G041", "H014", "`make graph`", "`make reset-overlay`", "`make margin` fails"):
        assert needed in walk, needed
    assert "discards" not in section and "discards" not in walk
    assert "`kge/eval/margin.py`, exits 0" in section and "`python3 -m kge.eval.margin --baseline " in section
    assert "`make margin` shows a 1 or a 2 as `Error 1` or `Error 2` and itself exits 2 on either" in section
    assert ("It exits 2 when it cannot check: no baseline it can read with a margin recorded on rules, or one of another "
            "profile's, a profile with no estate or no graph") in section, "a broken input is a 2, never the 1 of a margin that fell"
    assert "`make margin` exits 0" not in section and not re.search(r"`make margin`[^.]*\bexits 1\b", section)
    assert "make margin" in readme.split("## Running it", 1)[1]
    for needed in ("`manifest.gates`", "`gates.json`", "V01", "V02", "V03", "V04", "golden_min_pass", "control_margin",
                   "never fails", "`GATE`", "a reload", "no baseline"):
        assert needed in section, needed
    g = json.loads(B40.read_text())["margin"]["golden"]["all"]
    pts = {m: round(g[m]["mean"] * 100) for m in ("pass_rate", "figure_correct", "behaviour_class", "tier_correct")}
    assert (f"ahead by {pts['pass_rate']} points on pass rate, {pts['figure_correct']} on figure correct, {pts['behaviour_class']} "
            f"on behaviour class and {pts['tier_correct']} on tier-correct citations") in section
