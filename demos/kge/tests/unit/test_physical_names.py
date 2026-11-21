"""The code carries a physical name as the warehouse spells it (#176): an upper-case table and its upper-case columns, as
SAGE names them, pass the gate under their own names on DuckDB and on BigQuery, a query that writes them in another case
is read as DuckDB reads it (a table's and a column's name in any case), BigQuery, which reads a table's name only as
spelled, is sent the dataset's spelling, and the planner reads SAGE's indicators and a signed amount as every warehouse
reads a condition and a figure."""
from pathlib import Path

import pytest
import sqlglot

from kge.pipeline.sqlguard import Allowlist, as_figure, check, warehouse_names

T = "TRAN_FACT_1"
COLS = {"DW_TRAN_ID", "TRAN_AM", "DW_TRAN_DIRCTN_CD", "CURR_IN", "DW_TRAN_DT"}


def allowlist() -> Allowlist:
    return Allowlist("wealth", {T}, {T: set(COLS)}, restricted={(T, "ACCT_NM")}, schema={T: COLS | {"ACCT_NM"}},
                     measures={(T, "TRAN_AM")})


@pytest.mark.parametrize("sql", ["SELECT sum(f.TRAN_AM) FROM TRAN_FACT_1 f WHERE f.CURR_IN = 1",
                                 "SELECT sum(f.tran_am) FROM tran_fact_1 f WHERE f.curr_in = 1",
                                 "SELECT sum(TRAN_FACT_1.TRAN_AM) FROM TRAN_FACT_1",
                                 "SELECT sum(tran_fact_1.Tran_Am) FROM Tran_Fact_1"])
def test_duckdb_reads_an_upper_case_table_and_its_columns_in_any_case(sql):
    assert check(sql, allowlist(), "duckdb") == []
    tree = warehouse_names(sqlglot.parse_one(sql, read="duckdb"), allowlist(), "duckdb")
    assert {t.name for t in tree.find_all(sqlglot.exp.Table)} == {T}
    assert {c.name for c in tree.find_all(sqlglot.exp.Column)} <= COLS


def test_bigquery_s_gate_reads_a_table_and_a_column_in_any_case():
    assert check("SELECT sum(f.tran_am) FROM TRAN_FACT_1 f", allowlist(), "bigquery") == []
    assert check("SELECT sum(f.TRAN_AM) FROM tran_fact_1 f", allowlist(), "bigquery") == []
    assert check("SELECT sum(f.TRAN_AM) FROM tran_fact_2 f", allowlist(), "bigquery") == ["table tran_fact_2 is not in the wealth warehouse"]


def test_a_restricted_upper_case_column_is_refused_however_it_is_written():
    for sql in ("SELECT f.ACCT_NM FROM TRAN_FACT_1 f", "SELECT f.acct_nm FROM tran_fact_1 f"):
        assert check(sql, allowlist(), "duckdb") == ["column TRAN_FACT_1.ACCT_NM is restricted personal data"]


def test_a_signed_amount_is_a_figure_of_its_column():
    sql = "SELECT round(sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END), 2) FROM TRAN_FACT_1 f"
    assert as_figure(sql, allowlist()) == ([], [f"{T}.TRAN_AM"])
    other = "SELECT sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.DW_TRAN_ID END) FROM TRAN_FACT_1 f"
    assert as_figure(other, allowlist())[0]


def test_bigquery_is_sent_the_table_s_name_as_the_dataset_spells_it():
    from kge.adapters.warehouse.bigquery import route_sql
    spelled = {"wealth": {"tran_fact_1": "TRAN_FACT_1", "monthly_revenue": "monthly_revenue"}}.get
    for written in ("TRAN_FACT_1", "tran_fact_1"):
        sent = route_sql(f"SELECT sum(f.TRAN_AM) FROM {written} f WHERE f.CURR_IN <> 0", lambda unit: unit, "wealth", spelled)
        assert sent == "SELECT sum(f.TRAN_AM) FROM `wealth_TRAN_FACT_1` f WHERE f.CURR_IN <> 0"
    attached = route_sql("SELECT wealth.tran_fact_1.TRAN_AM FROM wealth.tran_fact_1", lambda unit: unit, spelled=spelled)
    assert attached == "SELECT wealth_TRAN_FACT_1.TRAN_AM FROM `wealth_TRAN_FACT_1` AS wealth_TRAN_FACT_1"
    assert route_sql("SELECT x.A FROM TRAN_FACT_1 x", lambda unit: unit, "wealth") == "SELECT x.A FROM `wealth_TRAN_FACT_1` x"


def test_bigquery_runs_an_upper_case_table_and_columns(monkeypatch):
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from tests.bigquery_fake import FakeBigQuery
    fake = FakeBigQuery()
    fake.con.execute('CREATE TABLE "wealth_TRAN_FACT_1" (TRAN_AM DECIMAL(16,2), DW_TRAN_DIRCTN_CD VARCHAR, CURR_IN BIGINT)')
    fake.con.execute("""INSERT INTO "wealth_TRAN_FACT_1" VALUES (100.00, 'I', 1), (40.00, 'O', 1), (7.00, 'I', 0)""")
    w = BigQueryWarehouses(Path("warehouses"), ["retail", "wealth"], project="pilot-project", client="meridian")
    sent = []

    def post(url, headers, body):
        sent.append(body)
        return fake.run(None, body["query"], dry_run=body.get("dryRun", False))
    monkeypatch.setattr(w, "_post", post)
    monkeypatch.setattr(w, "token", lambda: ("stand-in", "pilot-project"))
    sql = "SELECT sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END) FROM TRAN_FACT_1 f WHERE f.CURR_IN <> 0"
    assert w.gate("wealth", sql, allowlist(), figure=True, dialect="bigquery")["passed"]
    assert w.run("wealth", sql, "bigquery") == 60
    assert "`wealth_TRAN_FACT_1`" in sent[-1]["query"]
    lower = "SELECT sum(f.tran_am) FROM tran_fact_1 f WHERE f.curr_in <> 0"
    assert w.gate("wealth", lower, allowlist(), dialect="bigquery")["passed"]
    assert w.run("wealth", lower, "bigquery") == 140
    assert "`wealth_TRAN_FACT_1`" in sent[-1]["query"]
    assert w.run("wealth", "SELECT sum(TRAN_FACT_1.TRAN_AM) FROM TRAN_FACT_1 WHERE TRAN_FACT_1.CURR_IN <> 0") == 140
    assert "wealth_TRAN_FACT_1" in sent[-1]["query"]


@pytest.mark.parametrize("kind, read", [("BOOLEAN", "t.CURR_IN"), ("BIGINT", "t.CURR_IN <> 0"), ("VARCHAR", "t.CURR_IN = 'Y'")])
def test_a_flag_is_read_as_its_type_holds_one(kind, read):
    from kge.semantics.query import holds
    assert holds("t.CURR_IN", kind) == read


def test_a_case_condition_is_a_measure_s_filter_and_its_branches_its_value():
    from kge.compiler.checks import expression
    lock = {"sources": {"wealth": {"tables": [{"name": T, "columns": [{"name": c, "type": "VARCHAR"} for c in sorted(COLS)]}]}}}
    problems, cols, kind = expression("sum(CASE WHEN TRAN_FACT_1.DW_TRAN_DIRCTN_CD = 'O' THEN -TRAN_FACT_1.TRAN_AM "
                                      "ELSE TRAN_FACT_1.TRAN_AM END)", f"wealth.{T}.TRAN_AM", lock)
    assert (problems, kind) == ([], "sum")
    assert cols == {f"wealth.{T}.DW_TRAN_DIRCTN_CD": "filter", f"wealth.{T}.TRAN_AM": "value"}


H = "HSHLD_DIM_1"
HCOLS = {"HSHLD_DK", "RISK_PRFL_CD", "HSHLD_NM"}
HROWS = [(1, "LOW", "A"), (1, "LOW", "B"), (2, "HIGH", "C"), (3, "LOW", "D")]
CLAUSES = {
    "order by": ("SELECT HSHLD_DK, RISK_PRFL_CD FROM HSHLD_DIM_1 ORDER BY HSHLD_DK, RISK_PRFL_CD LIMIT 3",
                 [(1, "LOW"), (1, "LOW"), (2, "HIGH")]),
    "group by, order by": ("SELECT RISK_PRFL_CD, count(*) AS n FROM HSHLD_DIM_1 GROUP BY RISK_PRFL_CD ORDER BY RISK_PRFL_CD",
                           [("HIGH", 1), ("LOW", 3)]),
    "having": ("SELECT HSHLD_DK, count(*) AS n FROM HSHLD_DIM_1 GROUP BY HSHLD_DK HAVING count(HSHLD_DK) > 1", [(1, 2)]),
    "qualify": ("SELECT HSHLD_DK, HSHLD_NM FROM HSHLD_DIM_1 QUALIFY ROW_NUMBER() OVER (PARTITION BY HSHLD_DK ORDER BY HSHLD_NM) = 1 "
                "ORDER BY HSHLD_DK", [(1, "A"), (2, "C"), (3, "D")]),
    "distinct": ("SELECT DISTINCT HSHLD_DK FROM HSHLD_DIM_1 ORDER BY HSHLD_DK", [(1,), (2,), (3,)]),
    "alias in order by and having": ("SELECT RISK_PRFL_CD, count(*) AS n FROM HSHLD_DIM_1 GROUP BY RISK_PRFL_CD HAVING n > 1 "
                                     "ORDER BY n DESC", [("LOW", 3)]),
    "alias named as a column": ("SELECT count(*) AS HSHLD_DK FROM HSHLD_DIM_1 ORDER BY HSHLD_DK", [(4,)]),
}


def households() -> Allowlist:
    return Allowlist("wealth", {H}, {H: set(HCOLS)}, schema={H: set(HCOLS)})


def _cases():
    for name, (sql, rows) in CLAUSES.items():
        yield pytest.param(sql, rows, id=f"{name}, as spelled")
        yield pytest.param(sql.replace("HSHLD", "hshld").replace("RISK_PRFL_CD", "risk_prfl_cd"), rows, id=f"{name}, lower case")


@pytest.mark.parametrize("sql, rows", list(_cases()))
@pytest.mark.parametrize("dialect", ["duckdb", "bigquery"])
def test_order_by_having_qualify_and_distinct_name_an_upper_case_column_in_any_case(sql, rows, dialect):
    """#182: every column a scope names, its ORDER BY's, DISTINCT's, HAVING's and QUALIFY's among them, is spelled as the
    warehouse spells it, and a name the select outputs as the select spells it, so the gate passes the query and it
    reads the rows it reads on the warehouse."""
    import duckdb
    assert check(sql, households(), dialect) == []
    tree = warehouse_names(sqlglot.parse_one(sql, read=dialect), households(), dialect)
    outputs = set(tree.named_selects)
    assert {c.name for c in tree.find_all(sqlglot.exp.Column)} <= HCOLS | outputs | {"n"}
    con = duckdb.connect(":memory:")
    con.execute(f'CREATE TABLE "{H}" ("HSHLD_DK" BIGINT, "RISK_PRFL_CD" VARCHAR, "HSHLD_NM" VARCHAR)')
    con.executemany(f'INSERT INTO "{H}" VALUES (?, ?, ?)', HROWS)
    assert con.execute(tree.sql("duckdb", identify=True)).fetchall() == rows


def test_an_alias_named_as_a_column_is_still_the_alias_where_an_order_by_reads_it():
    tree = warehouse_names(sqlglot.parse_one("SELECT count(*) AS hshld_dk FROM hshld_dim_1 ORDER BY hshld_dk", read="bigquery"),
                           households(), "bigquery")
    assert tree.args["order"].expressions[0].this.name == tree.selects[0].alias == "hshld_dk"


@pytest.mark.parametrize("sql, rows", [c for c in _cases() if "lower case" in c.id])
def test_bigquery_s_gate_passes_and_runs_a_model_s_lower_case_query_on_an_upper_case_table(monkeypatch, sql, rows):
    """#182 on the path a model's query takes (Explore, Ask): the gate's static checks and dry run, then the query, on
    the BigQuery stand-in holding the upper-case table."""
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from tests.bigquery_fake import FakeBigQuery
    fake = FakeBigQuery()
    fake.con.execute(f'CREATE TABLE "wealth_{H}" ("HSHLD_DK" BIGINT, "RISK_PRFL_CD" VARCHAR, "HSHLD_NM" VARCHAR)')
    fake.con.executemany(f'INSERT INTO "wealth_{H}" VALUES (?, ?, ?)', HROWS)
    w = BigQueryWarehouses(Path("warehouses"), ["wealth"], project="pilot-project", client="meridian")
    monkeypatch.setattr(w, "_post", lambda url, headers, body: fake.run(None, body["query"], dry_run=body.get("dryRun", False)))
    monkeypatch.setattr(w, "token", lambda: ("stand-in", "pilot-project"))
    gated = w.gate("wealth", sql, households(), dialect="bigquery")
    assert gated["passed"], gated["problems"]
    assert [tuple(r) for r in w.query("wealth", sql, "bigquery")[1]] == rows
