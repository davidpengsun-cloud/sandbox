"""Period certificates use the question ledger and each pack's declared calendars."""
from dataclasses import replace
from datetime import date

import pytest
import yaml

from kge.pipeline.settings import ROOT
from kge.semantics.coverage import coverage, ground_plan, ledger, period_binding, time_matches
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planperiods import bounds
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary


CASES = [
    (pack, 'June 2025', 'month', '2025-06', 'calendar', ('2025-06-01', '2025-07-01'))
    for pack in ('meridian', 'thornbury', 'fellmoor')
] + [
    (pack, 'Q2 2025', 'calendar_quarter', '2025-Q2', 'calendar', ('2025-04-01', '2025-07-01'))
    for pack in ('meridian', 'thornbury', 'fellmoor')
] + [
    (pack, '2025', 'year', '2025', 'calendar', ('2025-01-01', '2026-01-01'))
    for pack in ('meridian', 'thornbury', 'fellmoor')
] + [
    ('meridian', 'FY25-Q2', 'fiscal_quarter', 'FY25-Q2', 'fiscal_april', ('2024-07-01', '2024-10-01')),
    ('meridian', 'FY25', 'fiscal_year', 'FY25', 'fiscal_april', ('2024-04-01', '2025-04-01')),
    ('meridian', 'June 2025', 'month', '2025-06', 'fiscal_april', ('2025-06-01', '2025-07-01')),
]


@pytest.mark.parametrize('pack,text,kind,value,calendar,window', CASES)
@pytest.mark.parametrize('grounding', [False, True])
def test_every_pack_calendar_binds_periods_without_trusting_the_proposal(pack, text, kind, value, calendar, window, grounding):
    declarations = yaml.safe_load((ROOT / 'clients' / pack / 'pack.yaml').read_text())['calendars']
    sem = semantics()
    sem.d.CALENDARS = {row['id']: {k: v for k, v in row.items() if k != 'id'} for row in declarations}
    sem.d.CALENDARS['other'] = dict(year_start_month=10, quarter_label='Other{yyyy}-Q{n}')
    sem.d.PERIODS['u.events']['calendar'] = calendar if kind == 'month' else 'calendar'
    vocab = vocabulary(sem)
    question = 'Metric for ' + text
    selection = dict(kind='single', period=dict(kind=kind, value=value, calendar=name(calendar)))
    plan = grounded(model(time=selection), question, vocab, {text: 'time'})
    period = next(p for p in ledger(question, 'turn', vocab) if p.kind == 'time')
    binding = period_binding(period, ledger(question, 'turn', vocab), vocab)
    assert binding.calendar.id == calendar
    assert bounds(binding, vocab.calendars) == tuple(date.fromisoformat(day) for day in window)
    assert isinstance(check_plan(ground_plan(plan, question, 'turn', vocab) if grounding else plan,
                                 vocab, question), CheckedPlan)
    for wrong in set(vocab.calendars) - {calendar}:
        data = plan.model_dump(mode='json', by_alias=True)
        data['time']['period']['calendar']['id'] = wrong
        mistaken = type(plan).model_validate(data)
        if grounding:
            mistaken = ground_plan(mistaken, question, 'turn', vocab)
        decision = check_plan(mistaken, vocab, question)
        if kind in ('day', 'month', 'calendar_quarter', 'year'):
            assert isinstance(decision, CheckedPlan), (wrong, decision)
            assert decision.plan.time.period.calendar.id == calendar
            assert bounds(decision.plan.time.period, vocab.calendars) == tuple(date.fromisoformat(day) for day in window)
        else:
            assert not isinstance(decision, CheckedPlan), (wrong, decision)
            assert text in str(decision.missing)


@pytest.mark.parametrize('fiscal', [False, True])
def test_year_to_date_uses_the_lexical_default_or_explicit_fiscal_calendar(fiscal):
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', ytd_calendar='cal')
    vocab = vocabulary(sem)
    question = 'Metric ' + ('fiscal ' if fiscal else '') + 'year to date through June 2025'
    calendar = 'fiscal' if fiscal else 'cal'
    plan = model(time=dict(kind='ytd', through=dict(kind='month', value='2025-06', calendar=name(calendar)), calendar=name(calendar)))
    assert isinstance(check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question), CheckedPlan)
    wrong = 'cal' if fiscal else 'fiscal'
    data = plan.model_dump(mode='json', by_alias=True)
    data['time']['calendar']['id'] = data['time']['through']['calendar']['id'] = wrong
    assert not isinstance(check_plan(ground_plan(type(plan).model_validate(data), question, 'turn', vocab), vocab, question), CheckedPlan)


@pytest.mark.parametrize('wrong_side', ['base', 'other', 'both'])
def test_comparison_calendar_is_bound_independently_on_both_sides(wrong_side):
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    vocab = vocabulary(sem)
    question = 'Metric for FY25-Q2 versus FY25-Q1'
    def quarter(value, wrong):
        return dict(kind='single', period=dict(kind='fiscal_quarter', value=value, calendar=name('cal' if wrong else 'fiscal')))
    plan = model(time=quarter('FY25-Q2', False), comparisons=[dict(kind='explicit_period', id='c', inputs=['m'],
        other=quarter('FY25-Q1', False), evidence=[])])
    assert isinstance(check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question), CheckedPlan)
    data = plan.model_dump(mode='json', by_alias=True)
    data['time'] = quarter('FY25-Q2', wrong_side in ('base', 'both'))
    data['comparisons'][0]['other'] = quarter('FY25-Q1', wrong_side in ('other', 'both'))
    assert not isinstance(check_plan(ground_plan(type(plan).model_validate(data), question, 'turn', vocab), vocab, question), CheckedPlan)


@pytest.mark.parametrize('grain,phrase,calendar', [
    ('month', 'month', 'cal'), ('calendar_quarter', 'quarter', 'cal'), ('year', 'year', 'cal'),
    ('fiscal_quarter', 'fiscal quarter', 'fiscal'), ('fiscal_year', 'fiscal year', 'fiscal'),
])
def test_time_grouping_cannot_certify_its_own_calendar_without_a_period(grain, phrase, calendar):
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    vocab = vocabulary(sem)
    question = 'Metric by ' + phrase
    def grouped(selected):
        return ground_plan(model(time=dict(kind='all_available', calendar=name(selected)),
            dimensions=[dict(kind='time', id='d', calendar=name(selected), grain=grain)]), question, 'turn', vocab)
    assert isinstance(check_plan(grouped(calendar), vocab, question), CheckedPlan)
    wrong = 'fiscal' if calendar == 'cal' else 'cal'
    decision = check_plan(grouped(wrong), vocab, question)
    assert not isinstance(decision, CheckedPlan)
    assert 'calendar disagrees' in str(decision.missing)


def test_a_measure_identifier_uses_its_declared_period_binding():
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    sem.d.PERIODS['u.other'] = dict(column='occurred', kind='day', calendar='fiscal')
    vocab = vocabulary(sem)
    question = 'u.metric for June 2025'
    plan = model(time=dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('cal'))))
    assert isinstance(check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question), CheckedPlan)


@pytest.mark.parametrize('phrase,kind,value,grain', [
    ('quarter for Q2 2025', 'calendar_quarter', '2025-Q2', 'calendar_quarter'),
    ('year for 2025', 'year', '2025', 'year'),
])
def test_a_periods_grain_cannot_certify_a_different_grouping_grain(phrase, kind, value, grain):
    vocab = vocabulary()
    question = 'Metric by ' + phrase
    def grouped(selected):
        return ground_plan(model(time=dict(kind='single', period=dict(kind=kind, value=value, calendar=name('cal'))),
            dimensions=[dict(kind='time', id='d', calendar=name('cal'), grain=selected)]), question, 'turn', vocab)
    assert isinstance(check_plan(grouped(grain), vocab, question), CheckedPlan)
    decision = check_plan(grouped('month'), vocab, question)
    assert not isinstance(decision, CheckedPlan)
    assert 'grain disagrees' in str(decision.missing)


def test_a_context_without_a_period_binding_uses_the_rules_declared_calendar_default():
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    sem.d.PERIODS.clear()
    vocab = vocabulary(sem)
    pieces = ledger('Metric for June 2025', 'turn', vocab)
    period = next(piece for piece in pieces if piece.kind == 'time')
    assert period_binding(period, pieces, vocab).calendar.id == 'cal'


def test_a_spelled_out_fiscal_year_cannot_certify_the_models_calendar():
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    vocab = vocabulary(sem)
    question = 'Metric for fiscal year 2025'
    def selected(calendar):
        return ground_plan(model(time=dict(kind='single', period=dict(kind='fiscal_year', value='2025',
            calendar=name(calendar)))), question, 'turn', vocab)
    assert isinstance(check_plan(selected('fiscal'), vocab, question), CheckedPlan)
    assert not isinstance(check_plan(selected('cal'), vocab, question), CheckedPlan)


@pytest.mark.parametrize('value,valid', [('2025-06', True), ('2025-05', False)])
def test_equivalent_absolute_calendar_in_existing_history_keeps_its_followup_binding(value, valid):
    from kge.semantics.plan import HistoryEntry, Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden

    sem = semantics()
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}')
    vocab = vocabulary(sem)
    text = 'Metric for June 2025'
    old = grounded(model(time=dict(kind='single', period=dict(kind='month', value='2025-06',
                    calendar=name('fiscal')))), text, vocab, {'June 2025': 'time'})
    history = (HistoryEntry(turn_id='turn', question=text, plan=old, clarification=None, binding=vocab.binding),)
    data = old.model_dump(mode='json', by_alias=True)
    data['time']['period']['calendar']['id'] = 'cal'
    data['time']['period']['value'] = value
    proposal = Propose(kind='propose', base_turn='turn', plan=type(old).model_validate(data))
    checked = Planner(vocab, None, Hidden([], set(), [])).prepare('Now just', proposal, 'next', history, grounding=True)
    assert isinstance(checked, CheckedPlan) is valid, checked
    if valid:
        assert checked.plan.time.period.calendar.id == 'cal'
        assert bounds(checked.plan.time.period, vocab.calendars) == (date(2025, 6, 1), date(2025, 7, 1))
    else:
        assert checked['kind'] == 'abstain' and 'explicit follow-up evidence' in checked['missing'][0]
    assert history[0].plan.time.period.calendar.id == 'fiscal'


@pytest.mark.parametrize('first,last', [('2025-03', '2025-04'), ('2025-02', '2025-03'), ('2025-04', '2025-05')])
@pytest.mark.parametrize('comparison', [False, True])
def test_an_endpoint_cannot_certify_a_range_in_either_period_field(first, last, comparison):
    vocab = vocabulary()
    question = 'Metric for June 2025 versus March 2025' if comparison else 'Metric for March 2025'
    wrong = dict(kind='range', first=dict(kind='month', value=first, calendar=name('cal')),
                 last=dict(kind='month', value=last, calendar=name('cal')))
    plan = model(time=dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('cal')))
                 if comparison else wrong,
                 comparisons=[dict(kind='explicit_period', id='c', inputs=['m'], other=wrong, evidence=[])] if comparison else [])
    plan = ground_plan(plan, question, 'turn', vocab)
    assert 'March 2025' in coverage(plan, question, 'turn', vocab)
    checked = check_plan(plan, vocab, question)
    assert not isinstance(checked, CheckedPlan), checked
    assert ('period order' if comparison else 'March 2025') in str(checked.missing)


@pytest.mark.parametrize('question,first,last,valid', [
    ('Metric for March 2025', '2025-03', '2025-03', True),
    ('Metric for Q1 2025', '2025-01', '2025-03', True),
    ('Metric for Q1 2025', '2025-01', '2025-02', False),
    ('Metric for Q1 2025', '2025-01', '2025-04', False),
    ('Metric for Q1 2025', '2025-02', '2025-04', False),
    ('Metric from March to April 2025', '2025-03', '2025-04', True),
    ('Metric from March through April 2025', '2025-03', '2025-04', True),
    ('Metric from March through April 2025', '2025-03', '2025-03', False),
    ('Metric from March through April 2025', '2025-02', '2025-04', False),
])
def test_only_the_full_ledger_window_certifies_a_range(question, first, last, valid):
    vocab = vocabulary()
    plan = model(time=dict(kind='range', first=dict(kind='month', value=first, calendar=name('cal')),
                           last=dict(kind='month', value=last, calendar=name('cal'))))
    checked = check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question)
    assert isinstance(checked, CheckedPlan) is valid, checked


@pytest.mark.parametrize('question', ['Metric from March to April 2025', 'Metric from March through April 2025'])
def test_a_rules_floor_certifies_the_same_complete_month_range(question):
    vocab = vocabulary()
    plan = model(time=dict(kind='range', first=dict(kind='month', value='2025-03', calendar=name('cal')),
                           last=dict(kind='month', value='2025-04', calendar=name('cal'))))
    assert isinstance(check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question), CheckedPlan)
    guarded = ground_plan(plan, question, 'turn', vocab, rules_floor=True)
    assert isinstance(check_plan(guarded, vocab, question), CheckedPlan)
    assert guarded == ground_plan(plan, question, 'turn', vocab)


@pytest.mark.parametrize('connector', ['versus', 'with', 'and', 'compared with', 'against', 'to'])
def test_comparison_connectors_do_not_merge_the_two_period_windows(connector):
    from tests.plan_fixtures import monthly_time

    vocab = vocabulary()
    question = f'Compare Metric for March 2025 {connector} June 2025'
    pieces = ledger(question, 'turn', vocab)
    times = [p for p in pieces if p.kind == 'time']
    assert len(times) == 2
    for piece, month in zip(times, ['2025-03', '2025-06']):
        selection = model(time=dict(kind='single', period=dict(kind='month', value=month, calendar=name('cal')))).time
        assert time_matches(piece, selection, pieces, vocab)
        assert not time_matches(piece, model(time=monthly_time('2025-03', '2025-06')).time, pieces, vocab)


def test_an_inherited_range_keeps_the_original_lexical_window():
    from kge.semantics.plan import HistoryEntry, Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import monthly_time

    vocab = vocabulary()
    question = 'Metric from April to June 2025'
    old = ground_plan(model(time=monthly_time()), question, 'turn', vocab)
    history = (HistoryEntry(turn_id='turn', question=question, plan=old, clarification=None, binding=vocab.binding),)
    checked = Planner(vocab, None, Hidden([], set(), [])).prepare('Now just',
        Propose(kind='propose', base_turn='turn', plan=old), 'next', history, grounding=True)
    assert isinstance(checked, CheckedPlan), checked
    assert checked.plan.time == old.time


@pytest.mark.parametrize('phrase,binding,first,last,valid', [
    ('as at', 'event_date', '0001-01-01', '2025-06-30', True),
    ('at', 'event_date', '0001-01-01', '2025-06-30', True),
    ('as of', 'event_date', '0001-01-01', '2025-06-30', True),
    ('as at', 'event_date', '2025-01-01', '2025-06-30', False),
    ('as at', 'event_date', '2025-06-30', '2025-06-30', False),
    ('as at', 'event_date', '0001-01-01', '2025-06-29', False),
    ('as at', 'event_date', '0001-01-01', '2025-07-01', False),
    ('on', 'event_date', '2025-06-30', '2025-06-30', True),
    ('on', 'event_date', '0001-01-01', '2025-06-30', False),
    ('as at', 'snapshot', '2025-06-30', '2025-06-30', True),
    ('as at', 'snapshot', '0001-01-01', '2025-06-30', False),
])
def test_event_count_cutoffs_require_the_full_lexical_window(phrase, binding, first, last, valid):
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], aggregation='count', grain='day')
    sem.d.PERIODS['u.events']['kind'] = binding
    vocab = vocabulary(sem)
    question = f'Count Metric {phrase} 2025-06-30'
    plan = model(time=dict(kind='range', first=dict(kind='day', value=first, calendar=name('cal')),
                           last=dict(kind='day', value=last, calendar=name('cal'))))
    checked = check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question)
    assert isinstance(checked, CheckedPlan) is valid, checked


@pytest.mark.parametrize('first,valid', [('0001-01-01', True), ('2025-06-30', False)])
def test_a_named_count_uses_its_cutoff_without_an_extra_count_word(first, valid):
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], aggregation='count', grain='day')
    sem.d.PERIODS['u.events']['kind'] = 'event_date'
    vocab = vocabulary(sem)
    question = 'Metric as at 2025-06-30'
    plan = model(time=dict(kind='range', first=dict(kind='day', value=first, calendar=name('cal')),
                           last=dict(kind='day', value='2025-06-30', calendar=name('cal'))))
    checked = check_plan(ground_plan(plan, question, 'turn', vocab), vocab, question)
    assert isinstance(checked, CheckedPlan) is valid, checked
