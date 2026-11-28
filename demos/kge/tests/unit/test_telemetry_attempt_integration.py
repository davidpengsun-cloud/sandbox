"""A real model attempt reaches retained telemetry through the production safety processor."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from kge.adapters.model.accounting import Attempt
from kge.adapters.objstore.folder import LocalStore
from kge.adapters.telemetry.store import Exporter, Store
from kge.pipeline import trace
from tests.tracing_setup import tracing_to
from tests.unit.test_model_usage_telemetry import Ledger, model, payload


def test_attempts_flow_through_safe_export_and_store_without_double_counting(tmp_path):
    store = Store(LocalStore(str(tmp_path), tmp_path, private=True), "meridian")
    exporter = Exporter(store)
    session = str(uuid4())
    with tracing_to(exporter), trace.telemetry_scope("meridian", "cerebro", session):
        root = trace.new_span("kge.ask")
        with trace.as_current(root):
            one = Attempt(model(), Ledger(), "question", "invocation")
            one.finish(payload=payload(), outcome="ok")
            one.finish(payload=payload(), outcome="ok")
        root.end()
    now = datetime.now(timezone.utc)
    detail = store.trace(f"{root.get_span_context().trace_id:032x}")
    assert {item["name"] for item in detail["spans"]} == {"kge.ask", "kge.model.attempt"}
    rows = store.usage((now - timedelta(days=1)).isoformat(), (now + timedelta(days=1)).isoformat(), "cerebro")
    assert len(rows) == 1
    assert rows[0]["calls"] == 1
    assert rows[0]["thinking_tokens"] == 5
    assert rows[0]["cost_usd"] == pytest.approx(0.00015)


def test_retry_keeps_reported_usage_through_safe_export(tmp_path):
    store = Store(LocalStore(str(tmp_path), tmp_path, private=True), "meridian")
    exporter = Exporter(store)
    with tracing_to(exporter), trace.telemetry_scope("meridian", "cerebro", str(uuid4())):
        root = trace.new_span("kge.ask")
        with trace.as_current(root):
            Attempt(model(), Ledger(), "question", "invocation").finish(outcome="failed")
            Attempt(model(), Ledger(), "question", "invocation").finish(payload=payload(), outcome="ok")
        root.end()
    now = datetime.now(timezone.utc)
    rows = store.usage((now - timedelta(days=1)).isoformat(), (now + timedelta(days=1)).isoformat(), "cerebro")
    assert len(rows) == 1
    assert rows[0]["calls"] == 2
    assert rows[0]["errors"] == 1
    assert rows[0]["thinking_tokens"] == 5
    assert rows[0]["thinking_tokens_reported"] == 1
    assert rows[0]["cost_usd"] == pytest.approx(0.00015)
    assert rows[0]["cost_usd_reported"] == 1
