"""Text filters belong to their declared count's population and never narrow another output."""
import datetime as dt
from copy import deepcopy

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import build_vocabulary
from tests.unit.test_declared_metric_gates import declared, rules
from tests.unit.test_measure_capability_figures import checked
from tests.unit.test_plan_compiler import connection


ROWS = [(dt.date(2025, 4, 10), value, category, entity, source) for value, category, entity, source in (
    (4, 'A', 1, 'Alpha'),
    (6, 'A', 1, 'Alpha'),
    (9, 'B', 2, 'xAlpha'),
    (5, 'B', 3, 'Beta'),
    (8, 'A', 4, None),
    (None, 'A', 5, 'Alpha'),
    (-7, 'B', 6, 'Alpha'),
)]


def prepared(monkeypatch, operation):
    engine, base = declared(monkeypatch)
    sem = engine.semantics
    count = next(row['params'] for row in sem.d.VOCABULARY if row['kind'] == 'count_metric')
    count['filters'] = [dict(column='u.events.source', op=operation, value='Al')]
    sem.d.VOCABULARY.append(dict(kind='count_metric', params={**deepcopy(count), 'id': 'all_entity_count',
        'term': 'all-entity-count', 'label': 'All entity count', 'filters': []}))
    engine.term_names.append('All entity count')
    vocabulary = build_vocabulary(sem, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('Units', 'units'), *sem.d.GROUP_BY.items()])
    return engine, vocabulary


def proposal(engine, vocabulary, text):
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'propose', reply
    return reply.plan


@pytest.mark.parametrize('operation,expected', [('contains', 2), ('starts_with', 1)])
def test_declared_count_text_filters_have_hand_counted_figures_on_both_paths(monkeypatch, operation, expected):
    engine, vocabulary = prepared(monkeypatch, operation)
    text = 'Metric with Entity count'
    plan = proposal(engine, vocabulary, text)
    result = compile_plan(checked(plan, text, vocabulary))
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        cursor = con.execute(result.answer.sql)
        values = dict(zip([column[0] for column in cursor.description], cursor.fetchone(), strict=True))
    count, = [output for output in plan.measures if output.kind == 'distinct_count']
    amount, = [output for output in plan.measures if output.kind == 'measure']
    assert count.declaration.id == 'entity_count'
    assert values[count.id] == expected
    assert values[amount.id] == 32


@pytest.mark.parametrize('operation', ['contains', 'starts_with'])
@pytest.mark.parametrize('recipient', ['measure', 'other-count', 'global'])
def test_a_count_text_declaration_cannot_filter_another_output_or_the_whole_plan(monkeypatch, operation, recipient):
    engine, vocabulary = prepared(monkeypatch, operation)
    owner = proposal(engine, vocabulary, 'Entity count')
    count, = owner.measures
    original, = count.filters
    if recipient == 'measure':
        text = 'Metric with Entity count'
        plan = proposal(engine, vocabulary, text)
        target = next(output for output in plan.measures if output.kind == 'measure')
    elif recipient == 'other-count':
        text = 'All entity count'
        plan = proposal(engine, vocabulary, text)
        target, = plan.measures
        assert target.declaration.id == 'all_entity_count'
    else:
        text, plan, target = 'Entity count', owner, None
    copied = original.model_copy(update={'id': 'unrequested-text-filter'})
    if target is None:
        wrong = plan.model_copy(update={'filters': (copied,)})
    else:
        wrong = plan.model_copy(update={'measures': tuple(output.model_copy(update={'filters': (*output.filters, copied)})
            if output.id == target.id else output for output in plan.measures)})
    decision = check_plan(ground_plan(wrong, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'undeclared text matching' in ' '.join(decision.missing)


@pytest.mark.parametrize('change', ['operation', 'value', 'column'])
def test_the_count_requires_its_exact_declared_text_filter(monkeypatch, change):
    engine, vocabulary = prepared(monkeypatch, 'contains')
    text = 'Entity count'
    plan = proposal(engine, vocabulary, text)
    count, = plan.measures
    predicate, = count.filters
    if change == 'operation':
        predicate = predicate.model_copy(update={'op': 'starts_with'})
    elif change == 'value':
        predicate = predicate.model_copy(update={'value': predicate.value.model_copy(update={'value': 'Beta'})})
    else:
        predicate = predicate.model_copy(update={'left': predicate.left.model_copy(update={
            'name': predicate.left.name.model_copy(update={'id': 'u.events.category'})})})
    wrong = plan.model_copy(update={'measures': (count.model_copy(update={'filters': (predicate,)}),)})
    decision = check_plan(ground_plan(wrong, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'declared local filters' in ' '.join(decision.missing)


@pytest.mark.parametrize('operation', ['contains', 'starts_with'])
def test_a_count_text_declaration_cannot_change_its_declared_context(monkeypatch, operation):
    engine, vocabulary = prepared(monkeypatch, operation)
    text = 'Entity count'
    plan = proposal(engine, vocabulary, text)
    count, = plan.measures
    wrong = plan.model_copy(update={'measures': (count.model_copy(update={
        'context': count.context.model_copy(update={'id': 'units'})}),)})
    decision = check_plan(ground_plan(wrong, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'undeclared text matching' in ' '.join(decision.missing)
