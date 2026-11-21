"""AI-Generated mode's corrections (#561): a model plan that fails goes back to the model as a correction that continues
the conversation, up to ASK_MODEL_CORRECTIONS times (two by default, so at most three calls), and only where a
correction can help.

    .venv/bin/python -m pytest tests/unit/test_ask_model_corrections.py -q

Each case asks the planner through a stand-in vertex-gemini gemini-3.1-pro-preview model: the adapter builds the real
request body and a stand-in transport keeps it and answers with scripted Gemini replies, so nothing leaves the process.
Sent back: a reply that fails the decisions-only contract, a reply cut off at its budget while thinking less leaves the
contract more room, a check_plan problem that is not a refusal, a compiler or release error, and a static SQL-gate
failure other than restricted personal data or a dry-run failure. Never sent back: an access refusal, a restricted
column, the cost gate, a partition, no data for the period, and the model's own abstain or clarify.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kge.adapters.model import LLM, model_for
from kge.adapters.model.providers import JSON_MODE_SCHEMA, Chat
from kge.semantics.plan import HistoryEntry, Propose
from kge.services.ask.engine import planmodel, runtrace
from kge.services.ask.engine import planner as planning
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, forget_dry_run, warehouse_guard
from kge.services.ask.engine.planner import Planner, retryable
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, metric_vocabulary as vocabulary, model
from tests.unit.test_planner_jobs import setup

PRO = 'gemini-3.1-pro-preview'
CLEARED = (*(f'ASK_{call}_{part}' for call in ('PLAN', 'CLASSIFY', 'WORDING')
             for part in ('MAX_TOKENS', 'THINKING_TOKENS', 'THINKING_LEVEL')),
           *(f'MODEL{tier}_THINKING_{part}' for tier in ('', '_HIGH', '_MEDIUM', '_LOW') for part in ('TOKENS', 'LEVEL')),
           'ASK_UNKNOWN_WORD', 'ASK_MODEL_CORRECTIONS', 'MODEL_MAX_TOKENS', 'MODEL_HIGH_MAX_TOKENS', 'MODEL_NAME',
           'MODEL_SEED', 'VERTEX_REGION', 'VERTEX_PROJECT')
GOOD = {'decision': 'propose', 'readings': [{'phrase': 'Metric', 'id': 'u.metric'}],
        'plan': {'measures': [{'kind': 'measure', 'id': 'm', 'measure': 'u.metric', 'context': 'ctx'}],
                 'time': {'kind': 'all_available', 'calendar': 'cal'}}}
UNDECLARED = {**GOOD, 'plan': {**GOOD['plan'], 'measures': [{**GOOD['plan']['measures'][0], 'context': 'nowhere'}]}}


def reply(contract=None, finish='STOP', thoughts=300, contract_tokens=40):
    """One Gemini generateContent reply: a thought part, then the contract as text (none when contract is None)."""
    parts = [{'text': 'thinking it through', 'thought': True}]
    if contract is not None:
        parts.append({'text': contract if isinstance(contract, str) else json.dumps(contract), 'thoughtSignature': 'c2ln'})
    return {'candidates': [{'content': {'role': 'model', 'parts': parts}, 'finishReason': finish}],
            'modelVersion': PRO + '-001',
            'usageMetadata': {'promptTokenCount': 900, 'candidatesTokenCount': contract_tokens, 'thoughtsTokenCount': thoughts}}


class Gemini:
    """The planner's model: a real vertex-gemini LLM whose transport keeps each request body and answers in turn."""

    def __init__(self, *replies):
        self.llm = model_for('answer')
        assert (self.llm.provider, self.llm.model) == ('vertex-gemini', PRO)
        self.replies, self.bodies = list(replies), []
        self.llm._post = self.post

    def post(self, url, headers, body):
        self.bodies.append(body)
        return self.replies.pop(0)

    @property
    def calls(self):
        return len(self.bodies)


@pytest.fixture
def bench(monkeypatch, tmp_path):
    def no_network(*a, **k):
        raise AssertionError('a model call reached the network')
    monkeypatch.setattr(LLM, '_post', staticmethod(no_network))
    monkeypatch.setattr('google.auth.default', no_network)
    monkeypatch.setattr(Chat, 'token', lambda self: ('ya29.stand-in', 'pilot-project'))
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('MODEL_PROVIDER', 'none')
    monkeypatch.setenv('MODEL_HIGH_PROVIDER', 'vertex-gemini')
    monkeypatch.setenv('MODEL_HIGH_NAME', PRO)
    monkeypatch.setenv('ANSWER_MODEL_TIER', 'high')
    vocab = vocabulary()
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    yield vocab, Planner(vocab, warehouses, Hidden([], set(), []))
    fake.con.close()


def rules_of(vocab, text='Metric'):
    def rules(question, vocabulary):
        return Propose(kind='propose', base_turn=None, plan=grounded(model(), text, vocab))
    return rules


def declining(question, vocabulary):
    from kge.semantics.plan import Abstain
    return Abstain(kind='abstain', spans=(), missing=('The rules have no reading.',))


def asked(planner, vocab, question, gemini, floor, history=(), turn='turn'):
    def read(corrections):
        return planmodel.read_on(gemini.llm, question, vocab, history, turn, corrections=corrections)
    return planner.answer_model(question, read, floor, turn, history)


def config(body):
    return body['generationConfig']


def gate_refusal(monkeypatch, failed_at, problem):
    """The gate refuses every statement with problem, put through warehouse_guard as execute_on_runtime puts it."""
    monkeypatch.setattr(planning, 'execute_on_runtime', lambda *a, **k: (
        warehouse_guard({'passed': False, 'failed_at': failed_at, 'problems': [problem]}), None, {}))


def test_the_first_request_sends_the_contract_schema_the_instruction_apart_and_the_question_last(bench):
    vocab, planner = bench
    gemini = Gemini(reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result and gemini.calls == 1
    body = gemini.bodies[0]
    instruction = body['systemInstruction']['parts'][0]['text']
    head, shown = instruction.split('\n\n' + JSON_MODE_SCHEMA + '\n', 1)
    assert head == planmodel.on_instructions(), 'on Gemini 3 the planner is sent in JSON mode (#561)'
    assert [c['role'] for c in body['contents']] == ['user']
    content = body['contents'][0]['parts'][0]['text']
    assert content.startswith('## Vocabulary\n') and content.endswith('## Question\nMetric')
    assert 'recording_key' not in content and 'turn_id' not in content
    schema = json.loads(shown)
    assert 'responseJsonSchema' not in config(body)
    assert config(body)['responseMimeType'] == 'application/json' and schema['required'] == ['decision']
    assert 'u.metric' not in json.dumps(schema) and 'u.metric' in content and 'ctx' in content
    assert (config(body)['maxOutputTokens'], config(body)['thinkingConfig']) == (16384, {'thinkingLevel': 'low'})
    assert 'temperature' not in config(body)


def test_an_undeclared_context_goes_back_as_the_models_own_turn_then_the_errors_with_the_allowed_ids(bench):
    vocab, planner = bench
    gemini = Gemini(reply(UNDECLARED), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert result['kind'] == 'result' and 'model_refusal' not in result and gemini.calls == 2
    first, second = gemini.bodies
    assert second['contents'][0] == first['contents'][0]
    assert [c['role'] for c in second['contents']] == ['user', 'model', 'user']
    assert [p['text'] for p in second['contents'][1]['parts']] == [json.dumps(UNDECLARED)]
    assert all(not p.get('thought') for p in second['contents'][1]['parts']), 'thought text is not replayed'
    note = second['contents'][2]['parts'][0]['text']
    assert '/plan/measures/0/context' in note and 'Allowed: ctx' in note
    assert second['systemInstruction'] == first['systemInstruction']


def test_three_bad_replies_are_exactly_three_calls_then_the_rules_floor_answers(bench):
    vocab, planner = bench
    gemini = Gemini(reply(UNDECLARED), reply(UNDECLARED), reply(UNDECLARED))
    result = asked(planner, vocab, 'Metric', gemini, rules_of(vocab))
    assert gemini.calls == 3
    assert [len(b['contents']) for b in gemini.bodies] == [1, 3, 5], 'each correction continues the conversation'
    assert result['kind'] == 'result' and result['model_refusal']['fallback'] == 'rules'
    assert result['model_refusal']['phase'] == 'contract'


@pytest.mark.parametrize('corrections, calls', [('0', 1), ('1', 2)])
def test_ask_model_corrections_sets_how_many_corrections_are_sent(bench, monkeypatch, corrections, calls):
    vocab, planner = bench
    monkeypatch.setenv('ASK_MODEL_CORRECTIONS', corrections)
    gemini = Gemini(*(reply(UNDECLARED) for _ in range(3)))
    result = asked(planner, vocab, 'Metric', gemini, rules_of(vocab))
    assert gemini.calls == calls and result['model_refusal']['fallback'] == 'rules'


def test_a_plan_naming_a_unit_the_asker_cannot_read_is_refused_after_one_call(bench):
    vocab, served = bench
    planner = Planner(vocab, served.warehouses, Hidden([], set(), []), readable_units={'elsewhere'})
    gemini = Gemini(reply(GOOD), reply(GOOD), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 1
    assert result['model_refusal']['kind'] == 'refuse' and result['model_refusal']['fallback'] is None


def test_a_denied_question_is_refused_with_no_call_and_no_correction(bench):
    _, served = bench
    vocab = vocabulary(denied_phrases=('salary',))
    planner = Planner(vocab, served.warehouses, Hidden([], set(), []))
    gemini = Gemini(reply(GOOD))
    result = asked(planner, vocab, 'Metric salary', gemini, declining)
    assert gemini.calls == 0 and result['kind'] != 'result'


@pytest.mark.parametrize('failed_at, problem', [
    ('static', 'column events.private is restricted personal data'),
    ('cost gate', 'The query would read more bytes than the runtime allows.'),
    ('partition', 'The query reads no partition the policy allows.'),
    ('execute', 'The requested data could not be read.')])
def test_a_restricted_column_the_cost_gate_a_partition_or_an_outage_is_never_sent_back(bench, monkeypatch, failed_at, problem):
    vocab, planner = bench
    gate_refusal(monkeypatch, failed_at, problem)
    gemini = Gemini(reply(GOOD), reply(GOOD), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 1
    assert result['kind'] != 'result' and result['model_refusal']['guard']['failed_at'] == failed_at


@pytest.mark.parametrize('failed_at, problem', [
    ('static', 'The statement reads a table outside the gate.'),
    ('dry run', 'dry run failed: Unrecognized name: region_code at [1:8]')])
def test_a_static_gate_failure_or_a_dry_run_failure_of_the_query_is_sent_back(bench, monkeypatch, failed_at, problem):
    vocab, planner = bench
    gate_refusal(monkeypatch, failed_at, problem)
    gemini = Gemini(reply(GOOD), reply(GOOD), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 3
    told = gemini.bodies[1]['contents'][2]['parts'][0]['text']
    assert problem.removeprefix('dry run failed: ') in told, 'the model is told the exact error'
    if failed_at == 'dry run':
        assert result['model_refusal']['guard']['problems'] == ['dry run failed: ' + WAREHOUSE_PROBLEM]
        assert 'region_code' not in json.dumps(result), "the warehouse's words never reach the reply"


@pytest.mark.parametrize('problem', ['dry run failed: 403 Access Denied: Table p:d.t: User does not have permission',
                                     'dry run failed: 503 Service Unavailable', 'dry run failed: Not found: Table p:d.t'])
def test_a_dry_run_the_warehouse_failed_for_its_own_reasons_is_never_sent_back(bench, monkeypatch, problem):
    vocab, planner = bench
    gate_refusal(monkeypatch, 'dry run', problem)
    gemini = Gemini(reply(GOOD), reply(GOOD), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 1 and result['model_refusal']['guard']['failed_at'] == 'dry run'


def test_a_prose_reply_goes_back_as_the_models_own_turn_on_gemini(bench):
    vocab, planner = bench
    gemini = Gemini(reply('I would read Metric as u.metric.'), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 2 and result['kind'] == 'result'
    model_turn = gemini.bodies[1]['contents'][1]
    assert model_turn['role'] == 'model'
    assert model_turn['parts'] == [{'text': 'I would read Metric as u.metric.', 'thoughtSignature': 'c2ln'}]


def test_no_data_for_the_period_is_never_sent_back(bench, monkeypatch):
    vocab, planner = bench
    monkeypatch.setattr(planning, 'empty_to_date_reason', lambda *a: 'The period has no released data yet.')
    gemini = Gemini(reply(GOOD), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 1 and result['model_refusal']['phase'] == 'observations'


def test_a_compile_error_goes_back_and_the_corrected_plan_answers(bench, monkeypatch):
    vocab, planner = bench
    compile_plan, failed = planning.compile_plan, []

    def once(*args, **kwargs):
        if not failed:
            failed.append(True)
            raise ValueError('The breakdown has no declared route.')
        return compile_plan(*args, **kwargs)
    monkeypatch.setattr(planning, 'compile_plan', once)
    gemini = Gemini(reply(GOOD), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 2 and result['kind'] == 'result' and 'model_refusal' not in result
    assert 'The breakdown has no declared route.' in gemini.bodies[1]['contents'][2]['parts'][0]['text']


def test_the_models_own_abstain_or_clarify_is_never_sent_back(bench):
    vocab, planner = bench
    gemini = Gemini(reply({'decision': 'abstain', 'missing': ['Metric of what?']}), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 1 and result['kind'] != 'result'


def test_a_reply_cut_off_at_its_budget_is_truncated_never_not_json_and_each_retry_thinks_half_as_much(bench, monkeypatch):
    vocab, planner = bench
    monkeypatch.setenv('ASK_PLAN_THINKING_TOKENS', '1024')
    gemini = Gemini(reply(None, 'MAX_TOKENS', 16000), reply('{"decision": "pro', 'MAX_TOKENS', 16000), reply(None, 'MAX_TOKENS'))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 3
    assert [config(b)['thinkingConfig'] for b in gemini.bodies] == [
        {'thinkingBudget': 1024}, {'thinkingBudget': 512}, {'thinkingBudget': 256}]
    assert [len(b['contents']) for b in gemini.bodies] == [1, 1, 1], 'a cut-off reply adds no model turn'
    assert 'cut off' in gemini.bodies[1]['contents'][0]['parts'][0]['text']
    refusal = result['model_refusal']
    assert refusal['phase'] == 'truncated'
    said = ' '.join(refusal['missing'])
    assert 'cut off' in said and '16384-token budget' in said and 'ASK_PLAN_MAX_TOKENS' in said
    assert 'JSON' not in said


def test_a_cut_off_reply_is_not_sent_back_once_thinking_is_at_its_floor(bench, monkeypatch):
    vocab, planner = bench
    monkeypatch.setenv('ASK_PLAN_THINKING_TOKENS', '256')
    gemini = Gemini(*(reply(None, 'MAX_TOKENS') for _ in range(3)))
    asked(planner, vocab, 'Metric', gemini, declining)
    assert [config(b)['thinkingConfig'] for b in gemini.bodies] == [{'thinkingBudget': 256}, {'thinkingBudget': 128}]


def test_a_cut_off_reply_on_the_lowest_thinking_level_is_not_sent_back(bench, monkeypatch):
    vocab, planner = bench
    monkeypatch.setenv('MODEL_HIGH_THINKING_LEVEL', 'low')
    gemini = Gemini(*(reply(None, 'MAX_TOKENS') for _ in range(3)))
    result = asked(planner, vocab, 'Metric', gemini, declining)
    assert gemini.calls == 1 and config(gemini.bodies[0])['thinkingConfig'] == {'thinkingLevel': 'low'}
    assert result['model_refusal']['phase'] == 'truncated'


def test_a_follow_up_continues_the_earlier_plan_and_a_correction_continues_the_conversation(bench):
    vocab, planner = bench
    first = HistoryEntry(turn_id='t1', question='Metric', plan=grounded(model(), 'Metric', vocab), clarification=None,
                         binding=vocab.binding)
    follow = {**GOOD, 'continues': True, 'readings': []}
    wrong = {**follow, 'plan': UNDECLARED['plan']}
    gemini = Gemini(reply(wrong), reply(follow))
    result = asked(planner, vocab, 'Metric again', gemini, declining, (first,), 't2')
    assert result['kind'] == 'result' and 'model_refusal' not in result, result
    assert gemini.calls == 2
    content = gemini.bodies[0]['contents'][0]['parts'][0]['text']
    assert '## Conversation\n' in content and content.endswith('## Question\nMetric again')
    assert 'continues true' in gemini.bodies[0]['systemInstruction']['parts'][0]['text']
    assert [c['role'] for c in gemini.bodies[1]['contents']] == ['user', 'model', 'user']
    reading = planmodel.read_on(Gemini(reply(follow)).llm, 'Metric again', vocab, (first,), 't2')
    assert reading.reply.base_turn == 't1'
    assert reading.reply.plan.obligations and all(o.state == 'applied' for o in reading.reply.plan.obligations)


def test_the_trace_names_each_failed_attempt_and_each_calls_thinking_and_contract_tokens(bench):
    vocab, planner = bench
    gemini = Gemini(reply(UNDECLARED, thoughts=700, contract_tokens=60), reply(GOOD, thoughts=200, contract_tokens=45))
    with runtrace.start('ask') as trace:
        asked(planner, vocab, 'Metric', gemini, declining)
    assert [(a['call'], a['attempt'], a['outcome']) for a in trace.model_attempts] == [('plan', 0, 'contract')]
    assert any('/plan/measures/0/context' in e for e in trace.model_attempts[0]['errors'])
    calls = [c for c in trace.calls if c.get('call') == 'plan']
    assert [(c['attempt'], c['tokens_thinking'], c['tokens_contract']) for c in calls] == [(0, 700, 60), (1, 200, 45)]
    assert [c['thinking'] for c in calls] == ['level:low', 'level:low']


def test_no_correction_starts_without_thirty_seconds_of_the_request_left(bench, monkeypatch):
    vocab, planner = bench
    monkeypatch.setattr(planning, 'request_remaining', lambda: 12.0)
    gemini = Gemini(reply(UNDECLARED), reply(GOOD))
    result = asked(planner, vocab, 'Metric', gemini, rules_of(vocab))
    assert gemini.calls == 1
    assert result['model_refusal']['phase'] == 'deadline' and result['model_refusal']['fallback'] == 'rules'


@pytest.mark.parametrize('result, retried', [
    ({'kind': 'refuse', 'guard': {'failed_at': 'static', 'problems': ['The statement reads a table outside the gate.']}}, True),
    ({'kind': 'refuse', 'guard': {'failed_at': 'static', 'problems': ['column t.c is restricted personal data']}}, False),
    ({'kind': 'abstain', 'guard': {'failed_at': 'dry run', 'problems': ['dry run failed']}, 'dry_run': 'Syntax error: x'}, True),
    ({'kind': 'abstain', 'guard': {'failed_at': 'dry run', 'problems': ['dry run failed']}, 'dry_run': '503 Unavailable'}, False),
    ({'kind': 'refuse', 'guard': {'failed_at': 'partition', 'problems': []}}, False),
    ({'kind': 'abstain', 'guard': {'failed_at': 'runtime', 'problems': []}}, False),
    ({'kind': 'abstain', 'guard': {'failed_at': 'implements', 'problems': []}}, False),
    ({'kind': 'abstain', 'guard': {'failed_at': 'cost gate', 'problems': []}}, False),
    ({'kind': 'abstain', 'guard': {'failed_at': 'execute', 'problems': []}}, False),
    ({'kind': 'abstain', 'phase': 'compile'}, True),
    ({'kind': 'abstain', 'phase': 'release'}, True),
    ({'kind': 'abstain', 'phase': 'observations'}, False),
    ({'kind': 'refuse', 'missing': ['No permitted runtime can answer.']}, False)])
def test_which_refusals_of_a_checked_plan_go_back_to_the_model(result, retried):
    forget_dry_run()
    if 'dry_run' in result:
        warehouse_guard({'passed': False, 'failed_at': 'dry run', 'problems': ['dry run failed: ' + result['dry_run']]})
    assert retryable(result) is retried


def test_the_models_own_abstain_names_its_unread_word_in_the_reason_and_a_cut_off_planner_reaches_the_trace(bench):
    from kge.services.ask.engine.planpublic import UNREAD_PHASES, model_abstained
    vocab, planner = bench
    gemini = Gemini(reply({'decision': 'abstain', 'unread': ['moolah']}))
    result = asked(planner, vocab, 'Net new moolah', gemini, declining)
    assert gemini.calls == 1 and result['kind'] == 'abstain'
    reply_read = SimpleNamespace(kind='abstain')
    assert model_abstained(reply_read, result) == "'moolah' matches no declared meaning."
    assert model_abstained(SimpleNamespace(kind='propose'), result) == ''
    assert model_abstained(None, result) == ''
    gemini = Gemini(*(reply(None, 'MAX_TOKENS', 16000) for _ in range(3)))
    cut = asked(planner, vocab, 'Metric', gemini, declining)
    assert cut['model_refusal']['phase'] in UNREAD_PHASES
    assert 'ASK_PLAN_MAX_TOKENS' in ' '.join(cut['model_refusal']['missing'])
