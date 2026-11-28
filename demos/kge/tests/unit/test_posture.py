"""The telemetry and model posture (#475), the values the kit's posture statement (kit/generate_posture.py) and the test
drive's report (kge/services/onboard/test_drive.py posture) give, proved where no other test proves them: with no model
setting, no agent has a model and nothing leaves the process for one; the app is built with FastAPI's own telemetry
off, every switch of it; and Claude on Vertex AI's client is built with the Anthropic SDK's own spans and trace header
off, as the client reads its option back."""
from __future__ import annotations

import os

import pytest

from kge.adapters.model.providers import LLM, PREFERRED_TIER, TIERS, make_llm, model_for, tagger_model
from kge.pipeline.telemetry import ANTHROPIC_TELEMETRY_OFF, FASTAPI_TELEMETRY_OFF
from kge.services.onboard.test_drive import posture

MODEL_SETTINGS = ("MODEL_PROVIDER", "TAGGER_PROVIDER", *(f"MODEL_{t.upper()}_PROVIDER" for t in TIERS if t != "default"),
                  *(f"{agent.upper()}_MODEL_TIER" for agent in PREFERRED_TIER))


@pytest.fixture
def no_model_setting(monkeypatch):
    """The image's environment for the model: none of its settings given, and no .env read."""
    for name in MODEL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)


def test_with_no_model_setting_no_agent_has_a_model(no_model_setting):
    assert make_llm() is None and tagger_model() is None
    assert {agent: model_for(agent) for agent in PREFERRED_TIER} == dict.fromkeys(PREFERRED_TIER)
    shown = posture()
    assert shown["MODEL_PROVIDER"] == "none"
    assert [shown[f"MODEL_{t.upper()}_PROVIDER"] for t in TIERS if t != "default"] == \
        ["unset: falls back to MODEL_PROVIDER (none)"] * 3


def test_a_tier_s_own_provider_is_shown_as_set(no_model_setting, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "Vertex-Gemini")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "vertex-claude")
    shown = posture()
    assert shown["MODEL_PROVIDER"] == "vertex-gemini" and shown["MODEL_HIGH_PROVIDER"] == "vertex-claude"
    assert shown["MODEL_LOW_PROVIDER"] == "unset: falls back to MODEL_PROVIDER (vertex-gemini)"


def test_an_unset_high_tier_names_the_medium_model_context_and_assist_take(no_model_setting, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", "mock")
    assert model_for("context").tier == model_for("assist").tier == "medium" and model_for("onboard").tier == "default"
    assert posture()["MODEL_HIGH_PROVIDER"] == ("unset: context and assist fall back to MODEL_MEDIUM_PROVIDER (mock), "
                                                "the others to MODEL_PROVIDER (mock)")


def test_the_app_is_built_with_fastapi_telemetry_off():
    import kge.app
    switches = {k: v for k, v in kge.app.app._telemetry.items() if isinstance(v, bool)}
    assert set(switches) == set(FASTAPI_TELEMETRY_OFF), "every switch FastAPI has is one the app sets"
    assert switches == FASTAPI_TELEMETRY_OFF and not any(switches.values())
    assert all(kge.app.app._telemetry[k] is None for k in ("tracer_provider", "meter_provider", "logger_provider"))


def test_the_anthropic_client_is_built_with_its_telemetry_off(monkeypatch):
    model = LLM("vertex-claude", "stand-in")
    monkeypatch.setattr(type(model.chat), "account", lambda self: (object(), "stand-in"))
    option = model.chat.anthropic_vertex().open_telemetry
    assert ANTHROPIC_TELEMETRY_OFF == {"traces": False, "propagation": False}
    assert option["propagation"] is False and option["traces"]["enabled"] is False
    assert "tracer_provider" not in option


def test_a_mistyped_tracing_setting_is_reported_never_raised(monkeypatch):
    monkeypatch.setenv("TRACING", "loud")
    monkeypatch.setenv("TRACE_PROPAGATE_TO", "ftp://kge.example.com")
    shown = posture()
    assert shown["TRACING"].startswith("refused: TRACING must be")
    assert shown["TRACE_PROPAGATE_TO"].startswith("refused: TRACE_PROPAGATE_TO must be")
    assert os.environ["TRACING"] == "loud", "posture reads, it changes nothing"


def test_the_opentelemetry_provider_settings_are_unset(monkeypatch):
    """An OpenTelemetry provider setting the environment gave is unset as the process starts (telemetry.off()), and the
    posture reports it unset."""
    from kge.pipeline import telemetry
    for name in telemetry.CLEARED:
        monkeypatch.setenv(name, "planted.Provider")
    telemetry.off()
    assert {name: posture()[name] for name in telemetry.CLEARED} == dict.fromkeys(telemetry.CLEARED, "unset")


def test_the_egress_limit_is_reported_as_given_and_unset_by_default(monkeypatch):
    monkeypatch.delenv("EGRESS_ALLOWED", raising=False)
    assert posture()["EGRESS_ALLOWED"] == "unset: no egress limit given to this process; the pilot's Terraform gives one"
    monkeypatch.setenv("EGRESS_ALLOWED", "storage.googleapis.com,bigquery.googleapis.com, ,bigquery.googleapis.com")
    assert posture()["EGRESS_ALLOWED"] == ("bigquery.googleapis.com, storage.googleapis.com, and the client's KGE and "
                                           "portal: every other address refused")
