"""/agent reaches KGE as the signed-in person (#497): each request opens its own KGE tools on the MCP transport with the
person's own bearer, from the request's header or the session cookie, hands them to that turn alone and never to the
shared agent; a business person's reply is compact and an engineer's whole; streamed, each pipeline event the tools
relay during the ask call reaches the stream while the call still runs, once and in order. No built graph is read."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from kge.adapters.kgetools import SignInRefused, ToolRefused, kge_tools
from kge.pipeline.trace import stream_event
from kge.services import http
from kge.services.ask import routes as ask_routes, service as ask_service
from kge.services.ask.models import AgentAsk

BUSINESS, ENGINEER = "analyst_retail", "knowledge_engineer"
TOKENS = {"tok-business": BUSINESS, "tok-engineer": ENGINEER}
SESSIONS = {"sid-business": (BUSINESS, "session-bearer-business")}
COOKIE = "kge_session"
INTERNALS = ("trace", "run_trace", "sql", "plan", "path")
ENGINEER_ONLY = ("trace", "run_trace", "plan", "path")
ANSWER = {"question": "q", "purpose": "analysis", "behaviour": "figure", "answer_text": "NNA was 10.",
          "phrased_by": "template", "trace": ["planned"], "sql": "SELECT 10", "plan": {"steps": ["a"]}, "figure": 10}
NODE = {"event": "node_added", "node": {"id": "term:nna", "type": "Term", "label": "NNA", "state": "done"}}
EDGE = {"event": "edge_added", "edge": {"source": "term:nna", "target": "col:nna", "type": "MAPS_TO"}}


def pack_roles(client):
    pack = yaml.safe_load((Path(__file__).resolve().parents[2] / "clients" / client / "pack.yaml").read_text())
    return {role["id"]: role for role in pack["access"]["roles"]}


class Rights:
    people = SimpleNamespace(is_admin=lambda who: False)

    def acting_roles(self, who):
        return {who}

    def role_templates(self):
        return pack_roles("meridian")

    def may(self, who, process):
        return who in (BUSINESS, ENGINEER)


class Ids:
    def resolve(self, authorization, cookie):
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
            return TOKENS.get(token) or next((who for who, held in SESSIONS.values() if held == token), None)
        return SESSIONS.get(cookie, (None,))[0]

    def session_bearer(self, cookie):
        held = SESSIONS.get(cookie)
        return None if held is None else {"access_token": held[1], "token_type": "Bearer", "expires_in": 900}


class Tools:
    """The KgeTools port as the MCP adapter offers it, opened with one bearer; ask relays the scripted events and
    holds the call open on gate, so a test can see the stream move before the answer comes."""

    def __init__(self, bearer, script):
        self.bearer = bearer
        self.script = script

    def get_context(self, question, *, purpose, arm, products, plan_vocabulary, history, runtimes):
        self.script.calls.append((self.bearer, "get_context", arm))
        return {"arm": arm}

    def ask(self, question, *, purpose, arm, stages, products, history, runtimes, on_event=None):
        self.script.calls.append((self.bearer, "ask", arm))
        if self.script.refuse is not None:
            raise self.script.refuse
        if self.script.barrier is not None:
            self.script.barrier.wait()
        for event in self.script.events:
            if on_event is not None:
                on_event(event)
        if self.script.gate is not None:
            self.script.released = self.script.gate.wait(10)
        return dict(ANSWER)


class Agent:
    """The shared reference agent as the route reaches it: its context step calls get_context on both arms, its plan
    step ask on the treatment arm, each through the turn's tools."""

    def __init__(self):
        self.g = self.e = None

    def ask(self, question, who, purpose="analysis", *, arm="treatment", products=None, history=(), runtimes=None,
            tools=None, on_event=None):
        tools.get_context(question, purpose=purpose, arm=arm, products=products, plan_vocabulary=True, history=history,
                          runtimes=runtimes)
        out = dict(ANSWER)
        if arm == "treatment":
            out = tools.ask(question, purpose=purpose, arm="graph", stages=None, products=products, history=history,
                            runtimes=runtimes, on_event=on_event)
        return {**out, "principal": who, "arm": arm, "agent": {"mode": "rules"}}


@pytest.fixture
def script(monkeypatch, tmp_path):
    monkeypatch.delenv("STORE_URL", raising=False)
    state = SimpleNamespace(calls=[], opened=[], events=[], gate=None, released=None, barrier=None, refuse=None, app=object(), agent=Agent())
    fake = SimpleNamespace(engine=lambda: SimpleNamespace(g=Rights()), PROFILE=SimpleNamespace(run_dir=lambda: tmp_path, client="meridian"),
                           ids=lambda: Ids(), COOKIE=COOKIE, SIGN_IN="Sign in first.", app=state.app,
                           agent=lambda: state.agent, UNRELEASED_MESSAGE=None)
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(ask_routes, "served", fake)

    def opened(transport, *, url, bearer, app, loop):
        assert loop is not None
        state.opened.append((transport, url, bearer))
        return Tools(bearer, state)
    monkeypatch.setattr(ask_routes, "kge_tools", opened)
    monkeypatch.setattr(ask_service, "log_unmatched", lambda *a, **k: None)
    monkeypatch.setattr(ask_service, "build_path", lambda e, out: {"nodes": [], "edges": []})
    monkeypatch.setattr(ask_service, "build_graph_path", lambda e, out: {"nodes": [], "edges": []})
    return state


@pytest.fixture
def client(script):
    app = FastAPI()
    app.include_router(ask_routes.router)
    with TestClient(app) as c:
        yield c


def agent_attributes(agent):
    return dict(vars(agent))


@pytest.mark.parametrize("arm, called", [("treatment", ["get_context", "ask"]), ("control", ["get_context"])])
@pytest.mark.parametrize("token, who", [("tok-business", BUSINESS), ("tok-engineer", ENGINEER)])
def test_each_request_opens_mcp_tools_with_the_persons_own_bearer(client, script, token, who, arm, called):
    held = agent_attributes(script.agent)
    for header in ("authorization", "x-kge-authorization"):
        r = client.post("/agent", json={"question": "NNA for June 2025?", "arm": arm}, headers={header: f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert r.json()["principal"] == who
    assert script.opened == [("mcp", "http://kge", token)] * 2
    assert [(bearer, name) for bearer, name, _ in script.calls] == [(token, name) for name in called] * 2
    assert agent_attributes(script.agent) == held


def test_a_cookie_session_reaches_kge_with_the_sessions_own_bearer(client, script):
    client.cookies.set(COOKIE, "sid-business")
    r = client.post("/agent", json={"question": "NNA for June 2025?"})
    assert r.status_code == 200, r.text
    assert script.opened == [("mcp", "http://kge", "session-bearer-business")]
    assert {bearer for bearer, _, _ in script.calls} == {"session-bearer-business"}


def test_two_people_at_once_each_reach_kge_with_their_own_bearer(client, script):
    script.barrier = threading.Barrier(len(TOKENS), timeout=10)
    with ThreadPoolExecutor(len(TOKENS)) as pool:
        replies = list(pool.map(lambda token: client.post("/agent", json={"question": "q"},
                                                          headers={"authorization": f"Bearer {token}"}), TOKENS))
    assert [r.json()["principal"] for r in replies] == list(TOKENS.values())
    assert sorted(bearer for bearer, _, _ in script.calls) == sorted([*TOKENS, *TOKENS])


def test_a_business_reply_is_compact_and_an_engineers_whole(client, script):
    business = client.post("/agent", json={"question": "q"}, headers={"authorization": "Bearer tok-business"}).json()
    assert not set(ENGINEER_ONLY) & set(business), business
    assert (business["answer_text"], business["sql"]) == ("NNA was 10.", "SELECT 10")
    engineer = client.post("/agent", json={"question": "q"}, headers={"authorization": "Bearer tok-engineer"}).json()
    assert {key: engineer[key] for key in ("trace", "sql", "plan")} == {key: ANSWER[key] for key in ("trace", "sql", "plan")}
    assert "run_trace" in engineer and "path" in engineer


def request_as(token, *, stream=False, cookies=None):
    """A request as the route function reads it, carrying token's bearer, streamed or not, and still connected."""
    headers = {"authorization": f"Bearer {token}"} if token else {}
    if stream:
        headers["accept"] = "application/x-ndjson"

    async def connected():
        return False
    return SimpleNamespace(headers=headers, cookies=cookies or {}, is_disconnected=connected)


def served_by_route(request, who, arm="treatment"):
    """The route function's reply, run as the app runs it, on an event loop."""
    async def call():
        return await ask_agent(request, who, arm)
    return anyio.run(call)


def ask_agent(request, who, arm):
    return ask_routes.ask_agent(AgentAsk(question="NNA for June 2025?", arm=arm), request, who=who)


def read_stream(token, gate):
    """/agent streamed as the holder of token: each line with whether gate was set when it came, gate set once the
    relayed node is seen, never before."""
    lines = []

    async def read():
        response = await ask_agent(request_as(token, stream=True), TOKENS[token], "treatment")
        async for chunk in response.body_iterator:
            for line in chunk.decode().splitlines():
                event = json.loads(line)
                lines.append((event, gate.is_set()))
                if event.get("event") == "node_added" and event["node"]["id"] == NODE["node"]["id"]:
                    gate.set()
    anyio.run(read)
    return lines


@pytest.mark.parametrize("token", ["tok-business", "tok-engineer"])
def test_the_stream_sends_each_relayed_event_while_the_ask_call_runs(script, token):
    script.events = [NODE, EDGE, NODE]
    script.gate = threading.Event()
    lines = read_stream(token, script.gate)
    assert script.released is True
    events = [event for event, _ in lines]
    [node_seen] = [i for i, event in enumerate(events) if event.get("event") == "node_added"]
    edge_seen = next(i for i, event in enumerate(events) if event.get("event") == "edge_added")
    assert node_seen < edge_seen
    assert lines[node_seen][1] is False
    assert events[-1]["event"] == "answer"
    final = events[-1]["answer"]
    if token == "tok-business":
        assert not set(ENGINEER_ONLY) & set(final), final
        assert final["sql"] == "SELECT 10"
        assert not [event for event in events if event.get("event") in ("plan", "stage")]
    else:
        assert final["sql"] == "SELECT 10"
        assert events[node_seen]["node"] == NODE["node"]


def test_without_a_stream_nothing_is_relayed_to_publish(script):
    seen = []

    class Recorded(Agent):
        def ask(self, *args, on_event=None, **kwargs):
            seen.append(on_event)
            return super().ask(*args, on_event=on_event, **kwargs)
    script.agent = Recorded()
    out = served_by_route(request_as("tok-engineer"), ENGINEER)
    assert out["answer_text"] == "NNA was 10."
    assert seen == [None]


def test_no_bearer_and_no_session_is_refused_before_the_agent_runs(script):
    with pytest.raises(HTTPException) as refused:
        served_by_route(request_as(None, cookies={COOKIE: "sid-none"}), BUSINESS)
    assert refused.value.status_code == 401
    assert script.calls == [] and script.opened == []


CONTEXT = {"arm": "treatment", "block": "", "sources": [], "units": ["retail"], "certified": {}, "in_window": [],
           "defines": {}, "meta": {}, "withheld": [], "needed": []}
FULL = {**ANSWER, "principal": "p", "arm": "graph", "versions": {"planner": "v"}, "run_trace": {"stages": []},
        "combined": False}


@pytest.fixture
def kge(script, monkeypatch):
    """The API's own ask routes and its /mcp and /mcp/answer servers, the real ones, on one app the route's MCP tools
    call in this process, behind the request limits: the tools' work stubbed (the context package, the answer), their
    sign-in, row checks, person slots and role audit not. kge.asked holds (who, compact) for each answer; kge.inside, a
    barrier the answers meet at, where set."""
    from contextlib import asynccontextmanager

    from kge.services.ask import mcp as ask_mcp
    context_server, answer_server = ask_mcp.mcp_server(), ask_mcp.answer_mcp_server()

    @asynccontextmanager
    async def lifespan(_app):
        async with context_server.session_manager.run(), answer_server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(ask_routes.router)
    app.add_route("/mcp", ask_mcp.MCPEndpoint(context_server), methods=["GET", "POST", "DELETE"])
    app.add_route("/mcp/answer", ask_mcp.MCPEndpoint(answer_server), methods=["GET", "POST", "DELETE"])
    app.add_middleware(http.RequestLimitsMiddleware)
    held = SimpleNamespace(asked=[], inside=None, events=[], gate=None, released=None)

    def answered(comb, profile, question, who, purpose, arm, cfg, *, products=None, history=None, compact=False,
                 runtimes=None, on_stage=None):
        held.asked.append((who, compact))
        if held.inside is not None:
            held.inside.wait()
        for event in held.events:
            stream_event(event)
        if held.gate is not None:
            held.released = held.gate.wait(10)
        out = {**FULL, "principal": who, "question": question}
        return {k: v for k, v in out.items() if k not in ENGINEER_ONLY} if compact else out

    monkeypatch.setattr(ask_routes, "kge_tools", kge_tools)
    monkeypatch.setattr(ask_mcp, "served", http.served)
    monkeypatch.setattr(ask_mcp, "context_package", lambda a, who: {**CONTEXT, "arm": a.arm})
    monkeypatch.setattr(ask_mcp, "run_config", lambda body, path: object())
    monkeypatch.setattr(ask_mcp.service, "ask", answered)
    http.served.app = app
    http.served.combined = lambda: None
    with TestClient(app) as client:
        held.client = client
        yield held


def audit(served):
    from kge.adapters.roleaudit import role_audit_log
    return [(e["who"], e["endpoint"], e["outcome"]) for e in role_audit_log(served.PROFILE.run_dir(), served.PROFILE.client).newest(1000)]


@pytest.mark.parametrize("token, who", [("tok-business", BUSINESS), ("tok-engineer", ENGINEER)])
def test_the_treatment_arm_reaches_get_context_and_ask_over_mcp_as_the_person(kge, token, who):
    r = kge.client.post("/agent", json={"question": "NNA for June 2025?", "arm": "treatment"},
                        headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert sorted(audit(http.served)) == sorted([(who, "POST /agent", "allowed"), (who, "mcp get_context", "allowed"),
                                                 (who, "mcp ask", "allowed")])
    assert kge.asked == [(who, who == BUSINESS)]
    body = r.json()
    assert body["principal"] == who and body["answer_text"] == "NNA was 10."
    if who == BUSINESS:
        assert not set(ENGINEER_ONLY) & set(body), body
        assert body["sql"] == "SELECT 10"
    else:
        assert body["sql"] == "SELECT 10" and "run_trace" in body


@pytest.mark.parametrize("token, who", [("tok-business", BUSINESS), ("tok-engineer", ENGINEER)])
def test_the_control_arm_reaches_get_context_alone(kge, token, who):
    r = kge.client.post("/agent", json={"question": "NNA for June 2025?", "arm": "control"},
                        headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert sorted(audit(http.served)) == sorted([(who, "POST /agent", "allowed"), (who, "mcp get_context", "allowed")])
    assert kge.asked == []


def test_a_cookie_session_reaches_mcp_as_the_sessions_person(kge):
    kge.client.cookies.set(COOKIE, "sid-business")
    r = kge.client.post("/agent", json={"question": "NNA for June 2025?"})
    assert r.status_code == 200, r.text
    assert {(who, endpoint) for who, endpoint, _ in audit(http.served)} == {
        (BUSINESS, "POST /agent"), (BUSINESS, "mcp get_context"), (BUSINESS, "mcp ask")}


def test_three_agent_calls_at_once_each_answer_within_the_persons_allowance(kge):
    assert http.MAX_IN_FLIGHT >= 3
    kge.inside = threading.Barrier(3, timeout=20)
    with ThreadPoolExecutor(3) as pool:
        replies = list(pool.map(lambda _: kge.client.post("/agent", json={"question": "NNA for June 2025?"},
                                                         headers={"authorization": "Bearer tok-business"}), range(3)))
    assert [r.status_code for r in replies] == [200] * 3, [r.text for r in replies]
    assert kge.asked == [(BUSINESS, True)] * 3


@pytest.mark.parametrize("token", ["tok-business", "tok-engineer"])
def test_the_stream_moves_with_the_mcp_ask_calls_events_while_it_runs(kge, token):
    kge.events = [NODE, EDGE, NODE]
    kge.gate = threading.Event()
    lines = []

    async def read():
        response = await ask_agent(request_as(token, stream=True), TOKENS[token], "treatment")
        async for chunk in response.body_iterator:
            for line in chunk.decode().splitlines():
                event = json.loads(line)
                lines.append((event, kge.gate.is_set()))
                if event.get("event") == "node_added" and event["node"]["id"] == NODE["node"]["id"]:
                    kge.gate.set()
    kge.client.portal.call(read)
    assert kge.released is True
    events = [event for event, _ in lines]
    [node_seen] = [i for i, event in enumerate(events) if event.get("event") == "node_added"]
    [edge_seen] = [i for i, event in enumerate(events) if event.get("event") == "edge_added"]
    assert lines[node_seen][1] is False and node_seen < edge_seen
    assert [event["event"] for event in events if event.get("event") == "answer"] == ["answer"]
    assert events[-1]["event"] == "answer"
    assert {(who, endpoint) for who, endpoint, _ in audit(http.served)} == {
        (TOKENS[token], "mcp get_context"), (TOKENS[token], "mcp ask")}
    if token == "tok-business":
        assert not set(ENGINEER_ONLY) & set(events[-1]["answer"])
        assert events[-1]["answer"]["sql"] == "SELECT 10"
        assert not [event for event in events if event.get("event") in ("plan", "stage")]
    else:
        assert events[-1]["answer"]["sql"] == "SELECT 10"


STARVED_SECONDS = 20
SPOOFED = {"x-kge-in-process-hop": "true", "x-kge-hop": "1", "x-forwarded-for": "127.0.0.1"}


def test_an_outside_request_cannot_take_the_in_process_hop(kge):
    held = [http.person_slot(BUSINESS) for _ in range(http.MAX_IN_FLIGHT)]
    for slot in held:
        slot.__enter__()
    try:
        r = kge.client.post("/agent", json={"question": "q"}, headers={"authorization": "Bearer tok-business", **SPOOFED})
        assert r.status_code == 429, r.text
        loop = kge.client.portal.call(anyio.lowlevel.current_token)
        outside = kge_tools("mcp", url="http://kge", bearer="tok-business", app=http.served.app, loop=loop)
        with pytest.raises(ToolRefused) as refused:
            outside.ask("q", purpose="analysis", arm="graph", stages=None, products=None, history=(), runtimes=None)
        assert refused.value.status == 429
        hop = kge_tools("mcp", url="http://kge", bearer="tok-business", app=http.in_process(http.served.app), loop=loop)
        assert hop.ask("q", purpose="analysis", arm="graph", stages=None, products=None, history=(), runtimes=None)["principal"] == BUSINESS
        assert kge.asked == [(BUSINESS, True)]
    finally:
        for slot in held:
            slot.__exit__(None, None, None)
    assert http._IN_FLIGHT.get(BUSINESS, 0) == 0


@pytest.mark.parametrize("refusal, status", [(SignInRefused(401, "Sign in first."), 401),
                                             (ToolRefused(403, "analyst_retail may not do this"), 403)])
def test_a_tool_refusing_the_person_mid_turn_is_its_own_status_as_json_and_streamed(client, script, refusal, status):
    script.refuse = refusal
    r = client.post("/agent", json={"question": "q"}, headers={"authorization": "Bearer tok-business"})
    assert (r.status_code, r.json()) == (status, {"detail": refusal.detail})
    r = client.post("/agent", json={"question": "q"}, headers={"authorization": "Bearer tok-business", "accept": "application/x-ndjson"})
    assert r.status_code == 200, r.text
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert events[-1] == {"event": "error", "error": refusal.detail, "status": status}
    assert not [event for event in events if event.get("event") == "answer"]


def test_agent_calls_at_once_cannot_starve_their_nested_tool_calls_of_threads(kge):
    def tokens(n):
        def set_tokens():
            limiter = anyio.to_thread.current_default_thread_limiter()
            held, limiter.total_tokens = limiter.total_tokens, n
            return held
        return kge.client.portal.call(set_tokens)
    calls = 3
    kge.inside = threading.Barrier(calls, timeout=STARVED_SECONDS)
    held = tokens(calls)
    pool = ThreadPoolExecutor(calls)
    try:
        futures = [pool.submit(kge.client.post, "/agent", json={"question": "q"}, headers={"authorization": "Bearer tok-business"})
                   for _ in range(calls)]
        done, waiting = wait(futures, timeout=STARVED_SECONDS)
    finally:
        tokens(held)
        kge.inside.abort()
        pool.shutdown(wait=True)
    assert not waiting, f"{len(waiting)} of {calls} /agent calls still waiting after {STARVED_SECONDS} s: their nested tool calls are starved of threads"
    assert [f.result().status_code for f in futures] == [200] * calls, [f.result().text for f in futures]
    assert kge.asked == [(BUSINESS, True)] * calls


def test_the_hop_reaches_the_app_as_no_loopback_caller_and_with_the_hop_flag(script):
    import httpx
    seen = []

    async def app(scope, receive, send):
        seen.append((scope["client"], http._IN_PROCESS_HOP.get(), http.demo_allowed(Request(scope))))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=http.in_process(app)), base_url="http://127.0.0.1") as c:
            await c.get("/")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            await c.get("/", headers={"x-kge-in-process-hop": "true"})
    http.served.DEMO_MODE, http.served.LOOPBACK = True, {"127.0.0.1", "::1"}
    anyio.run(call)
    assert seen[0] == (http.IN_PROCESS_CLIENT, True, False)
    assert http.IN_PROCESS_CLIENT[0] not in http.served.LOOPBACK
    assert seen[1][1:] == (False, True)
