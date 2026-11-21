"""Five rules-path questions that answered 500 in the owner's sandbox (PR #489) answer over /ask with figures that
reconcile with sample-warehouse SQL written here.

The 500 was coverage() shadowing the module function operations() with a comprehension variable, which some CPython
3.12 builds (the image's among them) make a local of the whole function; tests/unit/test_no_shadowed_module_calls.py
holds the static check that fails on that code under any interpreter. This file pins the five answers themselves.
Each reference reads the answer's own declared window (its measure column's lineage) and asserts that window too.
"""
from decimal import Decimal

import pytest

from kge.estate.golden import warehouses
from tests.integration.test_answer_text import client as client
from tests.scratch import BUILD

WHO = "knowledge_engineer.meridian@example.com"
FLOW = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT = m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID AND a.CURR_IN = 1
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD = b.BRCH_CD AND b.CURR_IN = 1
WHERE f.NNA_IN = 'Y' AND f.RCV_IN = 1 AND m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0
AND f.DW_TRAN_DT >= DATE '{first}' AND f.DW_TRAN_DT < DATE '{last}'"""
UNJOINED = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT = m.CLDR_EOM_DT
WHERE f.NNA_IN = 'Y' AND f.RCV_IN = 1 AND m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0
AND f.DW_TRAN_DT >= DATE '{first}' AND f.DW_TRAN_DT < DATE '{last}'"""
CURRENT = " AND f.CURR_IN = 1"
REPORTED = " AND f.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT AND COALESCE(f.DW_EXPR_DT, DATE '9999-12-31') >= m.FIN_MTRC_RLSE_DT"
CENT = Decimal("0.005")


@pytest.fixture(scope="module")
def warehouse():
    session = warehouses(BUILD, "meridian").open("wealth")
    yield session
    session.close()


def ask(client, question):
    response = client.post("/ask", json={"question": question}, headers=client.as_(WHO))
    assert response.status_code == 200, response.text[:2000]
    answer = response.json()
    assert answer["phrased_by"] == "template" and answer["guard"]["passed"], answer.get("reason")
    assert answer["behaviour"] in ("figure", "combined"), answer.get("reason")
    return answer


def window(answer):
    """The window the answer declares: its first measure column's lineage ends with [first, last)."""
    column = next(c for c in answer["result"]["columns"] if c["kind"] == "measure")
    return column["lineage"][-2], column["lineage"][-1]


def cells(row):
    return [Decimal(cell["value"]["value"]) for cell in row["cells"]]


def rows(warehouse, sql):
    return [tuple(Decimal(str(v)) if isinstance(v, (int, float, Decimal)) else v for v in row)
            for row in warehouse.execute(sql).fetchall()]


def close(actual, expected):
    assert abs(actual - expected) <= CENT, (actual, expected)


def test_region_or_flag_filter(client, warehouse):
    answer = ask(client, "NNA for Jan 2025 for Northeast region or SPS accounts")
    first, last = window(answer)
    assert (first, last) == ("2025-01-01", "2025-02-01")
    [(expected,)] = rows(warehouse, f"SELECT COALESCE(SUM(f.TRAN_AM), 0) {FLOW.format(first=first, last=last)}{CURRENT}"
                                    " AND (b.RGN_NM = 'NORTHEAST REGION' OR a.ACCT_RELT_SPS_IN = 'Y')")
    [row] = answer["result"]["rows"]
    close(cells(row)[0], expected)
    close(Decimal(str(answer["figure"])), expected)


def test_share_by_region(client, warehouse):
    answer = ask(client, "share of NNA by region for Q1 2025")
    first, last = window(answer)
    assert (first, last) == ("2025-01-01", "2025-04-01")
    expected = dict(rows(warehouse, f"SELECT b.RGN_CD, SUM(f.TRAN_AM) {FLOW.format(first=first, last=last)}{CURRENT}"
                                    " GROUP BY 1"))
    total = sum(expected.values())
    got = {row["dimensions"][0]["value"]: cells(row) for row in answer["result"]["rows"] if not row.get("total")}
    assert set(got) == set(expected)
    for region, (amount, share) in got.items():
        close(amount, expected[region])
        assert abs(share - expected[region] / total) < Decimal("1e-9"), (region, share)


def test_running_total_by_month(client, warehouse):
    answer = ask(client, "running total of NNA by month for Q1 2025")
    first, last = window(answer)
    assert (first, last) == ("2025-01-01", "2025-04-01")
    expected = rows(warehouse, "SELECT CAST(date_trunc('month', f.DW_TRAN_DT) AS DATE) AS month, SUM(f.TRAN_AM) "
                               f"{UNJOINED.format(first=first, last=last)}{CURRENT} GROUP BY 1 ORDER BY 1")
    got = [(row["dimensions"][0]["value"], *cells(row)) for row in answer["result"]["rows"] if not row.get("total")]
    assert [month for month, *_ in got] == [month.isoformat() for month, _ in expected] == [
        "2025-01-01", "2025-02-01", "2025-03-01"]
    running = Decimal(0)
    for (_, amount, total), (_, reference) in zip(got, expected):
        running += reference
        close(amount, reference)
        close(total, running)


def test_top_and_bottom_branches(client, warehouse):
    answer = ask(client, "top 2 and bottom 2 branches by NNA for Q1 2025")
    first, last = window(answer)
    assert (first, last) == ("2025-01-01", "2025-04-01")
    branches = rows(warehouse, f"SELECT b.BRCH_CD, SUM(f.TRAN_AM) AS amount {FLOW.format(first=first, last=last)}"
                               f"{CURRENT} GROUP BY 1")
    assert len({amount for _, amount in branches}) == len(branches), "ties would make the ranking ambiguous"
    ranked = sorted(branches, key=lambda r: r[1], reverse=True)
    expected = {("top", n + 1): r for n, r in enumerate(ranked[:2])}
    expected |= {("bottom", n + 1): r for n, r in enumerate(ranked[::-1][:2])}
    got = {(row["end"], row["rank"]): (row["dimensions"][0]["value"], cells(row)[0])
           for row in answer["result"]["rows"] if not row.get("total")}
    assert set(got) == set(expected)
    for key, (branch, amount) in got.items():
        assert branch == expected[key][0], key
        close(amount, expected[key][1])
    [total] = [row for row in answer["result"]["rows"] if row.get("total")]
    close(cells(total)[0], sum(amount for _, amount in branches))


def test_as_reported_against_current(client, warehouse):
    answer = ask(client, "NNA for March 2025 as reported vs current")
    first, last = window(answer)
    assert (first, last) == ("2025-03-01", "2025-04-01")
    population = UNJOINED.format(first=first, last=last)
    [(reported,)] = rows(warehouse, f"SELECT COALESCE(SUM(f.TRAN_AM), 0) {population}{REPORTED}")
    [(current,)] = rows(warehouse, f"SELECT COALESCE(SUM(f.TRAN_AM), 0) {population}{CURRENT}")
    assert reported != current, "the window must tell the two time modes apart"
    labels = [c["label"] for c in answer["result"]["columns"]]
    assert labels == ["Net new assets · as reported", "Net new assets · current", "Difference"]
    [row] = answer["result"]["rows"]
    got_reported, got_current, difference = cells(row)
    close(got_reported, reported)
    close(got_current, current)
    close(difference, reported - current)
