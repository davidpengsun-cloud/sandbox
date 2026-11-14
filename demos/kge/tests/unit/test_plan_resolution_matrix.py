from dataclasses import replace

import pytest

from kge.semantics.plan import Binding, Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, vocabulary
from tests.unit.test_plan_relational_matrix import dimension, replace_context


class NoWarehouse:
    def cursor(self, *args):
        pytest.fail('an incomplete interpretation opened the warehouse')

    def attached(self, *args):
        pytest.fail('an incomplete interpretation opened attached warehouses')


@pytest.mark.parametrize('interpretation', ['rules', 'recorded'])
@pytest.mark.parametrize('suffix', [
    'current vs certified by month', 'stock-plan share of client-directed',
    'and another measure by product code with counts', 'gross inflows and outflows',
    'inflows outflows ratio with household count', 'compare to plan',
    'sparkly', 'UNKNOWN', 'not', 'by category and source', 'above 50',
    'count Entities', 'ratio', 'share',
])
def test_every_a1_component_and_unknown_span_blocks_execution(interpretation, suffix):
    v = vocabulary()
    text = 'Metric ' + suffix
    p = grounded(model(), text, v)
    reply = Propose(kind='propose', base_turn=None, plan=p)
    proposal = (lambda question, bundle: reply) if interpretation == 'rules' else reply
    result = Planner(v, NoWarehouse(), Hidden([], set(), [])).answer(text, proposal)
    assert result['kind'] in ('abstain', 'clarify')
    assert result['missing']
    assert 'result' not in result and 'figure' not in result


def test_resolution_alias_column_value_and_unit_remain_distinct():
    v = vocabulary(aliases=[('Office', 'u.metric')], values=[Entry('value', 'value', 'Office', 'u', column='u.events.category', value='Office')])
    assert {e.id for e in v.candidates('Office')} == {'value', 'u.metric'}
    p = grounded(model(), 'Office', v)
    assert check_plan(p, v, 'Office').kind == 'clarify'
    v = vocabulary(aliases=[('Office', 'u'), ('Office', 'u.metric')])
    assert check_plan(model(), v, 'Office').kind == 'clarify'


def test_context_cannot_disambiguate_without_written_context():
    sem = semantics()
    sem.contexts['elsewhere'] = replace_context(sem.contexts['ctx'], id='elsewhere', label='Elsewhere')
    v = vocabulary(sem, values=[Entry('one', 'value', 'Office', 'u', context='ctx', column='u.events.category', value='Office'),
                           Entry('two', 'value', 'Office', 'u', context='elsewhere', column='u.events.source', value='Office')])
    assert check_plan(grounded(model(), 'Office', v), v, 'Office').kind == 'clarify'


def test_hidden_alternatives_keep_readable_values_and_unreadable_units_refuse():
    sem = semantics()
    sem.tables['secret.rows'] = {'category': 'VARCHAR'}
    v = vocabulary(sem, values=[Entry('visible', 'value', 'Office', 'u', column='u.events.category', value='Office'),
                           Entry('hidden', 'value', 'Office', 'secret', column='secret.rows.category', value='Office')])
    assert all(e['id'] != 'hidden' for e in v.public())
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.category'), 'path': []}, 'op': 'eq', 'value': {'kind': 'text', 'value': 'Office'}, 'evidence': []}
    text = 'Metric category equal to Office'
    p = grounded(model(filters=[predicate]), text, v, {'category': 'f', 'equal to': 'f', 'Office': 'f'})
    assert isinstance(check_plan(p, v, text), CheckedPlan)
    assert check_plan(p, v, 'secret ' + text).kind == 'refuse'
    assert 'secret' not in str(v.public())


def test_product_scope_hides_values_columns_contexts_and_aliases():
    sem = semantics(joined=True)
    binding = Binding(release_identity='r', access_fingerprint='a', products=('u.events',), purpose='analysis')
    v = build_vocabulary(sem, binding, ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True)],
                         scoped_tables={'u.events'}, values=[Entry('hidden', 'value', 'Office', 'u', column='u.members.grouping', value='Office')],
                         aliases=[('Outside grouping', 'u.members.grouping')])
    assert 'hidden' not in v.entries and 'u.members.grouping' not in v.entries
    assert not v.candidates('Outside grouping')
    assert check_plan(model(), v, 'Outside grouping').kind == 'abstain'


def test_candidate_terms_require_steward_certification():
    v = vocabulary(candidates=[Entry('candidate', 'candidate', 'Draft metric', 'u')])
    p = model()
    data = p.model_dump(mode='json', by_alias=True)
    data['measures'][0]['measure'] = name('candidate')
    assert 'steward' in str(check_plan(type(p).model_validate(data), v, 'Draft metric'))


@pytest.mark.parametrize('change,reason', [
    ('measure', 'belong'), ('calendar', 'calendar'), ('period', 'period binding'),
    ('release', 'release/freeze'), ('hierarchy', 'level'), ('local_output', 'narrow rows'),
    ('mixed_phase', 'mixes row'), ('boolean', 'nonboolean'), ('number', 'nonnumeric'),
    ('member', 'undeclared member'), ('output_text', 'numeric literal'),
])
def test_resolution_type_role_and_policy_matrix(change, reason):
    sem = semantics(joined=True)
    options, text, assignments = {}, 'Metric', {}
    if change == 'period':
        sem.d.PERIODS = {}
    v = vocabulary(sem)
    if change == 'measure':
        v = replace(v, contexts={'ctx': replace_context(sem.contexts['ctx'], measure='u.other')}, entries={**v.entries, 'u.metric': Entry('u.metric', 'measure', 'Metric', 'u')})
    if change in ('calendar', 'period'):
        options['time'] = monthly_time()
        if change == 'calendar':
            v = replace(v, calendars={})
        text += ' 2025-04 through 2025-06'
        assignments.update({'2025-04': 'time', 'through': 'time', '2025-06': 'time'})
    if change == 'release':
        options['measures'] = [model().measures[0].model_dump(mode='json') | {'mode': 'as_reported'}]
    if change == 'hierarchy':
        d = dimension('d', 'u.members.grouping', ['member_join'])
        d['kind'], d['bindings'][0]['level'] = 'hierarchy', 1
        options['dimensions'] = [d]
        text += ' by grouping'
        assignments.update({'by': 'd', 'grouping': 'd'})
    if change in ('local_output', 'mixed_phase', 'boolean', 'number', 'member', 'output_text'):
        pred = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.category'), 'path': []}, 'op': 'eq',
                'value': {'kind': 'text', 'value': 'Undeclared'}, 'evidence': []}
        literal, word = 'Undeclared', 'equal to'
        if change == 'number':
            pred['value'], literal = {'kind': 'integer', 'value': 5}, '5'
        if change == 'boolean':
            pred['value'], literal = {'kind': 'boolean', 'value': True}, 'True'
        if change == 'output_text':
            pred['left'] = {'kind': 'output', 'id': 'm'}
        if change in ('local_output', 'mixed_phase'):
            pred['left'], pred['value'], literal = {'kind': 'output', 'id': 'm'}, {'kind': 'integer', 'value': 5}, '5'
        if change == 'member' or change == 'output_text':
            v = replace(v, phrases={**v.phrases, 'undeclared': ('u.events.category',)})
        if change == 'boolean':
            v = replace(v, phrases={**v.phrases, 'true': ('u.events.category',)})
        text += f' category {word} {literal}'
        assignments.update({'category': 'f', word: 'f', literal: 'f'})
        if change == 'local_output':
            options['measures'] = [model().measures[0].model_dump(mode='json') | {'filters': [pred]}]
        elif change == 'mixed_phase':
            row = dict(pred, id='row', left={'kind': 'column', 'name': name('u.events.value'), 'path': []})
            pred = {'kind': 'all', 'id': 'both', 'children': [pred, row], 'evidence': []}
            text += ' all value equal to 5'
            v = replace(v, phrases={**v.phrases, 'all': ('u.events.value',)})
            assignments.update({'all': 'both', 'value': 'row', 'equal to': ['f', 'row'], '5': ['f', 'row']})
            options['filters'] = [pred]
        else:
            options['filters'] = [pred]
    p = grounded(model(**options), text, v, assignments)
    decision = check_plan(p, v, text)
    assert not isinstance(decision, CheckedPlan)
    assert reason in str(decision), decision


def test_injected_predicate_cannot_claim_only_a_measure_name():
    v = vocabulary()
    pred = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []}, 'op': 'gt', 'value': {'kind': 'integer', 'value': 10}, 'evidence': []}
    text = 'Metric value'
    p = grounded(model(filters=[pred]), text, v, {'value': 'f'})
    assert 'unrequested predicate' in str(check_plan(p, v, text))


@pytest.mark.parametrize('currency,population', [('EUR', 'entities'), ('USD', 'others')])
def test_arithmetic_requires_compatible_currency_and_population(currency, population):
    sem = semantics()
    sem.contexts['second'] = replace_context(sem.contexts['ctx'], id='ctx_second', measure='u.second', term='Second')
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('u.second', currency, population, True)])
    second = model().measures[0].model_dump(mode='json') | {'id': 'n', 'measure': name('u.second'), 'context': name('ctx_second')}
    p = model(measures=[model().measures[0], second], derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'n', 'evidence': []}])
    text = 'Metric and Second ratio'
    p = grounded(p, text, v, {'Second': 'n', 'ratio': 'r'})
    assert 'incompatible' in str(check_plan(p, v, text))


@pytest.mark.parametrize('shape', ['comparison', 'derived', 'dimension', 'order', 'mode', 'count'])
def test_claiming_a_name_cannot_authorize_an_unwritten_operation(shape):
    v = vocabulary(semantics(release=True))
    options = {}
    targets = ['m']
    if shape == 'comparison':
        options['comparisons'] = [{'kind': 'time_mode', 'id': 'c', 'inputs': ['m'], 'base': 'current', 'other': 'as_reported', 'evidence': []}]
        targets.append('c')
    elif shape == 'derived':
        options['derived'] = [{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'm', 'evidence': []}]
        targets.append('r')
    elif shape == 'dimension':
        options['dimensions'] = [dimension('d', 'u.events.category')]
        targets.append('d')
    elif shape == 'order':
        options['order'] = [{'output': 'm', 'direction': 'desc', 'nulls': 'last', 'evidence': []}]
    elif shape == 'mode':
        options['measures'] = [model().measures[0].model_dump(mode='json') | {'mode': 'as_reported'}]
    else:
        options['measures'] = [{'kind': 'distinct_count', 'id': 'm', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'current', 'filters': []}]
    text = 'Entities' if shape == 'count' else 'Metric'
    plan = grounded(model(**options), text, v, {text: targets})
    assert check_plan(plan, v, text).kind == 'abstain'


@pytest.mark.parametrize('release', [False, True])
def test_latest_released_never_falls_back_to_an_unqualified_total(release):
    sem = semantics(release=release)
    if release:
        ctx = sem.contexts['ctx']
        sem.contexts['ctx'] = replace_context(ctx, joins=tuple(replace(j, required=False) for j in ctx.joins))
    v = vocabulary(sem)
    p = model(time={'kind': 'latest_released', 'calendar': name('cal')})
    text = 'Metric latest released'
    p = grounded(p, text, v, {'latest released': 'time'})
    assert 'release/freeze policy' in str(check_plan(p, v, text))


def test_a_monthly_observation_cannot_be_silently_split_into_daily_values():
    sem = semantics()
    sem.d.PERIODS['u.events']['kind'] = 'month'
    v = vocabulary(sem)
    p = model(dimensions=[{'kind': 'time', 'id': 't', 'calendar': name('cal'), 'grain': 'day'}])
    text = 'Metric by day'
    p = grounded(p, text, v, {'by': 't', 'day': 't'})
    assert 'finer than' in str(check_plan(p, v, text))


@pytest.mark.parametrize('wording,expected', [('is null', 'abstain'), ('is not null', 'checked')])
def test_a_null_predicate_cannot_invert_the_requested_condition(wording, expected):
    v = vocabulary()
    predicate = {'kind': 'null', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.category'), 'path': []}, 'is_null': False, 'evidence': []}
    text = 'Metric category ' + wording
    p = grounded(model(filters=[predicate]), text, v, {'category': 'f', wording: 'f'})
    decision = check_plan(p, v, text)
    assert isinstance(decision, CheckedPlan) if expected == 'checked' else decision.kind == expected


def test_value_visibility_uses_the_actual_column_and_context_scope():
    v = vocabulary(values=[Entry('spoof', 'value', 'Private choice', 'u', column='secret.rows.category', value='private'),
                           Entry('unknown_column', 'value', 'Missing choice', 'u', column='u.events.missing', value='missing'),
                           Entry('hidden_context', 'value', 'Hidden choice', 'u', context='unreadable_context', column='u.events.category', value='hidden')])
    assert not {'spoof', 'unknown_column', 'hidden_context'} & set(v.entries)
    assert all(not v.candidates(phrase) for phrase in ('Private choice', 'Missing choice', 'Hidden choice'))


def test_readable_unit_names_resolve_through_the_selected_context():
    v = vocabulary()
    text = 'u Metric'
    assert isinstance(check_plan(grounded(model(), text, v), v, text), CheckedPlan)


@pytest.mark.parametrize('unit_alias', [False, True])
def test_written_column_disambiguates_a_member_from_other_members_or_a_unit(unit_alias):
    values = [Entry('one', 'value', 'Office', 'u', column='u.events.category', value='Office')]
    if not unit_alias:
        values.append(Entry('two', 'value', 'Office', 'u', column='u.events.source', value='Office'))
    v = vocabulary(values=values, aliases=[('Office', 'u')] if unit_alias else [])
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.category'), 'path': []}, 'op': 'eq', 'value': {'kind': 'text', 'value': 'Office'}, 'evidence': []}
    text = 'Metric category equal to Office'
    p = grounded(model(filters=[predicate]), text, v, {'category': 'f', 'equal to': 'f', 'Office': 'f'})
    assert isinstance(check_plan(p, v, text), CheckedPlan)


def test_written_context_disambiguates_a_measure_alias_from_a_member():
    v = vocabulary(aliases=[('Office', 'u.metric')], values=[Entry('member', 'value', 'Office', 'u', column='u.events.category', value='Office')])
    text = 'Context one Office'
    assert isinstance(check_plan(grounded(model(), text, v), v, text), CheckedPlan)


def test_a_measure_and_its_own_context_label_are_one_checked_binding():
    sem = semantics()
    sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], label='Metric')
    v = vocabulary(sem)
    assert {e.kind for e in v.candidates('Metric')} == {'context', 'measure'}
    assert isinstance(check_plan(grounded(model(), 'Metric', v), v, 'Metric'), CheckedPlan)


def test_a_measure_requiring_a_restricted_column_is_refused_without_naming_it():
    sem = semantics()
    sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], expression='count(events.private)', values=('u.events.private',))
    v = vocabulary(sem)
    decision = check_plan(grounded(model(), 'Metric', v), v, 'Metric')
    assert decision.kind == 'refuse'
    assert 'private' not in str(decision)


def test_an_unreadable_unit_alias_still_refuses_before_a_readable_value():
    sem = semantics()
    sem.tables['secret.rows'] = {'category': 'VARCHAR'}
    v = vocabulary(sem, aliases=[('Office', 'secret')], values=[Entry('visible', 'value', 'Office', 'u', column='u.events.category', value='Office')])
    assert check_plan(model(), v, 'Office').kind == 'refuse'
    assert 'secret' not in str(check_plan(model(), v, 'Office'))


def test_an_unreadable_measure_with_the_same_label_does_not_block_a_readable_measure():
    sem = semantics()
    sem.tables['secret.rows'] = {'value': 'INTEGER'}
    sem.contexts['private_ctx'] = replace_context(sem.contexts['ctx'], id='private_ctx', fact='secret.rows', measure='secret.metric')
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('secret.metric', 'USD', 'entities', True)])
    assert isinstance(check_plan(grounded(model(), 'Metric', v), v, 'Metric'), CheckedPlan)
    assert 'secret.metric' not in v.entries
