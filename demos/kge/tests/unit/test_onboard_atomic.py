"""A failed onboard leaves durable inputs alone, restores units, and rebuilds on the next run."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kge import cli, packstore
from kge.adapters import graphstore, retrieval
from kge.adapters.objstore import open_store
from kge.adapters.warehouse import estate_of
from kge.estate import from_files, upfront
from kge.graph import leftovers, release
from kge.pipeline import objstore
from kge.pipeline.profile import Profile
from tests import bigquery_fake
from tests.unit.test_from_files import fixture

HASH = "a" * 64
DEMO = Path(__file__).resolve().parents[2]


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(params=["duckdb", "bigquery"])
def atomic(tmp_path, monkeypatch, request):
    monkeypatch.setenv("WAREHOUSE_BACKEND", request.param)
    fake = bigquery_fake.install(monkeypatch) if request.param == "bigquery" else None
    app = tmp_path / "app"
    fx = fixture(app / "clients/shop")
    (fx.parent / "pack.yaml").write_text("manifest: {client: shop}\nterminology: {}\n")
    (fx.parent / "pack.lock").write_bytes(b"original lock")
    ids = app / "deploy/shop/identities.yaml"
    ids.parent.mkdir(parents=True)
    ids.write_text("client: shop\nprincipals: [{principal: sam, role: steward}]\nasker: sam\n")
    prof = Profile("shop", "shop", app / "clients", app / "build/artifacts", app / "build/stores",
                   app / "build/landing", app / "build/estate/warehouses", ids, None, app / "deploy/profiles.yaml")
    monkeypatch.setattr(cli.profile, "resolve", lambda *a, **k: prof)
    monkeypatch.setattr(objstore, "resolve", lambda *a, **k: prof)
    remote = open_store(str(tmp_path / "objects"))
    monkeypatch.setenv("STORE_URL", remote.url)
    objstore.push(prof.clients, remote, "clients/")
    remote.put("work/shop.tar.gz", b"previous work")
    remote.put("landing/shop/held", b"previous landing")
    pstore = packstore.for_profile(prof)
    pstore.write("units/extra/unit.yaml", yaml.safe_dump({"format": 2, "unit": "extra", "version": 1,
        "inputs": {}, "rows": {"origin": "invented"}, "requests": [{"id": "open", "reason": "held"}]}).encode())
    pstore.write("units/extra/schema.sql", b"CREATE TABLE extra (id INTEGER PRIMARY KEY);\nCOMMENT ON COLUMN extra.id IS 'Identifier';")
    pstore.write("units/extra/draft.yaml", yaml.safe_dump({"format": 1, "unit": "extra", "warehouse": {"version": 1, "inputs": "f" * 64}, "items": []}).encode())
    pstore.write("units/extra/history.jsonl", b'{"step":"before"}\n')
    pstore.write("units/extra/retained", b"retain these bytes")
    monkeypatch.setattr(upfront, "check", lambda prof: upfront.Report(False, [], [], None, {}, "inputs", from_files.fixture_sha256(fx.parent)))
    generations, chunks = {"live"}, {"live-chunks"}
    monkeypatch.setattr(graphstore, "generations", lambda: sorted(generations))
    monkeypatch.setattr(retrieval, "chunks_tables", lambda prof: sorted(chunks))

    def drop(held, names):
        found = set(names) & held
        held.difference_update(found)
        return sorted(found)

    monkeypatch.setattr(graphstore, "drop_generations", lambda names: drop(generations, names))
    monkeypatch.setattr(retrieval, "drop_chunks", lambda prof, names: drop(chunks, names))
    monkeypatch.setattr(leftovers, "named", lambda prof: leftovers.Held(frozenset({"live"}), frozenset({"live-chunks"}), frozenset()))
    monkeypatch.setattr(leftovers, "released", lambda prof: leftovers.Held(frozenset({"live"}), frozenset({"live-chunks"}), frozenset()))
    monkeypatch.setattr(cli, "profile_words", lambda model_hash=None: {})
    commands = cli.Commands({"PACK": str(fx.parent), "LANDED": str(prof.landing / prof.client / "current"),
                             "STORE": str(prof.store / prof.client), "MARGIN_STATUS": str(prof.run_dir() / "margin.status")})
    scenario = {"failure": None, "builds": 0, "unit_versions": [], "steps": []}
    actual_build = from_files.build

    def build(*args, **kwargs):
        result = actual_build(*args, **kwargs)
        scenario["builds"] += int(result["rebuilt"])
        if scenario["failure"] == "build":
            raise cli.Failed(7, "build")
        return result

    monkeypatch.setattr(from_files, "build", build)

    def module(name, *args, **kwargs):
        step = {"kge.estate.onboard": "units", "kge.estate.certify": "certify", "kge.compiler.lock": "lock",
                "kge.compiler.compile": "compile", "kge.graph.load": "load", "kge.eval.margin": "margin"}.get(name)
        if name == "kge.estate.onboard" and args[0] == "record-onboard":
            return 0
        scenario["steps"].append(step)
        if step == "units":
            scenario["unit_versions"].append(estate_of(prof).versions("extra"))
            store = packstore.for_profile(prof)
            store.write("units/extra/new", b"created during build")
            store.delete("units/extra/retained")
            store.write("units/extra/unit.yaml", b"unit: extra\nversion: 2\nrequests: []\nrows: {origin: invented}\n")
            store.write("units/extra/history.jsonl", b'{"step":"during"}\n')
        if step == "certify":
            target = prof.landing / prof.client / "current/manifest.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}")
        if step == "lock":
            (fx.parent / "pack.lock").write_bytes(b"new lock")
            Path(args[args.index("--changes") + 1]).write_text('{"written": true, "changes": []}')
        if step == "load":
            generations.add("new-generation")
            chunks.add("new-chunks")
        if step == "margin":
            graph = graphstore.graph_path(prof.store / prof.client / HASH)
            graph.parent.mkdir(parents=True, exist_ok=True)
            code = scenario.get("margin_exit", 1 if scenario["failure"] == "margin" else 0)
            run_id = scenario.get("run_id", "this-run")
            if code != 2:
                release.mark_gate(graph, {"passed": code == 0, "model_hash": HASH, "run_id": run_id})
            report = prof.paths()["runs"] / run_id / "report.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("the gate's report")
            return code
        if step is not None and scenario["failure"] == step:
            raise cli.Failed(7, step)
        return str(prof.artifacts / prof.client / HASH) if step == "compile" else '{"units": ["extra"]}' if step == "units" else 0

    monkeypatch.setattr(commands, "module", module)
    return commands, prof, remote, scenario, generations, chunks, fake, app


@pytest.mark.parametrize("step", ["build", "units", "certify", "lock", "compile", "load", "margin"])
def test_failure_at_each_step_restores_every_durable_input_and_rerun_rebuilds(atomic, step):
    commands, prof, remote, scenario, generations, chunks, fake, app = atomic
    before = {key: remote.get(key) for key in remote.list()}
    unit_files = files(prof.clients / prof.client / "units")
    scenario["failure"] = step
    with pytest.raises(cli.Failed) as error:
        commands.onboard()
    assert error.value.returncode == (1 if step == "margin" else 7)
    objstore.failed("onboard", app)
    after = {key: remote.get(key) for key in remote.list() if not key.startswith("runs/")}
    assert after == before
    assert files(prof.clients / prof.client / "units") == unit_files
    assert (prof.clients / prof.client / "pack.lock").read_bytes() == b"original lock"
    assert "fixture_sha256" not in json.loads((prof.estate() / "summary.json").read_text())
    assert generations == {"live"} and chunks == {"live-chunks"}
    assert estate_of(prof).held() == {}
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["step"] == ("fixture" if step == "build" else step)
    assert record["outcome"] == ("gate failed" if step == "margin" else "failed")
    assert len([key for key in remote.list("runs/") if key.endswith(".json")]) == 1
    if step == "margin":
        assert remote.get(objstore.onboard_run_key(prof, record) + "/report.md") == b"the gate's report"
    scenario["failure"] = None
    commands.onboard()
    assert scenario["builds"] == 2
    assert scenario["unit_versions"] and all(versions == [1] for versions in scenario["unit_versions"])
    assert json.loads((prof.estate() / "summary.json").read_text())["fixture_sha256"]
    assert not (prof.run_dir() / leftovers.JOURNAL).exists()
    assert json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())["outcome"] == "passed"
    objstore.after("onboard", app)
    assert remote.get("clients/shop/pack.lock") == b"new lock"
    assert remote.get("work/shop.tar.gz") != b"previous work"


@pytest.mark.parametrize("command", ["onboard", "release", "sign-off", "passwords", "seed", "estate"])
def test_cli_retry_refuses_every_writer_before_any_step(monkeypatch, tmp_path, command):
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "1")
    monkeypatch.setenv("STORE_URL", str(tmp_path / "untouched"))
    monkeypatch.setattr(cli.Commands, "run", lambda *args: pytest.fail("retry ran a writer"))
    assert cli.main([command]) == 2
    assert not (tmp_path / "untouched").exists()


@pytest.mark.parametrize("command", ["onboard", "release", "sign-off", "passwords", "seed", "teardown", "onboard-request"])
def test_entrypoint_retry_refuses_before_store_before(tmp_path, command):
    app = tmp_path / "entry"
    app.mkdir()
    shutil.copy(DEMO / "entrypoint.sh", app / "entrypoint.sh")
    env = {**os.environ, "CLOUD_RUN_TASK_ATTEMPT": "1", "STORE_URL": str(tmp_path / "untouched"),
           "PY": sys.executable, "PYTHONPATH": str(DEMO), "MODEL_PROVIDER": "none"}
    result = subprocess.run(["sh", str(app / "entrypoint.sh"), command], text=True, capture_output=True, env=env)
    assert result.returncode == 2
    assert f"an automatic retry cannot fix a failed {command}" in result.stderr
    assert not (tmp_path / "untouched").exists()


@pytest.mark.parametrize("gate_before", [None, {"passed": False, "model_hash": HASH, "run_id": "old"}])
@pytest.mark.parametrize("code", [0, 1, 2])
def test_loaded_candidate_rechecks_without_reload_and_keeps_old_gate_on_failure(atomic, monkeypatch, gate_before, code):
    commands, prof, remote, scenario, generations, chunks, _, app = atomic
    commands.onboard()
    held = leftovers.held(prof)
    monkeypatch.setattr(leftovers, "named", lambda prof: held)
    graph = graphstore.graph_path(prof.store / prof.client / HASH)
    gate_path = graph.parent / release.GATE
    if gate_before is None:
        gate_path.unlink()
    else:
        release.mark_gate(graph, gate_before)
    original = gate_path.read_bytes() if gate_path.exists() else None
    monkeypatch.setattr(cli, "profile_words", lambda model_hash=None: {"loaded": "yes"})
    scenario.update(margin_exit=code, run_id="recheck", steps=[])
    if code:
        with pytest.raises(cli.Failed) as error:
            commands.onboard()
        assert error.value.returncode == code
        assert (gate_path.read_bytes() if gate_path.exists() else None) == original
        objstore.failed("onboard", app)
    else:
        commands.onboard()
        assert release.gate(graph)["passed"] is True
        assert release.changes(graph)["rechecked"] is True
    assert scenario["builds"] == 1
    assert "load" not in scenario["steps"] and "margin" in scenario["steps"]
    assert generations == {"live", "new-generation"} and chunks == {"live-chunks", "new-chunks"}
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["outcome"] == {0: "passed", 1: "gate failed", 2: "not checked"}[code]
    assert record["gate"] == {"passed": code == 0, "run_id": "recheck"}
    if code:
        assert remote.get(objstore.onboard_run_key(prof, record) + "/report.md") == b"the gate's report"


def test_not_checked_before_a_report_never_reuses_an_older_gate_report(atomic, monkeypatch):
    commands, prof, _, scenario, _, _, _, _ = atomic
    graph = graphstore.graph_path(prof.store / prof.client / HASH)
    graph.parent.mkdir(parents=True)
    release.mark_gate(graph, {"passed": True, "model_hash": HASH, "run_id": "old"})
    report = prof.paths()["runs"] / "old/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("old report")
    module = commands.module
    monkeypatch.setattr(commands, "module", lambda name, *args, **kwargs:
                        2 if name == "kge.eval.margin" else module(name, *args, **kwargs))
    with pytest.raises(cli.Failed) as error:
        commands.onboard()
    assert error.value.returncode == 2
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["outcome"] == "not checked"
    assert record["gate"] == {"passed": False, "run_id": None}


def test_sweep_precedes_upfront_check_while_the_job_lease_is_held(atomic, monkeypatch):
    from dataclasses import asdict
    from kge.compiler.pack import Finding
    from kge.pipeline import lease
    commands, prof, remote, scenario, generations, chunks, _, app = atomic
    held = lease.take(remote, prof.client, "onboard")
    prof.run_dir().mkdir(parents=True, exist_ok=True)
    (prof.run_dir() / lease.LEASE_FILE).write_text(json.dumps(asdict(held)))
    generations.add("orphan")
    chunks.add("orphan-chunks")

    def check(prof):
        assert lease.holder(remote, prof.client)["command"] == "onboard"
        assert generations == {"live"} and chunks == {"live-chunks"}
        journal = json.loads((prof.run_dir() / leftovers.JOURNAL).read_text())
        assert leftovers.Held.from_json(journal) == leftovers.held(prof)
        return upfront.Report(False, [Finding("I01", "pack.yaml", "missing")], [], None, {}, "inputs", None)

    monkeypatch.setattr(upfront, "check", check)
    with pytest.raises(cli.Failed) as error:
        commands.onboard()
    assert error.value.returncode == 2 and scenario["builds"] == 0
    objstore.failed("onboard", app)
    assert lease.holder(remote, prof.client) is None


def test_same_hash_with_changed_people_does_not_reload_or_record_people(atomic, monkeypatch):
    commands, prof, _, scenario, _, _, _, _ = atomic
    commands.onboard()
    held = leftovers.held(prof)
    monkeypatch.setattr(leftovers, "named", lambda prof: held)
    monkeypatch.setattr(cli, "profile_words", lambda model_hash=None: {"loaded": "yes"})
    prof.identities.write_bytes(prof.identities.read_bytes().replace(b"sam", b"pat"))
    scenario["steps"] = []
    commands.onboard()
    graph = graphstore.graph_path(prof.store / prof.client / HASH)
    changes = release.changes(graph)
    assert "people" not in changes
    assert changes["reloaded"] is False and changes["rechecked"] is False
    assert "load" not in scenario["steps"] and "margin" not in scenario["steps"]
    assert scenario["builds"] == 1
    inputs = json.loads((graph.parent / release.INPUTS).read_text())
    assert inputs["model_hash"] == HASH and "roster" not in inputs


def test_cleanup_failure_still_restores_lock_records_failure_and_keeps_margin_code(atomic, monkeypatch, capsys):
    from kge.estate.onboard import stage
    commands, prof, _, scenario, generations, chunks, _, _ = atomic
    scenario["failure"] = "margin"

    def unavailable(prof, captured):
        raise RuntimeError("unit state unavailable")

    monkeypatch.setattr(stage, "restore", unavailable)
    with pytest.raises(cli.Failed) as error:
        commands.onboard()
    assert error.value.returncode == 1
    assert (prof.clients / prof.client / "pack.lock").read_bytes() == b"original lock"
    assert "fixture_sha256" not in json.loads((prof.estate() / "summary.json").read_text())
    assert generations == {"live"} and chunks == {"live-chunks"}
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["outcome"] == "gate failed" and record["step"] == "margin"
    assert "restoring units failed: unit state unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("origin", ["none", "invented", "csv"])
def test_repair_missing_unit_version_respects_row_origin(atomic, origin):
    commands, prof, _, _, _, _, _, _ = atomic
    store = packstore.for_profile(prof)
    meta = yaml.safe_load(store.read("units/extra/unit.yaml"))
    meta["rows"] = {"origin": origin}
    if origin == "csv":
        meta["rows"]["files"] = ["extra"]
        store.write("units/extra/data/extra.csv", b"id\n731\n")
    store.write("units/extra/unit.yaml", yaml.safe_dump(meta).encode())
    before = files(prof.clients / prof.client / "units")
    commands.repair_unit_warehouses(prof)
    estate = estate_of(prof)
    assert estate.versions("extra") == ([] if origin == "none" else [1])
    if origin != "none":
        session = estate.open("extra", 1)
        try:
            rows = session.sample("extra", "id")
            assert rows
            if origin == "csv":
                assert rows == ["731"]
        finally:
            session.close()
    assert files(prof.clients / prof.client / "units") == before


@pytest.mark.parametrize("step", ["apply", "sweep"])
def test_early_failure_restores_upload_and_preserves_durable_state(atomic, monkeypatch, step):
    from kge.pipeline import uploads
    from tests.unit.test_onboard_from import upload
    commands, prof, remote, scenario, generations, chunks, _, app = atomic
    upload(commands, prof, remote)
    pack = prof.clients / prof.client
    before, people = files(pack), prof.identities.read_bytes()
    durable = {key: remote.get(key) for key in remote.list()}
    held = leftovers.held(prof)
    if step == "apply":
        write = uploads._write

        def fail_write(path, data):
            write(path, data)
            if path == pack / "fixture/data/shop/sale.csv" and data != before["fixture/data/shop/sale.csv"]:
                assert files(pack) != before
                raise OSError("injected apply failure")

        monkeypatch.setattr(uploads, "_write", fail_write)
    else:
        def fail_chunks(prof, names):
            assert files(pack) != before
            raise OSError("injected sweep failure")

        monkeypatch.setattr(retrieval, "drop_chunks", fail_chunks)
    with pytest.raises(OSError, match=f"injected {step} failure"):
        commands.onboard()
    assert files(pack) == before and prof.identities.read_bytes() == people
    assert leftovers.held(prof) == held
    assert generations == {"live"} and chunks == {"live-chunks"}
    assert scenario["builds"] == 0
    assert not (prof.run_dir() / "onboard-inputs-before").exists()
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["step"] == ("inputs" if step == "apply" else "sweep")
    assert record["outcome"] == "failed"
    objstore.failed("onboard", app)
    assert {key: remote.get(key) for key in remote.list() if not key.startswith("runs/")} == durable


def test_next_run_reports_discarding_backup_kept_after_failed_restore(atomic, monkeypatch, capsys):
    from kge.pipeline import uploads
    from tests.unit.test_onboard_from import upload
    commands, prof, remote, scenario, _, _, _, _ = atomic
    upload(commands, prof, remote)
    scenario["failure"] = "margin"
    restore = uploads.restore

    def fail_restore(*args):
        raise OSError("restore unavailable")

    monkeypatch.setattr(uploads, "restore", fail_restore)
    with pytest.raises(cli.Failed):
        commands.onboard()
    backup = prof.run_dir() / "onboard-inputs-before"
    assert backup.exists()
    capsys.readouterr()
    monkeypatch.setattr(uploads, "restore", restore)
    scenario["failure"] = None
    commands.onboard()
    assert not backup.exists()
    assert "discarded onboard-inputs-before/ from a previous run: starting from the current working inputs" in capsys.readouterr().out
