from decimal import Decimal

import pytest

from kge.semantics.plan import Propose
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.present import present
from kge.services.ask.engine.wording import stray_numbers
from tests.integration.test_plan_wording_round2 import Recording, april, count, measure, vocabulary
from tests.plan_fixtures import grounded, model, name


def test_signed_flow_summary_has_named_outputs_in_question_order(eng, monkeypatch):
    """"ratio of inflows to outflows" names the pack's declared ratio, so the plan uses its formula over the declared
    inflow and outflow contexts (ef443f03; an undeclared ratio is refused: test_plan_wording_round2). The recorded model's
    plan is served under ASK_ORDER=model_first, A1's documented order (#464 requirement 2)."""
    monkeypatch.setenv('ASK_ORDER', 'model_first')
    text = ('Branch sales NNA with inflows and outflows and ratio of inflows to outflows '
            'with number of households during April 2025')
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    p = grounded(model(measures=[measure(ctx), measure(v.contexts['branch_sales_inflows'], 'i'),
                                  measure(v.contexts['branch_sales_outflows'], 'o'), count(ctx, 'wealth_account_household', 'n')],
                       time=april(), derived=[dict(kind='ratio', id='r', numerator='i', denominator='o',
                                                   declaration=name('branch-sales-inflow-outflow-ratio'), evidence=[])]), text, v,
                 {'Branch sales NNA': ['m', 'n'], 'inflows': 'i', 'outflows': 'o', 'ratio of inflows to outflows': ['r', 'i', 'o'],
                  'number of': 'n', 'households': 'n', 'April 2025': 'time'})
    answer = Combined(eng, llm=Recording(Propose(kind='propose', base_turn=None, plan=p))).ask(
        text, 'analyst_enterprise', config=PRESETS['graph'].with_stages(resolution='glossary+model'))
    assert answer['behaviour'] == 'combined', answer
    assert [s['interpretation'] for s in answer['trace'] if s.get('step') == 'plan'] == ['model:test:wording'], answer['trace']
    table = present(answer, eng)['table']
    assert [c['label'] for c in table['columns'][:5]] == ['Branch Sales NNA', 'Branch sales inflows', 'Branch sales outflows',
                                                          'Branch sales inflow to outflow ratio',
                                                          'Households with branch sales NNA']
    values = table['rows'][0]
    assert Decimal(values[1]) + Decimal(values[2]) == Decimal(values[0])
    assert Decimal(values[1]) > 0 > Decimal(values[2])
    assert Decimal(values[3]) == pytest.approx(Decimal(values[1]) / abs(Decimal(values[2])))
    assert values[4] > 0
    assert answer['answer_text'].startswith('April 2025: Branch Sales NNA | Branch sales inflows | Branch sales outflows | '
                                            'Branch sales inflow to outflow ratio | Households')
    assert not stray_numbers(answer)
