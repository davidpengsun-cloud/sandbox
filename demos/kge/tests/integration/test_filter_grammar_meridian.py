"""Filter acceptance on the sample warehouse, checked against independent SQL."""
import copy
from decimal import Decimal

import pytest
import sqlglot
from sqlglot import exp

from kge.semantics.coverage import ground_plan
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine.wording import worded
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_in_filters_meridian import figure
from tests.integration.test_plan_wording_round2 import sql

WHO = 'analyst_enterprise'
NORTHEAST = "b.RGN_CD='CEO'"
SPS = "a.ACCT_RELT_SPS_IN='Y'"
BROKERAGE = 'f.ACCT_SRCE_SYS_ID=100'


def nna_where(eng, condition, dimensions=True):
    """Apply a separately written condition to the declared current NNA population."""
    joins = """JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0""" if dimensions else ''
    statement = f"""SELECT COALESCE(SUM(f.TRAN_AM), 0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
{joins}
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.DW_TRAN_DT >= DATE '2025-01-01' AND f.DW_TRAN_DT < DATE '2025-02-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1
AND ({condition})"""
    return Decimal(str(sql(eng, statement)[0][0])).quantize(Decimal('0.01'))


def nna_excluding_regions(eng, codes):
    """Every NNA row less the rows whose branch is in the named regions: rows with no account, no branch or no region
    are kept. Written with inner joins only, as the SQL gate admits reference SQL since #474's declared join types."""
    return nna_where(eng, 'TRUE', dimensions=False) - nna_where(eng, f'b.RGN_CD IN ({codes})')


def model_accepts(eng, question, answer):
    """The supplied-plan path checks the same selections with freshly grounded evidence."""
    raw = answer['history'][-1]['plan']
    plan = QueryPlan.model_validate(raw).model_copy(update={'obligations': ()})
    words = vocabulary(eng, question)
    plan = ground_plan(plan, question, 'turn', words)
    assert isinstance(check_plan(plan, words, question), CheckedPlan)


def pack_copy(eng, params):
    """Add test-only vocabulary to isolated runtime copies, leaving the released pack intact."""
    clone = copy.copy(eng)
    clone.g = copy.copy(eng.g)
    clone.g._scoped_vocabularies = {}
    clone.VOCABULARY = [*eng.VOCABULARY, dict(kind='predicate', params=params)]
    domain = {**eng.g.domain(), 'vocabulary': clone.VOCABULARY}
    clone.g.domain = lambda: domain
    words = compiled(clone.VOCABULARY)
    clone.FILTERS, clone.FILTER_COLUMNS = words.filters, words.columns
    clone._semantics = copy.copy(eng.semantics)
    clone._semantics.d, clone._semantics.g = clone, clone.g
    for cached in ('plan_snapshot', 'baseline_snapshot'):
        if hasattr(clone._semantics, cached):
            delattr(clone._semantics, cached)
    return clone


@pytest.mark.parametrize('word', ['excluding', 'except', 'other than', 'not in'])
@pytest.mark.parametrize('values,codes', [('Pacific region', "'NER'"),
                                        ('Northeast and Pacific regions', "'CEO', 'NER'")])
def test_exclusions_match_independent_sql_and_keep_unvalued_rows(eng, word, values, codes):
    question = f'NNA for Jan 2025 {word} {values}'
    answer = eng.answer(question, WHO)
    expected = nna_excluding_regions(eng, codes)
    assert figure(answer) == expected
    assert 'IS NULL' in answer['sql'] and 'NOT' in answer['sql'] and 'IN (' in answer['sql']
    assert 'rows with no value' in worded(answer, eng)['answer_text'].casefold()
    model_accepts(eng, question, answer)


def test_excluding_one_region_equals_total_less_that_region(eng):
    total = figure(eng.answer('NNA for Jan 2025', WHO))
    pacific = figure(eng.answer('NNA for Jan 2025 for Pacific region', WHO))
    excluded = figure(eng.answer('NNA for Jan 2025 excluding Pacific region', WHO))
    assert excluded == total - pacific == nna_excluding_regions(eng, "'NER'")


def test_not_in_pack_phrase_is_the_same_null_preserving_filter(eng):
    clone = pack_copy(eng, dict(phrases=['outside eastern regions'], column='wealth.BRCH_DIM_1.RGN_CD',
                                op='not_in', values=['CEO', 'NER']))
    question = 'NNA for Jan 2025 for outside eastern regions'
    answer = clone.answer(question, WHO)
    assert figure(answer) == nna_excluding_regions(eng, "'CEO', 'NER'")
    assert figure(answer) == figure(eng.answer('NNA for Jan 2025 except Northeast and Pacific regions', WHO))
    assert 'rows with no value' in worded(answer, clone)['answer_text'].casefold()
    model_accepts(clone, question, answer)
    assert not any('outside eastern regions' in row['params'].get('phrases', ()) for row in eng.VOCABULARY)


def test_positive_null_pack_filter_retains_the_declared_inner_joins(eng):
    clone = pack_copy(eng, dict(phrases=['missing region'], column='wealth.BRCH_DIM_1.RGN_CD', op='is_null'))
    question = 'NNA for Jan 2025 for missing region or SPS accounts'
    answer = clone.answer(question, WHO)
    assert figure(answer) == nna_where(eng, "b.RGN_CD IS NULL OR a.ACCT_RELT_SPS_IN='Y'")
    tree = sqlglot.parse_one(answer['sql'], read='duckdb')
    assert not any(join.side == 'LEFT' for join in tree.find_all(exp.Join)
                   if isinstance(join.this, exp.Table) and join.this.name.upper() in ('ACCT_DIM2_1', 'BRCH_DIM_1'))
    model_accepts(clone, question, answer)


@pytest.mark.parametrize('joiner,condition', [('or', f'{NORTHEAST} OR {SPS}'),
                                           ('and', f'{NORTHEAST} AND {SPS}'),
                                           ('', f'{NORTHEAST} AND {SPS}')])
def test_cross_column_filters_use_union_or_intersection(eng, joiner, condition):
    question = f'NNA for Jan 2025 for Northeast region {joiner} SPS accounts'
    answer = eng.answer(question, WHO)
    assert figure(answer) == nna_where(eng, condition)
    model_accepts(eng, question, answer)
    if joiner == 'or':
        union = nna_where(eng, NORTHEAST) + nna_where(eng, SPS) - nna_where(eng, f'{NORTHEAST} AND {SPS}')
        assert figure(answer) == union


@pytest.mark.parametrize('filters', ['Northeast region or SPS accounts and BROKERAGE',
                                     'Northeast region and SPS accounts or BROKERAGE'])
def test_mixed_cross_column_joining_words_ask_for_grouping(eng, filters):
    answer = eng.answer('NNA for Jan 2025 for ' + filters, WHO)
    assert answer['behaviour'] == 'clarify', answer.get('reason')
    assert not answer.get('sql_run')


@pytest.mark.parametrize('filters,condition', [
    ('(Northeast region or SPS accounts) and BROKERAGE', f'({NORTHEAST} OR {SPS}) AND {BROKERAGE}'),
    ('Northeast region or (SPS accounts and BROKERAGE)', f'{NORTHEAST} OR ({SPS} AND {BROKERAGE})'),
])
def test_parentheses_make_mixed_filters_unambiguous(eng, filters, condition):
    question = 'NNA for Jan 2025 for ' + filters
    answer = eng.answer(question, WHO)
    assert figure(answer) == nna_where(eng, condition)
    model_accepts(eng, question, answer)


def test_branch_sales_name_does_not_turn_its_definition_into_an_exclusion(eng):
    question = 'Branch Sales NNA for Jan 2025'
    answer = eng.answer(question, WHO)
    expected = sql(eng, """SELECT COALESCE(SUM(f.TRAN_AM), 0)
FROM TRAN_FACT_EXCL_MFCLR_G f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.DW_TRAN_DT >= DATE '2025-01-01' AND f.DW_TRAN_DT < DATE '2025-02-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.ACCT_SRCE_SYS_ID IN (100,200) AND r.SEGMT_LVL_ID5='S4100' AND upper(b.BRCH_DIVSN_CD)='BR'
AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')""")[0][0]
    assert figure(answer) == Decimal(str(expected)).quantize(Decimal('0.01'))
    plan = QueryPlan.model_validate(answer['history'][-1]['plan'])
    assert not any(node.kind == 'not' for node in plan.nodes())
    assert 'rows with no value' not in worded(answer, eng)['answer_text'].casefold()
