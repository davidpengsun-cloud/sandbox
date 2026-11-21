"""Issue 474 on Meridian's sample: a question that names several regions reads one IN on the office's region code, and
equals the sum of each region's answer and of independently written SQL; values of different columns stay apart."""
from copy import deepcopy
from decimal import Decimal

import pytest

from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.core import parse
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_pack_wordings import reference as sql

WHO = 'analyst_enterprise'
JAN_2025 = "f.DW_TRAN_DT >= DATE '2025-01-01' AND f.DW_TRAN_DT < DATE '2025-02-01'"
LISTS = ['NNA for Jan 2025 for Northeast and Pacific regions', 'NNA for Jan 2025 for Northeast or Pacific region',
         'NNA for Jan 2025 for Northeast region and Pacific region']
BRANCH_SALES = 'sum Jan 2025 branch sales NNA inflow, outflow, and net by branch for {}'


def nna(eng, regions, excluded=False):
    """NNA for January 2025 on the named office regions, written from the pack's NNA definition, apart from the
    engine's SQL."""
    listed = ', '.join(f"'{r}'" for r in regions)
    condition = f"b.RGN_CD IN ({listed})"
    if excluded:
        total = Decimal(str(sql(eng, f"""SELECT COALESCE(SUM(f.TRAN_AM), 0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND {JAN_2025} AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1""")[0][0]))
        return (total - nna(eng, regions)).quantize(Decimal('0.01'))
    return Decimal(str(sql(eng, f"""SELECT COALESCE(SUM(f.TRAN_AM), 0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND {JAN_2025} AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1
AND ({condition})""")[0][0])).quantize(Decimal('0.01'))


def branch_sales(eng, regions):
    """Branch sales NNA for January 2025 by branch, inflow, outflow and net, on the named office regions."""
    listed = ', '.join(f"'{r}'" for r in regions)
    rows = sql(eng, f"""SELECT b.BRCH_CD, f.DW_TRAN_DIRCTN_CD,
COALESCE(SUM(f.TRAN_AM), 0)
FROM TRAN_FACT_EXCL_MFCLR_G f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND {JAN_2025} AND f.NNA_IN='Y' AND f.RCV_IN=1
AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT AND f.ACCT_SRCE_SYS_ID IN (100,200)
AND r.SEGMT_LVL_ID5='S4100' AND upper(b.BRCH_DIVSN_CD)='BR' AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')
AND b.RGN_CD IN ({listed}) GROUP BY 1, 2""")
    out = {}
    for branch, direction, amount in rows:
        out.setdefault(branch, {'I': Decimal(0), 'O': Decimal(0)})[direction] = Decimal(str(amount)).quantize(Decimal('0.01'))
    return {branch: (d['I'], d['O'], d['I'] + d['O']) for branch, d in out.items()}


def figure(answer):
    assert answer['behaviour'] == 'figure', answer.get('reason')
    cell, = answer['result']['rows'][0]['cells']
    return Decimal(cell['value']['value'])


def by_branch(answer):
    assert answer['behaviour'] == 'combined', answer.get('reason')
    rows = {}
    for row in answer['result']['rows']:
        key = row['dimensions'][0].get('value')
        rows[key] = tuple(Decimal(c['value']['value']) for c in row['cells'])
    return rows


@pytest.mark.parametrize('question', LISTS)
def test_a_list_of_regions_is_one_in_and_equals_the_sum_of_each_region(eng, question):
    answer = eng.answer(question, WHO)
    northeast = figure(eng.answer('NNA for Jan 2025 for Northeast region', WHO))
    pacific = figure(eng.answer('NNA for Jan 2025 for Pacific region', WHO))
    assert figure(answer) == northeast + pacific == nna(eng, ['CEO', 'NER'])
    assert northeast == nna(eng, ['CEO']) and pacific == nna(eng, ['NER']) and northeast and pacific
    assert '"BRCH_DIM_1"."RGN_CD" IN (\'CEO\', \'NER\')' in answer['sql']
    assert ('RGN_CD', 'IN', ('CEO', 'NER')) in parse(question, eng.term_names, eng).filters


def test_the_branch_sales_parts_keep_their_flows_on_a_list_of_regions(eng):
    answer = eng.answer(BRANCH_SALES.format('Northeast and Pacific regions'), WHO)
    rows = by_branch(answer)
    expected = branch_sales(eng, ['CEO', 'NER'])
    assert expected and {k: v for k, v in rows.items() if k is not None} == expected
    assert rows[None] == tuple(sum(v[i] for v in expected.values()) for i in range(3))
    assert '"BRCH_DIM_1"."RGN_CD" IN (\'CEO\', \'NER\')' in answer['sql']


def test_values_of_two_columns_are_not_merged(eng):
    question = 'NNA for Jan 2025 for branch BOS and NYC branch'
    filters = parse(question, eng.term_names, eng).filters
    assert not any(op == 'IN' for _, op, _ in filters)
    assert {('BRCH_CD', '=', '5H'), ('ACCT_DOMCL_BRCH_CD', '=', 'QO')} <= set(filters)


def test_the_rules_plan_passes_the_model_checks_and_a_changed_list_does_not(eng):
    question = LISTS[0]
    turn = eng.answer(question, WHO)['history'][-1]
    plan, at = turn['plan'], turn['turn_id']
    vocab = vocabulary(eng, question)
    assert isinstance(check_plan(QueryPlan.model_validate(plan), vocab, question, at), CheckedPlan)
    for values in (['CEO', 'MIDWEST'], ['CEO', 'NER', 'MIDWEST'], ['CEO', 'NOWHERE']):
        changed = deepcopy(plan)
        def mutate(value):
            if isinstance(value, dict):
                if value.get('kind') == 'in':
                    value['values'] = [{'kind': 'text', 'value': v} for v in values]
                for member in value.values():
                    mutate(member)
            elif isinstance(value, list):
                for member in value:
                    mutate(member)
        mutate(changed)
        assert not isinstance(check_plan(QueryPlan.model_validate(changed), vocab, question, at), CheckedPlan), values


def test_the_declared_coastal_group_equals_the_union_and_both_named_region_answers(eng):
    answer = eng.answer('NNA for Jan 2025 for coastal region', WHO)
    expected = nna(eng, ['CEO', 'NER'])
    assert figure(answer) == expected
    assert expected == sum(figure(eng.answer('NNA for Jan 2025 for ' + region + ' region', WHO))
                           for region in ('Northeast', 'Pacific'))
    for text in LISTS:
        assert figure(eng.answer(text, WHO)) == expected
    assert ' IN (' in answer['sql']


def test_the_branch_sales_parts_use_the_declared_coastal_group(eng):
    answer = eng.answer(BRANCH_SALES.format('coastal region'), WHO)
    rows = by_branch(answer)
    expected = branch_sales(eng, ['CEO', 'NER'])
    assert expected and {k: v for k, v in rows.items() if k is not None} == expected
    assert rows[None] == tuple(sum(v[i] for v in expected.values()) for i in range(3))
    assert ' IN (' in answer['sql']


def test_the_declared_outside_coastal_group_matches_exclusion_and_keeps_null_regions(eng):
    included = figure(eng.answer('NNA for Jan 2025 for coastal region', WHO))
    excluded = eng.answer('NNA for Jan 2025 for outside coastal region', WHO)
    assert figure(excluded) == figure(eng.answer('NNA for Jan 2025 excluding Northeast and Pacific regions', WHO))
    assert figure(excluded) == nna(eng, ['CEO', 'NER'], excluded=True)
    assert figure(excluded) + included == figure(eng.answer('NNA for Jan 2025', WHO))
    assert 'IS NULL' in excluded['sql'] and ' IN (' in excluded['sql']
    from kge.services.ask.engine.wording import worded
    assert 'Rows with no value in an excluded column are kept' in worded(excluded, eng)['answer_text']
