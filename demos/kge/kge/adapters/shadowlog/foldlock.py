"""A non-expiring conditional mutex around a client's entire evidence fold.

The graph store does not provide a fencing token for its multi-write projection. Consequently a time-limited lease
cannot safely be taken over while its former holder might still write. A crashed holder remains locked until an
operator has verified that the process and any outstanding graph operations have stopped and explicitly clears the
observed token and version. Normal observation appends do not take this lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog.record import release_segment
from kge.pipeline.settings import settings
from kge.ports import ObjectStore, StoreConflict, StoreError

LOCK = "_fold-lock.json"


class FoldBusy(RuntimeError):
    """A fold is already running or its mutex needs operator recovery."""


def _record(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        value = json.loads(data)
        if value == {}:
            return None
        if not isinstance(value, dict) or set(value) != {"version", "token", "host", "pid", "started_at"}:
            raise ValueError
        if (type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["token"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", value["token"])
                or not isinstance(value["host"], str) or not value["host"] or type(value["pid"]) is not int
                or value["pid"] < 1 or not isinstance(value["started_at"], str) or not value["started_at"]):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeDecodeError):
        raise FoldBusy("the fold mutex is unreadable; operator recovery is required") from None


class FoldMutex:
    """One object's conditional writes serialize all folds sharing the same client evidence store."""

    def __init__(self, store: ObjectStore, key: str):
        self.store = store
        self.key = key

    def _get(self) -> tuple[bytes | None, str]:
        try:
            return self.store.get_version(self.key)
        except OSError:
            raise StoreError("the fold mutex store could not be read") from None

    def _put(self, data: bytes, version: str) -> str:
        try:
            return self.store.put_version(self.key, data, version)
        except OSError:
            raise StoreError("the fold mutex store could not be written") from None

    def inspect(self) -> tuple[dict | None, str]:
        data, version = self._get()
        return _record(data), version

    def inspect_recovery(self) -> dict:
        """Show a valid holder or a corrupt object's digest and version, never its raw contents."""
        data, version = self._get()
        try:
            return {"holder": _record(data), "object_version": version, "corrupt_digest": None}
        except FoldBusy:
            return {"holder": None, "object_version": version, "corrupt_digest": hashlib.sha256(data).hexdigest()}

    def inspect_observed(self) -> dict:
        """Describe the current object without disclosing its token or unreadable contents."""
        data, version = self._get()
        try:
            holder = _record(data)
            public = {key: holder[key] for key in ("host", "pid", "started_at")} if holder else None
            corrupt = False
        except FoldBusy:
            public, corrupt = None, True
        return {"holder": public, "object_version": version,
                "object_digest": hashlib.sha256(data).hexdigest() if data is not None else None,
                "corrupt": corrupt}

    def recover_observed(self, expected_digest: str, expected_version: str, *, holder_stopped: bool) -> None:
        """Clear only the exact inspected bytes and generation after an operator confirms no writer remains."""
        if not holder_stopped:
            raise FoldBusy("confirm that the holder and all its graph operations have stopped")
        data, version = self._get()
        if (data is None or version != expected_version or not isinstance(expected_digest, str)
                or hashlib.sha256(data).hexdigest() != expected_digest):
            raise FoldBusy("the fold mutex changed; inspect it again")
        try:
            holder = _record(data)
        except FoldBusy:
            holder = True
        if holder is None:
            raise FoldBusy("no fold mutex is held")
        try:
            self._put(b"{}", version)
        except StoreConflict:
            raise FoldBusy("the fold mutex changed; inspect it again") from None

    def acquire(self) -> str:
        record, version = self.inspect()
        if record is not None:
            raise FoldBusy("another fold holds the mutex; verify it has stopped before operator recovery")
        token = secrets.token_hex(16)
        data = {"version": 1, "token": token, "host": socket.gethostname(), "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            self._put(json.dumps(data, sort_keys=True).encode(), version)
        except StoreConflict:
            raise FoldBusy("another fold acquired the mutex") from None
        return token

    def release(self, token: str) -> None:
        record, version = self.inspect()
        if record is None or record["token"] != token:
            raise FoldBusy("the fold mutex is no longer owned by this fold")
        self._put(b"{}", version)

    def recover(self, token: str, expected_version: str, *, holder_stopped: bool) -> None:
        """Clear exactly the inspected lock only after an operator proves its holder and graph writes are stopped."""
        if not holder_stopped:
            raise FoldBusy("confirm that the holder and all its graph operations have stopped")
        record, version = self.inspect()
        if record is None or record["token"] != token or version != expected_version:
            raise FoldBusy("the fold mutex changed; inspect it again")
        self._put(b"{}", version)

    def recover_corrupt(self, expected_digest: str, expected_version: str, *, holder_stopped: bool) -> None:
        """Clear exactly the corrupt object inspected, only after confirming no graph writer remains."""
        if not holder_stopped:
            raise FoldBusy("confirm that the holder and all its graph operations have stopped")
        data, version = self._get()
        if data is None or version != expected_version or hashlib.sha256(data).hexdigest() != expected_digest:
            raise FoldBusy("the fold mutex changed; inspect it again")
        try:
            _record(data)
        except FoldBusy:
            self._put(b"{}", version)
            return
        raise FoldBusy("the fold mutex is valid; recover it by its token")


def fold_mutex(state_dir: Path, client: str) -> FoldMutex:
    """Use the same client/store identity as ShadowSeals, including its local folder fallback."""
    url = (settings().STORE_URL or "").strip()
    graph = (settings().GRAPH_STORE or "").strip().lower()
    if graph == "bigquery" and not url.startswith("gs://"):
        raise FoldBusy("a BigQuery fold needs a shared gs:// STORE_URL for its mutex")
    if url:
        return FoldMutex(open_store(url, private=True), f"shadow/{release_segment(client)}/{LOCK}")
    root = (Path(state_dir) / "shadow").resolve()
    return FoldMutex(open_store(str(root), private=True), LOCK)


@contextmanager
def held(mutex: FoldMutex):
    """Hold through reads, seals, graph writes and report; release only the token this call acquired."""
    token = mutex.acquire()
    try:
        yield
    except BaseException as primary:
        try:
            mutex.release(token)
        except Exception:
            primary.add_note("fold mutex release failed; inspect it before retrying")
        raise
    else:
        mutex.release(token)


def main() -> int:
    """Inspect or explicitly recover a mutex after verifying the holder and graph operations have stopped."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("action", choices=("inspect", "recover"))
    parser.add_argument("--client", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--token")
    parser.add_argument("--version")
    parser.add_argument("--corrupt-digest")
    parser.add_argument("--object-digest", help="SHA-256 of the exact object bytes from job inspection")
    parser.add_argument("--holder-and-graph-stopped", action="store_true")
    args = parser.parse_args()
    try:
        mutex = fold_mutex(args.state_dir, args.client)
        if args.action == "inspect":
            print(json.dumps(mutex.inspect_recovery()))
        else:
            if args.object_digest and not args.token and not args.corrupt_digest:
                mutex.recover_observed(args.object_digest, args.version, holder_stopped=args.holder_and_graph_stopped)
            elif args.corrupt_digest and not args.token and not args.object_digest:
                mutex.recover_corrupt(args.corrupt_digest, args.version, holder_stopped=args.holder_and_graph_stopped)
            elif args.token and not args.corrupt_digest and not args.object_digest:
                mutex.recover(args.token, args.version, holder_stopped=args.holder_and_graph_stopped)
            else:
                raise FoldBusy("provide exactly one inspected token or corrupt digest")
            print("fold mutex cleared")
        return 0
    except (FoldBusy, StoreError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
