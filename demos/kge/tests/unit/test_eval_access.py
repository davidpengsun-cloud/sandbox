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
    served_ns = SimpleNamespace(PROFILE=profile, engine=lambda: engine, ids=lambda: identities)
    monkeypatch.setattr(routes, "served", served_ns)
    calls = []
    result = {"job": {"id": "20261108T100000Z-abcdef", "step": "eval", "state": "running"}}

    def start_eval(served_app, caller, *, arm=None, runtimes=None):
        calls.append((served_app, caller, arm, runtimes))
        return result

    monkeypatch.setattr(routes.service, "start_eval", start_eval)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[principal] = lambda: who
    with TestClient(app) as client:
        response = client.post("/eval")
    if allowed:
        assert response.status_code == 202
        assert response.json() == result
        assert calls == [(served_ns, who, None, None)]
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": "Only identities with the building process or admins may start an evaluation run."}
        assert calls == []


@pytest.mark.parametrize("stored", [False, True])
def test_get_eval_returns_stored_refusals(monkeypatch, tmp_path, stored):
    import json

    from kge.adapters.objstore import open_store

    result = {"graph": {"figure": {"passed": 0, "of": 1}}, "refused": [
        {"id": "restricted", "runtime": "reference", "reason": "restricted column totals.private_value"},
    ]}
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store") if stored else "")
    store = open_store(str(tmp_path / "store"))
    monkeypatch.setattr(routes.service, "the_store", lambda: store)
    monkeypatch.setattr(routes.service.onboard, "load_job", lambda served: None)
    if stored:
        store.put("clients/meridian/onboard/eval.json", json.dumps(result).encode())
    else:
        (tmp_path / "eval.json").write_text(json.dumps(result))
    served = SimpleNamespace(PROFILE=SimpleNamespace(client="meridian", run_dir=lambda: tmp_path))
    monkeypatch.setattr(routes, "served", served)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.evaluator] = lambda: "engineer"
    with TestClient(app) as client:
        response = client.get("/eval")
    assert response.status_code == 200
    assert response.json() == result
