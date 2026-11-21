"""Measure vocabulary keeps period-less figures on Ask's existing figure path (#250)."""
from pathlib import Path

import pytest

from kge.eval import harness, sets
from kge.eval.metrics import figure_ok
from kge.eval.run_golden import score
from kge.estate.golden import build_figure_questions, set_text
from kge.services.ask.engine import combined, core
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import CATALOGUE_ABSTENTION, catalogue_question, parse
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Stub
from tests.scratch import BUILD

DEMO = Path(__file__).resolve().parents[2]
FIXTURE = DEMO / "clients/meridian/fixture"
FIGURES = sets.items(FIXTURE / "golden_figures_v1.json")
WHO = "analyst_enterprise.meridian@example.com"


def test_four_figure_questions_are_in_the_declared_evaluation():
    assert [item["question"] for item in FIGURES] == [
        "What is the certified revenue for retail?",
        "What is the certified NNA for wealth?",
        "What is the trusted revenue figure for retail?",
        "Which products have revenue above 1000?",
    ]
    assert [item['kind'] for item in FIGURES] == ['shelf', 'shelf', 'shelf', 'abstain']
    pack = DEMO / "clients/meridian/pack.yaml"
    assert harness.set_version("golden_figures_v1") == "v1"
    assert "golden_figures_v1" in sets.declared_files(pack)
    assert "golden_figures_v1" in harness.choose_sets("all", BUILD, sets.declared(pack), pack)
    assert sets.items(BUILD / "golden_figures_v1.json") == FIGURES
    assert build_figure_questions(BUILD) == FIGURES
    assert (FIXTURE / "golden_figures_v1.json").read_bytes() == (set_text(build_figure_questions(BUILD)) + "\n").encode()


def test_threshold_question_abstains_under_the_approved_planner_boundary():
    item = FIGURES[3]
    assert item["id"] == "F04" and item["expect"] == {"behaviour": "abstain", "no_figure": True}
    assert score(item, {"behaviour": "abstain"})[0]
    assert figure_ok(item, {"behaviour": "abstain"}) is None
    for behaviour in ("parts_with_reason", "refuse", "cite_certified"):
        assert not score(item, {"behaviour": behaviour})[0]


@pytest.mark.parametrize("item", FIGURES, ids=lambda item: item["id"])
def test_periodless_figures_match_the_existing_figure_path(eng, monkeypatch, item):
    question = item["question"]
    req = parse(question, eng.term_names, eng)
    assert req.terms_found and req.period is None
    assert not catalogue_question(question, req.terms_found)
    callers = [lambda cfg=cfg: eng.answer(question, WHO, config=cfg) for cfg in PRESETS.values()]
    callers += [lambda model=model: Combined(eng, llm=Stub() if model else None).ask(question, WHO)
                for model in (False, True)]
    for call in callers:
        actual = call()
        assert actual.get("reason") != CATALOGUE_ABSTENTION
        with monkeypatch.context() as mp:
            mp.setattr(core, "catalogue_question", lambda *args: False)
            mp.setattr(combined, "catalogue_question", lambda *args: False)
            expected = call()
        assert actual == expected
    for actual in (eng.answer(question, WHO), Combined(eng, llm=None).ask(question, WHO)):
        ok, why = score(item, actual)
        assert ok, why
    model = Stub()
    Combined(eng, llm=model).ask(question, WHO)
    assert model.prompts


def test_all_45_golden_answers_are_unchanged(eng, monkeypatch):
    """Show that none of the 45 golden questions reach the catalogue gate."""
    golden = sets.items(FIXTURE / "golden.json")
    assert len(golden) == 45
    for item in golden:
        who = item.get("principal") or WHO
        callers = [lambda cfg=cfg: eng.answer(item["question"], who, config=cfg) for cfg in PRESETS.values()]
        callers.append(lambda: Combined(eng, llm=None).ask(item["question"], who))
        for call in callers:
            actual = call()
            with monkeypatch.context() as mp:
                mp.setattr(core, "catalogue_question", lambda *args: False)
                mp.setattr(combined, "catalogue_question", lambda *args: False)
                expected = call()
            assert actual == expected, item["id"]


@pytest.mark.parametrize("item", FIGURES, ids=lambda item: item["id"])
def test_figure_questions_over_ask_answer_assist_and_mcp(client, item):
    body = {"question": item["question"]}
    for path in ("/ask", "/answer", "/assist"):
        response = client.post(path, json=body, headers=client.as_(WHO))
        assert response.status_code == 200, response.text
        actual = response.json()
        assert actual.get("reason") != CATALOGUE_ABSTENTION
        ok, why = score(item, actual)
        assert ok, (path, why)
        if path == "/assist":
            assert actual["source"] == "ask"
    for path, tool in (("/mcp/answer", "ask"), ("/mcp/assist", "assist")):
        actual = mcp_wire(client, path, WHO, tool, body)["structuredContent"]
        assert actual.get("reason") != CATALOGUE_ABSTENTION
        ok, why = score(item, actual)
        assert ok, (path, why)
        if tool == "assist":
            assert actual["source"] == "ask"
