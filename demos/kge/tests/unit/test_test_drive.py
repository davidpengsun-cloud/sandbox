from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from kge.adapters.objstore import gcs as gcs_backend
from kge.adapters.objstore.gcs import GcsStore
from kge.pipeline import objstore
from kge.pipeline.settings import ROOT
from kge.ports import StoreError
from kge.services.onboard import test_drive_run
from kge.services.onboard.test_drive import PEOPLE, Refused, Result, filtered, guard
from tests.gcs_stub import serve

SECRETS = ("PORTAL_SYNC_TOKEN", "MODEL_API_KEY", "SESSION_SECRET", "SIGNIN_PASSWORDS", "TAGGER_API_KEY", "MODEL_HIGH_API_KEY",
           "MODEL_MEDIUM_API_KEY", "MODEL_LOW_API_KEY")
SENTINEL = "sentinel-7f3a9c"
USERINFO = ":".join(("user", SENTINEL))
SETTINGS = ("NAME", "CONFIRM", "PROFILE", "CLIENT", "STORE_URL", "LANDING_URL", "TMP_URL", "FROM", "STORAGE_EMULATOR_HOST",
            "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE", "MODEL_PROVIDER",
            "TAGGER_PROVIDER", "EMBEDDING_PROVIDER", "ANSWER_MODEL_TIER", *SECRETS)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROFILE", "meridian")
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)


class Store:
    url = "/store"

    def __init__(self, objects=None, failed=False, unwritable=False):
        self.objects = dict(objects or {})
        self.failed = failed
        self.unwritable = unwritable
        self.writes = []

    def get(self, key):
        if self.failed:
            raise StoreError("sentinel store failure")
        return self.objects.get(key)

    def list(self, prefix=""):
        if self.failed:
            raise StoreError("sentinel store failure")
        return sorted(key for key in self.objects if key.startswith(prefix))

    def put(self, key, data):
        if self.unwritable:
            raise StoreError(f"cannot put {key}: HTTP 403")
        self.writes.append(key)
        self.objects[key] = data


def passed(name):
    return Result(name, "passed", exercised=["read"])


@pytest.mark.parametrize("objects", [
    {"releases/meridian/current": b"x"},
    {"clients/meridian/pack.yaml": b"x"},
    {"work/meridian.tar.gz": b"x"},
])
def test_guard_refuses_every_existing_client_location_before_a_write(monkeypatch, objects):
    monkeypatch.setenv("CONFIRM", "test-drive")
    monkeypatch.setenv("PROFILE", "meridian")
    held = Store(objects)
    with pytest.raises(Refused):
        guard(held)
    assert held.writes == []


def test_guard_passes_a_store_with_another_client_only(monkeypatch):
    monkeypatch.setenv("CONFIRM", "test-drive")
    monkeypatch.setenv("PROFILE", "meridian")
    held = Store({"deploy/profiles.yaml": b"profiles:\n  fellmoor: {}\n", "clients/fellmoor/pack.yaml": b"x",
                  "examples/meridian/meridian/clients/meridian/pack.yaml": b"x"})
    guard(held)
    assert held.writes == []


def test_guard_refuses_confirmation_profile_client_and_store_errors_before_a_write(monkeypatch):
    monkeypatch.setenv("PROFILE", "meridian")
    held = Store()
    with pytest.raises(Refused, match="CONFIRM"):
        guard(held)
    monkeypatch.setenv("CONFIRM", "yes")
    with pytest.raises(Refused, match="CONFIRM"):
        guard(held)
    monkeypatch.setenv("CONFIRM", "test-drive")
    monkeypatch.setenv("PROFILE", "../invalid")
    with pytest.raises(Refused, match="PROFILE"):
        guard(held)
    monkeypatch.setenv("PROFILE", "meridian")
    monkeypatch.setenv("CLIENT", "meridian")
    with pytest.raises(Refused, match="no CLIENT"):
        guard(held)
    monkeypatch.delenv("CLIENT")
    failing = Store(failed=True)
    with pytest.raises(StoreError):
        guard(failing)
    assert held.writes == [] and failing.writes == []


@pytest.fixture
def entry(tmp_path):
    """The entry script beside a kge that records any call; the store a folder."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "make.log"
    fake = bin_ / "kge"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
    fake.chmod(0o755)
    store = tmp_path / "store"
    store.mkdir()
    base = {k: v for k, v in os.environ.items() if k not in SETTINGS}

    def run(**env):
        r = subprocess.run(["sh", str(ROOT / "entrypoint.sh"), "test-drive"], capture_output=True, text=True, cwd=tmp_path,
                           env={**base, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "PY": sys.executable,
                                "STORE_URL": str(store), **env})
        return r, (log.read_text() if log.exists() else ""), sorted(p.relative_to(store).as_posix() for p in store.rglob("*"))
    return run, store


def test_the_entry_script_refuses_without_confirm_or_on_an_invalid_profile_and_writes_nothing(entry):
    run, _ = entry
    for env, said in (({"PROFILE": "meridian"}, "CONFIRM"), ({"PROFILE": "../invalid", "CONFIRM": "test-drive"}, "PROFILE"),
                      ({"PROFILE": "meridian", "CONFIRM": "test-drive", "CLIENT": "meridian"}, "CLIENT")):
        r, made, written = run(**env)
        assert r.returncode == 2 and said in r.stderr, r.stderr
        assert made == "" and written == []


def test_the_entry_script_refuses_on_an_existing_client_and_writes_nothing(entry):
    run, store = entry
    (store / "work").mkdir()
    (store / "work" / "meridian.tar.gz").write_bytes(b"x")
    r, made, written = run(PROFILE="meridian", CONFIRM="test-drive")
    assert r.returncode == 2 and "work/meridian.tar.gz" in r.stderr
    assert made == "" and written == ["work", "work/meridian.tar.gz"]


def test_preflight_failure_stops_before_steps_and_still_saves_report(monkeypatch):
    monkeypatch.setattr(test_drive_run, "preflight", lambda kept: [Result("settings", "failed", detail="bad")])
    monkeypatch.setattr(test_drive_run, "run_command", lambda *args: pytest.fail("a step ran"))
    held = Store()
    assert test_drive_run.execute(held) == 1
    report = json.loads(next(v for k, v in held.objects.items() if k.endswith(".json")))
    assert report["steps"] == [] and report["result"] == "failed"


def test_steps_run_in_order_and_stop_at_the_first_failure_naming_it(monkeypatch):
    called = []
    monkeypatch.setattr(test_drive_run, "preflight", lambda kept: [passed("settings")])
    monkeypatch.setattr(test_drive_run, "golden", lambda: pytest.fail("the golden check ran"))

    def run(args, extra=None):
        called.append(args[0])
        if args[0] == "onboard":
            raise RuntimeError("exit 1: GATE: failed")
        return "ok"

    monkeypatch.setattr(test_drive_run, "run_command", run)
    held = Store()
    assert test_drive_run.execute(held) == 1
    assert called == ["setup-client", "put-baseline", "seed", "lock", "onboard"]
    report = json.loads(next(v for k, v in held.objects.items() if k.endswith(".json")))
    assert [s["name"] for s in report["steps"]] == ["setup-client", "put-baseline", "seed", "lock", "onboard"]
    assert report["steps"][-1]["result"] == "failed" and "GATE: failed" in report["steps"][-1]["detail"]
    assert report["steps"][-1]["what_to_do"]


def full_pass(monkeypatch, golden=None):
    calls = []
    monkeypatch.setattr(test_drive_run, "preflight", lambda kept: [passed("settings"), passed("store bucket")])
    monkeypatch.setattr(test_drive_run, "run_command", lambda args, extra=None: calls.append((args, extra)) or "ok")
    monkeypatch.setattr(test_drive_run, "golden", lambda: golden or {
        "passed": 2, "of": 2, "failures": [], "held_out": {"passed": 1, "of": 2, "failures": [{"id": "H1", "failure": "paraphrase"}]}})
    monkeypatch.setattr(test_drive_run, "catalogue_check", lambda: "retail 10, sme 9, corporate 8, wealth 20")
    monkeypatch.setattr(test_drive_run, "data_check", lambda: "preview and query returned rows; another unit was refused")
    return calls


def test_a_pass_runs_the_seven_steps_with_the_guide_s_people_and_saves_the_report(monkeypatch, capsys):
    calls = full_pass(monkeypatch)
    monkeypatch.setenv("FROM", "gs://store/examples")
    held = Store({"releases/meridian/current": b"abc123\n"})
    assert test_drive_run.execute(held) == 0
    assert [args for args, _ in calls] == [
        ["setup-client", "CLIENT=meridian", "ASKER=analyst_enterprise", f"PEOPLE={PEOPLE}"],
        ["put-baseline"], ["seed"], ["lock"], ["onboard"], ["sign-off", "PRINCIPAL=steward_glossary"], ["release"]]
    assert calls[1][1] == {"FROM": "gs://store/examples"}
    keys = [k for k in held.writes if k.startswith("runs/meridian/test-drive/")]
    assert [k.rsplit(".", 1)[1] for k in keys] == ["json", "md"] and keys[0][:-5] == keys[1][:-3]
    report = json.loads(held.objects[keys[0]])
    assert report["result"] == "passed" and report["released_hash"] == "abc123"
    assert [item["name"] for item in report["steps"][-3:]] == ["golden check", "catalogue", "data"]
    assert report["golden"]["held_out"]["of"] == 2
    md = held.objects[keys[1]].decode()
    assert "the job's configuration" in md and "abc123" in md and "2 of 2 golden questions passed" in md
    assert "H1: paraphrase" in md and "Starting baseline" in md and "_portal_" in md
    assert md.strip() in capsys.readouterr().out


def test_a_failed_golden_item_fails_the_run_and_the_report_words_it_as_the_scorer(monkeypatch):
    full_pass(monkeypatch, {"passed": 1, "of": 2, "failures": [{"id": "G002", "failure": "figure 1.0 != 2.0"}],
                            "held_out": {"passed": 0, "of": 0, "failures": []}})
    held = Store({"releases/meridian/current": b"abc123"})
    assert test_drive_run.execute(held) == 1
    report = json.loads(next(v for k, v in held.objects.items() if k.endswith(".json")))
    assert report["golden"]["failures"] == [{"id": "G002", "failure": "figure 1.0 != 2.0"}]
    assert report["steps"][-1]["result"] == "failed" and "G002: figure 1.0 != 2.0" in report["steps"][-1]["detail"]


@pytest.mark.parametrize("name", ["catalogue_check", "data_check"])
def test_a_failed_release_check_stops_the_test_drive_and_reports_an_action(monkeypatch, name):
    full_pass(monkeypatch)
    monkeypatch.setattr(test_drive_run, name, lambda: (_ for _ in ()).throw(RuntimeError("incomplete release")))
    held = Store({"releases/meridian/current": b"abc123"})
    assert test_drive_run.execute(held) == 1
    report = json.loads(next(v for k, v in held.objects.items() if k.endswith(".json")))
    failed = report["steps"][-1]
    assert failed["name"] == name.removesuffix("_check") and failed["result"] == "failed"
    assert "incomplete release" in failed["detail"] and failed["what_to_do"]


def test_a_store_that_cannot_be_written_gives_a_log_only_report_and_a_non_zero_exit(monkeypatch, capsys):
    full_pass(monkeypatch)
    held = Store({"releases/meridian/current": b"abc123"}, unwritable=True)
    assert test_drive_run.execute(held) == 1
    out = capsys.readouterr().out
    assert "Test drive: a diagnostic report on the job's configuration" in out and "in this log alone" in out
    assert "Result: **passed**" in out


def test_no_secret_reaches_the_json_the_markdown_or_the_log(monkeypatch, capsys):
    for name in SECRETS:
        monkeypatch.setenv(name, f"{SENTINEL}-{name}")
    monkeypatch.setenv("MODEL_BASE_URL", f"https://{USERINFO}@models.invalid/v1/{SENTINEL}")

    class Provider:
        name = "stand-in:model"

        def complete(self, prompt, max_tokens=800, *, fixed_budget=False):
            raise RuntimeError(f"HTTP 401 from https://models.invalid/v1?key={SENTINEL}-MODEL_API_KEY: "
                               f"{SENTINEL}-TAGGER_API_KEY rejected")

    monkeypatch.setattr(test_drive_run, "configured_models", lambda: {"stand-in:model": Provider()})
    for name in ("check_settings", "check_embedding", "check_versions"):
        monkeypatch.setattr(test_drive_run, name, lambda: "ok")
    monkeypatch.setattr(test_drive_run, "check_bucket", lambda url: "read, list")
    held = Store()
    assert test_drive_run.execute(held) == 1
    log = capsys.readouterr().out
    saved = {k: v.decode() for k, v in held.objects.items()}
    assert any(k.endswith(".json") for k in saved) and any(k.endswith(".md") for k in saved)
    for text in (*saved.values(), log):
        assert SENTINEL not in text
    report = json.loads(next(v for k, v in saved.items() if k.endswith(".json")))
    assert report["settings"]["MODEL_API_KEY"] == "set" and report["settings"]["MODEL_BASE_URL"] == "models.invalid"
    assert report["settings"]["SESSION_SECRET"].startswith("not applicable")
    assert [i["result"] for i in report["preflight"] if i["name"] == "models"] == ["failed"]


def test_a_step_s_output_is_filtered_in_the_log(monkeypatch, capsys):
    monkeypatch.setenv("TAGGER_API_KEY", SENTINEL)
    monkeypatch.setattr(test_drive_run.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, f"calling https://x.invalid/p?k={SENTINEL}\n", f"refused: {SENTINEL}\n"))
    with pytest.raises(RuntimeError) as failed:
        test_drive_run.run_command(["seed"])
    assert SENTINEL not in capsys.readouterr().out and SENTINEL not in filtered(failed.value)


def test_secret_filter_removes_settings_and_provider_urls(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "secret-sentinel")
    text = filtered(f"secret-sentinel from https://{USERINFO}@private.invalid/path and gs://private-bucket/object")
    assert "secret-sentinel" not in text
    assert "user" not in text
    assert "path" not in text
    assert "private-bucket" not in text


@pytest.fixture
def cloud(monkeypatch):
    """The stand-in of Cloud Storage's JSON API, called as Cloud Storage is (no emulator's leniency)."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, ",".join(x for x in (os.environ.get(name, ""), "127.0.0.1", "localhost") if x))
    monkeypatch.setattr(gcs_backend, "BACKOFF", 0.01)
    real = GcsStore.__init__

    def as_cloud(self, *args, **kwargs):
        real(self, *args, **kwargs)
        self.emulator = False

    monkeypatch.setattr(GcsStore, "__init__", as_cloud)
    monkeypatch.setattr(GcsStore, "headers", lambda self: {})
    with serve() as srv:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"127.0.0.1:{srv.server_address[1]}")
        yield srv


def by_name(results):
    return {r.name: r for r in results}


def test_buckets_pass_and_a_missing_bucket_fails_on_its_404(cloud, monkeypatch):
    cloud.buckets["store"] = {"deploy/profiles.yaml": b"profiles: {}\n"}
    monkeypatch.setenv("STORE_URL", "gs://store")
    monkeypatch.setenv("LANDING_URL", "gs://meridian-landing")
    results = by_name(test_drive_run.preflight(GcsStore("gs://store", "store")))
    assert results["store bucket"].result == "passed" and results["store bucket"].exercised == ["read", "list"]
    assert results["landing bucket"].result == "failed"
    assert "bucket meridian-landing does not exist" in results["landing bucket"].detail
    assert "LANDING_URL" in results["landing bucket"].what_to_do
    cloud.buckets["meridian-landing"] = {}
    assert by_name(test_drive_run.preflight(GcsStore("gs://store", "store")))["landing bucket"].result == "passed"


def test_the_emulator_passes_a_missing_bucket_and_says_it_proves_nothing_of_the_pilot_s(monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, ",".join(x for x in (os.environ.get(name, ""), "127.0.0.1", "localhost") if x))
    with serve() as srv:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"127.0.0.1:{srv.server_address[1]}")
        item = test_drive_run.timed("store bucket", lambda: test_drive_run.check_bucket("gs://absent"), ("read", "list"))
    assert item.result == "passed" and "says nothing of the pilot's buckets" in item.detail


def test_the_tmp_bucket_reads_metadata_and_removes_its_probe(cloud, monkeypatch):
    cloud.buckets["tmpb"] = {}
    monkeypatch.setenv("TMP_URL", "gs://tmpb")
    item = test_drive_run.timed("tmp bucket", test_drive_run.check_tmp, ())
    assert item.result == "passed", item.detail
    assert item.detail.startswith("bucket metadata, write, read, delete")
    assert cloud.buckets["tmpb"] == {}
    puts = [path for method, path, _ in cloud.seen if method == "POST"]
    assert puts and all("tmp%2Fmeridian%2F" in path for path in puts)
    assert not [path for method, path, _ in cloud.seen if method == "GET" and path.startswith("/storage/v1/b/tmpb/o?")]


def test_the_tmp_bucket_fails_when_missing_and_is_not_applicable_unset(cloud, monkeypatch):
    item = test_drive_run.timed("tmp bucket", test_drive_run.check_tmp, ())
    assert item.result == "not applicable" and item.exercised == []
    monkeypatch.setenv("TMP_URL", "gs://absent-tmp")
    item = test_drive_run.timed("tmp bucket", test_drive_run.check_tmp, ())
    assert item.result == "failed" and "bucket absent-tmp does not exist" in item.detail and "TMP_URL" in item.what_to_do


@pytest.mark.parametrize("name, value, said", [
    ("ANSWER_MODEL_TIER", "highest", "ANSWER_MODEL_TIER"),
    ("APP_STATE_BACKEND", "sqlite", "APP_STATE_BACKEND"),
    ("GRAPH_STORE", "neo4j", "GRAPH_STORE"),
    ("EMBEDDING_PROVIDER", "openai", "EMBEDDING_PROVIDER"),
])
def test_a_contradiction_is_found_by_the_factory_that_consumes_it(monkeypatch, name, value, said):
    assert test_drive_run.timed("settings", test_drive_run.check_settings).result == "passed"
    monkeypatch.setenv(name, value)
    item = test_drive_run.timed("settings", test_drive_run.check_settings)
    assert item.result == "failed" and said in item.detail, item.detail


def test_models_are_called_once_each_and_not_applicable_when_none(monkeypatch):
    assert test_drive_run.timed("models", test_drive_run.check_models).result == "not applicable"

    class Model:
        def __init__(self, name, fails=False):
            self.name, self.fails, self.calls = name, fails, 0

        def complete(self, prompt, max_tokens=800, *, fixed_budget=False):
            assert max_tokens == 256 and fixed_budget
            self.calls += 1
            if self.fails:
                raise RuntimeError("HTTP 404: model not found")
            return "OK"

    high, low = Model("vertex-gemini:high"), Model("vertex-gemini:low")
    agents = {"onboard": high, "enhance": high, "answer": low, "context": low}
    monkeypatch.setattr(test_drive_run, "model_for", lambda agent: agents[agent])
    monkeypatch.setattr(test_drive_run, "tagger_model", lambda: low)
    item = test_drive_run.timed("models", test_drive_run.check_models)
    assert item.result == "passed" and (high.calls, low.calls) == (1, 1)
    agents["answer"] = Model("vertex-gemini:missing", fails=True)
    item = test_drive_run.timed("models", test_drive_run.check_models)
    assert item.result == "failed" and "model not found" in item.detail


def test_an_embedding_of_the_wrong_width_is_caught(monkeypatch):
    assert test_drive_run.timed("embedding", test_drive_run.check_embedding).result == "passed"

    class Narrow:
        name, dim = "stand-in", 768

        def embed(self, text):
            return [0.0] * 128

    monkeypatch.setattr(test_drive_run, "make_embedder", lambda: Narrow())
    item = test_drive_run.timed("embedding", test_drive_run.check_embedding)
    assert item.result == "failed" and "128 wide, not the 768" in item.detail


@pytest.mark.parametrize("installed", ["meridian", "aster-bank"])
def test_bigquery_lists_the_client_s_datasets_on_the_pilot_and_is_not_applicable_elsewhere(monkeypatch, installed):
    from kge.adapters.bigquery import BigQueryLoader
    monkeypatch.setenv("PROFILE", installed)
    dataset = "kge_" + installed.replace("-", "_")
    assert test_drive_run.timed("datasets", test_drive_run.check_bigquery).result == "not applicable"
    listed = []
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setattr(BigQueryLoader, "account", lambda self: (None, "pilot-project"))
    monkeypatch.setattr(BigQueryLoader, "tables", lambda self, dataset: listed.append(dataset) or [])
    item = test_drive_run.timed("datasets", test_drive_run.check_bigquery)
    assert item.result == "passed" and "pilot-project" in item.detail
    assert listed == [f"{dataset}_warehouses", f"{dataset}_graph"]

    def missing(self, dataset):
        raise RuntimeError(f"HTTP 404: Not found: Dataset pilot-project:{dataset}")

    monkeypatch.setattr(BigQueryLoader, "tables", missing)
    item = test_drive_run.timed("datasets", test_drive_run.check_bigquery)
    assert item.result == "failed" and f"{dataset}_warehouses" in item.detail


def test_versions_read_the_installed_example_from_a_folder_or_a_store(monkeypatch, tmp_path):
    pack = tmp_path / "examples" / "meridian" / "clients" / "meridian"
    pack.mkdir(parents=True)
    (pack / "pack.yaml").write_text("manifest:\n  version: 9.8.7\n  meta_model_version: 1.2.3\n")
    monkeypatch.setenv("KGE_VERSION", "0.9.1")
    for source in (str(tmp_path / "examples"), f"file://{tmp_path / 'examples'}"):
        monkeypatch.setenv("FROM", source)
        item = test_drive_run.timed("versions", test_drive_run.check_versions)
        assert item.result == "passed" and "image and kit 0.9.1" in item.detail and "9.8.7, for meta-model 1.2.3" in item.detail
    monkeypatch.setenv("FROM", str(tmp_path / "nowhere"))
    item = test_drive_run.timed("versions", test_drive_run.check_versions)
    assert item.result == "failed" and item.what_to_do


def test_the_example_source_is_from_else_the_kit_s_mount_else_the_store_s_examples(monkeypatch, tmp_path):
    monkeypatch.setattr(test_drive_run, "KIT_EXAMPLES", tmp_path / "kit")
    monkeypatch.setenv("STORE_URL", "gs://store")
    assert test_drive_run.example_source() == "gs://store/examples"
    (tmp_path / "kit" / "meridian").mkdir(parents=True)
    assert test_drive_run.example_source() == str(tmp_path / "kit")
    monkeypatch.setenv("FROM", "gs://elsewhere/examples")
    assert test_drive_run.example_source() == "gs://elsewhere/examples"


def test_put_example_from_a_folder_and_from_a_store_location_gives_the_same_objects(monkeypatch, tmp_path):
    files = {"clients/sample/pack.yaml": b"pack", "clients/sample/pack.lock": b"lock",
             "clients/sample/baselines/issue-1.json": b"{}"}
    for root in ("folder", "remote"):
        for key, data in files.items():
            path = tmp_path / root / "sample" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    held = []
    for source in (str(tmp_path / "folder"), f"file://{tmp_path / 'remote'}"):
        target = tmp_path / f"target-{len(held)}"
        monkeypatch.setenv("STORE_URL", str(target))
        monkeypatch.setenv("EXAMPLE", "sample")
        monkeypatch.setenv("FROM", source)
        objstore.put_example()
        held.append({p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()})
    assert held[0] == held[1] == files


def test_put_example_refuses_a_store_location_without_the_example(monkeypatch, tmp_path):
    (tmp_path / "remote").mkdir()
    monkeypatch.setenv("STORE_URL", str(tmp_path / "target"))
    monkeypatch.setenv("EXAMPLE", "sample")
    monkeypatch.setenv("FROM", f"file://{tmp_path / 'remote'}")
    with pytest.raises(objstore.Refused, match="holds no file"):
        objstore.put_example()
    assert not (tmp_path / "target").exists()


def test_people_are_the_install_guides_people_in_order():
    guide = (ROOT / "kit/INSTALL.md").read_text()
    assert f'"PEOPLE={PEOPLE}"' in guide
    assert f"ASKER=analyst_enterprise,PEOPLE={PEOPLE}" in guide


def test_a_step_s_detail_is_its_gate_line_else_the_store_s():
    gate = "GATE: /app/build/stores/meridian/abc123/GATE: passed, run r1"
    assert test_drive_run.summary_line(f"compile: ok\n{gate}\nstore: work put\ncompile: again") == gate
    assert test_drive_run.summary_line("compile: ok\nstore: releases/meridian/current put\ncompile: again") == \
        "store: releases/meridian/current put"
    assert test_drive_run.summary_line("one\ntwo") == "two"


def test_real_data_check_names_columns_and_runs_through_the_gate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kge.adapters.warehouse.duckdb import DuckDBEstate, Warehouses
    from kge.landing.zone import Zone
    from kge.pipeline.landed_tables import write_landed_tables
    from kge.semantics.query import Semantics
    from kge.services.data import service
    estate = DuckDBEstate(tmp_path)
    for unit in ("retail", "wealth"):
        with estate.stage(unit, ["CREATE TABLE account(account_id INTEGER)", "INSERT INTO account VALUES (1), (2)"]):
            pass
        estate.publish(unit)
    warehouses = Warehouses(tmp_path, ["retail", "wealth"])

    def graph_query(sql, params=None):
        if "RETURN t.name" in sql:
            return [{"t.name": "account"}]
        if "RETURN DISTINCT c.tbl AS tbl" in sql:
            return [{"tbl": "account"}]
        if "RETURN t.id AS tbl" in sql:
            return [{"tbl": f"{unit}.account", "col": "account_id", "dtype": "INTEGER"} for unit in ("retail", "wealth")]
        return []

    landed = Zone(tmp_path / "landing", "fixture").land_files({
        f"structured/{unit}.json": json.dumps({"source": unit, "tables": [{"name": "account"}]}).encode()
        for unit in ("retail", "wealth")}, {})
    write_landed_tables(tmp_path, landed)
    graph = SimpleNamespace(domain=lambda: {"units": ["retail", "wealth"]}, readable_units=lambda who: ["retail"],
                            profile=SimpleNamespace(client="fixture", graph_path=lambda: tmp_path / "graph"), release=landed.about,
                            table_columns=lambda *args: ["account_id"], unit_tables=lambda unit: {"account"},
                            unit_joins=lambda unit: [], q=graph_query)
    engine = SimpleNamespace(g=graph, w=warehouses, UNIT_ORDER=["retail", "wealth"], RESTRICTED_COLUMNS=[],
                             PERIODS={}, close=warehouses.close)
    engine.semantics = Semantics(graph, engine)
    monkeypatch.setattr(test_drive_run.Engine, "serving", lambda *_: engine)
    monkeypatch.setattr(test_drive_run, "resolve", lambda *_: SimpleNamespace(asking=lambda who: who))
    monkeypatch.setattr(test_drive_run, "catalogue_get", lambda *_: SimpleNamespace(products=[SimpleNamespace(id="retail.unmapped", unit_id="retail", table="unmapped"),
                             *[SimpleNamespace(id=f"{u}.account", unit_id=u, table="account") for u in ("retail", "wealth")]]))
    monkeypatch.setattr(test_drive_run, "example_source", lambda: None)
    monkeypatch.setattr(test_drive_run, "example_pack", lambda *_: {"sources": [{"id": "retail"}], "mappings": [{"column": "retail.account.account_id", "certified": True}]})
    monkeypatch.setattr(service, "traced", lambda path, call, **kw: {**call(), "run_trace": {}})
    assert test_drive_run.data_check() == "preview and query returned rows for retail.account; wealth.account was refused"


def test_setup_only_client_is_allowed_but_its_data_is_refused(monkeypatch):
    monkeypatch.setenv("CONFIRM", "test-drive")
    monkeypatch.setenv("PROFILE", "aster-bank")
    configured = sample_setup()
    guard(Store(configured))
    for key in ("clients/aster-bank/pack.yaml", "releases/aster-bank/current", "work/aster-bank.tar.gz",
                "landing/aster-bank/current"):
        with pytest.raises(Refused, match="already holds"):
            guard(Store({**configured, key: b"existing"}))


def test_branding_keeps_structured_values_and_escapes_the_display_name():
    import yaml
    from kge.pipeline.branding import branded_file, display_name
    name = 'Aster: "Bank" & Trust'
    for path, raw in (("pack.yaml", b'client: meridian\ntitle: Meridian Union Bank governance graph\ncount: 12\n'),
                      ("golden.json", b'{"question":"Meridian revenue", "expect":12}')):
        data = branded_file(path, raw, "aster-bank", name)
        parsed = yaml.safe_load(data)
        assert "meridian" not in data.decode().lower()
        assert parsed.get("count", parsed.get("expect")) == 12
        assert name in str(parsed)
    assert display_name("aster-bank") == "Aster Bank"
    assert display_name("meridian") == "Meridian Union Bank"
    raw = b'title: Meridian Union Bank\n'
    assert branded_file("pack.yaml", raw, "meridian", display_name("meridian")) == raw


def test_baseline_copy_brands_pack_catalogue_documents_and_questions(monkeypatch, tmp_path):
    import yaml
    from kge.adapters.objstore import open_store
    source = open_store(str(tmp_path / "examples"))
    prefix = "meridian/clients/meridian/"
    files = {"pack.yaml": b"manifest:\n  client: meridian\n  title: Meridian Union Bank governance graph\n",
             "catalogue.yaml": b"path: Meridian > Retail\n", "pack.lock": b'{"client":"meridian"}',
             "fixture/golden.json": b'[{"question":"Meridian revenue", "figure":42}]',
             "fixture/docs/meridian.md": b"Meridian Union Bank: MERIDIAN and meridian"}
    for rel, data in files.items():
        source.put(prefix + rel, data)
    monkeypatch.setenv("PROFILE", "aster-bank")
    monkeypatch.setenv("EXAMPLE", "another-client")
    monkeypatch.setenv("NAME", 'Aster: "Bank"')
    monkeypatch.setenv("FROM", source.url)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    objstore.put_baseline()
    target = open_store(str(tmp_path / "store"))
    assert len(target.list("clients/aster-bank/")) == len(files)
    for key in target.list():
        assert "meridian" not in key.lower()
        assert b"meridian" not in target.get(key).lower()
    assert yaml.safe_load(target.get("clients/aster-bank/pack.yaml"))["manifest"]["client"] == "aster-bank"
    assert source.get(prefix + "pack.yaml") == files["pack.yaml"]
    with pytest.raises(objstore.Refused, match="already holds"):
        objstore.put_baseline()


def test_non_sample_client_uses_its_own_datasets_and_report(monkeypatch):
    full_pass(monkeypatch)
    monkeypatch.setenv("PROFILE", "aster-bank")
    held = Store({"releases/aster-bank/current": b"abc123"})
    assert test_drive_run.execute(held) == 0
    report = json.loads(next(value for key, value in held.objects.items() if key.endswith(".json")))
    assert {key.rsplit("/", 1)[0] for key in held.writes} == {"runs/aster-bank/test-drive"}
    assert report["client"] == "aster-bank"
    assert "kge_aster_bank_*" in " ".join(report["remove_before_rerun"])
    assert "meridian" not in json.dumps(report).lower()


@pytest.mark.parametrize("setting", ["CLIENT=another", "PROFILE=another", "EXAMPLE=another"])
@pytest.mark.parametrize("command", ["test-drive", "put-baseline"])
def test_test_drive_rejects_client_selection_parameters(tmp_path, setting, command):
    result = subprocess.run(["sh", str(ROOT / "entrypoint.sh"), command, "CONFIRM=test-drive", setting],
                            text=True, capture_output=True, env={**os.environ, "PROFILE": "aster-bank", "PY": sys.executable})
    assert result.returncode == 2
    assert "from the install" in result.stderr


def test_separate_landing_store_refuses_before_any_write(monkeypatch, tmp_path):
    from kge.adapters.objstore import open_store
    landing = open_store(str(tmp_path / "landing"))
    landing.put("retail/current", b"existing")
    monkeypatch.setenv("LANDING_URL", landing.url)
    monkeypatch.setenv("CONFIRM", "test-drive")
    held = Store()
    with pytest.raises(Refused, match="landing store already holds data"):
        guard(held)
    assert held.writes == []
    assert landing.get("retail/current") == b"existing"


def sample_setup():
    import yaml
    from kge.services.onboard.test_drive import CONFIG
    identities = {"client": "aster-bank", "asker": CONFIG["asker"],
                  "principals": [dict(zip(("principal", "role"), person.split(":"))) for person in CONFIG["people"]]}
    return {"deploy/profiles.yaml": b"profiles:\n  aster-bank:\n    client: aster-bank\n",
            "deploy/aster-bank/identities.yaml": yaml.safe_dump(identities).encode()}


@pytest.mark.parametrize("identities", [b"client: aster-bank\nasker: alice\nprincipals:\n- {principal: alice, role: analyst_enterprise}\n- {principal: bob, role: admin}\n", b"[invalid", b"client: aster-bank\n"])
def test_existing_people_are_refused_and_never_replaced(monkeypatch, identities):
    monkeypatch.setenv("PROFILE", "aster-bank")
    monkeypatch.setenv("CONFIRM", "test-drive")
    objects = {**sample_setup(), "deploy/aster-bank/identities.yaml": identities}
    held = Store(objects)
    with pytest.raises(Refused, match="never replaces"):
        guard(held)
    assert held.objects == objects and held.writes == []


@pytest.mark.parametrize("reordered", [False, True])
def test_existing_sample_setup_is_kept_byte_for_byte(monkeypatch, reordered):
    import yaml
    monkeypatch.setenv("PROFILE", "aster-bank")
    monkeypatch.setenv("CONFIRM", "test-drive")
    objects = sample_setup()
    if reordered:
        identities = yaml.safe_load(objects["deploy/aster-bank/identities.yaml"])
        identities["principals"].reverse()
        objects["deploy/aster-bank/identities.yaml"] = yaml.safe_dump(identities).encode()
    held = Store(objects)
    guard(held)
    calls = full_pass(monkeypatch)
    assert test_drive_run.execute(held) == 0
    assert "setup-client" not in [args[0] for args, _ in calls]
    assert all(held.objects[key] == value for key, value in objects.items())
    assert not any(key.startswith("deploy/") for key in held.writes)


@pytest.mark.parametrize("change", ["missing", "extra", "role", "asker"])
def test_changed_sample_people_are_refused_before_any_write(monkeypatch, change):
    import yaml
    monkeypatch.setenv("PROFILE", "aster-bank")
    monkeypatch.setenv("CONFIRM", "test-drive")
    objects = sample_setup()
    identities = yaml.safe_load(objects["deploy/aster-bank/identities.yaml"])
    if change == "missing":
        identities["principals"].pop()
    elif change == "extra":
        identities["principals"].append({"principal": "alice", "role": "admin"})
    elif change == "role":
        identities["principals"][0]["role"] = "admin"
    else:
        identities["asker"] = "analyst_retail"
    objects["deploy/aster-bank/identities.yaml"] = yaml.safe_dump(identities).encode()
    held = Store(objects)
    with pytest.raises(Refused, match="never replaces"):
        guard(held)
    assert held.objects == objects and held.writes == []


def test_branded_pack_keeps_the_sample_layout():
    from kge.pipeline.branding import brand, branded_file
    raw = (ROOT / "clients/meridian/pack.yaml").read_bytes()
    branded = branded_file("pack.yaml", raw, "aster-bank", "Aster Bank")
    assert branded.decode() == brand(raw.decode(), "aster-bank", "Aster Bank")
    assert branded.count(b"- {") == raw.count(b"- {") > 0


def test_non_sample_estate_brands_every_institution_reference(tmp_path):
    import yaml
    from kge.pipeline.branding import branded_file
    from kge.pipeline.profile import resolve
    profile = resolve("meridian")
    pack = tmp_path / "clients/aster-bank/pack.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(branded_file("pack.yaml", (profile.clients / "meridian/pack.yaml").read_bytes(), "aster-bank", "Aster Bank"))
    identities = tmp_path / "identities.yaml"
    identities.write_bytes(sample_setup()["deploy/aster-bank/identities.yaml"])
    registry = tmp_path / "profiles.yaml"
    pointers = {key: str(getattr(profile, key)) for key in ("artifacts", "store", "landing", "warehouses")}
    registry.write_text(yaml.safe_dump({"default": "aster-bank", "profiles": {"aster-bank": {**pointers, "client": "aster-bank", "clients": str(tmp_path / "clients"), "identities": str(identities)}}}))
    code = '''
from kge.estate import spec, generate, documents, golden_wealth
assert spec.CLIENT == "aster-bank"
assert generate.HERE == "ASTER-BANK"
for unit, table in (("retail", "transaction"),):
    descriptions = [description for column, dtype, description in spec.SCHEMAS[unit][table]["columns"] if column == "counterparty_bank"]
    assert len(descriptions) == 1
    assert "ASTER-BANK" in descriptions[0] and "MERIDIAN" not in descriptions[0]
assert "ASTER-BANK" in documents.CLOSING_NOTE["retail"]
assert "MERIDIAN" not in documents.CLOSING_NOTE["retail"]
for once in (True, False):
    sql = golden_wealth.xb4(once=once)
    assert "MERIDIAN" not in sql and "OFSET_SRCE_SYS_ID" in sql
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PROFILE": "aster-bank", "PROFILES": str(registry), "MODEL_PROVIDER": "none"}, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("registry", [None, b"profiles: {}\n"])
def test_sample_people_without_registry_entry_are_never_replaced(monkeypatch, registry):
    monkeypatch.setenv("PROFILE", "aster-bank")
    monkeypatch.setenv("CONFIRM", "test-drive")
    objects = sample_setup()
    if registry is None:
        del objects["deploy/profiles.yaml"]
    else:
        objects["deploy/profiles.yaml"] = registry
    held = Store(objects)
    with pytest.raises(Refused, match="never replaces"):
        guard(held)
    assert held.objects == objects and held.writes == []


def test_branding_refuses_folded_yaml_that_changes_structure():
    from kge.pipeline.branding import branded_file
    raw = b"title: >-\n  Meridian Union\n  Bank\n"
    with pytest.raises(ValueError, match="does not preserve the sample's structure"):
        branded_file("pack.yaml", raw, "aster-bank", "Aster Bank")
