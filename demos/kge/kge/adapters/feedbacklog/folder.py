"""The demo's feedback log: JSON lines, one file a release, under the run state's feedback/ folder, mode 600, each
record appended by one write and none rewritten."""
from __future__ import annotations

import os
from pathlib import Path

from kge.adapters.feedbacklog.record import line_of, release_segment
from kge.ports import FeedbackRecord


class FolderFeedbackLog:
    """<folder>/<release>.jsonl, appended to."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def append(self, record: FeedbackRecord) -> None:
        line = line_of(record)
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{release_segment(record.release)}.jsonl"
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(handle, line)
        finally:
            os.close(handle)
