"""Shared metadata questions preserve portal-alone decisions and governed Assist context fallback."""
import json
from pathlib import Path

import pytest

from tests.integration.test_answer_text import client as client

CASES = json.loads((Path(__file__).parents[1] / "fixtures/spec_portal_ask_contract.json").read_text())
WHO = "analyst_enterprise.meridian@example.com"


@pytest.fixture(scope="module")
def catalogue(client):
    response = client.get("/catalogue", headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    return {product["id"]: product for product in response.json()["products"]}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_portal_ask_contract_on_governed_assist(client, catalogue, case):
    body = {"question": case["question"]}
    if "products" in case:
        body["products"] = case["products"]
    response = client.post("/assist", json=body, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    answer = response.json()
    expected = case.get("governed", case)
    assert answer["assistant_available"] is False
    assert answer["answer_text"]
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")
    assert sorted(source["id"] for source in answer["sources"]) == sorted(expected["expected_products"])
    if expected["decision"] == "abstain":
        assert answer["behaviour"] == "abstain", answer
        return
    assert answer["behaviour"] == "catalogue", answer
    assert answer["source"] == "catalogue"
    for source in answer["sources"]:
        product = catalogue[source["id"]]
        assert source["kind"] == "catalogue"
        assert source["title"] == product["name"]
        assert source["unit"] == product["unit_id"]
        assert product["name"] in answer["answer_text"]
        assert product["id"] in answer["answer_text"]
        if expected["decision"] == "answer-owner":
            assert product["product_owner"] and product["business_owner"]
            assert f"Product owner: {product['product_owner']}" in answer["answer_text"]
            assert f"Business owner: {product['business_owner']}" in answer["answer_text"]
        else:
            assert expected["decision"] == "answer-fields"
            assert product["fields"]
            for field in product["fields"]:
                assert f"{field['name']} ({field['business_name']})" in answer["answer_text"]
