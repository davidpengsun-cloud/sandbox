"""Shared slot binding and the lexer markers (question-shapes.md §3.1, #450 requirement 2), on a synthetic pack: the
markers move no coded plan, from_to marks exactly the periods the coded comparison swaps, and the markers that cannot
reproduce their coded branch hold it coded (binding.HELD)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.semantics import coverage
from kge.compiler import standards
from kge.semantics.coverage import ADJACENT_DIRECTION, MARKERS, Piece, _flagged_part, ledger
from kge.semantics.plan import Span
from kge.semantics.query import Context
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import binding, planrules, planshapes
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, metric_vocabulary as vocabulary

SWEEP = [
    'Metric change from April 2025 to June 2025',
    'Metric difference from April 2025 through June 2025',
    'percent change in Metric from April 2025 to June 2025',
    'Metric from April 2025 to June 2025',
    'compare Metric from April 2025 to. June 2025',
    'Metric vs from April 2025 through. June 2025',
    'Metric between January 2025 and March 2025',
    'compare Metric for March 2025 to April 2025',
    'Metric for March 2025 vs April 2025',
    'Metric for March 2025 vs the previous year',
    'which category had the most Metric for Q1 2025',
    'which category had the highest and lowest Metric for Q1 2025',
    'which category had the most Metric in each source for Q1 2025',
    'top 2 categories by Metric for Q1 2025',
    'which 3 categories had the most Metric for Q1 2025',
    'bottom 3 sources by Metric for Q1 2025',
    'Metric inflows and outflows for Q1 2025',
    'Metric inflows for Q1 2025',
    'Metric for Q1 2025 by month',
    'certified Metric versus current Metric for March 2025',
    'Compare current with certified Metric for March 2025',
]


def pack(flag=True):
    """A synthetic pack: one measure on two contexts (Metric and certified Metric), two grouping columns and two
    predicates on one column, flagged `part: true` unless flag is False."""
    sem = semantics()
    sem.tables['u.events']['direction'] = 'VARCHAR'
    ctx = sem.contexts['ctx']
    sem.contexts['cert'] = Context('cert', 'Certified Metric', '', 'Certified Metric', ctx.measure, ctx.expression,
        ctx.aggregation, ctx.grain, ctx.fact, ctx.values, ctx.filters, ctx.selectors, ctx.joins)
    sem.d.GROUP_BY = {'by category': 'u.events.category', 'by source': 'u.events.source'}
    sem.d.VOCABULARY = [{'kind': 'predicate', 'params': {'column': 'u.events.direction', 'phrases': [word], 'op': 'eq',
                                                         'value': word, **({'part': True} if flag else {})}}
                        for word in ('inflows', 'outflows')]
    sem.d.VOCABULARY += [{'kind': 'predicate', 'params': {'column': 'u.events.direction', 'phrases': ['incoming'], 'op': 'eq',
                                                          'value': 'inflows'}},
                         {'kind': 'predicate', 'params': {'column': 'u.events.source', 'phrases': ['unsourced'], 'op': 'is_null',
                                                          **({'part': True} if flag else {})}}]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    sem.measure_declarations = {'u.metric': {}}
    sem.output_metadata = {}
    engine = SimpleNamespace(semantics=sem, ALIASES={'metric': 'Metric'}, UNIT_WORDS={}, ACRONYMS={}, GROUP_BY=sem.d.GROUP_BY,
                             PERIODS=sem.d.PERIODS, CALENDARS=sem.d.CALENDARS, FILTERS=words.filters, FILTER_COLUMNS=words.columns)
    return engine, sem


@pytest.fixture
def declared(monkeypatch):
    engine, sem = pack()
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    return engine


def fresh(engine):
    return vocabulary(engine.semantics, aliases=[('Metric', 'ctx'), ('certified Metric', 'cert'),
                                                 *engine.semantics.d.GROUP_BY.items()])


def coded(engine, text):
    reply = planrules.read(engine, parse(text, ['Metric', 'Certified Metric'], engine), fresh(engine), PRESETS['graph'])
    return reply.model_dump(mode='json')


def marked(engine, text):
    return {(p.text, m) for p in ledger(text, 'turn', fresh(engine)) for m in p.markers}


@pytest.mark.parametrize('text', SWEEP)
def test_the_markers_move_no_coded_plan(declared, monkeypatch, text):
    with_markers = coded(declared, text)
    monkeypatch.setattr(coverage, 'lexer_markers', lambda pieces, *a, **kw: pieces)
    assert not marked(declared, text)
    assert coded(declared, text) == with_markers


def test_every_marker_is_set_somewhere_in_the_sweep_and_the_named_ones_are_the_compilers(declared):
    assert {m for text in SWEEP for _, m in marked(declared, text)} == {*MARKERS, ADJACENT_DIRECTION}
    assert set(MARKERS) == set(standards.MARKERS)


@pytest.mark.parametrize('text', SWEEP)
def test_from_to_marks_exactly_the_periods_the_coded_comparison_takes_in_reverse(declared, text):
    reply = coded(declared, text)
    points = [p for p in ledger(text, 'turn', fresh(declared)) if p.kind == 'time']
    first, last = (planshapes.periods(points) + [None, None])[:2]
    swapped = (reply['kind'] == 'propose' and len(points) == 2 and first and last and reply['plan']['time']['kind'] == 'single'
               and reply['plan']['time']['period']['value'] == last.value[:7]
               and any(c['kind'] == 'explicit_period' and c['other']['period']['value'] == first.value[:7]
                       for c in reply['plan']['comparisons']))
    assert ('from_to' in {m for _, m in marked(declared, text)}) is bool(swapped)
    if swapped:
        assert {t for t, m in marked(declared, text) if m == 'from_to'} == {p.text for p in points}


def test_asks_one_cannot_give_the_coded_limit_so_ranking_is_held(declared):
    text = 'which category had the most Metric for Q1 2025'
    assert ('most', 'asks_one') in marked(declared, text)
    assert coded(declared, text)['plan']['limit'] is None
    assert not binding.retirable('planshapes.compose:ranking')
    assert 'asks_one' in binding.HELD['planshapes.compose:ranking']


def test_a_counted_which_is_not_asks_one(declared):
    assert not {m for _, m in marked(declared, 'which categories had Metric for Q1 2025')}


@pytest.mark.parametrize('text, number', [('top 2 categories by Metric for Q1 2025', '2'),
                                          ('bottom 3 sources by Metric for Q1 2025', '3')])
def test_a_number_beside_its_direction_word_is_marked_and_is_the_coded_limit(declared, text, number):
    assert (number, 'adjacent_direction') in marked(declared, text)
    assert coded(declared, text)['plan']['limit'] == int(number)


def test_a_number_beside_the_ranked_breakdown_is_a_coded_limit_no_marker_gives(declared):
    text = 'which 3 categories had the most Metric for Q1 2025'
    assert coded(declared, text)['plan']['limit'] == 3
    assert not {t for t, m in marked(declared, text) if m == ADJACENT_DIRECTION}
    assert 'adjacent_direction' in binding.HELD['planshapes.compose:ranking']


def test_a_number_apart_from_any_direction_word_is_not_marked(declared):
    assert not marked(declared, 'Metric for Q1 2025 top categories above 2')


def test_measure_part_marks_only_predicates_the_pack_flags(declared, monkeypatch):
    assert marked(declared, 'Metric inflows and outflows for Q1 2025') == {('inflows', 'measure_part'), ('outflows', 'measure_part')}
    assert not marked(declared, 'Metric incoming for Q1 2025')
    assert ('unsourced', 'measure_part') in marked(declared, 'Metric unsourced for Q1 2025')
    engine, sem = pack(flag=False)
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    assert not marked(engine, 'Metric inflows and outflows for Q1 2025')
    assert not marked(engine, 'Metric unsourced for Q1 2025')


def test_measure_part_fires_where_the_coded_parts_do_not_so_parts_is_held(declared):
    text = 'Metric inflows for Q1 2025'
    assert ('inflows', 'measure_part') in marked(declared, text)
    assert [m['id'] for m in coded(declared, text)['plan']['measures']] == ['m0']
    assert not binding.retirable('planshapes.parts')


def test_held_names_listed_coded_builders_and_every_other_listed_builder_stays_retirable():
    listed = standards.load().coded_builders
    assert set(binding.HELD) == {'planshapes.compose:ranking', 'planshapes.parts'} and set(binding.HELD) <= set(listed)
    assert binding.HELD is standards.HELD_BUILDERS
    assert [b for b in listed if not binding.retirable(b)] == sorted(binding.HELD, key=listed.index)


def test_a_window_binds_one_range_slot_and_separate_periods_bind_in_span_order(declared):
    v = fresh(declared)
    ranged = binding.slots(ledger('Metric between January 2025 and March 2025', 'turn', v), v)
    assert (ranged.count('range'), ranged.count('period')) == (1, 0)
    assert [p.value[:7] for p in ranged['range'][0].value] == ['2025-01', '2025-03']
    apart = binding.slots(ledger('Metric for March 2025 vs April 2025', 'turn', v), v)
    assert (apart.count('range'), apart.count('period')) == (0, 2)
    assert [s.value.value[:7] for s in apart['period']] == ['2025-03', '2025-04']
    assert apart['period'][0].spans[0].start < apart['period'][1].spans[0].start


def test_slots_carry_the_markers_and_the_ranking_slots(declared):
    v = fresh(declared)
    changed = binding.slots(ledger('Metric change from April 2025 to June 2025', 'turn', v), v)
    assert 'from_to' in changed.markers and changed.count('period') == 2
    ranked = binding.slots(ledger('top 2 categories by Metric for Q1 2025', 'turn', v), v)
    assert [s.value for s in ranked['direction']] == ['desc'] and [s.value for s in ranked['number']] == [2]
    assert ranked.count('dimension') == 1 and not ranked.markers
    assert ranked['number'][0].markers == (ADJACENT_DIRECTION,)


def test_two_contexts_bind_in_span_order_with_or_without_a_comparison_word(declared):
    v = fresh(declared)
    compared = ledger('Compare current with certified Metric for March 2025', 'turn', v)
    assert binding.shared_contexts(compared, v) == ('ctx', 'cert')
    bare = ledger('current and certified Metric for March 2025', 'turn', v)
    assert binding.shared_contexts(bare, v) == ('cert',)
    assert binding.shared_contexts(bare, v, paired=True) == ('ctx', 'cert')
    two = binding.slots(bare, v, two_contexts=True)
    assert [s.value for s in two['context']] == [('ctx',), ('cert',)] and two.count('measure') == 2
    assert two['context'][0].spans == (next(p.span for p in bare if p.operation == 'current'),)
    assert binding.slots(bare, v).count('context') == 1
    ordered = binding.slots(ledger('certified Metric versus current Metric for March 2025', 'turn', v), v, two_contexts=True)
    assert [s.value for s in ordered['context']] == [('cert',), ('ctx',)]


def test_shared_contexts_lives_in_the_binding_module_alone():
    assert planshapes.shared_contexts is binding.shared_contexts
    source = (Path(planshapes.__file__)).read_text()
    assert 'def shared_contexts' not in source and '.markers' not in source
    assert '.markers' not in Path(planrules.__file__).read_text()


@pytest.mark.parametrize('rule, operation, value, flagged', [
    ({'column': 'u.t.c', 'phrases': ['X'], 'op': 'is_null'}, 'null', None, True),
    ({'column': 'u.t.c', 'phrases': ['x'], 'op': 'is_true'}, 'eq', True, True),
    ({'column': 'u.t.c', 'phrases': ['x'], 'op': 'is_true'}, 'eq', 'yes', False),
    ({'column': 'u.t.c', 'phrases': ['x'], 'op': 'in', 'values': ['a', 'b']}, 'in', ('b', 'a'), True),
    ({'column': 'u.t.c', 'phrases': ['x'], 'op': 'in', 'values': ['a', 'b']}, 'in', ('a',), False),
    ({'column': 'u.t.c', 'phrases': ['x'], 'op': 'eq', 'value': 'a'}, 'eq', 'b', False),
    ({'column': 'u.t.d', 'phrases': ['x'], 'op': 'eq', 'value': 'a'}, 'eq', 'a', False),
    ({'column': 'u.t.c', 'phrases': ['y'], 'op': 'eq', 'value': 'a'}, 'eq', 'a', False),
])
def test_a_flagged_part_matches_on_the_lexers_operator_and_the_declared_value(rule, operation, value, flagged):
    piece = Piece(Span(turn='turn', start=0, end=1), 'x', 'predicate', ('u.t.c',), operation, value)
    assert _flagged_part(piece, [rule]) is flagged
