"""Unit-scoped improvement from authenticated transports through the job and evidence."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from kge.estate.onboard import OnboardError
from kge.improve import abtest, flow, gather, edits, rules
from kge.pipeline import objstore
from kge.pipeline.settings import ROOT
from kge.services.onboard import jobs
from tests.unit.test_improve_flow import prof
from tests.unit.test_image import entry, kge_path
from tests.unit.test_knowledge_engineer import boundary, call, ENGINEER
from tests.unit.test_objstore import released_role_store

__all__ = ["prof", "entry", "boundary"]
START_JOB = jobs.start


@pytest.fixture
def evidence_run(prof):
    run = prof.run_dir() / "runs" / "20261018T120000Z-aaaaaa"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({
        "graph": {"artifact": prof.model_hash()}, "arms": [{"arm": "treatment", "mode": "rules"}],
        "sets": {"golden_v1": {}}}))
    records = [{"set": "golden_v1", "id": unit, "arm": "treatment", "ok": False,
                "question": f"{phrase} for June 2025", "answer": {
                    "unit": unit, "reason": "No glossary term recognised in the question."}}
               for unit, phrase in (("retail", "shop turnover"), ("sme", "trade turnover"))]
    (run / "records.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    lock_path = prof.clients / prof.client / "pack.lock"
    lock = json.loads(lock_path.read_text())
    for unit, phrase in (("retail", "shop turnover"), ("sme", "trade turnover")):
        for table in lock["sources"][unit]["tables"]:
            for column in table["columns"]:
                if (table["name"], column["name"]) in {
                    ("monthly_revenue", "total_revenue_usd"), ("monthly_client_revenue", "revenue_usd")
                }:
                    column["description"] = phrase
    lock_path.write_text(json.dumps(lock))
    (prof.run_dir() / "unmatched.jsonl").write_text("\n".join(map(json.dumps, [
        {"question": "shop turnover", "unit": "retail"},
        {"question": "trade turnover", "unit": "sme"},
        {"question": "unattributed question"}])) + "\n")
    return records


def proposed(out):
    diagnoses = rules.diagnose_all(out["ab_misses"], out["page_misses"], out["evidence"])
    return edits.propose(**diagnoses, evidence=out["evidence"])


def test_unset_proposals_unchanged_and_selected_unit_bounds_all_evidence(prof, evidence_run):
    original = gather.gather(prof)
    assert original == gather.gather(prof, unit=None)
    client_proposals = proposed(original)
    assert [(item["phrase"], item["evidence_column"]) for item in client_proposals["edits"]] == [
        ("shop turnover", "retail.monthly_revenue.total_revenue_usd"),
        ("trade turnover", "sme.monthly_client_revenue.revenue_usd")]
    scoped = gather.gather(prof, unit="retail")
    assert [miss["id"] for miss in scoped["ab_misses"]] == ["retail"]
    assert [miss["question"] for miss in scoped["page_misses"]] == ["shop turnover"]
    assert {col["unit"] for col in scoped["evidence"]["columns"].values()} == {"retail"}
    assert all(col.startswith("retail.") for cols in scoped["evidence"]["term_columns"].values() for col in cols)
    assert proposed(scoped)["edits"] == [client_proposals["edits"][0]]
    assert not proposed(scoped)["notes"] and not proposed(scoped)["requests"]
    assert scoped["evidence"]["learnable_passes"] == original["evidence"]["learnable_passes"]


def test_workflow_writes_only_selected_proposal_items(prof, evidence_run, monkeypatch):
    monkeypatch.setattr(abtest, "build_candidate", lambda p, pid, changes, ev: {
        "next_version": "1.5.0", "kept_edits": changes, "candidate_hash": "b" * 64})
    monkeypatch.setattr(abtest, "test_candidate", lambda *args: {
        "after_run_id": "after", "after_run_dir": str(prof.run_dir() / "after"),
        "after_learnable_records": [{**record, "ok": True} for record in evidence_run]})
    monkeypatch.setattr(abtest, "judge_heldout", lambda *args: {"heldout_table": [], "heldout_fell": False})
    default = flow.run_improve(prof, llm=None)["proposal"]
    (prof.clients / prof.client / "proposals/open/proposal.yaml").unlink()
    explicit = flow.run_improve(prof, llm=None, unit=None)["proposal"]
    assert {k: v for k, v in default.items() if k != "created_at"} == {
        k: v for k, v in explicit.items() if k != "created_at"}
    (prof.clients / prof.client / "proposals/open/proposal.yaml").unlink()
    selected = flow.run_improve(prof, llm=None, unit="retail")["proposal"]
    assert selected["items"] == [default["items"][0]]
    saved = yaml.safe_load((prof.clients / prof.client / "proposals/open/proposal.yaml").read_text())
    assert saved == selected


def test_expected_unit_scopes_misses_without_answer_unit(prof, evidence_run):
    run = prof.run_dir() / "runs" / "20261018T120000Z-aaaaaa"
    records = [{**record, "answer": {"reason": record["answer"]["reason"]}} for record in evidence_run]
    (run / "records.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    prof.estate().mkdir(parents=True, exist_ok=True)
    (prof.estate() / "golden_v1.json").write_text(json.dumps([
        {"id": record["id"], "question": record["question"], "expect": {"unit": record["id"]}}
        for record in records]))
    selected = gather.gather(prof, unit="retail")
    assert [miss["id"] for miss in selected["ab_misses"]] == ["retail"]
    assert [item["phrase"] for item in proposed(selected)["edits"]] == ["shop turnover"]


def test_model_cannot_propose_another_units_alias(prof, evidence_run):
    scoped = gather.gather(prof, unit="retail")
    ok, reason = edits.validate_edit({"section": "terminology.aliases", "phrase": "trade turnover", "term": "revenue"},
                                     scoped["evidence"])
    assert not ok and "not backed" in reason


@pytest.mark.parametrize("unit,status,detail", [
    ("nope", 404, "unknown unit: nope"),
    ("no_such_unit", 404, "unknown unit: no_such_unit"),
    ("sme", 403, "knowledge_engineer may not improve unit sme"),
    ("retail\nnope", 422, "unit is not valid"),
])
def test_http_mcp_unit_refusals_have_the_same_plain_detail(boundary, unit, status, detail):
    _, profile, starts = boundary
    path = profile.clients / profile.client / "pack.yaml"
    pack = yaml.safe_load(path.read_text())
    next(role for role in pack["access"]["roles"] if role["id"] == ENGINEER)["reads"] = ["retail"]
    path.write_text(yaml.safe_dump(pack))
    code, body = call(boundary, "http", ENGINEER, "improve", {"unit": unit})
    assert code == status and body == {"detail": detail}
    code, body = call(boundary, "mcp", ENGINEER, "improve", {"unit": unit})
    assert code == 400 and detail in body
    assert not starts


@pytest.mark.parametrize("step,who", [
    ("checks", ENGINEER), ("sign-off", "steward_glossary"), ("release", "steward_glossary"),
])
def test_http_unit_is_refused_for_other_steps(boundary, monkeypatch, step, who):
    _, _, starts = boundary
    monkeypatch.setattr(jobs, "start", lambda *args, **kwargs: starts.append(kwargs) or {"job": {}})
    code, body = call(boundary, "http", who, step, {"unit": "retail"})
    assert code == 422 and body == {"detail": "unit is only supported for improve"}
    assert not starts


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_unit_reaches_job_and_extra_fields_still_refused(boundary, monkeypatch, transport):
    seen = []
    monkeypatch.setattr(jobs, "start", lambda *args, **kwargs: seen.append(kwargs) or {"job": {}})
    assert call(boundary, transport, ENGINEER, "improve", {"unit": "retail"})[0] in {200, 202}
    assert seen == [{"unit": "retail"}]
    assert call(boundary, transport, ENGINEER, "improve", {"unit": "retail", "invented": True})[0] >= 400
    assert len(seen) == 1


def test_invalid_unit_preserves_existing_proposal_and_cli_is_plain(prof, monkeypatch, capsys):
    path = prof.clients / prof.client / "proposals/open/proposal.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("keep this")
    monkeypatch.setattr(flow, "resolve", lambda _: prof)
    assert flow.main(["--unit", "nope"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "improve: unknown unit: nope\n"
    assert path.read_text() == "keep this"
    with pytest.raises(OnboardError, match="may not improve unit retail"):
        flow.run_improve(prof, unit="retail", as_="analyst_retail", llm=None)


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("settings,detail", [
    (["UNIT=nope"], "unknown unit: nope"),
    (["UNIT=retail", "PRINCIPAL=analyst_retail"], "analyst_retail may not improve unit retail"),
])
def test_manual_and_image_unknown_or_unauthorised_unit_exit_nonzero_plainly(image, settings, detail, tmp_path):
    command = ["sh", "entrypoint.sh", "improve"] if image else ["make", "-s", "improve"]
    env = {**os.environ, "PATH": kge_path(tmp_path / "bin"), "PY": sys.executable}
    result = subprocess.run([*command, *settings, f"PY={sys.executable}"], cwd=ROOT, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert f"improve: {detail}" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_image_forwards_unit_as_kge_setting(entry):
    result, calls = entry("improve", "UNIT=retail")
    assert result.returncode == 0
    assert calls[0]["args"] == ["improve", "UNIT=retail"]


@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("unit", [None, "retail"])
def test_job_serialises_unit_for_local_and_stored_execution(prof, tmp_path, monkeypatch, stored, unit):
    from kge.adapters.objstore import open_store
    store = open_store(str(tmp_path / "objects"))
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects") if stored else "")
    monkeypatch.setattr(jobs, "_cloud", lambda: False)
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "candidate", lambda *args: (None, None))
    monkeypatch.setattr(jobs, "the_store", lambda: store)
    commands = []
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(
        start=lambda: commands.append(kwargs["args"][2])))
    app = SimpleNamespace(PROFILE=prof)
    result = jobs.start(app, "improve", None, ENGINEER, {}, unit=unit)
    saved = json.loads(store.get("clients/meridian/onboard/job.json") if stored else jobs.job_path(app).read_text())
    assert saved == result["job"]
    assert saved.get("unit") == unit
    assert ("unit" in saved) == (unit is not None)
    if stored:
        request = json.loads(store.get("clients/meridian/onboard/request.json"))
        assert request.get("unit") == unit and request["principal"] == ENGINEER
        assert ("unit" in request) == (unit is not None)
        assert not commands
    else:
        assert commands == [[sys.executable, "-m", "kge.improve.flow"] + (
            ["--unit", unit, "--as", ENGINEER] if unit is not None else [])]
        jobs.active.discard(result["job"]["id"])


@pytest.mark.parametrize("unit,allowed", [(None, True), ("retail", True), ("sme", False), ("nope", False), ([], False)])
def test_stored_request_rechecks_released_units_and_forwards_setting(tmp_path, monkeypatch, unit, allowed):
    store = released_role_store(tmp_path, [{"id": "engineer", "reads": ["retail"], "processes": ["improving"]}])
    identities = tmp_path / "identities.yaml"
    identities.write_text("principals:\n  - {principal: operator, role: engineer}\n")
    profile = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    key = "clients/meridian/onboard/request.json"
    store.put(key, json.dumps({"id": "20261024T120000Z-abcdef", "command": "improve", "principal": "operator", "unit": unit,
                               "timestamp": "2026-10-24T12:00:00Z"}).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda *args: profile)
    calls = []
    monkeypatch.setattr(objstore.subprocess, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    if allowed:
        objstore.onboard_request(tmp_path)
        assert calls == [["sh", str(tmp_path / "entrypoint.sh"), "improve"] + (
            [f"UNIT={unit}"] if unit is not None else []) + ["PRINCIPAL=operator"]]
        saved = json.loads(store.get("clients/meridian/onboard/job.json"))
        assert saved["state"] == "done"
        assert saved.get("unit") == unit
        assert ("unit" in saved) == (unit is not None)
    else:
        with pytest.raises(objstore.Refused):
            objstore.onboard_request(tmp_path)
        assert not calls
    assert store.get(key) == b""


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("unit", [None, "retail"])
def test_improvement_status_preserves_recorded_scope(boundary, monkeypatch, transport, unit):
    monkeypatch.setattr(jobs, "start", START_JOB)
    monkeypatch.setattr(jobs, "_cloud", lambda: False)
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    code, started = call(boundary, transport, ENGINEER, "improve", {"unit": unit} if unit else {})
    assert code in {200, 202}
    job = started["job"]
    try:
        code, status = call(boundary, transport, ENGINEER, "status")
        assert code == 200
        assert status["job"] == job
        assert job.get("unit") == unit
        assert ("unit" in job) == (unit is not None)
        jobs.save(SimpleNamespace(PROFILE=boundary[1]), {**job, "state": "done"})
        code, finished = call(boundary, transport, ENGINEER, "status")
        assert code == 200 and finished["job"] == {**job, "state": "done"}
    finally:
        jobs.active.discard(job["id"])
