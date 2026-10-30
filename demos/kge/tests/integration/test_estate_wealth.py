"""The Wealth expansion and the certified client cross-reference (#39 item 7b): additive, seeded, and the owner's schema.

    .venv/bin/python -m pytest tests/integration/test_estate_wealth.py -q

kge/estate/generate.py appends the Wealth expansion's tables to the Wealth
warehouse, two counterparty columns to retail.transaction, and the certified
client cross-reference in a warehouse of its own, build/warehouses/xref.duckdb.
Each comes from a stream seeded by the estate's seed and a name of its own,
after every draw the four units take, so no row the estate had moves: make
check-estate (kge/estate/check_estate.py) holds the 35 tables of 4d9d5c9 to their
row checksums, frozen in clients/meridian/baselines/estate-4d9d5c9.json, and the
question sets to #38's A/B baseline's files
(clients/meridian/baselines/issue-38.json).

Since pack 1.5.0 every Wealth table has a physical name: SAGE's seventeen are held as SAGE has them
(kge/estate/sage.py), and Meridian's fifteen kept tables under physical names in SAGE's style (kge/estate/names.py),
their rows unchanged, which check-estate reads under their new names; Meridian's account, flow, month_end, office and
ref_source_system tables are gone, SAGE's ACCT_DIM2_1, TRAN_FACT_1, MONTH_DIM_1, BRCH_DIM_1 and SRCE_SYS_1 standing for
them. So a flow's signed amount is TRAN_AM signed by DW_TRAN_DIRCTN_CD (O negative), a version current while CURR_IN is
1 and open while DW_EXPR_DT is 9999-12-31, a source system SAGE's id (100 BROKERAGE, 200 BANK, as NNA_EXCL_1 names
them), an offset account at this institution one whose OFSET_SRCE_SYS_ID is set (not -1), a month released once it is
no longer SAGE's open month (DW_BRKRG_MNTH_FRM_TODAY_CT not 0) and read as reported at its release date
(FIN_MTRC_RLSE_DT), and whether a client directed a flow is F_NNA_FIN's CLIENT_DIRECTED_NNA_IN (the eligible, current
flows of live accounts only).

The data is shaped for what the owner's golden questions turn on: account
numbers shared by the two Wealth source systems, accounts and flows that keep
their history (SCD2), March 2025 restated after its freeze, July 2025 booked but
not released, effective-dated exclusions, test accounts, an office row closed
before the data window, a segment hierarchy, AUM as the holdings' market value,
and Retail debits that are transfers to the same client's Wealth account, each
mirrored by an inflow that is not new money.

Reads the demo's build (its warehouses, read only, and its set files); writes
only under the test's temporary folder. Opens no graph.

WEALTH_TABLES are the expansion's tables (the #39 Wealth addendum and its Option B update, and SAGE's), under their
physical names. DRAWN are the expansion's tables that come from its streams; the rest are fixed reference rows, or AUM,
derived from the holdings. SAGE_DRAWN are the SAGE tables (kge/estate/sage.py, pack 1.5.0) whose rows or SAGE columns its
seeded stream draws for the drawn accounts, flows and offices; the SAGE reference tables, the calendar and the month
ends stay fixed. BASELINE is #38's.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from kge.estate import check_estate, documents, generate, names, sage, spec
from kge.pipeline.chunking import document_chunks
from tests.scratch import BUILD

DEMO = BUILD.parent
WAREHOUSES = BUILD / "warehouses"
SNAPSHOT = DEMO / "clients" / "meridian" / "baselines" / "estate-4d9d5c9.json"
BASELINE = DEMO / "clients" / "meridian" / "baselines" / "issue-38.json"
FROZEN = json.loads((DEMO / "tests" / "fixtures" / "spec_4d9d5c9.json").read_text())["SCHEMAS"]
KEPT = ["division", "region", "client_segment", "ref_flow_type", "household_member", "nna_exclusion", "aum_month_end"]
WEALTH_TABLES = {names.table(t) for t in KEPT} | set(sage.TABLES.values())
DRAWN = {"wealth.ACCT_DIM2_1", "wealth.TRAN_FACT_1", "wealth.NNA_EXCL_1", "wealth.HSHLD_MBR_1", "xref.client_xref"}
SAGE_DRAWN = {"F_ACCT_SEGMT_ENRLMT", "BANK_DEP_ACCT_ADDR_1", "LGCY_ACCT_1", "CORE_OFFER_ENRLMT_1", "TRAN_FACT_EXCL_MFCLR_G",
              "INS_CONTR_1", "F_NNA_FIN", "BRCH_DIM_1"}
XREF_COLUMNS = ["xref_id", "retail_customer_id", "household_id", "member_id", "source_system", "valid_from", "valid_to",
                "is_current", "match_method", "match_confidence", "certification_status", "certified_by", "certified_on"]
OLD_SENTENCE = "It does not join to any other business unit's warehouse."
FAR = dt.date(9999, 12, 31)
SIGNED = "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"
SYSTEM = "CASE f.ACCT_SRCE_SYS_ID WHEN 100 THEN 'BROKERAGE' WHEN 200 THEN 'BANK' END"


def connect(name: str, folder: Path = WAREHOUSES):
    return duckdb.connect(str(folder / f"{name}.duckdb"), read_only=True)


@pytest.fixture(scope="module")
def wealth():
    con = connect("wealth")
    yield con
    con.close()


@pytest.fixture(scope="module")
def retail():
    con = connect("retail")
    yield con
    con.close()


@pytest.fixture(scope="module")
def xref():
    con = connect("xref")
    yield con
    con.close()


def rows(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def one(con, sql: str):
    return con.execute(sql).fetchone()[0]


def test_the_35_tables_of_4d9d5c9_hold_their_rows_and_the_sets_are_the_baseline_s(capsys):
    """make check-estate: every table of 4d9d5c9, over the columns it had (Wealth's under the physical names they took),
    holds the rows it held; heldout.json and golden v1 (which golden v2 names) are #38's A/B baseline's files; and what
    the expansion added is listed as added."""
    code = check_estate.main(["--build", str(BUILD), "--snapshot", str(SNAPSHOT), "--baseline", str(BASELINE)])
    out = capsys.readouterr().out
    assert code == 0, out
    snap = json.loads(SNAPSHOT.read_text())
    assert len(snap["tables"]) == 35 and sum(t["rows"] for t in snap["tables"].values()) == 39475
    assert "the 35 tables of 4d9d5c9" in out and "39,475 rows" in out and "passed" in out
    assert "heldout.json is the baseline's file" in out and "golden v1, which golden v2 changes from, is the baseline's file" in out
    assert "added since: 25 tables" in out, out
    for t in sorted(WEALTH_TABLES):
        assert f"wealth.{t}" in out, t
    assert "xref.client_xref" in out and "retail.transaction.counterparty_bank, retail.transaction.counterparty_account" in out


def test_the_snapshot_holds_no_row_and_no_hex_digest():
    text = SNAPSHOT.read_text()
    assert not re.search(r"[0-9a-f]{12,}", text.replace("checksum", "")), "no hex digest: the disclosure guard flags them"
    snap = json.loads(text)
    assert set(snap) == {"about", "taken_on", "tables"} and snap["taken_on"].startswith("4d9d5c9")
    for key, t in snap["tables"].items():
        unit, table = key.split(".")
        assert set(t) == {"columns", "rows", "checksum"} and re.fullmatch(r"[a-z2-7]{52}", t["checksum"]), key
        assert t["columns"] == [c[0] for c in FROZEN[unit][table]["columns"]], key


def scratch_estate(dest: Path) -> Path:
    """A copy of the demo's warehouses and set files, to change."""
    (dest / "warehouses").mkdir(parents=True)
    for w in WAREHOUSES.glob("*.duckdb"):
        shutil.copyfile(w, dest / "warehouses" / w.name)
    for name in ("heldout.json", "golden.json"):
        shutil.copyfile(BUILD / name, dest / name)
    return dest


def test_check_estate_names_each_table_that_moved_and_a_set_that_is_not_the_baseline_s(tmp_path, capsys):
    build = scratch_estate(tmp_path / "build")
    con = duckdb.connect(str(build / "warehouses" / "sme.duckdb"))
    con.execute("UPDATE facility SET limit_amount = limit_amount + 1 WHERE facility_id = 1")
    con.close()
    con = duckdb.connect(str(build / "warehouses" / "corporate.duckdb"))
    con.execute("DELETE FROM rating WHERE rating_id = 1")
    con.close()
    (build / "heldout.json").write_text((build / "heldout.json").read_text() + " ")
    code = check_estate.main(["--build", str(build), "--snapshot", str(SNAPSHOT), "--baseline", str(BASELINE)])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "sme.facility: the same" in out and "but a value moved" in out
    assert "corporate.rating: 39 rows, where it had 40" in out
    assert "heldout.json: its sha256 is not the baseline's" in out and "FAILED: 3 moved" in out
    assert "retail.transaction" not in out.split("FAILED")[1], "an appended column is not a move"


def test_check_estate_refuses_to_overwrite_a_snapshot_and_needs_an_estate(tmp_path, capsys):
    assert check_estate.main(["--build", str(BUILD), "--snapshot", str(SNAPSHOT), "--freeze", "x"]) == 2
    assert "a snapshot is frozen" in capsys.readouterr().err
    assert check_estate.main(["--build", str(tmp_path), "--snapshot", str(SNAPSHOT)]) == 2
    assert "make estate first" in capsys.readouterr().err


def test_make_check_estate_runs_it_on_the_profile_s_estate():
    from tests.unit.test_cli import planned
    assert ("kge.estate.check_estate --snapshot clients/meridian/baselines/estate-4d9d5c9.json "
            "--baseline clients/meridian/baselines/issue-38.json") in planned("check-estate")


def unexpand(folder: Path) -> None:
    """Take the expansion back out of a copy of the build's warehouses: its Wealth tables emptied (in the reverse of the
    order their keys need), the counterparty columns nulled, the cross-reference's warehouse gone."""
    con = duckdb.connect(str(folder / "wealth.duckdb"))
    for t in reversed([t for t in spec.SCHEMAS["wealth"] if t in WEALTH_TABLES]):
        con.execute(f"DELETE FROM {t}")
    con.close()
    con = duckdb.connect(str(folder / "retail.duckdb"))
    con.execute("UPDATE transaction SET counterparty_bank = NULL, counterparty_account = NULL WHERE counterparty_bank IS NOT NULL")
    con.close()
    (folder / "xref.duckdb").unlink(missing_ok=True)


def test_the_expansion_comes_from_seeded_streams_of_its_own(tmp_path):
    """The same seed gives every table the build has, byte for byte in its rows; another seed draws other accounts,
    flows, exclusions, members, matches and transfer legs, and leaves the 35 tables of 4d9d5c9 as they were."""
    built = check_estate.checksums(WAREHOUSES)
    folder = tmp_path / "warehouses"
    folder.mkdir()
    for w in ("retail", "wealth"):
        shutil.copyfile(WAREHOUSES / f"{w}.duckdb", folder / f"{w}.duckdb")
    unexpand(folder)
    added = generate.expand(folder, 7)
    again = check_estate.checksums(folder)
    assert again == {k: v for k, v in built.items() if k.split(".")[0] in ("retail", "wealth", "xref")}
    summary = json.loads((BUILD / "summary.json").read_text())
    assert added == {k: summary["curated"][k] for k in ("xref_rows", "transfer_legs")}

    unexpand(folder)
    generate.expand(folder, 8)
    other = check_estate.checksums(folder)
    snap = check_estate.renamed(json.loads(SNAPSHOT.read_text())["tables"], check_estate.physical_names())
    frozen = check_estate.checksums(folder, {k: t["columns"] for k, t in snap.items() if k.split(".")[0] in ("retail", "wealth")})
    assert all(frozen[k]["checksum"] == snap[k]["checksum"] for k in frozen) and len(frozen) == 18
    moved = {k for k in other if other[k]["checksum"] != again[k]["checksum"]}
    assert SAGE_DRAWN <= set(sage.TABLES.values())
    assert moved == DRAWN | {"retail.transaction"} | {f"wealth.{t}" for t in SAGE_DRAWN}, \
        "only what the expansion draws moves with its seed: the counterparty legs on Retail, and the SAGE rows and columns drawn for its accounts and offices"


def test_the_expansion_draws_nothing_from_the_master_stream_or_a_unit_s():
    src = (DEMO / "kge" / "estate" / "generate.py").read_text()
    body = src[src.index("def expand("):src.index("def write_glossary_layer(")]
    assert body.count("random.Random(f\"{seed}:") == 4 and "rng.randint(0, 10**9)" not in body
    main = src[src.index("def main()"):]
    assert main.index("POPULATE[unit]") < main.index("expand(estate") < main.index("write_documents(")


def test_the_owner_s_tables_are_appended_to_wealth_and_two_columns_to_retail_transaction():
    """4d9d5c9's eight Wealth tables first, each under its physical name with its columns and keys renamed and nothing
    else; then the expansion's, SAGE's seventeen as sage.json names them and Meridian's kept ones under physical names."""
    old = [names.table(t) for t in FROZEN["wealth"]]
    now = list(spec.SCHEMAS["wealth"])
    assert now[:len(old)] == old and set(now[len(old):]) == WEALTH_TABLES
    for unit in FROZEN:
        for t, table in FROZEN[unit].items():
            physical = names.table(t) if unit == "wealth" else t
            cols = [list(c) for c in spec.SCHEMAS[unit][physical]["columns"]]
            want = [[names.column(t, c[0]), *c[1:]] for c in table["columns"]] if unit == "wealth" else table["columns"]
            assert cols[:len(want)] == want, f"{unit}.{t}"
            extra = [c[0] for c in cols[len(want):]]
            assert extra == (["counterparty_bank", "counterparty_account"] if (unit, t) == ("retail", "transaction") else []), f"{unit}.{t}"
    cols = lambda t: [c[0] for c in spec.SCHEMAS["wealth"][t]["columns"]]  # noqa: E731
    pk = lambda t: [c.strip() for c in spec.SCHEMAS["wealth"][t]["pk"].split(",")]  # noqa: E731
    for business, physical in sage.TABLES.items():
        assert cols(physical) == [f["name"] for f in sage.fields(business)], physical
    assert pk("ACCT_DIM2_1") == ["DW_ACCT_ID", "SRCE_SYS_ID", "DW_EFF_DT"] and pk("TRAN_FACT_1") == ["DW_TRAN_ID", "DW_EFF_DT"]
    assert {"HSHLD_DK", "EFF_CLNT_CD", "TEST_ACCT_IN"} <= set(cols("ACCT_DIM2_1"))
    assert {"DW_TRAN_ID", "DW_ACCT_ID", "ACCT_SRCE_SYS_ID", "DW_TRAN_DT", "DW_TRAN_TYPE_CD", "TRAN_AM", "DW_TRAN_DIRCTN_CD", "NNA_IN",
            "OFSET_SRCE_SYS_ID", "OFSET_ACCT_ID"} <= set(cols("TRAN_FACT_1"))
    for t in ("ACCT_DIM2_1", "TRAN_FACT_1", "BRCH_DIM_1", "RGN_DIM_1", "DIVSN_DIM_1"):
        assert {"DW_EFF_DT", "DW_EXPR_DT", "CURR_IN"} <= set(cols(t)), t
    assert {"CLDR_EOM_DT", "FIN_MTRC_RLSE_DT", "DW_BRKRG_MNTH_FRM_TODAY_CT"} <= set(cols("MONTH_DIM_1"))
    assert {"EXCL_EFF_DT", "EXCL_EXPR_DT", "EXCL_RSN_CD"} <= set(cols("NNA_EXCL_1"))
    assert cols("CLNT_SEGMT_DIM_1")[2:] == ["PARNT_SEGMT_CD", "SEGMT_LVL_NR"] and "NNA_CNT_IN" in cols("TRAN_TYPE_1")
    assert cols("HSHLD_MBR_1") == ["HSHLD_DK", "MBR_ID", "MBR_ROLE_CD"]
    assert cols("AUM_MNTH_END_FACT_1") == ["HSHLD_DK", "CLDR_EOM_DT", "AUM_AM"]
    assert not {"account", "flow", "month_end", "office", "ref_source_system"} & set(now)


def test_no_foreign_key_is_declared_where_the_key_is_not_unique():
    """Flow and exclusion to account, advisor to office and up the hierarchy: SCD2 keys, so curated joins (the pack's)."""
    fks = {(t, c, rt) for t, table in spec.SCHEMAS["wealth"].items() for c, rt, _ in table["fks"]}
    for t, rt in [("TRAN_FACT_1", "ACCT_DIM2_1"), ("NNA_EXCL_1", "ACCT_DIM2_1"), ("ADV_DIM_1", "BRCH_DIM_1"), ("BRCH_DIM_1", "RGN_DIM_1"),
                  ("RGN_DIM_1", "DIVSN_DIM_1")]:
        assert not any(f[0] == t and f[2] == rt for f in fks), (t, rt)
    unique = {t: [c.strip() for c in table["pk"].split(",")] for t, table in spec.SCHEMAS["wealth"].items()}
    for t, c, rt in fks:
        assert len(unique[rt]) == 1, f"{t}.{c} references {rt}, whose key is not one column"
    joined = {rt for t, _, rt in fks if t in WEALTH_TABLES and rt not in WEALTH_TABLES}
    assert joined == {"HSHLD_DIM_1"}, "the expansion hangs off one table the estate had, so it opens no new path between two of them"


def test_sage_tables_have_rows_and_keep_their_join_keys(wealth):
    assert len(sage.REFERENCE) == 17 and set(sage.KEYS) == set(sage.REFERENCE)
    assert all(one(wealth, f"SELECT count(*) FROM {table}") > 0 for table in sage.TABLES.values())
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f LEFT JOIN MONTH_DIM_1 m ON f.MNTH_END_DT = m.CLDR_EOM_DT "
                       "WHERE m.CLDR_EOM_DT IS NULL") == 0
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f LEFT JOIN ACCT_DIM2_1 a ON f.DW_ACCT_DK = a.DW_ACCT_DK "
                       "WHERE a.DW_ACCT_DK IS NULL") == 0
    assert one(wealth, "SELECT count(*) FROM ACCT_DIM2_1 a LEFT JOIN CLIENT_CD_ROLLUP_1 c ON a.CLNT_CD = c.CLNT_CD "
                       "WHERE c.CLNT_CD IS NULL") == 0
    assert one(wealth, "SELECT count(*) FROM ACCT_DIM2_1 a WHERE NOT EXISTS "
                       "(SELECT 1 FROM BRCH_DIM_1 o WHERE a.ACCT_DOMCL_BRCH_CD = o.BRCH_CD)") == 0
    assert one(wealth, "SELECT count(*) FROM F_ACCT_SEGMT_ENRLMT e LEFT JOIN D_ACCT_SEGMT s "
                       "ON e.DW_ACCT_SEGMT_ID = s.DW_ACCT_SEGMT_ID WHERE s.DW_ACCT_SEGMT_ID IS NULL") == 0
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_EXCL_MFCLR_G") == one(wealth, "SELECT count(*) FROM TRAN_FACT_1")
    assert sage.ALIASES["flow"]["amount_usd"] == "TRAN_AM"
    assert sage.ALIASES["flow"]["nna_eligible_flag"] == "NNA_IN"
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN TRAN_FACT_EXCL_MFCLR_G x "
                       "ON x.DW_TRAN_ID = f.DW_TRAN_ID AND x.DW_EFF_DT = f.DW_EFF_DT "
                       "WHERE x.TRAN_AM <> f.TRAN_AM OR x.NNA_IN <> f.NNA_IN") == 0


def test_the_cross_reference_is_in_a_warehouse_of_its_own_and_is_no_unit(xref):
    assert list(spec.CURATED_SCHEMAS) == ["xref"] and "xref" not in spec.UNITS and "xref" not in spec.SCHEMAS
    assert [c[0] for c in spec.CURATED_SCHEMAS["xref"]["client_xref"]["columns"]] == XREF_COLUMNS
    assert [r[0] for r in rows(xref, "SELECT column_name FROM duckdb_columns() WHERE table_name = 'client_xref' ORDER BY column_index")] == XREF_COLUMNS
    assert not set(XREF_COLUMNS) & {c for _, _, c in spec.RESTRICTED_COLUMNS}, "no personal data, and no match key a gate refuses"
    assert sorted(p.stem for p in WAREHOUSES.glob("*.duckdb")) == sorted([*spec.UNITS, "xref"])


def test_an_account_number_stands_on_both_source_systems(wealth):
    shared = one(wealth, "SELECT count(*) FROM (SELECT DW_ACCT_ID FROM ACCT_DIM2_1 WHERE CURR_IN = 1 GROUP BY 1 "
                         "HAVING count(DISTINCT SRCE_SYS_ID) = 2)")
    assert shared > 20
    on_one = one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN ACCT_DIM2_1 a ON a.DW_ACCT_ID = f.DW_ACCT_ID AND a.CURR_IN = 1 "
                         "WHERE f.CURR_IN = 1")
    on_both = one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN ACCT_DIM2_1 a ON a.DW_ACCT_ID = f.DW_ACCT_ID "
                          "AND a.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID AND a.CURR_IN = 1 WHERE f.CURR_IN = 1")
    assert on_one > on_both, "a join on DW_ACCT_ID alone counts a flow twice"


def chained(versions: list[tuple]) -> bool:
    """One key's versions, by valid_from: each closes the day before the next opens, and only the last may be current;
    a version still open ends on no day (null) or on SAGE's 9999-12-31."""
    for a, b in zip(versions, versions[1:]):
        if a[1] in (None, FAR) or a[1] + dt.timedelta(days=1) != b[0] or a[2]:
            return False
    return bool(versions[-1][2]) == (versions[-1][1] in (None, FAR))


def test_accounts_keep_their_history_and_household_5001_had_another_account_at_the_end_of_2024(wealth):
    keyed: dict[tuple, list] = {}
    for a, s, f, t, cur in rows(wealth, "SELECT DW_ACCT_ID, SRCE_SYS_ID, DW_EFF_DT, DW_EXPR_DT, CURR_IN FROM ACCT_DIM2_1 ORDER BY 1, 2, 3"):
        keyed.setdefault((a, s), []).append((f, t, cur))
    assert all(chained(v) for v in keyed.values())
    assert sum(len(v) > 1 for v in keyed.values()) >= 15 and sum(not v[-1][2] for v in keyed.values()) >= 5, "re-segmented, moved, closed"
    now = rows(wealth, "SELECT DW_ACCT_ID, SRCE_SYS_ID FROM ACCT_DIM2_1 WHERE HSHLD_DK = 5001 AND CURR_IN = 1 ORDER BY 2, 1")
    then = rows(wealth, "SELECT DW_ACCT_ID, SRCE_SYS_ID FROM ACCT_DIM2_1 WHERE HSHLD_DK = 5001 "
                        "AND DATE '2024-12-31' BETWEEN DW_EFF_DT AND DW_EXPR_DT ORDER BY 2, 1")
    assert len(then) == len(now) + 1 and set(now) < set(then)
    tests = rows(wealth, "SELECT SRCE_SYS_ID, count(*) FROM ACCT_DIM2_1 WHERE TEST_ACCT_IN = 'Y' AND CURR_IN = 1 GROUP BY 1 ORDER BY 1")
    assert tests == [(sage.SOURCE_CODES["BROKERAGE"], 3), (sage.SOURCE_CODES["BANK"], 3)]
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN ACCT_DIM2_1 a ON a.DW_ACCT_ID = f.DW_ACCT_ID "
                       "AND a.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID AND a.CURR_IN = 1 WHERE a.TEST_ACCT_IN = 'Y' AND f.NNA_IN = 'Y' "
                       "AND f.DW_TRAN_DT >= DATE '2025-04-01'") > 0


def test_flows_are_signed_by_direction_booked_while_open_and_eligible_only_where_their_type_counts(wealth):
    assert rows(wealth, f"SELECT count(*) FROM TRAN_FACT_1 f JOIN TRAN_TYPE_1 t ON t.DW_TRAN_TYPE_CD = f.DW_TRAN_TYPE_CD "
                        f"WHERE (t.DIRCTN_CD = 'IN') <> ({SIGNED} > 0)") == [(0,)]
    assert rows(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN TRAN_TYPE_1 t ON t.DW_TRAN_TYPE_CD = f.DW_TRAN_TYPE_CD "
                        "WHERE f.NNA_IN = 'Y' AND NOT t.NNA_CNT_IN") == [(0,)]
    (first, last), = rows(wealth, "SELECT min(DW_TRAN_DT), max(DW_TRAN_DT) FROM TRAN_FACT_1")
    assert dt.date(2024, 7, 1) <= first and last <= generate.BOOKED_THROUGH, "from July 2024 to the open month, as far as it is booked"
    open_on = one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f WHERE NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 a WHERE a.DW_ACCT_ID = f.DW_ACCT_ID "
                          "AND a.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID AND f.DW_TRAN_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT)")
    assert open_on == 0, "a flow is booked to an account open on its day"
    assert rows(wealth, "SELECT count(*) FROM (SELECT DW_TRAN_ID FROM TRAN_FACT_1 WHERE CURR_IN = 1 GROUP BY 1 HAVING count(*) > 1)") == [(0,)]
    assert one(wealth, "SELECT count(*) FROM F_NNA_FIN WHERE CLIENT_DIRECTED_NNA_IN = 'N' AND DW_TRAN_DT "
                       "BETWEEN DATE '2025-06-01' AND DATE '2025-06-30'") > 0, "client-directed NNA is a part of NNA"
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN TRAN_TYPE_1 t USING (DW_TRAN_TYPE_CD) WHERE t.NNA_CNT_IN "
                       "AND f.NNA_IN = 'N'") > 0


def test_march_2025_is_restated_after_its_freeze_so_as_reported_and_restated_differ(wealth):
    """SAGE records no freeze date: a month as reported is read as it stood on its release date (FIN_MTRC_RLSE_DT)."""
    freeze = one(wealth, "SELECT FIN_MTRC_RLSE_DT FROM MONTH_DIM_1 WHERE CLDR_EOM_DT = DATE '2025-03-31'")
    restated = rows(wealth, "SELECT DISTINCT strftime(DW_TRAN_DT, '%Y-%m'), DW_EFF_DT FROM TRAN_FACT_1 WHERE DW_EFF_DT <> DW_TRAN_DT")
    assert len(restated) == 1 and restated[0][0] == "2025-03" and restated[0][1] > freeze
    as_reported = one(wealth, f"SELECT sum({SIGNED}) FROM TRAN_FACT_1 f WHERE NNA_IN = 'Y' AND DW_TRAN_DT BETWEEN DATE '2025-03-01' "
                              f"AND DATE '2025-03-31' AND DW_EFF_DT <= DATE '{freeze}' AND DW_EXPR_DT > DATE '{freeze}'")
    now = one(wealth, f"SELECT sum({SIGNED}) FROM TRAN_FACT_1 f WHERE CURR_IN = 1 AND NNA_IN = 'Y' AND DW_TRAN_DT "
                      "BETWEEN DATE '2025-03-01' AND DATE '2025-03-31'")
    assert as_reported != now
    keyed: dict[int, list] = {}
    for f, a, b, cur in rows(wealth, "SELECT DW_TRAN_ID, DW_EFF_DT, DW_EXPR_DT, CURR_IN FROM TRAN_FACT_1 ORDER BY 1, 2"):
        keyed.setdefault(f, []).append((a, b, cur))
    assert all(chained(v) for v in keyed.values()) and sum(len(v) == 2 for v in keyed.values()) == 12


def test_the_month_end_driver_releases_june_2025_last_and_july_2025_is_booked_but_not_released(wealth):
    """The release driver is SAGE's MONTH_DIM_1: a month is released once it is no longer SAGE's open brokerage month
    (DW_BRKRG_MNTH_FRM_TODAY_CT, 0 for the open month), its figures on FIN_MTRC_RLSE_DT. SAGE's month holds no fiscal
    quarter: each month falls in one quarter of the fiscal calendar (FISCAL_QTR_DIM_1), and July 2025, past the calendar,
    in none it holds."""
    months = rows(wealth, "SELECT CLDR_EOM_DT, DW_BRKRG_MNTH_FRM_TODAY_CT, FIN_MTRC_RLSE_DT FROM MONTH_DIM_1 ORDER BY 1")
    assert [m[0] for m in months] == [generate.month_end(m) for m in generate.MONTHS] + [dt.date(2025, 7, 31)]
    assert [m[0] for m in months if m[1] == 0] == [dt.date(2025, 7, 31)]
    assert all(m[0] < m[2] for m in months)
    fiscal = rows(wealth, "SELECT FISCAL_QTR_CD, QRTR_START_CLDR_DT, QRTR_END_CLDR_DT FROM FISCAL_QTR_DIM_1")
    for m in months[:12]:
        assert len([q for q, a, b in fiscal if a <= m[0] <= b]) == 1, m
    assert not [q for q, a, b in fiscal if a <= months[-1][0] <= b]
    assert one(wealth, "SELECT max(CLDR_EOM_DT) FROM MONTH_DIM_1 WHERE DW_BRKRG_MNTH_FRM_TODAY_CT <> 0") == dt.date(2025, 6, 30)
    assert one(wealth, "SELECT max(DW_TRAN_DT) FROM TRAN_FACT_1") > dt.date(2025, 7, 1), "the latest booked month is not the latest released"


def test_one_office_row_closed_before_the_data_window_so_a_join_without_is_current_counts_twice(wealth):
    offices = {r[0] for r in rows(wealth, "SELECT DISTINCT BRCH_CD FROM ADV_DIM_1")}
    assert rows(wealth, "SELECT BRCH_CD, count(*) FROM BRCH_DIM_1 WHERE CURR_IN = 1 GROUP BY 1 ORDER BY 1") == [(o, 1) for o in sorted(offices)]
    closed = rows(wealth, "SELECT BRCH_CD, DW_EXPR_DT FROM BRCH_DIM_1 WHERE CURR_IN = 0")
    assert len(closed) == 1 and closed[0][1] < dt.date(2024, 7, 1)
    twice = one(wealth, "SELECT count(*) FROM ADV_DIM_1 a JOIN BRCH_DIM_1 o ON o.BRCH_CD = a.BRCH_CD")
    once = one(wealth, "SELECT count(*) FROM ADV_DIM_1 a JOIN BRCH_DIM_1 o ON o.BRCH_CD = a.BRCH_CD AND o.CURR_IN = 1")
    assert once == one(wealth, "SELECT count(*) FROM ADV_DIM_1") < twice
    up = rows(wealth, "SELECT DISTINCT d.DIVSN_CD FROM BRCH_DIM_1 o JOIN RGN_DIM_1 r ON r.RGN_CD = o.RGN_CD AND r.CURR_IN "
                      "JOIN DIVSN_DIM_1 d ON d.DIVSN_CD = r.DIVSN_CD AND d.CURR_IN WHERE o.CURR_IN = 1 ORDER BY 1")
    assert len(up) == 2


def test_segments_roll_up_one_level_and_accounts_sit_on_the_lower_level(wealth):
    """An account's segment is its effective client code's level 6 in SAGE's CLIENT_CD_ROLLUP_1."""
    seg = {s: (p, lvl) for s, p, lvl in rows(wealth, "SELECT SEGMT_CD, PARNT_SEGMT_CD, SEGMT_LVL_NR FROM CLNT_SEGMT_DIM_1")}
    assert all((p is None) == (lvl == 1) and (p is None or seg[p][1] == 1) for p, lvl in seg.values())
    used = {r[0] for r in rows(wealth, "SELECT DISTINCT c.SEGMT_LVL_ID6 FROM ACCT_DIM2_1 a JOIN CLIENT_CD_ROLLUP_1 c ON c.CLNT_CD = a.EFF_CLNT_CD")}
    assert used and all(seg[s][1] == 2 for s in used)
    assert one(wealth, "SELECT count(DISTINCT PARNT_SEGMT_CD) FROM CLNT_SEGMT_DIM_1") == 2


def test_exclusions_are_effective_dated_and_keyed_on_both_the_account_and_its_source_system(wealth):
    ex = rows(wealth, "SELECT DW_ACCT_ID, SRCE_SYS_CD, EXCL_EFF_DT, EXCL_EXPR_DT FROM NNA_EXCL_1")
    at = dt.date(2025, 3, 31)
    in_force = [e for e in ex if e[2] <= at <= (e[3] or FAR)]
    assert 0 < len(in_force) < len(ex), "some exclusions are not in force on 31 March 2025"
    for acct, system, a, b in ex:
        quarters = {r[0] for r in rows(wealth, f"SELECT DISTINCT q.FISCAL_QTR_CD FROM TRAN_FACT_1 f JOIN FISCAL_QTR_DIM_1 q ON f.DW_TRAN_DT "
                                               f"BETWEEN q.QRTR_START_CLDR_DT AND q.QRTR_END_CLDR_DT WHERE f.CURR_IN = 1 AND f.NNA_IN = 'Y' "
                                               f"AND f.DW_ACCT_ID = {acct} AND {SYSTEM} = '{system}'")}
        assert {"FY25-Q4", "FY26-Q1"} <= quarters, (acct, system)
    hit = one(wealth, f"SELECT count(*) FROM TRAN_FACT_1 f JOIN NNA_EXCL_1 x ON x.DW_ACCT_ID = f.DW_ACCT_ID AND x.SRCE_SYS_CD <> {SYSTEM} "
                      "WHERE f.CURR_IN = 1 AND f.NNA_IN = 'Y'")
    assert hit > 0, "an anti-join on the account number alone would also drop another system's account"


def test_aum_is_the_households_holdings_at_market_value_and_differs_from_aua(wealth):
    assert rows(wealth, "SELECT count(*) FROM AUM_MNTH_END_FACT_1 a FULL JOIN (SELECT p.HSHLD_DK, h.AS_OF_DT, sum(h.MKT_VAL_AM) AS v "
                        "FROM HLDG_FACT_1 h JOIN PRTFL_DIM_1 p ON p.PRTFL_ID = h.PRTFL_ID GROUP BY ALL) x "
                        "ON x.HSHLD_DK = a.HSHLD_DK AND x.AS_OF_DT = a.CLDR_EOM_DT WHERE x.v IS DISTINCT FROM a.AUM_AM") == [(0,)]
    dates = [r[0] for r in rows(wealth, "SELECT DISTINCT CLDR_EOM_DT FROM AUM_MNTH_END_FACT_1 ORDER BY 1")]
    assert dates == [e for _, _, _, e in generate.FISCAL]
    aum = one(wealth, "SELECT AUM_AM FROM AUM_MNTH_END_FACT_1 WHERE HSHLD_DK = 5001 AND CLDR_EOM_DT = DATE '2025-03-31'")
    aua = one(wealth, "SELECT AUA_AM FROM AUA_FACT_1 WHERE HSHLD_DK = 5001 AND AS_OF_DT = DATE '2025-03-31'")
    assert aum and aua and aum != aua


def test_each_household_has_one_primary_member(wealth):
    assert rows(wealth, "SELECT count(*) FROM HSHLD_DIM_1 h WHERE (SELECT count(*) FROM HSHLD_MBR_1 m WHERE m.HSHLD_DK = h.HSHLD_DK "
                        "AND m.MBR_ROLE_CD = 'PRIMARY') <> 1") == [(0,)]
    assert {r[0] for r in rows(wealth, "SELECT DISTINCT MBR_ROLE_CD FROM HSHLD_MBR_1")} == {"PRIMARY", "JOINT", "DEPENDENT"}
    assert one(wealth, "SELECT count(*) - count(DISTINCT MBR_ID) FROM HSHLD_MBR_1") == 0


def test_the_cross_reference_is_mostly_certified_with_proposals_rejections_and_ended_matches(xref, retail, wealth):
    status = dict(rows(xref, "SELECT certification_status, count(*) FROM client_xref WHERE is_current GROUP BY 1"))
    assert status["CERTIFIED"] > 2 * (status["PROPOSED"] + status["REJECTED"]) and status["PROPOSED"] and status["REJECTED"]
    assert one(xref, "SELECT count(*) FROM client_xref WHERE NOT is_current") >= 3
    keyed: dict[int, list] = {}
    for x, a, b, cur in rows(xref, "SELECT xref_id, valid_from, valid_to, is_current FROM client_xref ORDER BY 1, 2"):
        keyed.setdefault(x, []).append((a, b, cur))
    assert all(chained(v) or not v[-1][2] for v in keyed.values())
    customers = {r[0] for r in rows(retail, "SELECT customer_id FROM customer")}
    members = set(rows(wealth, "SELECT HSHLD_DK, MBR_ID FROM HSHLD_MBR_1"))
    for c, h, m, method, conf, st, by, on in rows(xref, "SELECT retail_customer_id, household_id, member_id, match_method, match_confidence, "
                                                        "certification_status, certified_by, certified_on FROM client_xref"):
        assert c in customers and (h, m) in members
        assert method in ("DETERMINISTIC", "PROBABILISTIC", "MANUAL") and 0 <= conf <= 1
        assert (st == "CERTIFIED") == (by is not None) == (on is not None)
    floor = one(xref, "SELECT min(match_confidence) FROM client_xref WHERE certification_status = 'CERTIFIED'")
    assert one(xref, "SELECT max(match_confidence) FROM client_xref WHERE certification_status <> 'CERTIFIED'") < floor


def test_some_high_june_balance_retail_customers_are_certified_matches(retail, xref):
    """XB5 has an answer at the threshold Question 2's default restates it to, $8,000 of average June 2025 balance."""
    high = {r[0] for r in rows(retail, "SELECT a.customer_id FROM daily_balance b JOIN account a USING (account_id) "
                                       "WHERE b.as_of BETWEEN DATE '2025-06-01' AND DATE '2025-06-30' GROUP BY 1 HAVING avg(b.ledger_balance) > 8000")}
    assert 10 <= len(high) <= 40
    matched = {r[0] for r in rows(xref, "SELECT retail_customer_id FROM client_xref WHERE certification_status = 'CERTIFIED' AND is_current")}
    assert 0 < len(high & matched) < len(high)


def test_transfer_legs_are_existing_retail_debits_each_mirrored_by_a_wealth_inflow_that_is_not_new_money(retail, wealth, xref):
    """Each leg is a debit the estate had (no Retail row added), its counterparty this institution and the Wealth account;
    its mirror is an INT_TRF inflow into that account, of the same client by the cross-reference, on the same day, for
    the same money, its offset account the Retail one and at this institution (OFSET_SRCE_SYS_ID set). The mirror is not
    eligible and its type does not count: NNA's eligibility leaves it out on Wealth data alone, with no Retail read and
    no word of it in a question."""
    snap = json.loads(SNAPSHOT.read_text())["tables"]
    assert one(retail, "SELECT count(*) FROM transaction") == snap["retail.transaction"]["rows"]
    legs = rows(retail, "SELECT t.txn_id, t.account_id, a.customer_id, t.posted_on, t.amount, t.txn_type, t.counterparty_bank, t.counterparty_account "
                        "FROM transaction t JOIN account a ON a.account_id = t.account_id WHERE t.counterparty_bank IS NOT NULL OR "
                        "t.counterparty_account IS NOT NULL ORDER BY 1")
    assert 40 <= len(legs) <= 80
    mirrors = rows(wealth, f"SELECT OFSET_ACCT_ID, DW_TRAN_DT, {SIGNED}, DW_ACCT_ID, ACCT_SRCE_SYS_ID, NNA_IN, CURR_IN "
                           "FROM TRAN_FACT_1 f WHERE DW_TRAN_TYPE_CD = 'INT_TRF' ORDER BY DW_TRAN_ID")
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 WHERE OFSET_SRCE_SYS_ID <> -1") == len(mirrors) == len(legs)
    matches = rows(xref, "SELECT retail_customer_id, household_id, certification_status, valid_from, coalesce(valid_to, DATE '9999-12-31') FROM client_xref")
    by = {}
    for m in mirrors:
        by.setdefault((m[0], m[1], -m[2]), []).append(m)
    statuses = []
    for txn, account, customer, day, amount, kind, bank, cp in legs:
        assert kind == "DEBIT" and amount < 0 and bank == "MERIDIAN"
        (m,) = by[(account, day, amount)]
        assert m[3] == cp and m[5] == "N" and m[6] == 1
        owner = one(wealth, f"SELECT HSHLD_DK FROM ACCT_DIM2_1 WHERE DW_ACCT_ID = {m[3]} AND SRCE_SYS_ID = {m[4]} "
                            f"AND DATE '{day}' BETWEEN DW_EFF_DT AND DW_EXPR_DT")
        st = [s for c, h, s, a, b in matches if c == customer and h == owner and a <= day <= b]
        assert st and st[0] in ("CERTIFIED", "PROPOSED"), txn
        statuses.append(st[0])
    assert statuses.count("PROPOSED") <= 3 < statuses.count("CERTIFIED")
    assert one(wealth, "SELECT count(*) FROM TRAN_FACT_1 f JOIN TRAN_TYPE_1 t USING (DW_TRAN_TYPE_CD) WHERE f.OFSET_SRCE_SYS_ID <> -1 "
                       "AND (f.NNA_IN = 'Y' OR t.NNA_CNT_IN)") == 0, "an internal move is not new money"
    assert sum(dt.date(2025, 4, 1) <= leg[3] <= dt.date(2025, 6, 30) for leg in legs) > 0, "FY26-Q1 has internal moves to classify"


def landed_text(doc_id: str) -> str:
    docs = {d["doc_id"]: d for d in json.loads((BUILD / "documents.json").read_text())}
    return (BUILD / docs[doc_id]["path"]).read_text()


def old_dictionary(unit: str) -> str:
    """The unit's data dictionary as 4d9d5c9 wrote it: its tables then, and no word of a cross-reference."""
    doc = next(d for d in documents.data_dictionaries() if d.unit == unit)
    old = documents.Doc(doc.shelf, doc.doc_id, doc.title, doc.kind, doc.unit, documents.dictionary_body(unit, FROZEN[unit], OLD_SENTENCE),
                        doc.owner, doc.version, doc.published_at)
    return old.text()


@pytest.mark.parametrize("unit", list(FROZEN))
def test_each_dictionary_keeps_every_chunk_it_had_but_the_first_where_the_cross_reference_is_named(unit):
    """Retail's and Wealth's dictionaries say, in their first chunk, that they join only by the certified client
    cross-reference, in a sentence the chunk has room for, so every later chunk keeps its text. Wealth's new sections
    follow all it had, opened by a paragraph that names the expansion's tables and the cross-reference, and Retail's
    closes on a note on its transfer legs: each starts a chunk of its own, in names rather than prose. SME's and
    Corporate's are as they were. The counterparty columns are in Retail's transaction table, as every column is in
    its table. Wealth's names Meridian's tables by their business names, as it did (spec.DOCUMENTED), and its lead
    points to the data catalogue for their physical names since pack 1.5.0."""
    doc = next(d for d in documents.data_dictionaries() if d.unit == unit)
    text = landed_text(doc.doc_id)
    now, then = document_chunks(doc.title, text), document_chunks(doc.title, old_dictionary(unit))
    assert [i for i, (a, b) in enumerate(zip(now, then)) if a != b] == ([0] if unit in ("retail", "wealth") else [])
    added = now[len(then):]
    if unit == "retail":
        assert len(added) == 1 and added[0].startswith("Transfer legs:") and "`client_xref`" in added[0] and "counterparty_bank`" in added[0]
        assert "| counterparty_bank | VARCHAR |" in text and "| counterparty_account | BIGINT |" in text
    elif unit == "wealth":
        assert added[0].startswith("Expansion tables:") and "`client_xref`" in added[0]
        assert "Physical names since 1.5.0: the data catalogue's." in added[0]
        assert [h[3:] for c in added for h in c.split("\n\n") if h.startswith("## ")] == list(spec.DOCUMENTED)[len(FROZEN["wealth"]):]
    else:
        assert added == []
    first = now[0]
    if unit in ("retail", "wealth"):
        assert documents.XREF_SENTENCE in first and OLD_SENTENCE not in first and "certified client cross-reference" in first
    else:
        assert OLD_SENTENCE in first


def test_no_document_is_added():
    docs = json.loads((BUILD / "documents.json").read_text())
    assert (sum(d["shelf"] == "certified" for d in docs), sum(d["shelf"] == "uncertified" for d in docs)) == (28, 40)


def test_the_readme_and_the_makefile_name_check_estate_and_the_expansion():
    readme = (DEMO / "README.md").read_text()
    make = (DEMO / "Makefile").read_text()
    assert "check-estate:" in make and "estate-4d9d5c9.json" in make and "check-estate" in make.split(".PHONY:")[1].splitlines()[0]
    assert "make check-estate" in readme and "kge/estate/check_estate.py" in readme and "xref.duckdb" in readme
    assert "pytest tests/integration/test_estate_wealth.py -q" in readme


def test_the_generator_runs_from_the_command_line_on_its_documented_flag():
    r = subprocess.run([sys.executable, "-m", "kge.estate.generate", "--help"], capture_output=True, text=True, cwd=DEMO)
    assert r.returncode == 0 and "xref.duckdb" in r.stdout
