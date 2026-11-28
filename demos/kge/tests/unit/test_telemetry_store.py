"""Telemetry store keeps only safe spans and reads one client's retained records without broad listing."""
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock
from uuid import uuid4
from types import SimpleNamespace
import json

import pytest

from kge.adapters.deadline import request_remaining, request_scope
from kge.adapters.objstore.folder import LocalStore
from kge.adapters.objstore.gcs import GcsStore
from kge.adapters.telemetry.aggregate import Collection
from kge.adapters.telemetry.store import Exporter, Store
from opentelemetry.sdk.trace.export import SpanExportResult


def span(trace=None, sid=None, agent="cerebro", session=None, **attrs):
    return {"id": sid or uuid4().hex[:16], "trace_id": trace or uuid4().hex, "parent_id": None,
            "name": "kge.model.attempt", "started_at": "2026-11-20T12:00:00+00:00", "ended_at": "2026-11-20T12:00:01+00:00",
            "attributes": {"kge.agent": agent, "gen_ai.conversation.id": session or str(uuid4()),
                           "gen_ai.request.model": "example-model", "gen_ai.usage.input_tokens": 2,
                           "gen_ai.usage.output_tokens": 3, "kge.outcome": "ok", **attrs}}


def test_store_contract_sanitizes_and_deduplicates(tmp_path):
    now = datetime(2026, 11, 20, 13, tzinfo=timezone.utc)
    backend = LocalStore(str(tmp_path), tmp_path, private=True)
    store = Store(backend, "meridian", now=lambda: now)
    sample = span(question="private question", **{"kge.model.cost_usd": .02})
    store.append([sample, sample])
    assert store.agents("2026-11-20", "2026-11-21") == [{"id": "cerebro"}]
    session = store.sessions("cerebro", "2026-11-20", "2026-11-21")[0]
    assert session["id"] == sample["attributes"]["gen_ai.conversation.id"]
    trace = store.trace(sample["trace_id"])
    assert len(trace["spans"]) == 1
    assert "private question" not in str(trace)
    row = store.usage("2026-11-20", "2026-11-21", "cerebro")[0]
    assert (row["calls"], row["input_tokens"], row["output_tokens"], row["thinking_tokens"], row["cost_usd"]) == (1, 2, 3, None, .02)


def test_untrusted_tenant_replaced_and_invalid_session_refused(tmp_path):
    now = datetime(2026, 11, 20, 13, tzinfo=timezone.utc)
    store = Store(LocalStore(str(tmp_path), tmp_path, private=True), "meridian", now=lambda: now)
    sample = span(**{"kge.client": "another-client"})
    store.append([sample])
    assert store.trace(sample["trace_id"])["spans"][0]["attributes"]["kge.client"] == "meridian"
    sample["attributes"]["gen_ai.conversation.id"] = "alice@example.com"
    with pytest.raises(ValueError):
        store.append([sample])


def test_tmp_bucket_uses_client_scoped_telemetry_prefix(monkeypatch):
    from kge.adapters.telemetry import store as telemetry_store
    from types import SimpleNamespace
    captured = []
    monkeypatch.setattr(telemetry_store, "settings", lambda: SimpleNamespace(TMP_URL="gs://scratch/", STORE_URL="gs://other"))
    monkeypatch.setattr(telemetry_store, "open_store", lambda url, private: captured.append((url, private)) or SimpleNamespace(url=url))
    telemetry_store.for_profile(SimpleNamespace(client="meridian"))
    assert captured == [("gs://scratch/tmp/meridian/traces", True)]


def test_conflicting_duplicate_drops_trace_not_double_counted():
    sample = span()
    other = {**sample, "attributes": {**sample["attributes"], "gen_ai.usage.input_tokens": 500}}
    assert Collection([sample, other]).traces(None, None, "2026-11-20", "2026-11-21") == []


def test_export_retries_transient_batch_failure_and_ignores_dangling_index(tmp_path, monkeypatch):
    now = datetime(2026, 11, 20, 13, tzinfo=timezone.utc)
    backend = LocalStore(str(tmp_path), tmp_path, private=True)
    store = Store(backend, "meridian", now=lambda: now)
    put = backend.put
    attempts = []

    def flaky(key, data):
        if "/batches/" in key:
            attempts.append(key)
            if len(attempts) == 1:
                raise OSError("temporary outage")
        return put(key, data)

    monkeypatch.setattr(backend, "put", flaky)
    sample = span()
    record = SimpleNamespace(context=SimpleNamespace(is_valid=True, span_id=int(sample["id"], 16),
                                                      trace_id=int(sample["trace_id"], 16)),
                             parent=None, name=sample["name"], attributes=sample["attributes"],
                             start_time=int(datetime.fromisoformat(sample["started_at"]).timestamp() * 1e9),
                             end_time=int(datetime.fromisoformat(sample["ended_at"]).timestamp() * 1e9))
    exporter = Exporter(store)
    try:
        assert exporter.export([record]) == SpanExportResult.SUCCESS
        assert len(attempts) == 2
        assert len(store.trace(sample["trace_id"])["spans"]) == 1
    finally:
        exporter.shutdown()


def test_old_cloud_spans_are_read_without_listing_or_deleting(tmp_path, monkeypatch):
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    backend = LocalStore("gs://shared/traces/meridian", tmp_path, private=True)
    sample = span()
    store = Store(backend, "meridian", now=lambda: now)
    monkeypatch.setattr(backend, "list", lambda *_: pytest.fail("cloud listing is forbidden"))
    monkeypatch.setattr(backend, "delete", lambda *_: pytest.fail("application deletion is forbidden"))
    assert store.append([sample]) == 1
    monkeypatch.setattr(backend, "put", lambda *_: pytest.fail("reads must not write"))
    monkeypatch.setattr(backend, "put_version", lambda *_: pytest.fail("reads must not write"))
    start, end = "2026-11-20", "2026-11-21"
    assert store.agents(start, end) == [{"id": "cerebro"}]
    assert store.sessions(None, start, end)
    assert store.traces(None, None, start, end)[0]["id"] == sample["trace_id"]
    assert store.trace(sample["trace_id"], start, end)
    assert store.usage(start, end, None)[0]["calls"] == 1
    index, _ = backend.get_version("days/2026-11-20/index.json")
    batch = next(iter(json.loads(index)))
    (tmp_path / "days" / "2026-11-20" / "batches" / f"{batch}.json").unlink()
    assert store.trace(sample["trace_id"], start, end) is None
    assert store.usage(start, end, None) == []


def test_seven_day_cloud_read_overlaps_batch_fetches(monkeypatch):
    first = datetime(2026, 11, 14, tzinfo=timezone.utc)
    objects = {}
    for offset in range(7):
        day = first + timedelta(days=offset)
        prefix = f"days/{day.date().isoformat()}"
        index = {}
        for number in range(offset + 1, 4001, 7):
            trace_id = f"{number:032x}"
            sample = span(trace_id, f"{number:016x}", session="conv-1", **{"kge.client": "meridian"})
            sample["started_at"] = day.isoformat()
            sample["ended_at"] = (day + timedelta(seconds=1)).isoformat()
            index[trace_id] = sample["ended_at"]
            objects[f"{prefix}/batches/{trace_id}.json"] = json.dumps([sample]).encode()
        objects[f"{prefix}/index.json"] = json.dumps(index).encode()

    rendezvous = Barrier(2, timeout=3)
    lock = Lock()
    reads = []

    def get(backend, method, url, key):
        assert method == "GET"
        assert request_remaining() is not None
        if "/batches/" in key:
            with lock:
                reads.append(key)
                first_pair = len(reads) <= 2
            if first_pair:
                rendezvous.wait()
        return (200, objects[key]) if key in objects else (404, b"")

    monkeypatch.setattr(GcsStore, "call", get)
    store = Store(GcsStore("gs://telemetry", "telemetry"), "meridian")
    with request_scope():
        result = store.overview(first.isoformat(), (first + timedelta(days=7)).isoformat())
    assert len(reads) == len(set(reads)) == 4000
    assert result["summary"]["turns"] == 4000
    assert result["summary"]["model_calls"] == 4000
    assert len(result["daily"]) == 7


def test_store_refreshes_index_and_still_rejects_malformed_batches(tmp_path):
    backend = LocalStore(str(tmp_path), tmp_path, private=True)
    store = Store(backend, "meridian")
    start, end = "2026-11-20", "2026-11-21"
    store.append([span(session="conv-1")])
    assert store.overview(start, end)["summary"]["turns"] == 1
    store.append([span(session="conv-1")])
    assert store.overview(start, end)["summary"]["turns"] == 2
    batch = next(iter(json.loads(backend.get("days/2026-11-20/index.json"))))
    backend.put(f"days/2026-11-20/batches/{batch}.json", b"[{}]")
    with pytest.raises(ValueError, match="invalid telemetry batch"):
        store.overview(start, end)


def test_local_store_does_not_expire_after_idle_restart(tmp_path):
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    backend = LocalStore(str(tmp_path), tmp_path, private=True)
    store = Store(backend, "meridian", now=lambda: now)
    sample = span()
    assert store.append([sample]) == 1
    reopened = Store(backend, "meridian", now=lambda: now + timedelta(days=30))
    assert reopened.trace(sample["trace_id"], "2026-11-20", "2026-11-21")
    assert any("/batches/" in key for key in backend.list("days/"))


def test_old_trace_crossing_midnight_and_last_possible_date(tmp_path):
    backend = LocalStore(str(tmp_path), tmp_path, private=True)
    store = Store(backend, "meridian", now=lambda: datetime(2026, 12, 20, tzinfo=timezone.utc))
    sample = span()
    sample.update(started_at="2026-11-20T23:59:59+00:00", ended_at="2026-11-21T00:00:01+00:00")
    assert store.append([sample]) == 1
    assert store.trace(sample["trace_id"], "2026-11-20", "2026-11-21")
    assert store.agents("9999-12-30", "9999-12-31") == []


def test_http_detail_passes_historical_period_and_off_reader_returns_404(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kge.services.telemetry import routes, service
    from kge.pipeline.telemetry import FASTAPI_TELEMETRY_OFF

    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)
    app.include_router(routes.router)
    app.dependency_overrides[routes.engineer] = lambda: "engineer"
    store = Store(LocalStore(str(tmp_path), tmp_path, private=True), "meridian", now=lambda: datetime(2026, 12, 20, tzinfo=timezone.utc))
    sample = span()
    store.append([sample])
    monkeypatch.setattr(service, "reader", lambda profile: store)
    with TestClient(app) as client:
        path = f"/telemetry/traces/{sample['trace_id']}"
        assert client.get(path, params={"start": "2026-11-20", "end": "2026-11-21"}).json()["trace"]["id"] == sample["trace_id"]
        monkeypatch.setattr(service, "reader", lambda profile: Collection([]))
        assert client.get(path).status_code == 404
