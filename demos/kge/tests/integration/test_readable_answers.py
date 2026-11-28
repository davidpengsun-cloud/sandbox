"""Issue 456: breakdowns, comparisons and counts read as people write them, on the rules path and on a replayed model plan.

    .venv/bin/python -m pytest tests/integration/test_readable_answers.py -q

Each showcase question is asked on rules, then its rules plan is replayed as a model's reply: the answer
text, the API's result columns and the page's table name every column as asked (Month, Current NNA, Accounts with net
new assets), periods in words and in time order, each compared figure once, a decline in plain words, and no internal
id (t0, m0, c/base). The comparison uses December's business lines, whose certified month-end and current account
versions differ on the sample; March's two whole-month figures are equal under the golden semantics.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from kge.semantics.plan import Propose, QueryPlan
from kge.services.ask.engine import planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.present import present
from kge.services.ask.engine.question import parse
from kge.services.ask.engine.wording import worded
from tests.integration.test_answer_text import client as client
from tests.integration.test_plan_wording_round2 import Recording, vocabulary

ID_SHAPED = re.compile(r'(?<![\w/-])(?:[tmxd]\d+|c/[\w/]*)(?![\w-])', re.I)
BY_MONTH = 'current NNA for Q1 2025 by month'
COMPARED = 'certified vs current NNA for December 2024 by business line and the difference'
CURRENT = 'current NNA for March 2025'
ACCOUNTS = 'how many accounts had NNA in March 2025'
UNDATED = 'count of accounts in March 2025'
REVERSED = 'current versus certified NNA for December 2024 by business line and the difference'
QUESTIONS = (BY_MONTH, COMPARED, CURRENT, ACCOUNTS, UNDATED, REVERSED)
MODEL = PRESETS['graph'].with_stages(resolution='glossary+model')
COMPARED_VALUES = [
    ['1044732.59', '2317787.02', '-1273054.43'],
    ['583820.94', '1975569.62', '-1391748.68'],
    ['2713045.07', '2907708.38', '-194663.31'],
    ['1165430.41', '1699434.14', '-534003.73'],
    ['5507029.01', '8900499.16', '-3393470.15'],
]


def ids_in(text):
    return ID_SHAPED.findall(str(text or ''))


def shown(answer):
    """Every string a reader meets: the answer text, the reason, each result column's label and each page table's
    headers and cells."""
    table = (answer.get('display') or {}).get('table') or {}
    return [answer.get('answer_text'), answer.get('reason'),
            *(c.get('label') for c in (answer.get('result') or {}).get('columns', [])),
            *(c['label'] for c in table.get('columns', [])),
            *(cell for row in table.get('rows', []) for cell in row if isinstance(cell, str))]


def answered(eng, question, llm=None, config=PRESETS['graph']):
    answer = worded(Combined(eng, llm=llm).ask(question, 'analyst_enterprise', config=config), eng)
    return {**answer, 'display': present(answer, eng)}


def rules_reply(eng, question):
    req = parse(question, eng.term_names, eng)
    return planrules.read(eng, req, vocabulary(eng, question), PRESETS['graph'], 'turn', False)


@pytest.fixture(scope='module')
def answers(eng):
    out = {}
    for question in QUESTIONS:
        rules = answered(eng, question)
        plan = ((rules.get('history') or [{}])[-1].get('plan'))
        reply = (Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(plan)) if plan
                 else rules_reply(eng, question))
        assert reply.kind == 'propose', (question, reply)
        model = answered(eng, question, Recording(reply), MODEL)
        assert rules['versions']['interpretation'] == 'rules', rules['versions']
        assert model['versions']['interpretation'] == 'model:' + Recording.name, model['versions']
        out[question] = {'rules': rules, 'model': model}
    return out


def labels(answer):
    """The labels of the columns a reader is shown: every column but one that repeats another's figure."""
    return [c.get('label') for c in answer['result']['columns'] if not c.get('repeats')]


@pytest.mark.parametrize('path', ['rules', 'model'])
def test_a_breakdown_by_month_is_named_for_its_grain_and_its_months_read_in_order(answers, path):
    a = answers[BY_MONTH][path]
    assert a['behaviour'] == 'figure', a
    assert a['rows'] == {'2025-01-01': 10681305.25, '2025-02-01': 10248492.05, '2025-03-01': 9479714.83}
    assert labels(a) == ['Month', 'Current NNA']
    assert [r.get('dimension_text', [None])[0] for r in a['result']['rows'] if not r.get('total')] == [
        'January 2025', 'February 2025', 'March 2025']
    assert a['answer_text'].startswith('Current NNA for Q1 2025 by month, in USD: January 2025 10,681,305.25; '
                                       'February 2025 10,248,492.05; March 2025 9,479,714.83 (')
    assert '\nTotal: USD 30,409,512.13\n' in a['answer_text']
    table = a['display']['table']
    assert [c['label'] for c in table['columns']] == ['Month', 'Current NNA']
    assert [r[0] for r in table['rows']] == ['January 2025', 'February 2025', 'March 2025']


@pytest.mark.parametrize('path', ['rules', 'model'])
def test_a_comparison_shows_each_figure_once_then_its_difference(answers, path):
    a = answers[COMPARED][path]
    assert a['behaviour'] == 'combined', a
    assert labels(a) == ['Business line', 'Certified NNA', 'Current NNA', 'Difference']
    columns = [c for c in a['result']['columns'] if c['kind'] != 'dimension']
    rows = a['result']['rows']
    assert [row['dimensions'][0].get('value') for row in rows] == ['EMERGING', 'HNW', 'S5100', 'UHNW', None]
    assert [row['dimension_labels'][0] for row in rows if not row.get('total')] == [
        'Emerging Affluent', 'High Net Worth', 'Core Affluent', 'Ultra High Net Worth']
    values = [[cell['value']['value'] for c, cell in zip(columns, row['cells']) if not c.get('repeats')]
              for row in rows]
    assert values == COMPARED_VALUES
    assert all(certified != current and Decimal(difference) != 0 for certified, current, difference in values)
    repeated = {c['id']: c['repeats'] for c in columns if c.get('repeats')}
    comparison, = a['history'][-1]['plan']['comparisons']
    assert comparison['kind'] == 'contexts'
    aliases = {comparison['id'] + '/base': comparison['left'], comparison['id'] + '/other': comparison['right']}
    assert all(aliases.get(alias) == source for alias, source in repeated.items())
    for source in (comparison['left'], comparison['right']):
        assert sum(c['id'] == source and not c.get('repeats') for c in columns) == 1
    for row in rows:
        figures = {c['id']: cell['value']['value'] for c, cell in zip(columns, row['cells'])}
        assert all(figures[i] == figures[source] for i, source in repeated.items())
    lines = a['answer_text'].splitlines()
    assert lines[0] == 'December 2024: Business line | Certified NNA | Current NNA | Difference'
    assert lines[2:7] == [label + ' | ' + ' | '.join('USD ' + value for value in values)
                          for label, values in zip(['Emerging Affluent', 'High Net Worth',
                                                    'Core Affluent', 'Ultra High Net Worth', 'Total'],
                                                   COMPARED_VALUES)]
    assert [c['label'] for c in a['display']['table']['columns']][:4] == labels(a)
    assert all(a['answer_text'].count(value) == 1 for values in COMPARED_VALUES for value in values)


@pytest.mark.parametrize('path', ['rules', 'model'])
def test_current_nna_is_named_as_asked(answers, path):
    a = answers[CURRENT][path]
    assert a['behaviour'] == 'figure' and a['figure'] == 9479714.83, a
    assert labels(a) == ['Current NNA']
    assert a['answer_text'].startswith('Current NNA for March 2025: USD 9,479,714.83 (')


@pytest.mark.parametrize('path', ['rules', 'model'])
def test_a_count_is_named_for_what_it_counts(answers, path):
    a = answers[ACCOUNTS][path]
    assert a['behaviour'] == 'figure' and a['figure'] == 143, a
    assert labels(a) == ['Accounts with net new assets']
    assert a['answer_text'].startswith('Accounts with net new assets, March 2025: 143 (')


@pytest.mark.parametrize('path', ['rules', 'model'])
def test_a_count_of_what_has_no_period_declines_in_plain_words(answers, path):
    a = answers[UNDATED][path]
    assert a['behaviour'] == 'abstain' and a.get('figure') is None and not a.get('result'), a
    assert a['reason'] == 'Accounts have no period of their own; which figure should they be counted by? No figure is given.'
    assert 'binding' not in a['answer_text']


@pytest.mark.parametrize('question', QUESTIONS)
@pytest.mark.parametrize('path', ['rules', 'model'])
def test_no_answer_shows_an_internal_id(answers, question, path):
    found = {text: ids_in(text) for text in shown(answers[question][path]) if ids_in(text)}
    assert not found, found


@pytest.mark.parametrize('text,ids', [('Total: m0: USD 1', ['m0']), ('by t0', ['t0']), ('Difference x0', ['x0']),
                                      ('Certified NNA · c/base', ['c/base']), ('T0', ['T0']), ('d1 | M2', ['d1', 'M2']),
                                      ('Q1 2025 · FY26-Q1 · March 2025 · USD 5,929,714.83', [])])
def test_the_id_check_finds_every_id_shape_and_nothing_else(text, ids):
    assert ids_in(text) == ids


@pytest.mark.parametrize('path', ['rules', 'model'])
def test_the_mode_word_names_the_side_it_stands_for_in_a_comparison(answers, path):
    a = answers[REVERSED][path]
    assert a['behaviour'] == 'combined', a
    assert labels(a) == ['Business line', 'Current NNA', 'Certified NNA', 'Difference']
    columns = [c for c in a['result']['columns'] if c['kind'] != 'dimension' and not c.get('repeats')]
    shown = [[cell['value']['value'] for c, cell in zip([c for c in a['result']['columns'] if c['kind'] != 'dimension'],
                                                       row['cells']) if not c.get('repeats')]
             for row in a['result']['rows']]
    assert [c['label'] for c in columns] == ['Current NNA', 'Certified NNA', 'Difference']
    assert shown == [[current, certified, str(-Decimal(difference))] for certified, current, difference in COMPARED_VALUES]
    assert a['answer_text'].splitlines()[0] == 'December 2024: Business line | Current NNA | Certified NNA | Difference'


@pytest.mark.parametrize('surface', ['/ask', '/ask compact', 'mcp ask', '/assist', 'mcp assist'])
def test_api_and_mcp_clients_see_each_compared_figure_once(client, surface):
    from tests.integration.test_answer_text import mcp_wire
    who = 'analyst_enterprise.meridian@example.com'
    body = {'question': COMPARED, 'compact': surface.endswith('compact')}
    if surface.startswith('mcp'):
        tool = surface.split()[1]
        if tool == 'assist':
            body.pop('compact')
        reply = mcp_wire(client, '/mcp/answer' if tool == 'ask' else '/mcp/assist', who, tool, body)
        assert not reply.get('isError'), reply
        a = reply['structuredContent']
    else:
        if surface == '/assist':
            body.pop('compact')
        response = client.post(surface.split()[0], json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        a = response.json()
    columns = a['result']['columns']
    assert not any('repeats' in c for c in columns), columns
    assert [c['label'] for c in columns] == ['Business line', 'Certified NNA', 'Current NNA', 'Difference']
    assert all(len(row['cells']) == sum(c['kind'] != 'dimension' for c in columns) for row in a['result']['rows'])
    assert [[cell['value']['value'] for cell in row['cells']] for row in a['result']['rows']] == COMPARED_VALUES


@pytest.mark.parametrize('surface', ['/assist', 'mcp assist'])
def test_a_single_figure_from_the_assistant_passes_the_client_schema(client, surface):
    from tests.integration.test_answer_text import mcp_wire
    who = 'analyst_enterprise.meridian@example.com'
    body = {'question': CURRENT}
    if surface == 'mcp assist':
        reply = mcp_wire(client, '/mcp/assist', who, 'assist', body)
        assert not reply.get('isError'), reply
        a = reply['structuredContent']
    else:
        response = client.post('/assist', json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        a = response.json()
    assert a['figure'] == 9479714.83, a
    assert not any('repeats' in c for c in (a.get('result') or {}).get('columns', []))
