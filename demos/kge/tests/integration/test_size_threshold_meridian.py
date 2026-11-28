"""Issue 474, golden S13 on Meridian's sample: "transactions of $250K or more" is a size threshold on each transaction,
not a threshold on the count. Each transaction is sized by its declared amount whatever its sign (TRAN_AM is signed: an
outflow is negative), so the threshold reads ABS(TRAN_AM), and the branch sales NNA and the transaction count share it.
Every figure is checked against independently written SQL."""
from copy import deepcopy
from decimal import Decimal

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_pack_wordings import reference as sql

WHO = 'analyst_enterprise'
ASKED = 'How many transactions of {} did the Fort Collins branch have in January 2024, and what was their branch sales NNA?'
SIZES = [('$250K or more', '>=', 250000), ('at least $250,000', '>=', 250000), ('$250K and over', '>=', 250000),
         ('more than $10K', '>', 10000), ('at most $5,000', '<=', 5000), ('$10,000 or below', '<=', 10000)]


def expected(eng, op, amount, signed=False):
    """Branch sales NNA and the count of transactions for Fort Collins (5H) in January 2024, on the transactions whose
    amount (signed, or by default its absolute value) compares with the threshold: the owner's branch sales rules."""
    sized = f"f.TRAN_AM {op} {amount}" if signed else f"ABS(f.TRAN_AM) {op} {amount}"
    total, count = sql(eng, f"""SELECT COALESCE(SUM(f.TRAN_AM), 0), COUNT(DISTINCT (f.ACCT_SRCE_SYS_ID, f.DW_ACCT_ID, f.DW_TRAN_ID))
FROM TRAN_FACT_EXCL_MFCLR_G f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN=1
WHERE f.MNTH_END_DT = DATE '2024-01-31' AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.ACCT_SRCE_SYS_ID IN (100,200) AND r.SEGMT_LVL_ID5='S4100'
AND upper(b.BRCH_DIVSN_CD)='BR' AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')
AND b.BRCH_CD='5H' AND {sized}""")[0]
    return Decimal(str(total)).quantize(Decimal('0.01')), int(count)


def figures(answer):
    assert answer['behaviour'] == 'combined', answer.get('reason')
    row, = answer['result']['rows']
    nna, count = (Decimal(c['value']['value']) for c in row['cells'])
    return nna.quantize(Decimal('0.01')), int(count)


@pytest.mark.parametrize('size,op,amount', SIZES)
def test_a_size_threshold_filters_the_rows_both_outputs_read(eng, size, op, amount):
    answer = eng.answer(ASKED.format(size), WHO)
    assert figures(answer) == expected(eng, op, amount)
    assert expected(eng, op, amount)[1] > 0
    assert f'ABS("TRAN_FACT_EXCL_MFCLR_G"."TRAN_AM") {op} {amount}' in answer['sql']
    assert 'FROM aligned WHERE' not in answer['sql']


def test_the_absolute_amount_changes_the_answer_on_signed_rows(eng):
    """Outflows are negative: on the signed amount an outflow would never be at most $5,000 by size alone."""
    assert expected(eng, '<=', 5000) != expected(eng, '<=', 5000, signed=True)
    assert expected(eng, '>', 10000) != expected(eng, '>', 10000, signed=True)


def test_the_ledger_reads_one_size_piece_and_no_threshold_operator(eng):
    question = ASKED.format('$250K or more')
    pieces = ledger(question, 'turn', vocabulary(eng, question))
    sized = [p for p in pieces if p.operation == 'magnitude_ge']
    assert [(p.text, p.kind, Decimal(str(p.value))) for p in sized] == [('$250K or more', 'predicate', Decimal(250000))]
    assert 'wealth.TRAN_FACT_EXCL_MFCLR_G.TRAN_AM' in sized[0].candidates
    assert not any(p.kind == 'operator' and p.operation == 'ge' for p in pieces)


def test_the_model_checks_hold_the_size_threshold_as_asked(eng):
    """The rules plan passes; the same plan on the signed amount, with another threshold, or with the threshold dropped
    does not: a model cannot change the size filter."""
    question = ASKED.format('$250K or more')
    turn = eng.answer(question, WHO)['history'][-1]
    plan, at = turn['plan'], turn['turn_id']
    vocab = vocabulary(eng, question)
    assert isinstance(check_plan(QueryPlan.model_validate(plan), vocab, question, at), CheckedPlan)
    sized, = [f for f in plan['filters'] if f.get('magnitude')]

    def changed(edit):
        out = deepcopy(plan)
        edit(next(f for f in out['filters'] if f['id'] == sized['id']), out)
        return out
    def dropped(f, out):
        out['filters'].remove(f)
        out['obligations'] = [{**o, 'targets': [t for t in o['targets'] if t != f['id']]} for o in out['obligations']]
        out['obligations'] = [o for o in out['obligations'] if o['targets']]
    def one_output(f, out):
        out['filters'].remove(f)
        out['measures'][0]['filters'] = [*out['measures'][0]['filters'], f]
    variants = [changed(one_output), changed(lambda f, _: f.pop('magnitude')), changed(lambda f, _: f.update(value={'kind': 'decimal', 'value': '100000'})),
                changed(lambda f, _: f.update(op='gt')), changed(dropped)]
    for variant in variants:
        assert not isinstance(check_plan(QueryPlan.model_validate(variant), vocab, question, at), CheckedPlan), variant['filters']


def test_a_size_threshold_is_refused_off_a_declared_amount_and_in_a_question_without_one(eng):
    question = ASKED.format('$250K or more')
    turn = eng.answer(question, WHO)['history'][-1]
    plan, at = turn['plan'], turn['turn_id']
    vocab = vocabulary(eng, question)
    off_amount = deepcopy(plan)
    sized = next(f for f in off_amount['filters'] if f.get('magnitude'))
    sized['left']['name']['id'] = 'wealth.TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_ID'
    assert not isinstance(check_plan(QueryPlan.model_validate(off_amount), vocab, question, at), CheckedPlan)
    plain = 'How many transactions did the Fort Collins branch have in January 2024, and what was their branch sales NNA?'
    assert not isinstance(check_plan(QueryPlan.model_validate(plan), vocabulary(eng, plain), plain, at), CheckedPlan)
    with pytest.raises(ValueError):
        QueryPlan.model_validate(changed_value(plan, -1))


def changed_value(plan, value):
    out = deepcopy(plan)
    next(f for f in out['filters'] if f.get('magnitude'))['value'] = {'kind': 'integer', 'value': value}
    return out


def test_a_threshold_after_an_output_still_filters_that_output(eng):
    """Without "of" between an entity and its comparison, the comparison stays a threshold on the named output."""
    question = 'Branch sales NNA by branch for January 2024 with branch sales NNA above 1000'
    pieces = ledger(question, 'turn', vocabulary(eng, question))
    assert not any((p.operation or '').startswith('magnitude_') for p in pieces)
