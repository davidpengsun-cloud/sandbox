"""A failed onboard restores unit keys and native state, including files and records that were absent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kge import packstore
from kge.adapters import appstate
from kge.adapters.appstate.bigquery import BigQueryState
from kge.adapters.appstate.files import FilesState
from kge.estate.onboard import stage
from kge.pipeline.profile import Profile
from tests.bigquery_fake import FakeBigQuery


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    return Profile("meridian", "meridian", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores",
                   tmp_path / "landing", tmp_path / "warehouses", Path("/unused-identities"), None,
                   tmp_path / "profiles.yaml")


@pytest.mark.parametrize("remote", [False, True], ids=["folder", "object-store"])
def test_restore_keeps_exact_pack_bytes_and_absences(prof, monkeypatch, tmp_path, remote):
    if remote:
        monkeypatch.setenv("STORE_URL", (tmp_path / "objects").as_uri())
    store = packstore.for_profile(prof)
    initial = {"units/leasing/unit.yaml": b"unit: leasing\nversion: 1\n_history_entries: 0\n",
               "units/leasing/history.jsonl": b'{ "step" : "uncounted" }\n',
               "units/leasing/draft.yaml": b"warehouse: {version: 1}\n",
               "units/leasing/update/data/rows.csv": b"id,value\r\n1,4\r\n",
               "units/leasing/schema.sql": b"old schema\n",
               "units/other/unit.yaml": b"unit: other\n"}
    for key, value in initial.items():
        store.write(key, value)
    captured = stage.capture(prof, ["leasing", "new", "leasing"])
    assert {key: store.read(key) for key in store.list("units/")} == initial
    store.delete("units/leasing/update/")
    store.write("units/leasing/schema.sql", b"replacement")
    store.write("units/leasing/draft.yaml", b"warehouse: {version: 2}\n")
    state = FilesState(store)
    state.commit("leasing", {"unit": "leasing", "version": 2, "inputs": {"rows": "new"}, "requests": []},
                 [{"step": "update"}])
    state.commit("new", {"unit": "new", "version": 1}, [{"step": "build"}])
    store.write("units/new/data/rows.csv", b"new data")
    store.write("units/leasing/new-file", b"new")
    store.write("units/other/unit.yaml", b"unit: other\nversion: 9\n")
    stage.restore(prof, captured)
    expected = {**initial, "units/other/unit.yaml": b"unit: other\nversion: 9\n"}
    assert {key: store.read(key) for key in store.list("units/")} == expected
    assert not store.exists("units/new/unit.yaml")
    assert state.history("leasing") == []
    stage.restore(prof, captured)
    assert {key: store.read(key) for key in store.list("units/")} == expected
    if remote:
        assert {key.removeprefix(store.prefix): store.remote.get(key)
                for key in store.remote.list(store.prefix + "units/")} == expected


def test_files_checkpoint_restores_native_history_count_and_open_requests(prof):
    store = packstore.for_profile(prof)
    state = FilesState(store)
    raw = (b"unit: leasing\ninputs: {ddl: earlier}\nrequests: [{id: open-1}]\n"
           b"_history_entries: 1\nversion: 7\n")
    history = b'{"step":"draft"}\n{ "step": "uncounted" }\n'
    store.write("units/leasing/unit.yaml", raw)
    store.write("units/leasing/history.jsonl", history)
    checkpoint = state.capture_units(["leasing", "new"])
    state.commit("leasing", {"unit": "leasing", "inputs": {}, "requests": []}, [{"step": "build"}])
    state.commit("new", {"unit": "new"}, [{"step": "build"}])
    state.restore_units(checkpoint)
    assert store.read("units/leasing/unit.yaml") == raw
    assert store.read("units/leasing/history.jsonl") == history
    assert state.manifest("leasing")["requests"] == [{"id": "open-1"}]
    assert state.history("leasing") == [{"step": "draft"}]
    assert not store.exists("units/new/unit.yaml") and not store.exists("units/new/history.jsonl")


@pytest.fixture
def table_state(prof, monkeypatch):
    """Execute the adapter's actual BigQuery statements with bound parameters in an in-memory SQL database."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "test-project")
    monkeypatch.setattr("kge.adapters.appstate.bigquery._ENSURED", set())
    state = BigQueryState(prof, sleeper=lambda _: None)
    fake = FakeBigQuery()
    sent = []

    def post(body):
        sent.append(body)
        return fake.state_post(body)

    monkeypatch.setattr(state, "_post", post)
    monkeypatch.setattr(appstate, "for_profile", lambda prof, store: state)
    state._ensure()
    sent.clear()
    yield state, fake.con, sent
    fake.con.close()


def _native_rows(con):
    return {table: con.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
            for table in ("unit_manifest", "unit_history", "unit_requests")}


def test_table_restore_preserves_native_revisions_sequences_requests_and_absences(prof, table_state):
    state, con, sent = table_state
    manifest = {"unit": "leasing", "version": 7, "inputs": {"ddl": "before"}, "requests": [{"id": "open-1"}]}
    con.execute("INSERT INTO unit_manifest VALUES (?, ?, ?)", ["leasing", 11, json.dumps(manifest)])
    con.execute("INSERT INTO unit_history VALUES (?, ?, ?)", ["leasing", 9000007, '{"step":"draft"}'])
    con.execute("INSERT INTO unit_history VALUES (?, ?, ?)", ["leasing", 10000002, '{"step":"accept"}'])
    con.execute("INSERT INTO unit_requests VALUES (?, ?, ?)", ["leasing", "open-1", '{"id":"open-1","state":"open"}'])
    con.execute("INSERT INTO unit_manifest VALUES ('other', 17, '{}')")
    before = _native_rows(con)
    store = packstore.for_profile(prof)
    store.write("units/leasing/draft.yaml", b"warehouse: {version: 7}\n")
    captured = stage.capture(prof, ["leasing", "new"])
    assert _native_rows(con) == before
    con.execute("UPDATE unit_manifest SET revision=12, manifest='{}' WHERE unit='leasing'")
    con.execute("DELETE FROM unit_history WHERE unit='leasing'")
    con.execute("DELETE FROM unit_requests WHERE unit='leasing'")
    con.execute("INSERT INTO unit_manifest VALUES ('new', 1, '{}')")
    con.execute("INSERT INTO unit_history VALUES ('new', 0, '{}')")
    con.execute("INSERT INTO unit_requests VALUES ('new', 'new-request', '{}')")
    store.write("units/leasing/draft.yaml", b"warehouse: {version: 8}\n")
    stage.restore(prof, captured)
    restored = _native_rows(con)
    for table, rows in before.items():
        assert [(unit, native, json.loads(raw)) for unit, native, raw in restored[table]] == [
            (unit, native, json.loads(raw)) for unit, native, raw in rows]
    assert store.read("units/leasing/draft.yaml") == b"warehouse: {version: 7}\n"
    assert state.manifest("leasing") == {**manifest, "_app_revision": 11}
    assert state.history("leasing") == [{"step": "draft"}, {"step": "accept"}]
    transaction = next(body["query"] for body in sent if "BEGIN TRANSACTION" in body["query"])
    assert transaction.count("BEGIN TRANSACTION") == transaction.count("COMMIT TRANSACTION") == 1
    assert "onboard_job" not in transaction


def test_table_restore_retries_an_aborted_transaction_and_propagates_other_failures(table_state, monkeypatch):
    state, con, sent = table_state
    checkpoint = state.capture_units(["new"])
    execute = state._query
    attempts = []

    def abort_once(sql, **params):
        attempts.append(sql)
        if len(attempts) == 1:
            raise RuntimeError("transaction aborted")
        return execute(sql, **params)

    monkeypatch.setattr(state, "_query", abort_once)
    state.restore_units(checkpoint)
    assert len(attempts) == 2 and attempts[0] == attempts[1]

    def refused(sql, **params):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(state, "_query", refused)
    with pytest.raises(RuntimeError, match="permission denied"):
        state.restore_units(checkpoint)


def test_empty_capture_does_not_create_state_tables(prof, table_state, monkeypatch):
    state, con, sent = table_state
    state._ready = False
    captured = stage.capture(prof, [])
    stage.restore(prof, captured)
    assert sent == []
