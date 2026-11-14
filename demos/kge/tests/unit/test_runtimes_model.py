"""Unit tests for Issue #347 Slice 1 (Model): runtimes, runtime subgraph, CERTIFIED_IN, and partitioned policy kind."""
from __future__ import annotations

import json
import shutil

from tests.unit.test_compiler import CLIENT, compile_args, compile_in, committed, findings, load, manifest_of, printed, rows, run, scratch


def _set_1_5(p: dict) -> None:
    p["manifest"]["meta_model_version"] = "1.5.0"
    p["manifest"]["platform"]["meta_model"] = "^1.5"
    p["schema"]["meta_model"] = "1.5"


def test_runtimes_section_compiles_runtime_subgraph_and_pins_into_lock(tmp_path):
    def edit(p, l):
        _set_1_5(p)
        p["runtimes"] = [
            {
                "id": "bq_prod",
                "label": "BigQuery Production",
                "connection": {
                    "dialect": "bigquery",
                    "location": "US",
                    "execution_identity": "exec-svc-identity",
                    "billing_project": "proj-billing",
                },
                "tables": {
                    "retail.monthly_revenue": {
                        "name": "m_rev",
                        "columns": {
                            "total_revenue_usd": {"name": "rev_usd", "type": "NUMERIC"},
                            "month_start": "m_start",
                        },
                    },
                    "corporate.counterparty": "corp_cpty",
                },
                "absent": ["sme.covenant"],
            }
        ]
        p["default_runtimes"] = ["bq_prod", "reference"]
        p["measures"][0]["certified_in"] = "bq_prod"
        p["policies"]["partitioned"] = [
            {"id": "part-retail-rev", "table": "retail.monthly_revenue", "column": "month_start"}
        ]

    clients = scratch(tmp_path, edit)
    folder = printed(run(*compile_args(tmp_path / "out", clients)))
    man = manifest_of(folder)
    assert man["default_runtimes"] == ["bq_prod", "reference"]

    locked = json.loads((clients / CLIENT / "pack.lock").read_text())
    assert "runtimes" in locked and "bq_prod" in locked["runtimes"]
    pinned = locked["runtimes"]["bq_prod"]
    assert pinned["dialect"] == "bigquery"
    assert pinned["location"] == "US"
    assert pinned["execution_identity"] == "exec-svc-identity"
    assert pinned["billing_project"] == "proj-billing"
    assert pinned["absent"] == ["sme.covenant"]
    by_ref = {t["ref_table"]: t for t in pinned["tables"]}
    assert "sme.covenant" not in by_ref
    assert by_ref["retail.monthly_revenue"]["name"] == "m_rev"
    assert by_ref["corporate.counterparty"]["name"] == "corp_cpty"
    assert by_ref["retail.account"]["name"] == "account"
    cols = {c["ref_column"]: c for c in by_ref["retail.monthly_revenue"]["columns"]}
    assert cols["total_revenue_usd"] == {"ref_column": "total_revenue_usd", "name": "rev_usd", "type": "NUMERIC"}
    assert cols["month_start"]["name"] == "m_start"

    rt_rows = rows(folder, "data/nodes/Runtime.csv")[1:]
    assert rt_rows == [
        ["bq_prod", "BigQuery Production", "external", "bigquery", "US"],
        ["reference", "Reference", "internal", "\\N", "internal"],
    ]

    rt_tbls = dict(rows(folder, "data/nodes/RtTable.csv")[1:])
    assert rt_tbls["reference.retail.monthly_revenue"] == "monthly_revenue"
    assert rt_tbls["bq_prod.retail.monthly_revenue"] == "m_rev"
    assert rt_tbls["bq_prod.corporate.counterparty"] == "corp_cpty"
    assert "bq_prod.sme.covenant" not in rt_tbls
    assert "reference.sme.covenant" in rt_tbls

    rt_cols = {r[0]: (r[1], r[2]) for r in rows(folder, "data/nodes/RtColumn.csv")[1:]}
    assert rt_cols["bq_prod.retail.monthly_revenue.total_revenue_usd"] == ("rev_usd", "NUMERIC")
    assert rt_cols["reference.retail.monthly_revenue.total_revenue_usd"][0] == "total_revenue_usd"
    assert not any(k.startswith("bq_prod.sme.covenant.") for k in rt_cols)

    impl_tbl = dict(rows(folder, "data/edges/IMPLEMENTS__RtTable__Tbl.csv")[1:])
    assert impl_tbl["bq_prod.retail.monthly_revenue"] == "retail.monthly_revenue"
    assert impl_tbl["reference.retail.monthly_revenue"] == "retail.monthly_revenue"
    assert "bq_prod.sme.covenant" not in impl_tbl

    impl_col = dict(rows(folder, "data/edges/IMPLEMENTS__RtColumn__Col.csv")[1:])
    assert impl_col["bq_prod.retail.monthly_revenue.total_revenue_usd"] == "retail.monthly_revenue.total_revenue_usd"

    cert_in = rows(folder, "data/edges/CERTIFIED_IN__Measure__Runtime.csv")[1:]
    assert cert_in == [[committed()[0]["measures"][0]["id"], "bq_prod"]]

    pol_rows = {r[0]: (r[1], json.loads(r[2])) for r in rows(folder, "data/nodes/Policy.csv")[1:]}
    assert pol_rows["part-retail-rev"] == (
        "partitioned",
        {"column": "month_start", "table": "retail.monthly_revenue"},
    )
    gov_rows = rows(folder, "data/edges/GOVERNS__Policy__Col.csv")[1:]
    assert ["part-retail-rev", "retail.monthly_revenue.month_start", "1"] in gov_rows

    db, conn = load(folder, tmp_path / "graph")
    try:
        res = conn.execute(
            "MATCH (r:Runtime {id: 'bq_prod'})-[:HOLDS]->(rt:RtTable)-[:IMPLEMENTS]->(t:Tbl {id: 'retail.monthly_revenue'}), "
            "(rt)-[:HAS]->(rc:RtColumn {name: 'rev_usd'})-[:IMPLEMENTS]->(c:Col) "
            "RETURN rt.name, rc.data_type, c.id"
        ).get_all()
        assert res == [["m_rev", "NUMERIC", "retail.monthly_revenue.total_revenue_usd"]]
    finally:
        conn.close()
        db.close()


def test_runtimes_s31_and_s08_and_s28_validation_rules(tmp_path, capsys):
    def check_edit(fn):
        def wrapped(p, l):
            _set_1_5(p)
            fn(p, l)

        clients = scratch(tmp_path / "c", wrapped)
        code, _, err = compile_in(capsys, clients, tmp_path / "o")
        shutil.rmtree(tmp_path / "c", ignore_errors=True)
        shutil.rmtree(tmp_path / "o", ignore_errors=True)
        return code, findings(err)

    code, f = check_edit(
        lambda p, l: p.update(
            runtimes=[{"id": "reference", "label": "Ref", "connection": {"dialect": "duckdb", "location": "local"}}]
        )
    )
    assert code == 2 and any(r == "S31" and "internal reference runtime needs no entry" in m for r, _, m in f)

    code, f = check_edit(
        lambda p, l: p.update(
            runtimes=[
                {
                    "id": "ext",
                    "label": "Ext",
                    "connection": {"dialect": "bigquery", "location": "US"},
                    "tables": {"retail.no_such_table": "x"},
                    "absent": ["sme.missing_tbl"],
                }
            ]
        )
    )
    assert code == 2
    assert any(r == "S31" and "retail.no_such_table is not a table in pack.lock" in m for r, _, m in f)
    assert any(r == "S31" and "sme.missing_tbl is not a table in pack.lock" in m for r, _, m in f)

    code, f = check_edit(
        lambda p, l: p.update(
            runtimes=[
                {
                    "id": "ext",
                    "label": "Ext",
                    "connection": {"dialect": "bigquery", "location": "US"},
                    "tables": {"retail.monthly_revenue": {"name": "m", "columns": {"no_col": "x"}}},
                }
            ]
        )
    )
    assert code == 2 and any(r == "S31" and "no_col is not a column of retail.monthly_revenue" in m for r, _, m in f)

    code, f = check_edit(
        lambda p, l: p.update(
            runtimes=[
                {
                    "id": "ext",
                    "label": "Ext",
                    "connection": {"dialect": "bigquery", "location": "US"},
                    "tables": {
                        "retail.monthly_revenue": {
                            "name": "m",
                            "columns": {"total_revenue_usd": {"name": "rev_usd", "type": "STRING"}},
                        }
                    },
                }
            ]
        )
    )
    assert code == 2 and any(r == "S31" and "not compatible" in m for r, _, m in f)

    code, f = check_edit(
        lambda p, l: p.update(
            runtimes=[
                {
                    "id": "ext",
                    "label": "Ext",
                    "connection": {"dialect": "bigquery", "location": "US"},
                    "tables": {"retail.monthly_revenue": "same_tbl", "retail.account": "same_tbl"},
                }
            ]
        )
    )
    assert code == 2 and any(r == "S31" and "both map to 'same_tbl'" in m for r, _, m in f)

    code, f = check_edit(
        lambda p, l: p.update(default_runtimes=["unknown_rt"])
    )
    assert code == 2 and any(r == "S31" and "unknown_rt is not a declared runtime" in m for r, _, m in f)

    def edit_cert_absent(p, l):
        p["runtimes"] = [
            {
                "id": "ext",
                "label": "Ext",
                "connection": {"dialect": "bigquery", "location": "US"},
                "absent": ["retail.monthly_revenue"],
            }
        ]
        for m in p["measures"]:
            if m["column"].startswith("retail.monthly_revenue."):
                m["certified_in"] = "ext"
                break

    code, f = check_edit(edit_cert_absent)
    assert code == 2 and any(r == "S08" and "marks retail.monthly_revenue as absent" in m for r, _, m in f)

    def edit_bad_partition(p, l):
        p["policies"]["partitioned"] = [
            {"id": "bad-part", "table": "retail.monthly_revenue", "column": "total_revenue_usd"}
        ]

    code, f = check_edit(edit_bad_partition)
    assert code == 2 and any(r == "S28" and "not a date or timestamp" in m for r, _, m in f)


def test_pack_written_for_1_3_0_compiles_unchanged_against_1_4_0(tmp_path, capsys):
    def edit(p, l):
        p["manifest"]["meta_model_version"] = "1.3.0"
        p["manifest"]["platform"]["meta_model"] = "^1.3"
        p["schema"]["meta_model"] = "1.3"
        p["terminology"]["aliases"] = [a for a in p["terminology"]["aliases"] if "sources" not in a]

    clients = scratch(tmp_path, edit)
    code, out, err = compile_in(capsys, clients, tmp_path / "out")
    assert code == 0, err
    assert out.strip().endswith("/")
