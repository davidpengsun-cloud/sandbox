"""Untrusted shape plans cannot change their requested operands or population."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import gate
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.planner import result_of
from kge.services.ask.engine.planshapes import compose
from tests.unit.test_answer_shapes import dimension
from tests.unit.test_plan_compiler import connection
from tests.unit.test_rules_derived_shapes import declared, read_checked
from tests.unit.test_plan_relational_matrix import replace_context
from tests.plan_fixtures import model, name, semantics


def changed(checked, text, update):
    raw = checked.plan.model_dump(mode='json', by_alias=True)
    update(raw)
    raw['obligations'] = []
    plan = ground_plan(QueryPlan.model_validate(raw), text, 'turn', checked.vocabulary)
    return check_plan(plan, checked.vocabulary, text)


@pytest.mark.parametrize('base,other', [('current', 'as_reported'), ('current', 'current'), ('as_reported', 'as_reported')])
def test_mode_comparison_cannot_reverse_or_repeat_the_requested_modes(monkeypatch, base, other):
    eng, vocab = declared(monkeypatch, semantics(release=True))
    text = 'Metric for June 2025 as reported vs current with difference'
    checked = read_checked(eng, vocab, text)
    assert not isinstance(changed(checked, text, lambda raw: raw['comparisons'][0].update(base=base, other=other)), CheckedPlan)


@pytest.mark.parametrize('shape', ['share of Metric by category', 'Metric by category share of total',
                                 'Metric by category as a share of total', 'Metric by category % of total'])
def test_share_of_total_cannot_remove_a_named_filter(monkeypatch, shape):
    eng, vocab = declared(monkeypatch)
    text = shape + ' for Q2 2025 for x source'
    checked = read_checked(eng, vocab, text)
    assert checked.plan.filters
    assert not isinstance(changed(checked, text, lambda raw: raw['derived'][0].update(
        remove_filters=[raw['filters'][0]['id']])), CheckedPlan)


def test_running_total_cannot_forget_its_other_breakdown(monkeypatch):
    eng, vocab = declared(monkeypatch)
    text = 'running total of Metric by month and category for Q2 2025'
    checked = read_checked(eng, vocab, text)
    assert checked.plan.derived[0].partition_by
    assert not isinstance(changed(checked, text, lambda raw: raw['derived'][0].update(partition_by=[])), CheckedPlan)


@pytest.mark.parametrize('shape', ['share of Metric by category and source',
                                 'Metric by category and source as a share of total',
                                 'Metric by category and source % of total'])
def test_share_of_total_cannot_keep_a_requested_breakdown_in_its_denominator(monkeypatch, shape):
    eng, vocab = declared(monkeypatch)
    text = shape + ' for Q2 2025'
    checked = read_checked(eng, vocab, text)
    assert len(checked.plan.dimensions) == 2
    assert not isinstance(changed(checked, text, lambda raw: raw['derived'][0].update(
        remove_dimensions=[raw['dimensions'][0]['id']])), CheckedPlan)


def test_a_prefix_operation_uses_its_following_measure(monkeypatch):
    sem = semantics()
    sem.contexts['extra'] = replace_context(sem.contexts['ctx'], id='extra', label='Extra', term='Extra')
    eng, vocab = declared(monkeypatch, sem)
    text = 'Metric and cumulative Extra by month for Q2 2025'
    checked = read_checked(eng, vocab, text)
    derived, = checked.plan.derived
    measures = {m.context.id: m.id for m in checked.plan.measures}
    assert derived.input == measures['extra']
    assert not isinstance(changed(checked, text, lambda raw: raw['derived'][0].update(input=measures['ctx'])), CheckedPlan)


def test_a_prefix_operation_accepts_a_coordinated_measure_list(monkeypatch):
    sem = semantics()
    sem.contexts['extra'] = replace_context(sem.contexts['ctx'], id='extra', label='Extra', term='Extra')
    eng, vocab = declared(monkeypatch, sem)
    checked = read_checked(eng, vocab, 'cumulative Metric and Extra by month for Q2 2025')
    assert {d.input for d in checked.plan.derived} == {m.id for m in checked.plan.measures}


def test_one_cumulative_operation_cannot_add_another_cumulative_layer(monkeypatch):
    eng, vocab = declared(monkeypatch)
    text = 'cumulative Metric by month for Q2 2025'
    checked = read_checked(eng, vocab, text)
    def duplicate(raw):
        first = raw['derived'][0]
        raw['derived'].append(dict(first, id='second', input=first['id']))
    assert not isinstance(changed(checked, text, duplicate), CheckedPlan)


def test_time_mode_comparison_accepts_a_coordinated_measure_list(monkeypatch):
    sem = semantics(release=True)
    sem.contexts['extra'] = replace_context(sem.contexts['ctx'], id='extra', label='Extra', term='Extra')
    eng, vocab = declared(monkeypatch, sem)
    checked = read_checked(eng, vocab, 'Metric and Extra for June 2025 as reported vs current')
    comparison, = checked.plan.comparisons
    assert set(comparison.inputs) == {m.id for m in checked.plan.measures}
    assert comparison.base == 'as_reported' and comparison.other == 'current'
    assert len(checked.plan.derived) == 2


@pytest.mark.parametrize('shape', ['cumulative Metric and Extra by month', 'share of Metric and Extra by category'])
@pytest.mark.parametrize('mutation', ['omit', 'duplicate'])
def test_unary_shape_lists_need_one_output_for_each_requested_measure(monkeypatch, shape, mutation):
    sem = semantics()
    sem.contexts['extra'] = replace_context(sem.contexts['ctx'], id='extra', label='Extra', term='Extra')
    eng, vocab = declared(monkeypatch, sem)
    text = shape + ' for Q2 2025'
    checked = read_checked(eng, vocab, text)
    def update(raw):
        if mutation == 'omit':
            raw['derived'].pop()
        else:
            raw['derived'].append(dict(raw['derived'][0], id='duplicate'))
    assert not isinstance(changed(checked, text, update), CheckedPlan)


def test_time_mode_list_cannot_omit_one_requested_measure(monkeypatch):
    sem = semantics(release=True)
    sem.contexts['extra'] = replace_context(sem.contexts['ctx'], id='extra', label='Extra', term='Extra')
    eng, vocab = declared(monkeypatch, sem)
    text = 'Metric and Extra for June 2025 as reported vs current with difference'
    checked = read_checked(eng, vocab, text)
    def update(raw):
        removed = raw['comparisons'][0]['inputs'].pop()
        raw['derived'] = [d for d in raw['derived'] if d['numerator'] != 'c/' + removed + '/base']
    assert not isinstance(changed(checked, text, update), CheckedPlan)


@pytest.mark.parametrize('kind', ['share', 'running_total'])
def test_unary_measure_shape_keeps_an_accompanying_count_separate(monkeypatch, kind):
    """A count in the same context keeps its own figures without receiving the shape."""
    _, vocab = declared(monkeypatch)
    opening, grouping = ('share of', 'category') if kind == 'share' else ('running total of', 'month')
    text = f'{opening} Metric by {grouping} for Q2 2025 count Entities'
    counted = dict(kind='distinct_count', id='n', entity=name('entity'), context=name('ctx'), mode='current', filters=[])
    data = model(measures=[model().measures[0], counted],
                 time=dict(kind='single', period=dict(kind='calendar_quarter', value='2025-Q2', calendar=name('cal'))),
                 dimensions=[dimension()] if kind == 'share' else []).model_dump(mode='json', by_alias=True)
    assert compose(data, SimpleNamespace(question=text), ledger(text, 'turn', vocab), vocab) is None
    derived, = data['derived']
    assert derived['input'] == 'm'
    plan = ground_plan(QueryPlan.model_validate(data), text, 'turn', vocab)
    checked = check_plan(plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    rows = [(dt.date(2025, month, 10), value * entity, category, entity, 'x')
            for month, value in [(4, 2), (5, 3), (6, 5)] for category, entity in [('A', 1), ('B', 2)]]
    with connection(rows) as con:
        assert gate(con, compiled.answer.sql, compiled.answer.allow)['passed']
        result = result_of(compiled, con.execute(compiled.answer.sql).fetchall(), {})
    actual = [tuple(Decimal(str(cell.value.value)) for cell in row.cells) for row in result.rows if not row.total]
    if kind == 'share':
        assert [row[:2] for row in actual] == [(10, 1), (20, 1)]
        assert [float(row[2]) for row in actual] == pytest.approx([1 / 3, 2 / 3])
    else:
        assert actual == [(6, 2, 6), (9, 2, 15), (15, 2, 30)]
    assert not isinstance(changed(checked, text, lambda raw: raw['derived'][0].update(input='n')), CheckedPlan)


def test_count_only_share_still_uses_its_declared_context(monkeypatch):
    _, vocab = declared(monkeypatch)
    text = 'count Entities by category share'
    counted = dict(kind='distinct_count', id='n', entity=name('entity'), context=name('ctx'), mode='current', filters=[])
    data = model(measures=[counted], dimensions=[dimension()]).model_dump(mode='json', by_alias=True)
    assert compose(data, SimpleNamespace(question=text), ledger(text, 'turn', vocab), vocab) is None
    assert [derived['input'] for derived in data['derived']] == ['n']
    plan = ground_plan(QueryPlan.model_validate(data), text, 'turn', vocab)
    checked = check_plan(plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    rows = [(dt.date(2025, 6, 10), 1, category, entity, 'x')
            for category, entity in [('A', 1), ('A', 2), ('B', 3)]]
    with connection(rows) as con:
        assert gate(con, compiled.answer.sql, compiled.answer.allow)['passed']
        result = result_of(compiled, con.execute(compiled.answer.sql).fetchall(), {})
    actual = [tuple(float(cell.value.value) for cell in row.cells) for row in result.rows if not row.total]
    assert [row[0] for row in actual] == [2, 1]
    assert [row[1] for row in actual] == pytest.approx([2 / 3, 1 / 3])
