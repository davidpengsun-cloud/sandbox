"""The provider-neutral streaming port through a deterministic Vertex transport."""
from dataclasses import replace
import io
import time
import pytest
from kge.adapters.model import chat as gateway
from kge.adapters.model.errors import ExternalCallError
from kge.adapters.model.providers import Chat, LLM, Truncated
from kge.ports import ChatMessage, ChatPart, ChatRequest, ModelProvider
from kge.services.ask.engine import runtrace


def request(messages=None, **changes):
    return replace(ChatRequest(instructions="Use tools", messages=messages or (ChatMessage("user", (ChatPart("text", "latest"),)),),
                               tools=({"name": "search", "parameters": {"type": "object"}},), input_tokens=100,
                               output_tokens=40, thinking={"level": "low"}), **changes)


def vertex(monkeypatch, payloads, counts=None):
    model = LLM("vertex-gemini", "gemini-3-flash", project="test")
    calls = []
    monkeypatch.setattr(Chat, "_gemini_target", lambda self: ("https://example.test/model:generateContent", {"authorization": "Bearer secret"}))
    def count(url, headers, body):
        calls.append((url, body))
        return {"totalTokens": counts.pop(0) if counts else 10}
    def stream(url, headers, body):
        calls.append((url, body))
        yield from payloads
    monkeypatch.setattr(model, "_post", count)
    monkeypatch.setattr(model, "_stream_post", stream)
    return model, calls


def chunk(parts=(), finish=None, usage=None):
    return {"modelVersion": "served", "candidates": [{"content": {"parts": list(parts)}, **({"finishReason": finish} if finish else {})}],
            **({"usageMetadata": usage} if usage else {})}


def test_parallel_calls_keep_each_signature_and_response_id(monkeypatch):
    model, calls = vertex(monkeypatch, [chunk([{"text": "Searching"}, {"functionCall": {"name": "search", "args": {"q": "nna"}}, "thoughtSignature": "sig-1"},
                                                    {"functionCall": {"name": "search", "args": {"q": "aum"}}, "thoughtSignature": "sig-2"}]),
                                        chunk(finish="STOP", usage={"promptTokenCount": 11, "candidatesTokenCount": 4, "thoughtsTokenCount": 2})])
    assert isinstance(model, ModelProvider)
    events = list(model.stream_chat(request()))
    a, b = events[1].part, events[2].part
    assert a.call_id != b.call_id and (a.signature, b.signature) == ("sig-1", "sig-2")
    assert (events[-1].tokens_in, events[-1].tokens_out, events[-1].tokens_thinking) == (11, 6, 2)
    turns = (ChatMessage("user", (ChatPart("text", "latest"),)), ChatMessage("assistant", (a, b)),
             ChatMessage("tool", (ChatPart("result", call_id=a.call_id, name=a.name, value={"hits": [1]}),)),
             ChatMessage("tool", (ChatPart("result", call_id=b.call_id, name=b.name, value={"hits": [2]}),)))
    list(model.stream_chat(request(turns)))
    body = calls[-1][1]
    assert [p["thoughtSignature"] for p in body["contents"][1]["parts"]] == ["sig-1", "sig-2"]
    assert [p["functionResponse"]["id"] for p in body["contents"][2]["parts"]] == [a.call_id, b.call_id]


def test_fragmented_parallel_arguments_keep_ids_and_signatures(monkeypatch):
    parts = [
        {"functionCall": {"id": "a", "name": "search", "partialArgs": [{"jsonPath": "$.query", "stringValue": "net ", "willContinue": True}], "willContinue": True}, "thoughtSignature": "sig-a"},
        {"functionCall": {"id": "b", "name": "search", "partialArgs": [{"jsonPath": "$.filters[0]", "stringValue": "retail"}], "willContinue": True}, "thoughtSignature": "sig-b"},
        {"functionCall": {"id": "a", "partialArgs": [{"jsonPath": "$.query", "stringValue": "new assets"}], "willContinue": False}},
        {"functionCall": {"id": "b", "partialArgs": [{"jsonPath": "$.filters[1]", "stringValue": "current"}], "willContinue": False}},
    ]
    model, _ = vertex(monkeypatch, [*(chunk([part]) for part in parts), chunk(finish="STOP")])
    events = list(model.stream_chat(request()))
    assert [(e.part.call_id, e.part.value, e.part.signature) for e in events if e.part] == [
        ("a", {"query": "net new assets"}, "sig-a"), ("b", {"filters": ["retail", "current"]}, "sig-b")]


def test_fragmented_arguments_reject_incomplete_or_conflicting_fields(monkeypatch):
    initial = {"functionCall": {"id": "a", "name": "search", "partialArgs": [{"jsonPath": "$.q", "stringValue": "x"}], "willContinue": True}}
    overwrite = {"functionCall": {"id": "a", "partialArgs": [{"jsonPath": "$.q", "stringValue": "y"}]}}
    model, _ = vertex(monkeypatch, [chunk([initial]), chunk([overwrite]), chunk(finish="STOP")])
    with pytest.raises(ExternalCallError, match="conflicting"):
        list(model.stream_chat(request()))
    model, _ = vertex(monkeypatch, [chunk([initial]), chunk(finish="STOP")])
    with pytest.raises(ExternalCallError, match="incomplete"):
        list(model.stream_chat(request()))


def test_unnamed_parallel_fragments_cannot_be_silently_combined(monkeypatch):
    first = {"functionCall": {"name": "search", "partialArgs": [{"jsonPath": "$.q", "stringValue": "one"}], "willContinue": True}}
    second = {"functionCall": {"name": "search", "partialArgs": [{"jsonPath": "$.other", "stringValue": "two"}]}}
    model, _ = vertex(monkeypatch, [chunk([first]), chunk([second]), chunk(finish="STOP")])
    with pytest.raises(ExternalCallError, match="ambiguous"):
        list(model.stream_chat(request()))


def test_unsupported_provider_metadata_is_not_silently_lost(monkeypatch):
    model, calls = vertex(monkeypatch, [])
    turns = (ChatMessage("user", (ChatPart("text", "question", metadata={"opaque": "original"}),)),)
    with pytest.raises(ValueError, match="unsupported connector metadata"):
        list(model.stream_chat(request(turns)))
    assert calls == []


def test_counter_reduces_complete_old_exchanges_never_latest(monkeypatch):
    turns = (ChatMessage("user", (ChatPart("text", "old"),)),
             ChatMessage("assistant", (ChatPart("call", call_id="c", name="search", value={}),)),
             ChatMessage("tool", (ChatPart("result", call_id="c", name="search", value={"content": "secret" * 1000}),)),
             ChatMessage("user", (ChatPart("text", "latest"),)))
    model, calls = vertex(monkeypatch, [chunk(finish="STOP")], counts=[150, 110, 30])
    result = list(model.stream_chat(request(turns)))
    assert result[-1].reduced == 2
    assert calls[-1][1]["contents"] == [{"role": "user", "parts": [{"text": "latest"}]}]
    assert calls[1][1]["contents"][2]["parts"][0]["functionResponse"]["response"] == {"notice": "Tool result omitted to fit the input budget."}


def test_latest_exchange_over_budget_and_invalid_tool_sequence_fail_closed(monkeypatch):
    model, calls = vertex(monkeypatch, [], counts=[101])
    with pytest.raises(gateway.ChatInputLimit, match="latest exchange"):
        list(model.stream_chat(request()))
    assert len(calls) == 1
    turns = (ChatMessage("user", (ChatPart("text", "x"),)), ChatMessage("assistant", (ChatPart("call", call_id="id", name="search", value={}),)),
             ChatMessage("tool", (ChatPart("result", call_id="wrong", name="search"),)))
    with pytest.raises(ValueError, match="unmatched"):
        list(model.stream_chat(request(turns)))
    with pytest.raises(gateway.ChatUnsupported):
        list(LLM("mock", "mock").stream_chat(request()))


def test_oversized_latest_tool_result_is_not_shortened(monkeypatch):
    turns = (ChatMessage("user", (ChatPart("text", "latest"),)),
             ChatMessage("assistant", (ChatPart("call", call_id="c", name="search", value={}),)),
             ChatMessage("tool", (ChatPart("result", call_id="c", name="search", value={"content": "private" * 1000}),)))
    model, calls = vertex(monkeypatch, [], counts=[101])
    with pytest.raises(gateway.ChatInputLimit):
        list(model.stream_chat(request(turns)))
    assert len(calls) == 1
    assert calls[0][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["content"] == "private" * 1000


def test_close_failure_and_active_run_trace(monkeypatch):
    model, _ = vertex(monkeypatch, [chunk([{"text": "a"}]), chunk(finish="STOP")])
    with runtrace.start("rules") as t:
        t.stage("understand")
        stream = model.stream_chat(request())
        assert next(stream).part.text == "a"
        stream.close()
        assert len(t.calls) == 1 and t.calls[0]["error"] == "cancelled" and t.calls[0]["stage"] == "understand"
        assert "latest" not in repr(t.calls)
    def broken(*args):
        yield chunk([{"text": "a"}])
        raise ConnectionError("private text")
    monkeypatch.setattr(model, "_stream_post", broken)
    with runtrace.start("rules") as t:
        with pytest.raises(ConnectionError):
            list(model.stream_chat(request()))
        assert len(t.calls) == 1 and t.calls[0]["error"] == "ConnectionError"


def test_final_accounting_event_is_traced_before_consumer_stops(monkeypatch):
    model, _ = vertex(monkeypatch, [chunk(finish="STOP")])
    with runtrace.start("rules") as t:
        stream = model.stream_chat(request())
        assert next(stream).finish == "STOP"
        assert len(t.calls) == 1 and t.calls[0]["ok"]
        stream.close()
        assert len(t.calls) == 1


def test_sse_framing_and_timeout_while_waiting(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    response = Response(b'data: {"candidates":\n' b'data: []}\n\n' b'data: {"usageMetadata":{"promptTokenCount":2}}\n\n')
    monkeypatch.setattr(gateway.http, "open_http", lambda *a, **k: response)
    assert list(gateway._stream_http("https://example.test", {}, {})) == [{"candidates": []}, {"usageMetadata": {"promptTokenCount": 2}}]
    assert response.closed
    model, _ = vertex(monkeypatch, [])
    monkeypatch.setattr(gateway, "model_total_timeout", lambda: 0.2)
    def slow(*args):
        time.sleep(.5)
        yield chunk(finish="STOP")
    monkeypatch.setattr(model, "_stream_post", slow)
    with runtrace.start("rules") as t:
        started = time.monotonic()
        with pytest.raises(ExternalCallError):
            list(model.stream_chat(request()))
        assert time.monotonic() - started < .4
        assert len(t.calls) == 1 and not t.calls[0]["ok"]


def test_truncated_tool_call_is_provisional_and_failure_is_traced(monkeypatch):
    model, _ = vertex(monkeypatch, [chunk([{"functionCall": {"name": "search", "args": {}}}]), chunk(finish="MAX_TOKENS")])
    with runtrace.start("rules") as t:
        stream = model.stream_chat(request())
        assert next(stream).part.kind == "call"
        with pytest.raises(Truncated):
            list(stream)
        assert len(t.calls) == 1 and not t.calls[0]["ok"]


def test_trusted_purpose_caps_and_explicit_thinking(monkeypatch):
    model, calls = vertex(monkeypatch, [chunk(finish="STOP")])
    monkeypatch.setenv("MODEL_CHAT_AGENT_INPUT_TOKENS", "30")
    monkeypatch.setenv("MODEL_CHAT_AGENT_OUTPUT_TOKENS", "20")
    monkeypatch.setenv("MODEL_CHAT_AGENT_THINKING_LEVEL", "medium")
    list(model.stream_chat(request(input_tokens=1000, output_tokens=1000, thinking={"level": "high"})))
    body = calls[-1][1]
    assert body["generationConfig"] == {"maxOutputTokens": 20, "temperature": 0, "thinkingConfig": {"thinkingLevel": "medium"}}
    model, calls = vertex(monkeypatch, [chunk(finish="STOP")], counts=[31])
    with pytest.raises(gateway.ChatInputLimit):
        list(model.stream_chat(request(input_tokens=1000)))
    assert len(calls) == 1
    with pytest.raises(ValueError, match="unknown chat purpose"):
        list(model.stream_chat(request(purpose="unapproved")))
    with pytest.raises(ValueError, match="thinking"):
        list(model.stream_chat(request(thinking={"tokens": True})))


def test_setting_validation_and_gemini25_default(monkeypatch):
    from kge.adapters.model.providers import ModelSettingError, check_settings
    monkeypatch.setenv("MODEL_CHAT_AGENT_INPUT_TOKENS", "-1")
    with pytest.raises(ModelSettingError, match="MODEL_CHAT_AGENT_INPUT_TOKENS"):
        check_settings()
    monkeypatch.delenv("MODEL_CHAT_AGENT_INPUT_TOKENS")
    model, calls = vertex(monkeypatch, [chunk(finish="STOP")])
    model.model = "gemini-2.5-flash"
    list(model.stream_chat(request(thinking=None)))
    assert "thinkingConfig" not in calls[-1][1]["generationConfig"]
    monkeypatch.setenv("MODEL_CHAT_AGENT_THINKING_TOKENS", "128")
    list(model.stream_chat(request(output_tokens=400, thinking={"tokens": 1000})))
    assert calls[-1][1]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 128}
    with pytest.raises(ValueError, match="minimum"):
        list(model.stream_chat(request(output_tokens=100, thinking={"tokens": 1000})))


def test_json_mode_preserves_conversational_defaults_and_passes_schema(monkeypatch):
    model, calls = vertex(monkeypatch, [chunk([{"text": '{"answer":"ok"}'}]), chunk(finish="STOP")])
    list(model.stream_chat(request(purpose="catalogue", tools=(), response_format="json")))
    assert calls[-1][1]["generationConfig"]["responseMimeType"] == "application/json"
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    list(model.stream_chat(request(response_format="json", response_schema=schema)))
    assert calls[-1][1]["generationConfig"]["responseJsonSchema"] == schema
    list(model.stream_chat(request()))
    assert "responseMimeType" not in calls[-1][1]["generationConfig"]
    with pytest.raises(ValueError, match="requires JSON"):
        list(model.stream_chat(request(response_schema=schema)))


def test_json_response_is_validated_before_any_text_is_released(monkeypatch):
    schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
    model, _ = vertex(monkeypatch, [chunk([{"text": '{"ans'}]), chunk([{"text": 'wer":"ok"}'}]), chunk(finish="STOP")])
    events = list(model.stream_chat(request(tools=(), response_format="json", response_schema=schema)))
    assert events[0].part.text == '{"answer":"ok"}' and events[-1].finish == "STOP"
    for bad in ('{"answer": 3}', '{"private'):
        model, _ = vertex(monkeypatch, [chunk([{"text": bad}]), chunk(finish="STOP")])
        stream = model.stream_chat(request(tools=(), response_format="json", response_schema=schema))
        with pytest.raises(ExternalCallError, match="invalid structured"):
            next(stream)


def test_schema_refs_are_local_only_and_bounded_before_model_calls(monkeypatch):
    model, calls = vertex(monkeypatch, [chunk(finish="STOP")])
    for schema in ({"$ref": "https://example.test/private"}, {"$dynamicRef": "http://127.0.0.1/schema"},
                   {"$id": "https://example.test/base", "type": "object"}):
        with pytest.raises(ValueError, match="local references"):
            list(model.stream_chat(request(response_format="json", response_schema=schema)))
    with pytest.raises(ValueError, match="too large"):
        list(model.stream_chat(request(response_format="json", response_schema={"description": "a" * 131073})))
    assert calls == []
    schema = {"$defs": {"value": {"type": "string"}}, "type": "object",
              "properties": {"$ref": {"$ref": "#/$defs/value"}}}
    assert gateway.checked_schema(schema) == schema


def test_thinking_representation_cannot_bypass_configured_cap(monkeypatch):
    model, _ = vertex(monkeypatch, [chunk(finish="STOP")])
    monkeypatch.setenv("MODEL_CHAT_AGENT_THINKING_TOKENS", "128")
    with pytest.raises(ValueError, match="unsupported"):
        list(model.stream_chat(request(output_tokens=400, thinking={"level": "high"})))
    monkeypatch.delenv("MODEL_CHAT_AGENT_THINKING_TOKENS")
    monkeypatch.setenv("MODEL_CHAT_AGENT_THINKING_LEVEL", "low")
    with pytest.raises(ValueError, match="unsupported"):
        list(model.stream_chat(request(output_tokens=400, thinking={"tokens": 500})))
    monkeypatch.setenv("MODEL_CHAT_AGENT_THINKING_TOKENS", "128")
    with pytest.raises(ValueError, match="either"):
        list(model.stream_chat(request(output_tokens=400, thinking=None)))


def test_unsigned_gemini3_replay_placeholder_only_on_first_call(monkeypatch):
    turns = (ChatMessage("user", (ChatPart("text", "question"),)),
             ChatMessage("assistant", (ChatPart("text", "work"), ChatPart("call", name="search", call_id="x", value={}),
                                        ChatPart("call", name="search", call_id="y", value={}))),
             ChatMessage("tool", (ChatPart("result", call_id="x", name="search"), ChatPart("result", call_id="y", name="search"))))
    model, calls = vertex(monkeypatch, [chunk(finish="STOP")])
    list(model.stream_chat(request(turns)))
    parts = calls[-1][1]["contents"][1]["parts"]
    assert parts[1]["thoughtSignature"] == "skip_thought_signature_validator" and "thoughtSignature" not in parts[2]
    signed = replace(turns[1], parts=(turns[1].parts[0], replace(turns[1].parts[1], signature="original"), turns[1].parts[2]))
    list(model.stream_chat(request((turns[0], signed, turns[2]))))
    parts = calls[-1][1]["contents"][1]["parts"]
    assert parts[1]["thoughtSignature"] == "original" and "thoughtSignature" not in parts[2]
    model.model = "gemini-2.5-flash"
    list(model.stream_chat(request(turns, thinking=None)))
    assert "thoughtSignature" not in calls[-1][1]["contents"][1]["parts"][1]


def test_standalone_span_records_safe_reduction_and_call_fields(monkeypatch):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from tests.tracing_setup import tracing_to
    model, _ = vertex(monkeypatch, [chunk(finish="STOP", usage={"promptTokenCount": 11, "candidatesTokenCount": 4, "thoughtsTokenCount": 2})])
    exporter = InMemorySpanExporter()
    with tracing_to(exporter):
        parent = gateway.trace.new_span("request")
        with gateway.trace.as_current(parent):
            list(model.stream_chat(request()))
        parent.end()
    span = next(s for s in exporter.get_finished_spans() if s.name == "kge.model")
    assert span.attributes["kge.stage"] == "agent" and span.attributes["kge.chat.input_budget"] == 100
    assert span.attributes["kge.chat.output_budget"] == 40 and span.attributes["kge.chat.reductions"] == 0
    assert span.attributes["kge.chat.finish_reason"] == "STOP" and span.attributes["gen_ai.usage.reasoning_tokens"] == 2
    assert "latest" not in repr(span.attributes) and "secret" not in repr(span.attributes)
