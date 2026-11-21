"""ASK_ORDER is reported (#464): GET /health's ask field names the order in effect, and every planned answer carries the
order its planner ran as a field of its own, which a compact answer keeps though it drops the trace."""
from types import SimpleNamespace

import pytest

from kge.services.ask import service
from kge.services.ask.models import AskResponse
from kge.services.tools import service as tools


@pytest.mark.parametrize("order", ["rules_first", "model_first"])
def test_health_names_the_order_in_effect_where_ask_has_a_model(monkeypatch, order):
    import kge.adapters.model as models
    monkeypatch.setenv("ASK_ORDER", order)
    monkeypatch.setattr(models, "model_for", lambda agent: SimpleNamespace(name="mock:planner"))
    assert tools.ask_health() == {"ai_fallback": "on", "order": order, "model": "mock:planner"}


def test_health_names_rules_first_where_ask_has_no_model(monkeypatch):
    import kge.adapters.model as models
    monkeypatch.setenv("ASK_ORDER", "model_first")
    monkeypatch.setattr(models, "model_for", lambda agent: None)
    assert tools.ask_health() == {"ai_fallback": "off", "order": "rules_first", "model": "none"}


def test_health_names_rules_first_where_the_fallback_is_off_though_ask_has_a_model(monkeypatch):
    """With the fallback off, Ask plans without its model (planpublic), so every answer runs, and records, rules_first:
    health says so, not ASK_ORDER's model_first (#553)."""
    import kge.adapters.model as models
    monkeypatch.setenv("ASK_ORDER", "model_first")
    monkeypatch.setenv("ASK_AI_FALLBACK", "off")
    monkeypatch.setattr(models, "model_for", lambda agent: SimpleNamespace(name="mock:planner"))
    assert tools.ask_health() == {"ai_fallback": "off", "order": "rules_first", "model": "mock:planner"}


def planned(order="model_first"):
    return {"question": "q", "principal": "p", "purpose": "analysis", "arm": "graph", "behaviour": "figure",
            "combined": False, "answer_mode": "ai_generated", "answer_text": "AI-Generated: NNA: 15.",
            "trace": [{"step": "understand"}, {"step": "plan", "order": order, "answer_mode": "ai_generated"}]}


@pytest.mark.parametrize("order", ["rules_first", "model_first"])
def test_an_answer_carries_its_plan_steps_order_and_compact_keeps_it(order):
    out = service.ordered(planned(order))
    assert out["order"] == order
    compact = service.response_view(out, compact=True)
    assert "trace" not in compact and compact["order"] == order
    assert AskResponse.model_fields["order"].annotation.__args__[0].__args__ == ("rules_first", "model_first")


def test_an_answer_with_no_plan_step_has_no_order():
    assert "order" not in service.ordered({"behaviour": "refuse", "trace": [{"step": "access"}]})
