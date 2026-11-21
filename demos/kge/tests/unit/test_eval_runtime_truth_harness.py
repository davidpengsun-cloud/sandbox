"""The A/B harness, its report, eval post and the test drive's golden check judge each item by the one rule (#565):
the item's approved reference SQL, run now on the runtime the answer ran on; a draft or an expectation not available
there is reported apart, never a pass or a failure. With only the reference runtime, records are as before."""
import copy
import json
from types import SimpleNamespace

import pytest

from kge.eval import harness, report, stages
from kge.eval.run_golden import score
from kge.ports import QueryRefused
from kge.services.onboard import test_drive_run

APPROVED = {"reference_sql_approved_by": "A steward", "reference_sql_approved_on": "2026-01-05"}
OTHER = "other_rt"


def figure_item(item_id, sql=None, approved=False, figure=10.0):
    item = {"id": item_id, "kind": "figure", "question": f"how much {item_id}", "expect": {"figure": figure}}
    if sql:
        item["reference_sql"] = sql
    if approved:
        item.update(APPROVED)
    return item


ITEMS = [
    figure_item("A1", "SELECT 10", approved=True, figure=99.0),
    figure_item("A2", "SELECT broken", approved=True),
    figure_item("A3", "SELECT 10"),
    {"id": "A4", "kind": "abstain", "question": "who is the ceo", "expect": {}},
]


class Warehouses:
    """A stand-in warehouse set: approved SQL gives its rows, or is refused on the runtime asked."""

    def __init__(self):
        self.asked = []

    def resolve_reference_sql(self, sql, *, unit=None, runtimes=None, runtime_id=None, readable_units=None):
        self.asked.append((sql, unit, runtime_id, tuple(sorted(readable_units or ()))))
        if "broken" in sql:
            raise QueryRefused(f"no such table on {runtime_id}")
        return {"figure": 10, "sql": sql, "runtime": {"id": runtime_id}, "columns": ["v"], "rows": [[10]]}


class FakeArms:
    """Arms answering every figure question with 10 on runtime, and abstaining otherwise."""

    def __init__(self, runtime=OTHER, w=None):
        self.e = SimpleNamespace(w=w if w is not None else Warehouses(), UNIT_ORDER=["unit_a"],
                                 g=SimpleNamespace(readable_units=lambda who: {"unit_a"}))
        self.runtime, self.asked = runtime, []

    def about(self):
        return {}

    def describe(self, arm):
        return {"arm": arm, "side": "preset", "path": "answer", "mode": "rules", "config": arm, "stages": {}}

    def ask(self, arm, question, principal, runtimes=None):
        self.asked.append((question, runtimes))
        rt = {"runtime": {"id": self.runtime}} if self.runtime else {}
        if "ceo" in question:
            return {"behaviour": "abstain", "reason": "not in the estate", **rt}
        return {"behaviour": "figure", "figure": 10.0, **rt}


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(harness.metrics, "load_landing", lambda *args: {})
    monkeypatch.setattr(harness.metrics, "prices", lambda *args: {})


def ask_one(arms, item, cache=None):
    return harness.one(arms, "graph", item, "golden", 1, "run", "reader",
                       {"side": "preset", "mode": "rules", "config": "graph"}, {}, {}, set(), cache=cache)


def test_one_scores_approved_sql_on_the_answers_runtime_and_reports_the_rest_apart(quiet):
    arms = FakeArms()
    got = {item["id"]: ask_one(arms, item, {}) for item in ITEMS}
    assert got["A1"]["ok"] is True and "apart" not in got["A1"] and got["A1"]["runtime"] == OTHER
    assert got["A1"]["measures"]["figure_ok"] is True
    assert got["A2"]["apart"] == "unavailable" and got["A2"]["ok"] is None
    assert got["A2"]["why"].startswith(f"expectation not available on {OTHER}: no such table")
    assert got["A3"]["apart"] == "draft" and got["A3"]["ok"] is None and got["A3"]["why"].startswith("draft:")
    assert got["A4"]["ok"] is True and "apart" not in got["A4"]
    assert ("SELECT 10", "unit_a", OTHER, ("unit_a",)) in arms.e.w.asked


def test_one_asks_on_the_items_own_runtimes_and_judges_on_them(quiet):
    arms = FakeArms(runtime=None)
    item = {**figure_item("R1", "SELECT 10", approved=True), "runtimes": [OTHER]}
    rec = ask_one(arms, item, {})
    assert arms.asked == [(item["question"], [OTHER])]
    assert rec["ok"] is True and arms.e.w.asked[0][2] is None


def test_an_arm_without_warehouses_degrades_and_an_error_stays_a_fail(quiet):
    arms = FakeArms()
    arms.e = SimpleNamespace()
    assert ask_one(arms, ITEMS[3])["ok"] is True
    rec = ask_one(arms, ITEMS[1])
    assert rec["apart"] == "unavailable" and "no warehouse" in rec["why"]
    arms.runtime = None
    assert ask_one(arms, ITEMS[0])["ok"] is False

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    arms.ask = broken
    rec = ask_one(arms, ITEMS[0])
    assert rec["ok"] is False and "apart" not in rec and rec["error"].startswith("RuntimeError")


def test_reference_only_records_score_as_before(quiet):
    arms = FakeArms(runtime=None)
    legacy = [figure_item("L1", figure=10.0), figure_item("L2", figure=11.0), ITEMS[3]]
    for item in legacy:
        rec = ask_one(arms, item, {})
        ok, why = score(item, arms.ask("graph", item["question"], "reader"), set())
        assert (rec["ok"], rec["why"], rec.get("apart"), rec["runtime"]) == (ok, why, None, "reference")
    assert arms.e.w.asked == []


def run(tmp_path, monkeypatch, arms, items):
    monkeypatch.setattr(harness, "load", lambda *args: {"golden": items})
    monkeypatch.setattr(harness, "describe_sets", lambda *args: {"golden": {"items": len(items), "size": len(items), "version": "v2"}})
    monkeypatch.setattr(harness, "estate", lambda *args: {})
    monkeypatch.setattr(harness, "uncertified_ids", lambda *args: set())
    monkeypatch.setattr(harness.setfiles, "declared", lambda *args: {})
    profile = SimpleNamespace(id="test", estate=lambda: tmp_path, asking=lambda who: "reader",
                              landing=tmp_path, paths=lambda: {"pack": tmp_path / "pack.yaml"})
    lines = []
    folder = harness.run(profile, arms, ["graph"], ["golden"], 1, out=tmp_path / "runs", echo=lines.append)
    return folder, lines


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_run_writes_apart_items_beside_the_records_and_the_report_lists_them(tmp_path, monkeypatch, quiet):
    folder, lines = run(tmp_path, monkeypatch, FakeArms(), ITEMS)
    scored, held = rows(folder / "records.jsonl"), rows(folder / "apart.jsonl")
    assert [r["id"] for r in scored] == ["A1", "A4"] and all(isinstance(r["ok"], bool) for r in scored)
    assert [(r["id"], r["apart"]) for r in held] == [("A2", "unavailable"), ("A3", "draft")]
    assert "2 of 2" in lines[0] and "2 reported apart" in lines[0]
    md = (folder / "report.md").read_text()
    assert "## Reported apart" in md and "neither passes nor failures" in md
    assert f"expectation not available on {OTHER}" in md and "draft: " in md
    assert "Reported apart" in (folder / "report.html").read_text()
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["sets"]["golden"]["graph"]["all"]["passed"]["mean"] == 2


def test_a_run_with_nothing_apart_writes_no_apart_file_and_an_unchanged_report(tmp_path, monkeypatch, quiet):
    folder, lines = run(tmp_path, monkeypatch, FakeArms(runtime=None), [figure_item("L1"), ITEMS[3]])
    assert not (folder / "apart.jsonl").exists() and "apart" not in lines[0]
    manifest, records, base = report.load(folder)
    assert (folder / "report.md").read_text() == report.markdown(manifest, records, base)
    assert "Reported apart" not in (folder / "report.md").read_text()


def test_eval_post_keeps_and_compares_apart_records(tmp_path, monkeypatch, quiet):
    folder, _ = run(tmp_path, monkeypatch, FakeArms(), ITEMS)
    saved = {}
    monkeypatch.setattr(stages.released, "write_evaluation", lambda where, name, result: saved.update({name: result}))
    monkeypatch.setattr(stages.harness, "load", lambda *args: {})
    monkeypatch.setattr(stages, "grades", lambda *args: {"status": "not applicable", "scores": {}})
    monkeypatch.setattr(stages, "uncertified_ids", lambda *args: set())
    prof = SimpleNamespace(estate=lambda: tmp_path, model_hash=lambda: "h", graph_path=lambda: tmp_path / "g" / "graph")
    pre = stages.keep_pre(prof, folder, True)
    assert sorted(r["id"] for r in pre["records"]) == ["A1", "A2", "A3", "A4"]
    assert all("A2" not in name and "A3" not in name for name in pre["must_pass"])
    assert stages.compare(pre, copy.deepcopy(pre["records"]))["passed"]
    moved = copy.deepcopy(pre["records"])
    unavailable = next(r for r in moved if r["id"] == "A2")
    unavailable.update(ok=False, why="figure None != 10.0")
    unavailable.pop("apart")
    result = stages.compare(pre, moved)
    assert not result["passed"]
    assert any(d["question"][1] == "A2" and "apart" in d["fields"] and "ok" in d["fields"] for d in result["differences"])


def drive(monkeypatch, tmp_path, items, held=(), runtime=OTHER):
    engine = FakeArms(runtime=runtime)
    engine.e.answer = lambda question, principal, arm_name, runtimes=None: engine.ask(arm_name, question, principal, runtimes)
    engine.e.close = lambda: None
    (tmp_path / "golden.json").write_text("{}")
    if held:
        (tmp_path / "heldout.json").write_text("{}")
    monkeypatch.setattr(test_drive_run, "client", lambda: "demo")
    monkeypatch.setattr(test_drive_run, "resolve", lambda name: SimpleNamespace(estate=lambda: tmp_path, asking=lambda who: "reader"))
    monkeypatch.setattr(test_drive_run.Engine, "serving", staticmethod(lambda prof: engine.e))
    monkeypatch.setattr(test_drive_run, "uncertified_ids", lambda *args: set())
    monkeypatch.setattr(test_drive_run.sets, "items", lambda path: list(held) if path.name == "heldout.json" else list(items))


def test_test_drive_reports_draft_and_unavailable_apart_and_counts_only_scored_items(monkeypatch, tmp_path):
    drive(monkeypatch, tmp_path, ITEMS, held=[ITEMS[2]])
    detail = test_drive_run.golden()
    assert (detail["passed"], detail["of"], detail["failures"]) == (2, 2, [])
    assert [(a["id"], a["status"]) for a in detail["apart"]] == [("A2", "unavailable"), ("A3", "draft")]
    assert detail["held_out"]["of"] == 0 and detail["held_out"]["apart"][0]["id"] == "A3"
    result = test_drive_run.golden_check(rep := {})
    assert result.result == test_drive_run.PASSED and "3 reported apart" in result.detail
    lines = test_drive_run.markdown({"title": "t", "client": "demo", "time": "now", "result": "passed", "preflight": [],
                                     "steps": [], "settings": {}, "remove_before_rerun": [], "golden": rep["golden"]})
    assert "Reported apart, neither passed nor failed" in lines and "- A3: draft: draft:" in lines


def test_test_drive_without_apart_items_keeps_its_shape(monkeypatch, tmp_path):
    drive(monkeypatch, tmp_path, [figure_item("L1", figure=11.0), ITEMS[3]], runtime=None)
    detail = test_drive_run.golden()
    assert set(detail) == {"passed", "of", "failures", "held_out"} and set(detail["held_out"]) == {"passed", "of", "failures"}
    assert (detail["passed"], detail["of"], detail["failures"][0]["id"]) == (1, 2, "L1")


class ArmRuntimes(FakeArms):
    """Arms that answer on a runtime of their own: graph on the other runtime, schema naming none."""

    def ask(self, arm, question, principal, runtimes=None):
        self.runtime = OTHER if arm == "graph" else None
        return super().ask(arm, question, principal, runtimes)


def run_arms(tmp_path, monkeypatch, arms, sets, repeats=1):
    monkeypatch.setattr(harness, "load", lambda *args: sets)
    monkeypatch.setattr(harness, "describe_sets", lambda *args: {s: {"items": len(q), "size": len(q), "version": "v2"} for s, q in sets.items()})
    monkeypatch.setattr(harness, "estate", lambda *args: {})
    monkeypatch.setattr(harness, "uncertified_ids", lambda *args: set())
    monkeypatch.setattr(harness.setfiles, "declared", lambda *args: {})
    profile = SimpleNamespace(id="test", estate=lambda: tmp_path, asking=lambda who: "reader",
                              landing=tmp_path, paths=lambda: {"pack": tmp_path / "pack.yaml"})
    lines = []
    folder = harness.run(profile, arms, ["graph", "schema"], list(sets), repeats, out=tmp_path / "runs", echo=lines.append)
    return folder, lines


def test_an_item_one_arm_sets_apart_leaves_every_arms_scores_so_the_arms_are_put_on_the_same_items(tmp_path, monkeypatch, quiet):
    folder, lines = run_arms(tmp_path, monkeypatch, ArmRuntimes(), {"golden": [figure_item("A3", "SELECT 10"), ITEMS[3]]})
    scored, held = rows(folder / "records.jsonl"), rows(folder / "apart.jsonl")
    assert [(r["arm"], r["id"]) for r in scored] == [("graph", "A4"), ("schema", "A4")]
    assert [(r["arm"], r["id"], r.get("apart"), r.get("apart_on")) for r in held] == [
        ("graph", "A3", "draft", None), ("schema", "A3", None, ["graph"])]
    alone = ask_one(ArmRuntimes(), figure_item("A3", "SELECT 10"), {})
    assert {k: held[0][k] for k in ("ok", "why", "apart")} == {k: alone[k] for k in ("ok", "why", "apart")}
    schema = harness.one(ArmRuntimes(), "schema", figure_item("A3", "SELECT 10"), "golden", 1, "run", "reader",
                         {"side": "preset", "mode": "rules", "config": "schema"}, {}, {}, set(), cache={})
    assert {k: held[1].get(k) for k in ("ok", "why", "apart")} == {k: schema.get(k) for k in ("ok", "why", "apart")}
    assert "scored, set apart on graph" in (folder / "report.md").read_text()
    assert all("1 of 1" in line and "1 reported apart" in line for line in lines)


def test_reference_only_records_keep_the_order_repeat_arm_set_item(tmp_path, monkeypatch, quiet):
    sets = {"golden": [figure_item("L1"), ITEMS[3]], "heldout": [figure_item("H1"), figure_item("H2", figure=3.0)]}
    folder, _ = run_arms(tmp_path, monkeypatch, FakeArms(runtime=None), sets, repeats=2)
    order = [(r["repeat"], r["arm"], r["set"], r["id"]) for r in rows(folder / "records.jsonl")]
    assert order == [(n, arm, s, i["id"]) for n in (1, 2) for arm in ("graph", "schema") for s, qs in sets.items() for i in qs]
    assert not (folder / "apart.jsonl").exists()


def test_eval_post_asking_each_record_again_alone_finds_an_item_apart_on_one_arm_unchanged(tmp_path, monkeypatch, quiet):
    sets = {"golden": [figure_item("A3", "SELECT 10"), ITEMS[3]]}
    folder, _ = run_arms(tmp_path, monkeypatch, ArmRuntimes(), sets)
    saved = {}
    monkeypatch.setattr(stages.released, "write_evaluation", lambda where, name, result: saved.update({name: result}))
    monkeypatch.setattr(stages.harness, "load", lambda *args: sets)
    monkeypatch.setattr(stages, "grades", lambda *args: {"status": "not applicable", "scores": {}})
    monkeypatch.setattr(stages, "uncertified_ids", lambda *args: set())
    prof = SimpleNamespace(estate=lambda: tmp_path, model_hash=lambda: "h", graph_path=lambda: tmp_path / "g" / "graph")
    pre = stages.keep_pre(prof, folder, True)
    items = {(s, i["id"]): i for s, qs in sets.items() for i in qs}
    truth, arms = {}, ArmRuntimes()
    post = [harness.one(arms, row["arm"], items[(row["set"], row["id"])], row["set"], row["repeat"], "eval-post", "reader",
                        {"side": "preset", "mode": "rules", "config": row["arm"]}, {}, {}, set(), cache=truth)
            for row in pre["records"]]
    result = stages.compare(pre, post)
    assert result["passed"] and result["differences"] == [], result["differences"]
