"""A structured call whose schema the provider refuses, and JSON mode (#561).

    .venv/bin/python -m pytest tests/unit/test_model_schema_fallback.py -q

On Vertex AI, Gemini 3.1 Pro refused Ask's plan call with HTTP 400 "The input token count exceeds the maximum number of
tokens allowed (1048576)", having inlined every $ref of the schema, and the full plan with HTTP 400 "Invalid
response_json_schema: Limits exceeded while trying to flatten schema". So a request in JSON mode (ModelRequest.json_mode)
is sent no schema as structured output, the schema shown in its system instruction instead; Gemini's schema is unrolled
one level, not three; and a call whose schema is refused is made again once in JSON mode. Every other failure is raised
as it was. On Gemini 3 and later the planner itself is sent in JSON mode: Vertex AI answered its schema with a bare HTTP
400 "Request contains an invalid argument", which on a request that sent a schema is a refused schema too.

Nothing leaves the process: a REST provider's HTTP call is a stand-in that answers in the provider's place, through the
real post_json, AnthropicVertex sends to an HTTP transport that answers in Vertex AI's place, and the service account is a
stand-in.
"""
from __future__ import annotations

import io
import json
import urllib.error
from typing import Literal

import httpx2
import pytest

from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded
from kge.adapters.model import LLM, NotJSON, Refused, Truncated, model_for
from kge.adapters.model.errors import carries_schema, schema_refused
from kge.adapters.model.providers import GEMINI_UNROLL_DEPTH, JSON_MODE_SCHEMA, UNROLL_DEPTH, Chat, json_mode_call, post, provider_schema
from kge.ports import ModelReply, ModelRequest
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.modelcall import Contract, ContractFailure, structured_retry

TOKEN = "ya29.stand-in-token"
PROJECT = "pilot-project"
KEY = "stand-in-key"
PRO = "gemini-3.1-pro-preview"
ARGON = "gemini-4-argon"
FLATTEN = "Invalid response_json_schema: Limits exceeded while trying to flatten schema. Schema is too complex to process."
TOKEN_COUNT = "The input token count exceeds the maximum number of tokens allowed (1048576); input token count: 3571917"
GENERIC = "Request contains an invalid argument."
INSTRUCTIONS = "Reply with the contract alone."
CLEARED = ("MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_MAX_TOKENS", "MODEL_SEED", "MOCK_MODE", "VERTEX_REGION",
           "VERTEX_PROJECT", "CONTEXT_MODEL_TIER", "ASK_UNKNOWN_WORD", "ASK_MODEL_CORRECTIONS",
           *(f"ASK_{call}_{part}" for call in ("PLAN", "CLASSIFY", "WORDING") for part in ("MAX_TOKENS", "THINKING_TOKENS", "THINKING_LEVEL")),
           *(f"MODEL{tier}_THINKING_{part}" for tier in ("", "_HIGH", "_MEDIUM", "_LOW") for part in ("TOKENS", "LEVEL")),
           *(f"MODEL_{tier}_{part}" for tier in ("HIGH", "MEDIUM", "LOW") for part in ("PROVIDER", "NAME", "API_KEY", "BASE_URL", "MAX_TOKENS")))
SCHEMA = {"title": "Contract", "type": "object", "required": ["filter"], "properties": {"filter": {"$ref": "#/$defs/Node"}},
          "$defs": {"Node": {"title": "Node", "type": "object", "x-ids": "vocabulary", "required": ["kind"],
                             "properties": {"kind": {"const": "all"},
                                            "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}}}}}}
SHOWN = {"type": "object", "required": ["filter"], "properties": {"filter": {"$ref": "#/$defs/Node"}},
         "$defs": {"Node": {"type": "object", "required": ["kind"],
                            "properties": {"kind": {"enum": ["all"]}, "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}}}}}}
JSON_MODE_INSTRUCTIONS = (f"{INSTRUCTIONS}\n\nReply with one JSON object that matches this JSON schema:\n"
                          + json.dumps(SHOWN, separators=(",", ":"), sort_keys=True))
ANSWER = '{"filter": {"kind": "all"}}'
REST = ("gemini", "vertex-gemini", "anthropic", "openai", "ollama")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No call leaves the process, and no setting from the caller's environment steers a test."""
    def no_network(*a, **k):
        raise AssertionError("a model call reached the network")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    monkeypatch.setattr("kge.adapters.http.open_http", no_network)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr("google.auth.default", no_network)
    monkeypatch.setattr(Chat, "token", lambda self: (TOKEN, PROJECT))
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "none")


def request(**fields) -> ModelRequest:
    """A structured request in schema mode: the classifier's call, which Gemini is still sent with its schema; the
    planner is sent to Gemini 3 and later in JSON mode from the first attempt (#561)."""
    return ModelRequest(**{"call": "classify", "version": "test-v1", "instructions": INSTRUCTIONS, "content": "## Question\nRevenue?",
                           "schema": SCHEMA, "contract": "Contract", "legacy_prompt": "legacy\n{}", **fields})


def reply_for(provider: str, text: str = ANSWER) -> dict:
    """A provider's successful reply holding text."""
    if provider in ("gemini", "vertex-gemini"):
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                "modelVersion": "served-001", "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 9}}
    if provider in ("anthropic", "vertex-claude"):
        return claude_reply(text)
    return {"model": "served", "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 9}}


def claude_reply(text: str = ANSWER, stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_standin", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 40, "output_tokens": 9}}


class Answer:
    """An HTTP reply's body, as http.open_http gives it."""

    def __init__(self, data: dict):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self.data).encode()


def refusal(status: int, message: str):
    """An HTTP error whose JSON body carries message, as Google, Anthropic and OpenAI send one."""
    return status, message


def model(monkeypatch, provider: str, name: str, *outcomes) -> tuple[LLM, list[dict]]:
    """The context agent's model on provider, its REST calls going through the real post_json to a stand-in for the
    provider that answers each call with the next outcome: a reply (a dict) or an HTTP error (status, message)."""
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("MODEL_NAME", name)
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    sent: list[dict] = []
    queue = list(outcomes)

    def open_http(req, timeout=None):
        sent.append({"url": req.full_url, "body": json.loads(req.data)})
        outcome = queue.pop(0) if queue else reply_for(provider)
        if isinstance(outcome, tuple):
            status, message = outcome
            body = json.dumps({"error": {"code": status, "message": message}}).encode()
            raise urllib.error.HTTPError(req.full_url, status, "refused", {}, io.BytesIO(body))
        return Answer(outcome)
    monkeypatch.setattr("kge.adapters.http.open_http", open_http)
    llm = model_for("context")
    llm._post = post
    return llm, sent


class Account:
    valid, token, expired = True, TOKEN, False

    def refresh(self, request) -> None:
        pass


def vertex_claude(monkeypatch, *outcomes) -> tuple[LLM, list[dict]]:
    """Claude on Vertex AI through the Anthropic SDK, its HTTP calls answered in Vertex AI's place in turn: a reply (a
    dict) or an HTTP error (status, message) in Vertex AI's error body."""
    monkeypatch.setattr("google.auth.default", lambda scopes=None, **k: (Account(), PROJECT))
    sent: list[dict] = []
    queue = list(outcomes)

    def handle(self, req: httpx2.Request) -> httpx2.Response:
        sent.append({"url": str(req.url), "body": json.loads(req.content)})
        outcome = queue.pop(0) if queue else claude_reply()
        if isinstance(outcome, tuple):
            status, message = outcome
            return httpx2.Response(status, json={"error": {"code": status, "message": message, "status": "INVALID_ARGUMENT"}},
                                   request=req)
        return httpx2.Response(200, json=outcome, request=req)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", handle)
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-claude")
    return model_for("context"), sent


def sent_schema(provider: str, body: dict) -> dict | None:
    """The schema a body carries as structured output, None where it carries none."""
    if provider in ("gemini", "vertex-gemini"):
        return body["generationConfig"].get("responseJsonSchema")
    if provider in ("anthropic", "vertex-claude"):
        return (body.get("output_config") or {}).get("format", {}).get("schema")
    return (body["response_format"].get("json_schema") or {}).get("schema")


def instructions_of(provider: str, body: dict) -> str:
    if provider in ("gemini", "vertex-gemini"):
        return body["systemInstruction"]["parts"][0]["text"]
    if provider in ("anthropic", "vertex-claude"):
        return body["system"]
    return body["messages"][0]["content"]


def traced(call) -> dict:
    with runtrace.start("ask") as t:
        try:
            call()
        finally:
            rt = t.finish({})
    return rt


@pytest.mark.parametrize("provider", REST)
def test_a_json_mode_request_sends_no_schema_and_shows_it_in_the_system_instruction(monkeypatch, provider):
    llm, sent = model(monkeypatch, provider, PRO if "gemini" in provider else "a-model")
    holder: list = []
    rt = traced(lambda: holder.append(llm.structured(request(json_mode=True))))
    assert holder[0].data == {"filter": {"kind": "all"}}
    body, = (s["body"] for s in sent)
    assert sent_schema(provider, body) is None
    assert instructions_of(provider, body) == JSON_MODE_INSTRUCTIONS
    if provider in ("gemini", "vertex-gemini"):
        assert body["generationConfig"]["responseMimeType"] == "application/json"
    elif provider == "anthropic":
        assert "output_config" not in body, "no format and no effort: no output_config at all"
    else:
        assert body["response_format"] == {"type": "json_object"}
    record, = rt["llm_calls"]
    assert record["schema"] == "json" and "schema_fallback" not in record and record["ok"] is True


def test_a_json_mode_request_to_claude_keeps_its_effort(monkeypatch):
    llm, sent = model(monkeypatch, "anthropic", "claude-haiku-5-5", reply_for("anthropic"))
    monkeypatch.setenv("MODEL_THINKING_LEVEL", "low")
    llm.structured(request(json_mode=True))
    assert sent[0]["body"]["output_config"] == {"effort": "low"}


def test_a_json_mode_request_to_claude_on_vertex_sends_no_output_config_format(monkeypatch):
    llm, sent = vertex_claude(monkeypatch)
    assert llm.structured(request(json_mode=True)).data == {"filter": {"kind": "all"}}
    body = sent[0]["body"]
    assert "output_config" not in body and body["system"] == JSON_MODE_INSTRUCTIONS
    monkeypatch.setenv("MODEL_THINKING_LEVEL", "high")
    llm.structured(request(json_mode=True))
    assert sent[1]["body"]["output_config"] == {"effort": "high"}


def test_a_normal_call_records_its_schema_as_sent(monkeypatch):
    llm, sent = model(monkeypatch, "vertex-gemini", PRO)
    rt = traced(lambda: llm.structured(request()))
    assert sent_schema("vertex-gemini", sent[0]["body"]) is not None
    assert sent[0]["body"]["systemInstruction"]["parts"][0]["text"] == INSTRUCTIONS
    assert rt["llm_calls"][0]["schema"] == "sent" and "schema_fallback" not in rt["llm_calls"][0]


def test_gemini_is_sent_one_level_of_a_cycle_and_every_other_provider_three():
    assert (GEMINI_UNROLL_DEPTH, UNROLL_DEPTH) == (1, 3)
    for provider in ("gemini", "vertex-gemini"):
        defs = provider_schema(SCHEMA, provider)["$defs"]
        assert set(defs) == {"Node"} and "children" not in defs["Node"]["properties"]
    for provider in ("anthropic", "vertex-claude", "openai", "ollama"):
        assert set(provider_schema(SCHEMA, provider)["$defs"]) == {"Node", "NodeDepth1", "NodeDepth2"}


@pytest.mark.parametrize("name", [PRO, ARGON])
@pytest.mark.parametrize("message", [FLATTEN, TOKEN_COUNT], ids=["flatten", "token-count"])
def test_a_refused_schema_on_gemini_is_retried_once_in_json_mode(monkeypatch, name, message):
    llm, sent = model(monkeypatch, "vertex-gemini", name, refusal(400, message), reply_for("vertex-gemini"))
    holder: list = []
    rt = traced(lambda: holder.append(llm.structured(request())))
    assert holder[0].data == {"filter": {"kind": "all"}} and holder[0].text == ANSWER
    first, second = (s["body"] for s in sent)
    assert set(sent_schema("vertex-gemini", first)["$defs"]) == {"Node"}, "the one-level unroll was sent first"
    assert sent_schema("vertex-gemini", second) is None and instructions_of("vertex-gemini", second) == JSON_MODE_INSTRUCTIONS
    assert second["generationConfig"]["responseMimeType"] == "application/json"
    assert second["contents"] == first["contents"], "the same request, in JSON mode"
    refused, retried = rt["llm_calls"]
    assert (refused["ok"], refused["error"], refused["schema"]) == (False, "ExternalCallError", "refused")
    assert "schema_fallback" not in refused
    assert (retried["ok"], retried["schema"], retried["schema_fallback"]) == (True, "json", True)
    assert (refused["attempt"], retried["attempt"]) == (0, 0)
    assert "model_attempts" not in rt, "the retry is never a correction"


@pytest.mark.parametrize("provider", REST)
def test_every_rest_provider_falls_back_on_a_refused_schema(monkeypatch, provider):
    llm, sent = model(monkeypatch, provider, PRO if "gemini" in provider else "a-model", refusal(400, FLATTEN), reply_for(provider))
    assert llm.structured(request()).data == {"filter": {"kind": "all"}}
    assert [sent_schema(provider, s["body"]) is None for s in sent] == [False, True]


def test_a_second_schema_refusal_in_json_mode_is_raised_not_retried_again(monkeypatch):
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, refusal(400, TOKEN_COUNT), refusal(400, TOKEN_COUNT), reply_for("vertex-gemini"))
    with runtrace.start("ask") as t:
        with pytest.raises(ExternalCallError) as caught:
            llm.structured(request())
        rt = t.finish({})
    assert caught.value.schema_refused and len(sent) == 2
    refused, retried = rt["llm_calls"]
    assert refused["schema"] == "refused"
    assert (retried["ok"], retried["schema"], retried["schema_fallback"]) == (False, "json", True)


@pytest.mark.parametrize("status,message", [
    (401, "API key not valid. Please pass a valid API key."),
    (403, "Permission denied on resource project pilot-project."),
    (429, "Resource exhausted. Please try again later."),
    (500, "Internal error encountered."),
    (400, "Unable to submit request because thinking_budget is out of range; supported values are 128 to 32768."),
], ids=["401", "403", "429", "500", "400-thinking"])
def test_another_failure_is_raised_as_before_never_in_json_mode(monkeypatch, status, message):
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, refusal(status, message), refusal(status, message))
    with runtrace.start("ask") as t:
        with pytest.raises(ExternalCallError) as caught:
            llm.structured(request())
        rt = t.finish({})
    assert not caught.value.schema_refused and f"(HTTP {status})" in str(caught.value)
    assert all(sent_schema("vertex-gemini", s["body"]) is not None for s in sent), "no call was sent in JSON mode"
    assert len(sent) == (2 if status in (429, 500) else 1), "only post_json's transient retry, as before"
    record, = rt["llm_calls"]
    assert record["schema"] == "sent" and "schema_fallback" not in record


def test_a_request_already_in_json_mode_is_not_retried(monkeypatch):
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, refusal(400, TOKEN_COUNT), reply_for("vertex-gemini"))
    with runtrace.start("ask") as t:
        with pytest.raises(ExternalCallError):
            llm.structured(request(json_mode=True))
        rt = t.finish({})
    assert len(sent) == 1
    record, = rt["llm_calls"]
    assert (record["ok"], record["schema"]) == (False, "json") and "schema_fallback" not in record


def test_a_refusal_a_truncated_reply_and_a_reply_that_is_not_json_are_not_retried(monkeypatch):
    llm, sent = model(monkeypatch, "anthropic", "claude-haiku-5-5", claude_reply("", "refusal"))
    with pytest.raises(Refused):
        llm.structured(request())
    assert len(sent) == 1
    cut = {**reply_for("vertex-gemini", '{"fil'), "candidates": [{"content": {"parts": [{"text": '{"fil'}]}, "finishReason": "MAX_TOKENS"}]}
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, cut)
    with pytest.raises(Truncated):
        llm.structured(request())
    assert len(sent) == 1
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, reply_for("vertex-gemini", "I read it as revenue."))
    with pytest.raises(NotJSON):
        llm.structured(request())
    assert len(sent) == 1


def test_a_request_deadline_is_raised_not_retried(monkeypatch):
    llm, _ = model(monkeypatch, "vertex-gemini", PRO)
    calls: list[dict] = []

    def out_of_time(url, headers, body):
        calls.append(body)
        raise RequestDeadlineExceeded()
    llm._post = out_of_time
    with pytest.raises(RequestDeadlineExceeded):
        llm.structured(request())
    assert len(calls) == 1


def test_claude_on_vertex_falls_back_when_the_sdk_reports_a_refused_schema(monkeypatch):
    llm, sent = vertex_claude(monkeypatch, refusal(400, FLATTEN), claude_reply())
    rt = traced(lambda: llm.structured(request()))
    first, second = (s["body"] for s in sent)
    assert sent_schema("vertex-claude", first) is not None
    assert "output_config" not in second and second["system"] == JSON_MODE_INSTRUCTIONS
    assert [(c["schema"], c.get("schema_fallback")) for c in rt["llm_calls"]] == [("refused", None), ("json", True)]


def test_claude_on_vertex_raises_another_400_as_before(monkeypatch):
    llm, sent = vertex_claude(monkeypatch, refusal(400, "thinking.budget_tokens: must be at least 1024"))
    with pytest.raises(ExternalCallError) as caught:
        llm.structured(request())
    assert str(caught.value) == "Vertex AI error (HTTP 400)" and not caught.value.schema_refused and len(sent) == 1


def test_the_sdk_s_schema_refusal_names_no_provider_message(monkeypatch):
    llm, _ = vertex_claude(monkeypatch, refusal(400, FLATTEN), refusal(400, FLATTEN))
    with pytest.raises(ExternalCallError) as caught:
        llm.structured(request())
    assert str(caught.value) == "Vertex AI error (HTTP 400): the schema was refused" and caught.value.schema_refused


def test_a_rest_refusal_is_one_line_with_the_key_redacted_and_still_a_schema_refusal(monkeypatch):
    llm, sent = model(monkeypatch, "gemini", PRO, refusal(400, f"Invalid response_json_schema for key {KEY}"), reply_for("gemini"))
    seen: list[ExternalCallError] = []
    real = llm._post

    def kept(url, headers, body):
        try:
            return real(url, headers, body)
        except ExternalCallError as exc:
            seen.append(exc)
            raise
    llm._post = kept
    llm.structured(request())
    assert KEY not in str(seen[0]) and "[redacted]" in str(seen[0]) and seen[0].schema_refused


def test_only_a_400_naming_the_schema_or_the_input_token_count_is_a_schema_refusal():
    assert schema_refused(400, FLATTEN) and schema_refused(400, TOKEN_COUNT)
    assert not schema_refused(400, "thinking_budget is out of range")
    assert not any(schema_refused(status, FLATTEN) for status in (401, 403, 404, 429, 500, 503))
    assert ExternalCallError("x").schema_refused is False and RequestDeadlineExceeded().schema_refused is False


class Filter(Contract):
    """A contract whose reply the stand-in answers wrongly, so each attempt asks for a correction."""

    kind: Literal["all"]


def test_a_refused_schema_is_never_sent_again_in_the_conversation_its_corrections_are_json_mode(monkeypatch):
    wrong = reply_for("vertex-gemini", '{"kind": "some"}')
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, refusal(400, FLATTEN), wrong, wrong, wrong)
    monkeypatch.setenv("ASK_MODEL_CORRECTIONS", "2")
    with runtrace.start("ask") as t:
        with pytest.raises(ContractFailure):
            structured_retry(llm, "classify", version="test-v1", instructions=INSTRUCTIONS, data={}, question="Revenue?",
                             contract=Filter, legacy_prompt="legacy\n{}")
        rt = t.finish({})
    assert [sent_schema("vertex-gemini", s["body"]) is None for s in sent] == [False, True, True, True], (
        "one refusal, its JSON-mode retry, then two corrections in JSON mode: 4 requests, never the refused schema again")
    assert [c["schema"] for c in rt["llm_calls"]] == ["refused", "json", "json", "json"]
    assert [c.get("schema_fallback", False) for c in rt["llm_calls"]] == [False, True, False, False]
    assert [c["attempt"] for c in rt["llm_calls"]] == [0, 0, 1, 2]


def test_a_correction_after_a_json_mode_reply_is_sent_in_json_mode_and_one_after_a_schema_reply_is_not():
    seen: list[bool] = []

    class Stand:
        def __init__(self, answered_json):
            self.answered_json = answered_json

        def structured(self, req):
            seen.append(req.json_mode)
            return ModelReply(data={"kind": "some"}, text='{"kind": "some"}', json_mode=req.json_mode or self.answered_json)

    for answered_json in (True, False):
        seen.clear()
        with pytest.raises(ContractFailure):
            structured_retry(Stand(answered_json), "classify", limit=2, version="test-v1", instructions=INSTRUCTIONS,
                             data={}, question="Revenue?", contract=Filter, legacy_prompt="legacy\n{}")
        assert seen == ([False, True, True] if answered_json else [False, False, False])


def test_the_planner_s_reading_carries_json_mode_into_its_correction_and_through_dump_and_load():
    from kge.services.ask.engine.planmodel import Reading, correction
    for answered_json in (True, False):
        for truncated in (True, False):
            reading = Reading(None, truncated=truncated, contract='{"kind": "some"}', json_mode=answered_json)
            assert correction(reading, (), None).json_mode is answered_json
            held = reading.dump()
            assert ("json_mode" in held) is answered_json, "a reading's dump is unchanged unless it was answered in JSON mode"
            assert Reading.load(held).json_mode is answered_json


@pytest.mark.parametrize("provider", ["gemini", "vertex-gemini"])
@pytest.mark.parametrize("name", [PRO, ARGON])
def test_the_planner_on_gemini_3_and_later_is_sent_in_json_mode_from_the_first_attempt(monkeypatch, provider, name):
    """#561: Vertex AI refused the planner's schema on Gemini 3.1 Pro and Gemini 4 Argon, so it is sent as plan_full is,
    the schema in the system instruction, the reply validated by the same contract."""
    llm, sent = model(monkeypatch, provider, name)
    holder: list = []
    rt = traced(lambda: holder.append(llm.structured(request(call="plan"))))
    assert holder[0].data == {"filter": {"kind": "all"}} and holder[0].json_mode is True
    body, = (s["body"] for s in sent)
    assert sent_schema(provider, body) is None and instructions_of(provider, body) == JSON_MODE_INSTRUCTIONS
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    record, = rt["llm_calls"]
    assert record["schema"] == "json" and "schema_fallback" not in record


@pytest.mark.parametrize("provider,name", [("gemini", "gemini-2.5-pro"), ("vertex-gemini", "gemini-2.5-flash"),
                                           ("anthropic", "claude-haiku-5-5"), ("openai", "a-model"), ("ollama", "a-model")])
def test_the_planner_keeps_its_schema_on_every_other_model(monkeypatch, provider, name):
    llm, sent = model(monkeypatch, provider, name)
    llm.structured(request(call="plan"))
    assert sent_schema(provider, sent[0]["body"]) is not None and instructions_of(provider, sent[0]["body"]) == INSTRUCTIONS
    assert not json_mode_call(provider, name, "plan")
    assert not any(json_mode_call(p, PRO, call) for p in ("gemini", "vertex-gemini") for call in ("classify", "wording"))


def test_the_planner_s_corrections_on_gemini_stay_in_json_mode(monkeypatch):
    wrong = reply_for("vertex-gemini", '{"kind": "some"}')
    llm, sent = model(monkeypatch, "vertex-gemini", ARGON, wrong, reply_for("vertex-gemini", '{"kind": "all"}'))
    structured_retry(llm, "plan", limit=1, version="test-v1", instructions=INSTRUCTIONS, data={}, question="Revenue?", contract=Filter,
                     schema=SCHEMA, legacy_prompt="legacy")
    assert len(sent) == 2 and all(sent_schema("vertex-gemini", s["body"]) is None for s in sent)
    assert all(JSON_MODE_SCHEMA in instructions_of("vertex-gemini", s["body"]) for s in sent)


@pytest.mark.parametrize("provider", REST)
def test_a_generic_400_on_a_request_that_sent_a_schema_is_retried_once_in_json_mode(monkeypatch, provider):
    llm, sent = model(monkeypatch, provider, PRO if "gemini" in provider else "a-model", refusal(400, GENERIC), reply_for(provider))
    holder: list = []
    rt = traced(lambda: holder.append(llm.structured(request())))
    assert holder[0].data == {"filter": {"kind": "all"}} and holder[0].json_mode is True
    assert [sent_schema(provider, s["body"]) is None for s in sent] == [False, True]
    refused, retried = rt["llm_calls"]
    assert (refused["ok"], refused["schema"]) == (False, "refused")
    assert (retried["ok"], retried["schema"], retried["schema_fallback"]) == (True, "json", True)


def test_a_generic_400_on_a_request_without_a_schema_is_raised_not_retried(monkeypatch):
    llm, sent = model(monkeypatch, "vertex-gemini", PRO, refusal(400, GENERIC), reply_for("vertex-gemini"))
    with runtrace.start("ask") as t:
        with pytest.raises(ExternalCallError) as caught:
            llm.structured(request(json_mode=True))
        rt = t.finish({})
    assert not caught.value.schema_refused and "(HTTP 400)" in str(caught.value) and len(sent) == 1
    assert rt["llm_calls"][0]["schema"] == "json" and "schema_fallback" not in rt["llm_calls"][0]


def test_claude_on_vertex_takes_a_generic_400_for_a_refused_schema_only_when_one_was_sent(monkeypatch):
    llm, sent = vertex_claude(monkeypatch, refusal(400, GENERIC), claude_reply())
    assert llm.structured(request()).data == {"filter": {"kind": "all"}}
    assert [("output_config" in s["body"]) for s in sent] == [True, False]
    llm, sent = vertex_claude(monkeypatch, refusal(400, GENERIC), claude_reply())
    with pytest.raises(ExternalCallError) as caught:
        llm.structured(request(json_mode=True))
    assert not caught.value.schema_refused and len(sent) == 1


def test_the_generic_invalid_argument_is_a_schema_refusal_only_with_a_schema_sent():
    assert schema_refused(400, GENERIC, schema_sent=True) and not schema_refused(400, GENERIC)
    assert not schema_refused(400, GENERIC, schema_sent=False)
    assert not any(schema_refused(status, GENERIC, schema_sent=True) for status in (401, 403, 404, 429, 500))
    assert schema_refused(400, FLATTEN) and schema_refused(400, TOKEN_COUNT), "the named refusals need no schema flag"
    assert not schema_refused(400, "thinking_budget is out of range", schema_sent=True)


def test_carries_schema_reads_each_provider_s_place_for_one():
    assert carries_schema({"generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": {}}})
    assert carries_schema({"generationConfig": {"responseSchema": {"type": "object"}}})
    assert carries_schema({"output_config": {"format": {"type": "json_schema", "schema": {}}}})
    assert carries_schema({"extra_body": {"output_config": {"format": {"type": "json_schema", "schema": {}}}}})
    assert carries_schema({"response_format": {"type": "json_schema", "json_schema": {"schema": {}}}})
    assert not carries_schema({"generationConfig": {"responseMimeType": "application/json"}})
    assert not carries_schema({"output_config": {"effort": "low"}})
    assert not carries_schema({"response_format": {"type": "json_object"}})
    assert not carries_schema({})


@pytest.mark.parametrize("provider", ["gemini", "vertex-gemini"])
def test_gemini_is_sent_no_item_bound_and_validation_keeps_it(provider):
    """#561: Vertex AI's grammar compiler unrolls maxItems into states until it refuses the request."""
    bounded = {"type": "object", "properties": {"order": {"type": "array", "minItems": 1, "maxItems": 64,
                                                          "items": {"type": "array", "maxItems": 64, "items": {"type": "string"}}}}}
    sent = json.dumps(provider_schema(bounded, provider))
    assert "maxItems" not in sent and "minItems" not in sent
    assert '"maxItems": 64' in json.dumps(provider_schema(bounded, "openai")), "other providers keep their bounds"
