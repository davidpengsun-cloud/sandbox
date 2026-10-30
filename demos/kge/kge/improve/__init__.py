"""The LangGraph improvement job: learns from business units' content and A/B misses, proposes allowlisted pack edits, and tests them by A/B."""
from __future__ import annotations


class CannotImprove(RuntimeError):
    """A pre-flight condition the improvement job needs that is not met (exit 2)."""
