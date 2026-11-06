"""Assist contract and unit helpers run without a built graph."""
from types import SimpleNamespace

import pytest

from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded
from kge.adapters.model import NoFallback, NotJSON, PREFERRED_TIER, model_for
from kge.eval.run_assist import HELD_OUT, evaluate_suite
from kge.services.ask import PRESETS
from kge.services.assist.models import Assist, AssistResponse
from kge.services.assist.service import (
    NOTICE,
    ask_sources,
    catalogue_text,
    description,
    named,
    route_question,
    routing_model,
    rules_config,
)


@pytest.mark.parametrize("readable,expected", [
    (None, "none recorded in scope"),
    ({"u.a", "u.b", "u.c", "u.d"}, "u.a.id to u.b.a_id"),
    ({"u.a"}, "none recorded in scope"),
    ({"u.b"}, "none recorded in scope"),
    (set(), "none recorded in scope"),
])
@pytest.mark.parametrize("table", ["a", "b"])
def test_catalogue_joins_touch_either_scoped_endpoint_and_keep_the_unscoped_visible_filter(readable, expected, table):
    joins = [{"ft": "a", "fc": "id", "tt": "b", "tc": "a_id"},
             {"ft": "c", "fc": "id", "tt": "d", "tc": "c_id"}]
    engine = SimpleNamespace(g=SimpleNamespace(unit_joins=lambda unit: joins))
    product = {"name": table, "id": f"u.{table}", "unit_id": "u", "table": table}
    assert catalogue_text(engine, "How does this table join?", [product], {product["id"]}, readable) == (
        f"From the catalogue: {table} (u.{table}). Declared joins: {expected}."
    )
    assert catalogue_text(engine, "How does this table join?", [product], {"u.a", "u.b"}) == (
        f"From the catalogue: {table} (u.{table}). Declared joins: u.a.id to u.b.a_id."
    )


def test_assist_models_and_notice():
    req = Assist(question="Who owns Customer?")
    assert req.arm == "graph" and req.purpose == "analysis" and req.products is None
    props = AssistResponse.model_json_schema()["properties"]
    assert {"source", "sources", "assistant_available", "notice", "term", "citations", "reason", "products"} <= set(props)
    assert NOTICE == "The assistant is unavailable; showing governed answers."
    assert PREFERRED_TIER["assist"] == "low"
    assert len(HELD_OUT) == 40
    assert [i.get("batch", 1) for i in HELD_OUT].count(1) == 25
    assert [i.get("batch") for i in HELD_OUT].count(2) == 15


def test_assist_model_for_and_routing_failure_raises_no_fallback(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "default-router")
    monkeypatch.setenv("MODEL_LOW_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_LOW_NAME", "low-router")
    assert model_for("assist").name == "openai:low-router"

    class Router:
        provider = "openai"

        def __init__(self, reply):
            self.reply = reply
            self.prompts = []

        def json(self, prompt, max_tokens=200):
            self.prompts.append((prompt, max_tokens))
            if isinstance(self.reply, BaseException):
                raise self.reply
            return self.reply

    for expected in ("catalogue", "search", "ask"):
        router = Router({"source": expected, "figure": 99999, "sql": "SELECT 99999"})
        assert route_question(router, "Who owns Customer?") == expected
        assert len(router.prompts) == 1

    for bad in (
        RuntimeError("token sk-secret-value refused"),
        ExternalCallError("Vertex AI error (HTTP 503): unavailable"),
        NotJSON("model reply held no JSON object"),
        {"source": "warehouse"},
        "not-a-dict",
    ):
        with pytest.raises(NoFallback) as caught:
            route_question(Router(bad), "Who owns Customer?")
        assert "sk-secret-value" not in str(caught.value)

    with pytest.raises(RequestDeadlineExceeded):
        route_question(Router(RequestDeadlineExceeded()), "Who owns Customer?")

    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.delenv("MODEL_LOW_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_LOW_NAME", raising=False)
    assert routing_model(type("CombinedStub", (), {"llm": None})()) is None


def test_rules_config_and_text_helpers():
    cfg = rules_config(PRESETS["graph"])
    assert all(not v.endswith("+model") and v != "model" for v in cfg.stages().values())
    assert description("Customer dimension. Holds 42 records.") == "Customer dimension."
    products = [
        {"id": "retail.account", "name": "Account", "table": "account"},
        {"id": "retail.daily_balance", "name": "Daily Balance", "table": "daily_balance"},
        {"id": "corporate.contact", "name": "Contact", "table": "contact"},
        {"id": "retail.monthly_revenue", "name": "Monthly Revenue", "table": "monthly_revenue"},
    ]
    assert [p["id"] for p in named("Compare Account and Monthly Revenue", products)] == [
        "retail.account",
        "retail.monthly_revenue",
    ]
    assert [p["id"] for p in named("Who is the point of contact for Daily Balance?", products)] == [
        "retail.daily_balance",
    ]


def test_ask_sources_extracts_plan_parts_columns_and_citations():
    combined_out = {
        "behaviour": "parts",
        "plan": [
            {"unit": "corporate", "status": "abstain", "column": "quarterly_revenue.revenue_usd_converted"},
            {"unit": "retail", "status": "ok", "column": "monthly_revenue.total_revenue_usd"},
            {"unit": "sme", "status": "ok", "column": "monthly_client_revenue.revenue_usd"},
        ],
        "citations": [{"doc": "MD-REVENUE", "title": "Revenue Definition", "unit": "retail"}],
        "cite": ["MD-REVENUE", "GL-001"],
    }
    sources = ask_sources(combined_out)
    assert [s["id"] for s in sources] == [
        "MD-REVENUE",
        "GL-001",
        "retail.monthly_revenue.total_revenue_usd",
        "sme.monthly_client_revenue.revenue_usd",
    ]
    assert all(s["kind"] == "ask" for s in sources)


def test_evaluation_rejects_missing_ask_sources_and_invented_attributes():
    golden = [{"id": "G001", "question": "Total revenue for June 2025?"}]
    base = {"behaviour": "parts", "answer_text": "Retail 10."}
    no_sources = {**base, "source": "ask", "sources": []}
    report = evaluate_suite(golden, [], lambda _: base, lambda _: no_sources, heldout=())
    assert report["passed"] is False
    assert "G001: missing ask sources" in report["failures"]

    item = {"id": "C09", "question": "What is the quality score?"}
    product = {"unit_id": "u", "table": "t", "quality_score": 93, "fields": []}
    response = {"behaviour": "catalogue", "source": "catalogue", "answer_text": "Quality score: 999.",
                "sources": [{"kind": "catalogue", "id": "u.t", "title": "T"}]}
    report2 = evaluate_suite([], [item], None, lambda _: response, products=[product], heldout=())
    assert report2["unbacked_numbers"] == 1
    assert report2["passed"] is False


def test_evaluation_heldout_threshold_and_zero_catalogue_figures():
    heldout = [{"id": f"H{i}", "source": "catalogue" if i < 8 else "ask", "question": f"Q{i}?"} for i in range(10)]

    def ans_90(item):
        if item["id"] == "H9":
            return {"source": "catalogue", "behaviour": "abstain", "answer_text": "Abstained.", "sources": []}
        if item["source"] == "ask":
            return {"source": "ask", "behaviour": "figure", "figure": 10, "answer_text": "10.", "sources": [{"id": "u.t.c", "title": "c"}]}
        return {"source": "catalogue", "behaviour": "catalogue", "answer_text": "T (u.t).", "sources": [{"id": "u.t", "title": "T"}]}

    ok_report = evaluate_suite([], [], None, ans_90, heldout=heldout)
    assert ok_report["heldout_routed"] == 9 and ok_report["heldout_accuracy"] == 0.9
    assert ok_report["catalogue_figures"] == 0 and ok_report["passed"] is True

    def ans_80(item):
        if item["id"] in ("H8", "H9"):
            return {"source": "catalogue", "behaviour": "abstain", "answer_text": "Abstained.", "sources": []}
        return ans_90(item)

    low_report = evaluate_suite([], [], None, ans_80, heldout=heldout)
    assert low_report["heldout_routed"] == 8 and low_report["heldout_accuracy"] == 0.8
    assert low_report["passed"] is False
    assert any("heldout: routing accuracy 8/10 below 90%" in f for f in low_report["failures"])

    def ans_cat_figure(item):
        if item["id"] == "H0":
            return {"source": "ask", "behaviour": "figure", "figure": 42, "answer_text": "42.", "sources": [{"id": "u.t.c", "title": "c"}]}
        return ans_90(item)

    fig_report = evaluate_suite([], [], None, ans_cat_figure, heldout=heldout)
    assert fig_report["catalogue_figures"] == 1 and fig_report["passed"] is False
    assert "H0: catalogue question returned a figure" in fig_report["failures"]

    batch2 = [*heldout, {"id": "B0", "batch": 2, "source": "search", "question": "B0?"}]
    b2_low = evaluate_suite(
        [],
        [],
        None,
        lambda item: {"source": "ask", "behaviour": "figure", "figure": 7, "answer_text": "7.",
                      "sources": [{"id": "u.t.c", "title": "c"}], "assistant_available": True}
        if item["id"] == "B0"
        else {**ans_90(item), "assistant_available": True},
        heldout=batch2,
    )
    assert b2_low["heldout_batch2_routed"] == 0 and b2_low["heldout_batch2_accuracy"] == 0.0
    assert b2_low["catalogue_figures"] == 1 and b2_low["passed"] is False
    assert any("heldout_batch2: routing accuracy 0/1 below 90%" in f for f in b2_low["failures"])
    assert "B0: catalogue question returned a figure" in b2_low["failures"]
