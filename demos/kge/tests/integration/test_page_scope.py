"""Every certified term's implementing and supporting asset pages can answer definitions and figures."""
import pytest

from kge.eval import page_scope
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
    assert any(item["term"] == "Net New Assets" and item["products"] == ["wealth.F_NNA_FIN"] for item in items)
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
    assert figure["figure"] == 5868354.54
    assert "wealth.TRAN_FACT_1" in figure["answer_text"]


@pytest.mark.parametrize("question", ["what is NNA", "NNA for March 2025 as reported"])
def test_page_context_does_not_grant_wealth_access(eng, monkeypatch, question):
    monkeypatch.setattr(eng.w, "row", lambda *args, **kwargs: pytest.fail("access refusal queried warehouse"))
    answer = Combined(eng, llm=None).ask(question, "analyst_retail", products=["wealth.F_NNA_FIN"])
    assert answer["behaviour"] == "refuse", answer
    assert answer.get("figure") is None
