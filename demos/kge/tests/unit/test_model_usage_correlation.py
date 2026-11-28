"""The question capability is confidential, authenticated and scoped to a separately verified caller."""
from __future__ import annotations

import base64
import uuid
from dataclasses import replace

import pytest

from kge.adapters.modelusage import Actor, CorrelationInvalid, CorrelationTokens

KEY = b"one-random-shared-test-key-32bytes"
NEW_KEY = b"two-random-shared-test-key-32bytes"
ACTOR = Actor("meridian", "person", "alice@example.test", "cerebro")
QUESTION = str(uuid.uuid4())
CONVERSATION = str(uuid.uuid4())


def tokens(now=1000, keys=(KEY,), age=60):
    return CorrelationTokens(keys, max_age_seconds=age, clock=lambda: now)


def test_question_token_is_opaque_and_valid_on_another_instance():
    issuer, another = tokens(), tokens(now=1020)
    token = issuer.issue(ACTOR, QUESTION, "agent", CONVERSATION)
    again = issuer.issue(ACTOR, QUESTION, "agent", CONVERSATION)
    assert token != again
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    for private in (b"alice", b"meridian", QUESTION.encode(), CONVERSATION.encode(), b"agent"):
        assert private not in raw
    assert another.verify(token, ACTOR, "agent", CONVERSATION) == QUESTION


@pytest.mark.parametrize("actor", [replace(ACTOR, client="other"), replace(ACTOR, who="bob@example.test"),
                                   replace(ACTOR, kind="service"), replace(ACTOR, app="discover")])
def test_question_token_cannot_cross_authenticated_scope(actor):
    token = tokens().issue(ACTOR, QUESTION, "agent", CONVERSATION)
    with pytest.raises(CorrelationInvalid) as caught:
        tokens().verify(token, actor, "agent", CONVERSATION)
    assert "alice" not in str(caught.value) and QUESTION not in str(caught.value)


def test_purpose_substitution_and_token_tampering_fail_closed():
    token = tokens().issue(ACTOR, QUESTION, "agent", CONVERSATION)
    with pytest.raises(CorrelationInvalid):
        tokens().verify(token, ACTOR, "catalogue", CONVERSATION)
    with pytest.raises(CorrelationInvalid):
        tokens().verify(token, ACTOR, "agent", str(uuid.uuid4()))
    altered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(CorrelationInvalid):
        tokens().verify(altered, ACTOR, "agent", CONVERSATION)
    for invalid in ("", "💥", "==", token + "=", token + "x" * 9000):
        with pytest.raises(CorrelationInvalid):
            tokens().verify(invalid, ACTOR, "agent", CONVERSATION)


def test_expired_hitl_can_be_reissued_after_new_authentication_without_changing_id():
    old = tokens().issue(ACTOR, QUESTION, "agent", CONVERSATION)
    with pytest.raises(CorrelationInvalid):
        tokens(now=1060).verify(old, ACTOR, "agent", CONVERSATION)
    with pytest.raises(CorrelationInvalid):
        tokens(now=999).verify(old, ACTOR, "agent", CONVERSATION)
    renewed = tokens(now=2000).issue(ACTOR, QUESTION, "agent", CONVERSATION)
    assert tokens(now=2010).verify(renewed, ACTOR, "agent", CONVERSATION) == QUESTION


def test_rotation_verifies_old_key_then_new_key_and_can_retire_old():
    before = tokens(keys=(KEY,)).issue(ACTOR, QUESTION, "agent", CONVERSATION)
    rotation = tokens(keys=(NEW_KEY, KEY))
    assert rotation.verify(before, ACTOR, "agent", CONVERSATION) == QUESTION
    after = rotation.issue(ACTOR, QUESTION, "agent", CONVERSATION)
    assert tokens(keys=(NEW_KEY,)).verify(after, ACTOR, "agent", CONVERSATION) == QUESTION
    with pytest.raises(CorrelationInvalid):
        tokens(keys=(NEW_KEY,)).verify(before, ACTOR, "agent", CONVERSATION)


def test_invalid_key_clock_and_untrusted_actor_are_rejected():
    for keys in ((), (b"short",), (KEY,) * 5, KEY):
        with pytest.raises(ValueError):
            CorrelationTokens(keys)
    for age in (0, -1, 86401, True):
        with pytest.raises(ValueError):
            CorrelationTokens((KEY,), max_age_seconds=age)
    with pytest.raises(ValueError):
        tokens(now=-1).issue(ACTOR, QUESTION, "agent", CONVERSATION)
    with pytest.raises(ValueError):
        tokens().issue({"who": "alice"}, QUESTION, "agent", CONVERSATION)
    with pytest.raises(CorrelationInvalid):
        tokens().verify(tokens().issue(ACTOR, QUESTION, "agent", CONVERSATION), {"who": "alice"}, "agent", CONVERSATION)
    with pytest.raises(ValueError):
        tokens().issue(ACTOR, "question from user", "agent", CONVERSATION)
    with pytest.raises(ValueError):
        tokens().issue(ACTOR, QUESTION, "agent", "not-a-conversation-id")
