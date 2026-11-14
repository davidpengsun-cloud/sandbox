import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from kge.ports import AUTHORED
from kge.pipeline.sqlguard import schema_allowlist
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Asked, Plan
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.question import parse
from tests.integration.test_context_scope import vocabulary
from tests.plan_fixtures import grounded, model, name
from tests.unit.test_plan_correctness import Reply


@pytest.mark.parametrize('mode', ['current', 'as_reported'])
def test_branch_sales_versions_reconcile_with_release_date_sql(eng, mode):
    question = 'Branch sales NNA for region NORTHEAST during March 2025'
    v = vocabulary(eng, question)
    measure = dict(kind='measure', id='m', measure=name('wealth.branch-sales-nna'), context=name('nna_branch_sales'), mode=mode, filters=[])
    period = dict(kind='single', period=dict(kind='month', value='2025-03', calendar=name('calendar')))
    text = question + (' as reported' if mode == 'as_reported' else '')
    region = dict(kind='compare', id='f', left=dict(kind='column', name=name('wealth.BRCH_DIM_1.RGN_CD'),
                  path=[name('branch_sales_account'), name('account_current_office')]),
                  op='eq', value=dict(kind='text', value='NORTHEAST'), evidence=[])
    p = grounded(model(measures=[measure], time=period, filters=[region]), text, v,
                 {'March 2025': 'time', 'as reported': 'm', 'region NORTHEAST': 'f'})
    proposed = read(Reply(p), text, v)
    assert proposed.kind == 'propose'
    checked = check_plan(proposed.plan, v, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert eng.w.gate('wealth', compiled.release.sql, compiled.release.allow, dialect=AUTHORED)['passed']
    compiled = compile_plan(checked, eng.w.rows('wealth', compiled.release.sql, dialect=AUTHORED))
    assert eng.w.gate('wealth', compiled.answer.sql, compiled.answer.allow, dialect=AUTHORED)['passed']
    actual = Decimal(eng.w.row('wealth', compiled.answer.sql, dialect=AUTHORED)[0])
    sql = """SELECT SUM(CASE WHEN t.DW_TRAN_DIRCTN_CD = 'O' THEN -t.TRAN_AM ELSE t.TRAN_AM END)
             FROM TRAN_FACT_EXCL_MFCLR_G t JOIN MONTH_DIM_1 m ON t.MNTH_END_DT = m.CLDR_EOM_DT
             JOIN ACCT_DIM2_1 a ON a.DW_ACCT_ID = t.DW_ACCT_ID AND a.SRCE_SYS_ID = t.ACCT_SRCE_SYS_ID
               AND a.DW_EFF_DT <= t.MNTH_END_DT AND (a.DW_EXPR_DT IS NULL OR a.DW_EXPR_DT >= t.MNTH_END_DT)
             JOIN CLIENT_CD_ROLLUP_1 r ON r.CLNT_CD = a.CLNT_CD
               AND t.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
             JOIN BRCH_DIM_1 b ON b.BRCH_CD = a.ACCT_DOMCL_BRCH_CD AND b.CURR_IN <> 0
             WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0 AND t.MNTH_END_DT = DATE '2025-03-31'
               AND t.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT
               AND (t.DW_EXPR_DT IS NULL OR t.DW_EXPR_DT >= m.FIN_MTRC_RLSE_DT)
               AND t.NNA_IN = 'Y' AND t.RCV_IN = 1 AND t.ACCT_SRCE_SYS_ID IN (100, 200)
               AND r.SEGMT_LVL_ID5 = 'AFFLUENT' AND upper(b.BRCH_DIVSN_CD) = 'BR'
               AND upper(coalesce(t.DW_TRAN_TYPE_CD, '')) NOT IN ('AQ', 'DT') AND b.RGN_CD = 'NORTHEAST'"""
    legacy = eng.semantics.plan(v.contexts['nna_branch_sales'], Asked.of(parse(question, eng.term_names, eng)))
    allow = eng.semantics.allowlist(legacy)
    gate = eng.w.gate('wealth', sql, allow, dialect=AUTHORED)
    assert gate['passed'], gate
    expected = Decimal(eng.w.row('wealth', sql, dialect=AUTHORED)[0])
    assert actual == expected
    revised = sql.replace("AND t.NNA_IN = 'Y'", "AND t.CURR_IN = 0 AND t.NNA_IN = 'Y'").replace('AND t.RCV_IN = 1', '')
    assert eng.w.gate('wealth', revised, allow, dialect=AUTHORED)['passed']
    assert eng.w.row('wealth', revised, dialect=AUTHORED)[0] != 0


def test_release_date_version_rule_is_confined_to_branch_sales_fact(eng):
    context = eng.semantics.contexts['nna_branch_sales']
    p = Plan(eng.semantics, context, Asked())
    p.release = dict(eng.semantics.releases['wealth.MONTH_DIM_1'], alias='m')
    version = p.version('t', context.fact)
    assert 'FIN_MTRC_RLSE_DT' in version and 'CURR_IN' not in version
    other = Plan(eng.semantics, replace(context, id='another_context', grain=context.grain), Asked())
    assert other.version('t', context.fact) == 't.CURR_IN <> 0'
    assert 'MNTH_END_DT' in p.version('a', 'wealth.ACCT_DIM2_1')
    assert 'CURR_IN' not in p.version('a', 'wealth.ACCT_DIM2_1')
    legacy = eng.semantics.plan(context, Asked(period=type('Period', (), {'kind': 'month', 'value': '2025-03-31'})()))
    assert not legacy.decision
    assert 'FIN_MTRC_RLSE_DT' in ' '.join(legacy.where)
    assert legacy.parts([dt.date(2025, 3, 31)])


def test_bare_segment_code_and_name_identify_the_same_fixture_members(eng):
    allow = schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS)
    sql = """SELECT COUNT(*) FROM CLIENT_CD_ROLLUP_1
             WHERE (SEGMT_LVL_ID5 = 'AFFLUENT') <> (LOWER(SEGMT_LVL_NM5) = 'affluent')"""
    assert eng.w.gate('wealth', sql, allow, dialect=AUTHORED)['passed']
    assert eng.w.row('wealth', sql, dialect=AUTHORED)[0] == 0
