"""Exercise the agent's shipped token path against the real ASGI context route without sockets."""
import httpx
from fastapi.testclient import TestClient
from types import SimpleNamespace

import kge.app as app
from kge.pipeline.profile import resolve
from kge.services.ask import Engine
from kge.services.ask.engine.agent import Agent


def test_http_agent_asks_as_a_table_person_with_its_shipped_token(monkeypatch):
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    engine = Engine.serving(resolve())
    try:
        local = Agent(engine, llm=None)
        monkeypatch.setenv("CONTEXT_URL", "http://context")
        remote = Agent(engine, llm=None)
        seen = []
        def send(url, **kwargs):
            seen.append(remote.identities.from_token(kwargs["headers"]["authorization"].removeprefix("Bearer ")))
            kwargs.pop("timeout", None)
            with app.serving(remote), TestClient(app.app) as client:
                return client.post("/context", **kwargs)
        monkeypatch.setattr(httpx, "post", send)
        question = "What is Retail Banking revenue in June 2025?"
        person = "analyst_retail.meridian@example.com"
        assert remote.token("analyst_retail") is None
        for arm in ("control", "treatment"):
            assert remote.fetch(question, person, "analysis", arm) == local.fetch(question, person, "analysis", arm)
        assert seen == [person, person]
        assert remote.identities.role_ids == frozenset()
    finally:
        engine.close()


def test_frozen_live_answer_comparisons_with_person_credentials_over_asgi(monkeypatch, tmp_path):
    from tests import test_agent_loop as frozen

    monkeypatch.delenv("CONTEXT_URL", raising=False)
    engine = Engine.serving(resolve())
    server = Agent(engine, llm=None)
    posted = []
    log = tmp_path / "asgi-context.log"
    def send(url, **kwargs):
        token = kwargs["headers"]["authorization"].removeprefix("Bearer ")
        who = server.identities.from_token(token)
        assert who in engine.g.principal_ids_people()
        posted.append(who)
        kwargs.pop("timeout", None)
        with app.serving(server), TestClient(app.app) as client:
            response = client.post("/context", **kwargs)
        with log.open("a") as output:
            output.write(f'"POST /context HTTP/1.1" {response.status_code}\n')
        return response
    monkeypatch.setattr(httpx, "post", send)
    try:
        frozen.test_the_agent_answers_as_it_did_before_the_loop(engine, monkeypatch)
        frozen.test_with_context_url_at_a_live_service_the_answers_are_the_ones_in_this_process(
            SimpleNamespace(url="http://context", log=log), engine, monkeypatch)
        assert posted
    finally:
        frozen.HERE.clear()
        engine.close()
