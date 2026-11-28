"""Background questions over MCP (#630): the ask tool's background argument, the question_run tool, and stopping a run
with the stop_question_run tool and DELETE /ask/runs/{run_id}, on a synthetic fixture and a stubbed warehouse. The runs
stay in KGE's durable question store; MCP keeps no session."""
from __future__ import annotations

import json
import threading
import time
from functools import cache
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from kge.pipeline import questionrun
from kge.services import http
from kge.services.ask import mcp, routes, service
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.models import AskResponse, AskToolResponse, QuestionRunResponse

PEOPLE = {"ana", "ben"}
BLOCK = {"v": 1, "headline": "15", "table": {"rows": [[15]]}, "queries": [{"sql": "SELECT 15"}], "checked": [True]}


class Warehouse:
    """A stubbed warehouse whose one job runs until `ready` is set, reporting progress on each poll while it runs."""

    def __init__(self, ready: bool = False):
        self.ready = threading.Event()
        if ready:
            self.ready.set()
        self.cancelled: list[str] = []
        self.polls = 0

    def submit(self, statement, **kwargs):
        return {"id": kwargs["job_id"], "guard": {"passed": True}, "meta": {}, "job": {}}

    def poll(self, handle):
        self.polls += 1
        if handle["id"] in self.cancelled:
            return {"state": "failed", "error": "cancelled"}
        if self.ready.is_set():
            return {"state": "done", "rows": [(15,)], "progress": {"elapsed_ms": 10 * self.polls}}
        return {"state": "running", "progress": {"elapsed_ms": 10 * self.polls}}

    def release(self, handle):
        pass

    def cancel(self, handle):
        self.cancelled.append(handle["id"])


def answered(c, profile, question, who, purpose, arm, cfg, products=None, history=(), compact=False, runtimes=None,
             on_stage=None, display_engine=None):
    """service.ask's stand-in: one figure read through the run's checkpointed warehouse job where it runs as a background
    question, and given as service.ask gives it (compact view, and the display block a display engine asks for)."""
    if questionrun.active():
        _, rows, _ = questionrun.execute(c.e.w, {"unit": "u", "units": ["u"], "sql": "SELECT 15"}, "reference", ())
    else:
        rows = [(15,)]
    full = {"question": question, "principal": who, "purpose": purpose, "arm": arm, "behaviour": "figure",
            "figure": rows[0][0], "answer_text": str(rows[0][0]), "phrased_by": "template", "sql": "SELECT 15",
            "sql_certified": True, "plan": {"measure": "m"}, "trace": ["planned"], "versions": {}, "combined": False,
            "run_trace": {"stages": []}}
    view = service.response_view(full, compact)
    if display_engine is not None:
        view["display"] = service.displayed(service.response_view(full), compact, display_engine)
    return view


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("QUESTION_STATE_BACKEND", "store")
    monkeypatch.setenv("QUESTION_START_WAIT", "0.2")
    monkeypatch.setenv("QUESTION_POLL_SECONDS", "0.01")
    engine = SimpleNamespace(identity={"release": "20261122T101500Z-abc123", "hash": "f" * 64},
                             g=SimpleNamespace(may=lambda who, process: process == "querying" and who in PEOPLE,
                                               acting_roles=lambda who: {"analyst_retail"} if who in PEOPLE else set(),
                                               people=SimpleNamespace(is_admin=lambda who: False),
                                               role_templates=lambda: {"analyst_retail": {"processes": ["querying"]}}))
    warehouse = Warehouse()
    combined = SimpleNamespace(e=SimpleNamespace(w=warehouse, identity="release-1"), llm=None,
                               default_config=lambda arm: PRESETS[arm], g=SimpleNamespace(readable_units=lambda who: {"u"}))
    profile = SimpleNamespace(run_dir=lambda: tmp_path, client="meridian")
    state = SimpleNamespace(engine=lambda: engine, combined=lambda: combined, PROFILE=profile, SIGN_IN="sign in",
                            COOKIE="kge_session",
                            ids=lambda: SimpleNamespace(resolve=lambda header, cookie: header.removeprefix("Bearer ") if header else None))
    for module in (http, routes, mcp):
        monkeypatch.setattr(module, "served", state)
    monkeypatch.setattr(service, "ask", answered)
    monkeypatch.setattr(service, "present", lambda answer, engine: dict(BLOCK))
    return SimpleNamespace(warehouse=warehouse, profile=profile)


@pytest.fixture
def client(served):
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, http.request_validation_failed)
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        yield test_client


@cache
def answer_server():
    """/mcp/answer's server, built once: its tools read the served app as each call runs."""
    return mcp.answer_mcp_server()


def call(who: str, name: str, arguments: dict) -> dict:
    """The tool's structured content, called as who over /mcp/answer."""
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={"authorization": f"Bearer {who}"})))
    result = anyio.run(lambda: answer_server()._tool_manager.call_tool(name, arguments, ctx, convert_result=True))
    return result.structuredContent


def started(who: str = "ana", **arguments) -> dict:
    return call(who, "ask", {"question": "Total revenue for June 2025?", "background": True, **arguments})["run"]


def followed(run_id: str, who: str = "ana", after: int = 0, until=lambda run: run["state"] != "running") -> dict:
    for _ in range(200):
        run = call(who, "question_run", {"run_id": run_id, "after": after})
        if until(run):
            return run
        time.sleep(0.02)
    pytest.fail("the run did not reach the state awaited")


@pytest.mark.parametrize("display", [False, True])
def test_a_fast_background_question_answers_as_the_same_call_without_background(served, display):
    served.warehouse.ready.set()
    plain = call("ana", "ask", {"question": "Total revenue for June 2025?", "display": display})
    background = call("ana", "ask", {"question": "Total revenue for June 2025?", "display": display, "background": True})
    assert "run" not in background
    assert background == plain
    assert ("display" in background) is display
    if display:
        assert background["display"] == {"v": 1, "headline": "15", "table": {"rows": [[15]]}}


def test_the_synchronous_ask_tool_answers_as_before(served):
    plain = call("ana", "ask", {"question": "Total revenue for June 2025?"})
    assert plain == {"question": "Total revenue for June 2025?", "principal": "ana", "purpose": "analysis", "arm": "graph",
                     "behaviour": "figure", "figure": 15, "answer_text": "15", "phrased_by": "template",
                     "sql": "SELECT 15", "versions": {}, "combined": False}
    assert served.warehouse.polls == 0
    tools = {item.name: item for item in anyio.run(answer_server().list_tools)}
    assert tools["ask"].outputSchema == AskToolResponse.model_json_schema()
    assert tools["ask"].outputSchema["type"] == "object"
    assert {"$ref": "#/$defs/AskResponse"} in tools["ask"].outputSchema["anyOf"]
    assert tools["ask"].outputSchema["$defs"]["AskResponse"] == {
        k: v for k, v in AskResponse.model_json_schema().items() if k != "$defs"}


def test_a_slow_background_question_returns_its_run_then_progress_then_the_answer(served):
    run = started()
    assert set(run) == {"id", "state", "expires_at", "poll_after", "events", "cursor"}
    assert run["state"] == "running" and run["poll_after"] == 0.005
    progress = followed(run["id"], until=lambda r: any(e.get("event") == "progress" for e in r["events"]))
    assert progress["state"] == "running"
    assert {"event": "progress", "stage": "warehouse", "state": "running", "statement": 1}.items() <= \
        next(e for e in progress["events"] if e.get("event") == "progress").items()
    served.warehouse.ready.set()
    done = followed(run["id"])
    assert done["state"] == "done"
    plain = call("ana", "ask", {"question": "Total revenue for June 2025?"})
    assert done["answer"] == plain
    trimmed = call("ana", "question_run", {"run_id": run["id"], "after": done["cursor"] - 1})
    assert trimmed["events"] == done["events"][-1:] and trimmed["cursor"] == done["cursor"]
    assert call("ana", "question_run", {"run_id": run["id"], "after": done["cursor"]})["events"] == []


def test_a_display_run_carries_the_compact_display_block_the_ask_tool_gives(served):
    run = started(display=True)
    served.warehouse.ready.set()
    done = followed(run["id"])
    assert done["answer"] == call("ana", "ask", {"question": "Total revenue for June 2025?", "display": True})
    assert done["answer"]["display"] == {"v": 1, "headline": "15", "table": {"rows": [[15]]}}


def test_another_persons_run_or_an_unknown_id_is_not_found(served, client):
    run = started()
    for name in ("question_run", "stop_question_run"):
        for who, run_id in (("ben", run["id"]), ("ana", "f" * 32)):
            with pytest.raises(ToolError) as refused:
                call(who, name, {"run_id": run_id})
            assert str(refused.value).endswith("404: Question run not found.")
    response = client.delete(f"/ask/runs/{run['id']}", headers={"Authorization": "Bearer ben"})
    assert response.status_code == 404 and response.json() == {"detail": "Question run not found."}
    assert served.warehouse.cancelled == []
    assert followed(run["id"], until=lambda r: True)["state"] == "running"


def test_an_expired_run_returns_its_ending_reason(served, monkeypatch):
    monkeypatch.setenv("QUESTION_RUN_TIMEOUT", "0.6")
    run = started()
    time.sleep(0.7)
    expired = followed(run["id"])
    assert expired["state"] == "expired" and expired["reason"] == "run_time_limit"
    assert expired["error"] == questionrun.TIME_LIMIT and "answer" not in expired
    assert len(served.warehouse.cancelled) == 1


def test_a_run_the_warehouse_ends_returns_the_warehouse_reason(served, monkeypatch):
    run = started()
    monkeypatch.setattr(served.warehouse, "poll", lambda handle: {
        "state": "failed", "error": "The warehouse exceeded its time limit; ask a narrower question.",
        "reason": questionrun.WAREHOUSE_TIME_LIMIT})
    ended = followed(run["id"])
    assert ended["state"] == "failed" and ended["reason"] == "warehouse_time_limit"


def test_the_stop_tool_cancels_the_warehouse_job_and_the_run_reads_stopped(served):
    run = started()
    stopped = call("ana", "stop_question_run", {"run_id": run["id"]})
    assert stopped["state"] == "failed" and stopped["reason"] == "stopped" and stopped["error"] == "Stopped."
    assert len(served.warehouse.cancelled) == 1
    served.warehouse.ready.set()
    time.sleep(0.05)
    assert call("ana", "question_run", {"run_id": run["id"]}) == stopped
    again = call("ana", "stop_question_run", {"run_id": run["id"]})
    assert again == stopped and len(served.warehouse.cancelled) == 1


def test_delete_stops_the_run_and_a_later_poll_reads_it_stopped(served, client):
    run = started()
    response = client.delete(f"/ask/runs/{run['id']}", headers={"Authorization": "Bearer ana"})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "failed" and body["reason"] == "stopped" and body["error"] == "Stopped."
    QuestionRunResponse.model_validate(body)
    assert len(served.warehouse.cancelled) == 1
    served.warehouse.ready.set()
    polled = client.get(f"/ask/runs/{run['id']}", headers={"Authorization": "Bearer ana"})
    assert polled.status_code == 200 and polled.json() == body


def test_a_run_that_already_ended_is_returned_unchanged_by_stop(served, monkeypatch):
    ids, start_run = [], service.start_run

    def recorded(*args, **kwargs):
        out = start_run(*args, **kwargs)
        ids.append(out["id"])
        return out
    monkeypatch.setattr(service, "start_run", recorded)
    served.warehouse.ready.set()
    run = call("ana", "ask", {"question": "Total revenue for June 2025?", "background": True})
    assert "run" not in run
    run_id = ids[0]
    done = call("ana", "question_run", {"run_id": run_id})
    assert done["state"] == "done"
    assert call("ana", "stop_question_run", {"run_id": run_id}) == done
    assert served.warehouse.cancelled == []


def test_stop_rereads_a_run_another_instance_changed_and_retries_once(served):
    from kge.adapters.questionstate import for_profile
    from kge.ports import StateConflict
    run = started()
    store = for_profile(served.profile)
    real_put, conflicts = store.put, []

    class Racing:
        def get(self, run_id):
            return store.get(run_id)

        def put(self, run_id, record, revision):
            if not conflicts:
                conflicts.append(run_id)
                raise StateConflict("changed")
            return real_put(run_id, record, revision)

    stopped = service.stop_run(SimpleNamespace(e=SimpleNamespace(w=served.warehouse)), served.profile, run["id"], "ana",
                               state=Racing())
    assert stopped["reason"] == "stopped" and conflicts == [run["id"]]

    class Conflicting(Racing):
        def put(self, run_id, record, revision):
            raise StateConflict("changed")

    other = started()
    with pytest.raises(StateConflict):
        service.stop_run(SimpleNamespace(e=SimpleNamespace(w=served.warehouse)), served.profile, other["id"], "ana",
                         state=Conflicting())


def test_a_store_error_is_the_question_states_503_on_both_transports(served, client, monkeypatch):
    from kge.ports import StateUnavailable
    run = started()

    def unavailable(*args, **kwargs):
        raise StateUnavailable("down")
    monkeypatch.setattr(service, "stop_run", unavailable)
    with pytest.raises(ToolError) as refused:
        call("ana", "stop_question_run", {"run_id": run["id"]})
    assert str(refused.value).endswith(f"503: {service.STATE_UNAVAILABLE}")
    response = client.delete(f"/ask/runs/{run['id']}", headers={"Authorization": "Bearer ana"})
    assert response.status_code == 503 and response.json() == {"detail": service.STATE_UNAVAILABLE}


def test_a_background_ask_that_asks_for_progress_sends_the_runs_events_as_they_come(served):
    """The ask tool's progress notifications go on with background: true, as without it, while the call waits."""
    sent = []

    async def report_progress(progress, total, message):
        sent.append((progress, message))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={"authorization": "Bearer ana"}),
                                                          meta=SimpleNamespace(progressToken=7)),
                          report_progress=report_progress)
    result = anyio.run(lambda: answer_server()._tool_manager.call_tool(
        "ask", {"question": "Total revenue for June 2025?", "background": True}, ctx, convert_result=True))
    assert "run" in result.structuredContent
    events = [json.loads(message) for _, message in sent]
    assert [n for n, _ in sent] == list(range(1, len(sent) + 1))
    assert any(e.get("event") == "progress" and e.get("stage") == "warehouse" for e in events)
    assert not any(e.get("event") == "answer" for e in events)


def test_a_pass_that_meets_a_stopped_run_cancels_its_own_jobs_and_gives_the_stopped_run():
    cancelled = []
    record = {"id": "r1", "jobs": [{"handle": {"id": "j1"}}, {"handle": {"id": "j2"}, "result": {}}]}
    stopped = {"id": "r1", "state": "failed", "reason": "stopped", "error": "Stopped.", "expires_at": 1.0, "poll_after": 2.0,
               "events": []}
    c = SimpleNamespace(e=SimpleNamespace(w=SimpleNamespace(cancel=lambda handle: cancelled.append(handle["id"]))))
    out = service._stopped_meanwhile(c, record, SimpleNamespace(get=lambda run_id: (stopped, 2)))
    assert out["state"] == "failed" and out["reason"] == "stopped" and cancelled == ["j1"]
    still = {**stopped, "state": "running"}
    assert service._stopped_meanwhile(c, record, SimpleNamespace(get=lambda run_id: (still, 2))) is None
    assert cancelled == ["j1"]


def test_a_run_whose_access_or_release_changed_says_so_in_its_reason(served, monkeypatch):
    run = started()
    changed = call("ana", "question_run", {"run_id": run["id"]})
    assert changed["state"] == "running"
    combined = mcp.served.combined()
    monkeypatch.setattr(combined.e, "identity", "release-2")
    ended = call("ana", "question_run", {"run_id": run["id"]})
    assert ended["state"] == "failed" and ended["reason"] == "access_changed"
    assert ended["error"] == "Access or the served release changed; ask the question again."


def test_a_warehouse_that_cannot_cancel_never_keeps_a_run_from_stopping(served, monkeypatch):
    run = started()

    def refuses(handle):
        raise RuntimeError("the job already ended")
    monkeypatch.setattr(served.warehouse, "cancel", refuses)
    stopped = call("ana", "stop_question_run", {"run_id": run["id"]})
    assert stopped["state"] == "failed" and stopped["reason"] == "stopped"


def test_bound_run_rest_stream_reconstructs_owner_scope(served, monkeypatch, tmp_path):
    import uuid
    from kge.adapters import modelusage
    from kge.adapters.modelusage import Actor, for_actor, usage_scope, current_usage, current_usage_conversation
    from kge.adapters.objstore.folder import LocalStore
    question, conversation, invocation = (str(uuid.uuid4()) for _ in range(3))
    book = for_actor(LocalStore(str(tmp_path / 'usage'), tmp_path), Actor('meridian', 'person', 'ana', 'cerebro'),
                     key=b'a-strong-test-only-secret-key-32bytes', conversation_id=conversation)
    book.open_question(question)
    book.start_invocation(question, invocation)
    with usage_scope(book, question, invocation, conversation_id=conversation):
        run = service.start_run(http.served.combined(), served.profile, mcp.Ask(question='Metric'), 'ana', PRESETS['graph'])
    book.finish_invocation_no_inference(question, invocation, reason='idle_poll')
    monkeypatch.setattr(http.served.engine().g, 'processes', lambda who: {'querying'}, raising=False)
    monkeypatch.setattr(http, 'person', lambda _: True)
    monkeypatch.setattr(modelusage, 'for_profile', lambda profile, actor, conversation_id: book)
    seen = []
    def scoped_answer(*args, **kwargs):
        seen.append((current_usage()[1], current_usage_conversation()))
        return answered(*args, **kwargs)
    monkeypatch.setattr(service, 'ask', scoped_answer)
    time.sleep(0.03)
    import httpx
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, http.request_validation_failed)
    app.include_router(routes.router)
    async def run_requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            denied = await client.get(f"/ask/runs/{run['id']}", headers={'Authorization': 'Bearer ben'})
            assert denied.status_code == 404
            served.warehouse.ready.set()
            response = await client.get(f"/ask/runs/{run['id']}", headers={'Authorization': 'Bearer ana',
                                                                             'Accept': 'application/x-ndjson'})
            assert response.status_code in (200, 202)
            return [json.loads(line) for line in response.text.splitlines() if line]
    events = anyio.run(run_requests)
    assert events and seen == [(question, conversation)]
    assert book.report(question)['complete'] is False
