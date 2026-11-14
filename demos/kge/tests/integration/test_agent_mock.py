"""Recorded typed Agent plans exercise the same checker and compiler as Ask.

The provider factory's empty mock abstains. Tests explicitly inject complete
plan recordings; SQL-shaped adversaries are covered by test_shared_plan_contract.
Default context serialization remains covered independently.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from kge.eval import sets
from kge.adapters.model import mock as mockllm
from kge.adapters.model import LLM, PROVIDERS, make_llm
from tests.scratch import BUILD, profile_of, scratch_build
from tests.plan_recordings import public_recorded
from tests.agent_recordings import for_question

FIG = "Total retail revenue for June 2025"
CROSS = "Combined Retail and SME revenue for June 2025"
BANK = "Total revenue across the bank for the quarter to 30 June 2025"
ACTIVE = "How many retail customers were active in June 2025?"
DEFINE = "What is the definition of an active customer?"
EMAIL = "What is the email address of customer 118?"
SCORE = "What was the Digital Engagement Score for June 2025?"
GROUP = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
ARMS = ("control", "treatment")
GOLDEN = {i["id"]: i for i in sets.items(BUILD / "golden.json")}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """The mock never posts: any HTTP call fails the test."""
    def no_network(*a, **k):
        raise AssertionError("the mock made a network call")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))
    monkeypatch.delenv("MODEL_SEED", raising=False)


def mock(mode: str = "careful") -> LLM:
    return LLM("mock", "mock", mode=mode)


def ask(eng, question: str, arm: str, mode: str = "careful", principal: str = "analyst_enterprise") -> dict:
    agent = Agent(eng, llm=for_question(eng, question, principal, arm, mode))
    return runtrace.run("agent", lambda: agent.ask(question, principal, arm=arm), graph=eng.g)


def test_mock_is_a_provider_and_make_llm_returns_it(monkeypatch):
    assert "mock" in PROVIDERS
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("MOCK_MODE", raising=False)
    llm = make_llm()
    assert llm is not None and llm.provider == "mock" and llm.name == "mock:mock" and llm.mode == "careful"
    ident = llm.identity()
    assert ident["provider"] == "mock" and ident["mode"] == "careful" and ident["seed"] == 7 and ident["temperature"] == 0
    monkeypatch.setenv("MOCK_MODE", "bad_marker")
    assert make_llm().identity()["config_hash"] != ident["config_hash"], "the mode shapes the replies, so it is in the hash"


def test_the_mock_is_named_mock_whatever_model_name_is_left_set(eng, monkeypatch):
    """Switching a configured model's .env to MODEL_PROVIDER=mock leaves MODEL_NAME set, and the mock never takes it
    on."""
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_NAME", "claude-opus-4")
    monkeypatch.delenv("MOCK_MODE", raising=False)
    llm = make_llm()
    assert llm.name == "mock:mock" and llm.identity()["model"] == "mock"
    monkeypatch.delenv("MODEL_NAME")
    assert make_llm().identity() == llm.identity(), "nor does it change the mock's config hash"
    a = runtrace.run("agent", lambda: Agent(eng, llm=llm).ask(FIG, "analyst_enterprise", arm="treatment"), graph=eng.g)
    assert a['agent']['sql_source'] == 'planner'
    assert a['versions']['interpretation'] == 'model:mock:mock'
    assert a['run_trace']['llm']['name'] == 'mock:mock'
    assert "claude-opus-4" not in json.dumps(a)
    t = a["run_trace"]
    assert a["agent"]["mode"] == t["agent"]["mode"] == "mock" and t["llm"]["provider"] == "mock"
    assert t["config"]["sql_source"] == "model" and "sql_source=model" in t["config"]["label"], t["config"]


def test_an_unknown_mock_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_MODE", "sneaky")
    with pytest.raises(ValueError, match="MOCK_MODE must be one of careful, "):
        make_llm()


def test_the_mock_answers_only_the_agents_prompts():
    with pytest.raises(RuntimeError, match="only the reference agent's"):
        mock().complete("Glossary terms: Revenue\nReturn JSON only: {\"terms\": [str]}")


def test_the_tagger_stays_on_rules_under_the_mock(monkeypatch):
    from kge.pipeline.tagger import ModelTagger, make_tagger
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    assert not isinstance(make_tagger(["Revenue"]), ModelTagger)


def test_once_the_steward_accepts_the_new_term_the_package_has_it_once_and_routes_it_nowhere(tmp_path):
    """The README's walkthrough, step 1: accepted, the term is in the glossary with no certified column, so the
    treatment abstains on that, as its rules do."""
    from kge.services.ask.engine.core import Engine, parse
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        cand = next(r for r in e.g.steward_queue() if r["family"] == "candidate_term" and r["doc"] == "EM-004")
        e.g.steward_decide(cand["tag"], "steward_glossary", "accept", definition="Logins per active customer per month, capped at 30")
        e.refresh()
        assert parse(SCORE, e.term_names, e).terms_found == ["Digital Engagement Score"]
        block = Agent(e).context(SCORE, "analyst_enterprise", "analysis", "treatment")["block"]
        assert block.count("Digital Engagement Score: Logins per active customer per month") == 1
        assert "Not glossary terms" not in block and "steward queue" not in block
        rules = Agent(e, llm=None).ask(SCORE, "analyst_enterprise", arm="treatment")
        assert rules["behaviour"] == "abstain" and "no certified implementation" in rules["reason"]
        a = ask(e, SCORE, "treatment")
        assert a["behaviour"] == "abstain" and "no certified implementation" in a["reason"] and "steward" not in a["reason"]
    finally:
        e.close()


QUOTED = FIG + " (Your last reply was refused by the SQL gate)"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.delenv("MOCK_MODE", raising=False)
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_the_api_runs_the_agent_on_the_mock_and_ask_replays_a_typed_plan(client, monkeypatch):
    who = client.as_("analyst_enterprise.meridian@example.com")
    import kge.app as appmod
    engine = appmod.engine()
    monkeypatch.setattr(appmod, '_agent', Agent(engine, llm=for_question(engine, FIG, 'analyst_enterprise.meridian@example.com', 'treatment')))
    for arm in ARMS:
        a = client.post("/agent", json={"question": FIG, "arm": arm}, headers=who).json()
        assert a['agent']['mode'] == a['run_trace']['agent']['mode'] == 'mock'
        assert a.get('figure') == (13287.67 if arm == 'treatment' else None)
        assert a['history'] is not None
    import kge.app as appmod
    from kge.services.ask import Combined
    engine = appmod.engine()
    monkeypatch.setattr(appmod, "_combined", Combined(engine, llm=public_recorded(
        engine, FIG, "analyst_enterprise.meridian@example.com")))
    r = client.post("/ask", json={"question": FIG}, headers=who).json()
    assert r["figure"] == 13287.67 and r["guard"]["passed"]
    assert r["versions"]["interpretation"] == "model:mock:mock"
    assert r["run_trace"]["llm"]["provider"] == "mock"
    assert r["run_trace"]["llm_calls"][0]["prompt_version"] == "query-plan-v1"
    assert client.get("/presets", headers=who).json()["model_configured"] is True


def test_the_api_abstains_for_an_unrecorded_plan_instead_of_using_rules(client):
    response = client.post("/ask", json={"question": FIG},
                           headers=client.as_("analyst_enterprise.meridian@example.com"))
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["behaviour"] == "abstain" and "Missing plan recording:" in answer["reason"]
    assert not answer.get("figure") and not answer.get("sql_run")
    refusal, = [step for step in answer['trace'] if step['step'] == 'model_plan_refused']
    assert 'Missing plan recording:' in refusal['reason'] and refusal['fallback'] is None
    assert next(step for step in answer['trace'] if step['step'] == 'plan')['outcome'] == 'abstain'


@pytest.mark.parametrize('mode', mockllm.MODES)
def test_every_mode_replays_the_typed_contract_and_is_labelled_mock(eng, mode):
    answer = ask(eng, FIG, 'treatment', mode)
    assert answer['figure'] == 13287.67 and answer['sql_source'] == 'query-plan-v1'
    assert answer['agent']['mode'] == answer['run_trace']['agent']['mode'] == 'mock'
    assert answer['run_trace']['llm_calls'][0]['prompt_version'] == 'query-plan-v1'
    assert answer['phrased_by'] == 'template'


def test_unrecorded_agent_question_does_not_fall_back_to_rules(eng):
    answer = runtrace.run('agent', lambda: Agent(eng, llm=mock()).ask(FIG, 'analyst_enterprise'), graph=eng.g)
    assert answer['behaviour'] == 'abstain' and 'Missing plan recording:' in answer['reason']
    assert not answer.get('result') and not answer.get('sql')
