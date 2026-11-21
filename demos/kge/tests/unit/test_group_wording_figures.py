"""Pack-only group names select hand-counted rows, including missing regions."""
import pytest

from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import check_plan
from tests.unit.test_filter_grammar import execute


GROUPS = [
    {'kind': 'predicate', 'params': {'phrases': ['Midwest region'], 'column': 'u.events.category', 'op': 'eq', 'value': 'A'}},
    {'kind': 'predicate', 'params': {'phrases': ['Pacific region'], 'column': 'u.events.category', 'op': 'eq', 'value': 'B'}},
    {'kind': 'predicate', 'params': {'phrases': ['Heartland region'], 'column': 'u.events.category', 'op': 'in', 'values': ['A', 'B']}},
    {'kind': 'predicate', 'params': {'phrases': ['outside Heartland region'], 'column': 'u.events.category', 'op': 'not_in', 'values': ['A', 'B']}},
]


@pytest.mark.parametrize('wording,expected,excluded', [
    ('for Heartland region', 17, False),
    ('for Midwest and Pacific regions', 17, False),
    ('for Midwest or Pacific region', 17, False),
    ('for outside Heartland region', 8, True),
    ('excluding Heartland region', 8, True),
    ('excluding Midwest and Pacific regions', 8, True),
    ('except Midwest or Pacific region', 8, True),
])
def test_pack_groups_and_value_lists_have_identical_figures_with_nulls(monkeypatch, wording, expected, excluded):
    """A=10, B=7, C=5 and no region=3: membership=17, exclusion=5+3."""
    text = 'Metric ' + wording
    plan, vocab = execute(monkeypatch, text, expected, GROUPS)
    assert len(plan.filters) == 1
    sql = compile_plan(check_plan(plan, vocab, text)).answer.sql
    assert ' IN (' in sql
    assert (plan.filters[0].kind == 'any') == excluded
    if excluded:
        assert {node.kind for node in plan.filters[0].children} == {'not', 'null'}
        assert '"events"."category" IS NULL' in sql
