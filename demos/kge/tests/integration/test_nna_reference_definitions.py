from decimal import Decimal

import pytest

from kge.semantics.coverage import ledger
from tests.integration.test_plan_vocabulary_round3 import BRANCH_SALES, SIGNED, assignments, single
from tests.integration.test_plan_wording_round2 import cells, measure, run, sql, vocabulary


POPULATIONS = [
    ('NNA', 'nna', "SELECT sum(CASE WHEN f.DW_TRAN_DIRCTN_CD='O' THEN -f.TRAN_AM ELSE f.TRAN_AM END) "
     "FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT "
     "WHERE f.MNTH_END_DT=DATE '2025-05-31' AND f.CURR_IN<>0 AND f.RCV_IN=1 AND f.NNA_IN='Y' "
     "AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 a "
     "WHERE a.DW_ACCT_ID=f.DW_ACCT_ID AND a.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID AND a.CURR_IN<>0 AND a.TEST_ACCT_IN='Y')"),
    ('Certified NNA', 'nna_certified', "SELECT sum(f.NNA_AM) FROM F_NNA_FIN f "
     "JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT "
     "WHERE f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND f.FRZN_IN='Y' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0"),
    ('Client-directed NNA', 'nna_client_directed', "SELECT sum(f.NNA_AM) FROM F_NNA_FIN f "
     "JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT "
     "WHERE f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND f.CLIENT_DIRECTED_NNA_IN='Y' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0"),
    ('Branch sales NNA', 'nna_branch_sales', 'SELECT sum(' + SIGNED + ') ' + BRANCH_SALES
     + " AND f.MNTH_END_DT=DATE '2025-05-31'"),
]


@pytest.mark.parametrize('phrase,context,expected', POPULATIONS)
def test_reference_fixed_populations_reconcile_without_question_filters(eng, phrase, context, expected):
    question = phrase + ' during May 2025'
    v = vocabulary(eng, question)
    rows = run(eng, question, v, [measure(v.contexts[context])], assignments(question, v), time=single())
    assert cells(rows[0]) == [sql(eng, expected)[0][0]]


@pytest.mark.parametrize('phrase,context', [(p, c) for p, c, _ in POPULATIONS] + [
    ('Flow Amount', 'flow_amount'), ('Branch Sales Flow Amount', 'branch_sales_flow_amount')])
def test_every_nna_population_refuses_open_month_and_offers_latest_released(eng, phrase, context):
    from kge.semantics.compile import compile_plan
    from kge.semantics.plancheck import CheckedPlan, check_plan
    from kge.ports import AUTHORED
    from tests.plan_fixtures import grounded, model

    text = phrase + ' during July 2025'
    v = vocabulary(eng, text)
    proposed = grounded(model(measures=[measure(v.contexts[context])], time=single('2025-07')),
                        text, v, assignments(text, v))
    checked = check_plan(proposed, v, text)
    assert isinstance(checked, CheckedPlan), checked
    pending = compile_plan(checked)
    assert pending.release is not None
    assert eng.w.gate('wealth', pending.release.sql, pending.release.allow, dialect=AUTHORED)['passed']
    release_rows = eng.w.rows('wealth', pending.release.sql, dialect=AUTHORED)
    with pytest.raises(ValueError) as refused:
        compile_plan(checked, release_rows)
    assert 'July 2025 is not yet released' in str(refused.value)
    assert 'The latest released month is June 2025' in str(refused.value)


def test_declared_code_name_members_are_the_same_warehouse_rows(eng):
    pairs = [r['params'] for r in eng.g.domain()['vocabulary'] if r['kind'] == 'code_name_pair']
    assert len(pairs) == 5
    for pair in pairs:
        table, code = pair['code'].rsplit('.', 1)
        name = pair['name'].rsplit('.', 1)[1]
        members = {(str(code), name) for code, name in sql(eng, f'SELECT DISTINCT {code}, {name} FROM {table.split(".")[1]}')}
        assert members == {(m['code'], m['name']) for m in pair['members']}
    v = vocabulary(eng, 'Certified NNA for AFFLUENT during May 2025')
    piece, = [p for p in ledger('AFFLUENT', 'turn', v)]
    assert piece.kind == 'predicate'
    assert piece.candidates == ('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5',)


def test_month_end_and_release_versions_are_declared_independently_of_question_mode(eng):
    from kge.semantics.query import Asked, Plan
    for context in ('nna_certified', 'nna_client_directed', 'nna_branch_sales', 'branch_sales_flow_amount'):
        ctx = eng.semantics.contexts[context]
        for mode in ('current', 'as_reported'):
            plan = Plan(eng.semantics, ctx, Asked(time_mode=mode))
            plan.aliases[ctx.fact.split('.')[1]] = ctx.fact
            for table in ('wealth.ACCT_DIM2_1', 'wealth.CLIENT_CD_ROLLUP_1'):
                rule = plan.version('versioned', table)
                assert ctx.version_dates[table].split('.', 1)[1] in rule
                assert 'CURR_IN' not in rule


def test_excluded_transaction_types_are_deterministic_fixture_counterexamples(eng):
    assert dict(sql(eng, "SELECT DW_TRAN_TYPE_CD, count(*) FROM TRAN_FACT_EXCL_MFCLR_G "
                    "WHERE DW_TRAN_TYPE_CD IN ('AQ','DT') GROUP BY 1")) == {'AQ': 1, 'DT': 1}
    assert sql(eng, "SELECT count(*) FROM BRCH_DIM_1 WHERE BRCH_DIVSN_CD='BR'")[0][0] == 3


@pytest.mark.parametrize('phrase,context,expected_sql', POPULATIONS)
def test_owner_nna_populations_keep_two_native_jobs(monkeypatch, tmp_path, phrase, context, expected_sql):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import grounded, model
    from tests.integration.test_planner_request_jobs import budgets, pilot
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        finance = fake._physical(engine.w.dataset_id, engine.w.prefix('wealth') + '_F_NNA_FIN')
        fake.con.execute(f"""UPDATE "{finance}" SET FRZN_IN='N'
            WHERE DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND DW_TRAN_ID=(
                SELECT min(DW_TRAN_ID) FROM "{finance}"
                WHERE DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND CLIENT_DIRECTED_NNA_IN='Y' AND NNA_AM<>0)""")
        assert sql(engine, "SELECT sum(NNA_AM) FROM F_NNA_FIN WHERE DW_TRAN_MNTH_END_DT=DATE '2025-05-31' "
                   "AND FRZN_IN='N' AND CLIENT_DIRECTED_NNA_IN='Y'")[0][0] != 0
        text = phrase + ' during May 2025'
        v = vocabulary(engine, text)
        ctx = v.contexts[context]
        expected = sql(engine, expected_sql)[0][0]
        for _ in range(2):
            fake.requests.clear()
            rows = run(engine, text, v, [measure(ctx)], assignments(text, v), time=single())
            assert cells(rows[0]) == [Decimal(str(expected))]
            assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
        text = phrase + ' during July 2025'
        v = vocabulary(engine, text)
        proposed = grounded(model(measures=[measure(v.contexts[context])], time=single('2025-07')),
                            text, v, assignments(text, v))
        fake.requests.clear()
        refusal = Planner(v, engine.w, Hidden([], set(), [])).answer(
            text, Propose(kind='propose', base_turn=None, plan=proposed))
        assert refusal['kind'] == 'abstain' and refusal['phase'] == 'release'
        assert 'July 2025 is not yet released' in '; '.join(refusal['missing'])
        assert 'The latest released month is June 2025' in '; '.join(refusal['missing'])
        assert 'result' not in refusal
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=1, dry=1, catalogue=0)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
