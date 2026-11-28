"""Per-attempt accounting through inference transports, not token-counting preflights."""
from __future__ import annotations

import io
import json
import urllib.error
import uuid
from types import SimpleNamespace

import pytest

from kge.adapters.model import chat, claude_chat, errors, providers
from kge.adapters.model.accounting import model_scope, without_inference
from kge.adapters.modelusage import Actor, for_actor, usage_scope
from kge.adapters.objstore.folder import LocalStore
from kge.ports import ChatMessage, ChatPart, ChatRequest


def identity():
    return str(uuid.uuid4())


def ledger(tmp_path):
    return for_actor(LocalStore(str(tmp_path), tmp_path), Actor("meridian", "person", "alice", "cerebro"),
                     key=b"a-strong-test-only-secret-key-32bytes")


def model(name="gemini-3-test", *, inp=1, out=2, provider="vertex-gemini"):
    return SimpleNamespace(provider=provider, model=name, prices={"in": inp, "out": out})


def gemini(name="gemini-3-test", inp=10, out=4, thought=2, cached=0):
    return {"modelVersion": name, "usageMetadata": {"promptTokenCount": inp, "candidatesTokenCount": out,
                                                     "thoughtsTokenCount": thought, "toolUsePromptTokenCount": 0,
                                                     "cachedContentTokenCount": cached}}


def test_nonstream_429_retry_records_each_physical_attempt_and_unknown_cost(tmp_path, monkeypatch):
    from kge.pipeline import trace
    spans = []
    monkeypatch.setattr(trace, "finished", lambda name, start, end, attrs, **kwargs: spans.append((name, start, end, attrs)))
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    calls = []
    def post(*_args):
        calls.append(1)
        if len(calls) == 1:
            raise errors.ExternalCallError("retryable", transient=True)
        return gemini()
    monkeypatch.setattr(errors, "_post_json", post)
    with usage_scope(book, question, invocation), model_scope(model(), "plan"):
        assert providers.post("https://example.test", {}, {}) == gemini()
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert len(calls) == 2 and report["calls"] == 2
    assert len(spans) == 2 and all(s[0] == "kge.model.attempt" and s[2] >= s[1] for s in spans)
    assert [s[3]["kge.outcome"] for s in spans] == ["failed", "ok"]
    assert all(s[3]["kge.stage"] == "plan" and s[3]["gen_ai.request.model"] == "gemini-3-test" for s in spans)
    assert spans[1][3]["gen_ai.response.model"] == "gemini-3-test"
    assert report["input_tokens"] is None and report["output_tokens"] is None and report["cost_usd"] is None


def test_multiple_models_are_priced_per_physical_attempt(tmp_path, monkeypatch):
    book, question = ledger(tmp_path), identity()
    first, second = identity(), identity()
    book.open_question(question)
    replies = iter([gemini(inp=1000, out=20, thought=80), gemini("gemini-3-other", inp=500, out=200, thought=0)])
    monkeypatch.setattr(errors, "_post_json", lambda *_args: next(replies))
    for invocation, selected in ((first, model()), (second, model("gemini-3-other", inp=10, out=20))):
        book.start_invocation(question, invocation)
        with usage_scope(book, question, invocation), model_scope(selected):
            providers.post("https://example.test", {}, {})
        book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert report["calls"] == 2 and report["input_tokens"] == 1500 and report["output_tokens"] == 300
    assert report["thinking_tokens"] == 80 and report["cost_usd"] == 0.0102


def test_nested_kge_model_call_propagates_scope_into_deadline_worker(tmp_path, monkeypatch):
    from kge.adapters.deadline import request_window

    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    response = {**gemini(), "candidates": [{"content": {"parts": [{"text": "private reply"}]}, "finishReason": "STOP"}]}
    monkeypatch.setattr(providers.Chat, "_gemini_target", lambda self: ("https://example.test", {}))
    monkeypatch.setattr(errors, "_post_json", lambda *_args: response)
    selected = providers.LLM("vertex-gemini", "gemini-3-test", price_in=1, price_out=2)
    with usage_scope(book, question, invocation), request_window(5):
        assert selected.complete("private question") == "private reply"
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert report["calls"] == 1 and report["input_tokens"] == 10 and report["output_tokens"] == 6
    assert report["cost_usd"] == 0.000022
    raw = b"".join(path.read_bytes() for path in tmp_path.glob("model-usage/*.json"))
    assert b"private question" not in raw and b"private reply" not in raw


def test_preflight_is_not_an_inference_and_cannot_settle_an_invocation(tmp_path, monkeypatch):
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    monkeypatch.setattr(errors, "_post_json", lambda *_args: {"totalTokens": 50})
    with usage_scope(book, question, invocation), model_scope(model()), without_inference():
        providers.post("https://example.test", {}, {})
    with pytest.raises(Exception, match="not fully accounted"):
        book.finish_invocation(question, invocation)
    assert book.report(question)["calls"] == 0 and book.report(question)["cost_usd"] is None


def _sse(*messages):
    return io.BytesIO(b"".join(b"data: " + json.dumps(m).encode() + b"\n\n" for m in messages))


def test_stream_429_retry_records_both_and_never_double_charges(tmp_path, monkeypatch):
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    calls = []
    def opened(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://example.test", 429, "rate limited", {}, None)
        return _sse({**gemini(), "candidates": [{"finishReason": "STOP"}]})
    monkeypatch.setattr(chat.http, "open_http", opened)
    with usage_scope(book, question, invocation), model_scope(model()):
        assert list(chat._stream_http("https://example.test", {}, {}))
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert len(calls) == 2 and report["calls"] == 2 and report["cost_usd"] is None


def test_stream_cancellation_records_attempt_with_unknown_usage(tmp_path, monkeypatch):
    import time
    from kge.adapters.modelusage import UsageConflict
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    monkeypatch.setattr(chat.http, "open_http", lambda *_a, **_kw: _sse({"modelVersion": "gemini-3-test", "candidates": []}))
    with usage_scope(book, question, invocation), model_scope(model()):
        stream = chat._stream_http("https://example.test", {}, {})
        next(stream)
        stream.close()
    deadline = time.monotonic() + 2
    while True:
        try:
            book.finish_invocation(question, invocation)
            break
        except UsageConflict:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    book.finish_question(question)
    report = book.report(question)
    assert report["calls"] == 1 and report["cost_usd"] is None and report["input_tokens"] is None


def test_stream_final_event_without_trailing_blank_line_is_counted_before_close(tmp_path, monkeypatch):
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    terminal = {**gemini(), "candidates": [{"finishReason": "STOP"}]}
    monkeypatch.setattr(chat.http, "open_http", lambda *_a, **_kw: io.BytesIO(b"data: " + json.dumps(terminal).encode()))
    with usage_scope(book, question, invocation), model_scope(model()):
        assert list(chat._stream_http("https://example.test", {}, {})) == [terminal]
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert report["calls"] == 1 and report["input_tokens"] == 10 and report["output_tokens"] == 6
    assert report["cost_usd"] == 0.000022


def test_missing_served_model_or_cache_usage_is_not_priced(tmp_path, monkeypatch):
    book, question = ledger(tmp_path), identity()
    book.open_question(question)
    for response in ({"usageMetadata": gemini()["usageMetadata"]},
                     {"modelVersion": "gemini-3-test", "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2}}):
        invocation = identity()
        book.start_invocation(question, invocation)
        monkeypatch.setattr(errors, "_post_json", lambda *_args: response)
        with usage_scope(book, question, invocation), model_scope(model()):
            providers.post("https://example.test", {}, {})
        book.finish_invocation(question, invocation)
    book.finish_question(question)
    assert book.report(question)["cost_usd"] is None


def test_claude_stream_tracks_cache_usage_without_persisting_thinking(tmp_path, monkeypatch):
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    events = [
        {"type": "message_start", "message": {"model": "claude-test", "usage": {"input_tokens": 10,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "sensitive answer"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]
    class Stream:
        def __enter__(self):
            return iter(events)

        def __exit__(self, *_):
            return None

    class Messages:
        def count_tokens(self, **_kwargs):
            return {"input_tokens": 10}

        def stream(self, **_kwargs):
            return Stream()

    selected = model("claude-test", provider="vertex-claude")
    selected.name = "vertex-claude:claude-test"
    selected.max_tokens = 40
    selected.temperature = None
    selected.chat = SimpleNamespace(anthropic_vertex=lambda: SimpleNamespace(messages=Messages()))
    request = ChatRequest(instructions="secret instructions", messages=(ChatMessage("user", (ChatPart("text", "secret question"),)),),
                          tools=(), input_tokens=100, output_tokens=40)
    monkeypatch.setattr(claude_chat, "chat_policy", lambda _: (100, 40, None, "low"))
    with usage_scope(book, question, invocation), model_scope(selected):
        events = list(claude_chat.stream(selected, request))
    assert events[-1].finish == "STOP"
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert report["calls"] == 1 and report["input_tokens"] == 10 and report["output_tokens"] == 10
    assert report["cost_usd"] == 0.00003 and report["thinking_tokens"] is None
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("model-usage/*.json"))
    assert b"secret" not in raw and b"sensitive answer" not in raw


def test_claude_sdk_preopen_failure_records_attempt_before_retry(tmp_path, monkeypatch):
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    events = [
        {"type": "message_start", "message": {"model": "claude-test", "usage": {"input_tokens": 10,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "answer"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]
    class Stream:
        def __enter__(self):
            return iter(events)

        def __exit__(self, *_):
            return None

    class Messages:
        attempts = 0

        def count_tokens(self, **_kwargs):
            return {"input_tokens": 10}

        def stream(self, **_kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise errors.ExternalCallError("transient", transient=True)
            return Stream()

    sdk = Messages()
    selected = model("claude-test", provider="vertex-claude")
    selected.name, selected.max_tokens, selected.temperature = "vertex-claude:claude-test", 40, None
    selected.chat = SimpleNamespace(anthropic_vertex=lambda: SimpleNamespace(messages=sdk))
    request = ChatRequest(instructions="instructions", messages=(ChatMessage("user", (ChatPart("text", "question"),)),),
                          tools=(), input_tokens=100, output_tokens=40)
    monkeypatch.setattr(claude_chat, "chat_policy", lambda _: (100, 40, None, "low"))
    with usage_scope(book, question, invocation), model_scope(selected):
        assert list(claude_chat.stream(selected, request))[-1].finish == "STOP"
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert sdk.attempts == 2 and report["calls"] == 2 and report["cost_usd"] is None


def test_claude_sdk_quota_refusal_after_attempt_does_not_retry_or_report_zero(tmp_path, monkeypatch):
    from kge.adapters.model import quota

    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    class Messages:
        attempts = 0

        def count_tokens(self, **_kwargs):
            return {"input_tokens": 10}

        def stream(self, **_kwargs):
            self.attempts += 1
            raise errors.ExternalCallError("transient", transient=True)

    sdk = Messages()
    selected = model("claude-test", provider="vertex-claude")
    selected.name, selected.max_tokens, selected.temperature = "vertex-claude:claude-test", 40, None
    selected.chat = SimpleNamespace(anthropic_vertex=lambda: SimpleNamespace(messages=sdk))
    request = ChatRequest(instructions="instructions", messages=(ChatMessage("user", (ChatPart("text", "question"),)),),
                          tools=(), input_tokens=100, output_tokens=40)
    monkeypatch.setattr(claude_chat, "chat_policy", lambda _: (100, 40, None, "low"))
    def refused():
        raise quota.QuotaExceeded(60)
    monkeypatch.setattr(quota, "admit_retry", refused)
    with usage_scope(book, question, invocation), model_scope(selected):
        with pytest.raises(quota.QuotaExceeded):
            list(claude_chat.stream(selected, request))
    assert sdk.attempts == 1
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    assert book.report(question)["calls"] == 1 and book.report(question)["cost_usd"] is None


def test_claude_nonstream_retry_records_each_physical_call(tmp_path, monkeypatch):
    from anthropic import APIConnectionError
    import httpx

    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    class Result:
        def model_dump(self):
            return {"model": "claude-test", "usage": {"input_tokens": 10, "output_tokens": 10,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn"}

    class Messages:
        attempts = 0

        def create(self, **_kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise APIConnectionError(request=httpx.Request("POST", "https://example.test"))
            return Result()

    sdk = Messages()
    monkeypatch.setattr(providers.Chat, "anthropic_vertex", lambda self: SimpleNamespace(messages=sdk))
    selected = providers.LLM("vertex-claude", "claude-test", price_in=1, price_out=2)
    with usage_scope(book, question, invocation):
        assert selected.complete("private question") == "answer"
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    report = book.report(question)
    assert sdk.attempts == 2 and report["calls"] == 2 and report["cost_usd"] is None


def test_gemini_total_reconciles_only_provable_optional_counts():
    from kge.adapters.model.accounting import _Model, _usage
    selected = _Model('vertex-gemini', 'gemini-test', 1, 2)
    def result(usage):
        return _usage(selected, {'modelVersion': 'gemini-test', 'usageMetadata': usage}, 'ok')
    empty = result({'promptTokenCount': 10, 'candidatesTokenCount': 3, 'totalTokenCount': 13})
    assert (empty['input_tokens'], empty['output_tokens'], empty['thinking_tokens']) == (10, 3, 0)
    assert empty['cache_mode'] == 'unknown'
    full = result({'promptTokenCount': 10, 'candidatesTokenCount': 3, 'toolUsePromptTokenCount': 2,
                   'thoughtsTokenCount': 4, 'totalTokenCount': 19})
    assert (full['input_tokens'], full['output_tokens'], full['thinking_tokens']) == (12, 7, 4)
    inferred = result({'promptTokenCount': 10, 'candidatesTokenCount': 3, 'toolUsePromptTokenCount': 2,
                       'totalTokenCount': 19})
    assert (inferred['input_tokens'], inferred['output_tokens'], inferred['thinking_tokens']) == (12, 7, 4)
    ambiguous = result({'promptTokenCount': 10, 'candidatesTokenCount': 3, 'totalTokenCount': 19})
    assert ambiguous['input_tokens'] is None and ambiguous['output_tokens'] is None
    inconsistent = result({'promptTokenCount': 10, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 8,
                           'totalTokenCount': 15})
    assert inconsistent['input_tokens'] is None and inconsistent['output_tokens'] is None


def test_cancelled_stream_closes_socket_without_waiting_for_ledger():
    import threading
    import time
    entered, release = threading.Event(), threading.Event()
    class Accounting:
        def finish(self, **kwargs):
            entered.set()
            release.wait(2)
    class Socket:
        closed = False
        def shutdown(self, *_):
            self.closed = True
    sock = Socket()
    class Response:
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=sock))
        def close(self):
            pass
    source = chat._SSEStream('https://example.test', {}, {})
    source.response = Response()
    source._accounting = Accounting()
    started = time.monotonic()
    source.close()
    assert time.monotonic() - started < 0.5 and sock.closed and entered.wait(0.5)
    release.set()


def test_worker_context_carries_verified_usage_scope(tmp_path):
    import threading
    from kge.adapters.modelusage import current_usage
    from kge.pipeline.trace import carried
    book, question, invocation = ledger(tmp_path), identity(), identity()
    seen = []
    with usage_scope(book, question, invocation):
        worker = threading.Thread(target=carried(lambda: seen.append(current_usage())))
        worker.start()
        worker.join()
    assert seen == [(book, question, invocation)]
    assert current_usage() is None


def test_background_run_cannot_be_rebound_to_other_question(tmp_path):
    from kge.services.ask import service
    book, first, second, conversation = ledger(tmp_path), identity(), identity(), identity()
    inv = identity()
    book.open_question(first)
    book.start_invocation(first, inv)
    book.open_question(second)
    book.start_invocation(second, inv)
    record = {'id': 'run', 'principal': 'alice', 'usage_binding': book.scope_binding(first, conversation), 'identity': 'release',
              'readable': [], 'state': 'done', 'expires_at': 100, 'poll_after': 1, 'events': []}
    state = SimpleNamespace(get=lambda _: (record, 1))
    client = SimpleNamespace(e=SimpleNamespace(identity='release'),
                             g=SimpleNamespace(readable_units=lambda who: set()))
    profile = SimpleNamespace(client='meridian')
    with usage_scope(book, second, inv, conversation_id=conversation):
        assert service.get_run(client, profile, 'run', 'alice', state=state) is None
    other_app = for_actor(LocalStore(str(tmp_path), tmp_path), Actor('meridian', 'person', 'alice', 'discover'),
                          key=b'a-strong-test-only-secret-key-32bytes')
    other_app.open_question(first)
    other_app.start_invocation(first, inv)
    with usage_scope(other_app, first, inv, conversation_id=conversation):
        assert service.get_run(client, profile, 'run', 'alice', state=state) is None
        assert service.stop_run(client, profile, 'run', 'alice', state=state) is None
    wrong_conversation_invocation = identity()
    book.start_invocation(first, wrong_conversation_invocation)
    with usage_scope(book, first, wrong_conversation_invocation, conversation_id=identity()):
        assert service.get_run(client, profile, 'run', 'alice', state=state) is None
        assert service.stop_run(client, profile, 'run', 'alice', state=state) is None
    assert service.get_run(client, profile, 'run', 'alice', state=state) is None
    with usage_scope(book, first, inv, conversation_id=conversation):
        assert service.get_run(client, profile, 'run', 'alice', state=state)['state'] == 'done'
    book.finish_question(first)
    assert book.report(first)['calls'] == 0


def test_mcp_usage_verifies_identity_and_propagates_each_attempt(tmp_path, monkeypatch):
    from fastapi import HTTPException, Request
    from kge.adapters import modelusage
    from kge.adapters.modelusage import CorrelationTokens, current_usage
    from kge.services import http, identity as identity_service
    book, question, conversation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    invocation = identity()
    book.start_invocation(question, invocation)
    key = b'x' * 32
    actor = Actor('meridian', 'person', 'alice', 'cerebro')
    token = CorrelationTokens((key,)).issue(actor, question, 'agent', conversation)
    graph = SimpleNamespace(processes=lambda _: {'ask'}, people=SimpleNamespace(is_admin=lambda _: False))
    monkeypatch.setattr(http, 'served', SimpleNamespace(engine=lambda: SimpleNamespace(g=graph),
                        PROFILE=SimpleNamespace(client='meridian', run_dir=lambda: tmp_path)))
    monkeypatch.setattr(http, 'person', lambda _: True)
    monkeypatch.setattr(http, 'settings', lambda: SimpleNamespace(K_SERVICE='', SESSION_SECRET=''))
    monkeypatch.setattr(identity_service, 'session_key', lambda _: key)
    monkeypatch.setattr(modelusage, 'for_profile', lambda profile, scope, conversation: book)
    def request(value, conv=conversation):
        return Request({'type': 'http', 'method': 'POST', 'path': '/mcp', 'headers': [
            (b'x-kge-question-token', value.encode()), (b'x-kge-conversation-id', conv.encode()), (b'x-kge-invocation-id', invocation.encode())]})
    with http.mcp_usage_context(request(token), 'alice', 'ask'):
        active = current_usage()
        assert active[0] is book and active[1] == question
        with model_scope(model(), 'plan'):
            from kge.adapters.model.accounting import start_attempt
            attempt = start_attempt()
            attempt.finish(payload=gemini(), outcome='ok')
    assert current_usage() is None
    book.finish_question(question)
    assert book.report(question)['calls'] == 1
    with pytest.raises(HTTPException) as refused:
        with http.mcp_usage_context(request(token), 'bob', 'ask'):
            pass
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as refused:
        with http.mcp_usage_context(request(token, identity()), 'alice', 'ask'):
            pass
    assert refused.value.status_code == 403


def test_mcp_usage_without_observed_inference_remains_unknown(tmp_path, monkeypatch):
    from fastapi import Request
    from kge.adapters import modelusage
    from kge.adapters.modelusage import CorrelationTokens
    from kge.services import http, identity as identity_service
    book, question, conversation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    invocation = identity()
    book.start_invocation(question, invocation)
    key = b'x' * 32
    token = CorrelationTokens((key,)).issue(Actor('meridian', 'person', 'alice', 'cerebro'), question, 'agent', conversation)
    graph = SimpleNamespace(processes=lambda _: {'ask'}, people=SimpleNamespace(is_admin=lambda _: False))
    monkeypatch.setattr(http, 'served', SimpleNamespace(engine=lambda: SimpleNamespace(g=graph),
                        PROFILE=SimpleNamespace(client='meridian', run_dir=lambda: tmp_path)))
    monkeypatch.setattr(http, 'person', lambda _: True)
    monkeypatch.setattr(http, 'settings', lambda: SimpleNamespace(K_SERVICE='', SESSION_SECRET=''))
    monkeypatch.setattr(identity_service, 'session_key', lambda _: key)
    monkeypatch.setattr(modelusage, 'for_profile', lambda profile, scope, conversation: book)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/mcp', 'headers': [
        (b'x-kge-question-token', token.encode()), (b'x-kge-conversation-id', conversation.encode()), (b'x-kge-invocation-id', invocation.encode())]})
    with http.mcp_usage_context(request, 'alice', 'ask'):
        pass
    assert book.report(question)['complete'] is False and book.report(question)['cost_usd'] is None


def test_no_work_background_poll_settles_after_access_change_and_race(tmp_path):
    from kge.ports import StateConflict
    from kge.services.ask import service
    book, question, conversation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    record = {'id': 'run', 'principal': 'alice', 'usage_binding': book.scope_binding(question, conversation),
              'identity': 'release', 'readable': [], 'state': 'running', 'expires_at': 500,
              'poll_after': 1, 'events': [], 'lease': 0, 'next_poll': 0}
    profile = SimpleNamespace(client='meridian')
    client = SimpleNamespace(e=SimpleNamespace(identity='other'), g=SimpleNamespace(readable_units=lambda who: set()))
    one = identity()
    book.start_invocation(question, one)
    state = SimpleNamespace(get=lambda _: (record.copy(), 1))
    with usage_scope(book, question, one, conversation_id=conversation):
        assert service.get_run(client, profile, 'run', 'alice', state=state, clock=lambda: 100)['state'] == 'failed'
    client.e.identity = 'release'
    two = identity()
    book.start_invocation(question, two)
    def conflict(*args):
        raise StateConflict('race')
    state = SimpleNamespace(get=lambda _: (record.copy(), 1), put=conflict)
    with usage_scope(book, question, two, conversation_id=conversation):
        assert service.get_run(client, profile, 'run', 'alice', state=state, clock=lambda: 100)['state'] == 'running'
    book.finish_question(question)
    assert book.report(question)['complete'] and book.report(question)['calls'] == 0


def test_rest_owner_restores_bound_run_scope_but_other_user_cannot(tmp_path, monkeypatch):
    from kge.adapters import modelusage, questionstate
    from kge.adapters.modelusage import current_usage, current_usage_conversation
    from kge.services import http
    book, question, conversation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    record = {'principal': 'alice', 'usage_binding': book.scope_binding(question, conversation),
              'usage_scope': {'kind': 'person', 'app': 'cerebro', 'question': question, 'conversation': conversation}}
    graph = SimpleNamespace(processes=lambda _: {'ask'}, people=SimpleNamespace(is_admin=lambda _: False))
    monkeypatch.setattr(http, 'served', SimpleNamespace(engine=lambda: SimpleNamespace(g=graph),
                        PROFILE=SimpleNamespace(client='meridian')))
    monkeypatch.setattr(http, 'person', lambda _: True)
    monkeypatch.setattr(questionstate, 'for_profile', lambda _: SimpleNamespace(get=lambda _id: (record, 1)))
    monkeypatch.setattr(modelusage, 'for_profile', lambda profile, actor, conversation: book)
    with http.question_run_usage('alice', 'run', advance=False):
        assert current_usage()[1] == question and current_usage_conversation() == conversation
    with http.question_run_usage('bob', 'run', advance=False):
        assert current_usage() is None
    assert current_usage() is None


def test_dropped_correlation_headers_leave_expected_invocation_open(tmp_path):
    from fastapi import Request
    from kge.adapters.modelusage import current_usage, UsageConflict
    from kge.services import http
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/mcp', 'headers': []})
    with http.mcp_usage_context(request, 'alice', 'ask'):
        assert current_usage() is None
    with pytest.raises(UsageConflict):
        book.finish_question(question)
    assert book.report(question)['cost_usd'] is None


def test_offline_mock_accounts_its_synthetic_word_counts(tmp_path, monkeypatch):
    from kge.adapters.model import mock as mock_module
    monkeypatch.setattr(mock_module, 'reply', lambda *_: 'mock answer')
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    mock = providers.LLM('mock', '')
    with usage_scope(book, question, invocation):
        mock.complete('hello there')
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    result = book.report(question)
    assert result['calls'] == 1 and result['cost_usd'] == 0
    assert result['input_tokens'] == 2 and result['output_tokens'] > 0


def test_only_proven_rules_only_engine_can_settle_without_inference(tmp_path):
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.config import PRESETS
    from dataclasses import replace
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    cfg = PRESETS['graph']
    assert not cfg.model_stages()
    from kge.services.ask.engine.modelcall import observe_model_dispatch
    with usage_scope(book, question, invocation):
        service._settle_rules_only(object(), cfg)
        service._settle_rules_only(object.__new__(Combined), replace(cfg, renderer='model'))
        with observe_model_dispatch() as witness:
            service._settle_rules_only(object(), cfg, witness)
    with pytest.raises(UsageConflict):
        book.finish_question(question)
    with usage_scope(book, question, invocation), observe_model_dispatch() as witness:
        service._settle_rules_only(object.__new__(Combined), replace(cfg, renderer='model'), witness)
    book.finish_question(question)
    assert book.report(question)['calls'] == 0


def test_rest_live_worker_receives_owner_bound_usage_scope(tmp_path, monkeypatch):
    from kge.adapters import modelusage, questionstate
    from kge.adapters.modelusage import current_usage, current_usage_conversation
    from kge.services import http
    from kge.services.ask import routes, service
    book, question, conversation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    record = {'principal': 'alice', 'usage_binding': book.scope_binding(question, conversation),
              'usage_scope': {'kind': 'person', 'app': 'cerebro', 'question': question, 'conversation': conversation}}
    graph = SimpleNamespace(processes=lambda _: {'ask'}, people=SimpleNamespace(is_admin=lambda _: False))
    monkeypatch.setattr(http, 'served', SimpleNamespace(engine=lambda: SimpleNamespace(g=graph),
                        PROFILE=SimpleNamespace(client='meridian')))
    monkeypatch.setattr(http, 'person', lambda _: True)
    monkeypatch.setattr(questionstate, 'for_profile', lambda _: SimpleNamespace(get=lambda _id: (record, 1)))
    monkeypatch.setattr(modelusage, 'for_profile', lambda profile, actor, conversation: book)
    seen = []
    def get_run(*args, **kwargs):
        scope = current_usage()
        seen.append((scope[1], current_usage_conversation()))
        scope[0].finish_invocation_no_inference(scope[1], scope[2], reason='idle_poll')
        return {'id': 'run', 'state': 'done', 'events': [], 'cursor': 0}
    monkeypatch.setattr(service, 'get_run', get_run)
    request = SimpleNamespace(is_disconnected=lambda: False)
    out = routes._live_or_done(request, object(), 'run', 'alice', 0)
    assert out['state'] == 'done' and seen == [(question, conversation)]
    book.finish_question(question)
    assert book.report(question)['complete']


def _fake_ask_service(monkeypatch, answer):
    from kge.services.ask import service
    from kge.services.ask.engine.combined import Combined
    combined = object.__new__(Combined)
    combined.e, combined.g = object(), object()
    combined.llm = SimpleNamespace(name='configured-model')
    monkeypatch.setattr(Combined, 'ask', answer)
    monkeypatch.setattr(service.runtrace, 'run', lambda _path, call, **_kwargs: {**call(), 'run_trace': {'llm_calls': []}})
    monkeypatch.setattr(service, 'with_receipt', lambda out, *_args: out)
    monkeypatch.setattr(service, 'log_unmatched', lambda *_args: None)
    monkeypatch.setattr(service, 'worded', lambda out, *_args: out)
    monkeypatch.setattr(service, 'ordered', lambda out: out)
    monkeypatch.setattr(service, 'response_view', lambda out, *_args: out)
    return combined


def test_configured_rules_first_pass_settles_only_its_no_inference_invocation(tmp_path, monkeypatch):
    from dataclasses import replace
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    sent = []
    def rules(_self, *_args, **_kwargs):
        sent.append('rules')
        return {'behaviour': 'figure', 'answered_by': 'rules'}
    c = _fake_ask_service(monkeypatch, rules)
    cfg = replace(PRESETS['graph'], renderer='model')
    assert cfg.model_stages()
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    with usage_scope(book, question, invocation):
        assert service.ask(c, None, 'Metric', 'alice', 'analysis', 'graph', cfg)['answered_by'] == 'rules'
    book.finish_invocation(question, invocation)
    book.finish_question(question)
    assert sent == ['rules'] and book.report(question)['complete'] and book.report(question)['calls'] == 0


def test_model_dispatch_without_transport_instrumentation_never_becomes_zero_usage(tmp_path, monkeypatch):
    from dataclasses import replace
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.engine.modelcall import Contract, structured_call
    calls = []
    class Uninstrumented:
        def json(self, _prompt):
            calls.append('provider-called')
            return {}
    def fallback(_self, *_args, **_kwargs):
        structured_call(Uninstrumented(), 'plan', version='v1', instructions='', data={}, question='Metric',
                        contract=Contract, schema={}, legacy_prompt='', validate=False)
        return {'behaviour': 'figure', 'answered_by': 'rules'}
    c = _fake_ask_service(monkeypatch, fallback)
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    with usage_scope(book, question, invocation):
        service.ask(c, None, 'Metric', 'alice', 'analysis', 'graph', cfg)
    assert calls == ['provider-called']
    with pytest.raises(UsageConflict):
        book.finish_invocation(question, invocation)
    with pytest.raises(UsageConflict):
        book.finish_question(question)
    assert not book.report(question)['complete'] and book.report(question)['cost_usd'] is None


def test_background_witness_detects_model_entry_even_when_no_attempt_was_recorded(tmp_path, monkeypatch):
    from dataclasses import asdict, replace
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.engine.modelcall import Contract, structured_call
    import time
    c = _fake_ask_service(monkeypatch, lambda *_args, **_kwargs: {'behaviour': 'figure'})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    def advanced(*_args):
        class Uninstrumented:
            def json(self, _prompt):
                return {}
        structured_call(Uninstrumented(), 'plan', version='v1', instructions='', data={}, question='Metric',
                        contract=Contract, schema={}, legacy_prompt='', validate=False)
        return {'state': 'running'}
    monkeypatch.setattr(service, '_advance_pass', advanced)
    with usage_scope(book, question, invocation):
        assert service._advance_run(c, None, {'id': 'a' * 32, 'config': asdict(cfg)}, object(), '1', time.time)['state'] == 'running'
    with pytest.raises(UsageConflict):
        book.finish_invocation(question, invocation)
    assert not book.report(question)['complete']


def test_background_running_then_done_settles_each_verified_idle_pass_idempotently(tmp_path, monkeypatch):
    from dataclasses import asdict, replace
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    import time
    c = _fake_ask_service(monkeypatch, lambda *_args, **_kwargs: {'behaviour': 'figure'})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question = ledger(tmp_path), identity()
    book.open_question(question)
    state = ['running', 'done']
    monkeypatch.setattr(service, '_advance_pass', lambda *_args: {'state': state.pop(0)})
    for expected, reason in (('running', 'idle_poll'), ('done', 'rules_only')):
        invocation = identity()
        book.start_invocation(question, invocation)
        book.claim_invocation(question, invocation)
        with usage_scope(book, question, invocation):
            result = service._advance_run(c, None, {'id': 'a' * 32, 'config': asdict(cfg)}, object(), '1', time.time)
        assert result['state'] == expected
        book.finish_invocation_no_inference(question, invocation, reason=reason)
        book.finish_invocation(question, invocation)
    book.finish_question(question)
    assert book.report(question)['complete'] and book.report(question)['calls'] == 0


def test_a_physical_attempt_blocks_a_rules_only_marker_even_if_trace_is_empty(tmp_path, monkeypatch):
    from dataclasses import replace
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    c = _fake_ask_service(monkeypatch, lambda *_args, **_kwargs: {'behaviour': 'figure', 'answered_by': 'rules'})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, invocation, attempt = ledger(tmp_path), identity(), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    book.start_attempt(question, attempt, invocation)
    with usage_scope(book, question, invocation):
        service.ask(c, None, 'Metric', 'alice', 'analysis', 'graph', cfg)
    with pytest.raises(UsageConflict):
        book.finish_invocation(question, invocation)
    with pytest.raises(UsageConflict):
        book.finish_question(question)
    assert not book.report(question)['complete']


def test_failed_background_pass_never_marks_an_unobserved_invocation_as_rules_only(tmp_path, monkeypatch):
    from dataclasses import asdict, replace
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    import time
    c = _fake_ask_service(monkeypatch, lambda *_args, **_kwargs: {})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    monkeypatch.setattr(service, '_advance_pass', lambda *_args: {'state': 'failed'})
    with usage_scope(book, question, invocation):
        assert service._advance_run(c, None, {'id': 'a' * 32, 'config': asdict(cfg)}, object(), '1', time.time)['state'] == 'failed'
    with pytest.raises(UsageConflict):
        book.finish_question(question)
    assert not book.report(question)['complete']


def test_concurrent_questions_keep_dispatch_witnesses_isolated(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Barrier
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.engine.modelcall import Contract, structured_call
    barrier = Barrier(2)
    class Uninstrumented:
        def json(self, _prompt):
            return {}
    def answer(_self, question, *_args, **_kwargs):
        if question == 'model':
            structured_call(Uninstrumented(), 'plan', version='v1', instructions='', data={}, question='Metric',
                            contract=Contract, schema={}, legacy_prompt='', validate=False)
        barrier.wait(timeout=5)
        return {'behaviour': 'figure', 'answered_by': 'rules'}
    c = _fake_ask_service(monkeypatch, answer)
    cfg = replace(PRESETS['graph'], renderer='model')
    book = ledger(tmp_path)
    questions = {label: (identity(), identity()) for label in ('rules', 'model')}
    for question, invocation in questions.values():
        book.open_question(question)
        book.start_invocation(question, invocation)
        book.claim_invocation(question, invocation)
    def run(label):
        question, invocation = questions[label]
        with usage_scope(book, question, invocation):
            service.ask(c, None, label, 'alice', 'analysis', 'graph', cfg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, questions))
    rules, model = questions['rules'], questions['model']
    book.finish_question(rules[0])
    assert book.report(rules[0])['complete'] and book.report(rules[0])['calls'] == 0
    with pytest.raises(UsageConflict):
        book.finish_question(model[0])
    assert not book.report(model[0])['complete']


def test_unpropagated_model_dispatch_never_proves_zero_usage(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.engine.modelcall import Contract, structured_call
    class Uninstrumented:
        def json(self, _prompt):
            return {}
    def answer(_self, *_args, **_kwargs):
        def dispatch():
            structured_call(Uninstrumented(), 'plan', version='v1', instructions='', data={}, question='Metric',
                            contract=Contract, schema={}, legacy_prompt='', validate=False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(dispatch).result()
        return {'behaviour': 'figure', 'answered_by': 'rules'}
    c = _fake_ask_service(monkeypatch, answer)
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    with usage_scope(book, question, invocation):
        service.ask(c, None, 'Metric', 'alice', 'analysis', 'graph', replace(PRESETS['graph'], renderer='model'))
    with pytest.raises(UsageConflict):
        book.finish_invocation(question, invocation)
    assert not book.report(question)['complete']


def test_cancelled_ask_never_marks_an_unfinished_invocation_as_rules_only(tmp_path, monkeypatch):
    from dataclasses import replace
    from kge.adapters.modelusage import UsageConflict
    from kge.pipeline.trace import QueryCancelled
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    def cancelled(*_args, **_kwargs):
        raise QueryCancelled()
    c = _fake_ask_service(monkeypatch, cancelled)
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    with usage_scope(book, question, invocation), pytest.raises(QueryCancelled):
        service.ask(c, None, 'Metric', 'alice', 'analysis', 'graph', replace(PRESETS['graph'], renderer='model'))
    with pytest.raises(UsageConflict):
        book.finish_question(question)
    assert not book.report(question)['complete']


def test_repeated_running_pass_cannot_settle_after_earlier_uninstrumented_model_dispatch(tmp_path, monkeypatch):
    from dataclasses import replace
    from kge.adapters.modelusage import UsageConflict
    from kge.pipeline import questionrun
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.engine.modelcall import Contract, structured_call
    from kge.services.ask.models import Ask
    class Uninstrumented:
        def json(self, _prompt):
            return {}
    class Warehouse:
        def submit(self, *_args, **_kwargs):
            return {'id': 'job-1', 'guard': {'passed': True}, 'meta': {}}
        def poll(self, _handle):
            return {'state': 'done', 'rows': [[15]]}
        def release(self, _handle):
            return None
    class State:
        def put(self, _run_id, _record, _revision):
            return '1'
    passes = []
    def combined_answer(self, *_args, **_kwargs):
        passes.append(1)
        if len(passes) == 1:
            structured_call(Uninstrumented(), 'plan', version='v1', instructions='', data={}, question='Metric',
                            contract=Contract, schema={}, legacy_prompt='', validate=False)
        _, rows, _ = questionrun.execute(self.e.w, {'sql': 'select 15'}, 'test', ())
        return {'behaviour': 'figure', 'answered_by': 'rules', 'figure': rows[0][0]}
    c = _fake_ask_service(monkeypatch, combined_answer)
    c.e = SimpleNamespace(w=Warehouse(), identity='release-1')
    c.g = SimpleNamespace(readable_units=lambda _who: {'u'})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    with usage_scope(book, question, invocation, conversation_id=identity()):
        run = service.start_run(c, SimpleNamespace(client='meridian'), Ask(question='Metric'), 'alice', cfg, state=State(), wait=1)
    assert run['state'] == 'done' and len(passes) == 2, run
    with pytest.raises(UsageConflict):
        book.finish_invocation(question, invocation)
    assert not book.report(question)['complete']


@pytest.mark.parametrize('other_route', ['agent', 'quality'])
def test_unrelated_combined_model_work_does_not_taint_rules_only_pass(tmp_path, monkeypatch, other_route):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Barrier
    from kge.services.ask import service
    from kge.services.ask.engine import combined as combined_module
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.engine.modelcall import Contract, structured_call
    barrier = Barrier(2)
    original_ask = Combined.ask
    class Uninstrumented:
        def json(self, _prompt):
            return {}
    class Scope:
        def __init__(self, *_args):
            pass
        def metadata(self):
            return {}
    def rules(_self, *_args, **_kwargs):
        barrier.wait(timeout=5)
        barrier.wait(timeout=5)
        return {'behaviour': 'figure', 'answered_by': 'rules'}
    c = _fake_ask_service(monkeypatch, rules)
    monkeypatch.setattr(combined_module, 'ProductScope', Scope)
    def controlled(_self, *_args, **_kwargs):
        barrier.wait(timeout=5)
        structured_call(Uninstrumented(), 'plan', version='v1', instructions='', data={}, question='Other',
                        contract=Contract, schema={}, legacy_prompt='', validate=False)
        barrier.wait(timeout=5)
        return {'behaviour': 'figure'}
    monkeypatch.setattr(Combined, '_governed', controlled)
    other = object.__new__(Combined)
    other.e = SimpleNamespace(business=lambda answer: answer)
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, invocation = ledger(tmp_path), identity(), identity()
    book.open_question(question)
    book.start_invocation(question, invocation)
    book.claim_invocation(question, invocation)
    def rules_pass():
        with usage_scope(book, question, invocation):
            return service.ask(c, None, 'Metric', 'alice', 'analysis', 'graph', cfg)
    def independent_pass():
        return other.governed('Other', 'bob', 'analysis', cfg) if other_route == 'agent' else original_ask(other, 'Other', 'bob', 'analysis', cfg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(rules_pass), pool.submit(independent_pass)
        assert first.result()['behaviour'] == second.result()['behaviour'] == 'figure'
    book.finish_question(question)
    assert book.report(question)['complete'] and book.report(question)['calls'] == 0
