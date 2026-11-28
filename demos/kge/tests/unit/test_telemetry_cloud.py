"""Cloud telemetry filters tenant data and preserves unknown metrics."""
from __future__ import annotations

import pytest

from kge.adapters.deadline import ExternalCallError
from kge.adapters.telemetry.cloud import Cloud, METRIC_TYPE, _spans

CLIENT = "meridian"
TRACE = "a" * 32
SESSION = "abcdefab-cdef-4abc-8def-abcdefabcdef"
START = "2026-11-01T00:00:00Z"
END = "2026-11-03T00:00:00Z"


def span(span_id="1", client=CLIENT, **labels):
    return {"spanId": span_id, "name": "kge.model.attempt", "startTime": "2026-11-01T10:00:00Z",
            "endTime": "2026-11-01T10:00:01Z", "labels": {"kge.client": client, "kge.agent": "cerebro",
            "gen_ai.conversation.id": SESSION, **labels}}


class Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class Session:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        return Response(self.pages.pop(0))


class Captured:
    def __init__(self, spans):
        self.spans = list(spans)

    def trace(self, trace_id):
        return self.spans if any(s["trace_id"] == trace_id for s in self.spans) else None

    def agents(self, start, end):
        return self.spans

    def usage(self, start, end, agent):
        return self.spans


def test_cloud_trace_filters_every_span_and_sensitive_labels():
    raw = {"traceId": TRACE, "spans": [span(**{"gen_ai.usage.input_tokens": "321", "question": "secret",
                                                 "gen_ai.request.model": "gemini-3.1-pro-preview",
                                                 "kge.stage": "name@example.com"}),
                                           span("2", client="other"), span("3", **{"gen_ai.conversation.id": "Alice Smith"})]}
    spans = _spans(raw, CLIENT)
    assert len(spans) == 1 and spans[0]["id"] == f"{1:016x}"
    assert spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 321
    assert "question" not in spans[0]["attributes"] and "kge.stage" not in spans[0]["attributes"]
    assert spans[0]["duration_ms"] == 1000


def test_cloud_trace_paginates_and_verifies_client_even_if_remote_ignores_filter():
    http = Session({"traces": [{"traceId": TRACE, "spans": [span()]}], "nextPageToken": "next"},
                   {"traces": [{"traceId": "b" * 32, "spans": [span(client="other")]}]})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured)
    rows = cloud.agents(START, END)
    assert len(rows) == 1 and rows[0]["trace_id"] == TRACE
    assert http.calls[0][1]["filter"] == "+kge.client:meridian"
    assert http.calls[1][1]["pageToken"] == "next"


def test_trace_detail_refuses_cross_tenant_and_bad_ids():
    http = Session({"traceId": TRACE, "spans": [span(client="other")]})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured)
    assert cloud.trace("not-a-trace") is None and not http.calls
    assert cloud.trace(TRACE) is None


def test_repeated_page_token_fails_closed():
    http = Session({"traces": [], "nextPageToken": "same"}, {"traces": [], "nextPageToken": "same"})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured)
    with pytest.raises(ExternalCallError):
        cloud.agents(START, END)


def series(kind, value, *, client=CLIENT, model="gemini-3.1-pro-preview", agent="cerebro"):
    return {"metric": {"type": METRIC_TYPE, "labels": {"client": client, "agent": agent, "model": model, "kind": kind}},
            "points": [{"interval": {"startTime": "2026-11-01T00:00:00Z", "endTime": "2026-11-02T00:00:00Z"}, "value": {"doubleValue": value}}]}


def test_monitoring_scopes_tenant_and_keeps_partial_counts():
    http = Session({"timeSeries": [series("calls", 2), series("errors", 0), series("input_tokens", 900),
                                    series("input_tokens_reported", 2), series("output_tokens", 30),
                                    series("output_tokens_reported", 1), series("cost_usd", 999, client="other"),
                                    series("cost_usd_reported", 2, client="other")]})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured, monitoring_verified=True)
    assert cloud.usage(START, END, "cerebro") == [{"date": "2026-11-01", "agent": "cerebro", "model": "gemini-3.1-pro-preview",
        "calls": 2, "errors": 0, "input_tokens": 900, "output_tokens": 30, "thinking_tokens": None, "cost_usd": None,
        "input_tokens_reported": 2, "output_tokens_reported": 1, "thinking_tokens_reported": 0, "cost_usd_reported": 0}]
    assert 'metric.labels.client = "meridian"' in http.calls[0][1]["filter"]
    assert http.calls[0][1]["aggregation.perSeriesAligner"] == "ALIGN_SUM"


def test_client_marker_cannot_inject_monitoring_filter():
    with pytest.raises(ValueError):
        Cloud('other" OR true', session=Session(), project="project-1", collection=Captured)


def test_duplicate_trace_span_is_not_counted_twice():
    record = {"traceId": TRACE, "spans": [span()]}
    http = Session({"traces": [record], "nextPageToken": "next"}, {"traces": [record]})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured)
    assert len(cloud.agents(START, END)) == 1


def test_conflicting_duplicate_trace_span_fails_closed():
    http = Session({"traces": [{"traceId": TRACE, "spans": [span(), span(**{"gen_ai.usage.input_tokens": "123"})]}]})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured)
    with pytest.raises(ExternalCallError):
        cloud.agents(START, END)


def test_context_ask_opaque_conversation_id_is_allowed():
    spans = _spans({"traceId": TRACE, "spans": [span(**{"gen_ai.conversation.id": "conv-123"})]}, CLIENT)
    assert spans[0]["attributes"]["gen_ai.conversation.id"] == "conv-123"


def test_unverified_monitoring_uses_trace_attempts_without_querying_metrics():
    http = Session({"traces": [{"traceId": TRACE, "spans": [span()]}]})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured)
    assert len(cloud.usage(START, END, "cerebro")) == 1
    assert "cloudtrace.googleapis.com" in http.calls[0][0]


def test_cross_tenant_monitoring_series_never_appear_even_if_remote_filter_is_ignored():
    http = Session({"timeSeries": [series("calls", 9, client="other")]}, {"traces": []})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured, monitoring_verified=True)
    assert cloud.usage(START, END, "cerebro") == []


def test_out_of_period_monitoring_points_are_ignored():
    item = series("calls", 5)
    item["points"][0]["interval"] = {"startTime": "2026-10-28T00:00:00Z", "endTime": "2026-10-29T00:00:00Z"}
    http = Session({"timeSeries": [item]}, {"traces": []})
    cloud = Cloud(CLIENT, session=http, project="project-1", collection=Captured, monitoring_verified=True)
    assert cloud.usage(START, END, None) == []


def test_mixed_portal_and_kge_cloud_spans_keep_portal_attribution():
    from kge.adapters.telemetry.aggregate import Collection

    portal = span(**{"kge.telemetry.origin": "portal", "kge.agent": "discover_ask", "gen_ai.conversation.id": "conv-123"})
    child = span("2", **{"kge.telemetry.origin": "kge", "kge.agent": "kge_mcp"})
    child["parentSpanId"] = "1"
    collection = Collection(_spans({"traceId": TRACE, "spans": [portal, child]}, CLIENT))
    result = collection.trace(TRACE)
    assert result is not None
    assert result["agent"] == "discover_ask" and result["session_id"] == "conv-123"
    assert [piece["attributes"]["kge.telemetry.origin"] for piece in result["spans"]] == ["portal", "kge"]


@pytest.mark.parametrize("origin", ["untrusted", "Alice Smith", ["portal"], None])
def test_cloud_never_keeps_an_unapproved_origin(origin):
    rows = _spans({"traceId": TRACE, "spans": [span(**{"kge.telemetry.origin": origin})]}, CLIENT)
    assert len(rows) == 1 and "kge.telemetry.origin" not in rows[0]["attributes"]


def test_cloud_aborted_portal_root_is_failed_without_leaking_error_text():
    from kge.adapters.telemetry.aggregate import Collection

    portal = span(**{"kge.telemetry.origin": "portal", "kge.agent": "cerebro", "error.type": "aborted"})
    portal["name"] = "POST /api/cerebro/stream"
    result = Collection(_spans({"traceId": TRACE, "spans": [portal]}, CLIENT)).trace(TRACE)
    assert result["outcome"] == "failed"
    assert result["spans"][0]["attributes"]["error.type"] == "aborted"


def test_cloud_successful_root_stays_successful_when_a_model_attempt_fails():
    from kge.adapters.telemetry.aggregate import Collection

    portal = span(**{"kge.telemetry.origin": "portal", "kge.agent": "cerebro", "http.response.status_code": "200"})
    portal["name"] = "POST /api/cerebro/stream"
    child = span("2", **{"kge.telemetry.origin": "kge", "kge.outcome": "failed",
                          "gen_ai.request.model": "gemini-3.1-pro-preview"})
    child["parentSpanId"] = "1"
    collection = Collection(_spans({"traceId": TRACE, "spans": [portal, child]}, CLIENT))
    assert collection.trace(TRACE)["outcome"] == "ok"
    assert collection.usage(START, END, "cerebro")[0]["errors"] == 1


def test_child_failure_does_not_invent_a_missing_root_outcome():
    from kge.adapters.telemetry.aggregate import Collection

    child = span(**{"kge.telemetry.origin": "kge", "kge.outcome": "failed"})
    child["parentSpanId"] = "2"
    collection = Collection(_spans({"traceId": TRACE, "spans": [child]}, CLIENT))
    assert collection.trace(TRACE)["outcome"] is None


@pytest.mark.parametrize("status,outcome", [("200", "ok"), ("499", "ok"), ("500", "failed"), ("599", "failed"), ("600", None)])
def test_cloud_portal_http_status_maps_only_valid_statuses(status, outcome):
    attrs = _spans({"traceId": TRACE, "spans": [span(**{"kge.telemetry.origin": "portal", "http.response.status_code": status})]}, CLIENT)[0]["attributes"]
    assert attrs.get("kge.outcome") == outcome


def test_monitoring_is_enabled_only_by_valid_operator_setting(monkeypatch):
    from types import SimpleNamespace
    import kge.adapters.telemetry as telemetry
    import kge.adapters.telemetry.cloud as cloud
    from kge.adapters.tracing import check_settings

    monkeypatch.setattr(telemetry, "mode", lambda: "cloudtrace")
    monkeypatch.setattr(cloud, "Cloud", lambda client, *, monitoring_verified: (client, monitoring_verified))
    monkeypatch.delenv("TELEMETRY_MONITORING_USAGE", raising=False)
    assert telemetry.reader(SimpleNamespace(client=CLIENT)) == (CLIENT, False)
    monkeypatch.setenv("TELEMETRY_MONITORING_USAGE", "verified")
    assert telemetry.reader(SimpleNamespace(client=CLIENT)) == (CLIENT, True)
    monkeypatch.setenv("TELEMETRY_MONITORING_USAGE", "typo")
    with pytest.raises(ValueError, match="TELEMETRY_MONITORING_USAGE"):
        telemetry.reader(SimpleNamespace(client=CLIENT))
    monkeypatch.setenv("TRACING", "off")
    with pytest.raises(ValueError, match="TELEMETRY_MONITORING_USAGE"):
        check_settings()


def test_usage_http_contract_allows_unknown_calls_and_errors(monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kge.services.telemetry import routes

    source = Cloud(CLIENT, session=Session({"timeSeries": [series("input_tokens", 900)]}), project="project-1",
                   collection=Captured, monitoring_verified=True)
    row = source.usage(START, END, "cerebro")[0]
    assert row["calls"] is None and row["errors"] is None and row["input_tokens"] is None
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.engineer] = lambda: "engineer"
    monkeypatch.setattr(routes, "served", SimpleNamespace(PROFILE=SimpleNamespace(client=CLIENT)))
    monkeypatch.setattr(routes.service, "usage", lambda *args: {"usage": [row]})
    with TestClient(app) as client:
        response = client.get("/telemetry/usage")
    assert response.status_code == 200 and response.json()["usage"] == [row]


def test_kge_success_status_on_root_is_not_overridden_by_failed_attempt():
    from kge.adapters.telemetry.aggregate import Collection

    root = span(**{"kge.telemetry.origin": "kge", "kge.agent": "kge_http", "http.response.status_code": "200"})
    root["name"] = "POST /ask"
    attempt = span("2", **{"kge.telemetry.origin": "kge", "kge.agent": "kge_http", "kge.outcome": "failed"})
    attempt["parentSpanId"] = "1"
    trace = Collection(_spans({"traceId": TRACE, "spans": [root, attempt]}, CLIENT)).trace(TRACE)
    assert trace["outcome"] == "ok"


def test_empty_monitoring_overview_never_falls_back_but_usage_keeps_legacy_fallback():
    overview_http = Session({"timeSeries": []})
    cloud = Cloud(CLIENT, session=overview_http, project="project-1", collection=Captured, monitoring_verified=True)
    result = cloud.overview(START, END)
    assert result["summary"]["turns"] is None
    assert result["summary"]["model_calls"] is None
    assert len(overview_http.calls) == 1
    usage_http = Session({"timeSeries": []}, {"traces": []})
    cloud = Cloud(CLIENT, session=usage_http, project="project-1", collection=Captured, monitoring_verified=True)
    cloud.usage(START, END, None)
    assert len(usage_http.calls) == 2
