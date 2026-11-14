"""Year comparisons use released, populated history and reconcile to plain SQL."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.planrules import vocabulary
from kge.services.ask.engine.planner import Planner, execute
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_wealth_context_filters import check_reference_and_runtime, dimension, measure, predicate
from tests.plan_fixtures import grounded, model, name


@pytest.mark.parametrize("context,label,breakdown", [
    ("nna_certified", "Certified NNA", False),
    ("nna_client_directed", "Client-directed NNA", True),
])
def test_released_previous_year_segment_comparison_reconciles_every_cell(eng, context, label, breakdown):
    v = vocabulary(eng, "analyst_enterprise", "analysis", SimpleNamespace(products=[]), PRESETS["graph"])
    question = f"June 2025 {label} versus previous year with percent change for segment level 5 AFFLUENT"
    if breakdown:
        question += " by segment level 6"
    plan = model(
        measures=[measure(v, context, filters=[predicate(v, "wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5", "AFFLUENT", context)])],
        dimensions=[dimension(v, "wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6", context)] if breakdown else [],
        time={"kind": "single", "period": {"kind": "month", "value": "2025-06", "calendar": name("calendar")}},
        comparisons=[{"kind": "previous_year", "id": "c", "inputs": ["m"], "evidence": []}],
        derived=[{"kind": "percent_change", "id": "p", "numerator": "c/m/base", "denominator": "c/m/other", "evidence": []}],
    )
    plan = grounded(plan, question, v, {"June 2025": "time", "versus": "c", "previous year": "c",
        "percent change": "p", "segment level 5 AFFLUENT": "f", "by segment level 6": "d"})

    class Scripted:
        def json(self, prompt):
            return {"kind": "propose", "base_turn": None, "plan": plan.model_dump(mode="json", by_alias=True)}

    reply = read(Scripted(), question, v)
    assert reply.kind == "propose", reply
    checked = check_plan(reply.plan, v, question, "turn")
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert compiled.release is not None
    check_reference_and_runtime(compiled.release)
    guard, released = execute(eng.w, compiled.release)
    assert guard["passed"]
    compiled = compile_plan(checked, released)
    check_reference_and_runtime(compiled.answer)
    answer = Planner(v, eng.w, Hidden([], set(), [])).answer(question, reply)
    assert answer["kind"] == "result" and answer["guard"]["passed"], answer
    grouping = "r.SEGMT_LVL_ID6" if breakdown else "'all'"
    with eng.w.cursor("wealth", "duckdb") as con:
        if not breakdown:
            expected = con.execute(f"""
            SELECT {grouping} AS segment, f.DW_TRAN_MNTH_END_DT, SUM(f.NNA_AM)
            FROM F_NNA_FIN f
            JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT = m.CLDR_EOM_DT
            JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID
                AND m.CLDR_EOM_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
            JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD = r.CLNT_CD
                AND f.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
            WHERE f.DW_TRAN_MNTH_END_DT IN (DATE '2025-06-30', DATE '2024-06-30')
                AND f.FRZN_IN = 'Y' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0
                AND r.SEGMT_LVL_ID5 = 'AFFLUENT'
            GROUP BY 1, 2 ORDER BY 1, 2
            """).fetchall()
        else:
            expected = con.execute("""
                SELECT r.SEGMT_LVL_ID6, m.CLDR_EOM_DT, SUM(f.NNA_AM)
                FROM F_NNA_FIN f JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT = m.CLDR_EOM_DT
                JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID
                    AND f.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
                JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD = r.CLNT_CD
                    AND f.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
                WHERE m.CLDR_EOM_DT IN (DATE '2025-06-30', DATE '2024-06-30')
                    AND m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0
                    AND f.CLIENT_DIRECTED_NNA_IN = 'Y' AND r.SEGMT_LVL_ID5 = 'AFFLUENT'
                GROUP BY 1, 2 ORDER BY 1, 2
            """).fetchall()
    figures = {(segment, day.year): value for segment, day, value in expected}
    returned = [row for row in answer["result"]["rows"] if not row.get("total")]
    assert len(returned) == len({segment for segment, _, _ in expected}) > 0
    for row in returned:
        segment = row["dimensions"][0]["value"] if breakdown else "all"
        current, previous = figures[segment, 2025], figures[segment, 2024]
        assert previous != 0
        outputs = [column["id"] for column in answer["result"]["columns"] if column["kind"] != "dimension"]
        cells = {output: cell["value"] for output, cell in zip(outputs, row["cells"])}
        assert Decimal(cells["m"]["value"]) == Decimal(cells["c/m/base"]["value"]) == current
        assert Decimal(cells["c/m/other"]["value"]) == previous
        ratio = Decimal(cells["p"]["value"])
        assert ratio == pytest.approx((current - previous) / abs(previous), abs=Decimal("0.000000001"))
