import json
import re
from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from kge.semantics.coverage import ledger
from kge.semantics.plan import HistoryEntry, reply_adapter
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Join
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.planmodel import _normalize_name_evidence, _normalize_propose, payload, read
from tests.plan_fixtures import grounded, model, name, semantics, time_dimension, vocabulary


def proposal(plan=None):
    return {'kind': 'propose', 'base_turn': None, 'plan': (plan or model()).model_dump(mode='json', by_alias=True)}


def normalized(raw, question='Metric', v=None):
    v = v or vocabulary()
    original = deepcopy(raw)
    with runtrace.start('ask') as trace:
        result = _normalize_propose(raw, question, v, 'turn', attempt=1)
        records = trace.finish({}).get('plan_rewrites', [])
    assert raw == original
    for record in records:
        assert record['path'].startswith('/plan/') and record['reason']
        assert record['attempt'] == 1
        assert len(record['before_hash']) == len(record['after_hash']) == 64
    return result, records


def rules(records):
    return {r['rule'] for r in records}


def grouping(joined=False):
    sem = semantics(joined=joined)
    sem.d.GROUP_BY = {'category': 'u.events.category', 'grouping': 'u.members.grouping'}
    return sem


def dimension(column='u.events.category', path=()):
    return {'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name(column),
                                                     'path': [name(p) for p in path], 'level': None}]}


def predicate(column='u.events.entity_id', identifier='f', value=1):
    return {'kind': 'compare', 'id': identifier, 'left': {'kind': 'column', 'name': name(column), 'path': []},
            'op': 'lt', 'value': {'kind': 'integer', 'value': value}, 'evidence': []}


def filtered(dtype='INTEGER', column='u.events.entity_id'):
    sem = semantics()
    sem.tables['u.events'][column.rsplit('.', 1)[1]] = dtype
    sem.d.FILTERS = [(re.compile(r'above (\d+)'), None, '>', 1, None), (re.compile(r'below (\d+)'), None, '<', 1, None)]
    sem.d.FILTER_COLUMNS = [column, column]
    return vocabulary(sem)


@pytest.mark.parametrize('field,value,question,rule,changed', [
    ('mode', 'as_reported', 'Metric', 'default-mode', True),
    ('mode', 'as_reported', 'Metric as reported', 'default-mode', False),
    ('layout', 'separate', 'Metric', 'default-layout', True),
    ('layout', 'separate', 'Metric separate', 'default-layout', False),
    ('dimensions', [dimension()], 'Metric', 'ungrouped', True),
    ('dimensions', [dimension()], 'Metric by category', 'ungrouped', False),
])
def test_kept_defaults_are_traced_and_requested_values_preserved(field, value, question, rule, changed):
    raw = proposal()
    target = raw['plan']['measures'][0] if field == 'mode' else raw['plan']
    target[field] = value
    result, records = normalized(raw, question, vocabulary(grouping()))
    actual = result['plan']['measures'][0] if field == 'mode' else result['plan']
    assert (actual[field] != value) == changed
    assert (rule in rules(records)) == changed


def evidence_normalized(raw, question='Metric', v=None, history=()):
    v = v or vocabulary()
    with runtrace.start('ask') as trace:
        out = _normalize_name_evidence(raw, payload(question, v)['obligations'], v, history, attempt=1)
        return out, trace.finish({}).get('plan_rewrites', [])


def test_name_candidate_discard_is_traced_but_candidate_and_equivalence_survive():
    v = vocabulary()
    span = payload('Metric category', v)['obligations'][1]['span']
    raw = proposal()
    raw['plan']['measures'][0]['measure']['evidence'] = [span]
    out, records = evidence_normalized(raw, 'Metric category', v)
    assert out['plan']['measures'][0]['measure']['evidence'] == []
    assert rules(records) == {'name-candidate'}
    assert records[0]['spans'][0]['start'] == span['start']
    span = payload('Metric', v)['obligations'][0]['span']
    raw['plan']['measures'][0]['measure']['evidence'] = [span]
    out, records = evidence_normalized(raw)
    assert out == raw and not records
    v = replace(v, phrases={**v.phrases, 'metric': ('ctx',)})
    out, records = evidence_normalized(raw, v=v)
    assert out == raw and not records
    assert isinstance(check_plan(grounded(reply_adapter.validate_python(out).plan, 'Metric', v), v, 'Metric'), CheckedPlan)


def test_name_candidate_erasure_is_limited_to_measure_and_context_names_with_unrelated_candidates():
    v = member_vocabulary()
    span = payload('Metric', v)['obligations'][0]['span']
    for entry in v.entries.values():
        if entry.kind not in ('measure', 'context'):
            raw = name(entry.id, [span])
            out, records = evidence_normalized(raw, v=v)
            assert out == raw and not records, entry.id
    raw = name('ctx', [payload('current', v)['obligations'][0]['span']])
    out, records = evidence_normalized(raw, 'current', v)
    assert out == raw and not records


def test_validated_inherited_name_evidence_is_retained_only_on_unchanged_nodes():
    v = vocabulary()
    old = grounded(model(), 'Metric', v).model_dump(mode='json', by_alias=True)
    old['obligations'][0]['spans'][0]['turn'] = 'previous'
    span = old['obligations'][0]['spans'][0]
    old['measures'][0]['measure']['evidence'] = [span]
    history = (HistoryEntry.model_validate({'turn_id': 'previous', 'question': 'Metric', 'plan': old,
        'clarification': None, 'binding': v.binding.model_dump()}),)
    raw = {'kind': 'propose', 'base_turn': 'previous', 'plan': deepcopy(old)}
    out, records = evidence_normalized(raw, history=history)
    assert out == raw and not records
    raw['plan']['measures'][0]['mode'] = 'as_reported'
    out, records = evidence_normalized(raw, history=history)
    assert out == raw and not records
    assert 'unrelated evidence' in str(check_plan(reply_adapter.validate_python(out).plan, v, 'Metric'))


@pytest.mark.parametrize('bad', [None, {'turn': 'turn', 'start': 4, 'end': 4}, {'start': 'x', 'end': 4}])
@pytest.mark.parametrize('checked', [False, True])
def test_malformed_name_evidence_requires_checked_independent_obligation(bad, checked):
    raw = proposal(grounded(model(), 'Metric', vocabulary()) if checked else model())
    raw['plan']['measures'][0]['measure']['evidence'] = [bad]
    out, records = evidence_normalized(raw)
    assert out['plan']['measures'][0]['measure']['evidence'] == ([] if checked else [bad])
    assert ('name-structure' in rules(records)) == checked
    if checked:
        assert isinstance(check_plan(reply_adapter.validate_python(out).plan, vocabulary(), 'Metric'), CheckedPlan)
    else:
        with pytest.raises(ValidationError):
            reply_adapter.validate_python(out)


@pytest.mark.parametrize('location', ['predicate', 'obligation', 'clarify', 'abstain'])
def test_dropped_structural_repairs_leave_schema_rejection(location):
    bad = {'turn': 'turn', 'start': 0, 'end': 0}
    raw = proposal(grounded(model(filters=[predicate()]), 'Metric', vocabulary()))
    if location == 'predicate':
        raw['plan']['filters'][0]['evidence'] = [bad]
    elif location == 'obligation':
        raw['plan']['obligations'][0]['spans'].append(bad)
    else:
        raw = {'kind': location, 'spans': [bad], 'candidates' if location == 'clarify' else 'missing': []}
    out, records = evidence_normalized(raw)
    assert out == raw and not records
    with pytest.raises(ValidationError):
        reply_adapter.validate_python(out)


@pytest.mark.parametrize('value,calendar,changed', [('2026-Q1', 'fy', True), ('2025-Q2', 'fy', False),
                                                   ('2026-Q2', 'fy', False), ('2026-Q1', 'cal', False)])
def test_fiscal_format_requires_equivalent_year_quarter_and_declared_calendar(value, calendar, changed):
    sem = semantics()
    sem.d.CALENDARS['fy'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    raw = proposal(model(time={'kind': 'single', 'period': {'kind': 'fiscal_quarter', 'value': value, 'calendar': name(calendar)}}))
    out, records = normalized(raw, 'Metric FY26-Q1', vocabulary(sem))
    assert out['plan']['time']['period']['value'] == ('FY26-Q1' if changed else value)
    assert ('fiscal-format' in rules(records)) == changed


@pytest.mark.parametrize('reference', [None, 'comparison', 'derived', 'order'])
def test_time_dimension_is_removed_only_without_semantic_references(reference):
    raw = proposal(model(dimensions=[dimension(), time_dimension()]))
    if reference == 'comparison':
        raw['plan']['comparisons'] = [{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}]
    elif reference == 'derived':
        raw['plan']['derived'] = [{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': ['t'], 'remove_filters': [], 'evidence': []}]
    elif reference == 'order':
        raw['plan']['order'] = [{'output': 't', 'direction': 'asc', 'nulls': 'last', 'evidence': []}]
    out, records = normalized(raw, 'Metric by category', vocabulary(grouping()))
    assert len(out['plan']['dimensions']) == (1 if reference is None else 2)
    assert ('unused-time-dimension' in rules(records)) == (reference is None)


def test_requested_time_grain_is_preserved():
    raw = proposal(model(dimensions=[time_dimension()]))
    out, records = normalized(raw, 'Metric by month', vocabulary(grouping()))
    assert out == raw and not records


@pytest.mark.parametrize('multiple,compatible,changed', [(False, True, True), (True, True, False), (False, False, False)])
def test_grouping_column_requires_one_dimension_and_compatible_context(multiple, compatible, changed):
    sem = grouping()
    sem.d.GROUP_BY['grouping'] = 'u.members.grouping'
    if not compatible:
        sem.tables['u.members'] = {'grouping': 'VARCHAR'}
    v = vocabulary(sem)
    raw = proposal(model(dimensions=[dimension('u.events.source')]))
    question = 'Metric by category' if compatible else 'Metric by grouping'
    if multiple:
        raw['plan']['dimensions'].append({**dimension('u.events.source'), 'id': 'd2'})
    out, records = normalized(raw, question, v)
    assert out['plan']['dimensions'][0]['bindings'][0]['column']['id'] == ('u.events.category' if changed else 'u.events.source')
    assert ('grouping-column' in rules(records)) == changed


@pytest.mark.parametrize('node', ['dimension', 'predicate'])
@pytest.mark.parametrize('path_state', ['missing', 'empty', 'present', 'ambiguous', 'ambiguous-empty', 'fact-empty'])
def test_paths_fill_only_missing_unique_declared_routes(node, path_state):
    sem = grouping(joined=True)
    if path_state.startswith('ambiguous'):
        sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(*sem.contexts['ctx'].joins, replace(sem.contexts['ctx'].joins[0], id='second')))
    v = vocabulary(sem)
    if node == 'dimension':
        raw = proposal(model(dimensions=[dimension('u.members.grouping')]))
        binding = raw['plan']['dimensions'][0]['bindings'][0]
        question = 'Metric by grouping'
        rule = 'dimension-path'
    else:
        raw = proposal(model(filters=[predicate('u.members.grouping')]))
        binding = raw['plan']['filters'][0]['left']
        question = 'Metric'
        rule = 'predicate-path'
    if path_state in ('missing', 'ambiguous'):
        del binding['path']
    elif path_state == 'present':
        binding['path'] = [name('member_join')]
    elif path_state == 'fact-empty':
        binding['column' if node == 'dimension' else 'name'] = name('u.events.category')
        question = 'Metric by category' if node == 'dimension' else 'Metric'
    out, records = normalized(raw, question, v)
    actual = out['plan']['dimensions'][0]['bindings'][0] if node == 'dimension' else out['plan']['filters'][0]['left']
    if path_state in ('missing', 'empty'):
        assert actual['path'] == [name('member_join')]
        assert rule in rules(records)
    else:
        assert out == raw and not records
        if path_state == 'ambiguous':
            with pytest.raises(ValidationError):
                reply_adapter.validate_python(out)


@pytest.mark.parametrize('dtype,kind,value', [('INTEGER', 'integer', 30), ('DECIMAL', 'decimal', '30'), ('VARCHAR', 'text', '30')])
def test_unique_predicate_uses_declared_type_and_candidate_compatible_evidence(dtype, kind, value):
    v = filtered(dtype)
    raw = proposal(model(filters=[predicate()]))
    out, records = normalized(raw, 'Metric above 30', v)
    pred = out['plan']['filters'][0]
    assert pred['op'] == 'gt' and pred['value'] == {'kind': kind, 'value': value}
    span = ledger('Metric above 30', 'turn', v)[1].span.model_dump()
    assert pred['left']['name']['evidence'] == [span]
    assert rules(records) == {'predicate-op', 'predicate-scalar', 'predicate-evidence'}
    assert all(r['spans'][0]['start'] == span['start'] for r in records)


@pytest.mark.parametrize('dtype,kind,value', [('INTEGER', 'integer', 30), ('DECIMAL', 'decimal', '30'), ('VARCHAR', 'text', '30')])
def test_read_repaired_comparison_passes_declaration_aware_coverage(dtype, kind, value):
    from kge.semantics.planvocabulary import Entry

    v = filtered(dtype)
    if kind == 'text':
        v = replace(v, entries={**v.entries, 'thirty': Entry('thirty', 'value', 'Thirty', 'u', column='u.events.entity_id', value='30')})
    question = 'Metric above 30'
    raw = proposal(grounded(model(filters=[predicate()]), question, v, {'above 30': 'f'}))

    class Scripted:
        def json(self, prompt):
            return deepcopy(raw)

    reply = read(Scripted(), question, v)
    assert reply.plan.filters[0].value.model_dump() == {'kind': kind, 'value': value}
    assert isinstance(check_plan(reply.plan, v, question), CheckedPlan)


@pytest.mark.parametrize('dtype,kind,value,accepted', [('INTEGER', 'integer', 30, True), ('INTEGER', 'integer', 31, False),
                                                     ('INTEGER', 'decimal', '30', True), ('INTEGER', 'decimal', '30.0', True),
                                                     ('INTEGER', 'text', '30', False), ('INTEGER', 'boolean', True, False),
                                                     ('DECIMAL', 'decimal', '30.0', True), ('DECIMAL', 'decimal', '31', False),
                                                     ('INTEGER', 'null', None, False),
                                                     ('VARCHAR', 'text', '30', True), ('VARCHAR', 'text', '030', False)])
def test_read_followup_comparison_coverage_preserves_typed_value_mismatches(dtype, kind, value, accepted):
    from kge.semantics.planvocabulary import Entry
    from tests.unit.test_plan_model import prior

    v = filtered(dtype)
    if kind == 'text':
        v = replace(v, entries={**v.entries, **{s: Entry(s, 'value', s, 'u', column='u.events.entity_id', value=s) for s in ('30', '030')}})
    question = 'Metric above 30'
    pred = predicate()
    pred.update(op='gt', value={'kind': kind, **({'value': value} if kind != 'null' else {})})
    raw = proposal(grounded(model(filters=[pred]), question, v, {'above 30': 'f'}))
    raw['base_turn'] = 'previous'

    class Scripted:
        def json(self, prompt):
            return deepcopy(raw)

    reply = read(Scripted(), question, v, history=(prior(v),))
    assert getattr(reply.plan.filters[0].value, 'value', None) == value
    checked = check_plan(reply.plan, v, question)
    assert isinstance(checked, CheckedPlan) == accepted
    if not accepted:
        assert checked.kind == 'abstain'
        assert 'above 30' in str(checked) or 'literal on non' in str(checked)


@pytest.mark.parametrize('dtype,phrase,op,declared,kind,value,accepted', [
    *[(dtype, 'above 30.5', '>', '30.5', 'decimal', value, accepted)
      for dtype in ('INTEGER', 'DECIMAL', 'DOUBLE') for value, accepted in [('30.5', True), ('31', False)]],
    *[(dtype, 'after 2025-01-01', '>', '2025-01-01', 'date', value, accepted)
      for dtype in ('DATE', 'DATETIME', 'TIMESTAMP') for value, accepted in [('2025-01-01', True), ('2025-01-02', False)]],
    ('VARCHAR', 'category X', '=', 'X', 'text', 'X', True),
    ('VARCHAR', 'category X', '=', 'X', 'text', 'Y', False),
    ('BOOLEAN', 'active', '= TRUE', None, 'boolean', True, True),
    ('BOOLEAN', 'active', '= TRUE', None, 'boolean', False, False),
    ('BOOLEAN', 'inactive', '=', False, 'boolean', False, True),
    ('BOOLEAN', 'inactive', '=', False, 'boolean', True, False),
])
def test_read_followup_declared_scalar_coverage_compares_same_typed_values(dtype, phrase, op, declared, kind, value, accepted):
    from kge.semantics.planvocabulary import Entry
    from tests.unit.test_plan_model import prior

    sem = semantics()
    column = 'u.events.entity_id'
    sem.tables['u.events']['entity_id'] = dtype
    sem.d.FILTERS = [(re.compile(re.escape(phrase)), None, op, None, declared)]
    sem.d.FILTER_COLUMNS = [column]
    v = vocabulary(sem)
    if kind == 'text':
        v = replace(v, entries={**v.entries, **{s: Entry(s, 'value', s, 'u', column=column, value=s) for s in ('X', 'Y')}})
    question = 'Metric ' + phrase
    pred = predicate()
    pred.update(op='gt' if op == '>' else 'eq', value={'kind': kind, 'value': value})
    raw = proposal(grounded(model(filters=[pred]), question, v, {phrase: 'f'}))
    raw['base_turn'] = 'previous'

    class Scripted:
        def json(self, prompt):
            return deepcopy(raw)

    reply = read(Scripted(), question, v, history=(prior(v),))
    assert reply.plan.filters[0].value.model_dump() == {'kind': kind, 'value': value}
    checked = check_plan(reply.plan, v, question)
    assert isinstance(checked, CheckedPlan) == accepted, checked
    if not accepted:
        assert checked.kind == 'abstain' and phrase in str(checked)


def test_multiple_predicate_pieces_require_an_occurrence_match():
    v = filtered()
    question = 'Metric above 30 below 90'
    raw = proposal(model(filters=[predicate()]))
    out, records = normalized(raw, question, v)
    assert out == raw and not records
    span = ledger(question, 'turn', v)[-1].span.model_dump()
    raw['plan']['filters'][0]['evidence'] = [span]
    out, records = normalized(raw, question, v)
    assert out['plan']['filters'][0]['value']['value'] == 90
    assert out['plan']['filters'][0]['left']['name']['evidence'] == [span]
    assert rules(records) == {'predicate-scalar', 'predicate-evidence'}


def test_no_predicate_match_preserves_operator_scalar_and_evidence():
    raw = proposal(model(filters=[predicate()]))
    raw['plan']['filters'][0]['left']['name']['evidence'] = [{'turn': 'turn', 'start': 0, 'end': 6}]
    out, records = normalized(raw, 'Metric', filtered())
    assert out == raw and not records


@pytest.mark.parametrize('protected', [False, True])
def test_only_built_local_filters_are_hoisted_and_share_scope_is_preserved(protected):
    v = filtered()
    raw = proposal()
    raw['plan']['measures'][0]['filters'] = [predicate(), predicate(identifier='untouched', column='u.events.value')]
    if protected:
        raw['plan']['derived'] = [{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': [], 'remove_filters': ['f'], 'evidence': []}]
    out, records = normalized(raw, 'Metric above 30', v)
    assert [p['id'] for p in out['plan']['measures'][0]['filters']] == (['f', 'untouched'] if protected else ['untouched'])
    assert [p['id'] for p in out['plan']['filters']] == ([] if protected else ['f'])
    assert ('hoist-built-filter' in rules(records)) == (not protected)
    if protected:
        with pytest.raises(ValidationError, match='denominator scope'):
            reply_adapter.validate_python(out)


def member_vocabulary():
    sem = semantics()
    sem.tables['u.members'] = {'entity_id': 'INTEGER', 'source': 'VARCHAR'}
    sem.tables['u.links'] = {'entity_id': 'INTEGER', 'member_id': 'INTEGER'}
    sem.entities['member'] = {'label': 'Members', 'table': 'u.members', 'key': ['entity_id']}
    join = Join('member_rel', 'membership', 'events.entity_id = links.entity_id AND links.member_id = members.entity_id',
                None, 'u.links', 'u.events', 'u.members', False)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(join,))
    sem.contexts['far'] = replace(sem.contexts['ctx'], grain='day', id='far', fact='u.members', joins=(), filters=())
    return vocabulary(sem)


def membership():
    return {'kind': 'membership', 'id': 'f', 'entity': name('member'), 'relation': name('member_rel'),
            'context': name('far'), 'filters': [], 'evidence': []}


@pytest.mark.parametrize('collision', [False, True])
def test_membership_id_is_supplied_only_if_unique(collision):
    raw = proposal(model(filters=[membership()]))
    del raw['plan']['filters'][0]['id']
    if collision:
        raw['plan']['measures'][0]['id'] = 'membership'
    out, records = normalized(raw, 'Metric member_rel', member_vocabulary())
    assert ('id' in out['plan']['filters'][0]) == (not collision)
    assert ('membership-id' in rules(records)) == (not collision)


@pytest.mark.parametrize('state', ['absent', 'present', 'null', 'ungrounded'])
def test_membership_entity_fills_only_absent_on_grounded_relation(state):
    raw = proposal(model(filters=[membership()]))
    if state in ('absent', 'ungrounded'):
        del raw['plan']['filters'][0]['entity']
    elif state == 'present':
        raw['plan']['filters'][0]['entity'] = name('entity')
    else:
        raw['plan']['filters'][0]['entity'] = None
    out, records = normalized(raw, 'Metric' if state == 'ungrounded' else 'Metric member_rel', member_vocabulary())
    assert ('membership-entity' in rules(records)) == (state == 'absent')
    if state == 'absent':
        assert out['plan']['filters'][0]['entity'] == name('member')
    else:
        assert out == raw


@pytest.mark.parametrize('defect', ['relation-evidence', 'far-context', 'nested-filter'])
def test_dropped_membership_repairs_reach_checker_unchanged_and_abstain(defect):
    v = member_vocabulary()
    question = 'Metric membership member_rel'
    raw = proposal(grounded(model(filters=[membership()]), question, v, {'membership': 'f', 'member_rel': 'f'}))
    node = raw['plan']['filters'][0]
    if defect == 'relation-evidence':
        node['relation']['evidence'] = [ledger('Metric', 'turn', v)[0].span.model_dump()]
    elif defect == 'far-context':
        node['context'] = name('ctx')
    else:
        node['filters'] = [predicate('u.events.entity_id', identifier='nested')]
    class Scripted:
        def json(self, prompt):
            return deepcopy(raw)

    with runtrace.start('ask') as trace:
        reply = read(Scripted(), question, v)
        records = trace.finish({}).get('plan_rewrites', [])
    assert reply.plan.model_dump(mode='json', by_alias=True) == raw['plan'] and not records
    checked = check_plan(reply.plan, v, question)
    assert checked.kind == 'abstain'
    expected = {'relation-evidence': 'unrelated evidence', 'far-context': 'far context', 'nested-filter': 'role'}[defect]
    assert expected in str(checked)
    if defect == 'relation-evidence':
        assert checked.missing == ('A name has unrelated evidence.',)


@pytest.mark.parametrize('state,targets,changed', [('applied', [], True), ('unresolved', [], False),
                                                  ('ambiguous', [], False), ('applied', ['time'], False)])
def test_name_targets_fill_only_unique_explicit_meaning_without_replacing_obligation(state, targets, changed):
    v = vocabulary()
    raw = proposal(grounded(model(), 'Metric', v))
    obligation = raw['plan']['obligations'][0]
    obligation.update(id='model-owned', state=state, targets=targets)
    out, records = normalized(raw, v=v)
    assert out['plan']['obligations'][0] == {**obligation, 'targets': ['m'] if changed else targets}
    assert ('name-target' in rules(records)) == changed
    if not changed:
        assert check_plan(reply_adapter.validate_python(out).plan, v, 'Metric').kind == 'abstain'


def test_name_targets_do_not_expand_implicit_context_references_or_multiple_explicit_nodes():
    v = vocabulary()
    for text, duplicate in [('Entities', False), ('Metric', True)]:
        raw = proposal(grounded(model(), text, v))
        raw['plan']['obligations'][0]['targets'] = []
        if duplicate:
            raw['plan']['measures'].append({**deepcopy(raw['plan']['measures'][0]), 'id': 'extra'})
        out, records = normalized(raw, text, v)
        assert out == raw and not records
        assert check_plan(reply_adapter.validate_python(out).plan, v, text).kind == 'abstain'


def source_vocabulary():
    sem = semantics()
    sem.tables['v.events'] = sem.tables['u.events'].copy()
    sem.contexts['vctx'] = replace(sem.contexts['ctx'], grain='day', id='vctx', fact='v.events', measure='v.metric')
    from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
    return build_vocabulary(sem, vocabulary().binding, ['u', 'v'], ['u', 'v'],
                            [MeasureSpec('u.metric', None, '', True), MeasureSpec('v.metric', None, '', True)],
                            aliases=[('Both', ('u', 'v'))])


@pytest.mark.parametrize('duplicate', [False, True])
def test_source_set_targets_use_only_minimal_uniquely_supported_measure_set(duplicate):
    v = source_vocabulary()
    raw = proposal()
    raw['plan']['measures'].append({**deepcopy(raw['plan']['measures'][0]), 'id': 'v', 'context': name('vctx'), 'measure': name('v.metric')})
    if duplicate:
        raw['plan']['measures'].append({**deepcopy(raw['plan']['measures'][0]), 'id': 'extra'})
    raw['plan']['obligations'] = [{'id': 'owner', 'spans': [ledger('Both', 'turn', v)[0].span.model_dump()],
                                  'kind': 'name', 'state': 'applied', 'targets': []}]
    out, records = normalized(raw, 'Both', v)
    assert out['plan']['obligations'][0]['targets'] == ([] if duplicate else ['m', 'v'])
    assert ('source-set-target' in rules(records)) == (not duplicate)


@pytest.mark.parametrize('kind,question', [('filter', 'Metric above 30'), ('time', 'Metric June 2025'),
                                         ('name', 'Metric'), ('dimension', 'Metric by category'),
                                         ('mode', 'Metric current'), ('name', 'Metric 2025')])
def test_dropped_obligation_regeneration_preserves_model_ids_spans_kind_state_and_targets(kind, question):
    v = filtered()
    raw = proposal(model(filters=[predicate()]) if kind == 'filter' else model())
    raw['plan']['obligations'] = [{'id': 'model-uncertain', 'spans': [p.span.model_dump()], 'kind': kind,
                                  'targets': [], 'state': 'unresolved'} for p in ledger(question, 'turn', v)]
    out, records = normalized(raw, question, v)
    assert out['plan']['obligations'] == raw['plan']['obligations']
    assert not {'name-target', 'source-set-target'} & rules(records)
    assert check_plan(reply_adapter.validate_python(out).plan, v, question).kind == 'abstain'


def test_read_keeps_rewrites_on_failed_plancheck_and_separates_repair_attempts_without_raw_values():
    v = vocabulary()
    raw = proposal()
    raw['plan']['layout'] = 'separate'

    class Scripted:
        def json(self, prompt):
            return deepcopy(raw)

    with runtrace.start('ask') as trace:
        reply = read(Scripted(), 'Metric', v)
        assert check_plan(reply.plan, v, 'Metric').kind == 'abstain'
        out = trace.finish({})
    assert out['plan_rewrites'][0]['rule'] == 'default-layout'
    assert out['plan_rewrites'][0]['attempt'] == 0
    assert 'Metric' not in json.dumps(out)
    raw['plan']['measures'][0]['measure']['evidence'] = [None]
    with runtrace.start('ask') as trace:
        assert read(Scripted(), 'Metric', v).kind == 'abstain'
        out = trace.finish({})
    assert [r['attempt'] for r in out['plan_rewrites']] == [0, 1]


def test_attaching_predicate_evidence_does_not_hoist_an_unchanged_local_filter():
    v = filtered()
    raw = proposal()
    node = predicate(value=30)
    node['op'] = 'gt'
    raw['plan']['measures'][0]['filters'] = [node]
    out, records = normalized(raw, 'Metric above 30', v)
    assert out['plan']['filters'] == []
    assert out['plan']['measures'][0]['filters'][0]['value'] == node['value']
    assert rules(records) == {'predicate-evidence'}


def test_malformed_predicate_evidence_is_not_erased_by_scalar_reconstruction():
    raw = proposal(model(filters=[predicate()]))
    raw['plan']['filters'][0]['evidence'] = [None]
    out, records = normalized(raw, 'Metric above 30', filtered())
    assert out == raw and not records
    with pytest.raises(ValidationError):
        reply_adapter.validate_python(out)


def test_invalid_applied_predicate_obligation_cannot_ground_malformed_name_evidence():
    v = filtered()
    raw = proposal(grounded(model(filters=[predicate()]), 'Metric above 30', v, {'above 30': 'f'}))
    raw['plan']['filters'][0]['left']['name']['evidence'] = [None]
    out, records = evidence_normalized(raw, 'Metric above 30', v)
    assert out == raw and not records
    with pytest.raises(ValidationError):
        reply_adapter.validate_python(out)


def test_trace_noop_and_request_isolation():
    runtrace.plan_rewrite('default-mode', '/plan/measures/0/mode', 'Unrequested mode', 'as_reported', 'current', 0)
    raw = proposal()
    raw['plan']['layout'] = 'separate'
    with runtrace.start('ask') as outer:
        _normalize_propose(raw, 'Metric', vocabulary(), 'turn')
        with runtrace.start('ask') as inner:
            assert 'plan_rewrites' not in inner.finish({})
        assert len(outer.finish({})['plan_rewrites']) == 1


def test_malformed_name_is_not_grounded_by_an_obligation_on_another_occurrence():
    v = vocabulary()
    raw = proposal(grounded(model(), 'Metric', v))
    raw['plan']['measures'].append({**deepcopy(raw['plan']['measures'][0]), 'id': 'extra'})
    raw['plan']['measures'][1]['measure']['evidence'] = [None]
    out, records = evidence_normalized(raw)
    assert out == raw and not records
    with pytest.raises(ValidationError):
        reply_adapter.validate_python(out)


def test_multiple_missing_membership_ids_are_not_guessed():
    raw = proposal(model(filters=[membership(), {**membership(), 'id': 'second'}]))
    for node in raw['plan']['filters']:
        del node['id']
    out, records = normalized(raw, 'Metric member_rel', member_vocabulary())
    assert out == raw and not records


@pytest.mark.parametrize('evidence', [None, [None]])
def test_column_reconstruction_does_not_repair_malformed_name_evidence(evidence):
    raw = proposal(model(filters=[predicate()]))
    raw['plan']['filters'][0]['left']['name']['evidence'] = evidence
    out, records = normalized(raw, 'Metric above 30', filtered())
    assert out == raw and not records
    with pytest.raises(ValidationError):
        reply_adapter.validate_python(out)


@pytest.mark.parametrize('valid_obligation', [False, True])
def test_normalized_filter_has_no_extra_bigquery_jobs(monkeypatch, tmp_path, valid_obligation):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup

    fake, warehouses = setup(monkeypatch, tmp_path, False)
    sem = semantics()
    sem.entities = {'member': {'label': 'Member', 'table': 'u.events', 'key': ['entity_id']}}
    v = vocabulary(sem)
    question = 'Metric Member 1'
    raw = proposal(grounded(model(filters=[predicate(value=12)]), question, v, {'Member 1': 'f'}))
    if not valid_obligation:
        raw['plan']['obligations'][1]['state'] = 'unresolved'

    class Scripted:
        def json(self, prompt):
            return deepcopy(raw)

    try:
        with runtrace.start('ask') as trace:
            out = Planner(v, warehouses, Hidden([], set(), [])).answer(question, lambda q, scoped: read(Scripted(), q, scoped))
            rewrites = trace.finish(out)['plan_rewrites']
        assert out['kind'] == ('result' if valid_obligation else 'abstain')
        assert {'predicate-op', 'predicate-scalar', 'predicate-evidence'} <= rules(rewrites)
        assert jobs(fake) == ((1, 1) if valid_obligation else (0, 0))
    finally:
        fake.con.close()


@pytest.mark.parametrize('another_route', [False, True])
def test_alias_routes_are_counted_when_deciding_whether_a_missing_path_is_unique(another_route):
    sem = grouping(joined=True)
    original = sem.contexts['ctx'].joins[0]
    alias = replace(original, id='billing', alias='billing', condition=original.condition.replace('members.', 'billing.'))
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(original, alias) if another_route else (alias,))
    raw = proposal(model(dimensions=[dimension('u.members.grouping')]))
    del raw['plan']['dimensions'][0]['bindings'][0]['path']
    out, records = normalized(raw, 'Metric by grouping', vocabulary(sem))
    actual = out['plan']['dimensions'][0]['bindings'][0]
    if another_route:
        assert out == raw and not records
    else:
        assert actual['path'] == [name('billing')]
        assert rules(records) == {'dimension-path'}


@pytest.mark.parametrize('level', [1, 2])
def test_hierarchy_path_requires_exactly_one_route_at_the_declared_level(level):
    sem = grouping(joined=True)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(replace(sem.contexts['ctx'].joins[0], kind='hierarchy'),))
    dim = dimension('u.members.grouping')
    dim['kind'] = 'hierarchy'
    dim['bindings'][0]['level'] = level
    raw = proposal(model(dimensions=[dim]))
    del raw['plan']['dimensions'][0]['bindings'][0]['path']
    out, records = normalized(raw, 'Metric by grouping', vocabulary(sem))
    if level == 1:
        assert out['plan']['dimensions'][0]['bindings'][0]['path'] == [name('member_join')]
        assert rules(records) == {'dimension-path'}
    else:
        assert out == raw and not records


def test_two_requested_column_candidates_do_not_rebind_one_model_dimension():
    sem = grouping()
    sem.d.GROUP_BY['source'] = 'u.events.source'
    raw = proposal(model(dimensions=[dimension('u.events.entity_id')]))
    out, records = normalized(raw, 'Metric by category source', vocabulary(sem))
    assert out == raw and not records


def test_hoisting_membership_cannot_move_a_nested_denominator_filter():
    raw = proposal(model(filters=[]))
    member = membership()
    del member['entity']
    member['filters'] = [predicate('u.members.entity_id', identifier='nested')]
    raw['plan']['measures'][0]['filters'] = [member]
    raw['plan']['derived'] = [{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': [], 'remove_filters': ['nested'], 'evidence': []}]
    out, records = normalized(raw, 'Metric member_rel', member_vocabulary())
    assert out['plan']['filters'] == []
    assert out['plan']['measures'][0]['filters'][0]['filters'] == member['filters']
    assert rules(records) == {'membership-entity'}


def test_name_structure_trace_carries_the_checked_support_span_without_raw_turn():
    v = vocabulary()
    raw = proposal(grounded(model(), 'Metric', v))
    raw['plan']['measures'][0]['measure']['evidence'] = [None]
    out, records = evidence_normalized(raw)
    assert out['plan']['measures'][0]['measure']['evidence'] == []
    span = records[0]['spans'][0]
    assert span['start'] == 0 and span['end'] == 6
    assert len(span['turn_hash']) == 64 and 'turn' not in span


def test_unique_explicit_calendar_name_targets_the_existing_time_node():
    v = vocabulary()
    question = 'Metric cal'
    raw = proposal(grounded(model(), question, v, {'cal': []}))
    out, records = normalized(raw, question, v)
    assert out['plan']['obligations'][1]['targets'] == ['time']
    assert rules(records) == {'name-target'}
    assert isinstance(check_plan(reply_adapter.validate_python(out).plan, v, question), CheckedPlan)


def assert_dropped_coverage(raw, question, v):
    out, records = normalized(raw, question, v)
    assert out == raw and not records
    checked = check_plan(reply_adapter.validate_python(out).plan, v, question)
    assert checked.kind == 'abstain'
    return checked


def test_dropped_predicate_target_regeneration_does_not_resolve_a_correct_predicate():
    sem = semantics()
    sem.entities = {'member': {'label': 'Member', 'table': 'u.events', 'key': ['entity_id']}}
    v = vocabulary(sem)
    question = 'Metric Member 1'
    pred = predicate(value=1)
    pred['op'] = 'eq'
    pred['left']['name']['evidence'] = [ledger(question, 'turn', v)[1].span.model_dump()]
    raw = proposal(grounded(model(filters=[pred]), question, v, {'Member 1': 'f'}))
    raw['plan']['obligations'][1].update(state='unresolved', targets=[], kind='filter')
    assert 'Member 1' in str(assert_dropped_coverage(raw, question, v))


def test_dropped_time_target_regeneration_does_not_assign_comparison_period_to_base_time():
    v = vocabulary()
    def single(value):
        return {'kind': 'single', 'period': {'kind': 'month', 'value': value, 'calendar': name('cal')}}
    comparison = {'kind': 'explicit_period', 'id': 'c', 'inputs': ['m'], 'other': single('2025-05'), 'evidence': []}
    question = 'Metric June 2025 versus May 2025'
    raw = proposal(grounded(model(time=single('2025-06'), comparisons=[comparison]), question, v,
                             {'June 2025': 'time', 'versus': 'c', 'May 2025': 'c'}))
    raw['plan']['obligations'][-1].update(state='unresolved', targets=[], kind='time')
    assert 'May 2025' in str(assert_dropped_coverage(raw, question, v))


def test_dropped_operator_targets_cannot_cover_an_extra_measure():
    v = vocabulary()
    question = 'How much Metric'
    measures = [model().measures[0].model_dump(mode='json')]
    measures.append({**deepcopy(measures[0]), 'id': 'extra'})
    raw = proposal(grounded(model(measures=measures), question, v))
    raw['plan']['obligations'][0].update(state='unresolved', targets=[])
    assert 'extra' in str(assert_dropped_coverage(raw, question, v))


def test_dropped_grouping_targets_cannot_cover_an_extra_dimension():
    v = vocabulary(grouping())
    question = 'Metric by category'
    raw = proposal(grounded(model(dimensions=[dimension(), {**dimension('u.events.source'), 'id': 'extra'}]), question, v,
                             {'by': 'd', 'category': 'd'}))
    raw['plan']['obligations'][1].update(state='unresolved', targets=[], kind='dimension')
    assert 'extra' in str(assert_dropped_coverage(raw, question, v))


def test_dropped_literal_substring_targeting_leaves_an_unresolved_literal_for_checker():
    v = vocabulary()
    question = 'Metric 6'
    assert ledger(question, 'turn', v)[1].kind == 'literal'
    raw = proposal(grounded(model(time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}}),
                             question, v))
    raw['plan']['obligations'][1].update(state='unresolved', targets=[])
    assert '6' in str(assert_dropped_coverage(raw, question, v))
