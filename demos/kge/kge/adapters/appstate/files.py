"""Onboarding application state in the pack store's files, units/<unit>/unit.yaml and history.jsonl: the AppState port
(kge/ports.py) locally, and on a remote store through the pack store's mirror."""
from __future__ import annotations

import contextlib
import json

import yaml

from kge.adapters.appstate import _change, _digest
from kge.ports import StateConflict, StoreError


class FilesState:
    """Commit 4's files, including its process-local lock.

    The manifest records how many history entries its commit left (HISTORY); the history file is written first and
    unit.yaml last, so a crash between them leaves entries no manifest counts, which are not read and which the next
    commit writes again without. delete does nothing: the caller removes the whole unit prefix, these two files with it,
    and the job record is a file reset removes itself."""

    tables = False

    def __init__(self, store, access_store=None):
        self.store = store
        self._access_store = access_store
        self._access_read = None

    @property
    def access_store(self):
        """Open the separate store only for access operations; ordinary app state needs no access configuration."""
        if callable(self._access_store):
            self._access_store = self._access_store()
        return self._access_store

    @contextlib.contextmanager
    def lock(self):
        with self.store.lock() if self.store is not None else contextlib.nullcontext():
            yield

    def units(self) -> list[str]:
        return sorted({path.split("/")[1] for path in self.store.list("units") if path.count("/") == 2 and path.endswith("/unit.yaml")})

    HISTORY = "_history_entries"

    def _raw(self, unit: str) -> dict:
        return yaml.safe_load(self.store.read(f"units/{unit}/unit.yaml")) or {}

    def manifest(self, unit: str) -> dict:
        raw = self._raw(unit)
        raw.pop(self.HISTORY, None)
        return raw

    def history(self, unit: str) -> list[dict]:
        try:
            lines = self.store.read(f"units/{unit}/history.jsonl").decode().splitlines()
        except FileNotFoundError:
            return []
        entries = [json.loads(line) for line in lines if line.strip()]
        try:
            counted = self._raw(unit).get(self.HISTORY)
        except FileNotFoundError:
            counted = None
        return entries[:counted] if isinstance(counted, int) and not isinstance(counted, bool) else entries

    def append_history(self, unit: str, entries: list[dict]) -> None:
        """Keep base-unit audit without a manifest; the caller holds the store lock and remote writes compare generations."""
        if not entries:
            return
        try:
            manifest = self.manifest(unit)
        except FileNotFoundError:
            history = self.history(unit)
            self.store.write(f"units/{unit}/history.jsonl",
                             "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in [*history, *entries]).encode())
        else:
            self.commit(unit, manifest, entries)

    def snapshot(self, unit: str) -> tuple[dict, list[dict]]:
        """The manifest and history as the files backend has always read them: each file is replaced atomically, but
        the pair is not a cross-file snapshot."""
        return self.manifest(unit), self.history(unit)

    def capture_units(self, units) -> object:
        """Keep raw files, including uncounted history and absent files, without reserialising their contents."""
        captured = {}
        for unit in sorted(set(units)):
            for name in ("unit.yaml", "history.jsonl"):
                key = f"units/{unit}/{name}"
                try:
                    captured[key] = self.store.read(key)
                except FileNotFoundError:
                    captured[key] = None
        return captured

    def restore_units(self, captured) -> None:
        """Restore the exact file bytes and absences; open requests and status inputs live in the manifest."""
        for key, data in captured.items():
            present = self.store.exists(key)
            if data is None:
                if present:
                    self.store.delete(key)
            elif not present or self.store.read(key) != data:
                self.store.write(key, data)

    def commit(self, unit: str, manifest: dict, additions: list[dict]) -> dict:
        """Write the manifest against the generation the caller read, which a remote store's write compares, never one
        read here: history() reads unit.yaml again, and would take another writer's commit as the base."""
        name = f"units/{unit}/unit.yaml"
        versions = getattr(self.store, "_versions", None)
        read = versions is not None and name in versions
        generation = versions.get(name) if read else None
        history = self.history(unit)
        if read:
            versions[name] = generation
        for entry in additions:
            if entry.get("step") == "request-closed" and any(
                    old.get("step") == "request-closed" and
                    (old.get("detail") or {}).get("id") == (entry.get("detail") or {}).get("id") for old in history):
                continue
            history.append(entry)
        self.store.write(f"units/{unit}/history.jsonl",
                         "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in history).encode())
        self.store.write(f"units/{unit}/unit.yaml",
                         yaml.safe_dump({**manifest, self.HISTORY: len(history)}, sort_keys=False).encode())
        return manifest

    def delete(self, unit: str) -> None:
        pass

    def job(self) -> dict | None:
        return None

    def save_job(self, job: dict) -> None:
        raise RuntimeError("the files backend keeps job state in its existing file")

    def clear_job(self) -> None:
        pass

    GRANTS = "access-grants.json"
    AUDIT = "access-audit.jsonl"

    def _access_snapshot(self):
        if self.access_store is None:
            raise ValueError("access state needs its separate object store")
        data, version = self.access_store.get_version(self.GRANTS)
        if self.access_store.get_version(self.GRANTS) != (data, version):
            raise StateConflict("access changed while reading; reload")
        record = json.loads(data) if data is not None else {"grants": [], "audit": []}
        return record, version

    def access_grants(self) -> list[dict]:
        self._access_read = self._access_snapshot()
        return json.loads(json.dumps(self._access_read[0]["grants"]))

    def access_digest(self) -> str:
        return _digest(self.access_grants())

    def access_audit(self, limit: int = 200) -> list[dict]:
        record, _ = self._access_snapshot()
        return list(reversed(record["audit"]))[:max(0, limit)]

    def _access_write(self, username: str, row: dict | None, by: str, via: str) -> dict | None:
        """The grants envelope is the atomic commit: grants and audit share one conditional write. The JSONL is a
        readable projection, never the authority; a crash while projecting cannot lose an audit or expose half a
        change. A later write repairs it. Projection failure does not turn a committed change into a failed one."""
        record, version = self._access_read if self._access_read is not None else self._access_snapshot()
        self._access_read = None
        audit = record["audit"]
        rows, entry = _change(record["grants"], audit[-1]["sequence"] if audit else 0,
                              audit[-1]["digest"] if audit else None, username, row, by, via)
        if entry is None:
            if self._access_snapshot()[1] != version:
                raise StateConflict("access changed by someone else; reload")
            return None
        updated = {"grants": rows, "audit": [*audit, entry]}
        try:
            self.access_store.put_version(self.GRANTS, json.dumps(updated, ensure_ascii=False).encode(), version)
        except StoreError as exc:
            if self.access_store.get_version(self.GRANTS)[1] != version:
                raise StateConflict("access changed by someone else; reload") from None
            raise exc
        try:
            _, audit_version = self.access_store.get_version(self.AUDIT)
            current, _ = self._access_snapshot()
            self.access_store.put_version(self.AUDIT, "".join(json.dumps(e, ensure_ascii=False) + "\n"
                                                             for e in current["audit"]).encode(), audit_version)
        except (StoreError, StateConflict):
            pass
        return entry

    def access_migrate(self, rows: list[dict], *, by: str) -> list[dict]:
        """Commit the complete seed and its audit as one conditional envelope write."""
        record, version = self._access_read if self._access_read is not None else self._access_snapshot()
        self._access_read = None
        if record["grants"] or record["audit"]:
            raise StateConflict("access already initialized; reload")
        grants, audit = [], []
        for row in rows:
            grants, entry = _change(grants, len(audit), audit[-1]["digest"] if audit else None,
                                    row["username"], row, by, "migration")
            audit.append(entry)
        try:
            self.access_store.put_version(self.GRANTS, json.dumps({"grants": grants, "audit": audit},
                                                                 ensure_ascii=False).encode(), version)
        except StoreError as exc:
            if self.access_store.get_version(self.GRANTS)[1] != version:
                raise StateConflict("access changed by someone else; reload") from None
            raise exc
        try:
            _, audit_version = self.access_store.get_version(self.AUDIT)
            current, _ = self._access_snapshot()
            self.access_store.put_version(self.AUDIT, "".join(json.dumps(e, ensure_ascii=False) + "\n"
                                                             for e in current["audit"]).encode(), audit_version)
        except (StoreError, StateConflict):
            pass
        return audit

    def access_put(self, row: dict, *, by: str, via: str) -> dict:
        return self._access_write(row["username"], row, by, via)

    def access_delete(self, username: str, *, by: str, via: str) -> dict | None:
        return self._access_write(username, None, by, via)
