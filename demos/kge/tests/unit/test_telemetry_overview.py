"""Historical overview, completeness and stable cursor behavior on safe sample spans."""
from uuid import uuid4

import pytest

from kge.adapters.telemetry.aggregate import Collection
from kge.services.telemetry.engine import service


START, END = "2026-11-01T00:00:00+00:00", "2026-11-03T00:00:00+00:00"
SESSION = str(uuid4())


def span(trace, sid, name, at, duration, attrs=None, parent=None):
    return {"trace_id": f"{trace:032x}", "id": f"{sid:016x}", "parent_id": parent, "name": name,
            "started_at": at, "ended_at": at, "duration_ms": duration,
            "attributes": {"kge.agent": "cerebro", "gen_ai.conversation.id": SESSION, **(attrs or {})}}


def dataset():
    when = "2026-11-01T12:00:00+00:00"
    rows = [span(1, 1, "kge.ask", when, 10, {"kge.outcome": "ok"}),
            span(2, 2, "kge.ask", when, 10, {"kge.outcome": "failed"}),
            span(3, 3, "kge.ask", when, 10, {"kge.outcome": "ok"})]
    for i, amounts in enumerate(((10, 4, 2, .1), (20, 6, 3, .2), (None, None, None, None)), 4):
        attrs = {"gen_ai.request.model": "model-a"}
        for key, value in zip(("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.reasoning_tokens", "kge.model.cost_usd"), amounts):
            if value is not None:
                attrs[key] = value
        rows.append(span(1, i, "kge.model.attempt", when, i, attrs, f"{1:016x}"))
    rows.append(span(1, 8, "kge.warehouse", when, 9, parent=f"{1:016x}"))
    rows.append(span(3, 9, "kge.model", when, 4, parent=f"{3:016x}"))
    return Collection(rows)


def test_overview_partial_usage_and_session_filter():
    collection = dataset()
    result = collection.overview(START, END)
    summary, row = result["summary"], result["daily"][0]
    assert (summary["turns"], summary["sessions"], summary["ok"], summary["failed"]) == (3, 1, 2, 1)
    assert (summary["model_calls"], summary["warehouse_reads"], summary["no_model_turns"]) == (3, 1, 0)
    assert (summary["input_tokens"], summary["output_tokens"], summary["thinking_tokens"]) == (30, 10, 5)
    assert summary["cost_usd"] == pytest.approx(.3)
    assert summary["thinking_share"] == .5
    assert (row["turns"], len(row["models"]), row["models"][0]["input_tokens_reported"]) == (3, 1, 2)
    assert collection.overview(START, END, session=SESSION, outcome="failed")["summary"]["turns"] == 1
    assert collection.overview(START, END, session=str(uuid4()))["summary"]["turns"] == 0


def test_keyset_ties_ordinals_filter_scope_and_default_cursor(monkeypatch):
    rows = [span(i + 1, i + 1, "kge.ask", "2026-11-01T12:00:00+00:00", 10, {"kge.outcome": "failed" if i % 2 else "ok"}) for i in range(103)]
    collection = Collection(rows)
    monkeypatch.setattr(service, "reader", lambda profile: collection)
    page = service.traces(None, start=START, end=END, limit=50)
    assert len(page["traces"]) == 50
    all_rows = list(page["traces"])
    while page["next_cursor"]:
        page = service.traces(None, start=START, end=END, limit=50, cursor=page["next_cursor"])
        all_rows.extend(page["traces"])
    assert [r["id"] for r in all_rows] == [f"{i:032x}" for i in range(103, 0, -1)]
    assert all_rows[0]["turn_index"] == 103 and all_rows[0]["session_trace_count"] == 103
    filtered = service.traces(None, start=START, end=END, outcome="failed", day="2026-11-01", limit=50)
    assert filtered["traces"][0]["turn_index"] == 102
    assert filtered["traces"][0]["next_trace_id"] == f"{103:032x}"
    with pytest.raises(ValueError):
        service.traces(None, start=START, end=END, limit=101)
    first = service.traces(None, start=START, end=END, limit=2)
    with pytest.raises(ValueError):
        service.traces(None, start=START, end=END, limit=2, outcome="ok", cursor=first["next_cursor"])
    monkeypatch.setattr(service, "period", lambda start, end: (start or START, end or END))
    first = service.traces(None, limit=2)
    assert service.traces(None, limit=2, cursor=first["next_cursor"])["traces"][0]["id"] == f"{101:032x}"


def test_all_unknown_model_fields_are_not_reported():
    row = dataset().usage(START, END, "cerebro")[0]
    assert row["calls"] == 3 and row["thinking_tokens"] == 5 and row["thinking_tokens_reported"] == 2
    unknown = Collection([span(9, 9, "kge.ask", "2026-11-01T12:00:00+00:00", 1),
                          span(9, 10, "kge.model.attempt", "2026-11-01T12:00:00+00:00", 1, parent=f"{9:016x}")])
    row = unknown.usage(START, END, "cerebro")[0]
    assert row["thinking_tokens"] is None and row["thinking_tokens_reported"] == 0
    assert unknown.overview(START, END)["summary"]["ok"] is None


def test_unqualified_detail_uses_existing_id_only_lookup(monkeypatch):
    calls = []
    class Reader:
        def trace(self, *args):
            calls.append(args)
            return {"id": args[0]}
    monkeypatch.setattr(service, "reader", lambda profile: Reader())
    identity = f"{1:032x}"
    service.trace(None, identity)
    assert calls == [(identity,)]
    service.trace(None, identity, START, END)
    assert calls[-1] == (identity, START, END)


def test_overview_and_paged_traces_match_over_http_and_mcp_and_are_engineer_only(monkeypatch):
    from contextlib import asynccontextmanager, nullcontext
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kge.pipeline.telemetry import FASTAPI_TELEMETRY_OFF
    from kge.services import http
    from kge.services.ask import mcp
    from kge.services.telemetry import routes

    role, audit = {"value": "engineer"}, []
    profile = SimpleNamespace(client="meridian")
    for obj, name, value in [(http, "mcp_principal", lambda request: role["value"]),
                             (mcp, "mcp_principal", lambda request: role["value"]),
                             (mcp, "may", lambda who, process: who), (http, "may", lambda who, process: who),
                             (http, "cerebro_held", lambda who, state=None: [who]),
                             (http, "person_slot", lambda who: nullcontext()), (http, "quota_context", lambda who: nullcontext()),
                             (http, "_role_audit", lambda state, who, held, row, endpoint, decision: audit.append((who, row, decision))),
                             (service, "reader", lambda _profile: dataset()),
                             (routes, "served", SimpleNamespace(PROFILE=profile)), (mcp, "served", SimpleNamespace(PROFILE=profile))]:
        monkeypatch.setattr(obj, name, value)
    server = mcp.mcp_server()
    @asynccontextmanager
    async def lifespan(_app):
        async with server.session_manager.run():
            yield
    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF, lifespan=lifespan)
    app.include_router(routes.router)
    app.dependency_overrides[http.principal] = lambda: role["value"]
    app.add_route("/mcp", mcp.MCPEndpoint(server), methods=["GET", "POST", "DELETE"])
    with TestClient(app) as client:
        for name in ("overview", "traces"):
            params = {"start": START, "end": END}
            response = client.get(f"/telemetry/{name}", params=params)
            message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": f"telemetry_{name}", "arguments": params}}
            result = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json=message).json()["result"]
            assert result["structuredContent"] == response.json()
            for identity in ("business", "steward", "admin"):
                role["value"] = identity
                assert client.get(f"/telemetry/{name}", params=params).status_code == 403
                assert client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json=message).json()["result"]["isError"]
                assert (identity, "telemetry", "refused") in audit
            role["value"] = "engineer"


def test_two_models_on_one_turn_keep_daily_turn_and_session_counts_single():
    source = dataset()
    more = span(1, 20, "kge.model.attempt", "2026-11-01T12:00:00+00:00", 2,
                {"gen_ai.request.model": "model-b", "gen_ai.usage.input_tokens": 7}, f"{1:016x}")
    row = Collection([*source._spans, more]).overview(START, END)["daily"][0]
    assert (row["turns"], row["sessions"]) == (3, 1)
    by_model = {model["model"]: model for model in row["models"]}
    assert (by_model["model-a"]["calls"], by_model["model-a"]["input_tokens"]) == (3, 30)
    assert (by_model["model-b"]["calls"], by_model["model-b"]["input_tokens"]) == (1, 7)
