"""Shared argument validators for evaluation runs across pipeline, eval, and service layers."""
from __future__ import annotations

import re
from pathlib import Path

RUNTIME_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ARM_LIST = re.compile(r"^[a-z][a-z0-9_-]*(?:,[a-z][a-z0-9_-]*)*$")


def check_runtimes(label: Path | str, runtimes: object) -> list[str]:
    """Validate that runtimes is a non-empty list of valid runtime ids."""
    if not isinstance(runtimes, list) or not runtimes:
        raise ValueError(f"{label}: runtimes must be a non-empty list of runtime ids")
    out: list[str] = []
    for r in runtimes:
        if not isinstance(r, str) or not RUNTIME_ID.match(r):
            raise ValueError(f"{label}: runtime id {r!r} must be a runtime id, never a location")
        if r not in out:
            out.append(r)
    return out


def check_arm_shape(arm: object) -> list[str]:
    """Validate that arm is a non-empty comma-separated list of lowercase arm tokens."""
    if not isinstance(arm, str):
        raise ValueError("arm is not valid")
    parts = [c.strip() for c in arm.split(",") if c.strip()]
    if not parts or not ARM_LIST.fullmatch(",".join(parts)):
        raise ValueError("arm is not valid")
    return list(dict.fromkeys(parts))
