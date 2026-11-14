"""Access stores share their transaction, audit and digest contract, independently of pack and release state."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from kge import packstore
from kge.adapters import appstate
from kge.adapters.appstate import bigquery, files
from kge.adapters.graphstore.bigquery import clear_objects, dataset_inventory
from kge.adapters.objstore import open_store
from kge.pipeline.settings import ENVIRONMENT_NAMES, Settings, settings
from kge.ports import AppState, HeldObject, StateConflict, StateUnavailable, StoreError
from tests import bigquery_fake

ADMIN = "admin@example.com"
PERSON = {"username": "reader@example.com", "name": "Reader", "roles": ["reader", "analyst"]}


@pytest.fixture(params=["files", "bigquery"])
def access(request, monkeypatch, tmp_path):
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    monkeypatch.setenv("APP_STATE_BACKEND", request.param)
    monkeypatch.delenv("STORE_URL", raising=False)
    prof = SimpleNamespace(client="sample", store=tmp_path / "store")
    fake = None
    if request.param == "bigquery":
        fake = bigquery_fake.FakeBigQuery()
        monkeypatch.setattr(bigquery, "_ENSURED", set())
        monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: fake.state_post(body))
    return SimpleNamespace(new=lambda: appstate.access_state(prof), fake=fake, prof=prof)


def test_put_update_delete_and_audit(access):
    state = access.new()
    assert isinstance(state, AppState)
    assert state.access_grants() == state.access_audit() == []
    empty = state.access_digest()
    first = state.access_put(PERSON, by=ADMIN, via="api")
    assert first["sequence"] == 1 and first["before"] is None
    assert first["outside"] is False
    assert first["after"] == state.access_grants()[0]
    assert first["by"] == ADMIN and first["via"] == "api" and first["username"] == PERSON["username"]
    assert first["after"]["updated_at"] == first["at"] and first["after"]["updated_by"] == ADMIN
    assert first["after"]["roles"] == ["analyst", "reader"]
    assert first["digest"] == state.access_digest() != empty
    second = state.access_put({**PERSON, "roles": ["reader"], "name": "Renamed"}, by=ADMIN, via="job")
    assert second["before"] == first["after"] and second["sequence"] == 2
    assert second["outside"] is False
    assert access.new().access_audit() == [second, first]
    assert state.access_audit(1) == [second] and state.access_audit(0) == []
    assert state.access_audit(-1) == []
    third = state.access_delete(PERSON["username"], by=ADMIN, via="job")
    assert third["before"] == second["after"] and third["after"] is None and third["sequence"] == 3
    assert state.access_digest() == empty
    assert state.access_delete(PERSON["username"], by=ADMIN, via="job") is None
    assert len(state.access_audit()) == 3
    assert PERSON["roles"] == ["reader", "analyst"]


@pytest.mark.parametrize("access", ["bigquery"], indirect=True)
def test_unprovisioned_access_reads_do_not_create_tables(access, monkeypatch):
    statements = []

    def query(self, body):
        statements.append(body["query"].strip())
        return access.fake.state_post(body)

    monkeypatch.setattr(bigquery.BigQueryState, "_post", query)
    state = access.new()
    assert state.access_grants() == state.access_audit() == []
    assert state.access_digest() == appstate._digest([])
    assert all(statement.startswith("SELECT") for statement in statements)
    assert access.fake.held() == []
    assert state.access_delete("missing@example.com", by=ADMIN, via="api") is None
    assert access.fake.held() == []
    entry = state.access_put(PERSON, by=ADMIN, via="api")
    assert entry["sequence"] == 1
    assert state.access_grants() == [entry["after"]]


@pytest.mark.parametrize("reason,status,message", [
    ("accessDenied", 403, "Access denied: sample.access_grants"),
    ("backendError", 503, "Cannot read kge_sample_graph.access_grants"),
    ("notFound", 404, "Not found: Job query-id"),
    ("notFound", 404, "Not found: Table pilot-project:kge_other_graph.access_grants"),
    ("notFound", 404, "Not found: Table pilot-project:kge_sample_graph.access_grants_other"),
    ("notFound", 400, "Not found: Table pilot-project:kge_sample_graph.access_grants"),
    ("", None, "Not found: Table pilot-project:kge_sample_graph.access_grants")])
@pytest.mark.parametrize("read", ["access_grants", "access_audit", "access_digest"])
@pytest.mark.parametrize("access", ["bigquery"], indirect=True)
def test_access_read_errors_other_than_missing_tables_propagate(access, monkeypatch, reason, status, message, read):
    error = RuntimeError(message)
    error.reason, error.status = reason, status

    def fail(self, body):
        raise error

    monkeypatch.setattr(bigquery.BigQueryState, "_post", fail)
    with pytest.raises(RuntimeError) as caught:
        getattr(access.new(), read)()
    assert caught.value is error


@pytest.mark.parametrize("resource", ["Table pilot-project:kge_sample_graph.access_grants",
                                      "Dataset pilot-project:kge_sample_graph"])
@pytest.mark.parametrize("access", ["bigquery"], indirect=True)
def test_completed_queries_report_missing_access_state_as_empty(access, monkeypatch, resource):
    monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: {
        "jobComplete": True, "errors": [{"reason": "notFound", "message": f"Not found: {resource}"}]})
    state = access.new()
    assert state.access_grants() == state.access_audit() == []
    assert state.access_digest() == appstate._digest([])


@pytest.mark.parametrize("missing", ["access_grants", "access_audit"])
@pytest.mark.parametrize("access", ["bigquery"], indirect=True)
def test_missing_access_table_preserves_the_other_tables_evidence(access, missing):
    state = access.new()
    entry = state.access_put(PERSON, by=ADMIN, via="api")
    access.fake.run(None, f"DROP TABLE `pilot-project.kge_sample_graph.{missing}`")
    assert state.access_grants() == ([] if missing == "access_grants" else [entry["after"]])
    assert state.access_audit() == ([] if missing == "access_audit" else [entry])
    if missing == "access_grants":
        with pytest.raises(StateUnavailable, match="revision row is missing"):
            state.access_put(PERSON, by=ADMIN, via="api")


def test_digest_ignores_order_and_metadata_but_detects_each_access_field(access):
    state = access.new()
    state.access_put(PERSON, by=ADMIN, via="migration")
    original = state.access_digest()
    state.access_put({**PERSON, "roles": ["analyst", "reader"]}, by="another@example.com", via="api")
    assert state.access_digest() == original
    for field, value in (("name", "Another"), ("roles", ["reader"]), ("username", "new@example.com")):
        state.access_put({**PERSON, field: value}, by=ADMIN, via="api")
        assert state.access_digest() != original
        if field == "username":
            state.access_delete(value, by=ADMIN, via="api")
        state.access_put(PERSON, by=ADMIN, via="api")
        assert state.access_digest() == original


@pytest.mark.parametrize("same_user", [True, False])
def test_stale_read_refuses_even_two_first_grants(access, same_user):
    first, second = access.new(), access.new()
    assert first.access_grants() == second.access_grants() == []
    first.access_put(PERSON, by=ADMIN, via="job")
    row = {**PERSON, "name": "Other", "username": PERSON["username"] if same_user else "other@example.com"}
    with pytest.raises(StateConflict, match="reload"):
        second.access_put(row, by=ADMIN, via="api")
    assert len(access.new().access_audit()) == 1
    assert access.new().access_grants()[0]["name"] == "Reader"
    second.access_grants()
    assert second.access_put(row, by=ADMIN, via="api")["sequence"] == 2


def test_stale_delete_cannot_revoke_a_newer_grant(access):
    access.new().access_put(PERSON, by=ADMIN, via="job")
    stale, fresh = access.new(), access.new()
    stale.access_grants()
    fresh.access_put({**PERSON, "roles": ["steward"]}, by=ADMIN, via="api")
    with pytest.raises(StateConflict):
        stale.access_delete(PERSON["username"], by=ADMIN, via="job")
    assert access.new().access_grants()[0]["roles"] == ["steward"]


def test_stale_absent_delete_refuses_when_someone_has_since_been_granted_access(access):
    stale = access.new()
    assert stale.access_grants() == []
    access.new().access_put(PERSON, by=ADMIN, via="api")
    with pytest.raises(StateConflict, match="reload"):
        stale.access_delete(PERSON["username"], by=ADMIN, via="job")
    assert len(access.new().access_audit()) == 1


@pytest.mark.parametrize("missing_audit", [False, True])
def test_outside_edit_changes_digest_without_an_audit(access, missing_audit):
    state = access.new()
    state.access_put(PERSON, by=ADMIN, via="api")
    if missing_audit:
        if access.fake:
            access.fake.run(None, "DELETE FROM `pilot-project.kge_sample_graph.access_audit`")
        else:
            record = json.loads(state.access_store.get(files.FilesState.GRANTS))
            record["audit"] = []
            state.access_store.put(files.FilesState.GRANTS, json.dumps(record).encode())
    audit = state.access_audit()
    if access.fake:
        access.fake.run(None, "UPDATE `pilot-project.kge_sample_graph.access_grants` SET name='Outside' WHERE username!=''")
    else:
        record = json.loads(state.access_store.get(files.FilesState.GRANTS))
        record["grants"][0]["name"] = "Outside"
        state.access_store.put(files.FilesState.GRANTS, json.dumps(record).encode())
    assert state.access_digest() != (audit[0]["digest"] if audit else None)
    assert state.access_audit() == audit
    entry = state.access_put(PERSON, by=ADMIN, via="api")
    assert entry["outside"] is True
    assert access.new().access_audit()[0] == entry
    assert state.access_digest() == entry["digest"]


def test_mutating_returned_rows_does_not_mutate_the_next_audit(access):
    state = access.new()
    state.access_put(PERSON, by=ADMIN, via="job")
    rows = state.access_grants()
    rows[0]["roles"].append("admin")
    entry = state.access_put(PERSON, by=ADMIN, via="api")
    assert "admin" not in entry["before"]["roles"]


def test_failed_commit_keeps_grants_and_audit_together(access, monkeypatch):
    state = access.new()
    first = state.access_put(PERSON, by=ADMIN, via="job")
    if access.fake:
        run = access.fake.run

        def fail(loader, sql, *args, **kwargs):
            if sql.startswith("INSERT INTO") and "access_audit" in sql:
                raise RuntimeError("audit unavailable")
            return run(loader, sql, *args, **kwargs)
        monkeypatch.setattr(access.fake, "run", fail)
    else:
        put = state.access_store.put_version

        def fail(key, data, version):
            if key == files.FilesState.GRANTS:
                raise StoreError("unavailable")
            return put(key, data, version)
        monkeypatch.setattr(state.access_store, "put_version", fail)
    with pytest.raises(RuntimeError, match="unavailable"):
        state.access_put({**PERSON, "name": "Lost"}, by=ADMIN, via="api")
    assert access.new().access_grants() == [first["after"]]
    assert access.new().access_audit() == [first]


def test_jsonl_projection_failure_does_not_lose_committed_audit(tmp_path, monkeypatch):
    store = open_store(str(tmp_path))
    state = files.FilesState(None, store)
    put = store.put_version

    def fail(key, data, version):
        if key == state.AUDIT:
            raise StoreError("projection unavailable")
        return put(key, data, version)
    monkeypatch.setattr(store, "put_version", fail)
    first = state.access_put(PERSON, by=ADMIN, via="api")
    assert state.access_audit() == [first]
    monkeypatch.setattr(store, "put_version", put)
    second = state.access_put(PERSON, by=ADMIN, via="job")
    assert [json.loads(line) for line in store.get(state.AUDIT).splitlines()] == [first, second]


def test_a_write_during_a_file_read_cannot_pair_old_grants_with_a_new_version(tmp_path, monkeypatch):
    store = open_store(str(tmp_path))
    first, second = files.FilesState(None, store), files.FilesState(None, open_store(str(tmp_path)))
    original = first.access_put(PERSON, by=ADMIN, via="api")
    read = store.get_version
    raced = False

    def interrupted(key):
        nonlocal raced
        data, version = read(key)
        if key == first.GRANTS and not raced:
            raced = True
            second.access_put({**PERSON, "name": "Concurrent"}, by=ADMIN, via="job")
            version = read(key)[1]
        return data, version

    monkeypatch.setattr(store, "get_version", interrupted)
    with pytest.raises(StateConflict, match="reload"):
        first.access_put({**PERSON, "name": "Lost"}, by=ADMIN, via="api")
    assert second.access_grants()[0]["name"] == "Concurrent"
    assert second.access_audit()[1] == original
    assert len(second.access_audit()) == 2


@pytest.mark.parametrize("remote", [False, True])
def test_factory_separates_access_from_pack_and_shares_it_with_job(tmp_path, monkeypatch, remote):
    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects") if remote else "")
    prof = SimpleNamespace(client="sample", store=tmp_path / "store")
    pack = packstore.Store(tmp_path / "clients/sample", tmp_path / "run")
    state = appstate.for_profile(prof, pack)
    assert appstate.for_profile(prof, pack) is state
    entry = state.access_put(PERSON, by=ADMIN, via="migration")
    assert appstate.access_state(prof).access_grants() == [entry["after"]]
    expected = tmp_path / "objects/access/sample" if remote else tmp_path / "store/sample/access"
    assert (expected / "access-grants.json").exists()
    assert not pack.list()
    other = appstate.access_state(SimpleNamespace(client="other", store=prof.store))
    assert other.access_grants() == []


def test_regular_file_state_does_not_need_an_access_store_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    monkeypatch.delenv("STORE_URL", raising=False)
    pack = packstore.Store(tmp_path / "clients/sample", tmp_path / "run")
    state = appstate.for_profile(SimpleNamespace(client="sample"), pack)
    assert state.units() == []
    pack.write("units/unit/unit.yaml", b"state: draft\n")
    assert state.manifest("unit") == {"state": "draft"}


def test_lazy_access_store_keeps_the_factorys_selected_location(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    monkeypatch.setenv("STORE_URL", str(tmp_path / "first"))
    prof = SimpleNamespace(client="sample", store=tmp_path / "store")
    pack = packstore.Store(tmp_path / "clients/sample", tmp_path / "run")
    first = appstate.for_profile(prof, pack)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "second"))
    first.access_put(PERSON, by=ADMIN, via="api")
    assert (tmp_path / "first/access/sample/access-grants.json").exists()
    assert appstate.for_profile(prof, pack).access_grants() == []


def test_access_and_portal_tables_survive_inventory_and_forced_clear(monkeypatch, tmp_path):
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("APP_STATE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    monkeypatch.setattr(bigquery, "_ENSURED", set())
    fake = bigquery_fake.FakeBigQuery()
    monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: fake.state_post(body))
    state = appstate.access_state(SimpleNamespace(client="sample"))
    state.access_put(PERSON, by=ADMIN, via="job")
    fake.run(None, "CREATE TABLE `pilot-project.kge_sample_graph._portal_people` (id STRING)")
    def run(sql):
        return fake.run(None, sql)
    inventory = dataset_inventory(run, bigquery_fake.PROJECT, "kge_sample_graph")
    assert not any(obj.name.startswith(("access_", "_portal_")) for obj in inventory)
    forced = inventory + [HeldObject("kge_sample_graph", name, "table")
                          for name in ("access_grants", "access_audit", "_portal_people")]
    assert clear_objects(run, bigquery_fake.PROJECT, forced, forced) == sorted(inventory, key=lambda obj: obj.name)
    assert state.access_grants()[0]["username"] == PERSON["username"]
    assert fake.datasets == {"kge_sample_graph"}
    assert set(fake.held()) == {"access_grants", "access_audit", "_portal_people"}


def test_access_settings_are_read_afresh_and_documented(monkeypatch):
    from dataclasses import fields
    from kge.pipeline.settings import ROOT
    assert set(ENVIRONMENT_NAMES) == {field.name for field in fields(Settings)}
    for name in ("ACCESS_ADMINS",
                 "GRANT", "REVOKE", "LIST", "AUDIT", "MIGRATE", "EMAILS", "BY", "SIGNIN_PASSWORDS"):
        monkeypatch.delenv(name, raising=False)
        assert getattr(settings(), name) is None
        monkeypatch.setenv(name, "")
        assert getattr(settings(), name) == ""
        assert f"{name}=" in (ROOT / ".env.example").read_text()


def test_put_without_display_name_defaults_to_empty(access):
    entry = access.new().access_put({"username": PERSON["username"], "roles": ["reader"]}, by=ADMIN, via="api")
    assert entry["after"]["name"] == ""
    assert access.new().access_grants() == [entry["after"]]


def test_access_times_are_utc_in_a_non_utc_timezone(access, monkeypatch):
    try:
        with monkeypatch.context() as local:
            local.setenv("TZ", "EST5EDT")
            time.tzset()
            assert time.timezone != 0
            state = access.new()
            entry = state.access_put(PERSON, by=ADMIN, via="api")
            assert access.new().access_grants()[0]["updated_at"] == entry["at"]
            assert access.new().access_audit()[0] == entry
            assert bigquery.BigQueryState._access_time("2026-01-02 03:04:05") == "2026-01-02T03:04:05.000000+00:00"
    finally:
        time.tzset()


def test_deleting_every_grant_outside_the_api_is_recorded_on_the_next_change(access):
    state = access.new()
    state.access_put(PERSON, by=ADMIN, via="api")
    if access.fake:
        access.fake.run(None, "DELETE FROM `pilot-project.kge_sample_graph.access_grants` WHERE username!=''")
    else:
        record = json.loads(state.access_store.get(files.FilesState.GRANTS))
        record["grants"] = []
        state.access_store.put(files.FilesState.GRANTS, json.dumps(record).encode())
    assert access.new().access_grants() == []
    assert access.new().access_put(PERSON, by=ADMIN, via="api")["outside"] is True


@pytest.mark.parametrize("access", ["bigquery"], indirect=True)
@pytest.mark.parametrize("all_rows", [False, True])
def test_missing_access_revision_remains_readable_but_refuses_writes(access, all_rows):
    from kge.pipeline.access_table import changed_outside
    state = access.new()
    entry = state.access_put(PERSON, by=ADMIN, via="api")
    stale = access.new()
    stale.access_grants()
    suffix = "" if all_rows else " WHERE username=''"
    access.fake.run(None, "DELETE FROM `pilot-project.kge_sample_graph.access_grants`" + suffix)
    reader = access.new()
    assert reader.access_grants() == ([] if all_rows else [entry["after"]])
    assert changed_outside(access.prof) is all_rows
    assert reader.access_audit() == [entry]
    before = list(access.fake.statements)
    for writer in (reader, access.new()):
        with pytest.raises(StateUnavailable, match="revision row is missing"):
            writer.access_put(PERSON, by=ADMIN, via="api")
        with pytest.raises(StateUnavailable, match="revision row is missing"):
            writer.access_delete(PERSON["username"], by=ADMIN, via="api")
    assert all(statement.lstrip().startswith("SELECT") for statement in access.fake.statements[len(before):])
    with pytest.raises(StateConflict):
        stale.access_put(PERSON, by=ADMIN, via="api")
    assert reader.access_grants() == ([] if all_rows else [entry["after"]])
    assert reader.access_audit() == [entry]


def test_migration_middle_row_failure_is_atomic_and_retryable(access, monkeypatch):
    rows = [{"username": f"reader{i}@example.com", "name": "Reader", "roles": ["reader"]} for i in range(3)]
    state = access.new()
    if access.fake is None:
        original = files._change
        def fail_middle(*args, **kwargs):
            if args[3] == rows[1]["username"]:
                raise RuntimeError("middle row fault")
            return original(*args, **kwargs)
        monkeypatch.setattr(files, "_change", fail_middle)
    else:
        original = access.fake.run
        def fail_middle(params, sql):
            if "INSERT" in sql and "access_grants" in sql and "reader1@example.com" in sql:
                raise RuntimeError("middle row fault")
            return original(params, sql)
        monkeypatch.setattr(access.fake, "run", fail_middle)
    with pytest.raises(RuntimeError, match="middle row fault"):
        state.access_migrate(rows, by=ADMIN)
    assert access.new().access_grants() == access.new().access_audit() == []
    if access.fake is None:
        monkeypatch.setattr(files, "_change", original)
    else:
        monkeypatch.setattr(access.fake, "run", original)
    entries = access.new().access_migrate(rows, by=ADMIN)
    assert len(entries) == 3
    assert access.new().access_grants() == [entry["after"] for entry in entries]
    assert access.new().access_audit() == list(reversed(entries))
    assert all(entry["via"] == "migration" and not entry["outside"] for entry in entries)


def test_migration_refuses_a_concurrent_initialization(access):
    stale = access.new()
    stale.access_grants()
    first = access.new().access_migrate([PERSON], by=ADMIN)
    with pytest.raises(StateConflict):
        stale.access_migrate([{**PERSON, "username": "other@example.com"}], by=ADMIN)
    assert access.new().access_grants() == [first[0]["after"]]
    assert access.new().access_audit() == first


def test_migration_projection_keeps_an_intervening_writers_audit(tmp_path, monkeypatch):
    store = open_store(str(tmp_path))
    seed = files.FilesState(None, store)
    writer = files.FilesState(None, open_store(str(tmp_path)))
    put = store.put_version
    intervening = []

    def interrupted(key, data, version):
        result = put(key, data, version)
        if key == seed.GRANTS:
            intervening.append(writer.access_put({**PERSON, "name": "After migration"}, by=ADMIN, via="api"))
        return result

    monkeypatch.setattr(store, "put_version", interrupted)
    migrated = seed.access_migrate([PERSON], by=ADMIN)
    assert seed.access_audit() == [*intervening, *reversed(migrated)]
    assert [json.loads(line) for line in store.get(seed.AUDIT).splitlines()] == [*migrated, *intervening]
