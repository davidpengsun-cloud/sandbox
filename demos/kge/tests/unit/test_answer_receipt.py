"""The answer receipt (#464, requirement 8): sealed with AES-256-GCM under a key derived from the session key, opened only
by the same release under the same key, and refused (ValueError, the route's 400) when changed, of another release,
across a key rotation, or not a receipt at all."""
from __future__ import annotations

import base64
import json

import pytest

from kge.ports import FeedbackRecord
from kge.services.ask.receipt import Receipts, receipt_key

KEY = b"k" * 40
ROTATED = b"r" * 40
ANSWER = {"answer_id": "turn-7", "answer_mode": "certified", "answered_by": "rules",
          "plan": {"measure": "nna", "group_by": ["region"], "filters": [{"column": "client_id", "value": "<client>"}]}}


def test_a_receipt_opens_to_the_record_it_sealed():
    receipts = Receipts(KEY, "rel-1")
    token = receipts.seal(ANSWER)
    assert receipts.open(token) == {"release": "rel-1", **ANSWER}
    assert Receipts(KEY, "rel-1").open(token) == {"release": "rel-1", **ANSWER}


def test_two_receipts_of_one_answer_differ_and_hide_the_record():
    receipts = Receipts(KEY, "rel-1")
    first, second = receipts.seal(ANSWER), receipts.seal(ANSWER)
    assert first != second
    for token in (first, second):
        raw = base64.urlsafe_b64decode(token)
        assert b"certified" not in raw and b"nna" not in raw and b"rel-1" not in raw


@pytest.mark.parametrize("position", [0, 5, 13, -1])
def test_a_tampered_receipt_is_refused(position):
    receipts = Receipts(KEY, "rel-1")
    raw = bytearray(base64.urlsafe_b64decode(receipts.seal(ANSWER)))
    raw[position] ^= 0x01
    with pytest.raises(ValueError):
        receipts.open(base64.urlsafe_b64encode(bytes(raw)).decode())


def test_a_receipt_of_another_release_is_refused():
    token = Receipts(KEY, "rel-1").seal(ANSWER)
    with pytest.raises(ValueError):
        Receipts(KEY, "rel-2").open(token)


def test_a_receipt_across_a_key_rotation_is_refused():
    token = Receipts(KEY, "rel-1").seal(ANSWER)
    with pytest.raises(ValueError):
        Receipts(ROTATED, "rel-1").open(token)


@pytest.mark.parametrize("token", ["", "not a receipt", "AAAA", "@@@@", None, 7,
                                   base64.urlsafe_b64encode(b"\0" * 40).decode()])
def test_what_is_not_a_receipt_is_refused(token):
    with pytest.raises(ValueError):
        Receipts(KEY, "rel-1").open(token)


def test_the_receipt_key_is_not_the_session_key_and_follows_it():
    assert len(receipt_key(KEY)) == 32
    assert receipt_key(KEY) != KEY[:32]
    assert receipt_key(KEY) == receipt_key(KEY) != receipt_key(ROTATED)
    with pytest.raises(ValueError):
        receipt_key(b"")


def test_a_receipt_seals_its_fields_and_nothing_else():
    receipts = Receipts(KEY, "rel-1")
    for extra in ("question", "principal", "word", "figures"):
        with pytest.raises(ValueError):
            receipts.seal({**ANSWER, extra: "x"})
    with pytest.raises(ValueError):
        receipts.seal({k: v for k, v in ANSWER.items() if k != "plan"})


def test_feedback_on_a_receipt_is_the_log_record_and_checks_the_verdict():
    receipts = Receipts(KEY, "rel-1")
    token = receipts.seal(ANSWER)
    record = receipts.feedback(token, "not_what_i_meant")
    assert isinstance(record, FeedbackRecord)
    assert (record.release, record.verdict, record.answer_mode, record.answered_by, record.plan) == \
        ("rel-1", "not_what_i_meant", "certified", "rules", ANSWER["plan"])
    assert record.time.endswith("+00:00")
    assert record.receipt_id == receipts.feedback(token, "confirmed").receipt_id
    assert record.receipt_id != receipts.feedback(receipts.seal(ANSWER), "confirmed").receipt_id
    assert len(record.receipt_id) == 32 and ANSWER["answer_id"] not in record.receipt_id
    with pytest.raises(ValueError):
        receipts.feedback(token, "maybe")
    with pytest.raises(ValueError):
        Receipts(KEY, "rel-2").feedback(token, "confirmed")


def test_the_sealed_record_is_plain_json():
    receipts = Receipts(KEY, "rel-1")
    with pytest.raises(ValueError):
        receipts.seal({**ANSWER, "plan": float("nan")})
    assert json.dumps(receipts.open(receipts.seal(ANSWER)))
