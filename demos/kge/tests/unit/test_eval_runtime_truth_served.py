"""#565: make eval, POST /eval and the job's eval judge every answer against its truth on the runtime it ran on, ask and
compute truth on one runtime list, report drafts and unavailable expectations apart, and evaluate the approved items
of the estate's further golden sets (the golden 20) beside the gated golden set. No built graph: a stand-in engine
whose answers name their runtime, and a stand-in warehouse whose reference SQL gives each runtime its own rows."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.ports import REFERENCE
from kge.eval import run_golden
from kge.ports import QueryRefused
from kge.services.quality import service as quality

LIVE = "live"
APPROVED = {"reference_sql_approved_by": "steward@example.com", "reference_sql_approved_on": "2026-11-19"}

TRUTH = {
    "SELECT total_a": {REFERENCE: 100.0, LIVE: 250.0},
    "SELECT total_b": {REFERENCE: 40.0, LIVE: 70.0},
    "SELECT total_c": {REFERENCE: 9.0},
    "SELECT nna": {REFERENCE: 11.0, LIVE: 13.0},
}

ANSWERS = {
    "Total A?": {REFERENCE: 100.0, LIVE: 250.0},
    "Total B?": {REFERENCE: 40.0, LIVE: 71.5},
    "Total C?": {REFERENCE: 9.0, LIVE: 9.0},
    "Total D?": {REFERENCE: 5.0, LIVE: 6.0},
    "Which shelf?": {REFERENCE: None, LIVE: None},
    "NNA?": {REFERENCE: 11.0, LIVE: 13.0},
    "NNA again?": {REFERENCE: 11.0, LIVE: 13.0},
}


class Warehouse:
    """Reference SQL run per runtime: its figure on that runtime, or refused where the runtime cannot run it."""

    versions = {"retail": 1}

    def __init__(self):
        self.calls: list[dict] = []

    def resolve_reference_sql(self, sql, *, unit=None, runtimes=None, runtime_id=None, readable_units=None):
        rt = runtime_id or (runtimes or [REFERENCE])[0]
        self.calls.append({"sql": sql, "runtime_id": runtime_id, "runtimes": runtimes})
        if rt not in TRUTH[sql]:
            raise QueryRefused(f"{rt}: no mapping for table total_c")
        return {"figure": TRUTH[sql][rt], "sql": sql, "runtime": {"id": rt}, "columns": ["v"], "rows": [[TRUTH[sql][rt]]]}


def item(iid: str, question: str, *, sql: str | None = None, approved: bool = False, figure: float | None = None,
         kind: str = "within", unit: str = "retail") -> dict:
    out = {"id": iid, "kind": kind, "question": question, "unit": unit,
           "expect": {"unit": unit, **({"figure": figure} if figure is not None else {})}}
    if sql:
        out["reference_sql"] = sql
    if approved:
        out.update(APPROVED)
    return out


GOLDEN = [
    item("A", "Total A?", sql="SELECT total_a", approved=True, figure=100.0),
    item("B", "Total B?", sql="SELECT total_b", approved=True, figure=40.0),
    item("C", "Total C?", sql="SELECT total_c", approved=True, figure=9.0),
    item("D", "Total D?", figure=5.0),
    {"id": "S", "kind": "shelf", "question": "Which shelf?", "expect": {"cite": []}},
]

SAGE = {"version": "v4", "items": [
    item("S06", "NNA?", sql="SELECT nna", approved=True, figure=11.0, unit="wealth"),
    item("S06_alt1", "NNA again?", sql="SELECT nna", approved=True, figure=11.0, unit="wealth"),
    item("S01", "Total D?", sql="SELECT total_b", figure=40.0, unit="wealth"),
]}


def estate(tmp_path: Path, *, further: bool = True) -> Path:
    build = tmp_path / "build"
    build.mkdir(parents=True)
    (build / "documents.json").write_text("[]")
    (build / "golden.json").write_text(json.dumps(GOLDEN))
    if further:
        (build / "sage_v1.json").write_text(json.dumps(SAGE))
        (build / "heldout_wealth_v1.json").write_text(json.dumps({"version": "v1", "items": [
            item("H1", "NNA?", sql="SELECT nna", approved=True, figure=11.0, unit="wealth")]}))
        (build / "golden_other_v1.json").write_text(json.dumps([item("O1", "Total D?", figure=5.0)]))
    return build


def engine(w: Warehouse, asked: list[tuple[str, list[str] | None]]):
    def answer(question, principal=None, arm_name="graph", runtimes=None, **kwargs):
        asked.append((question, runtimes))
        rt = (runtimes or [REFERENCE])[0]
        figure = ANSWERS[question][rt]
        if figure is None:
            return {"behaviour": "cite_certified", "cite": [], "runtime": {"id": rt}}
        return {"behaviour": "figure", "figure": figure, "unit": "wealth" if question.startswith("NNA") else "retail",
                "runtime": {"id": rt}}

    graph = SimpleNamespace(readable_units=lambda who: {"retail", "wealth"}, as_roles=nullcontext)
    return SimpleNamespace(g=graph, w=w, UNIT_ORDER=["retail", "wealth"], answer=answer, close=lambda: None)


def profile(build: Path, run: Path):
    prof = SimpleNamespace(estate=lambda: build, run_dir=lambda: run, id="test", client="test",
                           asking=lambda who: who or "reader")
    prof.serve = lambda: prof
    return prof


def test_judge_scores_on_the_answers_runtime_and_reports_apart():
    w = Warehouse()
    on_live = {"behaviour": "figure", "figure": 250.0, "unit": "retail", "runtime": {"id": LIVE}}
    verdict = run_golden.judge(GOLDEN[0], on_live, w=w, runtimes=[LIVE], cache={})
    assert {k: v for k, v in verdict.items() if k != "expected"} == {"status": "passed", "ok": True, "why": "", "runtime": LIVE}
    assert verdict["expected"]["expect"]["figure"] == 250.0
    wrong = run_golden.judge(GOLDEN[0], {**on_live, "figure": 100.0}, w=w, runtimes=[LIVE], cache={})
    assert (wrong["status"], wrong["ok"]) == ("failed", False)
    refused: list[dict] = []
    gap = run_golden.judge(GOLDEN[2], {**on_live, "figure": 9.0}, w=w, runtimes=[LIVE], cache={}, refused=refused)
    assert (gap["status"], gap["ok"]) == ("unavailable", None)
    assert gap["why"] == "expectation not available on live: live: no mapping for table total_c"
    assert refused == [{"id": "C", "runtime": LIVE, "reason": "live: no mapping for table total_c"}]
    draft = run_golden.judge(GOLDEN[3], {**on_live, "figure": 6.0}, w=w, runtimes=[LIVE], cache={})
    assert (draft["status"], draft["ok"]) == ("draft", None)


def test_service_eval_judges_each_answer_on_its_runtime(tmp_path):
    w, asked = Warehouse(), []
    build = estate(tmp_path)
    out = quality.evaluate(profile(build, tmp_path / "run"), engine(w, asked), "reader", arm="graph", runtimes=[LIVE])
    graph = out["graph"]
    status = {r["id"]: r["status"] for r in graph["rows"]}
    assert status == {"A": "passed", "B": "failed", "C": "unavailable", "D": "draft", "S": "passed"}
    assert graph["by_kind"]["within"] == {"passed": 1, "of": 2}
    assert graph["by_kind"]["shelf"] == {"passed": 1, "of": 1}
    assert [(a["id"], a["status"], a["runtime"]) for a in graph["apart"]] == [("C", "unavailable", LIVE), ("D", "draft", LIVE)]
    assert graph["apart"][0]["why"].startswith("expectation not available on live: ")
    assert [rts for _, rts in asked[:len(GOLDEN)]] == [[LIVE]] * len(GOLDEN)
    assert all(c["runtimes"] == [c["runtime_id"]] for c in w.calls)
    assert {c["sql"] for c in w.calls} == {"SELECT total_a", "SELECT total_b", "SELECT total_c", "SELECT nna"}
    assert out["refused"] == [{"id": "C", "runtime": LIVE, "reason": "live: no mapping for table total_c"}]
    approved = out["approved"]
    assert approved["sets"] == [{"name": "sage_v1", "version": "v4", "items": 2}]
    assert [r["id"] for r in approved["graph"]["rows"]] == ["S06", "S06_alt1"]
    assert approved["graph"]["by_kind"]["within"] == {"passed": 2, "of": 2}
    assert approved["graph"]["apart"] == []
    assert json.loads((tmp_path / "run" / "eval.json").read_text()) == out


def test_service_eval_reference_only_is_unchanged(tmp_path):
    w, asked = Warehouse(), []
    build = estate(tmp_path, further=False)
    out = quality.evaluate(profile(build, tmp_path / "run"), engine(w, asked), "reader", arm="graph")
    graph = out["graph"]
    assert {r["id"]: r["status"] for r in graph["rows"]} == {"A": "passed", "B": "passed", "C": "passed",
                                                              "D": "passed", "S": "passed"}
    assert graph["by_kind"]["within"] == {"passed": 4, "of": 4}
    assert graph["apart"] == []
    assert "approved" not in out and "runtimes" not in out
    assert all(rts is None for _, rts in asked)
    assert all(c["runtime_id"] == REFERENCE for c in w.calls)


def test_make_eval_judges_reports_apart_and_the_approved_sets(tmp_path, monkeypatch, capsys):
    w, asked = Warehouse(), []
    build = estate(tmp_path)
    prof = profile(build, tmp_path / "run")
    monkeypatch.setattr(run_golden, "resolve", lambda name: prof)
    monkeypatch.setattr(run_golden.Engine, "serving", lambda p: engine(w, asked))
    assert run_golden.main(["--arm", "graph", "--as", "reader", "--runtimes", LIVE]) == 0
    result = json.loads((prof.run_dir() / "eval.json").read_text())
    assert result["graph"]["within"] == {"passed": 1, "of": 2}
    assert result["graph"]["shelf"] == {"passed": 1, "of": 1}
    assert [(a["id"], a["status"]) for a in result["graph"]["apart"]] == [("C", "unavailable"), ("D", "draft")]
    assert result["approved"]["sets"] == [{"name": "sage_v1", "version": "v4", "items": 2}]
    assert result["approved"]["graph"] == {"within": {"passed": 2, "of": 2}}
    assert result["refused"] == [{"id": "C", "runtime": LIVE, "reason": "live: no mapping for table total_c"}]
    assert [rts for _, rts in asked[:len(GOLDEN)]] == [[LIVE]] * len(GOLDEN)
    assert [rts for _, rts in asked[-2:]] == [[LIVE]] * 2
    assert all(c["runtimes"] == [c["runtime_id"]] for c in w.calls)
    assert {d["id"] for d in result["divergences"]} == {"A", "B", "D"}
    printed = capsys.readouterr().out
    assert "graph: 2 reported apart" in printed
    assert "C [live] expectation not available on live: live: no mapping for table total_c (graph)" in printed
    assert "D [live] draft: it has no approved reference SQL" in printed
    assert "approved reference SQL (golden 20 and others), on each answer's runtime: sage_v1 (2)" in printed


def test_make_eval_reference_only_keeps_its_keys(tmp_path, monkeypatch, capsys):
    w, asked = Warehouse(), []
    build = estate(tmp_path, further=False)
    prof = profile(build, tmp_path / "run")
    monkeypatch.setattr(run_golden, "resolve", lambda name: prof)
    monkeypatch.setattr(run_golden.Engine, "serving", lambda p: engine(w, asked))
    assert run_golden.main(["--arm", "graph", "--as", "reader"]) == 0
    result = json.loads((prof.run_dir() / "eval.json").read_text())
    assert result == {"graph": {"within": {"passed": 4, "of": 4}, "shelf": {"passed": 1, "of": 1}},
                      "refused": [], "heldout": {}}
    assert "reported apart" not in capsys.readouterr().out


def test_compare_runtimes_rows_carry_status(tmp_path):
    w, asked = Warehouse(), []
    eng = engine(w, asked)
    cmp = run_golden.compare_runtimes(GOLDEN[:4], lambda it, rts: eng.answer(it["question"], runtimes=rts), [LIVE], w=w)
    assert cmp["runtimes"] == [REFERENCE, LIVE]
    assert cmp["by_runtime"][REFERENCE]["by_kind"] == {"within": {"passed": 4, "of": 4}}
    assert cmp["by_runtime"][LIVE]["by_kind"] == {"within": {"passed": 1, "of": 2}}
    assert [r["status"] for r in cmp["by_runtime"][LIVE]["rows"]] == ["passed", "failed", "unavailable", "draft"]
    assert {d["id"] for d in cmp["divergences"]} == {"A", "B", "D"}


@pytest.mark.parametrize("units", [None, ["retail", "wealth"], ["retail"]])
def test_approved_items_takes_further_sets_with_approved_sql(tmp_path, units):
    build = estate(tmp_path)
    found = run_golden.approved_items(build, units)
    if units == ["retail"]:
        assert found == []
    else:
        assert [(s["name"], [i["id"] for i in s["items"]]) for s in found] == [("sage_v1", ["S06", "S06_alt1"])]
    assert run_golden.approved_items(estate(tmp_path / "bare", further=False)) == []
