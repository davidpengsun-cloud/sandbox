"""A shared per-caller model-call allowance; identity comes from the authenticated server, never model input.

Limits are off when MODEL_RATE_LIMITS is unset. When set, admission is a versioned write before model work. A failed or
cancelled call still spends its admission. One fixed one-minute window is shared by all model operations for a caller.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from kge.adapters.objstore import open_store, under
from kge.landing.zone import CLIENT_ID
from kge.pipeline import trace
from kge.pipeline.settings import settings
from kge.ports import ObjectStore, StoreConflict, StoreError

WINDOW_SECONDS = 60
MAX_CONFLICTS = 32


class QuotaExceeded(RuntimeError):
    """The caller has used its allowance; retry_after is seconds until the next window."""

    def __init__(self, retry_after: int):
        super().__init__("You have reached the model request limit. Please try again later.")
        self.retry_after = retry_after


class QuotaUnavailable(RuntimeError):
    """The quota could not be verified; do not allow the model call."""


@dataclass(frozen=True)
class Caller:
    """An identity established by server authentication or a trusted service entry point."""

    client: str
    kind: str
    identifier: str
    roles: tuple[str, ...] = ()


_caller: contextvars.ContextVar[Caller | Callable[[], Caller] | None] = contextvars.ContextVar("kge_model_quota_caller", default=None)
_retry: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("kge_model_quota_retry", default=None)


def current_quota_caller() -> Caller | Callable[[], Caller] | None:
    """The authenticated scope, for explicit propagation to a background worker."""
    return _caller.get()


def _validate_caller(caller: Caller) -> None:
    if (not isinstance(caller, Caller) or not isinstance(caller.client, str) or not CLIENT_ID.fullmatch(caller.client)
            or caller.kind not in ("person", "service") or not isinstance(caller.identifier, str)
            or not caller.identifier.strip() or len(caller.identifier) > 512
            or not isinstance(caller.roles, tuple)
            or any(not isinstance(role, str) or not role.strip() for role in caller.roles)):
        raise ValueError("invalid authenticated model caller")
@contextmanager
def quota_caller(caller: Caller | Callable[[], Caller]) -> Iterator[None]:
    """Bind an authenticated caller or a trusted fresh-authorization callback for this execution context."""
    if not callable(caller):
        _validate_caller(caller)
    token = _caller.set(caller)
    try:
        yield
    finally:
        _caller.reset(token)


@contextmanager
def retry_scope(model: str, call: str) -> Iterator[None]:
    """Attribute retries of an already-admitted inference to the same authenticated caller."""
    token = _retry.set((model, call))
    try:
        yield
    finally:
        _retry.reset(token)


@contextmanager
def without_retry_admission() -> Iterator[None]:
    """A token-count request is not a model inference and must not spend an inference retry allowance."""
    token = _retry.set(None)
    try:
        yield
    finally:
        _retry.reset(token)


def admit_retry() -> None:
    """Spend a new allowance before retrying a provider inference, if its original call was scoped."""
    if value := _retry.get():
        admit(*value)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate rate limit setting")
        out[key] = value
    return out


def limits(raw: str | None) -> dict | None:
    """Read per-minute ceilings; all matching role/person/service ceilings may lower the default ceiling."""
    if raw is None or not raw.strip():
        return None
    try:
        policy = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(policy, dict) or set(policy) - {"default", "persons", "roles", "services"}:
            raise ValueError
        default = policy.get("default")
        if not isinstance(default, int) or isinstance(default, bool) or default <= 0:
            raise ValueError
        for name in ("persons", "roles", "services"):
            entries = policy.get(name, {})
            if not isinstance(entries, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, int)
                                                     or isinstance(v, bool) or v <= 0 for k, v in entries.items()):
                raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise ValueError("MODEL_RATE_LIMITS must be JSON with a positive default and positive caller ceilings") from None
    return policy


def ceiling(policy: dict, caller: Caller) -> int:
    entries = [policy["default"]]
    if caller.kind == "person":
        personal = policy.get("persons", {}).get(caller.identifier)
        if personal is not None:
            entries.append(personal)
        entries.extend(policy.get("roles", {})[role] for role in caller.roles if role in policy.get("roles", {}))
    else:
        service = policy.get("services", {}).get(caller.identifier)
        if service is not None:
            entries.append(service)
    return min(entries)


def reserve(store: ObjectStore, caller: Caller, maximum: int, *, clock=time.time) -> None:
    """Reserve exactly once using store CAS; a backwards clock never resets the stored later window."""
    digest = hashlib.sha256(f"{caller.kind}\0{caller.identifier}".encode()).hexdigest()
    key = f"{caller.client}/{digest}.json"
    for _ in range(MAX_CONFLICTS):
        now = clock()
        if not math.isfinite(now) or now < 0:
            raise QuotaUnavailable("The model request limit is temporarily unavailable.")
        window = int(now // WINDOW_SECONDS)
        try:
            raw, version = store.get_version(key)
            state = json.loads(raw) if raw is not None else {"window": window, "count": 0}
            if (not isinstance(state, dict) or set(state) != {"window", "count"}
                    or any(not isinstance(state[k], int) or isinstance(state[k], bool) or state[k] < 0 for k in ("window", "count"))):
                raise ValueError
            if window < state["window"]:
                raise QuotaUnavailable("The model request limit is temporarily unavailable.")
            count = state["count"] if window == state["window"] else 0
            if count >= maximum:
                raise QuotaExceeded(max(1, math.ceil((window + 1) * WINDOW_SECONDS - now)))
            store.put_version(key, json.dumps({"window": window, "count": count + 1}, separators=(",", ":")).encode(),
                              version, retry_ambiguous=False)
            return
        except StoreConflict:
            continue
        except (StoreError, OSError, ValueError, TypeError, RecursionError):
            raise QuotaUnavailable("The model request limit is temporarily unavailable.") from None
    raise QuotaUnavailable("The model request limit is temporarily unavailable.")


def admit(model: str, call: str) -> None:
    """If enabled, enforce the policy before provider work; never include identity or content in the failure trace."""
    started = time.perf_counter()
    stage = call if call in ("agent", "catalogue", "complete", "plan", "classify", "wording") else "chat"
    try:
        current = settings()
        policy = limits(current.MODEL_RATE_LIMITS)
        if policy is None:
            return
        caller = _caller.get()
        if caller is None:
            raise QuotaUnavailable("The model request limit cannot verify the caller.")
        try:
            if callable(caller):
                caller = caller()
            _validate_caller(caller)
        except Exception:
            raise QuotaUnavailable("The model request limit cannot verify the caller.") from None
        url = (current.MODEL_QUOTA_STORE_URL or current.STORE_URL or "").strip()
        if not url:
            raise QuotaUnavailable("The model request limit is temporarily unavailable.")
        try:
            store = open_store(under(url, "model-quotas"))
        except (ValueError, StoreError):
            raise QuotaUnavailable("The model request limit is temporarily unavailable.") from None
        reserve(store, caller, ceiling(policy, caller))
    except (QuotaExceeded, QuotaUnavailable, ValueError) as exc:
        trace.llm_call(model, None, None, None, (time.perf_counter() - started) * 1000, ok=False,
                       error=type(exc).__name__, detail={"call": stage})
        raise
