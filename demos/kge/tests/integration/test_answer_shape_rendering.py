from decimal import Decimal

import pytest

from kge.semantics.plan import Propose
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.present import present
from kge.services.ask.engine.wording import stray_numbers
from tests.integration.test_plan_wording_round2 import Recording, april, count, measure, predicate, vocabulary
from tests.plan_fixtures import grounded, model


def test_signed_flow_summary_has_named_outputs_in_question_order(eng):
    text = ('Branch sales NNA with inflows and outflows and ratio of inflows to outflows '
            'with number of households during April 2025')
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    col = 'wealth.TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_DIRCTN_CD'
    p = grounded(model(measures=[measure(ctx), measure(ctx, 'i', [predicate(col, 'fi', 'I')]),
                                  measure(ctx, 'o', [predicate(col, 'fo', 'O')]), count(ctx, 'wealth_account_household', 'n')],
                       time=april(), derived=[dict(kind='ratio', id='r', numerator='i', denominator='o', evidence=[])]), text, v,
                 {'Branch sales NNA': ['m', 'i', 'o'], 'inflows': ['fi', 'i'], 'outflows': ['fo', 'o'],
                  'ratio': 'r', 'number of': 'n', 'households': 'n', 'April 2025': 'time'})
    answer = Combined(eng, llm=Recording(Propose(kind='propose', base_turn=None, plan=p))).ask(
        text, 'analyst_enterprise', config=PRESETS['graph'].with_stages(resolution='glossary+model'))
    assert answer['behaviour'] == 'combined', answer
    table = present(answer, eng)['table']
    assert [c['label'] for c in table['columns'][:6]] == ['Month', 'Inflows', 'Outflows', 'Net', 'Ratio', 'Households']
    values = table['rows'][0]
    assert values[0] == '2025-04'
    assert Decimal(values[1]) + Decimal(values[2]) == Decimal(values[3])
    assert Decimal(values[4]) == pytest.approx(Decimal(values[1]) / abs(Decimal(values[2])))
    assert values[5] > 0
    assert answer['answer_text'].startswith('Month | Inflows | Outflows | Net | Ratio | Households')
    assert not stray_numbers(answer)
