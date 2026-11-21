"""Certification guidance belongs to missing certification, never page context or figure planning."""
import pytest

from kge.services.ask.engine.wording import abstention_text, refusal_text


@pytest.mark.parametrize("reason", [
    "No EUR conversion standard exists: certified figures are in USD.",
    "Please give a valid period: June 2025.",
    "March 2025 is not yet released: its figures are released on April 5.",
    "No glossary term recognised in the question.",
])
def test_governance_or_question_limits_do_not_send_the_asker_for_certification(reason):
    text = abstention_text({"behaviour": "abstain", "reason": reason})
    assert reason in text
    assert "Ask the data steward" not in text


@pytest.mark.parametrize("reason", [
    "'Revenue' is not certified for this unit.",
    "There is no certified definition to cite.",
    "'Revenue' has no certified implementation in any warehouse.",
    "'New metric' is not a glossary term.",
])
def test_uncertified_terms_send_the_asker_to_the_data_steward(reason):
    assert "Ask the data steward" in abstention_text({"behaviour": "abstain", "reason": reason})


def test_access_and_sql_gate_refusals_do_not_send_the_asker_for_certification():
    denied = refusal_text({"behaviour": "refuse", "denied_warehouses": ["wealth"]})
    assert "business team" in denied
    assert "no warehouse was queried" in denied
    assert "Ask the data steward" not in denied
    sql = refusal_text({"behaviour": "refuse", "reason": "SQL gate: unknown column"})
    assert "did not run" in sql
    assert "Ask the data steward" not in sql
