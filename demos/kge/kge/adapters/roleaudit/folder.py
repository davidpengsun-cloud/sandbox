"""The demo's role-audit log: one JSON-lines file in the run state, mode 600, each entry appended by one write and none
rewritten."""
from __future__ import annotations

import json
import os
from pathlib import Path

from kge.adapters.roleaudit.record import line_of
from kge.ports import RoleAuditEntry

BLOCK = 64 * 1024
"""How much of the file's end a read of the newest entries takes at a time."""


class FolderRoleAuditLog:
    """<path>, appended to."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, entry: RoleAuditEntry) -> None:
        line = line_of(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(handle, line)
        finally:
            os.close(handle)

    def newest(self, limit: int) -> list[dict]:
        """At most limit entries, newest first, read from the file's end a block at a time until limit whole lines are
        in hand, so a read never loads the whole history."""
        limit = max(0, limit)
        if limit == 0 or not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            tail = b""
            while end > 0 and tail.count(b"\n") <= limit:
                start = max(0, end - BLOCK)
                handle.seek(start)
                tail = handle.read(end - start) + tail
                end = start
        pieces = tail.split(b"\n")
        if end > 0:
            pieces = pieces[1:]
        lines = [line for line in pieces if line.strip()]
        return [json.loads(line) for line in reversed(lines[-limit:])]
