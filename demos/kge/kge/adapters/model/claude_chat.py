"""Claude on Vertex through the chat port; no model SDK objects escape the adapter."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from typing import Iterator

from kge.adapters.model.chat import ChatInputLimit, _bounded, _groups, _shorten, chat_policy, checked_schema
from kge.adapters.model.continuation import ContinuationInvalid, current_continuation, prefix_digest
from kge.adapters.model.errors import ExternalCallError, model_timeout, model_total_timeout
from kge.pipeline import trace
from kge.ports import ChatEvent, ChatMessage, ChatPart, ChatRequest


def _value(value):
    return value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value


def _binding(request: ChatRequest, model: str, previous: list, sample: float | None, recipe: dict):
    scope = current_continuation()
    if scope is None:
        raise ExternalCallError("The model's private continuation is unavailable")
    codec, binding = scope
    if binding.provider != "vertex-claude" or binding.model != model or binding.purpose != request.purpose:
        raise ContinuationInvalid()
    prefix = prefix_digest({"instructions": request.instructions, "tools": request.tools, "messages": previous,
                            "thinking": request.thinking, "response_format": request.response_format,
                            "response_schema": request.response_schema, "max_tokens": request.output_tokens,
                            "temperature": sample, "reduction": recipe})
    return codec, replace(binding, prefix=prefix)


def _visible(parts: list[dict]) -> tuple[str, list[dict]]:
    """Compare the visible text and ordered tool calls despite chat frameworks combining text blocks."""
    text = []
    calls = []
    for part in parts:
        if part.get("type") == "text":
            text.append(part.get("text", ""))
        elif part.get("type") == "tool_use":
            calls.append(dict(part))
    return "".join(text), calls


def _visible_bytes(parts: list[dict]) -> bytes:
    """Compare JSON values without Python's boolean/integer equality coercion."""
    def keys_are_strings(value):
        if isinstance(value, dict):
            return all(isinstance(key, str) and keys_are_strings(item) for key, item in value.items())
        if isinstance(value, list):
            return all(keys_are_strings(item) for item in value)
        return True
    visible = _visible(parts)
    if not keys_are_strings(visible[1]):
        raise ValueError("invalid tool arguments")
    return json.dumps(visible, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _checked_recipe(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"drop", "omit"}:
        raise ContinuationInvalid()
    drop, omissions = value["drop"], value["omit"]
    if not isinstance(drop, int) or isinstance(drop, bool) or drop < 0 or drop > 200 or not isinstance(omissions, list) or len(omissions) > 200:
        raise ContinuationInvalid()
    seen = set()
    for item in omissions:
        if not isinstance(item, dict) or set(item) != {"turn", "message", "part", "call_id"}:
            raise ContinuationInvalid()
        if any(not isinstance(item[key], int) or isinstance(item[key], bool) or item[key] < 0 or item[key] > 200
               for key in ("turn", "message", "part")):
            raise ContinuationInvalid()
        if not isinstance(item["call_id"], str) or not item["call_id"] or len(item["call_id"].encode("utf-8")) > 512:
            raise ContinuationInvalid()
        slot = (item["turn"], item["message"], item["part"])
        if slot in seen:
            raise ContinuationInvalid()
        seen.add(slot)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 4096:
        raise ContinuationInvalid()
    return {"drop": drop, "omit": [dict(item) for item in omissions]}


def _model_groups(messages: tuple[ChatMessage, ...]):
    """Remove private continuations from completed turns, retain them during the active tool-use turn."""
    groups = _groups(messages)
    for i, group in enumerate(groups[:-1]):
        groups[i] = tuple(replace(message, parts=tuple(replace(part, metadata=None)
                                if part.kind == "call" and isinstance(part.metadata, dict)
                                and "claude_continuation" in part.metadata else part for part in message.parts))
                          for message in group)
    return groups


def _active_recipe(group: tuple[ChatMessage, ...]) -> dict:
    found = []
    for message in group:
        for part in message.parts:
            if isinstance(part.metadata, dict) and "claude_continuation" in part.metadata:
                found.append(_checked_recipe(part.metadata.get("reduction")))
    if found and any(item != found[0] for item in found[1:]):
        raise ContinuationInvalid()
    return found[0] if found else {"drop": 0, "omit": []}


def _apply_recipe(groups, recipe: dict):
    """Apply a validated recipe only to prior turns, leaving saved messages and the active turn whole."""
    recipe = _checked_recipe(recipe)
    if recipe["drop"] >= len(groups):
        raise ContinuationInvalid()
    groups = list(groups)
    for item in recipe["omit"]:
        turn, msg, index = item["turn"], item["message"], item["part"]
        if turn >= len(groups) - 1 or msg >= len(groups[turn]) or index >= len(groups[turn][msg].parts):
            raise ContinuationInvalid()
        message = groups[turn][msg]
        part = message.parts[index]
        if part.kind != "result" or part.call_id != item["call_id"] or len(json.dumps(part.value, ensure_ascii=False)) <= 4096:
            raise ContinuationInvalid()
        changed = replace(part, value={"notice": "Tool result omitted to fit the input budget."})
        parts = list(message.parts)
        parts[index] = changed
        changed_message = replace(message, parts=tuple(parts))
        group = list(groups[turn])
        group[msg] = changed_message
        groups[turn] = tuple(group)
    return groups[recipe["drop"]:]


def _replay(request: ChatRequest, model: str, previous: list, visible: list, token: str, sample: float | None, recipe: dict) -> list:
    codec, binding = _binding(request, model, previous, sample, recipe)
    try:
        data = json.loads(codec.open(token, binding))
        if (not isinstance(data, list) or not all(isinstance(p, dict) and p.get("type") in
                ("text", "tool_use", "thinking", "redacted_thinking") for p in data)
                or _visible_bytes(data) != _visible_bytes(visible)):
            raise ContinuationInvalid()
    except (ValueError, TypeError, UnicodeError):
        raise ContinuationInvalid() from None
    return data


def _body(request: ChatRequest, messages: tuple[ChatMessage, ...], model: str, *, sample: float | None,
          recipe: dict | None = None) -> dict:
    """Anthropic's message format: tool results belong to the following user message."""
    if request.response_format not in (None, "json") or (request.response_schema is not None and request.response_format != "json"):
        raise ValueError("invalid chat response format")
    if request.response_format == "json" and not isinstance(request.response_schema, dict):
        raise ValueError("Claude JSON output needs a response schema")
    out = []
    pending: dict[str, str] = {}
    for message in messages:
        if message.role not in ("user", "assistant", "tool") or not message.parts:
            raise ValueError("invalid chat message")
        allowed = {"user": {"text"}, "assistant": {"text", "call"}, "tool": {"result"}}[message.role]
        if any(part.kind not in allowed for part in message.parts):
            raise ValueError("invalid chat message parts")
        if message.role == "user" and pending:
            raise ValueError("unanswered tool calls")
        parts = []
        continuation = None
        continuation_recipe = None
        for part in message.parts:
            if part.kind == "text":
                if part.metadata or part.signature:
                    raise ValueError("unsupported text metadata")
                parts.append({"type": "text", "text": part.text})
            elif part.kind == "call":
                if not part.name or not part.call_id or not isinstance(part.value, dict) or part.call_id in pending:
                    raise ValueError("invalid or duplicate tool call")
                if part.signature:
                    raise ValueError("unsupported tool-call metadata")
                if part.metadata:
                    if (continuation is not None or pending or set(part.metadata) != {"claude_continuation", "reduction"}
                            or not isinstance(part.metadata["claude_continuation"], str)):
                        raise ValueError("unsupported tool-call metadata")
                    continuation = part.metadata["claude_continuation"]
                    continuation_recipe = _checked_recipe(part.metadata["reduction"])
                pending[part.call_id] = part.name
                parts.append({"type": "tool_use", "id": part.call_id, "name": part.name, "input": part.value})
            else:
                if not part.call_id or pending.pop(part.call_id, None) != part.name or part.metadata or part.signature:
                    raise ValueError("unmatched tool result")
                content = part.value if isinstance(part.value, str) else json.dumps(part.value, ensure_ascii=False)
                parts.append({"type": "tool_result", "tool_use_id": part.call_id, "content": content})
        if message.role == "assistant":
            if continuation is not None:
                if recipe is not None and continuation_recipe != recipe:
                    raise ContinuationInvalid()
                parts = _replay(request, model, out, parts, continuation, sample, continuation_recipe)
            out.append({"role": "assistant", "content": parts})
        elif message.role == "tool":
            if out and out[-1]["role"] == "user" and all(p.get("type") == "tool_result" for p in out[-1]["content"]):
                out[-1]["content"].extend(parts)
            else:
                out.append({"role": "user", "content": parts})
        else:
            out.append({"role": "user", "content": parts})
    if pending:
        raise ValueError("unanswered tool calls")
    tools = []
    for tool in request.tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"] or not isinstance(tool.get("parameters"), dict):
            raise ValueError("invalid chat tool")
        tools.append({"name": tool["name"], "description": tool.get("description", ""), "input_schema": tool["parameters"]})
    fields = {"model": model, "system": request.instructions, "messages": out, **({"tools": tools} if tools else {})}
    fields["max_tokens"] = request.output_tokens
    if sample is not None:
        fields["temperature"] = sample
    if request.thinking:
        if set(request.thinking) != {"level"} or request.thinking["level"] not in ("low", "medium", "high"):
            raise ValueError("unsupported Claude thinking budget")
        fields["output_config"] = {"effort": request.thinking["level"]}
    if request.response_format == "json":
        from kge.adapters.model.providers import provider_schema
        schema = dict(checked_schema(request.response_schema))
        fields["output_config"] = {**fields.get("output_config", {}), "format": {"type": "json_schema", "schema": provider_schema(schema, "vertex-claude")}}
    return fields


def _sdk_call(fn, expires):
    """Keep SDK errors free of provider payloads and credentials."""
    try:
        return _bounded(fn, expires)
    except ExternalCallError:
        raise
    except Exception as exc:
        from anthropic import APIConnectionError, APIStatusError
        if isinstance(exc, APIStatusError):
            code = exc.status_code
            raise ExternalCallError(f"Vertex AI error (HTTP {code})", transient=code == 429 or code >= 500) from None
        if isinstance(exc, (APIConnectionError, TimeoutError, ConnectionError)):
            raise ExternalCallError("Vertex AI unreachable", transient=True) from None
        raise ExternalCallError("Vertex AI chat failed") from None


def _retry_sdk(fn, expires, *, quota_retry=False):
    """One retry for a transient failure before the stream begins."""
    for attempt in range(2):
        if attempt and quota_retry:
            from kge.adapters.model.quota import admit_retry
            admit_retry()
        try:
            return _sdk_call(fn, expires)
        except ExternalCallError as exc:
            if attempt or not exc.transient:
                raise


def _close(manager):
    """Start cleanup without letting an SDK close outlive the caller's deadline."""
    def release():
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass
    thread = threading.Thread(target=release, daemon=True, name="claude-stream-close")
    thread.start()
    thread.join(.01)


def _policy(llm, request):
    if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in (request.input_tokens, request.output_tokens)):
        raise ValueError("chat budgets must be positive whole numbers")
    input_cap, output_cap, thinking_tokens, thinking_level = chat_policy(request.purpose)
    if thinking_tokens is not None:
        raise ValueError("Claude chat does not support a configured thinking token cap")
    thinking = request.thinking
    from kge.adapters.model.providers import thinking_support
    supports_level = thinking_support(llm.provider, llm.model)[1]
    if thinking:
        if set(thinking) != {"level"} or thinking["level"] not in ("low", "medium", "high"):
            raise ValueError("unsupported Claude thinking budget")
        if not supports_level:
            raise ValueError("the model does not support a thinking level")
        levels = ("low", "medium", "high")
        thinking = {"level": levels[min(levels.index(thinking["level"]), levels.index(thinking_level))]}
    elif supports_level:
        thinking = {"level": thinking_level}
    return replace(request, input_tokens=min(request.input_tokens, input_cap), output_tokens=min(request.output_tokens, output_cap, llm.max_tokens), thinking=thinking)


def stream(llm, request: ChatRequest) -> Iterator[ChatEvent]:
    """Count the complete request, stream and trace exactly once; never retry emitted output."""
    started = time.perf_counter()
    expires = time.monotonic() + model_total_timeout()
    served = finish = None
    tokens_in = tokens_out = None
    reduced = 0
    ok, error = False, "cancelled"
    manager = source = tracked = None
    handoff_lock = threading.Lock()
    handoff = {"closed": False, "manager": None}
    openings = 0
    final = None
    try:
        request = _policy(llm, request)
        original_groups = _model_groups(request.messages)
        recipe = _active_recipe(original_groups[-1])
        groups = _apply_recipe(original_groups, recipe)
        reduced = recipe["drop"] + len(recipe["omit"])
        _body(request, tuple(m for group in groups for m in group), llm.model, sample=llm.temperature, recipe=recipe)
        client = _sdk_call(llm.chat.anthropic_vertex, expires)
        while True:
            messages = tuple(m for group in groups for m in group)
            body = _body(request, messages, llm.model, sample=llm.temperature, recipe=recipe)
            count_body = {k: v for k, v in body.items() if k not in ("max_tokens", "temperature")}
            count = _value(_retry_sdk(lambda: client.messages.count_tokens(**count_body, timeout=min(model_timeout(), max(.001, expires - time.monotonic()))), expires))
            tokens_in = count.get("input_tokens") if isinstance(count, dict) else None
            if not isinstance(tokens_in, int) or isinstance(tokens_in, bool) or tokens_in < 0:
                raise ExternalCallError("Vertex AI did not return an input token count")
            if tokens_in <= request.input_tokens:
                break
            if len(groups) == 1:
                raise ChatInputLimit("The latest exchange exceeds the input token budget")
            if any(part.metadata and "claude_continuation" in part.metadata
                   for group in groups for message in group for part in message.parts):
                raise ChatInputLimit("The signed conversation cannot be safely shortened to fit the input budget")
            shortened = _shorten(groups[0])
            if shortened != groups[0]:
                for i, (before, after) in enumerate(zip(groups[0], shortened)):
                    for j, (old, new) in enumerate(zip(before.parts, after.parts)):
                        if old != new:
                            recipe["omit"].append({"turn": recipe["drop"], "message": i, "part": j, "call_id": old.call_id})
                groups[0] = shortened
            else:
                groups.pop(0)
                recipe["drop"] += 1
            try:
                _checked_recipe(recipe)
            except ContinuationInvalid:
                raise ChatInputLimit("The conversation cannot be safely shortened to fit the input budget") from None
            reduced += 1
        def open_stream():
            nonlocal manager, openings, tracked
            from kge.adapters.model.accounting import start_attempt
            openings += 1
            tracked = start_attempt()
            opened = None
            try:
                opened = client.messages.stream(**body, timeout=min(model_timeout(), max(.001, expires - time.monotonic())))
                stream_source = opened.__enter__()
            except BaseException:
                if tracked is not None:
                    tracked.finish(outcome="failed")
                if opened is not None:
                    _close(opened)
                raise
            with handoff_lock:
                if handoff["closed"]:
                    _close(opened)
                    raise ExternalCallError("Model call deadline exceeded")
                handoff["manager"] = opened
                manager = opened
            return stream_source
        source = _retry_sdk(open_stream, expires, quota_retry=True)
        iterator = iter(source)
        sentinel = object()
        blocks = {}
        in_usage = {}
        stopped = False
        started_message = False
        emitted = False
        json_text = []
        while True:
            try:
                data = _value(_sdk_call(lambda: next(iterator, sentinel), expires))
            except ExternalCallError as exc:
                if emitted or openings >= 2 or not exc.transient:
                    raise
                if tracked is not None:
                    tracked.finish(outcome="failed")
                _close(manager)
                from kge.adapters.model.quota import admit_retry
                admit_retry()
                source = _sdk_call(open_stream, expires)
                iterator = iter(source)
                blocks, in_usage = {}, {}
                json_text = []
                stopped = started_message = False
                served = finish = tokens_out = None
                continue
            if data is sentinel:
                break
            if not isinstance(data, dict):
                raise ExternalCallError("Vertex AI returned an invalid stream event")
            typ = data.get("type")
            if stopped:
                raise ExternalCallError("Vertex AI returned an event after chat completion")
            if typ == "message_start":
                if started_message:
                    raise ExternalCallError("Vertex AI returned duplicate chat starts")
                started_message = True
                message = data.get("message") or {}
                served = message.get("model") or served
                in_usage = message.get("usage") or {}
                count = in_usage.get("input_tokens")
                if isinstance(count, int):
                    tokens_in = count + (in_usage.get("cache_creation_input_tokens") or 0) + (in_usage.get("cache_read_input_tokens") or 0)
            elif typ == "content_block_start":
                index, block = data.get("index"), data.get("content_block")
                if not started_message or not isinstance(index, int) or index in blocks or not isinstance(block, dict):
                    raise ExternalCallError("Vertex AI returned an invalid chat block")
                if block.get("type") not in ("text", "tool_use", "thinking", "redacted_thinking"):
                    raise ExternalCallError("Vertex AI returned an unsupported chat block")
                public_block = {"type": block["type"]} if block["type"] in ("thinking", "redacted_thinking") else dict(block)
                blocks[index] = {"block": public_block, "private": dict(block) if block["type"] in ("thinking", "redacted_thinking") else None,
                                 "json": "", "closed": False}
                if block.get("type") == "text" and block.get("text"):
                    if not isinstance(block["text"], str):
                        raise ExternalCallError("Vertex AI returned invalid chat text")
                    if request.response_format == "json":
                        json_text.append(block["text"])
                    else:
                        emitted = True
                        yield ChatEvent(part=ChatPart(kind="text", text=block["text"]))
            elif typ == "content_block_delta":
                state = blocks.get(data.get("index"))
                delta = data.get("delta") or {}
                if state is None or state["closed"]:
                    raise ExternalCallError("Vertex AI returned an invalid chat delta")
                block = state["block"]
                kind = delta.get("type")
                if kind == "text_delta" and block.get("type") == "text" and isinstance(delta.get("text"), str):
                    text = delta["text"]
                    block["text"] = block.get("text", "") + text
                    if request.response_format == "json":
                        json_text.append(text)
                    else:
                        emitted = True
                        yield ChatEvent(part=ChatPart(kind="text", text=text))
                elif kind == "input_json_delta" and block.get("type") == "tool_use" and isinstance(delta.get("partial_json"), str):
                    state["json"] += delta["partial_json"]
                elif kind == "thinking_delta" and block.get("type") == "thinking" and isinstance(delta.get("thinking"), str):
                    state["private"]["thinking"] = state["private"].get("thinking", "") + delta["thinking"]
                elif kind == "signature_delta" and block.get("type") == "thinking" and isinstance(delta.get("signature"), str):
                    state["private"]["signature"] = state["private"].get("signature", "") + delta["signature"]
                else:
                    raise ExternalCallError("Vertex AI returned an invalid chat delta")
            elif typ == "content_block_stop":
                state = blocks.get(data.get("index"))
                if state is None or state["closed"]:
                    raise ExternalCallError("Vertex AI returned an invalid chat block")
                state["closed"] = True
                if state["block"].get("type") == "tool_use" and state["json"]:
                    if state["block"].get("input") not in (None, {}):
                        raise ExternalCallError("Vertex AI returned conflicting tool arguments")
                    try:
                        state["block"]["input"] = json.loads(state["json"])
                    except ValueError:
                        raise ExternalCallError("Vertex AI returned invalid tool arguments") from None
            elif typ == "message_delta":
                finish = (data.get("delta") or {}).get("stop_reason") or finish
                usage = data.get("usage") or {}
                tokens_out = usage.get("output_tokens", tokens_out)
            elif typ == "message_stop":
                stopped = True
            elif typ in ("ping", "text", "input_json", "thinking", "signature"):
                continue
            elif typ == "error":
                raise ExternalCallError("Vertex AI streaming failed")
            else:
                raise ExternalCallError("Vertex AI returned an unsupported chat event")
        if not stopped or not blocks or any(not state["closed"] for state in blocks.values()):
            raise ExternalCallError("Vertex AI returned an incomplete chat stream")
        if finish == "max_tokens":
            from kge.adapters.model.providers import Truncated
            raise Truncated(request.output_tokens, "chat output token budget")
        if finish not in ("end_turn", "tool_use"):
            raise ExternalCallError("Vertex AI returned an incomplete chat stream")
        replay = [blocks[i]["block"] for i in sorted(blocks)]
        calls = [b for b in replay if b.get("type") == "tool_use"]
        if bool(calls) != (finish == "tool_use"):
            raise ExternalCallError("Vertex AI returned an invalid tool stop")
        if len({b.get("id") for b in calls}) != len(calls):
            raise ExternalCallError("Vertex AI returned duplicate tool calls")
        if tracked is not None:
            tracked.finish(payload={"model": served, "usage": {**in_usage, "output_tokens": tokens_out}}, outcome="ok")
        has_private = any(b.get("type") in ("thinking", "redacted_thinking") for b in replay)
        token = None
        if calls and has_private:
            codec, binding = _binding(request, llm.model, body["messages"], llm.temperature, recipe)
            private_replay = [blocks[i]["private"] if blocks[i]["private"] is not None else blocks[i]["block"] for i in sorted(blocks)]
            try:
                token = codec.seal(json.dumps(private_replay, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"), binding)
            except (ValueError, TypeError, UnicodeError):
                raise ExternalCallError("The model's private continuation could not be protected") from None
        if request.response_format == "json" and not calls:
            from jsonschema import exceptions as schema_errors
            from jsonschema import Draft202012Validator
            rendered = "".join(json_text)
            try:
                value = json.loads(rendered)
                Draft202012Validator(dict(request.response_schema)).validate(value)
            except (ValueError, TypeError, schema_errors.ValidationError, schema_errors.SchemaError):
                raise ExternalCallError("Vertex AI returned an invalid structured answer") from None
            yield ChatEvent(part=ChatPart(kind="text", text=rendered))
        for index, call in enumerate(calls):
            if not call.get("id") or not call.get("name") or not isinstance(call.get("input"), dict):
                raise ExternalCallError("Vertex AI returned invalid tool arguments")
            yield ChatEvent(part=ChatPart(kind="call", call_id=call["id"], name=call["name"], value=call["input"],
                                          metadata={"claude_continuation": token, "reduction": recipe} if index == 0 and token else None))
        ok, error = True, None
        final = ChatEvent(model=served, tokens_in=tokens_in, tokens_out=tokens_out, tokens_thinking=None, finish="STOP", reduced=reduced)
    except GeneratorExit:
        raise
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        with handoff_lock:
            handoff["closed"] = True
            manager = handoff["manager"]
        if manager is not None:
            _close(manager)
        if tracked is not None:
            tracked.finish(payload={"model": served, "usage": {**(in_usage if isinstance(in_usage, dict) else {}),
                                                               "output_tokens": tokens_out}} if "in_usage" in locals() else None,
                           outcome="cancelled" if error == "cancelled" else "failed")
        purpose = request.purpose if request.purpose in ("agent", "catalogue") else "chat"
        thinking = request.thinking if (isinstance(request.thinking, dict) and
                                       ((set(request.thinking) == {"level"} and request.thinking["level"] in ("low", "medium", "high")) or
                                        (set(request.thinking) == {"tokens"} and isinstance(request.thinking["tokens"], int)
                                         and not isinstance(request.thinking["tokens"], bool) and request.thinking["tokens"] >= 0))) else {}
        trace.llm_call(llm.name, served, tokens_in, tokens_out, (time.perf_counter() - started) * 1000,
                       ok=ok, error=error, detail={"call": purpose, "chat_purpose": purpose,
                                                  "input_budget": request.input_tokens if isinstance(request.input_tokens, int) and not isinstance(request.input_tokens, bool) else None,
                                                  "max_tokens": request.output_tokens if isinstance(request.output_tokens, int) and not isinstance(request.output_tokens, bool) else None,
                                                  "thinking": dict(thinking),
                                                  "tokens_thinking": None, "finish": {"end_turn": "STOP", "tool_use": "STOP", "max_tokens": "MAX_TOKENS"}.get(finish),
                                                  "reduced": reduced})
    yield final
