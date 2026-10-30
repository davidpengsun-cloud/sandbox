"""The improvement job's HTTP boundary and onboarding drafting separation."""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.services.onboard import jobs, routes as onboard, service as onboard_service
from kge.estate import onboard as ob_core

DEMO = Path(__file__).resolve().parents[2]


class _Graph:
    def can_write_terms(self, who):
        return who == "business_team"

    def may(self, who, process):
        return who == "steward_glossary" and process == "certification"


def _client(monkeypatch, tmp_path, who="knowledge_engineer", writable=True):
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / "meridian", clients / "meridian", ignore=shutil.ignore_patterns("units", "proposals", "versions"))
    profile = SimpleNamespace(
        id="meridian", client="meridian", clients=clients,
        artifacts=tmp_path / "artifacts", store=tmp_path / "store",
        estate=tmp_path / "estate", warehouses=tmp_path / "warehouses",
        identities=DEMO / "deploy" / "meridian" / "identities.yaml",
        run_dir=lambda: tmp_path / "run", model_hash=lambda: "a" * 64,
    )
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    engine = SimpleNamespace(g=_Graph(), profile=profile)
    fake = SimpleNamespace(PROFILE=profile, engine=lambda: engine, principal=lambda request: request.headers.get("who", who), switch=lambda: None)
    monkeypatch.setattr(onboard, "_app", lambda: fake)
    monkeypatch.setattr(jobs, "state", lambda prof, model_hash: {"model_hash": model_hash})
    monkeypatch.setattr(onboard.core, "writable", lambda prof: (writable, "read-only until configured" if not writable else ""))
    app = FastAPI()
    app.include_router(onboard.router)
    return TestClient(app), profile, fake


def test_drafting_leasing_schema_does_not_propose_lease_income_before_improve(tmp_path):
    """Step 10's onboarding draft of examples/leasing/schema.sql proposes Lease Rentals and its column phrases,
    never 'lease income', which comes from the column description and waits for Step 12's improvement job."""
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients" / "meridian", clients / "meridian", ignore=shutil.ignore_patterns("units"))
    prof = SimpleNamespace(
        clients=clients, client="meridian", warehouses=tmp_path / "warehouses",
        estate=tmp_path / "estate", identities=DEMO / "deploy" / "meridian" / "identities.yaml",
        run_dir=lambda: tmp_path / "run",
    )
    ddl = (DEMO / "examples" / "leasing" / "schema.sql").read_text()
    csv = {p.name: p.read_bytes() for p in (DEMO / "examples" / "leasing" / "data").glob("*.csv")}
    ob_core.create(prof, "leasing", label="Asset Finance", rows="csv", csv=csv, ddl_text=ddl, as_="knowledge_engineer")
    ob_core.draft(prof, "leasing", as_="knowledge_engineer", on="2026-10-18")
    import yaml
    drafted = yaml.safe_load((clients / "meridian" / "units" / "leasing" / "draft.yaml").read_text())
    term_item = next(i for i in drafted["items"] if i["kind"] == "term")
    aliases = [a["phrase"] for a in term_item["pack"].get("terminology.aliases", [])]
    assert "lease rentals" in aliases
    assert "lease income" not in aliases
    for item in drafted["items"]:
        for a in item.get("pack", {}).get("terminology.aliases", []):
            assert a.get("phrase") != "lease income"


def test_onboard_api_exposes_proposal_accept_certify_notes_versions_and_rollback(monkeypatch, tmp_path):
    from kge.compiler import pack as packs
    c, prof, fake = _client(monkeypatch, tmp_path)
    pdir = prof.clients / prof.client / "proposals" / "prop-1"
    pdir.mkdir(parents=True)
    base_hash = packs.read(prof.clients, prof.client, lock=True, identities=prof.identities).hash
    proposal = {
        "id": "prop-1", "client": "meridian", "state": "drafted", "proposer": "rules",
        "kept": True, "after_run_id": "run-1",
        "base_version": "1.3.0", "next_version": "1.4.0",
        "base_hash": base_hash, "candidate_hash": "b" * 64,
        "version": {"from": "1.3.0", "to": "1.4.0", "bump": "minor"},
        "items": [{
            "id": "alias:lease-income", "kind": "alias", "state": "drafted",
            "title": "Alias 'lease income' -> Lease Rentals (leasing-lease-rentals)",
            "from": "improve job · rules",
            "detail": {"section": "terminology.aliases", "phrase": "lease income", "term": "leasing-lease-rentals",
                       "term_name": "Lease Rentals", "why": "missing alias", "evidence": "the business calls this lease income"},
            "pack": {"aliases": [{"phrase": "lease income", "term": "leasing-lease-rentals"}]},
        }],
        "learnable_table": [{"set": "team_leasing_v1", "before_passed": 1, "after_passed": 2, "of": 2, "delta": 1}],
        "heldout_table": [{"set": "heldout_leasing_v1", "before_passed": 1, "after_passed": 2, "of": 2, "delta": 1}],
        "notes": [{"question_id": "W08", "category": "steward_note", "summary": "Exclusions need a context."}],
        "requests": [{"question_id": "XB1", "category": "platform_request", "summary": "Cross-unit count."}],
    }
    import yaml
    (pdir / "meridian").mkdir(parents=True, exist_ok=True)
    shutil.copy2(prof.clients / prof.client / "pack.yaml", pdir / "meridian" / "pack.yaml")
    shutil.copy2(prof.clients / prof.client / "pack.lock", pdir / "meridian" / "pack.lock")

    unkept = {k: v for k, v in proposal.items() if k not in ("kept", "after_run_id")}
    (pdir / "proposal.yaml").write_text(yaml.safe_dump(unkept, sort_keys=False))
    assert c.get("/onboard", headers={"who": "business_team"}).json()["proposal"] is None

    fell_prop = {**proposal, "heldout_fell": True}
    (pdir / "proposal.yaml").write_text(yaml.safe_dump(fell_prop, sort_keys=False))
    assert c.get("/onboard", headers={"who": "business_team"}).json()["proposal"] is None
    fell_acc = c.post("/onboard/accept", headers={"who": "business_team"}, json={"proposal": "prop-1", "items": "all"})
    assert fell_acc.status_code == 409 and "held-out" in fell_acc.json()["detail"]
    fell_cert = c.post("/onboard/certify", headers={"who": "steward_glossary"}, json={"proposal": "prop-1", "items": "all"})
    assert fell_cert.status_code == 409 and "held-out" in fell_cert.json()["detail"]

    (pdir / "meridian").mkdir(parents=True, exist_ok=True)
    shutil.copy2(prof.clients / prof.client / "pack.yaml", pdir / "meridian" / "pack.yaml")
    shutil.copy2(prof.clients / prof.client / "pack.lock", pdir / "meridian" / "pack.lock")
    (pdir / "proposal.yaml").write_text(yaml.safe_dump(proposal, sort_keys=False))
    state_res = c.get("/onboard", headers={"who": "business_team"}).json()
    assert state_res["proposal"]["id"] == "prop-1"
    assert state_res["proposal"]["version"] == {"from": "1.3.0", "to": "1.4.0", "bump": "minor"}
    assert len(state_res["notes"]) == 1 and len(state_res["requests"]) == 1

    refused = c.post("/onboard/certify", headers={"who": "steward_glossary"}, json={"proposal": "prop-1", "items": "all"})
    assert refused.status_code == 409

    accepted = c.post("/onboard/accept", headers={"who": "business_team"}, json={"proposal": "prop-1", "items": "all"})
    assert accepted.status_code == 200
    assert accepted.json()["items"][0]["state"] == "accepted"

    certified = c.post("/onboard/certify", headers={"who": "steward_glossary"}, json={"proposal": "prop-1", "items": "all"})
    assert certified.status_code == 200
    assert certified.json()["items"][0]["state"] == "certified"

    class _DummyThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(jobs.threading, "Thread", _DummyThread)
    imp = c.post("/onboard/run", headers={"who": "knowledge_engineer"}, json={"step": "improve"})
    assert imp.status_code == 409 and "release it first" in imp.json()["detail"]
    assert not jobs.job_path(onboard._app()).exists()

    events = []
    fake.switch = lambda: events.append("switch")
    orig_save = jobs.save

    def tracked_save(app, job):
        if job.get("state") == "done" and job.get("step") == "release":
            events.append("save_done")
        return orig_save(app, job)

    import kge.graph.history as g_hist
    monkeypatch.setattr(g_hist, "check_rollback_allowed", lambda prof_, target_, cur_: None)
    monkeypatch.setattr(jobs, "read_artifact", lambda folder, client: {"model_hash": "a" * 64})
    monkeypatch.setattr(jobs, "save", tracked_save)

    class _FakeProc:
        def __init__(self, command, stdout=None, **kwargs):
            if stdout:
                stdout.write(f"current is now {'a' * 64}\n")
                stdout.flush()

        def wait(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", _FakeProc)

    class _SyncThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}
        def start(self):
            if self._target:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(jobs.threading, "Thread", _SyncThread)
    rel = c.post("/onboard/run", headers={"who": "steward_glossary"}, json={"step": "release", "hash": "a" * 64})
    assert rel.status_code == 202
    assert events == ["switch", "save_done"]


def test_source_and_rows_validate_and_cap_questions_and_heldout(monkeypatch, tmp_path):
    c, prof, _ = _client(monkeypatch, tmp_path)
    ddl = (DEMO / "examples" / "leasing" / "schema.sql").read_text()
    big = "x" * (onboard_service.MAX_UPLOAD_BYTES + 1)

    r_src_big = c.post("/onboard/source", headers={"who": "knowledge_engineer"}, json={
        "unit": "leasing", "label": "Asset Finance", "ddl": ddl, "rows": "none", "questions": big,
    })
    assert r_src_big.status_code == 413 and "question files" in r_src_big.json()["detail"]

    r_rows_big = c.post("/onboard/rows", headers={"who": "knowledge_engineer"}, json={
        "unit": "leasing", "rows": "invented", "heldout": big,
    })
    assert r_rows_big.status_code == 413 and "question files" in r_rows_big.json()["detail"]

    for bad_q in ("not json", "{}", "[]", '[{"id": "Q1"}]'):
        r_bad = c.post("/onboard/source", headers={"who": "knowledge_engineer"}, json={
            "unit": "leasing", "label": "Asset Finance", "ddl": ddl, "rows": "none", "questions": bad_q,
        })
        assert r_bad.status_code == 422
        assert not (prof.clients / prof.client / "units" / "leasing").exists()
