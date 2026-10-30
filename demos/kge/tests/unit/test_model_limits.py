"""A model's reply budget (#94 item 1): MODEL_MAX_TOKENS and MODEL_<TIER>_MAX_TOKENS.

    .venv/bin/python -m pytest tests/unit/test_model_limits.py -q

Each caller asks for its own budget; a configured one replaces it. Precedence: MODEL_<TIER>_MAX_TOKENS for the tier's
model, then MODEL_MAX_TOKENS, then the image default of 8192. A tier with no provider falls back to the default model whole,
its budget included. The budget is read where it is sent: Gemini's generationConfig.maxOutputTokens, through a stand-in
transport, so nothing leaves the process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kge.adapters.model import LLM, ModelSettingError, check_settings, model_for
from kge.pipeline.tagger import ModelTagger

LIMITS = ("MODEL_MAX_TOKENS", "MODEL_HIGH_MAX_TOKENS", "MODEL_MEDIUM_MAX_TOKENS", "MODEL_LOW_MAX_TOKENS")
TIERS = ("HIGH", "MEDIUM", "LOW")


@pytest.fixture(autouse=True)
def plain(monkeypatch):
    """No budget, tier or agent setting from the caller's environment steers a test."""
    for name in (*LIMITS, "MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL", "ANSWER_MODEL_TIER", "ONBOARD_MODEL_TIER",
                 "TAGGER_PROVIDER", "TAGGER_MODEL_TIER"):
        monkeypatch.delenv(name, raising=False)
    for tier in TIERS:
        for part in ("PROVIDER", "NAME", "API_KEY", "BASE_URL"):
            monkeypatch.delenv(f"MODEL_{tier}_{part}", raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_NAME", "default-model")
    monkeypatch.setenv("MODEL_API_KEY", "a-key")


def sent_budget(llm: LLM, asked: int) -> int:
    """The maxOutputTokens a Gemini call sends when its caller asks for `asked`."""
    sent: list[dict] = []
    llm._post = lambda url, headers, body: sent.append(body) or {
        "candidates": [{"content": {"parts": [{"text": "{}"}]}}], "usageMetadata": {}}
    llm.complete("a prompt", max_tokens=asked)
    return sent[0]["generationConfig"]["maxOutputTokens"]


def test_without_the_settings_the_image_budget_is_sent():
    llm = model_for("answer")
    assert llm.max_tokens == 8192
    assert sent_budget(llm, 900) == 8192 and sent_budget(llm, 200) == 8192
    assert llm.identity()["max_tokens"] == 8192


def test_model_max_tokens_replaces_every_caller_s_budget(monkeypatch):
    monkeypatch.setenv("MODEL_MAX_TOKENS", "4096")
    llm = model_for("answer")
    assert sent_budget(llm, 900) == 4096 and sent_budget(llm, 8000) == 4096
    assert llm.identity()["max_tokens"] == 4096


def test_a_tier_s_budget_wins_over_the_global_one(monkeypatch):
    monkeypatch.setenv("MODEL_MAX_TOKENS", "1000")
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "medium-model")
    monkeypatch.setenv("MODEL_MEDIUM_MAX_TOKENS", "4096")
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_LOW_NAME", "low-model")
    answer, context = model_for("answer"), model_for("context")
    assert (answer.tier, sent_budget(answer, 900)) == ("medium", 4096)
    assert (context.tier, sent_budget(context, 900)) == ("low", 1000)
    monkeypatch.delenv("MODEL_MAX_TOKENS")
    assert sent_budget(model_for("context"), 900) == 8192


def test_a_tier_with_no_provider_falls_back_to_the_default_model_and_its_budget(monkeypatch):
    monkeypatch.setenv("MODEL_HIGH_MAX_TOKENS", "4096")
    llm = model_for("onboard")
    assert llm.tier == "default" and llm.max_tokens == 8192 and sent_budget(llm, 1800) == 8192
    monkeypatch.setenv("MODEL_MAX_TOKENS", "2048")
    assert sent_budget(model_for("onboard"), 1800) == 2048


def test_the_budget_changes_the_config_hash_only_when_set(monkeypatch):
    before = model_for("answer").identity()["config_hash"]
    monkeypatch.setenv("MODEL_MAX_TOKENS", "4096")
    assert model_for("answer").identity()["config_hash"] != before
    monkeypatch.delenv("MODEL_MAX_TOKENS")
    assert model_for("answer").identity()["config_hash"] == before


def test_a_tagger_built_directly_reads_the_global_budget(monkeypatch):
    monkeypatch.setenv("MODEL_MAX_TOKENS", "2048")
    tagger = ModelTagger([], "gemini", "tagger-model", "a-key")
    assert sent_budget(tagger.llm, 400) == 2048
    assert LLM("gemini", "m", "k", max_tokens=300).max_tokens == 300


@pytest.mark.parametrize("raw", ["0", "-5", "many", "1.5"])
def test_a_budget_that_is_not_a_whole_number_above_zero_is_refused(monkeypatch, raw):
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_LOW_NAME", "low-model")
    monkeypatch.setenv("MODEL_LOW_MAX_TOKENS", raw)
    with pytest.raises(ModelSettingError, match="MODEL_LOW_MAX_TOKENS must be a whole number"):
        model_for("context")
    monkeypatch.delenv("MODEL_LOW_MAX_TOKENS")
    monkeypatch.setenv("MODEL_MAX_TOKENS", raw)
    with pytest.raises(ModelSettingError, match="MODEL_MAX_TOKENS must be a whole number"):
        model_for("answer")


def chat_completions_body(monkeypatch, provider: str, asked: int) -> dict:
    """The body an openai or ollama call sends to /chat/completions when its caller asks for `asked`."""
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    llm = model_for("answer")
    sent: list[dict] = []
    llm._post = lambda url, headers, body: sent.append(body) or {
        "choices": [{"message": {"content": "{}"}}], "usage": {}}
    llm.complete("a prompt", max_tokens=asked)
    return sent[0]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_a_configured_budget_reaches_the_chat_completions_body(monkeypatch, provider):
    monkeypatch.setenv("MODEL_MAX_TOKENS", "4096")
    body = chat_completions_body(monkeypatch, provider, 900)
    assert body["max_tokens"] == 4096


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_a_tier_budget_reaches_the_chat_completions_body(monkeypatch, provider):
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", provider)
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "tier-model")
    monkeypatch.setenv("MODEL_MEDIUM_MAX_TOKENS", "2048")
    body = chat_completions_body(monkeypatch, provider, 900)
    assert body["model"] == "tier-model" and body["max_tokens"] == 2048


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_without_an_override_chat_completions_sends_the_image_budget(monkeypatch, provider):
    body = chat_completions_body(monkeypatch, provider, 900)
    assert set(body) == {"model", "messages", "temperature", "seed", "max_tokens"}
    assert body["max_tokens"] == 8192


DEMO = Path(__file__).resolve().parents[2]
CHECKED = ("MODEL_SEED", "MODEL_MAX_TOKENS", "MODEL_HIGH_MAX_TOKENS", "MODEL_MEDIUM_MAX_TOKENS", "MODEL_LOW_MAX_TOKENS")


@pytest.mark.parametrize("name", CHECKED)
def test_every_process_wide_model_setting_is_checked_with_no_model_configured(monkeypatch, name):
    for other in CHECKED:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    check_settings()
    monkeypatch.setenv(name, "abc")
    with pytest.raises(ModelSettingError, match=f"^{name} must be a whole number"):
        check_settings()


def started(**env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in CHECKED}
    return subprocess.run([sys.executable, "-c", "import kge.app"], cwd=DEMO, env={**clean, "MODEL_PROVIDER": "none", **env},
                          capture_output=True, text=True, timeout=120)


def test_the_app_refuses_to_start_on_an_invalid_budget_naming_the_setting():
    """An invalid MODEL_*_MAX_TOKENS was a bare 500 at sign-in while /healthz answered ok (Grok on #97): the app now
    stops as it starts, in one line naming the setting, as it does for SIGNIN."""
    refused = started(MODEL_MEDIUM_MAX_TOKENS="lots")
    assert refused.returncode == 1 and "Traceback" not in refused.stderr
    assert refused.stderr.strip().splitlines()[-1] == "MODEL_MEDIUM_MAX_TOKENS must be a whole number of tokens above 0, not 'lots'"
    assert started(MODEL_MEDIUM_MAX_TOKENS="4096").returncode == 0
