"""Public background events, streaming refusals and run configuration errors."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kge.services.ask import service
from kge.services.ask.models import Ask, Stages
from kge.services.ask.routes import refusal, run_config


@pytest.mark.parametrize("compact", [True, False])
def test_background_poll_projects_events_for_compact_requests(monkeypatch, compact):
    events = [
        {"event": "node_added", "node": {"id": "n"}},
        {"event": "context", "context": {"id": "c", "label": "Revenue", "sql": "SELECT 1"}},
        {"event": "progress", "stage": "warehouse"},
    ]
    record = {"id": "run", "principal": "analyst", "identity": "release", "readable": ["unit"],
              "request": {"compact": compact}, "state": "running", "expires_at": 1000,
              "poll_after": 1, "next_poll": 0, "lease": 0, "events": [], "jobs": []}

    class State:
        def get(self, run_id):
            assert run_id == "run"
            return record, 1

        def put(self, run_id, value, revision):
            assert run_id == "run" and value is record
            return revision + 1

    def run_pass(c, profile, record, save, emit, clock):
        for event in events:
            emit(event)
        return {"answer_text": "done"}

    monkeypatch.setattr(service, "_run_pass", run_pass)
    combined = SimpleNamespace(e=SimpleNamespace(identity="release"),
                               g=SimpleNamespace(readable_units=lambda who: {"unit"}))
    result = service.get_run(combined, None, "run", "analyst", state=State(), clock=lambda: 100)

    assert result["state"] == "done"
    assert result["events"] == ([{"event": "context", "context": {"id": "c", "label": "Revenue"}},
                                 {"event": "progress", "stage": "warehouse"}] if compact else events)
    assert result["cursor"] == len(result["events"])


def test_refusal_event_preserves_http_status():
    assert refusal(HTTPException(status_code=403, detail="Access denied")) == {
        "event": "error", "error": "Access denied", "status": 403,
    }
    assert refusal(ValueError("other")) is None


def test_invalid_stage_run_config_returns_exact_problem_in_400_body(monkeypatch):
    from kge.services.ask import routes

    monkeypatch.setattr(routes.served, "combined", lambda: SimpleNamespace(llm=None))
    response = run_config(Ask(question="Metric", stages=Stages(resolution="invalid")), "answer")
    problem = "resolution must be one of none, words, names, glossary, glossary+model, not 'invalid'"
    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": problem,
        "problems": [problem],
        "config": {"resolution": "invalid", "context": "graph", "sql_source": "planner",
                   "citation_rule": "shelves", "renderer": "template"},
    }


def test_failed_run_events_end_with_its_error():
    out = {"id": "run", "state": "failed", "cursor": 2,
           "events": [{"event": "started"}, {"event": "progress", "stage": "warehouse"}],
           "error": "The warehouse could not finish."}
    events = service.run_events(out)
    assert [event["event"] for event in events] == ["run", "started", "progress", "error"]
    assert [event["cursor"] for event in events[:-1]] == [0, 1, 2]
    assert events[-1] == {"event": "error", "error": "The warehouse could not finish."}
