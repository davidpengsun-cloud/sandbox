"""Diagnosis and rule-based proposals for the five miss classes."""
import pytest

from kge.improve import edits, rules


@pytest.fixture
def evidence():
    return {
        "certified_terms": {
            "lease-rentals": {"id": "lease-rentals", "name": "Lease Rentals", "status": "certified"},
            "revenue": {"id": "revenue", "name": "Revenue", "status": "certified"},
        },
        "existing_aliases": {
            "lease rentals": "lease-rentals",
            "revenue": "revenue",
        },
        "candidate_phrases": ["customer satisfaction"],
        "columns": {
            "leasing.rental.lease_rentals_usd": {
                "column": "leasing.rental.lease_rentals_usd",
                "unit": "leasing",
                "table": "rental",
                "name": "lease_rentals_usd",
                "type": "DECIMAL(14,2)",
                "description": "Lease rentals billed, in USD; the business calls this lease income",
                "is_key": False,
                "certified_terms": ["lease-rentals"],
            },
            "leasing.rental.late_fee_usd": {
                "column": "leasing.rental.late_fee_usd",
                "unit": "leasing",
                "table": "rental",
                "name": "late_fee_usd",
                "type": "DECIMAL(14,2)",
                "description": "Late payment fee charged in USD",
                "is_key": False,
                "certified_terms": [],
            },
        },
        "term_columns": {"lease-rentals": ["leasing.rental.lease_rentals_usd"]},
    }


def test_five_diagnosis_classes_and_propose_outcomes(evidence):
    ab_misses = [
        {
            "id": "TEAM-LEASING-01",
            "set": "team_leasing_v1",
            "question": "Lease income for June 2025",
            "behaviour": "abstain",
            "reason": "No glossary term recognised in the question.",
        },
        {
            "id": "MISS-UNMAPPED",
            "set": "golden_v1",
            "question": "Late fee for June 2025 in leasing",
            "behaviour": "abstain",
            "reason": "'Late Fee' is not certified for leasing.",
        },
        {
            "id": "MISS-JOIN",
            "set": "golden_v1",
            "question": "Lease rentals by region for June 2025",
            "behaviour": "abstain",
            "reason": "no declared join path from leasing.rental to leasing.region",
        },
        {
            "id": "MISS-UNCLEAR",
            "set": "golden_v1",
            "question": "Revenue for June 2025",
            "behaviour": "figure",
            "column": "gross_revenue_usd",
            "expect": {"column": "net_revenue_usd"},
            "measures": {"figure_ok": False},
            "reason": "",
        },
        {
            "id": "MISS-CODE",
            "set": "golden_v1",
            "question": "Share of customers across units",
            "behaviour": "abstain",
            "reason": "Cross-unit ratio is not supported by the planner.",
        },
    ]
    page_misses = [
        {
            "source": "page",
            "question": "What was lease income in June 2025?",
            "reason": "No glossary term recognised in the question.",
            "count": 2,
        }
    ]

    diag = rules.diagnose_all(ab_misses, page_misses, evidence)
    classes = [d["diagnosis"] for d in diag["ab_diagnoses"]]
    assert classes == ["missing_alias", "unmapped_column", "missing_join", "unclear_measure", "needs_code"]

    out = edits.propose(diag["ab_diagnoses"], diag["page_diagnoses"], evidence, llm=None)
    assert out["proposer"] == "rules"
    assert len(out["edits"]) == 1
    ed = out["edits"][0]
    assert ed["phrase"] == "lease income"
    assert ed["term"] == "lease-rentals"
    assert ed["items"] == ["TEAM-LEASING-01"]
    assert ed["also_answers"] == ["What was lease income in June 2025?"]
    assert "asked 2 times on the page" in ed["evidence"]

    note_ids = {n["id"] for n in out["notes"]}
    req_ids = {r["id"] for r in out["requests"]}
    assert {"MISS-UNMAPPED", "MISS-UNCLEAR"}.issubset(note_ids)
    assert {"MISS-JOIN", "MISS-CODE"} == req_ids


def test_already_held_phrase_is_skipped(evidence):
    miss = {
        "id": "M-HELD",
        "set": "golden_v1",
        "question": "Customer satisfaction for June 2025",
        "behaviour": "abstain",
        "reason": "No glossary term recognised in the question.",
    }
    d = rules.diagnose_miss(miss, evidence)
    assert d["diagnosis"] == "missing_alias"
    assert d["outcome"] == "already_held"
    assert d["edit"] is None
