"""Bounded shape interpretation remains portable and independent of warehouse reads."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from kge.semantics.coverage import Piece, ground_plan, ledger
from kge.semantics.plan import Span
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Context
from kge.services.ask.engine import planrules, planshapes
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, semantics, vocabulary


@pytest.fixture
def declared(monkeypatch):
    sem = semantics()
    sem.d.GROUP_BY = {'by category': 'u.events.category', 'by source': 'u.events.source'}
    sem.measure_declarations = {'u.metric': {}}
    sem.output_metadata = {}
    eng = SimpleNamespace(semantics=sem, ALIASES={'metric': 'Metric'}, UNIT_WORDS={}, ACRONYMS={},
        GROUP_BY=sem.d.GROUP_BY, PERIODS=sem.d.PERIODS, CALENDARS=sem.d.CALENDARS)
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    return eng, vocabulary(sem, aliases=[('Metric', 'ctx'), ('by category', 'u.events.category'), ('by source', 'u.events.source')])


@pytest.mark.parametrize('phrase', ['by month', 'per month', 'each month', 'monthly', 'by quarter', 'per quarter',
                                   'each quarter', 'quarterly', 'by year', 'per year', 'each year', 'yearly'])
def test_time_grains_are_declared_on_the_measure_calendar(declared, phrase):
    eng, v = declared
    text = 'Metric for Q1 2025 ' + phrase
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)
    dimension, = reply.plan.dimensions
    assert dimension.calendar.id == 'cal'
    assert dimension.grain == ('calendar_quarter' if 'quarter' in phrase else 'year' if 'year' in phrase else 'month')


@pytest.mark.parametrize('phrase', ['by month and category', 'by category, source and month', 'by source by month by category',
                                   'by category and source', 'by month, source and category'])
def test_coordinated_dimensions_use_only_declared_columns(declared, phrase):
    eng, v = declared
    text = 'Metric for Q1 2025 ' + phrase
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)
    assert len(reply.plan.dimensions) == sum(word in phrase for word in ('month', 'category', 'source'))


@pytest.mark.parametrize('phrase', ['January through March 2025', 'January to March 2025', 'from January to June 2025',
                                   'between January and March 2025', 'between January 2025 and March 2025'])
def test_month_ranges_bind_both_endpoints(declared, phrase):
    eng, v = declared
    text = 'Metric for ' + phrase
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)
    assert reply.plan.time.kind == 'range'
    assert reply.plan.time.first.value == '2025-01'
    assert reply.plan.time.last.value == ('2025-06' if 'June' in phrase else '2025-03')


@pytest.mark.parametrize('phrase', ['March through January 2025', 'January through June', 'January through notamonth 2025',
                                   'January through 31 February 2025', 'January through March 2025 by mystery',
                                   'between March and January 2025', 'between January and notamonth 2025'])
def test_incomplete_or_ambiguous_windows_and_dimensions_never_execute(declared, phrase):
    eng, v = declared
    text = 'Metric for ' + phrase
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
    if reply.kind == 'propose':
        assert not isinstance(check_plan(reply.plan, v, text), CheckedPlan)
    else:
        assert reply.kind in ('abstain', 'clarify')


@pytest.mark.parametrize('phrase', ['top 2 categories by Metric', 'which category had the most Metric', 'bottom 3 sources by Metric'])
def test_ranking_grounds_the_declared_dimension_measure_and_limit(declared, phrase):
    eng, v = declared
    text = phrase + ' for Q1 2025'
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)
    assert reply.plan.limit == (1 if 'which' in phrase else 2 if 'top' in phrase else 3)
    data = reply.plan.model_dump(mode='json', by_alias=True)
    data['limit'] += 1
    wrong = type(reply.plan).model_validate(data)
    assert not isinstance(check_plan(ground_plan(wrong, text, 'turn', v), v, text), CheckedPlan)


def test_no_undeclared_grouping_alias_is_added_to_the_vocabulary(declared):
    _, v = declared
    before = dict(v.phrases)
    pieces = ledger('Metric by category and source', 'turn', v)
    assert [(p.text, p.candidates) for p in pieces if p.operation == 'dimension'] == [
        ('by category', ('u.events.category',)), ('source', ('u.events.source',))]
    assert dict(v.phrases) == before


def test_which_keeps_existing_model_ordering_and_explicit_numeric_limits(declared):
    eng, v = declared
    text = 'which category had the most Metric for Q1 2025'
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
    assert reply.kind == 'propose' and reply.plan.limit == 1
    unlimited = reply.plan.model_copy(update={'limit': None})
    assert isinstance(check_plan(ground_plan(unlimited, text, 'turn', v), v, text), CheckedPlan)
    text = 'which 2 categories had the most Metric for Q1 2025'
    explicit = reply.plan.model_copy(update={'limit': 2})
    assert isinstance(check_plan(ground_plan(explicit, text, 'turn', v), v, text), CheckedPlan)
    assert not isinstance(check_plan(ground_plan(reply.plan, text, 'turn', v), v, text), CheckedPlan)


def test_unrecognized_text_retains_the_existing_coverage_refusal_contract(declared):
    _, v = declared
    text = 'unknown versus Metric'
    pieces = ledger(text, 'turn', v)
    assert any(p.kind == 'unknown' for p in pieces)
    data = model().model_dump(mode='json', by_alias=True)
    before = model().model_dump(mode='json', by_alias=True)
    assert planshapes.compose(data, SimpleNamespace(question=text), pieces, v) is None
    assert data == before


def test_named_shares_preserve_the_existing_whole_population_words(declared):
    _, v = declared
    v = replace(v, phrases={**v.phrases, 'total': ('ctx',), 'total metric': ('ctx',)})
    pieces = ledger('Metric share of total for Q1 2025', 'turn', v)
    share, = [p for p in pieces if p.text == 'share']
    assert share.operation == 'share' and share.value == 'whole_population'
    pieces = ledger('Metric as a share of total Metric for Q1 2025', 'turn', v)
    assert next(p for p in pieces if p.text == 'share').operation == 'ratio'


def test_business_synonyms_deduplicate_only_when_the_pack_declares_one_column(declared):
    eng, _ = declared
    for same in (True, False):
        sem = eng.semantics
        sem.d.GROUP_BY = {'by business line': 'u.events.category',
                          'by sub-segment': 'u.events.category' if same else 'u.events.source'}
        eng.GROUP_BY = sem.d.GROUP_BY
        v = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
        text = 'Metric for Q1 2025 by business line by month by sub-segment'
        reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'])
        assert reply.kind == 'propose'
        assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)
        assert len(reply.plan.dimensions) == (2 if same else 3)


def test_measure_parts_keep_declared_predicate_types_and_ask_on_ambiguous_columns():
    sem = semantics()
    sem.tables['u.events']['direction'] = 'INTEGER'
    v = vocabulary(sem)
    text = 'inflows and outflows'
    pieces = [Piece(Span(turn='turn', start=0, end=7), 'inflows', 'predicate', ('u.events.direction',), 'eq', 1),
              Piece(Span(turn='turn', start=12, end=20), 'outflows', 'predicate', ('u.events.direction',), 'eq', -1)]
    data = model().model_dump(mode='json', by_alias=True)
    assert planshapes.parts(data, pieces, v, text) is None
    assert [m['filters'][0]['value'] for m in data['measures']] == [
        dict(kind='integer', value=1), dict(kind='integer', value=-1)]
    assert len({m['filters'][0]['id'] for m in data['measures']}) == 2
    data = model().model_dump(mode='json', by_alias=True)
    pieces[0] = Piece(pieces[0].span, 'inflows', 'predicate', ('u.events.direction', 'u.events.value'), 'eq', 1)
    assert planshapes.parts(data, pieces, v, text) == 'Please identify the declared predicate for this measure part.'


@pytest.mark.parametrize('operands', ['certified Metric versus current Metric',
                                    'Compare certified Metric with current Metric',
                                    'Compare current with certified Metric'])
def test_context_difference_keeps_its_month_range_instead_of_comparing_endpoints(operands):
    sem = semantics()
    ctx = sem.contexts['ctx']
    sem.contexts['cert'] = Context('cert', 'Certified Metric', '', 'Certified Metric', ctx.measure, ctx.expression,
        ctx.aggregation, ctx.grain, ctx.fact, ctx.values, ctx.filters, ctx.selectors, ctx.joins)
    v = vocabulary(sem, aliases=[('Metric', 'ctx'), ('certified Metric', 'cert')])
    text = operands + ' by month from January to March 2025 with difference'
    pieces = ledger(text, 'turn', v)
    points = [p for p in pieces if p.kind == 'time']
    assert len(points) == 2
    assert all(set(p.window) == {point.span for point in points} for p in points)
    assert next(p for p in pieces if p.text == 'difference').operation == 'difference'
