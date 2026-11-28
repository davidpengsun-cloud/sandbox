"""Issue 474: a pack filter phrase that names a list of values, and a question that names several declared values of one
column, each read as one IN predicate, checked as declared and compiled through the SQL gate."""
import datetime as dt
from types import SimpleNamespace

import pytest

from kge.compiler.checks import listed_values_problems
from kge.semantics.coverage import ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name, semantics, metric_vocabulary as vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_plan_phrase_grammar import compile_read

ROWS = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'), (dt.date(2025, 5, 10), 7, 'B', 2, 'x'), (dt.date(2025, 6, 10), 5, 'C', 3, 'y')]
A_AND_B = 10 + 7
PREDICATES = [
    dict(kind='predicate', params=dict(column='u.events.category', phrases=[f'{c} category'], op='eq', value=c)) for c in 'ABC'
] + [
    dict(kind='predicate', params=dict(column='u.events.source', phrases=[f'{s} source'], op='eq', value=s)) for s in 'xy'
] + [dict(kind='predicate', params=dict(column='u.events.category', phrases=['early categories'], op='in', values=['A', 'B']))]


def declared(rows=PREDICATES):
    sem = semantics()
    sem.d.VOCABULARY = [dict(kind='value', params=dict(column='u.events.category', values=['A', 'B', 'C'])), *rows]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    return sem


def read_filters(text, rows=PREDICATES):
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, **vars(declared(rows).d))
    req = parse(text, [], domain)
    return list(zip(req.filters, req.filtered))


def in_plan(values):
    return model(filters=[dict(kind='in', id='f', left=dict(kind='column', name=name('u.events.category'), path=[]),
                               values=[dict(kind='text', value=v) for v in values], evidence=[])])


@pytest.mark.parametrize('predicate, problem', [
    (dict(op='in'), "in names two or more values, not None"),
    (dict(op='in', values=[]), "in names two or more values, not []"),
    (dict(op='in', values=['A']), "in names two or more values, not ['A']"),
    (dict(op='in', values=['A', 1]), "are all text or all numbers, not ['A', 1]"),
    (dict(op='in', values=[True, False]), "are all text or all numbers, not [True, False]"),
    (dict(op='in', values=['A', 'A']), "names each value once, not ['A', 'A']"),
    (dict(op='in', values=[1, 1.0]), "names each value once, not [1, 1.0]"),
    (dict(op='eq', value='A', values=['A', 'B']), "eq takes no values: only in and not_in name a list"),
    (dict(op='is_null', values=['A', 'B']), "is_null takes no values: only in and not_in name a list"),
])
def test_check_pack_names_each_invalid_list(predicate, problem):
    assert listed_values_problems(predicate) == [problem]


@pytest.mark.parametrize('predicate', [dict(op='in', values=['A', 'B']), dict(op='in', values=[100, 200.5]), dict(op='eq', value='A')])
def test_a_valid_list_or_a_plain_predicate_has_no_problem(predicate):
    assert listed_values_problems(predicate) == []


def test_in_with_a_value_is_reported_by_the_value_rule():
    from kge.compiler.vocabulary import NEEDS_VALUE, NEEDS_VALUES
    assert 'in' in NEEDS_VALUES and 'in' not in NEEDS_VALUE


@pytest.mark.parametrize('text, values', [
    ('Metric for A and B categories', ('A', 'B')),
    ('Metric for A or B category', ('A', 'B')),
    ('Metric for A category and B category', ('A', 'B')),
    ('Metric for A, B, and C categories', ('A', 'B', 'C')),
    ('Metric for B and A categories', ('B', 'A')),
    ('Metric for early categories', ('A', 'B')),
])
def test_a_list_of_one_column_s_declared_values_is_one_in(text, values):
    assert read_filters(text) == [(('category', 'IN', values), 'u.events.category')]
    piece, = [p for p in ledger(text, 'turn', vocabulary(declared())) if p.kind == 'predicate']
    assert (piece.candidates, piece.operation, piece.value) == (('u.events.category',), 'in', values)


def test_values_of_different_columns_stay_separate():
    filters = read_filters('Metric for A category and x source')
    assert {(('category', '=', 'A'), 'u.events.category'), (('source', '=', 'x'), 'u.events.source')} == set(filters)


def test_values_without_the_filter_s_noun_are_not_merged():
    assert ('category', 'IN', ('A', 'B')) not in [f for f, _ in read_filters('Metric for A and B')]


def test_a_list_that_reads_as_two_columns_is_not_merged():
    rows = [dict(kind='predicate', params=dict(column='u.events.category', phrases=[f'group {c}'], op='eq', value=c)) for c in 'AB'] + [
        dict(kind='predicate', params=dict(column='u.events.source', phrases=[f'{c} group'], op='eq', value=c)) for c in 'AB']
    filters = read_filters('Metric for group A and B group', rows)
    assert {(('category', '=', 'A'), 'u.events.category'), (('source', '=', 'B'), 'u.events.source')} == set(filters)


@pytest.mark.parametrize('text, assignment', [
    ('Metric for A and B categories', 'A and B categories'),
    ('Metric for A category and B category', 'A category and B category'),
    ('Metric for early categories', 'early categories'),
])
def test_a_model_in_plan_passes_the_checks_and_sums_the_listed_values(text, assignment):
    v = vocabulary(declared())
    plan = compile_read(in_plan(['A', 'B']), text, v, {assignment: 'f'})
    assert 'IN (' in plan.answer.sql
    with connection(ROWS) as con:
        assert con.execute(plan.answer.sql).fetchone()[0] == A_AND_B


@pytest.mark.parametrize('values', [['A', 'C'], ['A', 'B', 'C'], ['A', 'Z']])
def test_a_model_in_plan_that_differs_from_the_question_fails_the_checks(values):
    from tests.plan_fixtures import grounded
    v = vocabulary(declared())
    text = 'Metric for A and B categories'
    assert not isinstance(check_plan(grounded(in_plan(values), text, v, {'A and B categories': 'f'}), v, text), CheckedPlan)


@pytest.mark.parametrize('text, expected', [
    ('Metric for Alpha and Beta category', [('source', 'x'), ('category', 'B')]),
    ('Metric for Alpha category and Beta category', [('source', 'x'), ('category', 'A'), ('category', 'B')]),
])
def test_a_list_whose_value_alone_filters_another_column_reads_as_before(monkeypatch, text, expected):
    from kge.services.ask.engine import planrules
    sem = semantics()
    sem.measure_declarations, sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {}, {}, {}, {}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(column='u.events.source', phrases=['Alpha'], op='eq', value='x')),
                        *(dict(kind='predicate', params=dict(column='u.events.category', phrases=[f'{w} category'], op='eq', value=c))
                          for w, c in (('Alpha', 'A'), ('Beta', 'B')))]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem, aliases=[('Metric', 'ctx')])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **k: sem)
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    assert not [p for p in ledger(text, 'turn', v) if p.operation == 'in']
    plan = planrules.read(engine, parse(text, ['Metric'], sem.d), v, SimpleNamespace(graph=True, glossary=True)).plan
    assert [(f.left.name.id.rsplit('.', 1)[1], f.value.value) for f in plan.nodes() if f.kind == 'compare'] == expected


@pytest.mark.parametrize('text', ['Metric for Ultra High tier', 'Metric for the Ultra High tier in May'])
def test_a_phrase_nested_in_a_longer_one_on_the_same_column_gives_way_to_it(text):
    rows = [dict(kind='predicate', params=dict(column='u.events.category', phrases=[phrase], op='eq', value=value))
            for phrase, value in (('High tier', 'H'), ('Ultra High tier', 'U'))]
    assert read_filters(text, rows) == [(('category', '=', 'U'), 'u.events.category')]


def nested_vocabulary():
    sem = semantics()
    sem.measure_declarations, sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {}, {}, {}, {}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(column='u.events.source', phrases=['Alpha'], op='eq', value='x')),
                        dict(kind='predicate', params=dict(column='u.events.source', phrases=['Gamma'], op='eq', value='y')),
                        dict(kind='predicate', params=dict(column='u.events.category', phrases=['Prime Alpha'], op='eq', value='A'))]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    return vocabulary(sem, aliases=[('Metric', 'ctx')])


def equality(identifier, column, value):
    return dict(kind='compare', id=identifier, op='eq', left=dict(kind='column', name=name(column), path=[]),
                value=dict(kind='text', value=value), evidence=[])


@pytest.mark.parametrize('filters, accepted', [
    ([('c', 'u.events.category', 'A'), ('p', 'u.events.source', 'x')], True),
    ([('c', 'u.events.category', 'A')], True),
    ([('p', 'u.events.source', 'x')], False),
    ([('c', 'u.events.category', 'A'), ('p', 'u.events.source', 'y')], False),
])
def test_a_name_holding_another_column_s_name_owns_that_equality_only_beside_its_own(filters, accepted):
    from tests.plan_fixtures import grounded
    v, text = nested_vocabulary(), 'Metric for Prime Alpha'
    plan = grounded(model(filters=[equality(*f) for f in filters]), text, v, {'Prime Alpha': [f[0] for f in filters]})
    assert isinstance(check_plan(plan, v, text), CheckedPlan) is accepted
