"""Exercise the agent's shipped token path against the real ASGI context route without sockets."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import kge.app as app
from kge.pipeline.profile import resolve
from kge.services.ask import Engine
from kge.services.ask.engine.agent import Agent
from tests.scratch import open_engine

QUESTION_SETS = ("golden", "heldout", "golden_wealth_v1")


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
        from kge.semantics.plan import HistoryEntry
        from kge.services.ask.engine.combined import Combined
        from kge.services.assist.service import assist
        combined = Combined(engine, llm=None)
        first = combined.ask(question, person)
        second = remote.ask(question, person, history=[HistoryEntry.model_validate(h) for h in first['history']])
        third = assist(combined, engine.profile, question, person, 'analysis', 'graph', combined.default_config(),
                       history=[HistoryEntry.model_validate(h) for h in second['history']])
        assert first['figure'] == second['figure'] == third['figure'] == 13287.67
        assert len(second['history']) == 2 and len(third['history']) == 3
        assert first['history'][0] == second['history'][0] == third['history'][0]
    finally:
        engine.close()


@pytest.mark.parametrize("question_set", QUESTION_SETS)
def test_frozen_live_answer_comparisons_with_person_credentials_over_asgi(monkeypatch, tmp_path, question_set):
    from tests import test_agent_loop as frozen

    monkeypatch.delenv("CONTEXT_URL", raising=False)
    engine = open_engine()
    server = Agent(engine, llm=None)
    posted = []
    log = tmp_path / "asgi-context.log"
    def send(url, **kwargs):
        token = kwargs["headers"]["authorization"].removeprefix("Bearer ")
        who = server.identities.from_token(token)
        assert who in engine.g.principal_ids_people()
        posted.append(who)
        kwargs.pop("timeout", None)
        response = client.post("/context", **kwargs)
        with log.open("a") as output:
            output.write(f'"POST /context HTTP/1.1" {response.status_code}\n')
        return response
    monkeypatch.setattr(httpx, "post", send)
    with app.serving(server), TestClient(app.app) as client:
        try:
            asks = {key: (q, who, arm) for key, q, who, arm in frozen.questions()}
            assert {key.split(":", 1)[0] for key in asks} == {*QUESTION_SETS, "golden_figures_v1"}
            included = {question_set, "golden_figures_v1"} if question_set == "golden" else {question_set}
            selected = {key for key in asks if key.split(":", 1)[0] in included}
            assert selected
            seals = json.loads(frozen.SNAPSHOT.read_text())["answers"]
            release = json.loads((frozen.SNAPSHOT.parent / "agent_query_plan_402_vocabulary_manifest.json").read_text())
            changed_release = {row["key"]: row for row in release["answers"]}
            seals = {key: changed_release.get(key, {}).get("after_seal", seal) for key, seal in seals.items()}
            seals = {key: seal for key, seal in seals.items() if seal is not None}
            migration = json.loads((frozen.SNAPSHOT.parent / "agent_query_plan_readable_vocabulary_manifest.json").read_text())
            preserved = {row["key"]: row for row in migration["answers"]}
            by_mode = {}
            for key in seals:
                by_mode.setdefault(key.rsplit(":", 1)[1], []).append(key.rsplit(":", 1)[0])
            assert set(by_mode) == {"rules", *frozen.modes()}
            assert set(by_mode["rules"]) == set(by_mode["careful"]) == set(asks)
            here_answers = {}
            moved = []
            for mode, keys in by_mode.items():
                monkeypatch.delenv("CONTEXT_URL", raising=False)
                agent = Agent(engine, llm=frozen.llm(None if mode == "rules" else mode))
                for key in keys:
                    if key not in selected:
                        continue
                    answer = frozen.asked(agent, *asks[key])
                    changed = preserved.get(f"{key}:{mode}")
                    if changed and f"{key}:{mode}" not in changed_release:
                        assert frozen.preserved_answer_sha(answer, changed["key"] == frozen.REASON_DIAGNOSTIC) == changed["answer_sha"]
                    here_answers[f"{key}:{mode}"] = frozen.answer_of(answer)
                    q, who, arm = asks[key]
                    block = agent.context(q, who, "analysis", arm)["block"]
                    if frozen.digest(answer, block) != seals[f"{key}:{mode}"]:
                        moved.append(f"{key}:{mode}")
            assert moved == []
            by_mode["careful"] = by_mode["careful"][::4]
            count = 0
            for mode, keys in by_mode.items():
                monkeypatch.delenv("CONTEXT_URL", raising=False)
                here = Agent(engine, llm=frozen.llm(None if mode == "rules" else mode))
                monkeypatch.setenv("CONTEXT_URL", "http://context/")
                there = Agent(engine, llm=frozen.llm(None if mode == "rules" else mode))
                assert (here.context_url, there.context_url) == (None, "http://context")
                for key in keys:
                    if key not in selected:
                        continue
                    q, who, arm = asks[key]
                    remote_ask = (q, f"{who}.meridian@example.com", arm)
                    answer = frozen.asked(there, *remote_ask)
                    assert frozen.answer_of(answer) == (here_answers.get(f"{key}:{mode}") or frozen.answer_of(frozen.asked(here, *remote_ask))), f"{key}:{mode}"
                    count += any(t["step"] == "context" and "sources" in t for t in answer["trace"])
            assert count > 0 and log.read_text().count('"POST /context HTTP/1.1" 200') == count
            assert posted
        finally:
            engine.close()
