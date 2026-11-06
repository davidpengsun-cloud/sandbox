"""Asking several questions at once (kge/eval/asking.py, EVAL_CONCURRENCY) gives what asking them one at a time gives: the
A/B harness's records, in the same order, on rules and on the mock, and make parity's results.

    python -m pytest tests/integration/test_eval_concurrency.py -q

Only what differs between two runs one at a time is set aside: the run's and each trace's id and start, and the
milliseconds. Opens the graph directly: stop the API first.
"""
from __future__ import annotations

from kge.adapters.model import LLM
from kge.eval import harness, parity
from kge.services.ask.engine import runtrace
from tests.scratch import BUILD, profile_of

ARMS = list(harness.ARM_NAMES)
SETS = ["golden", "heldout"]


def steady(record: dict) -> dict:
    """The record less what two one-at-a-time runs differ in: ids, start times and milliseconds."""
    out = {k: v for k, v in record.items() if k != "run_id"}
    out["measures"] = {k: v for k, v in record["measures"].items() if k != "ms"}
    rt = dict(record["run_trace"] or {})
    for k in ("run_id", "started_at", "total_ms"):
        rt.pop(k, None)
    rt["stages"] = [{k: v for k, v in s.items() if k != "ms"} for s in rt.get("stages") or []]
    rt["llm_calls"] = [{k: v for k, v in c.items() if k != "ms"} for c in rt.get("llm_calls") or []]
    out["run_trace"] = rt
    return out


def records(eng, arms, names: list[str], workers: str, tmp_path, monkeypatch) -> list[dict]:
    monkeypatch.setenv("EVAL_CONCURRENCY", workers)
    out = harness.run(profile_of(BUILD), arms, names, SETS, repeats=1, out=tmp_path / workers, echo=lambda *a: None)
    return [steady(r) for r in runtrace.read_jsonl(out / "records.jsonl")]


def test_eight_at_once_on_rules_writes_the_records_one_at_a_time_writes(eng, tmp_path, monkeypatch):
    arms = harness.Arms(eng, llm=None)
    one = records(eng, arms, ARMS, "1", tmp_path, monkeypatch)
    eight = records(eng, arms, ARMS, "8", tmp_path, monkeypatch)
    assert len(one) == len(ARMS) * 61
    assert [(r["arm"], r["set"], r["id"]) for r in eight] == [(r["arm"], r["set"], r["id"]) for r in one]
    for a, b in zip(one, eight):
        assert a == b, (a["arm"], a["id"])


def test_eight_at_once_on_the_mock_writes_the_records_one_at_a_time_writes(eng, tmp_path, monkeypatch):
    monkeypatch.setattr(LLM, "_post", staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("the mock made a network call"))))
    arms = harness.Arms(eng, llm=LLM("mock", "mock", mode="careful"))
    one = records(eng, arms, ["control", "treatment"], "1", tmp_path, monkeypatch)
    eight = records(eng, arms, ["control", "treatment"], "8", tmp_path, monkeypatch)
    assert {r["mode"] for r in one} == {"mock"} and len(one) == 2 * 61
    for a, b in zip(one, eight, strict=True):
        assert a == b, (a["arm"], a["id"])


def test_parity_s_results_are_the_same_at_once_as_one_at_a_time(eng, monkeypatch):
    monkeypatch.setenv("EVAL_CONCURRENCY", "1")
    serial = parity.results(eng, BUILD, "analyst_enterprise")
    monkeypatch.setenv("EVAL_CONCURRENCY", "8")
    assert list(parity.results(eng, BUILD, "analyst_enterprise").items()) == list(serial.items())
