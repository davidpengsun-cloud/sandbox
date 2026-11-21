"""Portal starter chips answer against the served Meridian sample without a model."""

import pytest

from tests.integration.test_answer_text import client as client

DOMAIN_PROMPTS = [
    "What products are in this domain?",
    "Which columns are available in this domain?",
    "Who owns the products in this domain?",
    "How can I join tables in this domain?",
]
PRODUCT_PROMPTS = [
    "What does this data asset contain?",
    "Which columns does this table have?",
    "Who owns this data asset?",
    "Is this data asset certified?",
]
WHO = "analyst_enterprise.meridian@example.com"


@pytest.mark.parametrize("scope", ["global", "domain", "search"])
@pytest.mark.parametrize("question", DOMAIN_PROMPTS)
def test_domain_starters_answer_in_each_portal_scope(client, scope, question):
    products = client.get("/catalogue", headers=client.as_(WHO)).json()["products"]
    ids = [f"{p['unit_id']}.{p['table']}" for p in products if p["unit_id"] == "retail"]
    if scope == "search":
        response = client.post("/search", json={"query": "credit", "limit": 100}, headers=client.as_(WHO))
        assert response.status_code == 200, response.text
        hits = response.json()["hits"]
        ids = [h["id"] for h in hits if h["kind"] == "product"]
        assert ids, hits
    body = {"question": question}
    if scope != "global":
        body["products"] = ids
    response = client.post("/assist", json=body, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["behaviour"] == "catalogue", out
    assert out["answer_text"] and out["sources"]
    assert out["assistant_available"] is False
    if scope != "global":
        assert {s["id"] for s in out["sources"]} <= set(ids)


@pytest.mark.parametrize("question", PRODUCT_PROMPTS)
def test_product_starters_answer_on_monthly_revenue(client, question):
    response = client.post("/assist", json={"question": question, "products": ["retail.monthly_revenue"]},
                           headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["behaviour"] == "catalogue", out
    assert out["answer_text"] and out["sources"]
    assert out["assistant_available"] is False
    assert {s["id"] for s in out["sources"]} == {"retail.monthly_revenue"}
