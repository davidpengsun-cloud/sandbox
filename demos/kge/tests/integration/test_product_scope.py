"""Product page and Explore context prefer local answers without blocking readable sources."""
import json

import pytest

from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.wording import worded
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q, Stub
from tests.integration.test_ask_model_period import PeriodStub
from tests.scratch import open_engine, scratch_build

REVENUE = "Total retail revenue for June 2025?"
EXPLORE = ["retail.account", "retail.monthly_revenue"]


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    engine = open_engine(scratch_build(tmp_path_factory.mktemp("product_scope") / "build"))
    yield engine
    engine.close()


def answered(answer, scope, behaviour="figure"):
    assert answer["behaviour"] == behaviour, answer
    assert answer["products"] == scope
    assert "scope" not in answer.get("reason", "").casefold()
    return answer


@pytest.mark.parametrize("model", [False, True])
def test_product_and_explore_figures(eng, model):
    combined = Combined(eng, llm=PeriodStub(eng, 'rules') if model else None)
    for question, scope in [(REVENUE, ["retail.monthly_revenue"]), ("Total revenue for June 2025?", EXPLORE)]:
        answer = combined.ask(question, "analyst_enterprise", products=scope)
        assert answer["behaviour"] == "figure", answer
        assert answer["figure"] == 13287.67
        assert answer["unit"] == "retail" and answer["products"] == scope
    assert answered(combined.ask(REVENUE, "analyst_enterprise", products=["retail.account"]), ["retail.account"])["figure"] == 13287.67
    answered(combined.ask("Total revenue in SME for June 2025?", "analyst_enterprise", products=EXPLORE), EXPLORE)
    answered(combined.ask("Total SME revenue for June 2025?", "analyst_enterprise", products=EXPLORE), EXPLORE)


@pytest.mark.parametrize("product", ["wealth.client_holding", "wealth.holdings", "wealth-holdings"])
def test_scope_is_never_an_access_grant(eng, monkeypatch, product):
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("scope denial read a figure"))
    model = Stub()
    answer = worded(Combined(eng, llm=model).ask(REVENUE, "analyst_retail", products=[product]), eng)
    assert answer["behaviour"] == "refuse"
    assert answer["denied_warehouses"] == ["wealth"]
    assert "may not read" in answer["reason"]
    assert "client_holding" not in json.dumps(answer)
    assert not model.prompts


@pytest.mark.parametrize("model", [False, True])
def test_unknown_scoped_unit_is_context_and_denied_units_still_refuse(eng, monkeypatch, model):
    llm = PeriodStub(eng, 'rules') if model else None
    combined = Combined(eng, llm=llm)
    scope = ["missing.account"]
    answer = answered(combined.ask(REVENUE, "analyst_retail", products=scope), scope)
    assert answer["figure"] == 13287.67
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("access denial read a figure"))
    denied = combined.ask(REVENUE, "analyst_retail", products=[*scope, "wealth.client_holding"])
    assert denied["behaviour"] == "refuse"
    assert denied["denied_warehouses"] == ["wealth"]


def test_access_check_precedes_empty_or_outside_scope(eng):
    for products in [[], ["retail.account"]]:
        answer = Combined(eng, llm=None).ask("Total wealth revenue for June 2025?", "analyst_retail", products=products)
        assert answer["behaviour"] == "refuse", answer
        assert answer["denied_warehouses"] == ["wealth"]


@pytest.mark.parametrize("model", [False, True])
def test_cohort_can_use_required_products_outside_page_context(eng, model):
    combined = Combined(eng, llm=PeriodStub(eng, 'rules') if model else None)
    scope = ["retail.customer_activity", "retail.complaint", "retail.daily_balance", "retail.monthly_revenue"]
    answer = combined.ask(Q, "analyst_retail", products=scope)
    assert answer["behaviour"] == "combined", answer
    assert {f["id"]: f["value"] for f in answer["figures"]} == {"c1": 176, "c2": 3, "m1": 6400.62, "m2": 42.64}
    fallback = answered(combined.ask(Q, "analyst_retail", products=scope[:-1]), scope[:-1], "combined")
    assert fallback["figures"] == answer["figures"]


@pytest.mark.parametrize("question", ["What is the definition of fee income?", "What is the definition of an active customer?"])
def test_definitions_resolve_on_unrelated_product_pages(eng, question):
    combined = Combined(eng, llm=None)
    outside = combined.ask(question, "analyst_retail", products=["retail.account"])
    assert outside["behaviour"] in {"cite_certified", "glossary_definition"}, outside
    product = "retail.monthly_revenue" if "fee income" in question else "retail.customer_activity"
    answer = combined.ask(question, "analyst_retail", products=[product])
    assert answer["behaviour"] in {"cite_certified", "glossary_definition"}, answer


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("scope,behaviour", [(["retail.monthly_revenue"], "figure"), (["retail.account"], "figure"), ([], "figure"), (["wealth.client_holding"], "refuse"), (["missing.account"], "figure")])
def test_scoped_public_ask(client, transport, scope, behaviour):
    who = "analyst_retail.meridian@example.com"
    body = {"question": REVENUE, "products": scope}
    if transport == "http":
        response = client.post("/ask", json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        answer = response.json()
    else:
        result = mcp_wire(client, "/mcp/answer", who, "ask", body)
        assert not result.get("isError"), result
        answer = result["structuredContent"]
    assert answer["behaviour"] == behaviour, answer
    assert "answer_text" in answer
    if behaviour != "refuse":
        assert answer["products"] == scope
    if behaviour == "figure":
        assert answer["figure"] == 13287.67


@pytest.mark.parametrize("model", [False, True])
def test_context_fact_scope_preserves_physical_table_case(eng, model):
    combined = Combined(eng, llm=PeriodStub(eng, 'rules') if model else None)
    question = "NNA for March 2025 as reported"
    scope = ["wealth.TRAN_FACT_1"]
    answer = combined.ask(question, "analyst_enterprise", products=scope)
    assert answer["behaviour"] == "figure", answer
    assert answer["figure"] == 5637481.28
    assert answer["products"] == ["wealth.tran_fact_1"]
    assert answered(combined.ask(question, "analyst_enterprise", products=["wealth.HLDG_FACT_1"]), ["wealth.hldg_fact_1"])["figure"] == 5637481.28


def test_definition_without_certified_implementation_uses_certified_text_on_any_page(eng, monkeypatch):
    monkeypatch.setattr(eng.g, "implementations", lambda term: [])
    question = "What is the definition of fee income?"
    outside = Combined(eng, llm=None).ask(question, "analyst_retail", products=["retail.account"])
    assert outside["behaviour"] == "cite_certified", outside
    answer = worded(Combined(eng, llm=None).ask(question, "analyst_retail", products=["retail.monthly_revenue"]), eng)
    assert answer["behaviour"] == "cite_certified", answer
    definitions = answer["certified_definitions"]
    assert any(d["term"] == "Fee Income" and d["definition"] in answer["answer_text"] for d in definitions)


@pytest.mark.parametrize("transport", ["ask", "answer", "assist", "mcp"])
def test_a_portal_only_document_set_abstains_on_every_transport(client, transport):
    who = "analyst_retail.meridian@example.com"
    body = {"question": REVENUE, "products": ["retail.document_set"]}
    if transport == "mcp":
        result = mcp_wire(client, "/mcp/answer", who, "ask", body)
        assert not result.get("isError"), result
        answer = result["structuredContent"]
    else:
        response = client.post(f"/{transport}", json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        answer = response.json()
    assert answer["behaviour"] == "abstain", answer
    assert answer.get("figure") != 13287.67
    assert "13287.67" not in json.dumps(answer.get("figures") or [])
    assert "Not in the knowledge graph" in answer.get("reason", "")


def test_an_unscoped_revenue_question_still_answers(eng):
    answer = Combined(eng, llm=None).ask(REVENUE, "analyst_enterprise")
    assert answer.get("figure") == 13287.67
    scoped = Combined(eng, llm=None).ask(REVENUE, "analyst_enterprise", products=["retail.document_set"])
    assert scoped["behaviour"] == "abstain", scoped
    assert scoped.get("figure") != 13287.67
    unknown = Combined(eng, llm=None).ask(REVENUE, "analyst_retail", products=["missing.account"])
    assert unknown["behaviour"] == "figure", unknown
    assert unknown["figure"] == 13287.67


@pytest.mark.parametrize("question", ["What is total revenue?", "What is the definition of active customer?"])
def test_definition_citations_stay_within_product_units(eng, question):
    product = "retail.monthly_revenue" if "revenue" in question else "retail.customer_activity"
    for answer in (eng.answer(question, "analyst_enterprise", products=[product]),
                   Combined(eng, llm=None).ask(question, "analyst_enterprise", products=[product])):
        assert answer["behaviour"] in {"cite_certified", "glossary_definition"}, answer
        docs = eng.g.certification(answer.get("cite") or [])
        assert all(d["unit"] in {"", "retail"} for d in docs.values())
        assert "DD-CORPORATE" not in answer.get("cite", [])
