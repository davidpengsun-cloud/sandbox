"""Knowledge engineering processes and human decisions over authenticated HTTP and MCP."""
from contextlib import asynccontextmanager
import shutil
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest
import yaml

from kge.compiler.pack import accepts_drafts
from kge.estate import onboard
from kge.estate.onboard.clients import _client_pack
from kge.pipeline.profile import Profile
from kge.pipeline.settings import ROOT
from kge.services import http
from kge.services.ask import mcp as ask_mcp
from kge.services.identity.service import Identities, token_for
from kge.services.onboard import jobs, mcp, routes

ENGINEER = "knowledge_engineer"
HASH = "a" * 64


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    clients = tmp_path / "clients"
    shutil.copytree(ROOT / "clients/meridian", clients / "meridian")
    identities = tmp_path / "identities.yaml"
    shutil.copy(ROOT / "deploy/meridian/identities.yaml", identities)
    prof = Profile("meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "warehouses", identities, None, tmp_path / "profiles.yaml")
    names = [row["principal"] for row in yaml.safe_load(identities.read_text())["principals"]]
    ids = Identities(prof.run_dir(), names)
    fake = SimpleNamespace(PROFILE=prof, ids=lambda: ids, COOKIE="demo_session", SIGN_IN="sign in",
                           principal=http.principal, engine=lambda: SimpleNamespace(profile=prof.at(HASH)))
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(ask_mcp, "served", fake)
    monkeypatch.setattr(routes, "_app", lambda: fake)
    starts = []

    def start(app, step, wanted_hash, who, shown):
        starts.append((step, who, shown))
        return {"job": {"id": "test-job", "step": step, "state": "running"}}

    monkeypatch.setattr(jobs, "start", start)
    server = mcp.mcp_server(ask_mcp.tool)

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, http.request_validation_failed)
    app.include_router(routes.router)
    app.add_route("/mcp/onboard", ask_mcp.MCPEndpoint(server), methods=["POST"])
    with TestClient(app) as client:
        yield client, prof, starts


def call(boundary, transport, who, capability, body=None):
    client, prof, _ = boundary
    headers = {"authorization": f"Bearer {token_for(prof.run_dir(), who)}"}
    if transport == "http":
        if capability == "margin":
            response = client.get("/onboard/margin", params=body, headers=headers)
        elif capability == "status":
            response = client.get("/onboard", headers=headers)
        else:
            path = "run" if capability in {"checks", "improve", "sign-off", "release"} else capability
            data = {**(body or {}), "step": capability} if path == "run" else body
            response = client.post(f"/onboard/{path}", json=data, headers=headers)
        return response.status_code, response.json()
    headers.update(accept="application/json, text/event-stream", host="localhost:18930")
    response = client.post("/mcp/onboard", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": f"onboard_{capability.replace('-', '_')}", "arguments": body or {}}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    if result.get("isError"):
        return 400, result["content"][0]["text"]
    return 200, result["structuredContent"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_engineer_lands_drafts_builds_reads_margin_and_improves(boundary, transport):
    _, prof, starts = boundary
    body = {"unit": "leasing", "label": "Asset Finance", "ddl": (ROOT / "examples/leasing/schema.sql").read_text(),
            "rows": "none"}
    assert call(boundary, transport, ENGINEER, "tables", body)[0] == 200
    assert call(boundary, transport, ENGINEER, "source", body)[0] == 200
    status, draft = call(boundary, transport, ENGINEER, "draft", {"unit": "leasing"})
    assert status == 200 and draft["items"]
    assert all(not item["accepted_by"] and not item["certified_by"] for item in draft["items"])
    csv = {p.name: p.read_text() for p in (ROOT / "examples/leasing/data").glob("*.csv")}
    assert call(boundary, transport, ENGINEER, "rows", {"unit": "leasing", "rows": "csv", "csv": csv})[0] == 200
    for step in ("checks", "improve"):
        status, result = call(boundary, transport, ENGINEER, step)
        assert status in {200, 202} and result["job"]["step"] == step
    assert [(step, who) for step, who, _ in starts] == [("checks", ENGINEER), ("improve", ENGINEER)]
    folder = prof.at(HASH).graph_path().parent
    folder.mkdir(parents=True)
    status, margin = call(boundary, transport, ENGINEER, "margin", {"hash": HASH})
    assert status == 200 and margin["hash"] == HASH
    assert margin["result"] == "not checked"
    can = onboard.permissions(prof, ENGINEER)
    assert all(can[key] for key in ("read", "create", "draft", "run", "improve", "participant"))
    assert not any(can[key] for key in ("accept", "certify", "sign_off", "release"))


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("step", ["accept", "certify", "sign-off", "release"])
def test_engineer_cannot_reach_human_steps(boundary, transport, step):
    code, result = call(boundary, transport, ENGINEER, step, {"unit": "leasing"} if step in {"accept", "certify"} else {})
    if transport == "http":
        assert code == 403, result
    else:
        assert code == 400 and "Unknown tool" in result
    assert boundary[2] == []


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("process,capability,body", [
    ("drafting", "source", {"unit": "u", "label": "U", "ddl": "CREATE TABLE t (id INTEGER)"}),
    ("building", "checks", {}), ("building", "margin", {"hash": HASH}), ("improving", "improve", {})])
def test_each_process_is_required_even_for_an_engineer(boundary, transport, process, capability, body):
    prof = boundary[1]
    path = prof.clients / prof.client / "pack.yaml"
    pack = yaml.safe_load(path.read_text())
    role = next(r for r in pack["access"]["roles"] if r["id"] == ENGINEER)
    role["processes"].remove(process)
    path.write_text(yaml.safe_dump(pack, sort_keys=False))
    status, result = call(boundary, transport, ENGINEER, capability, body)
    detail = f"{ENGINEER} is not {'an' if process[:1] in 'aeiou' else 'a'} {process} identity"
    if transport == "http":
        assert (status, result) == (403, {"detail": detail})
    else:
        assert status == 400 and result.endswith(f"403: {detail}")
    assert boundary[2] == []


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_analyst_is_not_an_onboarding_participant(boundary, transport):
    for step, body in (("draft", {"unit": "leasing"}), ("checks", {}), ("improve", {}), ("margin", {"hash": HASH})):
        status, result = call(boundary, transport, "analyst_enterprise", step, body)
        assert status == (403 if transport == "http" else 400)
        assert "identity" in str(result)
    assert boundary[2] == []


def test_business_accepts_and_steward_certifies_engineers_draft(boundary):
    test_engineer_lands_drafts_builds_reads_margin_and_improves(boundary, "http")
    for who, step in (("business_team", "accept"), ("steward_glossary", "certify")):
        status, result = call(boundary, "http", who, step, {"unit": "leasing"})
        assert status == 200, result
        assert any(item[f"{'accepted' if step == 'accept' else 'certified'}_by"] == who for item in result["items"])
    for step in ("sign-off", "release"):
        assert call(boundary, "http", "steward_glossary", step)[0] == 202
        assert call(boundary, "http", "business_team", step)[0] == 403


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("entry", ["http", "cli"])
def test_accepting_process_or_legacy_writes_allow_acceptance(boundary, monkeypatch, explicit, entry):
    from kge.estate.onboard import cli
    prof = boundary[1]
    path = prof.clients / prof.client / "pack.yaml"
    pack = yaml.safe_load(path.read_text())
    role = next(r for r in pack["access"]["roles"] if r["id"] == "business_team")
    role["processes"] = ["accepting"] if explicit else ["querying"]
    role["writes"] = [] if explicit else ["terms", "mappings"]
    path.write_text(yaml.safe_dump(pack, sort_keys=False))
    onboard.create(prof, "leasing", ddl_file=ROOT / "examples/leasing/schema.sql",
                   label="Leasing", rows="invented", as_=ENGINEER)
    onboard.draft(prof, "leasing", as_=ENGINEER)
    status, result = call(boundary, "http", "business_team", "status")
    assert status == 200 and result["can"]["accept"]
    if entry == "http":
        status, result = call(boundary, "http", "business_team", "accept", {"unit": "leasing"})
        assert status == 200, result
        assert all(item["accepted_by"] == "business_team" for item in result["items"])
    else:
        monkeypatch.setattr(cli, "resolve", lambda _: prof)
        assert cli.main(["accept", "--unit", "leasing", "--as", "business_team", "--json"]) == 0


def test_accepting_is_explicit_even_with_certification_and_without_writes():
    from kge.pipeline.access import OPERATIONAL_PROCESSES, permissions
    assert OPERATIONAL_PROCESSES["accepting"] == ("/onboard/accept",)
    assert accepts_drafts({"processes": ["accepting", "certification"]})
    assert not permissions({"processes": ["accepting"]}, False)["accept"]
    assert not accepts_drafts({"writes": ["terms"]})


def test_steward_cannot_accept_over_http(boundary):
    status, result = call(boundary, "http", "steward_glossary", "accept", {"unit": "leasing"})
    assert status == 403, result


def test_client_template_reads_the_meta_model_and_separates_the_roles(monkeypatch, tmp_path):
    from kge.estate.onboard import clients
    (tmp_path / "meta-model.yaml").write_text("version: 1.5.0\n")
    monkeypatch.setattr(clients, "ROOT", tmp_path)
    pack = _client_pack("invented", "Invented", ["leasing"])
    assert pack["manifest"]["meta_model_version"] == "1.5.0"
    assert pack["schema"]["meta_model"] == "1.5"
    assert pack["manifest"]["platform"]["meta_model"] == "^1.5"
    role = next(r for r in pack["access"]["roles"] if r["id"] == ENGINEER)
    assert set(role["processes"]) == {"drafting", "building", "improving", "querying"}
    assert not accepts_drafts(role)
    business = next(r for r in pack["access"]["roles"] if r["id"] == "business_team")
    assert business["processes"] == ["accepting", "querying"] and accepts_drafts(business)
    steward = next(r for r in pack["access"]["roles"] if r["id"] == "steward")
    assert steward["processes"] == ["certification", "querying"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("who", ["steward_glossary", "steward", "admin"])
def test_existing_roles_cannot_run_automated_steps(boundary, transport, who):
    for step in ("checks", "improve"):
        status, result = call(boundary, transport, who, step)
        assert status == (403 if transport == "http" else 400), result
        assert "identity" in str(result)
    assert boundary[2] == []


@pytest.mark.parametrize("path", ["/onboard/source", "/onboard/draft", "/onboard/run", "/mcp/onboard"])
def test_engineering_requires_identity(boundary, path):
    client = boundary[0]
    response = client.post(path, json={})
    assert response.status_code == 401


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_body_cannot_supply_a_human_principal(boundary, transport):
    status, result = call(boundary, transport, ENGINEER, "checks", {"principal": "steward_glossary"})
    assert status == (422 if transport == "http" else 400)
    assert "Extra inputs are not permitted" in str(result)
    assert boundary[2] == []


def test_mcp_checks_cannot_be_redirected_to_a_human_step(boundary):
    for step in ("sign-off", "release"):
        status, result = call(boundary, "mcp", ENGINEER, "checks", {"step": step})
        assert status == 400 and "Extra inputs are not permitted" in result
    assert boundary[2] == []


def test_permissions_follow_process_grants_and_write_policy(boundary):
    prof = boundary[1]
    assert not any(value for key, value in onboard.permissions(prof, ENGINEER, False).items()
                   if key not in {"participant", "read"})
    assert not any(onboard.permissions(prof, "analyst_enterprise").values())
    assert not any(onboard.permissions(prof, "platform").values())


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_business_reads_and_accepts_but_cannot_engineer(boundary, transport):
    body = {"unit": "leasing", "label": "Asset Finance", "ddl": "CREATE TABLE t (id INTEGER)"}
    for step, data in [("tables", body), ("source", body), ("draft", {"unit": "leasing"}),
                       ("rows", {"unit": "leasing", "rows": "invented"}), ("checks", {}),
                       ("margin", {"hash": HASH}), ("improve", {})]:
        status, result = call(boundary, transport, "business_team", step, data)
        assert status == (403 if transport == "http" else 400), result
    can = onboard.permissions(boundary[1], "business_team")
    assert can["read"] and can["participant"] and can["accept"]
    assert not any(can[key] for key in ("create", "draft", "run", "improve"))
    assert call(boundary, transport, "business_team", "status")[0] == 200
    assert boundary[2] == []


@pytest.mark.parametrize("step,who,process", [
    ("create", "business_team", "drafting"), ("draft", "business_team", "drafting"),
    ("rows", "business_team", "drafting"), ("accept", ENGINEER, "business-team"),
    ("accept", "steward_glossary", "business-team"),
])
@pytest.mark.parametrize("entry", ["core", "cli"])
def test_core_and_cli_enforce_roles_without_routes(boundary, monkeypatch, capsys, step, who, process, entry):
    from kge.estate.onboard import cli
    prof = boundary[1]
    ddl_file = ROOT / "examples/leasing/schema.sql"
    onboard.create(prof, "leasing", ddl_file=ddl_file, label="Leasing", rows="invented", as_=ENGINEER)
    onboard.draft(prof, "leasing", as_=ENGINEER)
    before = {p.relative_to(prof.clients): p.read_bytes() for p in prof.clients.rglob("*") if p.is_file()}
    if entry == "core":
        kwargs = {"ddl_file": ddl_file, "label": "Leasing", "rows": "invented"} if step == "create" else (
            {"rows": "invented"} if step == "rows" else {})
        with pytest.raises(onboard.OnboardError, match=process) as exc:
            getattr(onboard, step)(prof, "leasing", as_=who, **kwargs)
        assert exc.value.status == 403
    else:
        monkeypatch.setattr(cli, "resolve", lambda _: prof)
        args = [step, "--unit", "leasing", "--as", who, "--json"]
        if step == "create":
            args += ["--ddl", str(ddl_file), "--rows", "invented"]
        elif step == "rows":
            args += ["--rows", "invented"]
        assert cli.main(args) == 2
        assert process in capsys.readouterr().out
    assert before == {p.relative_to(prof.clients): p.read_bytes() for p in prof.clients.rglob("*") if p.is_file()}


def test_route_accept_dependency_refuses_engineer_without_the_core_transition(boundary):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        routes.business(ENGINEER)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("profile,steward", [
    ("meridian", "steward_glossary"), ("thornbury", "claims_steward"), ("fellmoor", "fares_steward")])
def test_bundled_roles_separate_engineering_from_steward_decisions(profile, steward):
    from kge.pipeline.profile import resolve
    can = onboard.permissions(resolve(profile), steward)
    assert all(can[action] for action in ("certify", "sign_off", "release"))
    assert not any(can[action] for action in ("create", "draft", "accept", "run", "improve"))
    can = onboard.permissions(resolve(profile), ENGINEER)
    assert all(can[action] for action in ("create", "draft", "run", "improve"))
    assert not any(can[action] for action in ("accept", "certify", "sign_off", "release"))


@pytest.mark.parametrize("capability,body", [
    ("source", {}),
    ("source", {"unit": 12, "label": "Invented", "ddl": "CREATE TABLE t (id INTEGER)"}),
    ("tables", {"unexpected": True}), ("rows", {"rows": "invalid"}),
    ("draft", {}), ("margin", {}), ("status", {"unexpected": True}),
    ("checks", {"unexpected": True}), ("improve", {"unexpected": True}),
])
def test_mcp_role_refusal_precedes_argument_validation(boundary, capability, body):
    status, result = call(boundary, "mcp", "analyst_enterprise", capability, body)
    http_body = {} if capability in {"checks", "improve"} else body
    http_status, http_result = call(boundary, "http", "analyst_enterprise", capability, http_body)
    assert http_status == 403
    assert status == 400 and result == f"403: {http_result['detail']}"
    assert boundary[2] == []


@pytest.mark.parametrize("body", [{}, {"unit": 12}, {"unit": "leasing", "unexpected": True}])
def test_authorised_mcp_caller_still_gets_validation_detail(boundary, body):
    http_status, http_result = call(boundary, "http", ENGINEER, "draft", body)
    status, result = call(boundary, "mcp", ENGINEER, "draft", body)
    assert http_status == 422
    assert all(set(error) == {"loc", "msg"} for error in http_result["detail"])
    assert status == 400 and result == f"422: {http_result['detail']}"
    assert boundary[2] == []


@pytest.mark.parametrize("bearer", [None, "bad-token"])
def test_invalid_mcp_arguments_do_not_precede_authentication(boundary, bearer):
    headers = {"accept": "application/json, text/event-stream", "host": "localhost:18930"}
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
    response = boundary[0].post("/mcp/onboard", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "onboard_source", "arguments": {"unexpected": True}}})
    assert response.status_code == 401
    assert boundary[2] == []


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_bootstrap_improve_status_says_to_run_checks(boundary, monkeypatch, transport):
    from kge.improve import CannotImprove
    from kge.improve.gather import find_before_run
    with pytest.raises(CannotImprove) as exc:
        find_before_run(boundary[1], HASH)
    diagnostic = f"improve: {exc.value}"
    assert f"stop the API and run make margin HASH={HASH} first" in diagnostic
    job = {"id": "bootstrap-improve", "step": "improve", "state": "failed",
           "message": diagnostic, "log_tail": "gather: starting\n" + diagnostic}
    monkeypatch.setattr(jobs, "load", lambda app: job)
    status, result = call(boundary, transport, ENGINEER, "status")
    assert status == 200
    message = "Run the checks first: this release has no margin yet."
    assert result["job"]["message"] == message
    assert result["job"]["log_tail"] == "gather: starting\n" + message
    assert job["message"] == diagnostic
    assert job["log_tail"].endswith(diagnostic)
