"""Seal private provider continuation state without exposing it to model-port consumers."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import struct
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = b"KMC1"
_PURPOSE = b"kge-model-continuation-v1"
_NONCE_BYTES = 12
_MAX_AGE = 30 * 24 * 60 * 60
CONTINUATION_MAX_ENCODED_CHARS = 400_000
CONTINUATION_MAX_PAYLOAD_BYTES = 299_952
_HEX = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = ("tenant", "principal", "app", "conversation", "purpose", "provider", "model", "prefix")


class ContinuationInvalid(ValueError):
    """A continuation is unavailable or does not belong to the current request."""

    def __init__(self):
        super().__init__("The model continuation is invalid or expired. Please start the question again.")


@dataclass(frozen=True)
class ContinuationBinding:
    """Trusted server-supplied identity and request binding; never built from an unauthenticated request body."""

    tenant: str = field(repr=False)
    principal: str = field(repr=False)
    app: str = field(repr=False)
    conversation: str = field(repr=False)
    purpose: str = field(repr=False)
    provider: str = field(repr=False)
    model: str = field(repr=False)
    prefix: str = field(repr=False)

    def associated_data(self) -> bytes:
        """Canonical binding authenticated by the cipher, not transmitted in the token."""
        values = {name: getattr(self, name) for name in _FIELDS}
        if any(not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512 for value in values.values()):
            raise ValueError("invalid continuation binding")
        if not _HEX.fullmatch(self.prefix):
            raise ValueError("invalid continuation binding")
        return _PURPOSE + b"\x00" + json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def prefix_digest(request_prefix: Mapping[str, Any]) -> str:
    """Digest the exact server-constructed provider prefix, without logging the request."""
    if not isinstance(request_prefix, Mapping):
        raise ValueError("invalid model request prefix")
    try:
        encoded = json.dumps(dict(request_prefix), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid model request prefix") from None
    return hashlib.sha256(encoded).hexdigest()


class Continuations:
    """AEAD continuation envelopes, valid across instances sharing a key and trusted binding.

    Tokens are reusable until expiry; this stateless primitive does not provide single-use or per-token revocation.
    Rotating the key invalidates every token sealed under the old key.  The caller controls retention and verifies
    authenticated identity before calling this utility.  No secrets or payloads are included in error messages.
    """

    def __init__(self, key: bytes, *, max_age_seconds: int, max_payload_bytes: int = 256 * 1024,
                 clock: Callable[[], float] = time.time):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("a continuation key requires at least 32 bytes")
        if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool) or not 0 < max_age_seconds <= _MAX_AGE:
            raise ValueError("invalid continuation lifetime")
        if not isinstance(max_payload_bytes, int) or isinstance(max_payload_bytes, bool) or not 0 < max_payload_bytes <= CONTINUATION_MAX_PAYLOAD_BYTES:
            raise ValueError("invalid continuation payload limit")
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_PURPOSE).derive(key)
        self._cipher = AESGCM(derived)
        self._age = max_age_seconds
        self._limit = max_payload_bytes
        self._clock = clock
        self._max_token_length = ((_limit_size(max_payload_bytes) + 2) // 3) * 4

    def seal(self, payload: bytes, binding: ContinuationBinding) -> str:
        """Seal a nonempty private payload for this exact trusted context."""
        if not isinstance(payload, bytes) or not payload or len(payload) > self._limit:
            raise ValueError("invalid continuation payload")
        if not isinstance(binding, ContinuationBinding):
            raise ValueError("invalid continuation binding")
        associated = binding.associated_data()
        now = int(self._clock())
        if now < 0 or now > (2**64 - 1) - self._age:
            raise ValueError("invalid continuation clock")
        plain = struct.pack("!QQ", now, now + self._age) + payload
        nonce = os.urandom(_NONCE_BYTES)
        sealed = _VERSION + nonce + self._cipher.encrypt(nonce, plain, associated)
        token = base64.urlsafe_b64encode(sealed).rstrip(b"=").decode("ascii")
        if len(token) > CONTINUATION_MAX_ENCODED_CHARS:
            raise ValueError("invalid continuation payload")
        return token

    def open(self, token: str, binding: ContinuationBinding) -> bytes:
        """Open for the same trusted context; all invalid, expired and tampered tokens look alike."""
        try:
            if not isinstance(token, str) or not token or len(token) > min(self._max_token_length, CONTINUATION_MAX_ENCODED_CHARS):
                raise ContinuationInvalid()
            if not isinstance(binding, ContinuationBinding):
                raise ContinuationInvalid()
            encoded = token.encode("ascii")
            data = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(data).rstrip(b"=") != encoded or len(data) < _limit_size(1) or not data.startswith(_VERSION):
                raise ContinuationInvalid()
            nonce = data[len(_VERSION):len(_VERSION) + _NONCE_BYTES]
            plain = self._cipher.decrypt(nonce, data[len(_VERSION) + _NONCE_BYTES:], binding.associated_data())
            if len(plain) <= 16 or len(plain) - 16 > self._limit:
                raise ContinuationInvalid()
            issued, expires = struct.unpack("!QQ", plain[:16])
            now = int(self._clock())
            if issued > now or expires <= now or expires <= issued or expires - issued > self._age:
                raise ContinuationInvalid()
            return plain[16:]
        except (InvalidTag, ContinuationInvalid, ValueError, TypeError, OverflowError, UnicodeError, binascii.Error, struct.error):
            raise ContinuationInvalid() from None


_scope: ContextVar[tuple[Continuations, ContinuationBinding] | None] = ContextVar("model_continuation_scope", default=None)


@contextmanager
def continuation_scope(codec: Continuations, binding: ContinuationBinding):
    """Install server-derived continuation identity while a model stream is consumed.

    The transport authenticates the caller and conversation before constructing the binding. The adapter replaces its
    prefix with the hash of the actual provider request; a client-supplied binding is never trusted.
    """
    if not isinstance(codec, Continuations) or not isinstance(binding, ContinuationBinding):
        raise ValueError("invalid continuation scope")
    binding.associated_data()
    token = _scope.set((codec, binding))
    try:
        yield
    finally:
        _scope.reset(token)


def current_continuation():
    """The trusted transport's active context, or None when replay is unavailable."""
    return _scope.get()


def _limit_size(payload_size: int) -> int:
    """Wire bytes for the version, nonce, timestamps, GCM tag and payload."""
    return len(_VERSION) + _NONCE_BYTES + 16 + 16 + payload_size
