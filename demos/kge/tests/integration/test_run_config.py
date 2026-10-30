"""The run config: which stage does what, named, validated and labelled.

    .venv/bin/python -m pytest tests/integration/test_run_config.py -q

The three presets are today's arms, flag for flag. An override changes one
stage at a time, is checked before anything runs, and is named in the answer.
A model stage asked for with no model configured is refused, never quietly
answered without one. Offline: where a model is needed, a stub stands in.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import ARMS, PRESETS, STAGES, RunConfig, parse
from tests.scratch import profile_of, scratch_build
from tests.integration.test_combined import Q, Stub

DEFINE = "What is the definition of an active customer?"
BANK = "Total revenue across the bank for the quarter to 30 June 2025"


def test_presets_map_to_todays_flags():
    """The glossary maps to resolution; the join graph, curated facts and the certified allowlist to context; the
    shelves to citation_rule for definitions, and to context for routing a candidate term."""
    assert PRESETS == {
        "schema": RunConfig("schema", resolution="names", context="schema", sql_source="planner", citation_rule="retrieval", renderer="template"),
        "catalog": RunConfig("catalog", resolution="glossary", context="schema", sql_source="planner", citation_rule="retrieval", renderer="template"),
        "graph": RunConfig("graph", resolution="glossary", context="graph", sql_source="planner", citation_rule="shelves", renderer="template"),
    }
    assert ARMS is PRESETS


def test_a_config_is_frozen_and_every_preset_value_is_allowed():
    with pytest.raises(dataclasses.FrozenInstanceError):
        PRESETS["graph"].context = "schema"
    for cfg in PRESETS.values():
        assert all(getattr(cfg, s) in allowed for s, allowed in STAGES.items())
        assert cfg.problems("answer", has_model=False) == []
    assert [a for a, cfg in PRESETS.items() if not cfg.problems("ask", has_model=False)] == ["graph"]


def test_a_preset_is_labelled_by_its_name():
    assert [cfg.label for cfg in PRESETS.values()] == ["schema", "catalog", "graph"]


def test_an_override_is_labelled_in_stage_order_whatever_order_it_was_given_in():
    a = PRESETS["catalog"].with_stages(citation_rule="shelves", resolution="none")
    b = PRESETS["catalog"].with_stages(resolution="none", citation_rule="shelves")
    assert a == b and a.label == b.label == "catalog[resolution=none,citation_rule=shelves]"


def test_an_override_equal_to_the_preset_is_no_override():
    assert PRESETS["graph"].with_stages(context="graph", renderer=None).label == "graph"


def test_an_unknown_value_is_a_problem_naming_the_allowed_ones():
    p = PRESETS["graph"].with_stages(resolution="telepathy").problems("answer", has_model=False)
    assert len(p) == 1 and "resolution" in p[0] and "telepathy" in p[0] and "glossary" in p[0]


def test_answer_is_the_rule_based_baseline_and_takes_no_model_stage():
    cfg = PRESETS["graph"].with_stages(renderer="template+model")
    assert any("rule-based" in p for p in cfg.problems("answer", has_model=True))


def test_a_model_stage_without_a_model_is_a_problem_not_a_fallback():
    cfg = PRESETS["graph"].with_stages(sql_source="planner+model")
    assert any("no model is configured" in p for p in cfg.problems("ask", has_model=False))
    assert cfg.problems("ask", has_model=True) == []


def test_the_graph_context_needs_the_glossary():
    for resolution in ("names", "none"):
        p = PRESETS["graph"].with_stages(resolution=resolution).problems("answer", has_model=False)
        assert any("context=graph" in x and "glossary" in x for x in p), p


def test_ask_answers_on_the_graph_context_only():
    p = PRESETS["catalog"].problems("ask", has_model=False)
    assert any("/answer" in x for x in p), p
    assert any("citation_rule" in x for x in PRESETS["graph"].with_stages(citation_rule="retrieval").problems("ask", has_model=False))


def test_no_resolution_means_no_alias_mapping(eng):
    assert parse(BANK, [], eng).term == "Revenue"
    assert parse(BANK, [], eng, aliases=False).term is None
    assert parse("What was the Digital Engagement Score for June 2025?", [], eng, aliases=False).candidate is None
    assert parse("How many customers in Retail in June 2025?", [], eng, aliases=False).term is None


def test_resolution_none_answers_nothing_it_cannot_name(eng):
    cfg = PRESETS["schema"].with_stages(resolution="none")
    a = eng.answer(BANK, config=cfg)
    assert a["behaviour"] == "abstain" and a["arm"] == "schema[resolution=none]"
    assert a["trace"][0]["term"] is None
    assert eng.answer(BANK, arm_name="schema")["trace"][0]["term"] == "Revenue"


def test_a_preset_by_name_and_by_config_answer_alike(eng):
    for arm, cfg in PRESETS.items():
        assert eng.answer(BANK, arm_name=arm) == eng.answer(BANK, config=cfg)


def test_citation_rule_is_its_own_switch(eng):
    assert eng.answer(DEFINE, arm_name="catalog")["behaviour"] == "cite"
    a = eng.answer(DEFINE, config=PRESETS["catalog"].with_stages(citation_rule="shelves"))
    assert a["behaviour"] == "cite_certified" and a["arm"] == "catalog[citation_rule=shelves]"


def test_the_engine_refuses_a_config_that_does_not_hold(eng):
    with pytest.raises(ValueError, match="rule-based"):
        eng.answer(BANK, config=PRESETS["graph"].with_stages(renderer="template+model"))


def test_the_default_reproduces_today_model_use_from_its_presence(eng):
    assert Combined(eng, llm=None).default_config() == PRESETS["graph"]
    with_model = Combined(eng, llm=Stub()).default_config()
    assert with_model.label == "graph[resolution=glossary+model,sql_source=planner+model,renderer=template+model]"
    assert Combined(eng, llm=None).ask(Q, "analyst_retail")["arm"] == "graph"


def test_an_explicit_model_stage_with_no_model_is_refused(eng):
    with pytest.raises(ValueError, match="no model is configured"):
        Combined(eng, llm=None).ask(Q, "analyst_retail", config=PRESETS["graph"].with_stages(renderer="template+model"))


def test_a_stage_without_the_model_does_not_call_it(eng, monkeypatch):
    said = "In June 2025, 176 Retail customers were active [1]; 3 of them complained [4]. Their average balance was 6,400.62 [2] and fee income USD 42.64 [3]."
    stub = Stub(answer=said, terms=["Active Customer"])
    c = Combined(eng, llm=stub)
    rules = PRESETS["graph"]
    a = c.ask(Q, "analyst_retail", config=rules)
    assert a["phrased_by"] == "template" and a["arm"] == "graph" and stub.prompts == []
    a = c.ask(Q, "analyst_retail", config=rules.with_stages(renderer="template+model"))
    assert a["phrased_by"] == "model:stub:fixed" and a["arm"] == "graph[renderer=template+model]"
    assert [p.split(" ", 1)[0] for p in stub.prompts] == ["Rewrite"]
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    stub.prompts.clear()
    a = c.ask(Q, "analyst_retail", config=rules)
    assert a["behaviour"] == "abstain" and stub.prompts == []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_presets_endpoint(client):
    p = client.get("/presets").json()
    assert list(p["presets"]) == list(PRESETS) and p["default"] == "graph" and p["model_configured"] is False
    assert p["stages"] == {s: list(v) for s, v in STAGES.items()}
    assert p["presets"]["graph"] == {**PRESETS["graph"].stages(), "arm": "graph", "label": "graph", "paths": ["answer", "ask"]}
    assert [a for a, v in p["presets"].items() if "ask" in v["paths"]] == ["graph"]


def test_answer_takes_a_stages_override(client):
    who = client.as_("analyst_enterprise")
    a = client.post("/answer", json={"question": BANK, "arm": "schema", "stages": {"resolution": "none"}}, headers=who)
    assert a.status_code == 200 and a.json()["arm"] == "schema[resolution=none]" and a.json()["behaviour"] == "abstain"
    assert client.post("/answer", json={"question": BANK}, headers=who).json()["arm"] == "graph"


@pytest.mark.parametrize("path, body, says", [
    ("/answer", {"arm": "oracle"}, "arm must be one of"),
    ("/answer", {"stages": {"resolution": "telepathy"}}, "resolution must be one of"),
    ("/answer", {"stages": {"renderer": "template+model"}}, "rule-based"),
    ("/answer", {"stages": {"resolution": "names"}}, "context=graph"),
    ("/ask", {"stages": {"renderer": "template+model"}}, "no model is configured"),
    ("/ask", {"arm": "schema"}, "/answer"),
])
def test_an_invalid_config_is_a_400(client, path, body, says):
    r = client.post(path, json={"question": Q, **body}, headers=client.as_("analyst_retail"))
    assert r.status_code == 400 and says in r.json()["error"], r.json()
    assert r.json()["problems"]


def test_an_unknown_stage_is_a_malformed_body(client):
    r = client.post("/answer", json={"question": Q, "stages": {"colour": "blue"}}, headers=client.as_("analyst_retail"))
    assert r.status_code == 422


def test_ask_takes_an_override_without_a_model(client):
    a = client.post("/ask", json={"question": Q, "stages": {"renderer": "template"}}, headers=client.as_("analyst_retail")).json()
    assert a["behaviour"] == "combined" and a["arm"] == "graph" and a["phrased_by"] == "template"


def test_the_page_does_not_hardcode_the_arms():
    html = (Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text()
    assert "/presets" in html
    assert not any(f'<option value="{a}">' in html for a in PRESETS)
    assert 'arm === "graph"' not in html


def test_run_golden_takes_its_arms_from_the_presets():
    from kge.eval.run_golden import choose_arms
    assert choose_arms("all", PRESETS) == list(PRESETS)
    assert choose_arms("catalog", PRESETS) == ["catalog"]
    with pytest.raises(SystemExit, match="oracle"):
        choose_arms("oracle", PRESETS)
