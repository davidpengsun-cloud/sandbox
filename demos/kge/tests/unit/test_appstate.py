"""Onboarding's application state (kge/adapters/appstate/) under concurrency, and its BigQuery backend's edges."""
from __future__ import annotations

import contextlib
import fcntl
import json
import shutil
from pathlib import Path

import pytest

from kge import packstore
from kge.estate import onboard
from kge.adapters import appstate
from kge.adapters.appstate import bigquery as bigquery_state, files as files_state
from kge.pipeline import unit_history
from kge.adapters.objstore import StoreConflict, open_store
from kge.pipeline.profile import Profile

DEMO = Path(__file__).resolve().parents[2]


def _remote_store(tmp_path, who: str, remote) -> packstore.Store:
    return packstore.Store(tmp_path / who / "mirror", tmp_path / who / "run", remote, "clients/acme/")


def test_two_requests_that_read_the_same_manifest_on_a_remote_store_cannot_both_commit(tmp_path):
    remote = open_store(f"file://{tmp_path}/object-store")
    files_state.FilesState(_remote_store(tmp_path, "seed", remote)).commit("leasing", {"unit": "leasing", "version": 1}, [])
    first, second = (files_state.FilesState(_remote_store(tmp_path, who, remote)) for who in ("a", "b"))
    read_first, read_second = first.manifest("leasing"), second.manifest("leasing")
    first.commit("leasing", {**read_first, "label": "A"}, [{"step": "draft"}])
    with pytest.raises(StoreConflict):
        second.commit("leasing", {**read_second, "label": "B"}, [{"step": "accept"}])
    held = files_state.FilesState(_remote_store(tmp_path, "c", remote))
    assert held.manifest("leasing")["label"] == "A", "the second request never overwrites the first"
    assert [entry["step"] for entry in held.history("leasing")] == ["draft"]


def test_a_remote_store_still_takes_the_local_lock(tmp_path):
    store = _remote_store(tmp_path, "a", open_store(f"file://{tmp_path}/object-store"))
    with store.lock():
        with (store.run_dir / "onboard.lock").open("a+b") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_a_bigquery_commit_leaves_the_caller_s_manifest_as_it_was(monkeypatch):
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    state = bigquery_state.BigQueryState(Profile("meridian", "meridian", *[Path("/nonexistent")] * 6, None,
                                           Path("/nonexistent")), sleeper=lambda _: None)
    state._ready = True
    monkeypatch.setattr(state, "_post", lambda body: {})
    mine = {"unit": "leasing", "_app_revision": 3, "requests": []}
    saved = state.commit("leasing", mine, [])
    assert mine == {"unit": "leasing", "_app_revision": 3, "requests": []} and saved["_app_revision"] == 4


def history_insert(body: dict, con) -> None:
    """The INSERT into unit_history a commit sends, its query parameters bound, read as BigQuery's dialect and run as
    DuckDB's (sqlglot) on con, whose unit_history stands for the dataset's."""
    import sqlglot
    from sqlglot import exp
    values = {p["name"]: (int(p["parameterValue"]["value"]) if p["parameterType"]["type"] == "INT64" else p["parameterValue"]["value"])
              for p in body["queryParameters"]}
    statements = [s for s in sqlglot.parse(body["query"], read="bigquery") if isinstance(s, exp.Insert)]
    tree = next(s for s in statements if s.this.find(exp.Table).name == "unit_history")
    for parameter in list(tree.find_all(exp.Parameter)):
        value = values[parameter.name]
        parameter.replace(exp.Literal.number(value) if isinstance(value, int) else exp.Literal.string(value))
    for table in tree.find_all(exp.Table):
        table.set("catalog", None)
        table.set("db", None)
    con.execute(tree.sql("duckdb"))


def test_a_bigquery_commit_records_each_request_closed_once_and_every_other_request_s(monkeypatch):
    """The live run on #97: inside the EXISTS that leaves out a request closed before, an unqualified entry named the
    history row's own column, so any earlier request-closed dropped a new one with another id. The statement the
    commit sends is run, as DuckDB reads it: request r1 closed, then r2 closed, then r1 closed again; r1 and r2 land
    once each."""
    import duckdb
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    state = bigquery_state.BigQueryState(Profile("meridian", "meridian", *[Path("/nonexistent")] * 6, None,
                                           Path("/nonexistent")), sleeper=lambda _: None)
    state._ready = True
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE unit_history (unit VARCHAR, sequence BIGINT, entry JSON)")
    monkeypatch.setattr(state, "_post", lambda body: history_insert(body, con) or {})
    manifest = {"unit": "leasing", "requests": []}
    for request in ("r1", "r2", "r1"):
        manifest = state.commit("leasing", manifest, [{"step": "request-closed", "detail": {"id": request}}])
    held = con.execute("SELECT json_extract_string(entry, '$.detail.id') FROM unit_history ORDER BY sequence").fetchall()
    assert [row[0] for row in held] == ["r1", "r2"]


def test_bigquery_tables_are_created_once_per_dataset_not_once_per_request(monkeypatch):
    """The API builds a store, and so a state, for each request, so the tables' creation is remembered per dataset."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    monkeypatch.setattr(bigquery_state, "_ENSURED", set())
    sent = []
    monkeypatch.setattr(bigquery_state.BigQueryState, "_post", lambda self, body: sent.append(body["query"]) or {})
    prof = Profile("meridian", "meridian", *[Path("/nonexistent")] * 6, None, Path("/nonexistent"))
    for _ in range(3):
        bigquery_state.BigQueryState(prof, sleeper=lambda _: None).units()
    assert sum("CREATE TABLE IF NOT EXISTS" in query for query in sent) == 1
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "other")
    bigquery_state.BigQueryState(prof, sleeper=lambda _: None).units()
    assert sum("CREATE TABLE IF NOT EXISTS" in query for query in sent) == 2, "another dataset gets its own tables"


def test_unit_history_skips_a_unit_with_no_manifest(tmp_path, monkeypatch):
    clients = tmp_path / "clients"; (clients / "meridian").mkdir(parents=True)
    prof = Profile("meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "p.yaml")
    store = packstore.for_profile(prof)
    store.write("units/aaa/schema.sql", b"-- a create stopped before its manifest")
    files_state.FilesState(store).commit("leasing", {"unit": "leasing", "version": 1}, [])
    unit_history.record(prof, "sign-off", "steward_glossary", {"model_hash": "a" * 64})
    assert [entry["step"] for entry in files_state.FilesState(store).history("leasing")] == ["sign-off"]


@pytest.fixture
def prof(tmp_path):
    clients = tmp_path / "clients"; target = clients / "meridian"; target.mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", target / "pack.yaml")
    return Profile("meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "profiles.yaml")


class TableState:
    """The BigQuery backend's shape: manifests and the job in tables, no unit.yaml in the store."""

    tables = True

    def __init__(self, store):
        self.store, self.rows, self.history_rows, self.job_row = store, {}, {}, None

    @contextlib.contextmanager
    def lock(self):
        with self.store.lock():
            yield

    def units(self):
        return sorted(self.rows)

    def manifest(self, unit):
        if unit not in self.rows:
            raise FileNotFoundError(unit)
        return json.loads(json.dumps(self.rows[unit]))

    def history(self, unit):
        return list(self.history_rows.get(unit, []))

    def snapshot(self, unit):
        return self.manifest(unit), self.history(unit)

    def commit(self, unit, manifest, additions):
        self.rows[unit] = json.loads(json.dumps(manifest))
        self.history_rows.setdefault(unit, []).extend(additions)
        return manifest

    def restore(self, manifests, additions, drop=()):
        """BigQueryState.restore's effect: the manifests put back, each kept history appended to, and the dropped units'
        rows gone, their history with them, as the files backend removes units/<unit>/."""
        for unit, manifest in manifests.items():
            self.rows[unit] = {k: v for k, v in json.loads(json.dumps(manifest)).items() if k != "_app_revision"}
        dropped = set(drop) - set(manifests)
        for unit in dropped:
            self.rows.pop(unit, None); self.history_rows.pop(unit, None)
        for unit, entries in additions.items():
            if unit not in dropped:
                self.history_rows.setdefault(unit, []).extend(entries)

    def delete(self, unit):
        self.rows.pop(unit, None); self.history_rows.pop(unit, None)

    def job(self):
        return self.job_row

    def save_job(self, job):
        self.job_row = job

    def clear_job(self):
        self.job_row = None


def test_reset_counts_units_from_the_state_backend_and_clears_its_job_with_the_last(monkeypatch, prof):
    held = {}
    monkeypatch.setattr(appstate, "for_profile", lambda profile, store: held.setdefault("state", TableState(store)))
    artifacts = prof.artifacts / prof.client
    (artifacts / ("a" * 64)).mkdir(parents=True)
    (artifacts / "current").symlink_to("a" * 64)
    ddl = (DEMO / "examples/leasing/schema.sql").read_text()
    for unit in ("leasing", "fleet"):
        onboard.create(prof, unit, label=unit.title(), rows="invented", ddl_text=ddl.replace("leasing", unit),
                       as_="knowledge_engineer")
    state, store = held["state"], packstore.for_profile(prof)
    assert not store.exists("units/leasing/unit.yaml"), "no manifest file: the table holds it"
    state.save_job({"id": "j1", "step": "checks", "state": "done"})
    store.write("onboard/request.json", b"{}")
    onboard.reset(prof, "leasing")
    assert state.job() is not None and store.exists("onboard/request.json"), "another unit remains: its job is kept"
    onboard.reset(prof, "fleet")
    assert state.job() is None and not store.exists("onboard/request.json"), "the last unit's reset clears the job"


def test_a_templated_semantic_folder_and_a_state_backend_with_no_credentials_are_one_line_errors(monkeypatch, prof, capsys):
    monkeypatch.setattr(onboard.cli, "resolve", lambda _p=None: prof)
    code = onboard.main(["create", "--unit", "bancassurance", "--semantic", str(DEMO / "examples/bancassurance-unsafe"),
                         "--as", "knowledge_engineer"])
    err = capsys.readouterr().err
    assert code == 2 and err == "onboard: models/schema.yml:1: templates are refused\n", err

    import google.auth.exceptions
    from kge.adapters import bigquery

    def no_credentials(project=None):
        raise google.auth.exceptions.DefaultCredentialsError("Your default credentials were not found.\nSee the docs.")
    monkeypatch.setattr(bigquery_state, "bq_account", no_credentials)
    monkeypatch.setattr(bigquery, "bq_account", no_credentials)
    monkeypatch.setattr(bigquery_state, "_ENSURED", set())
    monkeypatch.setenv("APP_STATE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    code = onboard.main(["status"])
    err = capsys.readouterr().err
    assert code == 2 and err == ("onboard: APP_STATE_BACKEND=bigquery cannot sign in to BigQuery: Your default credentials "
                                 "were not found\n"), err


def _bigquery_state(monkeypatch, reply):
    """A BigQueryState on a fresh process's table cache whose every query goes to reply(sql), and the queries sent."""
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    monkeypatch.setattr(bigquery_state, "_ENSURED", set())
    sent = []

    def post(self, body):
        sent.append(body["query"])
        return reply(body["query"])
    monkeypatch.setattr(bigquery_state.BigQueryState, "_post", post)
    prof = Profile("meridian", "meridian", *[Path("/nonexistent")] * 6, None, Path("/nonexistent"))
    return (lambda: bigquery_state.BigQueryState(prof, sleeper=lambda _: None)), sent


def _table(**row) -> dict:
    return {"schema": {"fields": [{"name": name} for name in row]}, "rows": [{"f": [{"v": v} for v in row.values()]}]}


def test_a_bigquery_snapshot_reads_the_unit_s_history_without_a_correlated_subquery(monkeypatch):
    """Issue #93 item 1: BigQuery refuses a subquery over unit_history, whose entry is JSON, correlated on m.unit (HTTP
    400), so the history is matched on the unit's parameter, in the same one statement."""
    def reply(sql):
        if "unit_history" in sql and "unit_manifest" in sql:
            return _table(revision="2", manifest=json.dumps({"unit": "leasing"}), history=json.dumps([{"step": "draft"}]))
        return {}
    new_state, sent = _bigquery_state(monkeypatch, reply)
    manifest, history = new_state().snapshot("leasing")
    assert manifest == {"unit": "leasing", "_app_revision": 2} and history == [{"step": "draft"}]
    statement = next(sql for sql in sent if "unit_history" in sql and "unit_manifest" in sql and "SELECT" in sql
                     and "CREATE" not in sql)
    assert "h.unit=@unit" in statement and "m.unit" not in statement, statement


def _create_refused_by_http(sql):
    if "CREATE TABLE IF NOT EXISTS" in sql:
        err = RuntimeError("BigQuery state error (HTTP 403): Access Denied: Dataset pilot-project:kge_meridian_graph: "
                           "Permission bigquery.tables.create denied on dataset pilot-project:kge_meridian_graph")
        err.reason = "accessDenied"
        raise err
    return None


def _create_refused_in_the_job(sql):
    if "CREATE TABLE IF NOT EXISTS" in sql:
        return {"jobComplete": True, "errors": [{"reason": "accessDenied", "message": "Access Denied: Permission "
                                                 "bigquery.tables.create denied on dataset kge_meridian_graph"}]}
    return None


@pytest.mark.parametrize("refuse", [_create_refused_by_http, _create_refused_in_the_job], ids=["http-403", "job-error"])
def test_a_reader_that_may_not_create_the_state_tables_still_reads_them(monkeypatch, refuse):
    """Issue #93 item 2: tables the job made are read by an account without bigquery.tables.create. The refused CREATE
    is not remembered (Grok on #97): each call tries it again, so a grant still propagating takes effect, and once a
    CREATE passes it is not sent again in the process."""
    def reply(sql):
        refused = refuse(sql)
        if refused is not None:
            return refused
        if "onboard_job" in sql:
            return _table(state=json.dumps({"unit": "leasing"}))
        if "unit_history" in sql:
            return _table(revision="1", manifest=json.dumps({"unit": "leasing"}), history="[]")
        return _table(unit="leasing")
    new_state, sent = _bigquery_state(monkeypatch, reply)
    assert new_state().units() == ["leasing"]
    assert new_state().job() == {"unit": "leasing"}
    assert new_state().snapshot("leasing") == ({"unit": "leasing", "_app_revision": 1}, [])
    assert sum("CREATE TABLE IF NOT EXISTS" in sql for sql in sent) == 3
    assert bigquery_state._ENSURED == set()


def test_a_create_that_passes_after_a_denial_is_remembered(monkeypatch):
    """A grant that arrives after the first call: the next call's CREATE passes and is not sent again."""
    replies = iter([_create_refused_by_http, lambda sql: None, lambda sql: None])

    def reply(sql):
        if "CREATE TABLE IF NOT EXISTS" in sql:
            refused = next(replies)(sql)
            return refused if refused is not None else {}
        return _table(unit="leasing")
    new_state, sent = _bigquery_state(monkeypatch, reply)
    state = new_state()
    assert state.units() == ["leasing"] and state.units() == ["leasing"] and new_state().units() == ["leasing"]
    assert sum("CREATE TABLE IF NOT EXISTS" in sql for sql in sent) == 2


def _denied_with(reason, message, status=403):
    err = RuntimeError(f"BigQuery state error (HTTP {status}): {message}")
    err.reason, err.status = reason, status
    return err


@pytest.mark.parametrize("error", [
    _denied_with("invalid", "Syntax error near permission"),
    _denied_with("", "Permission bigquery.tables.create denied on dataset kge_meridian_graph"),
    _denied_with("accessDenied", "Access Denied: Project pilot-project: User does not have bigquery.jobs.create "
                                 "permission in project pilot-project."),
    _denied_with("accessDenied", "Access Denied", status=400),
], ids=["text-only", "no-reason", "jobs-create", "not-403"])
def test_only_the_create_s_own_access_denied_is_passed_over(monkeypatch, error):
    """Matched on BigQuery's accessDenied reason and a 403 for the CREATE, never on text that mentions a permission, and
    not a refusal of the query job itself (bigquery.jobs.create), which no read could get past (Grok on #97)."""
    def reply(sql):
        if "CREATE TABLE IF NOT EXISTS" in sql:
            raise error
        return _table(unit="leasing")
    new_state, sent = _bigquery_state(monkeypatch, reply)
    with pytest.raises(RuntimeError) as raised:
        new_state().units()
    assert raised.value is error


@pytest.mark.parametrize("error", [
    RuntimeError("BigQuery state error (HTTP 400): Syntax error"),
    RuntimeError("BigQuery state error (HTTP 503): backend error"),
    bigquery_state.StateUnavailable("APP_STATE_BACKEND=bigquery cannot sign in to BigQuery: permission denied by the "
                                    "metadata server"),
], ids=["invalid-query", "unavailable", "no-sign-in"])
def test_a_state_table_creation_that_fails_for_any_other_reason_still_fails(monkeypatch, error):
    def reply(sql):
        raise error
    new_state, sent = _bigquery_state(monkeypatch, reply)
    with pytest.raises(type(error)):
        new_state().units()
    assert bigquery_state._ENSURED == set(), "the tables are tried again"
