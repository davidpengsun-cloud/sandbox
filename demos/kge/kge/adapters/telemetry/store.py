"""Batched, client-scoped question spans on the object-store port, indexed without bucket-wide listing."""
from __future__ import annotations

import json
import re
import secrets
import time
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from kge.adapters.objstore import open_store
from kge.adapters.telemetry.aggregate import Collection
from kge.landing.zone import CLIENT_ID
from kge.pipeline.settings import settings
from kge.ports import StoreConflict

from kge.pipeline.trace_context import AGENTS, HEX_SPAN, HEX_TRACE, SESSION, SPAN_NAME, instant

MAX_BATCH = 512
MAX_RETRIES = 64


def identity(agent: str, session: str) -> bool:
    """Only recognized agents and opaque session IDs may enter persisted telemetry."""
    return agent in AGENTS and isinstance(session, str) and bool(SESSION.fullmatch(session))


def normalized(span, client: str) -> dict:
    """Validate and sanitize an already finished span; trust the deployment, never the submitted tenant."""
    from kge.adapters.tracing import safe
    if not CLIENT_ID.fullmatch(client):
        raise ValueError("invalid telemetry client")
    if not isinstance(span, dict) or set(span) - {"id", "trace_id", "parent_id", "name", "started_at", "ended_at", "duration_ms", "attributes"}:
        raise ValueError("invalid telemetry span")
    sid, trace_id, parent = span.get("id"), span.get("trace_id"), span.get("parent_id")
    if not isinstance(sid, str) or not HEX_SPAN.fullmatch(sid) or not isinstance(trace_id, str) or not HEX_TRACE.fullmatch(trace_id):
        raise ValueError("invalid telemetry span identity")
    if parent is not None and (not isinstance(parent, str) or not HEX_SPAN.fullmatch(parent)):
        raise ValueError("invalid telemetry parent")
    name = span.get("name")
    if not isinstance(name, str) or not SPAN_NAME.fullmatch(name):
        raise ValueError("invalid telemetry span name")
    started, ended = instant(span["started_at"]), instant(span["ended_at"])
    if ended < started:
        raise ValueError("invalid telemetry span time")
    attrs = safe(span.get("attributes") if isinstance(span.get("attributes"), dict) else {})
    agent, session = attrs.get("kge.agent"), attrs.get("gen_ai.conversation.id")
    if not identity(agent, session):
        raise ValueError("invalid telemetry context")
    attrs["kge.client"] = client
    return {"id": sid, "trace_id": trace_id, "parent_id": parent, "name": name,
            "started_at": started.isoformat(), "ended_at": ended.isoformat(),
            "duration_ms": (ended - started).total_seconds() * 1000, "attributes": attrs}


class Store:
    """An index and immutable span batches; all paths live under a single client's tracing prefix."""

    def __init__(self, store, client, *, now=None):
        if not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
            raise ValueError("invalid telemetry client")
        self.store, self.client = store, client
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _read_index(self, day, *, versioned=True):
        key = f"days/{day}/index.json"
        data, version = self.store.get_version(key) if versioned else (self.store.get(key), None)
        if data is None:
            return {}, version
        try:
            parsed = json.loads(data)
            if not isinstance(parsed, dict) or not all(re.fullmatch(r"[0-9a-f]{32}", key) and isinstance(at, str) for key, at in parsed.items()):
                raise ValueError()
            for at in parsed.values():
                instant(at)
            return parsed, version
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("invalid telemetry index") from None

    def _change(self, day, change):
        for attempt in range(MAX_RETRIES):
            old, version = self._read_index(day)
            updated = change(dict(old))
            try:
                self.store.put_version(f"days/{day}/index.json", json.dumps(updated, separators=(",", ":"), sort_keys=True).encode(), version)
                return
            except StoreConflict:
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(min(.02, .001 * 2 ** min(attempt, 5)) * (.5 + secrets.randbelow(1000) / 2000))
        raise StoreConflict("telemetry index changed repeatedly")

    def append(self, spans):
        if not spans:
            return 0
        if len(spans) > MAX_BATCH:
            raise ValueError("too many spans")
        rows = [normalized(s, self.client) for s in spans]
        groups = defaultdict(list)
        for row in rows:
            ended = instant(row["ended_at"])
            groups[ended.date().isoformat()].append(row)
        for day, batch_rows in groups.items():
            batch = uuid4().hex
            latest = max(instant(row["ended_at"]) for row in batch_rows)
            self._change(day, lambda index: {**index, batch: latest.isoformat()})
            self.store.put(f"days/{day}/batches/{batch}.json", json.dumps(batch_rows, separators=(",", ":"), allow_nan=False).encode())
        return sum(len(group) for group in groups.values())

    def _collection(self, start, end):
        rows = []
        first = instant(start).date().toordinal()
        last = (instant(end) - timedelta(microseconds=1)).date().toordinal()
        days = (date.fromordinal(n).isoformat() for n in range(first, min(last + 1, date.max.toordinal()) + 1))
        for day in days:
            index, _ = self._read_index(day, versioned=False)
            keys = (f"days/{day}/batches/{key}.json" for key in sorted(index))
            with closing(self.store.get_many(keys)) as batches:
                for data in batches:
                    if data is None:
                        continue
                    try:
                        items = json.loads(data)
                        if not isinstance(items, list) or len(items) > MAX_BATCH:
                            raise ValueError()
                        checked = [normalized(row, self.client) for row in items]
                        rows.extend(checked)
                    except (ValueError, KeyError, TypeError, UnicodeError):
                        raise ValueError("invalid telemetry batch") from None
        return Collection(rows)

    def agents(self, start, end):
        return self._collection(start, end).agents(start, end)

    def sessions(self, agent, start, end):
        return self._collection(start, end).sessions(agent, start, end)

    def traces(self, agent, session, start, end):
        return self._collection(start, end).traces(agent, session, start, end)

    def page(self, agent, session, start, end, outcome=None, day=None, day_agent=None, limit=50, after=None):
        return self._collection(start, end).page(agent, session, start, end, outcome, day, day_agent, limit, after)

    def overview(self, start, end, agent=None, session=None, outcome=None):
        return self._collection(start, end).overview(start, end, agent, session, outcome)

    def trace(self, trace_id, start=None, end=None):
        if not isinstance(trace_id, str) or not HEX_TRACE.fullmatch(trace_id):
            raise ValueError("invalid trace ID")
        now = self.now()
        first, last = start or (now - timedelta(days=7)).isoformat(), end or now.isoformat()
        return self._collection(first, last).detail(trace_id, first, last)

    def usage(self, start, end, agent):
        return self._collection(start, end).usage(start, end, agent)


def for_profile(profile):
    """The client-scoped tmp store, else its persistent store or local profile state."""
    current = settings()
    tmp = (current.TMP_URL or "").strip()
    shared = (current.STORE_URL or "").strip()
    if tmp:
        url = "/".join((tmp.rstrip("/"), "tmp", profile.client, "traces"))
    elif shared:
        url = f"{shared.rstrip('/')}/traces/{profile.client}"
    else:
        url = str(profile.run_dir() / "traces")
    return Store(open_store(url, private=True), profile.client)


class Exporter(SpanExporter):
    """Persist the safe processor's spans in object-store batches."""

    def __init__(self, store):
        self.store = store
    def export(self, spans):
        try:
            rows = []
            for span in spans:
                context = span.context
                if not context or not context.is_valid:
                    continue
                attrs = dict(span.attributes or {})
                if not identity(attrs.get("kge.agent"), attrs.get("gen_ai.conversation.id")):
                    continue
                rows.append({"id": f"{context.span_id:016x}", "trace_id": f"{context.trace_id:032x}",
                             "parent_id": f"{span.parent.span_id:016x}" if span.parent else None, "name": span.name,
                             "started_at": datetime.fromtimestamp(span.start_time / 1e9, timezone.utc).isoformat(),
                             "ended_at": datetime.fromtimestamp(span.end_time / 1e9, timezone.utc).isoformat(), "attributes": attrs})
            for i in range(0, len(rows), MAX_BATCH):
                for attempt in range(3):
                    try:
                        self.store.append(rows[i:i + MAX_BATCH])
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(.05 * (attempt + 1))
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass
