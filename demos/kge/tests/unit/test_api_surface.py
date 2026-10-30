"""The HTTP/MCP capability contract, typed routes, documentation, and human-only steps."""
from __future__ import annotations

import anyio
import pytest
import re
from fastapi.routing import APIRoute
from pathlib import Path
from types import SimpleNamespace

from kge.services.ask.engine.wording import BEHAVIOURS
from kge.services.ask.mcp import answer_mcp_server, mcp_server, tool
from kge.services.ask.models import Ask, AskResponse, ContextResponse, RetrieveResponse, TermMap
from kge.services.ask import routes as ask_routes
from kge.services.ask import service as ask_service
from kge.services.onboard import routes
from kge.services.onboard import mcp as onboard_mcp
from kge.services.identity import mcp as identity_mcp
from kge.services.identity import routes as identity_routes
from kge.services.identity.models import ReadableUnits


CAPABILITIES = {
    "status": ("GET", "/onboard", "onboard_status", routes.StatusResponse, routes.participant),
    "tables": ("POST", "/onboard/tables", "onboard_tables", routes.TablesResponse, routes.drafting),
    "source": ("POST", "/onboard/source", "onboard_source", routes.SourceResponse, routes.drafting),
    "rows": ("POST", "/onboard/rows", "onboard_rows", routes.RowsResponse, routes.drafting),
    "draft": ("POST", "/onboard/draft", "onboard_draft", routes.DraftResponse, routes.drafting),
    "run:checks": ("POST", "/onboard/run", "onboard_checks", routes.JobResponse, routes.building),
    "run:improve": ("POST", "/onboard/run", "onboard_improve", routes.JobResponse, routes.improving),
    "margin": ("GET", "/onboard/margin", "onboard_margin", routes.MarginResponse, routes.building),
}
HUMAN = {
    "accept": ("POST", "/onboard/accept"),
    "certify": ("POST", "/onboard/certify"),
    "run:sign-off": ("POST", "/onboard/run"),
    "run:release": ("POST", "/onboard/run"),
}
DEMO = Path(__file__).resolve().parents[2]
NOT_JSON = "a page or a file, not a JSON body"
LATER = "typed by its own service's issue: the ask and onboard services' routes come first (#102)"
UNTYPED = {
    "GET /": NOT_JSON, "GET /ab/{run_id}/report.html": NOT_JSON, "GET /onboarding-guide": NOT_JSON,
    "GET /brand/logo": NOT_JSON, "GET /brand/logo.svg": NOT_JSON,
    "HEAD /health": "a health probe with no body", "HEAD /healthz": "a health probe with no body",
    **{f"{m} {p}": LATER for m, p in (
        ("POST", "/onboard/accept"), ("POST", "/onboard/certify"),
        ("GET", "/mode"), ("GET", "/presets"),
        ("GET", "/summary"), ("GET", "/profile"), ("GET", "/versions"), ("POST", "/sql/check"),
        ("POST", "/session"), ("DELETE", "/session"), ("POST", "/session/demo"), ("GET", "/whoami"),
        ("GET", "/principals"), ("GET", "/golden"), ("GET", "/ab"), ("POST", "/eval"),
        ("GET", "/steward/queue"), ("POST", "/steward/decide"))},
}
PUBLIC = {"POST /ask": "answer", "POST /context": "context", "POST /retrieve": "context", "GET /map/{term}": "context"}
INTERNAL = {"POST /answer", "POST /agent"}
ENGINE = [DEMO / "kge" / "services" / "ask" / "engine", DEMO / "kge" / "semantics"]
ASK_CAPABILITIES = {
    "context": {"get_context": ("POST", "/context", ContextResponse),
                "retrieve": ("POST", "/retrieve", RetrieveResponse),
                "map_term": ("GET", "/map/{term:path}", TermMap)},
    "answer": {"ask": ("POST", "/ask", AskResponse)},
}


def route(method: str, path: str) -> APIRoute:
    return next(r for r in routes.router.routes if isinstance(r, APIRoute) and r.path == path and method in r.methods)


def test_every_capability_has_exactly_its_allowed_transport_and_model():
    servers = {"context": mcp_server(), "answer": answer_mcp_server(), "onboard": onboard_mcp.mcp_server(tool),
               "identity": identity_mcp.mcp_server()}
    tools_by_server = {name: {item.name: item for item in anyio.run(server.list_tools)}
                       for name, server in servers.items()}
    assert set(tools_by_server["context"]) == {"get_context", "map_term", "retrieve"}
    assert set(tools_by_server["answer"]) == {"ask"}
    assert set(tools_by_server["identity"]) == {"readable_units"}
    readable_route = next(item for item in identity_routes.router.routes
                          if isinstance(item, APIRoute) and item.path == "/readable-units")
    assert readable_route.response_model is ReadableUnits
    expected_outputs = {
        **{server_name: {name: data[2] for name, data in capabilities.items()}
           for server_name, capabilities in ASK_CAPABILITIES.items()},
        "onboard": {cap[2]: cap[3] for cap in CAPABILITIES.values()},
        "identity": {"readable_units": ReadableUnits},
    }
    for capabilities in ASK_CAPABILITIES.values():
        for method, path, response in capabilities.values():
            http = next(item for item in ask_routes.router.routes
                        if isinstance(item, APIRoute) and item.path == path and method in item.methods)
            assert http.response_model is response
    for server_name, expected in expected_outputs.items():
        assert {name: tools_by_server[server_name][name].outputSchema for name in expected} == {
            name: model.model_json_schema() for name, model in expected.items()
        }
    assert all("principal" not in item.inputSchema.get("properties", {})
               for tools in tools_by_server.values() for item in tools.values())
    answer_tool = tools_by_server["answer"]["ask"]
    assert set(answer_tool.inputSchema.get("properties", {})) == set(Ask.model_fields)
    assert answer_tool.meta["role"] == "querying"
    tools = tools_by_server["onboard"]
    expected_tools = {cap[2] for cap in CAPABILITIES.values()}
    onboard_tools = {name for name in tools if name.startswith("onboard_")}
    assert onboard_tools == expected_tools
    assert not {"onboard_accept", "onboard_certify", "onboard_sign_off", "onboard_release"} & tools.keys()
    for method, path, tool_name, response, _check in CAPABILITIES.values():
        http = route(method, path)
        assert http.response_model is response
    inputs = {
        "onboard_status": set(),
        "onboard_tables": set(routes.SourceBody.model_fields),
        "onboard_source": set(routes.SourceBody.model_fields),
        "onboard_rows": set(routes.RowsBody.model_fields),
        "onboard_draft": set(routes.UnitBody.model_fields),
        "onboard_checks": set(routes.RunBody.model_fields) - {"step", "unit"},
        "onboard_improve": set(routes.RunBody.model_fields) - {"step"},
        "onboard_margin": {"hash"},
    }
    assert {name: set(tools[name].inputSchema.get("properties", {})) for name in expected_tools} == inputs
    routed = {(method, item.path) for item in routes.router.routes if isinstance(item, APIRoute)
              for method in item.methods if method in {"GET", "POST"}}
    contracted = {(m, p) for m, p, *_ in CAPABILITIES.values()} | set(HUMAN.values())
    assert routed == contracted
    assert {name: onboard_mcp.SERVICE_FUNCTIONS[data[2]] for name, data in CAPABILITIES.items()} == routes.SERVICE_FUNCTIONS


def test_capability_role_contract_names_the_same_check_for_each_transport():
    expected = {
        "status": routes.participant, "tables": routes.drafting, "source": routes.drafting,
        "rows": routes.drafting, "draft": routes.drafting, "run:checks": routes.building,
        "run:improve": routes.improving, "margin": routes.building,
    }
    assert {name: value[4] for name, value in CAPABILITIES.items()} == expected
    tools = {item.name: item for item in anyio.run(onboard_mcp.mcp_server(tool).list_tools)}
    assert {name: tools[data[2]].meta["role"] for name, data in CAPABILITIES.items()} == {
        name: check.__name__ for name, check in expected.items()
    }
    assert tools["onboard_checks"].meta["step"] == "checks"
    assert tools["onboard_improve"].meta["step"] == "improve"


@pytest.mark.parametrize("tool_name,tool_args,route_call", [
    ("onboard_tables", {"unit": "u", "label": "U", "ddl": "CREATE TABLE t (id INTEGER)"},
     lambda: routes.tables(routes.SourceBody(unit="u", label="U", ddl="CREATE TABLE t (id INTEGER)"), "business")),
    ("onboard_source", {"unit": "u", "label": "U", "ddl": "CREATE TABLE t (id INTEGER)"},
     lambda: routes.source(routes.SourceBody(unit="u", label="U", ddl="CREATE TABLE t (id INTEGER)"), "business")),
    ("onboard_rows", {"unit": "u", "rows": "invented"},
     lambda: routes.add_rows(routes.RowsBody(unit="u", rows="invented"), "business")),
    ("onboard_draft", {"unit": "u"}, lambda: routes.draft(routes.UnitBody(unit="u"), "business")),
    ("onboard_checks", {}, lambda: routes.run(routes.RunBody(step="checks"), "business")),
    ("onboard_improve", {}, lambda: routes.run(routes.RunBody(step="improve"), "business")),
    ("onboard_improve", {"unit": "retail"},
     lambda: routes.run(routes.RunBody(step="improve", unit="retail"), "business")),
])
def test_each_mutating_mcp_adapter_reaches_the_same_service_state(monkeypatch, tool_name, tool_args, route_call):
    state = []

    def call(fn, *args):
        state.append((fn, args))
        return {"job": {}} if fn is routes.SERVICE_FUNCTIONS.get("run:checks") else {}

    async def invoke(ctx, operation, check):
        return operation(check("business"))

    monkeypatch.setattr(routes, "service_call", call)
    monkeypatch.setattr(routes, "_role", lambda who, **kwargs: who)
    monkeypatch.setattr(routes, "_process", lambda *args: None)
    route_call()
    http_state = list(state)
    state.clear()
    registered = onboard_mcp.mcp_server(invoke)._tool_manager._tools[tool_name].fn
    anyio.run(lambda: registered(**tool_args, ctx=None))
    assert state == http_state


def operations() -> dict[str, dict]:
    import kge.app as appmod
    doc = appmod.app.openapi()
    return {f"{m.upper()} {p}": op for p, ops in doc["paths"].items() for m, op in ops.items()}


def typed(op: dict) -> bool:
    ok = op.get("responses", {}).get("200") or op.get("responses", {}).get("202") or {}
    schema = ok.get("content", {}).get("application/json", {}).get("schema")
    return bool(schema and "$ref" in schema)


def test_every_json_route_declares_a_response_model_or_is_named_with_its_reason():
    ops = operations()
    untyped = sorted(k for k, op in ops.items() if not typed(op) and k not in UNTYPED)
    assert not untyped, f"routes with no response model, and not named in UNTYPED: {untyped}"
    stale = sorted(k for k in UNTYPED if k not in ops or typed(ops[k]))
    assert not stale, f"named in UNTYPED, but gone or typed now: {stale}"


def test_the_ask_service_s_routes_are_public_or_internal_and_each_is_documented():
    from kge.services.ask.routes import router
    ours = {f"{m} {r.path.replace(':path', '')}" for r in router.routes for m in r.methods}
    assert ours == set(PUBLIC) | INTERNAL, ours ^ (set(PUBLIC) | INTERNAL)
    ops = operations()
    for key in ours:
        assert typed(ops[key]), key
        assert (ops[key].get("description") or "").strip(), f"{key} has no description in the OpenAPI schema"
    for key in INTERNAL:
        assert ops[key]["description"].startswith("Internal:"), key


def section(readme: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## )", readme, re.S | re.M)
    assert m, f"README has no section {heading!r}"
    return m.group(1)


def test_the_readme_names_the_two_public_capabilities_their_transports_and_the_internal_endpoints():
    text = section((DEMO / "README.md").read_text(), "The public API")
    capabilities = re.findall(r"^\| \*\*(\w+)\*\* \|", text, re.M)
    assert capabilities == ["Answer", "Context"], capabilities
    for key in PUBLIC:
        assert f"`{key}`" in text, f"{key} is public and not in the README's public API"
    for tool_name in ("`get_context`", "`retrieve`", "`map_term`", "`/mcp`"):
        assert tool_name in text, tool_name
    for key in INTERNAL:
        assert re.search(rf"`{re.escape(key)}`[^\n]*internal", text, re.I), f"{key} is not marked internal"
    for rule in ("answer_text", "never compute", "refusals and abstentions", "own principal"):
        assert rule in text, f"the guidance for callers of ask says nothing of {rule!r}"


def test_every_behaviour_the_engine_gives_is_documented_and_worded():
    """Every behaviour literal the engine and the semantics layer return is one of BEHAVIOURS, which AskResponse's
    behaviour enumerates and the template words; and every one of BEHAVIOURS is still given somewhere."""
    found = set()
    for folder in ENGINE:
        for path in folder.rglob("*.py"):
            found |= set(re.findall(r'"behaviour": "(\w+)"', path.read_text()))
    assert found == set(BEHAVIOURS), found ^ set(BEHAVIOURS)


def test_answer_route_and_tool_share_the_service_function():
    from kge.services.ask import routes as ask_routes
    from kge.services.ask import mcp as ask_mcp
    assert ask_routes.SERVICE_FUNCTIONS == {"ask": ask_service.ask}
    assert ask_mcp.SERVICE_FUNCTIONS == ask_routes.SERVICE_FUNCTIONS


@pytest.mark.parametrize("gate_sets, expected", [
    ({"golden": "passed", "heldout": "passed"}, {"golden": "passed", "heldout": "passed"}),
    ({"golden": "fell", "heldout": "passed"}, {"golden": "fell", "heldout": "passed"}),
])
def test_margin_reports_each_set_s_own_outcome(monkeypatch, tmp_path, gate_sets, expected):
    from kge.graph import release
    from kge.services.onboard.service import margin_report
    model_hash = "a" * 64
    graph = tmp_path / model_hash / "graph.ladybug"
    graph.parent.mkdir()
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    summary = {"sets": {
        "golden": {"graph": {"all": {"of": 2, "passed": {"min": 2}, "errors": 0}}},
        "heldout": {"graph": {"all": {"of": 2, "passed": {"min": 1}, "errors": 0}}},
    }}
    (run / "summary.json").write_text(__import__("json").dumps(summary))
    prof = SimpleNamespace(at=lambda value: SimpleNamespace(graph_path=lambda: graph), run_dir=lambda: tmp_path)
    monkeypatch.setattr(release, "marked", lambda *args: {
        "passed": "fell" not in gate_sets.values(), "run_id": "r1", "sets": gate_sets})
    result = margin_report(prof, model_hash)
    assert result["result"] == ("fell" if "fell" in gate_sets.values() else "passed")
    assert {row["name"]: row["result"] for row in result["sets"]} == expected


def test_margin_distinguishes_a_gate_failure_without_a_run(monkeypatch, tmp_path):
    from kge.graph import release
    from kge.services.onboard.service import margin_report
    model_hash = "b" * 64
    graph = tmp_path / model_hash / "graph.ladybug"
    graph.parent.mkdir()
    gate = {"passed": False, "run_id": None, "lines": ["V05 drafted items: 1 not certified: failed"]}
    prof = SimpleNamespace(at=lambda value: SimpleNamespace(graph_path=lambda: graph), run_dir=lambda: tmp_path)
    monkeypatch.setattr(release, "marked", lambda *args: gate)
    result = margin_report(prof, model_hash)
    assert result["result"] == "gate failed" and result["gate"]["lines"] == gate["lines"] and result["sets"] == []


def test_margin_gate_records_only_the_set_whose_margin_fell():
    from kge.eval.margin import set_outcomes
    manifest = {"sets": {"golden": {}, "heldout": {}, "golden_wealth_v1": {}}}
    gaps = {"fell": [{"set": "heldout"}]}
    assert set_outcomes(manifest, gaps, []) == {
        "golden": "passed", "heldout": "fell", "golden_wealth_v1": "passed"}


def test_margin_gate_uses_a_check_s_set_not_a_set_name_in_its_question():
    from kge.eval.margin import Check, set_outcomes
    manifest = {"sets": {"golden": {}, "heldout": {}}}
    check = Check("V04", None, False, "V04 heldout H001, Is golden data cited?: failed", "heldout")
    assert set_outcomes(manifest, {}, [check]) == {"golden": "passed", "heldout": "fell"}


def test_mcp_upload_limit_counts_ddl():
    with pytest.raises(onboard_mcp.ToolError, match="413: the MCP upload"):
        onboard_mcp._small("x" * (onboard_mcp.MCP_UPLOAD_BYTES + 1), None, None, None)


def test_every_mcp_tool_whose_route_checks_a_role_checks_it_before_its_arguments():
    from kge import app as api
    from kge.services import http
    from kge.services.ask.mcp import MCPEndpoint
    from tests.unit.test_engineer_skills import registered_routes
    endpoints = {f"{method} {route.path_format}": route for route in registered_routes(api.app) for method in route.methods}
    sign_in = {http.principal, routes.signed_in}
    seen = 0
    for route in api.app.routes:
        if not isinstance(getattr(route, "endpoint", None), MCPEndpoint):
            continue
        manager = route.endpoint.server._tool_manager
        for listed in anyio.run(route.endpoint.server.list_tools):
            http_route = endpoints[listed.meta["route"]]
            role = [d.call for d in http_route.dependant.dependencies if d.call not in sign_in]
            if role or listed.meta.get("role"):
                assert listed.name in manager.checks, (route.path, listed.name)
                seen += 1
            else:
                assert listed.name not in manager.checks, (route.path, listed.name)
    assert seen
