"""Offline tests for Claude-over-Vertex chat's port contract."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from kge.adapters.model import claude_chat
from kge.adapters.model.chat import ChatInputLimit
from kge.adapters.model.errors import ExternalCallError
from kge.ports import ChatMessage, ChatPart, ChatRequest


class Stream:
    def __init__(self, events):
        self.events, self.closed = events, False

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *_):
        self.closed = True


class Messages:
    def __init__(self, events, counts=(10,)):
        self.events, self.counts = events, list(counts)
        self.count_calls, self.stream_calls, self.manager = [], [], None

    def count_tokens(self, **fields):
        self.count_calls.append(fields)
        return {"input_tokens": self.counts.pop(0)}

    def stream(self, **fields):
        self.stream_calls.append(fields)
        self.manager = Stream(self.events)
        return self.manager


def harness(monkeypatch, events, counts=(10,)):
    monkeypatch.setattr(claude_chat, "chat_policy", lambda _: (100, 50, None, "low"))
    calls = []
    monkeypatch.setattr(claude_chat.trace, "llm_call", lambda *a, **k: calls.append((a, k)))
    messages = Messages(events, counts)
    model = SimpleNamespace(provider="vertex-claude", model="claude-haiku-5-5", name="vertex-claude:claude-haiku-5-5", max_tokens=40,
                            temperature=None, chat=SimpleNamespace(anthropic_vertex=lambda: SimpleNamespace(messages=messages)))
    return model, messages, calls


def req(*messages, **kwargs):
    return ChatRequest(instructions="Use the tools", messages=messages or (ChatMessage("user", (ChatPart("text", text="NNA?"),)),),
                       tools=({"name": "search", "description": "Search", "parameters": {"type": "object"}},),
                       input_tokens=kwargs.get("input_tokens", 80), output_tokens=kwargs.get("output_tokens", 30), thinking=kwargs.get("thinking"))


def signed_tool_events():
    return [
        {"type": "message_start", "message": {"model": "served-claude", "usage": {"input_tokens": 7, "cache_read_input_tokens": 3}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "HIDDEN_SECRET_REASONING"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-1"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Searching."}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "c1", "name": "search", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"query":'}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"NNA"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "content_block_start", "index": 3, "content_block": {"type": "tool_use", "id": "c2", "name": "search", "input": {"query": "AUM"}}},
        {"type": "content_block_stop", "index": 3},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    ]


def tool_events():
    events = signed_tool_events()[:1] + signed_tool_events()[5:]
    for event in events:
        if "index" in event:
            event["index"] -= 1
    return events


def test_parallel_calls_round_trip_and_trace(monkeypatch):
    model, sdk, calls = harness(monkeypatch, tool_events())
    results = list(claude_chat.stream(model, req()))
    text, first, second = [event.part for event in results[:-1]]
    assert text.text == "Searching." and [first.call_id, second.call_id] == ["c1", "c2"]
    assert first.value == {"query": "NNA"} and second.value == {"query": "AUM"}
    assert first.metadata is None and second.metadata is None
    assert results[-1].tokens_in == 10 and results[-1].tokens_out == 12 and results[-1].model == "served-claude"
    assert results[-1].tokens_thinking is None and sdk.manager.closed
    assert len(calls) == 1 and calls[0][1]["ok"] is True and "private" not in repr(calls)
    assert sdk.count_calls[0]["messages"] == sdk.stream_calls[0]["messages"]
    assert sdk.count_calls[0]["system"] == sdk.stream_calls[0]["system"]
    assert sdk.count_calls[0]["tools"] == sdk.stream_calls[0]["tools"]
    assert sdk.stream_calls[0]["max_tokens"] == 30
    follow = req(ChatMessage("user", (ChatPart("text", text="NNA?"),)),
                 ChatMessage("assistant", (text, first, second)),
                 ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={"hits": [1]}),)),
                 ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={"hits": [2]}),)))
    payload = claude_chat._body(follow, follow.messages, model.model, sample=None)
    assert [p["id"] for p in payload["messages"][1]["content"] if p["type"] == "tool_use"] == ["c1", "c2"]
    assert [p["tool_use_id"] for p in payload["messages"][2]["content"]] == ["c1", "c2"]


def test_budget_discards_old_complete_turns_without_changing_request(monkeypatch):
    model, sdk, calls = harness(monkeypatch, tool_events(), counts=(120, 40))
    original = req(ChatMessage("user", (ChatPart("text", text="old"),)), ChatMessage("assistant", (ChatPart("text", text="reply"),)),
                   ChatMessage("user", (ChatPart("text", text="new"),)))
    result = list(claude_chat.stream(model, original))
    assert result[-1].reduced == 1 and len(sdk.stream_calls[0]["messages"]) == 1
    assert len(original.messages) == 3 and sdk.stream_calls[0]["messages"][0]["content"][0]["text"] == "new"
    assert len(calls) == 1 and calls[0][1]["ok"]


def test_oversized_latest_exchange_never_starts_generation(monkeypatch):
    model, sdk, calls = harness(monkeypatch, [], counts=(101,))
    with pytest.raises(ChatInputLimit):
        list(claude_chat.stream(model, req()))
    assert not sdk.stream_calls and len(calls) == 1 and not calls[0][1]["ok"]


def test_private_thinking_with_tools_fails_closed_without_leaking(monkeypatch):
    model, sdk, calls = harness(monkeypatch, signed_tool_events())
    events = []
    with pytest.raises(ExternalCallError, match="continuation is unavailable") as caught:
        for event in claude_chat.stream(model, req()):
            events.append(event)
    assert not any(event.part and event.part.kind == "call" for event in events)
    assert "HIDDEN_SECRET_REASONING" not in repr(events) and "sig-1" not in repr(events)
    assert "HIDDEN_SECRET_REASONING" not in str(caught.value) and "sig-1" not in repr(calls)
    assert len(calls) == 1 and not calls[0][1]["ok"] and sdk.manager.closed


@pytest.mark.parametrize("mutate", [lambda e: e[:-1], lambda e: e[:-2], lambda e: e[:-2] + [{"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}, {"type": "message_stop"}]])
def test_incomplete_or_truncated_stream_never_emits_tool_calls(monkeypatch, mutate):
    model, sdk, calls = harness(monkeypatch, mutate(tool_events()))
    emitted = []
    with pytest.raises((ExternalCallError, RuntimeError)):
        for event in claude_chat.stream(model, req()):
            emitted.append(event)
    assert not any(event.part and event.part.kind == "call" for event in emitted)
    assert len(sdk.stream_calls) == 1 and sdk.manager.closed and len(calls) == 1 and not calls[0][1]["ok"]


def test_closing_partial_stream_closes_sdk_and_records_cancel(monkeypatch):
    model, sdk, calls = harness(monkeypatch, tool_events())
    stream = claude_chat.stream(model, req())
    assert next(stream).part.text == "Searching."
    stream.close()
    assert sdk.manager.closed and len(calls) == 1 and not calls[0][1]["ok"] and calls[0][1]["error"] == "cancelled"


def test_counter_failure_is_redacted_and_generation_never_starts(monkeypatch):
    model, sdk, calls = harness(monkeypatch, [])
    def failed_counter(**_fields):
        raise RuntimeError("private request and secret-token")
    sdk.count_tokens = failed_counter
    with pytest.raises(ExternalCallError, match="Vertex AI chat failed") as caught:
        list(claude_chat.stream(model, req()))
    assert "secret-token" not in str(caught.value) and not sdk.stream_calls
    assert len(calls) == 1 and not calls[0][1]["ok"] and "private request" not in repr(calls)


def test_stream_failure_after_text_is_not_retried_or_reported_as_success(monkeypatch):
    def broken():
        yield from tool_events()[:4]
        raise ConnectionError("secret-token")
    model, sdk, calls = harness(monkeypatch, broken())
    events = []
    with pytest.raises(ExternalCallError, match="Vertex AI unreachable") as caught:
        for event in claude_chat.stream(model, req()):
            events.append(event)
    assert "secret-token" not in str(caught.value)
    assert [event.part.text for event in events if event.part and event.part.kind == "text"] == ["Searching."]
    assert len(sdk.stream_calls) == 1 and sdk.manager.closed and len(calls) == 1 and not calls[0][1]["ok"]


def test_vertex_sdk_wire_contract_uses_count_and_stream_endpoints(monkeypatch):
    import json

    import httpx2
    from anthropic import AnthropicVertex

    events = tool_events()
    events[0]["message"].update({"id": "msg_fixture", "type": "message", "role": "assistant", "content": [],
                                 "stop_reason": None, "stop_sequence": None})
    events[0]["message"]["usage"]["output_tokens"] = 0
    events[-2]["delta"]["stop_sequence"] = None
    wire = "".join(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events).encode()
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path.endswith("/count-tokens:rawPredict"):
            return httpx2.Response(200, json={"input_tokens": 10})
        if request.url.path.endswith("/claude-haiku-5-5:streamRawPredict"):
            return httpx2.Response(200, content=wire, headers={"content-type": "text/event-stream"})
        return httpx2.Response(404)

    model, _fake, traces = harness(monkeypatch, [])
    sdk = AnthropicVertex(region="global", project_id="test-project", access_token=uuid.uuid4().hex,
                          base_url="https://vertex.invalid", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    model.chat = SimpleNamespace(anthropic_vertex=lambda: sdk)
    try:
        events_out = list(claude_chat.stream(model, req()))
    finally:
        sdk.close()
    assert len(seen) == 2
    assert seen[0][0].endswith("/count-tokens:rawPredict")
    assert seen[1][0].endswith("/claude-haiku-5-5:streamRawPredict")
    assert seen[0][1]["messages"] == seen[1][1]["messages"]
    assert seen[0][1]["tools"] == seen[1][1]["tools"]
    assert [event.part.call_id for event in events_out if event.part and event.part.kind == "call"] == ["c1", "c2"]
    assert len(traces) == 1 and traces[0][1]["ok"]


def test_count_and_stream_open_retry_once_before_any_output(monkeypatch):
    model, sdk, calls = harness(monkeypatch, tool_events())
    real_count, real_stream = sdk.count_tokens, sdk.stream
    attempts = {"count": 0, "open": 0}
    def count(**fields):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("secret-token")
        return real_count(**fields)
    def open_stream(**fields):
        attempts["open"] += 1
        if attempts["open"] == 1:
            raise ConnectionError("secret-token")
        return real_stream(**fields)
    sdk.count_tokens, sdk.stream = count, open_stream
    result = list(claude_chat.stream(model, req()))
    assert attempts == {"count": 2, "open": 2} and result[-1].finish == "STOP"
    assert len(calls) == 1 and calls[0][1]["ok"] and "secret-token" not in repr(calls)


def test_transient_first_read_retries_but_after_text_does_not(monkeypatch):
    model, sdk, calls = harness(monkeypatch, tool_events())
    streams = []
    def failed():
        raise ConnectionError("secret-token")
        yield
    def open_stream(**_fields):
        stream = Stream(failed() if not streams else tool_events())
        streams.append(stream)
        return stream
    sdk.stream = open_stream
    result = list(claude_chat.stream(model, req()))
    assert len(streams) == 2 and all(stream.closed for stream in streams)
    assert result[-1].finish == "STOP" and len(calls) == 1 and calls[0][1]["ok"]


def test_json_mode_without_schema_is_refused_without_provider_call(monkeypatch):
    model, sdk, calls = harness(monkeypatch, tool_events())
    from dataclasses import replace
    with pytest.raises(ValueError, match="needs a response schema"):
        list(claude_chat.stream(model, replace(req(), response_format="json")))
    assert not sdk.count_calls and not sdk.stream_calls and len(calls) == 1 and not calls[0][1]["ok"]


def json_events(chunks, *, finish="end_turn"):
    events = [{"type": "message_start", "message": {"model": "served", "usage": {"input_tokens": 5}}},
              {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}]
    events += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}} for chunk in chunks]
    return events + [{"type": "content_block_stop", "index": 0}, {"type": "message_delta", "delta": {"stop_reason": finish},
                     "usage": {"output_tokens": 4}}, {"type": "message_stop"}]


def test_generic_schema_is_sent_counted_and_validated_before_json_emission(monkeypatch):
    from dataclasses import replace
    model, sdk, calls = harness(monkeypatch, json_events(['{"answer":', '"yes"}']))
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}
    question = replace(req(), tools=(), response_format="json", response_schema=schema)
    events = list(claude_chat.stream(model, question))
    assert [event.part.text for event in events if event.part] == ['{"answer":"yes"}']
    assert sdk.count_calls[0]["output_config"] == sdk.stream_calls[0]["output_config"]
    assert sdk.stream_calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert len(calls) == 1 and calls[0][1]["ok"]


@pytest.mark.parametrize("chunks,finish", [(['{"answer":'], "end_turn"), (['{"answer":1}'], "end_turn"),
                                            (['{"answer":"yes"}'], "max_tokens")])
def test_bad_json_schema_or_truncation_never_emits_partial_output(monkeypatch, chunks, finish):
    from dataclasses import replace
    model, _sdk, calls = harness(monkeypatch, json_events(chunks, finish=finish))
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}
    question = replace(req(), tools=(), response_format="json", response_schema=schema)
    emitted = []
    with pytest.raises((ExternalCallError, RuntimeError)):
        for event in claude_chat.stream(model, question):
            emitted.append(event)
    assert not emitted and len(calls) == 1 and not calls[0][1]["ok"]


def test_slow_close_never_blocks_caller_deadline(monkeypatch):
    import threading
    import time

    model, sdk, calls = harness(monkeypatch, [])
    monkeypatch.setattr(claude_chat, "model_total_timeout", lambda: .08)
    closed = threading.Event()
    class SlowClose(Stream):
        def __exit__(self, *_):
            time.sleep(.4)
            closed.set()
    def slow_events():
        yield {"type": "message_start", "message": {"model": "served", "usage": {"input_tokens": 1}}}
        time.sleep(.35)
    manager = SlowClose(slow_events())
    sdk.stream = lambda **_: manager
    started = time.monotonic()
    with pytest.raises(ExternalCallError):
        list(claude_chat.stream(model, req()))
    elapsed = time.monotonic() - started
    assert elapsed < .3 and len(calls) == 1 and not calls[0][1]["ok"]
    assert closed.wait(1)


def test_late_stream_open_is_closed_after_caller_timeout(monkeypatch):
    import threading
    import time

    model, sdk, calls = harness(monkeypatch, [])
    monkeypatch.setattr(claude_chat, "model_total_timeout", lambda: .08)
    closed = threading.Event()
    class SlowOpen(Stream):
        def __enter__(self):
            time.sleep(.2)
            return iter(self.events)
        def __exit__(self, *_):
            closed.set()
    manager = SlowOpen([])
    sdk.stream = lambda **_: manager
    started = time.monotonic()
    with pytest.raises(ExternalCallError):
        list(claude_chat.stream(model, req()))
    assert time.monotonic() - started < .18 and len(calls) == 1 and not calls[0][1]["ok"]
    assert closed.wait(1)


def test_standalone_model_span_contains_safe_budget_and_outcome(monkeypatch):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from kge.pipeline import trace
    from tests.tracing_setup import tracing_to

    real_trace = trace.llm_call
    model, _sdk, _calls = harness(monkeypatch, tool_events())
    monkeypatch.setattr(claude_chat.trace, "llm_call", real_trace)
    exporter = InMemorySpanExporter()
    with tracing_to(exporter):
        parent = trace.new_span("gateway")
        with trace.as_current(parent):
            list(claude_chat.stream(model, req()))
        parent.end()
        model_spans = [s for s in exporter.get_finished_spans() if s.name == "kge.model"]
        assert len(model_spans) == 1
        attrs = model_spans[0].attributes
        assert attrs["kge.stage"] == "agent" and attrs["kge.chat.output_budget"] == 30
        assert attrs["kge.chat.finish_reason"] == "STOP" and attrs["kge.outcome"] == "ok"
        assert "private" not in repr(attrs) and "sig-1" not in repr(attrs)


def test_invalid_input_never_reaches_trace_detail(monkeypatch):
    from dataclasses import replace

    model, _sdk, calls = harness(monkeypatch, [])
    bad = replace(req(), input_tokens="SECRET_INPUT", thinking={"value": "SECRET_THINKING"}, purpose="SECRET_PURPOSE")
    with pytest.raises(ValueError):
        list(claude_chat.stream(model, bad))
    assert len(calls) == 1 and not calls[0][1]["ok"]
    assert "SECRET_" not in repr(calls) and calls[0][1]["detail"]["chat_purpose"] == "chat"


def _scope_binding(*, principal="person-a", conversation="thread-a", model="claude-haiku-5-5"):
    from kge.adapters.model import ContinuationBinding
    return ContinuationBinding(tenant="tenant-a", principal=principal, app="cerebro", conversation=conversation,
                               purpose="agent", provider="vertex-claude", model=model, prefix="0" * 64)


def test_private_thinking_is_sealed_and_replayed_only_in_trusted_context(monkeypatch):
    import base64
    from dataclasses import replace
    from kge.adapters.model import ContinuationInvalid, Continuations, continuation_scope
    codec = Continuations(b"x" * 32, max_age_seconds=600)
    model, sdk, traces = harness(monkeypatch, signed_tool_events())
    with continuation_scope(codec, _scope_binding()):
        result = list(claude_chat.stream(model, req()))
        parts = [e.part for e in result if e.part]
        text, first, second = parts
        token = first.metadata["claude_continuation"]
        assert second.metadata is None
        wire = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        assert b"HIDDEN_SECRET_REASONING" not in wire and b"sig-1" not in wire
        assert "HIDDEN_SECRET_REASONING" not in repr(parts) and "sig-1" not in repr(parts) and "HIDDEN_SECRET_REASONING" not in repr(traces)
        follow = req(ChatMessage("user", (ChatPart("text", text="NNA?"),)), ChatMessage("assistant", (text, first, second)),
                     ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={"hits": [1]}),)),
                     ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={"hits": [2]}),)))
        follow = claude_chat._policy(model, follow)
        body = claude_chat._body(follow, follow.messages, model.model, sample=model.temperature)
        assert body["messages"][1]["content"][0]["thinking"] == "HIDDEN_SECRET_REASONING"
        assert body["messages"][1]["content"][0]["signature"] == "sig-1"
        tampered_call = replace(first, value={"query": "wrong"})
        tampered_message = replace(follow.messages[1], parts=(text, tampered_call, second))
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(follow, (follow.messages[0], tampered_message, *follow.messages[2:]), model.model, sample=model.temperature)
        changed_prefix = replace(follow.messages[0], parts=(ChatPart("text", "different question"),))
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(follow, (changed_prefix, *follow.messages[1:]), model.model, sample=model.temperature)
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(replace(follow, output_tokens=follow.output_tokens - 1), follow.messages, model.model, sample=model.temperature)
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(follow, follow.messages, model.model, sample=0.4)
    with pytest.raises(ExternalCallError, match="continuation is unavailable"):
        claude_chat._body(follow, follow.messages, model.model, sample=model.temperature)
    with continuation_scope(codec, _scope_binding(principal="person-b")):
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(follow, follow.messages, model.model, sample=model.temperature)
    with continuation_scope(codec, _scope_binding(conversation="thread-b")):
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(follow, follow.messages, model.model, sample=model.temperature)


def test_secure_continuation_expires_and_reduction_refuses_prefix_change(monkeypatch):
    from dataclasses import replace
    from kge.adapters.model import ContinuationInvalid, Continuations, continuation_scope
    clock = [1000]
    codec = Continuations(b"y" * 32, max_age_seconds=30, clock=lambda: clock[0])
    model, sdk, traces = harness(monkeypatch, signed_tool_events())
    history = (ChatMessage("user", (ChatPart("text", "old"),)), ChatMessage("assistant", (ChatPart("text", "old answer"),)))
    with continuation_scope(codec, _scope_binding()):
        result = list(claude_chat.stream(model, req(*history, ChatMessage("user", (ChatPart("text", "NNA?"),)))))
        text, first, second = [event.part for event in result if event.part]
        follow = req(*history, ChatMessage("user", (ChatPart("text", text="NNA?"),)), ChatMessage("assistant", (text, first, second)),
                     ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={}),)),
                     ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={}),)))
        capped = replace(claude_chat._policy(model, follow), input_tokens=1)
        sdk.counts.append(10)
        with pytest.raises(ChatInputLimit, match="cannot be safely shortened"):
            list(claude_chat.stream(model, capped))
        clock[0] += 31
        with pytest.raises(ContinuationInvalid):
            claude_chat._body(claude_chat._policy(model, follow), follow.messages, model.model, sample=model.temperature)


def test_secure_continuation_survives_separate_authorized_requests(monkeypatch):
    from contextvars import copy_context
    from kge.adapters.model import Continuations, continuation_scope
    codec = Continuations(b"z" * 32, max_age_seconds=60)
    model, sdk, traces = harness(monkeypatch, signed_tool_events())
    with continuation_scope(codec, _scope_binding()):
        result = copy_context().run(list, claude_chat.stream(model, req()))
    text, first, second = [event.part for event in result if event.part]
    follow = req(ChatMessage("user", (ChatPart("text", text="NNA?"),)), ChatMessage("assistant", (text, first, second)),
                 ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={}),)),
                 ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={}),)))
    with continuation_scope(codec, _scope_binding()):
        governed = claude_chat._policy(model, follow)
        body = copy_context().run(claude_chat._body, governed, governed.messages, model.model, sample=model.temperature)
    assert body["messages"][1]["content"][0]["type"] == "thinking"


def test_secure_replay_rejects_boolean_integer_substitution_at_any_depth(monkeypatch):
    from dataclasses import replace
    from kge.adapters.model import ContinuationInvalid, Continuations, continuation_scope
    events = signed_tool_events()
    deltas = [event for event in events if event.get("index") == 2 and event.get("delta", {}).get("type") == "input_json_delta"]
    deltas[0]["delta"]["partial_json"] = '{"flag":true,"nested":[false,{"value":true}]}'
    deltas[1]["delta"]["partial_json"] = ""
    model, sdk, traces = harness(monkeypatch, events)
    with continuation_scope(Continuations(b"v" * 32, max_age_seconds=60), _scope_binding()):
        text, first, second = [event.part for event in claude_chat.stream(model, req()) if event.part]
        prefix = ChatMessage("user", (ChatPart("text", "NNA?"),))
        results = (ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={}),)),
                   ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={}),)))
        for changed in ({"flag": 1, "nested": [False, {"value": True}]},
                        {"flag": True, "nested": [0, {"value": True}]},
                        {"flag": True, "nested": [False, {"value": 1}]}):
            assistant = ChatMessage("assistant", (text, replace(first, value=changed), second))
            follow = claude_chat._policy(model, req(prefix, assistant, *results))
            with pytest.raises(ContinuationInvalid):
                claude_chat._body(follow, follow.messages, model.model, sample=model.temperature)


def test_secure_replay_handles_text_interleaved_between_parallel_calls(monkeypatch):
    from kge.adapters.model import Continuations, continuation_scope
    events = signed_tool_events()
    for event in events:
        if event.get("index") == 3:
            event["index"] = 4
    insert = next(i for i, event in enumerate(events) if event.get("index") == 4)
    events[insert:insert] = [
        {"type": "content_block_start", "index": 3, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 3, "delta": {"type": "text_delta", "text": " More."}},
        {"type": "content_block_stop", "index": 3},
    ]
    model, sdk, traces = harness(monkeypatch, events)
    with continuation_scope(Continuations(b"t" * 32, max_age_seconds=60), _scope_binding()):
        returned = [event.part for event in claude_chat.stream(model, req()) if event.part]
        text1, text2, first, second = returned
        follow = req(ChatMessage("user", (ChatPart("text", "NNA?"),)),
                     ChatMessage("assistant", (ChatPart("text", text1.text + text2.text), first, second)),
                     ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={}),
                                          ChatPart("result", call_id="c2", name="search", value={}))))
        governed = claude_chat._policy(model, follow)
        body = claude_chat._body(governed, governed.messages, model.model, sample=model.temperature)
        assert [part["type"] for part in body["messages"][1]["content"]] == ["thinking", "text", "tool_use", "text", "tool_use"]


def test_long_history_reduction_recipe_survives_parallel_and_multiple_tool_rounds(monkeypatch):
    from dataclasses import replace
    from kge.adapters.model import ContinuationInvalid, Continuations, continuation_scope
    history = tuple(item for i in range(21) for item in (
        ChatMessage("user", (ChatPart("text", f"old question {i}"),)),
        ChatMessage("assistant", (ChatPart("text", f"old answer {i}"),)),
    ))
    latest = ChatMessage("user", (ChatPart("text", "NNA?"),))
    codec = Continuations(b"l" * 32, max_age_seconds=60)
    model, sdk, traces = harness(monkeypatch, signed_tool_events(), counts=(120, 40))
    with continuation_scope(codec, _scope_binding()):
        first_result = list(claude_chat.stream(model, req(*history, latest)))
        text1, first, second = [event.part for event in first_result if event.part]
        assert first.metadata["reduction"] == {"drop": 1, "omit": []}
        assert len(history) == 42
        second_events = signed_tool_events()
        for event in second_events:
            block = event.get("content_block", {})
            if block.get("id") == "c1": block["id"] = "c3"
            if block.get("id") == "c2": block["id"] = "c4"
        sdk.events, sdk.counts = second_events, [40]
        tool_results1 = (ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={"hits": [1]}),)),
                         ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={"hits": [2]}),)))
        follow = req(*history, latest, ChatMessage("assistant", (text1, first, second)), *tool_results1)
        second_result = list(claude_chat.stream(model, follow))
        text2, third, fourth = [event.part for event in second_result if event.part]
        assert third.metadata["reduction"] == first.metadata["reduction"]
        sent = sdk.stream_calls[-1]["messages"]
        assert sent[0]["content"][0]["text"] == "old question 1"
        assert any(item.get("type") == "thinking" for item in sent[-2]["content"])
        sdk.events, sdk.counts = tool_events(), [40]
        tool_results2 = (ChatMessage("tool", (ChatPart("result", call_id="c3", name="search", value={}),)),
                         ChatMessage("tool", (ChatPart("result", call_id="c4", name="search", value={}),)))
        final = req(*history, latest, ChatMessage("assistant", (text1, first, second)), *tool_results1,
                    ChatMessage("assistant", (text2, third, fourth)), *tool_results2)
        assert list(claude_chat.stream(model, final))[-1].finish == "STOP"
        bad_metadata = {**first.metadata, "reduction": {"drop": 2, "omit": []}}
        bad_call = replace(first, metadata=bad_metadata)
        altered = req(*history, latest, ChatMessage("assistant", (text1, bad_call, second)), *tool_results1)
        before = len(sdk.count_calls)
        with pytest.raises(ContinuationInvalid):
            list(claude_chat.stream(model, altered))
        assert len(sdk.count_calls) == before


def test_old_tool_result_reduction_recipe_replays_while_saved_history_stays_whole(monkeypatch):
    from kge.adapters.model import ContinuationInvalid, Continuations, continuation_scope
    large = {"result": "private-old-contents" * 500}
    old = (ChatMessage("user", (ChatPart("text", "old"),)),
           ChatMessage("assistant", (ChatPart("call", call_id="old-call", name="search", value={}),)),
           ChatMessage("tool", (ChatPart("result", call_id="old-call", name="search", value=large),)),
           ChatMessage("assistant", (ChatPart("text", "old answer"),)))
    latest = ChatMessage("user", (ChatPart("text", "NNA?"),))
    codec = Continuations(b"m" * 32, max_age_seconds=60)
    model, sdk, traces = harness(monkeypatch, signed_tool_events(), counts=(120, 40))
    with continuation_scope(codec, _scope_binding()):
        first_result = list(claude_chat.stream(model, req(*old, latest)))
        text, first, second = [event.part for event in first_result if event.part]
        recipe = first.metadata["reduction"]
        assert recipe == {"drop": 0, "omit": [{"turn": 0, "message": 2, "part": 0, "call_id": "old-call"}]}
        assert old[2].parts[0].value == large
        sdk.events, sdk.counts = tool_events(), [40]
        follow = req(*old, latest, ChatMessage("assistant", (text, first, second)),
                     ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={}),)),
                     ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={}),)))
        assert list(claude_chat.stream(model, follow))[-1].finish == "STOP"
        sent = sdk.stream_calls[-1]["messages"]
        assert "omitted to fit" in sent[2]["content"][0]["content"]
        assert "private-old-contents" not in repr(sent)
        malicious = {"drop": 0, "omit": [{"turn": 1, "message": 0, "part": 0, "call_id": "old-call"}]}
        from dataclasses import replace
        bad = replace(first, metadata={**first.metadata, "reduction": malicious})
        altered = req(*old, latest, ChatMessage("assistant", (text, bad, second)), *follow.messages[-2:])
        before = len(sdk.count_calls)
        with pytest.raises(ContinuationInvalid):
            list(claude_chat.stream(model, altered))
        assert len(sdk.count_calls) == before


def test_completed_turns_drop_private_continuation_before_a_new_question(monkeypatch):
    from kge.adapters.model import Continuations, continuation_scope
    clock = [1000]
    codec = Continuations(b"n" * 32, max_age_seconds=1, clock=lambda: clock[0])
    model, sdk, traces = harness(monkeypatch, signed_tool_events())
    with continuation_scope(codec, _scope_binding()):
        first_result = list(claude_chat.stream(model, req()))
    text, first, second = [event.part for event in first_result if event.part]
    old = (ChatMessage("user", (ChatPart("text", "NNA?"),)), ChatMessage("assistant", (text, first, second)),
           ChatMessage("tool", (ChatPart("result", call_id="c1", name="search", value={}),)),
           ChatMessage("tool", (ChatPart("result", call_id="c2", name="search", value={}),)),
           ChatMessage("assistant", (ChatPart("text", "Answer complete."),)))
    clock[0] += 2
    sdk.events, sdk.counts = tool_events(), [40]
    new_question = req(*old, ChatMessage("user", (ChatPart("text", "A new question"),)))
    assert list(claude_chat.stream(model, new_question))[-1].finish == "STOP"
    sent = sdk.stream_calls[-1]["messages"]
    assert all(part.get("type") not in ("thinking", "redacted_thinking") for item in sent for part in item["content"])
    assert "claude_continuation" not in repr(sent)
    assert old[1].parts[1].metadata["claude_continuation"]
