"""The pilot's role-audit log: one object an entry under access/<client>/role-audit/<UTC day>/ in the object store,
beside the access audit, each written only where no object of its key exists (version "0"), so none is ever
overwritten. The newest entries are read a day's folder at a time, today's first, so a read lists only the days it
needs and never the whole history."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

from kge.adapters.roleaudit.record import line_of
from kge.ports import ObjectStore, RoleAuditEntry

DAYS_READ = 31
"""How many days back, today's included, an administrator's read of the newest entries looks."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoreRoleAuditLog:
    """access/<client>/role-audit/<YYYYMMDD>/<UTC time>-<random>.json in store."""

    def __init__(self, store: ObjectStore, client: str, clock: Callable[[], datetime] = utc_now):
        self.store = store
        self.prefix = f"access/{client}/role-audit/"
        self.clock = clock

    def append(self, entry: RoleAuditEntry) -> None:
        data = line_of(entry)
        now = self.clock()
        key = f"{self.prefix}{now:%Y%m%d}/{now:%Y%m%dT%H%M%S%fZ}-{secrets.token_hex(8)}.json"
        self.store.put_version(key, data, "0")

    def newest(self, limit: int) -> list[dict]:
        """At most limit entries, newest first, from the last DAYS_READ days: each day's folder listed in turn, today's
        first, until limit entries are found."""
        keys: list[str] = []
        today = self.clock()
        for back in range(DAYS_READ):
            if len(keys) >= limit:
                break
            day = today - timedelta(days=back)
            keys += sorted(self.store.list(f"{self.prefix}{day:%Y%m%d}/"), reverse=True)
        keys = keys[:max(0, limit)]
        return [json.loads(data) for key in keys if (data := self.store.get(key)) is not None]
