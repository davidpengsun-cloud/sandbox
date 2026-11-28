"""Compound context names and ambiguous predicates retain their declared meanings."""
import datetime as dt

import pytest

from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.unit.test_comparison_wording_families import filter_declarations, interpreted, rows


@pytest.mark.parametrize('text', [
    'Archived Amber group Metric for June 2025',
    'Archived June 2025 Amber group Metric',
    'Archived Metric for June 2025 for Amber group',
])
def test_declared_members_inside_a_compound_name_preserve_its_context_and_figures(monkeypatch, text):
    engine, vocabulary = filter_declarations(monkeypatch, [
        dict(kind='predicate', params=dict(column='u.archive.category', phrases=['Amber group'], op='eq', value='A')),
    ])
    plan, compiled = interpreted(engine, vocabulary, text)
    output, = plan.measures
    assert output.context.id == 'archived'
    predicate, = plan.filters
    assert predicate.left.name.id == 'u.archive.category' and predicate.value.value == 'A'
    result, = rows(compiled, [(dt.date(2025, 6, 10), 34, 'A', 1, 'x')], [
        (dt.date(2025, 6, 10), 25, 'A', 1, 'x'),
        (dt.date(2025, 6, 10), 99, 'B', 2, 'y'),
    ])
    assert result[output.id] == 25


@pytest.mark.parametrize('left,right,column,value', [
    ('eq', 'ne', 'u.events.source', 'x'),
    ('gt', 'le', 'u.events.value', 10),
    ('is_null', 'is_not_null', 'u.events.source', None),
])
def test_one_phrase_with_opposite_declared_operations_needs_clarification(monkeypatch, left, right, column, value):
    declarations = [dict(kind='predicate', params=dict(column=column, phrases=['Amber group'], op=operation,
                    **({'value': value} if value is not None else {}))) for operation in (left, right)]
    engine, vocabulary = filter_declarations(monkeypatch, declarations)
    text = 'Metric for June 2025 for Amber group'
    reply = planrules.read(engine, parse(text, ['Metric', 'Archived Metric'], engine), vocabulary, PRESETS['graph'])
    assert reply.kind != 'propose' or not isinstance(check_plan(reply.plan, vocabulary, text), CheckedPlan)
