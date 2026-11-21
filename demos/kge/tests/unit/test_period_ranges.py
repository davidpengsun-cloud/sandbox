"""Absolute and anchored ranges share rules and model certificates and independently counted figures."""
import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planperiods import time_bounds
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_plan_compiler import connection


@pytest.fixture
def period_reader(monkeypatch):
    sem = semantics()
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    sem.d.CALENDARS['cal']['current_period'] = '2025-06-15'
    sem.d.CALENDARS['fiscal'] = dict(year_start_month=4, quarter_label='FY{yy}-Q{n}', current_period='2025-06-15')
    sem.d.FISCAL_START = 4
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', ytd_calendar='cal')
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)

    def read(text):
        vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
        pieces = ledger(text, 'turn', vocab)
        assert not [p.text for p in pieces if p.kind == 'unknown'], pieces
        reply = planrules.read(engine, parse(text, ['Metric'], sem.d), vocab, SimpleNamespace(graph=True, glossary=True))
        assert reply.kind == 'propose', reply
        checked = check_plan(reply.plan, vocab, text)
        assert isinstance(checked, CheckedPlan), checked
        supplied = ground_plan(reply.plan.model_copy(update={'obligations': ()}), text, 'turn', vocab)
        assert isinstance(check_plan(supplied, vocab, text), CheckedPlan)
        compiled = compile_plan(checked)
        assert check(compiled.answer.sql, compiled.answer.allow) == []
        return checked.plan, compiled, vocab
    return read, sem


@pytest.mark.parametrize('phrase,first,last,expected', [
    ('January through September 2025', '2025-01-01', '2025-10-01', 45),
    ('Jan-Sep 2025', '2025-01-01', '2025-10-01', 45),
    ('Jan–Sep 2025', '2025-01-01', '2025-10-01', 45),
    ('from January to September 2025', '2025-01-01', '2025-10-01', 45),
    ('between January and September 2025', '2025-01-01', '2025-10-01', 45),
    ('from January to September', '2025-01-01', '2025-10-01', 45),
    ('first nine months of 2025', '2025-01-01', '2025-10-01', 45),
    ('Q1 through Q3 2025', '2025-01-01', '2025-10-01', 45),
    ('Q1-Q3 2025', '2025-01-01', '2025-10-01', 45),
    ('first three quarters of 2025', '2025-01-01', '2025-10-01', 45),
    ('first three quarters', '2025-01-01', '2025-10-01', 45),
    ('first nine months', '2025-01-01', '2025-10-01', 45),
    ('first three fiscal quarters', '2025-04-01', '2026-01-01', 72),
    ('first three fiscal months', '2025-04-01', '2025-07-01', 15),
    ('December 2024 through February 2025', '2024-12-01', '2025-03-01', 3),
    ('FY25-Q4 through FY26-Q1', '2025-01-01', '2025-07-01', 21),
    ('first three fiscal months of FY26', '2025-04-01', '2025-07-01', 15),
    ('first three quarters of FY26', '2025-04-01', '2026-01-01', 72),
    ('year to date through June 2025', '2025-01-01', '2025-07-01', 21),
    ('YTD through 18 June 2025', '2025-01-01', '2025-06-19', 21),
    ('YTD through Q2 2025', '2025-01-01', '2025-07-01', 21),
    ('YTD through 2025', '2025-01-01', '2026-01-01', 78),
    ('YTD 2025', '2025-01-01', '2026-01-01', 78),
    ('YTD through 2025-W01', '2025-01-01', '2025-01-06', 1),
    ('fiscal year to date through June 2025', '2025-04-01', '2025-07-01', 15),
    ('fiscal YTD through FY25', '2024-04-01', '2025-04-01', 6),
    ('fiscal YTD through 2025', '2025-04-01', '2026-01-01', 72),
    ('fiscal YTD through 2025-W14', '2025-04-01', '2025-04-07', 4),
    ('month to date through 2025-06-15', '2025-06-01', '2025-06-16', 6),
    ('MTD for June 2025', '2025-06-01', '2025-07-01', 6),
    ('MTD through 18 June 2025', '2025-06-01', '2025-06-19', 6),
    ('MTD through 2025', '2025-12-01', '2026-01-01', 12),
    ('MTD through Q2 2025', '2025-06-01', '2025-07-01', 6),
    ('fiscal MTD through FY26-Q1', '2025-06-01', '2025-07-01', 6),
    ('quarter to date through June 2025', '2025-04-01', '2025-07-01', 15),
    ('QTD through 2025-06-15', '2025-04-01', '2025-06-16', 15),
    ('QTD for 2025', '2025-10-01', '2026-01-01', 33),
    ('QTD through 18 June 2025', '2025-04-01', '2025-06-19', 15),
    ('QTD through 2025', '2025-10-01', '2026-01-01', 33),
    ('fiscal quarter to date through 2025-06-15', '2025-04-01', '2025-06-16', 15),
    ('year to date', '2025-01-01', '2025-06-16', 21),
    ('month-to-date', '2025-06-01', '2025-06-16', 6),
    ('quarter-to-date', '2025-04-01', '2025-06-16', 15),
    ('so far this year', '2025-01-01', '2025-06-16', 21),
    ('so far this month', '2025-06-01', '2025-06-16', 6),
    ('so far this quarter', '2025-04-01', '2025-06-16', 15),
])
def test_range_figures_on_small_inputs_on_rules_and_model_path(period_reader, phrase, first, last, expected):
    read, _ = period_reader
    plan, compiled, vocab = read('Metric for ' + phrase)
    assert time_bounds(plan.time, vocab.calendars) == (dt.date.fromisoformat(first), dt.date.fromisoformat(last))
    rows = [(dt.date(2025, m, 1), m, 'A', 1, 'x') for m in range(1, 13)]
    with connection(rows) as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] == expected
    raw = plan.model_dump(mode='json', by_alias=True)
    endpoint = raw['time'].get('last', raw['time'].get('through'))
    if endpoint:
        if endpoint['kind'] == 'day':
            endpoint['value'] = (dt.date.fromisoformat(endpoint['value']) + dt.timedelta(days=1)).isoformat()
        elif endpoint['kind'] == 'month':
            from kge.semantics.planperiods import add_months
            endpoint['value'] = add_months(dt.date.fromisoformat(endpoint['value'] + '-01'), 1).isoformat()[:7]
        elif endpoint['kind'] == 'calendar_quarter':
            endpoint['value'] = str(int(endpoint['value'][:4]) + 1) + endpoint['value'][4:]
        elif endpoint['kind'] == 'year':
            endpoint['value'] = str(int(endpoint['value']) + 1)
        elif endpoint['kind'] == 'week':
            shifted = dt.date.fromisocalendar(int(endpoint['value'][:4]), int(endpoint['value'][-2:]), 1) + dt.timedelta(days=7)
            year, week, _ = shifted.isocalendar()
            endpoint['value'] = f'{year}-W{week:02d}'
        if not endpoint['kind'].startswith('fiscal_'):
            wrong = ground_plan(QueryPlan.model_validate(raw), 'Metric for ' + phrase, 'turn', vocab)
            assert not isinstance(check_plan(wrong, vocab, 'Metric for ' + phrase), CheckedPlan)


def test_month_breakdown_inside_range_keeps_empty_months(period_reader):
    read, _ = period_reader
    _, compiled, _ = read('Metric by month for Jan-Sep 2025')
    with connection([(dt.date(2025, 1, 5), 4, 'A', 1, 'x'), (dt.date(2025, 9, 5), 6, 'A', 1, 'x')]) as con:
        assert [(r[0], r[1]) for r in con.execute(compiled.answer.sql).fetchall()] == [
            (dt.date(2025, m, 1), 4 if m == 1 else 6 if m == 9 else None) for m in range(1, 10)]


@pytest.mark.parametrize('window,expected', [('YTD', 26), ('fiscal YTD', 13), ('MTD', 2), ('QTD', 13)])
@pytest.mark.parametrize('cutoff', ['18 June 2025', '(18 June 2025)', 'the 18 June 2025'])
def test_named_day_cutoff_includes_that_day_and_excludes_the_following_day(period_reader, window, expected, cutoff):
    read, _ = period_reader
    text = f'Metric {window} through {cutoff}'
    plan, compiled, vocab = read(text)
    rows = [(dt.date(2025, 1, 15), 13, 'A', 1, 'x'), (dt.date(2025, 5, 31), 11, 'A', 2, 'x'),
            (dt.date(2025, 6, 18), 2, 'A', 3, 'x'), (dt.date(2025, 6, 19), 7, 'A', 4, 'x')]
    with connection(rows) as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] == expected
    raw = plan.model_dump(mode='json', by_alias=True)
    endpoint = raw['time'].get('last', raw['time'].get('through'))
    endpoint['value'] = '2025-06-30'
    wrong = ground_plan(QueryPlan.model_validate(raw), text, 'turn', vocab)
    assert not isinstance(check_plan(wrong, vocab, text), CheckedPlan)


def test_iso_week_reader_uses_its_week_year_and_monday_bounds(period_reader):
    read, _ = period_reader
    plan, compiled, vocab = read('Metric for 2025-W01')
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2024, 12, 30), dt.date(2025, 1, 6))
    rows = [(dt.date(2024, 12, 29), 3, 'A', 1, 'x'), (dt.date(2024, 12, 30), 5, 'A', 2, 'x'),
            (dt.date(2025, 1, 5), 7, 'A', 3, 'x'), (dt.date(2025, 1, 6), 11, 'A', 4, 'x')]
    with connection(rows) as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] == 12


def test_iso_week_ranges_cross_years_without_changing_calendar_years(period_reader):
    read, _ = period_reader
    plan, compiled, vocab = read('Metric for 2024-W52 through 2025-W01')
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2024, 12, 23), dt.date(2025, 1, 6))
    rows = [(dt.date(2024, 12, 22), 11, 'A', 1, 'x'), (dt.date(2024, 12, 23), 3, 'A', 2, 'x'),
            (dt.date(2024, 12, 30), 5, 'A', 3, 'x'), (dt.date(2025, 1, 5), 7, 'A', 4, 'x'),
            (dt.date(2025, 1, 6), 11, 'A', 5, 'x')]
    with connection(rows) as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] == 15


@pytest.mark.parametrize('question', ['Month over month change in Metric by month from January to September 2025',
                                    'Metric by month from January to September 2025 month over month change'])
def test_relative_change_keeps_a_range_and_time_breakdown(period_reader, question):
    read, _ = period_reader
    plan, _, vocab = read(question)
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2025, 1, 1), dt.date(2025, 10, 1))
    assert any(c.kind == 'previous_period' for c in plan.comparisons)


def test_percent_change_from_two_periods_grounds_the_comparison_on_both_paths(period_reader):
    read, _ = period_reader
    text = 'percent change in Metric from March to April 2025'
    plan, compiled, vocab = read(text)
    rows = [(dt.date(2025, 3, 1), 10, 'A', 1, 'x'), (dt.date(2025, 4, 1), 15, 'A', 1, 'x')]
    with connection(rows) as con:
        assert con.execute(compiled.answer.sql).fetchone()[:4] == (15, 10, 5, .5)
    supplied = plan.model_copy(update={'derived': tuple(d for d in plan.derived if d.kind == 'percent_change'), 'obligations': ()})
    assert isinstance(check_plan(ground_plan(supplied, text, 'turn', vocab), vocab, text), CheckedPlan)


@pytest.mark.parametrize('phrase', ['year to date', 'month to date', 'quarter to date', 'January through September'])
def test_unanchored_windows_refuse_without_declared_current_period(period_reader, phrase):
    read, sem = period_reader
    for calendar in sem.d.CALENDARS.values():
        calendar.pop('current_period', None)
    with pytest.raises(AssertionError):
        read('Metric for ' + phrase)


@pytest.mark.parametrize('phrase', ['September through January 2025', 'Q3 through Q1 2025',
                                  'January through September 2025 and June 2024',
                                  'year to date through June 2025 and May 2025'])
def test_ambiguous_or_extra_periods_never_shrink_to_one_endpoint(period_reader, phrase):
    read, _ = period_reader
    with pytest.raises(AssertionError):
        read('Metric for ' + phrase)


def test_model_calendar_metadata_is_serializable_and_only_names_readable_columns():
    from kge.services.ask.engine.planmodel import payload
    sem = semantics()
    sem.tables['u.days'] = {'day': 'DATE', 'month': 'INTEGER'}
    sem.d.CALENDARS['cal']['spine'] = dict(table='u.days', column='day',
        attributes={'month': 'u.days.month', 'year': 'hidden.days.year'})
    visible = payload('Metric', vocabulary(sem))['vocabulary']['calendars']['cal']['spine']
    assert visible == dict(table='u.days', column='day', attributes={'month': 'u.days.month'})
    sem.d.CALENDARS['cal']['spine']['table'] = 'hidden.days'
    assert 'spine' not in payload('Metric', vocabulary(sem))['vocabulary']['calendars']['cal']


def test_implied_year_uses_the_named_contexts_calendar(period_reader):
    read, sem = period_reader
    sem.d.CALENDARS['fiscal']['current_period'] = '2024-06-15'
    plan, _, vocab = read('Metric from January through June')
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2025, 1, 1), dt.date(2025, 7, 1))


def test_period_context_lookup_handles_empty_names_and_mixed_case_grouping():
    sem = semantics()
    sem.d.GROUP_BY = {'by CATEGORY': 'u.events.category'}
    assert ledger('Metric each CATEGORY for June 2025', 'turn', vocabulary(sem))
    empty = replace(vocabulary(), phrases={})
    assert ledger(' January through June', 'turn', empty)


def test_fiscal_breakdown_cannot_change_an_explicit_calendar_year_to_date(period_reader):
    read, _ = period_reader
    plan, _, vocab = read('Metric by fiscal quarter for calendar year to date through September 2025')
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2025, 1, 1), dt.date(2025, 10, 1))
    assert plan.time.calendar.id == 'cal'
    assert plan.dimensions[0].calendar.id == 'fiscal'


def test_output_threshold_cannot_consume_a_stated_year_cutoff(period_reader):
    read, _ = period_reader
    text = 'Metric equal to 2025 fiscal YTD through 2025'
    plan, _, vocab = read(text)
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2025, 4, 1), dt.date(2026, 1, 1))
    raw = plan.model_dump(mode='json', by_alias=True)
    raw['time']['through'] = dict(kind='day', value='2025-06-15', calendar=raw['time']['calendar'])
    wrong = ground_plan(QueryPlan.model_validate(raw), text, 'turn', vocab)
    assert not isinstance(check_plan(wrong, vocab, text), CheckedPlan)


@pytest.mark.parametrize('connector', ['through', 'to', 'as of', 'as at', 'for', 'in', 'during', '', 'through current',
                                     'for the', 'for (', '(', ':', 'for the ('])
def test_malformed_cutoff_cannot_become_an_output_threshold_or_current_date(period_reader, connector):
    read, sem = period_reader
    text = f'Metric equal to 2025 YTD {connector} 2025.0' + (')' if '(' in connector else '')
    with pytest.raises(AssertionError):
        read(text)
    plan, _, _ = read('Metric YTD')
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    raw = plan.model_dump(mode='json', by_alias=True)
    raw['filters'] = [dict(kind='compare', id='threshold', left=dict(kind='output', id=plan.measures[0].id), op='eq',
                           value=dict(kind='integer', value=2025), evidence=[])]
    wrong = ground_plan(QueryPlan.model_validate(raw), text, 'turn', vocab)
    decision = check_plan(wrong, vocab, text)
    assert decision.kind == 'abstain' and any('valid stated cutoff' in reason for reason in decision.missing)


@pytest.mark.parametrize('window', ['YTD', 'MTD', 'QTD'])
def test_explicit_calendar_to_date_cannot_silently_use_a_fiscal_year_cutoff(period_reader, window):
    read, _ = period_reader
    text = f'Metric calendar {window} through FY25'
    with pytest.raises(AssertionError):
        read(text)
    plan, _, vocab = read(f'Metric fiscal {window} through FY25')
    wrong = ground_plan(plan.model_copy(update={'obligations': ()}), text, 'turn', vocab)
    decision = check_plan(wrong, vocab, text)
    assert decision.kind == 'abstain' and any('calendar cutoff' in reason for reason in decision.missing)


def test_implicit_fiscal_labels_never_wrap_into_an_earlier_century(period_reader):
    read, sem = period_reader
    sem.d.CALENDARS['fiscal']['current_period'] = '2099-06-15'
    with pytest.raises(AssertionError):
        read('Metric for first three fiscal quarters')


@pytest.mark.parametrize('phrase,operation', [('year to date', 'ytd'), ('month to date', 'mtd'), ('quarter to date', 'qtd')])
def test_to_date_window_with_no_stated_cutoff_is_anchored_on_the_current_period(period_reader, phrase, operation):
    from kge.semantics.coverage import to_date_selection
    from kge.semantics.planperiods import to_date_operation
    _, sem = period_reader
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    current = to_date_selection(ledger('Metric ' + phrase, 'turn', vocab), vocab, 'Metric ' + phrase)
    assert to_date_operation(current) == operation
    assert current.model_dump(mode='json')['anchor'] == 'current'
    stated = to_date_selection(ledger('Metric ' + phrase + ' through June 2025', 'turn', vocab), vocab, 'Metric ' + phrase + ' through June 2025')
    assert to_date_operation(stated) is None
    assert 'anchor' not in stated.model_dump(mode='json') and 'to_date' not in stated.model_dump(mode='json')


@pytest.fixture
def released_reader(monkeypatch):
    """A release-governed context whose calendar's current period, 15 July 2025, sits in a month that is not released."""
    from tests.plan_fixtures import semantics as fixture_semantics
    sem = fixture_semantics(release=True)
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    sem.d.CALENDARS['cal']['current_period'] = '2025-07-15'
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', ytd_calendar='cal')
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)

    def read(text):
        vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
        reply = planrules.read(engine, parse(text, ['Metric'], sem.d), vocab, SimpleNamespace(graph=True, glossary=True))
        assert reply.kind == 'propose', reply
        checked = check_plan(reply.plan, vocab, text)
        assert isinstance(checked, CheckedPlan), checked
        return checked
    return read


def release_rows(compiled, released_through):
    """The release calendar for 2025 on every requirement: months through `released_through` released, later ones open."""
    from kge.semantics.planperiods import add_months
    return [(need.tag, add_months(dt.date(2025, month, 1), 1) - dt.timedelta(days=1), month <= released_through, None)
            for need in compiled.needs for month in range(1, 13)]


def release_connection(events):
    con = connection(events)
    con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
    from kge.semantics.planperiods import add_months
    for month in range(1, 13):
        con.execute('INSERT INTO calendar_rows VALUES (?, NULL, NULL, ?)', [add_months(dt.date(2025, month, 1), 1) - dt.timedelta(days=1), month <= 6])
    return con


@pytest.mark.parametrize('phrase,first,last,expected', [
    ('year to date', '2025-01-01', '2025-07-01', 21),
    ('month to date', '2025-06-01', '2025-07-01', 6),
    ('quarter to date', '2025-04-01', '2025-07-01', 15),
])
def test_current_to_date_ends_at_latest_released_month(released_reader, phrase, first, last, expected):
    """July is open, so the current window is the same to-date window as at the end of June, the latest released month."""
    checked = released_reader('Metric ' + phrase)
    compiled = compile_plan(checked, release_rows(compile_plan(checked), 6))
    assert time_bounds(compiled.checked.plan.time, checked.vocabulary.calendars) == (dt.date.fromisoformat(first), dt.date.fromisoformat(last))
    assert all(need.window[1] <= dt.date(2025, 7, 1) for need in compiled.needs)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with release_connection([(dt.date(2025, m, 1), m, 'A', 1, 'x') for m in range(1, 13)]) as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] == expected


def test_current_to_date_whose_month_is_released_is_not_moved(released_reader):
    checked = released_reader('Metric year to date')
    compiled = compile_plan(checked, release_rows(compile_plan(checked), 7))
    assert compiled.checked.plan.time == checked.plan.time


def test_current_to_date_with_nothing_released_still_refuses(released_reader):
    checked = released_reader('Metric year to date')
    with pytest.raises(ValueError, match='not yet released'):
        compile_plan(checked, release_rows(compile_plan(checked), 0))


def test_stated_cutoff_in_an_open_month_still_refuses(released_reader):
    checked = released_reader('Metric year to date through July 2025')
    with pytest.raises(ValueError, match='July 2025 is not yet released'):
        compile_plan(checked, release_rows(compile_plan(checked), 6))


def test_current_to_date_without_release_policy_ends_at_current_period(period_reader):
    read, _ = period_reader
    plan, compiled, vocab = read('Metric for year to date')
    assert compiled.checked.plan.time == plan.time
    assert time_bounds(plan.time, vocab.calendars) == (dt.date(2025, 1, 1), dt.date(2025, 6, 16))


def test_a_proposed_current_anchor_must_match_the_question(period_reader):
    """A proposer cannot re-anchor a stated cutoff on the current period, nor drop the anchor of an unstated one."""
    read, _ = period_reader
    plan, _, vocab = read('Metric for year to date through June 2025')
    anchored = plan.model_copy(update={'time': plan.time.model_copy(update={'anchor': 'current'})})
    assert not isinstance(check_plan(anchored, vocab, 'Metric for year to date through June 2025'), CheckedPlan)
    plan, _, vocab = read('Metric for year to date')
    stated = plan.model_copy(update={'time': plan.time.model_copy(update={'anchor': 'stated'})})
    assert not isinstance(check_plan(stated, vocab, 'Metric for year to date'), CheckedPlan)
