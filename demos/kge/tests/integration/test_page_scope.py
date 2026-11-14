"""Every certified term's implementing and supporting asset pages can answer definitions and figures."""
from dataclasses import replace
import json

import pytest

from kge.eval import page_scope
from kge.services.ask.engine import planner, planrules, planpublic
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.scope import ProductScope
from kge.semantics.plan import HistoryEntry
from tests.integration.test_combined import Stub
from kge.services.ask.engine.combined import Combined
from kge.services.assist import assist
from kge.services.ask.engine.wording import worded
from tests.scratch import open_engine, scratch_build


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    engine = open_engine(scratch_build(tmp_path_factory.mktemp("page_scope") / "build"))
    yield engine
    engine.close()


def test_generated_asset_page_evaluation(eng):
    artifact = eng.profile.artifacts / eng.profile.client / eng.profile.model_hash()
    items = page_scope.questions(artifact)
    assert items
    known_tables = {f"{unit}.{table}" for unit, tables in eng.g.catalogue().tables.items() for table in tables}
    assert all(product in known_tables for item in items for product in item["products"])
    assert len(items) == 176
    pairs = {(item["term"], item["products"][0]) for item in items}
    expected = {(term, f"{impl['unit']}.{impl['tbl']}") for term in eng.g.terms() for impl in eng.g.implementations(term)}
    for context in eng.semantics.contexts.values():
        expected.add((context.term, context.fact))
        for join in context.joins:
            expected.update((context.term, table) for table in (join.source, join.target, join.through) if table)
    assert expected <= pairs
    assert len({item["id"] for item in items}) == len(items)
    assert all({item["kind"] for item in items if item["products"] == products and item["term"] == term} == {"definition", "figure"}
               for products, term in [(item["products"], item["term"]) for item in items])
    assert {("Net New Assets", "wealth.TRAN_FACT_1"), ("Certified NNA", "wealth.F_NNA_FIN")} <= pairs
    combined = Combined(eng, llm=None)
    cfg = combined.default_config("graph")
    report = page_scope.evaluate(
        items,
        lambda item: worded(eng.answer(item["question"], "analyst_enterprise", products=item["products"]), eng),
        lambda item: assist(combined, eng.profile, item["question"], "analyst_enterprise", "analysis", "graph", cfg,
                            products=item["products"]),
    )
    assert report["passed"], report


@pytest.mark.parametrize("combined", [False, True])
def test_nna_on_its_supporting_asset_page(eng, combined):
    answer = Combined(eng, llm=None).ask if combined else eng.answer
    scope = ["wealth.F_NNA_FIN"]
    definition = worded(answer("what is NNA", "advisor_wealth", products=scope), eng)
    assert definition["behaviour"] in ("cite_certified", "glossary_definition"), definition
    assert definition["certified_definitions"]
    assert any(d["term"] == "Net New Assets" and d["definition"] in definition["answer_text"] for d in definition["certified_definitions"])
    assert definition.get("cite") or definition.get("citations") or definition.get("definition_citation")
    figure = worded(answer("NNA for March 2025 as reported", "advisor_wealth", products=scope), eng)
    assert figure["behaviour"] == "figure", figure
    assert figure["figure"] == 5637481.28
    assert "wealth.TRAN_FACT_1" in figure["answer_text"]


@pytest.mark.parametrize("question", ["what is NNA", "NNA for March 2025 as reported"])
def test_page_context_does_not_grant_wealth_access(eng, monkeypatch, question):
    for method in ("row", "rows", "cursor", "attached"):
        monkeypatch.setattr(eng.w, method, lambda *args, **kwargs: pytest.fail("access refusal queried warehouse"))
    answer = Combined(eng, llm=None).ask(question, "analyst_retail", products=["wealth.F_NNA_FIN"])
    assert answer["behaviour"] == "refuse", answer
    assert answer.get("figure") is None


@pytest.mark.parametrize("combined", [False, True])
def test_page_unit_that_cannot_answer_falls_back_to_readable_figures(eng, combined):
    answer = Combined(eng, llm=None).ask if combined else eng.answer
    result = worded(answer("Total revenue for June 2025?", "analyst_enterprise", products=["wealth.F_NNA_FIN"]), eng)
    assert result["behaviour"] in ("figure", "parts_with_reason", "sum_with_verdict"), result
    values = result.get("parts") if isinstance(result.get("parts"), dict) else {result.get("unit"): result.get("figure")}
    assert values["retail"] is not None, result
    assert any(step["step"] == "scope_fallback" for step in result["trace"])
    assert "retail" in result["answer_text"].lower(), result
    assert result["products"] == ["wealth.f_nna_fin"]


@pytest.mark.parametrize("products", [[], ["unknown.table"], ["retail.account"]])
def test_default_page_context_reads_only_permitted_units(eng, monkeypatch, products):
    queried = []
    original = planner.execute

    def execute(warehouses, statement):
        queried.extend(statement.units)
        assert statement.units == ("retail",)
        return original(warehouses, statement)

    monkeypatch.setattr(planner, "execute", execute)
    result = worded(eng.answer("Total revenue for June 2025?", "analyst_retail", products=products), eng)
    assert result["behaviour"] == "figure", result
    assert result["unit"] == "retail"
    assert result["figure"] is not None
    assert queried == ["retail"]
    assert "retail" in result["answer_text"].lower()


def test_explicit_unit_does_not_fall_back_when_page_cannot_answer(eng):
    result = eng.answer("Total wealth revenue for June 2025?", "analyst_enterprise", products=["wealth.F_NNA_FIN"])
    assert result["behaviour"] == "abstain", result
    assert not any(step["step"] == "scope_fallback" for step in result["trace"])


def test_preferred_page_refusal_is_not_replaced_by_another_figure(eng, monkeypatch):
    queried = []

    def execute(warehouses, statement):
        queried.extend(statement.units)
        return {"passed": False, "failed_at": "static", "problems": ["SQL gate: denied"]}, None

    monkeypatch.setattr(planner, "execute", execute)
    result = eng.answer("Total revenue for June 2025?", "analyst_enterprise", products=["retail.monthly_revenue"])
    assert result["behaviour"] == "refuse", result
    assert queried == ["retail"]


@pytest.mark.parametrize("products", [[], ["unknown.table"]])
def test_cohort_without_a_page_preference_uses_the_only_readable_unit(eng, products):
    result = Combined(eng, llm=None).ask(
        "How many active customers in June 2025, and what was their fee income?", "analyst_retail", products=products)
    assert result["behaviour"] == "combined", result
    assert result["unit"] == "retail"
    assert "retail" in result["answer_text"].lower()


def test_preferred_cohort_without_declared_relation_uses_another_readable_unit(eng, monkeypatch):
    combined = Combined(eng, llm=None)
    original = planrules.read
    tried = []

    def read(engine, req, *args, **kwargs):
        tried.extend(req.units)
        return original(engine, req, *args, **kwargs)

    monkeypatch.setattr(planrules, "read", read)
    result = combined.ask("How many active customers in June 2025, and what was their revenue?", "analyst_enterprise",
                          products=["sme.monthly_client_revenue"])
    assert result["behaviour"] == "combined", result
    assert result["unit"] == "retail"
    assert tried == ["sme", "retail"]
    assert any(step["step"] == "scope_fallback" for step in result["trace"])
    assert "retail" in result["answer_text"].lower()
    assert result["products"] == ["sme.monthly_client_revenue"]
    rendered = worded(result, eng)
    mapped = next(step for step in reversed(result["trace"]) if step["step"] == "map")
    expected = sorted({f"retail.{item['column'].rsplit('.', 1)[0]}" for item in mapped["columns"]})
    provenance = next(line for line in rendered["answer_text"].splitlines() if line.startswith("From "))
    assert provenance.split(", the certified", 1)[0] == "From " + ", ".join(expected)
    assert "sme.monthly_client_revenue" not in provenance
    assert "sme.client_activity" not in provenance


def test_preferred_cohort_sql_policy_rejection_does_not_try_another_unit(eng, monkeypatch):
    combined = Combined(eng, llm=None)
    tried = []
    original = planner.execute

    def execute(warehouses, statement):
        tried.extend(statement.units)
        return original(warehouses, replace(statement, sql="DELETE FROM customer"))

    monkeypatch.setattr(planner, "execute", execute)
    result = combined.ask("How many active customers in June 2025, and what was their revenue?", "analyst_enterprise",
                          products=["retail.monthly_revenue"])
    assert result["behaviour"] == "refuse", result
    assert result["guard"]["failed_at"] == "static"
    assert tried == ["retail"]
    assert not any(step["step"] == "scope_fallback" for step in result["trace"])


@pytest.mark.parametrize("products", [None, ["wealth.SUIT_RVW_FACT_1"]])
def test_cohort_without_declared_relation_abstains_before_giving_any_physical_figure(eng, products):
    """A1 coverage abstentions replace the old rules path's partial cohort figures."""
    result = Combined(eng, llm=None).ask(
        "How many suitability reviews and what was their revenue for Q1 2025?", "analyst_enterprise",
        products=products)
    assert result["behaviour"] == "abstain", result
    assert result.get("figure") is None and not result.get("figures") and not result.get("result") and not result.get("sql")
    assert result["reason"]


@pytest.mark.parametrize("route", ["ask", "assist"])
def test_readable_page_cannot_hide_cohort_access_refusal(eng, monkeypatch, route):
    for method in ("row", "rows", "cursor", "attached"):
        monkeypatch.setattr(eng.w, method, lambda *args, **kwargs: pytest.fail("access refusal queried warehouse"))
    combined = Combined(eng, llm=None)
    question = "How many suitability reviews and what was their revenue for Q1 2025?"

    def ask(products):
        if route == "ask":
            return combined.ask(question, "analyst_retail", products=products)
        return assist(combined, eng.profile, question, "analyst_retail", "analysis", "graph",
                      combined.default_config("graph"), products=products)

    unscoped = ask(None)
    scoped = ask(["retail.account"])
    assert unscoped["behaviour"] == "blocked_access", unscoped
    assert scoped["behaviour"] == "blocked_access", scoped
    for result in (unscoped, scoped):
        assert "wealth" in result["denied_warehouses"]
        assert "no single unit" not in result["reason"]
        assert result.get("figure") is None and not result.get("figures") and not result.get("result") and not result.get("sql")


@pytest.mark.parametrize("transport", ["answer", "ask", "assist", "model"])
@pytest.mark.parametrize("products", [None, [], ["unknown.table"], ["retail.account"]])
def test_cohort_certified_only_in_unreadable_unit_refuses_before_any_read(eng, monkeypatch, products, transport):
    for method in ("row", "rows", "cursor", "attached"):
        monkeypatch.setattr(eng.w, method, lambda *args, **kwargs: pytest.fail("access refusal queried warehouse"))
    monkeypatch.setattr(eng.g, "retrieve", lambda *args, **kwargs: pytest.fail("access refusal retrieved documents"))
    class Uncalled:
        name = "mock:uncalled"

        def json(self, *args, **kwargs):
            pytest.fail("access refusal called the model")

    question = "How many suitability reviews and what was their revenue for Q1 2025?"
    combined = Combined(eng, llm=Uncalled() if transport == "model" else None)
    if transport == "assist":
        result = assist(combined, eng.profile, question, "analyst_retail", "analysis", "graph", PRESETS["graph"], products=products)
    else:
        answer = eng.answer if transport == "answer" else combined.ask
        result = answer(question, "analyst_retail", products=products)
    assert result["behaviour"] == "blocked_access", result
    assert "wealth" in result["denied_warehouses"]
    assert result.get("figure") is None and not result.get("result") and not result.get("sql")
    assert "certified in no single unit" not in result.get("reason", "")


@pytest.mark.parametrize("fails", [False, True])
def test_model_page_fallback_keeps_checked_plans_and_cloud_no_fallback(eng, monkeypatch, fails):
    from kge.adapters.model import NoFallback
    question = "Total revenue for June 2025?"
    products = ["wealth.F_NNA_FIN"]
    scope = ProductScope(eng, products)
    req = parse(question, eng.term_names, eng)
    proposals = {}
    for selected in planpublic.scope_requests(eng, req, "analyst_enterprise", PRESETS["graph"], scope):
        vocabulary = planrules.vocabulary(eng, "analyst_enterprise", "analysis", scope, PRESETS["graph"], selected)
        proposals[tuple(selected.units)] = planrules.read(eng, selected, vocabulary, PRESETS["graph"]).model_dump(mode="json", by_alias=True)

    class AttemptModel(Stub):
        def json(self, prompt, max_tokens=800):
            payload = json.loads(prompt.split("\n", 1)[1])
            units = tuple(payload["vocabulary"]["default_units"])
            if fails and units != ("wealth",):
                raise RuntimeError("mock provider unavailable")
            self.proposal = proposals[units]
            return super().json(prompt, max_tokens)

    monkeypatch.setattr(planrules, "read", lambda *args, **kwargs: pytest.fail("model fell back to rules"))
    model = AttemptModel()
    combined = Combined(eng, llm=model)
    cfg = PRESETS["graph"].with_stages(resolution="glossary+model")
    if fails:
        monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
        with pytest.raises(NoFallback):
            combined.ask(question, "analyst_enterprise", config=cfg, products=products)
        return
    result = worded(combined.ask(question, "analyst_enterprise", config=cfg, products=products), eng)
    assert result["behaviour"] == "parts_with_reason" and result["parts"]["retail"] == 13287.67
    assert len(model.prompts) == 2
    assert result["versions"]["interpretation"] == "model:stub:fixed"
    assert any(step["step"] == "scope_fallback" for step in result["trace"])
    assert "From retail.monthly_revenue" in result["answer_text"]
    provenance = next(line for line in result["answer_text"].splitlines() if line.startswith("From "))
    assert "corporate.quarterly_revenue" not in provenance
    assert HistoryEntry.model_validate(result["history"][-1]).binding.products == tuple(scope.products)


def test_checked_scalar_names_its_final_physical_source_outside_the_page(eng):
    result = worded(eng.answer("Retail revenue for June 2025", "analyst_enterprise",
                              products=["wealth.F_NNA_FIN"]), eng)
    assert result["behaviour"] == "figure" and result["figure"] == 13287.67
    assert "From retail.monthly_revenue, the certified source" in result["answer_text"]


def test_page_preference_does_not_narrow_an_explicit_all_unit_question(eng):
    result = eng.answer("Total revenue across the bank for the quarter to 30 June 2025", "analyst_enterprise",
                        products=["retail.monthly_revenue"])
    assert result["behaviour"] == "parts_with_reason", result
    assert set(result["parts"]) == {"retail", "sme", "wealth", "corporate"}
    assert result["parts"]["wealth"] == 2506757.26
    assert not any(step["step"] == "scope_fallback" for step in result["trace"])
