"""Ask's planner thinks at level low by default on Gemini 3 and later (#589): with no thinking setting applying to it,
the planner call to a Gemini 3 or Gemini 4 model is sent the thinking level low, from #571's measurement on the pilot.
A set token budget or level still wins; other models, and the classify and wording calls, keep their defaults.

    .venv/bin/python -m pytest tests/unit/test_plan_thinking_low.py -q

Nothing leaves the process: the provider's post is a stand-in transport that keeps the body and answers in Vertex AI's
place, and the service account is a stand-in.
"""
from __future__ import annotations

import httpx2
import pytest

from kge.adapters.model import LLM, call_budget, check_settings, model_for
from kge.adapters.model.providers import Chat
from kge.ports import ModelRequest

TOKEN = "ya29.stand-in-token"
PROJECT = "pilot-project"
GEMINI_3_MODELS = ("gemini-3.1-pro-preview", "gemini-3-pro", "gemini-3.8-flash", "gemini-3.5-flash-lite")
GEMINI_4_MODELS = ("gemini-4-argon", "gemini-4.1-pro-preview")
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


def request(call: str) -> ModelRequest:
    return ModelRequest(call=call, version="test-v1", instructions="Reply with the contract alone.", content="## Question\nRevenue?",
                        schema=SCHEMA, contract="Contract", legacy_prompt="legacy\n{}")


def gemini(monkeypatch, model: str) -> tuple[LLM, list[dict]]:
    """The context agent's model on vertex-gemini, the bodies it sends kept by a stand-in transport."""
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_NAME", model)
    llm = model_for("context")
    sent: list[dict] = []
    reply = {"candidates": [{"content": {"role": "model", "parts": [{"text": '{"terms": ["revenue"]}'}]}, "finishReason": "STOP"}],
             "modelVersion": f"{model}-001", "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 9, "thoughtsTokenCount": 40}}

    def post(url, headers, body):
        sent.append(body)
        return reply
    llm._post = post
    return llm, sent


def thinking_sent(llm: LLM, sent: list[dict], call: str) -> dict:
    """The thinkingConfig of one call's request, as sent."""
    llm.structured(request(call))
    return sent[-1]["generationConfig"]["thinkingConfig"]


@pytest.mark.parametrize("model", (*GEMINI_3_MODELS, *GEMINI_4_MODELS))
def test_the_planner_on_gemini_3_and_later_thinks_at_level_low_by_default(monkeypatch, model):
    llm, sent = gemini(monkeypatch, model)
    assert thinking_sent(llm, sent, "plan") == {"thinkingLevel": "low"}
    budget = call_budget("plan", "default", "gemini", model, configured_max_tokens=None)
    assert (budget.thinking, budget.thinking_setting, budget.sent(), budget.more_room) == ({"level": "low"}, None, "level:low", False)
    check_settings()


@pytest.mark.parametrize("model", GEMINI_3_MODELS)
def test_classify_and_wording_on_gemini_3_keep_their_token_cap(monkeypatch, model):
    llm, sent = gemini(monkeypatch, model)
    for call in ("classify", "wording"):
        assert thinking_sent(llm, sent, call) == {"thinkingBudget": 512}


@pytest.mark.parametrize("model", GEMINI_4_MODELS)
def test_classify_and_wording_on_gemini_4_keep_their_level_low(monkeypatch, model):
    llm, sent = gemini(monkeypatch, model)
    for call in ("classify", "wording"):
        assert thinking_sent(llm, sent, call) == {"thinkingLevel": "low"}


@pytest.mark.parametrize("setting", ("ASK_PLAN_THINKING_TOKENS", "MODEL_THINKING_TOKENS", "MODEL_HIGH_THINKING_TOKENS"))
def test_a_set_token_budget_wins_on_gemini_3(monkeypatch, setting):
    monkeypatch.setenv("CONTEXT_MODEL_TIER", "high")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_HIGH_NAME", "gemini-3.1-pro-preview")
    monkeypatch.setenv(setting, "640")
    llm, sent = gemini(monkeypatch, "gemini-3.1-pro-preview")
    assert thinking_sent(llm, sent, "plan") == {"thinkingBudget": 640}
    assert call_budget("plan", "high", "vertex-gemini", "gemini-3.1-pro-preview", configured_max_tokens=None).thinking_setting == setting


@pytest.mark.parametrize("model", (*GEMINI_3_MODELS, *GEMINI_4_MODELS))
@pytest.mark.parametrize("setting", ("ASK_PLAN_THINKING_LEVEL", "MODEL_THINKING_LEVEL"))
@pytest.mark.parametrize("level", ("medium", "high"))
def test_a_set_level_wins(monkeypatch, model, setting, level):
    monkeypatch.setenv(setting, level)
    llm, sent = gemini(monkeypatch, model)
    assert thinking_sent(llm, sent, "plan") == {"thinkingLevel": level}
    assert call_budget("plan", "default", "vertex-gemini", model, configured_max_tokens=None).thinking_setting == setting


@pytest.mark.parametrize(("provider", "model", "thinking"), (
    ("vertex-gemini", "gemini-2.5-pro", {"tokens": 1024}),
    ("gemini", "gemini-2.5-flash", {"tokens": 1024}),
    ("vertex-claude", "claude-haiku-5-5", {}),
    ("anthropic", "claude-opus-5-5", {}),
    ("openai", "gpt-5", {}),
    ("ollama", "llama3", {}),
    ("mock", "mock", {"tokens": 1024}),
))
def test_other_models_keep_the_planner_s_default(provider, model, thinking):
    assert call_budget("plan", "default", provider, model, configured_max_tokens=None).thinking == thinking


def test_the_tier_check_leaves_ask_s_default_out(monkeypatch):
    budget = call_budget("plan", "high", "vertex-gemini", "gemini-3.1-pro-preview", configured_max_tokens=None, ask=False)
    assert budget.thinking == {"tokens": 1024}, "a tier's own check reads the tier's settings, not the planner's default"
