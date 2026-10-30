"""The A/B harness (kge/eval/harness.py), its measures (kge/eval/metrics.py) and its report (kge/eval/report.py).

    .venv/bin/python -m pytest tests/integration/test_harness.py -q

The harness runs each arm (the three presets and the reference agent's two) on
each set, N times, and writes one record per question with its run trace under
build/runs/<run_id>/, then a report in Markdown and HTML with each measure's
mean and spread across the repeats. The golden scorer decides pass or fail
unchanged; the measures sit beside it.

Offline: the agent arms run on rules, or on the mock model where a test says
so, and nothing here is a model result. Opens the graph directly: stop the API first.

FEW is a figure, a sum across units, a definition, a new term and a paraphrase.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.core import AGENT_ARMS, PRESETS
from kge.eval import harness, metrics, report, sets
from kge.eval.run_golden import score, uncertified_ids
from kge.adapters.model import LLM
from tests.scratch import BUILD, profile_of, run_of, running_as, scratch_build

DEMO = Path(__file__).resolve().parents[2]
FEW = ["G001", "G022", "G031", "G041", "H001"]
ARMS = list(PRESETS) + list(AGENT_ARMS)
LANDING = {"GOLD": {"asset_id": "GOLD", "ai_ready": "yes", "tier": "gold"}, "SILVER": {"asset_id": "SILVER", "ai_ready": "yes", "tier": "silver"},
           "BRONZE": {"asset_id": "BRONZE", "ai_ready": "yes", "tier": "bronze"}, "HELD": {"asset_id": "HELD", "ai_ready": "no", "tier": None}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No model is called: any HTTP post fails the test. No prices unless a test sets them."""
    def no_network(*a, **k):
        raise AssertionError("a model call left the process")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    for name in ("MODEL_PRICE_IN", "MODEL_PRICE_OUT", "MODEL_SEED"):
        monkeypatch.delenv(name, raising=False)


def trace(**more) -> dict:
    return {"total_ms": 10.0, "tokens": {"in": 0, "out": 0}, "llm_calls": [], "number_check": None, **more}


def item(kind: str, **expect) -> dict:
    return {"id": "T1", "kind": kind, "question": "Total retail revenue for June 2025", "expect": expect}


def test_the_expected_behaviour_and_its_class():
    """The owner's four classes are answer, parts, refuse and abstain; summing parts that must not be added is the
    graph-off failure, so it is the wrong class, not a near miss."""
    assert metrics.expected_behaviour(item("within", figure="1")) == "figure"
    assert metrics.expected_behaviour(item("cross", behaviour="parts_with_reason")) == "parts_with_reason"
    assert metrics.expected_behaviour(item("shelf", cite=["X"])) == "cite_certified"
    assert metrics.expected_behaviour(item("abstain")) is None
    assert {b: metrics.behaviour_class(b) for b in ("figure", "sum_with_verdict", "sum_without_verdict", "cite_certified", "cite",
                                                     "parts_with_reason", "refuse", "blocked_access", "abstain", "abstain_route_steward")} == {
        "figure": "answer", "sum_with_verdict": "answer", "sum_without_verdict": "answer", "cite_certified": "answer", "cite": "answer",
        "parts_with_reason": "parts", "refuse": "refuse", "blocked_access": "refuse", "abstain": "abstain", "abstain_route_steward": "abstain"}
    assert set(metrics.CLASSES.values()) == {"answer", "parts", "refuse", "abstain"}
    m = metrics.behaviour(item("abstain"), {"behaviour": "refuse"})
    assert m == {"expected": None, "got": "refuse", "exact": True, "class": True}
    for got in ("sum_with_verdict", "sum_without_verdict"):
        m = metrics.behaviour(item("cross", behaviour="parts_with_reason"), {"behaviour": got})
        assert m["exact"] is False and m["class"] is False, got
    m = metrics.behaviour(item("cross", behaviour="sum_with_verdict"), {"behaviour": "sum_without_verdict"})
    assert m["exact"] is False and m["class"] is True, "a sum, without the verdict"
    assert metrics.behaviour(item("cross", behaviour="refuse"), {"behaviour": "abstain"})["class"] is False
    assert metrics.behaviour(item("cross", behaviour="refuse"), {"behaviour": "blocked_access"})["class"] is True
    m = metrics.behaviour(item("abstain", behaviour="abstain"), {"behaviour": "refuse"})
    assert m["exact"] is False and m["class"] is True
    assert metrics.behaviour(item("abstain", behaviour="refuse"), {"behaviour": "figure"})["class"] is False
    assert metrics.behaviour(item("within", figure="1"), {"behaviour": "abstain"})["class"] is False


def test_figure_correctness_is_the_value_alone_and_null_where_none_is_agreed():
    within = item("within", figure="13287.67", unit="retail", columns=["monthly_revenue.total_revenue_usd"], join_path=[])
    assert metrics.figure_ok(within, {"behaviour": "figure", "figure": 13287.67, "column": "some.other"}) is True
    assert metrics.figure_ok(within, {"behaviour": "figure", "figure": 336.0}) is False
    assert metrics.figure_ok(within, {"behaviour": "abstain"}) is False
    parts = item("cross", behaviour="parts_with_reason", parts={"retail": "1.5", "sme": "2"})
    assert metrics.figure_ok(parts, {"parts": {"retail": 1.5, "sme": 2.0}}) is True
    assert metrics.figure_ok(parts, {"parts": {"retail": 1.5}}) is False
    assert metrics.figure_ok(parts, {"parts": [{"unit": "retail"}]}) is False
    assert metrics.figure_ok(item("shelf", cite=["X"]), {"behaviour": "cite_certified"}) is None


def test_the_gate_reads_the_planners_guards_and_the_agents_attempts():
    """A retry that runs no query reaches no gate but is still a try, as the trace's plan calls count it, and refuses
    nothing."""
    ok, no = {"passed": True, "problems": []}, {"passed": False, "problems": ["restricted"]}
    assert metrics.gate({"parts": [{"unit": "retail", "guard": ok}]}) == {"queries": 1, "first_try": True, "tries": 1, "passed": True}
    assert metrics.gate({"plan": [{"guard": ok}, {"guard": no}]}) == {"queries": 2, "first_try": False, "tries": 1, "passed": False}
    tries = [{"try": 1, "unit": "retail", **no}, {"try": 2, "unit": "retail", **ok}]
    assert metrics.gate({"attempts": tries}) == {"queries": 1, "first_try": False, "tries": 2, "passed": True}
    replanned = {"attempts": tries[:1], "run_trace": trace(llm_calls=[{"stage": "plan"}, {"stage": "plan"}, {"stage": "render"}])}
    assert metrics.gate(replanned) == {"queries": 1, "first_try": False, "tries": 2, "passed": True}
    assert metrics.gate({"attempts": [], "parts": {"retail": 1.0}}) is None
    assert metrics.gate({"behaviour": "abstain"}) is None


def test_a_citation_is_tier_correct_when_upstream_landed_it_ai_ready_gold_or_silver():
    m = metrics.citations({"cite": ["GOLD", "SILVER", "BRONZE", "HELD", "NOWHERE"]}, LANDING)
    assert m == {"cited": 5, "tier_correct": 2, "off_tier": ["BRONZE", "HELD", "NOWHERE"]}
    assert metrics.citations({"behaviour": "figure"}, LANDING) == {"cited": 0, "tier_correct": 0, "off_tier": []}


def test_unsupported_numbers_are_what_the_number_check_caught_and_what_reached_the_reader():
    """Model wording that reached the reader is checked again by the agent's own rule; whether a model or the mock
    worded it is the run trace's to say, not the label's prefix."""
    assert metrics.numbers({"run_trace": trace()}) == {"checked": False, "caught": None, "bad_markers": None, "shown": None}
    checked = {"run_trace": trace(number_check={"passed": False, "stray_numbers": 2, "bad_markers": 1}), "phrased_by": "template (…)"}
    assert metrics.numbers(checked) == {"checked": True, "caught": 2, "bad_markers": 1, "shown": 0}
    shown = {"question": "Total retail revenue for June 2025", "figure": 13287.67, "figures": [{"value": 13287.67}],
             "citations": [{"n": 1, "version": "3.2", "title": "Retail data dictionary"}], "phrased_by": "mock:mock",
             "answer_text": "Retail revenue was 13,287.67 [1] in June 2025, up 4 on May.",
             "run_trace": trace(number_check={"passed": True, "stray_numbers": 0, "bad_markers": 0},
                                agent={"arm": "treatment", "mode": "mock", "prompt_versions": {}}, llm={"name": "mock:mock"})}
    assert metrics.numbers(shown)["shown"] == 1
    assert metrics.numbers({**shown, "phrased_by": "model:openai:gpt-x", "run_trace": {**shown["run_trace"], "agent": None}})["shown"] == 1
    assert metrics.numbers({**shown, "phrased_by": "template (model text carried numbers ...)"})["shown"] == 0
    for glued, n in (("down -3000 on May", 1), ("USD9999 below plan", 1), ("in a 100-200 band", 2), ("or .5 of plan", 1),
                     ("as EM-004 v3.2 says", 0)):
        said = {**shown, "answer_text": f"Retail revenue was 13,287.67 [1] in June 2025, {glued}.",
                "citations": [{"n": 1, "doc": "EM-004", "version": "3.2", "title": "Retail data dictionary"}]}
        assert metrics.numbers(said)["shown"] == n, glued


def test_cost_is_known_only_with_prices_and_counts(monkeypatch):
    """A model call with no price, or with a token count the provider did not report, has an unknown cost, never a free
    one."""
    assert metrics.prices() == {"in": None, "out": None, "currency": "USD", "per_tokens": 1_000_000}
    none = metrics.prices()
    assert metrics.cost(trace(), none) == 0.0
    called = trace(tokens={"in": 1000, "out": 200}, llm_calls=[{"stage": "plan"}])
    assert metrics.cost(called, none) is None
    monkeypatch.setenv("MODEL_PRICE_IN", "3")
    monkeypatch.setenv("MODEL_PRICE_OUT", "15")
    p = metrics.prices()
    assert metrics.cost(called, p) == pytest.approx(1000 * 3 / 1e6 + 200 * 15 / 1e6)
    assert metrics.cost(trace(tokens={"in": None, "out": 200}, llm_calls=[{}]), p) is None
    monkeypatch.setenv("MODEL_PRICE_IN", "three")
    with pytest.raises(ValueError, match="MODEL_PRICE_IN"):
        metrics.prices()


def test_the_env_example_copied_as_it_is_leaves_the_prices_unset():
    """An empty value with a comment after it is empty, not the comment: .env.example's MODEL_BASE_URL was once read as
    one."""
    from kge.pipeline.envfile import parse
    values = parse((DEMO / ".env.example").read_text())
    assert values["MODEL_PRICE_IN"] == values["MODEL_PRICE_OUT"] == values["MODEL_BASE_URL"] == ""
    assert parse("A=1 # one\nB=   # none\nC='#not a comment'\n") == {"A": "1", "B": "", "C": "#not a comment"}


def test_measure_puts_every_measure_in_one_record():
    within = item("within", figure="13287.67")
    ans = {"behaviour": "figure", "figure": 13287.67, "parts": [{"guard": {"passed": True}}], "cite": [],
           "run_trace": trace(total_ms=12.5, tokens={"in": 0, "out": 0})}
    m = metrics.measure(within, ans, LANDING, metrics.prices())
    assert m["figure_ok"] is True and m["behaviour"]["exact"] is True and m["gate"]["first_try"] is True
    assert m["citations"]["cited"] == 0 and m["numbers"]["checked"] is False
    assert m["ms"] == 12.5 and m["tokens"] == {"in": 0, "out": 0} and m["llm_calls"] == 0 and m["cost_usd"] == 0.0


def record(arm: str, repeat: int, ok: bool, kind: str = "within", set_name: str = "golden", id_: str = "G001", mode: str = "rules",
           ms: float = 10.0, cost: float | None = 0.0, **more) -> dict:
    m = {"figure_ok": ok if kind == "within" else None, "behaviour": {"expected": "figure", "got": "figure" if ok else "abstain", "exact": ok, "class": ok},
         "gate": {"queries": 1, "first_try": ok, "tries": 1 if ok else 2, "passed": ok}, "citations": {"cited": 1, "tier_correct": int(ok), "off_tier": []},
         "numbers": {"checked": False, "caught": None, "bad_markers": None, "shown": None}, "ms": ms, "tokens": {"in": 0, "out": 0},
         "llm_calls": 0, "cost_usd": cost}
    return {"run_id": "r", "repeat": repeat, "arm": arm, "mode": mode, "config": arm, "set": set_name, "id": id_, "kind": kind,
            "question": f"Question {id_} <b>", "ok": ok, "why": "", "measures": m, **more}


def manifest(arms=("graph", "treatment"), modes=("rules", "rules"), repeats=2, llm=None) -> dict:
    return {"run_id": "r", "started_at": "2026-10-11T00:00:00+00:00", "finished_at": "2026-10-11T00:01:00+00:00", "principal": "analyst_enterprise",
            "repeats": repeats, "sets": {"golden": 1}, "profile": "meridian", "graph": {"model": "a" * 64, "overlay": "b" * 64}, "llm": llm,
            "prices": {"in": None, "out": None, "currency": "USD", "per_tokens": 1_000_000},
            "arms": [{"arm": a, "side": harness.SIDES[a], "path": "answer" if a in PRESETS else "agent", "mode": m, "config": a} for a, m in zip(arms, modes)]}


def test_the_summary_gives_each_measure_its_mean_and_spread_across_repeats():
    """A measure unknown in any repeat is unknown, never averaged over the repeats that know it."""
    recs = [record("graph", 1, True, ms=10), record("graph", 2, False, ms=30), record("treatment", 1, True), record("treatment", 2, True)]
    s = report.summarise(recs)["golden"]
    g = s["graph"]
    assert g["kinds"]["within"]["of"] == 1
    assert g["kinds"]["within"]["passed"] == {"mean": 0.5, "min": 0, "max": 1, "sd": pytest.approx(0.7071, abs=1e-3), "n": 2}
    assert g["all"]["figure_correct"]["mean"] == 0.5 and g["all"]["ms_mean"]["mean"] == 20.0 and g["all"]["ms_mean"]["max"] == 30.0
    assert g["all"]["gate_first_try"]["min"] == 0.0 and g["all"]["retries"]["max"] == 1.0
    assert s["treatment"]["kinds"]["within"]["passed"]["sd"] == 0.0
    unknown = report.summarise([record("graph", 1, True, cost=None), record("graph", 2, True, cost=0.5)])
    assert unknown["golden"]["graph"]["all"]["cost_usd"] is None


def test_the_report_says_what_ran_each_arm_and_marks_what_is_unstable_or_unknown():
    recs = [record("graph", 1, True), record("graph", 2, False), record("treatment", 1, True, cost=None), record("treatment", 2, True, cost=None)]
    md = report.markdown(manifest(), recs)
    assert "No model was run" in md and "| graph |" in md and "| treatment |" in md
    assert "unknown" in md
    unstable = md.split("## Questions whose result changed across repeats", 1)[1]
    assert "G001" in unstable.split("\n## ", 1)[0]
    page = report.html(manifest(), recs)
    assert page.startswith("<!doctype html>") and "<title>" in page and "No model was run" in page
    assert "Question G001 &lt;b&gt;" in page and "Question G001 <b>" not in page, "question text is escaped"


def test_a_mock_run_is_labelled_mock_and_said_to_prove_the_seam_only():
    llm = {"name": "mock:mock", "provider": "mock", "model": "mock", "seed": 7, "mode": "careful", "config_hash": "c" * 64}
    recs = [record("graph", 1, True), record("treatment", 1, True, mode="mock", cost=None)]
    md = report.markdown(manifest(modes=("rules", "mock"), repeats=1, llm=llm), recs)
    assert "treatment (mock)" in md and "graph (rules)" in md
    assert "prove the seam, not model quality" in md and "No real model was run" in md
    assert "prove the seam, not model quality" in report.html(manifest(modes=("rules", "mock"), repeats=1, llm=llm), recs)


def test_the_report_says_its_tiers_come_from_the_certification_stand_in():
    """Tier-correct citations, and the treatment's tier rule, rest on the tiers kge/estate/certify.py lands, which is a
    stand-in."""
    recs = [record("graph", 1, True), record("treatment", 1, True)]
    md = report.markdown(manifest(repeats=1), recs)
    header = md.split("\n## ", 1)[0]
    assert "`kge/estate/certify.py`" in header and "a stand-in for upstream AI certification" in header, header
    reading = md.split("## How to read it", 1)[1]
    tier = next(line for line in reading.splitlines() if "Tier-correct citations" in line)
    assert "`kge/estate/certify.py`" in tier and "stand-in" in tier, tier
    page = report.html(manifest(repeats=1), recs)
    assert page.count("kge/estate/certify.py") >= 2 and "a stand-in for upstream AI certification" in page


def test_the_arms_are_the_presets_and_the_agents_arms():
    assert harness.choose_arms("all") == ARMS
    assert harness.choose_arms("graph, treatment") == ["graph", "treatment"]
    with pytest.raises(SystemExit, match="no arm 'agent'.*schema, catalog, graph, control, treatment"):
        harness.choose_arms("graph,agent")
    assert harness.choose_sets("all", BUILD) == ["golden", "heldout", "golden_wealth_v1", "sage_v1"]
    assert harness.choose_sets("heldout", BUILD) == ["heldout"]
    with pytest.raises(SystemExit, match="no set 'train'"):
        harness.choose_sets("train", BUILD)


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_further_versioned_set_drops_in_by_name(tmp_path):
    """Any set named <name>_v<n> with its file in the build runs, under all too, and is reported apart; golden_wealth_v1
    (#39 item 7e) is one."""
    build = scratch_build(tmp_path / "build")
    items = [{**i, "id": "X" + i["id"][1:]} for i in sets.items(BUILD / "golden.json")[:1]]
    (build / "golden_extra_v1.json").write_text(json.dumps(items))
    assert harness.choose_sets("golden_extra_v1, golden", build) == ["golden_extra_v1", "golden"]
    assert harness.choose_sets("all", build) == ["golden", "heldout", "golden_extra_v1", "golden_wealth_v1", "sage_v1"], \
        "all is the sets the harness names, then each further set whose file the build holds"
    for bad in ("golden_extra", "golden_extra_v2", "../golden_v1"):
        with pytest.raises(SystemExit, match="no set"):
            harness.choose_sets(bad, build)
    out = harness.run(profile_of(build), Scripted(lambda n, arm, q: {"behaviour": "figure", "figure": 1.0}), ["graph"], ["golden_extra_v1"], repeats=1,
                      out=tmp_path / "runs", echo=lambda *a: None)
    m = json.loads((out / "manifest.json").read_text())
    assert m["sets"] == {"golden_extra_v1": {"items": 1, "size": 1, "version": "v1", "file": "golden_extra_v1.json",
                                             "sha256": sha(build / "golden_extra_v1.json")}}
    md = (out / "report.md").read_text()
    assert "## golden_extra_v1: passed, by kind" in md


def test_the_manifest_and_the_report_header_say_which_sets_estate_and_graph_ran(rules_run):
    run_dir, _ = rules_run
    m = json.loads((run_dir / "manifest.json").read_text())
    summary = json.loads((BUILD / "summary.json").read_text())
    assert m["sets"]["golden"] == {"items": 4, "size": 45, "version": "v2", "file": "golden.json", "sha256": sha(BUILD / "golden.json"),
                                   "changes_from": sets.read(BUILD / "golden.json")["changes_from"]}
    assert m["sets"]["heldout"]["sha256"] == sha(BUILD / "heldout.json") and m["sets"]["heldout"]["size"] == 16
    assert m["estate"] == {"seed": summary.get("seed"), "generator": "kge/estate/generate.py"}
    head = (run_dir / "report.md").read_text().split("\n## ", 1)[0]
    assert f"golden v2 (4 of 45 items, sha256 `{sha(BUILD / 'golden.json')[:12]}`; changed from v1 in G021 and G030)" in head
    assert f"heldout v1 (1 of 16 items, sha256 `{sha(BUILD / 'heldout.json')[:12]}`)" in head
    assert f"estate seed {summary.get('seed')}" in head and f"graph model `{m['graph']['model'][:12]}`" in head
    assert "model seed: none, no model ran" in head
    rel = m["release"]
    assert f"legacy model `{m['graph']['legacy_model'][:12]}`" in head, "the manifest's graph hashes, legacy model among them"
    assert f"landed release `{rel['id']}` ({rel['client']}, policy {rel['policy']}, as of {rel['as_of']})" in head


def test_the_report_header_says_a_run_recorded_no_release_or_legacy_model():
    """A run from before #39 (the baseline's) recorded neither: the header says so rather than leaving them out."""
    head = report.markdown(manifest(repeats=1), [record("graph", 1, True)]).split("\n## ", 1)[0]
    assert "legacy model not recorded" in head and "landed release not recorded" in head
    m = {**manifest(repeats=1), "release": {"id": "e" * 16, "client": "acme", "policy": "p", "as_of": "2025-07-01"}}
    m["graph"] = {**m["graph"], "legacy_model": "c" * 64}
    head = report.markdown(m, [record("graph", 1, True)]).split("\n## ", 1)[0]
    assert f"legacy model `{'c' * 12}`" in head and f"landed release `{'e' * 16}` (acme, policy p, as of 2025-07-01)" in head


def test_the_report_names_the_artifact_and_says_whether_the_legacy_digest_is_the_baseline_s_model():
    """A graph loaded from a compiled artifact names it (its manifest's model hash). Its full model hash differs from a
    baseline taken before #39, by the new DDL; whether what it holds moved is its legacy model digest, beside the
    baseline's graph model, and the report says which."""
    head = report.markdown(manifest(repeats=1), [record("graph", 1, True)]).split("\n## ", 1)[0]
    assert "artifact not recorded" in head
    b = report.baseline(with_sets(manifest(repeats=1)), base_records(), "pre-39")
    m = with_sets(manifest(repeats=1))
    m["graph"] = {"model": "f" * 64, "legacy_model": "a" * 64, "artifact": "d" * 64, "overlay": "b" * 64}
    md = report.markdown(m, base_records(), b)
    assert f"artifact `{'d' * 12}`" in md.split("\n## ", 1)[0]
    beside = " ".join(md.split("## Against the baseline", 1)[1].split("\n## ", 1)[0].split())
    assert f"legacy model digest, `{'a' * 12}`, is the baseline's graph model" in beside
    m["graph"]["legacy_model"] = "e" * 64
    beside = " ".join(report.markdown(m, base_records(), b).split("## Against the baseline", 1)[1].split("\n## ", 1)[0].split())
    assert f"legacy model digest, `{'e' * 12}`, is not the baseline's graph model" in beside


def test_the_generator_records_its_seed_in_the_summary():
    assert json.loads((BUILD / "summary.json").read_text())["seed"] == 7


def base_records(flip: bool = False) -> list[dict]:
    """Two arms on two golden items, one repeat. flip: the treatment loses G002."""
    return [record("graph", 1, True), record("graph", 1, True, id_="G002", kind="cross"),
            record("treatment", 1, True), record("treatment", 1, not flip, id_="G002", kind="cross")]


def with_sets(m: dict, golden_sha: str = "g" * 64) -> dict:
    return {**m, "sets": {"golden": {"items": 2, "size": 45, "version": "v1", "file": "golden.json", "sha256": golden_sha}},
            "estate": {"seed": 7, "generator": "kge/estate/generate.py"}}


def test_a_run_saved_as_a_baseline_holds_what_a_later_run_is_compared_on():
    b = report.baseline(with_sets(manifest(repeats=1)), base_records(), "pre-generator-v1")
    assert b["baseline"] == "pre-generator-v1" and b["run_id"] == "r" and b["estate"]["seed"] == 7 and b["graph"]["model"] == "a" * 64
    assert b["sets"]["golden"]["sha256"] == "g" * 64 and b["llm"] is None
    assert b["passed"]["golden"]["treatment"] == {"within": 1, "cross": 1, "all": 2}
    assert b["items"]["golden"]["G002"] == {"kind": "cross", "graph": 1.0, "treatment": 1.0}
    assert set(b["measures"]["golden"]["graph"]) >= {"figure_correct", "behaviour_exact", "behaviour_class", "tier_correct"}


def test_the_report_puts_a_baseline_beside_a_later_run():
    b = report.baseline(with_sets(manifest(repeats=1)), base_records(), "pre-generator-v1")
    md = report.markdown(with_sets(manifest(repeats=1)), base_records(flip=True), b)
    beside = md.split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "pre-generator-v1" in beside and "same golden set as the baseline" in beside
    assert "| treatment (rules) | Across units | 1 | 0 | -1 |" in beside and "| graph (rules) | All | 2 | 2 | 0 |" in beside
    assert "| golden | G002 | Across units | treatment | 1 of 1 | 0 of 1 |" in beside
    moved = report.markdown(with_sets(manifest(repeats=1), golden_sha="h" * 64), base_records(flip=True), b)
    beside = moved.split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "golden has changed since the baseline" in beside and "| golden | G002 |" not in beside
    m = with_sets(manifest(repeats=1))
    m["sets"]["golden_wealth_v1"] = {"items": 1, "size": 1, "version": "v1", "file": "golden_wealth_v1.json", "sha256": "w" * 64}
    later = report.markdown(m, base_records() + [record("graph", 1, True, set_name="golden_wealth_v1", id_="W001")], b)
    assert "golden_wealth_v1 is new since the baseline" in later


def test_a_set_that_declares_its_change_is_put_beside_the_baseline_question_by_question_and_its_changed_items_apart():
    """Golden v2 (#39 item 7a) declares the items it changed from v1; beside a baseline that ran that very v1 its other
    questions are compared one by one, and beside another file the change is not taken on trust."""
    whole = {"golden": {"items": 2, "size": 2, "version": "v1", "file": "golden.json", "sha256": "g" * 64}}
    m = {**manifest(repeats=1), "sets": whole, "estate": {"seed": 7, "generator": "kge/estate/generate.py"}}
    b = report.baseline(m, base_records(), "v1")
    v2 = {**m, "sets": {"golden": {**whole["golden"], "version": "v2", "sha256": "h" * 64,
                                   "changes_from": {"version": "v1", "items": ["G002"], "sha256": "g" * 64, "why": "the owner's stated change"}}}}
    recs = [{**r, "ok": False} if (r["arm"], r["id"]) in (("graph", "G001"), ("treatment", "G002")) else r for r in base_records()]
    md = report.markdown(v2, recs, b)
    assert "golden v2 (2 of 2 items, sha256 `hhhhhhhhhhhh`; changed from v1 in G002)" in md.split("\n## ", 1)[0]
    beside = md.split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert ("golden v2 changed from the baseline's v1 in G002 alone, as its file declares (the owner's stated change); sha256 "
            "`gggggggggggg` then, `hhhhhhhhhhhh` now. Its other question is put beside the baseline's question by question, and "
            "G002 is listed apart.") in " ".join(beside.split())
    moved, apart = beside.split("listed apart and never counted as moved", 1)
    assert "| golden | G001 | Within a unit | graph | 1 of 1 | 0 of 1 |" in moved and "| golden | G002 |" not in moved
    assert "| Set | Id | Kind | Arm | Baseline (v1) | This run (v2) |" in apart
    assert "| golden | G002 | Across units | graph | 1 of 1 | 1 of 1 |" in apart and "| golden | G002 | Across units | treatment | 1 of 1 | 0 of 1 |" in apart
    assert "| golden | G001 |" not in apart
    other = report.markdown(v2, recs, {**b, "sets": {"golden": {**whole["golden"], "sha256": "e" * 64}}})
    beside = other.split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "golden has changed since the baseline" in beside and "| golden | G001 |" not in beside and "listed apart" not in beside


def test_only_an_arm_on_the_baselines_mode_is_put_beside_it():
    """Under MODEL_PROVIDER=mock make ab the presets stay on rules and the agent's arms run on mock, and a mock arm
    against a rules arm is not a change since the baseline."""
    b = report.baseline(with_sets(manifest(repeats=1)), base_records(), "pre-generator-v1")
    llm = {"provider": "mock", "name": "mock", "seed": 7, "temperature": 0}
    recs = [{**r, "mode": "mock"} if r["arm"] == "treatment" else r for r in base_records(flip=True)]
    md = report.markdown(with_sets(manifest(repeats=1, modes=("rules", "mock"), llm=llm)), recs, b)
    beside = md.split("## Against the baseline", 1)[1].split("\n## ", 1)[0]
    assert "asked as `analyst_enterprise`, every arm on rules," in beside
    assert ("Only like with like is put beside the baseline, as for the margin: treatment ran on mock here and on rules at "
            "the baseline. Its passes and measures read - in the baseline's place, and none of its questions is listed as "
            "moved.") in beside
    assert "| graph (rules) | All | 2 | 2 | 0 |" in beside, "the presets, on rules as at the baseline, are put beside it"
    assert "| treatment (mock) | Across units | - | 0 | - |" in beside and "| treatment (mock) | All | - | 1 | - |" in beside
    assert re.search(r"^\| treatment \(mock\) \| - → 100% \| - → 50% \|", beside, re.M), beside
    assert re.search(r"^\| graph \(rules\) \| 100% → 100% \|", beside, re.M), beside
    assert "| golden | G002 |" not in beside and "No question's result moved from the baseline's." in beside, \
        "the treatment's G002 moved on another mode: it is not listed; the graph preset's questions did not move"
    mb = report.baseline(with_sets(manifest(repeats=1, modes=("rules", "mock"), llm=llm)),
                         [{**r, "mode": "mock"} if r["arm"] == "treatment" else r for r in base_records()], "mock")
    beside = report.markdown(with_sets(manifest(repeats=1, modes=("rules", "mock"), llm=llm)), recs, mb).split("## Against the baseline", 1)[1]
    assert "graph on rules, treatment on mock," in beside and "Only like with like" not in beside.split("\n## ", 1)[0]
    assert "| treatment (mock) | Across units | 1 | 0 | -1 |" in beside and "| golden | G002 | Across units | treatment | 1 of 1 | 0 of 1 |" in beside


def test_a_run_compared_with_a_baseline_keeps_it_beside_its_records(rules_run, tmp_path):
    run_dir, _ = rules_run
    saved = tmp_path / "baseline.json"
    assert report.main([str(run_dir), "--save-baseline", str(saved)]) == 0
    b = json.loads(saved.read_text())
    assert b["baseline"] == "baseline" and b["sets"]["golden"]["sha256"] == sha(BUILD / "golden.json")
    assert report.main([str(run_dir), "--baseline", str(saved)]) == 0
    assert (run_dir / "baseline.json").read_text() == saved.read_text(), "the run keeps the baseline it was put beside"
    assert "## Against the baseline" in (run_dir / "report.md").read_text()
    (run_dir / "baseline.json").unlink()
    report.write(run_dir)
    assert "## Against the baseline" not in (run_dir / "report.md").read_text()


def test_the_committed_baseline_is_this_branchs_rules_run_on_both_sets():
    """Recorded with make ab on a clean build before #39's generator changes; golden is v2 since #39 item 7a and
    declares the v1 file this baseline ran."""
    b = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())
    assert b["estate"]["seed"] == 7 and b["llm"] is None and b["repeats"] >= 1 and len(b["graph"]["model"]) == 64
    assert {s: (v["version"], v["size"], v["items"]) for s, v in b["sets"].items()} == {"golden": ("v1", 45, 45), "heldout": ("v1", 16, 16)}
    assert b["sets"]["golden"]["sha256"] == sets.read(BUILD / "golden.json")["changes_from"]["sha256"]
    assert b["sets"]["heldout"]["sha256"] == sha(BUILD / "heldout.json")
    assert [(a["arm"], a["mode"]) for a in b["arms"]] == [(a, "rules") for a in ARMS]
    assert {len(v) - 1 for v in b["items"]["golden"].values()} == {len(ARMS)} and len(b["items"]["heldout"]) == 16
    readme = (DEMO / "README.md").read_text()
    control = readme.split("## The control", 1)[1].split("\n## ", 1)[0]
    for arm in PRESETS:
        cells = [f"{b['passed']['golden'][arm][k]:.0f} of {n}" for k, n in (("within", 20), ("cross", 10), ("shelf", 10), ("abstain", 5))]
        assert " | ".join(cells) + " |" in control, (arm, cells)
    agent = readme.split("## The reference agent", 1)[1]
    for arm in AGENT_ARMS:
        cells = [f"{b['passed']['golden'][arm][k]:.0f} of {n}" for k, n in (("within", 20), ("cross", 10), ("shelf", 10), ("abstain", 5))]
        if arm == "control":
            assert (f"records {b['passed']['golden'][arm]['within']:.0f} of 20 within a unit and "
                    f"{b['passed']['heldout'][arm]['all']:.0f} of 16 held out") in agent, (arm, cells)
        else:
            assert f"| {arm.capitalize()} | " + " | ".join(cells) + f" | {b['passed']['heldout'][arm]['all']:.0f} of 16 |" in agent, (arm, cells)


class Scripted:
    """An arm set that answers from a script: the harness's own flow, with no graph."""
    def __init__(self, answers):
        self.answers, self.n = answers, 0

    def describe(self, arm: str) -> dict:
        return {"arm": arm, "side": harness.SIDES[arm], "path": "answer", "mode": "rules", "config": arm}

    def about(self) -> dict:
        return {"llm": None, "graph": {"model": "m" * 64, "overlay": "o" * 64}}

    def ask(self, arm: str, question: str, principal: str) -> dict:
        self.n += 1
        a = self.answers(self.n, arm, question)
        if isinstance(a, Exception):
            raise a
        return {**a, "run_trace": trace()}


def test_an_arm_that_fails_on_a_question_is_recorded_and_the_run_goes_on(tmp_path):
    def answers(n, arm, q):
        return RuntimeError("planner fell over") if n == 1 else {"behaviour": "figure", "figure": 13287.67, "unit": "retail",
                                                                  "column": "monthly_revenue.total_revenue_usd", "join_path": []}
    out = harness.run(profile_of(BUILD), Scripted(answers), ["graph"], ["golden"], repeats=2, items=["G001"], out=tmp_path, echo=lambda *a: None)
    recs = runtrace.read_jsonl(out / "records.jsonl")
    assert [r["ok"] for r in recs] == [False, True] and recs[0]["error"] == "RuntimeError: planner fell over"
    md = (out / "report.md").read_text()
    assert "planner fell over" in md and "G001" in md.split("## Questions whose result changed across repeats", 1)[1]


@pytest.fixture(scope="module")
def rules_run(eng, tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    run_dir = harness.run(profile_of(BUILD), harness.Arms(eng, llm=None), ARMS, ["golden", "heldout"], repeats=2, items=FEW, out=out, echo=lambda *a: None)
    return run_dir, runtrace.read_jsonl(run_dir / "records.jsonl")


def test_a_run_writes_one_record_per_arm_question_and_repeat_with_its_run_trace(rules_run):
    run_dir, recs = rules_run
    assert len(recs) == len(ARMS) * len(FEW) * 2
    assert {(r["arm"], r["id"], r["repeat"]) for r in recs} == {(a, i, n) for a in ARMS for i in FEW for n in (1, 2)}
    assert {r["set"] for r in recs if r["id"].startswith("H")} == {"heldout"}
    for r in recs:
        rt = r["run_trace"]
        assert rt["path"] == ("answer" if r["arm"] in PRESETS else "agent") and rt["config"]["label"] == r["config"]
        assert r["mode"] == "rules" and rt["llm"] is None and not r.get("error")
        assert rt["graph"]["model"] and r["measures"]["ms"] == rt["total_ms"]
    for a in AGENT_ARMS:
        assert all(r["run_trace"]["agent"] == {"arm": a, "mode": "rules", "prompt_versions": None} for r in recs if r["arm"] == a)
    for name in ("manifest.json", "records.jsonl", "summary.json", "report.md", "report.html"):
        assert (run_dir / name).is_file(), name


def test_the_scorer_is_the_golden_scorer_unchanged(eng, rules_run):
    _, recs = rules_run
    items = {i["id"]: i for s in ("golden", "heldout") for i in sets.items(BUILD / f"{s}.json")}
    unc = uncertified_ids(BUILD)
    for r in recs:
        if r["repeat"] == 1:
            ans = eng.answer(items[r["id"]]["question"], "analyst_enterprise", config=PRESETS.get(r["arm"]) or AGENT_ARMS[r["arm"]])
            assert (r["ok"], r["answer"]["behaviour"]) == (score(items[r["id"]], ans, unc)[0], ans["behaviour"]), (r["arm"], r["id"])


def test_a_record_holds_ids_never_chunk_text_or_prompts(rules_run):
    run_dir, recs = rules_run
    text = (run_dir / "records.jsonl").read_text()
    assert "excerpt" not in text and "You are a text-to-SQL agent" not in text
    shelf = next(r for r in recs if r["id"] == "G031" and r["arm"] == "graph")
    assert shelf["answer"]["cite"] and shelf["run_trace"]["retrieved"] and "text" not in shelf["run_trace"]["retrieved"][0]


def test_the_manifest_names_what_ran_each_arm(rules_run):
    run_dir, _ = rules_run
    m = json.loads((run_dir / "manifest.json").read_text())
    assert [a["arm"] for a in m["arms"]] == ARMS and {a["mode"] for a in m["arms"]} == {"rules"}
    assert m["repeats"] == 2 and {k: v["items"] for k, v in m["sets"].items()} == {"golden": 4, "heldout": 1} and m["items"] == FEW
    assert m["llm"] is None and len(m["graph"]["model"]) == 64 and m["principal"] == "analyst_enterprise" and m["finished_at"]


def test_the_report_is_rebuilt_from_the_records_alone(rules_run):
    run_dir, _ = rules_run
    before = (run_dir / "report.md").read_text()
    for name in ("report.md", "report.html", "summary.json"):
        (run_dir / name).unlink()
    assert report.main([str(run_dir)]) == 0
    assert (run_dir / "report.md").read_text() == before and (run_dir / "report.html").is_file()
    for arm in ARMS:
        assert f"| {arm} (rules) |" in before, arm
    assert "No model was run" in before


@pytest.fixture(scope="module")
def mock_run(eng, tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    mp = pytest.MonkeyPatch()
    mp.setenv("MODEL_PRICE_IN", "3")
    mp.setenv("MODEL_PRICE_OUT", "15")
    mp.setattr(LLM, "_post", staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("the mock made a network call"))))
    try:
        arms = harness.Arms(eng, llm=LLM("mock", "mock", mode="careful"))
        run_dir = harness.run(profile_of(BUILD), arms, ["graph", "control", "treatment"], ["golden"], repeats=2, items=["G001", "G022", "G031"],
                              out=out, echo=lambda *a: None)
    finally:
        mp.undo()
    return run_dir, runtrace.read_jsonl(run_dir / "records.jsonl")


def test_the_agent_arms_run_on_the_mock_and_every_record_says_mock(mock_run):
    """A preset is rule-based whatever model is configured."""
    run_dir, recs = mock_run
    for r in recs:
        rt = r["run_trace"]
        if r["arm"] == "graph":
            assert r["mode"] == "rules" and rt["llm"] is None and rt["llm_calls"] == []
            continue
        assert r["mode"] == "mock" and rt["agent"]["mode"] == "mock" and rt["llm"]["provider"] == "mock"
        assert rt["llm_calls"] and all(c["served_model"] == "mock" and c["prompt_version"] for c in rt["llm_calls"])
        assert r["measures"]["llm_calls"] == len(rt["llm_calls"]) and r["measures"]["tokens"]["in"] > 0
        tin, tout = rt["tokens"]["in"], rt["tokens"]["out"]
        assert r["measures"]["cost_usd"] == pytest.approx(tin * 3 / 1e6 + tout * 15 / 1e6)
    m = json.loads((run_dir / "manifest.json").read_text())
    assert m["llm"]["provider"] == "mock" and m["prices"]["in"] == 3.0
    assert {a["arm"]: a["mode"] for a in m["arms"]} == {"graph": "rules", "control": "mock", "treatment": "mock"}


def test_the_mock_run_report_says_it_proves_the_seam_not_model_quality(mock_run):
    run_dir, recs = mock_run
    md = (run_dir / "report.md").read_text()
    assert "control (mock)" in md and "treatment (mock)" in md and "graph (rules)" in md
    assert "prove the seam, not model quality" in md and "No real model was run" in md
    t = next(r for r in recs if r["arm"] == "treatment" and r["id"] == "G001")
    assert t["ok"] and t["measures"]["gate"]["first_try"] and t["measures"]["numbers"]["checked"]


@pytest.mark.parametrize("mode, caught", [
    ("restricted_column", lambda m: m["gate"] == {"queries": 1, "first_try": False, "tries": 2, "passed": True}),
    ("invented_number", lambda m: m["numbers"]["caught"] > 0 and m["numbers"]["shown"] == 0),
    ("malformed_json", lambda m: m["behaviour"]["got"] == "abstain" and m["gate"] is None and m["llm_calls"] == 1),
])
def test_what_the_seam_catches_shows_in_the_measures(eng, tmp_path, mode, caught):
    run_dir = harness.run(profile_of(BUILD), harness.Arms(eng, llm=LLM("mock", "mock", mode=mode)), ["treatment"], ["golden"], repeats=1,
                          items=["G001"], out=tmp_path, echo=lambda *a: None)
    rec = runtrace.read_jsonl(run_dir / "records.jsonl")[0]
    assert rec["mode"] == "mock" and caught(rec["measures"]), rec["measures"]
    assert f"mode `{mode}`" in (run_dir / "report.md").read_text()


def test_the_command_line_writes_a_run_on_a_scratch_build(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    build = scratch_build(tmp_path / "build")
    with running_as(build):
        assert harness.main(["--arms", "graph,treatment", "--sets", "golden", "--repeats", "1", "--items", "G001"]) == 0
    runs = list((run_of(build) / "runs").iterdir())
    assert len(runs) == 1 and (runs[0] / "report.html").is_file()
    said = capsys.readouterr().out
    assert str(runs[0] / "report.md") in said and "graph" in said and "treatment" in said
    assert "by category, passed (mean of 1): graph off and graph on" in said and "Golden set, baseline" in said
    assert not (run_of(BUILD) / "runs" / runs[0].name).exists(), "a scratch run never writes into the demo's own build"


def test_the_harness_says_so_when_the_graph_is_locked(tmp_path, capsys, monkeypatch):
    from kge.adapters.graphstore import GraphLocked

    def locked(build):
        raise GraphLocked("held by the API (pid 1)")
    monkeypatch.setattr(harness.Engine, "serving", staticmethod(locked))
    with running_as(BUILD):
        assert harness.main(["--repeats", "1"]) == 2
    assert "held by the API" in capsys.readouterr().err


def test_make_ab_takes_repeats_and_arms():
    mk = (DEMO / "Makefile").read_text()
    assert re.search(r"^REPEATS \?=", mk, re.M) and re.search(r"^ARMS \?=", mk, re.M) and re.search(r"^SETS \?=", mk, re.M)
    assert re.search(r"^\.PHONY:.*\bab\b.*\bbaseline\b", mk, re.M) and re.search(r"^BASELINE \?= \$\(call profile,baseline\)$", mk, re.M)
    for target in ("ab", "baseline"):
        assert mk.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0].split() == ["$(KGE)", target, "$(KGE_ARGS)"], target
    from tests.unit.test_cli import planned
    rule = planned("ab", "REPEATS=5", "ARMS=graph", "SETS=golden", "BASELINE=b.json")
    assert "kge.eval.harness --arms graph --sets golden --repeats 5 --baseline b.json\n" in rule, rule
    assert "kge.eval.harness --arms all --sets all --repeats 3 --baseline " in planned("ab")
    assert "--baseline" not in planned("ab", "BASELINE="), "no baseline where BASELINE is empty"
    keep = planned("baseline", "RUN=r1", "BASELINE=b.json")
    assert "kge.eval.report r1 --save-baseline b.json\n" in keep, keep


def test_the_readme_says_how_to_run_the_harness_and_read_its_report():
    readme = (DEMO / "README.md").read_text()
    section = readme.split("## The A/B harness", 1)[1].split("\n## ", 1)[0]
    for needed in ("make ab", "REPEATS=", "ARMS=", "build/stores/meridian/run/runs/", "report.md", "report.html", "records.jsonl",
                   "MODEL_PROVIDER=mock make ab", "MODEL_PRICE_IN", "unknown", "prove the seam", "clients/meridian/baselines/issue-38.json",
                   "clients/meridian/baselines/issue-39.json",
                   "make baseline RUN=", "sha256", "seed",
                   "golden_wealth_v1", "#39"):
        assert needed in section, needed
    assert "make ab" in readme.split("## Running it", 1)[1]


def test_the_sets_a_run_takes_and_their_versions_are_the_ones_the_profile_s_pack_names(tmp_path):
    """The harness ran golden v2 and heldout v1 for every profile, Meridian's sets, so a fixture profile whose pack names
    golden v1 alone was told its golden was stale and that make golden would write it again, which it did, as v1, for
    ever. The sets and their versions are now the ones the profile's pack names (tests.sets); Meridian's are the same."""
    meridian = sets.declared(DEMO / "clients" / "meridian" / "pack.yaml")
    assert meridian == {"golden": "v2", "heldout": "v1", "golden_wealth": "v1"}
    assert harness.choose_sets("all", BUILD, meridian) == harness.choose_sets("all", BUILD) == ["golden", "heldout", "golden_wealth_v1",
                                                                                                    "sage_v1"]
    fixture = sets.declared(DEMO / "clients" / "thornbury" / "pack.yaml")
    assert fixture == {"golden": "v1"}
    build = tmp_path / "estate"
    build.mkdir()
    (build / "golden.json").write_text(json.dumps([{"id": "T01", "kind": "within", "question": "q", "expect": {"figure": 1.0}}]))
    assert harness.stale(build, "golden", fixture) is None and harness.choose_sets("all", build, fixture) == ["golden"]
    assert harness.describe_sets(build, {"golden": sets.items(build / "golden.json")}, fixture)["golden"]["version"] == "v1"
    with pytest.raises(SystemExit, match=r"no set 'heldout' in this profile's pack \(tests\.sets\): its sets are golden"):
        harness.choose_sets("heldout", build, fixture)
    with pytest.raises(SystemExit, match="holds golden v1, and this code runs golden v2: make golden"):
        harness.choose_sets("golden", build, {"golden": "v2"})
    assert sets.declared(tmp_path / "no-pack.yaml") == {}, "no pack to read: the harness's own, as before"


def test_run_golden_and_the_harness_refuse_a_principal_the_profile_does_not_query_as(tmp_path):
    """make eval asked as analyst_enterprise whatever the profile, so on Thornbury, which has no such principal, every
    question was refused and every arm scored nought, exit 0. Asked as anyone who is not one of the profile's querying
    identities, both stop, exit 2, naming those it has; make eval and make eval-api pass PRINCIPAL."""
    import subprocess
    import sys

    from tests.scratch import script
    build = scratch_build(tmp_path / "build")
    for who in ("actuary", "pipeline_tagger"):
        for cmd in (["kge/eval/run_golden.py", "--as", who], ["kge/eval/harness.py", "--as", who, "--repeats", "1", "--sets", "golden"]):
            r = script(*cmd, build=build)
            assert r.returncode == 2, (cmd, r.stdout, r.stderr)
            assert f"{who} is not a querying identity of profile meridian: its querying identities are analyst_retail, " in r.stderr
            assert "PRINCIPAL=" in r.stderr and not (run_of(build) / "eval.json").exists(), r.stderr
    from tests.unit.test_cli import planned
    assert "kge.eval.run_golden --arm all --as actuary" in planned("eval", "PRINCIPAL=actuary")
    r = subprocess.run(["make", "-n", "eval-api", f"PY={sys.executable}", "PRINCIPAL=actuary"], capture_output=True, text=True, cwd=DEMO)
    assert "--as actuary" in r.stdout, r.stdout
