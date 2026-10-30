"""The combined answer: figures from the warehouse, the documents behind them, and the gate.

    .venv/bin/python -m pytest tests/integration/test_combined.py -q

Offline: no model is called. Where a test needs a model it uses a stub that
returns fixed text, so what is tested is what the code does with a model's
output, which is the part that has to hold whatever the model says.

RW is a customer group across Retail and Wealth, whose no-join fact pack 1.1.0
replaced with a certified join (#39 items 7c, 7d).
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine.combined import Combined, number_citations
from kge.pipeline.sqlguard import build_allowlist, check, gate
from tests.scratch import profile_of, scratch_build

Q = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
RW = "How many active customers in Retail and Wealth in June 2025 raised a complaint that month?"


@pytest.fixture(scope="module")
def comb(eng):
    return Combined(eng, llm=None)


@pytest.fixture(scope="module")
def example(comb):
    return comb.ask(Q, "analyst_retail")


class Stub:
    """A model that says what it is told to. name is what the answer records."""
    name = "stub:fixed"

    def __init__(self, sql=(), answer="", terms=()):
        self.sql, self.answer, self.terms, self.prompts = list(sql), answer, list(terms), []

    def json(self, prompt, max_tokens=800):
        self.prompts.append(prompt)
        if prompt.startswith("Glossary terms"):
            return {"terms": self.terms}
        if prompt.startswith("Write one DuckDB"):
            return {"sql": self.sql.pop(0) if self.sql else ""}
        return {"answer": self.answer}


def test_figures(example):
    assert example["behaviour"] == "combined" and example["unit"] == "retail"
    got = {f["id"]: f["value"] for f in example["figures"]}
    assert got == {"c1": 176, "c2": 3, "m1": 6400.62, "m2": 42.64}
    assert [f["column"] for f in example["figures"]] == ["customer_activity.is_active_flag", "complaint.complaint_id",
                                                        "daily_balance.ledger_balance", "monthly_revenue.fee_income"]


def test_the_planner_wrote_the_sql_and_the_gate_passed_it(example):
    assert example["sql_source"] == "cohort-v1"
    assert example["guard"]["passed"] and example["guard"]["checks"][-1] == "dry run"


def test_cites_the_certified_current_definition(example):
    cited = {c["doc"]: c for c in example["citations"]}
    assert cited["MD-ACTIVE-CUSTOMER"]["version"] == "2.0"
    assert {"MD-AVERAGE-BALANCE", "MD-FEE-INCOME"} <= set(cited)
    assert all(f["cite"] for f in example["figures"]), "every figure points at a document"


def test_citations_are_numbered_in_the_order_the_answer_reads_them(example):
    """[1], [2], ... as the figures use them, then the rest: never the order the graph happened to return rows in."""
    cited = {c["n"]: c["doc"] for c in example["citations"]}
    assert sorted(cited) == list(range(1, len(cited) + 1))
    assert [f["cite"] for f in example["figures"]] == [1, 2, 3, 4]
    assert [cited[f["cite"]] for f in example["figures"]] == ["MD-ACTIVE-CUSTOMER", "DD-RETAIL", "MD-AVERAGE-BALANCE", "MD-FEE-INCOME"]
    marks = [int(m) for m in re.findall(r"\[(\d+)\]", example["answer_text"])]
    assert marks == [1, 2, 3, 4]


def test_the_same_question_gets_the_same_markers_every_time(comb, example):
    """Two definitions published the same day came back in either order, and their markers swapped from run to run."""
    first = [(c["n"], c["doc"], c["chunk"], c["terms"]) for c in example["citations"]]
    for _ in range(6):
        again = comb.ask(Q, "analyst_retail")
        assert [(c["n"], c["doc"], c["chunk"], c["terms"]) for c in again["citations"]] == first
        assert [f["cite"] for f in again["figures"]] == [f["cite"] for f in example["figures"]]
        assert again["answer_text"] == example["answer_text"]


def test_numbering_does_not_depend_on_the_order_citations_were_found_in():
    found = [{"doc": d, "why": w, "terms": [t]} for d, w, t in (
        ("MD-FEE-INCOME", "certified definition", "Fee Income"), ("MD-AVERAGE-BALANCE", "certified definition", "Average Balance"),
        ("WIKI-009", "retrieved by the question", "Fee Income"), ("DD-RETAIL", "certified passage", "Complaint"),
        ("POL-FEES", "certified passage", "Fee Income"))]
    figures = [{"id": "m1", "cite": "MD-AVERAGE-BALANCE"}, {"id": "m2", "cite": "MD-FEE-INCOME"}, {"id": "m3", "cite": None}]
    results = set()
    for order in (found, found[::-1], found[2:] + found[:2]):
        cites, figs = [dict(c) for c in order], [dict(f) for f in figures]
        number_citations(cites, figs)
        results.add((tuple((c["n"], c["doc"]) for c in cites), tuple(f["cite"] for f in figs)))
    assert results == {(((1, "MD-AVERAGE-BALANCE"), (2, "MD-FEE-INCOME"), (3, "DD-RETAIL"), (4, "POL-FEES"), (5, "WIKI-009")),
                        (1, 2, None))}


def test_superseded_and_uncertified_are_shown_not_used(example):
    why = {n["doc"]: n["why"] for n in example["not_cited"]}
    assert why["MD-ACTIVE-CUSTOMER-V1"] == "superseded by MD-ACTIVE-CUSTOMER"
    assert why["DRAFT-002"] == "uncertified"
    assert not {"MD-ACTIVE-CUSTOMER-V1", "DRAFT-002"} & {c["doc"] for c in example["citations"]}


def test_the_gate_holds_back_personal_data_and_never_quotes_it(eng, example):
    assert "POL-COMPLAINTS" in {w["doc"] for w in example["withheld"]}
    assert "POL-COMPLAINTS" not in {c["doc"] for c in example["citations"]}
    text = eng.g.q("MATCH (c:Chunk {id: 'POL-COMPLAINTS#0'}) RETURN c.text AS t")[0]["t"]
    body = json.dumps(example)
    assert "@" in text and not any(line.strip() in body for line in text.splitlines() if "@" in line)


def test_the_template_says_what_it_did_not_use(example):
    t = example["answer_text"]
    assert "176" in t and "6,400.62" in t and "USD 42.64" in t
    assert ("Not used: superseded, MD-ACTIVE-CUSTOMER-V1; uncertified, DRAFT-002, EM-001, EM-004, NOTE-001, WIKI-003." in t
            and "POL-COMPLAINTS" in t), "each source not used named by its id, so the text holds no count the answer does not"
    assert example["phrased_by"] == "template"


def test_a_unit_the_principal_cannot_read_is_refused_before_any_query(comb):
    a = comb.ask(Q.replace("Retail", "SME"), "analyst_retail")
    assert a["behaviour"] == "refuse" and a["denied_warehouses"] == ["sme"]
    assert "sql" not in a and "citations" not in a


def test_a_group_across_units_is_refused(comb):
    a = comb.ask(Q.replace("in Retail", "in Retail and SME"), "admin")
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["retail-sme"]
    assert (a["fact"]["asserted_by"], a["fact"]["asserted_on"]) == ("Data Architecture Board", "2024-03-12")
    assert a["reason"] == ("A customer group cannot span Retail Banking and SME Banking: each unit keys its customers differently "
                           "and no certified join exists between them. " + a["fact"]["note"])


def test_a_group_across_retail_and_wealth_is_built_within_one_unit_and_names_the_certified_join(comb):
    """Pack 1.1.0 replaced retail-wealth's no-join fact with a certified join, which the runtime reads (#39 item 7d). A
    customer group is still built within one unit, so /ask abstains, refusing no join and quoting no fact (the page
    would show an empty 'Asserted by  on .'), and says the certified join exists; across a pair with a no-join fact as
    well, it is refused, listing that pair only."""
    a = comb.ask(RW, "analyst_enterprise")
    assert a["behaviour"] == "abstain" and a["joins_refused"] == [] and "fact" not in a, a.get("fact")
    assert a["reason"] == ("A customer group is built within one unit, and Retail Banking and Wealth Management key their customers "
                           "differently. They join only through a certified join (retail-wealth through the certified join client_xref "
                           "(certified client cross-reference)), which a customer group does not use: ask for a figure over it."), a["reason"]
    three = comb.ask(RW.replace("Retail and Wealth", "Retail, SME and Wealth"), "analyst_enterprise")
    assert three["behaviour"] == "refuse" and three["joins_refused"] == ["retail-sme", "sme-wealth"] and three["fact"]["asserted_by"]
    assert "no certified join" not in three["reason"] and three["reason"].endswith(three["fact"]["note"])


def test_other_questions_get_exactly_what_answer_returns(eng, comb):
    q = "How many active customers in Retail in June 2025?"
    a, b = comb.ask(q, "analyst_retail"), eng.answer(q, "analyst_retail")
    assert a["combined"] is False and a["behaviour"] == b["behaviour"] == "figure" and a["figure"] == b["figure"]


@pytest.fixture(scope="module")
def allow(eng, example):
    certified = [tuple(f["column"].split(".")) for f in example["figures"]]
    return build_allowlist(eng.g, "retail", certified)


@pytest.mark.parametrize("sql, problem", [
    ("DELETE FROM complaint", "read-only"),
    ("SELECT 1; SELECT 2", "one statement only"),
    ("SELECT customer.email FROM customer", "column customer.email is not certified"),
    ("SELECT sum(customer_activity.login_count) FROM customer_activity", "column customer_activity.login_count is not certified"),
    ("SELECT count(*) FROM complaint JOIN monthly_revenue ON complaint.customer_id = monthly_revenue.account_id", "is not a declared join"),
    ("SELECT count(*) FROM client", "table client is not in the retail warehouse"),
    ("SELECT count(*) FROM sme.client", "outside this unit's warehouse"),
])
def test_the_gate_rejects(allow, sql, problem):
    assert any(problem in p for p in check(sql, allow)), check(sql, allow)


def test_the_gate_dry_runs_what_passes_the_static_checks(eng, allow):
    g = gate(eng.w.con["retail"], "SELECT sum(customer_activity.month_start) FROM customer_activity", allow)
    assert not g["passed"] and g["problems"][0].startswith("dry run failed")


def test_an_alias_does_not_hide_a_column(allow):
    assert any("customer.email" in p for p in check("SELECT c.email FROM customer c", allow))


def test_generated_sql_goes_through_the_same_gate(eng, example, monkeypatch):
    bad = "SELECT count(*) AS c1_count, 0 AS c2_count, 0 AS m1_value, 0 AS m2_value FROM customer WHERE customer.email IS NOT NULL"
    stub = Stub(sql=[bad, example["sql"]])
    c = Combined(eng, llm=stub)
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    a = c.ask(Q, "analyst_retail")
    tries = next(t for t in a["trace"] if t["step"] == "plan")["attempts"]
    assert [t["source"] for t in tries] == ["cohort-v1", "model:stub:fixed", "model:stub:fixed"]
    assert not tries[1]["passed"] and any("customer.email" in p for p in tries[1]["problems"])
    assert tries[2]["passed"] and a["sql_source"] == "model:stub:fixed"
    assert {f["id"]: f["value"] for f in a["figures"]} == {"c1": 176, "c2": 3, "m1": 6400.62, "m2": 42.64}
    retry = [p for p in stub.prompts if p.startswith("Write one DuckDB")][1]
    assert "rejected: column customer.email is restricted personal data" in retry, "the model is told why, and tries once more"


def test_no_query_passes_no_answer(eng, monkeypatch):
    c = Combined(eng, llm=Stub(sql=["SELECT customer.email FROM customer", "DROP TABLE complaint"]))
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    a = c.ask(Q, "analyst_retail")
    assert a["behaviour"] == "abstain" and "figures" not in a


def test_without_a_model_the_planner_failing_is_an_abstention(eng, monkeypatch):
    c = Combined(eng, llm=None)
    monkeypatch.setattr(c, "plan", lambda *a, **k: {"reason": "planner cannot build this shape"})
    a = c.ask(Q, "analyst_retail")
    assert a["behaviour"] == "abstain" and "A model can be configured" in a["reason"]


def test_model_wording_with_an_invented_number_is_thrown_away(eng):
    a = Combined(eng, llm=Stub(answer="176 customers were active, of whom 4 complained [1].")).ask(Q, "analyst_retail")
    assert a["answer_text"].startswith("Retail Banking, June 2025") and "not in the result: 4" in a["phrased_by"]


@pytest.mark.parametrize("glued, stray", [("down -3000 on May", "-3000"), ("USD9999 below plan", "9999"),
                                          ("in a 176-200 band", "200"), ("or .5 of them", ".5")])
def test_model_wording_with_a_number_glued_to_a_sign_letters_or_a_hyphen_is_thrown_away(eng, glued, stray):
    a = Combined(eng, llm=Stub(answer=f"176 customers were active [1], {glued}.")).ask(Q, "analyst_retail")
    assert a["answer_text"].startswith("Retail Banking, June 2025") and a["phrased_by"].endswith(f"not in the result: {stray})")


def test_model_wording_may_name_the_documents_and_labels_it_was_given(eng):
    """A document id or a version is a name, not a number: EM-004's 004 and v2.0's 2 are not figures."""
    said = ("In June 2025, 176 Retail customers met the Active Customer definition v2.0 [1]; 3 of them complained [2]. Not used: "
            "MD-ACTIVE-CUSTOMER-V1, which is superseded, and EM-004 and WIKI-003, which are uncertified. Held back: POL-COMPLAINTS.")
    a = Combined(eng, llm=Stub(answer=said)).ask(Q, "analyst_retail")
    assert a["answer_text"] == said and a["phrased_by"] == "model:stub:fixed"


def test_model_wording_citing_a_source_that_is_not_there_is_thrown_away(eng):
    a = Combined(eng, llm=Stub(answer="176 customers were active [9].")).ask(Q, "analyst_retail")
    assert a["phrased_by"].startswith("template") and "[9]" in a["phrased_by"]


def test_model_wording_that_keeps_to_the_result_is_used(eng):
    said = "In June 2025, 176 Retail customers were active [1]; 3 of them complained [4]. Their average balance was 6,400.62 [2] and fee income USD 42.64 [3]."
    a = Combined(eng, llm=Stub(answer=said)).ask(Q, "analyst_retail")
    assert a["answer_text"] == said and a["phrased_by"] == "model:stub:fixed"


def test_the_model_cannot_add_a_term_outside_the_glossary(eng):
    a = Combined(eng, llm=Stub(terms=["Customer Lifetime Value", "Active Customer"], answer="")).ask(Q, "analyst_retail")
    assert a["trace"][0]["resolved_by"] == "rules" and "Customer Lifetime Value" not in a["trace"][0]["terms"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_across_retail_and_wealth_the_api_asserts_no_fact_the_pack_replaced(client):
    """What the page reads: /ask's answer to a group across Retail and Wealth carries no fact and names the certified join,
    and a term's map lists the five no-join facts there are, each with who asserted it, and the certified join beside them."""
    who = client.as_("analyst_enterprise")
    a = client.post("/ask", json={"question": RW}, headers=who).json()
    assert a["behaviour"] == "abstain" and a["joins_refused"] == [] and "fact" not in a and a["certified_joins"], a
    m = client.get("/map/Revenue", headers=who).json()
    assert [n["pair"] for n in m["no_join"]] == ["retail-sme", "retail-corporate", "sme-corporate", "sme-wealth", "corporate-wealth"]
    assert all(n["asserted_by"] and n["asserted_on"] and n["note"] for n in m["no_join"]), m["no_join"]
    assert [c["pair"] for c in m["certified_joins"]] == ["retail-wealth"]


def test_ask_through_the_api(client):
    a = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail")).json()
    assert a["behaviour"] == "combined" and a["principal"] == "analyst_retail"
    assert client.post("/ask", json={"question": Q, "arm": "schema"}, headers=client.as_("analyst_retail")).status_code == 400
    assert client.post("/ask", json={"question": Q}).status_code == 401
