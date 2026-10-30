"""The warehouses' BigQuery path (issue #95, item 1.2): with WAREHOUSE_BACKEND=bigquery the estate's builders,
certification and onboarding create, load, check and read their warehouses as tables in the client's BigQuery dataset
through the estate's warehouse port (kge/ports.py EstateWarehouses; kge/adapters/warehouse/bigquery_estate.py), and no
DuckDB file is made, no DuckDB module imported and no DuckDB connection opened. Each step runs against a stand-in for
BigQuery (tests/bigquery_fake.py) that records every statement and load job and answers from rows it holds, and its
results are held to the local stack's: the same catalogue, rows, draft and landed release."""
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from kge.adapters.warehouse import open_estate, types
from kge.adapters.warehouse.duckdb import DuckDBEstate, DuckDBSession
from kge.estate import ddl, from_files, generate, spec
from kge.pipeline.profile import Profile
from kge.ports import WarehouseRefused
from tests import bigquery_fake

DEMO = Path(__file__).resolve().parents[2]
LEASING = DEMO / "examples/leasing"


def _models() -> list[dict]:
    out = []
    for schema in sorted(DEMO.glob("examples/*/schema.sql")):
        text = schema.read_text()
        try:
            out.append(ddl.parse(text, schema.parent.name))
        except ddl.DdlError:
            out += ddl.parse_client(text)
    return out


def _described(session, names) -> dict:
    return {n: session.describe(n) for n in names}


def _shape(tables: list[dict]) -> dict:
    """declared tables in describe's shape, rows left out."""
    return {t["name"]: {"columns": [{"name": c["name"], "type": c["type"], "comment": c["comment"], "ordinal": i}
                                    for i, c in enumerate(t["columns"], 1)],
                        "primary_key": t["primary_key"], "foreign_keys": t["foreign_keys"]} for t in tables}


def _statement_sets():
    for model in _models():
        yield f"example {model['unit']}", ddl.statements(model, qualified=False)
    for unit in [*spec.UNITS, *spec.CURATED_SCHEMAS]:
        yield f"meridian {unit}", generate.schema_statements(unit, None if unit in spec.SCHEMAS else spec.CURATED_SCHEMAS)
    for client in ("thornbury", "fellmoor"):
        for unit, house in from_files.read_schema(DEMO / "clients" / client / "fixture").items():
            yield f"{client} {unit}", from_files.warehouse_statements(house["tables"])


@pytest.mark.parametrize("label,statements", list(_statement_sets()), ids=lambda x: x if isinstance(x, str) else "")
def test_declared_tables_are_the_ones_duckdb_holds(tmp_path, label, statements):
    """What BigQuery's adapter reads of a warehouse's DDL (types.declared) is what DuckDB's catalogue holds: every
    column's name, type as DuckDB names it, comment and place, the primary key and the foreign keys, for every example
    unit, every Meridian warehouse and every fixture warehouse."""
    tables = types.declared(statements)
    session = DuckDBEstate(tmp_path).stage("unit", statements)
    try:
        held = {n: {k: v for k, v in d.items() if k != "rows"} for n, d in _described(session, [t["name"] for t in tables]).items()}
    finally:
        session.close()
    assert _shape(tables) == held


CASTS = {
    "INTEGER": ["12", " 12", "12 ", "12.5", "-12.5", "1e3", "abc", "+5", "-7", "007", str(2 ** 31), "", "1.4999"],
    "DECIMAL(12,2)": ["12.345", "12.355", "-12.345", "12", "1e2", "abc", " 3.1", "12.3456789", "9" * 11, "9" * 10 + ".99"],
    "BOOLEAN": ["true", "TRUE", "t", "1", "0", "yes", "no", "y", "n", "on", "false", "F", "maybe"],
    "DATE": ["2025-01-01", "2025-1-1", "2025/01/01", "20250101", "2025-01-01 10:00", "2025-02-30", " 2025-01-01"],
    "TIMESTAMP": ["2025-01-01", "2025-01-01 10:00:00", "2025-01-01T10:00:00", "2025-01-01 10:00:00.5", "x"],
    "DOUBLE": ["1.5", "1e3", "abc", " 2"],
    "SMALLINT": ["40000", "12"],
    "BIGINT": [str(2 ** 63 - 1), str(2 ** 63)],
    "VARCHAR": ["x", "x'); DROP TABLE t; --"],
}


@pytest.mark.parametrize("type_name", sorted(CASTS))
def test_a_value_is_cast_as_duckdb_casts_it_on_insert(type_name):
    """types.cast, what the BigQuery path checks a CSV's value with, keeps what DuckDB keeps (the same value) and
    refuses what DuckDB refuses."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t (v {type_name})")
    for text in CASTS[type_name]:
        try:
            con.execute("DELETE FROM t")
            con.execute("INSERT INTO t VALUES (?)", [text])
            want = con.execute("SELECT v FROM t").fetchone()[0]
        except duckdb.Error:
            with pytest.raises((ValueError, ArithmeticError)):
                types.cast(text, type_name)
            continue
        assert types.cast(text, type_name) == want, (type_name, text)


def test_rows_duckdb_refuses_are_refused_before_bigquery_sees_them():
    """A duplicate primary key, a NULL in a NOT NULL column, a duplicate UNIQUE value and an orphan foreign key are
    refused in Python, as DuckDB refuses them on insert, so a staged warehouse never loads them."""
    tables = types.declared(["CREATE TABLE p (id INTEGER PRIMARY KEY, code VARCHAR UNIQUE, name VARCHAR NOT NULL)",
                             "CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES p (id))"])
    p, c = tables
    with pytest.raises(WarehouseRefused, match="Duplicate key"):
        types.checked(p, ["id", "code", "name"], [["1", "a", "x"], ["1", "b", "y"]], {})
    with pytest.raises(WarehouseRefused, match="NOT NULL"):
        types.checked(p, ["id", "code", "name"], [["1", "a", None]], {})
    with pytest.raises(WarehouseRefused, match="violates unique"):
        types.checked(p, ["id", "code", "name"], [["1", "a", "x"], ["2", "a", "y"]], {})
    held = {"p": types.checked(p, ["id", "code", "name"], [["1", "a", "x"]], {}),
            "c": types.checked(c, ["id", "p_id"], [["1", "2"]], {})}
    with pytest.raises(WarehouseRefused, match="foreign key"):
        types.orphans(tables, held)


def _profile(root: Path) -> Profile:
    clients = root / "clients"
    (clients / "meridian").mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", clients / "meridian" / "pack.yaml")
    return Profile("meridian", "meridian", clients, root / "artifacts", root / "stores", root / "landing",
                   root / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, root / "profiles.yaml")


def _duckdb_files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if ".duckdb" in p.name)


@pytest.fixture
def local(monkeypatch):
    monkeypatch.delenv("WAREHOUSE_BACKEND", raising=False)
    return monkeypatch


@pytest.fixture
def bigquery(monkeypatch):
    """WAREHOUSE_BACKEND=bigquery with the stand-in behind every loader, and DuckDB taken away."""
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    for name in ("RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "TAGGER_PROVIDER", "MODEL_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    fake = bigquery_fake.install(monkeypatch)
    fake.opened = bigquery_fake.block_duckdb(monkeypatch)
    return fake


def _onboard(prof: Profile) -> dict:
    """A unit onboarded end to end, as the page and the job run it: its tables previewed, created with no rows,
    drafted, given its CSV rows (the draft's figures computed from them), accepted, certified and built."""
    from kge.estate import onboard
    out = {"tables": onboard.tables(prof, "leasing", LEASING / "schema.sql", "Asset Finance")}
    out["create"] = onboard.create(prof, "leasing", LEASING / "schema.sql", "Asset Finance", rows="none", as_="knowledge_engineer")
    out["draft"] = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    out["rows"] = onboard.rows(prof, "leasing", "csv", csv_dir=LEASING / "data", as_="knowledge_engineer")
    out["shown"] = onboard.show(prof)
    out["accept"] = onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    out["certify"] = onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    out["build"] = onboard.build(prof)
    return out


def _comparable(result: dict) -> dict:
    """Each step's answer but where its warehouse is, the drafted source's connection to it, and when its history was
    written."""
    def plain(value):
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items() if k not in ("warehouse", "at", "connection")}
        return [plain(v) for v in value] if isinstance(value, list) else value
    return plain(result)


def test_a_unit_is_onboarded_in_bigquery_with_no_duckdb_and_the_local_stack_s_results(tmp_path, local, monkeypatch):
    """tables, create, rows, draft, accept, certify and build on the BigQuery path give what the local stack gives (the
    preview's catalogue, the rows, every drafted item and its figure), write the unit's version 1 as tables
    leasing_v1_<table> labelled with the unit and version, stamp it in the dataset's catalogue, and make no DuckDB file
    and open no DuckDB connection anywhere."""
    on_duckdb = _onboard(_profile(tmp_path / "local"))
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    fake.opened = bigquery_fake.block_duckdb(monkeypatch)
    on_bigquery = _onboard(_profile(tmp_path / "cloud"))
    assert _comparable(on_bigquery) == _comparable(on_duckdb)
    assert on_bigquery["create"]["warehouse"] == "bigquery:kge_meridian_warehouses.leasing_v1"
    assert [i["pack"]["sources"][0]["connection"] for i in on_duckdb["draft"]["items"] if i["kind"] == "source"] == [
        {"dialect": "duckdb", "ref": "warehouses/leasing.duckdb"}]
    assert [i["pack"]["sources"][0]["connection"] for i in on_bigquery["draft"]["items"] if i["kind"] == "source"] == [
        {"dialect": "bigquery", "ref": "kge_meridian_warehouses.leasing_*"}], "the drafted source names its warehouse (#97)"
    assert fake.opened == [] and _duckdb_files(tmp_path / "cloud") == []
    assert {"leasing_v1_lessee", "leasing_v1_lease", "leasing_v1_rental", "_kge_catalogue"} <= set(fake.held())
    assert not [t for t in fake.held() if t.startswith("leasing_x")], "rows' scratch warehouse, for its figures, is gone"
    assert fake.labels["leasing_v1_rental"] == {"kge_unit": "leasing", "kge_version": "v1"}
    assert sorted((t, n) for t, n, how in fake.loads if how == "WRITE_APPEND" and t.startswith("leasing_v1_")) == sorted(
        (f"leasing_v1_{p.stem}", len(p.read_text().splitlines()) - 1) for p in (LEASING / "data").glob("*.csv"))
    created = [s for s in fake.statements if s.startswith("CREATE TABLE `pilot-project.kge_meridian_warehouses.leasing_v1_")]
    assert len(created) == 6, "version 1 made at create, with no rows, and made again with them at rows"
    assert len(created) == 6 and all("NOT ENFORCED" in s and "kge_version" in s for s in created)
    stamped = fake.rows("_kge_catalogue")
    assert [(u, v) for u, v, *_ in stamped] == [("leasing", 1)] and stamped[0][2]


def test_an_update_lands_as_version_2_in_bigquery_and_retention_drops_nothing_released(tmp_path, bigquery):
    """A live unit's new upload is drafted in a scratch warehouse (dropped after) and lands as its own tables,
    leasing_v2_<table>, beside version 1's; the scratch tables do not outlive the draft."""
    from kge.estate import onboard
    prof = _profile(tmp_path)
    onboard.create(prof, "leasing", LEASING / "schema.sql", "Asset Finance", as_="knowledge_engineer")
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-17")
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-17")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-17")
    manifest = prof.clients / "meridian/units/leasing/unit.yaml"
    held = __import__("yaml").safe_load(manifest.read_text())
    held["release"] = {"model_hash": "1" * 64}
    manifest.write_text(__import__("yaml").safe_dump(held))
    csv = {p.name: p.read_bytes() for p in sorted((LEASING / "data").glob("*.csv"))}
    staged = onboard.create(prof, "leasing", label="Asset Finance", rows="csv", csv=csv,
                            ddl_text=(LEASING / "schema.sql").read_text(), as_="knowledge_engineer")
    assert staged.get("updated") and staged["version"] == 2
    onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-18")
    assert not [t for t in bigquery.held() if t.startswith("leasing_x")], "the scratch warehouse is dropped"
    assert any(s.startswith("DROP TABLE IF EXISTS `pilot-project.kge_meridian_warehouses.leasing_x") for s in bigquery.statements)
    onboard.accept(prof, "leasing", "all", "business_team", "2026-10-18")
    onboard.certify(prof, "leasing", "all", "steward_glossary", "2026-10-18")
    onboard.build(prof)
    assert open_estate(prof.warehouses, "meridian").versions("leasing") == [2]
    assert {"leasing_v2_rental"} <= set(bigquery.held()) and bigquery.opened == [] and _duckdb_files(tmp_path) == []


def _fixture_estate(out: Path, client: str = "thornbury") -> dict:
    from kge.estate import certify
    from kge.compiler import pack as packs
    summary = from_files.build(DEMO / "clients" / client / "fixture", out, client)
    release, rows, record = certify.land(out, client, out / "landing",
                                         role_mailboxes=certify.mailboxes(DEMO / "clients" / client / packs.PACK))
    return {"summary": summary, "release": release.id,
            "landed": {p.relative_to(release.path).as_posix(): p.read_bytes() for p in sorted(release.path.rglob("*")) if p.is_file()}}


@pytest.mark.parametrize("client", ["thornbury", "fellmoor"])
def test_a_fixture_estate_is_built_and_certified_in_bigquery_as_on_duckdb(tmp_path, local, monkeypatch, client):
    """The landed certification remains backend-independent."""
    on_duckdb = _fixture_estate(tmp_path / "local", client)
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    fake.opened = bigquery_fake.block_duckdb(monkeypatch)
    on_bigquery = _fixture_estate(tmp_path / "cloud", client)
    assert on_bigquery["landed"] == on_duckdb["landed"] and on_bigquery["release"] == on_duckdb["release"]
    assert on_bigquery["summary"] == on_duckdb["summary"]
    assert fake.opened == [] and _duckdb_files(tmp_path / "cloud") == []
    units = list(from_files.read_schema(DEMO / "clients" / client / "fixture"))
    assert sorted(u for u, v, *_ in fake.rows("_kge_catalogue")) == sorted(units)
    assert all(fake.labels[t] == {"kge_unit": t.split("_")[0]} for t in fake.held() if t != "_kge_catalogue")


def test_a_fixture_refused_part_way_leaves_the_dataset_as_it_was(tmp_path, bigquery):
    """A CSV value the warehouse refuses stops the build before anything reaches BigQuery: rows are checked in Python
    first, so no table is made or replaced."""
    fixture = tmp_path / "fixture"
    shutil.copytree(DEMO / "clients/thornbury/fixture", fixture)
    first = fixture / "data/underwriting/insured.csv"
    lines = first.read_text().splitlines()
    assert lines[0].split(",")[0] == "insured_id" and lines[1].split(",")[0].isdigit(), "an INTEGER first column"
    head, row = lines[0], lines[1].split(",")
    row[0] = "not-a-number"
    first.write_text("\n".join([head, ",".join(row), *lines[2:]]) + "\n")
    with pytest.raises(WarehouseRefused, match="Could not convert 'not-a-number'"):
        from_files.build(fixture, tmp_path / "out", "thornbury")
    assert [s for s in bigquery.statements if s.startswith(("CREATE TABLE `", "DROP"))] == []
    assert bigquery.loads == []


def test_the_sync_copies_nothing_where_the_warehouses_are_bigquery_s_and_prunes_only(tmp_path, bigquery):
    """load_bigquery.sync_warehouses reads no file on the BigQuery path: the warehouses are the dataset's tables, so it
    loads nothing and drops only a labelled table whose unit the catalogue no longer holds."""
    from kge.estate import load_bigquery
    from kge.adapters.bigquery import BigQueryLoader
    out = tmp_path / "estate"
    from_files.build(DEMO / "clients/thornbury/fixture", out, "thornbury")
    bigquery.run(None, "CREATE TABLE `pilot-project.kge_thornbury_warehouses.gone_t` (`x` INT64) "
                       "OPTIONS(labels=[('kge_unit', 'gone')])")
    loads = len(bigquery.loads)
    prof = Profile("thornbury", "thornbury", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores",
                   tmp_path / "landing", out / "warehouses", DEMO / "deploy/thornbury/identities.yaml", None, tmp_path / "p.yaml")
    report = load_bigquery.sync_warehouses(BigQueryLoader(project="pilot-project"), prof, "thornbury")
    assert all(r["in_place"] for r in report.values()) and len(bigquery.loads) == loads
    assert "gone_t" not in bigquery.held() and bigquery.opened == [] and _duckdb_files(tmp_path) == []


def test_no_warehouse_file_is_packed_or_unpacked_where_the_warehouses_are_bigquery_s(tmp_path, monkeypatch):
    """The work and a release carry no .duckdb where the warehouses are BigQuery's, not even a stale one an older work
    tarball left; on the local stack they carry them as before."""
    from kge.pipeline import objstore
    app = tmp_path / "app"
    prof = Profile("meridian", "meridian", app / "clients", app / "build/artifacts", app / "build/stores", app / "build/landing",
                   app / "build/warehouses", DEMO / "deploy/meridian/identities.yaml", None, app / "deploy/profiles.yaml")
    for name in ("build/warehouses/retail.duckdb", "build/warehouses/onboarded/leasing/v1/leasing.duckdb",
                 "build/summary.json", "build/artifacts/meridian/" + "a" * 64 + "/manifest.json"):
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        (app / name).write_text("x")
    monkeypatch.delenv("WAREHOUSE_BACKEND", raising=False)
    assert any(p.name.endswith(".duckdb") for p in objstore.release_paths(prof, "a" * 64))
    work = objstore.tarball(app, objstore.work_paths(app, objstore.local_files(prof)))
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    assert not [p for p in objstore.release_paths(prof, "a" * 64) if ".duckdb" in p.name]
    assert not [p for p in objstore.work_paths(app, objstore.local_files(prof)) if ".duckdb" in p.name]
    released = objstore.tarball(app, objstore.release_paths(prof, "a" * 64))
    import tarfile
    import io
    assert not [m for m in tarfile.open(fileobj=io.BytesIO(released)).getnames() if ".duckdb" in m]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    objstore.unpack(work, fresh, objstore.local_files(prof))
    assert _duckdb_files(fresh) == [] and (fresh / "build/summary.json").is_file()


def test_meridian_s_generator_statements_are_read_as_its_spec_declares():
    """Every Meridian warehouse's DDL names its spec's columns, in order, with the spec's types and descriptions."""
    for unit, tables in [*spec.SCHEMAS.items(), *spec.CURATED_SCHEMAS.items()]:
        schemas = None if unit in spec.SCHEMAS else spec.CURATED_SCHEMAS
        held = {t["name"]: t for t in types.declared(generate.schema_statements(unit, schemas))}
        for name, table in tables.items():
            assert [(c["name"], c["type"], c["comment"]) for c in held[name]["columns"]] == \
                [(n, types.duckdb_type(t), d) for n, t, d in table["columns"]]


def test_duckdb_session_describes_a_preview_in_its_unit_schema(tmp_path):
    """The local preview (qualified statements, its tables in the unit's schema) is read where they are."""
    model = ddl.parse((LEASING / "schema.sql").read_text(), "leasing")
    with DuckDBEstate(tmp_path).preview(ddl.statements(model)) as session:
        assert isinstance(session, DuckDBSession)
        assert session.describe("lease")["foreign_keys"] == [{"columns": ["lessee_id"], "ref_table": "lessee",
                                                              "ref_columns": ["lessee_id"]}]
    assert json.dumps(ddl.preview(model, "invented", DuckDBEstate(tmp_path)), default=str)


def test_rows_that_lose_the_manifest_race_leave_version_1_as_it_was(tmp_path, bigquery, monkeypatch):
    """rows reads its rows for the draft's figures in a scratch warehouse, and stages version 1 without reading it, so
    nothing replaces version 1 in BigQuery before the manifest's compare-and-swap: losing it leaves version 1's tables
    and stamp as they were, and the next rows publishes the new rows."""
    from kge.estate import onboard
    from kge.estate.onboard import source
    prof2 = _profile(tmp_path)
    estate = open_estate(prof2.warehouses, "meridian")
    onboard.create(prof2, "leasing", LEASING / "schema.sql", "Asset Finance", rows="none", as_="knowledge_engineer")
    onboard.draft(prof2, "leasing", "knowledge_engineer", "2026-10-17")
    stamp, tables = estate.stamped("leasing", 1), sorted(t for t in bigquery.held() if t.startswith("leasing_v1_"))
    assert stamp and tables

    def lost(*args, **kwargs):
        raise onboard.OnboardError(409, "unit leasing changed by someone else; reload")
    with monkeypatch.context() as race:
        race.setattr(source, "_commit_manifest", lost)
        with pytest.raises(onboard.OnboardError, match="changed by someone else"):
            onboard.rows(prof2, "leasing", "csv", csv_dir=LEASING / "data", as_="knowledge_engineer")
    assert estate.stamped("leasing", 1) == stamp
    assert sorted(t for t in bigquery.held() if t.startswith("leasing_v1_")) == tables
    assert all(not bigquery.rows(t) for t in tables), "version 1 keeps its no rows"
    assert not [t for t in bigquery.held() if t.startswith("leasing_x")], "the scratch warehouse is gone"
    onboard.rows(prof2, "leasing", "csv", csv_dir=LEASING / "data", as_="knowledge_engineer")
    assert estate.stamped("leasing", 1) and {"leasing_v1_rental"} <= set(bigquery.held())


UPDATED = ["CREATE TABLE ledger (id INTEGER PRIMARY KEY, bank VARCHAR, account VARCHAR, on_day DATE)"]
LEDGER = [[1, None, None, "2025-06-01"], [2, None, None, "2025-06-02"], [3, None, None, "2025-06-03"]]
CHANGES = [(1, "HERE", "W-1"), (3, "HERE", "O'Brien")]


def _updated(session) -> list[tuple]:
    session.load("ledger", ["id", "bank", "account", "on_day"], LEDGER)
    assert session.update_many("ledger", "id", ["bank", "account"], CHANGES) == 2
    return [tuple(r) for r in session.execute("SELECT id, bank, account FROM ledger ORDER BY id").fetchall()]


def test_many_rows_are_updated_by_one_bigquery_job_and_by_duckdb_s_own_statements(tmp_path, monkeypatch):
    """update_many (risk 1 of the GCP review on #97): DuckDB runs one UPDATE a row, the statements the estate's
    builders always ran; BigQuery sends one UPDATE ... FROM UNNEST for them all, a single DML job, and the rows read the
    same."""
    seen = []
    session = DuckDBEstate(tmp_path / "local").stage("unit", UPDATED)
    real = session.execute
    session.execute = lambda sql, parameters=None: seen.append((sql, parameters)) or real(sql, parameters)
    on_duckdb = _updated(session)
    assert [s for s in seen if s[0].startswith("UPDATE")] == [
        ("UPDATE ledger SET bank = ?, account = ? WHERE id = ?", ["HERE", "W-1", 1]),
        ("UPDATE ledger SET bank = ?, account = ? WHERE id = ?", ["HERE", "O'Brien", 3])]
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    fake = bigquery_fake.install(monkeypatch)
    on_bigquery = _updated(open_estate(tmp_path / "cloud" / "warehouses", "meridian").stage("unit", UPDATED))
    assert on_bigquery == on_duckdb == [(1, "HERE", "W-1"), (2, None, None), (3, "HERE", "O'Brien")]
    updates = [s for s in fake.statements if s.startswith("UPDATE")]
    assert len(updates) == 1 and "FROM UNNEST([STRUCT(1 AS `id`, 'HERE' AS `bank`, 'W-1' AS `account`)" in updates[0]
