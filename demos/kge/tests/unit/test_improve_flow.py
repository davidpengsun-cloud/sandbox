"""LangGraph improvement workflow, keep/drop rules, held-out isolation, and proposal governance."""
from pathlib import Path
import json
import shutil

import pytest
import yaml

from kge.compiler import pack as packs
from kge.estate import onboard
from kge.eval import margin
from kge.graph import history, release as rel
from kge.improve import abtest, flow, gather as gather_mod
from kge.pipeline.profile import Profile

DEMO = Path(__file__).resolve().parents[2]


@pytest.fixture
def prof(tmp_path):
    clients = tmp_path / "clients"
    target = clients / "meridian"
    target.mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", target / "pack.yaml")
    shutil.copy(DEMO / "clients/meridian/pack.lock", target / "pack.lock")
    artifacts = tmp_path / "artifacts"
    (artifacts / "meridian" / ("a" * 64)).mkdir(parents=True)
    (artifacts / "meridian" / "current").symlink_to("a" * 64)
    return Profile(
        "meridian", "meridian", clients, artifacts, tmp_path / "stores", tmp_path / "landing",
        tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "profiles.yaml"
    )


def test_compiled_langgraph_topology(prof):
    wf = flow.build_workflow(prof, llm=None)
    nodes = set(wf.get_graph().nodes.keys())
    assert {
        "gather", "diagnose", "propose", "build", "test", "keep", "judge", "hand_over"
    }.issubset(nodes)


def test_gather_drops_heldout_records_before_inspection(prof):
    served = prof.model_hash()
    run_id = "20261018T120000Z-aaaaaa"
    run_dir = prof.run_dir() / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "graph": {"artifact": served},
        "arms": [{"arm": "treatment", "mode": "rules"}],
        "sets": {"golden_v1": {}, "heldout": {}, "heldout_leasing_v1": {}},
    }))
    records = [
        {"set": "golden_v1", "id": "G-01", "arm": "treatment", "ok": True, "question": "Q1"},
        {"set": "heldout", "id": "H-01", "arm": "treatment", "ok": False, "question": "Secret heldout Q",
         "answer": {"behaviour": "abstain", "reason": "No glossary term recognised in the question."}},
        {"set": "heldout_leasing_v1", "id": "HL-01", "arm": "treatment", "ok": False, "question": "Secret unit heldout Q",
         "answer": {"behaviour": "abstain", "reason": "No glossary term recognised in the question."}},
    ]
    (run_dir / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

    out = gather_mod.gather(prof)
    assert out["ab_misses"] == []
    assert "heldout" not in out["evidence"]["before_sets"]
    assert "heldout_leasing_v1" not in out["evidence"]["before_sets"]
    assert all(not k.startswith("heldout") for k in out["evidence"]["learnable_passes"])


def test_keep_decision_drops_regression_or_zero_gain():
    evidence = {"learnable_passes": {"golden_v1:G-01": True, "team_leasing_v1:T-01": False}}
    edits = [{"id": "alias:lease income", "questions": ["Lease income for June 2025"], "proposer": "rules"}]

    kept = abtest.keep_decision(
        evidence,
        [
            {"set": "golden_v1", "id": "G-01", "arm": "treatment", "ok": True},
            {"set": "team_leasing_v1", "id": "T-01", "arm": "treatment", "ok": True},
        ],
        edits,
    )
    assert kept["kept"] is True
    assert kept["gained"] == ["team_leasing_v1:T-01"]
    assert kept["fell"] == []

    regressed = abtest.keep_decision(
        evidence,
        [
            {"set": "golden_v1", "id": "G-01", "arm": "treatment", "ok": False},
            {"set": "team_leasing_v1", "id": "T-01", "arm": "treatment", "ok": True},
        ],
        edits,
    )
    assert regressed["kept"] is False
    assert regressed["fell"] == ["golden_v1:G-01"]
    assert any("regression" in n["why"] for n in regressed["dropped_notes"])

    no_gain = abtest.keep_decision(
        evidence,
        [
            {"set": "golden_v1", "id": "G-01", "arm": "treatment", "ok": True},
            {"set": "team_leasing_v1", "id": "T-01", "arm": "treatment", "ok": False},
        ],
        edits,
    )
    assert no_gain["kept"] is False
    assert any("no learnable item gained" in n["why"] for n in no_gain["dropped_notes"])


def test_proposal_governance_accept_certify_and_v05_gate(prof):
    base_pack = packs.read(prof.clients, prof.client, lock=True, identities=prof.identities)
    base_text = (prof.clients / prof.client / "pack.yaml").read_text()

    cand_data = yaml.safe_load(base_text)
    cand_data["manifest"]["version"] = "1.3.0"
    cand_data.setdefault("terminology", {}).setdefault("aliases", []).append(
        {"phrase": "top-line turnover", "term": "revenue"}
    )

    pid = "open"
    cand_hash = "b" * 64
    pdir = prof.clients / prof.client / "proposals" / pid
    (pdir / prof.client).mkdir(parents=True)
    (pdir / prof.client / "pack.yaml").write_text(yaml.safe_dump(cand_data, sort_keys=False))
    prop = {
        "format": 1,
        "id": pid,
        "base_hash": base_pack.hash,
        "served_hash": "a" * 64,
        "base_version": "1.2.0",
        "next_version": "1.3.0",
        "candidate_hash": cand_hash,
        "created_at": "2026-10-18T12:00:00Z",
        "proposer": "rules",
        "state": "drafted",
        "kept": True,
        "after_run_id": "20261018T120000Z-bbbbbb",
        "items": [{
            "id": "alias:top-line turnover",
            "kind": "alias",
            "section": "terminology.aliases",
            "phrase": "top-line turnover",
            "term": "revenue",
            "proposer": "rules",
            "why": "Comment names top-line turnover.",
            "evidence": "the comment on retail.revenue.amount",
            "state": "drafted",
            "accepted_by": None,
            "accepted_on": None,
            "certified_by": None,
            "certified_on": None,
        }],
    }
    abtest.write_proposal_record(prof, pid, prop)
    abtest.sync_proposal_pack_to_store(prof, pid)

    checks = margin.v05_proposal(prof.at(cand_hash))
    assert len(checks) == 1 and checks[0].ok is False and "drafted" in checks[0].line

    with pytest.raises(rel.Unreleasable, match="uncertified item"):
        history.review_proposals(prof, cand_hash, released_before=False)

    with pytest.raises(onboard.OnboardError) as exc:
        onboard.certify(prof, proposal=pid, item="all", as_="steward_glossary", on="2026-10-18")
    assert exc.value.status == 409

    with pytest.raises(onboard.OnboardError) as exc:
        onboard.accept(prof, proposal=pid, item="all", as_="steward_glossary", on="2026-10-18")
    assert exc.value.status == 403

    accepted = onboard.accept(prof, proposal=pid, item="all", as_="business_team", on="2026-10-18")
    assert accepted["items"][0]["state"] == "accepted"
    assert (prof.clients / prof.client / "pack.yaml").read_text() == base_text, \
        "pack.yaml stays untouched until all proposal items are certified"

    with pytest.raises(onboard.OnboardError) as exc:
        onboard.certify(prof, proposal=pid, item="all", as_="business_team", on="2026-10-18")
    assert exc.value.status == 403

    certified = onboard.certify(prof, proposal=pid, item="all", as_="steward_glossary", on="2026-10-18")
    assert certified["items"][0]["state"] == "certified"
    updated_pack = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())
    assert updated_pack["manifest"]["version"] == "1.3.0"
    assert {"phrase": "top-line turnover", "term": "revenue"} in updated_pack["terminology"]["aliases"]

    checks = margin.v05_proposal(prof.at(cand_hash))
    assert len(checks) == 1 and checks[0].ok is True
    history.review_proposals(prof, cand_hash, released_before=False)


def test_stale_proposal_is_refused_if_pack_moved_on(prof):
    pid = "open"
    pdir = prof.clients / prof.client / "proposals" / pid
    (pdir / prof.client).mkdir(parents=True)
    (pdir / prof.client / "pack.yaml").write_text((prof.clients / prof.client / "pack.yaml").read_text())
    prop = {
        "format": 1,
        "id": pid,
        "base_hash": "0" * 64,
        "next_version": "1.3.0",
        "candidate_hash": "b" * 64,
        "kept": True,
        "after_run_id": "20261018T120000Z-bbbbbb",
        "items": [{
            "id": "alias:x",
            "kind": "alias",
            "phrase": "x",
            "term": "revenue",
            "accepted_by": "business_team",
            "accepted_on": "2026-10-18",
            "certified_by": None,
            "certified_on": None,
        }],
    }
    abtest.write_proposal_record(prof, pid, prop)
    with pytest.raises(onboard.OnboardError, match="pack changed since this proposal") as exc:
        onboard.certify(prof, proposal=pid, item="all", as_="steward_glossary", on="2026-10-18")
    assert exc.value.status == 409


def test_judge_heldout_reads_nested_sets_and_manifest_items(tmp_path):
    before_dir = tmp_path / "runs" / "before"
    after_dir = tmp_path / "runs" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)

    manifest = {
        "sets": {
            "golden_v1": {"items": 10},
            "heldout_v1": {"items": 18},
            "heldout_leasing_v1": {"items": 2},
        }
    }
    (before_dir / "manifest.json").write_text(json.dumps(manifest))
    (after_dir / "manifest.json").write_text(json.dumps(manifest))

    (before_dir / "summary.json").write_text(json.dumps({
        "sets": {
            "golden_v1": {"treatment": {"all": {"passed": {"mean": 10.0}}}},
            "heldout_v1": {"treatment": {"all": {"passed": {"mean": 13.0}}}},
            "heldout_leasing_v1": {"treatment": {"all": {"passed": {"mean": 1.0}}}},
        }
    }))
    (after_dir / "summary.json").write_text(json.dumps({
        "sets": {
            "golden_v1": {"treatment": {"all": {"passed": {"mean": 10.0}}}},
            "heldout_v1": {"treatment": {"all": {"passed": {"mean": 13.0}}}},
            "heldout_leasing_v1": {"treatment": {"all": {"passed": {"mean": 2.0}}}},
        }
    }))
    res = abtest.judge_heldout(before_dir, after_dir)
    assert res["heldout_fell"] is False
    by_set = {r["set"]: r for r in res["heldout_table"]}
    assert by_set["heldout_v1"]["before_passed"] == 13
    assert by_set["heldout_v1"]["after_passed"] == 13
    assert by_set["heldout_v1"]["of"] == 18
    assert by_set["heldout_leasing_v1"]["before_passed"] == 1
    assert by_set["heldout_leasing_v1"]["after_passed"] == 2
    assert by_set["heldout_leasing_v1"]["of"] == 2

    (after_dir / "summary.json").write_text(json.dumps({
        "sets": {
            "heldout_v1": {"treatment": {"all": {"passed": {"mean": 12.0}}}},
            "heldout_leasing_v1": {"treatment": {"all": {"passed": {"mean": 2.0}}}},
        }
    }))
    res_fell = abtest.judge_heldout(before_dir, after_dir)
    assert res_fell["heldout_fell"] is True


def test_unkept_draft_proposal_is_hidden_and_refused_and_cleaned_up_on_failure(prof, monkeypatch):
    pid = "open"
    unkept = {
        "format": 1,
        "id": pid,
        "base_hash": "a" * 64,
        "next_version": "1.3.0",
        "candidate_hash": "b" * 64,
        "state": "drafted",
        "kept": False,
        "after_run_id": None,
        "items": [{"id": "alias:x", "kind": "alias", "phrase": "x", "term": "revenue", "state": "drafted"}],
    }
    abtest.write_proposal_record(prof, pid, unkept)
    abtest.sync_proposal_pack_to_store(prof, pid)

    shown = onboard.show(prof)
    assert shown["proposal"] is None

    with pytest.raises(onboard.OnboardError, match="has not passed its candidate A/B check") as exc:
        onboard.accept(prof, proposal=pid, item="all", as_="business_team", on="2026-10-18")
    assert exc.value.status == 409

    def fake_build(p, prop_id, edits, ev):
        abtest.write_proposal_record(p, prop_id, unkept)
        abtest.sync_proposal_pack_to_store(p, prop_id)
        return {
            "next_version": "1.3.0",
            "kept_edits": edits,
            "compiler_refused": [],
            "candidate_hash": "b" * 64,
        }

    def boom_test(p, cand_hash):
        raise RuntimeError("simulated test_candidate failure")

    monkeypatch.setattr(abtest, "build_candidate", fake_build)
    monkeypatch.setattr(abtest, "test_candidate", boom_test)
    monkeypatch.setattr(gather_mod, "gather", lambda p: {
        "ab_misses": [{
            "question": "Top-line turnover for June 2025",
            "set": "team_v1",
            "id": "T1",
            "reason": "No glossary term recognised in the question.",
        }],
        "page_misses": [],
        "evidence": {
            "base_pack_hash": "a" * 64,
            "base_version": "1.2.0",
            "served_hash": p.model_hash(),
            "before_run_id": "before-1",
            "before_run_dir": str(p.run_dir() / "runs" / "before-1"),
            "before_sets": {},
            "learnable_passes": {"team_v1:T1": False},
            "certified_terms": {"revenue": {"id": "revenue", "name": "Revenue", "status": "certified"}},
            "existing_aliases": {"revenue": "revenue"},
            "candidate_phrases": [],
            "columns": {
                "retail.revenue.amount": {
                    "column": "retail.revenue.amount",
                    "unit": "retail",
                    "table": "revenue",
                    "name": "amount",
                    "type": "DECIMAL(14,2)",
                    "description": "Booked revenue in USD; also called top-line turnover",
                    "is_key": False,
                    "certified_terms": ["revenue"],
                }
            },
            "term_columns": {"revenue": ["retail.revenue.amount"]},
        },
    })

    with pytest.raises(RuntimeError, match="simulated test_candidate failure"):
        flow.run_improve(prof, llm=None)

    assert onboard.show(prof)["proposal"] is None
    assert not (prof.clients / prof.client / "proposals").exists()

    monkeypatch.setattr(abtest, "test_candidate", lambda p, ch: {
        "after_run_id": "after-1",
        "after_run_dir": str(p.run_dir() / "runs" / "after-1"),
        "after_learnable_records": [{"set": "team_v1", "id": "T1", "arm": "treatment", "ok": True}],
        "gate_lines": ["V-01 ok"],
    })
    monkeypatch.setattr(abtest, "judge_heldout", lambda bdir, adir: {
        "heldout_table": [{"set": "heldout_v1", "before_passed": 13, "after_passed": 12, "of": 18, "delta": -1}],
        "heldout_fell": True,
    })
    fell_state = flow.run_improve(prof, llm=None)
    assert fell_state["proposal"] is None
    assert any("held-out fell" in s for s in fell_state["status_lines"])
    assert onboard.show(prof)["proposal"] is None
    assert not (prof.clients / prof.client / "proposals").exists()


def test_improve_real_subprocesses_end_to_end_on_scratch_fellmoor(tmp_path):
    import subprocess
    import sys

    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients/fellmoor", clients / "fellmoor")
    shutil.copytree(DEMO / "deploy/fellmoor", tmp_path / "deploy/fellmoor")

    warehouses = tmp_path / "fellmoor_estate" / "warehouses"
    profiles_yaml = tmp_path / "profiles.yaml"
    profiles_yaml.write_text(yaml.safe_dump({
        "default": "fellmoor",
        "profiles": {
            "fellmoor": {
                "client": "fellmoor",
                "clients": str(clients),
                "artifacts": str(tmp_path / "artifacts"),
                "store": str(tmp_path / "stores"),
                "landing": str(tmp_path / "landing"),
                "warehouses": str(warehouses),
                "identities": str(tmp_path / "deploy/fellmoor/identities.yaml"),
                "baseline": None,
            }
        },
    }))
    prof = Profile(
        "fellmoor", "fellmoor", clients, tmp_path / "artifacts", tmp_path / "stores",
        tmp_path / "landing", warehouses, tmp_path / "deploy/fellmoor/identities.yaml",
        None, profiles_yaml,
    )
    sub_env = abtest._sub_env(prof, force_rules=True)

    def run_cmd(*args):
        res = subprocess.run([sys.executable, *args], cwd=DEMO, env=sub_env, capture_output=True, text=True)
        assert res.returncode == 0, f"{args} failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        return res.stdout.strip()

    run_cmd("kge/estate/from_files.py", "--profile", "fellmoor")
    run_cmd("kge/estate/certify.py", "--profile", "fellmoor")

    pack_path = clients / "fellmoor/pack.yaml"
    pack_path.write_text(
        pack_path.read_text() + "    - {name: team_fleet, version: v1}\n"
    )
    lock_path = clients / "fellmoor/pack.lock"
    lock_data = json.loads(lock_path.read_text())
    for tbl in lock_data["sources"]["fleet"]["tables"]:
        if tbl["name"] == "repair":
            for col in tbl["columns"]:
                if col["name"] == "cost_usd":
                    col["description"] = (
                        "Parts and labour charged to the repair, in USD; "
                        "the business calls this workshop charges"
                    )
    lock_path.write_text(json.dumps(lock_data, indent=1, sort_keys=True) + "\n")

    (prof.estate() / "team_fleet_v1.json").write_text(json.dumps([
        {
            "id": "FT01",
            "kind": "within",
            "question": "Workshop charges for June 2025",
            "expect": {
                "figure": "19012.14",
                "unit": "fleet",
                "columns": ["repair.cost_usd"],
                "join_path": [],
            },
        }
    ]))

    base_out = run_cmd(
        "kge/compiler/compile.py", "--profile", "fellmoor",
        "--clients", str(clients), "--client", "fellmoor", "--out", str(prof.artifacts),
    )
    base_hash = Path(base_out.splitlines()[-1].strip().rstrip("/")).name
    run_cmd("kge/graph/load.py", "--profile", "fellmoor", "--artifact", str(prof.artifacts / "fellmoor" / base_hash))
    run_cmd("kge/pipeline/run.py", "--profile", "fellmoor", "--hash", base_hash)
    run_cmd("kge/graph/release.py", "--profile", "fellmoor", "--hash", base_hash, "--mark-loaded")
    run_cmd(
        "kge/graph/release.py", "--profile", "fellmoor",
        "--hash", base_hash, "--as", "fares_steward", "--ungated",
    )
    subprocess.run(
        [sys.executable, "-m", "kge.eval.margin", "--profile", "fellmoor", "--hash", base_hash, "--repeats", "1", "--baseline", ""],
        cwd=DEMO, env=sub_env, check=False, capture_output=True, text=True,
    )

    summary = flow.run_improve(prof, llm=None)
    assert summary["kept"] is True
    assert summary["candidate_hash"] and summary["candidate_hash"] != base_hash
    assert summary["next_version"] == "1.1.0"
    assert "team_fleet_v1:FT01" in summary["gained"]
    assert summary["fell"] == []
    assert summary["heldout_fell"] is False
    by_set = {r["set"]: r for r in summary["heldout_table"]}
    assert by_set["heldout"]["after_passed"] == 3
    assert by_set["heldout"]["of"] == 3
