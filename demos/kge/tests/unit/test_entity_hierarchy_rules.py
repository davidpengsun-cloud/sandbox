"""Declared hierarchy levels and count nouns retain their distinct roles."""
import datetime as dt
from dataclasses import replace

import pytest

from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Context, Join
from tests.plan_fixtures import semantics
from tests.unit.test_plan_compiler import connection
from tests.unit.test_rules_derived_shapes import declared, execute, read_checked


def hierarchy(monkeypatch, depth):
    sem = semantics(joined=True)
    context = sem.contexts['ctx']
    member = replace(context.joins[0], kind='hierarchy' if depth else 'key')
    joins = (member,)
    column = 'u.members.grouping'
    if depth == 2:
        sem.tables['u.regions'] = {'grouping': 'VARCHAR', 'area': 'VARCHAR'}
        region = Join('region_join', 'hierarchy', 'members.grouping = regions.grouping', None, None,
                      'u.members', 'u.regions', False)
        joins += (region,)
        column = 'u.regions.area'
    sem.contexts['ctx'] = Context(context.id, context.label, context.description, context.term,
        context.measure, context.expression, context.aggregation, context.grain, context.fact,
        context.values, context.filters, context.selectors, joins)
    sem.declared = {join.id: join for join in joins}
    sem.d.GROUP_BY = {'by region': column}
    return declared(monkeypatch, sem)


@pytest.mark.parametrize('depth', [0, 1, 2])
def test_grouping_uses_the_declared_hierarchy_depth_and_keeps_figures(monkeypatch, depth):
    eng, vocab = hierarchy(monkeypatch, depth)
    checked = read_checked(eng, vocab, 'Metric by region for Q2 2025')
    dimension, = checked.plan.dimensions
    binding, = dimension.bindings
    assert dimension.kind == ('hierarchy' if depth else 'column')
    assert binding.level == (depth or None)
    rows = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'),
            (dt.date(2025, 5, 10), 20, 'B', 2, 'x'),
            (dt.date(2025, 6, 10), 30, 'C', 3, 'y')]
    with connection(rows) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'North'), (2, 'x', 'North'), (3, 'y', 'South')")
        if depth == 2:
            con.execute('CREATE TABLE regions(grouping VARCHAR, area VARCHAR)')
            con.execute("INSERT INTO regions VALUES ('North', 'East'), ('South', 'West')")
        actual = execute(checked, con)
    assert {r[dimension.id]: r[checked.plan.measures[0].id] for r in actual} == (
        {'East': 30, 'West': 30} if depth == 2 else {'North': 30, 'South': 30})


def test_complete_entity_counts_compose_with_a_hierarchy_breakdown(monkeypatch):
    eng, vocab = hierarchy(monkeypatch, 1)
    checked = read_checked(eng, vocab, 'Metric with number of Entities by region for Q2 2025')
    measured, counted = checked.plan.measures
    assert measured.kind == 'measure'
    dimension, = checked.plan.dimensions
    assert counted.kind == 'distinct_count' and dimension.kind == 'hierarchy'
    rows = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'),
            (dt.date(2025, 5, 10), 20, 'A', 1, 'x'),
            (dt.date(2025, 6, 10), 30, 'B', 2, 'x'),
            (dt.date(2025, 6, 11), 40, 'C', 3, 'y')]
    with connection(rows) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'North'), (2, 'x', 'North'), (3, 'y', 'South')")
        actual = execute(checked, con)
    assert {r[dimension.id]: r[counted.id] for r in actual} == {'North': 2, 'South': 1}
    assert {r[dimension.id]: r[measured.id] for r in actual} == {'North': 60, 'South': 40}


def counting(monkeypatch):
    sem = semantics()
    sem.entities = {
        'transaction': {'label': 'transaction', 'table': 'u.events', 'key': ['entity_id', 'source']},
        'household': {'label': 'household', 'table': 'u.events', 'key': ['entity_id']},
    }
    sem.d.ENTITY_WORDS = {'household': 'household'}
    sem.d.GROUP_BY = {'by household': 'u.events.entity_id'}
    for identifier, entity in sem.entities.items():
        key = ', '.join('events.' + column for column in entity['key'])
        expression = 'count(DISTINCT ' + ('(' + key + ')' if len(entity['key']) > 1 else key) + ')'
        context = 'context:count:' + identifier
        sem.contexts[context] = Context(context, identifier + ' count', '', identifier + ' count', 'count:' + identifier,
            expression, 'count', 'day', 'u.events', tuple('u.events.' + c for c in entity['key']), (), (), ())
    eng, base = declared(monkeypatch, sem)
    aliases = [('Metric', 'ctx'), *sem.d.GROUP_BY.items(), ('transactions', 'transaction'),
               ('account', 'transaction'), ('accounts', 'transaction'), ('households', 'household')]
    specs = [MeasureSpec('u.metric', 'USD', 'entities', True),
             *(MeasureSpec('count:' + identifier, None, identifier, False) for identifier in sem.entities)]
    return eng, build_vocabulary(sem, base.binding, ['u'], ['u'], specs, aliases=aliases)


@pytest.mark.parametrize('phrase', [
    'Metric with number of transactions by household',
    'Metric by household with number of transactions',
    'Metric with account counts by household',
    'Metric by household with account counts',
    'number of transactions by household',
    'account counts by household',
    'account counts and Metric by household',
    'number of transactions and Metric by household',
])
def test_counted_entity_is_owned_by_count_words_and_uses_the_complete_key(monkeypatch, phrase):
    eng, vocab = counting(monkeypatch)
    checked = read_checked(eng, vocab, phrase + ' for Q2 2025')
    counted, = [m for m in checked.plan.measures if m.kind == 'distinct_count']
    measured = [m for m in checked.plan.measures if m.kind == 'measure']
    assert len(measured) == int('Metric' in phrase)
    dimension, = checked.plan.dimensions
    assert counted.kind == 'distinct_count' and counted.entity.id == 'transaction'
    rows = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'),
            (dt.date(2025, 5, 10), 20, 'A', 1, 'x'),
            (dt.date(2025, 6, 10), 30, 'A', 1, 'y'),
            (dt.date(2025, 6, 11), 40, 'A', 1, None),
            (dt.date(2025, 4, 10), 50, 'B', 2, 'x')]
    with connection(rows) as con:
        actual = execute(checked, con)
    assert {r[dimension.id]: r[counted.id] for r in actual} == {1: 2, 2: 1}
    if measured:
        assert {r[dimension.id]: r[measured[0].id] for r in actual} == {1: 100, 2: 50}


def test_count_of_an_entity_without_a_measure_keeps_its_complete_key(monkeypatch):
    eng, vocab = counting(monkeypatch)
    checked = read_checked(eng, vocab, 'count of households by category for Q2 2025')
    counted, = checked.plan.measures
    dimension, = checked.plan.dimensions
    assert counted.entity.id == 'household'
    rows = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'),
            (dt.date(2025, 5, 10), 20, 'A', 1, 'y'),
            (dt.date(2025, 6, 10), 30, 'B', 2, 'x'),
            (dt.date(2025, 6, 11), 40, 'B', 3, 'x')]
    with connection(rows) as con:
        actual = execute(checked, con)
    assert {r[dimension.id]: r[counted.id] for r in actual} == {'A': 1, 'B': 2}


@pytest.mark.parametrize('text', ['How many accounts had Metric', 'number of transactions having Metric',
                                'count of transactions for Metric', 'accounts count with Metric'])
def test_a_leading_count_keeps_the_measure_as_its_population(monkeypatch, text):
    eng, vocab = counting(monkeypatch)
    checked = read_checked(eng, vocab, text + ' for Q2 2025')
    counted, = checked.plan.measures
    assert counted.kind == 'distinct_count' and counted.entity.id == 'transaction'
    rows = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'),
            (dt.date(2025, 5, 10), 20, 'A', 1, 'x'),
            (dt.date(2025, 6, 10), 30, 'A', 1, 'y')]
    with connection(rows) as con:
        actual = execute(checked, con)
    assert actual[0][counted.id] == 2


def test_model_cannot_replace_the_counted_entity_with_the_grouping_entity(monkeypatch):
    from kge.semantics.coverage import ground_plan
    from kge.semantics.plan import Name
    from kge.semantics.plancheck import CheckedPlan, check_plan
    eng, vocab = counting(monkeypatch)
    text = 'Metric with number of transactions by household for Q2 2025'
    plan = read_checked(eng, vocab, text).plan
    counted = next(m for m in plan.measures if m.kind == 'distinct_count')
    wrong = plan.model_copy(update={'measures': tuple(m.model_copy(update={'entity': Name(id='household', evidence=())})
                                                    if m is counted else m for m in plan.measures)})
    assert not isinstance(check_plan(ground_plan(wrong, text, 'turn', vocab), vocab, text), CheckedPlan)


@pytest.mark.parametrize('phrase,expected', [('Which transactions', set()), ('number of transactions', {'transaction'})])
def test_selection_words_do_not_add_a_numeric_count_intent(monkeypatch, phrase, expected):
    from kge.semantics.coverage import counted_names, ledger
    _, vocab = counting(monkeypatch)
    assert counted_names(ledger(phrase, 'turn', vocab), phrase, vocab) == expected
