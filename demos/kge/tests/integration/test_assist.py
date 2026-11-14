"""Assist selects readable evidence and preserves governed answers over both transports."""
from pathlib import Path

import pytest

from kge.eval import sets
from kge.eval.run_assist import HELD_OUT, evaluate_suite
from kge.eval.run_golden import asked_as
from kge.services.ask import Combined, normalise_question, parse
from kge.services.assist import service
from tests.integration.test_answer_text import client as client, mcp_wire

DEMO = Path(__file__).resolve().parents[2]
GOLDEN = sets.items(DEMO / "clients/meridian/fixture/golden.json")
CATALOGUE = sets.catalogue(DEMO / "clients/meridian/fixture/golden.json")
WHO = "analyst_enterprise.meridian@example.com"


class StandInRouter:
    provider = "vertex-gemini"
    name = "vertex-gemini:standin"
    EXPECTED = {item["question"]: item["source"] for item in HELD_OUT}

    def __init__(self, engine, choice=None):
        self.engine = engine
        self.choice = choice
        self.prompts = []

    def json(self, prompt, max_tokens=200):
        self.prompts.append(prompt)
        if isinstance(self.choice, BaseException):
            raise self.choice
        if callable(self.choice):
            return self.choice(prompt)
        if self.choice is not None:
            return self.choice
        question = prompt.rsplit("Question: ", 1)[-1]
        if question in self.EXPECTED:
            return {"source": self.EXPECTED[question], "figure": 999999}
        q = normalise_question(question).casefold()
        req = parse(question, self.engine.term_names, self.engine)
        return {"source": service.regex_source(q, req), "figure": 999999}


def test_deliverable_suite(client, monkeypatch):
    def call(path, item, expect_available=False):
        who = asked_as(item, "analyst_enterprise")
        if "@" not in who:
            who += ".meridian@example.com"
        response = client.post(path, json={"question": item["question"]}, headers=client.as_(who))
        assert response.status_code == 200, response.text
        result = response.json()
        if path == "/assist":
            assert result["assistant_available"] is expect_available
            assert result["notice"] == ("" if expect_available else service.NOTICE)
        return result

    products = client.get("/catalogue", headers=client.as_(WHO)).json()["products"]
    baselines = {}
    def baseline(item):
        result = call("/ask", item)
        baselines[item["id"]] = result
        return result
    report = evaluate_suite(GOLDEN, CATALOGUE, baseline, lambda item: call("/assist", item), products=products)
    assert report == {
        "catalogue_answered": 20,
        "catalogue_figures": 0,
        "golden_unchanged": 45,
        "heldout_routed": 23,
        "heldout_total": 25,
        "heldout_accuracy": 0.92,
        "heldout_batch2_routed": 4,
        "heldout_batch2_total": 15,
        "heldout_batch2_accuracy": 0.2667,
        "unbacked_numbers": 0,
        "failures": [],
        "passed": True,
    }

    import kge.app as appmod
    router = StandInRouter(appmod.engine())
    monkeypatch.setattr(service, "model_for", lambda agent, step=None: router if agent == "assist" else None)
    model_report = evaluate_suite(
        GOLDEN,
        CATALOGUE,
        lambda item: baselines[item["id"]],
        lambda item: call("/assist", item, expect_available=True),
        products=products,
    )
    assert model_report == {
        **report,
        "heldout_routed": 25,
        "heldout_accuracy": 1.0,
        "heldout_batch2_routed": 15,
        "heldout_batch2_accuracy": 1.0,
    }
    assert len(router.prompts) == len(GOLDEN) + len(CATALOGUE) + len(HELD_OUT)


def test_model_routing_chooses_source_without_stating_figures_and_503s_on_failure(client, monkeypatch):
    import kge.app as appmod
    from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded
    from kge.adapters.model import NotJSON

    eng = appmod.engine()
    for chosen, question, expected_behaviour in (
        ("catalogue", "Please share the details of the Monthly Revenue table.", "catalogue"),
        ("search", "Point me to customer email information.", "search"),
        ("ask", "Total retail revenue for June 2025", "figure"),
    ):
        router = StandInRouter(eng, choice={"source": chosen, "figure": 777777, "answer_text": "777777"})
        monkeypatch.setattr(service, "model_for", lambda agent, step=None, r=router: r if agent == "assist" else None)
        out = client.post("/assist", json={"question": question}, headers=client.as_(WHO)).json()
        assert out["source"] == chosen and out["behaviour"] == expected_behaviour, out
        assert out["assistant_available"] is True and out["notice"] == ""
        assert "777777" not in str(out)
        if chosen != "ask":
            assert out["figure"] is None and out["figures"] == []

    for failure in (
        RuntimeError("provider down"),
        ExternalCallError("Vertex AI error (HTTP 503): unavailable"),
        NotJSON("model reply held no JSON object"),
        {"source": "regex"},
        RequestDeadlineExceeded(),
    ):
        router = StandInRouter(eng, choice=failure)
        monkeypatch.setattr(service, "model_for", lambda agent, step=None, r=router: r if agent == "assist" else None)
        response = client.post("/assist", json={"question": "Who owns the Monthly Revenue product?"}, headers=client.as_(WHO))
        assert response.status_code == 503, response.text


@pytest.mark.parametrize("question", ["Certify the Monthly Revenue product", "Onboard a new table",
    "Approve the steward suggestion", "Grant access to retail", "Please delete the Customer product"])
def test_read_only(client, question):
    out = client.post("/assist", json={"question": question}, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "abstain"
    assert out["source"] == "catalogue"
    assert "read-only" in out["reason"]
    assert "Ask the data steward" not in out["answer_text"]
    assert out["figure"] is None and out["figures"] == []


@pytest.mark.parametrize("question", ["Is the retail Customer product certified?", "Has Monthly Revenue been signed off?",
    "Who runs Monthly Revenue?", "Who do I ask about Customer?", "Is Monthly Revenue reliable?",
    "What's the structure of the Account table?", "Which fields are available on Account?",
    "How up to date is the Daily Balance product?", "Is Daily Balance updated daily?",
    "What is the source of the Monthly Revenue product?", "Who is the point of contact for Daily Balance?",
    "Who owns Monthly Revenue for June 2025?", "When was Daily Balance last refreshed in June 2025?",
    "Give me an overview of the Account table by field."])
def test_status_and_paraphrases_are_read_only_catalogue(client, question):
    out = client.post("/assist", json={"question": question}, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "catalogue", out
    assert out["figure"] is None and out.get("parts") is None
    if "Daily Balance" in question:
        assert [s["id"] for s in out["sources"]] == ["retail.daily_balance"]


def test_discovery_mention_and_unit_filter(client):
    out = client.post("/assist", json={"question": "Which tables mention household?"}, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "search" and out["sources"], out
    retail_only = client.post("/assist", json={"question": "Where can I find balance in retail?"}, headers=client.as_(WHO)).json()
    assert retail_only["behaviour"] == "search" and retail_only["sources"]
    assert all(s["unit"] == "retail" for s in retail_only["sources"])


@pytest.mark.parametrize("question,products,behaviour", [
    ("Who owns the corporate Contact product?", None, "refuse"),
    ("Who owns this product?", ["corporate.contact"], "refuse"),
    ("Who owns Customer?", [], "catalogue"),
    ("Who owns Account?", ["retail.customer"], "catalogue"),
    ("Who owns the Unicorn product?", None, "abstain"),
    ("Who owns the Quarterly Fee Revenue product?", None, "abstain"),
    ("Who is the point of contact for Quarterly Fee Revenue?", None, "abstain"),
    ("How up to date is the Quarterly Fee Revenue product?", None, "abstain"),
    ("What is the source of the Quarterly Fee Revenue product?", None, "abstain"),
])
def test_access_and_scope(client, question, products, behaviour):
    body = {"question": question}
    if products is not None:
        body["products"] = products
    out = client.post("/assist", json=body, headers=client.as_("analyst_retail.meridian@example.com")).json()
    assert out["behaviour"] == behaviour, out
    assert bool(out["sources"]) is (behaviour == "catalogue")
    assert "monthly revenue" not in out["answer_text"].lower()
    if behaviour == "refuse":
        assert out["reason"] == "The requested catalogue is not readable. No warehouse was queried."
        assert "corporate" not in out["answer_text"].lower()


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question,products,behaviour", [
    ("Who owns this product?", [], "abstain"),
    ("Who owns this product?", ["missing.account"], "abstain"),
    ("Who owns this product?", ["corporate.contact"], "refuse"),
    ("Who owns this product?", ["missing.account", "corporate.contact"], "refuse"),
    ("Which products contain a balance column?", [], "search"),
    ("Which products contain a balance column?", ["missing.account"], "search"),
    ("Which products contain a balance column?", ["corporate.contact"], "refuse"),
    ("Which products contain a balance column?", ["missing.account", "corporate.contact"], "refuse"),
    ("Delete this product.", [], "abstain"),
    ("Delete this product.", ["missing.account"], "abstain"),
    ("Delete this product.", ["corporate.contact"], "refuse"),
    ("Delete this product.", ["missing.account", "corporate.contact"], "refuse"),
    ("Total retail revenue for June 2025?", [], "figure"),
    ("Total retail revenue for June 2025?", ["missing.account"], "figure"),
    ("Total retail revenue for June 2025?", ["corporate.contact"], "refuse"),
    ("Total retail revenue for June 2025?", ["missing.account", "corporate.contact"], "refuse"),
])
def test_unknown_and_unreadable_product_scopes_on_both_transports(client, transport, question, products, behaviour):
    who = "analyst_retail.meridian@example.com"
    body = {"question": question, "products": products}
    if transport == "http":
        response = client.post("/assist", json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        out = response.json()
    else:
        result = mcp_wire(client, "/mcp/assist", who, "assist", body)
        assert not result.get("isError"), result
        out = result["structuredContent"]
    assert out["behaviour"] == behaviour, out
    if behaviour != "figure":
        assert not out.get("figure") and not out.get("figures") and not out.get("sql")
    if behaviour in {"abstain", "refuse"}:
        assert out["sources"] == []
    if behaviour != "refuse":
        assert out["products"] == products
        assert "may not read" not in (out.get("reason") or "")
        assert not out.get("denied_warehouses")
    else:
        assert out["denied_warehouses"] == ["corporate"]
        assert "corporate.contact" not in out["products"]


@pytest.mark.parametrize("products", [None, [], ["missing.account"], ["retail.missing"]])
@pytest.mark.parametrize("question,source", [
    ("Who owns this product?", "catalogue"),
    ("Describe the selected table.", "catalogue"),
    ("Which fields are on the current data asset?", "ask"),
    ("Which fields are available on the current data asset?", "catalogue"),
])
def test_implicit_product_needs_known_context(client, products, question, source):
    body = {"question": question}
    if products is not None:
        body["products"] = products
    response = client.post("/assist", json=body, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["behaviour"] == "abstain", out
    assert out["source"] == source
    assert out["sources"] == []
    assert out.get("figure") is None
    if source == "catalogue":
        assert "product is not in the readable catalogue" in out["reason"]


def test_in_service_querying_and_withholding_guards(client, monkeypatch):
    import kge.app as appmod
    c = appmod.combined()
    cfg = c.default_config("graph")
    monkeypatch.setattr(c.e.g, "may", lambda who, proc: False)
    refused = service.assist(c, appmod.PROFILE, "Who owns Account?", WHO, "analysis", "graph", cfg)
    assert refused["behaviour"] == "refuse" and "not a querying identity" in refused["reason"]
    monkeypatch.undo()
    monkeypatch.setattr(service, "catalogue_text", lambda *args: "From the catalogue: corporate.counterparty")
    out = service.assist(c, appmod.PROFILE, "Who owns Account?", "analyst_retail.meridian@example.com", "analysis", "graph", cfg)
    assert out["behaviour"] == "catalogue" and "corporate" not in out["answer_text"].lower()


def test_mcp_matches_http(client):
    body = {"question": CATALOGUE[0]["question"]}
    http = client.post("/assist", json=body, headers=client.as_(WHO)).json()
    assert http["source"] == "catalogue"
    mcp = mcp_wire(client, "/mcp/assist", WHO, "assist", body)["structuredContent"]
    assert mcp["answer_text"] == http["answer_text"]
    assert mcp["sources"] == http["sources"]


def test_combined_figure_sources_and_no_fallback_on_external_errors(client, monkeypatch):
    import kge.app as appmod
    from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded
    from kge.adapters.model import NoFallback
    for q, has_sources in (("Total revenue for June 2025?", True), ("What was net interest income in June 2025?", True),
                           ("How many customers do we have?", False), ("Show me revenue by product.", False),
                           ("Retail revenue for June 2025 excluding unrecognized", False)):
        out = client.post("/assist", json={"question": q}, headers=client.as_(WHO)).json()
        assert out["source"] == "ask" and bool(out["sources"]) is has_sources, (q, out)
        if not has_sources:
            assert out["behaviour"] == "abstain" and not out.get("figure") and not out.get("result"), (q, out)
    orig_ask = service.ask
    combined = Combined(appmod.engine(), llm=StandInRouter(appmod.engine()))
    monkeypatch.setattr(appmod, "_combined", combined)
    for err in (NoFallback("vertex unavailable"), ExternalCallError("store busy"), RequestDeadlineExceeded()):
        def fail_when_llm(c, *args, exc=err, **kwargs):
            if c.llm is not None:
                raise exc
            return orig_ask(c, *args, **kwargs)
        monkeypatch.setattr(service, "ask", fail_when_llm)
        assert client.post("/assist", json={"question": GOLDEN[0]["question"]}, headers=client.as_(WHO)).status_code == 503


@pytest.mark.parametrize("index,expected", [
    (0, ["Monthly Revenue", "Data domain owner:", "Data steward:"]),
    (1, ["retail.customer", "customer_id", "Fields:"]),
    (2, ["email"]),
    (3, ["retail."]),
    (4, ["Net New Assets", "Fields:"]),
    (5, ["nii (Net Interest Income)"]),
    (6, ["complaint"]),
    (7, ["corporate.contact", "Last refresh:", "cadence"]),
    (8, ["retail.account", "Quality score:"]),
    (9, ["balance"]),
    (10, ["retail.customer", "Certified"]),
    (11, ["corporate.exposure", "Fields:"]),
    (12, ["Quarterly Fee Revenue", "Data domain owner:"]),
    (13, ["wealth."]),
    (14, ["retail.monthly_revenue"]),
    (15, ["retail.account", "retail.customer", "Declared joins:"]),
    (16, ["Daily Balance", "Last refresh:"]),
    (17, ["Monthly Revenue", "Customer", "Facility"]),
    (18, ["total_revenue_usd"]),
    (19, ["sme.facility", "Data steward:"]),
])
def test_catalogue_answers_name_the_requested_evidence(client, index, expected):
    out = client.post("/assist", json={"question": CATALOGUE[index]["question"]}, headers=client.as_(WHO)).json()
    for text in expected:
        assert text.casefold() in out["answer_text"].casefold(), out
    assert out["source"] == ("search" if index in {2, 5, 6, 9, 13, 14, 18} else "catalogue")
    assert out.get("parts") is None and out.get("rows") is None


@pytest.mark.parametrize("question", ["What products are in Unicorn Banking?", "Describe the Unicorn table."])
def test_unknown_subject_never_lists_the_catalogue(client, question):
    out = client.post("/assist", json={"question": question}, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "abstain", out
    assert out["sources"] == []


@pytest.mark.parametrize("question", ["Which products have the highest revenue?", "Which table has the most customers?",
    "Which products have revenue above 1000 in June 2025?", "Give me an overview of revenue by segment.",
    "What does the retail product earn?", "Who manages the most revenue in June 2025?",
    "Who is responsible for revenue growth in June 2025?"])
def test_discovery_aggregate_stays_governed(client, question):
    out = client.post("/assist", json={"question": question}, headers=client.as_(WHO)).json()
    assert out["source"] == "ask", out


def test_search_scope_and_model_availability(client, monkeypatch):
    import kge.app as appmod
    monkeypatch.setattr(appmod, "_combined", Combined(appmod.engine(), llm=StandInRouter(appmod.engine())))
    body = {"question": "Which products contain a balance column?", "products": ["retail.account"]}
    out = client.post("/assist", json=body, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "search", out
    assert out["assistant_available"] is True and out["notice"] == ""
    assert out["products"] == body["products"]
    assert all(s["id"] == "retail.account" or s["id"].startswith("retail.account.") for s in out["sources"])
    body["question"] = "Where can I find corporate Contact data?"
    out = client.post("/assist", json=body, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "search", out
    assert any(s["id"] == "corporate.contact" for s in out["sources"])


def test_api_evaluation_command(client, monkeypatch, capsys):
    import io
    import json
    from urllib.parse import urlparse
    import kge.app as appmod
    from kge.eval import run_assist

    def urlopen(request, **kwargs):
        path = urlparse(request.full_url).path
        response = client.request(request.get_method(), path, content=request.data, headers=dict(request.header_items()))
        assert response.status_code == 200, response.text
        return io.StringIO(response.text)

    monkeypatch.setattr(run_assist.http, "open_http", urlopen)
    monkeypatch.setattr(run_assist, "resolve", lambda _: appmod.PROFILE)
    monkeypatch.setattr("sys.argv", ["run_assist", "--api", "http://testserver", "--as", WHO])
    assert run_assist.main() == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_shared_product_name_uses_the_product_view(client):
    body = {"question": "Who owns Customer?", "products": ["retail.customer"]}
    out = client.post("/assist", json=body, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "catalogue", out
    assert [s["id"] for s in out["sources"]] == ["retail.customer"]
    body["question"] = "How do I join retail Customer to Account?"
    out = client.post("/assist", json=body, headers=client.as_(WHO)).json()
    assert out["behaviour"] == "catalogue", out
    assert {s["id"] for s in out["sources"]} == {"retail.customer", "retail.account"}
    assert "retail.account.customer_id to retail.customer.customer_id" in out["answer_text"]
    assert "none recorded in scope" not in out["answer_text"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_qualified_same_named_product_is_not_hidden_by_the_page(client, transport):
    body = {"question": "How do I join retail.calendar_month to sme.calendar_month?",
            "products": ["retail.calendar_month"]}
    if transport == "http":
        response = client.post("/assist", json=body, headers=client.as_(WHO))
        assert response.status_code == 200, response.text
        out = response.json()
    else:
        result = mcp_wire(client, "/mcp/assist", WHO, "assist", body)
        assert not result.get("isError"), result
        out = result["structuredContent"]
    assert out["behaviour"] == "catalogue", out
    assert {s["id"] for s in out["sources"]} == {"retail.calendar_month", "sme.calendar_month"}
    assert "retail.calendar_month" in out["answer_text"]
    assert "sme.calendar_month" in out["answer_text"]


def test_product_page_join_question_uses_readable_endpoints(client):
    body = {"question": "How do I join this product?", "products": ["retail.account"]}
    out = client.post("/assist", json=body, headers=client.as_("analyst_retail.meridian@example.com")).json()
    assert out["behaviour"] == "catalogue", out
    assert [s["id"] for s in out["sources"]] == ["retail.account"]
    assert "retail.account.customer_id to retail.customer.customer_id" in out["answer_text"]
    assert "none recorded in scope" not in out["answer_text"]
    assert "corporate" not in out["answer_text"].lower()


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("endpoint", ["ask", "assist"])
@pytest.mark.parametrize("question,behaviour", [("what is NNA", "cite_certified"),
                                                ("NNA for March 2025 as reported", "figure")])
def test_nna_answers_from_supporting_asset_page(client, transport, endpoint, question, behaviour):
    body = {"question": question, "products": ["wealth.F_NNA_FIN"]}
    who = "advisor_wealth.meridian@example.com"
    if transport == "http":
        response = client.post("/" + endpoint, json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        out = response.json()
    else:
        mount = "/mcp/answer" if endpoint == "ask" else "/mcp/assist"
        result = mcp_wire(client, mount, who, endpoint, body)
        assert not result.get("isError"), result
        out = result["structuredContent"]
    assert out["behaviour"] == behaviour, out
    assert out["products"] == ["wealth.f_nna_fin"]
    assert out["certified_definitions"]
    if behaviour == "figure":
        assert out["figure"] == 5637481.28
        assert "wealth.TRAN_FACT_1" in out["answer_text"]
    else:
        assert "MD-NET-NEW-ASSETS" in out["cite"]
        assert out["certified_definitions"][0]["term"] == "Net New Assets"


@pytest.mark.parametrize("provider", ["none", "mock"])
@pytest.mark.parametrize("narrow", [False, True])
@pytest.mark.parametrize("product,unit,expected", [
    ("wealth.TRAN_FACT_1", "wealth", [
        "wealth.TRAN_FACT_1.DW_TRAN_TYPE_CD to wealth.TRAN_TYPE_1.DW_TRAN_TYPE_CD",
        "wealth.TRAN_FACT_1.ACCT_SRCE_SYS_ID to wealth.SRCE_SYS_1.SRCE_SYS_ID",
        "wealth.TRAN_FACT_1.MNTH_END_DT to wealth.MONTH_DIM_1.CLDR_EOM_DT",
    ]),
    ("retail.monthly_revenue", "retail", [
        "retail.monthly_revenue.month_start to retail.calendar_month.month_start",
        "retail.monthly_revenue.account_id to retail.account.account_id",
    ]),
    ("wealth.BRCH_DIM_1", "wealth", []),
])
def test_product_scoped_declared_joins_on_rules_and_mock_model(client, monkeypatch, provider, narrow, product, unit, expected):
    import kge.app as appmod

    for name in ("_combined", "_agent"):
        monkeypatch.setattr(appmod, name, None)
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    router = StandInRouter(appmod.engine()) if provider == "mock" else None
    monkeypatch.setattr(service, "model_for", lambda agent: router)
    who = (f"{'advisor_wealth' if unit == 'wealth' else 'analyst_retail'}.meridian@example.com" if narrow else WHO)
    body = {"question": "How does this table join to other tables?", "products": [product]}
    response = client.post("/assist", json=body, headers=client.as_(who))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["source"] == out["behaviour"] == "catalogue", out
    assert out["assistant_available"] is (provider == "mock")
    assert out["products"] == [product.lower()]
    assert [source["id"] for source in out["sources"]] == [product]
    assert out["figure"] is None and out["figures"] == []
    declared = out["answer_text"].split("Declared joins: ", 1)[1].removesuffix(".")
    assert set(declared.split("; ")) == set(expected or ["none recorded in scope"])
    if router is not None:
        assert len(router.prompts) == 1
        assert body["question"] in router.prompts[0]


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_product_scoped_joins_hide_counterparts_outside_the_readable_catalogue(client, monkeypatch, transport):
    get = service.catalogue.get

    def without_counterparts(profile, readable):
        catalogue = get(profile, readable)
        return catalogue.model_copy(update={"products": [p for p in catalogue.products
                                    if (p.unit_id, p.table) not in {("retail", "account"), ("retail", "calendar_month")}]})

    monkeypatch.setattr(service.catalogue, "get", without_counterparts)
    who = "analyst_retail.meridian@example.com"
    body = {"question": "How does this table join to other tables?", "products": ["retail.monthly_revenue"]}
    if transport == "http":
        response = client.post("/assist", json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        out = response.json()
    else:
        result = mcp_wire(client, "/mcp/assist", who, "assist", body)
        assert not result.get("isError"), result
        out = result["structuredContent"]
    assert out["source"] == out["behaviour"] == "catalogue", out
    assert "Declared joins: none recorded in scope." in out["answer_text"]
    assert [s["id"] for s in out["sources"]] == ["retail.monthly_revenue"]
    assert "account" not in str(out).casefold()
    assert "calendar_month" not in str(out).casefold()
