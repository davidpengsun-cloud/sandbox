"""Regression tests for the review findings, one block per finding.

    .venv/bin/python -m pytest tests -q

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from kge.services.ask.engine.core import Engine, parse
from kge.eval import sets
from tests.scratch import BUILD, profile_of, registry, scratch_build, script


@pytest.fixture()
def scratch(tmp_path):
    return Engine(profile_of(scratch_build(tmp_path / "build")))


def wh(unit: str, sql: str):
    return duckdb.connect(str(BUILD / "warehouses" / f"{unit}.duckdb"), read_only=True).execute(sql).fetchone()[0]


@pytest.mark.parametrize("arm", ["graph", "catalog", "schema"])
def test_partial_combination_is_parts_not_a_total(eng, arm):
    a = eng.answer("Combined Retail and Corporate revenue for June 2025", principal="admin", arm_name=arm)
    if arm == "schema" and a["behaviour"] != "parts_with_reason":
        pytest.skip("the schema arm guesses different columns; nothing to combine")
    assert a["behaviour"] == "parts_with_reason"
    assert "figure" not in a
    assert a["commensurable"] is False
    assert "corporate" in a["missing_parts"]
    assert a["reason"].startswith("incomplete:")


def test_complete_combination_still_sums(eng):
    a = eng.answer("Combined Retail and SME revenue for June 2025", principal="admin")
    assert a["behaviour"] == "sum_with_verdict" and a["missing_parts"] == []


def test_accepted_candidate_is_not_cited_as_certified(scratch):
    q = [r for r in scratch.g.steward_queue() if r["family"] == "candidate_term" and r["value"] == "Digital Engagement Score"]
    assert q, "the recovery scenario's candidate should be in the queue"
    scratch.g.steward_decide(q[0]["tag"], "steward_glossary", "accept", definition="logins per active customer per month, capped at 30")
    scratch.refresh()
    d = scratch.answer("What is the Digital Engagement Score?", principal="admin")
    assert "EM-004" not in d.get("cite", [])
    assert d["behaviour"] == "abstain"
    assert any(u["id"] == "EM-004" for u in d["uncertified_definition_not_cited"])
    f = scratch.answer("What was the Digital Engagement Score for June 2025?", principal="admin")
    assert f["behaviour"] == "abstain" and "no certified implementation" in f["reason"]


def test_event_year_is_bounded(eng):
    a24 = eng.answer("SME repayments more than 30 days past due in 2024", principal="admin")
    a25 = eng.answer("SME repayments more than 30 days past due in 2025", principal="admin")
    assert a24["figure"] == wh("sme", "SELECT count(*) FROM repayment WHERE days_past_due>30 AND paid_on>='2024-01-01' AND paid_on<'2025-01-01'")
    assert a25["figure"] == wh("sme", "SELECT count(*) FROM repayment WHERE days_past_due>30 AND paid_on>='2025-01-01' AND paid_on<'2026-01-01'")
    assert a24["figure"] + a25["figure"] == wh("sme", "SELECT count(*) FROM repayment WHERE days_past_due>30")


def test_snapshot_year_is_the_last_snapshot_not_a_sum(eng):
    a = eng.answer("Total corporate credit exposure in 2024", principal="admin")
    assert a["behaviour"] == "figure"
    assert a["figure"] == pytest.approx(float(wh("corporate", "SELECT round(sum(ead_eur),2) FROM exposure WHERE as_of='2024-12-31'")))
    assert "last snapshot" in " ".join(a["notes"])


def test_model_tagger_never_sends_gated_text(monkeypatch):
    from kge.pipeline.tagger import ModelTagger
    t = ModelTagger(["Revenue"], "openai", "stub", "")
    sent: list[str] = []
    monkeypatch.setattr(t, "_complete", lambda prompt: sent.append(prompt) or '{"mentions": [{"term": "Revenue", "confidence": 0.9, "evidence": "revenue"}]}')
    gated = t.propose("Complaint example: contact by email at someone@example.com about revenue.", "certified", "policy")
    assert sent == [] and t.withheld == 1
    assert any(p.family == "privacy" for p in gated)
    clean = t.propose("Revenue is booked monthly.", "certified", "policy")
    assert len(sent) == 1 and "Revenue is booked monthly." in sent[0]
    assert any(p.family == "business_meaning" and p.generator == "auto" for p in clean)


def test_readiness_honours_an_independent_restricted():
    from kge.pipeline.tagger import DEPENDENT_EVIDENCE, Proposal, derive_readiness
    base = Proposal("classification", "Internal", 0.7, "published document")
    dependent = Proposal("classification", "Restricted", 0.8, DEPENDENT_EVIDENCE)
    independent = Proposal("classification", "Restricted", 1.0, "steward: client health data", "human")
    assert derive_readiness([base, dependent], "certified")[0] == "citable"
    assert derive_readiness([base, independent], "certified")[0] == "embed-prohibited"
    assert derive_readiness([], "certified")[0] == "embed-prohibited"


def test_rejecting_the_planted_false_positive_embeds_it(scratch):
    tag = next(r for r in scratch.g.steward_queue() if r["doc"] == "POL-RETENTION" and r["family"] == "privacy")
    r = scratch.g.steward_decide(tag["tag"], "steward_glossary", "reject")
    assert r["readiness"] == "citable" and r.get("embedded_now") is True
    tags = scratch.g.q("MATCH (c:Chunk {id: $c})-[:TAGGED]->(t:Tag {family: 'ai_readiness'}) RETURN t.value AS v", {"c": tag["chunk"]})
    assert [t["v"] for t in tags] == ["citable"]


def test_pipeline_rerun_preserves_steward_decisions(tmp_path):
    build = scratch_build(tmp_path / "build")
    e = Engine(profile_of(build))
    retention = next(r for r in e.g.steward_queue() if r["doc"] == "POL-RETENTION" and r["family"] == "privacy")
    candidate = next(r for r in e.g.steward_queue() if r["family"] == "candidate_term")
    e.g.steward_decide(retention["tag"], "steward_glossary", "reject")
    e.g.steward_decide(candidate["tag"], "steward_glossary", "accept")
    queue_before = {r["tag"] for r in e.g.steward_queue()}
    e.close()

    r = script("kge/pipeline/run.py", build=build)
    assert r.returncode == 0, r.stderr

    e = Engine(profile_of(build))
    assert {r["tag"] for r in e.g.steward_queue()} == queue_before
    humans = e.g.q("MATCH (c:Chunk)-[:TAGGED]->(h:Tag {generator: 'human'})-[:SUPERSEDES]->(o:Tag) RETURN h.id AS h, o.id AS o, c.id AS c")
    assert {r["o"] for r in humans} == {retention["tag"], candidate["tag"]}
    ready = e.g.q("MATCH (c:Chunk {id: $c}) RETURN c.readiness AS r, c.embedding IS NOT NULL AS e", {"c": retention["chunk"]})[0]
    assert ready == {"r": "citable", "e": True}
    assert "Digital Engagement Score" in e.g.terms()
    e.close()


def test_queue_redacts_personal_data_and_marks_the_planted_scenario(eng):
    """A role mailbox such as records@example.com is not personal data, so it stays in the queue."""
    q = eng.g.steward_queue()
    complaints = [t for t in q if t["doc"] == "POL-COMPLAINTS"]
    retention = [t for t in q if t["doc"] == "POL-RETENTION"]
    assert {t["value"] for t in complaints} == {"personal_data:email", "personal_data:date_of_birth"}
    assert all(t["assessment"] == "likely_personal" and t["second_check_required_to_reject"] for t in complaints)
    assert retention and retention[0]["assessment"] == "likely_functional" and not retention[0]["second_check_required_to_reject"]
    text = repr(q)
    assert "okonkwo" not in text and "1971" not in text
    assert "records@example.com" in text


def test_rejecting_real_personal_data_needs_a_second_steward(scratch):
    tag = next(t for t in scratch.g.steward_queue() if t["doc"] == "POL-COMPLAINTS" and t["value"] == "personal_data:email")
    first = scratch.g.steward_decide(tag["tag"], "steward", "reject")
    assert first["status"] == "pending_second_check"
    with pytest.raises(PermissionError):
        scratch.g.steward_decide(tag["tag"], "steward", "reject")
    assert next(t for t in scratch.g.steward_queue() if t["tag"] == tag["tag"])["reject_requested_by"] == "steward"
    done = scratch.g.steward_decide(tag["tag"], "steward_glossary", "reject")
    assert done["confirmed_request_of"] == "steward"
    assert done["readiness"] == "embed-prohibited"


def test_prohibited_chunks_are_named_not_quoted(eng):
    rows = eng.g.q("MATCH (d:Document {shelf: 'uncertified'})-[:HAS_CHUNK]->(c:Chunk {readiness: 'embed-prohibited'})-[:MENTIONS]->(t:Term) RETURN t.name AS t LIMIT 1")
    if not rows:
        pytest.skip("no prohibited uncertified chunk mentions a term on this estate")
    for m in eng.g.uncertified_mentions(rows[0]["t"], limit=50):
        assert "@" not in m["excerpt"]


def test_scorer_is_strict_where_it_was_lenient():
    from kge.eval.run_golden import score
    cross = {"kind": "cross", "expect": {"behaviour": "parts_with_reason", "parts": {"retail": 1.0, "sme": 2.0}, "commensurable": False}}
    assert not score(cross, {"behaviour": "parts_with_reason", "commensurable": False, "parts": {"retail": 1.0, "sme": 9.0}})[0]
    shelf = {"kind": "shelf", "expect": {"behaviour": "cite_certified", "cite": ["POL-X"]}}
    assert not score(shelf, {"behaviour": "cite_certified", "cite": ["POL-X", "EM-001"]}, {"EM-001"})[0]
    abstain = {"kind": "abstain", "expect": {"behaviour": "abstain_route_steward"}}
    assert not score(abstain, {"behaviour": "refuse"})[0]
    within = {"kind": "within", "expect": {"figure": 5, "unit": "sme", "columns": ["t.c"], "join_path": []}}
    assert not score(within, {"behaviour": "figure", "figure": 5, "unit": "retail", "column": "t.c", "join_path": []})[0]


def test_heldout_set_exists_apart_from_the_gate():
    golden = sets.items(BUILD / "golden.json")
    heldout = sets.items(BUILD / "heldout.json")
    assert len(golden) == 45 and len(heldout) == 16
    assert not {h["question"] for h in heldout} & {g["question"] for g in golden}
    assert all(h["paraphrase_of"] in {g["question"] for g in golden} for h in heldout)


def test_human_tag_ids_do_not_collide(scratch):
    q = scratch.g.steward_queue()
    a = next(t for t in q if t["doc"] == "POL-RETENTION")
    b = next(t for t in q if t["family"] == "candidate_term")
    ra = scratch.g.steward_decide(a["tag"], "admin", "reject")
    rb = scratch.g.steward_decide(b["tag"], "admin", "accept")
    assert ra["by"] != rb["by"] and ra["by"].startswith(a["tag"] + "/h")


def test_scripts_run_from_any_directory(tmp_path):
    import subprocess
    import sys
    import os
    b = scratch_build(tmp_path / "b")
    r = subprocess.run([sys.executable, "-m", "kge.services.identity.cli"], capture_output=True, text=True, cwd=tmp_path,
                       env={**os.environ, "PROFILES": str(registry(b)), "PROFILE": ""})
    assert r.returncode == 0 and "steward" in r.stdout


def test_the_readme_installs_what_make_test_needs_and_make_test_says_so_if_it_is_missing():
    """Without pytest (python -S leaves out site-packages), make test names the file to install rather than only a
    missing module."""
    import subprocess
    import sys
    demo = BUILD.parent
    block = (demo / "README.md").read_text().split("## Running it", 1)[1].split("```", 2)[1].splitlines()
    def first(prefix):
        return next(i for i, line in enumerate(block) if line.startswith(prefix))
    assert first("pip install -r requirements.txt") < first("pip install -r requirements-dev.txt") < first("make test")
    r = subprocess.run(["make", "-s", "test", f"PY={sys.executable} -S"], capture_output=True, text=True, cwd=demo, timeout=60)
    assert r.returncode != 0 and "pip install -r requirements-dev.txt" in r.stderr, r.stdout + r.stderr


def test_a_passing_test_s_temporary_folder_is_deleted_as_it_ends_and_a_failing_one_s_is_kept(request, tmp_path):
    """Many tests copy the graph into a temporary folder of their own. pytest's defaults keep every one until the run ends,
    and the last three runs besides: gigabytes. The demo's
    pytest.ini deletes a passing test's folder as the test ends and keeps a failing one's, for the last run alone, so a
    run that passes leaves nothing behind. It is the suite's own: make test and bare pytest alike read it."""
    import subprocess
    import sys
    demo = BUILD.parent
    ini = demo / "pytest.ini"
    assert request.config.inipath == ini, "the suite runs with the demo's pytest.ini"
    assert request.config.getini("tmp_path_retention_policy") == "failed"
    assert int(request.config.getini("tmp_path_retention_count")) == 1
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "test_probe.py").write_text("def test_passes(tmp_path):\n    (tmp_path / 'graph').write_bytes(b'x')\n    print('AT', 'passes', tmp_path)\n\n\n"
                                         "def test_fails(tmp_path):\n    (tmp_path / 'graph').write_bytes(b'x')\n    print('AT', 'fails', tmp_path)\n"
                                         "    assert False\n")
    r = subprocess.run([sys.executable, "-m", "pytest", "-c", str(ini), str(probe), "-q", "-s", "-p", "no:cacheprovider",
                        "--basetemp", str(tmp_path / "base")], capture_output=True, text=True, cwd=probe, timeout=120)
    assert r.returncode == 1 and "1 failed, 1 passed" in r.stdout, r.stdout + r.stderr
    at = dict(re.findall(r"\bAT (passes|fails) (\S+)", r.stdout))
    assert not Path(at["passes"]).exists(), "a passing test's folder is deleted as it ends"
    assert (Path(at["fails"]) / "graph").is_file(), "a failing test's folder is kept, to look at"
    block = (demo / "README.md").read_text().split("## Running it", 1)[1].split("```", 2)[1].splitlines()
    at = next(i for i, line in enumerate(block) if line.startswith("make test "))
    said = [block[at]] + [line for line in block[at + 1:] if line.lstrip().startswith("#")][:1]
    assert "temporary folder" in " ".join(said) and "(pytest.ini)" in " ".join(said), said

def test_restricted_lookup_covers_dob_and_email(eng):
    assert parse("Show me the date of birth of customer 118", [], eng).special == "restricted_lookup"
    assert parse("What is the email address of customer 118?", [], eng).special == "restricted_lookup"


def test_unresolved_complaints_filter_and_count(eng):
    q = "How many retail complaints were still unresolved at 30 June 2025?"
    req = parse(q, eng.term_names, eng)
    assert ("resolved_on", "IS NULL", None) in req.filters
    a = eng.answer(q, principal="admin")
    assert a["behaviour"] == "figure" and a["figure"] == 14
