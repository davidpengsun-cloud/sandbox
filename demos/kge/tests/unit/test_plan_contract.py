"""The planner's decisions-only contract (#561): kge/services/ask/engine/plancontract.py and read_on in planmodel.py.

    .venv/bin/python -m pytest tests/unit/test_plan_contract.py -q

The contract mirrors kge/semantics/plan.py field for field, less evidence, and names bare ids; fill() turns it into the
PlanReply the planner checks, with the evidence, obligations, turn ids and base turn the code works out itself. A full
PlanReply, the {reply, readings} envelope and the recorded replies still read as before. A failed contract goes back to
the model as a correction at the field it names, with the ids that field may name. ASK_UNKNOWN_WORD picks the one
sentence on unknown words. Through a stand-in vertex-gemini transport, the request is the contract's schema with each id a
plain string (no enum, #561), the question last, and no recording key, turn id or schema text in the content. Small synthetic vocabularies.
"""
from __future__ import annotations

import json
from typing import Literal, get_args, get_origin

import pytest

from kge.adapters.model import providers
from kge.semantics import plan as declared
from kge.semantics.plan import HistoryEntry, reply_adapter
from kge.services.ask.engine import planmodel, plancontract
from kge.services.ask.engine.modelcall import FieldError
from kge.services.ask.engine.plancontract import PlanContract, bind_ids, fill, locate
from kge.services.ask.engine.planner import unresolved_phrases
from tests.plan_fixtures import grounded, model, name, vocabulary
from tests.plan_recordings import recorded

STEERING = ("MODEL_MAX_TOKENS", "MODEL_HIGH_MAX_TOKENS", "ASK_PLAN_MAX_TOKENS", "ASK_PLAN_THINKING_TOKENS",
            "ASK_PLAN_THINKING_LEVEL", "MODEL_THINKING_TOKENS", "MODEL_HIGH_THINKING_TOKENS", "MODEL_THINKING_LEVEL",
            "MODEL_HIGH_THINKING_LEVEL", "ASK_MODEL_CORRECTIONS", "ASK_UNKNOWN_WORD", "MODEL_SEED",
            "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE")
OLD_SENTENCES = (
    'Map a word the vocabulary does not list to the closest declared meaning and give that reading.',
    'obligations is how the rules read each phrase: keep those readings, including every reporting-basis word, exactly as they are given.',
    'errors, when present, are the checks the previous plan failed: correct them.',
)
TIME = {'kind': 'all_available', 'calendar': 'cal'}
MEASURE = {'kind': 'measure', 'id': 'm1', 'measure': 'u.metric', 'context': 'ctx'}


@pytest.fixture(autouse=True)
def plain(monkeypatch):
    """No budget, thinking, correction or backend setting from the caller's environment steers a test."""
    for setting in STEERING:
        monkeypatch.delenv(setting, raising=False)
    monkeypatch.setattr(providers.Chat, "token", lambda self: ("stand-in", "your-project-id"))


class Json:
    """A stand-in that takes the single-text prompt alone, as the recordings' models do; it keeps what it was sent."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def json(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)

    def sent(self, index=-1):
        return json.loads(self.prompts[index].split('\n', 1)[1])


def enum_values(node):
    """Every value any enum in a schema lists."""
    if isinstance(node, dict):
        return set(node.get('enum') or ()) | {v for child in node.values() for v in enum_values(child)}
    if isinstance(node, list):
        return {v for child in node for v in enum_values(child)}
    return set()


def gemini(*replies, finish='STOP'):
    """A vertex-gemini Gemini 3 model on its high tier whose transport answers each call with the next reply."""
    llm = providers.LLM("vertex-gemini", "gemini-3.1-pro-preview", project="your-project-id", region="global", tier="high")
    queue, sent = list(replies), []

    def post(url, headers, body):
        sent.append(json.loads(json.dumps(body)))
        reply = queue.pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}],
                "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 40, "thoughtsTokenCount": 200}}

    llm._post = post
    llm.sent = sent
    return llm


def propose(**plan):
    return {'decision': 'propose', 'plan': {'measures': [MEASURE], 'time': TIME, **plan}}


def members(union):
    return get_args(get_args(union)[0])


MIRRORED = (*members(declared.Predicate), *members(declared.Comparison), *members(declared.Derived),
            *members(declared.Dimension), *members(declared.Time), *members(declared.Operand),
            *members(declared.MeasureOutput), declared.DimensionBinding, declared.Period, declared.Order)


@pytest.mark.parametrize('model_class', MIRRORED, ids=lambda m: m.__name__)
def test_every_plan_record_has_a_contract_record_with_its_fields_less_evidence(model_class):
    mirror = getattr(plancontract, model_class.__name__)
    assert plancontract.MIRRORS[mirror] is model_class
    assert set(mirror.model_fields) == set(model_class.model_fields) - {'evidence'}
    for field, info in mirror.model_fields.items():
        original = model_class.model_fields[field].annotation
        if get_origin(original) is Literal:
            assert get_args(info.annotation) == get_args(original), field
        assert ('Name' in repr(original)) == (field in plancontract.NAME_FIELDS), field


def test_the_contract_plan_is_the_query_plan_less_its_schema_and_obligations():
    assert set(plancontract.ContractPlan.model_fields) == set(declared.QueryPlan.model_fields) - {'schema_version', 'obligations'}
    assert plancontract.ContractPlan.model_validate({'measures': [MEASURE], 'time': TIME}).layout == 'aligned'


def test_a_proposal_needs_its_plan_and_a_clarification_its_alternatives():
    with pytest.raises(ValueError, match='plan'):
        PlanContract.model_validate({'decision': 'propose'})
    with pytest.raises(ValueError, match='clarify'):
        PlanContract.model_validate({'decision': 'clarify'})
    assert PlanContract.legacy({'kind': 'propose'}) and PlanContract.legacy({'reply': {}, 'readings': []})
    assert not PlanContract.legacy(propose())


def test_fill_gives_each_id_its_spans_each_node_its_readings_and_the_obligations():
    v = vocabulary()
    question = 'Metric value above 6 sparkly'
    contract = PlanContract.model_validate({
        **propose(filters=[{'kind': 'compare', 'id': 'f1', 'left': {'kind': 'column', 'name': 'u.events.value'}, 'op': 'gt',
                            'value': {'kind': 'integer', 'value': 6}}]),
        'unread': ['sparkly'], 'readings': [{'phrase': 'value above 6', 'id': 'u.events.value'},
                                            {'phrase': 'Metric', 'id': 'u.metric'}]})
    reply = fill(contract, question, v, 't9')
    plan = reply['plan']
    assert reply['kind'] == 'propose' and reply['base_turn'] is None and plan['schema'] == 'query-plan/v1'
    assert plan['measures'][0]['measure'] == {'id': 'u.metric', 'evidence': [{'turn': 't9', 'start': 0, 'end': 6}]}
    assert plan['filters'][0]['evidence'] == [{'turn': 't9', 'start': 7, 'end': 20}]
    assert 'path' not in plan['filters'][0]['left']
    assert [(o['id'], o['state'], o['targets']) for o in plan['obligations']] == [
        ('o1', 'applied', ['m1']), ('o2', 'applied', ['f1']), ('o3', 'applied', ['f1']), ('o4', 'unresolved', [])]
    assert plan['obligations'][2]['spans'] == [{'turn': 't9', 'start': 7, 'end': 20}]
    assert {s['turn'] for o in plan['obligations'] for s in o['spans']} == {'t9'}
    planmodel._routes(plan, v)
    checked = reply_adapter.validate_python(reply)
    assert checked.plan.filters[0].left.path == ()
    assert unresolved_phrases(question, checked.plan, 't9', [{'phrase': 'Metric', 'id': 'u.metric'}]) == ['sparkly']


def test_a_reading_no_name_piece_covers_becomes_an_applied_obligation():
    v = vocabulary()
    contract = PlanContract.model_validate({**propose(dimensions=[{'kind': 'column', 'id': 'd1', 'bindings': [
        {'context': 'ctx', 'column': 'u.events.category'}]}]), 'readings': [{'phrase': 'kind of event', 'id': 'u.events.category'}]})
    obligations = fill(contract, 'Metric by kind of event', v, 'turn')['plan']['obligations']
    assert {'id': 'o2', 'spans': [{'turn': 'turn', 'start': 10, 'end': 23}], 'kind': 'name', 'targets': ['d1'],
            'state': 'applied'} in obligations


def test_clarify_and_abstain_fill_to_their_replies():
    v = vocabulary()
    clarify = PlanContract.model_validate({'decision': 'clarify', 'clarify': {'phrase': 'thing', 'candidates': ['u.metric', 'u.events.value']}})
    assert fill(clarify, 'Total thing', v, 'turn') == {'kind': 'clarify', 'spans': [{'turn': 'turn', 'start': 6, 'end': 11}],
        'candidates': [{'id': 'u.metric', 'evidence': []}, {'id': 'u.events.value', 'evidence': []}]}
    unread = PlanContract.model_validate({'decision': 'abstain', 'unread': ['sparkly']})
    assert fill(unread, 'Metric sparkly', v, 'turn') == {'kind': 'abstain', 'spans': [{'turn': 'turn', 'start': 7, 'end': 14}],
                                                        'missing': ["'sparkly' matches no declared meaning"]}
    stated = PlanContract.model_validate({'decision': 'abstain', 'missing': ['No such figure.']})
    assert fill(stated, 'Metric', v, 'turn')['missing'] == ['No such figure.']
    bare = PlanContract.model_validate({'decision': 'abstain'})
    assert fill(bare, 'Metric', v, 'turn')['missing'] == ['The model could not plan the question.']


def history_of(v, plan, question):
    data = grounded(plan, question, v, {'by': 'd', 'category': 'd'}).model_dump(mode='json', by_alias=True)
    for obligation in data['obligations']:
        for span in obligation['spans']:
            span['turn'] = 'previous'
    return (HistoryEntry.model_validate({'turn_id': 'previous', 'question': question, 'plan': data,
                                         'clarification': None, 'binding': v.binding.model_dump()}),)


def test_a_follow_up_continues_the_plan_with_its_base_turn_and_obligations_for_what_changed():
    from kge.semantics.planhistory import replacement
    v = vocabulary()
    history = history_of(v, model(), 'Metric')
    contract = PlanContract.model_validate({
        'decision': 'propose', 'continues': True, 'readings': [{'phrase': 'category', 'id': 'u.events.category'}],
        'plan': {'measures': [{**MEASURE, 'id': 'm'}], 'time': TIME,
                 'dimensions': [{'kind': 'column', 'id': 'd1', 'bindings': [{'context': 'ctx', 'column': 'u.events.category'}]}]}})
    reply = fill(contract, 'Metric by category', v, 't2', history)
    assert reply['base_turn'] == 'previous'
    assert any(o['targets'] == ['d1'] and o['state'] == 'applied' for o in reply['plan']['obligations'])
    planmodel._routes(reply['plan'], v)
    checked = reply_adapter.validate_python(reply)
    assert replacement(checked, history, v, 'Metric by category', 't2') == (None, ())
    assert fill(contract.model_copy(update={'continues': False}), 'Metric by category', v, 't2', history)['base_turn'] is None


def test_a_follow_up_removal_is_a_removed_obligation_on_its_words():
    from kge.semantics.planhistory import replacement
    v = vocabulary()
    dimension = {'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'),
                                                            'path': [], 'level': None}]}
    history = history_of(v, model(dimensions=[dimension]), 'Metric by category')
    contract = PlanContract.model_validate({'decision': 'propose', 'continues': True,
                                            'plan': {'measures': [{**MEASURE, 'id': 'm'}], 'time': TIME},
                                            'removed': [{'node': 'd', 'phrase': 'just the total'}]})
    reply = fill(contract, 'just the total', v, 't2', history)
    assert reply['plan']['obligations'] == [{'id': 'o1', 'spans': [{'turn': 't2', 'start': 0, 'end': 14}],
                                             'kind': 'dimension', 'targets': ['d'], 'state': 'removed'}]
    assert replacement(reply_adapter.validate_python(reply), history, v, 'just the total', 't2') == (None, ())


def test_read_on_reads_a_contract_and_fills_a_route_left_out():
    v = vocabulary()
    double = Json(propose(dimensions=[{'kind': 'column', 'id': 'd1', 'bindings': [{'context': 'ctx', 'column': 'u.events.category'}]}]))
    reading = planmodel.read_on(double, 'Metric by category', v)
    assert reading.reply.kind == 'propose' and reading.problems == ()
    assert reading.reply.plan.dimensions[0].bindings[0].path == ()
    assert reading.reply.plan.dimensions[0].bindings[0].column.evidence[0].start == 10
    assert reading.contract and reading.ids == tuple(sorted(reading.ids))


def test_a_full_reply_the_envelope_and_the_recorded_replies_still_read():
    v = vocabulary()
    full = {'kind': 'propose', 'base_turn': None, 'plan': grounded(model(), 'Metric', v).model_dump(mode='json', by_alias=True)}
    assert planmodel.read_on(Json(full), 'Metric', v).reply.kind == 'propose'
    envelope = planmodel.read_on(Json({'reply': full, 'readings': [{'phrase': 'Metric', 'id': 'u.metric'},
                                                                  {'phrase': 'x', 'id': 'undeclared'}]}), 'Metric', v)
    assert envelope.reply.kind == 'propose' and envelope.readings == ({'phrase': 'Metric', 'id': 'u.metric'},)
    for question, kind in [('Metric', 'propose'), ('Which metric?', 'clarify'), ('Unavailable', 'abstain')]:
        assert planmodel.read_on(recorded(), question, v).reply.kind == kind


def test_the_legacy_text_keeps_its_key_and_carries_the_contract_schema():
    v = vocabulary()
    double = Json(propose())
    planmodel.read_on(double, 'Metric', v, turn='turn-7f3a')
    sent = double.sent()
    assert double.prompts[0].startswith(planmodel.ON_VERSION + '\n')
    assert sent['recording_key'] == planmodel.payload('Metric', v)['recording_key'] and sent['turn_id'] == 'turn-7f3a'
    assert sent['schema']['$defs']['ContractReading']['properties']['id']['type'] == 'string'
    assert not enum_values(sent['schema']) & {n['id'] for n in sent['vocabulary']['names']}
    assert sent['errors'] == [] and 'decision is propose' in sent['instructions']


def test_bind_ids_binds_words_to_the_one_declared_id_of_the_fields_kind():
    v = vocabulary()
    raw = propose(dimensions=[{'kind': 'column', 'id': 'd1', 'bindings': [{'context': 'Context one', 'column': 'category',
                                                                          'path': []}]}])
    raw['plan']['measures'] = [{**MEASURE, 'measure': 'Metric', 'context': 'Metric'}]
    bound = bind_ids(raw, v)
    assert bound['plan']['measures'][0]['measure'] == 'u.metric'
    assert bound['plan']['measures'][0]['context'] == 'Metric'
    assert bound['plan']['dimensions'][0]['bindings'][0] == {'context': 'ctx', 'column': 'u.events.category', 'path': []}
    legacy = {'kind': 'propose', 'plan': {'measures': [{'measure': 'Metric'}]}}
    assert bind_ids(legacy, v) == legacy


def test_locate_puts_a_problem_at_the_field_holding_the_id_it_names():
    v = vocabulary()
    ids = sorted(v.entries)
    text = json.dumps(propose(dimensions=[{'kind': 'column', 'id': 'd1', 'bindings': [{'context': 'ctx', 'column': 'u.events.source'}]}]))
    located = locate("Column 'u.events.source' cannot group this context.", text, v, ids)
    assert located.field == '/plan/dimensions/0/bindings/0/column'
    assert set(located.allowed) == {i for i in ids if v.entries[i].kind == 'column'}
    assert locate('Dimension d1 is not supported.', text, v, ids) == FieldError('/plan/dimensions/0', 'Dimension d1 is not supported.')
    assert locate('No permitted runtime.', text, v, ids) == FieldError('', 'No permitted runtime.')
    assert locate('No permitted runtime.', '', v, ids) == FieldError('', 'No permitted runtime.')


def test_a_failed_contract_is_corrected_at_its_field_with_the_ids_it_may_name():
    v = vocabulary()
    bad = propose()
    bad['plan']['measures'] = [{**MEASURE, 'context': 'undeclared'}]
    reading = planmodel.read_on(Json(bad), 'Metric', v)
    assert reading.reply is None and reading.problems == ('/plan/measures/0/context: Value error, not a declared id',)
    assert reading.errors == ({'field': '/plan/measures/0/context', 'error': 'Value error, not a declared id', 'allowed': ['ctx']},)
    correction = planmodel.correction(reading, ('Some check failed.',), v)
    assert correction.previous == reading.contract and json.loads(correction.previous) == bad
    assert correction.errors == (FieldError('/plan/measures/0/context', 'Value error, not a declared id', ('ctx',)),
                                 FieldError('', 'Some check failed.'))
    again = Json(propose())
    assert planmodel.read_on(again, 'Metric', v, corrections=(correction,)).reply.kind == 'propose'
    assert again.sent()['errors'] == [{'problem': 'Value error, not a declared id'}, {'problem': 'Some check failed.'}]


def test_a_plan_check_failure_after_fill_is_put_at_the_contract_field():
    v = vocabulary()
    hierarchy = propose(dimensions=[{'kind': 'hierarchy', 'id': 'd1', 'bindings': [{'context': 'ctx', 'column': 'u.events.category', 'path': []}]}])
    reading = planmodel.read_on(Json(hierarchy), 'Metric by category', v)
    assert reading.reply is None
    assert reading.errors[0]['field'] == '/plan/dimensions/0'
    assert 'hierarchies require a level' in reading.problems[0]


def test_a_reading_round_trips_through_its_dump():
    reading = planmodel.Reading(None, problems=('p',), schema='abc', truncated=True, more_room=True, contract='{}',
                                replay={'parts': [{'text': '{}'}]}, errors=[{'field': '/x', 'error': 'e', 'allowed': ['a']}],
                                ids=('a', 'b'), attempt=1)
    held = json.loads(json.dumps(reading.dump()))
    assert planmodel.Reading.load(held).dump() == reading.dump()
    older = {'reply': None, 'readings': [], 'problems': ['p'], 'schema': 's', 'unavailable': False}
    assert planmodel.Reading.load(older).truncated is False and planmodel.Reading.load(older).errors == ()


@pytest.mark.parametrize('rule,other', [('closest', 'abstain'), ('abstain', 'closest')])
def test_ask_unknown_word_picks_the_one_sentence_on_unknown_words(monkeypatch, rule, other):
    monkeypatch.setenv('ASK_UNKNOWN_WORD', rule)
    double = Json(propose())
    planmodel.read_on(double, 'Metric', vocabulary())
    instructions = double.sent()['instructions']
    assert planmodel.UNKNOWN[rule] in instructions and planmodel.UNKNOWN[other] not in instructions
    assert not any(sentence in instructions for sentence in OLD_SENTENCES)
    assert planmodel.on_instructions() == planmodel.on_instructions(rule)


def test_the_prompt_version_follows_the_unknown_word_rule(monkeypatch):
    assert planmodel.on_prompt_version() == planmodel.ON_PROMPT_VERSION
    monkeypatch.setenv('ASK_UNKNOWN_WORD', 'abstain')
    assert planmodel.on_prompt_version() != planmodel.ON_PROMPT_VERSION
    monkeypatch.setenv('ASK_UNKNOWN_WORD', 'maybe')
    with pytest.raises(ValueError, match='ASK_UNKNOWN_WORD'):
        planmodel.on_instructions()


def test_read_on_sends_the_contract_schema_and_the_question_last_and_no_prompt_internals():
    """On Gemini 3 the planner is sent in JSON mode (#561): the contract's schema follows the instruction."""
    v = vocabulary()
    examples = ({'question': 'Metric', 'plan': grounded(model(), 'Metric', v).model_dump(mode='json', by_alias=True)},)
    llm = gemini(propose())
    reading = planmodel.read_on(llm, 'Metric by category', v, turn='turn-7f3a', examples=examples)
    assert reading.reply.kind == 'propose'
    body, = llm.sent
    instruction = body['systemInstruction']['parts'][0]['text']
    head, shown = instruction.split('\n\n' + providers.JSON_MODE_SCHEMA + '\n', 1)
    assert head == planmodel.on_instructions()
    content = body['contents'][0]['parts'][0]['text']
    assert content.endswith('## Question\nMetric by category')
    assert content.index('## Vocabulary') < content.index('## Examples') < content.index("## The rules' readings") < content.index('## Question')
    for internal in ('recording_key', 'turn-7f3a', planmodel.ON_VERSION, '"schema"', 'evidence', 'obligations', '"errors"'):
        assert internal not in content
    assert '{"question":"Metric","plan":{"layout":"aligned","measures":[{"kind":"measure","id":"m","measure":"u.metric",' in content
    config = body['generationConfig']
    assert config['responseMimeType'] == 'application/json' and 'temperature' not in config
    assert config['maxOutputTokens'] == 16384 and config['thinkingConfig'] == {'thinkingLevel': 'low'}
    assert 'responseJsonSchema' not in config
    schema = json.loads(shown)
    assert 'VocabularyId' not in schema.get('$defs', {}) and 'ReadingId' not in schema.get('$defs', {})
    assert not enum_values(schema) & set(reading.ids), 'the ids are in the data, not the schema'
    assert all(i in content for i in reading.ids)
    assert set(schema['properties']) == {'decision', 'continues', 'plan', 'clarify', 'missing', 'unread', 'readings', 'removed'}


def test_a_reply_cut_off_at_its_budget_is_a_truncated_reading_and_a_note_to_think_less(monkeypatch):
    monkeypatch.setenv('ASK_PLAN_THINKING_TOKENS', '1024')
    v = vocabulary()
    llm = gemini('{"decision": "propose", "plan": {"measu', finish='MAX_TOKENS')
    reading = planmodel.read_on(llm, 'Metric', v)
    assert reading.reply is None and reading.truncated and reading.more_room
    assert '16384-token budget' in reading.problems[0] and 'not JSON' not in reading.problems[0]
    correction = planmodel.correction(reading, reading.problems, v)
    assert correction.truncated
    llm = gemini(propose())
    assert planmodel.read_on(llm, 'Metric', v, corrections=(correction,)).reply.kind == 'propose'
    body, = llm.sent
    assert body['generationConfig']['thinkingConfig'] == {'thinkingBudget': 512}
    assert [c['role'] for c in body['contents']] == ['user']
    assert 'Think less' in body['contents'][0]['parts'][0]['text']


def test_a_correction_continues_the_conversation_with_the_previous_contract():
    v = vocabulary()
    bad = propose()
    bad['plan']['measures'] = [{**MEASURE, 'context': 'undeclared'}]
    first = gemini(bad)
    reading = planmodel.read_on(first, 'Metric', v)
    second = gemini(propose())
    assert planmodel.read_on(second, 'Metric', v, corrections=(planmodel.correction(reading, (), v),)).reply.kind == 'propose'
    contents = second.sent[0]['contents']
    assert [c['role'] for c in contents] == ['user', 'model', 'user']
    assert json.loads(contents[1]['parts'][0]['text']) == bad
    assert contents[2]['parts'][0]['text'].endswith('- /plan/measures/0/context: Value error, not a declared id Allowed: ctx')


def test_the_full_plan_prompt_shows_the_turn_its_spans_name_before_the_question():
    from kge.ports import ModelReply

    class Capture:
        def __init__(self):
            self.requests = []

        def structured(self, request):
            self.requests.append(request)
            return ModelReply(data={'kind': 'abstain', 'spans': [], 'missing': ['none']}, text='{}')

    double = Capture()
    planmodel.read(double, 'Metric', vocabulary(), turn='turn-7f3a')
    content = double.requests[0].content
    assert '## This turn\n{"turn_id":"turn-7f3a"}' in content
    assert content.index('## This turn') < content.index('## Question\nMetric') and content.endswith('## Question\nMetric')


def test_a_plan_field_with_more_ids_of_its_kind_than_a_correction_lists_offers_those_nearest_the_reply():
    """#561: above MAX_ALLOWED, the ids of the field's kinds nearest the one the model gave, and how many there are."""
    from types import SimpleNamespace
    from kge.services.ask.engine.modelcall import MAX_ALLOWED
    from kge.services.ask.engine.plancontract import id_error
    entries = {f"u.measure_{i:03d}": SimpleNamespace(kind='measure') for i in range(120)}
    entries.update({f"u.context_{i:03d}": SimpleNamespace(kind='context') for i in range(120)})
    entries['wealth.net_new_assets'] = SimpleNamespace(kind='measure')
    error = id_error('/plan/measures/0/measure', 'not a declared id', 'measure', tuple(entries),
                     SimpleNamespace(entries=entries), 'wealth.nna')
    assert error.allowed[0] == 'wealth.net_new_assets' and len(error.allowed) == MAX_ALLOWED and error.of == 121
    assert not any(a.startswith('u.context_') for a in error.allowed)


def test_a_field_error_that_names_no_id_is_narrowed_to_its_field_as_written_with_no_union_tag():
    from pydantic import ValidationError
    from kge.services.ask.engine.modelcall import contract_schema, validation_errors
    reply = {"decision": "propose", "plan": {"time": {"kind": "single", "period": {"kind": "month", "value": 7}}}}
    with pytest.raises(ValidationError) as caught:
        PlanContract.model_validate(reply, context={"ids": frozenset()})
    errors = validation_errors(caught.value, contract_schema(PlanContract))
    assert "/plan/time/single/period/value" in [e.field for e in errors], "pydantic's location carries the union's tag"
    fields = [e.field for e in planmodel.narrowed(errors, json.dumps(reply), None, ())]
    assert "/plan/time/period/value" in fields
    assert not any("/single/" in f for f in fields), "a correction names the field as the model wrote it"
