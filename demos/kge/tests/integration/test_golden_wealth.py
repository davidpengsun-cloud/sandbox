"""golden_wealth v1: the owner's Wealth and cross-unit questions, with expected behaviour and figure (#39 item 7e).

    .venv/bin/python -m pytest tests/integration/test_golden_wealth.py -q

kge/estate/golden_wealth.py writes build/golden_wealth_v1.json, called from
kge/estate/generate.py: the owner's twenty Wealth pairs in the owner's categories,
XB1 to XB6, and the refusals the owner names beside them (XB1 as
analyst_retail, XB3 as advisor_wealth, a date-of-birth match and a cross-BU
question asking for email). Each item carries its reference SQL, and each
figure is that query's result on the warehouses just generated, in a session
that attaches Retail, Wealth and the cross-reference for an item across units.
None equals a naive reading of its question, and a build whose figure would is
stopped.

The scorer reads the new expectations: a grouped figure's rows, scored like
parts; XB2's ratio; and, for a refusal, no partial figure. The harness asks an
item that names its principal as that principal. The metrics put
abstain_with_reason in the abstain class. The report adds a table per category,
graph off and graph on, with the baseline sets and the expansion side by side.

On rules, graph on (the graph preset and the agent's treatment) answers every
item its runtime has the semantics for. Assets under management came by a pack
edit alone (pack 1.3.0, issue #40's pack-only test): W16 and W17 pass since;
XB1 and XB2 ask for a count and a share across the certified join that the
runtime does not compute yet, and stay known misses (the owner's Question 23
default). The other items graph on does not answer need semantics the runtime
lacks: exclusions in force on a date, an account's version on a date, internal
and external inflows, and advisor coverage by office. W08 gets no figure rather
than another question's: its context applies no condition for the accounts
excluded on a date, so a figure would count those not excluded.

Reads the demo's build (the warehouses read only, the set files); the end-to-end
tests open the demo's graph: stop the API first. make golden runs on a scratch
folder.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

import duckdb
import pytest
import sqlglot
import yaml
from sqlglot import exp

from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.core import PRESETS
from kge.estate import generate, spec
from kge.estate import golden_wealth as gw
from kge.eval import harness, metrics, report, sets
from kge.eval.run_golden import score, uncertified_ids
from tests.scratch import BUILD, DEMO

PATH = BUILD / "golden_wealth_v1.json"
IDS = [f"W{n:02d}" for n in range(1, 21)] + [f"XB{n}" for n in range(1, 7)] + ["XB1-R", "XB3-W", "XB-DOB", "XB-EMAIL"]
COUNTS = {"NNA overall / client-directed": 3, "Eligibility + test-account filtering": 3, "Exclusions": 2, "As-of vs current (SCD2)": 3,
          "Month-end freeze / release": 2, "Hierarchy roll-ups": 2, "AUM vs AUA disambiguation": 2, "Acronym resolution": 1,
          "Refusal / abstain": 2, "Cross-BU (Retail↔Wealth)": 10}
AUM = ["W16", "W17"]
AUM_MISSES = ["XB1", "XB2"]
ANSWERED = ["W01", "W02", "W03", "W04", "W05", "W06", "W07", "W09", "W10", "W12", "W13", "W14", "W15", "W16", "W17", "W18", "W19",
            "W20", "XB3", "XB6", "XB1-R", "XB3-W", "XB-DOB", "XB-EMAIL"]
NO_FIGURE = ["W08"]


@pytest.fixture(scope="module")
def built() -> list[dict]:
    return sets.items(PATH)


def by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


def session(attached: bool):
    if not attached:
        return duckdb.connect(str(BUILD / "warehouses" / "wealth.duckdb"), read_only=True)
    con = duckdb.connect(":memory:")
    for u in ("retail", "wealth", "xref"):
        con.execute(f"ATTACH '{BUILD / 'warehouses' / u}.duckdb' AS {u} (READ_ONLY)")
    return con


def across(item: dict) -> bool:
    return bool(item["reference_sql"]) and bool(re.search(r"\b(retail|xref)\.", item["reference_sql"]))


def test_the_set_is_a_first_version_of_its_own_named_in_the_pack_and_run_under_all(built):
    data = sets.read(PATH)
    assert data["version"] == "v1" and data["changes_from"] is None and isinstance(json.loads(PATH.read_text()), list)
    assert [i["id"] for i in built] == IDS
    pack = yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())
    named = {s["name"]: s for s in pack["tests"]["sets"]}["golden_wealth"]
    assert named["version"] == "v1" and named["items"] == IDS, "the pack names the set's items, by id alone"
    assert harness.SETS == {"golden": "v2", "heldout": "v1"} and harness.set_version("golden_wealth_v1") == "v1"
    assert harness.choose_sets("all", BUILD) == ["golden", "heldout", "golden_wealth_v1", "sage_v1"]
    assert not [p for d in ("services", "semantics", "compiler", "eval", "graph", "landing", "pipeline", "ui") for p in (DEMO / (d if d == "ui" else f"kge/{d}")).rglob("*")
                if p.suffix in (".py", ".html", ".js") and "golden_wealth" in p.read_text()], "nor in a docstring, a help or a report"


def test_each_item_names_its_kind_category_question_expectation_and_reference_sql(built):
    for i in built:
        assert set(i) - {"principal"} == {"id", "kind", "category", "question", "expect", "reference_sql"}, i["id"]
        assert i["kind"] in report.KINDS and i["category"] in COUNTS, i["id"]
    assert {c: sum(i["category"] == c for i in built) for c in COUNTS} == COUNTS
    assert [i["category"] for i in built] == sorted((i["category"] for i in built), key=list(COUNTS).index), "in the owner's order"
    items = by_id(built)
    assert {i["id"]: i["principal"] for i in built if "principal" in i} == {"XB1-R": "analyst_retail", "XB3-W": "advisor_wealth"}
    assert items["XB1-R"]["question"] == items["XB1"]["question"] and items["XB3-W"]["question"] == items["XB3"]["question"]
    declines = {i["id"] for i in built if i["expect"].get("behaviour") in ("refuse", "blocked_access")}
    assert declines == {"W17", "W19", "W20", "XB6", "XB1-R", "XB3-W", "XB-DOB", "XB-EMAIL"}
    assert all((i["reference_sql"] is None) == (i["id"] in declines) for i in built)
    for i in built:
        e = i["expect"]
        if i["id"] in declines:
            assert e.get("no_figure") or i["kind"] == "abstain", i["id"]
            assert not {"figure", "rows", "parts"} & set(e), i["id"]
        elif i["id"] != "W13":
            assert {"figure", "rows", "parts"} & set(e), i["id"]
    assert items["W13"]["expect"]["behaviour"] == "abstain_with_reason" and items["W13"]["kind"] == "abstain"


def test_the_file_is_what_the_generator_gives_from_the_warehouses_on_disk(built):
    again = gw.build_golden_wealth(BUILD)
    assert again == built and gw.set_text(again) == PATH.read_text()


def test_each_expectation_is_its_reference_sql_s_result_on_the_warehouses(built):
    """Each query run again here, apart from the generator's reading: the figure, the rows a grouped figure gives, or
    XB2's parts and AUM share and XB4's internal and external inflows."""
    for i in built:
        e, sql = i["expect"], i["reference_sql"]
        if not sql:
            continue
        con = session(across(i))
        try:
            rows = con.execute(sql).fetchall()
            names = [d[0] for d in con.description]
        finally:
            con.close()
        if "figure" in e:
            assert len(rows) == 1 and float(rows[0][0]) == e["figure"], i["id"]
        elif "rows" in e:
            assert {str(k): float(v) for k, v in rows} == e["rows"] and len(e["rows"]) > 1, i["id"]
        elif "ratio" in e:
            (row,) = rows
            assert dict(zip(names, map(float, row))) == {**e["parts"], "ratio": e["ratio"]}, i["id"]
        elif "parts" in e:
            assert {str(k): float(v) for k, v in rows} == e["parts"], i["id"]
        else:
            assert i["id"] == "W13" and rows == [(False,)]


def test_no_figure_equals_a_naive_reading_of_its_question(built):
    items = by_id(built)
    for item in gw.items():
        e = items[item.id]["expect"]
        if not {"figure", "rows", "parts"} & set(e):
            assert not item.naive, item.id
            continue
        assert item.naive, f"{item.id} has no naive reading to differ from"
        con = session(item.attached)
        try:
            for what, sql in item.naive:
                assert not gw.same(e, gw.read(con, item, sql)), (item.id, what)
        finally:
            con.close()
    assert items["W09"]["expect"]["figure"] != items["W10"]["expect"]["figure"], "as reported and restated differ (ex. 3)"
    assert items["W12"]["expect"]["figure"] == items["W03"]["expect"]["figure"], "the latest month is June 2025, the latest released"
    assert items["W02"]["expect"]["figure"] < items["W03"]["expect"]["figure"], "client-directed is part of NNA"
    assert sum(items["W01"]["expect"]["rows"].values()) == pytest.approx(sum(items["W14"]["expect"]["rows"].values()))
    assert set(items["W14"]["expect"]["rows"]) == {"MIDWEST", "NORTHEAST", "PACIFIC"}
    assert set(items["W18"]["expect"]["rows"]) == {"100", "200"} and items["W06"]["expect"]["figure"] == items["W18"]["expect"]["rows"]["100"]


Q8, Q9, Q10, Q11, Q12 = (f"Question {n}" for n in range(8, 13))
DEPARTS = {"W01": Q8, "W02": Q8, "W07": Q11, "W09": Q8, "W12": Q8, "W14": Q8, "W18": Q12, "XB2": Q9, "XB3": Q8, "XB4": Q10}
MEASURED = {"W01": ({"EAST": 12371673.22, "WEST": 8831519.54}, {"EAST": 16817884.48, "WEST": 8831519.54}),
            "W02": ({"figure": 4753885.36}, {"figure": 5685962.36}),
            "W07": ({"figure": 17080976.76}, {"figure": 17118215.01}),
            "W09": ({"figure": 5868354.54}, {"figure": 8088907.20}),
            "W12": ({"figure": 6069905.64}, {"figure": 7141850.98}),
            "W14": ({"MIDWEST": 3755170.97, "NORTHEAST": 12371673.22, "PACIFIC": 5076348.57},
                    {"MIDWEST": 3755170.97, "NORTHEAST": 16817884.48, "PACIFIC": 5076348.57}),
            "W18": ({"200": 4292235.04, "100": 16910957.72}, {"200": 4743669.30, "100": 17264316.78}),
            "XB2": ({"retail": 252002.68, "wealth": 101566960.12, "ratio": 0.9975}, {"retail": 323203.52, "wealth": 111354177.52, "ratio": 0.9971}),
            "XB3": ({"figure": 4985720.27}, {"figure": 6157094.19}),
            "XB4": ({"EXTERNAL": 40539392.68, "INTERNAL_FROM_RETAIL": 6177.94}, {"EXTERNAL": 40870054.31, "INTERNAL_FROM_RETAIL": 6177.94})}


def flat(reading: dict) -> dict:
    return {**({"figure": reading["figure"]} if "figure" in reading else {}), **(reading.get("rows") or {}),
            **(reading.get("parts") or {}), **({"ratio": reading["ratio"]} if "ratio" in reading else {})}


def item_(qid: str) -> gw.Item:
    return next(i for i in gw.items() if i.id == qid)


def reading(item: gw.Item, sql: str) -> dict:
    con = session(item.attached)
    try:
        return gw.read(con, item, sql)
    finally:
        con.close()


def test_each_departure_from_the_owner_s_example_is_an_owner_question_its_example_kept_beside_it(built):
    """W01 and W14 join office, region and division on their current version, and so do W02, W09, W12 and XB3, which the
    owner writes as changes to example 1 (Question 8: example 1 joins the office on its code alone, which counts a closed
    office version's flows twice); W07 counts a flow unless its account is a test account in any version (Question 11:
    example 1 joins the account's current version and reads its flag there, so a closed account's flows drop out); W18
    reads example 6 as net new assets in full, for FY26-Q1 (Question 12: example 6 sums eligible, non-test flows only and
    names no period); XB2 counts current and savings accounts as deposits, as XB1 does (Question 9: XB2 as written counts
    every retail account, lending and cards among them); XB4 classes each inflow once (Question 10: XB4 as written
    left-joins the cross-reference, so an inflow to a household with two certified matches counts twice). Each example as
    written is kept beside its item as the question's alternative, never as a naive reading, and its figure differs from
    the default's, or the departure would change nothing."""
    items = by_id(built)
    assert {i.id: i.as_written[0] for i in gw.items() if i.as_written} == DEPARTS
    for item in gw.items():
        if not item.as_written:
            continue
        written = item.as_written[1]
        assert written not in [sql for _, sql in item.naive], (item.id, "the owner's example is no naive reading")
        got = reading(item, written)
        assert not gw.same(items[item.id]["expect"], got), item.id
        assert (flat(items[item.id]["expect"]), flat(got)) == MEASURED[item.id], item.id
    w01, w02, w07, w09, w12, w18, xb2, xb3, xb4 = map(item_, ("W01", "W02", "W07", "W09", "W12", "W18", "XB2", "XB3", "XB4"))
    for item in (w01, w02, w09, w12, xb3):
        assert "o.BRCH_CD=ad.BRCH_CD JOIN" in item.as_written[1] and "o.CURR_IN" not in item.as_written[1], item.id
    for item in (w02, w09, w12, xb3):
        assert item.reads == "figure" and "d.DIVSN_CD" in item.as_written[1] and "sum(nna_usd)" in item.as_written[1], item.id
    assert "F_NNA_FIN" in w02.as_written[1] and "CLIENT_DIRECTED_NNA_IN = 'Y'" in w02.as_written[1]
    assert "m.CLDR_EOM_DT=DATE '2025-06-30'" in w02.as_written[1]
    assert ("WHERE f.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT AND (f.DW_EXPR_DT IS NULL OR f.DW_EXPR_DT > m.FIN_MTRC_RLSE_DT)" in w09.as_written[1]
            and "m.CLDR_EOM_DT=DATE '2025-03-31'" in w09.as_written[1])
    assert "m.CLDR_EOM_DT=(SELECT max(CLDR_EOM_DT) FROM MONTH_DIM_1 WHERE DW_BRKRG_MNTH_FRM_TODAY_CT <> 0)" in w12.as_written[1]
    assert "a.HSHLD_DK IN (SELECT x.household_id FROM xref.client_xref x" in xb3.as_written[1] and "wealth.BRCH_DIM_1 o" in xb3.as_written[1]
    assert "AND a.CURR_IN = 1" in w07.as_written[1] and "a.TEST_ACCT_IN = 'N'" in w07.as_written[1] and "BRCH_DIM_1" not in w07.as_written[1]
    assert "m.CLDR_EOM_DT BETWEEN DATE '2025-01-31' AND DATE '2025-03-31'" in w07.as_written[1]
    assert "NNA_EXCL_1" in w18.sql and "NNA_EXCL_1" not in w18.as_written[1] and "TEST_ACCT_IN" in w18.as_written[1]
    assert "f.NNA_IN = 'Y'" in w18.as_written[1] and "f.ACCT_SRCE_SYS_ID" in w18.as_written[1] and "FY26-Q1" in w18.question
    assert "product_family" not in xb2.as_written[1] and "product_family IN ('current', 'savings')" in xb2.sql
    assert "LEFT JOIN xref.client_xref x" in xb4.as_written[1] and "EXISTS" not in xb4.as_written[1] and "EXISTS" in xb4.sql
    assert set(items["XB4"]["expect"]["parts"]) == {"INTERNAL_FROM_RETAIL", "EXTERNAL"}


def test_question_8_s_departure_is_the_office_s_version_alone(built):
    """Each item under Question 8, the example as written with the office joined on its current version, gives the item's
    own figure: the closed office version is the whole of the departure, and nothing else the example writes moves it."""
    items = by_id(built)
    for item in gw.items():
        if not item.as_written or item.as_written[0] != Q8:
            continue
        written = item.as_written[1]
        assert written.count("o.BRCH_CD=ad.BRCH_CD JOIN") == 1, item.id
        current = written.replace("o.BRCH_CD=ad.BRCH_CD", "o.BRCH_CD=ad.BRCH_CD AND o.CURR_IN = 1")
        assert gw.same(items[item.id]["expect"], reading(item, current)), item.id


def test_question_11_s_test_account_rule_moves_w07_alone(built):
    """The default reads a test account on every version of the account (gw.RULES["not_test"]); example 1 joins the
    account's current version and reads its flag there. Every item whose query applies the rule, read by example 1's
    instead, gives its own figure, but W07, whose FY25-Q4 holds flows on accounts closed since; and W07 so read is the
    example's figure kept beside it."""
    items, moved = by_id(built), []
    for item in gw.items():
        w = "wealth." if item.attached else ""
        rule = gw.RULES["not_test"].format(w=w)
        if not item.sql or rule not in item.sql:
            continue
        sql = item.sql.replace(rule, "a.TEST_ACCT_IN = 'N'")
        if " a ON " not in sql:
            sql = sql.replace(" WHERE ", f" {gw.ACCOUNT.format(w=w)} WHERE ", 1)
        got = reading(item, sql)
        if not gw.same(items[item.id]["expect"], got):
            moved.append(item.id)
            assert gw.same(got, reading(item, item.as_written[1])), item.id
    assert moved == ["W07"]


def test_w09_reads_example_3_s_version_as_written(built):
    """The as-reported version is example 3's predicate as written, valid_to after the freeze date, on SAGE's columns
    (pack 1.5.0: DW_EFF_DT and DW_EXPR_DT, and the month's release date, FIN_MTRC_RLSE_DT, for its freeze, which SAGE
    records none of). The warehouse keeps valid_to as a version's last day (the pack's scd2 policy, valid_to_inclusive);
    no flow version ends on its month's freeze date, so the predicate reads the rows the inclusive window does, and W09's
    figure is the same either way."""
    assert gw.AS_REPORTED == "f.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT AND (f.DW_EXPR_DT IS NULL OR f.DW_EXPR_DT > m.FIN_MTRC_RLSE_DT)"
    w09 = item_("W09")
    assert gw.AS_REPORTED in w09.sql
    inclusive = w09.sql.replace("f.DW_EXPR_DT > m.FIN_MTRC_RLSE_DT", "f.DW_EXPR_DT >= m.FIN_MTRC_RLSE_DT")
    assert inclusive != w09.sql and gw.same(by_id(built)["W09"]["expect"], reading(w09, inclusive))


def test_the_readme_and_the_generator_name_each_departure_s_question_and_default():
    readme = " ".join((DEMO / "README.md").read_text().split())
    section = readme.split("**golden_wealth_v1.**", 1)[1].split(" **", 1)[0]
    doc = " ".join((gw.__doc__ or "").split())
    for text in (section, doc):
        for said in (Q8, Q9, Q10, Q11, Q12, "current version", "current and savings", "each inflow once", "as written",
                     "test account", "no period"):
            assert said in text, said
        assert "closed office" not in text.split("naive reading", 1)[1].split(". ", 1)[0], "the owner's example is no naive reading"
        covers = {q: text.split(q, 1)[1][:600] for q in (Q8, Q11, Q12)}
        assert all(i in covers[Q8].split(Q9, 1)[0] for i in ("W01", "W02", "W09", "W12", "W14", "XB3")), covers[Q8]
        assert "W07" in covers[Q11].split(Q12, 1)[0] and "W18" in covers[Q12], (covers[Q11], covers[Q12])
    rows = [line for line in (DEMO / "README.md").read_text().splitlines() if line.startswith("| ")]
    for qid, key in (("W01", "EAST"), ("W14", "NORTHEAST"), ("W02", "figure"), ("W09", "figure"), ("W12", "figure"), ("XB3", "figure"),
                     ("XB2", "retail"), ("XB4", "EXTERNAL"), ("W07", "figure"), ("W18", "200"), ("W18", "100")):
        default, written = (r[key] for r in MEASURED[qid])
        assert any(row.startswith(f"| {DEPARTS[qid]} | ") and qid in row and (key in ("figure", "retail") or key in row)
                   and f"| ${default:,.2f} | ${written:,.2f} |" in row for row in rows), (qid, key)


def test_the_build_stops_on_a_departure_that_changes_nothing(monkeypatch):
    w03 = next(i for i in gw.items() if i.id == "W03")
    idle = gw.Item(w03.id, w03.kind, w03.category, w03.question, w03.expect, w03.sql, w03.reads, w03.naive,
                   as_written=("Question 8", w03.sql))
    monkeypatch.setattr(gw, "items", lambda: [idle])
    with pytest.raises(ValueError, match=r"W03 \(NNA for June 2025\).*equals the owner's example as written.*Question 8"):
        gw.build_golden_wealth(BUILD)


def test_the_build_stops_on_a_figure_that_equals_a_naive_reading_and_writes_no_set(tmp_path, monkeypatch):
    real = gw.items()
    w03 = next(i for i in real if i.id == "W03")
    blind = gw.Item(w03.id, w03.kind, w03.category, w03.question, w03.expect, w03.sql, w03.reads, (("nothing left out", w03.sql),))
    monkeypatch.setattr(gw, "items", lambda: [blind])
    with pytest.raises(gw.Naive, match=r"W03 \(NNA for June 2025\).*equals the naive reading with nothing left out"):
        gw.build_golden_wealth(BUILD)
    out = tmp_path / "build"
    out.mkdir()
    (out / "warehouses").symlink_to(BUILD / "warehouses")
    with pytest.raises(gw.Naive):
        generate.write_question_sets(out)
    assert sorted(p.name for p in out.iterdir()) == ["warehouses"], "no set is written when one cannot be built"


def test_the_release_items_rest_on_the_release_driver(built):
    con = session(False)
    try:
        released = con.execute("SELECT max(CLDR_EOM_DT) FROM MONTH_DIM_1 WHERE DW_BRKRG_MNTH_FRM_TODAY_CT <> 0").fetchone()[0]
        unreleased = con.execute("SELECT CLDR_EOM_DT FROM MONTH_DIM_1 WHERE DW_BRKRG_MNTH_FRM_TODAY_CT = 0").fetchall()
    finally:
        con.close()
    assert str(released) == "2025-06-30" and [str(m) for (m,) in unreleased] == ["2025-07-31"]
    items = by_id(built)
    assert "July 2025" in items["W13"]["question"] and "July 2025 is not yet released" in items["W13"]["expect"]["reason"]


def test_no_reference_sql_reads_restricted_personal_data(built):
    restricted = {c for _, _, c in spec.RESTRICTED_COLUMNS}
    for i in built:
        for sql in [i["reference_sql"], *(s for item in gw.items() if item.id == i["id"]
                                          for _, s in [*item.naive, *([item.as_written] if item.as_written else [])])]:
            if sql:
                named = {c.name for c in sqlglot.parse_one(sql, read="duckdb").find_all(exp.Column)}
                assert not named & restricted, (i["id"], named & restricted)


def test_xb5_takes_question_2_s_threshold_and_xb2_its_aum_share(built):
    items = by_id(built)
    xb5 = items["XB5"]
    assert "$8,000" in xb5["question"] and "> 8000" in xb5["reference_sql"] and "250" not in xb5["question"]
    rows = xb5["expect"]["rows"]
    assert sum(rows.values()) == 20 and rows.get("UNCOVERED") and len(rows) > 1, "about the top decile, some covered by an advisor"
    xb2 = items["XB2"]["expect"]
    assert set(xb2["parts"]) == {"retail", "wealth"} and xb2["commensurable"] is False
    assert xb2["ratio"] == round(xb2["parts"]["wealth"] / (xb2["parts"]["retail"] + xb2["parts"]["wealth"]), 4)
    assert set(items["XB4"]["expect"]["parts"]) == {"INTERNAL_FROM_RETAIL", "EXTERNAL"} and items["XB4"]["expect"]["parts"]["INTERNAL_FROM_RETAIL"] > 0


def test_make_golden_writes_the_four_sets_from_the_warehouses_on_disk(tmp_path):
    out = tmp_path / "build"
    out.mkdir()
    (out / "warehouses").symlink_to(BUILD / "warehouses")
    r = subprocess.run([sys.executable, "-m", "kge.estate.generate", "--out", str(out), "--golden-only"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"golden_questions": 45, "heldout_questions": 16, "golden_wealth_questions": len(IDS),
                                    "sage_questions": 5}
    for name in ("golden.json", "heldout.json", "golden_wealth_v1.json", "sage_v1.json"):
        assert (out / name).read_bytes() == (BUILD / name).read_bytes(), name


ROWS = {"id": "T1", "kind": "within", "question": "NNA for FY26-Q1 by division",
        "expect": {"unit": "wealth", "columns": ["flow.amount_usd"], "rows": {"EAST": 2.5, "WEST": 1.0}}}


def test_the_scorer_reads_a_grouped_figure_s_rows_like_parts():
    ans = {"behaviour": "figure", "figure": None, "rows": {"EAST": 2.5, "WEST": 1.0}, "unit": "wealth", "column": "flow.amount_usd",
           "join_path": ["flow_month_end", "flow_account"]}
    assert score(ROWS, ans) == (True, "")
    for wrong in ({"EAST": 2.5}, {"EAST": 2.5, "WEST": 1.0, "NORTH": 0.0}, {"EAST": 2.5, "WEST": 1.5}, None, [["EAST", 2.5]]):
        ok, why = score(ROWS, {**ans, "rows": wrong})
        assert not ok and "rows" in why, wrong
    assert not score(ROWS, {**ans, "column": "flow.flow_id"})[0] and not score(ROWS, {**ans, "unit": "retail"})[0]
    assert not score(ROWS, {**ans, "behaviour": "abstain"})[0]
    across_ = {"id": "T2", "kind": "cross", "question": "q", "expect": {"behaviour": "figure", "rows": {"NYC": 4.0, "UNCOVERED": 16.0}}}
    assert score(across_, {"behaviour": "figure", "rows": {"NYC": 4, "UNCOVERED": 16}})[0]
    assert not score(across_, {"behaviour": "figure", "rows": {"NYC": 4, "UNCOVERED": 15}})[0]
    assert metrics.figure_ok(ROWS, ans) is True and metrics.figure_ok(ROWS, {**ans, "rows": {"EAST": 2.5}}) is False


def test_the_scorer_reads_a_ratio_and_a_refusal_with_no_partial_figure():
    xb2 = {"id": "XB2", "kind": "cross", "question": "q",
           "expect": {"behaviour": "parts_with_reason", "commensurable": False, "parts": {"retail": 1.0, "wealth": 3.0}, "ratio": 0.75}}
    good = {"behaviour": "parts_with_reason", "commensurable": False, "parts": {"retail": 1.0, "wealth": 3.0}, "ratio": 0.75004}
    assert score(xb2, good)[0] and metrics.figure_ok(xb2, good) is True
    for off in ({**good, "ratio": 0.7}, {k: v for k, v in good.items() if k != "ratio"}):
        ok, why = score(xb2, off)
        assert not ok and "ratio" in why and metrics.figure_ok(xb2, off) is False
    refused = {"id": "XB1-R", "kind": "cross", "question": "q", "expect": {"behaviour": "blocked_access", "no_figure": True}}
    assert score(refused, {"behaviour": "blocked_access", "denied_warehouses": ["wealth"]})[0]
    assert score(refused, {"behaviour": "blocked_access", "parts": [{"unit": "retail", "status": "refused"}]})[0], "a plan is no figure"
    for partial in ({"figure": 24.0}, {"parts": {"retail": 200.0}}, {"rows": {"NYC": 4.0}}):
        ok, why = score(refused, {"behaviour": "blocked_access", **partial})
        assert not ok and "partial figure" in why, partial
    assert not score(refused, {"behaviour": "refuse"})[0], "the join-time access rule's own behaviour, exactly"
    assert metrics.behaviour(refused, {"behaviour": "refuse"})["class"] is True


def test_abstain_with_reason_is_the_abstain_class_and_scored_exactly():
    w13 = {"id": "W13", "kind": "abstain", "question": "NNA for July 2025", "expect": {"behaviour": "abstain_with_reason"}}
    assert metrics.behaviour_class("abstain_with_reason") == "abstain"
    assert score(w13, {"behaviour": "abstain_with_reason"})[0] and not score(w13, {"behaviour": "abstain"})[0]
    assert metrics.behaviour(w13, {"behaviour": "abstain"}) == {"expected": "abstain_with_reason", "got": "abstain", "exact": False, "class": True}
    assert metrics.behaviour(w13, {"behaviour": "figure"})["class"] is False


class Asked:
    """An arm set that records who each question was asked as."""
    def __init__(self):
        self.asked = []

    def describe(self, arm: str) -> dict:
        return {"arm": arm, "side": harness.SIDES[arm], "path": "answer", "mode": "rules", "config": arm,
                "stages": {"context": "graph" if arm in ("graph", "treatment") else "schema"}}

    def about(self) -> dict:
        return {"llm": None, "graph": {"model": "m" * 64, "overlay": "o" * 64}}

    def ask(self, arm: str, question: str, principal: str) -> dict:
        self.asked.append((arm, question, principal))
        return {"behaviour": "blocked_access" if principal == "analyst_retail" else "figure", "figure": None,
                "run_trace": {"total_ms": 1.0, "tokens": {"in": 0, "out": 0}, "llm_calls": [], "number_check": None}}


def test_the_harness_asks_an_item_that_names_its_principal_as_that_principal(tmp_path, built):
    """The scratch build's set file is a link to the demo's own, so it is unlinked first and never written through."""
    from tests.scratch import profile_of, scratch_build
    build = scratch_build(tmp_path / "build", graph=False)
    (build / "golden_wealth_v1.json").unlink(missing_ok=True)
    two = [by_id(built)["XB1"], by_id(built)["XB1-R"]]
    (build / "golden_wealth_v1.json").write_text(gw.set_text(two))
    arms = Asked()
    out = harness.run(profile_of(build), arms, ["graph", "treatment"], ["golden_wealth_v1"], repeats=1, out=tmp_path / "runs", echo=lambda *a: None)
    assert [(q == two[0]["question"], who) for _, q, who in arms.asked] == [(True, "analyst_enterprise"), (True, "analyst_retail")] * 2
    recs = [json.loads(line) for line in (out / "records.jsonl").read_text().splitlines()]
    assert [(r["id"], r["principal"], r["category"], r["ok"]) for r in recs] == [
        ("XB1", "analyst_enterprise", "Cross-BU (Retail↔Wealth)", False), ("XB1-R", "analyst_retail", "Cross-BU (Retail↔Wealth)", True)] * 2
    assert json.loads((out / "manifest.json").read_text())["principal"] == "analyst_enterprise", "the run's principal is unchanged"


def rec(arm: str, repeat: int, ok: bool, set_name: str, id_: str, kind: str, category: str | None = None) -> dict:
    m = {"figure_ok": None, "behaviour": {"expected": "figure", "got": "figure", "exact": ok, "class": ok}, "gate": None,
         "citations": {"cited": 0, "tier_correct": 0, "off_tier": []}, "numbers": {"checked": False, "caught": None, "bad_markers": None, "shown": None},
         "ms": 1.0, "tokens": {"in": 0, "out": 0}, "llm_calls": 0, "cost_usd": 0.0}
    return {"run_id": "r", "repeat": repeat, "arm": arm, "mode": "rules", "config": arm, "set": set_name, "id": id_, "kind": kind,
            "question": f"Question {id_}", "ok": ok, "why": "", "measures": m, **({"category": category} if category else {})}


def a_run() -> tuple[dict, list[dict]]:
    arms = ["schema", "graph", "control", "treatment"]
    m = {"run_id": "r", "started_at": "2026-10-13T00:00:00+00:00", "finished_at": "2026-10-13T00:01:00+00:00", "principal": "analyst_enterprise",
         "repeats": 2, "sets": {"golden": 1, "golden_wealth_v1": 2}, "profile": "meridian", "graph": {"model": "a" * 64, "overlay": "b" * 64},
         "llm": None, "prices": {"in": None, "out": None, "currency": "USD", "per_tokens": 1_000_000},
         "arms": [{"arm": a, "side": harness.SIDES[a], "path": "answer", "mode": "rules", "config": a,
                   "stages": {"context": "graph" if a in ("graph", "treatment") else "schema"}} for a in arms]}
    recs = []
    for n in (1, 2):
        for a in arms:
            on = a in ("graph", "treatment")
            recs += [rec(a, n, True, "golden", "G001", "within"),
                     rec(a, n, on, "golden_wealth_v1", "W01", "within", "NNA overall / client-directed"),
                     rec(a, n, on and n == 1, "golden_wealth_v1", "XB3", "cross", "Cross-BU (Retail↔Wealth)")]
    return m, recs


def test_the_report_puts_each_category_side_by_side_graph_off_and_graph_on_baseline_and_expansion():
    m, recs = a_run()
    md = report.markdown(m, recs)
    section = md.split("## By category: graph off and graph on", 1)[1].split("\n## ", 1)[0]
    assert ("| Set | Category | Questions | schema (graph off) | graph (graph on) | control (graph off) | treatment (graph on) | "
            "Margin, treatment less control |") in section
    assert "| Golden set, baseline | Within a unit | 1 | 1 | 1 | 1 | 1 | 0 pts |" in section
    assert "| golden_wealth_v1, expansion | NNA overall / client-directed | 1 | 0 | 1 | 0 | 1 | +100 pts |" in section
    assert "| golden_wealth_v1, expansion | Cross-BU (Retail↔Wealth) | 1 | 0 | 0.5 (0–1) | 0 | 0.5 (0–1) | +50 pts (0–+100) |" in section
    assert section.index("Golden set, baseline") < section.index("golden_wealth_v1, expansion")
    paragraph = section.split("\n|", 1)[0]
    assert "a set whose items name their category, by those, and any other by its kinds" in paragraph
    assert "The baseline sets are the golden and held-out sets the harness names; any further set is the expansion." in paragraph
    assert not re.search(r"golden_wealth|#\d|owner", paragraph), paragraph
    assert "By category: graph off and graph on" in report.html(m, recs)
    only = [r for r in recs if r["arm"] in ("schema", "graph")]
    m2 = {**m, "arms": [a for a in m["arms"] if a["arm"] in ("schema", "graph")]}
    section = report.markdown(m2, only).split("## By category: graph off and graph on", 1)[1].split("\n## ", 1)[0]
    assert "| golden_wealth_v1, expansion | NNA overall / client-directed | 1 | 0 | 1 | n/a |" in section


def test_the_harness_prints_the_categories_too(capsys):
    m, recs = a_run()
    for line in report.category_lines(m, recs):
        print(line)
    out = capsys.readouterr().out
    assert "by category, passed (mean of 2): graph off and graph on" in out
    assert re.search(r"golden_wealth_v1, expansion\s+NNA overall / client-directed\s+1\s+0\s+1\s+0\s+1\s+\+100 pts", out), out


@pytest.fixture(scope="module")
def answered(eng, built):
    """Each item on the graph preset, the agent's control and its treatment, asked as its principal: passed or not."""
    agent, unc = Agent(eng, llm=None), uncertified_ids(BUILD)
    out: dict[str, dict[str, bool]] = {}
    answers: dict[tuple[str, str], dict] = {}
    for i in built:
        who = i.get("principal") or "analyst_enterprise"
        got = {"graph": eng.answer(i["question"], who, config=PRESETS["graph"]),
               "control": agent.ask(i["question"], who, arm="control"), "treatment": agent.ask(i["question"], who, arm="treatment")}
        out[i["id"]] = {arm: score(i, a, unc)[0] for arm, a in got.items()}
        answers.update({(i["id"], arm): a for arm, a in got.items()})
    return out, answers


def test_graph_on_answers_what_its_runtime_has_the_semantics_for_aum_among_it(answered):
    passed, answers = answered
    for arm in ("graph", "treatment"):
        got = [i for i in IDS if passed[i][arm]]
        assert set(ANSWERED) <= set(got), (arm, sorted(set(ANSWERED) - set(got)))
        assert not [i for i in AUM_MISSES if passed[i][arm]], (arm, "a count and a share across the certified join: known misses")
        w16, w17 = answers[("W16", arm)], answers[("W17", arm)]
        assert w16["behaviour"] == "figure" and w16.get("figure") is not None, (arm, w16["behaviour"], w16.get("reason"))
        if arm == "graph":
            assert w16["context"]["context"] == "aum" and w16["context"]["fact"] == "wealth.AUM_MNTH_END_FACT_1", w16["context"]
        assert w17["behaviour"] == "refuse" and w17.get("figure") is None, (arm, w17["behaviour"], w17.get("figure"))
    for i in NO_FIGURE:
        for arm in ("graph", "treatment"):
            a = answers[(i, arm)]
            assert a["behaviour"] == "abstain" and a.get("figure") is None and not a.get("sql_run"), (i, arm, a["behaviour"], a.get("figure"))
    off, on = (sum(passed[i][arm] for i in IDS) for arm in ("control", "treatment"))
    assert on > off, "the graph adds on the expansion as on the baseline sets"


def test_w12_answers_june_2025_and_w13_says_july_2025_is_not_yet_released(answered):
    _, answers = answered
    for arm in ("graph", "treatment"):
        assert answers[("W12", arm)].get("months") == ["2025-06-30"], arm
        w13 = answers[("W13", arm)]
        assert w13["behaviour"] == "abstain_with_reason" and "July 2025 is not yet released" in w13["reason"], arm


def test_a_refusal_across_the_units_gives_no_partial_figure_on_any_arm(answered):
    _, answers = answered
    for qid in ("XB1-R", "XB3-W", "XB-DOB", "XB-EMAIL", "XB6", "W19"):
        for arm in ("graph", "treatment"):
            a = answers[(qid, arm)]
            assert a["behaviour"] in ("refuse", "blocked_access") and a.get("figure") is None and not a.get("rows"), (qid, arm)
            assert not isinstance(a.get("parts"), dict), (qid, arm)


def test_the_readme_and_the_makefile_say_what_golden_wealth_v1_holds_and_how_it_runs():
    readme = " ".join((DEMO / "README.md").read_text().split())
    section = readme.split("## The A/B harness", 1)[1].split(" ## ", 1)[0]
    for needed in ("`golden_wealth_v1`", "`kge/estate/golden_wealth.py`", "`build/golden_wealth_v1.json`", "reference SQL", "naive reading",
                   "XB1 as `analyst_retail`", "XB3 as `advisor_wealth`", "$8,000", "Question 2", "Question 1", "Question 23",
                   "twenty-four of the thirty", "W16 and W17",
                   "By category: graph off and graph on", "`.venv/bin/python -m pytest tests/integration/test_golden_wealth.py -q`",
                   "make ab SETS=golden_wealth_v1"):
        assert needed in section, needed
    assert "golden_wealth_v1" in readme.split("## Running it", 1)[1].split(" ## ", 1)[0]
    assert "golden_wealth_v1" in readme.split("## The estate", 1)[1].split(" ## ", 1)[0], "the estate's table names the new set"
    mk = (DEMO / "Makefile").read_text()
    rule = mk.split("\ngolden:", 1)[0].rsplit("\n\n", 1)[1]
    assert "golden_wealth_v1" in rule and "kge/estate/golden_wealth.py" in rule, rule
    ab = mk.split("\nab:", 1)[0].rsplit("\n\n", 1)[1]
    assert "golden_wealth_v1" in ab and "category" in ab, ab


def test_a_build_made_before_the_set_runs_the_others_and_says_where_it_is_when_asked_for(tmp_path, capsys):
    from kge.eval import margin
    from tests.scratch import graph_of, running_as, scratch_build
    build = scratch_build(tmp_path / "build", graph=False)
    (build / "golden_wealth_v1.json").unlink()
    assert harness.choose_sets("all", build) == ["golden", "heldout", "sage_v1"]
    with pytest.raises(SystemExit, match=f"no set 'golden_wealth_v1'.*with its file in {re.escape(str(build))}"):
        harness.choose_sets("golden_wealth_v1", build)
    (build / "heldout.json").unlink()
    with running_as(build):
        assert harness.main(["--repeats", "1", "--sets", "golden,heldout"]) == 2
    assert f"no heldout.json in {build}: make golden writes the question sets" in capsys.readouterr().err
    with running_as(build):
        assert margin.main(["--repeats", "1", "--sets", "golden,heldout", "--out", str(tmp_path / "runs"),
                            "--baseline", str(DEMO / "clients" / "meridian" / "baselines" / "issue-39.json")]) == 2
    err = capsys.readouterr().err
    assert f"no heldout.json in {build}: make golden writes the question sets from the warehouses on disk, then make margin" in err
    assert not (tmp_path / "runs").exists() and not graph_of(build).exists(), "no graph opened, no run written"
