"""The KgeTools port over MCP (#497): McpKgeTools calls KGE's own get_context and ask tools as the person whose bearer
it carries, through the app's MCP mounts with their session managers running (TestClient as a context manager runs
the lifespan). A 401 or 403 is a typed refusal; an ask given on_event relays the pipeline events the direct /ask
stream sends, in order and each once, and the tool's result is the same as without it. Stubs stand in for the served
release and the answer, so no built graph is read."""
from __future__ import annotations

import contextvars
import json
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from anyio.lowlevel import current_token
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters.deadline import ExternalCallError
from kge.adapters.kgetools import AccessRefused, KgeToolsSettingError, SignInRefused, ToolRefused, kge_tools
from kge.pipeline.trace import stream_event
from kge.services import http
from kge.services.ask import mcp, routes, service
from kge.services.ask.engine.config import PRESETS

pytestmark = pytest.mark.unit

ROLES = {"ana": "analyst_retail", "eve": "knowledge_engineer", "tom": "tagger"}
TEMPLATES = {"analyst_retail": {"processes": ["querying"]},
             "knowledge_engineer": {"processes": ["drafting", "building", "improving", "querying"]},
             "tagger": {"processes": ["tagging"]}}
QUESTION = "NNA for June 2025"
NODE = {"id": "n1", "type": "Term", "label": "NNA", "state": "done", "time": 1.5, "props": {"unit": "retail"}}
SECOND = {"id": "n2", "type": "Column", "label": "nna", "state": "done", "time": 2.0, "props": {"unit": "retail"}}
LINK = {"from": "n1", "to": "n2", "type": "MAPS_TO", "label": "maps to"}


def answered(question: str, who: str, arm: str, compact: bool) -> dict:
    full = {"question": question, "principal": who, "purpose": "analysis", "arm": arm,
            "behaviour": "combined", "answer_text": "Certified: NNA for June 2025 was 10.", "phrased_by": "template",
            "trace": ["planned"], "versions": {"planner": "test"}, "combined": True,
            "run_trace": {"stages": [{"stage": "parse", "ms": 1.0}, {"stage": "plan", "ms": 2.0}]},
            "graph_path": {"nodes": [NODE, SECOND], "edges": [LINK], "unused": 0}}
    return service.response_view(full, compact)


def fake_ask(c, profile, question, who, purpose, arm, cfg, products=None, history=(), compact=False, runtimes=None,
             on_stage=None):
    stage = on_stage or (lambda name: None)
    stage("parse")
    stream_event({"event": "node_added", "node": NODE})
    stage("plan")
    stream_event({"event": "plan", "plan": {"context": "retail.nna", "measures": ["nna"]}})
    elsewhere = threading.Thread(target=contextvars.copy_context().run, args=(stream_event, {"event": "node_added", "node": SECOND}))
    elsewhere.start()
    elsewhere.join()
    stream_event({"event": "edge_added", "edge": LINK})
    return answered(question, who, arm, compact)


@pytest.fixture
def served(tmp_path, monkeypatch):
    seen: list[str | None] = []

    def resolve(header, cookie):
        seen.append(header)
        token = header.removeprefix("Bearer ") if header else None
        return token if token in ROLES else None
    engine = SimpleNamespace(identity={"release": "r1", "hash": "f" * 64},
                             g=SimpleNamespace(may=lambda who, process: process in TEMPLATES[ROLES[who]]["processes"],
                                               acting_roles=lambda who: {ROLES[who]} if who in ROLES else set(),
                                               people=SimpleNamespace(is_admin=lambda who: False),
                                               role_templates=lambda: TEMPLATES))
    combined = SimpleNamespace(default_config=lambda arm: PRESETS[arm], llm=None)
    state = SimpleNamespace(engine=lambda: engine, combined=lambda: combined, PROFILE=SimpleNamespace(run_dir=lambda: tmp_path, client="meridian"),
                            SIGN_IN="sign in", COOKIE="kge_session", ids=lambda: SimpleNamespace(resolve=resolve))
    for module in (http, routes, mcp):
        monkeypatch.setattr(module, "served", state)
    monkeypatch.setattr(service, "ask", fake_ask)
    monkeypatch.setattr(mcp, "context_package", lambda body, who: {"arm": body.arm, "units": ["retail"], "block": f"for {who}",
                                                                      "sources": [], "certified": {}, "in_window": [], "defines": {},
                                                                      "meta": {}, "withheld": [], "needed": []})
    return SimpleNamespace(seen=seen)


@pytest.fixture
def client(served):
    context, answer = mcp.mcp_server(), mcp.answer_mcp_server()

    @asynccontextmanager
    async def lifespan(_app):
        async with context.session_manager.run(), answer.session_manager.run():
            yield
    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router)
    app.add_route("/mcp", mcp.MCPEndpoint(context), methods=["GET", "POST", "DELETE"])
    app.add_route("/mcp/answer", mcp.MCPEndpoint(answer), methods=["GET", "POST", "DELETE"])
    with TestClient(app) as test_client:
        test_client.tools = lambda who: kge_tools("mcp", url="http://kge", bearer=who, app=app,
                                                  loop=test_client.portal.call(current_token))
        yield test_client


def ask(tools, on_event=None, arm="graph"):
    return tools.ask(QUESTION, purpose="analysis", arm=arm, stages=None, products=None, history=[], runtimes=None,
                     on_event=on_event)


def direct_stream(client, who: str) -> list[dict]:
    r = client.post("/ask", json={"question": QUESTION, "arm": "graph"},
                    headers={"Authorization": f"Bearer {who}", "Accept": "application/x-ndjson"})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def test_the_bearer_reaches_mcp_principal_as_the_person_on_both_tools(served, client):
    tools = client.tools("ana")
    package = tools.get_context(QUESTION, purpose="analysis", arm="treatment", products=None, plan_vocabulary=False,
                                history=[], runtimes=None)
    answer = ask(tools)
    assert package["block"] == "for ana" and answer["principal"] == "ana"
    assert served.seen and set(served.seen) == {"Bearer ana"}


def test_a_bearer_no_one_holds_is_a_typed_401(served, client):
    with pytest.raises(SignInRefused) as refused:
        ask(client.tools("nobody"))
    assert refused.value.status == 401 and isinstance(refused.value, ToolRefused)


def test_a_person_without_the_querying_role_is_a_typed_403(served, client):
    with pytest.raises(AccessRefused) as refused:
        ask(client.tools("tom"))
    assert refused.value.status == 403 and "tom" in refused.value.detail


def test_a_bad_arm_is_the_tool_400_and_not_a_typed_sign_in_or_access_refusal(served, client):
    with pytest.raises(ToolRefused) as refused:
        ask(client.tools("ana"), arm="treatment")
    assert refused.value.status == 400 and not isinstance(refused.value, (SignInRefused, AccessRefused))


@pytest.mark.parametrize("who", ["ana", "eve"])
def test_an_ask_relays_the_direct_streams_events_in_order_once_each(served, client, who):
    direct = direct_stream(client, who)
    assert direct[-1]["event"] == "answer"
    relayed: list[dict] = []
    answer = ask(client.tools(who), relayed.append)
    assert relayed == direct[:-1]
    assert len(relayed) > 3 and any(event["event"] == "node_added" for event in relayed)
    assert answer == direct[-1]["answer"]


def test_a_business_persons_relayed_events_are_compact_as_their_direct_stream(served, client):
    relayed: list[dict] = []
    ask(client.tools("ana"), relayed.append)
    assert not {"plan", "stage"} & {event["event"] for event in relayed}
    engineer: list[dict] = []
    ask(client.tools("eve"), engineer.append)
    assert {"plan", "stage"} <= {event["event"] for event in engineer}


def test_the_tools_result_is_the_same_with_and_without_events(served, client):
    tools = client.tools("eve")
    assert ask(tools, lambda event: None) == ask(tools)


def test_a_call_without_progress_is_answered_in_json_as_before(served, client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ask", "arguments": {"question": QUESTION}}}
    headers = {"Authorization": "Bearer ana", "accept": "application/json, text/event-stream"}
    plain = client.post("/mcp/answer", json=body, headers=headers)
    assert plain.headers["content-type"].startswith("application/json")
    assert plain.json()["result"]["structuredContent"]["principal"] == "ana"
    streamed = client.post("/mcp/answer", json={**body, "params": {**body["params"], "_meta": {"progressToken": 7}}}, headers=headers)
    assert streamed.headers["content-type"].startswith("text/event-stream")
    notes = [json.loads(line[5:]) for line in streamed.text.splitlines() if line.startswith("data:") and line[5:].strip()]
    progress = [note["params"] for note in notes if note.get("method") == "notifications/progress"]
    assert [p["progress"] for p in progress] == list(range(1, len(progress) + 1)) and progress
    assert all(p["progressToken"] == 7 for p in progress)


def test_an_unknown_transport_is_refused_by_name():
    with pytest.raises(KgeToolsSettingError, match="'carrier pigeon'"):
        kge_tools("carrier pigeon")


def test_a_server_that_does_not_answer_is_a_transient_external_call_error():
    tools = kge_tools("mcp", url="http://127.0.0.1:9", bearer="ana", timeout=2)
    with pytest.raises(ExternalCallError) as failed:
        ask(tools)
    assert failed.value.transient


def test_events_reach_on_event_while_the_answer_is_still_running(served, client, monkeypatch):
    arrived = threading.Event()
    waited: list[bool] = []

    def slow_ask(c, profile, question, who, purpose, arm, cfg, products=None, history=(), compact=False, runtimes=None,
                 on_stage=None):
        on_stage("parse")
        stream_event({"event": "node_added", "node": NODE})
        waited.append(arrived.wait(5))
        return answered(question, who, arm, compact)
    monkeypatch.setattr(service, "ask", slow_ask)
    ask(client.tools("eve"), lambda event: arrived.set())
    assert waited == [True]


def test_an_on_event_that_raises_stops_the_call_with_its_own_error(served, client, monkeypatch):
    import time

    class ReaderGone(Exception):
        pass

    def slow_ask(c, profile, question, who, purpose, arm, cfg, products=None, history=(), compact=False, runtimes=None,
                 on_stage=None):
        on_stage("parse")
        stream_event({"event": "node_added", "node": NODE})
        threading.Event().wait(3)
        return answered(question, who, arm, compact)

    def gone(event):
        raise ReaderGone()
    monkeypatch.setattr(service, "ask", slow_ask)
    started = time.monotonic()
    with pytest.raises(ReaderGone):
        ask(client.tools("eve"), gone)
    assert time.monotonic() - started < 2.5


def test_each_tool_s_output_schema_is_compiled_once_and_every_answer_is_checked_against_it(served, monkeypatch):
    """The SDK compiled ask's output schema again on every call, on the event loop; the tools' manager compiles each
    tool's once, as the tool is added, and checks every structured answer against it, refusing one that does not hold."""
    import jsonschema
    from mcp import types
    from mcp.server.fastmcp.exceptions import ToolError
    compiled, real = [], jsonschema.validators.validator_for

    def validator_for(schema, *args, **kwargs):
        if isinstance(schema, dict) and "$defs" in schema or schema in tops:
            compiled.append(schema)
        return real(schema, *args, **kwargs)
    tops: list = []
    monkeypatch.setattr(jsonschema.validators, "validator_for", validator_for)
    server = mcp.answer_mcp_server()
    manager = server._tool_manager
    names = ("ask", "list_runtimes", "feedback", "question_run", "stop_question_run")
    tops.extend(manager.get_tool(name).output_schema for name in names)
    assert all(tops) and set(manager.outputs) == set(names)
    built = len(compiled)
    assert built > 0
    good = answered(QUESTION, "ana", "graph", True)
    result = manager.checked("ask", ([types.TextContent(type="text", text="{}")], good))
    assert isinstance(result, types.CallToolResult) and result.structuredContent == good and not result.isError
    with pytest.raises(ToolError, match="Output validation error: .* is not valid under any of the given schemas"):
        manager.checked("ask", ([], {k: v for k, v in good.items() if k != "principal"}))
    with pytest.raises(ToolError, match="no structured output returned"):
        manager.checked("ask", ([], None))
    assert len(compiled) == built, "no call compiles a schema again"


def test_an_ask_call_through_the_mcp_mount_never_runs_the_sdk_s_per_call_schema_check(served, client, monkeypatch):
    import mcp.server.lowlevel.server as lowlevel
    def per_call(*args, **kwargs):
        raise AssertionError("the SDK's per-call jsonschema.validate ran")
    monkeypatch.setattr(lowlevel.jsonschema, "validate", per_call)
    assert ask(client.tools("ana"))["principal"] == "ana"
