"""Liveness, version reporting and response serialization for the service."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from kge.services.ask.engine.wording import BEHAVIOURS
from kge.services.ask.routes import AnswerResponse, AskResponse, ContextResponse
from kge.services.ask.engine.core import AGENT_ARMS
from kge.eval import sets
from kge.pipeline.settings import settings
from tests.scratch import BUILD, profile_of, scratch_build


def test_healthz_needs_no_identity_or_engine(monkeypatch):
    import kge.app as appmod

    for opener in ("engine", "combined", "agent", "ids"):
        monkeypatch.setattr(appmod, opener, lambda name=opener: (_ for _ in ()).throw(AssertionError(f"healthz called {name}")))
    response = TestClient(appmod.app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": appmod.VERSION}
    head = TestClient(appmod.app).head("/healthz")
    assert head.status_code == 200 and head.content == b"", "a probe that asks with HEAD gets a 200, with no body"


def test_settings_are_frozen_and_read_the_environment_at_call_time(monkeypatch):
    first = settings()
    monkeypatch.setenv("KGE_VERSION", "1.2.3")
    second = settings()
    assert first.KGE_VERSION != "1.2.3" and second.KGE_VERSION == "1.2.3"
    try:
        second.KGE_VERSION = "changed"
    except AttributeError:
        pass
    else:
        raise AssertionError("settings are mutable")


def test_ask_answer_and_context_declare_their_response_models():
    """/ask, /answer and /context declare their response models (#102 §5: no response_model=None): the OpenAPI schema
    is the model's, every field a client relies on is declared, and a field the engine did not give is left out."""
    import kge.app as appmod
    import kge.services.ask.routes as askroutes
    doc = appmod.app.openapi()
    for path, model, fields in (("/ask", AskResponse, {"question", "behaviour", "answer_text", "phrased_by", "combined", "run_trace"}),
                                ("/answer", AnswerResponse, {"question", "behaviour", "answer_text", "phrased_by", "run_trace"}),
                                ("/context", ContextResponse, {"arm", "block"})):
        schema = doc["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{model.__name__}"}, (path, schema)
        properties = doc["components"]["schemas"][model.__name__]["properties"]
        assert fields <= set(properties) and fields <= set(doc["components"]["schemas"][model.__name__]["required"]), path
        route = next(r for r in askroutes.router.routes if r.path == path)
        assert route.response_model is model and route.response_model_exclude_unset, path
    ask = doc["components"]["schemas"]["AskResponse"]["properties"]
    assert {"figures", "citations", "not_cited", "withheld", "answer"} <= set(ask)
    assert set(ask["behaviour"]["enum"]) == set(BEHAVIOURS)
    payload = {"question": "Q", "principal": "p", "purpose": "analysis", "arm": "graph", "behaviour": "abstain", "combined": False,
               "trace": [], "versions": {}, "run_trace": {}, "answer_text": "No answer.", "phrased_by": "template", "zeta": 1}
    app = FastAPI()

    @app.get("/ask", response_model=AskResponse, response_model_exclude_unset=True)
    def documented():
        return payload
    assert TestClient(app).get("/ask").json() == payload, "the fields as given: none added, none dropped"


def test_the_page_displays_the_health_version_as_text():
    page = (Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text()
    assert 'id="version"' in page
    assert '("#version").textContent = `Version ${health.version}`' in page


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None),
                        ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        prof = profile_of(build)
        c.headers_ = {"authorization": f"Bearer {token_for(prof.run_dir(), prof.asking())}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_real_ask_and_context_responses_are_the_engine_s_fields_through_the_model(client, monkeypatch):
    """Every golden question, /ask with and without its display block and /context on each arm: the response holds what
    the service gave, field for field, nothing added and nothing dropped by the model, and validates against it."""
    import kge.app as appmod
    import kge.services.ask.routes as askmod

    seen: list[dict] = []
    real_shown, real_as_json = askmod.shown, appmod.as_json
    monkeypatch.setattr(askmod, "shown", lambda out, display: seen.append(real_shown(out, display)) or seen[-1])
    monkeypatch.setattr(appmod, "as_json", lambda package: seen.append(real_as_json(package)) or seen[-1])
    checked = 0
    for item in sets.items(BUILD / "golden.json"):
        calls = [("/ask", {"question": item["question"]}, display) for display in ("0", "1")]
        calls += [("/context", {"question": item["question"], "arm": arm}, None) for arm in AGENT_ARMS]
        for path, body, display in calls:
            seen.clear()
            response = client.post(path, json=body, headers=client.headers_,
                                   params={"display": display} if display else None)
            assert response.status_code == 200, (path, body, response.text)
            assert len(seen) == 1, (path, body)
            assert response.json() == jsonable_encoder(seen[0]), (path, body)
            (AskResponse if path == "/ask" else ContextResponse).model_validate(response.json())
            checked += 1
    assert checked > 100
