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
vertex-gemini is Gemini on Vertex AI, the model MODEL_NAME names (it has no
default: a Flash or Flash-Lite model, or a Pro thinking model), with a bearer
token from those credentials.

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

Ask's three calls (the planner, plan; the glossary classifier, classify; the
wording, wording) are structured: structured() sends a ModelRequest (kge/ports.py)
as a system instruction, the data and the question as the first user turn, any
correction turns after it, and the contract's JSON schema as the provider's
structured output (Gemini's responseJsonSchema, Claude's output_config format,
OpenAI's and Ollama's response_format), and parses the reply as one JSON object.
The schema sent has no cycle: a predicate's nesting is unrolled to three levels,
one for Gemini, as Vertex AI inlines every $ref. A request in JSON mode
(ModelRequest.json_mode) is sent no schema as structured output: the provider's
JSON mode (Gemini's responseMimeType alone, Claude no output_config format,
OpenAI and Ollama a json_object response_format), the schema shown in the system
instruction instead, and the reply validated in code all the same. A call whose
schema the provider refuses (an HTTP 400 naming the schema, or the input token
count its inlined schema reached, or, on a request that sent a schema, Vertex AI's
generic "Request contains an invalid argument") is made again once in JSON mode,
traced as a schema fallback; any other failure is raised as it was. On Gemini 3 and
later the planner (plan) is always sent in JSON mode (GEMINI_JSON_MODE_CALLS):
Vertex AI's grammar compiler refuses its schema, unrolled predicates and all.
Each call has its own budget and thinking cap (call_budget): ASK_<CALL>_MAX_TOKENS,
else the tier's or the global configured budget, else 16384 tokens for plan and
4096 for classify and wording; thinking is ASK_<CALL>_THINKING_*, else
MODEL_<TIER>_THINKING_*, else MODEL_THINKING_*, else 1024 tokens for plan and 512
for the others, except the planner on Gemini 3 and later, which is sent the
level low when no thinking setting applies to it (#589). It is sent as a token
budget where the model takes one, never
more than half the reply budget, and as a level (low, medium, high) where the
model takes a level and one is set; never both. A setting the model cannot honour
stops the process at its start (check_settings). No temperature is sent to Gemini
3 on these calls; every other call is unchanged. A reply that stops at its budget
raises Truncated, whose more_room says whether thinking less would leave the
contract more room. The mock is sent the request's legacy single-text form, so its
recordings replay by key as before.
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
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr, SecretStr

from kge.adapters.model.errors import (CallDeadline, ExternalCallError, UnavailablePlan, carries_schema, model_timeout, model_total_timeout,
                                       post_json, schema_refused, transient, vertex_url)
from kge.adapters.model.budget import request_model_call, request_remaining, request_timeout
from kge.pipeline.telemetry import ANTHROPIC_TELEMETRY_OFF
from kge.pipeline import trace
from kge.pipeline.settings import settings
from kge.ports import ChatRequest, ModelReply, ModelRequest

MOCK = "mock"
VERTEX_CLAUDE, VERTEX_GEMINI = "vertex-claude", "vertex-gemini"
VERTEX = (VERTEX_CLAUDE, VERTEX_GEMINI)
PROVIDERS = ("gemini", "anthropic", "openai", "ollama", *VERTEX, MOCK)
TIERS = ("high", "medium", "low", "default")
PREFERRED_TIER = {"onboard": "high", "enhance": "high", "answer": "medium", "context": "high", "assist": "high", "tagger": "low", "reader": "medium"}
SEEDED = ("gemini", VERTEX_GEMINI, "openai", "ollama", MOCK)
CLAUDE = ("anthropic", VERTEX_CLAUDE)
DEFAULT_SEED = 7
DEFAULT_MAX_TOKENS = 8192
log = logging.getLogger(__name__)
DEFAULT_MODEL = {VERTEX_CLAUDE: "claude-haiku-5-5"}
DEFAULT_REGION = "global"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
NO_SAMPLING = re.compile(r"^claude-(?:opus-4-[78]|[a-z]+-(?:[5-9]|\d\d))(?!\d)")
CALLS = ("plan", "classify", "wording")
CALL_DEFAULTS = {"plan": (16384, 1024), "classify": (4096, 512), "wording": (4096, 512)}
THINKING_LEVELS = ("low", "medium", "high")
MIN_THINKING_TOKENS = 128
FLASH_LITE_MIN_THINKING_TOKENS = 512
FLASH_LITE = re.compile(r"^gemini-.*flash-lite")
GEMINI_BUDGET = re.compile(r"^gemini-(?:2\.5|3)(?:[.-]|$)")
GEMINI_3 = re.compile(r"^gemini-[3-9]")
LEVEL_LOW_BY_DEFAULT = frozenset({"plan"})
"""The structured calls sent the thinking level low on Gemini 3 and later (GEMINI_3) when no thinking setting applies to
them: on the pilot's Gemini 3.1 Pro the planner at level low gave as many correct figures as any setting measured,
fewer wrong ones than the 1,024-token default and the shortest tail (#571, #589)."""
GEMINI_JSON_MODE_CALLS = ("plan",)
"""The structured calls sent to Gemini 3 and later (GEMINI_3) in JSON mode, the schema in the system instruction and the
reply validated by the same contract: Vertex AI answers the planner's schema with HTTP 400 "Request contains an invalid
argument" on Gemini 3.1 Pro and Gemini 4 Argon, its predicate union being too large for the grammar compiler (#561)."""
TRUNCATED = ("max_tokens", "length")
UNBUDGETED = ("openai", "ollama")
"""The providers whose requests send a reply budget only when one is configured, as they did before budgets existed."""
FENCED = re.compile(r"^\s*```(?:json)?[ \t]*\n(.*?)\n?[ \t]*```\s*$", re.S)


class ModelSettingError(ValueError):
    """A model setting that names no model this process can build (an unknown MODEL_<TIER>_PROVIDER, a tier with no
    name, a mistyped *_MODEL_TIER, MOCK_MODE or MODEL_SEED): the command that resolves the model stops on it, and a
    command line prints it in one line, never a traceback."""


class NotJSON(ValueError):
    """The model's reply held no JSON object, or one that does not parse. Its text is about the reply,
    never the transport's: a transport error can quote a request header, the key among them. reply_text and replay
    are the reply itself and the provider's record of it, where a structured call had them, so a correction can show
    the model what it sent."""

    def __init__(self, message: str, *, reply_text: str = "", replay: dict | None = None, json_mode: bool = False):
        """json_mode: the reply was asked for in JSON mode (ModelReply.json_mode)."""
        super().__init__(message)
        self.reply_text, self.replay, self.json_mode = reply_text, replay, json_mode


class Truncated(RuntimeError):
    """The reply stopped at its token budget, so it is not the whole answer. Its text names the budget and the setting
    that raises it, never the reply."""

    def __init__(self, budget: int | None, setting: str, *, more_room: bool = False, thinking: int | None = None,
                 json_mode: bool = False):
        """more_room: whether thinking less would leave the contract more room; thinking: the thinking tokens it took,
        where the provider said. budget None: no budget was sent, so the reply stopped at the model's own limit.
        json_mode: the reply was asked for in JSON mode (ModelReply.json_mode)."""
        self.budget, self.setting, self.more_room, self.thinking = budget, setting, more_room, thinking
        self.json_mode = json_mode
        after = f" after {thinking} thinking tokens" if thinking is not None else ""
        if budget is None:
            super().__init__(f"the reply reached the model's own output limit{after}, as no budget is sent; set {setting}")
        else:
            super().__init__(f"the reply reached its {budget}-token budget{after}; raise {setting}")


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


BUDGET_TIERS = tuple(MAX_TOKENS_SETTING)


def json_mode_call(provider: str, model: str, call: str) -> bool:
    """Whether this call to this model is always sent in JSON mode: one of GEMINI_JSON_MODE_CALLS to a Gemini 3 or later
    model, on the Gemini API or Vertex AI. Every other provider, and an older Gemini, keeps its schema mode."""
    return provider in ("gemini", VERTEX_GEMINI) and bool(GEMINI_3.match(model)) and call in GEMINI_JSON_MODE_CALLS


def thinking_support(provider: str, model: str) -> tuple[bool, bool]:
    """(takes a thinking token budget, takes a thinking level) for a provider's model. Gemini 2.5 and 3 take a budget
    (thinkingBudget), Gemini 3 and every later Gemini a level (thinkingLevel), and Gemini 4 and later a level alone
    (Vertex AI is reported to refuse their thinkingBudget, pending the pilot's live check); Claude takes no budget, and the models that take no sampling
    a level (output_config.effort); OpenAI a level (reasoning_effort); Ollama neither. The mock takes both and ignores
    them."""
    if provider in ("gemini", VERTEX_GEMINI):
        return bool(GEMINI_BUDGET.match(model)), bool(GEMINI_3.match(model))
    if provider in CLAUDE:
        return False, bool(NO_SAMPLING.match(model))
    if provider == "openai":
        return False, True
    if provider == MOCK:
        return True, True
    return False, False


def min_thinking_tokens(provider: str, model: str) -> int:
    """The smallest thinking budget a model takes: 512 on a Gemini Flash-Lite model, else 128."""
    if provider in ("gemini", VERTEX_GEMINI) and FLASH_LITE.match(model):
        return FLASH_LITE_MIN_THINKING_TOKENS
    return MIN_THINKING_TOKENS


def _thinking_tokens(name: str, raw: str | None) -> int | None:
    """A thinking token setting's number, None when unset; one that is not a whole number of 128 or more is refused."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < MIN_THINKING_TOKENS:
        raise ModelSettingError(f"{name} must be a whole number of thinking tokens of {MIN_THINKING_TOKENS} or more, not {raw!r}")
    return n


def _thinking_level(name: str, raw: str | None) -> str | None:
    """A thinking level setting's word, None when unset; one that is not low, medium or high is refused."""
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value not in THINKING_LEVELS:
        raise ModelSettingError(f"{name} must be one of {', '.join(THINKING_LEVELS)}, not {raw!r}")
    return value


def thinking_scopes(call: str | None, tier: str) -> tuple[str, ...]:
    """The thinking settings' name stems for a call on a tier, most specific first: ASK_<CALL>_THINKING, then
    MODEL_<TIER>_THINKING (high, medium and low only), then MODEL_THINKING. call None leaves Ask's own out."""
    return (*((f"ASK_{call.upper()}_THINKING",) if call else ()),
            *((f"MODEL_{tier.upper()}_THINKING",) if tier in BUDGET_TIERS else ()), "MODEL_THINKING")


def structured_settings(tier: str) -> dict[str, str]:
    """The budget and thinking settings of Ask's structured calls that are set and apply to a tier's model, by name, as
    set: Ask's own, MODEL_THINKING_* and the tier's MODEL_<TIER>_THINKING_*. Only Ask makes structured calls, so they
    apply to the tier Ask's calls are made on (ask_tier) alone: none to another tier's model, such as the document
    reader's, whose identity then never moves with them."""
    if tier != ask_tier():
        return {}
    current = settings()
    names = [f"ASK_{call.upper()}_MAX_TOKENS" for call in CALLS]
    for stem in (*(f"ASK_{call.upper()}_THINKING" for call in CALLS), *thinking_scopes(None, tier)):
        names += [f"{stem}_TOKENS", f"{stem}_LEVEL"]
    held = {name: (getattr(current, name) or "").strip() for name in names}
    return {name: value for name, value in held.items() if value}


@dataclass(frozen=True)
class CallBudget:
    """One structured call's reply budget and the thinking sent within it. thinking is {}, {"tokens": n} or
    {"level": name}; more_room says whether one more step of less thinking would lower what is sent."""
    call: str
    max_tokens: int
    max_tokens_setting: str
    configured: bool
    thinking: dict
    thinking_setting: str | None
    more_room: bool

    def sent(self) -> str | None:
        """The thinking sent, as a trace records it: tokens:1024, level:low, or None."""
        if "tokens" in self.thinking:
            return f"tokens:{self.thinking['tokens']}"
        if "level" in self.thinking:
            return f"level:{self.thinking['level']}"
        return None


def call_budget(call: str, tier: str, provider: str, model: str, *, configured_max_tokens: int | None,
                less_thinking: int = 0, ask: bool = True) -> CallBudget:
    """A structured call's budget and thinking. The budget is ASK_<CALL>_MAX_TOKENS, else configured_max_tokens (the
    model's tier or global budget, or its constructor's), else the call's image default. Thinking is the most specific
    THINKING_TOKENS and THINKING_LEVEL set (thinking_scopes), the call's default cap where no tokens are set: a level
    set at least as specifically as the tokens, on a model that takes levels, is sent as the level (stepped down
    less_thinking times); else, on a model that takes a budget, the tokens, never more than half the budget, halved
    less_thinking times and never below the model's minimum (min_thinking_tokens); else, on a Gemini that takes a level
    alone (Gemini 4 and later), the level low, in place of the token cap; else nothing. With no thinking setting set for
    it, a call in LEVEL_LOW_BY_DEFAULT (the planner) on Gemini 3 and later is sent the level low ahead of the token cap. A setting the model cannot honour is refused, naming it, as is
    a budget too small to leave room for thinking. ask False leaves Ask's own thinking settings out (a tier's check)."""
    if call not in CALLS:
        raise ValueError(f"a structured call is one of {', '.join(CALLS)}, not {call!r}")
    current = settings()
    default_budget, default_cap = CALL_DEFAULTS[call]
    own = f"ASK_{call.upper()}_MAX_TOKENS"
    budget = _whole(own, getattr(current, own))
    setting = own
    if budget is None and configured_max_tokens is not None:
        budget = configured_max_tokens
        for name in (MAX_TOKENS_SETTING.get(tier), "MODEL_MAX_TOKENS"):
            if name and _whole(name, getattr(current, name)) == configured_max_tokens:
                setting = name
                break
    configured = budget is not None
    max_tokens = budget if budget is not None else default_budget
    tokens = level = None
    tokens_at = level_at = None
    for rank, stem in enumerate(thinking_scopes(call if ask else None, tier)):
        if tokens is None:
            tokens = _thinking_tokens(f"{stem}_TOKENS", getattr(current, f"{stem}_TOKENS"))
            tokens_at = (rank, f"{stem}_TOKENS") if tokens is not None else None
        if level is None:
            level = _thinking_level(f"{stem}_LEVEL", getattr(current, f"{stem}_LEVEL"))
            level_at = (rank, f"{stem}_LEVEL") if level is not None else None
    takes_budget, takes_level = thinking_support(provider, model)
    name = f"{provider}:{model}"
    if (ask and call in LEVEL_LOW_BY_DEFAULT and tokens_at is None and level_at is None
            and provider in ("gemini", VERTEX_GEMINI) and GEMINI_3.match(model)):
        least = min_thinking_tokens(provider, model)
        if takes_budget and max_tokens // 2 < least:
            raise ModelSettingError(f"{setting} must be at least {2 * least} tokens to leave room for thinking on {name}, "
                                    f"not {max_tokens}")
        return CallBudget(call, max_tokens, setting, configured, {"level": THINKING_LEVELS[0]}, None, False)
    level_wins = level_at is not None and (tokens_at is None or level_at[0] <= tokens_at[0] or not takes_budget)
    if level_wins and takes_level:
        step = max(0, THINKING_LEVELS.index(level) - max(0, less_thinking))
        return CallBudget(call, max_tokens, setting, configured, {"level": THINKING_LEVELS[step]}, level_at[1], step > 0)
    if level_wins and takes_budget:
        raise ModelSettingError(f"{level_at[1]} is set, but {name} takes no thinking level: set "
                                f"{level_at[1][:-len('LEVEL')]}TOKENS ({MIN_THINKING_TOKENS} or more) instead, or unset it")
    if takes_budget:
        least = min_thinking_tokens(provider, model)
        if max_tokens // 2 < least:
            raise ModelSettingError(f"{setting} must be at least {2 * least} tokens to leave room for thinking on {name}, "
                                    f"not {max_tokens}")
        cap = min(tokens if tokens is not None else default_cap, max_tokens // 2)
        sent = max(least, cap >> max(0, less_thinking))
        return CallBudget(call, max_tokens, setting, configured, {"tokens": sent}, tokens_at[1] if tokens_at else None,
                          sent > least)
    if tokens_at is not None:
        instead = (f"set {tokens_at[1][:-len('TOKENS')]}LEVEL ({', '.join(THINKING_LEVELS[:-1])} or {THINKING_LEVELS[-1]}) "
                   "instead, or unset it" if takes_level else "unset it")
        raise ModelSettingError(f"{tokens_at[1]} is set, but {name} takes no thinking budget: {instead}")
    if level_at is not None:
        raise ModelSettingError(f"{level_at[1]} is set, but {name} takes no thinking setting: unset it")
    if takes_level and not takes_budget and provider in ("gemini", VERTEX_GEMINI):
        return CallBudget(call, max_tokens, setting, configured, {"level": THINKING_LEVELS[0]}, None, False)
    return CallBudget(call, max_tokens, setting, configured, {}, None, False)


def check_settings() -> None:
    """The process-wide model settings, checked when a process that serves starts: MODEL_SEED, every reply budget
    (MODEL_MAX_TOKENS and MODEL_<TIER>_MAX_TOKENS, whether or not a model or the tier is configured), and every budget and
    thinking setting of Ask's structured calls (ASK_<CALL>_MAX_TOKENS, ASK_<CALL>_THINKING_*, MODEL_THINKING_* and
    MODEL_<TIER>_THINKING_*): each must parse, Ask's model (the context agent's) must honour every one that applies to its
    calls, and each configured tier the thinking settings set for it. ModelSettingError names the first one that does
    not. An agent's own setting (*_MODEL_TIER, a tier's provider) is not read here: a mistyped one stops that agent
    alone, at its start."""
    model_seed()
    from kge.adapters.model.chat import chat_policy
    try:
        for purpose in ("agent", "catalogue"):
            chat_policy(purpose)
    except ValueError as exc:
        raise ModelSettingError(str(exc)) from None
    try:
        model_timeout()
        model_total_timeout()
        request_timeout()
    except ValueError as exc:
        raise ModelSettingError(str(exc)) from None
    current = settings()
    for name in ("MODEL_MAX_TOKENS", *MAX_TOKENS_SETTING.values(), *(f"ASK_{call.upper()}_MAX_TOKENS" for call in CALLS)):
        _whole(name, getattr(current, name))
    for stem in {stem for call in CALLS for tier in BUDGET_TIERS for stem in thinking_scopes(call, tier)}:
        _thinking_tokens(f"{stem}_TOKENS", getattr(current, f"{stem}_TOKENS"))
        _thinking_level(f"{stem}_LEVEL", getattr(current, f"{stem}_LEVEL"))
    try:
        ask = model_for("context")
    except ModelSettingError:
        ask = None
    if ask is not None:
        for call in CALLS:
            call_budget(call, ask.tier, ask.provider, ask.model, configured_max_tokens=ask.configured_max_tokens)
    for tier, prefix in (*((tier, f"MODEL_{tier.upper()}") for tier in BUDGET_TIERS), ("default", "MODEL")):
        provider = os.getenv(f"{prefix}_PROVIDER", "").strip().lower()
        if provider not in PROVIDERS:
            continue
        stem = f"{prefix}_THINKING"
        if not any((getattr(current, f"{stem}_{part}") or "").strip() for part in ("TOKENS", "LEVEL")):
            continue
        name = MOCK if provider == MOCK else (os.getenv(f"{prefix}_NAME", "").strip() or DEFAULT_MODEL.get(provider, ""))
        call_budget("plan", tier, provider, name, configured_max_tokens=configured_max_tokens_for(tier), ask=False)


def one_prompt(messages: list[BaseMessage]) -> str:
    """The one human message a call sends, its text: the demo's prompts are one user turn each, never a prefill."""
    if len(messages) != 1 or not isinstance(messages[0], HumanMessage) or not isinstance(messages[0].content, str):
        raise ValueError("a call sends one human message, of text")
    return messages[0].content


def result(text: str, served: str | None, tokens_in: int | None, tokens_out: int | None, stop_reason: str | None = None, *,
           tokens_thinking: int | None = None, replay: dict | None = None) -> ChatResult:
    """One reply as LangChain carries it: its text, the model that served it, why it stopped, and the tokens the provider
    reported, in token_usage (None for a count it did not report) and in usage_metadata too where it reported both. A
    structured call's reply also carries the thinking tokens within tokens out (token_usage's thinking_tokens) and the
    provider's own record of the reply (replay), each only where known."""
    usage = ({"input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out}
             if tokens_in is not None and tokens_out is not None else None)
    tokens = {"input_tokens": tokens_in, "output_tokens": tokens_out, **({"thinking_tokens": tokens_thinking} if tokens_thinking is not None else {})}
    meta = {"model_name": served, "stop_reason": stop_reason, "token_usage": tokens, **({"replay": replay} if replay is not None else {})}
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text, response_metadata=meta, usage_metadata=usage))])


def conversation(messages: list[BaseMessage]) -> tuple[str, list[tuple[str, str, dict | None]]]:
    """A structured call's messages as (system instruction, turns): a system message, the first human message, then
    model and user turns alternating, each (role, text, the provider's replay of a model turn or None)."""
    if (len(messages) < 2 or not isinstance(messages[0], SystemMessage) or not isinstance(messages[1], HumanMessage)
            or any(not isinstance(m.content, str) for m in messages)):
        raise ValueError("a structured call sends a system message, then a human message, of text")
    turns = []
    for m in messages[1:]:
        if isinstance(m, AIMessage):
            turns.append(("model", m.content, m.additional_kwargs.get("replay")))
        elif isinstance(m, HumanMessage):
            turns.append(("user", m.content, None))
        else:
            raise ValueError("a structured call's turns are the model's and the user's")
    return messages[0].content, turns


SCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")
SCHEMA_VALUES = ("enum", "default", "examples", "required")
SCHEMA_DROPPED = ("discriminator", "title", "x-ids")
CLAUDE_SCHEMA_DROPPED = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern", "maxItems")
GEMINI_SCHEMA_DROPPED = ("default", "minLength", "maxLength", "pattern", "maxItems", "minItems")
UNROLL_DEPTH = 3
GEMINI_UNROLL_DEPTH = 1
UNROLLED = "{name}Depth{level}"
JSON_MODE_SCHEMA = "Reply with one JSON object that matches this JSON schema:"


def provider_schema(schema: dict, provider: str) -> dict:
    """A contract's JSON schema as a provider's structured output takes it, a copy: no discriminator, title or x-ids
    marker; oneOf as anyOf; const as a one-value enum; an integer's exclusiveMinimum k as minimum k + 1. For Claude also
    no numeric or length bound, pattern, maxItems or minItems above 1; for Gemini no default, length bound, pattern,
    maxItems or minItems: Vertex AI's grammar compiler unrolls an item bound into states (64 by 64 for a nested
    order's partition_by) until it refuses the request (#561). $defs and $ref are kept, but never a cycle: a definition that refers back
    to itself (a predicate's children are predicates) is unrolled (unrolled), since Claude takes no recursive schema and
    Gemini a cyclic one only in a property that is not required. Gemini's is unrolled to GEMINI_UNROLL_DEPTH, one level,
    every other provider's to UNROLL_DEPTH, three: Vertex AI inlines every $ref of Gemini's schema and counts the result
    as input tokens, and the planner's schema, with its ids plain strings, inlines to about 605 KB at three levels and
    about 71 KB at one. The cost: under Gemini's structured output a junction, a not or a membership cannot sit inside
    another's children or filters, so "(A and B) or C" cannot be written there; no recorded plan nests so. Pydantic still
    validates every bound dropped here, and accepts deeper nesting from any reply that has it."""
    node = _schema_node(deepcopy(dict(schema)), provider in CLAUDE)
    if provider in ("gemini", VERTEX_GEMINI):
        return unrolled(_dropped(node, GEMINI_SCHEMA_DROPPED), GEMINI_UNROLL_DEPTH)
    return unrolled(node)


def json_mode_instructions(instructions: str, schema: dict) -> str:
    """A JSON-mode call's system instruction: the request's, then a blank line, JSON_MODE_SCHEMA and the contract's
    schema as compact JSON, provider-neutral (no discriminator, title or x-ids marker, its cycles kept as $ref, since
    text takes a recursive schema where structured output does not)."""
    shown = _schema_node(deepcopy(dict(schema)), False)
    return f"{instructions}\n\n{JSON_MODE_SCHEMA}\n{json.dumps(shown, separators=(',', ':'), sort_keys=True)}"


def _dropped(node: Any, keys: tuple[str, ...]) -> Any:
    """node without the schema keywords keys, at every level; the keys of a properties or $defs map are names."""
    if isinstance(node, list):
        return [_dropped(n, keys) for n in node]
    if not isinstance(node, dict):
        return node
    return {k: ({n: _dropped(v, keys) for n, v in value.items()} if k in SCHEMA_MAPS and isinstance(value, dict)
                else value if k in SCHEMA_VALUES else _dropped(value, keys))
            for k, value in node.items() if k not in keys}


def _def_name(ref: Any) -> str | None:
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) and ref.startswith("#/$defs/") else None


def _refs(node: Any, out: set[str]) -> set[str]:
    if isinstance(node, list):
        for n in node:
            _refs(n, out)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and (name := _def_name(value)):
                out.add(name)
            elif key not in SCHEMA_VALUES:
                _refs(value, out)
    return out


def cyclic(schema: dict) -> set[str]:
    """The $defs that refer, through any chain of $ref, back to themselves."""
    defs = schema.get("$defs") or {}
    graph = {name: _refs(d, set()) for name, d in defs.items()}
    found = set()
    for name in graph:
        seen, todo = set(), list(graph[name])
        while todo:
            step = todo.pop()
            if step == name:
                found.add(name)
                break
            if step not in seen:
                seen.add(step)
                todo.extend(graph.get(step, ()))
    return found


def unrolled(schema: dict, depth: int = UNROLL_DEPTH) -> dict:
    """schema with no cycle: each cyclic definition D sent as D (level 0) and D's copies DDepth1 ... at levels below it,
    a reference from level k to a cyclic definition pointing at level k + 1; at the last level such a reference is left
    out of its anyOf, and a property that holds nothing else is left out (and out of required). The rest is unchanged."""
    loops = cyclic(schema)
    if not loops:
        return schema
    defs = dict(schema["$defs"])

    def at(name: str, level: int) -> str:
        return name if level == 0 else UNROLLED.format(name=name, level=level)

    def deeper(node: Any, level: int) -> Any:
        if isinstance(node, list):
            kept = [deeper(n, level) for n in node]
            return [n for n in kept if n is not None]
        if not isinstance(node, dict):
            return node
        name = _def_name(node.get("$ref"))
        if name in loops:
            return {**node, "$ref": f"#/$defs/{at(name, level + 1)}"} if level + 1 < depth else None
        out: dict = {}
        for key, value in node.items():
            if key in SCHEMA_VALUES:
                out[key] = value
            elif key in SCHEMA_MAPS and isinstance(value, dict):
                held = {n: deeper(v, level) for n, v in value.items()}
                out[key] = {n: v for n, v in held.items() if v is not None}
            elif key in ("anyOf", "oneOf", "allOf"):
                kept = deeper(value, level)
                if not kept:
                    return None
                out[key] = kept
            elif key in ("items", "additionalProperties", "not") and isinstance(value, dict):
                kept = deeper(value, level)
                if kept is None:
                    return None
                out[key] = kept
            else:
                out[key] = deeper(value, level)
        if "required" in out and "properties" in out:
            out["required"] = [r for r in out["required"] if r in out["properties"]]
        return out

    for name in loops:
        for level in range(depth):
            defs[at(name, level)] = deeper(schema["$defs"][name], level)
    return {**schema, "$defs": defs}


def _schema_node(node: Any, claude: bool) -> Any:
    if isinstance(node, list):
        return [_schema_node(n, claude) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict = {}
    for key, value in node.items():
        if key in SCHEMA_DROPPED:
            continue
        if key in SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _schema_node(v, claude) for name, v in value.items()}
        elif key in SCHEMA_VALUES:
            out[key] = value
        elif key == "const":
            out["enum"] = [value]
        elif key == "oneOf":
            out["anyOf"] = [*out.get("anyOf", []), *_schema_node(value, claude)]
        elif key == "anyOf":
            out["anyOf"] = [*_schema_node(value, claude), *out.get("anyOf", [])]
        else:
            out[key] = _schema_node(value, claude)
    bound = out.get("exclusiveMinimum")
    if out.get("type") == "integer" and isinstance(bound, int) and not isinstance(bound, bool):
        del out["exclusiveMinimum"]
        out["minimum"] = max(bound + 1, out.get("minimum", bound + 1))
    if claude:
        for key in CLAUDE_SCHEMA_DROPPED:
            out.pop(key, None)
        if isinstance(out.get("minItems"), int) and out["minItems"] > 1:
            del out["minItems"]
    return out


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
    from kge.adapters.model.accounting import start_attempt
    if not (settings().MODEL_RATE_LIMITS or "").strip():
        return post_json(url, headers, body, on_attempt=start_attempt)
    from kge.adapters.model.quota import admit_retry
    return post_json(url, headers, body, on_retry=admit_retry, on_attempt=start_attempt)


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
        if "schema" in kwargs:
            return self._structured(messages, **kwargs)
        return result(*self._reply(one_prompt(messages), kwargs.get("max_tokens", 800), kwargs.get("document")))

    def _gemini_target(self) -> tuple[str, dict]:
        """A Gemini call's URL and headers: Vertex AI's as the service account, else the Gemini API's with the key."""
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
        return url, {**headers, "content-type": "application/json"}

    def _structured(self, messages: list[BaseMessage], *, max_tokens: int, schema: dict | None, contract: str, thinking: dict,
                    sample: bool = True, configured: bool = False, **_ignored) -> ChatResult:
        """A structured call (LLM.structured): the system instruction, the turns and the contract's schema in the
        provider's own form, thinking as a budget or a level, never both. schema None is JSON mode: the provider's JSON
        output with no schema (Gemini's responseMimeType alone, no output_config format for Claude, a json_object
        response_format for OpenAI and Ollama), the schema being in the system instruction already. A reply that stopped
        at its budget comes back with stop reason max_tokens, its text whatever there was."""
        instructions, turns = conversation(messages)
        sampling = {"temperature": self.temperature} if sample and self.temperature is not None else {}
        level = thinking.get("level")
        if self.provider in ("gemini", VERTEX_GEMINI):
            contents = [({"role": "model", **{k: v for k, v in replay.items() if k != "role"}} if role == "model" and isinstance(replay, dict)
                         and isinstance(replay.get("parts"), list) else {"role": role, "parts": [{"text": text}]})
                        for role, text, replay in turns]
            config: dict = {**sampling, "seed": self.seed, "maxOutputTokens": max_tokens, "responseMimeType": "application/json",
                            **({"responseJsonSchema": schema} if schema is not None else {})}
            if "tokens" in thinking:
                config["thinkingConfig"] = {"thinkingBudget": thinking["tokens"]}
            elif level:
                config["thinkingConfig"] = {"thinkingLevel": level}
            url, headers = self._gemini_target()
            data = self.transport(url, headers, {"systemInstruction": {"parts": [{"text": instructions}]}, "contents": contents,
                                                 "generationConfig": config})
            use = data.get("usageMetadata") or {}
            out = use.get("candidatesTokenCount")
            thought = use.get("thoughtsTokenCount") or 0
            candidate = next(iter(data.get("candidates") or []), {})
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            text = "".join(p.get("text") or "" for p in parts if not p.get("thought"))
            reason = candidate.get("finishReason")
            if reason == "MAX_TOKENS":
                reason = "max_tokens"
            elif not text:
                raise ExternalCallError(f"Gemini returned no content (finishReason={reason or 'unknown'})")
            replay = {**content, "role": "model", "parts": [p for p in parts if not p.get("thought")]} if parts else None
            return result(text, data.get("modelVersion"), use.get("promptTokenCount"), None if out is None else out + thought, reason,
                          tokens_thinking=None if out is None else thought, replay=replay)
        output = {**({"format": {"type": "json_schema", "schema": schema}} if schema is not None else {}),
                  **({"effort": level} if level else {})}
        configured_output = {"output_config": output} if output else {}
        chat = [{"role": "assistant" if role == "model" else "user", "content": text} for role, text, _ in turns]
        if self.provider == VERTEX_CLAUDE:
            return result(*claude(self.vertex_create(max_tokens=max_tokens, **sampling, system=instructions, messages=chat,
                                                     **({"extra_body": configured_output} if configured_output else {}))))
        if self.provider == "anthropic":
            return result(*claude(self.transport("https://api.anthropic.com/v1/messages",
                                                 {"x-api-key": self.api_key.get_secret_value(), "anthropic-version": "2023-06-01",
                                                  "content-type": "application/json"},
                                                 {"model": self.model, "max_tokens": max_tokens, "system": instructions,
                                                  "messages": chat, **configured_output, **sampling})))
        base = self.base_url or ("http://localhost:11434/v1" if self.provider == "ollama" else "https://api.openai.com/v1")
        body = {"model": self.model, "messages": [{"role": "system", "content": instructions}, *chat], "temperature": self.temperature,
                "seed": self.seed, **({"max_tokens": max_tokens} if configured else {}),
                "response_format": ({"type": "json_schema", "json_schema": {"name": contract, "schema": schema, "strict": False}}
                                    if schema is not None else {"type": "json_object"}),
                **({"reasoning_effort": level} if level and self.provider == "openai" else {})}
        data = self.transport(f"{base}/chat/completions",
                              {"authorization": f"Bearer {self.api_key.get_secret_value() or 'ollama'}", "content-type": "application/json"},
                              body)
        use = data.get("usage") or {}
        choice = data["choices"][0]
        reason = choice.get("finish_reason")
        reasoning = (use.get("completion_tokens_details") or {}).get("reasoning_tokens")
        return result(choice["message"].get("content") or "", data.get("model"), use.get("prompt_tokens"), use.get("completion_tokens"),
                      "max_tokens" if reason == "length" else reason, tokens_thinking=reasoning)

    def _reply(self, prompt: str, max_tokens: int, document: dict | None = None) -> tuple[str, str | None, int | None, int | None, str | None]:
        """(text, model that served, tokens in, tokens out, stop reason); a count the provider does not report is None.
        Gemini's thinking tokens are billed as output, so they are counted in tokens out."""
        if document is not None and self.provider != VERTEX_GEMINI:
            raise ModelSettingError("document reading needs vertex-gemini")
        sampling = {"temperature": self.temperature} if self.temperature is not None else {}
        if self.provider == VERTEX_CLAUDE:
            return claude(self.vertex_reply(prompt, max_tokens, sampling))
        if self.provider in ("gemini", VERTEX_GEMINI):
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": self.temperature, "seed": self.seed, "maxOutputTokens": max_tokens}}
            if document is not None:
                body["contents"][0]["parts"].append(document)
            url, headers = self._gemini_target()
            data = self.transport(url, headers, body)
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
            return text, data.get("modelVersion"), use.get("promptTokenCount"), out, candidate.get("finishReason")
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
        return self.vertex_create(max_tokens=max_tokens, **sampling, messages=[{"role": "user", "content": prompt}])

    def vertex_create(self, **fields) -> dict:
        """One Messages API call to Claude on Vertex AI with these fields (a structured call's system instruction and
        output_config among them, the latter as extra_body), with one transient retry and safe errors matching the REST
        providers."""
        from anthropic import APIConnectionError, APIStatusError
        from kge.adapters.model.accounting import start_attempt
        from kge.adapters.model.quota import admit_retry
        deadline = CallDeadline()
        for attempt in range(2):
            if attempt:
                admit_retry()
            tracked = start_attempt()
            try:
                response = self.anthropic_vertex().messages.create(
                    model=self.model, timeout=deadline.remaining_timeout(), **fields).model_dump()
            except APIStatusError as exc:
                if tracked is not None:
                    tracked.finish(outcome="failed")
                refused = schema_refused(exc.status_code, str(getattr(exc, "message", "") or exc), schema_sent=carries_schema(fields))
                error = ExternalCallError(f"Vertex AI error (HTTP {exc.status_code})" + (": the schema was refused" if refused else ""),
                                          transient=exc.status_code == 429 or exc.status_code >= 500, schema_refused=refused)
            except (APIConnectionError, TimeoutError, ConnectionError):
                if tracked is not None:
                    tracked.finish(outcome="failed")
                error = ExternalCallError("Vertex AI unreachable", transient=True)
            except BaseException:
                if tracked is not None:
                    tracked.finish(outcome="failed")
                raise
            else:
                if tracked is not None:
                    tracked.finish(payload=response, outcome="ok")
                return response
            if attempt or not transient(error):
                raise error from None

    def anthropic_vertex(self):
        """Claude on Vertex AI: the Anthropic SDK's client, signed in as the service account, refreshing its token as it needs.
        Its own OpenTelemetry is off (ANTHROPIC_TELEMETRY_OFF): no span of its own, and no trace header sent to the
        provider; its calls go through kge/adapters/http.py's sdk_http_client: KGE's User-Agent, no X-Stainless-*
        header."""
        credentials, project = self.account()
        with self._lock:
            if self._vertex is None:
                from anthropic import AnthropicVertex

                from kge.adapters.http import sdk_http_client
                self._vertex = AnthropicVertex(region=self.region, project_id=project, credentials=credentials, timeout=model_timeout(), max_retries=0,
                                               open_telemetry=ANTHROPIC_TELEMETRY_OFF,
                                               http_client=sdk_http_client(timeout=model_timeout(), follow_redirects=True))
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
            raise ModelSettingError("MODEL_PROVIDER=vertex-gemini needs MODEL_NAME: the Gemini model to call, as Vertex AI names it")
        if provider in VERTEX:
            api_key, base_url = "", None
        self.provider, self.model, self.api_key, self.base_url = provider, model, api_key, base_url
        self.tier = tier
        configured_budget = max_tokens if max_tokens is not None else configured_max_tokens_for(tier)
        self.configured_max_tokens = configured_budget
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
        unchanged unless a budget override is explicit. The budget and thinking settings of Ask's structured calls that
        apply to this model (structured_settings) enter it, as set, only when one is set: unset, the identity and its
        hash are as they were before those settings existed."""
        seed = self.seed if self.provider in SEEDED else None
        mode = {"mode": self.mode} if self.provider == MOCK else {}
        region = {"region": self.region} if self.provider in VERTEX else {}
        budget = {"max_tokens": self.max_tokens} if self._record_budget else {}
        calls = structured_settings(self.tier)
        structured = {"structured": calls} if calls else {}
        settings = {"provider": self.provider, "model": self.model, "base_url": self.base_url or "", "temperature": self.temperature, "seed": seed,
                    **mode, **region, **budget, **structured}
        return {"name": self.name, "provider": self.provider, "model": self.model, "temperature": self.temperature, "seed": seed, **mode,
                **region, **budget, **structured, "config_hash": hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()}

    def stream_chat(self, request: ChatRequest):
        """The model port's streamed chat capability, with provider-verified input limits."""
        from kge.adapters.model.chat import stream
        from kge.adapters.model.quota import admit, retry_scope
        admit(self.name, request.purpose)

        def run():
            from kge.adapters.model.accounting import model_scope
            with retry_scope(self.name, request.purpose), model_scope(self, request.purpose):
                yield from stream(self, request)
        return run()

    @staticmethod
    def _stream_post(url: str, headers: dict, body: dict):
        from kge.adapters.model.chat import _stream_http
        return _stream_http(url, headers, body)

    def complete(self, prompt: str, max_tokens: int = 800, *, fixed_budget: bool = False, document: dict | None = None,
                 default_budget: int | None = None, whole: bool = False) -> str:
        """The reply's text, through the chat model, the call recorded in the run's trace. A refusal raises Refused, recorded
        as refused with the tokens it took; a failed call raises what it raised, recorded by its type. max_tokens is the
        caller's budget, replaced by self.max_tokens except for a fixed_budget connectivity probe; default_budget, a
        caller's own default, replaces the image default only, never a configured budget. document is one more part of the
        request (a Gemini inlineData or fileData part). whole: a reply that stopped at its budget raises Truncated."""
        from kge.adapters.model.quota import admit, retry_scope
        from kge.adapters.model.accounting import model_scope
        admit(self.name, "complete")
        if default_budget is not None and self.configured_max_tokens is None and not fixed_budget:
            max_tokens = default_budget
        elif self.max_tokens is not None and not fixed_budget:
            max_tokens = self.max_tokens
        started = time.perf_counter()
        try:
            with retry_scope(self.name, "complete"), model_scope(self, "complete"):
                from kge.adapters.model.accounting import start_attempt
                mock_attempt = start_attempt() if self.provider == MOCK else None
                try:
                    reply = request_model_call(lambda: self.chat.invoke([HumanMessage(content=prompt)], max_tokens=max_tokens,
                                                                        **({"document": document} if document is not None else {})))
                except BaseException:
                    if mock_attempt is not None:
                        mock_attempt.finish(outcome="failed")
                    raise
                if mock_attempt is not None:
                    mock_attempt.finish(payload={"model": "mock", "usage": reply.response_metadata.get("token_usage")}, outcome="ok")
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
        if meta.get("stop_reason") == "unavailable_plan":
            raise UnavailablePlan(json.loads(reply.content)['missing'][0])
        if whole and str(meta.get("stop_reason") or "").lower() == "max_tokens":
            raise Truncated(max_tokens, MAX_TOKENS_SETTING.get(self.tier, "MODEL_MAX_TOKENS"))
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

    @property
    def single_text(self) -> bool:
        """Whether this model takes a structured call's legacy single-text form alone (the mock), which carries no
        correction turns, so a correction would send it the identical prompt again."""
        return self.provider == MOCK

    def structured(self, request: ModelRequest) -> ModelReply:
        """One structured call (kge/ports.py ModelProvider.structured): the request's system instruction, content and
        correction turns, its contract's schema as the provider's structured output, and the call's budget and thinking
        (call_budget), the call recorded in the run's trace with its thinking and contract tokens apart, and how the schema
        went (detail schema: sent, json or refused). A request in JSON mode (json_mode) is sent no schema as structured
        output: the provider's JSON mode, the schema shown in the system instruction (json_mode_instructions). A call
        whose schema the provider refuses (ExternalCallError.schema_refused: an HTTP 400 naming the schema or the input
        token count, or Vertex AI's generic invalid argument on a request that sent one) is made again once, in JSON mode, recorded with schema_fallback; that retry is not a correction, and
        a refusal in JSON mode is raised. Its reply, or its Truncated or NotJSON, says json_mode, so the conversation's
        corrections are sent in JSON mode too and the refused schema is not sent again (structured_call). The planner on
        Gemini 3 and later is sent in JSON mode from the first attempt (json_mode_call). The mock is
        sent the request's legacy single-text form through json(), so it replays its recordings by key as before. Raises Refused on a refusal, Truncated on a reply that stopped at its
        budget (whatever text came with it), NotJSON on a reply that is not one JSON object, and whatever the call
        raised."""
        if request.call not in CALLS:
            raise ValueError(f"a structured call is one of {', '.join(CALLS)}, not {request.call!r}")
        if self.provider == MOCK:
            data = self.json(request.legacy_prompt)
            return ModelReply(data=data, text=json.dumps(data, sort_keys=True))
        if not request.json_mode and json_mode_call(self.provider, self.model, request.call):
            request = replace(request, json_mode=True)
        try:
            return self._structured_call(request)
        except ExternalCallError as exc:
            if request.json_mode or not getattr(exc, "schema_refused", False):
                raise
        return self._structured_call(replace(request, json_mode=True), schema_fallback=True)

    def _structured_call(self, request: ModelRequest, schema_fallback: bool = False) -> ModelReply:
        """One attempt of structured(): the request sent once, with its schema as structured output or, in JSON mode, in
        its system instruction; recorded in the trace, schema_fallback marking the JSON-mode retry of a refused schema."""
        from kge.adapters.model.quota import admit, retry_scope
        from kge.adapters.model.accounting import model_scope
        admit(self.name, request.call)
        plan = call_budget(request.call, self.tier, self.provider, self.model, configured_max_tokens=self.configured_max_tokens,
                           less_thinking=request.less_thinking)
        instructions = json_mode_instructions(request.instructions, request.schema) if request.json_mode else request.instructions
        messages: list[BaseMessage] = [SystemMessage(content=instructions), HumanMessage(content=request.content)]
        for turn in request.turns:
            messages.append(AIMessage(content=turn.text, additional_kwargs={"replay": dict(turn.replay)} if turn.replay is not None else {})
                            if turn.role == "model" else HumanMessage(content=turn.text))
        budget_sent = plan.max_tokens if plan.configured or self.provider not in UNBUDGETED else None
        detail = {"call": request.call, "attempt": request.attempt, "max_tokens": budget_sent, "thinking": plan.sent(),
                  "schema": "json" if request.json_mode else "sent", **({"schema_fallback": True} if schema_fallback else {})}
        sample = not (self.provider in ("gemini", VERTEX_GEMINI) and GEMINI_3.match(self.model))
        schema = None if request.json_mode else provider_schema(dict(request.schema), self.provider)
        started = time.perf_counter()
        try:
            with retry_scope(self.name, request.call), model_scope(self, request.call):
                reply = request_model_call(lambda: self.chat.invoke(messages, max_tokens=plan.max_tokens, schema=schema, contract=request.contract,
                                                                    thinking=dict(plan.thinking), sample=sample, configured=plan.configured))
        except Exception as exc:
            refused_schema = not request.json_mode and getattr(exc, "schema_refused", False)
            trace.llm_call(self.name, None, None, None, (time.perf_counter() - started) * 1000, ok=False, error=type(exc).__name__,
                           detail={**detail, **({"schema": "refused"} if refused_schema else {}), "tokens_thinking": None,
                                   "tokens_contract": None, "finish": None})
            request_remaining()
            raise
        meta = reply.response_metadata
        use = meta.get("token_usage") or {}
        tokens_out, tokens_thinking = use.get("output_tokens"), use.get("thinking_tokens")
        tokens_contract = tokens_out - tokens_thinking if tokens_out is not None and tokens_thinking is not None else None
        finish = meta.get("stop_reason")
        refused = finish == "refusal"
        truncated = str(finish or "").lower() in TRUNCATED
        trace.llm_call(self.name, meta.get("model_name"), use.get("input_tokens"), tokens_out, (time.perf_counter() - started) * 1000,
                       **({"ok": False, "error": "refused"} if refused else {}),
                       detail={**detail, "tokens_thinking": tokens_thinking, "tokens_contract": tokens_contract, "finish": finish})
        if refused:
            raise Refused()
        if truncated:
            raise Truncated(budget_sent, plan.max_tokens_setting, more_room=plan.more_room, thinking=tokens_thinking,
                            json_mode=request.json_mode)
        text = reply.content if isinstance(reply.content, str) else ""
        fenced = FENCED.match(text)
        try:
            data = json.loads(fenced.group(1) if fenced else text)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            raise NotJSON("model reply held no JSON object", reply_text=text, replay=meta.get("replay"), json_mode=request.json_mode)
        return ModelReply(data=data, text=text, replay=meta.get("replay"), tokens_in=use.get("input_tokens"),
                          tokens_thinking=tokens_thinking, tokens_contract=tokens_contract, finish=finish,
                          json_mode=request.json_mode)

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


def agent_tier(agent: str, selected: str) -> tuple[str, str | None]:
    """The tier an agent's model comes from, as model_for picks it, and why it fell back when its selected tier
    (selected, its <AGENT>_MODEL_TIER, already checked) has no provider; None when it did not."""
    wanted, reason = selected or PREFERRED_TIER.get(agent, "default"), None
    if wanted != "default" and not os.getenv(f"MODEL_{wanted.upper()}_PROVIDER", "").strip():
        fallback = "medium" if agent in ("context", "assist") and wanted == "high" and os.getenv("MODEL_MEDIUM_PROVIDER", "").strip() else "default"
        if selected:
            reason = f"{agent.upper()}_MODEL_TIER={selected}, but MODEL_{wanted.upper()}_PROVIDER is not set: the {fallback} model answers"
        wanted = fallback
    return wanted, reason


def ask_tier() -> str | None:
    """The tier Ask's model calls are made on: the context agent's, as model_for picks it; None when its
    CONTEXT_MODEL_TIER names no tier (the agent itself then stops, naming it)."""
    selected = os.getenv("CONTEXT_MODEL_TIER", "").strip().lower()
    if selected and selected not in TIERS:
        return None
    return agent_tier("context", selected)[0]


def model_for(agent: str, step: str | None = None, *, expected_model: str | None = None) -> LLM | None:
    """The model pinned for an agent (onboard, enhance, answer, context, assist, tagger or reader). Its <AGENT>_MODEL_TIER selects a
    tier (high, medium, low or default), else it prefers PREFERRED_TIER's; a tier with no MODEL_<TIER>_PROVIDER falls
    back to the default model whole, never half of each. Context and assist try medium before default when high is
    unset, never low; their default model must be medium or better. When the tier was selected the fallback is logged and kept
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
    if expected_model and not selected and agent in ("cerebro", "catalogue"):
        matches = []
        for tier in ("default", "high", "medium", "low"):
            prefix = "MODEL" if tier == "default" else f"MODEL_{tier.upper()}"
            provider = os.getenv(f"{prefix}_PROVIDER", "none").strip().lower()
            if provider not in PROVIDERS:
                continue
            name = MOCK if provider == MOCK else (os.getenv(f"{prefix}_NAME", "").strip() or DEFAULT_MODEL.get(provider, ""))
            if name == expected_model:
                matches.append((tier, prefix, provider))
        candidates = [_built(prefix, provider, tier, default) for tier, prefix, provider in matches]
        def configuration(model):
            return (model.provider, model.model, model.api_key, model.base_url, model.region,
                    getattr(model.chat, "project", None), model.max_tokens, model.prices["in"], model.prices["out"])
        if not candidates or any(configuration(model) != configuration(candidates[0]) for model in candidates[1:]):
            return None
        return candidates[0]
    if agent == "tagger" and step in PROVIDERS:
        if step == default:
            return _built("MODEL", step, "default", default)
        if not (settings().TAGGER_NAME or "").strip() and step not in DEFAULT_MODEL:
            raise ModelSettingError(f"TAGGER_PROVIDER={step} is not MODEL_PROVIDER ({default}): set TAGGER_NAME to its model, "
                             "and TAGGER_API_KEY to its key where it takes one; MODEL_NAME and MODEL_API_KEY are "
                             f"{default}'s")
        return _built("TAGGER", step, "default", default)
    wanted, reason = agent_tier(agent, selected)
    if reason:
        log.warning(reason)
    prefix = f"MODEL_{wanted.upper()}" if wanted != "default" else "MODEL"
    llm = _built(prefix, os.getenv(f"{prefix}_PROVIDER", "none").strip().lower(), wanted, default)
    if llm is not None:
        llm.tier_reason = reason
    return llm


def tagger_model() -> LLM | None:
    """The model the tagger tags with, or None for the rule tagger. A provider TAGGER_PROVIDER names wins over the tiers
    (rules, or none named outright, is the rule tagger); model, or no TAGGER_PROVIDER, takes the tagger's tier, and with
    none set the tagger stays on rules. The pipeline uses rules for the mock; the evaluator can tag with it explicitly. On the
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


def grading_model():
    """The advisory evaluator's model; offline providers do not supply model quality evidence."""
    model = model_for("answer")
    return None if model is None or model.provider == "mock" else model
