"""Evaluation admission does not grant the caller any additional data access."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.services.http import principal
from kge.services.quality import routes


@pytest.mark.parametrize("processes,roles,access_admin,allowed", [
    (["building", "querying"], ["knowledge_engineer"], False, True),
    (["building"], ["custom_checker"], False, True),
    (["querying"], ["admin"], False, True),
    ([], ["access_admin"], True, True),
    (["querying"], ["analyst_retail"], False, False),
    (["querying", "certification"], ["steward"], False, False),
    (["querying", "drafting", "improving", "accepting"], ["custom_operator"], False, False),
    ([], [], False, False),
])
def test_eval_requires_building_or_admin_and_preserves_caller(monkeypatch, processes, roles, access_admin, allowed):
    who = "person@example.com"
    graph = SimpleNamespace(may=lambda person, process: person == who and process in processes,
                            acting_roles=lambda person: set(roles) if person == who else set())
    engine = SimpleNamespace(g=graph)
    profile = object()
    identities = SimpleNamespace(is_admin=lambda person: person == who and access_admin)
    monkeypatch.setattr(routes, "served", SimpleNamespace(PROFILE=profile, engine=lambda: engine, ids=lambda: identities))
    calls = []
    result = {"graph": {"rows": [{"id": "G001", "ok": True}], "by_kind": {}}}

    def evaluate(*args):
        calls.append(args)
        return result

    monkeypatch.setattr(routes.service, "evaluate", evaluate)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[principal] = lambda: who
    with TestClient(app) as client:
        response = client.post("/eval")
    if allowed:
        assert response.status_code == 200
        assert response.json() == result
        assert calls == [(profile, engine, who)]
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": "Only identities with the building process or admins may start an evaluation run."}
        assert calls == []
