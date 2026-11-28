"""Streaming chat through the model port. Vertex's token counter bounds the exact request sent."""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from typing import Iterator

from kge.adapters import http
from kge.adapters.deadline import request_cap, request_window
from kge.adapters.model.budget import request_model_call
from kge.adapters.model.errors import ExternalCallError, model_timeout, model_total_timeout
from kge.pipeline import trace
from kge.pipeline.settings import settings
from kge.ports import ChatEvent, ChatMessage, ChatPart, ChatRequest


class ChatUnsupported(ValueError):
    """The configured connector does not yet offer streamed chat."""


class ChatInputLimit(ValueError):
    """The newest exchange and instructions do not fit the requested input budget."""


def checked_schema(schema) -> dict:
    """Validate a bounded local-only schema so output validation never fetches remote references."""
    if not isinstance(schema, dict):
        raise ValueError("invalid chat response schema")
    try:
        if len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
            raise ValueError("chat response schema is too large")
    except (TypeError, OverflowError, RecursionError):
        raise ValueError("invalid chat response schema") from None

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            for key, child in node.items():
                if key in ("$ref", "$dynamicRef", "$id"):
                    if not isinstance(child, str) or not child.startswith("#"):
                        raise ValueError("chat response schemas may only use local references")
                elif key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas") and isinstance(child, dict):
                    for value in child.values():
                        visit(value)
                elif key not in ("enum", "const", "default", "examples"):
                    visit(child)

    visit(schema)
    from jsonschema import Draft202012Validator
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise ValueError("invalid chat response schema") from None
    return schema


_POLICIES = {
    "agent": ("MODEL_CHAT_AGENT_INPUT_TOKENS", "MODEL_CHAT_AGENT_OUTPUT_TOKENS", "MODEL_CHAT_AGENT_THINKING_TOKENS", "MODEL_CHAT_AGENT_THINKING_LEVEL"),
    "catalogue": ("MODEL_CHAT_CATALOGUE_INPUT_TOKENS", "MODEL_CHAT_CATALOGUE_OUTPUT_TOKENS", "MODEL_CHAT_CATALOGUE_THINKING_TOKENS", "MODEL_CHAT_CATALOGUE_THINKING_LEVEL"),
}


def chat_policy(purpose: str) -> tuple[int, int, int | None, str]:
    """The operator's caps, validated independently of all caller-provided values."""
    if purpose not in _POLICIES:
        raise ValueError("unknown chat purpose")
    current = settings()
    names = _POLICIES[purpose]
    parsed = []
    for name, default in zip(names[:3], (32768, 8192, None)):
        value = (getattr(current, name) or "").strip()
        if not value:
            parsed.append(default)
            continue
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise ValueError(f"{name} must be a positive whole number")
        parsed.append(int(value))
    level = (getattr(current, names[3]) or "low").strip().lower()
    if level not in ("low", "medium", "high"):
        raise ValueError(f"{names[3]} must be low, medium or high")
    if parsed[2] is not None and (getattr(current, names[3]) or "").strip():
        raise ValueError("configure either a chat thinking token cap or a level cap, not both")
    return (*parsed, level)


def governed(llm, request: ChatRequest) -> ChatRequest:
    """The caller may lower, but cannot raise, the per-purpose budgets."""
    input_cap, output_cap, thinking_cap, level_cap = chat_policy(request.purpose)
    thinking = request.thinking
    model3 = bool(re.match(r"^gemini-(?:[3-9]|\d{2,})", llm.model))
    model25 = bool(re.match(r"^gemini-2\.5(?:[.-]|$)", llm.model))
    if model25 and (getattr(settings(), _POLICIES[request.purpose][3]) or "").strip():
        raise ValueError("the model does not support a thinking level")
    if thinking is not None and not isinstance(thinking, dict):
        raise ValueError("invalid chat thinking budget")
    if thinking and set(thinking) == {"level"} and thinking["level"] in ("low", "medium", "high") and model3 and thinking_cap is None:
        levels = ("low", "medium", "high")
        thinking = {"level": levels[min(levels.index(thinking["level"]), levels.index(level_cap))]}
    elif thinking and set(thinking) == {"tokens"} and isinstance(thinking["tokens"], int) and not isinstance(thinking["tokens"], bool) and thinking["tokens"] >= 0 and (model25 or (llm.model.startswith("gemini-3") and thinking_cap is not None)):
        thinking = {"tokens": min(thinking["tokens"], thinking_cap if thinking_cap is not None else 1024)}
    elif thinking:
        raise ValueError("invalid or unsupported chat thinking budget")
    elif model3 and thinking_cap is None:
        thinking = {"level": "low"}
    elif (model25 or llm.model.startswith("gemini-3")) and thinking_cap is not None:
        thinking = {"tokens": thinking_cap}
    elif thinking_cap is not None:
        raise ValueError("the model does not support a thinking token budget")
    else:
        thinking = None
    output = min(request.output_tokens, output_cap, llm.max_tokens)
    if thinking and "tokens" in thinking:
        requested_thinking = thinking["tokens"]
        thinking = {"tokens": min(thinking["tokens"], output // 2)}
        minimum = 512 if re.match(r"^gemini-.*flash-lite", llm.model) else 128
        if requested_thinking and thinking["tokens"] < minimum:
            raise ValueError("the configured output or thinking budget is below the model's minimum")
    return replace(request, input_tokens=min(request.input_tokens, input_cap), output_tokens=output, thinking=thinking)


def _part(part: ChatPart) -> dict:
    if part.metadata:
        raise ValueError("Vertex chat cannot replay unsupported connector metadata")
    if part.kind == "text":
        return {"text": part.text}
    if part.kind == "call" and part.name and part.call_id and isinstance(part.value, dict):
        return {"functionCall": {"name": part.name, "args": part.value, "id": part.call_id},
                **({"thoughtSignature": part.signature} if part.signature else {})}
    if part.kind == "result" and part.name and part.call_id:
        return {"functionResponse": {"name": part.name, "response": part.value if isinstance(part.value, dict) else {"result": part.value}, "id": part.call_id}}
    raise ValueError("invalid chat part")


def _contents(messages: tuple[ChatMessage, ...], *, unsigned_placeholder: bool = False) -> list[dict]:
    contents: list[dict] = []
    outstanding: dict[str, str] = {}
    for message in messages:
        if message.role not in ("user", "assistant", "tool") or not message.parts:
            raise ValueError("invalid chat message")
        expected = {"user": {"text"}, "assistant": {"text", "call"}, "tool": {"result"}}[message.role]
        if any(p.kind not in expected for p in message.parts):
            raise ValueError("invalid chat message parts")
        if message.role == "user" and outstanding:
            raise ValueError("unanswered tool calls")
        for part in message.parts:
            if part.kind == "call":
                if part.call_id in outstanding:
                    raise ValueError("duplicate tool call")
                outstanding[part.call_id] = part.name
            elif part.kind == "result":
                if outstanding.pop(part.call_id, None) != part.name:
                    raise ValueError("unmatched tool result")
        role = "model" if message.role == "assistant" else "user"
        parts = [_part(p) for p in message.parts]
        if unsigned_placeholder and message.role == "assistant" and not any(p.signature for p in message.parts):
            first_call = next((p for p in parts if "functionCall" in p), None)
            if first_call is not None:
                first_call["thoughtSignature"] = "skip_thought_signature_validator"
        if message.role == "tool" and contents and contents[-1]["role"] == "user" and all("functionResponse" in p for p in contents[-1]["parts"]):
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    if outstanding:
        raise ValueError("unanswered tool calls")
    return contents


def _body(request: ChatRequest, messages: tuple[ChatMessage, ...], *, unsigned_placeholder: bool = False,
          temperature: float | None = 0) -> dict:
    declarations = []
    for tool in request.tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            raise ValueError("invalid chat tool")
        declarations.append(dict(tool))
    config = {"maxOutputTokens": request.output_tokens, **({"temperature": temperature} if temperature is not None else {})}
    if request.response_format is not None:
        if request.response_format != "json":
            raise ValueError("unsupported chat response format")
        config["responseMimeType"] = "application/json"
    if request.response_schema is not None:
        if request.response_format != "json" or not isinstance(request.response_schema, dict):
            raise ValueError("a chat response schema requires JSON mode")
        config["responseJsonSchema"] = dict(checked_schema(request.response_schema))
    thinking = request.thinking or {}
    if thinking:
        if set(thinking) == {"tokens"} and isinstance(thinking["tokens"], int) and not isinstance(thinking["tokens"], bool) and thinking["tokens"] >= 0:
            config["thinkingConfig"] = {"thinkingBudget": thinking["tokens"]}
        elif set(thinking) == {"level"} and thinking["level"] in ("low", "medium", "high"):
            config["thinkingConfig"] = {"thinkingLevel": thinking["level"]}
        else:
            raise ValueError("invalid chat thinking budget")
    return {"systemInstruction": {"parts": [{"text": request.instructions}]}, "contents": _contents(messages, unsigned_placeholder=unsigned_placeholder),
            **({"tools": [{"functionDeclarations": declarations}]} if declarations else {}), "generationConfig": config}


def _groups(messages: tuple[ChatMessage, ...]) -> list[tuple[ChatMessage, ...]]:
    groups: list[list[ChatMessage]] = []
    for message in messages:
        if message.role == "user":
            groups.append([])
        if not groups:
            raise ValueError("chat must start with a user message")
        groups[-1].append(message)
    if not groups:
        raise ValueError("chat needs a user message")
    return [tuple(group) for group in groups]


def _shorten(messages: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
    return tuple(replace(m, parts=tuple(replace(p, value={"notice": "Tool result omitted to fit the input budget."})
                                           if p.kind == "result" and len(json.dumps(p.value, ensure_ascii=False)) > 4096 else p
                                           for p in m.parts)) for m in messages)


_PATH = re.compile(r"(?:\.([\w-]+)|\[(\d+)\]|\['([^'\\]*)'\]|\[\"([^\"\\]*)\"\])", re.UNICODE)


def _partial_args(target: dict, open_paths: set, partials: list) -> None:
    """Merge Vertex's streamed argument fields; unfinished strings append only at the same path."""
    for partial in partials:
        path = partial.get("jsonPath")
        if not isinstance(path, str) or not path.startswith("$"):
            raise ExternalCallError("Vertex AI returned an invalid tool argument path")
        segments, at = [], 1
        while at < len(path):
            match = _PATH.match(path, at)
            if not match:
                raise ExternalCallError("Vertex AI returned an unsupported tool argument path")
            key, index, single, double = match.groups()
            segments.append(int(index) if index is not None else next(v for v in (key, single, double) if v is not None))
            at = match.end()
        if not segments or not isinstance(segments[0], str):
            raise ExternalCallError("Vertex AI returned an invalid tool argument path")
        fields = [key for key in ("stringValue", "numberValue", "boolValue", "nullValue") if key in partial]
        if len(fields) != 1:
            raise ExternalCallError("Vertex AI returned an invalid tool argument")
        field = fields[0]
        value = None if field == "nullValue" else partial[field]
        node = target
        for i, key in enumerate(segments):
            last = i == len(segments) - 1
            if isinstance(key, str) and isinstance(node, dict):
                if last:
                    if key in node:
                        if path not in open_paths or not isinstance(node[key], str) or not isinstance(value, str):
                            raise ExternalCallError("Vertex AI returned conflicting tool arguments")
                        node[key] += value
                    else:
                        node[key] = value
                else:
                    node = node.setdefault(key, [] if isinstance(segments[i + 1], int) else {})
            elif isinstance(key, int) and isinstance(node, list) and key <= len(node) and key < 10000:
                if key == len(node):
                    node.append(value if last else ([] if isinstance(segments[i + 1], int) else {}))
                elif last:
                    if path not in open_paths or not isinstance(node[key], str) or not isinstance(value, str):
                        raise ExternalCallError("Vertex AI returned conflicting tool arguments")
                    node[key] += value
                if not last:
                    node = node[key]
            else:
                raise ExternalCallError("Vertex AI returned conflicting tool arguments")
        if partial.get("willContinue"):
            open_paths.add(path)
        else:
            open_paths.discard(path)


class _SSEStream:
    """An SSE response whose active socket can be interrupted when the caller times out."""

    def __init__(self, url: str, headers: dict, body: dict):
        self.request = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body).encode())
        self.response = None
        self.done = False
        self._accounting = None
        self._accounting_payload = {}
        self._accounting_stop = None
        self.expires = time.monotonic() + model_total_timeout()

    def __iter__(self):
        return self

    def __next__(self):
        if self.done:
            raise StopIteration
        try:
            if self.response is None:
                for attempt in range(2):
                    try:
                        if attempt:
                            from kge.adapters.model.quota import admit_retry
                            admit_retry()
                        from kge.adapters.model.accounting import start_attempt
                        self._accounting = start_attempt()
                        self.response = http.open_http(self.request, timeout=request_cap(model_timeout()))
                        break
                    except urllib.error.HTTPError as exc:
                        if self._accounting is not None:
                            self._accounting.finish(outcome="failed")
                            self._accounting = None
                        retryable = exc.code == 429 or exc.code >= 500
                        if attempt or not retryable:
                            raise ExternalCallError(f"Vertex AI error (HTTP {exc.code})", transient=retryable) from None
                        if self.done:
                            raise StopIteration
                if self.done:
                    self.response.close()
                    raise StopIteration
            lines = []
            eof = False
            while True:
                raw = self.response.readline()
                if not raw:
                    if not lines:
                        self.close(outcome="ok" if self._accounting_stop == "STOP" else "failed")
                        raise StopIteration
                    eof = True
                    break
                line = raw.decode("utf-8").rstrip("\r\n")
                if not line:
                    if lines:
                        break
                elif line.startswith("data:"):
                    lines.append(line[5:].lstrip())
            data = "\n".join(lines)
            if data == "[DONE]":
                self.close(outcome="ok" if self._accounting_stop == "STOP" else "failed")
                raise StopIteration
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("modelVersion"), str):
                    self._accounting_payload["modelVersion"] = parsed["modelVersion"]
                if isinstance(parsed.get("usageMetadata"), dict):
                    self._accounting_payload.setdefault("usageMetadata", {}).update(parsed["usageMetadata"])
                for candidate in parsed.get("candidates") or []:
                    if isinstance(candidate, dict) and candidate.get("finishReason"):
                        self._accounting_stop = candidate["finishReason"]
            if eof:
                self.close(outcome="ok" if self._accounting_stop == "STOP" else "failed")
            return parsed
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            self.close(outcome="failed")
            raise ExternalCallError(f"Vertex AI streaming failed ({type(exc).__name__})", transient=True) from None
        except ValueError as exc:
            self.close(outcome="failed")
            raise ExternalCallError(f"Vertex AI streaming failed ({type(exc).__name__})") from None

    def close(self, *, outcome="cancelled"):
        self.done = True
        accounting, self._accounting = self._accounting, None
        response = self.response
        raw = getattr(getattr(response, "fp", None), "raw", None) if response is not None else None
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if response is not None:
            response.close()
        if accounting is not None:
            def settle():
                accounting.finish(payload=self._accounting_payload, outcome=outcome)
            if outcome == "cancelled" or time.monotonic() >= self.expires:
                from threading import Thread
                Thread(target=settle, daemon=True, name="model-usage-finalize").start()
            else:
                _bounded(settle, self.expires)


def _stream_http(url: str, headers: dict, body: dict) -> Iterator[dict]:
    return _SSEStream(url, headers, body)


def _bounded(call, expires: float):
    remaining = expires - time.monotonic()
    if remaining <= 0:
        raise ExternalCallError("Model call deadline exceeded")
    with request_window(remaining):
        return request_model_call(call)


def stream(llm, request: ChatRequest) -> Iterator[ChatEvent]:
    """Send a Vertex chat; never retry a stream after yielding output."""
    if llm.provider == "vertex-claude":
        from kge.adapters.model.claude_chat import stream as claude_stream
        yield from claude_stream(llm, request)
        return
    started = time.perf_counter()
    expires = time.monotonic() + model_total_timeout()
    served = None
    tokens_in = tokens_out = tokens_thinking = None
    reduced = 0
    finish = None
    ok = False
    error = "cancelled"
    source = None
    final_event = None
    pending: dict[str, dict] = {}
    unnamed: set[str] = set()
    json_text: list[str] = []
    saw_call = False
    purpose = request.purpose if request.purpose in ("agent", "catalogue") else "chat"
    thinking = request.thinking if (isinstance(request.thinking, dict) and
                                    ((set(request.thinking) == {"level"} and request.thinking["level"] in ("low", "medium", "high")) or
                                     (set(request.thinking) == {"tokens"} and isinstance(request.thinking["tokens"], int)
                                      and not isinstance(request.thinking["tokens"], bool) and request.thinking["tokens"] >= 0))) else {}
    try:
        if llm.provider != "vertex-gemini":
            raise ChatUnsupported("The configured model connector does not support streamed chat")
        if (not isinstance(request.input_tokens, int) or isinstance(request.input_tokens, bool) or request.input_tokens <= 0
                or not isinstance(request.output_tokens, int) or isinstance(request.output_tokens, bool) or request.output_tokens <= 0):
            raise ValueError("chat budgets must be positive whole numbers")
        request = governed(llm, request)
        thinking = request.thinking or {}
        groups = _groups(request.messages)
        _contents(request.messages)
        url, headers = _bounded(llm.chat._gemini_target, expires)
        count_url = url.replace(":generateContent", ":countTokens")
        while True:
            messages = tuple(m for group in groups for m in group)
            body = _body(request, messages, unsigned_placeholder=bool(re.match(r"^gemini-(?:[3-9]|\d{2,})", llm.model)),
                         temperature=llm.temperature)
            from kge.adapters.model.quota import without_retry_admission
            from kge.adapters.model.accounting import without_inference
            with without_retry_admission(), without_inference():
                counted = _bounded(lambda: llm._post(count_url, headers, body), expires)
            tokens_in = counted.get("totalTokens")
            if not isinstance(tokens_in, int) or isinstance(tokens_in, bool) or tokens_in < 0:
                raise ExternalCallError("Vertex AI did not return an input token count")
            if tokens_in <= request.input_tokens:
                break
            if len(groups) == 1:
                raise ChatInputLimit("The latest exchange exceeds the input token budget")
            shortened = _shorten(groups[0])
            if shortened != groups[0]:
                groups[0] = shortened
            else:
                groups.pop(0)
            reduced += 1
        stream_url = url.replace(":generateContent", ":streamGenerateContent?alt=sse")
        source = iter(llm._stream_post(stream_url, headers, body))
        if isinstance(source, _SSEStream):
            source.expires = expires
        sentinel = object()
        while True:
            data = _bounded(lambda: next(source, sentinel), expires)
            if data is sentinel:
                break
            if not isinstance(data, dict):
                raise ExternalCallError("Vertex AI returned an invalid stream event")
            served = data.get("modelVersion") or served
            usage = data.get("usageMetadata") or {}
            tokens_in = usage.get("promptTokenCount", tokens_in)
            tokens_thinking = usage.get("thoughtsTokenCount", tokens_thinking)
            if usage.get("candidatesTokenCount") is not None:
                tokens_out = usage["candidatesTokenCount"] + (tokens_thinking or 0)
            candidate = next(iter(data.get("candidates") or []), {})
            finish = candidate.get("finishReason") or finish
            for part in (candidate.get("content") or {}).get("parts") or []:
                if part.get("thought"):
                    continue
                if isinstance(part.get("text"), str):
                    if request.response_format == "json":
                        json_text.append(part["text"])
                    else:
                        yield ChatEvent(part=ChatPart(kind="text", text=part["text"]))
                if isinstance(part.get("functionCall"), dict):
                    call = part["functionCall"]
                    call_id = call.get("id")
                    if call_id is not None and (not isinstance(call_id, str) or not call_id):
                        raise ExternalCallError("Vertex AI returned an invalid tool call")
                    if call_id is None:
                        if len(unnamed) > 1:
                            raise ExternalCallError("Vertex AI returned ambiguous streamed tool calls")
                        if unnamed and call.get("name"):
                            raise ExternalCallError("Vertex AI returned ambiguous streamed tool calls without ids")
                        call_id = next(iter(unnamed), None) or str(uuid.uuid4())
                    state = pending.get(call_id)
                    if state is None:
                        if not isinstance(call.get("name"), str) or not call["name"]:
                            raise ExternalCallError("Vertex AI returned an invalid tool call")
                        state = {"name": call["name"], "args": {}, "signature": None, "paths": set()}
                        pending[call_id] = state
                        if "id" not in call:
                            unnamed.add(call_id)
                    elif call.get("name") and call["name"] != state["name"]:
                        raise ExternalCallError("Vertex AI returned conflicting tool calls")
                    if "args" in call:
                        if not isinstance(call["args"], dict) or state["args"] or "partialArgs" in call:
                            raise ExternalCallError("Vertex AI returned conflicting tool arguments")
                        state["args"] = call["args"]
                    if "partialArgs" in call:
                        if not isinstance(call["partialArgs"], list):
                            raise ExternalCallError("Vertex AI returned invalid tool arguments")
                        _partial_args(state["args"], state["paths"], call["partialArgs"])
                    state["signature"] = part.get("thoughtSignature") or state["signature"]
                    if not call.get("willContinue"):
                        if state["paths"]:
                            raise ExternalCallError("Vertex AI returned incomplete tool arguments")
                        yield ChatEvent(part=ChatPart(kind="call", call_id=call_id, name=state["name"],
                                                       value=state["args"], signature=state["signature"]))
                        saw_call = True
                        pending.pop(call_id)
                        unnamed.discard(call_id)
            if data.get("promptFeedback", {}).get("blockReason"):
                raise ExternalCallError("Vertex AI refused the chat request")
        if pending:
            raise ExternalCallError("Vertex AI returned incomplete tool calls")
        if finish == "MAX_TOKENS":
            from kge.adapters.model.providers import Truncated
            raise Truncated(request.output_tokens, "chat output token budget")
        if finish != "STOP":
            raise ExternalCallError("Vertex AI returned an incomplete chat stream")
        if request.response_format == "json":
            if json_text:
                text = "".join(json_text)
                try:
                    value = json.loads(text)
                    if request.response_schema is not None:
                        from jsonschema import Draft202012Validator
                        Draft202012Validator(request.response_schema).validate(value)
                except Exception:
                    raise ExternalCallError("Vertex AI returned invalid structured chat output") from None
                yield ChatEvent(part=ChatPart(kind="text", text=text))
            elif not saw_call:
                raise ExternalCallError("Vertex AI returned empty structured chat output")
        ok, error = True, None
        final_event = ChatEvent(model=served, tokens_in=tokens_in, tokens_out=tokens_out, tokens_thinking=tokens_thinking,
                                finish=finish, reduced=reduced)
    except GeneratorExit:
        raise
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        if source is not None and hasattr(source, "close"):
            try:
                source.close()
            except Exception:
                pass
        trace.llm_call(llm.name, served, tokens_in, tokens_out, (time.perf_counter() - started) * 1000,
                       ok=ok, error=error, detail={"call": purpose, "chat_purpose": purpose, "input_budget": request.input_tokens if isinstance(request.input_tokens, int) else None,
                                                  "max_tokens": request.output_tokens if isinstance(request.output_tokens, int) else None, "thinking": dict(thinking),
                                                  "tokens_thinking": tokens_thinking, "finish": finish, "reduced": reduced})
    yield final_event
