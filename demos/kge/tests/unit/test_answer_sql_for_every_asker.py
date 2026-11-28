"""An answer's SQL for every role who may ask (#624): a compact answer keeps the answered query's SQL, its own and its
figures', with sql_certified (true for a certified answer, false for an AI-Generated one), and drops the plan, the trace
and every other query; a decline's compact reply carries no SQL. Answers are delivered, never held for a confirm: no
reply carries confirm_first or confirmed_text, and an AI-Generated answer's text carries its figure. A clarification
keeps its choices. Each answer here comes from a synthetic pack with a stub model and a stub rules adapter."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kge.semantics.plan import Propose
from kge.semantics.planvocabulary import Entry
from kge.services.ask import service
from kge.services.ask.engine import contextpath, path as pathmod, planpublic, planrules, wording
from kge.services.ask.engine.core import Engine, PRESETS
from kge.services.ask.engine.question import Request
from kge.services.ask.engine.wording import AI_GENERATED_LABEL, CERTIFIED_LABEL, DECLINES, REFUSALS, Hidden, worded
from kge.services.ask.models import AskResponse
from tests.plan_fixtures import model, metric_vocabulary as vocabulary
from tests.unit.test_planner_jobs import setup
from tests.unit.test_rules_port import Stub, clarifying, public as rules_public, served_by

HELD_FOR_A_CONFIRM = ("confirm_first", "confirmed_text")


class Proposing:
    """A stub model that proposes the synthetic pack's one plan for any question."""
    name = "stub:proposing"

    def json(self, prompt):
        return Propose(kind="propose", base_turn=None, plan=model()).model_dump(mode="json", by_alias=True)


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    """Ask on the synthetic pack (tests/plan_fixtures.py) with its fake warehouse and the stub model, model first, the
    fallback on: answered(rules_agree) is the worded answer, certified where the model's plan is the rules' plan
    (rules_agree) and AI-Generated where it is not."""
    monkeypatch.setenv("ASK_AI_FALLBACK", "on")
    monkeypatch.setenv("ASK_ORDER", "model_first")
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    v = vocabulary()
    sem = v.semantics
    runtime = SimpleNamespace(contexts=v.contexts, mapping_declarations=[], output_metadata={})
    engine = SimpleNamespace(semantics=sem, w=warehouses, UNIT_ORDER=["u"], UNIT_LABEL={"u": "Unit"}, ALIASES={},
                             PERIODS=sem.d.PERIODS, CALENDARS=sem.d.CALENDARS, ACRONYMS={}, term_names=[])
    engine.g = SimpleNamespace(readable_units=lambda principal: {"u"})
    engine.request_abstention = lambda request, reason: Engine.request_abstention(engine, request, reason)
    monkeypatch.setattr(planrules, "vocabulary", lambda *args: v)
    monkeypatch.setattr(planrules, "snapshot", lambda *args: runtime)
    monkeypatch.setattr(planrules, "cohort_terms", lambda *args: [])
    monkeypatch.setattr(Hidden, "of", lambda *args: Hidden([], set(), []))
    monkeypatch.setattr(wording, "certified_definitions", lambda a, e: [])
    monkeypatch.setattr(pathmod, "build_path", lambda e, out: {})
    monkeypatch.setattr(contextpath, "build_graph_path", lambda e, out: {})

    def answered(rules_agree: bool) -> dict:
        monkeypatch.setattr(planpublic, "model_plan_is_rules_plan", lambda *args: rules_agree)
        cfg = PRESETS["graph"].with_stages(resolution="glossary+model")
        out = planpublic.selected_answer(engine, Request("Metric", term="Metric", terms_found=["Metric"]), "reader",
                                         "analysis", cfg, SimpleNamespace(products=None), {"versions": {}, "trace": []},
                                         combined=SimpleNamespace(llm=Proposing()))
        return worded(out, engine)

    yield answered
    fake.con.close()


def sql_strings(value, key=None) -> list[str]:
    """Every string anywhere in value that is a query: each value of a key named sql, sql_run or reference_sql."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in sql_strings(v, k)]
    if isinstance(value, list):
        return [s for item in value for s in sql_strings(item, key)]
    return [value] if key in ("sql", "sql_run", "reference_sql") and isinstance(value, str) else []


@pytest.mark.parametrize("rules_agree,mode,label,certified", [(False, "ai_generated", AI_GENERATED_LABEL, False),
                                                              (True, "certified", CERTIFIED_LABEL, True)])
def test_an_answer_is_delivered_with_its_figure_its_label_and_its_sql_never_held_for_a_confirm(synthetic, rules_agree, mode,
                                                                                               label, certified):
    full = synthetic(rules_agree)
    assert (full["behaviour"], full["answer_mode"], full["figure"]) == ("figure", mode, 15.0)
    for compact in (False, True):
        view = service.response_view(full, compact)
        assert view["answer_text"].startswith(label) and "15" in view["answer_text"], view["answer_text"]
        assert not set(HELD_FOR_A_CONFIRM) & set(view), view
        assert (view["sql"], view["sql_certified"]) == (full["sql"], certified)
        assert AskResponse.model_validate({**view, "question": "Metric", "principal": "reader", "purpose": "analysis",
                                           "arm": "graph", "phrased_by": "template", "combined": False}).sql_certified is certified


def test_a_compact_answers_sql_is_the_answered_query_alone(synthetic):
    full = synthetic(False)
    assert full["sql_run"] and full["guard"], "the full answer carries every query it ran and the gate's verdict"
    compact = service.response_view(full, True)
    assert sql_strings(compact) == [full["sql"]]
    for engineers in ("plan", "trace", "run_trace", "sql_run", "reference_sql", "guard", "path"):
        assert engineers not in compact, engineers


def test_a_compact_combined_answer_keeps_each_figures_sql_and_no_other_query():
    out = {"behaviour": "combined", "answer_mode": "ai_generated", "answer_text": "AI-Generated: Metric 15 [1].",
           "figures": [{"id": "f1", "value": 15, "sql": "SELECT 15", "reference_sql": "SELECT 15 -- reference"}],
           "citations": [{"n": 1, "doc": "D1", "sql": "SELECT 0"}], "graph_path": {"nodes": [{"id": "q", "sql": "SELECT 1"}]},
           "decided": {"sql": "SELECT 2"}, "plan": [{"unit": "u", "sql": "SELECT 3"}], "sql_run": ["SELECT 15", "SELECT 4"]}
    compact = service.response_view(out, True)
    assert sql_strings(compact) == ["SELECT 15"]
    assert compact["sql_certified"] is False


@pytest.mark.parametrize("behaviour", DECLINES)
@pytest.mark.parametrize("mode", ["certified", "ai_generated", "not_sure", None])
def test_a_declines_compact_reply_carries_no_sql(behaviour, mode):
    out = {"behaviour": behaviour, "answer_text": "Not answered.", "reason": "Read is not permitted.", "sql": "SELECT 1",
           "parts": [{"unit": "u", "sql": "SELECT 2"}], "figures": [{"id": "f1", "value": None, "sql": "SELECT 3"}],
           **({"answer_mode": mode} if mode else {})}
    compact = service.response_view(out, True)
    assert sql_strings(compact) == [] and "sql_certified" not in compact, compact
    assert compact["reason"] == out["reason"]
    if behaviour in REFUSALS:
        assert "SELECT" not in json.dumps(compact)


def test_sql_certified_is_given_with_sql_and_an_answer_mode_and_is_true_only_for_a_certified_answer():
    assert service.response_view({"behaviour": "figure", "answer_mode": "not_sure", "sql": "SELECT 1"})["sql_certified"] is False
    not_sure = service.response_view({"behaviour": "figure", "answer_mode": "not_sure", "sql": "SELECT 1"}, True)
    assert (not_sure["sql"], not_sure["sql_certified"]) == ("SELECT 1", False)
    assert "sql_certified" not in service.response_view({"behaviour": "figure", "sql": "SELECT 1"})
    assert "sql_certified" not in service.response_view({"behaviour": "figure", "answer_mode": "certified"})
    assert service.response_view({"behaviour": "figure", "answer_mode": "certified", "sql": "SELECT 1"})["sql_certified"] is True
    stale = {"behaviour": "figure", "answer_mode": "ai_generated", "sql": "SELECT 1", "sql_certified": True}
    assert service.response_view(stale, True)["sql_certified"] is False
    unchanged = {"behaviour": "figure", "answer_mode": "certified", "sql": "SELECT 1", "sql_certified": True}
    assert service.response_view(unchanged) is unchanged


def test_a_clarifications_compact_reply_keeps_its_choices_and_no_sql(monkeypatch):
    v = vocabulary(values=[Entry("one", "value", "First grouping", "u", column="u.events.category", value="A"),
                           Entry("two", "value", "Second grouping", "u", column="u.events.source", value="x")],
                   aliases=[("Office", "one"), ("Office", "two")])
    served_by(monkeypatch, Stub(clarifying))
    full = rules_public(monkeypatch, v, Request("Office"))
    assert full["behaviour"] == "clarify"
    compact = service.response_view(full, True)
    assert compact["behaviour"] == "clarify"
    assert compact["clarification"]["choices"] == full["clarification"]["choices"]
    assert {c["id"] for c in compact["clarification"]["choices"]} == {"one", "two"}
    assert sql_strings(compact) == [] and not set(HELD_FOR_A_CONFIRM) & set(compact)
