"""What both role-audit logs write: one entry as one line of JSON, its fields and nothing else."""
from __future__ import annotations

import json
from dataclasses import asdict

from kge.ports import RoleAuditEntry

OUTCOMES = ("allowed", "refused")


def line_of(entry: RoleAuditEntry) -> bytes:
    """The entry as one line of JSON; ValueError for anything but a RoleAuditEntry with a known outcome."""
    if type(entry) is not RoleAuditEntry:
        raise ValueError("a role-audit log keeps a RoleAuditEntry and nothing else")
    if entry.outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, not {entry.outcome!r}")
    row = {**asdict(entry), "cerebro_roles": list(entry.cerebro_roles)}
    return (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
