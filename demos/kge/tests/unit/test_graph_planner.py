"""The graph adapter of the rules-planner port (question-shapes.md §3.1 and §4.3, #450 requirement 1, #518), on a
synthetic pack and templates written here: clarification rules run before any template; a template matches on the
ledger's operations, markers, slot counts and closed position constraints alone; every matching template contributes
in the platform's fixed block order; slot binding is the shared one; the reply passes the port as the coded one does.
The adapter is CPU only and serves nothing: the port's default stays the coded adapter. No question set, fixture or
data of the Meridian sample is read."""
import inspect
import threading
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters.graphstore import members as member_graph
from kge.adapters.graphstore.members import MemberTerms
from kge.compiler import standards
from kge.semantics.coverage import ledger
from kge.semantics.plan import Propose
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import graphplanner, planpublic, planrules, rulesport, shadow, shadowinputs
from kge.services.ask.engine.core import Engine, PRESETS
from kge.services.ask.engine.graphplanner import GraphPlanner, Matched, Reading, apply, holds
from kge.services.ask.engine.graphview import TEMPLATES, WRITES, Graph, Templates
from kge.services.ask.engine.question import parse
from kge.services.ask.engine.rulesport import RulesPlanner, RulesQuestion, planned
from kge.services.ask.engine.shadowing import Shadowing
from kge.compiler.checks import rule_condition, shape_condition
from tests.unit.test_slot_binding import declared, fresh

__all__ = ['declared']

CONTEXTS_OR_PERIODS = {'id': 'contexts-or-periods-2', 'message': 'contexts-or-periods',
                       'text': 'Please identify whether to compare the contexts or their periods.',
                       'when': {'requires': ['comparison'], 'slots': {'context': 2, 'period': 2},
                                'constraints': [{'before': ['comparison', 'period']}]}}
ONE_PERIOD = {'id': 'one-comparison-period', 'message': 'one-comparison-period', 'text': 'Please identify one comparison period.',
              'when': {'requires': ['comparison'], 'slots': {'period': 2}, 'constraints': [{'before': ['comparison', 'period']}]}}
PERIOD_COMPARISON = {
    'id': 'period-comparison', 'stage': 'shadow', 'slots': [{'name': 'measure', 'kind': 'measure'}, {'name': 'period', 'kind': 'period', 'count': 2}],
    'requires': ['comparison'], 'forbids': ['from_to'], 'constraints': [],
    'recipe': {'id': 'period-comparison', 'blocks': [
        {'block': 'single', 'id': 't', 'period': '$period[0]'},
        {'block': 'explicit_period', 'id': 'c', 'inputs': ['$measure'], 'other': '$period[1]'},
        {'block': 'difference', 'id': 'x0', 'numerator': 'c/m0/base', 'denominator': 'c/m0/other'},
        {'block': 'percent_change', 'id': 'x1', 'numerator': 'c/m0/base', 'denominator': 'c/m0/other'}]}}


def templates(*shapes, rules=()):
    return Templates({'release': 'r1'}, {s['id']: s for s in shapes}, {r['id']: r for r in rules})


def shape(sid, stage='shadow', recipe=(), **condition):
    return {'id': sid, 'stage': stage, 'slots': condition.pop('slots', []), 'requires': condition.pop('requires', []),
            'forbids': condition.pop('forbids', []), 'constraints': condition.pop('constraints', []),
            'recipe': {'id': sid, 'blocks': list(recipe)}}


def asked(engine, text, history=()):
    return RulesQuestion(engine, parse(text, ['Metric', 'Certified Metric'], engine), text, fresh(engine), PRESETS['graph'],
                         'turn', tuple(history))


def reading(engine, text, paired=False):
    return Reading.of(ledger(text, 'turn', fresh(engine)), fresh(engine), paired=paired)


def dumped(reply):
    return reply.model_dump(mode='json')


def test_the_port_gives_the_coded_adapter_by_default_and_the_graph_one_only_by_name():
    assert rulesport.rules_planner() is planrules.CODED
    assert rulesport.rules_planner('coded') is planrules.CODED
    assert rulesport.rules_planner('graph') is graphplanner.GRAPH
    assert isinstance(graphplanner.GRAPH, RulesPlanner) and graphplanner.GRAPH.name == 'graph'
    with pytest.raises(ValueError, match="No rules planner is named 'model'"):
        rulesport.rules_planner('model')


def test_public_ask_serves_the_default_adapter_and_names_no_graph_adapter():
    """A lint, beside the port's own tests: the caller takes rules_planner() with no name, so no served answer moves."""
    source = inspect.getsource(planpublic)
    assert 'graphplanner' not in source and "rules_planner('graph')" not in source
    assert 'rules_planner()' in source


def test_with_no_templates_a_question_no_shape_reads_gets_the_coded_reading(declared):
    for text in ('Metric for Q1 2025', 'certified Metric for March 2025'):
        graph = planned(GraphPlanner(templates()), asked(declared, text))
        coded = planned(planrules.CODED, asked(declared, text))
        assert graph.kind == 'propose' and shadow.compare(graph, coded) == 'agree'


def test_with_no_templates_a_comparison_is_left_unbuilt_and_differs_from_the_coded_plan(declared):
    text = 'Metric for March 2025 vs April 2025'
    graph = planned(GraphPlanner(templates()), asked(declared, text))
    coded = planned(planrules.CODED, asked(declared, text))
    assert graph.kind == 'propose' and not graph.plan.comparisons and coded.plan.comparisons
    assert shadow.compare(graph, coded) == 'plans_differ'


def test_a_mirror_template_builds_the_coded_period_comparison(declared):
    text = 'Metric for March 2025 vs April 2025'
    reply, used = GraphPlanner(templates(PERIOD_COMPARISON)).read(asked(declared, text))
    assert used == ('period-comparison',)
    coded = planned(planrules.CODED, asked(declared, text))
    assert shadow.compare(reply, coded) == 'agree'
    assert [c.kind for c in reply.plan.comparisons] == ['explicit_period']
    assert reply.plan.comparisons[0].other.period.value == '2025-04'


def test_forbids_keeps_a_template_from_a_question_carrying_the_marker(declared):
    """from_to, set by the lexer where "from X to Y" is a change, is forbidden by the period comparison mirror."""
    text = 'Metric change from April 2025 to June 2025'
    assert 'from_to' in reading(declared, text).names
    assert GraphPlanner(templates(PERIOD_COMPARISON)).read(asked(declared, text))[1] == ()
    swapped = {**PERIOD_COMPARISON, 'id': 'from-to', 'forbids': [], 'requires': ['from_to']}
    assert GraphPlanner(templates(swapped)).read(asked(declared, text))[1] == ('from-to',)


def test_clarification_rules_run_first_and_in_id_order(declared):
    text = 'compare Metric for March 2025 to April 2025'
    planner = GraphPlanner(templates(PERIOD_COMPARISON, rules=(ONE_PERIOD, {**CONTEXTS_OR_PERIODS, 'when': {
        'requires': ['comparison'], 'slots': {'period': 2}}})))
    reply, used = planner.read(asked(declared, text))
    assert dumped(reply) == {'kind': 'abstain', 'spans': [], 'missing': [CONTEXTS_OR_PERIODS['text']]}
    assert used == ()


def test_a_clarification_rule_that_does_not_hold_lets_the_templates_match(declared):
    text = 'Metric for March 2025 vs April 2025'
    reply, used = GraphPlanner(templates(PERIOD_COMPARISON, rules=(CONTEXTS_OR_PERIODS,))).read(asked(declared, text))
    assert reply.kind == 'propose' and used == ('period-comparison',)


@pytest.mark.parametrize('constraint, text, expected', [
    (('before', 'comparison', 'period'), 'compare Metric for March 2025 to April 2025', True),
    (('before', 'period', 'comparison'), 'compare Metric for March 2025 to April 2025', False),
    (('after', 'period', 'comparison'), 'compare Metric for March 2025 to April 2025', True),
    (('after', 'comparison', 'period'), 'compare Metric for March 2025 to April 2025', False),
    (('before', 'comparison', 'direction'), 'compare Metric for March 2025 to April 2025', True),
    (('before', 'direction', 'period'), 'compare Metric for March 2025 to April 2025', False),
    (('adjacent', 'direction', 'number'), 'top 2 categories by Metric for Q1 2025', True),
    (('adjacent', 'direction', 'number'), 'bottom 3 sources by Metric for Q1 2025', True),
    (('adjacent', 'direction', 'number'), 'which 3 categories had the most Metric for Q1 2025', False),
    (('adjacent', 'direction', 'number'), 'Metric for Q1 2025', False),
])
def test_position_constraints_read_positions_alone(declared, constraint, text, expected):
    op, a, b = constraint
    asks = shape_condition(shape('s', constraints=[{op: [a, b]}]))
    assert holds(asks, reading(declared, text)) is expected


def test_slot_counts_tell_a_range_from_two_periods(declared):
    ranged, apart = reading(declared, 'Metric between January 2025 and March 2025'), reading(declared, 'Metric for March 2025 vs April 2025')
    one_range = shape_condition(shape('r', slots=[{'name': 'range', 'kind': 'range'}]))
    two_periods = shape_condition(shape('p', slots=[{'name': 'period', 'kind': 'period', 'count': 2}]))
    assert holds(one_range, ranged) and not holds(two_periods, ranged)
    assert holds(two_periods, apart) and not holds(one_range, apart)
    assert holds(shape_condition(shape('o', slots=[{'name': 'period', 'kind': 'period', 'count': {'min': 0, 'max': 1}}])), ranged)


def test_two_context_slots_are_bound_by_shared_contexts(declared):
    """A bare "current" side takes the other side's noun where the contexts are read in pairs."""
    text = 'Compare current with certified Metric for March 2025'
    two = shape_condition(shape('two', slots=[{'name': 'context', 'kind': 'context', 'count': 2}]))
    assert reading(declared, text).slots.count('context') == 1 and not holds(two, reading(declared, text))
    assert reading(declared, text, True).slots.count('context') == 2 and holds(two, reading(declared, text, True))


def test_one_reading_of_the_contexts_serves_every_condition(declared):
    """The design's comparison-contexts rule (context at most one) and a two-context template read one question's
    contexts alike: where any condition counts two, both read them paired, so the rule does not fire on a question the
    template matches."""
    rule = {'id': 'comparison-contexts', 'message': 'both-comparison-contexts', 'text': 'Please identify both comparison contexts.',
            'when': {'requires': ['comparison'], 'slots': {'context': {'max': 1}}}}
    two = shape('two-contexts', requires=['comparison'], slots=[{'name': 'left', 'kind': 'context'}, {'name': 'right', 'kind': 'context'}],
                recipe=[{'block': 'contexts', 'id': 'c', 'left': '$left', 'right': '$right'}])
    held = templates(two, rules=(rule,))
    assert graphplanner.paired(held) and not graphplanner.paired(templates(rules=(rule,)))
    reply, used = GraphPlanner(held).read(asked(declared, 'Compare current with certified Metric for March 2025'))
    assert used == ('two-contexts',) and reply.kind == 'propose'


def test_two_named_slots_of_one_kind_bind_two_slots_in_span_order(declared):
    two = shape('two-contexts', requires=['comparison'], slots=[{'name': 'left', 'kind': 'context'}, {'name': 'right', 'kind': 'context'}],
                recipe=[{'block': 'contexts', 'id': 'c', 'left': '$left', 'right': '$right'}])
    reply = planned(GraphPlanner(templates(two)), asked(declared, 'certified Metric versus current Metric for March 2025'))
    contexts = {m.id: m.context.id for m in reply.plan.measures}
    comparison, = reply.plan.comparisons
    assert (contexts[comparison.left], contexts[comparison.right]) == ('cert', 'ctx')
    coded = planned(planrules.CODED, asked(declared, 'certified Metric versus current Metric for March 2025'))
    assert {m.id: m.context.id for m in coded.plan.measures}[coded.plan.comparisons[0].left] == 'cert'


def test_a_slot_after_a_ranged_one_of_its_kind_has_no_fixed_place(declared):
    ranged = shape('ranged-first', requires=['comparison'], slots=[{'name': 'some', 'kind': 'context', 'count': {'min': 1, 'max': 2}},
                                                                 {'name': 'last', 'kind': 'context', 'count': {'min': 0, 'max': 1}}],
                   recipe=[{'block': 'contexts', 'id': 'c', 'left': '$some', 'right': '$last'}])
    reply = planned(GraphPlanner(templates(ranged)), asked(declared, 'certified Metric versus current Metric for March 2025'))
    assert reply.kind == 'abstain' and 'its place is not fixed' in reply.missing[0]


def test_an_unindexed_measure_in_a_list_takes_every_measure_and_measures_bind_through_their_contexts(declared):
    """Two contexts of one measure: the measure slots name the two outputs, as the coded comparison's inputs do."""
    mirror = {**PERIOD_COMPARISON, 'slots': [{'name': 'measure', 'kind': 'measure', 'count': {'min': 1, 'max': 2}},
                                             {'name': 'period', 'kind': 'period', 'count': 2}],
              'recipe': {'id': 'p', 'blocks': PERIOD_COMPARISON['recipe']['blocks'][:2]}}
    text = 'certified Metric and Metric for March 2025 vs April 2025'
    reply = planned(GraphPlanner(templates(mirror)), asked(declared, text))
    coded = planned(planrules.CODED, asked(declared, text))
    assert reply.kind == 'propose' and len(reply.plan.measures) == 2
    assert reply.plan.comparisons[0].inputs == coded.plan.comparisons[0].inputs == tuple(m.id for m in coded.plan.measures)


def test_requires_forbids_and_one_of_read_the_operations_and_markers(declared):
    read = reading(declared, 'Metric for March 2025 vs the previous year')
    assert holds(shape_condition(shape('a', requires=['comparison', {'one_of': ['previous_period', 'previous_year']}])), read)
    assert not holds(shape_condition(shape('b', requires=[{'one_of': ['previous_period', 'ratio']}])), read)
    assert not holds(shape_condition(shape('c', forbids=['previous_year'])), read)
    assert holds(rule_condition({'when': {'requires': ['previous_year'], 'forbids': ['ratio']}}), read)


def test_a_specify_template_runs_at_build_alone(declared):
    text = 'Metric for March 2025 vs April 2025'
    held = {**PERIOD_COMPARISON, 'stage': 'specify'}
    assert GraphPlanner(templates(held)).read(asked(declared, text))[1] == ()
    built, used = GraphPlanner(templates(held), build=True).read(asked(declared, text))
    assert used == ('period-comparison',) and built.plan.comparisons


def test_every_matching_template_contributes_in_the_fixed_block_order():
    data = {'measures': [{'id': 'm0'}], 'dimensions': [], 'filters': [], 'comparisons': [], 'derived': [], 'order': [],
            'time': {'kind': 'all_available'}, 'limit': None}
    order = {'output': 'm0', 'direction': 'desc', 'nulls': 'last', 'evidence': []}
    contributed = [('a-ranking', {'order': [order], 'limit': [3]}),
                   ('b-change', {'derived': [{'kind': 'difference', 'id': 'x0'}], 'comparisons': [{'kind': 'previous_year', 'id': 'c'}]}),
                   ('c-grain', {'dimensions': [{'kind': 'time', 'id': 'd0'}], 'time': [{'kind': 'single'}]})]
    seen = []
    for section in graphplanner.COMPOSED:
        before = {k: len(v) if isinstance(v, list) else v for k, v in data.items()}
        apply(data, contributed, (section,))
        seen += [k for k, v in data.items() if (len(v) if isinstance(v, list) else v) != before[k]]
    assert seen == ['dimensions', 'time', 'comparisons', 'derived', 'order', 'limit']
    assert data['limit'] == 3 and data['time'] == {'kind': 'single'} and data['order'] == [order]
    apply(data, [('d-parts', {'measures': [{'id': 'part0'}], 'filters': [{'id': 'pf0'}]})], graphplanner.PARTS)
    assert [m['id'] for m in data['measures']] == ['m0', 'part0'] and data['filters'] == [{'id': 'pf0'}]


def test_two_templates_that_cannot_make_one_plan_decline():
    data = {'measures': [{'id': 'm0'}], 'dimensions': [], 'filters': [], 'comparisons': [], 'derived': [], 'order': [],
            'time': {'kind': 'all_available'}, 'limit': None}
    with pytest.raises(graphplanner.Unbound, match="time differently"):
        apply(dict(data), [('a', {'time': [{'kind': 'single'}]}), ('b', {'time': [{'kind': 'range'}]})], ('time',))
    with pytest.raises(graphplanner.Unbound, match='share the id m0'):
        apply(dict(data, measures=[{'id': 'm0'}]), [('a', {'measures': [{'id': 'm0'}]})], ('measures',))


def test_measure_parts_are_applied_at_the_parts_step(declared):
    """A recipe's measures and filters are its measure parts: applied after binding's filters, last."""
    part = shape('inflow-part', requires=['measure_part'], slots=[{'name': 'measure', 'kind': 'measure'},
                                                                 {'name': 'context', 'kind': 'context'}], recipe=[
        {'block': 'compare', 'id': 'pf0', 'left': {'kind': 'column', 'name': 'u.events.direction', 'path': []}, 'op': 'eq',
         'value': {'kind': 'text', 'value': 'inflows'}},
        {'block': 'measure', 'id': 'part0', 'measure': '$measure', 'context': '$context', 'mode': 'current', 'filters': ['pf0']}])
    reply, used = GraphPlanner(templates(part)).read(asked(declared, 'Metric inflows for Q1 2025'))
    assert used == ('inflow-part',) and reply.kind == 'propose'
    assert [m.id for m in reply.plan.measures][-1] == 'part0'
    assert reply.plan.measures[-1].filters[0].id == 'pf0'


def test_an_unordered_range_is_the_platforms_clarification(declared):
    ranged = shape('ranged', slots=[{'name': 'measure', 'kind': 'measure'}, {'name': 'range', 'kind': 'range'}], recipe=[
        {'block': 'explicit_period', 'id': 'c', 'inputs': ['$measure'], 'other': '$range'}])
    reply = planned(GraphPlanner(templates(ranged)), asked(declared, 'Metric between March 2025 and January 2025'))
    assert dumped(reply) == {'kind': 'abstain', 'spans': [], 'missing': ['Please give an ordered range at one period grain.']}


def test_member_groups_inherit_only_their_narrower_codes():
    category, division = 'u.events.category', 'u.events.division'
    graph = {
        member_graph.CODED: [{'term': 'Crown', 'col': category, 'code': 'CR'},
                            {'term': 'Harbor', 'col': category, 'code': 'HB'},
                            {'term': 'Parent', 'col': division, 'code': 'P'}],
        member_graph.BROADER: [{'member': 'Crown', 'parent': 'Border group'},
                               {'member': 'Harbor', 'parent': 'Border group'},
                               {'member': 'Border group', 'parent': 'Parent'}],
        member_graph.CLASSES: [{'member': 'Crown', 'parent': 'Territory'},
                               {'member': 'Harbor', 'parent': 'Territory'},
                               {'member': 'Uncoded', 'parent': 'Territory'},
                               {'member': 'Border group', 'parent': 'Territory Group'},
                               {'member': 'Parent', 'parent': 'Division'}],
        member_graph.ALIASES: [{'name': 'Border group', 'phrase': 'border'}],
    }
    members = member_graph.members(graph.__getitem__)
    assert [row[1:] for row in members.codes if row[0] == 'Border group'] == [(category, 'CR'), (category, 'HB')]
    assert [row[1:] for row in members.codes if row[0] == 'Parent'] == [(division, 'P')]
    assert members.columns('Division') == (division,) and members.columns('Territory Group') == (category,)
    assert 'Uncoded' in members.names and not members.columns('Uncoded')
    assert members.aliases == (('border', 'Border group'),)


def test_a_structured_term_without_coded_neighbours_is_still_governed():
    graph = {member_graph.CODED: [], member_graph.BROADER: [],
             member_graph.CLASSES: [{'member': 'Later', 'parent': 'Line'}], member_graph.ALIASES: []}
    members = member_graph.members(graph.__getitem__)
    assert members.names == {'Later', 'Line'}
    assert not members.codes and not members.attached


def observed_members(declared, text, codes, aliases, ordinary=()):
    from tests.unit.test_currency_candidates import coordinator

    runtime = declared.semantics
    runtime.d.VOCABULARY += [{'kind': 'predicate', 'params': {'column': column, 'phrases': [phrase],
                              'op': 'eq', 'value': value}} for column, phrase, value in ordinary]
    filters = compiled(runtime.d.VOCABULARY)
    declared.FILTERS, declared.FILTER_COLUMNS = filters.filters, filters.columns
    runtime.d.FILTERS, runtime.d.FILTER_COLUMNS = filters.filters, filters.columns
    held = templates()
    runtime.plan_snapshot, runtime.plan_writes, runtime.mapping_declarations = runtime, (0, ()), []
    declared.identity, declared.term_names = held.release, ['Metric', 'Certified Metric']
    declared.g = SimpleNamespace(writes=0, identity=held.release, _shadow_templates=held,
                                 _member_terms=MemberTerms(held.release, frozenset(name for name, _, _ in codes), codes, aliases))
    declared.shadow_write_guard = nullcontext
    question = asked(declared, text)
    baseline = planned(planrules.CODED, question)
    inputs = shadowinputs.ShadowInputs(declared, templates=held, graph_mode=True, runtime=runtime, columns={})
    inputs.prepare_members(question.request, question.vocabulary)
    graph = planned(GraphPlanner(held), replace(question, engine=inputs))
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(declared, question.request, question.cfg, question.turn, (), False,
                                   coordinator(question.vocabulary), question.vocabulary, baseline, 'coded',
                                   shadow_runtime=runtime)
    return graph, captured[0]


@pytest.mark.parametrize('ordinary, expected_column, expected_values, via_terms', [
    (('u.events.category', 'harbor territory', 'HB'), 'u.events.category', ('CR', 'HB'), True),
    (('u.events.source', 'harbor territory', 'Harbor'), 'u.events.source', ('Crown', 'Harbor'), False),
    (None, 'u.events.category', ('CR', 'HB'), True),
])
@pytest.mark.parametrize('said', ['crown and harbor territories', 'crown territory and harbor territory'])
def test_member_lists_keep_alternatives_with_shared_suffix(declared, ordinary, expected_column, expected_values, via_terms, said):
    category, source = 'u.events.category', 'u.events.source'
    codes = (('Crown', category, 'CR'),)
    aliases = (('crown territory', 'Crown'),)
    rows = ((source, 'crown territory', 'Crown'), ordinary) if ordinary else ()
    if not ordinary:
        codes += (('Harbor', category, 'HB'),)
        aliases += (('harbor territory', 'Harbor'),)
    graph, observation = observed_members(declared, f'Metric for March 2025 in {said}', codes, aliases, rows)
    assert observation.graph_outcome == 'propose', graph
    assert observation.via_terms is via_terms
    predicate, = graph.plan.filters
    assert predicate.kind == 'in' and predicate.left.name.id == expected_column
    assert tuple(value.value for value in predicate.values) == expected_values


@pytest.mark.parametrize('reachable', [False, True])
def test_member_code_is_selected_after_filter_path_resolution(declared, reachable):
    declared.semantics.tables['u.archive'] = {'carrier': 'VARCHAR'}
    category = 'u.events.category'
    other = 'u.events.source' if reachable else 'u.archive.carrier'
    graph, observation = observed_members(declared, 'Metric for March 2025 in crown territory',
                                          (('Crown', other, 'ZZ'), ('Crown', category, 'CR')), (('crown territory', 'Crown'),))
    if reachable:
        assert observation.graph_outcome == 'abstain' and not observation.via_terms
        assert 'unambiguous role' in repr(graph.missing)
    else:
        assert observation.graph_outcome == 'propose' and observation.via_terms
        predicate, = graph.plan.filters
        assert predicate.kind == 'compare' and predicate.left.name.id == category and predicate.value.value == 'CR'


def test_shadow_member_code_takes_precedence_over_a_legacy_name_filter(declared):
    from tests.unit.test_currency_candidates import coordinator

    name, label = 'Crown (territory)', 'crown territory'
    code, caption = 'u.events.category', 'u.events.source'
    declared.semantics.d.VOCABULARY += [
        {'kind': 'predicate', 'params': {'column': caption, 'phrases': [label], 'op': 'eq', 'value': 'Crown'}},
        {'kind': 'code_name_pair', 'params': {'code': code, 'name': caption, 'members': [{'code': 'CR', 'name': 'Crown'}]}},
    ]
    filters = compiled(declared.semantics.d.VOCABULARY)
    declared.FILTERS, declared.FILTER_COLUMNS = filters.filters, filters.columns
    declared.semantics.d.FILTERS, declared.semantics.d.FILTER_COLUMNS = filters.filters, filters.columns
    held = templates()
    runtime = declared.semantics
    runtime.plan_snapshot, runtime.plan_writes, runtime.mapping_declarations = runtime, (0, ()), []
    declared.identity, declared.term_names = held.release, ['Metric', 'Certified Metric']
    declared.g = SimpleNamespace(writes=0, identity=held.release, _shadow_templates=held,
                                 _member_terms=MemberTerms(held.release, frozenset({name}), ((name, code, 'CR'),),
                                                           ((label, name),), ((name, code),)))
    declared.shadow_write_guard = nullcontext
    question = asked(declared, f'Metric for March 2025 in {label}')
    baseline = planned(planrules.CODED, question)
    assert baseline.kind == 'propose'
    assert any(node.left.name.id == caption for node in baseline.plan.nodes() if node.kind == 'compare')

    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(declared, question.request, question.cfg, question.turn, (), False,
                                   coordinator(question.vocabulary), question.vocabulary, baseline, 'coded',
                                   shadow_runtime=runtime)
    observation, = captured
    assert observation.via_terms and observation.graph_outcome == 'propose'
    graph_plan = observation.evidence['graph']['plan']
    assert code in repr(graph_plan) and caption not in repr(graph_plan)
    assert baseline == planned(planrules.CODED, question)

    for text in (label, f'{label} Metric for March 2025'):
        bare = asked(declared, text)
        bare = replace(bare, vocabulary=replace(bare.vocabulary, phrases={**bare.vocabulary.phrases,
                                                                        f'{label} metric': ('ctx',)}))
        coded = planned(planrules.CODED, bare)
        with planpublic.capture_shadow() as captured:
            planpublic._shadow_compare(declared, bare.request, bare.cfg, bare.turn, (), False,
                                       coordinator(bare.vocabulary), bare.vocabulary, coded, 'coded', shadow_runtime=runtime)
        seen, = captured
        assert seen.via_terms is False


@pytest.mark.parametrize('message_id,text', [('one-comparison-period', 'Metric for March 2025 vs April 2025'),
                                            ('ordered-range', 'Metric between March 2025 and January 2025')])
def test_shadow_uses_prepared_platform_text_without_preload_io(declared, monkeypatch, message_id, text):
    if message_id == 'one-comparison-period':
        rule = {**ONE_PERIOD, 'text': None, 'when': {'requires': ['comparison']}}
        held = templates(PERIOD_COMPARISON, rules=(rule,))
    else:
        ranged = shape('ranged', slots=[{'name': 'measure', 'kind': 'measure'}, {'name': 'range', 'kind': 'range'}], recipe=[
            {'block': 'explicit_period', 'id': 'c', 'inputs': ['$measure'], 'other': '$range'}])
        held = templates(ranged)
    question = asked(declared, text)
    coded = planned(planrules.CODED, question)
    runtime = declared.semantics
    runtime.plan_snapshot, runtime.plan_writes, runtime.mapping_declarations = runtime, (0, ()), []
    declared.identity, declared.term_names = held.release, ['Metric', 'Certified Metric']
    declared.g = SimpleNamespace(writes=0, identity=held.release, _shadow_templates=held, prepare_shadow_templates=lambda: None)
    monkeypatch.setattr(shadowinputs, 'prepare_baseline', lambda _: runtime)
    assert Engine._warm_shadow_inputs(declared)
    expected = standards.load().clarification_texts[message_id]

    def forbid_preload(operation):
        def guarded(path, *args, **kwargs):
            assert path != standards.PRELOAD, 'a shadow comparison opened the platform preload'
            return operation(path, *args, **kwargs)
        return guarded

    with monkeypatch.context() as blocked:
        blocked.setattr(Path, 'stat', forbid_preload(Path.stat))
        blocked.setattr(Path, 'read_text', forbid_preload(Path.read_text))
        facade = shadowinputs.ShadowInputs(declared, templates=held, graph_mode=True, runtime=runtime, columns={})
        reply = Shadowing().read(replace(question, engine=facade), coded=coded, check=lambda reply: reply).graph
    assert dumped(reply) == {'kind': 'abstain', 'spans': [], 'missing': [expected]}


def test_a_slot_the_question_does_not_bind_declines_with_the_reason(declared):
    entity = shape('per-entity', slots=[{'name': 'entity', 'kind': 'entity', 'count': {'min': 0, 'max': 1}}], recipe=[
        {'block': 'measure', 'id': 'e', 'measure': '$entity', 'context': 'ctx', 'mode': 'current', 'filters': []}])
    reply = planned(GraphPlanner(templates(entity)), asked(declared, 'Metric for Q1 2025'))
    assert reply.kind == 'abstain' and reply.missing == ('The question binds no entity slot 0 for a question shape.',)


def test_nodes_that_make_no_valid_plan_decline_rather_than_raise(declared):
    broken = shape('broken', requires=['comparison'], recipe=[
        {'block': 'contexts', 'id': 'c', 'left': 'nowhere', 'right': 'm0'}])
    reply = planned(GraphPlanner(templates(broken)), asked(declared, 'Metric for March 2025 vs April 2025'))
    assert dumped(reply) == {'kind': 'abstain', 'spans': [], 'missing': ['The matched question shapes do not make a valid plan.']}


def test_the_graph_adapter_reads_the_graphs_phrase_table_and_the_shared_binding(declared, monkeypatch):
    seen = []
    real = planrules.ledger
    monkeypatch.setattr(planrules, 'ledger', lambda *a, **kw: seen.append(kw.get('baseline')) or real(*a, **kw))
    planned(GraphPlanner(templates()), asked(declared, 'Metric for Q1 2025'))
    assert seen and set(seen) == {False}
    seen.clear()
    planned(planrules.CODED, asked(declared, 'Metric for Q1 2025'))
    assert set(seen) == {True}
    assert Matched.baseline is False and planrules.CODED_SHAPES.baseline is True


def test_a_follow_up_is_read_with_the_graph_shape_steps(declared, monkeypatch):
    seen, earlier = [], planned(planrules.CODED, asked(declared, 'Metric for Q1 2025')).plan
    monkeypatch.setattr(planrules, 'followup', lambda *args, **kw: seen.append(kw['shapes']) or Propose(
        kind='propose', base_turn=None, plan=earlier))
    monkeypatch.setattr(planrules, 'read', lambda *a, **kw: pytest.fail('read after a follow-up'))
    assert planned(GraphPlanner(templates()), asked(declared, 'Metric for Q2 2025')).kind == 'propose'
    assert len(seen) == 1 and isinstance(seen[0], Matched) and seen[0].baseline is False


class Refused:
    """A backend the adapter must never reach: any attribute fails the test."""

    def __init__(self, what):
        self.what = what

    def __getattr__(self, name):
        pytest.fail(f'the graph adapter reached the {self.what} ({name})')


class Store:
    """A graph whose query is counted: what the per-release templates read sends, and nothing else."""

    def __init__(self, held):
        self.held, self.calls = held, []

    def templates(self):
        self.calls.append('templates')
        return self.held

    def q(self, *args, **kw):
        pytest.fail('the graph adapter queried the graph store')


def test_the_graph_adapter_makes_no_model_call_no_warehouse_job_and_no_write(declared):
    store = Store(templates(PERIOD_COMPARISON, rules=(CONTEXTS_OR_PERIODS,)))
    declared.g, declared.warehouses, declared.model, declared.retrieval = store, Refused('warehouse'), Refused('model'), Refused('retrieval')
    for text in ('Metric for March 2025 vs April 2025', 'compare Metric for March 2025 to April 2025', 'Metric for Q1 2025',
                 'certified Metric versus current Metric for March 2025', 'top 2 categories by Metric for Q1 2025'):
        assert planned(graphplanner.GRAPH, asked(declared, text)).kind in ('propose', 'clarify', 'abstain')
    assert store.calls == ['templates'] * 5


def graph(q) -> Graph:
    held = Graph.__new__(Graph)
    held.identity, held._snapshot_lock = {'release': 'r1'}, threading.RLock()
    held.q = q
    return held


SHAPE_ROW = {'id': 'period-comparison', 'stage': 'shadow', 'slots': '[{"kind":"period","name":"period","count":2}]',
             'requires': '["comparison"]', 'forbids': '["from_to"]', 'constraints': '[]', 'replaces': None,
             'coupled_group': 'period-comparison', 'recipe': 'period-comparison',
             'blocks': '[{"block":"single","id":"t","period":"$period[0]"}]'}
RULE_ROW = {'id': 'one-comparison-period', 'condition': '{"requires":["previous_period","previous_year"]}',
            'message': 'one-comparison-period', 'text': 'Please identify one comparison period.'}


def test_the_templates_are_read_once_per_release_as_the_pack_wrote_them():
    sent = []

    def q(cypher, params=None):
        sent.append(cypher)
        return [SHAPE_ROW] if 'Shape' in cypher else [RULE_ROW]
    g = graph(q)
    first = g.templates()
    assert g.templates() is first and len(sent) == 2
    assert first.shapes['period-comparison'] == {
        'id': 'period-comparison', 'stage': 'shadow', 'slots': [{'kind': 'period', 'name': 'period', 'count': 2}],
        'requires': ['comparison'], 'forbids': ['from_to'], 'constraints': [], 'replaces': None,
        'coupled_group': 'period-comparison',
        'recipe': {'id': 'period-comparison', 'blocks': [{'block': 'single', 'id': 't', 'period': '$period[0]'}]}}
    assert first.clarifications == {'one-comparison-period': {
        'id': 'one-comparison-period', 'when': {'requires': ['previous_period', 'previous_year']},
        'message': 'one-comparison-period', 'text': 'Please identify one comparison period.'}}
    g.identity = {'release': 'r2'}
    assert g.templates() is not first and len(sent) == 4


def test_a_graph_without_the_shape_types_reads_no_templates(monkeypatch):
    from kge.services.ask.engine import graphview

    def q(cypher, params=None):
        raise RuntimeError('missing')
    monkeypatch.setattr(graphview, 'missing_type', lambda exc: str(exc) == 'missing')
    held = graph(q).templates()
    assert held.shapes == {} and held.clarifications == {}

    def broken(cypher, params=None):
        raise RuntimeError('the store is down')
    with pytest.raises(RuntimeError, match='down'):
        graph(broken).templates()


def test_the_template_reads_are_reads_and_a_shape_write_drops_them():
    assert not any(WRITES.search(c) for c in TEMPLATES.values())
    assert graphview_drops('MATCH (s:Shape) SET s.stage = $s')
    assert graphview_drops('MERGE (r:ClarificationRule {id: $i})') and not graphview_drops('MATCH (t:Term) SET t.x = 1')


def graphview_drops(cypher: str) -> bool:
    from kge.services.ask.engine import graphview
    return bool(graphview.SHAPES.search(cypher))


def test_the_template_reads_translate_to_bigquery_projections():
    from kge.adapters.graphstore.gql import Schema, Translate
    from kge.graph.schema import meta_model
    from tests.unit.test_graphstore_bigquery import NS
    tr = Translate(Schema.from_meta(meta_model(), 128), NS)
    shapes = tr(TEMPLATES['shapes'])
    assert shapes.kind == 'rows' and '-[:`USES`]->' in shapes.sql
    assert [c.name for c in shapes.columns] == ['id', 'stage', 'slots', 'requires', 'forbids', 'constraints', 'replaces',
                                                'coupled_group', 'recipe', 'blocks']
    rules = tr(TEMPLATES['clarifications'])
    assert rules.kind == 'rows' and [c.name for c in rules.columns] == ['id', 'condition', 'message', 'text']


def test_the_matched_shapes_are_kept_per_question_not_on_the_shared_adapter(declared):
    """Two questions read one shared adapter: each gets its own matched shapes, so concurrent requests cannot mix them."""
    planner = GraphPlanner(templates(PERIOD_COMPARISON))
    first = planner.read(asked(declared, 'Metric for March 2025 vs April 2025'))
    second = planner.read(asked(declared, 'Metric for Q1 2025'))
    assert first[1] == ('period-comparison',) and second[1] == ()
    assert not any(isinstance(v, Matched) for v in vars(planner).values())


def test_a_write_through_the_graph_naming_a_shape_type_drops_the_held_templates():
    g = graph(lambda cypher, params=None: [SHAPE_ROW] if 'Shape' in cypher else [RULE_ROW])
    first = g.templates()
    g.writes, g.conn = 0, SimpleNamespace(execute=lambda cypher, params: SimpleNamespace(get_as_df=lambda: __import__('pandas').DataFrame()))
    Graph.q(g, 'MATCH (t:Term) SET t.x = 1')
    assert g._templates is first
    Graph.q(g, 'MATCH (s:Shape) SET s.stage = $s', {'s': 'retired'})
    assert g._templates is None


SHAPE_ORDER = [
    shape('a-ranking', requires=['comparison'], slots=[{'name': 'measure', 'kind': 'measure'}],
          recipe=[{'block': 'order', 'id': 'o', 'output': 'c/m0/base', 'direction': 'desc', 'nulls': 'last'}]),
    shape('b-derived', requires=['comparison'], recipe=[{'block': 'difference', 'id': 'x0', 'numerator': 'c/m0/base', 'denominator': 'c/m0/other'}]),
    shape('c-comparison', requires=['comparison'], slots=[{'name': 'measure', 'kind': 'measure'}, {'name': 'period', 'kind': 'period', 'count': 2}],
          recipe=[{'block': 'explicit_period', 'id': 'c', 'inputs': ['$measure'], 'other': '$period[1]'}]),
    shape('d-time', requires=['comparison'], slots=[{'name': 'period', 'kind': 'period', 'count': 2}],
          recipe=[{'block': 'single', 'id': 't', 'period': '$period[0]'}]),
]


def test_the_adapter_applies_every_template_in_the_fixed_block_order_whatever_their_ids(declared, monkeypatch):
    """Shape ids sort against block order (a-ranking first, d-time last): the adapter applies time, comparisons,
    derived values, then order, so each later node can name an earlier one and the plan is valid."""
    calls = []
    real = graphplanner.apply
    monkeypatch.setattr(graphplanner, 'apply', lambda data, contributed, sections: calls.append(
        (tuple(sid for sid, _ in contributed), sections)) or real(data, contributed, sections))
    reply, used = GraphPlanner(templates(*SHAPE_ORDER)).read(asked(declared, 'Metric for March 2025 vs April 2025'))
    assert used == ('a-ranking', 'b-derived', 'c-comparison', 'd-time')
    assert reply.kind == 'propose' and reply.plan.order and reply.plan.derived and reply.plan.comparisons
    assert calls == [(used, graphplanner.COMPOSED), (used, graphplanner.PARTS)]
    assert graphplanner.COMPOSED == ('dimensions', 'time', 'comparisons', 'derived', 'order', 'limit')


def test_apply_puts_each_section_before_the_next_across_shapes():
    data = {'measures': [{'id': 'm0'}], 'dimensions': [], 'filters': [], 'comparisons': [], 'derived': [], 'order': [],
            'time': {'kind': 'all_available'}, 'limit': None}
    seen = []
    contributed = [('a', {'derived': [{'id': 'x0'}]}), ('b', {'comparisons': [{'id': 'c'}]}), ('c', {'dimensions': [{'id': 'd9'}]})]
    for section in ('dimensions', 'comparisons', 'derived'):
        data[section] = type('Logged', (list,), {'append': lambda self, v, s=section: seen.append((s, v['id'])) or list.append(self, v)})()
    apply(data, contributed, graphplanner.COMPOSED)
    assert seen == [('dimensions', 'd9'), ('comparisons', 'c'), ('derived', 'x0')]


def test_measure_parts_wait_for_the_parts_step(declared, monkeypatch):
    part = shape('part', requires=['measure_part'], slots=[{'name': 'measure', 'kind': 'measure'}, {'name': 'context', 'kind': 'context'}],
                 recipe=[{'block': 'measure', 'id': 'part0', 'measure': '$measure', 'context': '$context', 'mode': 'current', 'filters': []}])
    after_compose = []
    real = Matched.compose

    def compose(self, data, req, pieces, vocab):
        problem = real(self, data, req, pieces, vocab)
        after_compose.append([m['id'] for m in data['measures']])
        return problem
    monkeypatch.setattr(Matched, 'compose', compose)
    reply = planned(GraphPlanner(templates(part)), asked(declared, 'Metric inflows for Q1 2025'))
    assert 'part0' not in after_compose[0] and reply.plan.measures[-1].id == 'part0'


def test_a_question_with_an_unknown_word_is_not_shaped_as_the_coded_compose_does_not(declared):
    text = 'Metric zzzq for March 2025 vs April 2025'
    reply, used = GraphPlanner(templates(PERIOD_COMPARISON, rules=(CONTEXTS_OR_PERIODS,))).read(asked(declared, text))
    coded = planned(planrules.CODED, asked(declared, text))
    assert used == () and shadow.compare(reply, coded) == 'agree'


@pytest.mark.parametrize('condition, expected', [
    ({'requires': ['comparison']}, True),
    ({'requires': ['ratio']}, False),
    ({'requires': ['comparison', 'ratio']}, False),
    ({'slots': [{'name': 'period', 'kind': 'period', 'count': {'min': 2, 'max': 2}}]}, True),
    ({'slots': [{'name': 'period', 'kind': 'period', 'count': {'min': 0, 'max': 1}}]}, False),
    ({'slots': [{'name': 'period', 'kind': 'period', 'count': {'min': 3}}]}, False),
])
def test_plain_requires_and_slot_count_bounds_are_each_read(declared, condition, expected):
    assert holds(shape_condition(shape('s', **condition)), reading(declared, 'Metric for March 2025 vs April 2025')) is expected


@pytest.mark.parametrize('constraint, text, expected', [
    (('before', 'period', 'comparison'), 'Metric for March 2025 vs April 2025', True),
    (('after', 'comparison', 'period'), 'Metric for March 2025 vs April 2025', True),
    (('adjacent', 'period', 'comparison'), 'Metric for March 2025 vs April 2025', True),
    (('adjacent', 'measure', 'comparison'), 'Metric for March 2025 vs April 2025', False),
])
def test_before_reads_first_pieces_and_adjacent_needs_no_piece_between(declared, constraint, text, expected):
    """March 2025 ends before vs and vs starts after it: before and after read the first period, not April, which comes
    after vs. Metric is never next to vs."""
    op, a, b = constraint
    assert holds(shape_condition(shape('s', constraints=[{op: [a, b]}])), reading(declared, text)) is expected


def test_matched_shapes_come_in_id_order(declared):
    shapes = [shape(sid, requires=['comparison']) for sid in ('zeta', 'alpha', 'mid')]
    assert graphplanner.matched(templates(*shapes), reading(declared, 'Metric for March 2025 vs April 2025')) == ('alpha', 'mid', 'zeta')


@pytest.mark.parametrize('kind, value, expected', [
    ('scalar', 3, {'kind': 'integer', 'value': 3}),
    ('scalar', '2.5', {'kind': 'decimal', 'value': '2.5'}),
    ('positive', 4, 4),
    ('positive', 0, None),
    ('positive', '1.5', None),
    ('int', -2, -2),
])
def test_a_number_fills_its_parameter_by_type(kind, value, expected):
    from kge.semantics.blocks import Param
    bound = graphplanner.Bound(shape('n'), SimpleNamespace(slots=None), {}, None, 'cal')
    if expected is None:
        with pytest.raises(graphplanner.Unbound):
            bound.number(Param('value', kind, True), value)
    else:
        assert bound.number(Param('value', kind, True), value) == expected


def test_a_range_across_grains_is_the_platforms_clarification(declared):
    ranged = shape('ranged', slots=[{'name': 'measure', 'kind': 'measure'}, {'name': 'range', 'kind': 'range'}], recipe=[
        {'block': 'explicit_period', 'id': 'c', 'inputs': ['$measure'], 'other': '$range'}])
    bound = graphplanner.Bound(ranged, SimpleNamespace(slots={'range': (SimpleNamespace(value=(
        SimpleNamespace(plan_kind='month', value='2025-01-01'), SimpleNamespace(plan_kind='year', value='2026'))),)}), {}, None, 'cal')
    from kge.semantics.blocks import CATALOGUE
    with pytest.raises(graphplanner.Declined, match='ordered range'):
        bound.value(CATALOGUE['explicit_period'].params['other'], 'range', 0)


def test_a_slot_binding_two_declarations_or_two_outputs_is_refused():
    bound = graphplanner.Bound(shape('s'), SimpleNamespace(slots={'context': (SimpleNamespace(value=('a', 'b')),)}),
                               {'measures': [{'id': 'm0', 'context': {'id': 'a'}}, {'id': 'm1', 'context': {'id': 'b'}}]}, None, 'cal')
    with pytest.raises(graphplanner.Unbound, match='binds 2 declarations'):
        bound.one('context', SimpleNamespace(value=('a', 'b')))
    with pytest.raises(graphplanner.Unbound, match='names 2 plan nodes'):
        bound.output('context', SimpleNamespace(value=('a', 'b')), 0)


def test_the_adapter_reads_on_the_vocabulary_a_clarification_narrows_with_the_cohort(declared, monkeypatch):
    narrowed, calls = fresh(declared), []
    earlier = planned(planrules.CODED, asked(declared, 'Metric for Q1 2025')).plan
    monkeypatch.setattr(graphplanner, 'clarified_vocabulary', lambda history, vocab, text: calls.append(text) or narrowed)
    monkeypatch.setattr(planrules, 'read', lambda engine, req, vocab, cfg, turn, cohort=False, shapes=None: calls.append((vocab, cohort)) or
                        Propose(kind='propose', base_turn=None, plan=earlier))
    monkeypatch.setattr(planrules, 'followup', lambda *a, **kw: None)
    question = RulesQuestion(declared, parse('Metric', ['Metric'], declared), 'the words asked', fresh(declared), PRESETS['graph'], 'turn', (), cohort=True)
    planned(GraphPlanner(templates()), question)
    assert calls[0] == 'the words asked' and calls[1] == (narrowed, True)


def test_malformed_graph_rows_decline_instead_of_raising_or_passing(declared):
    no_id = shape('no-id', requires=['comparison'], recipe=[{'block': 'single', 'period': '$period[0]'}])
    reply = planned(GraphPlanner(templates(no_id)), asked(declared, 'Metric for March 2025 vs April 2025'))
    assert reply.kind == 'abstain' and reply.missing == ("A block of a question shape's recipe has no id.",)
    silent = {**ONE_PERIOD, 'text': None, 'when': {'requires': ['comparison']}}
    reply, used = GraphPlanner(templates(PERIOD_COMPARISON, rules=(silent,))).read(asked(declared, 'Metric for March 2025 vs April 2025'))
    assert dumped(reply) == {'kind': 'abstain', 'spans': [], 'missing': ['Please identify one comparison period.']} and used == ()
