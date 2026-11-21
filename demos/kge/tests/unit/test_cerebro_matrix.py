"""The Cerebro role matrix's KGE checks (cerebro.md §2.1, §8.1-8.4) for rows 1 to 8: each of the four Cerebro
roles on each row, allowed or refused, on the routes and the MCP tools; curation, review, building, release and rollback on
every steward and onboarding route and onboarding tool, a blank cell refused before today's check or any work; separation
of duties, engineers releasing and stewards signing off, in every client's pack; the role-audit entry for each
check, allowed or refused, holding no question text, read by an admin; a non-engineer's Ask answered compact; and the
check's roles the same as /whoami's cerebro_roles."""
from pathlib import Path
from types import SimpleNamespace

import json
from datetime import datetime, timedelta, timezone

import anyio
import pytest
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from kge.pipeline import access
from kge.adapters.roleaudit import role_audit_log
from kge.adapters.roleaudit.folder import FolderRoleAuditLog
from kge.adapters.roleaudit import objstore as roleaudit_store
from kge.adapters.roleaudit.objstore import StoreRoleAuditLog
from kge.adapters.objstore import open_store
from kge.pipeline.access import CEREBRO_MATRIX, cerebro_roles
from kge.ports import RoleAuditEntry
from kge.services import http
from kge.services.access import routes as access_routes, service as access_service
from kge.services.assist import mcp as assist_mcp, routes as assist_routes
from kge.services.ask import routes as ask_routes, service as ask_service
from kge.services.ask.engine.graphview import Graph
from kge.services.ask.models import Ask
from kge.services.graph import routes as graph_routes
from kge.services.identity import service as identity_service
from kge.services.onboard import jobs, mcp as onboard_mcp, routes as onboard_routes, service as onboard_service
from kge.services.search import mcp as search_mcp, routes as search_routes
from kge.services.steward import routes as steward_routes, service as steward_service

PERSONAS = {"business": "analyst_retail", "steward": "steward", "engineer": "knowledge_engineer", "admin": "admin"}
GRID = {
    "ask": {"business", "steward", "engineer"},
    "graph": {"business", "steward", "engineer"},
    "feedback": {"business", "steward", "engineer"},
    "curate": {"steward"},
    "review": {"steward"},
    "build": {"engineer"},
    "release": {"engineer"},
    "retrieval": {"engineer"},
}
QUESTION_TEXT = "What is the secret question text"


class People:
    """The access table's reader as the check reads it: who it names an access administrator."""

    def __init__(self, admins=()):
        self.admins = set(admins)

    def is_admin(self, who):
        return who in self.admins


class Rights:
    def __init__(self):
        self.people = People()

    def acting_roles(self, who):
        return {who}

    def role_templates(self):
        return pack_roles("meridian")

    def may(self, who, process):
        return who in PERSONAS.values()


@pytest.fixture
def served(monkeypatch, tmp_path):
    monkeypatch.delenv("STORE_URL", raising=False)
    profile = SimpleNamespace(run_dir=lambda: tmp_path, client="meridian")
    engine = SimpleNamespace(g=Rights())
    fake = SimpleNamespace(engine=lambda: engine, PROFILE=profile)
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(graph_routes, "served", fake)
    monkeypatch.setattr(access_routes, "served", fake)
    return fake


def principal_from_header(request: Request) -> str:
    return request.headers["who"]


@pytest.fixture
def client(served):
    app = FastAPI()
    app.include_router(ask_routes.router)
    app.include_router(graph_routes.router)
    app.include_router(access_routes.router)
    app.include_router(search_routes.router)
    app.include_router(assist_routes.router)
    for row in GRID:
        app.get(f"/probe/{row}")(probe(row))
    app.dependency_overrides[http.principal] = principal_from_header
    app.dependency_overrides[http.admin] = admin_from_header
    with TestClient(app) as c:
        yield c


def admin_from_header(request: Request) -> str:
    if request.headers["who"] != "admin":
        raise HTTPException(403, "not an admin")
    return "admin"


def probe(row):
    def endpoint(who: str = Depends(http.matrix(row))) -> dict:
        return {"who": who}
    return endpoint


def audit(served):
    return role_audit_log(served.PROFILE.run_dir(), served.PROFILE.client).newest(1000)


@pytest.mark.parametrize("row", sorted(GRID))
@pytest.mark.parametrize("role", sorted(PERSONAS))
def test_each_role_on_each_row_is_allowed_or_refused_as_the_matrix_states(client, served, role, row):
    who = PERSONAS[role]
    r = client.get(f"/probe/{row}", headers={"who": who})
    if role in GRID[row]:
        assert r.status_code == 200, r.text
        assert r.json() == {"who": who}
    else:
        assert r.status_code == 403, r.text
        assert CEREBRO_MATRIX[row][0] in r.json()["detail"]
    [entry] = audit(served)
    assert entry == {"time": entry["time"], "who": who, "cerebro_roles": [role], "row": row, "endpoint": f"GET /probe/{row}",
                     "outcome": "allowed" if role in GRID[row] else "refused"}


@pytest.mark.parametrize("row,tool", [("ask", "ask"), ("ask", "get_context"), ("ask", "list_runtimes"), ("graph", "read_graph"),
                                      ("graph", "explore"), ("feedback", "feedback"), ("retrieval", "retrieve"),
                                      ("retrieval", "map_term")])
@pytest.mark.parametrize("role", sorted(PERSONAS))
def test_each_mcp_tool_checks_its_row_and_audits_the_tool(served, role, row, tool):
    who = PERSONAS[role]
    check = http.tool_matrix(row, tool)
    if role in GRID[row]:
        assert check(who) == who
    else:
        with pytest.raises(HTTPException) as refused:
            check(who)
        assert refused.value.status_code == 403
    assert [(e["who"], e["row"], e["endpoint"]) for e in audit(served)] == [(who, row, f"mcp {tool}")]


TOOL_ROWS = {"get_context": "ask", "ask": "ask", "list_runtimes": "ask", "feedback": "feedback", "read_graph": "graph",
             "explore": "graph", "explore_question": "graph", "retrieve": "retrieval", "map_term": "retrieval"}


def test_the_mcp_servers_check_each_tool_on_its_row(served):
    from kge.services.ask import mcp as ask_mcp
    from kge.services.graph import mcp as graph_mcp
    servers = (ask_mcp.mcp_server(), ask_mcp.answer_mcp_server(), graph_mcp.mcp_server(lambda ctx, call, check: None))
    checks = {name: check for server in servers for name, check in server._tool_manager.checks.items()}
    assert set(checks) == set(TOOL_ROWS)
    for name, check in checks.items():
        for role, who in PERSONAS.items():
            try:
                allowed = check(who) == who
            except HTTPException as exc:
                assert exc.status_code == 403
                allowed = False
            assert allowed is (role in GRID[TOOL_ROWS[name]]), (name, role)
    assert {(e["row"], e["endpoint"]) for e in audit(served)} == {(row, f"mcp {name}") for name, row in TOOL_ROWS.items()}


@pytest.mark.parametrize("method,path,route,body,refused", [
    ("post", "/ask", "/ask", {"question": QUESTION_TEXT}, ("admin",)),
    ("post", "/answer", "/answer", {"question": QUESTION_TEXT}, ("admin",)),
    ("post", "/agent", "/agent", {"question": QUESTION_TEXT}, ("admin",)),
    ("post", "/search", "/search", {"query": QUESTION_TEXT}, ("admin",)),
    ("post", "/assist", "/assist", {"question": QUESTION_TEXT}, ("admin",)),
    ("post", "/context", "/context", {"question": QUESTION_TEXT}, ("admin",)),
    ("get", "/runtimes", "/runtimes", None, ("admin",)),
    ("get", "/ask/runs/run-1", "/ask/runs/{run_id}", None, ("admin",)),
    ("post", "/ask/feedback", "/ask/feedback", {"receipt": "r", "verdict": "confirmed"}, ("admin",)),
    ("get", "/graph", "/graph", None, ("admin",)),
    ("get", "/graph/explore?node=n", "/graph/explore", None, ("admin",)),
    ("post", "/graph/explore", "/graph/explore", {"question": QUESTION_TEXT}, ("admin",)),
    ("post", "/retrieve", "/retrieve", {"query": QUESTION_TEXT}, ("business", "steward", "admin")),
    ("get", "/map/secret-term", "/map/{term:path}", None, ("business", "steward", "admin")),
])
def test_the_routes_refuse_the_blank_cells_before_any_work_and_audit_no_question(client, served, method, path, route, body,
                                                                                 refused):
    for role in refused:
        r = getattr(client, method)(path, headers={"who": PERSONAS[role]}, **({"json": body} if body else {}))
        assert r.status_code == 403, (role, r.text)
    entries = audit(served)
    assert [e["outcome"] for e in entries] == ["refused"] * len(refused)
    assert {e["endpoint"] for e in entries} == {f"{method.upper()} {route}"}
    logged = (served.PROFILE.run_dir() / "role-audit.jsonl").read_text()
    assert QUESTION_TEXT not in logged and "secret-term" not in logged


@pytest.mark.parametrize("path", ["/answer", "/agent"])
def test_a_streamed_answer_or_agent_refuses_the_blank_cell_before_the_stream_and_audits_it(client, served, path):
    r = client.post(path, json={"question": QUESTION_TEXT}, headers={"who": "admin", "accept": "application/x-ndjson"})
    assert r.status_code == 403, r.text
    assert CEREBRO_MATRIX["ask"][0] in r.json()["detail"]
    [entry] = audit(served)
    assert entry == {"time": entry["time"], "who": "admin", "cerebro_roles": ["admin"], "row": "ask",
                     "endpoint": f"POST {path}", "outcome": "refused"}


@pytest.mark.parametrize("module,tool", [(search_mcp, "search"), (assist_mcp, "assist")])
@pytest.mark.parametrize("role", sorted(PERSONAS))
def test_the_search_and_assist_tools_check_row_one_and_audit_the_tool_once(served, module, tool, role):
    checks = module.mcp_server(lambda ctx, call, check: None)._tool_manager.checks
    assert set(checks) == {tool}
    who = PERSONAS[role]
    try:
        allowed = checks[tool](who) == who
    except HTTPException as exc:
        assert exc.status_code == 403
        assert CEREBRO_MATRIX["ask"][0] in exc.detail
        allowed = False
    assert allowed is (role in GRID["ask"])
    [entry] = audit(served)
    assert (entry["who"], entry["row"], entry["endpoint"], entry["outcome"]) == (
        who, "ask", f"mcp {tool}", "allowed" if allowed else "refused")


FULL_ANSWER = {"question": "q", "principal": "p", "purpose": "analysis", "arm": "rules", "behaviour": "figure",
               "answer_text": "Revenue was 10.", "phrased_by": "template", "trace": ["planned"], "versions": {"planner": "v"},
               "run_trace": {"stages": []}, "sql": "SELECT 10", "plan": {"steps": ["a"]}, "figure": 10,
               "graph_path": {"nodes": [], "edges": []}}
INTERNALS = ("trace", "run_trace", "sql", "plan")


@pytest.fixture
def answering(served, monkeypatch):
    """/answer's work on the stand-in app: the rules preset's config and a full answer, plan, SQL and trace in it."""
    asked = []
    monkeypatch.setattr(ask_service, "run_config", lambda a, path, combined: SimpleNamespace(graph=False))

    def answer(eng, prof, question, who, *args, **kwargs):
        asked.append(who)
        return {**FULL_ANSWER, "principal": who}

    monkeypatch.setattr(ask_service, "answer", answer)
    monkeypatch.setattr(ask_routes, "served", served)
    served.combined = lambda: SimpleNamespace(llm=None)
    return asked


@pytest.mark.parametrize("role", ["business", "steward"])
def test_a_business_or_steward_callers_answer_is_compact_without_plan_sql_or_trace(client, answering, role):
    r = client.post("/answer", json={"question": "q"}, headers={"who": PERSONAS[role]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not set(INTERNALS) & set(body), body
    assert (body["answer_text"], body["figure"], body["graph_path"]) == ("Revenue was 10.", 10, {"nodes": [], "edges": []})
    assert answering == [PERSONAS[role]]


def test_an_engineers_answer_keeps_plan_sql_and_trace(client, answering):
    body = client.post("/answer", json={"question": "q"}, headers={"who": PERSONAS["engineer"]}).json()
    assert {key: body[key] for key in INTERNALS} == {key: FULL_ANSWER[key] for key in INTERNALS}
    compact = client.post("/answer", json={"question": "q", "compact": True}, headers={"who": PERSONAS["engineer"]}).json()
    assert not set(INTERNALS) & set(compact)


@pytest.fixture
def agenting(served, monkeypatch):
    """/agent's work on the stand-in app: the reference agent's full answer, plan, SQL, trace and path in it, through
    the real service.agent and its compact view."""
    asked = []

    def ask(question, who, *args, **kwargs):
        asked.append(who)
        return {**FULL_ANSWER, "principal": who, "arm": "treatment", "agent": {"mode": "rules"}}

    monkeypatch.setattr(ask_service.runtrace, "run", lambda path, fn, **kw: fn())
    monkeypatch.setattr(ask_service, "log_unmatched", lambda *a, **k: None)
    monkeypatch.setattr(ask_service, "build_path", lambda e, out: {"nodes": ["plan"], "edges": []})
    monkeypatch.setattr(ask_service, "build_graph_path", lambda e, out: {"nodes": [], "edges": []})
    monkeypatch.setattr(ask_routes, "served", served)
    monkeypatch.setattr(ask_routes, "person_tools", lambda request, loop: None)
    served.agent = lambda: SimpleNamespace(ask=ask, g=None, e=None)
    return asked


AGENT_INTERNALS = (*INTERNALS, "path")


@pytest.mark.parametrize("role", ["business", "steward"])
def test_a_business_or_steward_callers_agent_reply_is_compact_as_json_and_streamed(client, agenting, role):
    r = client.post("/agent", json={"question": "q"}, headers={"who": PERSONAS[role]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not set(AGENT_INTERNALS) & set(body), body
    assert (body["answer_text"], body["figure"], body["agent"]) == ("Revenue was 10.", 10, {"mode": "rules"})
    r = client.post("/agent", json={"question": "q"}, headers={"who": PERSONAS[role], "accept": "application/x-ndjson"})
    assert r.status_code == 200, r.text
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    [final] = [event["answer"] for event in events if event.get("event") == "answer"]
    assert not set(AGENT_INTERNALS) & set(final), final
    assert final["answer_text"] == "Revenue was 10."
    assert not [event for event in events if event.get("event") in ("plan", "stage")]
    assert agenting == [PERSONAS[role]] * 2


def test_an_engineers_agent_reply_keeps_plan_sql_and_trace_as_json_and_streamed(client, agenting):
    body = client.post("/agent", json={"question": "q"}, headers={"who": PERSONAS["engineer"]}).json()
    assert {key: body[key] for key in INTERNALS} == {key: FULL_ANSWER[key] for key in INTERNALS}
    assert body["path"] == {"nodes": ["plan"], "edges": []}
    r = client.post("/agent", json={"question": "q"}, headers={"who": PERSONAS["engineer"], "accept": "application/x-ndjson"})
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    [final] = [event["answer"] for event in events if event.get("event") == "answer"]
    assert {key: final[key] for key in INTERNALS} == {key: FULL_ANSWER[key] for key in INTERNALS}


def test_a_business_callers_streamed_answer_is_compact_too(client, answering):
    r = client.post("/answer", json={"question": "q"}, headers={"who": PERSONAS["business"], "accept": "application/x-ndjson"})
    assert r.status_code == 200, r.text
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    [final] = [event["answer"] for event in events if event.get("event") == "answer"]
    assert not set(INTERNALS) & set(final), final
    assert final["answer_text"] == "Revenue was 10."
    assert not [event for event in events if event.get("event") in ("plan", "stage")]


def test_an_admin_reads_the_role_audit_and_no_one_else_does(client, served):
    client.post("/ask", json={"question": QUESTION_TEXT}, headers={"who": "admin"})
    client.get("/probe/ask", headers={"who": "analyst_retail"})
    r = client.get("/access/role-audit", headers={"who": "admin"})
    assert r.status_code == 200, r.text
    assert [(e["who"], e["outcome"]) for e in r.json()["entries"]] == [("analyst_retail", "allowed"), ("admin", "refused")]
    assert QUESTION_TEXT not in r.text
    assert client.get("/access/role-audit", headers={"who": "analyst_retail"}).status_code == 403
    assert access_service.role_audit(served.PROFILE, 1).entries[0].who == "analyst_retail"


@pytest.mark.parametrize("role,compact", [("business", True), ("steward", True), ("engineer", False), ("admin", True)])
def test_only_an_engineer_is_answered_with_plan_sql_and_trace(served, role, compact):
    asked = ask_routes.as_compact_unless_engineer(Ask(question="q"), PERSONAS[role])
    assert asked.compact is compact
    assert ask_routes.as_compact_unless_engineer(Ask(question="q", compact=True), PERSONAS["engineer"]).compact is True


ASSIST_INTERNALS = ("plan", "sql", "reference_sql", "trace", "run_trace")
FULL_ASSIST = {"question": "q", "principal": "p", "purpose": "analysis", "behaviour": "figure", "answer_text": "Revenue was 10.",
               "phrased_by": "template", "figure": 10, "source": "ask", "assistant_available": False, "notice": "",
               "sources": [{"kind": "ask", "id": "retail.revenue", "title": "Revenue", "unit": "retail", "why": "Governed answer source"}],
               "plan": {"steps": ["a"]}, "sql": "SELECT 10", "reference_sql": "SELECT 10", "trace": ["planned"],
               "run_trace": {"stages": []}}


@pytest.fixture
def assisting(served, monkeypatch):
    """/assist's work on the stand-in app: a reply routed to Ask, plan, SQL, reference SQL and trace in it."""
    asked = []

    def assist(c, profile, question, who, *args, **kwargs):
        asked.append(who)
        return {**FULL_ASSIST, "principal": who}

    config = SimpleNamespace(with_stages=lambda **stages: config, problems=lambda path, model: [])
    monkeypatch.setattr(assist_routes, "served", SimpleNamespace(combined=lambda: SimpleNamespace(default_config=lambda arm: config),
                                                                 PROFILE=served.PROFILE))
    monkeypatch.setattr(assist_routes.service, "assist", assist)
    served.SIGN_IN = "sign in"
    served.ids = lambda: SimpleNamespace(resolve=lambda token, cookie: token.removeprefix("Bearer ") if token else None)
    return asked


def assist_over_mcp(who):
    """The assist tool called as an MCP client calls it: the bearer's person, the tool's check, the tool runner and the
    tool's output model."""
    from kge.services.ask import mcp as ask_mcp
    server = assist_mcp.mcp_server(ask_mcp.tool)
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={"authorization": f"Bearer {who}"})))
    result = anyio.run(lambda: server._tool_manager.call_tool("assist", {"question": "q"}, ctx, convert_result=True))
    assert not result.isError
    return result.structuredContent


@pytest.mark.parametrize("role", ["business", "steward"])
def test_a_business_or_steward_callers_assist_reply_routed_to_ask_is_compact_over_http_and_mcp(client, assisting, role):
    r = client.post("/assist", json={"question": "q"}, headers={"who": PERSONAS[role]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not set(ASSIST_INTERNALS) & set(body), body
    assert (body["answer_text"], body["figure"], body["source"]) == ("Revenue was 10.", 10, "ask")
    assert [s["id"] for s in body["sources"]] == ["retail.revenue"]
    tool = assist_over_mcp(PERSONAS[role])
    assert not set(ASSIST_INTERNALS) & set(tool), tool
    assert (tool["answer_text"], tool["figure"], tool["source"]) == ("Revenue was 10.", 10, "ask")
    assert assisting == [PERSONAS[role]] * 2


def test_an_engineers_assist_reply_keeps_plan_sql_and_trace_over_http_and_mcp(client, assisting):
    body = client.post("/assist", json={"question": "q"}, headers={"who": PERSONAS["engineer"]}).json()
    assert {key: body[key] for key in ASSIST_INTERNALS} == {key: FULL_ASSIST[key] for key in ASSIST_INTERNALS}
    tool = assist_over_mcp(PERSONAS["engineer"])
    assert {key: tool[key] for key in ASSIST_INTERNALS} == {key: FULL_ASSIST[key] for key in ASSIST_INTERNALS}


class ListingStore:
    """A store that records the prefix of every listing it is asked for."""

    def __init__(self, store):
        self.store, self.listed = store, []

    def list(self, prefix=""):
        self.listed.append(prefix)
        return self.store.list(prefix)

    def get(self, key):
        return self.store.get(key)

    def put_version(self, key, data, version):
        return self.store.put_version(key, data, version)


def entry_of(who):
    return RoleAuditEntry(time="t", who=who, cerebro_roles=("business",), row="ask", endpoint="POST /ask", outcome="allowed")


def test_the_store_logs_newest_read_lists_only_the_days_it_needs(tmp_path):
    store = ListingStore(open_store(str(tmp_path), private=True))
    now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    clock = [now - timedelta(days=400)]
    log = StoreRoleAuditLog(store, "meridian", clock=lambda: clock[0])
    log.append(entry_of("long-ago"))
    for n, who in enumerate(("yesterday-1", "yesterday-2")):
        clock[0] = now - timedelta(days=1, seconds=-n)
        log.append(entry_of(who))
    for n, who in enumerate(("today-1", "today-2", "today-3")):
        clock[0] = now + timedelta(seconds=n)
        log.append(entry_of(who))
    clock[0] = now
    store.listed.clear()
    assert [e["who"] for e in log.newest(3)] == ["today-3", "today-2", "today-1"]
    assert store.listed == ["access/meridian/role-audit/20261117/"]
    store.listed.clear()
    assert [e["who"] for e in log.newest(4)] == ["today-3", "today-2", "today-1", "yesterday-2"]
    assert store.listed == ["access/meridian/role-audit/20261117/", "access/meridian/role-audit/20261116/"]
    store.listed.clear()
    assert "long-ago" not in [e["who"] for e in log.newest(1000)]
    assert store.listed == [f"access/meridian/role-audit/{now - timedelta(days=back):%Y%m%d}/"
                            for back in range(roleaudit_store.DAYS_READ)]
    days = [f"{now - timedelta(days=back):%Y%m%d}" for back in (400, 1, 1, 0, 0, 0)]
    assert [key.split("/")[3] for key in store.store.list("access/meridian/role-audit/")] == days


def test_the_folder_logs_newest_read_takes_the_files_end_alone(tmp_path, monkeypatch):
    monkeypatch.setattr("kge.adapters.roleaudit.folder.BLOCK", 64)
    log = FolderRoleAuditLog(tmp_path / "role-audit.jsonl")
    for n in range(50):
        log.append(entry_of(f"p{n}"))
    reads = []
    opened = Path.open

    def counted(self, *args, **kwargs):
        handle = opened(self, *args, **kwargs)
        read = handle.read

        def recorded(size=-1):
            data = read(size)
            reads.append(len(data))
            return data
        handle.read = recorded
        return handle

    monkeypatch.setattr(Path, "open", counted)
    assert [e["who"] for e in log.newest(3)] == ["p49", "p48", "p47"]
    assert sum(reads) < (tmp_path / "role-audit.jsonl").stat().st_size / 4
    assert [e["who"] for e in log.newest(1000)] == [f"p{n}" for n in reversed(range(50))]
    assert log.newest(0) == []


def test_the_store_log_keeps_each_entry_as_its_own_object_beside_the_access_audit(tmp_path):
    store = open_store(str(tmp_path), private=True)
    log = StoreRoleAuditLog(store, "meridian")
    for who in ("a", "b"):
        log.append(RoleAuditEntry(time="t", who=who, cerebro_roles=("business",), row="ask", endpoint="POST /ask",
                                  outcome="allowed"))
    keys = store.list("access/meridian/role-audit/")
    assert len(keys) == 2
    assert [e["who"] for e in log.newest(10)] == ["b", "a"]
    assert [e["who"] for e in FolderRoleAuditLog(tmp_path / "none.jsonl").newest(5)] == []
    with pytest.raises(ValueError):
        log.append(RoleAuditEntry(time="t", who="a", cerebro_roles=(), row="ask", endpoint="POST /ask", outcome="maybe"))


@pytest.mark.parametrize("role", ["analyst_retail", "analyst_enterprise", "advisor_wealth", "finance_reporting", "steward",
                                  "steward_glossary", "business_team", "knowledge_engineer", "admin", "pipeline_tagger"])
def test_the_checks_roles_are_whoamis_cerebro_roles(served, role):
    people = SimpleNamespace(access=SimpleNamespace(name_of=lambda _: "P", roles_of=lambda _: {role}, is_admin=lambda _: False))
    stated = identity_service.whoami(SimpleNamespace(g=SimpleNamespace(principal=lambda who: {"id": who},
                                                                        role_templates=lambda: pack_roles("meridian"))),
                                     role, people)
    assert http.cerebro_held(role) == stated["cerebro_roles"] == cerebro_roles([role], pack_roles("meridian"))


class Grants:
    """The access table's reader as /whoami and the check both read it: one person's grants, the reserved access_admin
    among them where the table names them an access administrator, as Access.roles_of gives it."""

    def __init__(self, roles, admin):
        self.roles, self.admin = set(roles) | ({access.ACCESS_ADMIN} if admin else set()), admin

    def roles_of(self, who):
        return set(self.roles)

    def is_admin(self, who):
        return self.admin

    def name_of(self, who):
        return who

    def people(self):
        return {"ada"}


@pytest.mark.parametrize("roles,held", [((), ["admin"]), (("steward",), ["steward", "admin"]),
                                        (("knowledge_engineer",), ["engineer", "admin"])])
def test_an_access_administrators_audit_roles_are_whoamis(client, served, monkeypatch, roles, held):
    grants = Grants(roles, admin=True)
    graph = SimpleNamespace(_roles_only=False, people=grants, role_ids=lambda: set(pack_roles("meridian")),
                            role_templates=lambda: pack_roles("meridian"), principal=lambda who: {"id": who},
                            may=lambda who, process: True)
    graph.acting_roles = lambda who: Graph.acting_roles(graph, who)
    monkeypatch.setattr(served, "engine", lambda: SimpleNamespace(g=graph))
    stated = identity_service.whoami(SimpleNamespace(g=graph), "ada", SimpleNamespace(access=grants))
    assert stated["admin"] is True
    assert http.cerebro_held("ada") == stated["cerebro_roles"] == held
    client.get("/probe/ask", headers={"who": "ada"})
    [entry] = audit(served)
    assert entry["cerebro_roles"] == held


SOURCE = {"unit": "leasing", "label": "Leasing", "ddl": "CREATE TABLE t (id INTEGER)"}
GOVERNED = [
    ("curate", "get", "/steward/queue", "GET /steward/queue", None),
    ("curate", "get", "/steward/gaps", "GET /steward/gaps", None),
    ("curate", "post", "/steward/decide", "POST /steward/decide", {"tag": "t", "decision": "accept"}),
    ("review", "post", "/onboard/accept", "POST /onboard/accept", {"unit": "leasing"}),
    ("review", "post", "/onboard/certify", "POST /onboard/certify", {"unit": "leasing"}),
    ("review", "post", "/onboard/run", "POST /onboard/run sign-off", {"step": "sign-off"}),
    ("build", "post", "/onboard/tables", "POST /onboard/tables", SOURCE),
    ("build", "post", "/onboard/source", "POST /onboard/source", SOURCE),
    ("build", "post", "/onboard/rows", "POST /onboard/rows", {"unit": "leasing", "rows": "invented"}),
    ("build", "post", "/onboard/draft", "POST /onboard/draft", {"unit": "leasing"}),
    ("build", "post", "/onboard/run", "POST /onboard/run checks", {"step": "checks"}),
    ("build", "post", "/onboard/run", "POST /onboard/run improve", {"step": "improve"}),
    ("build", "get", "/onboard/margin?hash=" + "a" * 64, "GET /onboard/margin", None),
    ("release", "post", "/onboard/run", "POST /onboard/run release", {"step": "release"}),
    ("release", "post", "/onboard/run", "POST /onboard/run rollback", {"step": "rollback"}),
    ("release", "post", "/onboard/rollback", "POST /onboard/rollback", None),
]
ONBOARD_TOOLS = ("onboard_tables", "onboard_source", "onboard_rows", "onboard_draft", "onboard_checks", "onboard_improve",
                 "onboard_margin")


@pytest.fixture
def governed(served, monkeypatch):
    """The steward and onboarding routes on the stand-in app: today's checks pass for every persona and record each call,
    and every service raises 418, so a 418 is a call the checks let through to its work."""
    today = []

    def reached(*args, **kwargs):
        raise HTTPException(418, "reached the service")

    def permissions(prof, who, writable_=True, reader=None):
        today.append(who)
        return {"accept": True, "certify": True, "release": True, "participant": True}

    for name in ("tables", "source", "add_rows", "draft", "accept", "certify", "run", "margin"):
        monkeypatch.setattr(onboard_service, name, reached)
    for name in ("queue", "decided", "decide", "gaps"):
        monkeypatch.setattr(steward_service, name, reached)
    monkeypatch.setattr(onboard_routes.core, "permissions", permissions)
    monkeypatch.setattr(onboard_routes.core, "require_process", lambda prof, who, process, reader=None: today.append(who))
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "access_reader", lambda app: None)
    monkeypatch.setattr(onboard_routes, "_app", lambda: served)
    monkeypatch.setattr(steward_routes, "served", served)
    served.principal = principal_from_header
    app = FastAPI()
    app.include_router(onboard_routes.router)
    app.include_router(steward_routes.router)
    app.dependency_overrides[http.principal] = principal_from_header
    with TestClient(app) as c:
        yield c, today


@pytest.mark.parametrize("row,method,path,endpoint,body", GOVERNED, ids=[g[3] for g in GOVERNED])
@pytest.mark.parametrize("role", sorted(PERSONAS))
def test_each_role_on_each_steward_and_onboarding_route_is_refused_or_passed_through(governed, served, role, row, method,
                                                                                    path, endpoint, body):
    c, today = governed
    who = PERSONAS[role]
    r = getattr(c, method)(path, headers={"who": who}, **({"json": body} if body else {}))
    if role in GRID[row]:
        assert r.status_code == 418, r.text
    else:
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == f"{who} may not do this: {CEREBRO_MATRIX[row][0]} is for {', '.join(CEREBRO_MATRIX[row][1])}"
        assert today == []
    [entry] = audit(served)
    assert entry == {"time": entry["time"], "who": who, "cerebro_roles": [role], "row": row, "endpoint": endpoint,
                     "outcome": "allowed" if role in GRID[row] else "refused"}


def test_an_admins_sign_off_is_the_matrixs_403_not_the_services_409(governed, served, monkeypatch):
    c, _ = governed
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": "b" * 64})
    monkeypatch.setattr(jobs, "candidate_permissions", lambda *args: {"sign_off": True, "release": True, "participant": True})

    def refused(*args):
        raise HTTPException(409, "the candidate has not passed its checks")

    monkeypatch.setattr(onboard_service, "run", refused)
    r = c.post("/onboard/run", json={"step": "sign-off"}, headers={"who": "admin"})
    assert r.status_code == 403, r.text
    assert CEREBRO_MATRIX["review"][0] in r.json()["detail"]
    assert c.post("/onboard/run", json={"step": "sign-off"}, headers={"who": "steward"}).status_code == 409
    assert [(e["who"], e["outcome"]) for e in audit(served)] == [("steward", "allowed"), ("admin", "refused")]


def test_today_still_refuses_what_it_refused_and_leaves_a_refused_entry_not_an_allowed_one(governed, served, monkeypatch):
    c, _ = governed

    def no_certification(prof, who, writable_=True, reader=None):
        return {"accept": False, "certify": False, "participant": True}

    monkeypatch.setattr(onboard_routes.core, "permissions", no_certification)
    r = c.post("/onboard/certify", json={"unit": "leasing"}, headers={"who": "steward"})
    assert r.status_code == 403
    assert r.json()["detail"] == "steward is not a steward whose role performs certification"
    [entry] = audit(served)
    assert entry == {"time": entry["time"], "who": "steward", "cerebro_roles": ["steward"], "row": "review",
                     "endpoint": "POST /onboard/certify", "outcome": "refused"}


def test_a_failure_of_todays_check_that_is_not_a_refusal_leaves_no_entry(governed, served, monkeypatch):
    c, _ = governed

    def unavailable(prof, who, writable_=True, reader=None):
        raise HTTPException(503, "the access table is unavailable")

    monkeypatch.setattr(onboard_routes.core, "permissions", unavailable)
    r = c.post("/onboard/certify", json={"unit": "leasing"}, headers={"who": "steward"})
    assert r.status_code == 503
    assert audit(served) == []


def test_the_onboard_mcp_tools_check_the_build_row_and_audit_the_tool(governed, served):
    checks = onboard_mcp.mcp_server(lambda *args: None)._tool_manager.checks
    assert set(checks) == {*ONBOARD_TOOLS, "onboard_status"}
    for name in ONBOARD_TOOLS:
        for role, who in PERSONAS.items():
            try:
                allowed = checks[name](who) == who
            except HTTPException as exc:
                assert exc.status_code == 403
                assert CEREBRO_MATRIX["build"][0] in exc.detail
                allowed = False
            assert allowed is (role == "engineer"), (name, role)
    assert sorted((e["who"], e["endpoint"], e["outcome"]) for e in audit(served)) == sorted(
        (who, f"mcp {name}", "allowed" if role == "engineer" else "refused")
        for name in ONBOARD_TOOLS for role, who in PERSONAS.items())


def pack_roles(client):
    pack = yaml.safe_load((Path(__file__).resolve().parents[2] / "clients" / client / "pack.yaml").read_text())
    return {role["id"]: role for role in pack["access"]["roles"]}


@pytest.mark.parametrize("client", ["meridian", "thornbury", "fellmoor"])
def test_separation_of_duties_engineers_release_and_stewards_sign_off(client):
    for role_id, role in pack_roles(client).items():
        allowed = access.permissions(role)
        assert allowed["release"] is ("building" in role.get("processes", [])), role_id
        assert allowed["sign_off"] is ("certification" in role.get("processes", [])), role_id
        assert not (allowed["release"] and allowed["sign_off"]), role_id
        held = cerebro_roles([role_id], pack_roles(client))
        if allowed["release"]:
            assert held == ["engineer"], role_id
        if allowed["sign_off"] and role_id != "admin":
            assert held == ["steward"], role_id


@pytest.mark.parametrize("client,role,held", [
    ("thornbury", "claims_steward", ["steward"]), ("fellmoor", "fares_steward", ["steward"]),
    ("meridian", "steward_glossary", ["steward"]), ("meridian", "knowledge_engineer", ["engineer"]),
    ("thornbury", "knowledge_engineer", ["engineer"]), ("fellmoor", "knowledge_engineer", ["engineer"])])
def test_every_clients_steward_and_engineer_roles_map_to_their_cerebro_role(client, role, held):
    assert cerebro_roles([role], pack_roles(client)) == held


@pytest.mark.parametrize("role,step,allowed", [
    ("engineer", "release", True), ("engineer", "rollback", True), ("steward", "release", False),
    ("steward", "rollback", False), ("admin", "release", False), ("admin", "rollback", False),
    ("business", "release", False), ("business", "rollback", False)])
def test_a_candidates_release_and_rollback_are_the_engineers(governed, served, monkeypatch, role, step, allowed):
    c, _ = governed
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": "b" * 64})
    monkeypatch.setattr(jobs, "candidate_permissions", lambda *args: {"sign_off": True, "release": True, "participant": True})
    r = c.post("/onboard/run", json={"step": step}, headers={"who": PERSONAS[role]})
    assert r.status_code == (418 if allowed else 403), r.text
    if not allowed:
        assert CEREBRO_MATRIX["release"][0] in r.json()["detail"]
    [entry] = audit(served)
    assert (entry["row"], entry["endpoint"], entry["outcome"]) == ("release", f"POST /onboard/run {step}",
                                                                 "allowed" if allowed else "refused")


def test_an_engineer_the_candidates_roles_do_not_let_release_is_refused_in_their_words(governed, served, monkeypatch):
    c, _ = governed
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": "b" * 64})
    monkeypatch.setattr(jobs, "candidate_permissions", lambda *args: {"sign_off": False, "release": False, "participant": True})
    r = c.post("/onboard/run", json={"step": "release"}, headers={"who": "knowledge_engineer"})
    assert r.status_code == 403
    assert r.json()["detail"] == f"knowledge_engineer may not release {'b' * 12}: its roles do not let them build and release"
    [entry] = audit(served)
    assert entry == {"time": entry["time"], "who": "knowledge_engineer", "cerebro_roles": ["engineer"], "row": "release",
                     "endpoint": "POST /onboard/run release", "outcome": "refused"}


def test_today_refuses_a_rollback_to_a_role_that_does_not_build(governed, served, monkeypatch):
    c, _ = governed
    monkeypatch.setattr(onboard_routes.core, "permissions", lambda *args, **kwargs: {"release": False, "participant": True})
    r = c.post("/onboard/rollback", headers={"who": "knowledge_engineer"})
    assert r.status_code == 403
    assert r.json()["detail"] == "knowledge_engineer is not a knowledge engineer whose role builds and releases"
    [entry] = audit(served)
    assert entry == {"time": entry["time"], "who": "knowledge_engineer", "cerebro_roles": ["engineer"], "row": "release",
                     "endpoint": "POST /onboard/rollback", "outcome": "refused"}


def test_the_business_teams_decide_is_refused_by_certification_and_audited_refused(governed, served, monkeypatch):
    c, _ = governed
    processes = {"steward": {"certification", "querying"}, "business_team": {"querying"}}
    monkeypatch.setattr(Rights, "may", lambda self, who, process: process in processes.get(who, ()))
    r = c.post("/steward/decide", json={"tag": "t", "decision": "accept"}, headers={"who": "business_team"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "business_team is not a certification identity"
    assert c.get("/steward/queue", headers={"who": "business_team"}).status_code == 418
    assert c.get("/steward/gaps", headers={"who": "business_team"}).status_code == 418
    assert c.post("/steward/decide", json={"tag": "t", "decision": "accept"}, headers={"who": "steward"}).status_code == 418
    assert [(e["who"], e["cerebro_roles"], e["row"], e["endpoint"], e["outcome"]) for e in reversed(audit(served))] == [
        ("business_team", ["steward"], "curate", "POST /steward/decide", "refused"),
        ("business_team", ["steward"], "curate", "GET /steward/queue", "allowed"),
        ("business_team", ["steward"], "curate", "GET /steward/gaps", "allowed"),
        ("steward", ["steward"], "curate", "POST /steward/decide", "allowed"),
    ]


def test_the_folder_logs_newest_read_keeps_a_whole_first_line_when_the_tail_starts_on_a_newline(tmp_path):
    from kge.adapters.roleaudit import folder
    path = tmp_path / "role-audit.jsonl"
    first = (json.dumps({"who": "p0", "pad": ""}) + "\n").encode()
    rest = [json.dumps({"who": f"p{n}", "pad": ""}) for n in range(1, 257)]
    spare = folder.BLOCK - 1 - sum(len(line) + 1 for line in rest)
    rest = [json.dumps({"who": f"p{n}", "pad": "x" * (spare // 256 + (n <= spare % 256))}) for n in range(1, 257)]
    path.write_bytes(first + "".join(line + "\n" for line in rest).encode())
    data = path.read_bytes()
    assert data[len(data) - folder.BLOCK:len(data) - folder.BLOCK + 1] == b"\n"
    log = FolderRoleAuditLog(path)
    assert [e["who"] for e in log.newest(256)] == [f"p{n}" for n in range(256, 0, -1)]
    assert [e["who"] for e in log.newest(257)] == [f"p{n}" for n in range(256, -1, -1)]
