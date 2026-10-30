"""Pilot regression checks, with every external model transport replaced in process."""
import io
import urllib.error
from types import SimpleNamespace

import httpx
import pytest
from anthropic import APIConnectionError, APIStatusError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters.model import LLM
from kge.adapters.model.errors import ExternalCallError, model_timeout, post_json
from kge.services.onboard import test_drive_run
from kge.services.tools import routes as tools_routes


@pytest.mark.parametrize("tier", ["default", "high", "medium", "low"])
def test_every_tier_defaults_to_8192(monkeypatch, tier):
    for name in ("MODEL_MAX_TOKENS", "MODEL_HIGH_MAX_TOKENS", "MODEL_MEDIUM_MAX_TOKENS", "MODEL_LOW_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    assert LLM("mock", "mock", tier=tier).max_tokens == 8192


def test_mock_records_an_explicit_budget_but_not_the_unused_image_default(monkeypatch):
    monkeypatch.delenv("MODEL_MAX_TOKENS", raising=False)
    default = LLM("mock", "mock").identity()
    assert "max_tokens" not in default
    monkeypatch.setenv("MODEL_MAX_TOKENS", "8192")
    explicit = LLM("mock", "mock").identity()
    assert explicit["max_tokens"] == 8192 and explicit["config_hash"] != default["config_hash"]


@pytest.mark.parametrize("path", ["/health", "/healthz"])
def test_health_works_without_an_engine(monkeypatch, path):
    import kge.app as appmod
    monkeypatch.setattr(appmod, "engine", lambda: pytest.fail("health must not open the graph"))
    app = FastAPI()
    app.include_router(tools_routes.router)
    app.add_middleware(appmod.PinEngineMiddleware)
    with TestClient(app) as client:
        payload = client.get(path).json()
        assert payload["status"] == "ok"
        if path == "/health":
            assert payload == {"status": "ok"}
        else:
            assert "version" in payload
        reply = client.head(path)
        assert reply.status_code == 200 and not reply.content


@pytest.mark.parametrize("candidate", [None, {"finishReason": "MAX_TOKENS"},
                                      {"finishReason": "MAX_TOKENS", "content": {}},
                                      {"finishReason": "MAX_TOKENS", "content": {"parts": []}}])
def test_empty_gemini_candidate_reports_finish_reason(candidate):
    model = LLM("gemini", "stand-in")
    model._post = lambda *args: {"candidates": [candidate] if candidate else []}
    with pytest.raises(ExternalCallError, match="finishReason=" + ("MAX_TOKENS" if candidate else "unknown")):
        model.complete("probe")


@pytest.mark.parametrize("budget", [1, 8192, 32000])
def test_probe_sends_256_tokens_even_with_a_configured_budget(monkeypatch, budget):
    sent = []
    model = LLM("gemini", "stand-in", max_tokens=budget)
    model._post = lambda url, headers, body: sent.append(body) or {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
    monkeypatch.setattr(test_drive_run, "configured_models", lambda: {model.name: model})
    assert "one call each" in test_drive_run.check_models()
    assert sent[0]["generationConfig"]["maxOutputTokens"] == 256
    assert model.max_tokens == budget


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 503])
@pytest.mark.parametrize("recovery", [False, True])
def test_http_retries_only_one_transient_failure(monkeypatch, code, recovery):
    calls = []
    monkeypatch.delenv("MODEL_TIMEOUT", raising=False)

    def send(req, timeout):
        calls.append(timeout)
        if len(calls) == 2 and recovery:
            return io.BytesIO(b'{"ok":true}')
        raise urllib.error.HTTPError(req.full_url, code, "failed", {}, io.BytesIO(b"failed"))

    monkeypatch.setattr("urllib.request.urlopen", send)
    retry = code == 429 or code >= 500
    if recovery and retry:
        assert post_json("https://models.example.com", {}, {}) == {"ok": True}
    else:
        with pytest.raises(ExternalCallError):
            post_json("https://models.example.com", {}, {})
    assert calls == [180] * (2 if retry else 1)


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), urllib.error.URLError("offline"), ConnectionError("offline")])
def test_transport_retry_and_timeout_override(monkeypatch, failure):
    calls = []
    monkeypatch.setenv("MODEL_TIMEOUT", "17.5")

    def send(req, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise failure
        return io.BytesIO(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", send)
    assert post_json("https://models.example.com", {}, {}) == {}
    assert calls == [17.5, 17.5]


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "many"])
def test_invalid_timeouts_are_refused(monkeypatch, raw):
    monkeypatch.setenv("MODEL_TIMEOUT", raw)
    with pytest.raises(ValueError, match="MODEL_TIMEOUT"):
        model_timeout()


@pytest.mark.parametrize("code", [None, 400, 401, 403, 429, 503])
@pytest.mark.parametrize("recovery", [False, True])
def test_vertex_sdk_has_one_transient_retry(monkeypatch, code, recovery):
    calls = []
    model = LLM("vertex-claude", "stand-in")
    request = httpx.Request("POST", "https://models.example.com")

    def create(**kwargs):
        calls.append(kwargs)
        if recovery and len(calls) == 2:
            return SimpleNamespace(model_dump=lambda: {"content": [{"type": "text", "text": "OK"}]})
        if code is None:
            raise APIConnectionError(request=request)
        raise APIStatusError("secret must not leak", response=httpx.Response(code, request=request), body=None)

    monkeypatch.setattr(type(model.chat), "anthropic_vertex", lambda self: SimpleNamespace(messages=SimpleNamespace(create=create)))
    retry = code is None or code == 429 or code >= 500
    if recovery and retry:
        assert model.complete("probe") == "OK"
    else:
        with pytest.raises(ExternalCallError) as caught:
            model.complete("probe")
        assert "secret must not leak" not in str(caught.value)
    assert len(calls) == (2 if retry else 1)


@pytest.mark.parametrize("timeout", [None, "23"])
def test_vertex_sdk_timeout_and_internal_retries(monkeypatch, timeout):
    captured = []
    model = LLM("vertex-claude", "stand-in")
    monkeypatch.setattr(type(model.chat), "account", lambda self: (object(), "stand-in"))
    monkeypatch.setattr("anthropic.AnthropicVertex", lambda **kwargs: captured.append(kwargs) or object())
    monkeypatch.delenv("MODEL_TIMEOUT", raising=False)
    if timeout:
        monkeypatch.setenv("MODEL_TIMEOUT", timeout)
    model.chat.anthropic_vertex()
    assert captured[0]["timeout"] == (23 if timeout else 180) and captured[0]["max_retries"] == 0


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "many", "300", "360"])
def test_invalid_total_budgets_are_refused_at_startup(monkeypatch, raw):
    from kge.adapters.model.providers import check_settings, ModelSettingError
    monkeypatch.setenv("MODEL_TOTAL_TIMEOUT", raw)
    with pytest.raises(ModelSettingError, match="MODEL_TOTAL_TIMEOUT"):
        check_settings()


@pytest.mark.parametrize("transport", ["rest", "vertex"])
@pytest.mark.parametrize("elapsed,budget,expected", [(180, 270, [180, 90]), (270, 270, [180]),
                                                    (280, 270, [180]), (20, 60, [60, 40])])
def test_attempt_and_retry_share_one_deadline(monkeypatch, transport, elapsed, budget, expected):
    clock = [1000.0]
    calls = []
    monkeypatch.setenv("MODEL_TIMEOUT", "180")
    monkeypatch.setenv("MODEL_TOTAL_TIMEOUT", str(budget))
    monkeypatch.setattr("kge.adapters.model.errors.time.monotonic", lambda: clock[0])

    def send(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            clock[0] += elapsed
            raise TimeoutError("slow model")
        return {}

    if transport == "rest":
        def rest(*args):
            try:
                return send(args[3])
            except TimeoutError:
                raise ExternalCallError("slow model", transient=True) from None
        monkeypatch.setattr("kge.adapters.model.errors._post_json", rest)
        def invoke():
            return post_json("https://models.example.com", {}, {})
    else:
        model = LLM("vertex-claude", "stand-in")
        def create(**kwargs):
            send(kwargs["timeout"])
            return SimpleNamespace(model_dump=lambda: {"content": [{"type": "text", "text": "OK"}]})
        monkeypatch.setattr(type(model.chat), "anthropic_vertex", lambda self: SimpleNamespace(messages=SimpleNamespace(create=create)))
        def invoke():
            return model.complete("probe")
    if len(expected) == 1:
        with pytest.raises(ExternalCallError, match="deadline"):
            invoke()
    else:
        invoke()
    assert calls == expected
    if len(calls) == 2:
        assert elapsed + calls[1] <= budget < 300
