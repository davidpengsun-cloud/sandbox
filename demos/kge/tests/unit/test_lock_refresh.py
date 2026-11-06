"""Schema refresh is deterministic and validates the new lock before touching either output."""
from __future__ import annotations

from copy import deepcopy
import json
import shutil
import threading
from types import SimpleNamespace

import pytest

from kge.compiler import lock
from kge.pipeline.settings import ROOT


def table(name="ledger"):
    return {"name": name, "columns": [{"name": "id", "type": "INTEGER", "description": None, "ordinal": 1},
                                     {"name": "amount", "type": "INTEGER", "description": None, "ordinal": 2}],
            "primary_key": ["id"], "foreign_keys": []}


def schema(*tables):
    return {"client": "fixture", "format": 1, "as_of": "2026-10-01", "sources": {"unit": {"tables": list(tables)}}}


def test_changes_covers_every_kind_with_values_and_stable_order():
    before = schema(table(), table("removed"))
    after = deepcopy(before)
    changed = after["sources"]["unit"]["tables"][0]
    changed["columns"] = [{**changed["columns"][0], "type": "BIGINT", "description": "Identifier", "ordinal": 3},
                          {"name": "added", "type": "VARCHAR", "description": None, "ordinal": 2}]
    changed["primary_key"] = ["id", "added"]
    after["sources"]["unit"]["tables"][1] = table("new")
    result = lock.changes(before, after)
    assert [(r["table"], r["column"], r["change"]) for r in result] == [
        ("ledger", None, "key-changed"), ("ledger", "added", "column-added"),
        ("ledger", "amount", "column-removed"), ("ledger", "id", "column-described"),
        ("ledger", "id", "column-moved"), ("ledger", "id", "column-retyped"),
        ("new", None, "table-added"), ("removed", None, "table-removed")]
    assert all(set(r) == {"source", "table", "column", "change", "was", "now"} and r["source"] == "unit" for r in result)
    assert result[0]["was"] == {"primary_key": ["id"], "foreign_keys": []}
    assert result[0]["now"] == {"primary_key": ["id", "added"], "foreign_keys": []}
    assert result[1]["was"] is None and result[1]["now"] == changed["columns"][1]
    assert result[2]["was"]["name"] == "amount" and result[2]["now"] is None
    assert (result[3]["was"], result[3]["now"]) == (None, "Identifier")
    assert (result[4]["was"], result[4]["now"]) == (1, 3)
    assert (result[5]["was"], result[5]["now"]) == ("INTEGER", "BIGINT")
    assert result[6]["was"] is None and result[6]["now"] == table("new")
    assert result[7]["was"] == table("removed") and result[7]["now"] is None


def test_foreign_key_changes_and_whole_source_changes():
    before = schema(table())
    after = deepcopy(before)
    key = {"columns": ["id"], "ref_table": "ledger", "ref_columns": ["id"]}
    after["sources"]["unit"]["tables"][0]["foreign_keys"] = [key]
    result = lock.changes(before, after)
    assert len(result) == 1 and result[0]["change"] == "key-changed"
    assert result[0]["now"]["foreign_keys"] == [key]
    assert lock.changes(after, before)[0]["was"]["foreign_keys"] == [key]
    after["sources"]["another"] = after["sources"].pop("unit")
    assert [(r["source"], r["change"]) for r in lock.changes(before, after)] == [
        ("another", "table-added"), ("unit", "table-removed")]
    assert all(r["change"] == "table-added" and r["was"] is None for r in lock.changes(None, after))


def test_legacy_facts_do_not_drift_or_appear_in_changes():
    before = schema({**table(), "rows": 10, "storage": 4096})
    after = schema({**table(), "rows": 100, "storage": 8192})
    assert lock.changes(before, after) == lock.drift(before, after) == []
    assert lock.drift(before, schema(table())) == []
    assert not {"rows", "storage"} & lock.changes(None, before)[0]["now"].keys()
    assert not {"rows", "storage"} & lock.changes(before, schema())[0]["was"].keys()


@pytest.fixture
def refresh(tmp_path, monkeypatch):
    clients = tmp_path / "clients"
    shutil.copytree(ROOT / "clients/meridian", clients / "meridian", ignore=shutil.ignore_patterns("baselines"))
    path = clients / "meridian/pack.lock"
    frozen = lock.read(path)
    release = SimpleNamespace(client="meridian", id="landed", manifest={"as_of": frozen["as_of"]},
                              has=lambda _: True,
                              structured=lambda source: {"tables": [
                                  {**t, "columns": [{**c, "comment": c["description"]} for c in t["columns"]]}
                                  for t in frozen["sources"][source]["tables"]]})
    monkeypatch.setattr(lock, "Zone", lambda *_: SimpleNamespace(open=lambda _: release))
    record = tmp_path / "changes.json"
    args = ["--client", "meridian", "--clients", str(clients), "--landing", str(tmp_path / "landing"),
            "--refresh", "--changes", str(record)]
    return path, frozen, record, args


def test_refresh_rerun_is_byte_identical_and_written_false(refresh):
    path, frozen, record, args = refresh
    extra = deepcopy(frozen["sources"]["retail"]["tables"][0])
    extra.update(name="new_warehouse_table", primary_key=[], foreign_keys=[])
    frozen["sources"]["retail"]["tables"].append(extra)
    assert lock.main(args) == 0
    change = json.loads(record.read_text())
    assert change["written"] is True and [r["change"] for r in change["changes"]] == ["table-added"]
    before = path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino
    assert lock.main(args) == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) == before
    assert json.loads(record.read_text()) == {"written": False, "changes": []}


@pytest.mark.parametrize("missing", ["table", "column"])
@pytest.mark.parametrize("record_exists", [False, True])
def test_pack_reference_stop_names_entries_and_writes_nothing(refresh, capsys, missing, record_exists):
    path, frozen, record, args = refresh
    tables = frozen["sources"]["retail"]["tables"]
    target = next(t for t in tables if t["name"] == "daily_balance")
    if missing == "table":
        tables.remove(target)
        for t in tables:
            t["foreign_keys"] = [k for k in t["foreign_keys"] if k["ref_table"] != "daily_balance"]
    else:
        target["columns"] = [c for c in target["columns"] if c["name"] != "ledger_balance"]
    if record_exists:
        record.write_text("previous changes\n")
    before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    assert lock.main(args) == 2
    stderr = capsys.readouterr().err
    reference = "retail.daily_balance" + (".ledger_balance" if missing == "column" else "")
    assert f"names {reference}, which the warehouse no longer holds" in stderr
    assert "pack.yaml: mappings[14].column" in stderr and "catalogue.yaml: products[6]" in stderr
    assert {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()} == before
    assert record.read_text() == "previous changes\n" if record_exists else not record.exists()


def test_refresh_migrates_old_lock_once_without_schema_changes(refresh):
    path, _, record, args = refresh
    old = lock.read(path)
    for source in old["sources"].values():
        for t in source["tables"]:
            t.update(rows=123, storage=456)
    path.write_text(lock.render(old))
    assert lock.main([a for a in args[:-2] if a != "--refresh"] + ["--check"]) == 0
    assert lock.main(args) == 0
    assert json.loads(record.read_text()) == {"written": True, "changes": []}
    assert all(not {"rows", "storage"} & t.keys() for s in lock.read(path)["sources"].values() for t in s["tables"])


def test_refresh_reuses_compiler_check(refresh, monkeypatch):
    from kge.compiler import checks
    from kge.compiler.pack import Finding
    path, frozen, record, args = refresh
    before = path.read_bytes()
    def refuse(pack, *unused):
        assert pack.lock == frozen and pack.lock_bytes == lock.render(frozen).encode()
        assert path.read_bytes() == before and not record.exists()
        return [Finding("S07", "terms.yaml:mappings[0].column", "retail.account.gone is not a column in pack.lock (source.table.column)")]
    monkeypatch.setattr(checks, "check", refuse)
    assert lock.main(args) == 2
    assert path.read_bytes() == before and not record.exists()


def test_refresh_without_a_previous_lock_records_every_table(refresh):
    path, frozen, record, args = refresh
    path.unlink()
    assert lock.main(args) == 0
    result = json.loads(record.read_text())
    assert result["written"] is True
    assert len(result["changes"]) == sum(len(s["tables"]) for s in frozen["sources"].values())
    assert all(r["change"] == "table-added" and r["was"] is None for r in result["changes"])


@pytest.mark.parametrize("field,value,kind", [("description", "A clearer description", "column-described"),
                                             ("ordinal", 99, "column-moved")])
def test_refresh_records_description_and_position_only_changes(refresh, field, value, kind):
    from kge.services.onboard.service import change_lines
    path, frozen, record, args = refresh
    column = frozen["sources"]["retail"]["tables"][0]["columns"][0]
    before = column[field]
    column[field] = value
    assert lock.main(args) == 0
    result = json.loads(record.read_text())
    assert result["written"] is True
    assert len(result["changes"]) == 1
    change = result["changes"][0]
    assert (change["change"], change["was"], change["now"]) == (kind, before, value)
    line, = change_lines({"lock": result})
    assert str(value) in line and column["name"] in line
    assert ("Description of column" if field == "description" else "moved from position") in line


def test_refresh_creates_change_record_parent_before_writing_lock(refresh, monkeypatch):
    path, frozen, record, args = refresh
    frozen["sources"]["retail"]["tables"][0]["columns"][0]["description"] = "changed"
    nested = record.parent / "new" / "folder" / record.name
    args[-1] = str(nested)
    original = lock.write
    def write(target, text):
        if target == path:
            assert nested.is_file()
            assert json.loads(nested.read_text())["changes"]
        return original(target, text)
    monkeypatch.setattr(lock, "write", write)
    assert lock.main(args) == 0
    assert json.loads(nested.read_text())["written"] is True


@pytest.mark.parametrize("failure", ["parent", "record"])
def test_refresh_does_not_write_lock_when_changes_cannot_be_written(refresh, monkeypatch, failure):
    path, frozen, record, args = refresh
    before = path.read_bytes()
    frozen["sources"]["retail"]["tables"][0]["columns"][0]["description"] = "changed"
    if failure == "parent":
        record.write_text("a file, not a folder")
        args[-1] = str(record / "changes.json")
    else:
        original = lock.write
        def write(target, text):
            if target == record:
                raise OSError("record write failed")
            return original(target, text)
        monkeypatch.setattr(lock, "write", write)
    assert lock.main(args) == 2
    assert path.read_bytes() == before


def test_refresh_names_a_missing_quoted_sql_column(refresh, capsys):
    import yaml
    path, _, record, args = refresh
    pack_path = path.with_name("pack.yaml")
    pack = yaml.safe_load(pack_path.read_text())
    pack["measures"][0]["expression"] = 'SUM("monthly_revenue"."missing_value")'
    pack_path.write_text(yaml.safe_dump(pack))
    before = path.read_bytes(), pack_path.read_bytes()
    assert lock.main(args) == 2
    assert "pack.yaml: measures[0].expression names retail.monthly_revenue.missing_value, which the warehouse no longer holds" in capsys.readouterr().err
    assert (path.read_bytes(), pack_path.read_bytes()) == before and not record.exists()


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
def test_table_facts_read_the_opened_version_including_curated_sources(tmp_path, monkeypatch, backend):
    import duckdb
    from kge.adapters.warehouse.duckdb import Warehouses
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from kge.pipeline import warehouse_versions
    from tests.bigquery_fake import install
    from kge.ports import WarehouseRefused

    fake = install(monkeypatch) if backend == "bigquery" else None
    if fake:
        import json
        fake.load_rows(None, "kge_fixture_warehouses", "_kge_catalogue",
                       [{"name": "unit", "type": "STRING"}, {"name": "version", "type": "INT64"},
                        {"name": "tables", "type": "STRING"}],
                       [{"unit": unit, "version": version, "tables": json.dumps([{"name": "ledger"}])}
                        for unit in ("unit", "xref") for version in (0, 1, 2)])
    for unit in ("unit", "xref"):
        for version, values in ((None, [("old",)]), (1, [("one",)]), (2, [("two",), ("more",)])):
            if fake:
                name = f'{unit}{"_v" + str(version) if version else ""}_ledger'
                fake.load_rows(None, "kge_fixture_warehouses", name, [{"name": "value", "type": "STRING"}],
                               [{"value": value} for (value,) in values])
            else:
                path = warehouse_versions.path(tmp_path, unit, version) if version else tmp_path / f"{unit}.duckdb"
                path.parent.mkdir(parents=True, exist_ok=True)
                con, name = duckdb.connect(str(path)), "ledger"
                con.execute(f'CREATE TABLE "{name}" (value VARCHAR)')
                con.executemany(f'INSERT INTO "{name}" VALUES (?)', values)
                con.close()
    def opened(version):
        versions = {unit: version for unit in ("unit", "xref")} if version is not None else {}
        if fake:
            warehouses = BigQueryWarehouses(tmp_path, ["unit"], ["xref"], project="pilot-project", client="fixture", versions=versions)
            monkeypatch.setattr(warehouses, "token", lambda: ("stand-in", "pilot-project"))
            return warehouses
        return Warehouses(tmp_path, ["unit"], ["xref"], versions)
    for version, expected in ((None, {"records": 1, "storage": 3}), (2, {"records": 2, "storage": 7}),
                              (1, {"records": 1, "storage": 3})):
        warehouses = opened(version)
        try:
            for unit in ("unit", "xref"):
                assert warehouses.table_facts(unit, "ledger") == expected
                with pytest.raises(WarehouseRefused, match="no such table"):
                    warehouses.table_facts(unit, "absent")
            with pytest.raises(ValueError, match="unknown warehouse"):
                warehouses.table_facts("unknown", "ledger")
        finally:
            warehouses.close()
    if fake:
        assert all(".__TABLES__`" in sql or "._kge_catalogue`" in sql for sql in fake.statements)
        assert len(fake.statements) == 12
        assert all("table_id IN (" in sql for sql in fake.statements if ".__TABLES__`" in sql)
        assert opened(0).table_facts("unit", "ledger") == {"records": 1, "storage": 3}
        fake.con.execute("UPDATE unit_v2_ledger SET value = 'changed' WHERE value = 'two'")
        fake.con.execute("INSERT INTO unit_v2_ledger VALUES ('extra')")
        assert opened(2).table_facts("unit", "ledger") == {"records": 3, "storage": 16}
        assert opened(1).table_facts("unit", "ledger") == {"records": 1, "storage": 3}
        fake.con.close()


def test_catalogue_requests_spend_no_warehouse_calls_after_the_snapshot(tmp_path, monkeypatch):
    from kge.services.ask.engine import graphview
    from kge.services.catalogue import routes
    from tests.unit.test_catalogue import released, request

    profile = released(tmp_path)
    graph = object.__new__(graphview.Graph)
    graph._role_templates = {}
    graph.profile, graph.identity = profile, profile.identity()
    graph._catalogue, graph._snapshot_lock = None, threading.Lock()
    graph._domain = {"units": ["retail", "wealth"], "curated": []}
    graph.warehouse_versions = lambda: {"retail": 2, "wealth": 1}
    graph.warehouse_inputs = lambda: {}
    graph.q = lambda query: [{"unit": unit, "name": "accounts"} for unit in ("retail", "wealth")] if "t.unit AS unit" in query else []
    calls, openings, closures = [], [], []
    def opened(profile, units, curated, versions, inputs):
        openings.append(dict(versions))
        def facts(unit, table):
            calls.append((unit, table))
            return {"records": versions[unit] * 10, "storage": 2048 if unit == "retail" else None}
        return SimpleNamespace(table_facts=facts, close=lambda: closures.append(True))
    monkeypatch.setattr(graphview, "open_warehouses", opened)
    monkeypatch.setattr(routes, "served", SimpleNamespace(engine=lambda: SimpleNamespace(profile=profile, g=graph)))
    monkeypatch.setattr(routes, "authenticate_portal", lambda _: None)
    monkeypatch.setattr(routes.identity, "readable_units", lambda *_: ["retail"])
    first = routes.whole_catalogue(request({}))
    assert routes.portal_catalogue_release(request({})).release == first.release
    assert [(p.records, p.storage) for p in first.products] == [(20, "2.0 KiB"), (10, "")]
    assert calls == [("retail", "accounts"), ("wealth", "accounts")]
    assert openings == [{"retail": 2, "wealth": 1}] and closures == [True]
    calls.clear()
    for _ in range(10):
        assert routes.whole_catalogue(request({})) == first
        assert routes.portal_catalogue_release(request({})).release == first.release
        assert [(p.id, p.records) for p in routes.catalogue_for("reader").products] == [("retail.accounts", 20)]
    assert calls == [] and len(openings) == 1
    graph.identity = {**graph.identity, "warehouses": {"retail": 3, "wealth": 1}}
    graph.warehouse_versions = lambda: graph.identity["warehouses"]
    assert routes.catalogue_for("reader").products[0].records == 30
    assert routes.portal_catalogue_release(request({})).release != first.release
    assert len(calls) == 2 and len(openings) == len(closures) == 2


@pytest.mark.parametrize("failure", [None, "open", "query"])
def test_sixty_table_engine_open_reads_metadata_once_and_survives_failure(tmp_path, monkeypatch, caplog, failure):
    from collections import defaultdict
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from kge.services.ask.engine import core, graphview
    from tests.bigquery_fake import FakeBigQuery

    fake = FakeBigQuery()
    units = ["unit", "other"]
    tables = [{"unit": unit, "name": f"table_{n}"} for unit in units for n in range(30)]
    fake.load_rows(None, "kge_fixture_warehouses", "_kge_catalogue",
                   [{"name": "unit", "type": "STRING"}, {"name": "version", "type": "INT64"},
                    {"name": "tables", "type": "STRING"}],
                   [{"unit": unit, "version": 2,
                     "tables": json.dumps([{"name": row["name"]} for row in tables if row["unit"] == unit])}
                    for unit in units])
    for row in tables:
        fake.load_rows(None, "kge_fixture_warehouses", f"{row['unit']}_v2_{row['name']}",
                       [{"name": "value", "type": "INT64"}], [{"value": 1}, {"value": 2}])
    graph = object.__new__(graphview.Graph)
    graph._role_templates = {}
    profile = SimpleNamespace(client="fixture", pin=lambda: profile, asker=lambda: None)
    graph.profile, graph.identity = profile, {"hash": "a" * 64, "warehouses": {u: 2 for u in units}}
    graph._catalogue, graph._rights, graph._snapshot_lock = None, None, threading.Lock()
    graph._domain = defaultdict(dict, units=units, curated=[], mailboxes=[], onboarded=[], vocabulary=[],
                                restricted=[], not_joinable=[], currencies=[], conversions=[])
    graph.warehouse_versions = lambda: graph.identity["warehouses"]
    graph.warehouse_inputs = lambda: {}
    graph.q = lambda query: tables if "t.unit AS unit" in query else []
    graph.close = lambda: None
    openings, queries, closed = [], [], []
    def opened(*args):
        openings.append(True)
        if failure == "open" and len(openings) == 1:
            raise RuntimeError("metadata connection unavailable")
        warehouses = BigQueryWarehouses(tmp_path, units, project="pilot-project", client="fixture", versions={u: 2 for u in units})
        warehouses.token = lambda: ("stand-in", "pilot-project")
        def query(sql):
            queries.append(sql)
            if failure == "query" and ".__TABLES__`" in sql:
                raise RuntimeError("metadata query unavailable\nsecond line")
            return fake.run(None, sql)
        warehouses._query = query
        warehouses.close = lambda: closed.append(True)
        return warehouses
    monkeypatch.setattr(core, "Graph", lambda _: graph)
    monkeypatch.setattr(core, "open_warehouses", opened)
    monkeypatch.setattr(graphview, "open_warehouses", opened)
    engine = core.Engine(profile)
    try:
        figures = engine.g.catalogue().figures
        assert len(figures) == 60
        expected_units = [] if failure == "open" else sorted(units)[:1] if failure == "query" else sorted(units)
        assert len(queries) == 2 * len(expected_units)
        assert all("COUNT(" not in sql and "SUM(" not in sql for sql in queries)
        for unit, catalogue_sql, facts_sql in zip(expected_units, queries[::2], queries[1::2]):
            assert "._kge_catalogue`" in catalogue_sql and f'WHERE unit = "{unit}" AND version = 2' in catalogue_sql
            names = ", ".join(json.dumps(f"{unit}_v2_table_{n}") for n in range(30))
            assert ".__TABLES__`" in facts_sql and f"WHERE table_id IN ({names})" in facts_sql
        assert all(value == ({"records": None, "storage": ""} if failure else {"records": 2, "storage": "2 bytes"})
                   for value in figures.values())
        warnings = [r.message for r in caplog.records if r.name == graphview.__name__]
        assert warnings == (["Catalogue figures unavailable; serving with blank counts and sizes"] if failure else [])
        for _ in range(10):
            assert engine.g.catalogue().figures is figures
        assert len(queries) == 2 * len(expected_units)
        assert len(closed) == (0 if failure == "open" else 1)
    finally:
        engine.close()
        fake.con.close()
