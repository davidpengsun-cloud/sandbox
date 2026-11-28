"""The gateway's provider-neutral request; identity and model selection are never client fields."""
from __future__ import annotations

import json
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, UUID4, model_validator

from kge.ports import ChatMessage, ChatPart, ChatRequest

MAX_CONTINUATION_CHARS = 400_000


def valid_continuation(metadata: Any) -> bool:
    """Accept only opaque replay and a bounded structural reduction recipe."""
    if not isinstance(metadata, dict) or set(metadata) not in ({"claude_continuation"}, {"claude_continuation", "reduction"}):
        return False
    token = metadata["claude_continuation"]
    if not isinstance(token, str) or not 1 <= len(token) <= MAX_CONTINUATION_CHARS:
        return False
    if "reduction" not in metadata:
        return True
    recipe = metadata["reduction"]
    if not isinstance(recipe, dict) or set(recipe) != {"drop", "omit"}:
        return False
    drop, items = recipe["drop"], recipe["omit"]
    if not isinstance(drop, int) or isinstance(drop, bool) or not 0 <= drop <= 200 or not isinstance(items, list) or len(items) > 200:
        return False
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"turn", "message", "part", "call_id"}:
            return False
        if any(not isinstance(item[k], int) or isinstance(item[k], bool) or not 0 <= item[k] <= 200 for k in ("turn", "message", "part")):
            return False
        if not isinstance(item["call_id"], str) or not item["call_id"] or len(item["call_id"].encode("utf-8")) > 512:
            return False
        slot = (item["turn"], item["message"], item["part"])
        if slot in seen:
            return False
        seen.add(slot)
    return len(json.dumps(recipe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 4096


def local_references(value: Any) -> bool:
    """A caller's schema never causes a model service to fetch an external reference."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef", "$id"}:
                if not isinstance(item, str) or not item.startswith("#"):
                    return False
            elif key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"} and isinstance(item, dict):
                if not all(local_references(child) for child in item.values()):
                    return False
            elif key not in {"enum", "const", "default", "examples"} and not local_references(item):
                return False
        return True
    if isinstance(value, list):
        return all(local_references(item) for item in value)
    return True


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Part(Body):
    kind: Literal["text", "call", "result"]
    text: str = Field(default="", max_length=1_000_000)
    call_id: str = Field(default="", max_length=256)
    name: str = Field(default="", max_length=256)
    value: Any = None
    signature: str | None = Field(default=None, max_length=100_000)
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def valid_kind(self):
        if self.kind == "text" and (self.call_id or self.name or self.value is not None or self.signature or self.metadata):
            raise ValueError("text parts cannot hold a tool call")
        if self.kind in ("call", "result") and (not self.call_id or not self.name or self.text):
            raise ValueError("tool parts need their call id and name")
        if self.kind == "call" and not isinstance(self.value, dict):
            raise ValueError("tool arguments must be an object")
        if self.metadata is not None and not valid_continuation(self.metadata):
            raise ValueError("unsupported continuation metadata")
        if self.kind == "result" and (self.signature or self.metadata):
            raise ValueError("tool results cannot hold provider replay metadata")
        return self


class Message(Body):
    role: Literal["user", "assistant", "tool"]
    parts: list[Part] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_role(self):
        allowed = {"user": {"text"}, "assistant": {"text", "call"}, "tool": {"result"}}[self.role]
        if any(part.kind not in allowed for part in self.parts):
            raise ValueError("part is not valid for this message role")
        return self


class Tool(Body):
    name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    description: str = Field(default="", max_length=4000)
    parameters: dict[str, Any]


class QuestionBody(Body):
    purpose: Literal["agent", "catalogue"]
    question_id: UUID4
    conversation_id: UUID4
    expected_model: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class QuestionReply(Body):
    question_id: UUID4
    correlation_token: str


class ReservationReply(Body):
    invocation_id: UUID4


class FinishBody(QuestionBody):
    no_inference: Literal["deterministic_cancelled"] | None = None


class UsageReply(Body):
    calls: StrictInt | None = Field(default=None, ge=0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    thinking_tokens: StrictInt | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    complete: StrictBool = False


class ChatBody(QuestionBody):
    instructions: str = Field(min_length=1, max_length=100_000)
    messages: list[Message] = Field(min_length=1, max_length=200)
    tools: list[Tool] = Field(default_factory=list, max_length=32)
    response_format: Literal["json"] | None = None
    response_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def valid_request(self):
        if self.response_schema is not None:
            if self.response_format != "json":
                raise ValueError("a response schema requires JSON response format")
            if len(json.dumps(self.response_schema).encode("utf-8")) > 65_536:
                raise ValueError("response schema exceeds its size limit")
            if not local_references(self.response_schema):
                raise ValueError("response schema may use local references only")
            try:
                jsonschema.Draft202012Validator.check_schema(self.response_schema)
            except jsonschema.SchemaError:
                raise ValueError("response schema is invalid") from None
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("tool names must be unique")
        if self.messages[0].role != "user":
            raise ValueError("the first chat message must be from the user")
        pending: dict[str, str] = {}
        for message in self.messages:
            if message.role == "user" and pending:
                raise ValueError("tool calls need results before the next user message")
            for part in message.parts:
                if part.kind == "call":
                    if part.call_id in pending:
                        raise ValueError("tool call ids must be unique within an exchange")
                    pending[part.call_id] = part.name
                elif part.kind == "result" and pending.pop(part.call_id, None) != part.name:
                    raise ValueError("tool results must match their calls")
        if pending:
            raise ValueError("all tool calls need results")
        return self

    def request(self) -> ChatRequest:
        """The port's policy clamps these requested ceilings to the operator's caps."""
        messages = tuple(ChatMessage(message.role, tuple(ChatPart(**part.model_dump()) for part in message.parts))
                         for message in self.messages)
        tools = tuple(tool.model_dump() for tool in self.tools)
        values = {"instructions": self.instructions, "messages": messages, "tools": tools, "purpose": self.purpose,
                  "input_tokens": 2_147_483_647, "output_tokens": 2_147_483_647}
        if self.response_format:
            values["response_format"] = self.response_format
        if self.response_schema is not None:
            values["response_schema"] = self.response_schema
        return ChatRequest(**values)
