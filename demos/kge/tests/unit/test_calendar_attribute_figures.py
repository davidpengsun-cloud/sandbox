"""Calendar attribute names and recurring values come from data and retain hand-computed figures."""
import copy
import datetime as dt
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.compiler.checks import SECTIONS, calendars, shape
from kge.compiler.pack import Pack
from kge.pipeline.sqlguard import check
from kge.semantics.calendarattributes import masked_question, matches
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_calendar_spine import calendar_connection
from tests.unit.test_plan_phrase_grammar import Reply


def declarations():
    return {'table': 'u.days', 'column': 'date', 'attributes': {
        'season_slot': 'u.events.entity_id', 'block_slot': 'u.events.entity_id'},
        'recurrences': [dict(attribute='season_slot', grain='month', cycle='year', values=[
            dict(value=6, phrases=['June', 'Jun'])]),
            dict(attribute='block_slot', grain='week', cycle='month', values=[
                dict(value=2, phrases=['week 2'])])]}


def declared():
    sem = semantics()
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    sem.d.FISCAL_START = 4
    sem.tables['u.days'] = {'date': 'DATE', 'calendar_year': 'INTEGER'}
    sem.d.CALENDARS['cal']['spine'] = declarations()
    return sem


def checked_rules(text, sem, monkeypatch):
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    assert not [piece.text for piece in ledger(text, 'turn', vocab) if piece.kind == 'unknown']
    reply = planrules.read(engine, parse(text, ['Metric'], sem.d), vocab,
                           SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'propose', reply
    decision = check_plan(reply.plan, vocab, text)
    assert isinstance(decision, CheckedPlan), decision
    replay = planmodel.read(Reply(ground_plan(reply.plan.model_copy(update={'obligations': ()}),
                                             text, 'turn', vocab)), text, vocab)
    assert replay.kind == 'propose', replay
    model_decision = check_plan(replay.plan, vocab, text)
    assert isinstance(model_decision, CheckedPlan), model_decision
    statements = [compile_plan(plan).answer for plan in (decision, model_decision)]
    for statement in statements:
        assert check(statement.sql, statement.allow) == []
    return reply.plan, statements, vocab


@pytest.mark.parametrize('wording', ['every June', 'each June', 'every Jun'])
def test_month_of_year_across_years_uses_arbitrary_declared_attribute(wording, monkeypatch):
    text = f'Metric for {wording}, 2023 to 2025'
    sem = declared()
    plan, statements, _ = checked_rules(text, sem, monkeypatch)
    dimension, = plan.dimensions
    predicate, = plan.filters
    assert dimension.grain == 'year' and predicate.value.value == 6
    assert predicate.left.name.id == 'u.events.entity_id'
    rows = [(dt.date(year, month, 10), value, 'A', month, 'x')
            for year, june, july in ((2023, 10, 100), (2024, 20, 200), (2025, 30, 300))
            for month, value in ((6, june), (7, july))]
    with calendar_connection(rows, dt.date(2023, 1, 1), dt.date(2026, 1, 1)) as connection:
        for statement in statements:
            actual = connection.execute(statement.sql).fetchall()
            assert [(row[0].year, row[1]) for row in actual] == [(2023, 10), (2024, 20), (2025, 30)]


def test_week_of_month_across_months_keeps_only_the_declared_week(monkeypatch):
    text = 'Metric for week 2 of each month for January through March 2025'
    plan, statements, _ = checked_rules(text, declared(), monkeypatch)
    assert [dimension.grain for dimension in plan.dimensions] == ['month']
    rows = [(dt.date(2025, month, day), value, 'A', week, 'x')
            for month, selected, other in ((1, 3, 30), (2, 5, 50), (3, 7, 70))
            for day, week, value in ((8, 2, selected), (22, 4, other))]
    with calendar_connection(rows, dt.date(2025, 1, 1), dt.date(2025, 4, 1)) as connection:
        for statement in statements:
            actual = connection.execute(statement.sql).fetchall()
            assert [(row[0].month, row[1]) for row in actual] == [(1, 3), (2, 5), (3, 7)]


@pytest.mark.parametrize('grain', ['month', 'quarter', 'quarter_end', 'year'])
def test_coarser_observations_cannot_be_filtered_as_a_recurring_week(monkeypatch, grain):
    sem = declared()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain=grain)
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    text = 'Metric for week 2 of each month for January through March 2025'
    rules = planrules.read(engine, parse(text, ['Metric'], sem.d), vocab,
                           SimpleNamespace(graph=True, glossary=True))
    assert rules.kind == 'propose', rules
    replay = planmodel.read(Reply(ground_plan(rules.plan.model_copy(update={'obligations': ()}),
                                             text, 'turn', vocab)), text, vocab)
    assert replay.kind == 'propose', replay
    for reply in (rules, replay):
        decision = check_plan(reply.plan, vocab, text)
        assert not isinstance(decision, CheckedPlan)
        assert 'requested calendar attribute is finer than the declared observations' in decision.missing


def test_calendar_wordings_do_not_change_an_ordinary_absolute_month():
    vocab = vocabulary(declared())
    text = 'Metric for June 2025'
    assert not matches(text, vocab)
    assert masked_question(text, vocab) == text


def test_a_calendar_value_without_a_declaration_remains_unknown():
    sem = declared()
    sem.d.CALENDARS['cal']['spine'].pop('recurrences')
    vocab = vocabulary(sem)
    assert not matches('Metric for every June, 2023 to 2025', vocab)


def test_a_bare_week_number_is_not_guessed_as_week_of_month():
    vocab = vocabulary(declared())
    assert not matches('Metric for week 2 of 2025', vocab)
    assert not matches('Metric for week 2 of each year', vocab)


def test_hidden_attribute_columns_never_enter_the_lexical_vocabulary():
    sem = declared()
    sem.d.CALENDARS['cal']['spine']['attributes']['season_slot'] = 'hidden.days.month'
    assert not matches('Metric for every June', vocabulary(sem))
    payload = planmodel.payload('Metric', vocabulary(sem))['vocabulary']['calendars']['cal']['spine']
    assert 'season_slot' not in payload['attributes']
    assert [entry['attribute'] for entry in payload['recurrences']] == ['block_slot']


def test_a_model_cannot_change_the_recurring_value(monkeypatch):
    text = 'Metric for every June, 2023 to 2025'
    plan, _, vocab = checked_rules(text, declared(), monkeypatch)
    bad = plan.model_copy(update={'filters': (plan.filters[0].model_copy(update={
        'value': plan.filters[0].value.model_copy(update={'value': 7})}),)})
    reply = planmodel.read(Reply(ground_plan(bad, text, 'turn', vocab)), text, vocab)
    assert reply.kind != 'propose' or not isinstance(check_plan(reply.plan, vocab, text), CheckedPlan)


def pack():
    spine = declarations()
    data = dict(calendars=[dict(id='cal', year_start_month=1, quarter_label='{yyyy}-Q{n}', spine=spine)], measures=[])
    lock = dict(sources={'u': dict(tables=[
        dict(name='days', primary_key=['date'], columns=[dict(name='date', type='DATE')]),
        dict(name='events', primary_key=['entity_id'], columns=[dict(name='entity_id', type='INTEGER')])])})
    return Pack('example', Path('.'), data=data, lock=lock)


def test_recurring_calendar_values_round_trip_through_the_pack_contract():
    held = pack()
    findings = []
    shape(held.data['calendars'], SECTIONS['calendars'], 'calendars', findings)
    assert findings == [] and calendars(held) == []


@pytest.mark.parametrize('change,reason', [
    (lambda item: item.update(attribute='undeclared'), 'attribute declared'),
    (lambda item: item.update(cycle='month'), 'coarser'),
    (lambda item: item.update(values=[]), 'at least one'),
    (lambda item: item['values'][0].update(value=True), 'text or numeric'),
    (lambda item: item['values'][0].update(value='June'), 'scalar type'),
    (lambda item: item['values'][0].update(phrases=[]), 'at least one'),
    (lambda item: item['values'][0].update(phrases=['   ']), 'nonempty'),
    (lambda item: item['values'][0].update(phrases=['June', 'JUNE']), 'wording once'),
    (lambda item: item['values'].append(copy.deepcopy(item['values'][0])), 'value once'),
])
def test_invalid_recurring_calendar_values_fail_closed(change, reason):
    held = pack()
    change(held.data['calendars'][0]['spine']['recurrences'][0])
    assert any(reason in finding.message for finding in calendars(held))


def shared_calendar_pack():
    held = pack()
    second = copy.deepcopy(held.data['calendars'][0])
    second.update(id='fiscal', year_start_month=4, quarter_label='FY{yy}-Q{n}')
    held.data['calendars'].append(second)
    return held


def test_identical_recurrence_meanings_can_be_shared_by_calendar_and_fiscal_calendars():
    held = shared_calendar_pack()
    held.data['calendars'][1]['spine']['recurrences'][0]['values'][0]['phrases'] = ['JUNE', 'Jun']
    assert calendars(held) == []


@pytest.mark.parametrize('field,value', [('column', 'u.events.other_slot'), ('value', 7),
                                       ('grain', 'week'), ('cycle', 'fiscal_year')])
def test_one_recurring_wording_cannot_have_different_meanings_across_calendars(field, value):
    held = shared_calendar_pack()
    spine = held.data['calendars'][1]['spine']
    recurrence = spine['recurrences'][0]
    if field == 'column':
        held.lock['sources']['u']['tables'][1]['columns'].append(dict(name='other_slot', type='INTEGER'))
        spine['attributes']['season_slot'] = value
    elif field == 'value':
        recurrence['values'][0]['value'] = value
    else:
        recurrence[field] = value
    findings = calendars(held)
    assert any('different calendar meanings across calendars' in finding.message for finding in findings)
