"""F13 on upload (#513): a pack's declared codes and BROADER edges against the fixture's rows, through the up-front check
(kge/estate/upfront.py) that check-pack and onboarding's step 3 run, on small synthetic packs and fixtures written to
tmp_path. Planted-invalid cases: a code not in its column, a broader edge the rows contradict, a NULL broader code,
alternatives, on one column and across the columns of one table, a historical version, a join across two tables, a
member coded on a dimension's key and on a fact column joined to it, and a unit with no fixture rows, which is not
checked here and is no finding."""
import json

import pytest
import yaml

from kge.compiler import terms
from kge.estate import check_pack, term_data, upfront
from kge.pipeline.profile import Profile

LEVELS = [{"name": "ID", "type": "VARCHAR", "description": "Row"},
          {"name": "LVL5", "type": "VARCHAR", "description": "Level 5"},
          {"name": "LVL6", "type": "VARCHAR", "description": "Level 6"},
          {"name": "CURR", "type": "VARCHAR", "description": "Current version"}]
GROUPS = [{"name": "GRP_CD", "type": "VARCHAR", "description": "Group"},
          {"name": "LINE_CD", "type": "VARCHAR", "description": "Line"}]
ACCOUNTS = [{"name": "ACCT", "type": "INTEGER", "description": "Account"},
            {"name": "GRP_CD", "type": "VARCHAR", "description": "Group"}]


JOINS = [{"id": "account_group", "kind": "key", "cardinality": "one-to-one", "from": "ops.ACCOUNTS", "to": "ops.GROUPS",
          "condition": "ACCOUNTS.GRP_CD = GROUPS.GRP_CD"},
         {"id": "rollup_group", "kind": "key", "cardinality": "one-to-one", "from": "ops.ROLLUP", "to": "ops.GROUPS",
          "condition": "ROLLUP.LVL5 = GROUPS.GRP_CD"},
         {"id": "account_rollup", "kind": "hierarchy", "cardinality": "one-to-many", "from": "ops.ACCOUNTS", "to": "ops.ROLLUP",
          "condition": "ACCOUNTS.GRP_CD = ROLLUP.LVL5"}]


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def rows(path, header, *body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in [header, *body]) + "\n")


def glossary():
    """A class, two business lines and two groups under them, coded on the rollup's two levels and the group table."""
    return [
        {"id": "line", "name": "business line"},
        {"id": "group", "name": "group"},
        {"id": "inv", "name": "Investing (line)", "is_a": ["line"], "codes": [{"column": "ops.ROLLUP.LVL6", "code": "L1"},
                                                                             {"column": "ops.GROUPS.LINE_CD", "code": "L1"}]},
        {"id": "adv", "name": "Advice (line)", "is_a": ["line"], "codes": [{"column": "ops.ROLLUP.LVL6", "code": "L2"},
                                                                          {"column": "ops.GROUPS.LINE_CD", "code": "L2"}]},
        {"id": "retail", "name": "Retail (group)", "is_a": ["group"], "broader": ["inv"],
         "codes": [{"column": "ops.ROLLUP.LVL5", "code": "G1"}, {"column": "ops.ACCOUNTS.GRP_CD", "code": "G1"}]},
        {"id": "plan", "name": "Planning (group)", "is_a": ["group"], "broader": ["adv"],
         "codes": [{"column": "ops.ROLLUP.LVL5", "code": "G2"}]},
    ]


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """A client whose pack declares member terms and whose fixture's rows agree with them."""
    prof = Profile("shop", "shop", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "estate/warehouses", tmp_path / "identities.yaml", None, tmp_path / "profiles.yaml")
    folder = prof.clients / prof.client
    put(folder / "pack.yaml", {
        "manifest": {"client": "shop", "meta_model_version": "1.6.0",
                     "inputs": {"estate": {"fixture": "fixture"}, "sets": ["golden"]}},
        "sources": [{"id": "ops", "harvest": True}],
        "access": {"roles": [{"id": "reader", "processes": ["querying"]}]},
        "glossary": glossary(),
        "joins": JOINS,
        "tests": {"sets": [{"name": "golden", "version": "v2"}]}})
    put(folder / "fixture/schema.yaml", {"warehouses": {"ops": {"label": "Operations", "tables": [
        {"name": "ROLLUP", "columns": LEVELS, "primary_key": ["ID"]},
        {"name": "GROUPS", "columns": GROUPS, "primary_key": ["GRP_CD"]},
        {"name": "ACCOUNTS", "columns": ACCOUNTS, "primary_key": ["ACCT"]}]}}})
    data = folder / "fixture/data/ops"
    rows(data / "ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "G1", "L1", "Y"], ["2", "G2", "L2", "Y"],
         ["3", "G9", "", "Y"])
    rows(data / "GROUPS.csv", ["GRP_CD", "LINE_CD"], ["G1", "L1"], ["G2", "L2"])
    rows(data / "ACCOUNTS.csv", ["ACCT", "GRP_CD"], ["1", "G1"], ["2", "G2"])
    (folder / "fixture/golden.json").write_text(json.dumps(
        {"version": "v2", "items": [{"id": "one", "question": "One?", "kind": "within", "expect": {}}]}))
    monkeypatch.setattr(upfront.graphstore, "store_info", lambda: {"engine": "here"})
    return prof, folder


def edit(folder, change):
    path = folder / "pack.yaml"
    value = yaml.safe_load(path.read_text())
    change(value)
    put(path, value)


def term(value, term_id):
    return next(g for g in value["glossary"] if g["id"] == term_id)


def f13(result):
    return [(f.path, f.message) for f in result.findings if f.rule == "F13"]


def at(found, path):
    """The messages of the findings at one place."""
    return [message for where, message in found if where == path]


def test_rows_that_agree_give_no_finding_and_nothing_not_checked(shop):
    prof, _ = shop
    result = upfront.check(prof)
    assert result.findings == [] and result.not_checked == []


def test_a_code_not_in_its_column_is_refused_at_the_code(shop):
    prof, folder = shop
    edit(folder, lambda p: term(p, "plan")["codes"].append({"column": "ops.ROLLUP.LVL5", "code": "G7"}))
    assert f13(upfront.check(prof)) == [
        ("pack.yaml:glossary[5].codes[1].code", "ops.ROLLUP.LVL5 holds no row with G7: plan's code is a value of its column")]


def test_a_broader_edge_the_same_row_contradicts_is_refused_at_the_edge(shop):
    prof, folder = shop
    rows(folder / "fixture/data/ops/ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "G1", "L1", "Y"],
         ["2", "G2", "L1", "Y"])
    found = f13(upfront.check(prof))
    assert ("pack.yaml:glossary[3].codes[0].code", "ops.ROLLUP.LVL6 holds no row with L2: adv's code is a value of its "
                                                   "column") in found
    assert at(found, "pack.yaml:glossary[5].broader[0]") == [
        "plan is under adv, but rows with ops.ROLLUP.LVL5 G2 have ops.ROLLUP.LVL6 L1 on the same row, not L2: a broader "
        "term agrees with the rows"]


def test_a_null_broader_code_disagrees_with_a_declared_edge(shop):
    prof, folder = shop
    rows(folder / "fixture/data/ops/ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "G1", "L1", "Y"],
         ["2", "G2", "L2", "Y"], ["4", "G1", "", "Y"])
    found = f13(upfront.check(prof))
    assert {path for path, _ in found} == {"pack.yaml:glossary[4].broader[0]"}
    assert ("retail is under inv, but rows with ops.ROLLUP.LVL5 G1 have ops.ROLLUP.LVL6 NULL (a NULL is no broader term) "
            "on the same row, not L1: a broader term agrees with the rows") in at(found, "pack.yaml:glossary[4].broader[0]")


def test_a_null_beside_a_code_no_term_declares_is_no_finding(shop):
    """G9 is no term's code, so its NULL level 6 says nothing about a declared edge."""
    prof, folder = shop
    assert "G9" in (folder / "fixture/data/ops/ROLLUP.csv").read_text()
    assert f13(upfront.check(prof)) == []


def test_codes_on_one_column_are_alternatives(shop):
    prof, folder = shop

    def alternatives(p):
        term(p, "retail")["codes"].insert(1, {"column": "ops.ROLLUP.LVL5", "code": "G1B"})
        term(p, "inv")["codes"].insert(1, {"column": "ops.ROLLUP.LVL6", "code": "L1B"})
    edit(folder, alternatives)
    rows(folder / "fixture/data/ops/ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "G1", "L1", "Y"],
         ["2", "G2", "L2", "Y"], ["5", "G1B", "L1", "Y"], ["6", "G1", "L1B", "Y"])
    assert f13(upfront.check(prof)) == []
    rows(folder / "fixture/data/ops/ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "G1", "L1", "Y"],
         ["2", "G2", "L2", "Y"], ["5", "G1B", "L2", "Y"], ["6", "G1", "L1B", "Y"])
    found = f13(upfront.check(prof))
    assert {path for path, _ in found} == {"pack.yaml:glossary[4].broader[0]"}
    assert ("retail is under inv, but rows with ops.ROLLUP.LVL5 G1, G1B have ops.ROLLUP.LVL6 L2 on the same row, not L1, "
            "L1B: a broader term agrees with the rows") in at(found, "pack.yaml:glossary[4].broader[0]")


def test_every_row_is_read_historical_versions_too(shop):
    """A current-version flag is not read: an earlier version of a row that puts the group under another line
    contradicts the edge too (F8)."""
    prof, folder = shop
    rows(folder / "fixture/data/ops/ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "G1", "L1", "Y"],
         ["2", "G2", "L2", "Y"], ["7", "G1", "L2", "N"])
    found = f13(upfront.check(prof))
    assert {path for path, _ in found} == {"pack.yaml:glossary[4].broader[0]"}
    assert any("have ops.ROLLUP.LVL6 L2 on the same row" in m for m in at(found, "pack.yaml:glossary[4].broader[0]"))


def test_across_two_tables_rows_are_compared_through_the_declared_join(shop):
    prof, folder = shop
    rows(folder / "fixture/data/ops/GROUPS.csv", ["GRP_CD", "LINE_CD"], ["G1", "L2"], ["G2", "L2"])
    found = f13(upfront.check(prof))
    assert ("pack.yaml:glossary[4].broader[0]",
            "retail is under inv, but rows with ops.ACCOUNTS.GRP_CD G1 have ops.GROUPS.LINE_CD L2 through account_group, "
            "not L1: a broader term agrees with the rows") in found


def test_a_joined_row_with_a_null_broader_code_disagrees(shop):
    prof, folder = shop
    rows(folder / "fixture/data/ops/GROUPS.csv", ["GRP_CD", "LINE_CD"], ["G1", ""], ["G2", "L2"])
    found = f13(upfront.check(prof))
    assert any("have ops.GROUPS.LINE_CD NULL (a NULL is no broader term) through account_group" in m
               for m in at(found, "pack.yaml:glossary[4].broader[0]"))


def test_a_row_that_joins_no_broader_row_is_not_compared(shop):
    """The SQL gate joins a declared join inner, and so does F13: an account whose group has no row is not compared."""
    prof, folder = shop
    rows(folder / "fixture/data/ops/GROUPS.csv", ["GRP_CD", "LINE_CD"], ["G2", "L2"])
    found = f13(upfront.check(prof))
    assert found == [("pack.yaml:glossary[2].codes[1].code",
                      "ops.GROUPS.LINE_CD holds no row with L1: inv's code is a value of its column")]


def test_a_parent_child_table_is_compared_through_its_join_to_itself(shop):
    """Two terms coded on one column (a segment and its parent segment, in one table) are compared through the table's
    hierarchy join to itself under an alias, never on the same row."""
    prof, folder = shop

    def segments(p):
        p["glossary"] += [{"id": "seg", "name": "segment"},
                          {"id": "core", "name": "Core (segment)", "is_a": ["seg"],
                           "codes": [{"column": "ops.SEGS.CD", "code": "C"}]},
                          {"id": "core-a", "name": "Core A (segment)", "is_a": ["seg"], "broader": ["core"],
                           "codes": [{"column": "ops.SEGS.CD", "code": "CA"}]}]
        p["joins"].append({"id": "seg_parent", "kind": "hierarchy", "cardinality": "one-to-one", "from": "ops.SEGS",
                           "to": "ops.SEGS", "alias": "parent_seg", "condition": "SEGS.PAR = parent_seg.CD"})
    edit(folder, segments)
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    schema["warehouses"]["ops"]["tables"].append({"name": "SEGS", "primary_key": ["CD"], "columns": [
        {"name": "CD", "type": "VARCHAR", "description": "Segment"}, {"name": "PAR", "type": "VARCHAR", "description": "Parent"}]})
    put(folder / "fixture/schema.yaml", schema)
    rows(folder / "fixture/data/ops/SEGS.csv", ["CD", "PAR"], ["C", ""], ["CA", "C"])
    assert f13(upfront.check(prof)) == []
    rows(folder / "fixture/data/ops/SEGS.csv", ["CD", "PAR"], ["C", ""], ["X", ""], ["CA", "X"])
    assert f13(upfront.check(prof)) == [
        ("pack.yaml:glossary[8].broader[0]", "core-a is under core, but rows with ops.SEGS.CD CA have ops.SEGS.CD X through "
                                             "seg_parent, not C: a broader term agrees with the rows")]
    edit(folder, lambda p: p["joins"].pop())
    assert f13(upfront.check(prof)) == [
        ("pack.yaml:glossary[8].broader[0]", "core-a is under core, coded on ops.SEGS.CD and ops.SEGS.CD, and no hierarchy "
                                             "or key join declared between those tables (from a table to itself, under an "
                                             "alias) equates their columns: the rows cannot be compared")]


def test_values_compare_as_their_declared_type(shop):
    """A number by its value and a boolean as true or false, as the warehouse holds them: 1.5 is 1.50 and 1 is 01."""
    prof, folder = shop

    def typed(p):
        p["glossary"] += [{"id": "half", "name": "Half (rate)", "codes": [{"column": "ops.RATES.RATE", "code": "1.5"}]},
                          {"id": "one", "name": "One (rank)", "codes": [{"column": "ops.RATES.RANK", "code": "1"}]},
                          {"id": "live", "name": "Live (rate)", "codes": [{"column": "ops.RATES.LIVE", "code": "True"}]}]
    edit(folder, typed)
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    schema["warehouses"]["ops"]["tables"].append({"name": "RATES", "primary_key": ["RANK"], "columns": [
        {"name": "RANK", "type": "INTEGER", "description": "Rank"},
        {"name": "RATE", "type": "DECIMAL(10,2)", "description": "Rate"},
        {"name": "LIVE", "type": "BOOLEAN", "description": "Live"}]})
    put(folder / "fixture/schema.yaml", schema)
    rows(folder / "fixture/data/ops/RATES.csv", ["RANK", "RATE", "LIVE"], ["01", "1.50", "true"])
    assert f13(upfront.check(prof)) == []
    rows(folder / "fixture/data/ops/RATES.csv", ["RANK", "RATE", "LIVE"], ["2", "1.25", "false"])
    assert [m for _, m in f13(upfront.check(prof))] == [
        "ops.RATES.RATE holds no row with 1.5: half's code is a value of its column",
        "ops.RATES.RANK holds no row with 1: one's code is a value of its column",
        "ops.RATES.LIVE holds no row with True: live's code is a value of its column"]


def test_a_table_that_cannot_be_read_is_left_to_f07_and_shows_nothing_else(shop):
    prof, folder = shop
    (folder / "fixture/data/ops/GROUPS.csv").unlink()
    result = upfront.check(prof)
    assert [f.rule for f in result.findings] == ["F07"]
    rows(folder / "fixture/data/ops/GROUPS.csv", ["GRP_CD"], ["G1"])
    assert "F13" not in [f.rule for f in upfront.check(prof).findings]


def test_a_code_on_a_restricted_column_is_not_read(shop, monkeypatch):
    """S37 refuses it; F13 reads none of the column's rows, so no restricted value is printed."""
    prof, folder = shop
    edit(folder, lambda p: p.setdefault("policies", {}).setdefault("restricted_attributes", []).append(
        {"id": "pii", "columns": ["ops.ROLLUP.LVL5"]}))
    rows(folder / "fixture/data/ops/ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR"], ["1", "SECRET", "L1", "Y"])
    found = f13(upfront.check(prof))
    assert found and not any("SECRET" in m or "ops.ROLLUP.LVL5" in m for _, m in found)


def test_a_malformed_join_is_no_crash_at_upload(shop):
    """S01 has not run at upload: a join whose ends are not text is passed over, and the edge it would carry is a
    finding, never a traceback."""
    prof, folder = shop
    edit(folder, lambda p: p["joins"].__setitem__(0, {**p["joins"][0], "from": ["ops.ACCOUNTS"]}))
    found = f13(upfront.check(prof))
    assert any("ops.ACCOUNTS.GRP_CD and ops.GROUPS.LINE_CD" in m and "cannot be compared" in m for _, m in found)


def test_a_unit_with_no_fixture_rows_is_not_checked_here_and_is_no_finding(shop, capsys):
    prof, folder = shop
    put(folder / "units/hr/draft.yaml", {"format": 1, "unit": "hr", "warehouse": 1, "items": []})
    edit(folder, lambda p: p["glossary"].append(
        {"id": "senior", "name": "Senior (grade)", "codes": [{"column": "hr.STAFF.GRADE", "code": "S"}]}))
    result = upfront.check(prof)
    assert result.findings == []
    assert result.not_checked == ["F13 hr: the fixture holds no rows for hr, so its declared codes and broader terms "
                                  "are checked against its warehouse when onboarding compiles"]
    assert check_pack.main(["--pack", str(folder)]) == 0
    out = capsys.readouterr().out
    assert ("check-pack: not checked here: F13 hr: the fixture holds no rows for hr, so its declared codes and broader "
            "terms are checked against its warehouse when onboarding compiles\n") in out
    assert out.endswith("check-pack: no findings\n")


def test_check_pack_reports_f13_with_its_line_and_exits_2(shop, capsys, tmp_path):
    prof, folder = shop
    edit(folder, lambda p: term(p, "plan")["codes"].append({"column": "ops.ROLLUP.LVL5", "code": "G7"}))
    target = tmp_path / "out.json"
    assert check_pack.main(["--pack", str(folder), "--json", str(target)]) == 2
    out = capsys.readouterr().out
    line = next(k for k, text in enumerate((folder / "pack.yaml").read_text().splitlines(), 1) if "G7" in text)
    assert f"check-pack: F13 pack.yaml:{line}: ops.ROLLUP.LVL5 holds no row with G7" in out
    assert "not_checked" not in json.loads(target.read_text()), "a pack with every unit checked keeps check-pack's JSON as it was"


def test_a_pack_with_no_codes_reads_no_row(shop, monkeypatch):
    prof, folder = shop
    edit(folder, lambda p: p.pop("glossary"))
    monkeypatch.setattr(term_data, "from_csv", lambda *args: pytest.fail("read rows"))
    result = upfront.check(prof)
    assert result.findings == [] and result.not_checked == []


def test_the_plan_reads_one_pair_per_column_pair_and_one_column_per_unpaired_column():
    """Retail's two coded columns pair with Investing's two: once on the same row and three times through a join;
    Planning's pairs are among them. The rollup's levels come from their same-row pair; the others are read alone."""
    the_plan = term_data.plan({"glossary": glossary(), "joins": JOINS})
    assert the_plan.pairs == [
        term_data.Pair("ops.ROLLUP.LVL5", ("ops.ROLLUP.LVL6",)),
        term_data.Pair("ops.ROLLUP.LVL5", ("ops.GROUPS.LINE_CD",), term_data.Route("rollup_group", (("LVL5", "GRP_CD"),))),
        term_data.Pair("ops.ACCOUNTS.GRP_CD", ("ops.ROLLUP.LVL6",), term_data.Route("account_rollup", (("GRP_CD", "LVL5"),))),
        term_data.Pair("ops.ACCOUNTS.GRP_CD", ("ops.GROUPS.LINE_CD",),
                       term_data.Route("account_group", (("GRP_CD", "GRP_CD"),)))]
    assert len(the_plan.edges) == 6 and the_plan.unlinked == []
    assert the_plan.alone == ["ops.GROUPS.LINE_CD", "ops.ACCOUNTS.GRP_CD"]
    assert the_plan.units == ["ops"]


def test_an_edge_across_sources_or_tables_without_a_join_cannot_be_compared():
    """S37 refuses such an edge at compile; F13, which runs first at upload, says it cannot compare it."""
    the_plan = term_data.plan({"glossary": glossary()})
    assert all(not p.keys for p in the_plan.pairs) and len(the_plan.unlinked) == 4
    data = {"glossary": [{"id": "a", "codes": [{"column": "x.T.C", "code": "1"}], "broader": ["b"]},
                         {"id": "b", "codes": [{"column": "y.T.C", "code": "2"}]}]}
    assert term_data.plan(data).pairs == []
    assert term_data.plan(data).unlinked == [term_data.Unlinked("a", "b", "glossary[0].broader[0]", "x.T.C", "y.T.C")]


def test_a_joins_column_equalities_are_read_and_the_rest_left_out():
    join = {"id": "j", "kind": "hierarchy", "from": "s.A", "to": "s.B",
            "condition": "A.K = B.K2 AND B.CURR = 'Y' AND s.A.Z = B.Y AND A.Q > B.Q"}
    assert terms.equalities(join) == [("s.A.K", "s.B.K2"), ("s.A.Z", "s.B.Y")]
    assert terms.equalities({**join, "condition": "B.K2 = A.K"}) == [("s.A.K", "s.B.K2")]
    assert terms.equalities({**join, "to": "s.A", "alias": "p", "condition": "A.PAR = p.K"}) == [("s.A.PAR", "s.A.K")]
    assert terms.equalities({**join, "to": "s.A", "condition": "A.PAR = A.K"}) == []
    assert terms.equalities({**join, "through": "s.C"}) == []
    assert terms.equalities({**join, "condition": "A.K = ("}) == []
    assert terms.equalities({**join, "from": ["s.A"]}) == []
    assert terms.row_join({"joins": [{**join, "kind": "calendar_rollup"}]}, "s.A", "s.B") is None
    assert terms.row_join({"joins": [{**join, "from": ["s.A"]}, {**join, "to": {"t": 1}}]}, "s.A", "s.B") is None
    assert terms.row_join({"joins": [join]}, "s.B", "s.A") == ("j", [("s.B.K2", "s.A.K"), ("s.B.Y", "s.A.Z")])


def test_a_null_join_key_is_no_broader_row_and_disagrees(shop):
    """An account with Retail's code and a NULL group key joins no group: it has no broader row, so no broader code (F4).
    A key that matches no row is the inner join's and is not compared."""
    prof, folder = shop
    rows(folder / "fixture/data/ops/ACCOUNTS.csv", ["ACCT", "GRP_CD"], ["1", "G1"], ["2", "G2"], ["3", ""])
    assert f13(upfront.check(prof)) == []
    the_plan = term_data.plan({"glossary": glossary(), "joins": JOINS})
    tables = term_data.types_of(yaml.safe_load((folder / "fixture/schema.yaml").read_text())["warehouses"]["ops"])
    found = term_data.Rows()
    term_data.from_csv(the_plan, "ops", folder / "fixture/data/ops", tables, found)
    pair = next(p for p in the_plan.pairs if p.join == "account_group")
    assert ("G1", None) not in found.pairs[pair]
    data = folder / "fixture/data/ops"
    rows(data / "ACCOUNTS.csv", ["ACCT", "GRP_CD"], ["1", "G1"], ["2", "G2"])
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    schema["warehouses"]["ops"]["tables"].append({"name": "LEDGER", "primary_key": ["ID"], "columns": [
        {"name": "ID", "type": "INTEGER", "description": "Row"}, {"name": "GRP", "type": "VARCHAR", "description": "Group"},
        {"name": "KEY", "type": "VARCHAR", "description": "Group key"}]})
    put(folder / "fixture/schema.yaml", schema)
    rows(data / "LEDGER.csv", ["ID", "GRP", "KEY"], ["1", "G1", "G1"], ["2", "G1", ""])

    def ledger(p):
        term(p, "retail")["codes"].append({"column": "ops.LEDGER.GRP", "code": "G1"})
        p["joins"] += [{"id": "ledger_group", "kind": "key", "cardinality": "one-to-one", "from": "ops.LEDGER", "to": "ops.GROUPS",
                        "condition": "LEDGER.KEY = GROUPS.GRP_CD"},
                       {"id": "ledger_rollup", "kind": "key", "cardinality": "one-to-many", "from": "ops.LEDGER", "to": "ops.ROLLUP",
                        "condition": "LEDGER.KEY = ROLLUP.LVL5"}]
    edit(folder, ledger)
    found = at(f13(upfront.check(prof)), "pack.yaml:glossary[4].broader[0]")
    assert ("retail is under inv, but rows with ops.LEDGER.GRP G1 have ops.GROUPS.LINE_CD NULL (a NULL is no broader term) "
            "through ledger_group, not L1: a broader term agrees with the rows") in found


def test_a_parent_child_join_names_the_parent_row_by_its_alias(shop):
    """The alias is the broader, parent row. A join to itself written the other way round, the alias the child row, reads
    the hierarchy upside down, and the rows then disagree with the declared edge: the contract and the guide say so."""
    prof, folder = shop

    def segments(p):
        p["glossary"] += [{"id": "core", "name": "Core (segment)", "codes": [{"column": "ops.SEGS.CD", "code": "C"}]},
                          {"id": "core-a", "name": "Core A (segment)", "broader": ["core"],
                           "codes": [{"column": "ops.SEGS.CD", "code": "CA"}]}]
        p["joins"].append({"id": "segment_child", "kind": "hierarchy", "cardinality": "one-to-many", "from": "ops.SEGS",
                           "to": "ops.SEGS", "alias": "child_seg", "condition": "(SEGS.CD = child_seg.PAR)"})
    edit(folder, segments)
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    schema["warehouses"]["ops"]["tables"].append({"name": "SEGS", "primary_key": ["CD"], "columns": [
        {"name": "CD", "type": "VARCHAR", "description": "Segment"}, {"name": "PAR", "type": "VARCHAR", "description": "Parent"}]})
    put(folder / "fixture/schema.yaml", schema)
    rows(folder / "fixture/data/ops/SEGS.csv", ["CD", "PAR"], ["C", ""], ["CA", "C"], ["CAA", "CA"])
    assert any("core-a is under core, but rows with ops.SEGS.CD CA have ops.SEGS.CD CAA through segment_child" in m
               for _, m in f13(upfront.check(prof)))
    edit(folder, lambda p: p["joins"].__setitem__(-1, {**p["joins"][-1], "alias": "parent_seg",
                                                       "condition": "(SEGS.PAR = parent_seg.CD)"}))
    assert f13(upfront.check(prof)) == []


def test_type_names_the_estate_accepts_read_as_their_type(shop):
    """INT8 is BIGINT, FLOAT8 DOUBLE, BOOL BOOLEAN, as the warehouse describes them; a date compares as YYYY-MM-DD."""
    prof, folder = shop

    def typed(p):
        p["glossary"] += [{"id": "one", "codes": [{"column": "ops.KINDS.BIG", "code": "01"}]},
                          {"id": "half", "codes": [{"column": "ops.KINDS.SHARE", "code": "1.5"}]},
                          {"id": "on", "codes": [{"column": "ops.KINDS.FLAG", "code": "yes"}]},
                          {"id": "day", "codes": [{"column": "ops.KINDS.DAY", "code": "2026-01-05"}]}]
    edit(folder, typed)
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    schema["warehouses"]["ops"]["tables"].append({"name": "KINDS", "primary_key": ["BIG"], "columns": [
        {"name": "BIG", "type": "int8", "description": "Big"}, {"name": "SHARE", "type": "FLOAT8", "description": "Share"},
        {"name": "FLAG", "type": "BOOL", "description": "Flag"}, {"name": "DAY", "type": "DATE", "description": "Day"}]})
    put(folder / "fixture/schema.yaml", schema)
    rows(folder / "fixture/data/ops/KINDS.csv", ["BIG", "SHARE", "FLAG", "DAY"], ["1", "1.50", "true", "2026-1-5"])
    assert f13(upfront.check(prof)) == []


def test_only_a_table_the_fixture_declares_is_read(shop, tmp_path):
    """A coded column whose table is a path, or a table the schema does not declare, reads no file: F03 reports it."""
    prof, folder = shop
    secret = tmp_path / "secret.csv"
    secret.write_text("A,B\nTOPSECRET,X\n")
    edit(folder, lambda p: p["glossary"].append({"id": "odd", "codes": [{"column": f"ops.{tmp_path}/secret.B", "code": "Y"},
                                                                         {"column": "ops.STRAY.B", "code": "Y"}]}))
    rows(folder / "fixture/data/ops/STRAY.csv", ["A", "B"], ["TOPSECRET", "Z"])
    result = upfront.check(prof)
    assert not any("TOPSECRET" in f.message for f in result.findings)
    assert not [f for f in result.findings if f.rule == "F13"]


def test_numbers_are_exact_and_an_absurd_one_is_no_crash():
    """Exact to 100 digits, so a HUGEINT's largest value or a DECIMAL(38) keeps every digit; a number beyond any column's
    reach, or not a number, is kept as written and matches only itself."""
    biggest = str(2 ** 127 - 1)
    assert term_data.number(biggest) == biggest and term_data.number(biggest + "0") != biggest
    assert term_data.number("0." + "1" * 38) == "0." + "1" * 38
    assert term_data.number("1.50") == "1.5" and term_data.number("-0") == "0" and term_data.number("1E+2") == "100"
    for absurd in ("1e9999999", "1e-9999999", "NaN", "inf", "x"):
        assert term_data.number(absurd) is None
        assert term_data.canonical(absurd, "number") == absurd


def test_a_nested_and_in_a_join_condition_is_read_whole():
    join = {"id": "j", "kind": "key", "from": "s.A", "to": "s.B"}
    want = [("s.A.X", "s.B.Y"), ("s.A.Z", "s.B.W")]
    for condition in ("A.X = B.Y AND (A.Z = B.W AND B.CUR = 'Y')", "(A.X = B.Y AND A.Z = B.W) AND B.CUR = 'Y'",
                      "((A.X = B.Y)) AND ((A.Z = B.W))", "A.X = B.Y AND A.Z = B.W AND A.X = B.Y"):
        assert terms.equalities({**join, "condition": condition}) == want, condition


def test_a_table_named_in_another_case_reads_the_declared_one(shop):
    prof, folder = shop
    edit(folder, lambda p: term(p, "plan")["codes"].append({"column": "ops.rollup.LVL5", "code": "G7"}))
    assert ("pack.yaml:glossary[5].codes[1].code", "ops.rollup.LVL5 holds no row with G7: plan's code is a value of its "
                                                   "column") in f13(upfront.check(prof))


def test_a_join_condition_too_deep_to_read_is_no_crash():
    deep = " AND ".join(["A.X = B.Y"] * 3000)
    assert terms.equalities({"from": "s.A", "to": "s.B", "condition": deep}) in ([], [("s.A.X", "s.B.Y")])


def two_line_columns(folder):
    """Investing coded on a second level-6 column of the rollup, ALT6, beside LVL6: alternatives on one table."""
    edit(folder, lambda p: term(p, "inv")["codes"].insert(1, {"column": "ops.ROLLUP.ALT6", "code": "L1X"}))
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    rollup = next(t for t in schema["warehouses"]["ops"]["tables"] if t["name"] == "ROLLUP")
    rollup["columns"].append({"name": "ALT6", "type": "VARCHAR", "description": "Level 6, alternate"})
    put(folder / "fixture/schema.yaml", schema)


def test_a_broader_terms_codes_on_two_columns_of_one_table_are_alternatives(shop):
    """A row holding Investing's code in either of its two columns agrees, and a row holding it in neither is reported
    once for the row, never once a column (the coordinator's decision on #513's F7 question)."""
    prof, folder = shop
    two_line_columns(folder)
    data = folder / "fixture/data/ops"
    rows(data / "ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR", "ALT6"], ["1", "G1", "L1", "Y", ""], ["8", "G1", "", "Y", "L1X"],
         ["2", "G2", "L2", "Y", ""])
    assert f13(upfront.check(prof)) == []
    the_plan = term_data.plan(yaml.safe_load((folder / "pack.yaml").read_text()))
    assert term_data.Pair("ops.ROLLUP.LVL5", ("ops.ROLLUP.LVL6", "ops.ROLLUP.ALT6")) in the_plan.pairs
    rows(data / "ROLLUP.csv", ["ID", "LVL5", "LVL6", "CURR", "ALT6"], ["1", "G1", "L1", "Y", ""], ["8", "G1", "", "Y", "L1X"],
         ["2", "G2", "L2", "Y", ""], ["9", "G1", "", "Y", ""])
    same_row = [m for m in at(f13(upfront.check(prof)), "pack.yaml:glossary[4].broader[0]") if "on the same row" in m]
    assert same_row == [
        "retail is under inv, but rows with ops.ROLLUP.LVL5 G1 have ops.ROLLUP.LVL6 / ops.ROLLUP.ALT6 NULL / NULL (a NULL is "
        "no broader term) on the same row, not ops.ROLLUP.LVL6 L1 or ops.ROLLUP.ALT6 L1X: a broader term agrees with the "
        "rows"]


def segment_pack(p, self_join=True):
    """A segment dimension (SEGS) whose rows name their parent segment, joined to itself, and a fact table (FACTS) whose
    segment column is joined to the dimension's key; Core A is coded on both, under Core."""
    p["glossary"] += [{"id": "seg", "name": "segment"},
                      {"id": "core", "name": "Core (segment)", "is_a": ["seg"], "codes": [{"column": "ops.SEGS.CD", "code": "C"}]},
                      {"id": "core-a", "name": "Core A (segment)", "is_a": ["seg"], "broader": ["core"],
                       "codes": [{"column": "ops.SEGS.CD", "code": "CA"}, {"column": "ops.FACTS.SEG", "code": "CA"}]}]
    p["joins"].append({"id": "fact_segment", "kind": "key", "cardinality": "many-to-one", "from": "ops.FACTS",
                       "to": "ops.SEGS", "condition": "FACTS.SEG = SEGS.CD"})
    if self_join:
        p["joins"].append({"id": "seg_parent", "kind": "hierarchy", "cardinality": "many-to-one", "from": "ops.SEGS",
                           "to": "ops.SEGS", "alias": "parent_seg", "condition": "SEGS.PAR = parent_seg.CD"})


SEGS = {"name": "SEGS", "primary_key": ["CD"], "columns": [{"name": "CD", "type": "VARCHAR", "description": "Segment"},
                                                          {"name": "PAR", "type": "VARCHAR", "description": "Parent"}]}
FACTS = {"name": "FACTS", "primary_key": ["ID"], "columns": [{"name": "ID", "type": "INTEGER", "description": "Row"},
                                                            {"name": "SEG", "type": "VARCHAR", "description": "Segment"}]}


def test_a_member_on_a_dimension_key_and_a_fact_column_compares_its_parent_row(shop):
    """Core A is coded on the segment dimension's key and on a fact column joined to it. The fact-to-dimension join
    reaches Core A's own row, so it is followed by the dimension's join to itself and Core A's parent row is compared
    (the coordinator's decision on #513's second question); without that join to itself the rows cannot be compared."""
    prof, folder = shop
    edit(folder, segment_pack)
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    schema["warehouses"]["ops"]["tables"] += [SEGS, FACTS]
    put(folder / "fixture/schema.yaml", schema)
    data = folder / "fixture/data/ops"
    rows(data / "SEGS.csv", ["CD", "PAR"], ["C", ""], ["CA", "C"])
    rows(data / "FACTS.csv", ["ID", "SEG"], ["1", "CA"], ["2", "CA"])
    the_plan = term_data.plan(yaml.safe_load((folder / "pack.yaml").read_text()))
    assert term_data.Pair("ops.FACTS.SEG", ("ops.SEGS.CD",), term_data.Route(
        "fact_segment", (("SEG", "CD"),), "seg_parent", (("PAR", "CD"),))) in the_plan.pairs
    assert f13(upfront.check(prof)) == []
    rows(data / "SEGS.csv", ["CD", "PAR"], ["C", ""], ["X", ""], ["CA", "X"])
    assert at(f13(upfront.check(prof)), "pack.yaml:glossary[8].broader[0]") == [
        "core-a is under core, but rows with ops.SEGS.CD CA have ops.SEGS.CD X through seg_parent, not C: a broader "
        "term agrees with the rows",
        "core-a is under core, but rows with ops.FACTS.SEG CA have ops.SEGS.CD X through fact_segment and seg_parent, "
        "not C: a broader term agrees with the rows"]
    rows(data / "SEGS.csv", ["CD", "PAR"], ["C", ""], ["CA", ""])
    assert any("rows with ops.FACTS.SEG CA have ops.SEGS.CD NULL (a NULL is no broader term) through fact_segment and "
               "seg_parent" in m for m in at(f13(upfront.check(prof)), "pack.yaml:glossary[8].broader[0]"))
    edit(folder, lambda p: p["joins"].pop())
    assert at(f13(upfront.check(prof)), "pack.yaml:glossary[8].broader[0]") == [
        "core-a is under core, coded on ops.SEGS.CD and ops.SEGS.CD, and no hierarchy or key join declared between those "
        "tables (from a table to itself, under an alias) equates their columns: the rows cannot be compared",
        "core-a is under core, coded on ops.FACTS.SEG and ops.SEGS.CD, and both are coded on a column of ops.SEGS, so the "
        "row reached is core-a's own, and no hierarchy or key join from ops.SEGS to itself, under an alias, leads on to "
        "its parent row: the rows cannot be compared"]


def test_a_fact_column_of_a_term_not_coded_on_the_dimension_keeps_the_direct_join(shop):
    """Only a term coded on the reached column itself goes on to the parent row: Retail, coded on the accounts' group and
    not on the groups' line column, is compared on the group row the join reaches, as before."""
    the_plan = term_data.plan({"glossary": glossary(), "joins": JOINS})
    assert all(p.route.hop is None for p in the_plan.pairs)


def test_a_second_coded_column_of_the_dimension_is_compared_on_the_parent_row_too(shop):
    """Core and Core A are both coded on the dimension's key, and Core on its name column too: every column of the
    dimension Core is coded on is reached through the parent row, from the fact column and from the dimension's own key,
    so correct rows give no finding, and the key and the name are alternatives, one finding a row. On one table, two
    terms coded on both columns compare each through the join to itself, never on the member's own row."""
    prof, folder = shop

    def named(p):
        segment_pack(p)
        term(p, "core")["codes"].append({"column": "ops.SEGS.NAME", "code": "Core"})
    edit(folder, named)
    schema = yaml.safe_load((folder / "fixture/schema.yaml").read_text())
    segs = {**SEGS, "columns": SEGS["columns"] + [{"name": "NAME", "type": "VARCHAR", "description": "Name"}]}
    schema["warehouses"]["ops"]["tables"] += [segs, FACTS]
    put(folder / "fixture/schema.yaml", schema)
    data = folder / "fixture/data/ops"
    rows(data / "SEGS.csv", ["CD", "PAR", "NAME"], ["C", "", "Core"], ["CA", "C", "Core A"])
    rows(data / "FACTS.csv", ["ID", "SEG"], ["1", "CA"])
    the_plan = term_data.plan(yaml.safe_load((folder / "pack.yaml").read_text()))
    assert term_data.Pair("ops.SEGS.CD", ("ops.SEGS.CD", "ops.SEGS.NAME"),
                          term_data.Route("seg_parent", (("PAR", "CD"),))) in the_plan.pairs
    assert term_data.Pair("ops.FACTS.SEG", ("ops.SEGS.CD", "ops.SEGS.NAME"), term_data.Route(
        "fact_segment", (("SEG", "CD"),), "seg_parent", (("PAR", "CD"),))) in the_plan.pairs
    assert f13(upfront.check(prof)) == []
    rows(data / "SEGS.csv", ["CD", "PAR", "NAME"], ["C", "", "Core"], ["X", "", "Other"], ["CA", "X", "Core A"])
    assert at(f13(upfront.check(prof)), "pack.yaml:glossary[8].broader[0]") == [
        "core-a is under core, but rows with ops.SEGS.CD CA have ops.SEGS.CD / ops.SEGS.NAME X / Other through seg_parent, "
        "not ops.SEGS.CD C or ops.SEGS.NAME Core: a broader term agrees with the rows",
        "core-a is under core, but rows with ops.FACTS.SEG CA have ops.SEGS.CD / ops.SEGS.NAME X / Other through "
        "fact_segment and seg_parent, not ops.SEGS.CD C or ops.SEGS.NAME Core: a broader term agrees with the rows"]
    edit(folder, lambda p: term(p, "core-a")["codes"].append({"column": "ops.SEGS.NAME", "code": "Core A"}))
    rows(data / "SEGS.csv", ["CD", "PAR", "NAME"], ["C", "", "Core"], ["CA", "C", "Core A"])
    assert f13(upfront.check(prof)) == []
