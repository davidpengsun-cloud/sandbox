"""Durable numeric-only accounting using the object's versioned-write port.

The service constructs an Actor from authenticated server state and binds the ledger once. This module does not parse
HTTP requests or authenticate identities. Never construct an Actor from a client-provided identity or price.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import base64
import binascii
import contextlib
import os
import secrets
import stat
import tempfile
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from kge.ports import ModelUsage, ObjectStore, StoreConflict

_FIELDS = frozenset({"outcome", "input_tokens", "output_tokens", "thinking_tokens", "input_price", "output_price",
                     "cache_mode", "cache_read_tokens", "cache_write_tokens", "cache_read_price", "cache_write_price"})
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "cache_write_tokens")
_PRICE_FIELDS = ("input_price", "output_price", "cache_read_price", "cache_write_price")
_OUTCOMES = frozenset({"ok", "failed", "cancelled"})
_MAX_ATTEMPTS = 2048
_MAX_RETRIES = 64


def _pause_conflict(attempt: int) -> None:
    """Spread competing compare-and-swap writers without exceeding the enclosing request's deadline."""
    from kge.adapters.deadline import request_remaining
    cap = min(0.02, 0.001 * 2 ** min(attempt, 5))
    delay = cap * (0.5 + secrets.randbelow(1000) / 2000)
    remaining = request_remaining()
    time.sleep(delay if remaining is None else min(delay, remaining))
    request_remaining()


class UsageUnavailable(RuntimeError):
    """Accounting state is absent, invalid, unavailable, or outside the verified actor's scope."""


class UsageConflict(RuntimeError):
    """The requested update conflicts with a completed or concurrent update."""


@dataclass(frozen=True)
class Actor:
    """Identity obtained by the server's authentication boundary, not the request body."""

    client: str = field(repr=False)
    kind: str = field(repr=False)
    who: str = field(repr=False)
    app: str = field(repr=False)

    def __post_init__(self):
        if self.kind not in ("person", "service") or self.app not in ("cerebro", "discover", "context", "service"):
            raise ValueError("invalid usage scope")
        if any(not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512
               for value in (self.client, self.who)):
            raise ValueError("invalid usage scope")


def _id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.version != 4:
            raise ValueError()
        return value
    except (ValueError, AttributeError, TypeError):
        raise ValueError("a server-generated UUID is required") from None


def _numeric(value: Any, *, price: bool) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid usage value")
    if not price:
        if not isinstance(value, int) or value < 0 or value > 2**53 - 1:
            raise ValueError("invalid token count")
        return value
    if not isinstance(value, (int, float, Decimal)):
        raise ValueError("invalid usage rate")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid usage rate") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal(1_000_000_000):
        raise ValueError("invalid usage rate")
    return str(amount.normalize())


def _usage(usage: Mapping[str, Any]) -> dict:
    if not isinstance(usage, Mapping) or set(usage) != _FIELDS:
        raise ValueError("invalid usage record")
    if not isinstance(usage["outcome"], str) or usage["outcome"] not in _OUTCOMES or usage["cache_mode"] not in ("none", "separate", "unknown"):
        raise ValueError("invalid usage record")
    result = {field: _numeric(usage[field], price=False) for field in _TOKEN_FIELDS}
    result.update({field: _numeric(usage[field], price=True) for field in _PRICE_FIELDS})
    result.update(outcome=usage["outcome"], cache_mode=usage["cache_mode"])
    inp, out, think = result["input_tokens"], result["output_tokens"], result["thinking_tokens"]
    read, write = result["cache_read_tokens"], result["cache_write_tokens"]
    if out is not None and think is not None and think > out:
        raise ValueError("thinking tokens exceed output tokens")
    if inp is not None and ((read is not None and read > inp) or (write is not None and write > inp)
                            or (read is not None and write is not None and read + write > inp)):
        raise ValueError("cache tokens exceed input tokens")
    if result["cache_mode"] == "none" and (read not in (None, 0) or write not in (None, 0)):
        raise ValueError("unexpected cache tokens")
    return result


def _cost(item: dict) -> Decimal | None:
    inp, out = item["input_tokens"], item["output_tokens"]
    ir, o = item["input_price"], item["output_price"]
    if None in (inp, out, ir, o) or item["cache_mode"] == "unknown":
        return None
    if item["cache_mode"] == "none":
        return (Decimal(inp) * Decimal(ir) + Decimal(out) * Decimal(o)) / 1_000_000
    read, write = item["cache_read_tokens"], item["cache_write_tokens"]
    if read is None or write is None or (read and item["cache_read_price"] is None) or (write and item["cache_write_price"] is None):
        return None
    return (Decimal(inp - read - write) * Decimal(ir) + Decimal(read) * Decimal(item["cache_read_price"] or "0")
            + Decimal(write) * Decimal(item["cache_write_price"] or "0") + Decimal(out) * Decimal(o)) / 1_000_000


def attempt_cost(usage: Mapping[str, Any]) -> float | None:
    """Price one validated physical attempt by the same rules as the question ledger."""
    cost = _cost(_usage(usage))
    return float(cost) if cost is not None else None


def _settled(value) -> bool:
    return value is True or value in ("quota_refused", "quota_unavailable", "deterministic_cancelled", "idle_poll", "rules_only")


class Ledger:
    """One verified actor's question ledger; writes to each question use atomic compare-and-swap."""

    def __init__(self, store: ObjectStore, actor: Actor, key: bytes, conversation_id: str | None = None):
        if not isinstance(actor, Actor) or not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("a trusted scope and random usage key are required")
        if conversation_id is not None:
            _id(conversation_id)
        self._store, self._actor, self._key, self._conversation = store, actor, key, conversation_id

    def _path(self, question_id: str) -> str:
        _id(question_id)
        values = [self._actor.client, self._actor.kind, self._actor.who, self._actor.app, self._conversation, question_id]
        raw = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
        return "model-usage/" + hmac.new(self._key, b"kge-model-usage-v1\0" + raw, hashlib.sha256).hexdigest() + ".json"

    def scope_binding(self, question_id: str, conversation_id: str) -> str:
        """Stable opaque binding for checking a persisted background run against the current verified actor."""
        _id(conversation_id)
        if self._conversation is not None and conversation_id != self._conversation:
            raise UsageConflict("The model question scope is invalid.")
        return hmac.new(self._key, ("kge-background-scope-v1\0" + self._path(question_id) + "\0" + conversation_id).encode(), hashlib.sha256).hexdigest()

    def run_identity(self) -> tuple[str, str]:
        return self._actor.kind, self._actor.app

    def _read(self, path: str) -> tuple[dict | None, str]:
        raw, version = self._store.get_version(path)
        if raw is None:
            return None, version
        try:
            record = json.loads(raw)
            if not isinstance(record, dict) or set(record) != {"version", "closed", "attempts", "invocations"} or record["version"] != 2:
                raise ValueError()
            if not isinstance(record["closed"], bool) or not isinstance(record["attempts"], dict) or len(record["attempts"]) > _MAX_ATTEMPTS:
                raise ValueError()
            if not isinstance(record["invocations"], dict) or len(record["invocations"]) > _MAX_ATTEMPTS:
                raise ValueError()
            for invocation_id, done in record["invocations"].items():
                _id(invocation_id)
                if not isinstance(done, bool) and done not in ("claimed", "quota_refused", "quota_unavailable", "deterministic_cancelled", "idle_poll", "rules_only"):
                    raise ValueError()
            for attempt_id, attempt in record["attempts"].items():
                _id(attempt_id)
                if not isinstance(attempt, dict) or set(attempt) != {"invocation", "usage"}:
                    raise ValueError()
                invocation, value = attempt["invocation"], attempt["usage"]
                if invocation is not None and invocation not in record["invocations"]:
                    raise ValueError()
                if value is not None:
                    if not isinstance(value, dict) or set(value) != _FIELDS:
                        raise ValueError()
                    original = dict(value)
                    for field in _PRICE_FIELDS:
                        if original[field] is not None:
                            if not isinstance(original[field], str) or len(original[field]) > 80:
                                raise ValueError()
                            original[field] = Decimal(original[field])
                    _usage(original)
            if record["closed"] and (any(item["usage"] is None for item in record["attempts"].values())
                                     or not all(_settled(value) for value in record["invocations"].values())):
                raise ValueError()
            if record["closed"] and not record["attempts"] and not record["invocations"]:
                raise ValueError()
            return record, version
        except (UnicodeError, ValueError, TypeError, InvalidOperation):
            raise UsageUnavailable("The model usage record is unavailable.") from None

    def _update(self, question_id: str, action) -> bool:
        path = self._path(question_id)
        for attempt in range(_MAX_RETRIES):
            record, version = self._read(path)
            updated = action(record)
            if updated is None:
                return False
            try:
                self._store.put_version(path, json.dumps(updated, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(), version)
                return True
            except StoreConflict:
                if attempt + 1 < _MAX_RETRIES:
                    _pause_conflict(attempt)
                continue
        raise UsageConflict("The model usage record changed repeatedly; retry the operation.")

    def bind_question(self, question_id: str, model_binding: tuple[str, str], *, legacy_binding) -> None:
        """First writer pins a private connector digest; legacy questions may adopt only the previous selection."""
        def digest(binding):
            if not isinstance(binding, tuple) or len(binding) != 2 or any(not isinstance(v, str) or not v or len(v) > 256 for v in binding):
                raise ValueError("invalid model binding")
            raw = json.dumps(binding, ensure_ascii=False, separators=(",", ":")).encode()
            return hmac.new(self._key, b"kge-question-model-v1\0" + raw, hashlib.sha256).hexdigest()
        selected = digest(model_binding)
        path = self._path(question_id)
        sidecar = path[:-5] + ".model.json"
        for attempt in range(_MAX_RETRIES):
            raw, version = self._store.get_version(sidecar)
            if raw is not None:
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict) or set(value) != {"version", "binding"} or value["version"] != 1 or not isinstance(value["binding"], str) or len(value["binding"]) != 64:
                        raise ValueError()
                    if not hmac.compare_digest(value["binding"], selected):
                        raise UsageConflict("The model changed during this question.")
                    return
                except (UnicodeError, ValueError, TypeError):
                    raise UsageUnavailable("The model binding is unavailable.") from None
            record, _ = self._read(path)
            if record is not None and digest(legacy_binding()) != selected:
                raise UsageConflict("The model changed during this question.")
            payload = json.dumps({"version": 1, "binding": selected}, sort_keys=True, separators=(",", ":")).encode()
            try:
                self._store.put_version(sidecar, payload, version)
                return
            except StoreConflict:
                if attempt + 1 < _MAX_RETRIES:
                    _pause_conflict(attempt)
        raise UsageConflict("The model binding changed repeatedly; retry the operation.")

    def open_question(self, question_id: str) -> None:
        def opened(record):
            if record is None:
                return {"version": 2, "closed": False, "attempts": {}, "invocations": {}}
            if record["closed"]:
                raise UsageConflict("The question is already finished.")
            return None
        self._update(question_id, opened)

    def start_invocation(self, question_id: str, invocation_id: str) -> bool:
        _id(invocation_id)
        def start(record):
            if record is None:
                raise UsageUnavailable("The model usage record is unavailable.")
            if record["closed"]:
                raise UsageConflict("The question is already finished.")
            if invocation_id in record["invocations"]:
                return None
            if len(record["invocations"]) >= _MAX_ATTEMPTS:
                raise UsageConflict("The question has too many model invocations.")
            record["invocations"][invocation_id] = False
            return record
        return self._update(question_id, start)

    def claim_invocation(self, question_id: str, invocation_id: str) -> bool:
        _id(invocation_id)
        def claim(record):
            if record is None or invocation_id not in record["invocations"]:
                raise UsageUnavailable("The model invocation is unavailable.")
            if record["closed"]:
                raise UsageConflict("The question is already finished.")
            if record["invocations"][invocation_id] is not False:
                return None
            record["invocations"][invocation_id] = "claimed"
            return record
        return self._update(question_id, claim)

    def finish_invocation(self, question_id: str, invocation_id: str) -> None:
        _id(invocation_id)
        def finish(record):
            if record is None or invocation_id not in record["invocations"]:
                raise UsageUnavailable("The model usage record is unavailable.")
            if _settled(record["invocations"][invocation_id]):
                return None
            linked = [attempt for attempt in record["attempts"].values() if attempt["invocation"] == invocation_id]
            if not linked or any(attempt["usage"] is None for attempt in linked):
                raise UsageConflict("The model invocation is not fully accounted.")
            record["invocations"][invocation_id] = True
            return record
        self._update(question_id, finish)

    def finish_invocation_no_inference(self, question_id: str, invocation_id: str, *, reason: str) -> None:
        """A trusted pre-provider refusal or model-free cancellation may settle an invocation with no inference."""
        _id(invocation_id)
        if reason not in ("quota_refused", "quota_unavailable", "idle_poll", "rules_only"):
            raise ValueError("invalid no-inference reason")
        def finish(record):
            if record is None or invocation_id not in record["invocations"]:
                raise UsageUnavailable("The model usage record is unavailable.")
            prior = record["invocations"][invocation_id]
            if prior == reason:
                return None
            if _settled(prior) or any(attempt["invocation"] == invocation_id for attempt in record["attempts"].values()):
                raise UsageConflict("The model invocation cannot be settled without inference.")
            record["invocations"][invocation_id] = reason
            return record
        self._update(question_id, finish)

    def start_attempt(self, question_id: str, attempt_id: str, invocation_id: str | None = None) -> bool:
        _id(attempt_id)
        if invocation_id is not None:
            _id(invocation_id)
        def start(record):
            if record is None:
                raise UsageUnavailable("The model usage record is unavailable.")
            if record["closed"]:
                raise UsageConflict("The question is already finished.")
            if invocation_id is not None and (invocation_id not in record["invocations"] or _settled(record["invocations"][invocation_id])):
                raise UsageConflict("The model invocation is not active.")
            if attempt_id in record["attempts"]:
                if record["attempts"][attempt_id]["invocation"] != invocation_id:
                    raise UsageConflict("The model attempt belongs to another invocation.")
                return None
            if len(record["attempts"]) >= _MAX_ATTEMPTS:
                raise UsageConflict("The question has too many model attempts.")
            record["attempts"][attempt_id] = {"invocation": invocation_id, "usage": None}
            return record
        return self._update(question_id, start)

    def finish_attempt(self, question_id: str, attempt_id: str, usage: Mapping[str, Any]) -> None:
        _id(attempt_id)
        value = _usage(usage)
        def finish(record):
            if record is None or attempt_id not in record["attempts"]:
                raise UsageUnavailable("The model usage record is unavailable.")
            previous = record["attempts"][attempt_id]["usage"]
            if previous == value:
                return None
            if record["closed"] or previous is not None:
                raise UsageConflict("The model attempt is already finished.")
            record["attempts"][attempt_id]["usage"] = value
            return record
        self._update(question_id, finish)

    def finish_question(self, question_id: str) -> None:
        def finish(record):
            if record is None:
                raise UsageUnavailable("The model usage record is unavailable.")
            if record["closed"]:
                return None
            if not record["attempts"] and not record["invocations"]:
                raise UsageConflict("The question has no verified accounting outcome.")
            if (any(item["usage"] is None for item in record["attempts"].values())
                    or not all(_settled(value) for value in record["invocations"].values())):
                raise UsageConflict("A model attempt is still running.")
            record["closed"] = True
            return record
        self._update(question_id, finish)

    def finish_question_no_inference(self, question_id: str, invocation_id: str, *, reason: str) -> None:
        """Atomically record a server-proven deterministic cancellation with no prior or pending model work."""
        _id(invocation_id)
        if reason != "deterministic_cancelled":
            raise ValueError("invalid no-inference reason")
        def finish(record):
            if record is None:
                raise UsageUnavailable("The model usage record is unavailable.")
            if record["closed"] and record["invocations"] == {invocation_id: reason} and not record["attempts"]:
                return None
            if record["closed"] or record["attempts"] or record["invocations"]:
                raise UsageConflict("The model-free cancellation is not verified.")
            record["invocations"][invocation_id] = reason
            record["closed"] = True
            return record
        self._update(question_id, finish)

    def report(self, question_id: str) -> Mapping[str, Any]:
        record, _ = self._read(self._path(question_id))
        if record is None:
            raise UsageUnavailable("The model usage record is unavailable.")
        attempts = [attempt["usage"] for attempt in record["attempts"].values()]
        complete = record["closed"] and all(item is not None for item in attempts) and all(_settled(value) for value in record["invocations"].values())
        def total(name):
            values = [item[name] for item in attempts if item is not None]
            return sum(values) if complete and all(value is not None for value in values) else None
        costs = [_cost(item) for item in attempts if item is not None]
        cost = sum(costs, Decimal(0)) if complete and all(value is not None for value in costs) else None
        return {"calls": len(attempts), "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
                "thinking_tokens": total("thinking_tokens"), "cost_usd": float(cost) if cost is not None else None,
                "complete": complete}


def for_actor(store: ObjectStore, actor: Actor, *, key: bytes, conversation_id: str | None = None):
    """Bind the port to identity authenticated by the hosting service; key must be random and shared by instances."""
    return Ledger(store, actor, key, conversation_id)


def _decode_key(encoded: str) -> bytes:
    """Decode a random 32-byte URL-safe base64 secret without echoing it on failure."""
    try:
        source = encoded.encode("ascii")
        key = base64.b64decode(source + b"=" * (-len(source) % 4), altchars=b"-_", validate=True)
        if len(key) != 32 or base64.urlsafe_b64encode(key).rstrip(b"=") != source:
            raise ValueError()
        return key
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ValueError("MODEL_USAGE_KEY must be 32 random bytes encoded as unpadded URL-safe base64") from None


def _local_key(folder: Path) -> bytes:
    """Create a local-only mode-600 key atomically; every process using this run folder reads the same key."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "model_usage_key"
    if not path.exists():
        fd, temp = tempfile.mkstemp(prefix=".model-usage-key-", dir=folder)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temp)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
                raise ValueError("the local model usage key must be private")
            raw = stream.read(256)
        return _decode_key(raw.decode("ascii").strip())
    except (OSError, UnicodeError):
        raise ValueError("the local model usage key is unavailable or invalid") from None


def _verify_key_marker(store: ObjectStore, key: bytes) -> None:
    """Refuse a different key for an existing ledger instead of opening an apparently empty namespace."""
    expected = hmac.new(key, b"kge-model-usage-key-marker-v1", hashlib.sha256).hexdigest().encode("ascii")
    for _ in range(_MAX_RETRIES):
        held, version = store.get_version("key-fingerprint")
        if held is not None:
            if not hmac.compare_digest(held, expected):
                raise UsageUnavailable("The model usage key does not match the existing ledger.")
            return
        try:
            store.put_version("key-fingerprint", expected, version)
            return
        except StoreConflict:
            continue
    raise UsageConflict("The model usage key changed repeatedly; retry the operation.")


def for_profile(prof, actor: Actor, conversation_id: str):
    """Select private durable state and a stable key for a server-authenticated actor in this profile.

    Cloud Run and any remote store require MODEL_USAGE_KEY, shared by every instance. The value must be generated from
    32 random bytes, URL-safe base64 encoded without padding, stored as a secret, and kept stable for ledger lifetime.
    A key change without an explicit ledger migration is refused; no key or question data is logged.
    """
    from kge.adapters.objstore import open_store, under
    from kge.pipeline.settings import settings

    current = settings()
    if not isinstance(actor, Actor) or actor.client != prof.client:
        raise ValueError("invalid usage scope")
    url = (current.STORE_URL or "").strip()
    configured = (current.MODEL_USAGE_KEY or "").strip()
    if (current.K_SERVICE or "").strip() and not url.startswith("gs://"):
        raise ValueError("a shared STORE_URL is required for model usage on Cloud Run")
    if not configured and ((current.K_SERVICE or "").strip() or url.startswith("gs://")):
        raise ValueError("MODEL_USAGE_KEY is required for shared model usage")
    key = _decode_key(configured) if configured else _local_key(Path(prof.run_dir()))
    base = under(url, "model-usage", prof.client) if url else str((Path(prof.run_dir()) / "model-usage").resolve())
    store = open_store(base, private=True)
    _verify_key_marker(store, key)
    return for_actor(store, actor, key=key, conversation_id=conversation_id)


_active_usage: ContextVar[tuple[ModelUsage, str, str | None] | None] = ContextVar("kge_model_usage", default=None)
_usage_conversation: ContextVar[str | None] = ContextVar("kge_usage_conversation", default=None)


@contextlib.contextmanager
def usage_scope(ledger: ModelUsage, question_id: str, invocation_id: str | None = None, *, conversation_id: str | None = None):
    """Attach a verified question to model calls; the hosting service propagates this context to worker threads."""
    if not isinstance(ledger, ModelUsage):
        raise ValueError("a model usage port is required")
    _id(question_id)
    if invocation_id is not None:
        _id(invocation_id)
    if conversation_id is not None:
        _id(conversation_id)
    token = _active_usage.set((ledger, question_id, invocation_id))
    conversation_token = _usage_conversation.set(conversation_id)
    try:
        yield
    finally:
        _usage_conversation.reset(conversation_token)
        _active_usage.reset(token)


def current_usage() -> tuple[ModelUsage, str, str | None] | None:
    """The active trusted question for provider-attempt accounting, or None outside the verified scope."""
    return _active_usage.get()


def current_usage_conversation() -> str | None:
    return _usage_conversation.get()


def __getattr__(name: str):
    """Expose the correlation helper without making its cryptographic implementation a caller dependency."""
    if name in ("CorrelationTokens", "CorrelationInvalid"):
        from kge.adapters.modelusage import correlation
        return getattr(correlation, name)
    raise AttributeError(name)
