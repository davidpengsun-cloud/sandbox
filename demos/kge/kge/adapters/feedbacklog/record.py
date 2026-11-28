"""What both feedback logs write: one record checked and put as one line of JSON, its fields and nothing else."""
from __future__ import annotations

import json
import re
from dataclasses import asdict

from kge.ports import FEEDBACK_VERDICTS, FeedbackRecord

RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def release_segment(release: str) -> str:
    """release, if it is one path segment a folder or a store key can be named by; ValueError otherwise."""
    if not isinstance(release, str) or not RELEASE_ID.fullmatch(release) or ".." in release:
        raise ValueError(f"{release!r} is not a release id")
    return release


def line_of(record: FeedbackRecord) -> bytes:
    """The record as one line of JSON, its fields alone; ValueError for anything but a FeedbackRecord with a known
    verdict and a release id."""
    if type(record) is not FeedbackRecord:
        raise ValueError("a feedback log keeps a FeedbackRecord and nothing else")
    release_segment(record.release)
    if record.verdict not in FEEDBACK_VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(FEEDBACK_VERDICTS)}, not {record.verdict!r}")
    try:
        text = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("a feedback record's plan must be plain JSON") from None
    return (text + "\n").encode("utf-8")
