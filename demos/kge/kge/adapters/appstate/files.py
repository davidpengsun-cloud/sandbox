"""Onboarding application state in the pack store's files, units/<unit>/unit.yaml and history.jsonl: the AppState port
(kge/ports.py) locally, and on a remote store through the pack store's mirror."""
from __future__ import annotations

import contextlib
import json

import yaml


class FilesState:
    """Commit 4's files, including its process-local lock.

    The manifest records how many history entries its commit left (HISTORY); the history file is written first and
    unit.yaml last, so a crash between them leaves entries no manifest counts, which are not read and which the next
    commit writes again without. delete does nothing: the caller removes the whole unit prefix, these two files with it,
    and the job record is a file reset removes itself."""

    tables = False

    def __init__(self, store):
        self.store = store

    @contextlib.contextmanager
    def lock(self):
        with self.store.lock():
            yield

    def units(self) -> list[str]:
        return sorted({path.split("/")[1] for path in self.store.list("units") if path.count("/") >= 2})

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

    def snapshot(self, unit: str) -> tuple[dict, list[dict]]:
        """The manifest and history as the files backend has always read them: each file is replaced atomically, but
        the pair is not a cross-file snapshot."""
        return self.manifest(unit), self.history(unit)

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
        if additions:
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
