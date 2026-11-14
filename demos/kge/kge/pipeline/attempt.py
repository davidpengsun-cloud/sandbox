"""Refuse automatic retries of commands whose failed inputs need an operator's fix."""
from __future__ import annotations

import sys

from kge.pipeline.settings import settings

WRITERS = ("onboard", "release", "sign-off", "passwords", "seed", "estate", "teardown", "onboard-request")


def retry_refusal(command: str) -> str | None:
    """None on a first attempt, or for a reader; a malformed task attempt is never a first attempt."""
    if command not in WRITERS:
        return None
    raw = settings().CLOUD_RUN_TASK_ATTEMPT
    if raw is None or not raw.strip():
        return None
    if not raw.isascii() or not raw.isdecimal():
        return "CLOUD_RUN_TASK_ATTEMPT must be a whole number at least 0"
    if raw.strip("0"):
        return f"an automatic retry cannot fix a failed {command}: fix the cause and run it again"
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m kge.pipeline.attempt <command>", file=sys.stderr)
        return 2
    reason = retry_refusal(args[0])
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
