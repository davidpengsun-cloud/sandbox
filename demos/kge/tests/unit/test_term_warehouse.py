"""F13 at onboarding (#513): a pack's declared codes and BROADER edges against each unit's live warehouse version, read
through the estate's warehouse seam and the SQL gate with one bounded GROUP BY query per declared column pair and per
unpaired coded column. A DuckDB estate in tmp_path, and the BigQuery stand-in (tests/bigquery_fake.py), hold small
synthetic warehouses built from the same CSVs the up-front check reads, so both paths give the same findings."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters.warehouse import open_estate
from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.estate import from_files, term_data, term_warehouse
from tests import bigquery_fake
from tests.unit.test_term_data import ACCOUNTS, GROUPS, JOINS, LEVELS, glossary, rows

TABLES = [{"name": "ROLLUP", "columns": LEVELS, "primary_key": ["ID"]},
          {"name": "GROUPS", "columns": GROUPS, "primary_key": ["GRP_CD"]},
          {"name": "ACCOUNTS", "columns": ACCOUNTS, "primary_key": ["ACCT"]}]
AGREE = [["1", "G1", "L1", "Y"], ["2", "G2", "L2", "Y"], ["3", "G9", "", "Y"]]
DISAGREE = [["1", "G1", "L1", "Y"], ["2", "G2", "L1", "Y"], ["4", "G1", "", "N"]]


def pack(extra=()):
    data = {"glossary": glossary() + list(extra), "joins": JOINS}
    return SimpleNamespace(data=data, at=lambda location: f"pack.yaml:{location}")


def data(folder: Path, rollup, groups=(("G1", "L1"), ("G2", "L2"))) -> Path:
    rows(folder / "ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], *rollup)
    rows(folder / "GROUPS.csv", ["GRP_CD", "LINE_CD"], *map(list, groups))
    rows(folder / "ACCOUNTS.csv", ["ACCT", "GRP_CD"], ["1", "G1"], ["2", "G2"])
    return folder


def built(estate, folder: Path, version: int, rollup, groups=(("G1", "L1"), ("G2", "L2")), tables=TABLES):
    from_files.build_warehouse(estate, "ops", tables, data(folder / f"v{version}", rollup, groups), version)
    estate.publish("ops", version)


def from_csv(folder: Path, the_pack) -> list:
    the_plan = term_data.plan(the_pack.data)
    found = term_data.Rows()
    term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": TABLES}), found)
    return term_data.evaluate(the_plan, found, the_pack.at)


class Counted:
    """The estate, its sessions counting each statement sent that is not the gate's EXPLAIN."""

    def __init__(self, estate):
        self.estate, self.sent, self.opened = estate, [], []

    def open(self, unit, version=None, write=False):
        self.opened.append((unit, version))
        session = self.estate.open(unit, version, write)
        real = session.execute

        def execute(sql, parameters=None):
            if not sql.startswith("EXPLAIN "):
                self.sent.append(sql)
            return real(sql, parameters)
        session.execute = execute
        return session


@pytest.fixture
def estate(tmp_path):
    local = DuckDBEstate(tmp_path / "warehouses")
    built(local, tmp_path / "data", 1, AGREE)
    built(local, tmp_path / "data", 2, DISAGREE)
    return Counted(local)


def test_rows_that_agree_give_no_finding_at_the_live_version(estate):
    assert term_warehouse.check(pack(), estate, {"ops": 1}) == []
    assert estate.opened == [("ops", 1)]


def test_the_live_version_is_the_one_read_and_its_rows_are_all_read(estate, tmp_path):
    found = term_warehouse.check(pack(), estate, {"ops": 2})
    assert estate.opened == [("ops", 2)]
    assert found == from_csv(tmp_path / "data/v2", pack())
    assert {(f.rule, f.path) for f in found} == {("F13", "pack.yaml:glossary[3].codes[0].code"),
                                                 ("F13", "pack.yaml:glossary[4].broader[0]"),
                                                 ("F13", "pack.yaml:glossary[5].broader[0]")}
    assert ("retail is under inv, but rows with ops.ROLLUP.LVL5 G1 have ops.ROLLUP.LVL6 NULL (a NULL is no broader term) "
            "on the same row, not L1: a broader term agrees with the rows") in [f.message for f in found]


def test_one_bounded_group_by_per_column_pair_and_per_unpaired_column(estate):
    """Four column pairs and two unpaired columns: six queries, however many rows, each GROUP BY over rows holding a
    declared code."""
    term_warehouse.check(pack(), estate, {"ops": 1})
    the_plan = term_data.plan(pack().data)
    assert len(estate.sent) == len(the_plan.pairs) + len(the_plan.alone) == 6
    assert all(" GROUP BY " in sql and " IN ('" in sql for sql in estate.sent)
    assert estate.sent[3] == ('SELECT CAST(c."GRP_CD" AS VARCHAR), CAST(p."LINE_CD" AS VARCHAR) FROM "ACCOUNTS" AS c '
                              'JOIN "GROUPS" AS p ON c."GRP_CD" = p."GRP_CD" WHERE CAST(c."GRP_CD" AS VARCHAR) IN (\'G1\') '
                              'GROUP BY CAST(c."GRP_CD" AS VARCHAR), CAST(p."LINE_CD" AS VARCHAR) UNION ALL '
                              'SELECT CAST(c."GRP_CD" AS VARCHAR), CAST(NULL AS VARCHAR) FROM "ACCOUNTS" AS c '
                              'WHERE CAST(c."GRP_CD" AS VARCHAR) IN (\'G1\') AND (c."GRP_CD" IS NULL) '
                              'GROUP BY CAST(c."GRP_CD" AS VARCHAR)')


def test_alternatives_on_the_warehouse_as_on_the_fixture(tmp_path):
    extra_codes = [{"column": "ops.ROLLUP.LVL5", "code": "G1B"}]
    the_pack = pack()
    next(g for g in the_pack.data["glossary"] if g["id"] == "retail")["codes"] += extra_codes
    next(g for g in the_pack.data["glossary"] if g["id"] == "inv")["codes"].append({"column": "ops.ROLLUP.LVL6", "code": "L1B"})
    local = DuckDBEstate(tmp_path / "warehouses")
    built(local, tmp_path / "data", 1, AGREE + [["5", "G1B", "L1", "Y"], ["6", "G1", "L1B", "Y"]])
    assert term_warehouse.check(the_pack, local, {"ops": 1}) == from_csv(tmp_path / "data/v1", the_pack) == []
    built(local, tmp_path / "data", 2, AGREE + [["5", "G1B", "L2", "Y"], ["6", "G1", "L1B", "Y"]])
    found = term_warehouse.check(the_pack, local, {"ops": 2})
    assert found == from_csv(tmp_path / "data/v2", the_pack) and found
    assert ("retail is under inv, but rows with ops.ROLLUP.LVL5 G1, G1B have ops.ROLLUP.LVL6 L2 on the same row, not L1, "
            "L1B: a broader term agrees with the rows") in [f.message for f in found]


def test_a_broader_edge_the_joined_rows_contradict(tmp_path):
    local = DuckDBEstate(tmp_path / "warehouses")
    built(local, tmp_path / "data", 1, AGREE, groups=(("G1", "L2"), ("G2", "L2")))
    found = term_warehouse.check(pack(), local, {"ops": 1})
    assert found == from_csv(tmp_path / "data/v1", pack())
    assert ("retail is under inv, but rows with ops.ACCOUNTS.GRP_CD G1 have ops.GROUPS.LINE_CD L2 through account_group, "
            "not L1: a broader term agrees with the rows") in [f.message for f in found]


def test_typed_columns_compare_as_on_the_fixture(tmp_path):
    """A DECIMAL, an INTEGER and a BOOLEAN column: the warehouse's own text for 1.50, 1 and true is put in the form the
    fixture's is, so a pack check-pack passes is not refused when onboarding compiles, and the other way round."""
    rates = {"name": "RATES", "primary_key": ["RANK"], "columns": [
        {"name": "RANK", "type": "INTEGER", "description": "Rank"},
        {"name": "RATE", "type": "DECIMAL(10,2)", "description": "Rate"},
        {"name": "SHARE", "type": "DOUBLE", "description": "Share"},
        {"name": "LIVE", "type": "BOOLEAN", "description": "Live"}]}
    extra = [{"id": "half", "codes": [{"column": "ops.RATES.RATE", "code": "1.5"}]},
             {"id": "one", "codes": [{"column": "ops.RATES.RANK", "code": "01"}]},
             {"id": "two", "codes": [{"column": "ops.RATES.SHARE", "code": "2"}]},
             {"id": "live", "codes": [{"column": "ops.RATES.LIVE", "code": "True"}, {"column": "ops.RATES.RANK", "code": "x"}]}]
    the_pack = pack(extra)
    local = DuckDBEstate(tmp_path / "warehouses")
    folder = data(tmp_path / "data/v1", AGREE)
    rows(folder / "RATES.csv", ["RANK", "RATE", "SHARE", "LIVE"], ["1", "1.50", "2.0", "true"])
    from_files.build_warehouse(local, "ops", TABLES + [rates], folder, 1)
    local.publish("ops", 1)
    the_plan = term_data.plan(the_pack.data)
    found = term_data.Rows()
    term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": TABLES + [rates]}), found)
    on_fixture = term_data.evaluate(the_plan, found, the_pack.at)
    on_warehouse = term_warehouse.check(the_pack, local, {"ops": 1})
    assert on_warehouse == on_fixture == [
        type(on_fixture[0])("F13", "pack.yaml:glossary[9].codes[1].code", "ops.RATES.RANK holds no row with x: live's code "
                                                                          "is a value of its column")]


def test_a_parent_child_table_on_the_warehouse(tmp_path):
    segs = {"name": "SEGS", "primary_key": ["CD"], "columns": [
        {"name": "CD", "type": "VARCHAR", "description": "Segment"}, {"name": "PAR", "type": "VARCHAR", "description": "Parent"}]}
    the_pack = pack([{"id": "core", "codes": [{"column": "ops.SEGS.CD", "code": "C"}]},
                     {"id": "core-a", "broader": ["core"], "codes": [{"column": "ops.SEGS.CD", "code": "CA"}]}])
    the_pack.data["joins"] = JOINS + [{"id": "seg_parent", "kind": "hierarchy", "from": "ops.SEGS", "to": "ops.SEGS",
                                       "alias": "parent_seg", "condition": "SEGS.PAR = parent_seg.CD"}]
    local = DuckDBEstate(tmp_path / "warehouses")
    folder = data(tmp_path / "data/v1", AGREE)
    rows(folder / "SEGS.csv", ["CD", "PAR"], ["C", ""], ["X", ""], ["CA", "X"])
    from_files.build_warehouse(local, "ops", TABLES + [segs], folder, 1)
    local.publish("ops", 1)
    found = term_warehouse.check(the_pack, local, {"ops": 1})
    assert [(f.path, f.message) for f in found] == [
        ("pack.yaml:glossary[7].broader[0]", "core-a is under core, but rows with ops.SEGS.CD CA have ops.SEGS.CD X through "
                                             "seg_parent, not C: a broader term agrees with the rows")]


def test_codes_quoted_and_compared_as_text(tmp_path):
    """A code with a quote is a literal, never SQL, and an integer column's values are compared as their text."""
    local = DuckDBEstate(tmp_path / "warehouses")
    built(local, tmp_path / "data", 1, AGREE)
    extra = [{"id": "odd", "name": "Odd (group)", "codes": [{"column": "ops.ROLLUP.LVL5", "code": "G'1"}]},
             {"id": "first", "name": "First (account)", "codes": [{"column": "ops.ACCOUNTS.ACCT", "code": "1"}]}]
    found = term_warehouse.check(pack(extra), local, {"ops": 1})
    assert [f.message for f in found] == ["ops.ROLLUP.LVL5 holds no row with G'1: odd's code is a value of its column"]


def test_a_query_the_gate_refuses_is_a_finding(estate, monkeypatch):
    monkeypatch.setattr(term_warehouse.sqlguard, "gate", lambda *a, **k: {"passed": False, "problems": ["not allowed"]})
    found = term_warehouse.check(pack(), estate, {"ops": 1})
    assert estate.sent == []
    assert [(f.rule, f.path) for f in found] == [("F13", "ops:rows")] * 6
    assert found[0].message == ("ops's declared codes and broader terms are not checked: the SQL gate refused its query: "
                                "not allowed")


def test_a_warehouse_that_cannot_be_opened_is_a_finding(tmp_path):
    found = term_warehouse.check(pack(), DuckDBEstate(tmp_path / "none"), {"ops": 3})
    assert [(f.rule, f.path) for f in found] == [("F13", "ops:rows")]
    assert found[0].message.startswith("ops's declared codes and broader terms are not checked: its warehouse version 3 "
                                       "cannot be opened")


def test_a_pack_with_no_codes_opens_no_warehouse(estate):
    assert term_warehouse.check(SimpleNamespace(data={"glossary": [{"id": "x"}]}, at=str), estate, {}) == []
    assert estate.opened == []


def test_bigquery_answers_as_duckdb_with_one_job_a_query_and_its_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    fake = bigquery_fake.install(monkeypatch)
    cloud = open_estate(tmp_path / "cloud", "shop")
    built(cloud, tmp_path / "data", 2, DISAGREE)
    before = len(fake.statements)
    found = term_warehouse.check(pack(), cloud, {"ops": 2})
    assert found == from_csv(tmp_path / "data/v2", pack())
    sent = fake.statements[before:]
    grouped = [sql for sql in sent if " GROUP BY " in sql]
    assert len(grouped) == 2 * 6 and all(" FROM ops_v2_" in sql for sql in grouped)
    rest = [sql for sql in sent if " GROUP BY " not in sql]
    assert "_kge_catalogue" in rest[0] and len(rest) == 1 + 3, "the catalogue read, and one description a coded table"


def test_a_null_join_key_disagrees_on_the_warehouse_as_on_the_fixture(tmp_path):
    """A ledger row with Retail's code and a NULL group key joins no group: no broader row, so no broader code. The NULL
    rows come back in the pair's own query, so the count stays one query a pair."""
    ledger = {"name": "LEDGER", "primary_key": ["ID"], "columns": [
        {"name": "ID", "type": "INTEGER", "description": "Row"}, {"name": "GRP", "type": "VARCHAR", "description": "Group"},
        {"name": "KEY", "type": "VARCHAR", "description": "Group key"}]}
    the_pack = pack()
    next(g for g in the_pack.data["glossary"] if g["id"] == "retail")["codes"].append({"column": "ops.LEDGER.GRP", "code": "G1"})
    the_pack.data["joins"] = JOINS + [
        {"id": "ledger_group", "kind": "key", "from": "ops.LEDGER", "to": "ops.GROUPS", "condition": "LEDGER.KEY = GROUPS.GRP_CD"},
        {"id": "ledger_rollup", "kind": "key", "from": "ops.LEDGER", "to": "ops.ROLLUP", "condition": "LEDGER.KEY = ROLLUP.LVL5"}]
    local = Counted(DuckDBEstate(tmp_path / "warehouses"))
    folder = data(tmp_path / "data/v1", AGREE)
    rows(folder / "LEDGER.csv", ["ID", "GRP", "KEY"], ["1", "G1", "G1"], ["2", "G1", ""])
    from_files.build_warehouse(local.estate, "ops", TABLES + [ledger], folder, 1)
    local.estate.publish("ops", 1)
    found = term_warehouse.check(the_pack, local, {"ops": 1})
    the_plan = term_data.plan(the_pack.data)
    on_fixture = term_data.Rows()
    term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": TABLES + [ledger]}), on_fixture)
    assert found == term_data.evaluate(the_plan, on_fixture, the_pack.at)
    assert ("retail is under inv, but rows with ops.LEDGER.GRP G1 have ops.GROUPS.LINE_CD NULL (a NULL is no broader term) "
            "through ledger_group, not L1: a broader term agrees with the rows") in [f.message for f in found]
    assert len(local.sent) == len(the_plan.pairs) + len(the_plan.alone)


def test_dates_and_type_aliases_compare_as_on_the_fixture(tmp_path):
    kinds = {"name": "KINDS", "primary_key": ["BIG"], "columns": [
        {"name": "BIG", "type": "INT8", "description": "Big"}, {"name": "SHARE", "type": "FLOAT8", "description": "Share"},
        {"name": "FLAG", "type": "BOOL", "description": "Flag"}, {"name": "DAY", "type": "DATE", "description": "Day"}]}
    the_pack = pack([{"id": "one", "codes": [{"column": "ops.KINDS.BIG", "code": "01"}]},
                     {"id": "half", "codes": [{"column": "ops.KINDS.SHARE", "code": "1.5"}]},
                     {"id": "on", "codes": [{"column": "ops.KINDS.FLAG", "code": "yes"}]},
                     {"id": "day", "codes": [{"column": "ops.KINDS.DAY", "code": "2026-1-5"},
                                             {"column": "ops.KINDS.DAY", "code": "2026-02-30x"}]}])
    local = DuckDBEstate(tmp_path / "warehouses")
    folder = data(tmp_path / "data/v1", AGREE)
    rows(folder / "KINDS.csv", ["BIG", "SHARE", "FLAG", "DAY"], ["1", "1.50", "true", "2026-01-05"])
    from_files.build_warehouse(local, "ops", TABLES + [kinds], folder, 1)
    local.publish("ops", 1)
    the_plan = term_data.plan(the_pack.data)
    on_fixture = term_data.Rows()
    term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": TABLES + [kinds]}), on_fixture)
    found = term_warehouse.check(the_pack, local, {"ops": 1})
    assert found == term_data.evaluate(the_plan, on_fixture, the_pack.at)
    assert [f.message for f in found] == ["ops.KINDS.DAY holds no row with 2026-02-30x: day's code is a value of its column"]


def test_a_coded_table_the_warehouse_does_not_hold_is_a_finding(tmp_path):
    local = DuckDBEstate(tmp_path / "warehouses")
    built(local, tmp_path / "data", 1, AGREE)
    found = term_warehouse.check(pack([{"id": "gone", "codes": [{"column": "ops.GONE.X", "code": "1"}]}]), local, {"ops": 1})
    assert [(f.path, f.message) for f in found] == [
        ("ops:rows", "ops's declared codes and broader terms are not checked: its tables cannot be described: it holds no "
                     "table GONE")]


def test_an_absurd_number_code_matches_no_row_and_sends_no_literal(tmp_path):
    local = Counted(DuckDBEstate(tmp_path / "warehouses"))
    built(local.estate, tmp_path / "data", 1, AGREE)
    found = term_warehouse.check(pack([{"id": "huge", "codes": [{"column": "ops.ACCOUNTS.ACCT", "code": "1e9999999"}]}]),
                                 local, {"ops": 1})
    assert [f.message for f in found] == ["ops.ACCOUNTS.ACCT holds no row with 1e9999999: huge's code is a value of its column"]
    assert not any("9999999" in sql for sql in local.sent)


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
def test_dates_and_timestamps_in_every_form_the_estate_loads(tmp_path, monkeypatch, backend):
    """A DATE cell written with a time, a TIMESTAMP cell written as a date or with a T, and codes written either way:
    each compares as its value, on the fixture and in the warehouse, BigQuery's zone-marked timestamp text included."""
    times = {"name": "TIMES", "primary_key": ["ID"], "columns": [
        {"name": "ID", "type": "INTEGER", "description": "Row"}, {"name": "DAY", "type": "DATE", "description": "Day"},
        {"name": "STAMP", "type": "TIMESTAMP", "description": "At"}]}
    the_pack = pack([{"id": "day", "codes": [{"column": "ops.TIMES.DAY", "code": "2026-01-05"},
                                             {"column": "ops.TIMES.DAY", "code": "2026-1-6 00:00:00"}]},
                     {"id": "at", "codes": [{"column": "ops.TIMES.STAMP", "code": "2026-01-05 00:00:00"},
                                            {"column": "ops.TIMES.STAMP", "code": "2026-01-06T10:00:00.500"},
                                            {"column": "ops.TIMES.STAMP", "code": "2026-01-07 09:00:00"},
                                            {"column": "ops.TIMES.STAMP", "code": "soon"}]}])
    if backend == "bigquery":
        monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
        bigquery_fake.install(monkeypatch)
        estate = open_estate(tmp_path / "cloud", "shop")
    else:
        estate = DuckDBEstate(tmp_path / "warehouses")
    folder = data(tmp_path / "data/v1", AGREE)
    rows(folder / "TIMES.csv", ["ID", "DAY", "STAMP"], ["1", "2026-01-05T00:00:00", "2026-01-05"],
         ["2", "2026-01-06", "2026-01-06T10:00:00.5"])
    from_files.build_warehouse(estate, "ops", TABLES + [times], folder, 1)
    estate.publish("ops", 1)
    the_plan = term_data.plan(the_pack.data)
    on_fixture = term_data.Rows()
    term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": TABLES + [times]}), on_fixture)
    found = term_warehouse.check(the_pack, estate, {"ops": 1})
    assert found == term_data.evaluate(the_plan, on_fixture, the_pack.at)
    assert [f.message for f in found] == ["ops.TIMES.STAMP holds no row with 2026-01-07 09:00:00: at's code is a value of its column",
                                          "ops.TIMES.STAMP holds no row with soon: at's code is a value of its column"]


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
def test_alternatives_on_two_columns_and_a_fact_column_through_the_parent_row(tmp_path, monkeypatch, backend):
    """The two coordinator decisions on #513 in the warehouse, as on the fixture: Investing's codes on two columns of one
    table are alternatives, read in one query and reported once a row; and Core A's fact column, joined to the segment
    dimension's key it is also coded on, is compared with its parent row through the dimension's join to itself, in one
    query with its NULL arms."""
    from tests.unit.test_term_data import FACTS, SEGS, segment_pack
    rollup = {"name": "ROLLUP", "primary_key": ["ID"], "columns": LEVELS + [
        {"name": "ALT6", "type": "VARCHAR", "description": "Level 6, alternate"}]}
    tables = [rollup] + TABLES[1:] + [SEGS, FACTS]
    the_pack = pack()
    next(g for g in the_pack.data["glossary"] if g["id"] == "inv")["codes"].insert(1, {"column": "ops.ROLLUP.ALT6", "code": "L1X"})
    the_pack.data["joins"] = list(JOINS)
    segment_pack(the_pack.data)
    if backend == "bigquery":
        monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
        bigquery_fake.install(monkeypatch)
        estate = open_estate(tmp_path / "cloud", "shop")
    else:
        estate = DuckDBEstate(tmp_path / "warehouses")
    the_plan = term_data.plan(the_pack.data)
    for version, segs in ((1, [["C", ""], ["CA", "C"]]), (2, [["C", ""], ["X", ""], ["CA", "X"], ["CB", ""]])):
        folder = tmp_path / f"data/v{version}"
        rows(folder / "ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR", "ALT6"], ["1", "G1", "L1", "Y", ""],
             ["8", "G1", "", "Y", "L1X"], ["2", "G2", "L2", "Y", ""], *([["9", "G1", "", "Y", ""]] if version == 2 else []))
        rows(folder / "GROUPS.csv", ["GRP_CD", "LINE_CD"], ["G1", "L1"], ["G2", "L2"])
        rows(folder / "ACCOUNTS.csv", ["ACCT", "GRP_CD"], ["1", "G1"], ["2", "G2"])
        rows(folder / "SEGS.csv", ["CD", "PAR"], *segs)
        rows(folder / "FACTS.csv", ["ID", "SEG"], ["1", "CA"], ["2", "CA"], ["3", ""])
        from_files.build_warehouse(estate, "ops", tables, folder, version)
        estate.publish("ops", version)
        counted = Counted(estate)
        found = term_warehouse.check(the_pack, counted, {"ops": version})
        on_fixture = term_data.Rows()
        term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": tables}), on_fixture)
        assert found == term_data.evaluate(the_plan, on_fixture, the_pack.at)
        assert len([sql for sql in counted.sent if " GROUP BY " in sql]) == len(the_plan.pairs) + len(the_plan.alone)
        if version == 1:
            assert found == []
            continue
        messages = [f.message for f in found]
        assert sum("ops.ROLLUP.LVL5 G1 have" in m and "on the same row" in m for m in messages) == 1
        assert ("retail is under inv, but rows with ops.ROLLUP.LVL5 G1 have ops.ROLLUP.LVL6 / ops.ROLLUP.ALT6 NULL / NULL "
                "(a NULL is no broader term) on the same row, not ops.ROLLUP.LVL6 L1 or ops.ROLLUP.ALT6 L1X: a broader "
                "term agrees with the rows") in messages
        assert ("core-a is under core, but rows with ops.FACTS.SEG CA have ops.SEGS.CD X through fact_segment and "
                "seg_parent, not C: a broader term agrees with the rows") in messages


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
def test_a_second_coded_column_of_the_dimension_on_the_warehouse_as_on_the_fixture(tmp_path, monkeypatch, backend):
    """Core coded on the dimension's key and name, Core A on its key, name and a fact column: every pair reaches Core's
    columns through the parent row, so correct rows give no finding, and a wrong parent gives one finding a row and
    narrower column, the same on the warehouse as on the fixture."""
    from tests.unit.test_term_data import FACTS, SEGS, segment_pack
    segs = {**SEGS, "columns": SEGS["columns"] + [{"name": "NAME", "type": "VARCHAR", "description": "Name"}]}
    tables = TABLES + [segs, FACTS]
    the_pack = pack()
    the_pack.data["joins"] = list(JOINS)
    segment_pack(the_pack.data)
    glossary_terms = {g["id"]: g for g in the_pack.data["glossary"]}
    glossary_terms["core"]["codes"].append({"column": "ops.SEGS.NAME", "code": "Core"})
    glossary_terms["core-a"]["codes"].append({"column": "ops.SEGS.NAME", "code": "Core A"})
    if backend == "bigquery":
        monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
        bigquery_fake.install(monkeypatch)
        estate = open_estate(tmp_path / "cloud", "shop")
    else:
        estate = DuckDBEstate(tmp_path / "warehouses")
    the_plan = term_data.plan(the_pack.data)
    for version, body in ((1, [["C", "", "Core"], ["CA", "C", "Core A"]]),
                          (2, [["C", "", "Core"], ["X", "", "Other"], ["CA", "X", "Core A"]])):
        folder = data(tmp_path / f"data/v{version}", AGREE)
        rows(folder / "SEGS.csv", ["CD", "PAR", "NAME"], *body)
        rows(folder / "FACTS.csv", ["ID", "SEG"], ["1", "CA"])
        from_files.build_warehouse(estate, "ops", tables, folder, version)
        estate.publish("ops", version)
        found = term_warehouse.check(the_pack, estate, {"ops": version})
        on_fixture = term_data.Rows()
        term_data.from_csv(the_plan, "ops", folder, term_data.types_of({"tables": tables}), on_fixture)
        assert found == term_data.evaluate(the_plan, on_fixture, the_pack.at)
        if version == 1:
            assert found == []
            continue
        assert sorted(f.message.split(" have ")[0] for f in found) == [
            "core-a is under core, but rows with ops.FACTS.SEG CA", "core-a is under core, but rows with ops.SEGS.CD CA",
            "core-a is under core, but rows with ops.SEGS.NAME Core A"]
