"""Teardown over folder stores and the BigQuery stand-in, without sockets or live credentials."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from kge.adapters import graphstore, retrieval
from kge.adapters.objstore import open_store
from kge.pipeline import lease, teardown
from kge.pipeline.settings import ROOT
from kge.ports import StoreError
from kge.services.onboard.test_drive import guard
from tests import bigquery_fake

CLIENT = "aster-bank"


@pytest.fixture
def install(tmp_path, monkeypatch):
    for name in ("PROFILE", "PROFILES", "CONFIRM", "PREVIEW", "PEOPLE", "CLIENT", "NAME", "STORE_URL", "LANDING_URL",
                 "TMP_URL", "CLOUD_RUN_TASK_ATTEMPT", "GRAPH_DATASET", "STORAGE_EMULATOR_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"PROFILE": CLIENT, "CONFIRM": CLIENT, "MODEL_PROVIDER": "none", "GRAPH_STORE": "ladybug",
                        "WAREHOUSE_BACKEND": "duckdb", "RETRIEVAL_BACKEND": "graph"}.items():
        monkeypatch.setenv(name, value)
    store = open_store((tmp_path / "store").as_uri())
    landing = open_store((tmp_path / "landing").as_uri())
    monkeypatch.setenv("STORE_URL", store.url)
    monkeypatch.setenv("LANDING_URL", landing.url)
    entry = {"client": CLIENT, "clients": "../clients", "artifacts": "../build/artifacts", "store": "../build/stores",
             "landing": "../build/landing", "warehouses": f"../build/{CLIENT}/warehouses",
             "identities": f"{CLIENT}/identities.yaml"}
    saved = {"default": CLIENT, "profiles": {CLIENT: entry}}
    store.put(teardown.REGISTRY, yaml.safe_dump(saved, sort_keys=False).encode())
    return SimpleNamespace(store=store, landing=landing, saved=saved, tmp=tmp_path)


def snapshot(store):
    return {key: store.get(key) for key in store.list()}


def populate(install):
    removed = {f"clients/{CLIENT}/pack.yaml": b"pack-data", f"clients/{CLIENT}/fixture/data.csv": b"fixture-data",
               f"work/{CLIENT}.tar.gz": b"work-data", f"releases/{CLIENT}/hash/release.tar.gz": b"release-data",
               f"secrets/{CLIENT}/passwords.json": b"hashed-password-data", f"secrets/{CLIENT}/passwords.txt": b"plaintext-data",
               f"readings/{CLIENT}/hash/reading.json": b"converted-document-data",
               f"tmp/{CLIENT}/load": b"tmp-data", f"uploads/{CLIENT}/upload/pack.yaml": b"upload-data"}
    kept = {f"deploy/{CLIENT}/identities.yaml": b"people-data", "clients/another/pack.yaml": b"other-data",
            f"clients/{CLIENT}-other/pack.yaml": b"neighbour-data", f"work/{CLIENT}.tar.gz.extra": b"unrelated-work",
            f"access/{CLIENT}/access-grants.json": b"live grants",
            f"access/{CLIENT}/access-audit.jsonl": b"live audit",
            f"runs/{CLIENT}/old.json": b"old-run-data", f"backups/{CLIENT}/old/pack.yaml": b"old-backup-data"}
    for key, value in {**removed, **kept}.items():
        install.store.put(key, value)
    install.landing.put("retail/v1/document.md", b"landing-document-data")
    install.landing.put("retail/current", b"v1")
    return removed, kept


def records(store):
    return [json.loads(store.get(key)) for key in store.list(f"runs/{CLIENT}/teardown/") if key.endswith(".json")]


@pytest.mark.parametrize("confirm", [None, "wrong", "another"])
def test_confirmation_refuses_before_any_write(install, monkeypatch, capsys, confirm):
    populate(install)
    before = snapshot(install.store), snapshot(install.landing)
    if confirm is None:
        monkeypatch.delenv("CONFIRM")
    else:
        monkeypatch.setenv("CONFIRM", confirm)
    assert teardown.main() == 2
    assert f"teardown CONFIRM={CLIENT}: CONFIRM must name this install's client, {CLIENT}" in capsys.readouterr().err
    assert (snapshot(install.store), snapshot(install.landing)) == before


@pytest.mark.parametrize("remove_people", [False, True])
def test_preview_names_and_counts_every_key_without_writing(install, monkeypatch, capsys, remove_people):
    removed, _ = populate(install)
    monkeypatch.setenv("PREVIEW", "yes")
    if remove_people:
        monkeypatch.setenv("PEOPLE", "remove")
    before = snapshot(install.store), snapshot(install.landing)
    assert teardown.main() == 0
    output = capsys.readouterr().out
    assert all(f"teardown: store {key}\n" in output for key in removed)
    assert f"clients/{CLIENT}/: 2 object(s)" in output
    assert "landing: 2 object(s)" in output
    assert "retail/v1/document.md" in output and "retail/current" in output
    assert (f"teardown: store deploy/{CLIENT}/identities.yaml" in output) is remove_people
    assert "preview only: nothing removed" in output
    assert (snapshot(install.store), snapshot(install.landing)) == before


def test_backup_removal_records_and_people_kept(install, capsys):
    removed, kept = populate(install)
    registry_before = install.store.get(teardown.REGISTRY)
    assert teardown.main() == 0
    output = capsys.readouterr().out
    record, = records(install.store)
    backup = record["backup"]
    assert f"teardown: store part backed up to {backup}; the landing's objects and BigQuery tables are not copied" in output
    assert {key.removeprefix(backup): install.store.get(key) for key in install.store.list(backup)} == {
        key: value for key, value in removed.items() if not key.endswith("passwords.txt")}
    assert all(install.store.get(key) is None for key in removed)
    assert all(install.store.get(key) == value for key, value in kept.items())
    assert install.store.get(teardown.REGISTRY) == registry_before
    assert install.landing.list() == []
    assert record["outcome"] == "removed" and record["profile_removed"] is False
    assert record["counts"]["landing"] == 2
    assert record["counts"]["store"][f"clients/{CLIENT}/"] == 2
    assert {key for group in record["removed"]["store"].values() for key in group} == set(removed)
    text = "\n".join(install.store.get(key).decode() for key in install.store.list(f"runs/{CLIENT}/teardown/"))
    assert "30 days" in text and "7-day lifecycle" in text and f"tmp/{CLIENT}/" in text
    assert all(value.decode() not in text + output for value in [*removed.values(), *kept.values()])
    assert lease.holder(install.store, CLIENT) is None


@pytest.mark.parametrize("other", [False, True])
def test_people_removed_registry_updated_and_test_drive_guard_accepts(install, monkeypatch, other):
    populate(install)
    if other:
        install.saved["profiles"]["other"] = {**install.saved["profiles"][CLIENT], "client": "other"}
        install.store.put(teardown.REGISTRY, yaml.safe_dump(install.saved, sort_keys=False).encode())
    registry_before = install.store.get(teardown.REGISTRY)
    monkeypatch.setenv("PEOPLE", "remove")
    assert teardown.main() == 0
    assert install.store.list(f"deploy/{CLIENT}/") == []
    record, = records(install.store)
    assert record["profile_removed"]
    assert install.store.get(record["backup"] + teardown.REGISTRY) == registry_before
    assert install.store.get(record["backup"] + f"deploy/{CLIENT}/identities.yaml") == b"people-data"
    if other:
        saved = yaml.safe_load(install.store.get(teardown.REGISTRY))
        assert saved == {"default": "other", "profiles": {"other": install.saved["profiles"]["other"]}}
    else:
        assert install.store.get(teardown.REGISTRY) is None
    monkeypatch.setenv("CONFIRM", "test-drive")
    guard(install.store)


@pytest.mark.parametrize("remove_people", [False, True])
def test_repeat_writes_record_but_no_new_backup(install, monkeypatch, capsys, remove_people):
    populate(install)
    if remove_people:
        monkeypatch.setenv("PEOPLE", "remove")
    assert teardown.main() == 0
    backups = snapshot(open_store(install.store.url + "/backups"))
    assert teardown.main() == 0
    assert f"teardown: nothing left for {CLIENT}" in capsys.readouterr().out
    assert snapshot(open_store(install.store.url + "/backups")) == backups
    saved = records(install.store)
    assert len(saved) == 2
    assert saved[-1]["outcome"] == "nothing left" and saved[-1]["backup"] is None


def test_fallback_landing_is_scoped_to_installed_client(install, monkeypatch):
    monkeypatch.delenv("LANDING_URL")
    install.store.put(f"landing/{CLIENT}/retail/current", b"v1")
    install.store.put("landing/another/retail/current", b"v2")
    assert teardown.main() == 0
    assert install.store.list(f"landing/{CLIENT}/") == []
    assert install.store.get("landing/another/retail/current") == b"v2"


def test_retry_and_live_job_refuse_without_deleting(install, monkeypatch, capsys):
    populate(install)
    before = snapshot(install.store)
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "1")
    assert teardown.main() == 2
    assert "an automatic retry cannot fix a failed teardown" in capsys.readouterr().err
    assert snapshot(install.store) == before
    monkeypatch.delenv("CLOUD_RUN_TASK_ATTEMPT")
    held = lease.take(install.store, CLIENT, "onboard")
    before = snapshot(install.store)
    assert teardown.main() == 2
    assert "another job is running" in capsys.readouterr().err
    assert snapshot(install.store) == before
    lease.release(install.store, held)


def test_backup_failure_removes_nothing_and_releases_lease(install, monkeypatch):
    removed, kept = populate(install)
    landing_before = snapshot(install.landing)
    put = type(install.store).put

    def fail_backup(self, key, data):
        if key.startswith(f"backups/{CLIENT}/"):
            raise StoreError("backup failed")
        return put(self, key, data)

    monkeypatch.setattr(type(install.store), "put", fail_backup)
    assert teardown.main() == 2
    assert all(install.store.get(key) == data for key, data in {**removed, **kept}.items())
    assert snapshot(install.landing) == landing_before
    assert lease.holder(install.store, CLIENT) is None
    assert records(install.store)[0]["outcome"] == "failed"


def test_registry_write_is_conditional_and_deploy_is_removed_last(install, monkeypatch):
    populate(install)
    monkeypatch.setenv("PEOPLE", "remove")
    original_version = install.store.get_version(teardown.REGISTRY)[1]
    put_version = type(install.store).put_version
    observed = []

    def conditional(self, key, data, version):
        if key == teardown.REGISTRY:
            observed.append(version)
            assert self.list(f"clients/{CLIENT}/") == []
            assert install.landing.list() == []
            assert self.get(f"deploy/{CLIENT}/identities.yaml") == b"people-data"
        return put_version(self, key, data, version)

    monkeypatch.setattr(type(install.store), "put_version", conditional)
    assert teardown.main() == 0
    assert observed == [original_version]


def test_module_entrypoint_uses_settings(install):
    result = subprocess.run([sys.executable, "-m", "kge.pipeline.teardown"], cwd=ROOT,
                            env={**os.environ, "PREVIEW": "yes"}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "preview only: nothing removed" in result.stdout


@pytest.fixture
def cloud(install, monkeypatch):
    from kge.adapters.graphstore import bigquery

    for name in ("GRAPH_STORE", "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND"):
        monkeypatch.setenv(name, "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "pilot")
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setattr(bigquery.Client, "query", lambda self, sql, parameters, read=False: fake.run(self, sql))
    datasets = [f"pilot_aster_bank_{kind}" for kind in ("warehouses", "graph", "candidates")]
    for dataset in datasets:
        for name, kind in (("node", "TABLE"), ("latest", "VIEW"), ("_portal_state", "TABLE"), ("access_grants", "TABLE"), ("access_audit", "TABLE")):
            ref = f"`{bigquery_fake.PROJECT}.{dataset}.{name}`"
            fake.run(None, f"CREATE {kind} {ref} " + ("AS SELECT 1 AS id" if kind == "VIEW" else "(id INT64)"))
        if dataset != datasets[0]:
            fake.run(None, f"CREATE PROPERTY GRAPH `{bigquery_fake.PROJECT}.{dataset}.graph` "
                          f"NODE TABLES (`{bigquery_fake.PROJECT}.{dataset}.node` KEY(id))")
            fake.run(None, f"CREATE TABLE `{bigquery_fake.PROJECT}.{dataset}.chunks_aaaaaaaaaaaaaaaa_12345678` (id INT64)")
    return fake


def test_bigquery_preview_deduplicates_chunks_and_keeps_portal_out_of_counts(install, cloud, monkeypatch, capsys):
    monkeypatch.setenv("PREVIEW", "yes")
    before = set(cloud.objects), set(cloud.datasets)
    count = len(cloud.statements)
    assert teardown.main() == 0
    output = capsys.readouterr().out
    assert "BigQuery: 10 object(s)" in output
    assert "_portal_" not in output
    for dataset, name in before[0]:
        if not name.startswith(("_portal_", "access_")):
            assert output.count(f"{dataset}.{name}\n") == 1
    assert (set(cloud.objects), set(cloud.datasets)) == before
    assert all(sql.startswith("SELECT ") for sql in cloud.statements[count:])


def test_bigquery_removal_precedes_landing_and_store_and_leaves_datasets(install, cloud, monkeypatch):
    populate(install)
    datasets = set(cloud.datasets)
    delete = type(install.store).delete
    events = []

    def checked_delete(self, key):
        assert all(name.startswith(("_portal_", "access_")) for dataset, name in cloud.objects)
        if self.url == install.store.url:
            assert install.landing.list() == []
        events.append((self.url, key))
        return delete(self, key)

    monkeypatch.setattr(type(install.store), "delete", checked_delete)
    assert teardown.main() == 0
    assert events and cloud.datasets == datasets
    assert set(cloud.objects) == {(dataset, name) for dataset in datasets for name in ("_portal_state", "access_grants", "access_audit")}
    removed = records(install.store)[0]["removed"]["objects"]
    assert len(removed) == 10
    assert len({(obj["dataset"], obj["name"]) for obj in removed}) == 10
    drops = [sql for sql in cloud.statements if sql.startswith("DROP ")]
    assert all("DATASET" not in sql and "_portal_" not in sql for sql in drops)
    assert max(i for i, sql in enumerate(drops) if "PROPERTY GRAPH" in sql) < min(
        i for i, sql in enumerate(drops) if "TABLE" in sql)
    assert teardown.main() == 0
    assert records(install.store)[-1]["outcome"] == "nothing left"


def test_inventory_routes_shared_objects_to_one_owner(install, cloud, monkeypatch):
    original = retrieval.clear
    calls = []

    def clear(prof, objects):
        calls.append(objects)
        return original(prof, objects)

    monkeypatch.setattr(retrieval, "clear", clear)
    assert teardown.main() == 0
    assert calls == []


def test_retrieval_inventory_is_cleared_with_a_local_graph_store(install, cloud, monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    chunks = {(dataset, name) for dataset, name in cloud.objects if name.startswith("chunks_")}
    assert len(chunks) == 2
    assert teardown.main() == 0
    assert not (chunks & set(cloud.objects))
    removed = records(install.store)[0]["removed"]["objects"]
    assert chunks <= {(obj["dataset"], obj["name"]) for obj in removed}


def test_tmp_bucket_is_left_to_lifecycle_without_opening_it(install, monkeypatch):
    monkeypatch.setenv("TMP_URL", "gs://unreachable-tmp-bucket")
    opened = []
    original = teardown.open_store

    def tracked(url):
        opened.append(url)
        assert url != "gs://unreachable-tmp-bucket"
        return original(url)

    monkeypatch.setattr(teardown, "open_store", tracked)
    assert teardown.main() == 0
    assert opened == [install.store.url, install.landing.url]


def test_people_remove_keeps_a_different_default(install, monkeypatch):
    install.saved["profiles"]["other"] = {**install.saved["profiles"][CLIENT], "client": "other"}
    install.saved["default"] = "other"
    install.store.put(teardown.REGISTRY, yaml.safe_dump(install.saved, sort_keys=False).encode())
    monkeypatch.setenv("PEOPLE", "remove")
    assert teardown.main() == 0
    saved = yaml.safe_load(install.store.get(teardown.REGISTRY))
    assert saved["default"] == "other" and list(saved["profiles"]) == ["other"]


def test_partial_backend_failure_records_actual_drops_and_keeps_landing_and_store(install, cloud, monkeypatch):
    removed, kept = populate(install)
    landing_before = snapshot(install.landing)
    original = graphstore.clear
    dropped = []

    def fail_after_one(objects):
        dropped.extend(original(objects[:1]))
        raise RuntimeError("injected drop failure")

    monkeypatch.setattr(graphstore, "clear", fail_after_one)
    assert teardown.main() == 2
    record, = records(install.store)
    assert record["outcome"] == "failed"
    assert record["removed"]["objects"] == [obj._asdict() for obj in dropped]
    assert len(dropped) == 1 and record["counts"]["objects"] == 1
    assert record["unconfirmed"] == []
    assert all(install.store.get(key) == data for key, data in {**removed, **kept}.items())
    assert snapshot(install.landing) == landing_before
    assert lease.holder(install.store, CLIENT) is None
