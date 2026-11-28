"""Ask's structured model calls (#561): the request each provider is sent, the budget and the thinking cap, and how a reply
that stopped at its budget, a refusal and a reply that is not JSON come back.

    .venv/bin/python -m pytest tests/unit/test_model_structured.py -q

LLM.structured sends a ModelRequest (kge/ports.py) as a system instruction, the data and the question as the first user
turn, any correction turns after it, and the contract's JSON schema as the provider's structured output. Each of Ask's
calls (plan, classify, wording) has its own budget and thinking cap (call_budget): 16384 and 1024 tokens for plan, 4096
and 512 for the others; the settings ASK_<CALL>_*, MODEL_<TIER>_THINKING_* and MODEL_THINKING_* override them, most
specific first. Thinking is a token budget where the model takes one, never more than half the reply budget, else a
level where one is set, never both. Gemini 3 is sent no temperature on these calls.

Nothing leaves the process: a REST provider's post is a stand-in transport that keeps the body, AnthropicVertex sends to
an HTTP transport that answers in Vertex AI's place, and the service account is a stand-in.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest

from kge.adapters.model import LLM, NotJSON, Refused, Truncated, call_budget, model_for, thinking_support
from kge.adapters.model import ExternalCallError, ModelSettingError, check_settings
from kge.adapters.model.providers import JSON_MODE_SCHEMA, Chat, provider_schema
from kge.pipeline import settings as service_settings
from kge.ports import ModelRequest, ModelTurn
from kge.services.ask.engine import runtrace

DEMO = Path(__file__).resolve().parents[2]
TOKEN = "ya29.stand-in-token"
PROJECT = "pilot-project"
PRO = "gemini-3.1-pro-preview"
CALL_SETTINGS = tuple(f"ASK_{call}_{part}" for call in ("PLAN", "CLASSIFY", "WORDING")
                      for part in ("MAX_TOKENS", "THINKING_TOKENS", "THINKING_LEVEL"))
THINKING = tuple(f"MODEL{tier}_THINKING_{part}" for tier in ("", "_HIGH", "_MEDIUM", "_LOW") for part in ("TOKENS", "LEVEL"))
CLEARED = (*CALL_SETTINGS, *THINKING, "ASK_UNKNOWN_WORD", "ASK_MODEL_CORRECTIONS", "MODEL_MAX_TOKENS", "MODEL_SEED",
           "MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL", "MOCK_MODE", "VERTEX_REGION", "VERTEX_PROJECT", "CONTEXT_MODEL_TIER",
           *(f"MODEL_{tier}_{part}" for tier in ("HIGH", "MEDIUM", "LOW") for part in ("PROVIDER", "NAME", "API_KEY", "BASE_URL", "MAX_TOKENS")))
SCHEMA = {"title": "Contract", "type": "object", "additionalProperties": False, "required": ["terms"],
          "properties": {"terms": {"title": "Terms", "type": "array", "items": {"type": "string", "enum": ["revenue", "margin"]}},
                         "period": {"oneOf": [{"$ref": "#/$defs/Period"}, {"type": "null"}]}},
          "$defs": {"Period": {"title": "Period", "type": "object", "properties": {"kind": {"const": "day"}, "title": {"type": "string"}},
                               "discriminator": {"propertyName": "kind"}}}}
CONTENT = "## Glossary terms\n[\"revenue\", \"margin\"]\n\n## Question\nWhat was revenue in June?"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No call leaves the process, and no setting from the caller's environment steers a test."""
    def no_network(*a, **k):
        raise AssertionError("a model call reached the network")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr("google.auth.default", no_network)
    monkeypatch.setattr(Chat, "token", lambda self: (TOKEN, PROJECT))
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "none")


def request(call: str = "plan", **fields) -> ModelRequest:
    return ModelRequest(**{"call": call, "version": "test-v1", "instructions": "Reply with the contract alone.",
                           "content": CONTENT, "schema": SCHEMA, "contract": "Contract", "legacy_prompt": "legacy\n{}", **fields})


def gemini_reply(text: str | None = '{"terms": ["revenue"]}', finish: str = "STOP", thoughts: int | None = 300) -> dict:
    parts = [{"text": "thinking out loud", "thought": True}] + ([{"text": text, "thoughtSignature": "c2ln"}] if text is not None else [])
    usage = {"promptTokenCount": 120, "candidatesTokenCount": 9, **({"thoughtsTokenCount": thoughts} if thoughts is not None else {})}
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "modelVersion": "gemini-3.1-pro-preview-001", "usageMetadata": usage}


def gemini(monkeypatch, model: str = PRO, tier: str | None = None, *replies: dict) -> tuple[LLM, list[dict]]:
    """A vertex-gemini model (the default, or a tier's for the context agent) whose bodies are kept, answered in turn."""
    if tier:
        monkeypatch.setenv(f"MODEL_{tier.upper()}_PROVIDER", "vertex-gemini")
        monkeypatch.setenv(f"MODEL_{tier.upper()}_NAME", model)
    else:
        monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
        monkeypatch.setenv("MODEL_NAME", model)
    llm = model_for("context")
    sent: list[dict] = []
    queue = list(replies)

    def post(url, headers, body):
        sent.append({"url": url, "headers": headers, "body": body})
        return queue.pop(0) if queue else gemini_reply()
    llm._post = post
    return llm, sent


def test_vertex_gemini_3_pro_gets_the_instruction_apart_the_planner_in_json_mode_and_a_capped_thinking_budget(monkeypatch):
    """#561: on Gemini 3 and later the planner is sent in JSON mode, its schema in the system instruction; the
    classifier keeps its schema as structured output."""
    llm, sent = gemini(monkeypatch, PRO, "high")
    reply = llm.structured(request("plan"))
    llm.structured(request("classify"))
    assert reply.data == {"terms": ["revenue"]} and reply.text == '{"terms": ["revenue"]}'
    plan, classify = (s["body"] for s in sent)
    assert sent[0]["url"].endswith(f"/projects/{PROJECT}/locations/global/publishers/google/models/{PRO}:generateContent")
    assert sent[0]["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert plan["systemInstruction"]["parts"][0]["text"].startswith(f"Reply with the contract alone.\n\n{JSON_MODE_SCHEMA}\n")
    assert classify["systemInstruction"] == {"parts": [{"text": "Reply with the contract alone."}]}
    assert plan["contents"] == [{"role": "user", "parts": [{"text": CONTENT}]}]
    assert plan["contents"][0]["parts"][0]["text"].endswith("## Question\nWhat was revenue in June?"), "the question is last"
    config = plan["generationConfig"]
    assert config["responseMimeType"] == "application/json" and "responseJsonSchema" not in config
    assert classify["generationConfig"]["responseJsonSchema"]["required"] == ["terms"]
    assert (config["maxOutputTokens"], config["thinkingConfig"]) == (16384, {"thinkingLevel": "low"}), "the planner thinks at level low (#589)"
    assert (classify["generationConfig"]["maxOutputTokens"], classify["generationConfig"]["thinkingConfig"]) == (4096, {"thinkingBudget": 512})
    assert "temperature" not in config and "temperature" not in classify["generationConfig"], "Gemini 3 is sent no temperature"
    assert config["seed"] == 7


def test_a_gemini_2_5_model_keeps_temperature_zero(monkeypatch):
    llm, sent = gemini(monkeypatch, "gemini-2.5-flash")
    llm.structured(request("wording"))
    config = sent[0]["body"]["generationConfig"]
    assert config["temperature"] == 0 and config["thinkingConfig"] == {"thinkingBudget": 512}


def test_gemini_3_s_other_calls_are_unchanged(monkeypatch):
    """No temperature on Gemini 3 is for Ask's structured calls alone: complete() and json() send what they always did,
    so the document reader's tier keeps its identity."""
    llm, sent = gemini(monkeypatch, PRO)
    monkeypatch.setenv("MODEL_HIGH_THINKING_LEVEL", "low")
    monkeypatch.setenv("ASK_PLAN_MAX_TOKENS", "20000")
    llm.json("a prompt", 400)
    assert sent[0]["body"] == {"contents": [{"role": "user", "parts": [{"text": "a prompt"}]}],
                               "generationConfig": {"temperature": 0, "seed": 7, "maxOutputTokens": 8192}}


def test_a_level_setting_sends_the_level_and_never_both(monkeypatch):
    monkeypatch.setenv("MODEL_HIGH_THINKING_LEVEL", "low")
    llm, sent = gemini(monkeypatch, PRO, "high")
    llm.structured(request("plan"))
    assert sent[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    monkeypatch.setenv("ASK_PLAN_THINKING_TOKENS", "600")
    llm.structured(request("plan"))
    llm.structured(request("classify"))
    assert sent[1]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 600}, "a more specific budget wins"
    assert sent[2]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    monkeypatch.setenv("ASK_PLAN_THINKING_LEVEL", "medium")
    llm.structured(request("plan"))
    assert sent[3]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}, "a level as specific as the budget wins"
    assert all(len(s["body"]["generationConfig"]["thinkingConfig"]) == 1 for s in sent)


def test_a_thinking_budget_setting_is_sent_and_never_more_than_half_the_reply_budget(monkeypatch):
    monkeypatch.setenv("ASK_PLAN_THINKING_TOKENS", "500")
    llm, sent = gemini(monkeypatch, PRO, "high")
    llm.structured(request("plan"))
    monkeypatch.setenv("ASK_PLAN_THINKING_TOKENS", "99999")
    llm.structured(request("plan"))
    monkeypatch.setenv("ASK_PLAN_MAX_TOKENS", "3000")
    llm.structured(request("plan"))
    budgets = [(s["body"]["generationConfig"]["maxOutputTokens"], s["body"]["generationConfig"]["thinkingConfig"]["thinkingBudget"]) for s in sent]
    assert budgets == [(16384, 500), (16384, 8192), (3000, 1500)]


def test_the_budget_takes_ask_s_setting_then_the_tier_s_then_the_global_then_the_call_s_default(monkeypatch):
    llm, sent = gemini(monkeypatch, PRO, "high")
    assert call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=None).max_tokens == 16384
    monkeypatch.setenv("MODEL_MAX_TOKENS", "12000")
    plan = call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=12000)
    assert (plan.max_tokens, plan.max_tokens_setting, plan.configured) == (12000, "MODEL_MAX_TOKENS", True)
    monkeypatch.setenv("MODEL_HIGH_MAX_TOKENS", "10000")
    plan = call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=10000)
    assert (plan.max_tokens, plan.max_tokens_setting) == (10000, "MODEL_HIGH_MAX_TOKENS")
    monkeypatch.setenv("ASK_PLAN_MAX_TOKENS", "20000")
    plan = call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=10000)
    assert (plan.max_tokens, plan.max_tokens_setting) == (20000, "ASK_PLAN_MAX_TOKENS")
    assert call_budget("classify", "high", "vertex-gemini", PRO, configured_max_tokens=10000).max_tokens == 10000
    default = call_budget("wording", "high", "vertex-gemini", PRO, configured_max_tokens=None)
    assert (default.max_tokens_setting, default.configured) == ("ASK_WORDING_MAX_TOKENS", False)
    llm.structured(request("plan"))
    assert sent[-1]["body"]["generationConfig"]["maxOutputTokens"] == 20000


def test_less_thinking_halves_the_cap_to_a_floor_where_there_is_no_more_room(monkeypatch):
    monkeypatch.setenv("MODEL_HIGH_THINKING_TOKENS", "1024")
    steps = [call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=None, less_thinking=n) for n in range(5)]
    assert [b.thinking["tokens"] for b in steps] == [1024, 512, 256, 128, 128]
    assert [b.more_room for b in steps] == [True, True, True, False, False]
    assert [b.sent() for b in steps[:2]] == ["tokens:1024", "tokens:512"]
    llm, sent = gemini(monkeypatch, PRO, "high")
    llm.structured(request("plan", less_thinking=1, attempt=1))
    assert sent[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 512}
    monkeypatch.setenv("MODEL_HIGH_THINKING_LEVEL", "medium")
    levels = [call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=None, less_thinking=n) for n in range(3)]
    assert [(b.thinking, b.more_room) for b in levels] == [({"level": "medium"}, True), ({"level": "low"}, False), ({"level": "low"}, False)]
    monkeypatch.setenv("MODEL_HIGH_THINKING_LEVEL", "low")
    assert not call_budget("plan", "high", "vertex-gemini", PRO, configured_max_tokens=None).more_room
    assert call_budget("plan", "default", "ollama", "llama3", configured_max_tokens=None).thinking == {}


@pytest.mark.parametrize("text", ['{"terms": ["rev', None, ""])
def test_a_reply_that_stops_at_its_budget_is_truncated_never_not_json_or_a_failed_call(monkeypatch, text):
    monkeypatch.setenv("MODEL_HIGH_THINKING_TOKENS", "1024")
    llm, _ = gemini(monkeypatch, PRO, "high", *[gemini_reply(text, "MAX_TOKENS", thoughts=15000)] * 2)
    with pytest.raises(Truncated) as caught:
        llm.structured(request("plan"))
    exc = caught.value
    assert not isinstance(exc, (NotJSON, ExternalCallError))
    assert (exc.budget, exc.setting, exc.more_room, exc.thinking) == (16384, "ASK_PLAN_MAX_TOKENS", True, 15000)
    assert str(exc) == "the reply reached its 16384-token budget after 15000 thinking tokens; raise ASK_PLAN_MAX_TOKENS"
    with pytest.raises(Truncated) as floor:
        llm.structured(request("plan", less_thinking=3))
    assert floor.value.more_room is False


def test_a_reply_with_no_text_for_another_reason_is_still_a_failed_call(monkeypatch):
    llm, _ = gemini(monkeypatch, PRO, "high", gemini_reply(None, "SAFETY"))
    with pytest.raises(ExternalCallError, match="finishReason=SAFETY"):
        llm.structured(request("plan"))


@pytest.mark.parametrize("text", ["no json here", "[1, 2]", '"a string"'])
def test_a_reply_that_is_not_one_json_object_is_not_json(monkeypatch, text):
    llm, _ = gemini(monkeypatch, PRO, "high", gemini_reply(text))
    with pytest.raises(NotJSON, match="^model reply held no JSON object$"):
        llm.structured(request("plan"))


def test_one_json_fence_is_stripped_and_the_text_kept_as_returned(monkeypatch):
    fenced = '```json\n{"terms": []}\n```'
    llm, _ = gemini(monkeypatch, PRO, "high", gemini_reply(fenced))
    reply = llm.structured(request("plan"))
    assert reply.data == {"terms": []} and reply.text == fenced


def test_correction_turns_continue_the_conversation_with_the_model_s_own_parts(monkeypatch):
    llm, sent = gemini(monkeypatch, PRO, "high")
    first = llm.structured(request("plan"))
    assert first.replay == {"role": "model", "parts": [{"text": '{"terms": ["revenue"]}', "thoughtSignature": "c2ln"}]}, \
        "the replay keeps the thought signature and drops the thought text"
    turns = (ModelTurn("model", first.text, first.replay), ModelTurn("user", "- /terms/0: not a declared id Allowed: revenue, margin"),
             ModelTurn("model", '{"terms": []}'), ModelTurn("user", "Reply again."))
    llm.structured(request("plan", turns=turns, attempt=2))
    contents = sent[1]["body"]["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user", "model", "user"]
    assert contents[1] == first.replay
    assert contents[2] == {"role": "user", "parts": [{"text": "- /terms/0: not a declared id Allowed: revenue, margin"}]}
    assert contents[3] == {"role": "model", "parts": [{"text": '{"terms": []}'}]}


def test_the_schema_is_sent_in_the_form_each_provider_takes():
    gemini_form = provider_schema(SCHEMA, "vertex-gemini")
    assert "title" not in gemini_form and "title" in gemini_form["$defs"]["Period"]["properties"], "a property named title stays"
    assert gemini_form["properties"]["period"] == {"anyOf": [{"$ref": "#/$defs/Period"}, {"type": "null"}]}
    assert gemini_form["$defs"]["Period"]["properties"]["kind"] == {"enum": ["day"]}
    assert "discriminator" not in gemini_form["$defs"]["Period"]
    bounded = {"type": "object", "x-ids": "vocabulary", "properties": {
        "n": {"type": "integer", "exclusiveMinimum": 0, "maximum": 9}, "s": {"type": "string", "minLength": 1, "pattern": "^a"},
        "xs": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 64}, "ys": {"type": "array", "minItems": 1}}}
    assert provider_schema(bounded, "gemini")["properties"]["n"] == {"type": "integer", "minimum": 1, "maximum": 9}
    claude_form = provider_schema(bounded, "anthropic")
    assert claude_form == {"type": "object", "properties": {"n": {"type": "integer"}, "s": {"type": "string"},
                                                           "xs": {"type": "array", "items": {"type": "string"}},
                                                           "ys": {"type": "array", "minItems": 1}}}
    assert SCHEMA["title"] == "Contract" and bounded["x-ids"] == "vocabulary", "the request's schema is not changed"


def test_what_each_provider_takes_for_thinking():
    assert thinking_support("vertex-gemini", PRO) == (True, True)
    assert thinking_support("gemini", "gemini-2.5-flash") == (True, False)
    assert thinking_support("gemini", "gemini-2.0-flash") == (False, False)
    assert thinking_support("vertex-claude", "claude-haiku-5-5") == (False, True)
    assert thinking_support("anthropic", "claude-3-5-haiku") == (False, False)
    assert thinking_support("openai", "gpt-5") == (False, True)
    assert thinking_support("ollama", "llama3") == (False, False)
    assert thinking_support("mock", "mock") == (True, True)


def rest_model(monkeypatch, provider: str, model: str, reply: dict, **env: str) -> tuple[LLM, list[dict]]:
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("MODEL_NAME", model)
    monkeypatch.setenv("MODEL_API_KEY", "a-key")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    llm = model_for("context")
    sent: list[dict] = []
    llm._post = lambda url, headers, body: sent.append({"url": url, "body": body}) or reply
    return llm, sent


def claude_reply(text: str = '{"terms": []}', stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_standin", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "thinking", "thinking": "", "signature": "c2ln"}, {"type": "text", "text": text}],
            "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 40, "output_tokens": 9}}


def test_anthropic_gets_the_system_instruction_the_turns_and_an_output_config(monkeypatch):
    llm, sent = rest_model(monkeypatch, "anthropic", "claude-haiku-5-5", claude_reply(), MODEL_THINKING_LEVEL="low")
    reply = llm.structured(request("classify", turns=(ModelTurn("model", "{}"), ModelTurn("user", "Correct it."))))
    body = sent[0]["body"]
    assert sent[0]["url"] == "https://api.anthropic.com/v1/messages" and reply.data == {"terms": []}
    assert body["system"] == "Reply with the contract alone." and body["max_tokens"] == 4096
    assert body["messages"] == [{"role": "user", "content": CONTENT}, {"role": "assistant", "content": "{}"},
                                {"role": "user", "content": "Correct it."}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": provider_schema(SCHEMA, "anthropic")}, "effort": "low"}
    assert "temperature" not in body, "Claude Haiku 5.5 takes no sampling parameter"
    monkeypatch.delenv("MODEL_THINKING_LEVEL")
    older, sent = rest_model(monkeypatch, "anthropic", "claude-3-5-haiku", claude_reply())
    older.structured(request("plan"))
    assert sent[0]["body"]["temperature"] == 0 and "effort" not in sent[0]["body"]["output_config"]


def test_a_claude_refusal_raises_refused_and_its_cap_raises_truncated(monkeypatch):
    llm, _ = rest_model(monkeypatch, "anthropic", "claude-haiku-5-5", claude_reply("", "refusal"))
    with pytest.raises(Refused):
        llm.structured(request("plan"))
    llm, _ = rest_model(monkeypatch, "anthropic", "claude-haiku-5-5", claude_reply('{"te', "max_tokens"))
    with pytest.raises(Truncated) as caught:
        llm.structured(request("plan"))
    assert caught.value.more_room is False and caught.value.thinking is None, "no thinking budget is sent to Claude"


class Account:
    valid, token, expired = True, TOKEN, False

    def refresh(self, request) -> None:
        pass


def test_claude_on_vertex_sends_the_output_config_through_the_sdk(monkeypatch):
    monkeypatch.setattr("google.auth.default", lambda scopes=None, **k: (Account(), PROJECT))
    seen: list[dict] = []

    def handle(self, req: httpx2.Request) -> httpx2.Response:
        seen.append({"url": str(req.url), "body": json.loads(req.content)})
        return httpx2.Response(200, json=claude_reply(), request=req)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", handle)
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-claude")
    monkeypatch.setenv("MODEL_HIGH_THINKING_LEVEL", "medium")
    monkeypatch.setenv("MODEL_THINKING_LEVEL", "high")
    llm = model_for("context")
    assert llm.structured(request("wording")).data == {"terms": []}
    body = seen[0]["body"]
    assert seen[0]["url"].endswith(f"/projects/{PROJECT}/locations/global/publishers/anthropic/models/claude-haiku-5-5:rawPredict")
    assert body["system"] == "Reply with the contract alone." and body["max_tokens"] == 4096
    assert body["messages"] == [{"role": "user", "content": CONTENT}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": provider_schema(SCHEMA, "vertex-claude")},
                                     "effort": "high"}, "the default tier reads MODEL_THINKING_LEVEL, never a tier's"
    assert "temperature" not in body


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_chat_completions_get_a_system_message_and_a_json_schema_response_format(monkeypatch, provider):
    reply = {"model": "served", "choices": [{"message": {"content": '{"terms": []}'}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 50, "completion_tokens": 70, "completion_tokens_details": {"reasoning_tokens": 40}}}
    llm, sent = rest_model(monkeypatch, provider, "a-model", reply)
    with runtrace.start("ask") as t:
        result = llm.structured(request("plan"))
        rt = t.finish({})
    body = sent[0]["body"]
    assert body["messages"] == [{"role": "system", "content": "Reply with the contract alone."}, {"role": "user", "content": CONTENT}]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "Contract", "schema": provider_schema(SCHEMA, provider),
                                                                              "strict": False}}
    assert (body["temperature"], body["seed"]) == (0, 7) and "max_tokens" not in body and "reasoning_effort" not in body
    assert (result.tokens_thinking, result.tokens_contract) == (40, 30)
    assert rt["llm_calls"][0]["tokens_thinking"] == 40
    monkeypatch.setenv("ASK_PLAN_MAX_TOKENS", "9000")
    if provider == "openai":
        monkeypatch.setenv("MODEL_THINKING_LEVEL", "low")
    llm.structured(request("plan"))
    assert sent[1]["body"]["max_tokens"] == 9000
    assert sent[1]["body"].get("reasoning_effort") == ("low" if provider == "openai" else None)


def test_a_chat_completions_reply_cut_at_its_length_is_truncated(monkeypatch):
    reply = {"choices": [{"message": {"content": '{"ter'}, "finish_reason": "length"}], "usage": {}}
    llm, _ = rest_model(monkeypatch, "openai", "a-model", reply)
    with pytest.raises(Truncated):
        llm.structured(request("plan"))


@pytest.mark.parametrize("env,named", [
    ({"MODEL_PROVIDER": "ollama", "MODEL_NAME": "llama3", "MODEL_THINKING_LEVEL": "low"}, "MODEL_THINKING_LEVEL is set, but ollama:llama3 takes no thinking setting"),
    ({"MODEL_PROVIDER": "none", "MODEL_HIGH_PROVIDER": "vertex-claude", "MODEL_HIGH_THINKING_TOKENS": "800"},
     "MODEL_HIGH_THINKING_TOKENS is set, but vertex-claude:claude-haiku-5-5 takes no thinking budget: set MODEL_HIGH_THINKING_LEVEL "
     "(low, medium or high) instead, or unset it"),
    ({"MODEL_PROVIDER": "none", "MODEL_LOW_PROVIDER": "vertex-claude", "MODEL_LOW_THINKING_TOKENS": "800"},
     "MODEL_LOW_THINKING_TOKENS is set, but vertex-claude:claude-haiku-5-5 takes no thinking budget"),
    ({"MODEL_PROVIDER": "vertex-gemini", "MODEL_NAME": "gemini-2.5-flash", "ASK_PLAN_THINKING_LEVEL": "low"},
     "ASK_PLAN_THINKING_LEVEL is set, but vertex-gemini:gemini-2.5-flash takes no thinking level: set ASK_PLAN_THINKING_TOKENS"),
    ({"MODEL_PROVIDER": "none", "MODEL_MEDIUM_THINKING_LEVEL": "lowest"}, "MODEL_MEDIUM_THINKING_LEVEL must be one of low, medium, high, not 'lowest'"),
    ({"MODEL_PROVIDER": "none", "ASK_WORDING_THINKING_TOKENS": "64"},
     "ASK_WORDING_THINKING_TOKENS must be a whole number of thinking tokens of 128 or more, not '64'"),
    ({"MODEL_PROVIDER": "none", "ASK_CLASSIFY_MAX_TOKENS": "lots"}, "ASK_CLASSIFY_MAX_TOKENS must be a whole number of tokens above 0"),
    ({"MODEL_PROVIDER": "vertex-gemini", "MODEL_NAME": PRO, "ASK_PLAN_MAX_TOKENS": "200"},
     f"ASK_PLAN_MAX_TOKENS must be at least 256 tokens to leave room for thinking on vertex-gemini:{PRO}, not 200"),
    ({"MODEL_PROVIDER": "vertex-gemini", "MODEL_NAME": "gemini-2.5-flash-lite", "ASK_PLAN_MAX_TOKENS": "800"},
     "ASK_PLAN_MAX_TOKENS must be at least 1024 tokens to leave room for thinking on vertex-gemini:gemini-2.5-flash-lite, not 800"),
])
def test_check_settings_refuses_a_thinking_setting_naming_it(monkeypatch, env, named):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ModelSettingError) as caught:
        check_settings()
    assert str(caught.value).startswith(named)


def test_check_settings_passes_settings_the_models_honour(monkeypatch):
    for name, value in {"MODEL_PROVIDER": "vertex-gemini", "MODEL_NAME": PRO, "MODEL_HIGH_PROVIDER": "vertex-claude",
                        "MODEL_HIGH_THINKING_LEVEL": "low", "ASK_PLAN_THINKING_TOKENS": "2000", "MODEL_LOW_PROVIDER": "ollama",
                        "MODEL_LOW_NAME": "llama3", "ASK_PLAN_MAX_TOKENS": "20000", "MODEL_THINKING_LEVEL": "medium"}.items():
        monkeypatch.setenv(name, value)
    check_settings()
    assert call_budget("plan", "high", "vertex-claude", "claude-haiku-5-5", configured_max_tokens=None).thinking == {"level": "low"}, \
        "a model that takes no budget takes the level, even where a budget is set more specifically"


@pytest.mark.parametrize("name,raw,message", [
    ("ASK_MODEL_CORRECTIONS", "3", "ASK_MODEL_CORRECTIONS must be 0, 1 or 2, not '3'"),
    ("ASK_MODEL_CORRECTIONS", "two", "ASK_MODEL_CORRECTIONS must be 0, 1 or 2, not 'two'"),
    ("ASK_UNKNOWN_WORD", "maybe", "ASK_UNKNOWN_WORD must be one of closest, abstain, not 'maybe'"),
])
def test_the_service_refuses_a_mistyped_ask_setting_naming_it(monkeypatch, name, raw, message):
    service_settings.check_service_auth_settings()
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError) as caught:
        service_settings.check_service_auth_settings()
    assert str(caught.value) == message


def test_the_ask_settings_default_and_parse(monkeypatch):
    assert (service_settings.ask_unknown_word(), service_settings.ask_model_corrections()) == ("closest", 2)
    monkeypatch.setenv("ASK_UNKNOWN_WORD", " Abstain ")
    monkeypatch.setenv("ASK_MODEL_CORRECTIONS", "0")
    assert (service_settings.ask_unknown_word(), service_settings.ask_model_corrections()) == ("abstain", 0)


def test_the_config_hash_moves_only_when_a_setting_is_set(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_NAME", PRO)
    identity = model_for("context").identity()
    before = {"provider": "vertex-gemini", "model": PRO, "base_url": "", "temperature": 0, "seed": 7, "region": "global", "max_tokens": 8192}
    assert identity["config_hash"] == hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(), "byte-identical to before"
    assert "structured" not in identity
    monkeypatch.setenv("MODEL_HIGH_THINKING_LEVEL", "low")
    assert model_for("context").identity() == identity, "another tier's setting does not apply to the default model"
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_HIGH_NAME", PRO)
    high = model_for("context").identity()
    assert high["structured"] == {"MODEL_HIGH_THINKING_LEVEL": "low"} and high["config_hash"] != identity["config_hash"]
    monkeypatch.setenv("ASK_UNKNOWN_WORD", "abstain")
    monkeypatch.setenv("ASK_MODEL_CORRECTIONS", "1")
    assert model_for("context").identity() == high, "the corrections and unknown-word settings are not the model's"


def test_the_readers_identity_never_moves_with_asks_settings(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_NAME", PRO)
    for tier, name in (("HIGH", PRO), ("MEDIUM", "gemini-3.8-flash")):
        monkeypatch.setenv(f"MODEL_{tier}_PROVIDER", "vertex-gemini")
        monkeypatch.setenv(f"MODEL_{tier}_NAME", name)
    reader, ask = model_for("reader").identity(), model_for("context").identity()
    for name, value in (("ASK_PLAN_MAX_TOKENS", "20000"), ("MODEL_THINKING_LEVEL", "low"), ("MODEL_HIGH_THINKING_TOKENS", "600")):
        monkeypatch.setenv(name, value)
    assert model_for("reader").identity() == reader, "Ask's settings stay out of the document reader's tier"
    moved = model_for("context").identity()
    assert moved["config_hash"] != ask["config_hash"]
    assert moved["structured"] == {"ASK_PLAN_MAX_TOKENS": "20000", "MODEL_THINKING_LEVEL": "low", "MODEL_HIGH_THINKING_TOKENS": "600"}
    monkeypatch.delenv("MODEL_HIGH_PROVIDER")
    assert model_for("context").identity()["structured"] == {"ASK_PLAN_MAX_TOKENS": "20000", "MODEL_THINKING_LEVEL": "low"}, \
        "with no high tier Ask runs on medium, whose identity then carries them"


def test_the_mock_is_sent_the_legacy_prompt_and_replays_its_recording_by_key():
    from tests.plan_recordings import recorded

    key = '["query-plan-v1","Metric","a","b"]'
    reply = {"kind": "abstain", "spans": [], "missing": ["recorded"]}
    llm = recorded({key: reply})
    legacy = "query-plan-v1\n" + json.dumps({"recording_key": key, "question": "Metric"})
    with runtrace.start("ask") as t:
        out = llm.structured(request("plan", legacy_prompt=legacy, content="never read"))
        rt = t.finish({})
    assert out.data == reply and out.text == json.dumps(reply, sort_keys=True)
    assert llm.single_text, "the mock takes the single-text form alone, so it is sent no correction"
    assert rt["llm_calls"][0]["served_model"] == "mock" and rt["llm_calls"][0]["tokens_in"] == len(legacy.split())
    with pytest.raises(ValueError, match="plan, classify, wording"):
        llm.structured(request("summary"))


def test_the_trace_keeps_thinking_and_contract_tokens_apart_and_the_attempts(monkeypatch):
    llm, _ = gemini(monkeypatch, PRO, "high", gemini_reply(), gemini_reply(None, "MAX_TOKENS", thoughts=16000))
    with runtrace.start("ask") as t:
        runtrace.stage("plan")
        llm.structured(request("plan"))
        with pytest.raises(Truncated):
            llm.structured(request("plan", attempt=1))
        runtrace.model_attempt("plan", 1, "truncated", ["the reply reached its 16384-token budget"])
        llm.complete("a plain call")
        rt = t.finish({})
    first, second, plain = rt["llm_calls"]
    assert (first["tokens_in"], first["tokens_out"], first["tokens_thinking"], first["tokens_contract"]) == (120, 309, 300, 9)
    assert (first["call"], first["attempt"], first["max_tokens"], first["thinking"], first["finish"]) == ("plan", 0, 16384, "level:low", "STOP")
    assert (second["attempt"], second["finish"], second["tokens_thinking"]) == (1, "max_tokens", 16000)
    assert set(plain) == {"stage", "model", "served_model", "tokens_in", "tokens_out", "ms", "ok"}, "a plain call's record is as before"
    assert rt["model_attempts"] == [{"call": "plan", "attempt": 1, "outcome": "truncated", "errors": ["the reply reached its 16384-token budget"]}]
    with runtrace.start("ask") as t:
        assert "model_attempts" not in t.finish({})
    runtrace.model_attempt("plan", 0, "contract")


def test_a_failed_structured_call_is_traced_with_its_call_and_raises_what_it_raised(monkeypatch):
    llm, _ = gemini(monkeypatch, PRO, "high")

    def down(url, headers, body):
        raise ExternalCallError("Vertex AI unreachable", transient=True)
    llm._post = down
    with runtrace.start("ask") as t:
        with pytest.raises(ExternalCallError):
            llm.structured(request("classify"))
        rt = t.finish({})
    call, = rt["llm_calls"]
    assert (call["ok"], call["error"], call["call"], call["thinking"], call["tokens_thinking"]) == (False, "ExternalCallError", "classify",
                                                                                                    "tokens:512", None)


def started(**env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in CLEARED}
    return subprocess.run([sys.executable, "-c", "import kge.app"], cwd=DEMO, env={**clean, "MODEL_PROVIDER": "none", **env},
                          capture_output=True, text=True, timeout=120)


def test_the_app_refuses_to_start_on_a_thinking_setting_its_model_cannot_honour():
    refused = started(MODEL_HIGH_PROVIDER="vertex-claude", MODEL_HIGH_THINKING_TOKENS="800")
    assert refused.returncode == 1 and "Traceback" not in refused.stderr
    assert refused.stderr.strip().splitlines()[-1].startswith("MODEL_HIGH_THINKING_TOKENS is set, but vertex-claude:claude-haiku-5-5")
    corrections = started(ASK_MODEL_CORRECTIONS="3")
    assert corrections.returncode == 1 and "ASK_MODEL_CORRECTIONS must be 0, 1 or 2" in corrections.stderr


def plan_schemas() -> list[tuple[str, dict]]:
    """The planner's two contracts as Ask sends them: the rules-first contract over a few ids, and query-plan-v1's whole
    PlanReply (ASK_ORDER=model_first), both of which nest predicates."""
    from kge.services.ask.engine.modelcall import contract_schema
    from kge.services.ask.engine.plancontract import PlanContract
    from kge.services.ask.engine.planmodel import reply_schema
    return [("contract", contract_schema(PlanContract, ["u.metric", "ctx", "cal"])), ("plan-reply", reply_schema(removals=True))]


def keywords(node, found: set[str] | None = None) -> set[str]:
    """Every schema keyword in node; the keys of a properties or $defs map are names, not keywords."""
    found = set() if found is None else found
    if isinstance(node, list):
        for child in node:
            keywords(child, found)
    elif isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in ("properties", "$defs"):
                for child in value.values():
                    keywords(child, found)
            elif key not in ("enum", "required", "default"):
                keywords(value, found)
    return found


@pytest.mark.parametrize("provider", ["gemini", "vertex-gemini", "anthropic", "vertex-claude", "openai", "ollama"])
def test_no_provider_is_sent_a_cyclic_schema_and_gemini_no_unlisted_keyword(provider):
    from kge.adapters.model.providers import cyclic
    for name, schema in plan_schemas():
        assert {"Junction", "Not", "Membership"} <= cyclic(schema), f"the {name} schema nests predicates"
        sent = provider_schema(schema, provider)
        assert cyclic(sent) == set(), f"a cyclic $ref, required or not, reaches {provider} in the {name} schema"
        if provider in ("gemini", "vertex-gemini"):
            assert not keywords(sent) & {"default", "minLength", "maxLength", "pattern"}, name


def test_the_unrolled_contract_takes_predicates_three_deep_for_claude_one_for_gemini_and_pydantic_the_rest():
    """Gemini's schema is unrolled one level (Vertex AI inlines every $ref), so it takes a junction of comparisons and not
    a predicate nested in another's children; Claude's takes three levels; Pydantic takes any depth."""
    import jsonschema
    from kge.services.ask.engine.modelcall import contract_schema
    from kge.services.ask.engine.plancontract import PlanContract
    compare = {"kind": "compare", "id": "c", "left": {"kind": "column", "name": "u.metric"}, "op": "eq",
               "value": {"kind": "text", "value": "x"}}
    nested = {"kind": "all", "id": "a", "children": [{"kind": "not", "id": "n", "child": {"kind": "any", "id": "b",
                                                                                           "children": [compare]}}]}
    deeper = {"kind": "all", "id": "a", "children": [{"kind": "not", "id": "n", "child": {"kind": "any", "id": "b",
              "children": [{"kind": "not", "id": "m", "child": compare}]}}]}
    schema = contract_schema(PlanContract, ["u.metric", "ctx", "cal"])

    def contract(predicate):
        return {"decision": "propose", "plan": {"measures": [{"kind": "measure", "id": "m", "measure": "u.metric",
                                                             "context": "ctx", "filters": [predicate]}],
                                                "time": {"kind": "all_available", "calendar": "cal"}}}
    flat = {"kind": "all", "id": "a", "children": [compare]}
    claude_form = provider_schema(schema, "anthropic")
    jsonschema.validate(contract(nested), claude_form)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(contract(deeper), claude_form)
    gemini_form = provider_schema(schema, "vertex-gemini")
    jsonschema.validate(contract(flat), gemini_form)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(contract(nested), gemini_form)
    ids = {"ids": frozenset({"u.metric", "ctx", "cal"})}
    PlanContract.model_validate(contract(deeper), context=ids)


def test_a_flash_lite_model_is_never_sent_less_thinking_than_it_takes(monkeypatch):
    steps = [call_budget("plan", "high", "vertex-gemini", "gemini-2.5-flash-lite", configured_max_tokens=None, less_thinking=n)
             for n in range(3)]
    assert [(b.thinking["tokens"], b.more_room) for b in steps] == [(1024, True), (512, False), (512, False)]
    monkeypatch.setenv("ASK_CLASSIFY_THINKING_TOKENS", "256")
    assert call_budget("classify", "high", "vertex-gemini", "gemini-2.5-flash-lite", configured_max_tokens=None).thinking == {"tokens": 512}
    assert call_budget("classify", "high", "vertex-gemini", "gemini-2.5-pro", configured_max_tokens=None).thinking == {"tokens": 256}


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_a_chat_completions_reply_cut_off_with_no_budget_sent_names_the_models_own_limit(monkeypatch, provider):
    reply = {"choices": [{"message": {"content": '{"ter'}, "finish_reason": "length"}], "usage": {}}
    llm, sent = rest_model(monkeypatch, provider, "a-model", reply)
    with runtrace.start("ask") as t:
        with pytest.raises(Truncated) as caught:
            llm.structured(request("plan"))
        rt = t.finish({})
    assert "max_tokens" not in sent[0]["body"] and caught.value.budget is None
    assert str(caught.value) == "the reply reached the model's own output limit, as no budget is sent; set ASK_PLAN_MAX_TOKENS"
    assert rt["llm_calls"][0]["max_tokens"] is None
    monkeypatch.setenv("ASK_PLAN_MAX_TOKENS", "9000")
    with pytest.raises(Truncated, match="its 9000-token budget"):
        llm.structured(request("plan"))


def test_a_reply_that_is_not_json_carries_its_text_and_record_for_the_correction(monkeypatch):
    llm, _ = gemini(monkeypatch, PRO, "high", gemini_reply("I would read it as revenue."))
    with pytest.raises(NotJSON) as caught:
        llm.structured(request("plan"))
    assert caught.value.reply_text == "I would read it as revenue."
    assert caught.value.replay == {"role": "model", "parts": [{"text": "I would read it as revenue.", "thoughtSignature": "c2ln"}]}
