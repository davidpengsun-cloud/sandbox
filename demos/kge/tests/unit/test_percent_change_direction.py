from copy import deepcopy
from types import SimpleNamespace

import pytest

from kge.semantics.plan import HistoryEntry, Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planhistory import inherited
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine import planrules
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.question import Request
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, vocabulary
from tests.unit.test_plan_relational_matrix import replace_context


FORMS = ('previous_period', 'previous_year', 'from', 'versus',
         'current', 'as_reported', 'contexts', 'measures')
REASON = 'percent change requires the comparison base as numerator and its other output as denominator'


def comparison_plan(form):
    sem = semantics(release=True)
    sem.contexts['second'] = replace_context(sem.contexts['ctx'], id='second', label='Other metric',
                                            term='Other metric', measure='u.other')
    specs = [MeasureSpec(i, 'USD', 'entities', True) for i in ('u.metric', 'u.other')]
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'], specs)
    measures = [model().measures[0].model_dump(mode='json')]
    comparison = {'id': 'c', 'inputs': ['m'], 'evidence': []}
    text = 'Metric 2025-06'
    assignments = {'2025-06': 'time', 'percent change': 'p'}
    numerator, denominator = 'c/m/base', 'c/m/other'
    if form in ('contexts', 'measures'):
        measures.append(dict(measures[0], id='n', context=name('second'), measure=name('u.other')))
        comparison = {'kind': 'contexts', 'id': 'c', 'left': 'm', 'right': 'n', 'evidence': []}
        text += ' versus Other metric'
        assignments.update({'versus': 'c', 'Other metric': 'n'})
        numerator, denominator = ('m', 'n') if form == 'measures' else ('c/base', 'c/other')
    elif form in ('current', 'as_reported'):
        other = 'as_reported' if form == 'current' else 'current'
        comparison.update(kind='time_mode', base=form, other=other)
        text += ' ' + form.replace('_', ' ') + ' versus ' + other.replace('_', ' ')
        assignments.update({'current': 'c', 'versus': 'c', 'as reported': 'c'})
    elif form in ('from', 'versus'):
        comparison.update(kind='explicit_period', other=monthly_time('2025-05', '2025-05'))
        text = 'Compare Metric from 2025-05 to 2025-06' if form == 'from' else text + ' versus 2025-05'
        assignments.update({'2025-05': 'c', 'versus': 'c', 'Compare': 'c'})
    else:
        comparison['kind'] = form
        text += ' ' + form.replace('_', ' ')
        assignments[form.replace('_', ' ')] = 'c'
    text += ' percent change'
    plan = model(measures=measures, time=monthly_time('2025-06', '2025-06'), comparisons=[comparison],
                 derived=[{'kind': 'percent_change', 'id': 'p', 'numerator': numerator,
                           'denominator': denominator, 'evidence': []}])
    return v, grounded(plan, text, v, assignments), text


def swapped(plan):
    change = plan.derived[0]
    return plan.model_copy(update={'derived': (change.model_copy(update={
        'numerator': change.denominator, 'denominator': change.numerator}),)})


@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('invert', [False, True])
def test_model_read_checks_percent_change_comparison_direction(form, invert):
    v, plan, text = comparison_plan(form)
    if invert:
        plan = swapped(plan)

    class Scripted:
        def json(self, prompt):
            return {'kind': 'propose', 'base_turn': None, 'plan': plan.model_dump(mode='json', by_alias=True)}

    reply = read(Scripted(), text, v)
    assert isinstance(reply, Propose), reply
    assert reply.plan.derived == plan.derived
    checked = check_plan(reply.plan, v, text)
    if invert:
        assert checked.kind == 'abstain' and checked.missing == (REASON,)
    else:
        assert isinstance(checked, CheckedPlan), checked


@pytest.mark.parametrize('form', FORMS)
def test_rules_preserve_checked_percent_changes_and_checker_rejects_inversion(monkeypatch, form):
    v, plan, text = comparison_plan(form)
    assert isinstance(check_plan(plan, v, text), CheckedPlan)
    base = HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding)
    engine = SimpleNamespace(semantics=v.semantics, PERIODS=v.semantics.d.PERIODS,
                             CALENDARS=v.calendars)
    monkeypatch.setattr(planrules, 'snapshot', lambda *_: v.semantics)
    request = Request(question='Metric', term='Metric', terms_found=['Metric'])
    cfg = SimpleNamespace(graph=True, glossary=True, resolution='glossary')
    reply = planrules.read(engine, request, v, cfg, turn='next', base=base)
    assert isinstance(reply, Propose), reply
    assert reply.plan.derived == plan.derived
    pieces = inherited(reply.plan, (base,), v)
    checked = check_plan(reply.plan, v, request.question, 'next', pieces)
    assert isinstance(checked, CheckedPlan), checked
    inverted = check_plan(swapped(reply.plan), v, request.question, 'next', pieces)
    assert inverted.kind == 'abstain' and inverted.missing == (REASON,)


@pytest.mark.parametrize('form', FORMS)
def test_a_bare_ratio_without_ordered_operands_abstains_in_both_orders(form):
    v, plan, text = comparison_plan(form)
    text = text.replace('percent change', 'ratio')
    for p in (plan, swapped(plan)):
        raw = p.model_dump(mode='json', by_alias=True)
        raw['derived'][0]['kind'] = 'ratio'
        assignments = {}
        for obligation in p.obligations:
            span = obligation.spans[0]
            assignments[text[span.start:span.end]] = list(obligation.targets)
        assignments['ratio'] = 'p'
        checked = check_plan(grounded(type(p).model_validate(raw), text, v, assignments), v, text)
        assert checked.kind == 'abstain' and checked.missing == ('ratio requires grounded operands in question order',), checked


@pytest.mark.parametrize('numerator,denominator', [
    ('c/m/base', 'c/n/other'), ('c/m/base', 'd/m/other'),
    ('c/m/base', 'c/m/base'), ('c/m/base', 'm'), ('m', 'n'),
])
def test_percent_change_cannot_mix_comparisons_inputs_or_unbound_measures(numerator, denominator):
    v, plan, text = comparison_plan('previous_period')
    raw = plan.model_dump(mode='json', by_alias=True)
    raw['measures'].append(dict(raw['measures'][0], id='n'))
    raw['comparisons'][0]['inputs'].append('n')
    raw['comparisons'].append(dict(deepcopy(raw['comparisons'][0]), id='d'))
    raw['derived'][0].update(numerator=numerator, denominator=denominator)
    checked = check_plan(type(plan).model_validate(raw), v, text)
    assert checked.kind == 'abstain' and checked.missing == (REASON,)


@pytest.mark.parametrize('invert', [False, True])
@pytest.mark.parametrize('current,previous,expected', [(34, 25, .36), (5, 20, -.75), (5, -20, 1.25), (-34, -25, -.36)])
def test_inverted_percent_change_has_no_native_warehouse_jobs(monkeypatch, tmp_path, invert, current, previous, expected):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup

    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        v, plan, text = comparison_plan('previous_period')
        sem = semantics()
        sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], filters=())
        v = vocabulary(sem)
        fake.con.execute("INSERT INTO u_events VALUES ('2025-05-10', ?, 'A', 2, 'x', NULL)", [previous])
        fake.con.execute("INSERT INTO u_events VALUES ('2025-06-11', ?, 'A', 2, 'x', NULL)", [current - 5])
        reply = Propose(kind='propose', base_turn=None, plan=swapped(plan) if invert else plan)
        planner = Planner(v, warehouses, Hidden([], set(), []))
        for _ in range(2):
            fake.requests.clear()
            answer = planner.answer(text, reply)
            if invert:
                assert answer['kind'] == 'abstain' and answer['missing'] == [REASON]
                assert 'result' not in answer
                assert jobs(fake) == (0, 0)
            else:
                assert answer['kind'] == 'result', answer
                assert float(answer['result']['rows'][0]['cells'][-1]['value']['value']) == expected
                assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()


@pytest.mark.parametrize('grouped', [False, True])
def test_negative_comparison_uses_absolute_denominator_in_rows_and_totals(monkeypatch, tmp_path, grouped):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup

    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        _, plan, text = comparison_plan('previous_period')
        sem = semantics()
        sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], filters=())
        v = vocabulary(sem)
        fake.con.execute("INSERT INTO u_events VALUES ('2025-05-10', -20, 'A', 2, 'x', NULL)")
        if grouped:
            raw = plan.model_dump(mode='json', by_alias=True)
            raw['dimensions'] = [{'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}]
            assignments = {text[o.spans[0].start:o.spans[0].end]: list(o.targets) for o in plan.obligations}
            text += ' by category'
            assignments.update({'by': 'd', 'category': 'd'})
            plan = grounded(type(plan).model_validate(raw), text, v, assignments)
        fake.requests.clear()
        answer = Planner(v, warehouses, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=plan))
        assert answer['kind'] == 'result', answer
        assert all(float(r['cells'][-1]['value']['value']) == 1.25 for r in answer['result']['rows'])
        assert any(r.get('total') for r in answer['result']['rows']) is grouped
        assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()
