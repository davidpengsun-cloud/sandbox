from dataclasses import replace

import pytest

from kge.semantics.coverage import coverage, ledger, one_meaning
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine.planmodel import read
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary


class Reply:
    def __init__(self, plan):
        self.plan = plan

    def json(self, prompt):
        return Propose(kind='propose', base_turn=None, plan=self.plan).model_dump(mode='json', by_alias=True)


def predicate(identifier, column, value):
    return dict(kind='compare', id=identifier, left=dict(kind='column', name=name(column), path=[]),
                op='eq', value=dict(kind='text', value=value), evidence=[])


def declarations(sem, rows):
    sem.d.VOCABULARY = rows
    words = compiled(rows)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns


@pytest.mark.parametrize('phrasing', ['ratio of {first} to {second}', '{first}-to-{second} ratio', '{first} to {second} ratio'])
@pytest.mark.parametrize('reverse_question', [False, True])
@pytest.mark.parametrize('inverted', [False, True])
def test_ratio_direction_survives_read_and_check(phrasing, reverse_question, inverted):
    sem = semantics()
    declarations(sem, [dict(kind='predicate', params=dict(column='u.events.category', phrases=[phrase], op='eq', value=value))
                       for phrase, value in [('credits', 'I'), ('debits', 'O')]])
    v = vocabulary(sem)
    measures = [dict(model().measures[0].model_dump(), id=identifier,
                     filters=[predicate('f' + identifier, 'u.events.category', value)])
                for identifier, value in [('a', 'I'), ('b', 'O')]]
    first, second = ('debits', 'credits') if reverse_question else ('credits', 'debits')
    num, den = ('b', 'a') if reverse_question else ('a', 'b')
    if inverted:
        num, den = den, num
    text = 'Give Metric with the ' + phrasing.format(first=first, second=second) + '.'
    p = grounded(model(measures=measures, derived=[dict(kind='ratio', id='r', numerator=num, denominator=den, evidence=[])]),
                 text, v, {'Metric': ['a', 'b'], 'ratio': 'r', 'credits': 'fa', 'debits': 'fb'})
    reply = read(Reply(p), text, v)
    assert reply.kind == 'propose'
    decision = check_plan(reply.plan, v, text)
    if inverted:
        assert decision.kind == 'abstain'
        assert 'first named operand as numerator' in decision.missing[0]
    else:
        assert isinstance(decision, CheckedPlan), decision
        from kge.semantics.compile import compile_plan
        from tests.unit.test_plan_compiler import connection
        import datetime as dt
        with connection([(dt.date(2025, 6, 1), value, category, 1, 'x') for value, category in [(20, 'I'), (5, 'O')]]) as con:
            assert con.execute(compile_plan(decision).answer.sql).fetchone()[2] == (.25 if reverse_question else 4)


def test_context_and_unit_candidates_cannot_be_answered_on_one_reading():
    v = vocabulary(aliases=[('Other Metric', 'ctx')])
    unit = Entry('other', 'unit', 'Other', 'other')
    v = replace(v, entries={**v.entries, unit.id: unit}, phrases={**v.phrases, 'other metric': ('ctx', 'other')})
    text = 'Give Other Metric'
    p = grounded(model(), text, v)
    assert not one_meaning(('ctx', 'other'), v)
    assert coverage(p, text, 'turn', v) == ('Other Metric',)
    reply = read(Reply(p), text, v)
    assert reply.kind == 'propose'
    decision = check_plan(reply.plan, v, text)
    assert decision.kind == 'clarify' and set(decision.candidates) == {'ctx', 'other'}

    class Clarify:
        def json(self, prompt):
            return dict(kind='clarify', spans=[ledger(text, 'turn', v)[0].span.model_dump()], candidates=[name('ctx'), name('other')])

    assert read(Clarify(), text, v).kind == 'clarify'


def test_several_named_ratios_bind_their_own_operand_order():
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Context two', term='Second', grain='day')
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'ctx2')])
    measures = [model().measures[0].model_dump(), dict(model().measures[0].model_dump(), id='n', context=name('ctx2'))]
    text = 'Give the ratio of First to Second and the ratio of Second to First'
    p = grounded(model(measures=measures, derived=[dict(kind='ratio', id='r1', numerator='m', denominator='n', evidence=[]),
                                                  dict(kind='ratio', id='r2', numerator='n', denominator='m', evidence=[])]),
                 text, v, {'First': 'm', 'Second': 'n', 'ratio': 'r1'})
    data = p.model_dump(mode='json', by_alias=True)
    data['obligations'][3]['targets'] = ['r2']
    p = type(p).model_validate(data)
    proposed = read(Reply(p), text, v)
    assert proposed.kind == 'propose'
    assert isinstance(check_plan(proposed.plan, v, text), CheckedPlan)


@pytest.mark.parametrize('spelling', ['PRIME', 'Prime', 'prime'])
def test_bare_code_and_its_name_filter_on_the_code(spelling):
    sem = semantics()
    sem.tables['u.events'].update(SEGMT_LVL_ID5='VARCHAR', SEGMT_LVL_NM5='VARCHAR')
    declarations(sem, [dict(kind='value', params=dict(column='u.events.' + column, values=[value]))
                       for column, value in [('SEGMT_LVL_ID5', 'PRIME'), ('SEGMT_LVL_NM5', 'Prime')]])
    sem.d.VOCABULARY.append(dict(kind='code_name_pair', params=dict(
        code='u.events.SEGMT_LVL_ID5', name='u.events.SEGMT_LVL_NM5', members=[dict(code='PRIME', name='Prime')])))
    v = vocabulary(sem, values=[Entry(column, 'value', value, 'u', column='u.events.' + column, value=value)
                               for column, value in [('SEGMT_LVL_ID5', 'PRIME'), ('SEGMT_LVL_NM5', 'Prime')]])
    text = f'Give {spelling} Metric'
    p = grounded(model(filters=[predicate('f', 'u.events.SEGMT_LVL_ID5', 'PRIME')]), text, v, {spelling: 'f'})
    piece = next(p for p in ledger(text, 'turn', v) if p.kind == 'predicate')
    assert piece.candidates == ('u.events.SEGMT_LVL_ID5',) and piece.value == 'PRIME'
    reply = read(Reply(p), text, v)
    assert reply.kind == 'propose'
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)


@pytest.mark.parametrize('declared', [False, True])
def test_values_of_different_members_still_clarify(declared):
    sem = semantics()
    sem.tables['u.events'].update(SEGMT_LVL_ID5='VARCHAR', SEGMT_LVL_NM6='VARCHAR')
    if declared:
        declarations(sem, [dict(kind='value', params=dict(column='u.events.' + column, values=[value]))
                           for column, value in [('SEGMT_LVL_ID5', 'PRIME'), ('SEGMT_LVL_NM6', 'Prime')]])
    v = vocabulary(sem, values=[Entry('parent', 'value', 'PRIME', 'u', column='u.events.SEGMT_LVL_ID5', value='PRIME'),
                               Entry('child', 'value', 'Prime', 'u', column='u.events.SEGMT_LVL_NM6', value='Prime')])
    text = 'Give PRIME Metric'
    decision = check_plan(grounded(model(), text, v), v, text)
    assert decision.kind == 'clarify' and set(decision.candidates) == {'parent', 'child'}


@pytest.mark.parametrize('phrasing', ['{first}/{second}', '{first} per {second}', '{first} and {second} ratio'])
@pytest.mark.parametrize('reverse_question', [False, True])
@pytest.mark.parametrize('inverted', [False, True])
def test_unbound_ratio_phrasings_abstain(phrasing, reverse_question, inverted):
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Second', term='Second', grain='day')
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'ctx2')])
    first, second = ('Second', 'First') if reverse_question else ('First', 'Second')
    num, den = ('n', 'm') if reverse_question else ('m', 'n')
    if inverted:
        num, den = den, num
    p = model(measures=[model().measures[0], dict(model().measures[0].model_dump(), id='n', context=name('ctx2'))],
              derived=[dict(kind='ratio', id='r', numerator=num, denominator=den, evidence=[])])
    text = phrasing.format(first=first, second=second)
    decision = check_plan(grounded(p, text, v, {'First': 'm', 'Second': 'n', 'ratio': 'r', 'per': 'r'}), v, text)
    assert decision.kind == 'abstain'


@pytest.mark.parametrize('phrasing', ['Compare Metric from {first} to {second} percent change',
                                     'Metric {first} versus {second} percent change',
                                     'Metric {first} vs {second} percent change',
                                     'Compare Metric {first} with {second} percent change',
                                     'Compare Metric {first} to {second} percent change',
                                     'Compare Metric {first} and {second} percent change',
                                     'Metric {first} compared with {second} percent change'])
@pytest.mark.parametrize('reverse_question', [False, True])
@pytest.mark.parametrize('inverted', [False, True])
def test_question_period_order_rejects_swapped_periods_and_evidence(phrasing, reverse_question, inverted):
    import datetime as dt
    from kge.semantics.compile import compile_plan
    from tests.unit.test_plan_compiler import connection
    from tests.plan_fixtures import monthly_time
    v = vocabulary()
    first, second = ('2025-06', '2025-05') if reverse_question else ('2025-05', '2025-06')
    base, other = (second, first) if 'from' in phrasing else (first, second)
    if inverted:
        base, other = other, base
    p = model(time=monthly_time(base, base), comparisons=[dict(kind='explicit_period', id='c', inputs=['m'],
              other=monthly_time(other, other), evidence=[])],
              derived=[dict(kind='percent_change', id='p', numerator='c/m/base', denominator='c/m/other', evidence=[])])
    text = phrasing.format(first=first, second=second)
    assignments = {base: 'time', other: 'c', 'Compare': 'c', 'versus': 'c', 'vs': 'c', 'percent change': 'p'}
    if 'with' in phrasing and 'compared' not in phrasing:
        data = p.model_dump(mode='json', by_alias=True)
        data['derived'].append(dict(kind='difference', id='d', numerator='c/m/base', denominator='c/m/other', evidence=[]))
        p = type(p).model_validate(data)
        assignments['Compare'] = ['c', 'd']
    checked = check_plan(grounded(p, text, v, assignments), v, text)
    if inverted or 'compared with' in phrasing:
        assert checked.kind == 'abstain'
    else:
        assert isinstance(checked, CheckedPlan), checked
        with connection([(dt.date(2025, month, 1), value, 'A', 1, 'x') for month, value in [(5, 25), (6, 34)]]) as con:
            value = con.execute(compile_plan(checked).answer.sql).fetchone()[3]
        assert value == pytest.approx(.36 if base == '2025-06' else -9 / 34)


@pytest.mark.parametrize('phrase', ['previous period', 'previous year', 'year over year', 'month-over-month', 'month over month'])
@pytest.mark.parametrize('inverted', [False, True])
def test_relative_comparison_later_period_is_base(phrase, inverted):
    import datetime as dt
    from kge.semantics.compile import compile_plan
    from tests.unit.test_plan_compiler import connection
    from tests.plan_fixtures import monthly_time
    v = vocabulary()
    kind = 'previous_year' if 'year' in phrase else 'previous_period'
    other = '2024-06' if kind == 'previous_year' else '2025-05'
    p = model(time=monthly_time('2025-06', '2025-06'), comparisons=[dict(kind=kind, id='c', inputs=['m'], evidence=[])],
              derived=[dict(kind='percent_change', id='p', numerator='c/m/' + ('other' if inverted else 'base'),
                            denominator='c/m/' + ('base' if inverted else 'other'), evidence=[])])
    text = 'Metric 2025-06 percent change ' + phrase
    checked = check_plan(grounded(p, text, v, {'2025-06': 'time', phrase: 'c', 'percent change': 'p'}), v, text)
    if inverted:
        assert checked.kind == 'abstain'
    else:
        assert isinstance(checked, CheckedPlan), checked
        with connection([(dt.date.fromisoformat(other + '-01'), 25, 'A', 1, 'x'), (dt.date(2025, 6, 1), 34, 'A', 1, 'x')]) as con:
            assert con.execute(compile_plan(checked).answer.sql).fetchone()[3] == pytest.approx(.36)


@pytest.mark.parametrize('inverted', [False, True])
def test_change_since_without_a_grounded_current_period_abstains(inverted):
    from tests.plan_fixtures import monthly_time
    v = vocabulary()
    base, other = ('2025-05', '2025-06') if inverted else ('2025-06', '2025-05')
    p = model(time=monthly_time(base, base), comparisons=[dict(kind='explicit_period', id='c', inputs=['m'], other=monthly_time(other, other), evidence=[])],
              derived=[dict(kind='percent_change', id='p', numerator='c/m/base', denominator='c/m/other', evidence=[])])
    text = 'Metric percent change since 2025-05'
    assert check_plan(grounded(p, text, v, {'2025-05': 'c' if not inverted else 'time', 'percent change': 'p'}), v, text).kind == 'abstain'


@pytest.mark.parametrize('qualified', [False, True])
@pytest.mark.parametrize('scope', ['first', 'all', 'global'])
def test_followup_filter_covers_every_applicable_measure_with_figures(qualified, scope):
    import datetime as dt
    from kge.semantics.compile import compile_plan
    from kge.semantics.plan import HistoryEntry
    from kge.semantics.planhistory import inherited
    from tests.plan_fixtures import monthly_time
    from tests.unit.test_plan_compiler import connection
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Second', term='Second', grain='day')
    declarations(sem, [dict(kind='predicate', params=dict(column='u.events.category', phrases=['eligible accounts'], op='eq', value='A'))])
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'ctx2')])
    measures = [model().measures[0].model_dump(), dict(model().measures[0].model_dump(), id='n', context=name('ctx2'))]
    question = 'First and Second 2025-06'
    base = grounded(model(measures=measures, time=monthly_time('2025-06', '2025-06')), question, v,
                    {'First': 'm', 'Second': 'n', '2025-06': 'time'})
    assert isinstance(check_plan(base, v, question), CheckedPlan)
    data = base.model_dump(mode='json', by_alias=True)
    targets = []
    for measure in data['measures'] if scope == 'all' else data['measures'][:1] if scope == 'first' else []:
        identifier = 'f' + measure['id']
        measure['filters'] = [predicate(identifier, 'u.events.category', 'A')]
        targets.append(identifier)
    if scope == 'global':
        data['filters'] = [predicate('f', 'u.events.category', 'A')]
        targets = ['f']
    followup = 'Now just ' + ('First ' if qualified else '') + 'eligible accounts'
    for index, piece in enumerate(ledger(followup, 'next', v)):
        data['obligations'].append(dict(id='follow' + str(index), state='applied', kind='name',
                                        spans=[piece.span.model_dump()], targets=['m'] if piece.kind == 'name' else targets))
    p = type(base).model_validate(data)
    history = (HistoryEntry(turn_id='turn', question=question, plan=base, clarification=None, binding=v.binding),)
    checked = check_plan(p, v, followup, 'next', inherited(p, history, v))
    rows = [(dt.date(2025, 6, 1), value, category, 1, 'x') for value, category in [(5, 'A'), (80, 'B')]]
    with connection(rows) as con:
        assert con.execute(compile_plan(check_plan(base, v, question)).answer.sql).fetchone()[:2] == (85, 85)
        if scope == 'first' and not qualified:
            assert checked.kind == 'abstain'
            assert 'every applicable retained measure' in str(checked.missing)
        else:
            assert isinstance(checked, CheckedPlan), checked
            assert con.execute(compile_plan(checked).answer.sql).fetchone()[:2] == ((5, 85) if scope == 'first' else (5, 5))


@pytest.mark.parametrize('operation', ['percent_change', 'ratio'])
@pytest.mark.parametrize('inverted', [False, True])
def test_direction_checks_preserve_native_bigquery_job_budget(monkeypatch, tmp_path, operation, inverted):
    from tests.unit.test_planner_jobs import jobs, setup
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import monthly_time
    fake, warehouse = setup(monkeypatch, tmp_path, False)
    fake.con.execute('DELETE FROM u_events')
    fake.con.execute("INSERT INTO u_events VALUES ('2025-05-01', 25, 'A', 1, 'x', NULL), ('2025-06-01', 34, 'B', 1, 'x', NULL)")
    sem = semantics()
    if operation == 'percent_change':
        base, other = ('2025-05', '2025-06') if inverted else ('2025-06', '2025-05')
        p = model(time=monthly_time(base, base), comparisons=[dict(kind='explicit_period', id='c', inputs=['m'], other=monthly_time(other, other), evidence=[])],
                  derived=[dict(kind=operation, id='r', numerator='c/m/base', denominator='c/m/other', evidence=[])])
        text = 'Compare Metric from 2025-05 to 2025-06 percent change'
        assignments = {'Compare': 'c', base: 'time', other: 'c', 'percent change': 'r'}
    else:
        declarations(sem, [dict(kind='predicate', params=dict(column='u.events.category', phrases=[phrase], op='eq', value=value))
                           for phrase, value in [('credits', 'A'), ('debits', 'B')]])
        measures = [dict(model().measures[0].model_dump(), id=identifier, filters=[predicate('f' + identifier, 'u.events.category', value)])
                    for identifier, value in [('a', 'A'), ('b', 'B')]]
        p = model(measures=measures, derived=[dict(kind='ratio', id='r', numerator='b' if inverted else 'a', denominator='a' if inverted else 'b', evidence=[])])
        text = 'Metric credits-to-debits ratio'
        assignments = {'Metric': ['a', 'b'], 'credits': 'fa', 'debits': 'fb', 'ratio': 'r'}
    v = vocabulary(sem)
    p = grounded(p, text, v, assignments)
    try:
        for _ in range(2):
            fake.requests.clear()
            result = Planner(v, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
            assert result['kind'] == ('abstain' if inverted else 'result'), result
            assert jobs(fake) == ((0, 0) if inverted else (1, 1))
    finally:
        fake.con.close()


@pytest.mark.parametrize('inverted', [False, True])
@pytest.mark.parametrize('reverse_question', [False, True])
def test_comparison_range_sides_are_bound_independently(inverted, reverse_question):
    from tests.plan_fixtures import monthly_time
    v = vocabulary()
    first, second = (('2025-04', '2025-06'), ('2025-01', '2025-03'))
    if reverse_question:
        first, second = second, first
    base, other = (second, first) if inverted else (first, second)
    p = model(time=monthly_time(*base), comparisons=[dict(kind='explicit_period', id='c', inputs=['m'], other=monthly_time(*other), evidence=[])],
              derived=[dict(kind='percent_change', id='p', numerator='c/m/base', denominator='c/m/other', evidence=[])])
    text = f'Metric {first[0]} through {first[1]} versus {second[0]} through {second[1]} percent change'
    assignments = {**{period: 'time' for period in base}, **{period: 'c' for period in other}, 'through': ['time', 'c'], 'versus': 'c', 'percent change': 'p'}
    checked = check_plan(grounded(p, text, v, assignments), v, text)
    assert (checked.kind == 'abstain') if inverted else isinstance(checked, CheckedPlan)


@pytest.mark.parametrize('filtered_both', [False, True])
def test_followup_filter_native_bigquery_budget(monkeypatch, tmp_path, filtered_both):
    from kge.semantics.plan import HistoryEntry
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup
    from tests.plan_fixtures import monthly_time
    fake, warehouse = setup(monkeypatch, tmp_path, False)
    fake.con.execute("INSERT INTO u_events VALUES ('2025-06-01', 80, 'B', 1, 'x', NULL)")
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Second', term='Second', grain='day')
    declarations(sem, [dict(kind='predicate', params=dict(column='u.events.category', phrases=['eligible accounts'], op='eq', value='A'))])
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'ctx2')])
    text = 'First and Second 2025-06'
    measures = [model().measures[0].model_dump(), dict(model().measures[0].model_dump(), id='n', context=name('ctx2'))]
    base = grounded(model(measures=measures, time=monthly_time('2025-06', '2025-06')), text, v,
                    {'First': 'm', 'Second': 'n', '2025-06': 'time'})
    history = (HistoryEntry(turn_id='turn', question=text, plan=base, clarification=None, binding=v.binding),)
    followup = 'Now just eligible accounts'
    data = base.model_dump(mode='json', by_alias=True)
    targets = []
    for measure in data['measures'] if filtered_both else data['measures'][:1]:
        identifier = 'f' + measure['id']
        measure['filters'] = [predicate(identifier, 'u.events.category', 'A')]
        targets.append(identifier)
    piece, = ledger(followup, 'next', v)
    data['obligations'].append(dict(id='followup', state='applied', kind='name', spans=[piece.span.model_dump()], targets=targets))
    proposal = Propose(kind='propose', base_turn='turn', plan=type(base).model_validate(data))
    try:
        for _ in range(2):
            fake.requests.clear()
            result = Planner(v, warehouse, Hidden([], set(), [])).answer(followup, proposal, 'next', history)
            assert result['kind'] == ('result' if filtered_both else 'abstain'), result
            assert jobs(fake) == ((1, 1) if filtered_both else (0, 0))
            if filtered_both:
                assert [float(cell['value']['value']) for cell in result['result']['rows'][0]['cells']] == [5, 5]
    finally:
        fake.con.close()


def test_ratio_abstains_when_both_named_operands_fit_both_measures():
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Second', term='Second', grain='day')
    v = vocabulary(sem)
    v = replace(v, phrases={**v.phrases, 'metric': ('u.metric',)})
    p = model(measures=[model().measures[0], dict(model().measures[0].model_dump(), id='n', context=name('ctx2'))],
              derived=[dict(kind='ratio', id='r', numerator='m', denominator='n', evidence=[])])
    text = 'ratio of Metric to Metric'
    decision = check_plan(grounded(p, text, v, {'ratio': 'r', 'Metric': ['m', 'n']}), v, text)
    assert decision.kind == 'abstain' and 'unique order' in str(decision.missing)
