"""Catalogue questions abstain before model calls on every answer entry point."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.eval import sets
from kge.eval.run_golden import score
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import CATALOGUE_ABSTENTION, catalogue_question, parse
from kge.services.ask.engine.wording import worded
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Stub

DEMO = Path(__file__).resolve().parents[2]
CATALOGUE = sets.catalogue(DEMO / "clients/meridian/fixture/golden.json")
PRINCIPAL = "analyst_enterprise.meridian@example.com"


@pytest.mark.parametrize("question", [
    "What is the certified revenue for retail?",
    "What is the certified NNA for wealth?",
    "What is the trusted revenue figure for retail?",
    "Which products have revenue above 1000?",
    "What is the trusted NNA for wealth?",
    "What is the certified Lumen Mass?",
])
def test_named_pack_measures_keep_periodless_questions_on_figure_path(question):
    domain = SimpleNamespace(ALIASES={"nna": "Net New Assets"}, UNIT_WORDS={})
    terms = ["Revenue", "Net New Assets", "Lumen Mass"]
    req = parse(question, terms, domain)
    assert req.terms_found and req.period is None
    assert not catalogue_question(question, req.terms_found)
    unknown = parse(question, [], SimpleNamespace(ALIASES={}, UNIT_WORDS={}))
    assert catalogue_question(question, unknown.terms_found)


@pytest.mark.parametrize("question", [
    "Who owns the Monthly Revenue product?",
    "Describe the Net New Assets product.",
    "Which table has net interest income?",
    "Which product should I use for retail revenue?",
    "What is the refresh cadence of Monthly Revenue?",
    "Who owns the certified Revenue product?",
    "Who owns the Monthly Revenue product with revenue above 1000?",
    "What fields are in products with revenue above 1000?",
    "Is NNA certified?",
    "Is retail revenue certified?",
    "Are the wealth figures trusted?",
    "Is Monthly Revenue certified?",
    "Was total retail revenue certified in June 2025?",
    "Were the wealth figures trusted in June 2025?",
])
def test_explicit_metadata_stays_catalogue_when_a_pack_term_is_named(question):
    assert catalogue_question(question, ["Revenue", "Net New Assets", "Net Interest Income"])


def assert_abstention(item, answer):
    assert answer["behaviour"] == "abstain"
    assert answer.get("figure") is None
    assert not answer.get("parts") and not answer.get("sum")
    assert score(item, answer)[0] is True
    assert answer["reason"] == CATALOGUE_ABSTENTION
    assert CATALOGUE_ABSTENTION in answer["answer_text"]
    assert all(name not in answer["answer_text"] for name in
               ("monthly_revenue", "total_revenue_usd", "QTR_FEE_REV_FACT_1", "HSHLD_DIM_1", "customer_id"))


def test_catalogue_set_and_detection():
    assert len(CATALOGUE) == 20
    assert [item["id"] for item in CATALOGUE] == [f"C{i:02}" for i in range(1, 21)]
    assert all(catalogue_question(item["question"]) for item in CATALOGUE)
    assert not catalogue_question("")
    assert not catalogue_question("Who owns the account table and what is the average balance?")
    assert not catalogue_question("Which products contain balance and count the matches?")
    assert catalogue_question("  Ｗho owns the Monthly Revenue product?\u200b")


def test_engine_and_combined_abstain_without_model_calls(eng):
    model = Stub()
    for item in CATALOGUE:
        for cfg in PRESETS.values():
            assert_abstention(item, worded(eng.answer(item["question"], PRINCIPAL, config=cfg), eng))
        for llm in (None, model):
            combined = Combined(eng, llm=llm)
            assert_abstention(item, worded(combined.ask(item["question"], PRINCIPAL), eng))
    assert model.prompts == []


@pytest.mark.parametrize("with_model", [False, True])
def test_catalogue_over_http_and_mcp(client, monkeypatch, with_model):
    import kge.app as appmod
    model = Stub()
    monkeypatch.setattr(appmod, "_combined", Combined(appmod.engine(), llm=model if with_model else None))
    for item in CATALOGUE:
        for path in ("/ask", "/answer"):
            response = client.post(path, json={"question": item["question"]}, headers=client.as_(PRINCIPAL))
            assert response.status_code == 200, response.text
            assert_abstention(item, response.json())
        result = mcp_wire(client, "/mcp/answer", PRINCIPAL, "ask", {"question": item["question"]})
        assert_abstention(item, result["structuredContent"])
    assert model.prompts == []


def test_shelf_definitions_and_mixed_figure_stay_on_figure_path(eng):
    shelf = [item for item in sets.items(DEMO / "clients/meridian/fixture/golden.json")
             if "G031" <= item["id"] <= "G040"]
    assert len(shelf) == 10
    for item in shelf:
        assert not catalogue_question(item["question"])
        assert eng.answer(item["question"], PRINCIPAL)["behaviour"] == "cite_certified"
        assert Combined(eng, llm=None).ask(item["question"], PRINCIPAL)["behaviour"] == "cite_certified"
    question = "Who owns the account table and what is the average balance?"
    for answer in (eng.answer(question, PRINCIPAL), Combined(eng, llm=None).ask(question, PRINCIPAL)):
        assert answer.get("reason") != CATALOGUE_ABSTENTION


@pytest.mark.parametrize("extra", [[{"id": "C01"}, {"id": "C01"}], "invalid"])
def test_catalogue_validation(tmp_path, extra):
    import json
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"version": "v1", "items": [], "catalogue": extra}))
    with pytest.raises(ValueError):
        sets.read(path)


def test_catalogue_hash_preserves_baseline_and_core_changes_are_detected(tmp_path):
    import json
    path = tmp_path / "golden.json"
    core = {"version": "v1", "items": [{"id": "G001", "question": "A figure"}]}
    path.write_text(json.dumps(core, indent=2))
    before = sets.sha256(path)
    path.write_text(json.dumps({**core, "catalogue": CATALOGUE}, indent=2))
    assert sets.sha256(path) == before
    assert sets.read(path) == {**core, "changes_from": None}
    assert sets.catalogue(path) == CATALOGUE
    core["items"][0]["question"] = "A changed figure"
    path.write_text(json.dumps({**core, "catalogue": CATALOGUE}, indent=2))
    assert sets.sha256(path) != before
    assert sets.catalogue(tmp_path / "absent.json") == []


PARAPHRASES = [
    "What columns are in the retail Customer product?",
    "Is retail Customer certified?",
    "Is NNA certified?",
    "Is retail revenue certified?",
    "Are the wealth figures trusted?",
    "Is Monthly Revenue certified?",
    "Tell me about the Net New Assets product.",
    "Who is responsible for the Monthly Revenue product?",
    "Which data product holds customer emails?",
    "Show me the fields of the customer table.",
    "Which table should I use for net interest income?",
    "Who owns monthly revenue?",
    "Who owns sme.facility?",
    "Please tell me who is the custodian for Customer.",
    "Who is the data steward of Monthly Revenue?",
    "Show the schema of the retail Customer table.",
    "What is the certification status of Customer?",
    "What is the certified status of the Customer product?",
    "What is the refresh cadence of Monthly Revenue?",
    "When was Customer last refreshed?",
    "How often does the Customer table refresh?",
    "Show the data quality score for Customer.",
    "Please describe the Customer table.",
    "List tables in the retail domain.",
    "Show me all products in the wealth unit.",
    "Which product stores customer emails?",
    "Which table contains customer emails?",
    "Which tables include customer emails?",
    "Where to find customer data?",
    "What data do we have on customers?",
    "What data do we have for households?",
    "How can I link Account to Customer?",
    "Please explain what does the column customer_id mean?",
    "Who looks after the Monthly Revenue product?",
    "Who maintains the customer table?",
    "Which team is accountable for Monthly Revenue?",
    "What attributes does the Customer product have?",
    "Give me an overview of Net New Assets.",
    "Can I trust the Monthly Revenue product?",
    "Is Monthly Revenue approved for use?",
    "How many columns does the customer table have?",
]
FIGURES = [
    "Show revenue by account owner for June 2025.",
    "Show me products with revenue above 1000 in June 2025.",
    "What is the revenue product?",
    "Which products have the highest revenue?",
    "Which table has the most customers?",
    "What is the total revenue for the retail product in June 2025?",
    "How many customers do we have?",
    "What was total revenue in June 2025?",
    "Which tables have the lowest income?",
    "Which products have the largest balance?",
    "What is the average balance of the retail product?",
    "What is net interest income for the retail product?",
    "What is revenue in June 2025 for the retail product?",
]


@pytest.mark.parametrize("question", PARAPHRASES)
def test_catalogue_paraphrases(eng, question):
    item = {**CATALOGUE[0], "question": question}
    assert catalogue_question(question)
    for answer in (eng.answer(question, PRINCIPAL), Combined(eng, llm=None).ask(question, PRINCIPAL)):
        assert_abstention(item, worded(answer, eng))


def test_literal_wording_and_score(eng):
    literal = "Ask answers governed figures and definitions. Use the product page or Search in the catalogue."
    assert CATALOGUE_ABSTENTION == literal
    answer = worded(eng.answer(CATALOGUE[0]["question"], PRINCIPAL), eng)
    assert answer["reason"] == literal
    assert answer["answer_text"] == "No answer: " + literal
    ok, why = score(CATALOGUE[0], {**answer, "reason": "wrong"})
    assert not ok
    assert "reason='wrong'" in why


def test_figure_detection_and_unchanged_sets(eng):
    questions = list(FIGURES)
    for name in ("golden.json", "heldout.json", "golden_wealth_v1.json", "sage_v1.json"):
        questions.extend(item["question"] for item in sets.items(DEMO / "clients/meridian/fixture" / name))
    for question in questions:
        assert not catalogue_question(question), question
    for question in FIGURES[:2] + FIGURES[3:5]:
        answer = eng.answer(question, PRINCIPAL)
        assert answer['behaviour'] in ('abstain', 'clarify') and answer.get('reason') != CATALOGUE_ABSTENTION, question
        assert not answer.get('figure') and not answer.get('parts'), question
    definition = eng.answer(FIGURES[2], PRINCIPAL)
    assert definition["behaviour"] == "cite_certified"
    assert "MD-REVENUE" in str(definition)
    for question in FIGURES[5:8]:
        assert eng.answer(question, PRINCIPAL).get("reason") != CATALOGUE_ABSTENTION


def test_catalogue_access_order(client):
    principal = "analyst_retail.meridian@example.com"
    question = CATALOGUE[0]["question"]
    response = client.post("/answer", json={"question": question}, headers=client.as_(principal))
    assert response.status_code == 200
    assert_abstention(CATALOGUE[0], response.json())
    response = client.post("/ask", json={"question": question, "products": ["sme.facility"]},
                           headers=client.as_(principal))
    assert response.status_code == 200
    answer = response.json()
    assert answer["behaviour"] == "refuse"
    assert answer["denied_warehouses"] == ["sme"]


def test_catalogue_digest(tmp_path):
    import hashlib
    import json

    path = tmp_path / "set.json"
    assert sets.catalogue_sha256(path) is None
    path.write_text(json.dumps({"items": []}))
    assert sets.catalogue_sha256(path) is None
    path.write_text(json.dumps({"catalogue": CATALOGUE}, indent=2))
    expected = hashlib.sha256(json.dumps(CATALOGUE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert sets.catalogue_sha256(path) == expected
    path.write_text(json.dumps({"catalogue": CATALOGUE}, sort_keys=True))
    assert sets.catalogue_sha256(path) == expected
    path.write_text(json.dumps({"catalogue": []}))
    assert sets.catalogue_sha256(path) != expected


@pytest.mark.parametrize("failure", [{"reason": "wrong"}, {"behaviour": "figure", "figure": 1}])
def test_evaluate_scores_catalogue_answers(eng, monkeypatch, tmp_path, failure):
    import json
    from types import SimpleNamespace

    from kge.eval import run_golden

    build = tmp_path / "build"
    build.mkdir()
    (build / "golden.json").write_text(json.dumps({"version": "v1", "items": [], "catalogue": CATALOGUE}))
    profile = SimpleNamespace(serve=lambda: profile, asking=lambda value: PRINCIPAL,
                              estate=lambda: build, run_dir=lambda: tmp_path)
    monkeypatch.setattr(run_golden, "resolve", lambda value: profile)
    monkeypatch.setattr(run_golden, "unknown_principal", lambda *args: None)
    monkeypatch.setattr(run_golden, "uncertified_ids", lambda *args: set())
    monkeypatch.setattr(run_golden.Engine, "serving", lambda *args: eng)
    monkeypatch.setattr(eng, "close", lambda: None)
    def answer(question, **kwargs):
        result = {"behaviour": "abstain", "reason": CATALOGUE_ABSTENTION}
        return {**result, **failure} if question == CATALOGUE[0]["question"] else result

    monkeypatch.setattr(eng, "answer", answer)
    monkeypatch.setattr("sys.argv", ["run_golden", "--arm", "all"])
    assert run_golden.evaluate() == 0
    report = json.loads((tmp_path / "eval.json").read_text())
    for arm in report["catalogue"].values():
        assert arm["abstain"] == {"passed": 19, "of": 20}


def test_catalogue_table(capsys):
    from kge.eval.run_golden import print_catalogue_table

    print_catalogue_table("catalogue abstentions", {arm: {"abstain": {"passed": 20, "of": 20}}
                                                   for arm in ("schema", "catalog", "graph")})
    output = capsys.readouterr().out
    assert "within" not in output and "0/0" not in output
    assert output.count("20/20") == 3
