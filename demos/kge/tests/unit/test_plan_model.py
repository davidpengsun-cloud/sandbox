import json
from copy import deepcopy

import pytest

from kge.adapters.model import NoFallback, NotJSON
from kge.semantics.plan import HistoryEntry
from kge.services.ask.engine.planmodel import VERSION, bundle, payload, read
from tests.plan_fixtures import grounded, model, semantics, vocabulary
from tests.plan_recordings import load, recorded


def test_public_vocabulary_migration_changes_only_keys_and_preserves_recorded_replies():
    from tests.agent_recordings import VOCABULARY_402_MIGRATION, key_of
    from tests.scratch import DEMO

    folder = DEMO / 'tests/fixtures/query-plan-v1'
    original = (folder / 'public-ask.json').read_text()
    current = (folder / 'public-ask-readable-vocabulary.json').read_text()
    before, after = json.loads(original), json.loads(current)
    migration = json.loads(VOCABULARY_402_MIGRATION.read_text())['cleanup']['public_recordings']
    entries = {row['key']: row for row in migration}
    assert len(before) == len(after) == len(entries) == len(migration)
    for old, new in zip(before, after):
        entry = entries[json.loads(new['key'])[1]]
        assert entry['after'] == key_of(new['key'])
        assert old['reply'] == new['reply']
        assert entry['reply_sha'] == key_of(json.dumps(new['reply'], sort_keys=True))
        original = original.replace(json.dumps(old['key']), json.dumps(new['key']), 1)
    assert original.encode() == current.encode()


def test_recorded_full_plan_clarification_abstention_and_followup():
    v = vocabulary()
    for question, kind in [('Metric', 'propose'), ('Which metric?', 'clarify'), ('Unavailable', 'abstain'),
                           ('Metric again', 'propose')]:
        reply = read(recorded(), question, v)
        assert reply.kind == kind
        if question.endswith('again'):
            assert reply.base_turn == 'previous'


def test_missing_recording_keeps_the_mock_response_token_trace():
    from kge.services.ask.engine import runtrace

    vocab = vocabulary()
    key = payload('Metric', vocab)['recording_key']
    expected = {'kind': 'abstain', 'spans': [], 'missing': ['Missing plan recording: ' + key]}
    out = runtrace.run('ask', lambda: {'reply': read(recorded({}), 'Metric', vocab).model_dump(mode='json')})
    assert out['reply'] == expected
    call, = out['run_trace']['llm_calls']
    assert call['ok'] is True and 'error' not in call
    assert call['model'] == 'mock:mock' and call['served_model'] == 'mock'
    assert call['tokens_in'] > 0 and call['tokens_out'] == len(json.dumps(expected).split())


def test_measure_evidence_citing_its_declaring_context_is_preserved():
    from dataclasses import replace
    from kge.semantics.plan import Propose
    from kge.semantics.plancheck import CheckedPlan, check_plan
    from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary

    sem = semantics()
    ctx = replace(sem.contexts['ctx'], id='context:wealth.nna', measure='measure:wealth.nna', term='NNA', grain='day')
    sem.contexts = {ctx.id: ctx}
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'],
                         [MeasureSpec(ctx.measure, 'USD', 'entities', True)])
    v = replace(v, phrases={**v.phrases, 'nna': (ctx.id,)})
    pieces = payload('NNA', v)['obligations']
    assert pieces[0]['candidates'] == ('context:wealth.nna',)
    span = pieces[0]['span']
    p = grounded(model(measures=[{'kind': 'measure', 'id': 'm', 'measure': {'id': ctx.measure, 'evidence': [span]},
                                  'context': {'id': ctx.id, 'evidence': [span]}, 'mode': 'current', 'filters': []}]), 'NNA', v)

    class Scripted:
        def json(self, prompt):
            return {'kind': 'propose', 'base_turn': None, 'plan': p.model_dump(mode='json', by_alias=True)}

    reply = read(Scripted(), 'NNA', v)
    assert isinstance(reply, Propose)
    assert reply.plan.measures[0].measure.evidence == reply.plan.measures[0].context.evidence
    assert reply.plan.measures[0].context.evidence
    checked = check_plan(reply.plan, v, 'NNA', 'turn')
    assert isinstance(checked, CheckedPlan), checked


@pytest.mark.parametrize('mapping', [None, {}])
def test_missing_recording_abstains_by_key_without_sql_or_rules(mapping):
    v = vocabulary()
    key = payload('Unrecorded', v)['recording_key']
    reply = read(recorded(load() if mapping is None else mapping), 'Unrecorded', v)
    assert reply.kind == 'abstain' and reply.missing == ('Missing plan recording: ' + key,)


def test_prompt_is_scoped_versioned_and_sql_free():
    v = vocabulary()
    data = payload('Metric', v)
    assert data['version'] == VERSION
    assert 'SELECT' not in json.dumps(data)
    assert 'sum(events.value)' not in json.dumps(bundle(v))
    assert 'private' not in json.dumps(bundle(v))
    assert data['obligations'][0]['candidates']
    assert data['recording_key'] != payload('Metric', vocabulary(aliases=[('Other', 'u.metric')]))['recording_key']


def test_history_prompt_explains_removed_node_evidence():
    v = vocabulary()
    history = (HistoryEntry(turn_id='turn', question='Metric', plan=grounded(model(), 'Metric', v),
        clarification=None, binding=v.binding),)
    class Capture:
        def json(self, prompt):
            self.instructions = json.loads(prompt.split('\n', 1)[1])['instructions']
            return {'kind': 'abstain', 'spans': [], 'missing': ['Test recording']}
    recording = Capture()
    assert read(recording, 'just the total', v, history, turn='next').kind == 'abstain'
    assert 'state removed' in recording.instructions
    assert 'previous node ids' in recording.instructions


def test_pack_definitions_are_sanitized_before_prompt_disclosure():
    sem = semantics()
    object.__setattr__(sem.contexts['ctx'], 'description', 'Private policy')
    public = bundle(vocabulary(sem))
    assert public['contexts'][0]['definition'] == '(withheld) policy'


def test_large_private_denial_index_is_compiled_once_for_the_whole_bundle(monkeypatch):
    from types import SimpleNamespace
    from kge.services.ask.engine import planmodel
    from kge.semantics import planvocabulary
    planvocabulary.denial_pattern.cache_clear()
    original = planvocabulary.re.compile
    compiled = []

    def counted(*args, **kwargs):
        compiled.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(planvocabulary.re, 'compile', counted)
    denied = tuple('private_' + str(i) for i in range(1000))
    public = planmodel.sanitized({'names': list(denied), 'kept': 'private_1000'}, SimpleNamespace(denied=denied))
    assert public == {'names': ['(withheld)'] * 1000, 'kept': 'private_1000'}
    from dataclasses import replace
    v = replace(vocabulary(), denied=tuple(sorted(denied, key=lambda p: (-len(p), p.casefold()))))
    for text, expected in [('private_999', True), ('private_1000', False), ('public', False)]:
        assert v.denial(text) is expected
    assert len(compiled) == 1


def test_code_name_filter_and_bundle_share_the_private_denial_index(monkeypatch):
    from kge.semantics import planvocabulary

    sem = semantics()
    sem.d.VOCABULARY = [dict(kind='code_name_pair', params=dict(code='u.events.category', name='u.events.source',
        members=[dict(code=str(i), name=label)])) for i, label in enumerate(('Public', 'PRIVATE', 'privateish'))]
    object.__setattr__(sem.contexts['ctx'], 'description', 'private u.events.private Public privateish')
    planvocabulary.denial_pattern.cache_clear()
    original = planvocabulary.re.compile
    compiled = []

    def counted(*args, **kwargs):
        compiled.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(planvocabulary.re, 'compile', counted)
    v = vocabulary(sem)
    assert [row['params']['members'][0]['name'] for row in v.semantics.d.VOCABULARY] == ['Public', 'privateish']
    assert bundle(v)['contexts'][0]['definition'] == '(withheld) (withheld) Public privateish'
    assert v.denial('PRIVATE') and v.denial('u.events.private')
    assert not v.denial('privateish')
    assert len(compiled) == 1


@pytest.mark.parametrize('base_turn', [None, 'previous'])
@pytest.mark.parametrize('fallback', [False, True])
def test_clarification_label_is_a_grounded_choice_without_a_previous_plan(monkeypatch, tmp_path, base_turn, fallback):
    from kge.semantics.plan import Propose
    from kge.semantics.planhistory import clarified_vocabulary
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import setup

    fake, warehouses = setup(monkeypatch, tmp_path, False)
    v = vocabulary(aliases=[('Metric', 'u.events.category')])
    question = 'Total Metric' if fallback else 'Metric'
    history = (HistoryEntry.model_validate({'turn_id': 'previous', 'question': question, 'plan': None,
        'clarification': {'spans': [{'turn': 'previous', 'start': 0, 'end': len(question)}], 'slot': 'meaning',
                          'choices': [{'id': 'u.metric', 'label': 'Metric'}, {'id': 'u.events.category', 'label': 'category'}]},
        'binding': v.binding.model_dump()}),)
    selected = clarified_vocabulary(history, v, 'Metric')
    p = grounded(model(), 'Metric', selected)
    reply = Propose(kind='propose', base_turn=base_turn, plan=p)
    def proposal(question, scoped):
        assert tuple(e.id for e in scoped.candidates(question)) == ('u.metric',)
        return reply
    out = Planner(v, warehouses, Hidden([], set(), [])).answer('Metric', proposal, history=history)
    assert out['kind'] == 'result', out
    fake.con.close()


def test_schema_repair_is_bounded_and_error_payload_does_not_echo_input():
    v = vocabulary()
    p = grounded(model(), 'Metric', v)
    good = {'kind': 'propose', 'base_turn': None, 'plan': p.model_dump(mode='json', by_alias=True)}

    class Scripted:
        def __init__(self, replies):
            self.replies, self.prompts = iter(replies), []

        def json(self, prompt):
            self.prompts.append(prompt)
            return next(self.replies)

    scripted = Scripted([{'sql': 'executable adversary'}, good])
    assert read(scripted, 'Metric', v).kind == 'propose'
    assert len(scripted.prompts) == 2
    assert 'executable adversary' not in scripted.prompts[1]
    scripted = Scripted([{'sql': 'bad'}] * 3)
    assert read(scripted, 'Metric', v).kind == 'abstain'
    assert len(scripted.prompts) == 2


@pytest.mark.parametrize('field', ['measures', 'bindings', 'derived', 'remove_filters', 'filters', 'evidence', 'name-evidence', 'obligations', 'targets', 'spans'])
@pytest.mark.parametrize('scalar', [7, 'wrong-shape'])
def test_scalar_list_fields_reach_schema_retry_on_cloud_path(monkeypatch, field, scalar):
    import kge.pipeline.settings as settings_module
    from tests.unit.test_plan_normalize import dimension, filtered, predicate, proposal

    monkeypatch.setattr(settings_module, 'gcp_native', lambda: True)
    v = filtered()
    question = 'Metric above 30 by category'
    good = proposal(grounded(model(filters=[predicate()], dimensions=[dimension()]), question, v,
                             {'above 30': 'f', 'by': 'd', 'category': 'd'}))
    bad = deepcopy(good)
    plan = bad['plan']
    if field == 'bindings':
        plan['dimensions'][0]['bindings'] = scalar
    elif field == 'remove_filters':
        plan['derived'] = [{'kind': 'share', 'id': 's', 'input': 'm', 'remove_filters': scalar, 'remove_dimensions': [], 'evidence': []}]
    elif field in ('evidence', 'name-evidence'):
        target = plan['filters'][0] if field == 'evidence' else plan['filters'][0]['left']['name']
        target['evidence'] = scalar
    elif field in ('targets', 'spans'):
        plan['obligations'][1][field] = scalar
    else:
        plan[field] = scalar

    class Scripted:
        def __init__(self):
            self.prompts = []

        def json(self, prompt):
            self.prompts.append(json.loads(prompt.split('\n', 1)[1]))
            return deepcopy(bad if len(self.prompts) == 1 else good)

    scripted = Scripted()
    assert read(scripted, question, v).kind == 'propose'
    assert len(scripted.prompts) == 2
    assert any(e['code'] in ('tuple_type', 'list_type') for e in scripted.prompts[1]['errors'])


def test_provider_failure_and_malformed_reply_keep_cloud_no_fallback(monkeypatch):
    import kge.pipeline.settings as settings_module
    monkeypatch.setattr(settings_module, 'gcp_native', lambda: True)

    class Failed:
        def json(self, prompt):
            raise NotJSON('invalid')

    with pytest.raises(NoFallback):
        read(Failed(), 'Metric', vocabulary())


def prior(v):
    p = grounded(model(), 'Metric', v).model_dump(mode='json', by_alias=True)
    for obligation in p['obligations']:
        for span in obligation['spans']:
            span['turn'] = 'previous'
    return HistoryEntry.model_validate({'turn_id': 'previous', 'question': 'Metric', 'plan': p,
                                        'clarification': None, 'binding': v.binding.model_dump()})


def test_history_is_part_of_the_recording_key_and_replayed_followup():
    v = vocabulary()
    history = (prior(v),)
    data = payload('Metric', v, history)
    assert data['recording_key'] != payload('Metric', v)['recording_key']
    reply = read(recorded(), 'Metric', v, history)
    assert reply.kind == 'propose' and reply.base_turn == 'previous'


@pytest.mark.parametrize('change', ['binding', 'plan', 'choice', 'unused', 'clarification_text'])
def test_invalid_history_never_reaches_the_model_even_when_unused(change):
    v = vocabulary()
    data = prior(v).model_dump(mode='json', by_alias=True)
    if change == 'binding':
        data['binding']['release_identity'] = 'stale'
    elif change == 'plan':
        data['plan']['measures'][0]['context']['id'] = 'invented'
    elif change == 'choice':
        data['plan'] = None
        data['clarification'] = {'spans': [], 'slot': 'meaning', 'choices': [{'id': 'private', 'label': 'private'}]}
    elif change == 'clarification_text':
        data['plan'] = None
        data['question'] = 'Metric undisclosed'
        data['clarification'] = {'spans': [{'turn': 'previous', 'start': 0, 'end': 6}], 'slot': 'meaning',
                                 'choices': [{'id': 'u.metric', 'label': 'Metric'}]}
    else:
        data['turn_id'] = 'unused'
        data['question'] = 'Metric private'
    history = (HistoryEntry.model_validate(data),)
    if change == 'unused':
        history += (prior(v),)

    class Uncalled:
        def json(self, prompt):
            pytest.fail('invalid history was disclosed')

    assert read(Uncalled(), 'Metric', v, history).kind == 'abstain'


def test_history_overflow_is_rejected_rather_than_truncated():
    v = vocabulary()
    with pytest.raises(ValueError, match='bounds'):
        payload('Metric', v, (prior(v),) * 9)


def test_trace_records_prompt_and_vocabulary_hashes_without_raw_question_or_history():
    from kge.services.ask.engine import runtrace
    with runtrace.start('ask') as trace:
        runtrace.stage('understand')
        assert read(recorded(), 'Metric', vocabulary()).kind == 'propose'
        out = trace.finish({})
    call = out['llm_calls'][0]
    assert call['prompt_version'] == VERSION
    assert len(call['prompt_hash']) == len(call['context_hash']) == 64
    assert 'Metric' not in json.dumps(out)


def test_recorded_plan_executes_through_checker_compiler_and_gate(monkeypatch, tmp_path):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    v = vocabulary()
    out = Planner(v, warehouses, Hidden([], set(), []), {'ctx': ('source',)}).answer(
        'Metric', lambda q, vocab: read(recorded(), q, vocab))
    assert out['kind'] == 'result'
    assert out['result']['rows'][0]['cells'] == [
        {'value': {'kind': 'decimal', 'value': '15.0'}, 'citations': ['source'], 'null_reasons': []}]
    assert jobs(fake) == (1, 1)


@pytest.mark.parametrize('question', ['Metric sparkle', 'Metric invented context', 'Metric previous period', 'Executable reply'])
def test_recorded_adversaries_produce_no_figure_or_warehouse_jobs(monkeypatch, tmp_path, question):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    out = Planner(vocabulary(), warehouses, Hidden([], set(), [])).answer(
        question, lambda q, vocab: read(recorded(), q, vocab))
    assert out['kind'] == 'abstain'
    assert 'result' not in out and jobs(fake) == (0, 0)


def test_default_unit_attempts_have_distinct_recording_keys_without_changing_history_binding():
    from dataclasses import replace
    original = vocabulary()
    preferred = replace(original, default_units=("u",))
    assert payload("Metric", preferred)["recording_key"] != payload("Metric", original)["recording_key"]
    assert bundle(preferred)["default_units"] == ["u"]
    assert preferred.binding == original.binding


def test_prompt_deletion_state_requires_a_numeric_history_base():
    v = vocabulary()
    def states(history):
        return payload('Metric', v, history)['schema']['$defs']['Obligation']['properties']['state']['enum']
    assert 'removed' not in states(())
    assert 'removed' in states((prior(v),))
