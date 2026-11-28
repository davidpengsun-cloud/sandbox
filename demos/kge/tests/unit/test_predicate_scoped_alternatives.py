"""A scoped lexical predicate displaces only its overlapping parser alternatives."""
from copy import copy
from dataclasses import replace
import datetime as dt
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger
from kge.semantics.planvocabulary import build_vocabulary
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, metric_vocabulary
from tests.unit.test_filter_grammar import ROWS
from tests.unit.test_plan_compiler import connection
from tests.unit.test_declared_metric_gates import declared, execute


def scoped(monkeypatch):
    sem = semantics()
    sem.tables['u.archive'] = {'category': 'VARCHAR'}
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {'metric': 'Metric'}, {}, {}
    shared = [{'kind': 'predicate', 'params': {'phrases': ['accepted entries'], 'column': column, 'op': 'eq', 'value': 'A'}}
              for column in ('u.events.category', 'u.archive.category')]
    other = [
        {'kind': 'predicate', 'params': {'phrases': ['archive entries'], 'column': 'u.archive.category', 'op': 'eq', 'value': 'A'}},
        {'kind': 'predicate', 'params': {'phrases': ['private entries'], 'column': 'u.events.private', 'op': 'eq', 'value': 'secret'}},
    ]
    sem.d.VOCABULARY = [*shared, *other]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    snapshot = copy(sem)
    snapshot.d = copy(sem.d)
    snapshot.d.VOCABULARY = [shared[0]]
    words = compiled(snapshot.d.VOCABULARY)
    snapshot.d.FILTERS, snapshot.d.FILTER_COLUMNS = words.filters, words.columns
    vocabulary = metric_vocabulary(snapshot, aliases=[('Metric', 'ctx')])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return engine, vocabulary


def test_an_overlapping_parser_predicate_cannot_force_an_unrequested_fact(monkeypatch):
    engine, vocabulary = scoped(monkeypatch)
    text = 'Metric for accepted entries'
    request = parse(text, engine.term_names, engine)
    assert set(request.filtered) == {'u.events.category', 'u.archive.category'}
    assert 'u.archive.category' in vocabulary.entries
    piece, = [piece for piece in ledger(text, 'turn', vocabulary) if piece.kind == 'predicate']
    assert piece.candidates == ('u.events.category',)
    proposal = planrules.read(engine, request, vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'propose', proposal
    predicate, = proposal.plan.filters
    assert predicate.left.name.id == 'u.events.category'
    checked = check_plan(proposal.plan, vocabulary, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 10


@pytest.mark.parametrize('wording', ['archive entries', 'private entries'])
def test_a_different_unreadable_or_restricted_filter_is_never_displaced(monkeypatch, wording):
    engine, vocabulary = scoped(monkeypatch)
    text = 'Metric for accepted entries with ' + wording
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary,
                              SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'abstain', proposal
    assert any('predicate' in reason for reason in proposal.missing)


def test_a_canonical_lexical_filter_replaces_a_raw_name_filter_without_losing_the_condition(monkeypatch):
    engine, _ = scoped(monkeypatch)
    raw = {'kind': 'predicate', 'params': {'phrases': ['preferred rows'], 'column': 'u.events.source', 'op': 'eq', 'value': 'y'}}
    lexical = {'kind': 'predicate', 'params': {'phrases': ['preferred rows'], 'column': 'u.events.category', 'op': 'eq', 'value': 'A'}}
    words = compiled([raw])
    engine.FILTERS, engine.FILTER_COLUMNS = words.filters, words.columns
    snapshot = copy(engine.semantics)
    snapshot.d = copy(snapshot.d)
    snapshot.d.VOCABULARY = [lexical]
    words = compiled([lexical])
    snapshot.d.FILTERS, snapshot.d.FILTER_COLUMNS = words.filters, words.columns
    vocabulary = metric_vocabulary(snapshot, aliases=[('Metric', 'ctx')])
    text = 'Metric for preferred rows'
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary,
                              SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'propose', proposal
    predicate, = proposal.plan.filters
    assert predicate.left.name.id == 'u.events.category' and predicate.value.value == 'A'
    checked = check_plan(proposal.plan, vocabulary, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 10


def test_a_longer_declared_predicate_displaces_an_enclosed_bare_member_on_another_column(monkeypatch):
    engine, _ = scoped(monkeypatch)
    canonical = {'kind': 'predicate', 'params': {'phrases': ['category A'], 'column': 'u.events.category', 'op': 'eq', 'value': 'A'}}
    raw = [{'kind': 'value', 'params': {'column': 'u.events.source', 'values': ['A']}}, canonical]
    words = compiled(raw)
    engine.FILTERS, engine.FILTER_COLUMNS = words.filters, words.columns
    snapshot = copy(engine.semantics)
    snapshot.d = copy(snapshot.d)
    snapshot.d.VOCABULARY = [canonical]
    words = compiled([canonical])
    snapshot.d.FILTERS, snapshot.d.FILTER_COLUMNS = words.filters, words.columns
    vocabulary = metric_vocabulary(snapshot, aliases=[('Metric', 'ctx')])
    text = 'Metric for category A'
    request = parse(text, engine.term_names, engine)
    assert set(request.filtered) == {'u.events.source', 'u.events.category'}
    proposal = planrules.read(engine, request, vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'propose', proposal
    predicate, = proposal.plan.filters
    assert predicate.left.name.id == 'u.events.category'
    checked = check_plan(proposal.plan, vocabulary, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 10


def test_a_shorter_public_predicate_cannot_hide_a_longer_restricted_request(monkeypatch):
    engine, vocabulary = scoped(monkeypatch)
    raw = {'kind': 'predicate', 'params': {'phrases': ['accepted entries preference'],
        'column': 'u.events.private', 'op': 'eq', 'value': 'secret'}}
    words = compiled([raw])
    engine.FILTERS, engine.FILTER_COLUMNS = words.filters, words.columns
    text = 'Metric for accepted entries preference'
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary,
                              SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'abstain' and 'predicate' in ' '.join(proposal.missing)


@pytest.mark.parametrize('text,expected', [('Metric with row value at least 5', 10),
                                          ('Entity count with row value at least 5', 1)])
def test_a_column_comparison_and_typed_literal_filter_the_declared_metric(monkeypatch, text, expected):
    engine, base = declared(monkeypatch)
    vocabulary = build_vocabulary(engine.semantics, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('row value', 'u.events.value')])
    request = parse(text, engine.term_names, engine)
    assert request.filters == []
    proposal = planrules.read(engine, request, vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'propose', proposal
    predicate, = proposal.plan.filters
    assert predicate.left.name.id == 'u.events.value' and predicate.op == 'ge' and predicate.value.value == '5'
    output, = proposal.plan.measures
    assert execute(proposal.plan, text, vocabulary)[0][output.id] == expected


def test_a_column_comparison_never_guesses_between_readable_columns(monkeypatch):
    engine, base = declared(monkeypatch)
    vocabulary = build_vocabulary(engine.semantics, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('row value', 'u.events.value'), ('row value', 'u.events.entity_id')])
    text = 'Metric with row value at least 5'
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary,
                              SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'abstain' and 'one declared readable column' in ' '.join(proposal.missing)


def test_a_restricted_or_unknown_column_comparison_cannot_produce_an_executable_plan(monkeypatch):
    engine, base = declared(monkeypatch)
    vocabulary = build_vocabulary(engine.semantics, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('private row value', 'u.events.private')])
    for text in ('Metric with private row value at least 5', 'Metric with undeclared row value at least 5'):
        proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary,
                                  SimpleNamespace(graph=True, glossary=True))
        if proposal.kind == 'propose':
            assert not isinstance(check_plan(proposal.plan, vocabulary, text), CheckedPlan)


@pytest.mark.parametrize('grouping,expected', [
    ('by category', [('A', 15)]),
    ('by source', [('x', 10), ('y', 5)]),
])
def test_context_bindings_and_grouping_preserve_a_shadowed_parser_predicate(monkeypatch, grouping, expected):
    sem = semantics()
    sem.measure_declarations = {}
    sem.tables['u.canonical'] = {'category': 'VARCHAR'}
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', bindings={
        'u.canonical.category': 'u.events.category'})
    sem.d.ALIASES, sem.d.UNIT_WORDS = {'metric': 'Metric'}, {}
    sem.d.GROUP_BY = {'by category': 'u.canonical.category', 'by source': 'u.events.source'}
    sem.d.VOCABULARY = [
        dict(kind='value', params=dict(column='u.events.source', values=['A'])),
        dict(kind='predicate', params=dict(column='u.events.category', phrases=['category A'], op='eq', value='A')),
    ]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    snapshot = copy(sem)
    snapshot.d = planrules.bound_domain(sem.d, sem.contexts['ctx'].bindings)
    vocabulary = metric_vocabulary(snapshot, aliases=[('Metric', 'ctx'), *snapshot.d.GROUP_BY.items()])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    text = 'Metric for category A ' + grouping
    request = parse(text, engine.term_names, engine)
    assert set(request.filtered) == {'u.events.source', 'u.events.category'}
    proposal = planrules.read(engine, request, vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert proposal.kind == 'propose', proposal
    predicate, = proposal.plan.filters
    assert predicate.left.name.id == 'u.events.category' and predicate.value.value == 'A'
    dimension, = proposal.plan.dimensions
    assert dimension.bindings[0].column.id == snapshot.d.GROUP_BY[grouping]
    checked = check_plan(proposal.plan, vocabulary, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    observed = [(dt.date(2025, 4, 10), value, category, entity, source)
                for value, category, entity, source in (
                    (10, 'A', 1, 'x'), (7, 'B', 2, 'x'), (5, 'A', 3, 'y'), (3, None, 4, 'y'))]
    with connection(observed) as con:
        cursor = con.execute(result.answer.sql)
        columns = [column[0] for column in cursor.description]
        result_rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    output, = proposal.plan.measures
    actual = sorted((row[dimension.id], row[output.id]) for row in result_rows)
    assert actual == expected
