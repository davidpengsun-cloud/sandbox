"""The model path for Vertex AI (#48 item 3): LangChain chat models, Claude and Gemini on Vertex AI, with stand-ins only.

    .venv/bin/python -m pytest tests/unit/test_model_path.py -q

make_llm() builds the configured model as a LangChain chat model, chosen by MODEL_PROVIDER as before: kge/adapters/model/providers.py's
Chat for a provider, kge/adapters/model/mock.py's ChatMock for the mock, which keeps its modes and is named mock. The agent's
plan node calls the model through it, and identity(), the run trace's token counts and the served model are recorded as
before. Two providers call Vertex AI as the runtime's service account (google-auth's application default credentials),
never with a key from config: vertex-claude, Claude Haiku 5.5 through the official Anthropic SDK (AnthropicVertex), and
vertex-gemini, a Flash-Lite model with a token fetched from those credentials. Claude Haiku 5.5 takes no sampling
parameters, thinks by default (only its text blocks are read), and may refuse: a refusal is no answer, never a guess.

Nothing here reaches a network or a Google or Anthropic account. The REST providers' post is replaced, as in
tests/integration/test_run_trace.py; AnthropicVertex sends to an HTTP transport that answers in Vertex AI's place; and the service
account is a stand-in for google.auth.default.

Opens the demo's graph; stop the API first.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx2
import pytest
from langchain_core.language_models.chat_models import BaseChatModel

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import PHRASE_PROMPT, PROMPTS, Agent
from kge.adapters.model import mock as mockllm
from kge.adapters.model import LLM, PREFERRED_TIER, PROVIDERS, Refused, make_llm, model_for
from tests.integration.test_agent import FIG, FIG_SQL

DEMO = Path(__file__).resolve().parents[2]
HEX = re.compile(r"^[0-9a-f]{64}$")
KEY = "sk-NEVER-SENT-123"
TOKEN = "ya29.stand-in-token"
PROJECT = "pilot-project"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
VERTEX = "https://aiplatform.googleapis.com/v1/projects"
WHO = "analyst_enterprise"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No call leaves the process: a REST post, an HTTP transport or a credentials lookup not stood in fails the test."""
    def no_network(*a, **k):
        raise AssertionError("a model call reached the network")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr("google.auth.default", no_network)
    for name in ("MODEL_SEED", "MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL", "MOCK_MODE", "VERTEX_REGION", "VERTEX_PROJECT"):
        monkeypatch.delenv(name, raising=False)


class Account:
    """The runtime's service account, as google.auth.default gives it: a token minted on refresh, expiring when told."""
    def __init__(self):
        self.token, self.expired, self.refreshes, self.scopes = None, False, 0, None

    @property
    def valid(self) -> bool:
        return bool(self.token) and not self.expired

    def refresh(self, request) -> None:
        self.refreshes += 1
        self.token, self.expired = f"{TOKEN}-{self.refreshes}", False


@pytest.fixture()
def account(monkeypatch) -> Account:
    acct = Account()

    def default(scopes=None, **k):
        acct.scopes = scopes
        return acct, PROJECT
    monkeypatch.setattr("google.auth.default", default)
    return acct


def claude_reply(text: str = "ok", stop_reason: str = "end_turn", thinking: bool = True) -> dict:
    """A Messages API reply as Claude Haiku 5.5 gives one: adaptive thinking first (its text omitted), then the answer."""
    content = ([{"type": "thinking", "thinking": "", "signature": "c2ln"}] if thinking else []) + \
              ([{"type": "text", "text": text}] if text else [])
    return {"id": "msg_standin", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 40, "output_tokens": 9, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3}}


class Seen(list):
    """The requests an endpoint was sent, in order, and the replies queued for it."""
    def __init__(self):
        super().__init__()
        self.queue: list = []


@pytest.fixture()
def vertex(monkeypatch) -> Seen:
    """Vertex AI's Claude endpoint, stood in at the HTTP transport AnthropicVertex sends through: each request is kept,
    and answered by the next reply queued (a dict, or a function of the request's body), else a plain one."""
    seen = Seen()

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        reply = seen.queue.pop(0) if seen.queue else claude_reply()
        reply = reply(json.loads(request.content)) if callable(reply) else reply
        return httpx2.Response(200, json=reply, request=request)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", handle)
    return seen


def vertex_claude(monkeypatch, **env) -> LLM:
    """Claude on Vertex AI, with MODEL_API_KEY left set in config so that a test can hold that it is never sent."""
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-claude")
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return make_llm()


def sent_anywhere(request: httpx2.Request) -> str:
    return " ".join([str(request.url), json.dumps(dict(request.headers)), request.content.decode()])


def test_make_llm_builds_a_langchain_chat_model_for_every_provider(monkeypatch):
    assert {"vertex-claude", "vertex-gemini"} <= set(PROVIDERS)
    for provider in PROVIDERS:
        monkeypatch.setenv("MODEL_PROVIDER", provider)
        monkeypatch.setenv("MODEL_NAME", "gemini-2.5-flash-lite" if provider == "vertex-gemini" else "m")
        llm = make_llm()
        assert isinstance(llm.chat, BaseChatModel), provider
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    assert make_llm() is None


def test_every_agent_is_exactly_the_default_without_tier_settings(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "default-model")
    expected = make_llm().identity()
    for agent in PREFERRED_TIER:
        assert model_for(agent).identity() == expected


def test_tier_selection_is_atomic_and_honours_agent_override(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "default-model")
    monkeypatch.setenv("MODEL_HIGH_NAME", "orphaned-name")
    assert model_for("onboard").name == "openai:default-model", "a name alone does not configure a tier"
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_HIGH_NAME", "high-model")
    assert model_for("onboard").name == "anthropic:high-model"
    monkeypatch.setenv("ONBOARD_MODEL_TIER", "low")
    assert model_for("onboard").name == "openai:default-model", "an unconfigured selected tier falls back atomically"
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_LOW_NAME", "low-model")
    assert model_for("onboard").name == "ollama:low-model"


def test_invalid_agent_tier_fails_and_mock_can_be_a_tier(monkeypatch):
    monkeypatch.setenv("ANSWER_MODEL_TIER", "fast")
    with pytest.raises(ValueError, match="ANSWER_MODEL_TIER must be one of high, medium, low, default"):
        model_for("answer")
    monkeypatch.setenv("ANSWER_MODEL_TIER", "medium")
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "must-not-label-the-mock")
    llm = model_for("answer")
    assert llm.name == "mock:mock" and llm.tier == "medium"


def test_tier_prices_belong_to_the_selected_model(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "default-model")
    monkeypatch.setenv("MODEL_PRICE_IN", "1")
    monkeypatch.setenv("MODEL_PRICE_OUT", "2")
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "medium-model")
    monkeypatch.setenv("MODEL_MEDIUM_PRICE_IN", "3")
    monkeypatch.setenv("MODEL_MEDIUM_PRICE_OUT", "4")
    llm = model_for("answer")
    assert llm.name == "openai:medium-model"
    assert llm.prices == {"in": 3.0, "out": 4.0, "currency": "USD", "per_tokens": 1_000_000}


def test_the_mock_is_a_langchain_chat_model_with_its_modes_and_named_mock(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_MODE", "invented_number")
    llm = make_llm()
    assert isinstance(llm.chat, mockllm.ChatMock) and llm.chat.mode == "invented_number" and llm.chat._llm_type == "mock"
    prompt = PHRASE_PROMPT.format(facts=json.dumps({"behaviour": "figure", "figures": [{"unit": "Retail", "value": "13,287.67", "cite": 1}]}),
                                  question=FIG)
    said = llm.chat.invoke(prompt)
    assert said.content == mockllm.reply(prompt, "invented_number"), "the mock's reply, mode and all, as before"
    assert said.response_metadata["model_name"] == "mock"
    assert said.usage_metadata["input_tokens"] == len(prompt.split()) and said.usage_metadata["output_tokens"] == len(said.content.split())
    with pytest.raises(RuntimeError, match="only the reference agent's"):
        mockllm.ChatMock().invoke("Glossary terms: Revenue")


def test_the_identity_is_recorded_as_before():
    """Real providers record the image budget; the offline mock retains its historical identity."""
    def as_before(settings: dict) -> str:
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    mock = LLM("mock", "mock").identity()
    assert mock == {"name": "mock:mock", "provider": "mock", "model": "mock", "temperature": 0, "seed": 7, "mode": "careful",
                    "config_hash": as_before({"provider": "mock", "model": "mock", "base_url": "", "temperature": 0, "seed": 7,
                                              "mode": "careful"})}
    openai = LLM("openai", "gpt-test", "k").identity()
    assert openai == {"name": "openai:gpt-test", "provider": "openai", "model": "gpt-test", "temperature": 0, "seed": 7, "max_tokens": 8192,
                      "config_hash": as_before({"provider": "openai", "model": "gpt-test", "base_url": "", "temperature": 0,
                                                "seed": 7, "max_tokens": 8192})}


def test_the_agents_plan_node_calls_the_model_through_the_chat_model(eng, monkeypatch):
    asked: list[str] = []
    generate = mockllm.ChatMock._generate

    def spy(self, messages, *a, **k):
        asked.append(messages[-1].content)
        return generate(self, messages, *a, **k)
    monkeypatch.setattr(mockllm.ChatMock, "_generate", spy)
    a = runtrace.run("agent", lambda: Agent(eng, llm=LLM("mock", "mock")).ask(FIG, WHO, arm="treatment"), graph=eng.g)
    assert asked and asked[0].startswith(mockllm.SQL_TASK), "the plan node's prompt went through the chat model"
    assert a["behaviour"] == "figure" and a["agent"]["sql_source"] == "mock:mock"
    calls = a["run_trace"]["llm_calls"]
    assert len(calls) == len(asked) and calls[0]["stage"] == "plan" and calls[0]["prompt_version"] == PROMPTS["sql"]
    assert (calls[0]["served_model"], calls[0]["tokens_in"]) == ("mock", len(asked[0].split()))


def test_claude_on_vertex_sends_no_sampling_and_signs_in_as_the_service_account(monkeypatch, account, vertex):
    """One user turn and the cap: no temperature, top_p or top_k, no thinking budget and no assistant prefill."""
    llm = vertex_claude(monkeypatch)
    assert llm.model == "claude-haiku-5-5" and llm.api_key == "", "the model is Claude Haiku 5.5, and no key is held"
    with runtrace.start("ask") as t:
        runtrace.stage("render")
        assert llm.complete("hello", max_tokens=300) == "ok", "only the text blocks are read, not the thinking"
        rt = t.finish({})
    (req,) = vertex
    assert (req.method, str(req.url)) == ("POST", f"{VERTEX}/{PROJECT}/locations/global/publishers/anthropic/models/claude-haiku-5-5:rawPredict")
    assert req.headers["authorization"] == f"Bearer {TOKEN}-1" and account.scopes == [CLOUD_PLATFORM]
    assert json.loads(req.content) == {"anthropic_version": "2026-10-21", "max_tokens": 8192,
                                       "messages": [{"role": "user", "content": "hello"}]}
    assert KEY not in sent_anywhere(req) and "x-api-key" not in req.headers
    call = rt["llm_calls"][0]
    assert (call["model"], call["served_model"], call["ok"]) == ("vertex-claude:claude-haiku-5-5", "claude-haiku-5-5", True)
    assert (call["tokens_in"], call["tokens_out"]) == (40 + 2 + 3, 9), "cache tokens count as input, as the anthropic provider's do"
    assert KEY not in json.dumps(rt)


def test_claude_on_vertex_takes_its_region_and_project_from_config_and_reuses_the_token(monkeypatch, account, vertex):
    llm = vertex_claude(monkeypatch, VERTEX_REGION="europe-west1", VERTEX_PROJECT="pilot-other")
    llm.complete("one")
    llm.complete("two")
    assert [str(r.url) for r in vertex] == [
        "https://europe-west1-aiplatform.googleapis.com/v1/projects/pilot-other/locations/europe-west1/publishers/anthropic/"
        "models/claude-haiku-5-5:rawPredict"] * 2
    assert account.refreshes == 1, "the service account's token is reused while it is valid"
    account.expired = True
    llm.complete("three")
    assert account.refreshes == 2 and vertex[-1].headers["authorization"] == f"Bearer {TOKEN}-2"


def test_no_credentials_are_looked_up_until_a_call(monkeypatch):
    """google.auth.default is the offline fixture's, which fails any lookup."""
    llm = vertex_claude(monkeypatch)
    assert llm.identity()["provider"] == "vertex-claude"
    with pytest.raises(AssertionError, match="network"):
        llm.complete("hello")


def test_gemini_on_vertex_sends_a_token_from_the_service_account_never_the_key(monkeypatch, account):
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_NAME", "gemini-2.5-flash-lite")
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    llm = make_llm()
    sent: list[tuple] = []
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: sent.append((url, headers, body)) or {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}], "modelVersion": "gemini-2.5-flash-lite-001",
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3}})
    with runtrace.start("ask") as t:
        runtrace.stage("render")
        assert llm.complete("hello", max_tokens=200) == "ok" and llm.complete("again") == "ok"
        rt = t.finish({})
    url, headers, body = sent[0]
    assert url == f"{VERTEX}/{PROJECT}/locations/global/publishers/google/models/gemini-2.5-flash-lite:generateContent"
    assert headers["authorization"] == f"Bearer {TOKEN}-1" and account.scopes == [CLOUD_PLATFORM] and account.refreshes == 1
    assert body["generationConfig"] == {"temperature": 0, "seed": 7, "maxOutputTokens": 8192}
    assert KEY not in json.dumps(sent)
    call = rt["llm_calls"][0]
    assert (call["model"], call["served_model"], call["tokens_in"], call["tokens_out"]) == (
        "vertex-gemini:gemini-2.5-flash-lite", "gemini-2.5-flash-lite-001", 12, 3)
    monkeypatch.setenv("VERTEX_REGION", "us-central1")
    regional = make_llm()
    monkeypatch.setattr(regional, "_post", lambda url, headers, body: sent.append((url, headers, body)) or {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    regional.complete("hello")
    assert sent[-1][0].startswith(f"https://us-central1-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/")


def test_gemini_on_vertex_needs_its_model_named_in_config(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-gemini")
    with pytest.raises(ValueError, match="MODEL_NAME"):
        make_llm()


def test_gemini_with_a_key_works_as_before(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_NAME", "gemini-test")
    monkeypatch.setenv("MODEL_API_KEY", "AIza-test")
    llm = make_llm()
    sent: list[tuple] = []
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: sent.append((url, headers, body)) or {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    assert llm.complete("hello") == "ok"
    url, headers, body = sent[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent?key=AIza-test"
    assert headers == {"content-type": "application/json"} and body["generationConfig"]["temperature"] == 0


@pytest.mark.parametrize("model, temperature", [("claude-haiku-5-5", None), ("claude-opus-4-7", None), ("claude-sonnet-5", None),
                                                ("claude-sonnet-4-6", 0), ("claude-haiku-4-5", 0), ("m", 0)])
def test_the_anthropic_provider_sends_temperature_only_to_a_model_that_takes_it(model, temperature, monkeypatch):
    llm = LLM("anthropic", model, "k")
    sent: list[dict] = []
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: sent.append(body) or claude_reply("answer"))
    assert llm.complete("hello") == "answer"
    assert sent[0].get("temperature") == temperature and ("temperature" in sent[0]) == (temperature is not None)
    assert not {"top_p", "top_k", "thinking"} & set(sent[0]) and sent[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert llm.identity()["temperature"] == temperature


def test_a_refusal_raises_and_the_trace_says_refused(monkeypatch, account, vertex):
    llm = vertex_claude(monkeypatch)
    vertex.queue.append(claude_reply("", stop_reason="refusal"))
    with runtrace.start("ask") as t:
        runtrace.stage("plan")
        with pytest.raises(Refused):
            llm.complete("hello")
        rt = t.finish({})
    call = rt["llm_calls"][0]
    assert (call["ok"], call["error"], call["served_model"], call["tokens_out"]) == (False, "refused", "claude-haiku-5-5", 9)


def test_the_agent_abstains_on_a_refused_plan_and_keeps_the_template_on_a_refused_wording(eng, monkeypatch, account, vertex):
    llm = vertex_claude(monkeypatch)
    vertex.queue.append(claude_reply('{"behaviour": "figure", "sql": {"retail": "SELECT 1"}', stop_reason="refusal"))
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, WHO, arm="treatment"), graph=eng.g)
    assert a["behaviour"] == "abstain" and a["reason"] == "No answer: the model refused to answer."
    assert not a.get("figures") and not a["attempts"], "nothing the refused reply held was run: no guess"
    assert [(c["stage"], c["ok"], c["error"]) for c in a["run_trace"]["llm_calls"]] == [("plan", False, "refused")]
    plan = {"behaviour": "figure", "sql": {"retail": FIG_SQL}, "citations": [1], "answer_text": ""}
    vertex.queue.extend([claude_reply(json.dumps(plan)), claude_reply("", stop_reason="refusal")])
    b = Agent(eng, llm=llm).ask(FIG, WHO, arm="treatment")
    assert b["behaviour"] == "figure" and b["figure"] == 13287.67
    assert b["phrased_by"] == "template (the model refused to answer)"


def test_the_vertex_identities_name_the_region_and_never_a_key(monkeypatch):
    claude = vertex_claude(monkeypatch).identity()
    assert {k: claude[k] for k in ("name", "provider", "model", "temperature", "seed", "region")} == {
        "name": "vertex-claude:claude-haiku-5-5", "provider": "vertex-claude", "model": "claude-haiku-5-5", "temperature": None,
        "seed": None, "region": "global"}
    assert HEX.match(claude["config_hash"]) and KEY not in json.dumps(claude)
    monkeypatch.setenv("MODEL_API_KEY", "another")
    assert make_llm().identity() == claude, "no key is part of the config"
    monkeypatch.setenv("VERTEX_REGION", "us-east5")
    assert make_llm().identity()["config_hash"] != claude["config_hash"], "the region shapes the replies, so it is in the hash"
    monkeypatch.delenv("VERTEX_REGION")
    gemini = LLM("vertex-gemini", "gemini-2.5-flash-lite").identity()
    assert (gemini["temperature"], gemini["seed"], gemini["region"]) == (0, 7, "global")


def test_the_tagger_uses_the_same_vertex_model_and_never_the_key(monkeypatch):
    from kge.pipeline.tagger import ModelTagger, make_tagger
    monkeypatch.setenv("MODEL_PROVIDER", "vertex-claude")
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.setenv("VERTEX_REGION", "us-east5")
    t = make_tagger(["Revenue"])
    assert isinstance(t, ModelTagger) and t.llm.identity() == make_llm().identity() and t.llm.api_key == ""


def test_the_report_says_a_model_that_takes_no_temperature_runs_on_its_own_defaults():
    from kge.eval.report import statement
    said = statement({"arms": [{"mode": "model"}], "llm": {"name": "vertex-claude:claude-haiku-5-5", "temperature": None, "seed": None}})
    assert "temperature None" not in said and "vertex-claude:claude-haiku-5-5" in said


def test_the_readme_and_env_example_document_the_vertex_providers():
    env = (DEMO / ".env.example").read_text()
    provider = next(line for line in env.splitlines() if line.startswith("MODEL_PROVIDER="))
    assert "vertex-claude" in provider and "vertex-gemini" in provider
    from kge.pipeline.envfile import parse
    values = parse(env)
    assert values["VERTEX_REGION"] == values["VERTEX_PROJECT"] == "", "copied as it is: global, and the credentials' project"
    readme = (DEMO / "README.md").read_text()
    for said in ("`vertex-claude`", "`vertex-gemini`", "`VERTEX_REGION`", "`VERTEX_PROJECT`", "application default credentials",
                 "claude-haiku-5-5", "refus"):
        assert said in readme, said


def _reply(url: str) -> dict:
    if "anthropic" in url:
        return claude_reply("ok")
    if ":generateContent" in url:
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    return {"choices": [{"message": {"content": "ok"}}]}


def _sent(monkeypatch, llm) -> list[tuple]:
    """Every request the model sends, captured at its REST seam, answered by a plain reply."""
    sent: list[tuple] = []
    monkeypatch.setattr(llm, "_post", lambda url, headers, body: sent.append((url, headers, body)) or _reply(url))
    llm.complete("hello")
    return sent


def test_a_tier_on_another_provider_is_never_sent_the_default_key_or_endpoint(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_NAME", "claude-default")
    monkeypatch.setenv("MODEL_API_KEY", "sk-ant-default")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_HIGH_NAME", "gpt-high")
    url, headers, _ = _sent(monkeypatch, model_for("onboard"))[0]
    assert url == "https://api.openai.com/v1/chat/completions" and "sk-ant-default" not in json.dumps(headers)
    monkeypatch.setenv("MODEL_HIGH_API_KEY", "sk-openai-high")
    url, headers, _ = _sent(monkeypatch, model_for("onboard"))[0]
    assert headers["authorization"] == "Bearer sk-openai-high"
    url, headers, _ = _sent(monkeypatch, model_for("answer"))[0]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "sk-ant-default"


def test_a_corporate_endpoint_for_the_default_provider_does_not_carry_another_tier(monkeypatch):
    """A tier on the default's own provider inherits its key and endpoint unless it names its own."""
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-default")
    monkeypatch.setenv("MODEL_API_KEY", "sk-openai-default")
    monkeypatch.setenv("MODEL_BASE_URL", "https://corp-proxy.example/v1")
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_LOW_NAME", "gemini-low")
    monkeypatch.setenv("MODEL_LOW_API_KEY", "AIza-low")
    url, headers, _ = _sent(monkeypatch, model_for("context"))[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-low:generateContent?key=AIza-low"
    assert "corp-proxy" not in url and "sk-openai-default" not in json.dumps(headers) + url
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "gpt-medium")
    url, headers, _ = _sent(monkeypatch, model_for("answer"))[0]
    assert url == "https://corp-proxy.example/v1/chat/completions" and headers["authorization"] == "Bearer sk-openai-default"
    monkeypatch.setenv("MODEL_MEDIUM_BASE_URL", "https://medium-proxy.example/v1")
    monkeypatch.setenv("MODEL_MEDIUM_API_KEY", "sk-openai-medium")
    url, headers, _ = _sent(monkeypatch, model_for("answer"))[0]
    assert url == "https://medium-proxy.example/v1/chat/completions" and headers["authorization"] == "Bearer sk-openai-medium"


def test_a_vertex_tier_signs_in_and_reads_no_key(monkeypatch, account):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_HIGH_NAME", "gemini-2.5-flash-lite")
    monkeypatch.setenv("MODEL_HIGH_API_KEY", KEY)
    sent = _sent(monkeypatch, model_for("onboard"))
    assert sent[0][1]["authorization"] == f"Bearer {TOKEN}-1" and KEY not in json.dumps(sent)


def test_tier_edge_cases_fail_or_fall_back_plainly(monkeypatch, caplog):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-default")
    monkeypatch.setenv("TAGGER_MODEL_TIER", "fastest")
    assert model_for("answer").name == "openai:gpt-default", "another agent's mistyped tier is not this agent's"
    with pytest.raises(ValueError, match="TAGGER_MODEL_TIER must be one of"):
        model_for("tagger")
    monkeypatch.delenv("TAGGER_MODEL_TIER")
    monkeypatch.setenv("MODEL_PRICE_IN", "abc")
    with caplog.at_level("WARNING"):
        llm = model_for("answer")
    assert llm.prices["in"] is None and "MODEL_PRICE_IN" in caplog.text, "a price that does not parse never stops an answer"
    monkeypatch.setenv("ANSWER_MODEL_TIER", "high")
    llm = model_for("answer")
    assert llm.tier == "default" and llm.tier_reason.startswith("ANSWER_MODEL_TIER=high, but MODEL_HIGH_PROVIDER is not set")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="MODEL_HIGH_PROVIDER=anthropic needs MODEL_HIGH_NAME"):
        model_for("answer")
    monkeypatch.setenv("DEFAULT_MODEL_TIER", "high")
    assert make_llm().name == "openai:gpt-default", "make_llm is the default model and reads no tier setting"


def test_an_explicit_tagger_provider_wins_over_the_low_tier(monkeypatch):
    from kge.pipeline.tagger import ModelTagger, make_tagger
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-default")
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_LOW_NAME", "claude-low")
    monkeypatch.delenv("TAGGER_PROVIDER", raising=False)
    assert make_tagger([]).llm.name == "anthropic:claude-low", "no TAGGER_PROVIDER: the tagger's tier"
    monkeypatch.setenv("TAGGER_PROVIDER", "model")
    assert make_tagger([]).llm.name == "anthropic:claude-low"
    monkeypatch.setenv("TAGGER_PROVIDER", "openai")
    tagger = make_tagger([])
    assert isinstance(tagger, ModelTagger) and tagger.llm.name == "openai:gpt-default"


def test_the_tagger_follows_its_tier_when_the_default_is_none_like_the_other_agents(monkeypatch):
    from kge.pipeline.tagger import ModelTagger, make_tagger
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.delenv("TAGGER_PROVIDER", raising=False)
    assert not isinstance(make_tagger([]), ModelTagger), "no tier set: rules, as before tiers"
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_LOW_NAME", "claude-low")
    assert make_tagger([]).llm.name == "anthropic:claude-low", "a low tier set while the default is none: the model path"
    assert model_for("context").name == "anthropic:claude-low", "as for the context agent"
    monkeypatch.setenv("TAGGER_MODEL_TIER", "default")
    assert not isinstance(make_tagger([]), ModelTagger), "TAGGER_MODEL_TIER=default keeps the default, none: rules"
    monkeypatch.delenv("TAGGER_MODEL_TIER")
    for rules in ("rules", "none"):
        monkeypatch.setenv("TAGGER_PROVIDER", rules)
        assert not isinstance(make_tagger([]), ModelTagger), f"TAGGER_PROVIDER={rules} is rules whatever the tiers"


def test_a_mistyped_tier_provider_is_refused_and_none_turns_the_tier_off(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-default")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "antropic")
    monkeypatch.setenv("MODEL_HIGH_NAME", "claude-high")
    with pytest.raises(ValueError, match="MODEL_HIGH_PROVIDER must be one of .* or none, not 'antropic'"):
        model_for("onboard")
    assert model_for("answer").name == "openai:gpt-default", "an agent on another tier is not stopped"
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "none")
    assert model_for("onboard") is None, "none is the tier's off switch: the agents on it take no model"


def test_a_tagger_provider_other_than_the_default_names_its_own_model_and_key(monkeypatch):
    from kge.pipeline.tagger import ModelTagger, make_tagger
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-default")
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.setenv("TAGGER_PROVIDER", "anthropic")
    for name in ("TAGGER_NAME", "TAGGER_API_KEY", "TAGGER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match=r"TAGGER_PROVIDER=anthropic is not MODEL_PROVIDER \(openai\): set TAGGER_NAME"):
        make_tagger([])
    monkeypatch.setenv("TAGGER_NAME", "claude-tagger")
    monkeypatch.setenv("TAGGER_API_KEY", "tagger-key")
    tagger = make_tagger([])
    assert isinstance(tagger, ModelTagger) and tagger.llm.name == "anthropic:claude-tagger"
    assert tagger.llm.api_key == "tagger-key", "its own key, never MODEL_API_KEY"
    monkeypatch.delenv("TAGGER_API_KEY")
    assert make_tagger([]).llm.api_key == "", "MODEL_API_KEY is openai's: never sent to anthropic"
    monkeypatch.setenv("TAGGER_PROVIDER", "openai")
    assert make_tagger([]).llm.name == "openai:gpt-default", "the default provider keeps MODEL_NAME, as before"


def test_a_mistyped_model_setting_is_one_line_and_exit_2_from_every_command_that_resolves_a_model(tmp_path):
    """make agent, make pipeline, make ab and make improve on a scratch build, with every tier's provider mistyped: each
    stops in one line on stderr, exit 2, never a traceback (make draft's: tests/unit/test_onboard_unit.py)."""
    from tests.scratch import scratch_build, script
    build = scratch_build(tmp_path / "build")
    bad = {"MODEL_PROVIDER": "none", **{f"MODEL_{tier}_PROVIDER": "antropic" for tier in ("HIGH", "MEDIUM", "LOW")}}
    for command in (("kge/services/ask/engine/agent.py", "--arm", "treatment", "NNA for June 2025"), ("kge/pipeline/run.py",),
                    ("kge/eval/harness.py", "--arms", "treatment", "--sets", "golden", "--items", "G001", "--repeats", "1",
                     "--out", tmp_path / "runs"),
                    ("kge/improve/flow.py",)):
        done = script(*command, build=build, env=bad)
        assert done.returncode == 2 and done.stderr.count("\n") == 1 and "Traceback" not in done.stderr, (command, done.stderr)
        assert re.search(r"MODEL_(HIGH|MEDIUM|LOW)_PROVIDER must be one of .* or none, not 'antropic'", done.stderr), done.stderr
