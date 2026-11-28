"""The feedback log, the FeedbackLog port (kge/ports.py): JSON lines under the run state's feedback/ folder
(folder.py), or, with STORE_URL set, one object a record under feedback/<release>/ in that object store (objstore.py).
Callers take a log from feedback_log, never from a backend's module."""
from __future__ import annotations

from pathlib import Path

from kge.adapters.objstore import open_store
from kge.pipeline.settings import settings
from kge.ports import FEEDBACK_VERDICTS, FeedbackLog, FeedbackRecord

__all__ = ["FEEDBACK_VERDICTS", "FeedbackLog", "FeedbackRecord", "feedback_log"]


def feedback_log(state_dir: Path) -> FeedbackLog:
    """The log this deployment keeps: the object store STORE_URL names, else <state_dir>/feedback/."""
    url = (settings().STORE_URL or "").strip()
    if url:
        from kge.adapters.feedbacklog.objstore import StoreFeedbackLog
        return StoreFeedbackLog(open_store(url, private=True))
    from kge.adapters.feedbacklog.folder import FolderFeedbackLog
    return FolderFeedbackLog(Path(state_dir) / "feedback")
