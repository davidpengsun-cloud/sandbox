"""Declared calendar periods and week comparisons against hand-computed figures."""
import datetime as dt

import pytest

from kge.compiler.vocabulary import calendar_entries, measure_entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plan import Binding, Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name
from tests.unit.test_measure_capability_figures import declarations, SuppliedPlan
from tests.unit.test_plan_compiler import connection


def calendar_vocabulary(monkeypatch=None, empty='null', week_start='monday'):
    engine, _ = declarations(monkeypatch)
    sem = engine.semantics
    sem.tables['u.days'] = {'date_value': 'DATE', 'other_date': 'DATE'}
    pack = {'calendars': [{'id': 'cal', 'week_start': week_start, 'spine': {'table': 'u.days', 'column': 'date_value'}}],
            'measures': [{'id': 'u.metric', 'empty': empty}]}
    sem.d.VOCABULARY = calendar_entries(pack) + measure_entries(pack)
    vocab = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                             ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True)], aliases=[('Metric', 'ctx')])
    return engine, vocab


def ranged(kind, first, last):
    return {'kind': 'range', 'first': {'kind': kind, 'value': first, 'calendar': name('cal')},
            'last': {'kind': kind, 'value': last, 'calendar': name('cal')}}


def dimension(grain):
    return {'kind': 'time', 'id': 't', 'calendar': name('cal'), 'grain': grain}


def compiled(vocab, plan, text):
    proposed = ground_plan(plan, text, 'turn', vocab)
    reply = planmodel.read(SuppliedPlan(proposed), text, vocab)
    assert isinstance(reply, Propose), reply
    checked = check_plan(reply.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None
    assert check(result.answer.sql, result.answer.allow) == []
    return result


def figures(result, rows):
    with connection([(dt.date.fromisoformat(day), amount, 'A', 1, 'x') for day, amount in rows]) as con:
        con.execute('CREATE TABLE days(date_value DATE PRIMARY KEY, other_date DATE)')
        con.execute("INSERT INTO days SELECT CAST(d AS DATE), CAST(d AS DATE) FROM generate_series(DATE '2022-01-01', DATE '2027-12-31', INTERVAL 1 DAY) t(d)")
        assert check(result.answer.sql, result.answer.allow) == []
        return con.execute(result.answer.sql).fetchall()


CASES = [
    ('day', 'month', '2025-04', '2025-04', 'April through April 2025',
     [f'2025-04-{n:02}' for n in range(1, 31)], [('2025-04-01', 2), ('2025-04-30', 5)]),
    ('week', 'day', '2024-12-23', '2025-01-12', '2024-12-23 through 2025-01-12',
     ['2024-12-23', '2024-12-30', '2025-01-06'], [('2024-12-23', 2), ('2025-01-10', 5)]),
    ('month', 'month', '2025-04', '2025-06', 'April through June 2025',
     ['2025-04-01', '2025-05-01', '2025-06-01'], [('2025-04-10', 2), ('2025-06-10', 5)]),
    ('calendar_quarter', 'month', '2025-01', '2025-09', 'January through September 2025',
     ['2025-01-01', '2025-04-01', '2025-07-01'], [('2025-01-10', 2), ('2025-07-10', 5)]),
    ('year', 'month', '2023-01', '2025-12', 'January 2023 through December 2025',
     ['2023-01-01', '2024-01-01', '2025-01-01'], [('2023-04-10', 2), ('2025-06-10', 5)]),
]


@pytest.mark.parametrize('empty', ['zero', 'null'])
@pytest.mark.parametrize('grain,kind,first,last,phrase,ticks,rows', CASES)
def test_every_grain_returns_the_empty_period_with_the_declared_value(empty, grain, kind, first, last, phrase, ticks, rows):
    _, vocab = calendar_vocabulary(empty=empty)
    text = 'Metric by ' + grain.replace('_', ' ') + ' for ' + phrase
    result = compiled(vocab, model(time=ranged(kind, first, last), dimensions=[dimension(grain)]), text)
    actual = figures(result, rows)
    assert [r[0].isoformat() for r in actual] == ticks
    assert [r[1] for r in actual] == [2, *([0 if empty == 'zero' else None] * (len(ticks) - 2)), 5]
    assert 'days' in result.answer.sql and 'GENERATE_SERIES' not in result.answer.sql


@pytest.mark.parametrize('word', ['by week', 'by weeks', 'weekly', 'per week'])
def test_rules_and_model_group_iso_weeks_across_the_year(monkeypatch, word):
    engine, vocab = calendar_vocabulary(monkeypatch, empty='zero')
    text = f'Metric {word} for January 2025'
    reply = planrules.read(engine, parse(text, engine.term_names, engine), vocab, PRESETS['graph'])
    assert isinstance(reply, Propose), reply
    assert [d.grain for d in reply.plan.dimensions] == ['week']
    result = compiled(vocab, reply.plan, text)
    actual = figures(result, [('2024-12-29', 2), ('2024-12-30', 3), ('2025-01-01', 4), ('2025-01-05', 1), ('2025-01-12', 5)])
    assert [(r[0].isoformat(), r[1]) for r in actual] == [('2024-12-30', 5), ('2025-01-06', 5),
                                                                     ('2025-01-13', 0), ('2025-01-20', 0), ('2025-01-27', 0)]


def test_declared_week_start_applies_to_both_facts_and_calendar():
    _, vocab = calendar_vocabulary(empty='zero', week_start='sunday')
    text = 'Metric by week for 2025-01-01 through 2025-01-12'
    plan = model(time=ranged('day', '2025-01-01', '2025-01-12'), dimensions=[dimension('week')])
    actual = figures(compiled(vocab, plan, text), [('2025-01-04', 2), ('2025-01-05', 3), ('2025-01-12', 5)])
    assert [(r[0].isoformat(), r[1]) for r in actual] == [('2024-12-29', 2), ('2025-01-05', 3), ('2025-01-12', 5)]


def test_all_available_breakdown_uses_the_same_declared_calendar():
    _, vocab = calendar_vocabulary(empty='zero')
    actual = figures(compiled(vocab, model(dimensions=[dimension('month')]), 'Metric by month for all available'),
                     [('2025-04-10', 2), ('2025-06-10', 5)])
    assert [(r[0].isoformat(), r[1]) for r in actual] == [('2025-04-01', 2), ('2025-05-01', 0), ('2025-06-01', 5)]


@pytest.mark.parametrize('grain,first,last,rows,ticks', [
    ('fiscal_quarter', 'FY26-Q1', 'FY26-Q3', [('2025-04-10', 2), ('2025-10-10', 5)],
     ['2025-04-01', '2025-07-01', '2025-10-01']),
    ('fiscal_year', 'FY24', 'FY26', [('2023-04-10', 2), ('2025-10-10', 5)],
     ['2023-04-01', '2024-04-01', '2025-04-01']),
])
def test_fiscal_periods_derive_from_the_same_daily_spine(grain, first, last, rows, ticks):
    from dataclasses import replace
    _, vocab = calendar_vocabulary(empty='zero')
    cal = dict(vocab.calendars['cal']) | {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    vocab = replace(vocab, calendars={'cal': cal})
    text = 'Metric by ' + grain.replace('_', ' ') + ' for ' + first + ' through ' + last
    result = compiled(vocab, model(time=ranged(grain, first, last), dimensions=[dimension(grain)]), text)
    actual = figures(result, rows)
    assert [(str(r[0])[:10], r[1]) for r in actual] == list(zip(ticks, [2, 0, 5], strict=True))


@pytest.mark.parametrize('grain', ['week', 'month', 'fiscal_quarter', 'fiscal_year'])
def test_bigquery_translation_preserves_the_checked_spine(grain):
    from dataclasses import replace
    from kge.adapters.warehouse.bigquery import rewrite_sql
    _, vocab = calendar_vocabulary()
    time = ranged('month', '2025-01', '2025-06')
    phrase = 'January through June 2025'
    if grain.startswith('fiscal_'):
        vocab = replace(vocab, calendars={'cal': dict(vocab.calendars['cal']) | {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}})
        time = {'kind': 'single', 'period': {'kind': 'fiscal_quarter', 'value': 'FY26-Q1', 'calendar': name('cal')}}
        phrase = 'FY26-Q1'
    text = 'Metric by ' + grain.replace('_', ' ') + ' for ' + phrase
    result = compiled(vocab, model(time=time, dimensions=[dimension(grain)]), text)
    translated = rewrite_sql(result.answer.sql, lambda _: '')
    assert check(translated, result.answer.allow, dialect='bigquery') == []
    if grain == 'week':
        for anchor in ('0001-01-02', '0001-01-07'):
            changed = translated.replace('0001-01-01', anchor)
            assert changed != translated
            assert check(changed, result.answer.allow, dialect='bigquery')


@pytest.mark.parametrize('word', ['week over week', 'week-over-week', 'previous week', 'prior week', 'wow'])
def test_week_comparison_shifts_by_seven_days(monkeypatch, word):
    engine, vocab = calendar_vocabulary(monkeypatch, empty='zero')
    text = f'Metric by week for January 2025 {word}'
    reply = planrules.read(engine, parse(text, engine.term_names, engine), vocab, PRESETS['graph'])
    assert isinstance(reply, Propose), reply
    result = compiled(vocab, reply.plan, text)
    actual = figures(result, [('2025-01-03', 2), ('2025-01-10', 5)])
    assert [(r[0].isoformat(), r[1], r[2]) for r in actual[:2]] == [('2024-12-30', 2, 0), ('2025-01-06', 5, 2)]
    assert "INTERVAL '-7' DAY" in result.answer.sql


@pytest.mark.parametrize('before,after', [('SELECT DISTINCT', 'SELECT'), ("'MONTH'", "'YEAR'"),
                                        ('"calendar_days"."date_value"', '"calendar_days"."other_date"'),
                                        ("'2025-04-01'", "'2025-05-01'"), ('CROSS JOIN calendar_ticks', 'CROSS JOIN calendar_ticks AS changed')])
def test_gate_binds_calendar_source_grain_bounds_and_distinct(before, after):
    _, vocab = calendar_vocabulary()
    result = compiled(vocab, model(time=ranged('month', '2025-04', '2025-06'), dimensions=[dimension('month')]),
                      'Metric by month for April through June 2025')
    sql = result.answer.sql.replace(before, after)
    assert sql != result.answer.sql
    assert check(sql, result.answer.allow)


def test_partial_first_week_does_not_hide_the_next_weeks_predecessor():
    _, vocab = calendar_vocabulary(empty='zero')
    plan = model(time=ranged('day', '2025-01-01', '2025-01-19'), dimensions=[dimension('week')],
                 comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    result = compiled(vocab, plan, 'Metric by week for 2025-01-01 through 2025-01-19 previous period')
    actual = figures(result, [('2025-01-03', 2), ('2025-01-10', 5), ('2025-01-17', 9)])
    assert [r[2] for r in actual] == [None, 2, 5]


def test_a_short_range_has_no_predecessor_even_when_empty_observations_are_zero():
    _, vocab = calendar_vocabulary(empty='zero')
    plan = model(time=ranged('day', '2025-01-04', '2025-01-10'), dimensions=[dimension('week')],
                 comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    result = compiled(vocab, plan, 'Metric by week for 2025-01-04 through 2025-01-10 previous period')
    actual = figures(result, [('2025-01-04', 7), ('2025-01-10', 31)])
    assert [r[2] for r in actual] == [None, None]
    assert [r[-1] for r in actual] == ['outside_requested_range', 'outside_requested_range']


@pytest.mark.parametrize('phrase', ['week over week', 'week-over-week', 'wow', 'prior week',
                                   'previous day', 'day over day', 'day-over-day', 'dod'])
def test_submonth_comparison_cannot_silently_compare_months(phrase):
    _, vocab = calendar_vocabulary()
    plan = model(time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-01', 'calendar': name('cal')}},
                 comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric for January 2025 ' + phrase
    result = check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text)
    assert not isinstance(result, CheckedPlan)
    assert 'comparison grain disagrees' in str(result)


@pytest.mark.parametrize('grain', ['day', 'week'])
def test_month_observations_cannot_be_broken_into_submonth_periods(grain):
    from dataclasses import replace
    engine, vocab = calendar_vocabulary()
    ctx = replace(engine.semantics.contexts['ctx'], grain='month')
    vocab = replace(vocab, contexts={'ctx': ctx})
    text = 'Metric by ' + grain + ' for January 2025'
    plan = model(time=ranged('month', '2025-01', '2025-01'), dimensions=[dimension(grain)])
    result = check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text)
    assert not isinstance(result, CheckedPlan)
    assert 'finer than the declared observations' in str(result)
