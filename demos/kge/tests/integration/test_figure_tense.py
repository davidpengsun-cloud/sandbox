"""A measure and period return the same governed figure in present and past tense."""
import pytest

from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.wording import worded
from tests.scratch import open_engine, scratch_build


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    engine = open_engine(scratch_build(tmp_path_factory.mktemp("figure_tense") / "build"))
    yield engine
    engine.close()


@pytest.mark.parametrize("tense", ["is", "was"])
@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("products", [None, ["retail.monthly_revenue"]])
def test_period_scoped_revenue_is_independent_of_tense(eng, tense, combined, products):
    unit = "retail " if products is None else ""
    question = f"What {tense} the total {unit}revenue in June 2025?"
    answer = (Combined(eng, llm=None).ask(question, "analyst_enterprise", products=products) if combined
              else eng.answer(question, principal="analyst_enterprise", products=products))
    answer = worded(answer, eng)
    assert answer["behaviour"] == "figure", answer
    assert answer["figure"] == 13287.67
    assert answer["unit"] == "retail"
    definitions = answer["certified_definitions"]
    assert any(d["term"] == "Revenue" and d["definition"] in answer["answer_text"] for d in definitions)
    assert worded(answer, eng) == answer
    if products is not None:
        assert answer["products"] == products
        assert "DD-CORPORATE" not in answer.get("cite", [])
