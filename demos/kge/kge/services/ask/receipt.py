"""The answer receipt: what the feedback log would keep of an answer, sealed with AES-256-GCM under a fresh random 96-bit
nonce, so the caller can neither read nor change it. The key is derived with HKDF-SHA256 from the session key
(session_key in the identity service) under the purpose label answer-receipt, so it needs no secret of its own and a
rotated session key refuses the receipts sealed before it. The release id is inside the receipt and bound to it as
associated data: a receipt of another release does not open."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from kge.ports import FEEDBACK_VERDICTS, FeedbackRecord

PURPOSE = b"answer-receipt"
NONCE_BYTES = 12
ANSWER_FIELDS = ("answer_id", "answer_mode", "answered_by", "plan")
RECEIPT_FIELDS = ("release", *ANSWER_FIELDS)


def receipt_key(session_key: bytes) -> bytes:
    """The 256-bit receipt key, derived from the session key under the purpose label."""
    if not session_key:
        raise ValueError("no session key to derive the receipt key from")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=PURPOSE).derive(session_key)


class Receipts:
    """Seals an answer's record into a receipt, and opens one, for one release under one session key."""

    def __init__(self, session_key: bytes, release: str):
        if not release:
            raise ValueError("a receipt needs the release id")
        self.release = release
        self.cipher = AESGCM(receipt_key(session_key))

    def seal(self, answer: dict) -> str:
        """The receipt of answer, which gives exactly answer_id, answer_mode, answered_by and plan."""
        if set(answer) != set(ANSWER_FIELDS):
            raise ValueError(f"a receipt holds {', '.join(ANSWER_FIELDS)} and nothing else")
        record = {"release": self.release, **{name: answer[name] for name in ANSWER_FIELDS}}
        plain = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        nonce = os.urandom(NONCE_BYTES)
        return base64.urlsafe_b64encode(nonce + self.cipher.encrypt(nonce, plain, self.release.encode())).decode()

    def open(self, token: str) -> dict:
        """The record a receipt of this release holds; ValueError for anything else: changed, of another release, sealed
        under another key, or not a receipt."""
        sealed = sealed_bytes(token)
        try:
            plain = self.cipher.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], self.release.encode())
        except InvalidTag:
            raise ValueError("the receipt does not open: changed, of another release or under another key") from None
        record = json.loads(plain)
        if not isinstance(record, dict) or set(record) != set(RECEIPT_FIELDS) or record["release"] != self.release:
            raise ValueError("the receipt is not of this release")
        return record

    def feedback(self, token: str, verdict: str) -> FeedbackRecord:
        """The feedback log's record of verdict on the answer the receipt was given with."""
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(FEEDBACK_VERDICTS)}")
        record = self.open(token)
        return FeedbackRecord(release=record["release"], time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              verdict=verdict, answer_mode=record["answer_mode"], answered_by=record["answered_by"],
                              plan=record["plan"], receipt_id=receipt_id(token))


def sealed_bytes(token: str) -> bytes:
    """The nonce and ciphertext a receipt token carries; ValueError for a token that is not one."""
    try:
        sealed = base64.urlsafe_b64decode(token.encode("ascii"))
    except (AttributeError, UnicodeError, binascii.Error, ValueError):
        raise ValueError("not an answer receipt") from None
    if len(sealed) <= NONCE_BYTES + 16:
        raise ValueError("not an answer receipt")
    return sealed


def receipt_id(token: str) -> str:
    """The receipt's id in the feedback log: a SHA-256 digest of its sealed bytes, unique to the receipt (its nonce is
    random) and telling nothing of what it holds, so repeats of one receipt share it."""
    return hashlib.sha256(sealed_bytes(token)).hexdigest()[:32]
