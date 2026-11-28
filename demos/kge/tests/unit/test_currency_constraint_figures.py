"""Output currency constraints preserve independent row constraints and temporal eligibility."""
from dataclasses import replace

import duckdb
import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry
from kge.services.ask.engine.question import read_period
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary
from tests.unit.test_currency_candidates import clarify, coordinator, revenues


def foreign_column_vocabulary():
    sem = semantics()
    sem.tables['u.events']['revenue_eur'] = 'DECIMAL'
    sem.d.GROUP_BY = {'by revenue_eur': 'u.events.revenue_eur'}
    return vocabulary(sem, values=[Entry('USD', 'currency', 'USD', None)],
                      aliases=[('by revenue_eur', 'u.events.revenue_eur')])


def test_a_usd_output_keeps_its_foreign_currency_row_filter_and_figure():
    """The EUR threshold selects rows; it does not change the USD output's currency."""
    scoped = foreign_column_vocabulary()
    question = 'Metric in USD revenue_eur at least 20'
    column, = (piece for piece in ledger(question, 'turn', scoped) if piece.text == 'revenue_eur')
    assert column.candidates == ('u.events.revenue_eur',)
    predicate = {'kind': 'compare', 'id': 'f',
                 'left': {'kind': 'column', 'name': name('u.events.revenue_eur'), 'path': []},
                 'op': 'ge', 'value': {'kind': 'integer', 'value': 20}, 'evidence': []}
    plan = grounded(model(filters=[predicate]), question, scoped,
                    {'revenue_eur': 'f', 'at least': 'f', '20': 'f'})
    checked = check_plan(plan, scoped, question)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with duckdb.connect(':memory:') as connection:
        connection.execute('CREATE TABLE events(value DECIMAL(12,2), revenue_eur DECIMAL(12,2))')
        connection.executemany('INSERT INTO events VALUES (?, ?)',
                               [(7, 10), (11, 20), (13, 30), (-5, 40), (17, None)])
        assert connection.execute(compiled.answer.sql).fetchone()[0] == 11 + 13


def test_a_usd_output_keeps_its_foreign_currency_dimension():
    scoped = foreign_column_vocabulary()
    question = 'Metric in USD by revenue_eur'
    dimension_piece, = (piece for piece in ledger(question, 'turn', scoped) if piece.operation == 'dimension')
    assert dimension_piece.candidates == ('u.events.revenue_eur',)
    dimension = {'kind': 'column', 'id': 'd',
                 'bindings': [{'context': name('ctx'), 'column': name('u.events.revenue_eur'),
                               'path': [], 'level': None}]}
    plan = grounded(model(dimensions=[dimension]), question, scoped, {'by revenue_eur': 'd'})
    checked = check_plan(plan, scoped, question)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []


@pytest.mark.parametrize('one_bound', [False, True], ids=['no-period-binding', 'one-period-binding'])
@pytest.mark.parametrize('phrase,operation', [
    ('latest released', 'latest_released'),
    ('published interval', 'latest_released'),
    ('year to date', 'ytd'),
    ('month to date', 'mtd'),
    ('quarter to date', 'qtd'),
    ('compared with previous period', 'previous_period'),
], ids=['structural-latest', 'declared-latest', 'ytd', 'mtd', 'qtd', 'previous-period'])
def test_temporal_clarification_keeps_only_period_bound_contexts(one_bound, phrase, operation):
    """Relative periods need bindings even without an explicit date or a time breakdown."""
    scoped, (first, second) = revenues(('USD', 'USD'), bound=one_bound)
    scoped.semantics.d.NAMED_PERIODS = {'published interval': 'latest'}
    if one_bound:
        scoped = replace(scoped, contexts={**scoped.contexts,
            second: replace(scoped.contexts[second], fact='u.archive', grain='day')})
    question = 'Revenue ' + phrase
    assert read_period(question)[0] is None
    assert any(piece.operation == operation for piece in ledger(question, 'turn', scoped))
    result = coordinator(scoped).prepare(question, clarify(scoped, question, (first, second)))
    if one_bound:
        assert result['kind'] == 'clarify'
        assert [choice['id'] for choice in result['choices']] == [first]
    else:
        assert result['kind'] == 'abstain'
        assert result['missing'] == ['missing compatible period binding']
        assert result['choices'] == []
