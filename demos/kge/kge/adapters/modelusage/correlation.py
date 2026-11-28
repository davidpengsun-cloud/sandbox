"""Opaque model-question correlation capabilities bound to an authenticated server-side actor.

The token does not authenticate a caller: every endpoint must authenticate the person or service first, construct the
Actor from that verified identity and the served profile, and only then call verify. An issued token can be reissued for
the same question after expiry when that actor is still authorized. Keys are shared across instances and rotated by
issuing with the first key and retaining previous keys temporarily for verification.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import time
from typing import Callable, Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from kge.adapters.modelusage import Actor, _id

_DOMAIN = b"kge-model-question-correlation-v1"
_VERSION = b"KMQ1"
_NONCE = 12
_MAX_AGE = 24 * 60 * 60
_MAX_TOKEN = 8192
_FIELDS = {"client", "kind", "who", "app", "question_id", "conversation_id", "purpose", "issued", "expires"}


class CorrelationInvalid(ValueError):
    """The correlation is expired or not valid for this authenticated caller."""

    def __init__(self):
        super().__init__("The question session is invalid or expired. Please start the question again.")


def _purpose(value):
    if value not in ("agent", "catalogue"):
        raise ValueError("invalid question purpose")
    return value


def _identity(actor: Actor) -> bytes:
    if not isinstance(actor, Actor):
        raise ValueError("a verified actor is required")
    return json.dumps([actor.client, actor.kind, actor.who, actor.app], ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class CorrelationTokens:
    """Authenticated and encrypted question IDs; issue with the first key, verify with any configured key."""

    def __init__(self, keys: Sequence[bytes], *, max_age_seconds: int = 3600, clock: Callable[[], float] = time.time):
        if (not isinstance(keys, (tuple, list)) or not 1 <= len(keys) <= 4
                or any(not isinstance(key, bytes) or len(key) < 32 for key in keys)):
            raise ValueError("one or more shared random correlation keys of at least 32 bytes are required")
        if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool) or not 0 < max_age_seconds <= _MAX_AGE:
            raise ValueError("invalid question session lifetime")
        self._ciphers = tuple(AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_DOMAIN).derive(key)) for key in keys)
        self._age, self._clock = max_age_seconds, clock

    def issue(self, actor: Actor, question_id: str, purpose: str, conversation_id: str) -> str:
        """Create a token only after the hosting service has authorized the actor and generated the question id."""
        _identity(actor)
        _id(question_id)
        _id(conversation_id)
        _purpose(purpose)
        now = int(self._clock())
        if now < 0 or now > 2**53 - 1 - self._age:
            raise ValueError("invalid question session clock")
        body = {"client": actor.client, "kind": actor.kind, "who": actor.who, "app": actor.app,
                "question_id": question_id, "conversation_id": conversation_id, "purpose": purpose,
                "issued": now, "expires": now + self._age}
        plaintext = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        nonce = os.urandom(_NONCE)
        wire = _VERSION + nonce + self._ciphers[0].encrypt(nonce, plaintext, _DOMAIN)
        return base64.urlsafe_b64encode(wire).rstrip(b"=").decode("ascii")

    def verify(self, token: str, actor: Actor, purpose: str, conversation_id: str) -> str:
        """Return the bound question id only for the current independently authenticated actor and purpose."""
        try:
            if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN:
                raise CorrelationInvalid()
            expected = _identity(actor)
            _purpose(purpose)
            _id(conversation_id)
            encoded = token.encode("ascii")
            wire = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if (base64.urlsafe_b64encode(wire).rstrip(b"=") != encoded or not wire.startswith(_VERSION)
                    or len(wire) < len(_VERSION) + _NONCE + 16 + 1):
                raise CorrelationInvalid()
            nonce = wire[len(_VERSION):len(_VERSION) + _NONCE]
            encrypted = wire[len(_VERSION) + _NONCE:]
            plaintext = None
            for cipher in self._ciphers:
                try:
                    plaintext = cipher.decrypt(nonce, encrypted, _DOMAIN)
                    break
                except InvalidTag:
                    continue
            if plaintext is None:
                raise CorrelationInvalid()
            body = json.loads(plaintext)
            if not isinstance(body, dict) or set(body) != _FIELDS:
                raise CorrelationInvalid()
            actual_actor = Actor(body["client"], body["kind"], body["who"], body["app"])
            if (not hmac.compare_digest(_identity(actual_actor), expected) or body["purpose"] != purpose
                    or body["conversation_id"] != conversation_id):
                raise CorrelationInvalid()
            question_id = _id(body["question_id"])
            issued, expires, now = body["issued"], body["expires"], int(self._clock())
            if (not isinstance(issued, int) or isinstance(issued, bool) or not isinstance(expires, int)
                    or isinstance(expires, bool) or issued > now or expires <= now or expires <= issued
                    or expires - issued > self._age):
                raise CorrelationInvalid()
            return question_id
        except (CorrelationInvalid, ValueError, TypeError, OverflowError, UnicodeError, binascii.Error, InvalidTag):
            raise CorrelationInvalid() from None
