"""The one language model the demo uses, wherever it uses one.

MODEL_PROVIDER picks it: none (the default), gemini, anthropic, openai,
ollama, vertex-claude, vertex-gemini or mock. The tagger and the assistant share
this client, so a run uses one model with one pinned name, and the golden set is
scored against that model.

Each model is a LangChain chat model (langchain-core's BaseChatModel), which
make_llm() builds: Chat, below, for a provider, and ChatMock
(kge/adapters/model/mock.py) for the mock. LLM is what the demo calls: complete() and
json() send one prompt through the chat model, as one human message, so no
assistant prefill is ever sent, and record the call in the run's trace.

mock is not a model. It is the offline stand-in (kge/adapters/model/mock.py) that
proves the reference agent's model path end to end with no key: deterministic,
no network, served model "mock", tokens counted as the words of the prompt and
the reply. MOCK_MODE picks an adversarial mode the agent's seam must catch. It
answers only the agent's prompts, so the tagger and /ask stay on rules under it,
and every answer and trace it produces says mock.

gemini calls the Gemini API with an API key. Pointed at Vertex AI with
MODEL_BASE_URL (the publisher path) and an access token as MODEL_API_KEY, it
still works as it did; vertex-gemini is the way to call Gemini on Vertex AI now.

vertex-claude and vertex-gemini call Vertex AI as the runtime's service account:
google-auth's application default credentials, with the cloud-platform scope,
looked up at the first call (on Cloud Run, the service's own account; on a
laptop, gcloud auth application-default login), never a key from config:
MODEL_API_KEY is not read for them. VERTEX_REGION is the location (default
global), VERTEX_PROJECT the project (default the credentials' own).
vertex-claude is Claude on Vertex AI through the official Anthropic SDK
(AnthropicVertex), claude-haiku-5-5 unless MODEL_NAME names another.
vertex-gemini is Gemini on Vertex AI, the Flash-Lite model MODEL_NAME names (it
has no default), with a bearer token from those credentials.

With none, nothing leaves the process and every step that could use a model
falls back to rules. Whatever the model returns is a proposal: access,
certification and the SQL that runs are decided in code.

Every call is sent at temperature 0, with a seed (MODEL_SEED, default 7) where
the provider takes one: gemini, vertex-gemini, openai and ollama do, anthropic
and vertex-claude do not; the mock takes it and is deterministic whatever it is.
Claude's newer models (Opus 4.7 and 4.8, and the 5 generation on, Claude Haiku
5.5 among them) take no sampling parameter at all, so none is sent to them. Of a
Claude reply only the text blocks are read: Claude Haiku 5.5 thinks by default,
and its thinking blocks come first. A stop_reason of refusal is no answer:
complete() raises Refused, and the trace records the call as refused. Each call
is recorded in the run's trace (kge/pipeline/trace.py) with the tokens the provider
reports and the model that served it; identity() is what a run records about
the model, with a hash of its settings and never the key.

Each caller asks for the reply budget its prompt needs (max_tokens: the tagger
400, an answer's SQL 900, a draft's mapping 1800, and so on). A thinking model
spends part of that budget thinking before it answers, so a deployment can set
the budget instead: MODEL_<TIER>_MAX_TOKENS (HIGH, MEDIUM, LOW) for a tier's
model, else MODEL_MAX_TOKENS for every model, else the image default of 8192 tokens.
A configured budget replaces the caller's, larger or smaller. A tier with no
MODEL_<TIER>_PROVIDER falls back to the default model whole, so its
MODEL_<TIER>_MAX_TOKENS is not read: MODEL_MAX_TOKENS is, as for the default.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr, SecretStr

from kge.adapters.model.errors import CallDeadline, ExternalCallError, model_timeout, model_total_timeout, post_json, transient, vertex_url
from kge.adapters.model.budget import request_model_call, request_remaining, request_timeout
from kge.pipeline import trace
from kge.pipeline.settings import settings

MOCK = "mock"
VERTEX_CLAUDE, VERTEX_GEMINI = "vertex-claude", "vertex-gemini"
VERTEX = (VERTEX_CLAUDE, VERTEX_GEMINI)
PROVIDERS = ("gemini", "anthropic", "openai", "ollama", *VERTEX, MOCK)
TIERS = ("high", "medium", "low", "default")
PREFERRED_TIER = {"onboard": "high", "enhance": "high", "answer": "medium", "context": "low", "tagger": "low"}
SEEDED = ("gemini", VERTEX_GEMINI, "openai", "ollama", MOCK)
CLAUDE = ("anthropic", VERTEX_CLAUDE)
DEFAULT_SEED = 7
DEFAULT_MAX_TOKENS = 8192
log = logging.getLogger(__name__)
DEFAULT_MODEL = {VERTEX_CLAUDE: "claude-haiku-5-5"}
DEFAULT_REGION = "global"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
NO_SAMPLING = re.compile(r"^claude-(?:opus-4-[78]|[a-z]+-(?:[5-9]|\d\d))(?!\d)")


class ModelSettingError(ValueError):
    """A model setting that names no model this process can build (an unknown MODEL_<TIER>_PROVIDER, a tier with no
    name, a mistyped *_MODEL_TIER, MOCK_MODE or MODEL_SEED): the command that resolves the model stops on it, and a
    command line prints it in one line, never a traceback."""


class NotJSON(ValueError):
    """The model's reply held no JSON object, or one that does not parse. Its text is about the reply,
    never the transport's: a transport error can quote a request header, the key among them."""


class Refused(RuntimeError):
    """The model declined to answer (a stop_reason of refusal). No answer, and never a guess: whatever text came with
    it is not read. Its text is fixed."""

    def __init__(self):
        super().__init__("the model refused to answer")


def model_seed() -> int:
    raw = (settings().MODEL_SEED or "").strip()
    if not raw:
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError:
        raise ModelSettingError(f"MODEL_SEED must be a whole number, not {raw!r}") from None


MAX_TOKENS_SETTING = {"high": "MODEL_HIGH_MAX_TOKENS", "medium": "MODEL_MEDIUM_MAX_TOKENS", "low": "MODEL_LOW_MAX_TOKENS"}


def _whole(name: str, raw: str | None) -> int | None:
    """A reply budget setting's number, None when unset; a value that is not a whole number above 0 is refused."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 1:
        raise ModelSettingError(f"{name} must be a whole number of tokens above 0, not {raw!r}")
    return n


def configured_max_tokens_for(tier: str) -> int | None:
    """An explicit tier budget, else an explicit global budget; None when neither is set."""
    s = settings()
    name = MAX_TOKENS_SETTING.get(tier)
    if name:
        n = _whole(name, getattr(s, name))
        if n is not None:
            return n
    return _whole("MODEL_MAX_TOKENS", s.MODEL_MAX_TOKENS)


def max_tokens_for(tier: str) -> int:
    """The tier's configured reply budget, else the image default of 8192."""
    return configured_max_tokens_for(tier) or DEFAULT_MAX_TOKENS


def check_settings() -> None:
    """The process-wide model settings, checked when a process that serves starts: MODEL_SEED and every reply budget
    (MODEL_MAX_TOKENS and MODEL_<TIER>_MAX_TOKENS, whether or not a model or the tier is configured). ModelSettingError
    names the first one that does not parse. An agent's own setting (*_MODEL_TIER, a tier's provider) is not read here:
    a mistyped one stops that agent alone, at its start."""
    model_seed()
    try:
        model_timeout()
        model_total_timeout()
        request_timeout()
    except ValueError as exc:
        raise ModelSettingError(str(exc)) from None
    current = settings()
    for name in ("MODEL_MAX_TOKENS", *MAX_TOKENS_SETTING.values()):
        _whole(name, getattr(current, name))


def one_prompt(messages: list[BaseMessage]) -> str:
    """The one human message a call sends, its text: the demo's prompts are one user turn each, never a prefill."""
    if len(messages) != 1 or not isinstance(messages[0], HumanMessage) or not isinstance(messages[0].content, str):
        raise ValueError("a call sends one human message, of text")
    return messages[0].content


def result(text: str, served: str | None, tokens_in: int | None, tokens_out: int | None, stop_reason: str | None = None) -> ChatResult:
    """One reply as LangChain carries it: its text, the model that served it, why it stopped, and the tokens the provider
    reported, in token_usage (None for a count it did not report) and in usage_metadata too where it reported both."""
    usage = ({"input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out}
             if tokens_in is not None and tokens_out is not None else None)
    meta = {"model_name": served, "stop_reason": stop_reason, "token_usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}}
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text, response_metadata=meta, usage_metadata=usage))])


def claude(data: dict) -> tuple[str, str | None, int | None, int | None, str | None]:
    """A Messages API reply, the anthropic provider's or Vertex AI's: its text blocks only (thinking blocks come first on a
    model that thinks), the model that served it, its tokens (cache writes and reads count as input) and its stop reason."""
    use = data.get("usage") or {}
    tokens_in = use.get("input_tokens")
    if tokens_in is not None:
        tokens_in += (use.get("cache_creation_input_tokens") or 0) + (use.get("cache_read_input_tokens") or 0)
    text = "".join(b.get("text") or "" for b in data.get("content") or [] if b.get("type", "text") == "text")
    return text, data.get("model"), tokens_in, use.get("output_tokens"), data.get("stop_reason")


def post(url: str, headers: dict, body: dict) -> dict:
    """A REST provider's call. ExternalCallError, in one line that names the provider and never the key, if it fails."""
    return post_json(url, headers, body)


class Chat(BaseChatModel):
    """A provider's model as a LangChain chat model: one human message in, one AIMessage out (result, above). A REST
    provider's request goes through transport (the LLM's own post); vertex-claude's goes through the Anthropic SDK. The
    key is a SecretStr, and never among the model's params. The Vertex AI providers sign in as the service account,
    looked up at the first call. A temperature of None means the model takes no sampling parameter, and none is sent.
    budget is the LLM's configured reply budget (MODEL_MAX_TOKENS, MODEL_<TIER>_MAX_TOKENS): with one, the openai and
    ollama requests send max_tokens too, which they leave out otherwise, as they did before the budget existed."""
    provider: str
    model: str
    api_key: SecretStr = SecretStr("")
    base_url: str | None = None
    temperature: float | None = 0
    seed: int | None = DEFAULT_SEED
    region: str = DEFAULT_REGION
    project: str | None = None
    budget: int | None = None
    transport: Callable[[str, dict, dict], dict] = post

    _account: tuple | None = PrivateAttr(default=None)
    _vertex: Any = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return self.provider

    @property
    def _identifying_params(self) -> dict:
        return {"provider": self.provider, "model": self.model}

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None, **kwargs) -> ChatResult:
        return result(*self._reply(one_prompt(messages), kwargs.get("max_tokens", 800)))

    def _reply(self, prompt: str, max_tokens: int) -> tuple[str, str | None, int | None, int | None, str | None]:
        """(text, model that served, tokens in, tokens out, stop reason); a count the provider does not report is None.
        Gemini's thinking tokens are billed as output, so they are counted in tokens out."""
        sampling = {"temperature": self.temperature} if self.temperature is not None else {}
        if self.provider == VERTEX_CLAUDE:
            return claude(self.vertex_reply(prompt, max_tokens, sampling))
        if self.provider in ("gemini", VERTEX_GEMINI):
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": self.temperature, "seed": self.seed, "maxOutputTokens": max_tokens}}
            if self.provider == VERTEX_GEMINI:
                token, project = self.token()
                url = f"{vertex_url(self.region)}/projects/{project}/locations/{self.region}/publishers/google/models/{self.model}:generateContent"
                headers = {"authorization": f"Bearer {token}"}
            elif self.base_url:
                url = f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"
                headers = {"authorization": f"Bearer {self.api_key.get_secret_value()}"}
            else:
                url = (f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
                       f"?key={urllib.parse.quote(self.api_key.get_secret_value())}")
                headers = {}
            data = self.transport(url, {**headers, "content-type": "application/json"}, body)
            use = data.get("usageMetadata") or {}
            out = use.get("candidatesTokenCount")
            if out is not None:
                out += use.get("thoughtsTokenCount") or 0
            candidate = next(iter(data.get("candidates") or []), {})
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(p.get("text") or "" for p in parts)
            if not text:
                reason = candidate.get("finishReason") or "unknown"
                raise ExternalCallError(f"Gemini returned no content (finishReason={reason})")
            return text, data.get("modelVersion"), use.get("promptTokenCount"), out, None
        if self.provider == "anthropic":
            return claude(self.transport("https://api.anthropic.com/v1/messages",
                                         {"x-api-key": self.api_key.get_secret_value(), "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"},
                                         {"model": self.model, "max_tokens": max_tokens, **sampling,
                                          "messages": [{"role": "user", "content": prompt}]}))
        base = self.base_url or ("http://localhost:11434/v1" if self.provider == "ollama" else "https://api.openai.com/v1")
        data = self.transport(f"{base}/chat/completions",
                              {"authorization": f"Bearer {self.api_key.get_secret_value() or 'ollama'}", "content-type": "application/json"},
                              {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": self.temperature,
                               "seed": self.seed, **({"max_tokens": max_tokens} if self.budget is not None else {})})
        use = data.get("usage") or {}
        return data["choices"][0]["message"]["content"], data.get("model"), use.get("prompt_tokens"), use.get("completion_tokens"), None

    def account(self) -> tuple[Any, str]:
        """The runtime's service account, google-auth's application default credentials with the cloud-platform scope,
        looked up once, at the first call; and the project: VERTEX_PROJECT, else the credentials' own."""
        with self._lock:
            if self._account is None:
                import google.auth
                credentials, project = google.auth.default(scopes=[CLOUD_PLATFORM])
                self._account = (credentials, self.project or project)
        credentials, project = self._account
        if not project:
            raise ValueError("no Google Cloud project: set VERTEX_PROJECT, or run where the credentials name one")
        return credentials, project

    def token(self) -> tuple[str, str]:
        """A bearer token from the service account, refreshed when it is no longer valid, and the project."""
        credentials, project = self.account()
        with self._lock:
            if not credentials.valid:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            return credentials.token, project

    def vertex_reply(self, prompt: str, max_tokens: int, sampling: dict) -> dict:
        """Claude on Vertex AI, with one transient retry and safe errors matching the REST providers."""
        from anthropic import APIConnectionError, APIStatusError
        deadline = CallDeadline()
        for attempt in range(2):
            try:
                return self.anthropic_vertex().messages.create(
                    model=self.model, max_tokens=max_tokens, timeout=deadline.remaining_timeout(), **sampling,
                    messages=[{"role": "user", "content": prompt}]).model_dump()
            except APIStatusError as exc:
                error = ExternalCallError(f"Vertex AI error (HTTP {exc.status_code})",
                                          transient=exc.status_code == 429 or exc.status_code >= 500)
            except (APIConnectionError, TimeoutError, ConnectionError):
                error = ExternalCallError("Vertex AI unreachable", transient=True)
            if attempt or not transient(error):
                raise error from None

    def anthropic_vertex(self):
        """Claude on Vertex AI: the Anthropic SDK's client, signed in as the service account, refreshing its token as it needs."""
        credentials, project = self.account()
        with self._lock:
            if self._vertex is None:
                from anthropic import AnthropicVertex
                self._vertex = AnthropicVertex(region=self.region, project_id=project, credentials=credentials, timeout=model_timeout(), max_retries=0)
            return self._vertex


class LLM:
    temperature = 0

    def __init__(self, provider: str, model: str, api_key: str = "", base_url: str | None = None, seed: int | None = None,
                 mode: str | None = None, region: str | None = None, project: str | None = None,
                 tier: str = "default", price_in: float | None = None, price_out: float | None = None,
                 max_tokens: int | None = None):
        """max_tokens: the reply budget every call sends, whatever its caller asks; None reads the tier's setting
        (max_tokens_for), defaulting to 8192."""
        model = model or DEFAULT_MODEL.get(provider, "")
        if provider == VERTEX_GEMINI and not model:
            raise ModelSettingError("MODEL_PROVIDER=vertex-gemini needs MODEL_NAME: the Flash-Lite model to call, as Vertex AI names it")
        if provider in VERTEX:
            api_key, base_url = "", None
        self.provider, self.model, self.api_key, self.base_url = provider, model, api_key, base_url
        self.tier = tier
        configured_budget = max_tokens if max_tokens is not None else configured_max_tokens_for(tier)
        self.max_tokens = configured_budget if configured_budget is not None else DEFAULT_MAX_TOKENS
        self._record_budget = provider != MOCK or configured_budget is not None
        self.tier_reason: str | None = None
        self.prices = {"in": price_in, "out": price_out, "currency": "USD", "per_tokens": 1_000_000}
        self.seed = model_seed() if seed is None else seed
        self.mode = None
        self.region = None
        if provider in CLAUDE and NO_SAMPLING.match(model):
            self.temperature = None
        if provider == MOCK:
            from kge.adapters.model.mock import IMPROVE_MODES, MODES, ChatMock
            self.mode = (mode or "careful").strip().lower()
            if self.mode not in MODES and self.mode not in IMPROVE_MODES:
                raise ModelSettingError(f"MOCK_MODE must be one of {', '.join((*MODES, *IMPROVE_MODES))}, not {mode!r}")
            self.chat: BaseChatModel = ChatMock(mode=self.mode)
            return
        if provider in VERTEX:
            self.region = (region or settings().VERTEX_REGION or "").strip() or DEFAULT_REGION
            project = (project or settings().VERTEX_PROJECT or "").strip() or None
        self.chat = Chat(provider=provider, model=model, api_key=SecretStr(api_key), base_url=base_url, temperature=self.temperature,
                         seed=self.seed, region=self.region or DEFAULT_REGION, project=project, budget=self.max_tokens,
                         transport=lambda url, headers, body: self._post(url, headers, body))

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    def identity(self) -> dict:
        """The model as a run records it. config_hash covers every setting that shapes a reply, never the key;
        real providers include the image budget. The offline mock ignores budgets, so its historical identity stays
        unchanged unless a budget override is explicit."""
        seed = self.seed if self.provider in SEEDED else None
        mode = {"mode": self.mode} if self.provider == MOCK else {}
        region = {"region": self.region} if self.provider in VERTEX else {}
        budget = {"max_tokens": self.max_tokens} if self._record_budget else {}
        settings = {"provider": self.provider, "model": self.model, "base_url": self.base_url or "", "temperature": self.temperature, "seed": seed,
                    **mode, **region, **budget}
        return {"name": self.name, "provider": self.provider, "model": self.model, "temperature": self.temperature, "seed": seed, **mode,
                **region, **budget, "config_hash": hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()}

    def complete(self, prompt: str, max_tokens: int = 800, *, fixed_budget: bool = False) -> str:
        """The reply's text, through the chat model, the call recorded in the run's trace. A refusal raises Refused, recorded
        as refused with the tokens it took; a failed call raises what it raised, recorded by its type. max_tokens is the
        caller's budget, replaced by self.max_tokens except for a fixed_budget connectivity probe."""
        if self.max_tokens is not None and not fixed_budget:
            max_tokens = self.max_tokens
        started = time.perf_counter()
        try:
            reply = request_model_call(lambda: self.chat.invoke([HumanMessage(content=prompt)], max_tokens=max_tokens))
        except Exception as exc:
            trace.llm_call(self.name, None, None, None, (time.perf_counter() - started) * 1000, ok=False, error=type(exc).__name__)
            request_remaining()
            raise
        meta = reply.response_metadata
        use = meta.get("token_usage") or {}
        refused = meta.get("stop_reason") == "refusal"
        trace.llm_call(self.name, meta.get("model_name"), use.get("input_tokens"), use.get("output_tokens"),
                          (time.perf_counter() - started) * 1000, **({"ok": False, "error": "refused"} if refused else {}))
        if refused:
            raise Refused()
        return reply.content

    def json(self, prompt: str, max_tokens: int = 800) -> dict:
        """The first JSON object in the reply. Raises NotJSON if there is none; a failed call raises what it raised."""
        raw = self.complete(prompt, max_tokens)
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise NotJSON("model reply held no JSON object")
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            raise NotJSON(str(exc)) from None

    @staticmethod
    def _post(url: str, headers: dict, body: dict) -> dict:
        return post(url, headers, body)


def _price(name: str) -> float | None:
    """A model's price setting. A price is for the A/B report's costing only, so one that does not parse is logged and
    left unknown: it never stops a model from answering."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("%s must be a price in USD per million tokens, not %r: the cost is left unknown", name, raw)
        return None


def _built(prefix: str, provider: str, tier: str, default_provider: str) -> LLM | None:
    """The model a prefix's settings name (MODEL for the default, MODEL_<TIER> for a tier). A tier has its own
    <prefix>_API_KEY and <prefix>_BASE_URL; it falls back to MODEL_API_KEY and MODEL_BASE_URL only when its provider is
    the default provider, so one provider's key or endpoint is never sent to another. The Vertex AI providers read
    neither (LLM): they sign in as the runtime's service account. A tier's provider named none is a deliberate off
    switch, so the agents on that tier take no model; any other name it does not know is a mistype, refused as a
    mistyped *_MODEL_TIER is, never a quiet drop to rules."""
    if provider not in PROVIDERS:
        if prefix != "MODEL" and provider not in ("none", ""):
            raise ModelSettingError(f"{prefix}_PROVIDER must be one of {', '.join(PROVIDERS)} or none, not {provider!r}")
        return None
    price_in, price_out = _price(f"{prefix}_PRICE_IN"), _price(f"{prefix}_PRICE_OUT")
    if provider == MOCK:
        return LLM(MOCK, MOCK, mode=(settings().MOCK_MODE or "").strip() or None, tier=tier,
                   price_in=price_in, price_out=price_out)
    name = os.getenv(f"{prefix}_NAME", "")
    if prefix != "MODEL" and not name.strip() and provider not in DEFAULT_MODEL:
        raise ModelSettingError(f"{prefix}_PROVIDER={provider} needs {prefix}_NAME: a tier is a provider and its model together")
    inherit = provider == default_provider
    key = os.getenv(f"{prefix}_API_KEY", "").strip() if prefix != "MODEL" else ""
    url = (os.getenv(f"{prefix}_BASE_URL") or None) if prefix != "MODEL" else None
    key = key or ((settings().MODEL_API_KEY or "").strip() if inherit else "")
    url = url or ((settings().MODEL_BASE_URL or None) if inherit else None)
    return LLM(provider, name, key, url, tier=tier, price_in=price_in, price_out=price_out)


def model_for(agent: str, step: str | None = None) -> LLM | None:
    """The model pinned for an agent (onboard, enhance, answer, context or tagger). Its <AGENT>_MODEL_TIER selects a
    tier (high, medium, low or default), else it prefers PREFERRED_TIER's; a tier with no MODEL_<TIER>_PROVIDER falls
    back to the default model whole, never half of each, and when the tier was selected the fallback is logged and kept
    as the model's tier_reason. Only the agent's own setting is read, so a mistyped one fails that agent alone, at its
    start. step: for the tagger, a provider TAGGER_PROVIDER names explicitly, which wins over any tier, as before tiers;
    one other than MODEL_PROVIDER takes its model from TAGGER_NAME (required) and its key from TAGGER_API_KEY."""
    from kge.pipeline.envfile import load_env
    load_env()
    default = (settings().MODEL_PROVIDER or "none").strip().lower()
    setting = f"{agent.upper()}_MODEL_TIER"
    selected = os.getenv(setting, "").strip().lower()
    if selected and selected not in TIERS:
        raise ModelSettingError(f"{setting} must be one of {', '.join(TIERS)}, not {selected!r}")
    if agent == "tagger" and step in PROVIDERS:
        if step == default:
            return _built("MODEL", step, "default", default)
        if not (settings().TAGGER_NAME or "").strip() and step not in DEFAULT_MODEL:
            raise ModelSettingError(f"TAGGER_PROVIDER={step} is not MODEL_PROVIDER ({default}): set TAGGER_NAME to its model, "
                             "and TAGGER_API_KEY to its key where it takes one; MODEL_NAME and MODEL_API_KEY are "
                             f"{default}'s")
        return _built("TAGGER", step, "default", default)
    wanted, reason = selected or PREFERRED_TIER.get(agent, "default"), None
    if wanted != "default" and not os.getenv(f"MODEL_{wanted.upper()}_PROVIDER", "").strip():
        if selected:
            reason = f"{setting}={selected}, but MODEL_{wanted.upper()}_PROVIDER is not set: the default model answers"
            log.warning(reason)
        wanted = "default"
    prefix = f"MODEL_{wanted.upper()}" if wanted != "default" else "MODEL"
    llm = _built(prefix, os.getenv(f"{prefix}_PROVIDER", "none").strip().lower(), wanted, default)
    if llm is not None:
        llm.tier_reason = reason
    return llm


def tagger_model() -> LLM | None:
    """The model the tagger tags with, or None for the rule tagger. A provider TAGGER_PROVIDER names wins over the tiers
    (rules, or none named outright, is the rule tagger); model, or no TAGGER_PROVIDER, takes the tagger's tier, and with
    none set the tagger stays on rules. The mock answers only the reference agent's prompts, so it never tags. On the
    pilot's cloud path (gcp_native), TAGGER_PROVIDER=model with no model for the tagger, or only the mock, is refused
    rather than tagged by rules."""
    from kge.pipeline.envfile import load_env
    from kge.pipeline.settings import gcp_native
    load_env()
    current = settings()
    explicit = current.TAGGER_PROVIDER
    provider = (explicit if explicit is not None else (current.MODEL_PROVIDER or "none")).strip().lower()
    if provider in ("rule", "rules") or explicit is not None and provider in ("none", ""):
        return None
    if not (provider in ("none", "", "model") or provider in PROVIDERS and provider != MOCK):
        return None
    llm = model_for("tagger", provider if explicit is not None and provider != "model" else None)
    if llm is not None and llm.provider != MOCK:
        return llm
    if provider == "model" and gcp_native():
        raise ModelSettingError("TAGGER_PROVIDER=model names no model for the tagger (MODEL_PROVIDER, or the tagger's "
                                "tier; the mock never tags), and the pilot on BigQuery does not fall back to rules")
    return None


def make_llm() -> LLM | None:
    """The configured model, or None when MODEL_PROVIDER is none or unset. MODEL_PROVIDER=mock gives
    the mock, in MOCK_MODE (careful by default); an unknown mode is an error, never a quiet default. The
    mock is always named mock: a MODEL_NAME left set for a real model never labels a mock run. The Vertex
    AI providers never read MODEL_API_KEY: they sign in as the runtime's service account. No tier and no
    *_MODEL_TIER setting is read: it is the default model, as it was before tiers."""
    from kge.pipeline.envfile import load_env
    load_env()
    default = (settings().MODEL_PROVIDER or "none").strip().lower()
    return _built("MODEL", default, "default", default)
