"""One writing job per client, through the object store's conditional writes.

A released lease is an empty JSON object. The port has no conditional delete: deleting after a read could remove
the next holder's lease. The empty record is taken over by version, just as an expired lease is.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from kge.adapters.objstore import check
from kge.ports import ObjectStore, StoreError

LEASE_SECONDS = 600
RENEW_SECONDS = 120
LEASE_FILE = "lease.json"


class Refused(ValueError):
    """Another job holds the client's lease, or this job no longer holds it."""


@dataclass
class Lease:
    """Serializable in the run folder; the token stays fixed across renewals, including in another process."""

    client: str
    command: str
    token: str
    started_at: str
    expires_at: str
    version: str = "0"


def key(client: str) -> str:
    return check(f"jobs/{client}/lease.json")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _read(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        record = json.loads(data)
        if record == {}:
            return None
        if not isinstance(record, dict) or not all(isinstance(record.get(n), str) and record[n]
                                                   for n in ("client", "command", "token", "started_at", "expires_at")):
            raise ValueError
        if datetime.fromisoformat(record["expires_at"]).tzinfo is None:
            raise ValueError
        return record
    except (ValueError, TypeError):
        raise Refused("the job lease cannot be read: nothing was changed") from None


def _active(record: dict | None) -> bool:
    return record is not None and datetime.fromisoformat(record["expires_at"]) > _now()


def _busy(client: str, record: dict) -> Refused:
    return Refused(f"another job is running for {client}: {record['command']} since {record['started_at']}; "
                   f"its lease ends at {record['expires_at']} unless it renews")


def holder(store: ObjectStore, client: str) -> dict | None:
    """The active holder, or None for an absent, released or expired lease."""
    record = _read(store.get(key(client)))
    return record if _active(record) else None


def take(store: ObjectStore, client: str, command: str) -> Lease:
    """Create with version 0, or take over a released or expired record with its observed version."""
    data, version = store.get_version(key(client))
    record = _read(data)
    if _active(record):
        raise _busy(client, record)
    now = _now()
    lease = Lease(client, command, secrets.token_hex(16), _stamp(now), _stamp(now + timedelta(seconds=LEASE_SECONDS)))
    try:
        lease.version = store.put_version(key(client), json.dumps(asdict(lease)).encode(), version)
    except StoreError:
        current = holder(store, client)
        if current:
            raise _busy(client, current) from None
        raise
    return lease


def _owned(store: ObjectStore, lease: Lease) -> tuple[dict, str]:
    data, version = store.get_version(key(lease.client))
    record = _read(data)
    if not _active(record) or record["token"] != lease.token:
        raise Refused(f"the job lease for {lease.client} is no longer held by this {lease.command}")
    return record, version


def renew(store: ObjectStore, lease: Lease) -> Lease:
    """Extend only the still-active holder; a lost or expired lease is never revived."""
    record, version = _owned(store, lease)
    record["expires_at"] = _stamp(_now() + timedelta(seconds=LEASE_SECONDS))
    lease.version = store.put_version(key(lease.client), json.dumps(record).encode(), version)
    lease.expires_at = record["expires_at"]
    return lease


def release(store: ObjectStore, lease: Lease) -> None:
    """Release only this token, even if the run folder has an earlier renewal's version."""
    data, version = store.get_version(key(lease.client))
    record = _read(data)
    if record is None or record["token"] != lease.token:
        return
    lease.version = store.put_version(key(lease.client), b"{}", version)


def heartbeat(store: ObjectStore, lease: Lease) -> threading.Event:
    """Renew every 120 seconds until the returned event is set. A renewal failure sets it too and logs the failure."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(RENEW_SECONDS):
            try:
                renew(store, lease)
            except (Refused, StoreError) as exc:
                logging.getLogger(__name__).error("job lease: %s", exc)
                stop.set()

    threading.Thread(target=beat, name=f"lease-{lease.client}", daemon=True).start()
    return stop
