"""Scope rules use lexical bindings rather than the model's choice of targets."""
from dataclasses import replace
import datetime as dt
import re

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.compile import compile_plan
from kge.semantics.plan import HistoryEntry, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planhistory import inherited
from kge.semantics.planvocabulary import Entry
from kge.services.ask.engine.planmodel import read
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import connection


def checked(plan, text, vocab):
    class Scripted:
        def json(self, prompt):
            return dict(kind='propose', base_turn=None, plan=plan.model_dump(mode='json', by_alias=True))
    reply = read(Scripted(), text, vocab)
    assert reply.kind == 'propose'
    return check_plan(reply.plan, vocab, text)


def predicate(identifier='f'):
    return dict(kind='compare', id=identifier, left=dict(kind='column', name=name('u.events.category'), path=[]),
                op='eq', value=dict(kind='text', value='A'), evidence=[])


def two_outputs(release=False):
    sem = semantics(release=release)
    sem.contexts['second'] = replace(sem.contexts['ctx'], id='second', label='Second', term='Second', grain='day')
    sem.d.FILTERS = [(re.compile(r'\bGroup A\b', re.I), 'category', '=', None, 'A')]
    sem.d.FILTER_COLUMNS = ['u.events.category']
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'second')],
                   values=[Entry('a', 'value', 'Group A', 'u', column='u.events.category', value='A')])
    outputs = [model().measures[0].model_dump(), dict(model().measures[0].model_dump(), id='n', context=name('second'))]
    return v, outputs


@pytest.mark.parametrize('wording', ["Group A's First and Second", 'First and Second for the measure First Group A'])
def test_explicit_measure_scope_allows_one_output_to_keep_the_filter(wording):
    v, outputs = two_outputs()
    outputs[0]['filters'] = [predicate()]
    plan = grounded(model(measures=outputs), wording, v, {'First': 'm', 'Second': 'n', 'Group A': 'f'})
    assert isinstance(checked(plan, wording, v), CheckedPlan)


def test_first_question_cannot_use_an_unscoped_named_measure_to_drop_a_filter():
    v, outputs = two_outputs()
    outputs[0]['filters'] = [predicate()]
    text = 'First and Second Group A'
    plan = grounded(model(measures=outputs), text, v, {'First': 'm', 'Second': 'n', 'Group A': 'f'})
    assert checked(plan, text, v).kind == 'abstain'


@pytest.mark.parametrize('operator', ['versus', 'vs', 'compared with', 'against'])
def test_comparison_contexts_sharing_a_measure_keep_their_question_order(operator):
    v, outputs = two_outputs()
    text = f'First {operator} Second'
    plan = grounded(model(measures=outputs, comparisons=[dict(kind='contexts', id='c', left='m', right='n', evidence=[])]),
                    text, v, {'First': 'm', 'Second': 'n', operator: 'c'})
    assert isinstance(checked(plan, text, v), CheckedPlan)
    data = plan.model_dump(mode='json', by_alias=True)
    data['comparisons'][0].update(left='n', right='m')
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    assert isinstance(check_plan(plan, v, 'Now just', 'next', inherited(plan, history, v)), CheckedPlan)


def test_inherited_ambiguous_value_keeps_only_its_checked_column_binding():
    v = vocabulary(values=[Entry('category-a', 'value', 'Same', 'u', column='u.events.category', value='A'),
                           Entry('source-a', 'value', 'Same', 'u', column='u.events.source', value='A')])
    text = 'Metric category equal to Same'
    plan = grounded(model(filters=[predicate()]), text, v, {'category': 'f', 'equal to': 'f', 'Same': 'f'})
    assert isinstance(checked(plan, text, v), CheckedPlan)
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    pieces = inherited(plan, history, v)
    original = next(p for p in ledger(text, 'turn', v) if p.text == 'Same')
    resolved = next(p for p in pieces if p.text == 'Same')
    assert set(original.candidates) == {'category-a', 'source-a'}
    assert resolved.candidates == ('category-a',)
    assert isinstance(check_plan(plan, v, 'Now just', 'next', pieces), CheckedPlan)


def test_composed_modifier_uses_declared_measure_identity_for_any_pack():
    v, outputs = two_outputs()
    v = replace(v, phrases={**v.phrases, 'qualified metric': ('ctx',), 'directed metric': ('second',), 'metric': ('u.metric',)})
    text = 'Qualified directed Metric'
    plan = grounded(model(measures=[outputs[1]]), text, v, {text: 'n'})
    assert ledger(text, 'turn', v)[0].candidates == ('second',)
    assert isinstance(checked(plan, text, v), CheckedPlan)
    data = plan.model_dump(mode='json', by_alias=True)
    data['measures'][0]['context'] = name('ctx')
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


def test_measure_parts_cannot_move_to_another_context_named_elsewhere():
    v, outputs = two_outputs()
    sem = v.semantics
    sem.d.FILTERS = (*sem.d.FILTERS, (re.compile(r'\bGroup B\b', re.I), 'category', '=', None, 'B'))
    sem.d.FILTER_COLUMNS = (*sem.d.FILTER_COLUMNS, 'u.events.category')
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'second')], values=[
        Entry('a', 'value', 'Group A', 'u', column='u.events.category', value='A'),
        Entry('b', 'value', 'Group B', 'u', column='u.events.category', value='B')])
    first = dict(outputs[0], id='a', filters=[predicate('fa')])
    second = dict(outputs[0], id='b', filters=[{**predicate('fb'), 'value': dict(kind='text', value='B')}])
    text = 'First Group A, Group B and Second sum'
    plan = grounded(model(measures=[first, second, outputs[1]]), text, v,
                    {'First': ['a', 'b'], 'Group A': 'fa', 'Group B': 'fb', 'Second': 'n', 'sum': 'n'})
    assert isinstance(checked(plan, text, v), CheckedPlan)
    data = plan.model_dump(mode='json', by_alias=True)
    data['measures'][1]['context'] = name('second')
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


def test_whole_population_permission_belongs_only_to_its_own_share():
    v, outputs = two_outputs()
    text = 'First share of the total and Second share for Group A'
    plan = grounded(model(measures=outputs, filters=[predicate()], derived=[
        dict(kind='share', id='a', input='m', remove_dimensions=[], remove_filters=['f'], evidence=[]),
        dict(kind='share', id='b', input='n', remove_dimensions=[], remove_filters=[], evidence=[])]),
        text, v, {'First': 'm', 'Second': 'n', 'Group A': 'f'})
    data = plan.model_dump(mode='json', by_alias=True)
    shares = iter(['a', 'b'])
    for obligation, piece in zip(data['obligations'], ledger(text, 'turn', v)):
        if piece.operation == 'share':
            obligation['targets'] = [next(shares)]
    plan = QueryPlan.model_validate(data)
    assert isinstance(checked(plan, text, v), CheckedPlan)
    data['derived'][0]['remove_filters'] = []
    data['derived'][1]['remove_filters'] = ['f']
    share_obligations = [o for o, piece in zip(data['obligations'], ledger(text, 'turn', v)) if piece.operation == 'share']
    share_obligations[0]['targets'], share_obligations[1]['targets'] = ['b'], ['a']
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


def test_inherited_filter_scopes_never_read_followup_text_at_old_span_offsets():
    v, outputs = two_outputs()
    sem = v.semantics
    sem.d.FILTERS = (*sem.d.FILTERS, (re.compile(r'\bGroup B\b', re.I), 'category', '=', None, 'B'))
    sem.d.FILTER_COLUMNS = (*sem.d.FILTER_COLUMNS, 'u.events.category')
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'second')], values=[
        Entry('a', 'value', 'Group A', 'u', column='u.events.category', value='A'),
        Entry('b', 'value', 'Group B', 'u', column='u.events.category', value='B')])
    outputs[0]['filters'] = [{**predicate('fb'), 'value': dict(kind='text', value='B')}]
    outputs[1]['filters'] = [predicate('fa')]
    text = 'First Group A for the measure Second and Group B for the measure First'
    plan = grounded(model(measures=outputs), text, v, {'First': 'm', 'Second': 'n', 'Group A': 'fa', 'Group B': 'fb'})
    assert isinstance(checked(plan, text, v), CheckedPlan)
    left, right = [p for p in ledger(text, 'turn', v) if p.kind == 'predicate']
    followup = 'Now just'.ljust(left.span.end) + 'and' + ' ' * (right.span.start - left.span.end - 3)
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    assert isinstance(check_plan(plan, v, followup, 'next', inherited(plan, history, v)), CheckedPlan)


def figures(plan, text, vocab, rows):
    decision = checked(plan, text, vocab)
    assert isinstance(decision, CheckedPlan), decision
    compiled = compile_plan(decision)
    with connection(rows) as con:
        result = con.execute(compiled.answer.sql).fetchone()
    return dict(zip((column.id for column in compiled.columns), result))


@pytest.mark.parametrize('prefix,operator', [('', 'versus'), ('Compare ', 'with'), ('Compare ', 'to'),
                                          ('Compare ', 'and'), ('Compare ', 'versus'), ('', 'compared with')])
def test_comparison_occurrences_cannot_reuse_an_earlier_operand_pair(prefix, operator):
    v, outputs = two_outputs()
    v = replace(v, contexts={**v.contexts, 'second': replace(v.contexts['second'], expression='sum(events.value) - 10', grain='day')})
    text = f'{prefix}First {operator} Second and {prefix}Second {operator} First with difference'
    comparisons = [dict(kind='contexts', id=identifier, left=left, right=right, evidence=[])
                   for identifier, left, right in [('c1', 'm', 'n'), ('c2', 'n', 'm')]]
    derived = [dict(kind='difference', id='d' + c['id'], numerator=c['id'] + '/base',
                    denominator=c['id'] + '/other', evidence=[]) for c in comparisons]
    plan = grounded(model(measures=outputs, comparisons=comparisons, derived=derived), text, v,
                    {'First': 'm', 'Second': 'n', 'difference': ['dc1', 'dc2']})
    data = plan.model_dump(mode='json', by_alias=True)
    occurrence = 0
    boundary = text.index(' and ' + prefix + 'Second')
    for obligation, piece in zip(data['obligations'], ledger(text, 'turn', v)):
        if piece.operation == 'comparison':
            obligation['targets'] = ['c1' if piece.span.start < boundary else 'c2']
            occurrence += 1
    assert occurrence >= 2
    plan = QueryPlan.model_validate(data)
    row = figures(plan, text, v, [(dt.date(2025, 6, 1), 20, 'A', 1, 'x')])
    assert row['c1/base'] == 20 and row['c1/other'] == 10 and row['dc1'] == 10
    assert row['c2/base'] == 10 and row['c2/other'] == 20 and row['dc2'] == -10
    data['comparisons'][1].update(left='m', right='n')
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


@pytest.mark.parametrize('omitted', ['m', 'n'])
@pytest.mark.parametrize('separator', [' and ', ', ', '; '])
@pytest.mark.parametrize('preposition', ['for ', ''])
def test_coordinated_measure_clauses_keep_their_own_filters(omitted, separator, preposition):
    v, outputs = two_outputs()
    sem = v.semantics
    sem.d.FILTERS = (*sem.d.FILTERS, (re.compile(r'\bGroup B\b', re.I), 'category', '=', None, 'B'))
    sem.d.FILTER_COLUMNS = (*sem.d.FILTER_COLUMNS, 'u.events.category')
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'second')], values=[
        Entry('a', 'value', 'Group A', 'u', column='u.events.category', value='A'),
        Entry('b', 'value', 'Group B', 'u', column='u.events.category', value='B')])
    outputs[0]['filters'] = [predicate('fa')]
    outputs[1]['filters'] = [{**predicate('fb'), 'value': dict(kind='text', value='B')}]
    text = f'First {preposition}Group A{separator}Second {preposition}Group B'
    plan = grounded(model(measures=outputs), text, v, {'First': 'm', 'Second': 'n', 'Group A': 'fa', 'Group B': 'fb'})
    row = figures(plan, text, v, [(dt.date(2025, 6, 1), value, group, 1, 'x') for value, group in [(20, 'A'), (5, 'B')]])
    assert row['m'] == 20 and row['n'] == 5
    data = plan.model_dump(mode='json', by_alias=True)
    next(m for m in data['measures'] if m['id'] == omitted)['filters'] = []
    for obligation in data['obligations']:
        obligation['targets'] = [t for t in obligation['targets'] if t != ('fa' if omitted == 'm' else 'fb')]
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'
    data = plan.model_dump(mode='json', by_alias=True)
    data['measures'][0]['filters'], data['measures'][1]['filters'] = data['measures'][1]['filters'], data['measures'][0]['filters']
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


@pytest.mark.parametrize('word,mode', [('current', 'current'), ('live', 'current'), ('recorded', 'as_reported')])
def test_declared_mode_operand_binds_the_unique_unnamed_measure(word, mode):
    v, outputs = two_outputs(release=True)
    v.semantics.d.TIME_MODES = {word: mode}
    outputs[0]['mode'] = mode
    text = f'{word} versus Second'
    plan = grounded(model(measures=outputs, comparisons=[dict(kind='contexts', id='c', left='m', right='n', evidence=[])]),
                    text, v, {word: 'm/mode', 'versus': 'c', 'Second': 'n'})
    assert isinstance(checked(plan, text, v), CheckedPlan)
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    assert isinstance(check_plan(plan, v, 'Now just', 'next', inherited(plan, history, v)), CheckedPlan)
    data = plan.model_dump(mode='json', by_alias=True)
    data['comparisons'][0].update(left='n', right='m')
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'
    extra = dict(outputs[0], id='extra')
    data = plan.model_dump(mode='json', by_alias=True)
    data['measures'].append(extra)
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


@pytest.mark.parametrize('word,mode', [('current', 'current'), ('live', 'current'), ('recorded', 'as_reported')])
@pytest.mark.parametrize('plain_first', [True, False])
@pytest.mark.parametrize('wording', ['{word} versus Qualified Metric', 'Compare {word} with Qualified Metric',
                                    '{word} versus Qualified June 2025 Metric'])
def test_implicit_mode_operand_uses_the_declared_head_noun_independently_of_proposals(word, mode, plain_first, wording):
    sem = semantics(release=True)
    for identifier, label in [('qualified', 'Qualified Metric'), ('directed', 'Directed Metric'), ('other', 'Other Metric')]:
        sem.contexts[identifier] = replace(sem.contexts['ctx'], id=identifier, label=label, term=label, grain='day')
    if not plain_first:
        sem.contexts = {**{identifier: ctx for identifier, ctx in sem.contexts.items() if identifier != 'ctx'}, 'ctx': sem.contexts['ctx']}
    sem.d.TIME_MODES = {word: mode}
    v = vocabulary(sem)
    text = wording.format(word=word)
    outputs = [dict(model().measures[0].model_dump(), mode=mode),
               dict(model().measures[0].model_dump(), id='n', context=name('qualified'))]
    plan = grounded(model(measures=outputs,
        time=(dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('cal')))
              if 'June 2025' in text else model().time.model_dump(mode='json')),
        comparisons=[dict(kind='contexts', id='c', left='m', right='n', evidence=[])]), text, v,
        {piece.text: 'time' if piece.kind == 'time' else 'c' if piece.operation == 'comparison' else
         'm/mode' if piece.operation == mode else 'n' for piece in ledger(text, 'turn', v)})
    assert isinstance(checked(plan, text, v), CheckedPlan)
    for context in ('directed', 'other', 'qualified'):
        data = plan.model_dump(mode='json', by_alias=True)
        data['measures'][0]['context'] = name(context)
        assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain', context


@pytest.mark.parametrize('scope', ['possessive', 'coordinated', 'unscoped'])
def test_named_filter_covers_attached_counts_without_leaking_to_another_clause(scope):
    v, outputs = two_outputs()
    sem = v.semantics
    sem.d.FILTERS = (*sem.d.FILTERS, (re.compile(r'\bGroup B\b', re.I), 'category', '=', None, 'B'))
    sem.d.FILTER_COLUMNS = (*sem.d.FILTER_COLUMNS, 'u.events.category')
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'second')], values=[
        Entry('a', 'value', 'Group A', 'u', column='u.events.category', value='A'),
        Entry('b', 'value', 'Group B', 'u', column='u.events.category', value='B')])
    outputs.append(dict(kind='distinct_count', id='c', entity=name('entity'), context=name('ctx'), mode='current', filters=[]))
    for output in (outputs if scope == 'unscoped' else [outputs[0], outputs[2]]):
        output['filters'] = [predicate('f' + output['id'])]
    if scope == 'coordinated':
        outputs[1]['filters'] = [{**predicate('fb'), 'value': dict(kind='text', value='B')}]
    text = {'possessive': "Group A's First with count Entities and Second",
            'coordinated': 'First with count Entities for Group A and Second for Group B',
            'unscoped': 'First and Second with count Entities for Group A'}[scope]
    plan = grounded(model(measures=outputs), text, v, {'First': 'm', 'Second': 'n', 'count': 'c', 'Entities': 'c',
        'Group A': ['fm', 'fc', 'fn'] if scope == 'unscoped' else ['fm', 'fc'], 'Group B': 'fb'})
    row = figures(plan, text, v, [(dt.date(2025, 6, 1), value, group, entity, 'x')
                                for value, group, entity in [(20, 'A', 1), (5, 'B', 2)]])
    assert row == dict(m=20, n=5 if scope == 'coordinated' else 20 if scope == 'unscoped' else 25, c=1)
    data = plan.model_dump(mode='json', by_alias=True)
    data['measures'][2]['filters'] = []
    for obligation in data['obligations']:
        obligation['targets'] = [target for target in obligation['targets'] if target != 'fc']
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


def test_measure_scoped_filter_does_not_narrow_a_count_attached_to_another_measure():
    v, outputs = two_outputs()
    outputs[0]['filters'] = [predicate()]
    outputs.append(dict(kind='distinct_count', id='c', entity=name('entity'), context=name('second'), mode='current', filters=[]))
    text = "Group A's First and Second with count Entities"
    plan = grounded(model(measures=outputs), text, v,
                    {'Group A': 'f', 'First': 'm', 'Second': 'n', 'count': 'c', 'Entities': 'c'})
    row = figures(plan, text, v, [(dt.date(2025, 6, 1), value, group, entity, 'x')
                                for value, group, entity in [(20, 'A', 1), (5, 'B', 2)]])
    assert row == dict(m=20, n=25, c=2)


@pytest.mark.parametrize('omitted', ['m', 'n'])
def test_unscoped_filter_after_coordinated_measures_still_covers_every_output(omitted):
    v, outputs = two_outputs()
    for output in outputs:
        output['filters'] = [predicate('f' + output['id'])]
    text = 'First and Second for Group A'
    plan = grounded(model(measures=outputs), text, v, {'First': 'm', 'Second': 'n', 'Group A': ['fm', 'fn']})
    row = figures(plan, text, v, [(dt.date(2025, 6, 1), value, group, 1, 'x') for value, group in [(20, 'A'), (5, 'B')]])
    assert row['m'] == row['n'] == 20
    data = plan.model_dump(mode='json', by_alias=True)
    next(m for m in data['measures'] if m['id'] == omitted)['filters'] = []
    for obligation in data['obligations']:
        obligation['targets'] = [t for t in obligation['targets'] if t != 'f' + omitted]
    assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'


@pytest.mark.parametrize('keep', [False, True])
@pytest.mark.parametrize('period,kind,value', [('', None, None), ('June 2025 ', 'month', '2025-06'),
    ('2025-06-01 ', 'day', '2025-06-01'), ('Q2 2025 ', 'calendar_quarter', '2025-Q2'), ('CY2025 ', 'year', '2025')])
def test_declared_adjacent_modifier_keeps_its_predicate_in_a_narrow_history_vocabulary(keep, period, kind, value):
    from kge.semantics.query import Rule
    from kge.semantics.vocabulary import compiled
    sem = semantics()
    sem.contexts['directed'] = replace(sem.contexts['ctx'], id='directed', term='Directed Metric', label='Directed Metric', grain='day')
    sem.contexts['approved'] = replace(sem.contexts['ctx'], id='approved', term='Approved Metric', label='Approved Metric', grain='day',
        filters=(*sem.contexts['ctx'].filters, Rule('archived', 'Archived rows', "events.source = 'x'", 'u.events')))
    del sem.contexts['ctx']
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(label='Approved (archived)', modifier=True,
        phrases=['approved'], column='u.events.source', op='eq', value='x'))]
    sem.d.VOCABULARY[0]['params']['with'] = ['Directed Metric']
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem)
    text = 'approved ' + period + 'Directed Metric'
    pieces = ledger(text, 'turn', v)
    assert [(p.text, p.candidates) for p in pieces if p.kind == 'name'] == [('Directed Metric', ('directed',))]
    assert next(p for p in pieces if p.text == 'approved').kind == 'predicate'
    predicate = dict(kind='compare', id='archive', left=dict(kind='column', name=name('u.events.source'), path=[]),
                     op='eq', value=dict(kind='text', value='x'), evidence=[])
    output = model().measures[0].model_dump()
    output['context'] = name('directed')
    time = dict(kind='single', period=dict(kind=kind, value=value, calendar=name('cal'))) if period else model().time.model_dump()
    plan = grounded(model(measures=[output], time=time, filters=[predicate] if keep else []), text, v,
                    {'approved': 'archive' if keep else 'm', 'Directed Metric': 'm', period.strip(): 'time'})
    result = checked(plan, text, v)
    assert isinstance(result, CheckedPlan) if keep else result.kind == 'abstain'
    if keep:
        data = plan.model_dump(mode='json', by_alias=True)
        data['measures'][0]['context'] = name('approved', [pieces[0].span.model_dump()])
        assert checked(QueryPlan.model_validate(data), text, v).kind == 'abstain'
