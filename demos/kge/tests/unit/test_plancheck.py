import pytest
from dataclasses import replace

from kge.semantics.coverage import ledger, name_supported
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, vocabulary


def test_minimal_complete_plan_is_checked():
    v = vocabulary()
    p = grounded(model(), 'Metric', v)
    assert isinstance(check_plan(p, v, 'Metric'), CheckedPlan)
    with pytest.raises(TypeError):
        CheckedPlan(None, p, v, (), ())


def test_scope_denial_precedes_ambiguity():
    v = vocabulary(values=[Entry('value', 'value', 'Office', 'u', column='u.events.category', value='Office')], denied_phrases=['Office'])
    assert check_plan(model(), v, 'Office').kind == 'refuse'
    assert all('private' not in str(e) for e in v.public())


def test_narrow_policy_names_keep_restricted_columns_unavailable():
    v = vocabulary(qualified_policy_names=True)
    assert not v.denial('private')
    assert v.denial('events.private') and v.denial('u.events.private')
    assert 'u.events.private' not in v.entries
    assert check_plan(model(), v, 'Metric private').kind == 'abstain'
    assert check_plan(model(), v, 'Metric events.private').kind == 'refuse'


def test_multimap_never_picks_first():
    v = vocabulary(values=[Entry('one', 'value', 'Office', 'u', column='u.events.category', value='Office'),
                           Entry('two', 'value', 'Office', 'u', column='u.events.source', value='Office')])
    assert len(v.candidates('Office')) == 2
    decision = check_plan(model(), v, 'Office')
    assert decision.kind == 'clarify' and set(decision.candidates) == {'one', 'two'}


def test_unknown_name_and_wrong_context_fail_before_compilation():
    v = vocabulary()
    p = model()
    data = p.model_dump(mode='json', by_alias=True)
    data['measures'][0]['measure']['id'] = 'unknown'
    assert check_plan(type(p).model_validate(data), v, 'Metric').kind == 'abstain'


def test_shared_measure_does_not_make_another_context_support_a_name_span():
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', grain='day')
    v = vocabulary(sem)
    v = replace(v, phrases={**v.phrases, 'ctx': ('ctx',)})
    piece = ledger('ctx', 'turn', v)[0]
    assert name_supported('u.metric', piece.candidates, v)
    assert not name_supported('ctx2', piece.candidates, v)
    measure = model().measures[0].model_dump(mode='json')
    measure['context'] = name('ctx2', [piece.span.model_dump()])
    measure['measure'] = name('u.metric', [piece.span.model_dump()])
    p = grounded(model(measures=[measure]), 'ctx', v)
    decision = check_plan(p, v, 'ctx')
    assert decision.kind == 'abstain' and decision.missing == ('A name has unrelated evidence.',)


def test_context_name_cannot_cite_a_measure_only_span():
    v = vocabulary()
    v = replace(v, phrases={**v.phrases, 'metric': ('u.metric',)})
    piece = ledger('Metric', 'turn', v)[0]
    assert not name_supported('ctx', piece.candidates, v)
    measure = model().measures[0].model_dump(mode='json')
    measure['context'] = name('ctx', [piece.span.model_dump()])
    p = grounded(model(measures=[measure]), 'Metric', v)
    decision = check_plan(p, v, 'Metric')
    assert decision.kind == 'abstain' and decision.missing == ('A name has unrelated evidence.',)


@pytest.mark.parametrize('time', [
    monthly_time('2025-01', '2025-03'),
    {'kind': 'ytd', 'through': {'kind': 'month', 'value': '2025-03', 'calendar': name('cal')}, 'calendar': name('cal')},
])
@pytest.mark.parametrize('dimensions', [[], [
    {'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]},
]])
def test_previous_period_of_multi_period_window_without_time_dimension_abstains(time, dimensions):
    v = vocabulary()
    p = model(time=time, dimensions=dimensions,
              comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric 2025-01 through 2025-03 previous period' if time['kind'] == 'range' else 'Metric year to date calendar Q1 2025 previous period'
    if dimensions:
        text += ' by category'
    decision = check_plan(grounded(p, text, v, {'2025-01': 'time', 'through': 'time', '2025-03': 'time',
                                              'year to date': 'time', 'calendar Q1 2025': 'time', 'previous period': 'c',
                                              'by': 'd', 'category': 'd'}), v, text)
    assert decision.kind == 'abstain'
    assert decision.missing == ("The previous period of a multi-period range isn't declared.",)
