"""Rows 4 and 5 of the Cerebro role matrix (cerebro.md §8.1, §8.4 changes 5 and 8): the curate row alone checks the
steward queue, so the business team, a Cerebro steward with no `certification`, reads the queue and the gaps, while a
decision also asks `certification`, refused and audited refused for the business team;
business acceptance is the steward's, so a role that certifies accepts; certifying and signing off stay the
`certification` process's; and every blank cell of the two rows is refused, audited, before any work."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from kge.adapters.roleaudit import role_audit_log
from kge.pipeline import access
from kge.services import http
from kge.services.onboard import jobs, routes as onboard_routes, service as onboard_service
from kge.services.steward import routes as steward_routes, service as steward_service

MERIDIAN = {role["id"]: role for role in yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "clients" / "meridian" / "pack.yaml").read_text())["access"]["roles"]}
STEWARDS = ("steward", "steward_glossary", "business_team")
BLANK = ("analyst_retail", "advisor_wealth", "finance_reporting", "knowledge_engineer", "admin")


class People:
    def is_admin(self, who):
        return False


class Rights:
    """The served graph's rights: each person acts in the Meridian role of their name, and today's process check is
    the role's own processes, so a check that still asked for `certification` would refuse the business team."""

    people = People()

    def acting_roles(self, who):
        return {who}

    def role_templates(self):
        return MERIDIAN

    def may(self, who, process):
        return process in MERIDIAN.get(who, {}).get("processes", [])


@pytest.fixture
def app(monkeypatch, tmp_path):
    """The steward and onboarding routes on a stand-in app whose role permissions are the core's own, read from the
    Meridian pack; every service raises 418, so a 418 is a call the checks let through to its work."""
    monkeypatch.delenv("STORE_URL", raising=False)
    profile = SimpleNamespace(run_dir=lambda: tmp_path, client="meridian")
    engine = SimpleNamespace(g=Rights())
    served = SimpleNamespace(engine=lambda: engine, PROFILE=profile, principal=lambda request: request.headers["who"])
    reached = []

    def work(*args, **kwargs):
        reached.append(args)
        raise HTTPException(418, "reached the service")

    for name in ("accept", "certify", "run"):
        monkeypatch.setattr(onboard_service, name, work)
    for name in ("queue", "decided", "decide", "gaps"):
        monkeypatch.setattr(steward_service, name, work)
    monkeypatch.setattr(onboard_routes.core, "permissions",
                        lambda prof, who, writable_=True, reader=None: access.permissions(MERIDIAN.get(who, {}), writable_))
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "access_reader", lambda app: None)
    monkeypatch.setattr(onboard_routes, "_app", lambda: served)
    monkeypatch.setattr(http, "served", served)
    monkeypatch.setattr(steward_routes, "served", served)
    api = FastAPI()
    api.include_router(onboard_routes.router)
    api.include_router(steward_routes.router)

    def principal(request: Request) -> str:
        return request.headers["who"]

    api.dependency_overrides[http.principal] = principal
    with TestClient(api) as c:
        yield c, served, reached


def audit(served):
    return role_audit_log(served.PROFILE.run_dir(), served.PROFILE.client).newest(100)


def test_the_business_team_has_no_certification_but_is_a_cerebro_steward():
    assert "certification" not in MERIDIAN["business_team"]["processes"]
    assert access.cerebro_roles(["business_team"], MERIDIAN) == ["steward"]


@pytest.mark.parametrize("path", ["/steward/queue", "/steward/queue?status=decided", "/steward/gaps"])
def test_the_business_team_reads_the_queue_on_the_curate_row_alone(app, path):
    c, served, reached = app
    r = c.get(path, headers={"who": "business_team"})
    assert r.status_code == 418, r.text
    assert reached
    [entry] = audit(served)
    assert (entry["who"], entry["row"], entry["outcome"]) == ("business_team", "curate", "allowed")


def test_the_business_teams_decision_is_refused_by_certification_and_audited_refused(app):
    c, served, reached = app
    r = c.post("/steward/decide", json={"tag": "t", "decision": "accept"}, headers={"who": "business_team"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "business_team is not a certification identity"
    assert reached == []
    assert [(e["who"], e["row"], e["endpoint"], e["outcome"]) for e in audit(served)] == [
        ("business_team", "curate", "POST /steward/decide", "refused")]


@pytest.mark.parametrize("who", ["steward", "steward_glossary"])
def test_a_certifying_stewards_decision_reaches_the_service_and_is_audited_allowed(app, who):
    c, served, reached = app
    r = c.post("/steward/decide", json={"tag": "t", "decision": "accept"}, headers={"who": who})
    assert r.status_code == 418, r.text
    assert reached
    assert [(e["who"], e["row"], e["endpoint"], e["outcome"]) for e in audit(served)] == [
        (who, "curate", "POST /steward/decide", "allowed")]


@pytest.mark.parametrize("who", BLANK)
@pytest.mark.parametrize("method,path,body", [("get", "/steward/queue", None), ("get", "/steward/gaps", None),
                                              ("post", "/steward/decide", {"tag": "t", "decision": "accept"})])
def test_the_curate_rows_blank_cells_are_refused_before_any_work(app, who, method, path, body):
    c, served, reached = app
    r = getattr(c, method)(path, headers={"who": who}, **({"json": body} if body else {}))
    assert r.status_code == 403, r.text
    assert r.json()["detail"].startswith(f"{who} may not do this: Curate")
    assert reached == []
    [entry] = audit(served)
    assert (entry["row"], entry["outcome"]) == ("curate", "refused")


@pytest.mark.parametrize("who", STEWARDS)
def test_every_steward_records_business_acceptance(app, who):
    c, served, reached = app
    r = c.post("/onboard/accept", json={"unit": "leasing"}, headers={"who": who})
    assert r.status_code == 418, r.text
    assert reached
    [entry] = audit(served)
    assert (entry["who"], entry["row"], entry["outcome"]) == (who, "review", "allowed")


@pytest.mark.parametrize("who", BLANK)
@pytest.mark.parametrize("path,body", [("/onboard/accept", {"unit": "leasing"}), ("/onboard/certify", {"unit": "leasing"}),
                                       ("/onboard/run", {"step": "sign-off"})])
def test_the_review_rows_blank_cells_are_refused_before_any_work(app, who, path, body):
    c, served, reached = app
    r = c.post(path, json=body, headers={"who": who})
    assert r.status_code == 403, r.text
    assert r.json()["detail"].startswith(f"{who} may not do this: Review candidates")
    assert reached == []
    [entry] = audit(served)
    assert (entry["row"], entry["outcome"]) == ("review", "refused")


@pytest.mark.parametrize("path,body", [("/onboard/certify", {"unit": "leasing"}), ("/onboard/run", {"step": "sign-off"})])
def test_the_business_team_still_neither_certifies_nor_signs_off(app, path, body):
    c, served, reached = app
    r = c.post(path, json=body, headers={"who": "business_team"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "business_team is not a steward whose role performs certification"
    assert reached == []


@pytest.mark.parametrize("role_id", sorted(MERIDIAN))
def test_acceptance_is_a_stewards_and_certifying_stays_the_certification_processs(role_id):
    role = MERIDIAN[role_id]
    allowed = access.permissions(role)
    certifies = "certification" in role.get("processes", [])
    assert allowed["certify"] is certifies and allowed["sign_off"] is certifies
    assert allowed["accept"] is (certifies or access.accepts_drafts(role))
    if "steward" in access.cerebro_role_of(role_id, role):
        assert allowed["accept"]
    assert not access.permissions(role, False)["accept"]
