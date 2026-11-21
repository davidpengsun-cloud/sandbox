"""Gemini 4 Argon in KGE's model-family checks (#577): later Gemini generations are treated as Gemini 3 is, no temperature
on Ask's structured calls and a thinking level, except where Vertex AI takes a setting differently: Gemini 4 refuses a
thinking token budget, so it is sent a level alone.

    .venv/bin/python -m pytest tests/unit/test_model_gemini4.py -q

Nothing leaves the process: the provider's post is a stand-in transport that keeps the body and answers in Vertex AI's
place, and the service account is a stand-in.
"""
from __future__ import annotations

from dataclasses import replace

import httpx2
import pytest

from kge.adapters.model import LLM, ModelSettingError, call_budget, check_settings, model_for, thinking_support
from kge.adapters.model.providers import GEMINI_3, GEMINI_BUDGET, Chat
from kge.ports import ModelRequest, ModelTurn

TOKEN = "ya29.stand-in-token"
PROJECT = "pilot-project"
ARGON = "gemini-4-argon"
GEMINI_4 = (ARGON, "gemini-4.0-argon", "gemini-4.1-pro-preview", "gemini-4-flash", "gemini-5-pro")
CLEARED = ("MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_MAX_TOKENS", "MODEL_SEED", "MOCK_MODE", "VERTEX_REGION",
           "VERTEX_PROJECT", "CONTEXT_MODEL_TIER", "ASK_UNKNOWN_WORD", "ASK_MODEL_CORRECTIONS",
           *(f"ASK_{call}_{part}" for call in ("PLAN", "CLASSIFY", "WORDING") for part in ("MAX_TOKENS", "THINKING_TOKENS", "THINKING_LEVEL")),
           *(f"MODEL{tier}_THINKING_{part}" for tier in ("", "_HIGH", "_MEDIUM", "_LOW") for part in ("TOKENS", "LEVEL")),
           *(f"MODEL_{tier}_{part}" for tier in ("HIGH", "MEDIUM", "LOW") for part in ("PROVIDER", "NAME", "API_KEY", "BASE_URL", "MAX_TOKENS")))
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["terms"],
          "properties": {"terms": {"type": "array", "items": {"type": "string"}}}}


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


def request(call: str = "plan") -> ModelRequest:
    return ModelRequest(call=call, version="test-v1", instructions="Reply with the contract alone.", content="## Question\nRevenue?",
                        schema=SCHEMA, contract="Contract", legacy_prompt="legacy\n{}")


def argon(monkeypatch, model: str = ARGON) -> tuple[LLM, list[dict]]:
    """The context agent's model on vertex-gemini, its bodies kept; each reply has a thought part and a signed text part."""
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_NAME", model)
    llm = model_for("context")
    sent: list[dict] = []
    reply = {"candidates": [{"content": {"role": "model", "parts": [{"text": "private thinking", "thought": True},
                                                                    {"text": '{"terms": ["revenue"]}', "thoughtSignature": "ZzQ="}]},
                             "finishReason": "STOP"}],
             "modelVersion": f"{model}-001", "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 9, "thoughtsTokenCount": 40}}

    def post(url, headers, body):
        sent.append({"url": url, "body": body})
        return reply
    llm._post = post
    return llm, sent


@pytest.mark.parametrize("model", GEMINI_4)
def test_gemini_4_ids_are_a_later_gemini_that_takes_a_level_and_no_budget(model):
    assert GEMINI_3.match(model)
    assert not GEMINI_BUDGET.match(model)
    for provider in ("gemini", "vertex-gemini"):
        assert thinking_support(provider, model) == (False, True)


@pytest.mark.parametrize("model", ["gemini-3.1-pro-preview", "gemini-3-pro", "gemini-3", "gemini-3.8-flash", "gemini-2.5-flash"])
def test_gemini_2_5_and_3_keep_their_budget(model):
    assert GEMINI_BUDGET.match(model)
    assert thinking_support("vertex-gemini", model) == (True, bool(GEMINI_3.match(model)))


def test_gemini_4_argon_s_ask_call_has_no_temperature_and_thinks_at_level_low_unless_a_level_is_set(monkeypatch):
    llm, sent = argon(monkeypatch)
    reply = llm.structured(request("plan"))
    assert reply.data == {"terms": ["revenue"]}, "the thought part is not read as the reply"
    assert sent[0]["url"].endswith(f"/projects/{PROJECT}/locations/global/publishers/google/models/{ARGON}:generateContent")
    config = sent[0]["body"]["generationConfig"]
    assert "temperature" not in config, "Gemini 4 is sent no temperature, as Gemini 3 is"
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}, "no budget: the level low in place of the token cap"
    assert config["maxOutputTokens"] == 16384 and config["responseMimeType"] == "application/json"
    monkeypatch.setenv("MODEL_THINKING_LEVEL", "high")
    llm.structured(request("classify"))
    assert sent[1]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}
    assert call_budget("wording", "default", "vertex-claude", "claude-haiku-5-5", configured_max_tokens=None).thinking == {"level": "high"}
    monkeypatch.delenv("MODEL_THINKING_LEVEL")
    assert call_budget("wording", "default", "vertex-claude", "claude-haiku-5-5", configured_max_tokens=None).thinking == {}, \
        "the default level is Gemini's alone: another level-only model is sent nothing, as before"


def test_a_thinking_token_setting_on_gemini_4_is_refused_naming_the_level_to_set(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_NAME", ARGON)
    monkeypatch.setenv("ASK_PLAN_THINKING_TOKENS", "600")
    with pytest.raises(ModelSettingError, match="ASK_PLAN_THINKING_TOKENS is set, but vertex-gemini:gemini-4-argon takes no thinking budget: set ASK_PLAN_THINKING_LEVEL"):
        check_settings()
    with pytest.raises(ModelSettingError, match="ASK_PLAN_THINKING_TOKENS"):
        call_budget("plan", "default", "vertex-gemini", ARGON, configured_max_tokens=None)


def test_a_correction_turn_sends_gemini_4_s_reply_back_with_its_signature_and_no_thought(monkeypatch):
    llm, sent = argon(monkeypatch)
    first = llm.structured(request("plan"))
    assert first.replay == {"role": "model", "parts": [{"text": '{"terms": ["revenue"]}', "thoughtSignature": "ZzQ="}]}
    turns = (ModelTurn("model", first.text, first.replay), ModelTurn("user", "- /terms/0: not a declared id"))
    llm.structured(replace(request("plan"), turns=turns, attempt=2))
    assert sent[1]["body"]["contents"][1] == first.replay, "Gemini 4's reply goes back with its signature, without the thought"
