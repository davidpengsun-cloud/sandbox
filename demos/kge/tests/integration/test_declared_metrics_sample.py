"""Golden count and ratio figures come from named metrics in the onboarded pack."""
from decimal import Decimal
import json

import pytest

from tests.scratch import BUILD
from tests.integration.test_pack_wordings import figure, reference


ITEMS = {i['id']: i for i in json.loads((BUILD / 'sage_v1.json').read_text())['items']}


@pytest.mark.parametrize('identifier,question,column,item', [
    ('nna-branch-sales-wealth-flow-excl-mf-clearing-count',
     'Branch sales transaction count for January 2024 for branch BOS with transaction amount at least 250000',
     'transactions_count', 'S13'),
    ('nna-branch-sales-wealth-account-household-count',
     'Branch sales household count for January 2024', 'households_count', 'S23'),
])
def test_named_count_metrics_equal_the_golden_reference_sql(eng, identifier, question, column, item):
    expected = reference(eng, ITEMS[item]['reference_sql'])[0][ITEMS[item]['expect']['table']['columns'].index(column)]
    answer = eng.answer(question, 'analyst_enterprise')
    assert figure(answer) == Decimal(str(expected))
    plan = answer['history'][-1]['plan']
    assert len(plan['measures']) == 1
    assert plan['measures'][0]['declaration']['id'] == identifier


def test_named_account_count_equals_each_golden_product_group(eng):
    item = ITEMS['S12']
    rows = reference(eng, item['reference_sql'])
    columns = item['expect']['table']['columns']
    expected = {r[columns.index('account_product_product_id')]: Decimal(str(r[columns.index('accounts_count')])) for r in rows}
    question = 'Certified account count for February 2024 for Affluent by account product code'
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] in ('figure', 'combined'), answer.get('reason')
    actual = {r['dimensions'][0]['value']: Decimal(r['cells'][0]['value']['value'])
              for r in answer['result']['rows'] if not r.get('total')}
    assert actual == expected
    assert answer['history'][-1]['plan']['measures'][0]['declaration']['id'] == 'nna-certified-wealth-account-count'


def test_named_ratio_plans_the_declared_inputs_and_reconciles_to_the_golden_sql(eng):
    item = ITEMS['S23']
    expected = reference(eng, item['reference_sql'])[0][item['expect']['table']['columns'].index('nna_am_inflows_to_outflows_ratio')]
    answer = eng.answer('Branch sales inflow to outflow ratio for January 2024', 'analyst_enterprise')
    assert answer['behaviour'] in ('figure', 'combined'), answer.get('reason')
    plan = answer['history'][-1]['plan']
    metric = next(d for d in plan['derived'] if d.get('declaration', {}).get('id') == 'branch-sales-inflow-outflow-ratio')
    columns = [c['id'] for c in answer['result']['columns'] if c['kind'] != 'dimension']
    value = answer['result']['rows'][0]['cells'][columns.index(metric['id'])]['value']['value']
    assert Decimal(value).quantize(Decimal('0.01')) == Decimal(str(expected))
