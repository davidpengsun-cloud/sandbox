"""Version history ledger, scores, and rollback pack checks."""
from pathlib import Path
import hashlib
import json
import shutil

import pytest
import yaml

from kge.compiler import pack as packs
from kge.graph import history, release as rel
from kge.improve import abtest
from kge.adapters.model import DIM
from kge.pipeline.profile import Profile

DEMO = Path(__file__).resolve().parents[2]


def _make_artifact(prof: Profile) -> str:
    p = packs.read(prof.clients, prof.client, lock=True, identities=prof.identities)
    version = str((p.data.get("manifest") or {}).get("version") or "1.0.0")
    rel_files = {
        "ddl/kuzu.cypher": b"// ddl\n",
        "access/roles.json": b'{"roles": []}\n',
        "graph-model.yaml": f"version: {version}\n".encode(),
    }
    listed = {k: hashlib.sha256(v).hexdigest() for k, v in sorted(rel_files.items())}
    model_hash = hashlib.sha256("".join(f"{listed[k]}  {k}\n" for k in sorted(listed)).encode()).hexdigest()
    folder = prof.artifacts / prof.client / model_hash
    folder.mkdir(parents=True, exist_ok=True)
    for k, v in rel_files.items():
        fp = folder / k
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(v)
    manifest = {
        "format": "graph-model/v1",
        "client": prof.client,
        "title": "Meridian",
        "model_hash": model_hash,
        "meta_model_version": "1.2.0",
        "params": {"embedding_dim": DIM},
        "pack": {"id": prof.client, "version": version, "hash": p.hash},
        "files": listed,
        "load_order": [],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    gpath = prof.at(model_hash).graph_path()
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("stub")
    rel.write_marker(gpath.parent, {rel.store_of(gpath): {"client": prof.client, "model_hash": model_hash, "release": "r1", "chunks": 1}}, rel.LOADED)
    return model_hash


@pytest.fixture
def prof(tmp_path):
    clients = tmp_path / "clients"
    target = clients / "meridian"
    target.mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", target / "pack.yaml")
    shutil.copy(DEMO / "clients/meridian/pack.lock", target / "pack.lock")
    return Profile(
        "meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
        tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "profiles.yaml"
    )


def test_release_records_versions_and_rollback_restores_pack(prof):
    """The gate's run is found through graph.artifact even when gate_run_id is None, and a rollback is refused while the
    store's pack has unreleased edits."""
    pack_v1 = (prof.clients / prof.client / "pack.yaml").read_text()
    unit_dir = prof.clients / prof.client / "units" / "leasing"
    unit_dir.mkdir(parents=True)
    (unit_dir / "unit.yaml").write_text("format: 1\nunit: leasing\nlabel: Asset Finance\nrows: {origin: none}\n")
    (unit_dir / "draft.yaml").write_text("format: 1\nunit: leasing\nitems: []\n")
    h1 = _make_artifact(prof)

    rel.release(prof, h1, ungated=True)
    vlist = history.list_versions(prof)["versions"]
    assert len(vlist) == 1
    assert vlist[0]["version"] == "1.5.0"
    assert vlist[0]["model_hash"] == h1
    assert vlist[0]["current"] is True
    assert vlist[0]["kept_pack"] is True

    pack_v2_data = yaml.safe_load(pack_v1)
    pack_v2_data["manifest"]["version"] = "1.6.0"
    pack_v2_data["terminology"]["aliases"].append({"phrase": "top-line turnover", "term": "revenue"})
    pack_v2 = yaml.safe_dump(pack_v2_data, sort_keys=False)
    (prof.clients / prof.client / "pack.yaml").write_text(pack_v2)
    h2 = _make_artifact(prof)

    run_id = "20261018T120000Z-222222"
    run_dir = prof.run_dir() / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "graph": {"artifact": h2},
        "arms": [{"arm": "treatment", "mode": "rules"}],
        "sets": {
            "golden_v1": {"items": 20},
            "team_leasing_v1": {"items": 1},
            "heldout": {"items": 12},
            "heldout_leasing_v1": {"items": 1},
        },
    }))
    (run_dir / "summary.json").write_text(json.dumps({
        "sets": {
            "golden_v1": {"treatment": {"all": {"passed": {"mean": 20}}}},
            "team_leasing_v1": {"treatment": {"all": {"passed": {"mean": 1}}}},
            "heldout": {"treatment": {"all": {"passed": {"mean": 12}}}},
            "heldout_leasing_v1": {"treatment": {"all": {"passed": {"mean": 1}}}},
        }
    }))
    found_run_id, found_scores = history.compute_scores(prof, h2, None)
    assert found_run_id == run_id
    assert found_scores["learnable"] == {"passed": 21, "total": 21}

    abtest.write_proposal_record(prof, "open", {
        "format": 1,
        "id": "open",
        "kept": True,
        "next_version": "1.6.0",
        "candidate_hash": h2,
        "proposer": "rules",
        "after_run_id": run_id,
        "change_note": "Add alias 'top-line turnover' -> revenue (by rules; A/B: team_leasing_v1 0 -> 1 of 1)",
        "items": [{
            "id": "alias:top-line turnover",
            "kind": "alias",
            "phrase": "top-line turnover",
            "term": "revenue",
            "accepted_by": "business_team",
            "accepted_on": "2026-10-18",
            "certified_by": "steward_glossary",
            "certified_on": "2026-10-18",
        }],
    })
    g2 = prof.at(h2).graph_path()
    rel.mark_gate(g2, {"passed": True, "run_id": run_id, "model_hash": h2})
    rel.put(g2, rel.SIGNOFF, {"principal": "steward_glossary", "at": "2026-10-18T12:01:00Z", "run_id": run_id, "model_hash": h2})

    rel.release(prof, h2, ungated=False)
    vlist = history.list_versions(prof)["versions"]
    assert len(vlist) == 2
    assert vlist[1]["version"] == "1.6.0"
    assert vlist[1]["signed_off_by"] == "steward_glossary"
    assert vlist[1]["scores"]["learnable"] == {"passed": 21, "total": 21}
    assert vlist[1]["scores"]["heldout"] == {"passed": 13, "total": 13}
    assert "top-line turnover" in vlist[1]["note"]
    assert vlist[1]["current"] is True
    assert vlist[0]["current"] is False

    dirty_data = yaml.safe_load(pack_v2)
    dirty_data["manifest"]["version"] = "9.9.9"
    (prof.clients / prof.client / "pack.yaml").write_text(yaml.safe_dump(dirty_data, sort_keys=False))
    with pytest.raises(rel.Unreleasable, match="holds changes not yet released"):
        rel.release(prof, h1, ungated=False)

    (prof.clients / prof.client / "pack.yaml").write_text(pack_v2)
    rel.release(prof, h1, ungated=False)
    assert yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())["manifest"]["version"] == "1.5.0"
    assert (unit_dir / "unit.yaml").is_file()
    vlist = history.list_versions(prof)["versions"]
    assert len(vlist) == 3
    assert vlist[-1]["version"] == "1.5.0"
    assert vlist[-1]["rollback_of"] == h2
    assert vlist[-1]["current"] is True
    assert vlist[0]["current"] is False, "only the latest rollback row is marked current"


def test_last_unit_reset_allows_same_hash_on_second_walkthrough(prof):
    from kge.adapters import released
    from kge.estate import onboard
    from kge.pipeline.overlay import Journal

    h0 = _make_artifact(prof)
    rel.release(prof, h0, ungated=True)
    base = (prof.clients / prof.client / "pack.yaml").read_bytes()
    candidate = yaml.safe_load(base)
    candidate["manifest"]["version"] = "1.6.0"
    hashes = []
    other = prof.store / "another-client" / h0 / rel.RELEASED
    other.parent.mkdir(parents=True)
    other.write_text("untouched")

    for _ in range(2):
        unit_dir = prof.clients / prof.client / "units" / "leasing"
        unit_dir.mkdir(parents=True)
        (unit_dir / "original-pack.yaml").write_bytes(base)
        shutil.copy2(prof.clients / prof.client / "pack.lock", unit_dir / "original-pack.lock")
        (unit_dir / "original.json").write_text(json.dumps({"format": 1, "current": h0, "lock": True}))
        (unit_dir / "unit.yaml").write_text("format: 1\nunit: leasing\n")
        (prof.clients / prof.client / "pack.yaml").write_text(yaml.safe_dump(candidate, sort_keys=False))
        h1 = _make_artifact(prof)
        hashes.append(h1)
        graph = prof.at(h1).graph_path()
        with pytest.raises(rel.Unreleasable, match="not checked since it was loaded"):
            rel.release(prof, h1)
        rel.mark_gate(graph, {"passed": True, "run_id": "checked", "model_hash": h1})
        rel.put(graph, rel.SIGNOFF, {"principal": "steward_glossary", "run_id": "checked", "model_hash": h1})
        rel.release(prof, h1)
        assert history.list_versions(prof)["versions"][-1]["rollback_of"] is None
        assert history.has_kept_pack(prof, h1)
        released.write(graph.parent, h1, {"generation": "kept-for-server"}, None)
        record = released.path(graph.parent).read_bytes()
        ledger = (prof.store / prof.client / rel.SERVED).read_bytes()

        assert onboard.reset(prof, "leasing")["current"] == h0
        assert not history.has_kept_pack(prof, h1)
        assert history.has_kept_pack(prof, h0)
        for name in (rel.LOADED, rel.GATE, rel.SIGNOFF, rel.RELEASED):
            assert not (graph.parent / name).exists()
        assert released.path(graph.parent).read_bytes() == record
        assert (prof.store / prof.client / rel.SERVED).read_bytes() == ledger
        assert other.read_text() == "untouched"

        Journal(prof.overlay_dir(), prof.client).reset()
        rel.unmark(prof.at(h0).graph_path())
        assert _make_artifact(prof) == h0
        rel.release(prof, h0, ungated=True)
        assert rel.marked(graph, rel.RELEASED) is None
        with pytest.raises(rel.Unreleasable, match="not LOADED"):
            rel.release(prof, h1)

    assert hashes[0] == hashes[1]


@pytest.mark.parametrize("remaining_unit", [False, True], ids=["last-unit", "partial-reset"])
def test_reset_preserves_other_clients_and_partial_approvals_across_stores(prof, remaining_unit):
    from kge.estate import onboard

    h0 = _make_artifact(prof)
    rel.release(prof, h0, ungated=True)
    pack = prof.clients / prof.client / "pack.yaml"
    unit_dir = pack.parent / "units" / "leasing"
    unit_dir.mkdir(parents=True)
    (unit_dir / "original-pack.yaml").write_bytes(pack.read_bytes())
    shutil.copy2(pack.with_suffix(".lock"), unit_dir / "original-pack.lock")
    (unit_dir / "original.json").write_text(json.dumps({"format": 1, "current": h0, "lock": True}))
    (unit_dir / "unit.yaml").write_text("format: 1\nunit: leasing\n")
    if remaining_unit:
        other_unit = unit_dir.parent / "factoring"
        other_unit.mkdir()
        (other_unit / "unit.yaml").write_text("format: 1\nunit: factoring\n")
    candidate = yaml.safe_load(pack.read_bytes())
    candidate["manifest"]["version"] = "1.6.0"
    pack.write_text(yaml.safe_dump(candidate, sort_keys=False))
    h1 = _make_artifact(prof)
    assert h1 != h0
    folders = [prof.store / prof.client / h0, prof.store / prof.client / h1,
               prof.store / "another-client" / h1]
    before = {}
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        for name in (rel.LOADED, rel.GATE, rel.SIGNOFF, rel.RELEASED):
            rel.write_marker(folder, {
                backend: {"model_hash": folder.name, "client": folder.parent.name,
                          "passed": True, "run_id": "checked", "principal": "steward_glossary"}
                for backend in ("ladybug", "bigquery")
            }, name)
            before[folder / name] = (folder / name).read_bytes()

    assert onboard.reset(prof, "leasing")["current"] == h0
    assert not unit_dir.exists()
    if remaining_unit:
        assert (other_unit / "unit.yaml").is_file()
    for path, contents in before.items():
        if remaining_unit or path.parent != folders[1]:
            assert path.read_bytes() == contents, str(path)
        else:
            assert not path.exists(), str(path)


@pytest.mark.parametrize("ungated", [False, True])
def test_missing_kept_pack_without_reset_still_refuses_rollback(prof, ungated):
    h0 = _make_artifact(prof)
    rel.release(prof, h0, ungated=True)
    pack = prof.clients / prof.client / "pack.yaml"
    data = yaml.safe_load(pack.read_text())
    data["manifest"]["version"] = "1.6.0"
    pack.write_text(yaml.safe_dump(data, sort_keys=False))
    h1 = _make_artifact(prof)
    rel.release(prof, h1, ungated=True)
    shutil.rmtree(prof.artifacts / prof.client / "versions" / h0)
    before = history.versions_path(prof).read_bytes(), pack.read_bytes()
    with pytest.raises(rel.Unreleasable, match="no kept pack"):
        rel.release(prof, h0, ungated=ungated)
    assert prof.model_hash() == h1
    assert (history.versions_path(prof).read_bytes(), pack.read_bytes()) == before


def test_rollback_to_pre_unit_snapshot_removes_unit_files_and_reset_keeps_history(prof):
    from kge.estate import onboard
    h0 = _make_artifact(prof)
    rel.release(prof, h0, ungated=True)

    unit_dir = prof.clients / prof.client / "units" / "leasing"
    unit_dir.mkdir(parents=True)
    (unit_dir / "unit.yaml").write_text("format: 1\nunit: leasing\nlabel: Asset Finance\nrows: {origin: none}\n")
    (unit_dir / "draft.yaml").write_text("format: 1\nunit: leasing\nitems: []\n")
    prof.warehouses.mkdir(parents=True, exist_ok=True)
    (prof.warehouses / "leasing.duckdb").write_bytes(b"stub")
    est = prof.estate()
    est.mkdir(parents=True, exist_ok=True)
    for stem in ("golden_leasing_v1.json", "team_leasing_v1.json", "heldout_leasing_v1.json"):
        (est / stem).write_text("{}\n")
    doc_file = est / "docs" / "uncertified" / "DD-LEASING.md"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text("# DD\n")
    (est / "documents.json").write_text(json.dumps([{"doc_id": "DD-LEASING"}, {"doc_id": "MD-REVENUE"}]))

    pack_data = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())
    pack_data["manifest"]["version"] = "1.6.0"
    (prof.clients / prof.client / "pack.yaml").write_text(yaml.safe_dump(pack_data, sort_keys=False))
    h1 = _make_artifact(prof)
    rel.release(prof, h1, ungated=True)

    rel.release(prof, h0, ungated=False)
    assert not unit_dir.exists()
    assert not (prof.warehouses / "leasing.duckdb").exists()
    for stem in ("golden_leasing_v1.json", "team_leasing_v1.json", "heldout_leasing_v1.json"):
        assert not (est / stem).exists()
    assert not doc_file.exists()
    assert [d["doc_id"] for d in json.loads((est / "documents.json").read_text())] == ["MD-REVENUE"]

    res = onboard.reset(prof, "leasing")
    assert res["reset"] == "nothing"
    assert len(history.list_versions(prof)["versions"]) == 3
    assert (prof.artifacts / prof.client / "versions" / h1 / "pack.yaml").is_file()


def test_release_refuses_missing_warehouse_missing_kept_pack_and_corrupted_rebuild(prof, monkeypatch, capsys):
    """Whichever step of a release or rollback fails, the rebuild, the warehouse check, the swap or the versions.jsonl
    write, current, versions.jsonl and the store are left or put back as they were, a newly written RELEASED marker
    goes, and the warehouses of units the rollback would drop survive."""
    import runpy
    import sys
    from kge.estate import onboard
    from kge.pipeline import profile as profile_mod
    h0 = _make_artifact(prof)
    h0_unit_csv = prof.artifacts / prof.client / h0 / "data" / "nodes" / "Unit.csv"
    h0_unit_csv.parent.mkdir(parents=True, exist_ok=True)
    h0_unit_bytes = b"id,name\nsme,SME\n"
    h0_unit_csv.write_bytes(h0_unit_bytes)
    h0_man_path = prof.artifacts / prof.client / h0 / "manifest.json"
    h0_man = json.loads(h0_man_path.read_text())
    h0_man["files"]["data/nodes/Unit.csv"] = hashlib.sha256(h0_unit_bytes).hexdigest()
    new_h0 = hashlib.sha256("".join(f"{h0_man['files'][k]}  {k}\n" for k in sorted(h0_man["files"])).encode()).hexdigest()
    h0_man["model_hash"] = new_h0
    h0_man_path.write_text(json.dumps(h0_man, indent=2) + "\n")
    (prof.artifacts / prof.client / h0).rename(prof.artifacts / prof.client / new_h0)
    shutil.rmtree(prof.at(h0).graph_path().parent, ignore_errors=True)
    h0 = new_h0
    g0 = prof.at(h0).graph_path()
    g0.parent.mkdir(parents=True, exist_ok=True)
    g0.write_text("stub")
    rel.write_marker(g0.parent, {rel.store_of(g0): {"client": prof.client, "model_hash": h0, "release": "r1", "chunks": 1}}, rel.LOADED)
    prof.warehouses.mkdir(parents=True, exist_ok=True)
    (prof.warehouses / "sme.duckdb").write_bytes(b"sme-stub")
    rel.release(prof, h0, ungated=True)

    unit_dir = prof.clients / prof.client / "units" / "leasing"
    unit_dir.mkdir(parents=True)
    shutil.copy2(prof.clients / prof.client / "pack.yaml", unit_dir / "original-pack.yaml")
    shutil.copy2(prof.clients / prof.client / "pack.lock", unit_dir / "original-pack.lock")
    (unit_dir / "original.json").write_text(json.dumps({"format": 1, "current": h0, "lock": True}))
    (unit_dir / "unit.yaml").write_text("format: 1\nunit: leasing\nlabel: Asset Finance\nrows: {origin: none}\n")
    (unit_dir / "draft.yaml").write_text("format: 1\nunit: leasing\nitems: []\n")

    pack_data = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())
    pack_data["manifest"]["version"] = "1.6.0"
    (prof.clients / prof.client / "pack.yaml").write_text(yaml.safe_dump(pack_data, sort_keys=False))
    h1 = _make_artifact(prof)
    unit_csv = prof.artifacts / prof.client / h1 / "data" / "nodes" / "Unit.csv"
    unit_csv.parent.mkdir(parents=True, exist_ok=True)
    unit_csv_bytes = b"id,name\nleasing,Asset Finance\n"
    unit_csv.write_bytes(unit_csv_bytes)
    man_path = prof.artifacts / prof.client / h1 / "manifest.json"
    man = json.loads(man_path.read_text())
    man["files"]["data/nodes/Unit.csv"] = hashlib.sha256(unit_csv_bytes).hexdigest()
    new_h1 = hashlib.sha256("".join(f"{man['files'][k]}  {k}\n" for k in sorted(man["files"])).encode()).hexdigest()
    man["model_hash"] = new_h1
    man_path.write_text(json.dumps(man, indent=2) + "\n")
    (prof.artifacts / prof.client / h1).rename(prof.artifacts / prof.client / new_h1)
    shutil.rmtree(prof.at(h1).graph_path().parent, ignore_errors=True)
    h1 = new_h1
    gpath = prof.at(h1).graph_path()
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("stub")
    rel.write_marker(gpath.parent, {rel.store_of(gpath): {"client": prof.client, "model_hash": h1, "release": "r1", "chunks": 1}}, rel.LOADED)

    v_before = history.list_versions(prof)["versions"]
    with pytest.raises(rel.Unreleasable, match=r"no \S*leasing\.duckdb: the graph.s units each need a warehouse"):
        rel.release(prof, h1, ungated=True)
    assert prof.model_hash() == h0
    assert history.list_versions(prof)["versions"] == v_before

    (prof.warehouses / "leasing.duckdb").write_bytes(b"stub")
    rel.release(prof, h1, ungated=True)
    assert prof.model_hash() == h1

    onboard.reset(prof, "leasing")
    assert history.has_kept_pack(prof, h0) is True

    real_snapshot_pack = history.snapshot_pack
    (unit_dir / "original-pack.yaml").parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prof.clients / prof.client / "pack.yaml", unit_dir / "original-pack.yaml")
    shutil.copy2(prof.clients / prof.client / "pack.lock", unit_dir / "original-pack.lock")
    (unit_dir / "original.json").write_text(json.dumps({"format": 1, "current": h0, "lock": True}))
    (unit_dir / "unit.yaml").write_text("format: 1\nunit: leasing\n")
    shutil.rmtree(prof.artifacts / prof.client / "versions" / h0, ignore_errors=True)
    monkeypatch.setattr(history, "snapshot_pack", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")))
    onboard.reset(prof, "leasing")
    assert "reset: snapshot_pack failed: disk full" in capsys.readouterr().err
    monkeypatch.setattr(history, "snapshot_pack", real_snapshot_pack)

    shutil.rmtree(prof.artifacts / prof.client / "versions" / h0, ignore_errors=True)
    assert history.has_kept_pack(prof, h0) is False
    with pytest.raises(rel.Unreleasable, match="no kept pack"):
        rel.release(prof, h0, ungated=False)
    assert prof.model_hash() == h1

    extra_unit = prof.clients / prof.client / "units" / "factoring"
    extra_unit.mkdir(parents=True, exist_ok=True)
    (extra_unit / "unit.yaml").write_text("format: 1\nunit: factoring\n")
    (prof.warehouses / "factoring.duckdb").write_bytes(b"factoring-stub")
    history.snapshot_pack(prof, h0, json.loads((prof.artifacts / prof.client / h0 / "manifest.json").read_text())["pack"]["hash"])
    shutil.rmtree(prof.artifacts / prof.client / "versions" / h0 / "units" / "factoring", ignore_errors=True)
    snap_unit = prof.artifacts / prof.client / "versions" / h0 / "units" / "leasing"
    snap_unit.mkdir(parents=True, exist_ok=True)
    (snap_unit / "unit.yaml").write_text("format: 1\nunit: leasing\n")
    calls = []
    def broken_build(_prof):
        calls.append(1)
        raise ValueError("corrupted questions.json")
    monkeypatch.setattr(onboard, "build", broken_build)
    monkeypatch.setattr(profile_mod, "resolve", lambda _p=None: prof)
    monkeypatch.setattr(rel, "resolve", lambda _p=None: prof)
    monkeypatch.setattr(sys, "argv", ["release.py", "--hash", h0])
    saved_rel = sys.modules.pop("kge.graph.release", None)
    try:
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(Path(rel.__file__)), run_name="__main__")
        assert excinfo.value.code == 2
    finally:
        if saved_rel is not None:
            sys.modules["kge.graph.release"] = saved_rel
            if "graph" in sys.modules:
                setattr(sys.modules["graph"], "release", saved_rel)
    err = capsys.readouterr().err
    assert "release: failed to rebuild onboarded unit(s)" in err
    assert "Unreleasable" not in err
    assert len(calls) == 1
    assert prof.model_hash() == h1
    assert (prof.warehouses / "factoring.duckdb").is_file()

    ok_calls = []
    monkeypatch.setattr(onboard, "build", lambda _prof: ok_calls.append(1))
    (prof.warehouses / "sme.duckdb").unlink()
    with pytest.raises(rel.Unreleasable, match=r"no \S*sme\.duckdb: "):
        rel.release(prof, h0, ungated=False)
    assert prof.model_hash() == h1
    assert (prof.warehouses / "factoring.duckdb").is_file()
    assert (extra_unit / "unit.yaml").is_file()

    (prof.warehouses / "sme.duckdb").write_bytes(b"sme-stub")
    v_before_swap = history.list_versions(prof)["versions"]
    real_swap = rel.swap
    def broken_swap(_prof, _h):
        raise OSError("swap failed")
    monkeypatch.setattr(rel, "swap", broken_swap)
    with pytest.raises(OSError, match="swap failed"):
        rel.release(prof, h0, ungated=False)
    assert prof.model_hash() == h1
    assert history.list_versions(prof)["versions"] == v_before_swap
    assert (prof.warehouses / "factoring.duckdb").is_file()
    assert (extra_unit / "unit.yaml").is_file()

    monkeypatch.setattr(rel, "swap", real_swap)
    real_record = history.record_release
    vpath = history.versions_path(prof)
    history.append_jsonl(vpath, {"version": "1.6.0", "model_hash": h1, "pack_hash": None, "released_at": "2026-01-01T00:00:00Z", "signed_off_by": None, "run_id": None, "scores": {"scored": False, "label": "not scored", "sets": {}, "learnable": None, "heldout": None}, "note": "released by hand", "kept_pack": False, "rollback_of": None})
    vbytes_before_m2 = vpath.read_bytes()
    v_before_m2 = history.list_versions(prof)["versions"]
    def partial_record(*_a, **_kw):
        vpath.write_bytes(vbytes_before_m2 + b'{"partial":true}\n')
        raise OSError("Permission denied: versions.jsonl")
    monkeypatch.setattr(history, "record_release", partial_record)
    rc = rel.main(["--hash", h0])
    assert rc == 2
    err_m2 = capsys.readouterr().err
    assert "release: Permission denied: versions.jsonl. current is as it was." in err_m2
    assert prof.model_hash() == h1
    assert vpath.read_bytes() == vbytes_before_m2
    assert history.list_versions(prof)["versions"] == v_before_m2
    assert (prof.warehouses / "factoring.duckdb").is_file()
    assert (extra_unit / "unit.yaml").is_file()

    h0_gpath = prof.at(h0).graph_path()
    saved_h0_rel = rel.held(h0_gpath.parent, rel.RELEASED)
    rel.write_marker(h0_gpath.parent, {}, rel.RELEASED)
    rel.put(h0_gpath, rel.GATE, {"passed": True, "run_id": "run-h0", "model_hash": h0})
    rel.put(h0_gpath, rel.SIGNOFF, {"principal": "steward_glossary", "at": "2026-01-01T00:00:00Z", "run_id": "run-h0", "model_hash": h0})
    with pytest.raises(OSError, match="Permission denied: versions.jsonl"):
        rel.release(prof, h0, ungated=False)
    assert rel.marked(h0_gpath, rel.RELEASED) is None
    rel.write_marker(h0_gpath.parent, saved_h0_rel, rel.RELEASED)

    swap_count = []
    def swap_once_then_fail(p, h):
        swap_count.append(h)
        if len(swap_count) > 1:
            raise OSError("swap-back failed")
        real_swap(p, h)
    monkeypatch.setattr(rel, "swap", swap_once_then_fail)
    rc_p5 = rel.main(["--hash", h0])
    assert rc_p5 == 2
    err_p5 = capsys.readouterr().err
    assert f"current is now {h0}, not {h1}." in err_p5
    assert prof.model_hash() == h0
    assert history.read_jsonl(vpath)[-1]["model_hash"] == h1
    monkeypatch.setattr(rel, "swap", real_swap)
    monkeypatch.setattr(history, "record_release", real_record)
    rel.release(prof, h0, ungated=False)
    assert history.read_jsonl(vpath)[-1]["model_hash"] == h0
    vpath.write_bytes(vbytes_before_m2)
    real_swap(prof, h1)

    ok_calls.clear()
    rel.release(prof, h0, ungated=False)
    assert len(ok_calls) == 1
    assert prof.model_hash() == h0
    assert not (prof.warehouses / "factoring.duckdb").exists()

    rel.put(gpath, rel.LOADED, {"client": prof.client, "model_hash": h1, "release": "r1", "chunks": 1})
    rel.put(gpath, rel.RELEASED, {"model_hash": h1})
    snap_h1 = prof.artifacts / prof.client / "versions" / h1
    snap_h1_unit = snap_h1 / "units" / "factoring"
    snap_h1_unit.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prof.artifacts / prof.client / "versions" / h0 / "pack.yaml", snap_h1 / "pack.yaml")
    shutil.copy2(prof.artifacts / prof.client / "versions" / h0 / "pack.lock", snap_h1 / "pack.lock")
    (snap_h1_unit / "unit.yaml").write_text("format: 1\nunit: factoring\n")
    (prof.warehouses / "leasing.duckdb").write_bytes(b"leasing-stub")
    def build_factoring(p):
        (p.warehouses / "factoring.duckdb").write_bytes(b"rebuilt-factoring")
        (p.estate() / "golden_factoring_v1.json").write_text("[]")
    monkeypatch.setattr(onboard, "build", build_factoring)
    monkeypatch.setattr(rel, "swap", broken_swap)
    with pytest.raises(OSError, match="swap failed"):
        rel.release(prof, h1, ungated=False)
    assert prof.model_hash() == h0
    assert not (prof.warehouses / "factoring.duckdb").exists()
    assert not (prof.estate() / "golden_factoring_v1.json").exists()






def _loaded_here(prof: Profile, model_hash: str) -> str:
    """_make_artifact's LOADED marker, for the graph store's entry."""
    gpath = prof.at(model_hash).graph_path()
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("stub")
    rel.write_marker(gpath.parent, {rel.store_of(gpath): {"client": prof.client, "model_hash": model_hash, "release": "r1",
                                                          "chunks": 1}}, rel.LOADED)
    return model_hash


def test_a_rollback_on_the_bigquery_state_backend_restores_the_units_manifests_and_keeps_their_history(prof, monkeypatch):
    """With APP_STATE_BACKEND=bigquery a unit's manifest and history are table rows, not units/ files: a rollback puts
    the manifest back as the release had it (before the units are rebuilt from it) and appends to its history rather
    than erasing it; a unit the release did not have loses its rows, its history with them, as the files backend loses
    units/<unit>/; a rollback that fails puts them back as they were."""
    from kge.adapters import appstate
    from tests.unit.test_appstate import TableState
    from kge import packstore
    held = {}
    monkeypatch.setattr(appstate, "for_profile", lambda profile, store: held.setdefault("state", TableState(store)))
    state = TableState(packstore.for_profile(prof)); held["state"] = state
    store = packstore.for_profile(prof)

    def bump(version):
        data = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())
        data["manifest"]["version"] = version
        (prof.clients / prof.client / "pack.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        return _loaded_here(prof, _make_artifact(prof))

    h0 = _loaded_here(prof, _make_artifact(prof))
    rel.release(prof, h0, ungated=True)
    store.write("units/leasing/draft.yaml", b"format: 1\nunit: leasing\nitems: []\n")
    state.commit("leasing", {"unit": "leasing", "version": 1, "requests": []}, [{"step": "source", "version": 1}])
    h1 = bump("1.6.0")
    rel.release(prof, h1, ungated=True)
    state.commit("leasing", {"unit": "leasing", "version": 2, "requests": []}, [{"step": "update", "version": 2}])
    h2 = bump("1.7.0")
    rel.release(prof, h2, ungated=True)
    assert not store.exists("units/leasing/unit.yaml"), "the table holds the manifest"

    seen = []
    rel.release(prof, h1, build_units=lambda p: seen.append(state.manifest("leasing")["version"]))
    assert seen == [1], "the units are rebuilt from the restored manifest"
    assert state.manifest("leasing")["version"] == 1
    steps = [e["step"] for e in state.history("leasing")]
    assert steps[:2] == ["source", "update"] and "rolled-back" in steps, steps
    rolled = next(e for e in state.history("leasing") if e["step"] == "rolled-back")
    assert rolled["version"] == 1 and rolled["detail"] == {"to": h1}

    from kge.graph import history as history_mod
    kept = state.history("leasing")
    recorded = history_mod.record_release

    def disk_full(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(history_mod, "record_release", disk_full)
    with pytest.raises(OSError, match="disk full"):
        rel.release(prof, h0)
    monkeypatch.setattr(history_mod, "record_release", recorded)
    assert state.manifest("leasing")["version"] == 1, "a failed rollback puts the dropped unit back"
    assert state.history("leasing")[:len(kept)] == kept and state.history("leasing")[-1]["step"] == "rollback-undone", \
        "with the history it had, as the files backend puts units/leasing/history.jsonl back"

    rel.release(prof, h0)
    assert state.units() == [], "no ghost unit after a rollback to before it"
    assert state.history("leasing") == [], "its history goes with it, as units/leasing/ does on the files backend"

    def broken(_prof):
        raise ValueError("corrupted questions.json")
    with pytest.raises(rel.Unreleasable, match="failed to rebuild"):
        rel.release(prof, h2, build_units=broken)
    assert prof.model_hash() == h0 and state.units() == [], "a failed rollback leaves the tables as they were"
    assert state.history("leasing") == [], "the unit h0 does not have is dropped again, with the history h2 gave it"


def test_a_release_on_a_bigquery_state_backend_with_no_credentials_is_a_one_line_refusal(prof, monkeypatch, capsys):
    """A release snapshots the units' manifests from the state backend: one it cannot sign in to refuses the release in
    one line, current as it was, never a traceback."""
    import google.auth.exceptions
    from kge.adapters.appstate import bigquery as bigquery_state
    from kge.pipeline import profile as profile_mod
    h0 = _loaded_here(prof, _make_artifact(prof))

    def no_credentials(project=None):
        raise google.auth.exceptions.DefaultCredentialsError("Your default credentials were not found.")
    monkeypatch.setattr(bigquery_state, "bq_account", no_credentials)
    monkeypatch.setattr(bigquery_state, "_ENSURED", set())
    monkeypatch.setenv("APP_STATE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    monkeypatch.setattr(profile_mod, "resolve", lambda _p=None: prof)
    monkeypatch.setattr(rel, "resolve", lambda _p=None: prof)
    assert rel.main(["--hash", h0, "--ungated"]) == 2
    err = capsys.readouterr().err
    assert err == ("release: APP_STATE_BACKEND=bigquery cannot sign in to BigQuery: Your default credentials were not "
                   "found. current is as it was.\n"), err
    assert not (prof.artifacts / prof.client / "current").exists(), "nothing released"


def test_a_reload_of_the_released_hash_is_served_only_once_it_is_released_again(prof, monkeypatch):
    """Grok m2 on #97: on BigQuery a load of the hash current names makes a new generation and repoints that hash's
    folder (graph.bigquery, chunks.bigquery). A release records what it released, the graph and the chunks in one
    release record (release.json); a server (Profile.serve: the API, the job's eval) opens that, so a reload is not
    served, from the job's work or after a restart, until it is released; a release refused part way leaves the record
    as it was; every other step reads the reload. A release carries the record."""
    from kge.pipeline import objstore
    h, graph, prefix, table, load = _bigquery_hash(prof, monkeypatch)
    g1, g2 = list(prefix)[:2]

    def served() -> tuple[str, str]:
        named = prof.serve().at(h).identity()
        return named["graph"], named["chunks"]

    load(g1)
    rel.release(prof, h, ungated=True)
    assert served() == (prefix[g1], f"kge_meridian_graph.{table[g1]}")
    load(g2)
    assert served() == (prefix[g1], f"kge_meridian_graph.{table[g1]}"), "a reload is not served"
    assert prof.at(h).identity() == {"hash": h, "graph": prefix[g2], "chunks": f"kge_meridian_graph.{table[g2]}"}

    def refused(*args, **kwargs):
        raise RuntimeError("the release's record could not be written")
    with monkeypatch.context() as m:
        m.setattr(rel, "put", refused)
        with pytest.raises(RuntimeError):
            rel.release(prof, h, ungated=True)
    assert served() == (prefix[g1], f"kge_meridian_graph.{table[g1]}"), "a refused release leaves the record"
    rel.release(prof, h, ungated=True)
    assert served() == (prefix[g2], f"kge_meridian_graph.{table[g2]}")
    shipped = {p.name for p in objstore.release_paths(prof, h)}
    assert {"graph.bigquery", "chunks.bigquery", "release.json"} <= shipped


@pytest.mark.parametrize("published", ["identity", "no identity", "an unreadable identity"])
def test_a_service_follows_each_release_of_one_hash_with_its_graph_and_chunks_paired_and_prune_keeps_what_it_holds(
        prof, monkeypatch, tmp_path, published):
    """Copilot on #97 (kge/graph/release.py): a release's identity is its hash, its graph generation and its chunks table
    (Profile.identity). Release H as g1, g2 and g3, the service never restarted: after each, the service follows to it
    (follow_release compares identities, so the same hash at a new generation is followed); the graph it opens pins
    the chunks table of the same release, never another's; and the prune each release makes never drops what the
    service holds, keeping the release before as well (the ledger's two), while g1 goes once nothing holds it.
    Grok m1 on #97: where the store holds no identity for the hash, or one that cannot be read, the service fetches the
    release and compares the identity its release record names, so it still follows, and the g3 prune never drops g1
    under it."""
    from types import SimpleNamespace
    from kge.adapters import retrieval
    from kge.adapters.graphstore import bigquery as bq
    from kge.adapters.graphstore.gql import Schema
    from kge.adapters.retrieval import bigquery as bqr
    from kge.pipeline import objstore
    from kge.services.ask.engine import graphview
    from kge.services.onboard import jobs
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    for name in ("WAREHOUSE_BACKEND", "APP_STATE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"VERTEX_PROJECT": "p", "RUN_REGION": "r", "K_SERVICE": "kge-meridian",
                        "STORE_URL": f"file://{tmp_path}/store"}.items():
        monkeypatch.setenv(name, value)
    h = _make_artifact(prof)
    graph = prof.at(h).graph_path()
    gens = ("1a1a1a1a", "2b2b2b2b", "3c3c3c3c")
    prefix = {g: bq.prefix_of(h, g) for g in gens}
    table = {g: f"chunks_{h[:16]}_{g}" for g in gens}
    live_graphs, live_chunks = set(), set()

    def graph_query(self, sql, parameters):
        if "INFORMATION_SCHEMA" in sql:
            return {"rows": [{"f": [{"v": f"{p}Unit"}]} for p in sorted(live_graphs)]}
        live_graphs.difference_update({p for p in list(live_graphs) if f"{p}Unit" in sql})
        return {}

    def chunks_query(sql, project=None):
        if "INFORMATION_SCHEMA" in sql:
            return [{"table_name": t} for t in sorted(live_chunks)]
        live_chunks.difference_update({t for t in list(live_chunks) if f".{t}`" in sql})
        return []
    monkeypatch.setattr(bq.Client, "query", graph_query)
    monkeypatch.setattr(bqr, "bq_query", chunks_query)
    monkeypatch.setattr(objstore, "release_paths", lambda prof_, h_: [])
    monkeypatch.setattr(objstore, "tarball", lambda app, paths: b"release")
    fetched = []
    monkeypatch.setattr(jobs, "fetch", lambda store, app, prof_: fetched.append(h) or h)
    monkeypatch.setattr(jobs, "FETCHED", {})
    store = objstore.the_store()

    def load(g: str) -> None:
        rel.unmark(graph)
        made = bq.Graph(graph)
        made.ns.prefix = prefix[g]
        made.write(Schema.build([], []))
        retrieval.forget_chunks(graph.parent)
        retrieval.name_chunks(prof.at(h), "kge_meridian_graph", table[g])
        live_graphs.add(prefix[g])
        live_chunks.add(table[g])
        rel.write_marker(graph.parent, {rel.store_of(graph): {"client": prof.client, "model_hash": h, "release": "r1",
                                                               "chunks": 1}}, rel.LOADED)

    class Service:
        """The API on Cloud Run: its graph opened as it serves (Engine.serving), switched to by follow_release."""
        PROFILE = prof.serve()

        def __init__(self):
            self.opened = None

        def engine(self):
            return self.opened

        def switch(self):
            g = graphview.Graph(self.PROFILE)
            self.opened = SimpleNamespace(g=g, profile=g.profile, identity=g.identity)

    service = Service()
    held = []
    for n, g in enumerate(gens):
        load(g)
        rel.release(prof, h, ungated=True)
        objstore.publish(store, tmp_path, prof)
        if published == "no identity":
            store.delete(objstore.identity_key(prof))
        elif published == "an unreadable identity":
            store.put(objstore.identity_key(prof), b"{not json")
        if service.opened is None:
            service.switch()
        job = {"id": f"job-{n}", "step": "release", "state": "done"}
        jobs.follow_release(service, prof, job)
        before = len(fetched)
        jobs.follow_release(service, prof, job)
        assert len(fetched) == before, "a status poll after the release has been followed fetches nothing again"
        opened = service.engine().g
        assert service.engine().identity == {"hash": h, "graph": prefix[g], "chunks": f"kge_meridian_graph.{table[g]}"}, g
        assert opened.db.ns.prefix == prefix[g] and opened.chunks == f"`proj.kge_meridian_graph.{table[g]}`", g
        held.append((prefix[g], table[g]))
        assert prefix[g] in live_graphs and table[g] in live_chunks, f"the prune dropped what the service holds at {g}"
        if n:
            assert {prefix[gens[n - 1]], table[gens[n - 1]]} <= live_graphs | live_chunks, "the release before is kept"
    assert prefix[gens[0]] not in live_graphs and table[gens[0]] not in live_chunks, "g1 goes once nothing holds it"


def test_a_graph_whose_release_moves_as_it_opens_is_opened_again(prof, monkeypatch):
    """The graph and chunks a graph pins are of one release: its identity is read before and after it opens, and a
    release that moved it meanwhile is opened again, never paired across releases."""
    from kge.services.ask.engine import graphview
    from kge.pipeline.profile import Profile
    h = _make_artifact(prof)
    a, b = {"hash": h, "graph": "ga", "chunks": "ca"}, {"hash": h, "graph": "gb", "chunks": "cb"}
    reads = iter([a, b, b, b])
    opened = []
    monkeypatch.setattr(Profile, "identity", lambda self: next(reads))
    monkeypatch.setattr(graphview, "open_db", lambda path, pidfile=None: opened.append(path) or type("D", (), {"close": lambda s: None})())
    monkeypatch.setattr(graphview, "connect", lambda db: object())
    g = graphview.Graph(prof.at(h))
    assert len(opened) == 2 and g.identity == b


def _bigquery_hash(prof, monkeypatch):
    """A hash on stand-ins for BigQuery's graph store and retrieval, and load(g), which leaves its folder pointing at
    generation g and its chunks table, as a load and its sync do. The prunes see no table, so they drop nothing."""
    from kge.adapters import retrieval
    from kge.adapters.graphstore import bigquery as bq
    from kge.adapters.graphstore.gql import Schema
    from kge.adapters.retrieval import bigquery as bqr
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    for name in ("WAREHOUSE_BACKEND", "APP_STATE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bq.Client, "query", lambda self, sql, parameters: {"rows": []})
    monkeypatch.setattr(bqr, "bq_query", lambda sql, project=None: [])
    h = _make_artifact(prof)
    graph = prof.at(h).graph_path()
    prefix = {g: bq.prefix_of(h, g) for g in ("1a1a1a1a", "2b2b2b2b", "3c3c3c3c")}
    table = {g: f"chunks_{h[:16]}_{g}" for g in prefix}

    def load(g: str) -> None:
        rel.unmark(graph)
        made = bq.Graph(graph)
        made.ns.prefix = prefix[g]
        made.write(Schema.build([], []))
        retrieval.forget_chunks(graph.parent)
        retrieval.name_chunks(prof.at(h), "kge_meridian_graph", table[g])
        rel.write_marker(graph.parent, {rel.store_of(graph): {"client": prof.client, "model_hash": h, "release": "r1",
                                                               "chunks": 1}}, rel.LOADED)
    return h, graph, prefix, table, load


def test_a_server_opening_while_a_release_is_recorded_never_pairs_one_release_s_graph_with_another_s_chunks(
        prof, monkeypatch):
    """Copilot on #97 (kge/services/ask/engine/graphview.py, kge/graph/release.py): a release used to replace the
    released graph pointer and the released chunks pointer one after the other, so a server opening between the two read
    the new generation with the old chunks, and its identity, read before and after, agreed. Each replacement a release
    makes is interleaved here with a server opening its graph (graphview.Graph on a serving profile): every open pins the
    graph and the chunks of one release, and its identity names the two it opened."""
    import os
    from kge.services.ask.engine import graphview
    h, graph, prefix, table, load = _bigquery_hash(prof, monkeypatch)
    pair = {prefix[g]: f"`proj.kge_meridian_graph.{table[g]}`" for g in prefix}
    opens: list[tuple[str, str | None, dict]] = []
    real_replace = os.replace

    def replace_then_open(src, dst):
        real_replace(src, dst)
        if Path(dst).parent == graph.parent:
            g = graphview.Graph(prof.serve().at(h))
            opens.append((g.db.ns.prefix, g.chunks, g.identity))
    for g in prefix:
        load(g)
        with monkeypatch.context() as m:
            m.setattr(os, "replace", replace_then_open)
            rel.release(prof, h, ungated=True)
    assert len(opens) >= 3
    for generation, chunks, identity in opens:
        assert chunks == pair[generation], f"graph {generation} opened with chunks {chunks}"
        assert identity == {"hash": h, "graph": generation, "chunks": chunks.strip("`").split(".", 1)[1]}


def test_a_release_recorded_in_the_separate_records_before_the_release_record_is_served_and_then_retired(
        prof, monkeypatch):
    """Upgrade: a folder a release recorded before the release record holds graph.bigquery.released and
    chunks.bigquery.released. A server opens what they name, as before, and a prune keeps it; the next release writes
    the one record, release.json, and retires the two."""
    import json as json_
    from kge.adapters import released
    from kge.services.ask.engine import graphview
    h, graph, prefix, table, load = _bigquery_hash(prof, monkeypatch)
    g1, g2 = list(prefix)[:2]
    load(g1)
    folder = graph.parent
    (folder / "graph.bigquery.released").write_bytes(graph.read_bytes())
    (folder / "chunks.bigquery.released").write_bytes((folder / "chunks.bigquery").read_bytes())
    load(g2)
    opened = graphview.Graph(prof.serve().at(h))
    assert (opened.db.ns.prefix, opened.chunks) == (prefix[g1], f"`proj.kge_meridian_graph.{table[g1]}`")
    assert prof.serve().at(h).identity() == {"hash": h, "graph": prefix[g1], "chunks": f"kge_meridian_graph.{table[g1]}"}
    rel.release(prof, h, ungated=True)
    assert not (folder / "graph.bigquery.released").exists() and not (folder / "chunks.bigquery.released").exists()
    record = json_.loads(released.path(folder).read_text())
    assert record["hash"] == h and record["graph"]["prefix"] == prefix[g2]
    assert record["chunks"] == {"dataset": "kge_meridian_graph", "table": table[g2]}
    opened = graphview.Graph(prof.serve().at(h))
    assert (opened.db.ns.prefix, opened.chunks) == (prefix[g2], f"`proj.kge_meridian_graph.{table[g2]}`")


def test_a_failed_switch_to_a_same_hash_release_is_tried_again_on_the_next_poll(monkeypatch):
    """Copilot on #97: with no readable identity, the service records the release job as followed (FETCHED) only once
    its switch is done, so a switch that fails leaves the next status poll to fetch and switch again."""
    from types import SimpleNamespace
    from kge.services.onboard import jobs

    h = "ab" * 32
    store = SimpleNamespace(get=lambda key: h.encode())
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: store)
    monkeypatch.setattr(jobs, "released_identity", lambda store_, prof_, released: None)
    monkeypatch.setattr(jobs, "fetch", lambda store_, root, prof_: None)
    monkeypatch.setattr(jobs, "FETCHED", {})
    served = {"hash": h, "graph": "g1", "chunks": "c1"}
    released = {"hash": h, "graph": "g2", "chunks": "c2"}
    prof = SimpleNamespace(client="meridian", model_hash=lambda: h)
    prof.serve = lambda: SimpleNamespace(at=lambda hash_: SimpleNamespace(identity=lambda: released))
    tries = []

    class App:
        current = SimpleNamespace(identity=served, profile=prof)

        def engine(self):
            return self.current

        def switch(self):
            tries.append(1)
            if len(tries) == 1:
                raise OSError("the replacement engine could not open")
            self.current = SimpleNamespace(identity=released, profile=prof)

    app, job = App(), {"id": "job-1", "step": "release", "state": "done"}
    with pytest.raises(jobs.JobRefused):
        jobs.follow_release(app, prof, job)
    assert jobs.FETCHED == {} and app.engine().identity == served
    jobs.follow_release(app, prof, job)
    assert app.engine().identity == released and jobs.FETCHED == {"meridian": "job-1"}
    jobs.follow_release(app, prof, job)
    assert len(tries) == 2
