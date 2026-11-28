"""Private model continuation envelopes bind callers without exposing provider reasoning."""
from __future__ import annotations

from dataclasses import replace

import pytest

from kge.adapters.model.continuation import (CONTINUATION_MAX_ENCODED_CHARS, CONTINUATION_MAX_PAYLOAD_BYTES,
                                             ContinuationBinding, ContinuationInvalid, Continuations, prefix_digest)


KEY = b"shared-key-32-bytes-for-test-only"
OTHER_KEY = b"other-key-32-bytes-for-test-only!"
PRIVATE = b'{"thinking":"very private reasoning","signature":"secret signature"}'


def binding():
    return ContinuationBinding(tenant="meridian", principal="alice", app="cerebro", conversation="session-1", purpose="agent",
                               provider="vertex-claude", model="claude-example", prefix=prefix_digest({"messages": ["hello"], "tools": []}))


def cipher(now=1000, key=KEY, **kwargs):
    return Continuations(key, max_age_seconds=kwargs.pop("max_age_seconds", 60), clock=lambda: now, **kwargs)


def test_opaque_token_opens_across_instances_and_repeated_retry():
    first, second = cipher(), cipher(now=1010)
    one = first.seal(PRIVATE, binding())
    two = first.seal(PRIVATE, binding())
    assert one != two and "private" not in one and "signature" not in one
    assert second.open(one, binding()) == PRIVATE and second.open(one, binding()) == PRIVATE
    assert "alice" not in repr(binding())


@pytest.mark.parametrize("field,wrong", [("tenant", "other"), ("principal", "bob"), ("app", "discover"),
                                        ("conversation", "session-2"), ("purpose", "catalogue"),
                                        ("provider", "vertex-gemini"), ("model", "different"),
                                        ("prefix", "0" * 64)])
def test_context_cannot_be_rebound(field, wrong):
    token = cipher().seal(PRIVATE, binding())
    with pytest.raises(ContinuationInvalid, match="invalid or expired") as caught:
        cipher().open(token, replace(binding(), **{field: wrong}))
    assert "alice" not in str(caught.value) and "private" not in str(caught.value)


def test_token_tamper_key_rotation_and_domain_separation_fail_closed():
    token = cipher().seal(PRIVATE, binding())
    altered = token[:-1] + ("A" if token[-1] != "A" else "B")
    for candidate, instance in ((altered, cipher()), (token, cipher(key=OTHER_KEY))):
        with pytest.raises(ContinuationInvalid):
            instance.open(candidate, binding())


def test_expiration_and_clock_skew_are_checked_at_open():
    token = cipher().seal(PRIVATE, binding())
    assert cipher(now=1059).open(token, binding()) == PRIVATE
    for now in (999, 1060, 1100):
        with pytest.raises(ContinuationInvalid):
            cipher(now=now).open(token, binding())
    with pytest.raises(ContinuationInvalid):
        cipher(now=1030, max_age_seconds=30).open(token, binding())


def test_size_limit_and_invalid_tokens_fail_without_echoing_data():
    small = cipher(max_payload_bytes=4)
    token = small.seal(b"1234", binding())
    assert small.open(token, binding()) == b"1234"
    with pytest.raises(ValueError, match="payload") as caught:
        small.seal(b"PRIVATE_TOO_LONG", binding())
    assert "PRIVATE" not in str(caught.value)
    for invalid in ("", token + "a" * 100, "==", "💥", token + "="):
        with pytest.raises(ContinuationInvalid):
            small.open(invalid, binding())


def test_encoded_limit_exact_boundary_and_one_byte_over():
    maximum = cipher(max_payload_bytes=CONTINUATION_MAX_PAYLOAD_BYTES)
    payload = b"x" * CONTINUATION_MAX_PAYLOAD_BYTES
    token = maximum.seal(payload, binding())
    assert len(token) == CONTINUATION_MAX_ENCODED_CHARS == 400_000
    assert maximum.open(token, binding()) == payload
    with pytest.raises(ValueError, match="payload"):
        maximum.seal(payload + b"x", binding())
    with pytest.raises(ValueError, match="limit"):
        cipher(max_payload_bytes=CONTINUATION_MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(ContinuationInvalid):
        maximum.open(token + "a", binding())


def test_prefix_digest_is_canonical_and_detects_scope_changes():
    one = {"tools": [{"name": "search"}], "messages": [{"role": "user", "text": "NNA?"}], "system": "sys"}
    same = {"system": "sys", "messages": [{"text": "NNA?", "role": "user"}], "tools": [{"name": "search"}]}
    assert prefix_digest(one) == prefix_digest(same)
    for altered in ({**one, "system": "other"}, {**one, "tools": []}, {**one, "messages": [{"role": "user", "text": "AUM?"}]}):
        assert prefix_digest(one) != prefix_digest(altered)
    with pytest.raises(ValueError, match="prefix"):
        prefix_digest({"not-json": float("nan")})


@pytest.mark.parametrize("kwargs", [{"max_age_seconds": 0}, {"max_age_seconds": -1}, {"max_age_seconds": 2_592_001},
                                    {"max_payload_bytes": 0}, {"max_payload_bytes": 1_048_577}])
def test_invalid_configuration_is_refused(kwargs):
    options = {"max_age_seconds": 60, **kwargs}
    with pytest.raises(ValueError):
        Continuations(KEY, **options)


def test_short_key_and_untrusted_binding_are_refused():
    with pytest.raises(ValueError):
        Continuations(b"short", max_age_seconds=60)
    with pytest.raises(ValueError):
        cipher().seal(PRIVATE, {"principal": "alice"})
    with pytest.raises(ContinuationInvalid):
        cipher().open("bad", {"principal": "alice"})
    with pytest.raises(ValueError):
        cipher().seal(PRIVATE, replace(binding(), prefix="invalid"))
