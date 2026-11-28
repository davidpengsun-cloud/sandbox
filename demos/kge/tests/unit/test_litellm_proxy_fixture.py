"""The offline proxy fixture's own wire contract; no LiteLLM connector exists in this slice."""
import http.client
import json
import urllib.error
import urllib.request
from dataclasses import asdict

import pytest

from kge.ports import ChatMessage, ChatPart, ChatRequest
from tests.litellm_proxy_fixture import Reply, completion, litellm_proxy, sse_events


def post(server, path, body, *, token="offline-fixture-token"):
    host, port = server.server_address
    request = urllib.request.Request(f"http://{host}:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return urllib.request.urlopen(request, timeout=2)


def delta(index, **kwargs):
    return {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": index, **kwargs}]}, "finish_reason": None}]}


def test_prescribed_count_and_structured_reply_keep_secrets_out_of_logs(capsys):
    count = {"total_tokens": 37, "source": "fixture-scripted"}
    output = completion(content='{"approved":true}', model="proxy-served", usage={"prompt_tokens": 37, "completion_tokens": 6,
                                                                                  "completion_tokens_details": {"reasoning_tokens": 2}})
    body = {"model": "deployment-alias", "messages": [{"role": "system", "content": "Use the schema."},
                                                      {"role": "user", "content": "fixture-private-question"}],
            "max_tokens": 300, "response_format": {"type": "json_schema", "json_schema": {
                "name": "Result", "strict": False, "schema": {"type": "object"}}}}
    with litellm_proxy([Reply("/utils/token_counter", json_body=count), Reply("/v1/chat/completions", json_body=output)]) as proxy:
        with post(proxy, "/utils/token_counter?call_endpoint=true", body) as response:
            assert json.load(response) == count
        with post(proxy, "/v1/chat/completions", body) as response:
            result = json.load(response)
        assert json.loads(result["choices"][0]["message"]["content"]) == {"approved": True}
        assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2
        assert [record["path"] for record in proxy.records] == ["/utils/token_counter", "/v1/chat/completions"]
        assert proxy.records[0]["query"] == "call_endpoint=true"
        assert proxy.records[0]["body"] == proxy.records[1]["body"] == body
        assert "offline-fixture-token" not in json.dumps(proxy.records)
    captured = capsys.readouterr()
    assert "offline-fixture-token" not in captured.out + captured.err
    assert "fixture-private-question" not in captured.out + captured.err


def test_stream_splits_unicode_and_interleaves_parallel_tool_arguments():
    events = (
        {"model": "proxy-served", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Café 🌍"}, "finish_reason": None}]},
        delta(0, id="call_A__thought__opaque", type="function", function={"name": "search", "arguments": '{"query":"net '}),
        delta(1, id="call_B", type="function", function={"name": "map_term", "arguments": '{"term":"new '}),
        delta(0, function={"arguments": 'assets"}'}), delta(1, function={"arguments": 'assets"}'}),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"model": "proxy-served", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4}}, "[DONE]",
    )
    encoded = sse_events(events)
    splits = (encoded.index("é".encode()) + 1, encoded.index(b"call_A") + 3)
    with litellm_proxy([Reply("/v1/chat/completions", events=events, splits=splits)]) as proxy:
        with post(proxy, "/v1/chat/completions", {"model": "alias", "stream": True, "stream_options": {"include_usage": True}}) as response:
            body = response.read()
    assert body == encoded
    frames = [frame.removeprefix("data: ") for frame in body.decode().strip().split("\n\n")]
    decoded = [json.loads(frame) for frame in frames[:-1]]
    assert frames[-1] == "[DONE]" and decoded[0]["choices"][0]["delta"]["content"] == "Café 🌍"
    first, second = (decoded[i]["choices"][0]["delta"]["tool_calls"][0] for i in (1, 2))
    assert (first["id"], second["id"]) == ("call_A__thought__opaque", "call_B")
    suffixes = [decoded[i]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] for i in (3, 4)]
    assert json.loads(first["function"]["arguments"] + suffixes[0]) == {"query": "net assets"}
    assert json.loads(second["function"]["arguments"] + suffixes[1]) == {"term": "new assets"}
    assert decoded[-2]["choices"][0]["finish_reason"] == "tool_calls" and decoded[-1]["usage"]["prompt_tokens"] == 10


def test_model_port_data_preserves_opaque_signatures_and_tool_ids():
    first = ChatPart(kind="call", call_id="a", name="search", value={"query": "NNA"}, signature="opaque-signature")
    second = ChatPart(kind="call", call_id="b", name="map_term", value={"term": "NNA"}, metadata={"provider_specific_fields": {"opaque": "value"}})
    request = ChatRequest(instructions="Use tools", messages=(ChatMessage("user", (ChatPart("text", "NNA?"),)),
                          ChatMessage("assistant", (first, second)),
                          ChatMessage("tool", (ChatPart(kind="result", call_id="a", name="search", value={"hits": []}),
                                               ChatPart(kind="result", call_id="b", name="map_term", value={"term": "NNA"})))),
                          tools=({"name": "search", "parameters": {"type": "object"}},), input_tokens=1000, output_tokens=300)
    raw = asdict(request)
    assert raw["messages"][1]["parts"][0]["signature"] == "opaque-signature"
    assert raw["messages"][1]["parts"][1]["metadata"]["provider_specific_fields"]["opaque"] == "value"
    assert [part["call_id"] for part in raw["messages"][2]["parts"]] == ["a", "b"]


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_scripted_failures_preserve_status_without_logging_inputs(status, capsys):
    with litellm_proxy([Reply("/v1/chat/completions", status=status, json_body={"error": "fixture failure"})]) as proxy:
        with pytest.raises(urllib.error.HTTPError) as failure:
            post(proxy, "/v1/chat/completions", {"message": "sensitive-fixture-input"})
        assert failure.value.code == status
        assert len(proxy.records) == 1 and proxy.records[0]["authenticated"]
    captured = capsys.readouterr()
    assert "offline-fixture-token" not in captured.out + captured.err
    assert "sensitive-fixture-input" not in captured.out + captured.err


def test_invalid_bearer_and_unknown_path_do_not_consume_response():
    with litellm_proxy([Reply("/v1/chat/completions", json_body=completion(content="ok"))]) as proxy:
        for path, token, status in [("/v1/chat/completions", "wrong", 401), ("/not-a-model", "offline-fixture-token", 404)]:
            with pytest.raises(urllib.error.HTTPError) as error:
                post(proxy, path, {"model": "alias"}, token=token)
            assert error.value.code == status
        with post(proxy, "/v1/chat/completions", {"model": "alias"}) as response:
            assert json.load(response)["choices"][0]["message"]["content"] == "ok"
        assert len(proxy.records) == 1


def test_incomplete_network_stream_is_not_a_complete_response():
    event = {"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]}
    with litellm_proxy([Reply("/v1/chat/completions", events=(event,), partial=True)]) as proxy:
        with post(proxy, "/v1/chat/completions", {"stream": True}) as response:
            with pytest.raises(http.client.IncompleteRead):
                response.read()


def test_unknown_usage_is_omitted_instead_of_zero():
    assert "usage" not in completion(content="No usage returned.")


def test_short_id_signature_is_transmitted_as_opaque_provider_field():
    event = delta(0, id="short-id", type="function", function={"name": "search", "arguments": "{}"},
                  provider_specific_fields={"thought_signature": "opaque-provider-signature"})
    with litellm_proxy([Reply("/v1/chat/completions", events=(event, "[DONE]"))]) as proxy:
        with post(proxy, "/v1/chat/completions", {"stream": True}) as response:
            first = response.readline().decode().removeprefix("data: ")
        tool = json.loads(first)["choices"][0]["delta"]["tool_calls"][0]
        assert tool["id"] == "short-id"
        assert tool["provider_specific_fields"]["thought_signature"] == "opaque-provider-signature"


def test_scripted_schema_failure_and_retry_record_both_requests():
    replies = [Reply("/v1/chat/completions", status=400, json_body={"error": "schema refused"}),
               Reply("/v1/chat/completions", json_body=completion(content='{"ok":true}'))]
    with litellm_proxy(replies) as proxy:
        schema = {"response_format": {"type": "json_schema"}}
        fallback = {"response_format": {"type": "json_object"}}
        with pytest.raises(urllib.error.HTTPError) as failure:
            post(proxy, "/v1/chat/completions", schema)
        assert failure.value.code == 400
        with post(proxy, "/v1/chat/completions", fallback) as response:
            assert json.loads(json.load(response)["choices"][0]["message"]["content"]) == {"ok": True}
        assert [r["body"]["response_format"]["type"] for r in proxy.records] == ["json_schema", "json_object"]


def test_fixture_can_send_invalid_json_without_changing_bytes():
    with litellm_proxy([Reply("/v1/chat/completions", raw_bytes=b'{"invalid":')]) as proxy:
        with post(proxy, "/v1/chat/completions", {"model": "alias"}) as response:
            with pytest.raises(json.JSONDecodeError):
                json.load(response)
