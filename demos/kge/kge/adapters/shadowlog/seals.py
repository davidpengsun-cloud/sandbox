"""Conditional, per-event folded evidence, kept apart from raw shadow observations.

No observations are deleted here. The fold owns the record schema and reconciliation of late or pruned observations;
the seal store never infers that an observation is absent or silently rebuilds a partial event. Event ids include the
history position, so a rollback to a previously served release cannot replace its earlier event's evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from kge.adapters.shadowlog.record import ID, NAME, finite, forbidden_key, release_segment
from kge.ports import ObjectStore, StoreConflict

EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,249}")
DIGEST = re.compile(r"[0-9a-f]{64}")


def event_segment(event: str) -> str:
    """A history-indexed event can be longer than its valid 128-character release id."""
    if not isinstance(event, str) or not EVENT_ID.fullmatch(event) or ".." in event:
        raise ValueError(f"{event!r} is not an event id")
    return event


class StoreShadowSeals:
    """Event records under <prefix>/<release>/_sealed/<event>.json, with object-store conditional writes."""

    def __init__(self, store: ObjectStore, client: str | None):
        self.store = store
        self.prefix = f"shadow/{release_segment(client)}/" if client is not None else ""

    def key(self, release: str, event: str) -> str:
        return f"{self.prefix}{release_segment(release)}/_sealed/{event_segment(event)}.json"

    def _manifest(self):
        data, version = self.store.get_version(f"{self.prefix}_fold-manifest.json")
        if data is None:
            return None, version
        try:
            result = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("the fold manifest must be valid JSON") from None
        if (not isinstance(result, dict) or set(result) != {"version", "entries", "known"}
                or type(result["version"]) is not int or result["version"] != 2
                or not isinstance(result["entries"], list) or not isinstance(result["known"], list)):
            raise ValueError("the fold manifest has an invalid schema")
        if any(not isinstance(value, str) or value.count("@") != 1
               or not NAME.fullmatch(value.split("@", 1)[0])
               or not EVENT_ID.fullmatch(value.split("@", 1)[1])
               or ".." in value.split("@", 1)[1] for value in result["known"]):
            raise ValueError("the fold manifest has invalid projection ids")
        return result, version

    def snapshot(self) -> tuple[dict, dict, str, frozenset[str]]:
        """Read only snapshots reachable from the atomic manifest, or the legacy set before migration."""
        manifest, version = self._manifest()
        if manifest is None:
            records = {}
            for release, event in self._legacy_events():
                row, _ = self._legacy_read(release, event)
                if row is None:
                    raise ValueError("a listed sealed event disappeared")
                records[(release, event)] = row
            return records, {}, version, frozenset(row["id"] for item in records.values() for row in item["rows"])
        records, windows = {}, {}
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"release", "event", "key", "digest", "window"}:
                raise ValueError("the fold manifest entry has an invalid schema")
            release, event = release_segment(entry["release"]), event_segment(entry["event"])
            key, digest, window = entry["key"], entry["digest"], entry["window"]
            expected = f"{self.prefix}{release}/_sealed/_tx/"
            if (not isinstance(key, str) or not key.startswith(expected) or ".." in key.split("/")
                    or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                    or not isinstance(window, dict) or set(window) != {"start", "end", "min_time", "max_time"}
                    or any(value is not None and not isinstance(value, str) for value in window.values())):
                raise ValueError("the fold manifest entry is invalid")
            identity = (release, event)
            if identity in records:
                raise ValueError("the fold manifest repeats an event")
            data = self.store.get(key)
            if data is None or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("a committed fold snapshot is missing or corrupt")
            try:
                row = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                raise ValueError("a committed fold snapshot is invalid") from None
            self._check(release, event, row)
            records[identity], windows[identity] = row, window
        if any(row["id"] not in manifest["known"] for record in records.values() for row in record["rows"]):
            raise ValueError("the fold manifest omits a committed projection id")
        return records, windows, version, frozenset(manifest["known"])

    def commit(self, records: dict, windows: dict, version: str, known: frozenset[str]) -> None:
        """Stage immutable event snapshots, then atomically publish all of them with one CAS."""
        if set(records) != set(windows):
            raise ValueError("fold snapshots and windows must name the same events")
        entries = []
        for (release, event), row in sorted(records.items()):
            self._check(release, event, row)
            data = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            digest = hashlib.sha256(data).hexdigest()
            key = f"{self.prefix}{release_segment(release)}/_sealed/_tx/{digest}/{event_segment(event)}.json"
            if self.store.get(key) is None:
                try:
                    self.store.put_version(key, data, "0")
                except StoreConflict:
                    pass
            if self.store.get(key) != data:
                raise ValueError("a staged fold snapshot could not be verified")
            entries.append({"release": release, "event": event, "key": key,
                            "digest": digest, "window": windows[(release, event)]})
        data = json.dumps({"version": 2, "entries": entries, "known": sorted(known)}, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.store.put_version(f"{self.prefix}_fold-manifest.json", data, version)

    def read(self, release: str, event: str) -> tuple[dict | None, str]:
        manifest, version = self._manifest()
        if manifest is not None:
            records, _, _, _ = self.snapshot()
            return records.get((release_segment(release), event_segment(event))), version
        return self._legacy_read(release, event)

    def _legacy_read(self, release: str, event: str) -> tuple[dict | None, str]:
        data, version = self.store.get_version(self.key(release, event))
        if data is None:
            return None, version
        try:
            record = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("a sealed event record must be valid JSON") from None
        self._check(release, event, record)
        return record, version

    def write(self, release: str, event: str, record: dict, version: str) -> str:
        if self._manifest()[0] is not None:
            raise ValueError("a committed fold manifest requires an atomic snapshot update")
        self._check(release, event, record)
        try:
            data = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("a sealed event record must be plain JSON") from None
        return self.store.put_version(self.key(release, event), data, version)

    @staticmethod
    def _check(release: str, event: str, record: object) -> None:
        if not isinstance(record, dict) or set(record) != {"version", "release", "event", "observation_digests", "rows", "unattributed"}:
            raise ValueError("a sealed event record holds its version, identity, observation digests, rows and unattributed count")
        if type(record["version"]) is not int or record["version"] != 1 or record["release"] != release or record["event"] != event:
            raise ValueError("a sealed event record names its version, release and event")
        digests = record["observation_digests"]
        if not isinstance(digests, dict) or any(not isinstance(key, str) or not ID.fullmatch(key)
                                                or not isinstance(value, str) or not DIGEST.fullmatch(value)
                                                for key, value in digests.items()):
            raise ValueError("a sealed event record maps each opaque observation id to a SHA-256 digest")
        if type(record["unattributed"]) is not int or record["unattributed"] < 0:
            raise ValueError("a sealed event record's unattributed count is a non-negative integer")
        rows = record["rows"]
        if not isinstance(rows, list):
            raise ValueError("a sealed event record holds a list of counter rows")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", "shape", "release_event", "counters"}:
                raise ValueError("a sealed row holds its id, shape, release event and counters")
            if not all(isinstance(row[key], str) and row[key] for key in ("id", "shape", "release_event", "counters")):
                raise ValueError("a sealed row's fields are non-empty strings")
            if not NAME.fullmatch(row["shape"]) or row["id"] != f'{row["shape"]}@{event}' or row["release_event"] != event or row["id"] in seen:
                raise ValueError("a sealed row names its event and appears once")
            seen.add(row["id"])
            try:
                counters = json.loads(row["counters"])
            except ValueError:
                raise ValueError("a sealed row's counters are a JSON object") from None
            if not isinstance(counters, dict) or forbidden_key(counters) is not None or not finite(counters):
                raise ValueError("sealed counters never carry private observation evidence")

    def events(self) -> list[tuple[str, str]]:
        manifest, _ = self._manifest()
        if manifest is not None:
            records, _, _, _ = self.snapshot()
            return sorted(records)
        return self._legacy_events()

    def _legacy_events(self) -> list[tuple[str, str]]:
        found = []
        for key in self.store.list(self.prefix):
            parts = key[len(self.prefix):].split("/")
            if len(parts) >= 2 and parts[1] == "_sealed":
                if len(parts) >= 3 and parts[2] == "_tx":
                    continue
                if len(parts) != 3 or not parts[2].endswith(".json"):
                    raise ValueError("a sealed event key names one JSON record")
                release, _, filename = parts
                event = filename[:-5]
                release_segment(release)
                event_segment(event)
                found.append((release, event))
        return sorted(found)
