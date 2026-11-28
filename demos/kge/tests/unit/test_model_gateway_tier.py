"""Gateway model matching and question binding without a network or provider call."""
import uuid
from types import SimpleNamespace
import pytest
from fastapi import HTTPException, Request

from kge.adapters.modelusage import Actor, CorrelationTokens, Ledger
from kge.adapters.objstore.folder import LocalStore
from kge.services.modelgateway import models, routes


@pytest.mark.parametrize('purpose,agent', [('agent', 'cerebro'), ('catalogue', 'catalogue')])
def test_selected_tier_is_bound_for_chat_and_legacy_tokens_keep_original_selection(monkeypatch, tmp_path, purpose, agent):
    monkeypatch.setenv('MODEL_PROVIDER', 'openai')
    monkeypatch.setenv('MODEL_NAME', 'default-model')
    monkeypatch.setenv('MODEL_HIGH_PROVIDER', 'openai')
    monkeypatch.setenv('MODEL_HIGH_NAME', 'high-model')
    monkeypatch.delenv(f'{agent.upper()}_MODEL_TIER', raising=False)
    codec = CorrelationTokens((b'a secure key for the tests only 32 bytes',))
    scope = Actor('meridian', 'person', 'alice', 'cerebro' if purpose == 'agent' else 'discover')
    monkeypatch.setattr(routes, 'correlation', lambda caller, purpose: (codec, scope))
    monkeypatch.setattr(routes, 'session_key', lambda path: b'a secure key for the tests only 32 bytes')
    monkeypatch.setattr(routes, 'served', SimpleNamespace(PROFILE=SimpleNamespace(run_dir=lambda: tmp_path)))
    ledger = Ledger(LocalStore(str(tmp_path), tmp_path), scope, b'a secure key for the tests only 32 bytes')
    monkeypatch.setattr(routes, 'usage_ledger', lambda *args: ledger)
    caller = routes.Actor('alice', 'person', ('analyst',), 'meridian')
    question_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4())
    body = {'question_id': question_id, 'conversation_id': conversation_id, 'purpose': purpose}
    def request(token=''):
        return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'x-kge-question-token', token.encode())]})
    def chat(expected):
        return models.ChatBody(**body, expected_model=expected, instructions='Help', messages=[{'role': 'user', 'parts': [{'kind': 'text', 'text': 'Hello'}]}])
    opened = routes.question(models.QuestionBody(**body, expected_model='high-model'), request(), caller=caller)
    assert codec.verify(opened['correlation_token'], scope, purpose, conversation_id) == question_id
    assert routes.chat(chat('high-model'), request(opened['correlation_token']), caller=caller).status_code == 200
    monkeypatch.setenv('MODEL_HIGH_BASE_URL', 'https://other.example')
    with pytest.raises(HTTPException) as endpoint_switch:
        routes.chat(chat('high-model'), request(opened['correlation_token']), caller=caller)
    assert endpoint_switch.value.status_code == 409
    monkeypatch.delenv('MODEL_HIGH_BASE_URL')
    assert routes.question(models.QuestionBody(**body, expected_model='high-model'), request(), caller=caller)['correlation_token']
    monkeypatch.setattr(routes, 'session_key', lambda path: b'a rotated key for the tests only 32 bytes')
    assert routes.question(models.QuestionBody(**body, expected_model='high-model'), request(), caller=caller)['correlation_token']
    with pytest.raises(HTTPException) as renewed_switch:
        routes.question(models.QuestionBody(**body, expected_model='default-model'), request(), caller=caller)
    assert renewed_switch.value.status_code == 409
    with pytest.raises(HTTPException) as switched:
        routes.chat(chat('default-model'), request(opened['correlation_token']), caller=caller)
    assert switched.value.status_code == 409
    question_id = str(uuid.uuid4())
    body['question_id'] = question_id
    ledger.open_question(question_id)
    old = codec.issue(scope, question_id, purpose, conversation_id)
    with pytest.raises(HTTPException) as legacy_reissue:
        routes.question(models.QuestionBody(**body, expected_model='high-model'), request(), caller=caller)
    assert legacy_reissue.value.status_code == 409
    renewed = routes.question(models.QuestionBody(**body, expected_model='default-model'), request(), caller=caller)
    assert renewed['correlation_token']
    assert routes.chat(chat('default-model'), request(old), caller=caller).status_code == 200
    with pytest.raises(HTTPException) as legacy_switch:
        routes.chat(chat('high-model'), request(old), caller=caller)
    assert legacy_switch.value.status_code == 409
    with pytest.raises(HTTPException) as legacy_renewal:
        routes.question(models.QuestionBody(**body, expected_model='high-model'), request(), caller=caller)
    assert legacy_renewal.value.status_code == 409
    question_id = str(uuid.uuid4())
    body['question_id'] = question_id
    ledger.open_question(question_id)
    old = codec.issue(scope, question_id, purpose, conversation_id)
    monkeypatch.setenv(f'{agent.upper()}_MODEL_TIER', 'high')
    assert routes.chat(chat('high-model'), request(old), caller=caller).status_code == 200
    with pytest.raises(HTTPException) as explicit_mismatch:
        routes.question(models.QuestionBody(**body, expected_model='default-model'), request(), caller=caller)
    assert explicit_mismatch.value.status_code == 409


def test_equivalent_tiers_compare_effective_connector_and_inherited_budget(monkeypatch):
    from kge.adapters.model.providers import model_for
    monkeypatch.setenv('MODEL_PROVIDER', 'vertex-gemini')
    monkeypatch.setenv('MODEL_NAME', 'gemini-3.8-flash')
    monkeypatch.setenv('MODEL_MEDIUM_PROVIDER', 'vertex-gemini')
    monkeypatch.setenv('MODEL_MEDIUM_NAME', 'gemini-3.8-flash')
    monkeypatch.setenv('MODEL_MAX_TOKENS', '4096')
    monkeypatch.setenv('MODEL_MEDIUM_API_KEY', 'ignored-by-vertex')
    monkeypatch.setenv('MODEL_MEDIUM_BASE_URL', 'https://ignored.example')
    monkeypatch.delenv('CEREBRO_MODEL_TIER', raising=False)
    model = model_for('cerebro', expected_model='gemini-3.8-flash')
    assert model is not None and model.max_tokens == 4096
    monkeypatch.setenv('MODEL_MEDIUM_MAX_TOKENS', '2048')
    assert model_for('cerebro', expected_model='gemini-3.8-flash') is None
