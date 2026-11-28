"""Context reach, column usability and prompt boundaries over compiled declarations."""
import csv
import json
import re

import pytest

from kge.semantics.plancheck import role, declared_value
from kge.pipeline.settings import ROOT
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.scope import ProductScope
from tests.plan_fixtures import name
from kge.semantics.plan import Name


CONTEXTS = ('nna', 'nna_client_directed', 'nna_certified', 'nna_branch_sales')
TABLES = {'wealth.' + t for t in (
    'F_NNA_FIN', 'TRAN_FACT_1', 'TRAN_FACT_EXCL_MFCLR_G', 'MONTH_DIM_1', 'ACCT_DIM2_1',
    'CLIENT_CD_ROLLUP_1', 'BRCH_DIM_1', 'SRCE_SYS_1', 'DATE_DIM_1',
)}
REQUIRED_COLUMNS = [
    'F_NNA_FIN.NNA_AM',
    'F_NNA_FIN.DW_TRAN_MNTH_END_DT',
    'F_NNA_FIN.DW_ACCT_ID',
    'F_NNA_FIN.ACCT_SRCE_SYS_ID',
    'F_NNA_FIN.CLIENT_DIRECTED_NNA_IN',
    'F_NNA_FIN.DW_TRAN_DT',
    'F_NNA_FIN.DW_TRAN_DIRCTN_CD',
    'F_NNA_FIN.DW_TRAN_ID',
    'TRAN_FACT_1.TRAN_AM',
    'TRAN_FACT_1.MNTH_END_DT',
    'TRAN_FACT_1.CURR_IN',
    'TRAN_FACT_1.RCV_IN',
    'TRAN_FACT_1.NNA_IN',
    'TRAN_FACT_1.DW_TRAN_ID',
    'TRAN_FACT_1.DW_EFF_DT',
    'TRAN_FACT_EXCL_MFCLR_G.TRAN_AM',
    'TRAN_FACT_EXCL_MFCLR_G.MNTH_END_DT',
    'TRAN_FACT_EXCL_MFCLR_G.DW_ACCT_ID',
    'TRAN_FACT_EXCL_MFCLR_G.ACCT_SRCE_SYS_ID',
    'TRAN_FACT_EXCL_MFCLR_G.DW_EFF_DT',
    'TRAN_FACT_EXCL_MFCLR_G.DW_EXPR_DT',
    'TRAN_FACT_EXCL_MFCLR_G.RCV_IN',
    'TRAN_FACT_EXCL_MFCLR_G.NNA_IN',
    'TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_TYPE_CD',
    'TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_DIRCTN_CD',
    'TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_ID',
    'MONTH_DIM_1.CLDR_EOM_DT',
    'MONTH_DIM_1.FIN_MTRC_RLSE_DT',
    'ACCT_DIM2_1.DW_ACCT_ID',
    'ACCT_DIM2_1.SRCE_SYS_ID',
    'ACCT_DIM2_1.DW_EFF_DT',
    'ACCT_DIM2_1.DW_EXPR_DT',
    'ACCT_DIM2_1.CLNT_CD',
    'ACCT_DIM2_1.ACCT_RELT_SPS_IN',
    'ACCT_DIM2_1.ACCT_DOMCL_BRCH_CD',
    'ACCT_DIM2_1.LAST_RCV_HSHLD_DK',
    'ACCT_DIM2_1.ACCT_DETL_PROD_CD',
    'ACCT_DIM2_1.JRSDCTN_CNTRY_CD',
    'BRCH_DIM_1.BRCH_CD',
    'BRCH_DIM_1.BRCH_NM',
    'BRCH_DIM_1.BRCH_DIVSN_CD',
    'BRCH_DIM_1.RGN_CD',
    'BRCH_DIM_1.CURR_IN',
    'BRCH_DIM_1.DW_EFF_DT',
    'CLIENT_CD_ROLLUP_1.CLNT_CD',
    'CLIENT_CD_ROLLUP_1.DW_EFF_DT',
    'CLIENT_CD_ROLLUP_1.DW_EXPR_DT',
    'CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6',
    'CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM6',
    'CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5',
    'CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM5',
    'CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID4',
    'CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM4',
    'SRCE_SYS_1.SRCE_SYS_ID',
    'SRCE_SYS_1.SRCE_SYS_NM',
    'SRCE_SYS_1.CURR_IN',
    'SRCE_SYS_1.RCV_IN',
    'SRCE_SYS_1.DW_EFF_DT',
    'DATE_DIM_1.CALENDAR_DATE',
    'DATE_DIM_1.DAY_OF_WEEK',
    'DATE_DIM_1.DAY_OF_WEEK_NUMBER',
]


def vocabulary(eng, question):
    req = parse(question, eng.term_names, eng)
    return planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)


@pytest.mark.parametrize('identifier', CONTEXTS)
def test_compiled_context_reach_has_a_table_ceiling(eng, identifier):
    ctx = eng.semantics.contexts[identifier]
    reached = {ctx.fact} | {t for j in ctx.joins for t in (j.source, j.target)}
    reached.update(r.table for r in (*ctx.filters, *ctx.selectors))
    assert reached <= TABLES
    assert not any(j.through for j in ctx.joins)
    assert ctx.ytd_calendar == 'calendar'
    assert 'not_excluded' not in {r.id for r in ctx.selectors}


@pytest.mark.parametrize('column', REQUIRED_COLUMNS)
def test_required_column_is_usable_through_a_declared_role(eng, column):
    qualified = 'wealth.' + column
    table = qualified.rsplit('.', 1)[0]
    usable = []
    for identifier in CONTEXTS:
        ctx = eng.semantics.contexts[identifier]
        path = planrules.path_to(ctx, table)
        if path is not None:
            v = vocabulary(eng, ctx.term + ' for June 2025')
            assert qualified in v.entries
            if any(kind in v.tables[table][qualified.rsplit('.', 1)[1]].upper() for kind in ('CHAR', 'STRING', 'TEXT')):
                with (ROOT / 'clients/meridian/fixture/data/wealth' / (table.split('.')[1] + '.csv')).open() as source:
                    values = {r[qualified.rsplit('.', 1)[1]] for r in csv.DictReader(source)} - {''}
                assert all(declared_value(v, qualified, value) for value in values)
            role(v, identifier, qualified, tuple(Name.model_validate(name(p)) for p in path))
            usable.append(identifier)
    assert usable
    assert len(REQUIRED_COLUMNS) == len(set(REQUIRED_COLUMNS)) == 61


@pytest.mark.parametrize('question', [
    'NNA for June 2025', 'Certified NNA for June 2025',
    'Client-directed NNA for June 2025', 'Branch Sales NNA for June 2025',
])
def test_prompt_vocabulary_uses_only_resolved_context_reach(eng, question):
    v = vocabulary(eng, question)
    assert set(v.tables) | set(v.periods) | set(v.scd2) | set(v.releases) <= TABLES
    public = json.dumps(planmodel.payload(question, v)['vocabulary'])
    assert set(re.findall(r'wealth\.([A-Z][A-Z0-9_]+)', public)) <= {t.split('.')[1] for t in TABLES}
    assert 'HSHLD_DIM_1' not in public
    assert 'NNA_EXCL_1' not in public
    assert 'CLNT_SEGMT_DIM_1' not in public
    assert v.binding.access_fingerprint


def test_context_bindings_leave_other_contexts_unchanged(eng):
    aum = eng.semantics.contexts['aum']
    assert aum.bindings is None and aum.ytd_calendar is None
    assert 'wealth.HSHLD_DIM_1' in {j.target for j in aum.joins}
    v = vocabulary(eng, 'NNA for June 2025')
    assert {e.id for e in v.candidates('by region')} == {'wealth.BRCH_DIM_1.RGN_CD'}
    assert {e.id for e in v.candidates('by division')} == {'wealth.BRCH_DIM_1.BRCH_DIVSN_CD'}
    assert {e.id for e in v.candidates('by client segment name')} == {'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM6'}
    assert {e.id for e in v.candidates('household')} == {'wealth_account_household'}


@pytest.mark.parametrize('fiscal', [False, True])
def test_ytd_answer_states_the_calendar_window(eng, fiscal):
    from kge.services.ask.engine.combined import Combined

    question = 'NNA ' + ('fiscal ' if fiscal else '') + 'year to date through June 2025'
    answer = Combined(eng, llm=None).ask(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'figure', answer
    label = ('fiscal year to date, Apr' if fiscal else 'year to date, Jan') + '–Jun 2025'
    assert answer['period_label'] == label
    from kge.services.ask.engine.wording import worded
    assert label in worded(answer, eng)['answer_text']
    assert len(answer['sql_run']) == 2


def test_unrelated_page_product_does_not_enter_the_context_prompt(eng):
    question = 'NNA for June 2025'
    req = parse(question, eng.term_names, eng)
    scope = ProductScope(eng, ['wealth.HSHLD_DIM_1'])
    v = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', scope, PRESETS['graph'], req)
    assert v.binding.products == ('wealth.hshld_dim_1',)
    assert 'hshld_dim_1' not in json.dumps(planmodel.bundle(v)).casefold()


def test_mixed_request_keeps_the_context_prompt_boundary(eng):
    v = vocabulary(eng, 'NNA and AUM for June 2025')
    assert set(v.tables) <= TABLES
    assert 'AUM_MNTH_END_FACT_1' not in json.dumps(planmodel.bundle(v))


def test_followup_inherits_the_context_prompt_boundary(eng):
    from kge.semantics.plan import HistoryEntry

    question = 'NNA for June 2025'
    req = parse(question, eng.term_names, eng)
    scope = ProductScope(eng, None)
    v = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', scope, PRESETS['graph'], req)
    reply = planrules.read(eng, req, v, PRESETS['graph'])
    history = (HistoryEntry(turn_id='turn', question=question, plan=reply.plan, clarification=None, binding=v.binding),)
    followup = parse('And for April 2025?', eng.term_names, eng)
    next_v = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', scope, PRESETS['graph'], followup, history)
    assert set(next_v.tables) <= TABLES
