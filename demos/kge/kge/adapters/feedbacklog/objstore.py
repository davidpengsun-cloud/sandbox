"""The pilot's feedback log: one object a record under feedback/<release>/ in the object store, each written only where
no object of its key exists (version "0"), so none is ever overwritten."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from kge.adapters.feedbacklog.record import line_of, release_segment
from kge.ports import FeedbackRecord, ObjectStore

PREFIX = "feedback"


class StoreFeedbackLog:
    """feedback/<release>/<UTC time>-<random>.json in store."""

    def __init__(self, store: ObjectStore):
        self.store = store

    def append(self, record: FeedbackRecord) -> None:
        data = line_of(record)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        key = f"{PREFIX}/{release_segment(record.release)}/{stamp}-{secrets.token_hex(8)}.json"
        self.store.put_version(key, data, "0")
