"""The onboarding DDL boundary: accepted catalogue, deterministic rows and hostile statements."""
from pathlib import Path

import duckdb
import pytest

from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.estate import ddl
from kge.ports import WarehouseRefused

DEMO = Path(__file__).resolve().parents[2]
SAFE = (DEMO / "examples/leasing/schema.sql").read_text()


def test_leasing_ddl_catalogue_and_rows_are_deterministic(tmp_path):
    model = ddl.parse(SAFE, "leasing")
    a, b = ddl.build(model, DuckDBEstate(tmp_path / "one"), True), ddl.build(model, DuckDBEstate(tmp_path / "two"), True)
    one, two = tmp_path / "one" / "leasing.duckdb", tmp_path / "two" / "leasing.duckdb"
    assert a == b
    assert [(t["name"], t["rows"]) for t in a["tables"]] == [("lessee", 12), ("lease", 12), ("rental", 120)]
    assert sum(len(t["foreign_keys"]) for t in a["tables"]) == 2
    assert all(t["comment"] and all(c["comment"] for c in t["columns"]) for t in a["tables"])
    for name, key in (("lessee", "lessee_id"), ("lease", "lease_id"), ("rental", "rental_id")):
        with duckdb.connect(str(one), read_only=True) as x, duckdb.connect(str(two), read_only=True) as y:
            assert x.execute(f"SELECT * FROM {name} ORDER BY {key}").fetchall() == y.execute(
                f"SELECT * FROM {name} ORDER BY {key}").fetchall()


@pytest.mark.parametrize("sql,kind", [
    ("CREATE TABLE leasing.x AS SELECT * FROM read_text('README.md')", "CREATE TABLE AS SELECT"),
    ("CREATE VIEW x AS SELECT 1", "CREATE VIEW"), ("CREATE MACRO x() AS 1", "CREATE MACRO"),
    ("CREATE SECRET x (TYPE s3)", "CREATE SECRET"), ("ATTACH 'x'", "ATTACH"), ("INSTALL httpfs", "INSTALL"),
    ("LOAD httpfs", "LOAD"), ("COPY x FROM 'x'", "COPY"), ("INSERT INTO x VALUES (1)", "INSERT"),
    ("DROP TABLE x", "DROP"), ("PRAGMA version", "PRAGMA"), ("SET threads=1", "SET"),
    ("CREATE TABLE leasing.x (a INTEGER DEFAULT 1)", "DEFAULT"),
    ("CREATE TABLE leasing.x (a INTEGER CHECK (a > 0))", "CHECK"),
])
def test_refused_ddl_names_the_statement_and_kind(sql, kind):
    with pytest.raises(ddl.DdlError, match=rf"statement 1, {kind}: refused"):
        ddl.parse(sql, "leasing")


@pytest.mark.parametrize("sql", ["", "   \n\t", "-- only a comment\n", "/* only a comment */", ";", "; ;\n;"])
def test_ddl_with_no_statements_is_refused_cleanly(sql):
    with pytest.raises(ddl.DdlError, match="no statements"):
        ddl.parse(sql, "leasing")


@pytest.mark.parametrize("sql", ["'", "/*"])
def test_tokenizer_errors_are_clean_refusals(sql):
    with pytest.raises(ddl.DdlError, match="statement 1, SQL: refused"):
        ddl.parse(sql, "leasing")


def test_deeply_nested_ddl_is_refused_before_the_parser_recurses():
    sql = "CREATE TABLE leasing.x (a " + "(" * 500 + "INTEGER" + ")" * 500 + ")"
    with pytest.raises(ddl.DdlError, match="nesting.*100 parentheses"):
        ddl.parse(sql, "leasing")


def test_duckdb_has_external_access_disabled(tmp_path):
    with DuckDBEstate(tmp_path).preview([]) as con, pytest.raises(WarehouseRefused):
        con.execute("SELECT * FROM read_text('README.md')")


def test_client_mode_accepts_two_schemas_while_unit_mode_refuses_them():
    text = "CREATE SCHEMA one; CREATE TABLE one.a (id INTEGER); CREATE SCHEMA two; CREATE TABLE two.b (id INTEGER)"
    assert [m["unit"] for m in ddl.parse_client(text)] == ["one", "two"]
    with pytest.raises(ddl.DdlError, match="second schema"):
        ddl.parse(text, "one")


def test_invented_database_is_opened_read_only_without_external_access(tmp_path):
    path = tmp_path / "invented.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA sample")
        con.execute("CREATE TABLE sample.fact(id INTEGER, amount_gbp DECIMAL(10,2))")
        con.execute("INSERT INTO sample.fact VALUES (1, 2.50)")
    before = path.read_bytes()
    models, files = DuckDBEstate(tmp_path).read_database(path)
    assert models[0]["unit"] == "sample" and files["sample"]["fact.csv"].endswith(b"1,2.50\n")
    assert path.read_bytes() == before


def test_database_table_names_are_validated_before_catalogue_sql(tmp_path):
    path = tmp_path / "invented.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE SCHEMA sample')
        con.execute('CREATE TABLE sample."we""ird" (id INTEGER)')
    with pytest.raises(WarehouseRefused, match=r"table name 'we\"ird' must match") as refused:
        DuckDBEstate(tmp_path).read_database(path)
    assert "Parser Error" not in str(refused.value) and "\n" not in str(refused.value)


def test_main_schema_database_with_an_invalid_stem_says_to_name_a_schema(tmp_path):
    path = tmp_path / "not-valid.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE fact (id INTEGER)")
    with pytest.raises(WarehouseRefused, match="uses the main schema; name a schema") as refused:
        DuckDBEstate(tmp_path).read_database(path)
    assert "\n" not in str(refused.value)


def test_unqualified_warehouse_statements_do_not_rewrite_comment_text(tmp_path):
    text = """CREATE TABLE leasing.t (id INTEGER);
COMMENT ON TABLE leasing.t IS 'Copied from leasing.contracts and "leasing".x';
COMMENT ON COLUMN leasing.t.id IS 'See leasing.t for detail';"""
    cat = ddl.build(ddl.parse(text, "leasing"), DuckDBEstate(tmp_path), None)
    assert cat["tables"][0]["comment"] == 'Copied from leasing.contracts and "leasing".x'
    assert cat["tables"][0]["columns"][0]["comment"] == "See leasing.t for detail"


@pytest.mark.parametrize("unit", sorted(ddl.RESERVED_UNITS))
def test_duckdb_reserved_unit_names_are_refused(unit):
    with pytest.raises(ddl.DdlError, match="reserved by DuckDB"):
        ddl.parse("CREATE TABLE t (id INTEGER)", unit)


@pytest.mark.parametrize("sql", [
    "CREATE TEMP TABLE leasing.t (id INTEGER)",
    "CREATE OR REPLACE TABLE leasing.t (id INTEGER)",
    "CREATE TABLE IF NOT EXISTS leasing.t (id INTEGER)",
    "CREATE TABLE other.leasing.t (id INTEGER)",
])
def test_create_table_variants_that_would_be_silently_normalised_are_refused(sql):
    with pytest.raises(ddl.DdlError, match="CREATE TABLE: refused"):
        ddl.parse(sql, "leasing")


def test_ddl_resource_limits_are_checked_before_duckdb():
    tables = ";".join(f"CREATE TABLE t{i} (id INTEGER)" for i in range(ddl.MAX_TABLES + 1))
    with pytest.raises(ddl.DdlError, match=f"at most {ddl.MAX_TABLES} tables"):
        ddl.parse(tables, "leasing")
    columns = ",".join(f"c{i} INTEGER" for i in range(ddl.MAX_COLUMNS + 1))
    with pytest.raises(ddl.DdlError, match=f"at most {ddl.MAX_COLUMNS} columns"):
        ddl.parse(f"CREATE TABLE t ({columns})", "leasing")


def test_csv_values_are_parameters_not_sql(tmp_path):
    model = ddl.parse("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)", "leasing")
    attack = "x'); DROP TABLE t; SELECT * FROM read_text('/etc/passwd'); --"
    rows = ddl.read_csvs(model, {"t.csv": f"id,value\n1,{attack}\n".encode()})
    path = tmp_path / "leasing.duckdb"
    ddl.build(model, DuckDBEstate(tmp_path), {"csv": rows})
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT value FROM t").fetchone()[0] == attack
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1


@pytest.mark.parametrize("files, reason", [
    ({"t.csv": b"wrong\n1\n"}, "header must be id, value"),
    ({"t.csv": b"id,value\n1\n"}, "expected 2 fields, got 1"),
    ({"t.csv": b"id,value\n1,\xff\n"}, "CSV is not UTF-8"),
])
def test_csv_shape_and_encoding_are_refused(files, reason):
    model = ddl.parse("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)", "leasing")
    with pytest.raises(ddl.DdlError, match=reason):
        ddl.read_csvs(model, files)


def test_csv_limits_and_orphans_are_refused(monkeypatch, tmp_path):
    model = ddl.parse("CREATE TABLE p (id INTEGER PRIMARY KEY); "
                      "CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES p(id))", "leasing")
    with pytest.raises(ddl.DdlError, match="larger than"):
        ddl.read_csvs(model, {"p.csv": b"id\n" + b"1" * (ddl.MAX_CSV_BYTES + 1), "c.csv": b"id,p_id\n"})
    monkeypatch.setattr(ddl, "MAX_CSV_ROWS", 1)
    with pytest.raises(ddl.DdlError, match="more than 1 rows"):
        ddl.read_csvs(model, {"p.csv": b"id\n1\n2\n", "c.csv": b"id,p_id\n"})
    monkeypatch.setattr(ddl, "MAX_CSV_ROWS", 100_000)
    rows = ddl.read_csvs(model, {"p.csv": b"id\n1\n", "c.csv": b"id,p_id\n1,999\n"})
    with pytest.raises(ddl.DdlError, match="orphan foreign key p_id=999"):
        ddl.build(model, DuckDBEstate(tmp_path), {"csv": rows})
