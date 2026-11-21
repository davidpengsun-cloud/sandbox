"""One outcome per diagnosis (#531): each miss gets exactly one outcome from rules.OUTCOMES, with its route, the run
trace's ledger and the matched or nearest templates handed to improve.

    .venv/bin/python -m pytest tests/unit/test_diagnosis_outcomes.py -q

Synthetic misses, hand-written ledger blocks in the run trace's format (kge/services/ask/engine/planledger.py) and
synthetic shapes only: no pack, no question set file, no held-out record.
"""
from __future__ import annotations

import json

import pytest

from kge.compiler import standards
from kge.improve import edits, gather, rules
from kge.semantics.blocks import CATALOGUE

PINNED_UNEXPRESSIBLE = {"add"}

SHAPES = {
    "s-share-ratio": {"id": "s-share-ratio", "requires": ["share", "ratio"], "slots": [{"name": "m", "kind": "context"}],
                      "recipe": "r1"},
    "s-share-not": {"id": "s-share-not", "requires": ["ratio"], "forbids": ["share"], "recipe": "r2"},
    "s-difference": {"id": "s-difference", "requires": ["difference"], "slots": [{"name": "m", "kind": "context", "count": 2}],
                     "recipe": "r3"},
    "s-from-to": {"id": "s-from-to", "requires": ["difference", "from_to"], "recipe": "r4"},
}


def piece(kind, start, end, operation=None, slot=None):
    return {"kind": kind, "operation": operation, "slot": slot, "start": start, "end": end}


def miss(question, trace=None, **over):
    m = {"source": "ab", "set": "team_synthetic_v1", "id": "S-01", "question": question, "behaviour": "abstain",
         "reason": "", "kind": "figure", "expect": {"value": 10.0}, "unit": "synthetic", "measures": {}, "trace": trace}
    m.update(over)
    return m


@pytest.fixture
def evidence():
    return {
        "certified_terms": {"lease-rentals": {"id": "lease-rentals", "name": "Lease Rentals", "status": "certified"}},
        "existing_aliases": {"lease rentals": "lease-rentals"},
        "candidate_phrases": [],
        "columns": {"leasing.rental.lease_rentals_usd": {
            "column": "leasing.rental.lease_rentals_usd", "unit": "leasing", "table": "rental", "name": "lease_rentals_usd",
            "type": "DECIMAL(14,2)", "description": "Lease rentals billed, in USD; the business calls this lease income",
            "is_key": False, "certified_terms": ["lease-rentals"]}},
        "term_columns": {"lease-rentals": ["leasing.rental.lease_rentals_usd"]},
        "shapes": SHAPES,
    }


def share_miss(**over):
    q = "share of widgets hm"
    trace = {"pieces": [piece("operator", 0, 5, "share"), piece("name", 9, 16, slot="context"),
                        piece("unknown", 17, 19)], "outcome": "result", "applied": [[9, 16]]}
    return miss(q, trace, **over)


def test_unread_unknown_word_is_a_missing_phrase(evidence):
    d = rules.diagnose_miss(share_miss(), evidence)
    assert (d["diagnosis"], d["outcome"], d["route"], d["trace"], d["hypothesis"]) == (
        "missing_phrase", "note", "pack", "read", False)
    assert d["phrase"] == "hm"
    assert d["ledger"]["unknown"] is True
    assert [(u["text"], u["kind"]) for u in d["ledger"]["unapplied"]] == [("share", "operator"), ("hm", "unknown")]
    assert "missing phrase (pack author)" in d["why"] and "'hm'" in d["why"]


def test_unread_word_naming_a_certified_term_is_a_missing_alias_note(evidence):
    q = "lease income for June"
    trace = {"pieces": [piece("unknown", 0, 12), piece("time", 17, 21, slot="period")], "outcome": "result", "applied": [[17, 21]]}
    d = rules.diagnose_miss(miss(q, trace), evidence)
    assert (d["diagnosis"], d["outcome"], d["edit"], d["route"]) == ("missing_alias", "note", None, "pack")
    assert d["matching_comments"][0]["term"] == "lease-rentals"


def test_matched_template_that_still_missed_is_a_template_mismatch(evidence):
    q = "difference between a and b"
    trace = {"pieces": [piece("operator", 0, 10, "difference"), piece("name", 19, 20, slot="context"),
                        piece("name", 25, 26, slot="context")], "outcome": "result", "applied": [[0, 10], [19, 20], [25, 26]]}
    d = rules.diagnose_miss(miss(q, trace, behaviour="figure"), evidence)
    assert (d["diagnosis"], d["outcome"], d["route"]) == ("template_mismatch", "note", "pack")
    assert d["templates"]["matched"] == ["s-difference"]
    assert d["templates"]["nearest"] == []
    assert "s-difference" in d["why"]


def test_expressible_operations_no_shape_covers_are_a_missing_template(evidence):
    q = "share of widgets"
    trace = {"pieces": [piece("operator", 0, 5, "share"), piece("name", 9, 16, slot="context")], "outcome": "result", "applied": [[9, 16]]}
    d = rules.diagnose_miss(miss(q, trace), evidence)
    assert (d["diagnosis"], d["outcome"], d["route"], d["hypothesis"]) == ("missing_template", "note", "pack", False)
    assert d["ledger"] == {"names": ["share"], "slots": {"context": 1}, "unknown": False,
                           "unapplied": [{"text": "share", "start": 0, "end": 5, "kind": "operator", "operation": "share"}],
                           "localized": True, "follow_up": False}
    nearest = d["templates"]["nearest"]
    assert nearest[0] == {"shape": "s-share-ratio", "missing": ["ratio"], "forbidden": [], "unchecked": []}
    assert nearest[1]["shape"] == "s-share-not" and nearest[1]["forbidden"] == ["share"]
    assert "s-from-to" not in [n["shape"] for n in nearest]
    assert "nearest: s-share-ratio needs ratio" in d["why"]
    assert edits.signature(d) == (("share",), (("context", 1),))


def test_marker_requirements_are_unchecked_never_matched(evidence):
    q = "difference from a to b"
    trace = {"pieces": [piece("operator", 0, 10, "difference")], "outcome": "result", "applied": []}
    d = rules.diagnose_miss(miss(q, trace), evidence)
    from_to = next(n for n in d["templates"]["nearest"] if n["shape"] == "s-from-to")
    assert from_to["unchecked"] == ["from_to"] and from_to["missing"] == []
    assert "s-from-to" not in d["templates"]["matched"]


def test_an_operation_no_block_expresses_is_a_building_block_request(evidence):
    q = "a plus b"
    trace = {"pieces": [piece("name", 0, 1, slot="context"), piece("operator", 2, 6, "add"),
                        piece("name", 7, 8, slot="context")], "outcome": "result", "applied": [[0, 1], [7, 8]]}
    d = rules.diagnose_miss(miss(q, trace), evidence)
    assert (d["diagnosis"], d["outcome"], d["route"], d["hypothesis"]) == (
        "missing_building_block", "request", "platform", False)
    assert "no block expresses add" in d["why"] and "'plus'" in d["why"]


@pytest.mark.parametrize("trace,status", [
    (None, "absent"),
    ({"pieces": "malformed"}, "absent"),
    ({"pieces": [piece("name", 0, 1, slot="context")], "outcome": "result", "applied": [[0, 1]]}, "read"),
])
def test_no_evidence_falls_back_to_a_building_block_hypothesis(evidence, trace, status):
    d = rules.diagnose_miss(miss("a for June", trace, reason="Not supported."), evidence)
    assert (d["diagnosis"], d["outcome"], d["route"], d["trace"], d["hypothesis"]) == (
        "missing_building_block", "request", "platform", status, True)
    assert "a hypothesis" in d["why"] and "Not supported." in d["why"]


def test_positions_outside_the_question_make_a_hypothesis(evidence):
    trace = {"pieces": [piece("operator", 0, 5, "share"), piece("unknown", 40, 44)], "outcome": "result", "applied": []}
    d = rules.diagnose_miss(miss("share", trace), evidence)
    assert d["ledger"]["unapplied"][1]["text"] is None
    assert d["hypothesis"] is True


def test_reason_rules_win_with_a_ledger_present(evidence):
    plain = miss("lease income for June 2025", None, reason="No glossary term recognised in the question.")
    traced = {**plain, "trace": share_miss()["trace"]}
    a, b = rules.diagnose_miss(plain, evidence), rules.diagnose_miss(traced, evidence)
    today = ("id", "set", "source", "question", "diagnosis", "outcome", "phrase", "term", "term_name",
             "evidence_column", "matching_comments", "edit", "why")
    assert {k: a[k] for k in today} == {k: b[k] for k in today}
    assert b["edit"] == {"section": "terminology.aliases", "phrase": "lease income", "term": "lease-rentals"}
    assert (a["trace"], b["trace"]) == ("absent", "read")


def test_every_outcome_is_one_of_the_list_with_its_route(evidence):
    cases = [share_miss(), miss("a plus b", {"pieces": [piece("operator", 2, 6, "add")], "outcome": "result", "applied": []}),
             miss("x", None), miss("share", {"pieces": [piece("operator", 0, 5, "share")], "outcome": "result", "applied": []}),
             miss("q", None, reason="no declared join path from a.t to b.u")]
    for d in rules.diagnose_all(cases, [], evidence)["ab_diagnoses"]:
        assert d["diagnosis"] in rules.OUTCOMES
        assert d["route"] == rules.ROUTES[d["diagnosis"]]
        assert "needs_code" not in json.dumps(d)
    assert set(rules.ROUTES) == set(rules.OUTCOMES) and len(rules.OUTCOMES) == 8


def test_ledger_signature_ignores_names_numbers_and_periods(evidence):
    def traced(question, extra=()):
        pieces = [piece("operator", 0, 5, "share"), piece("name", 9, 16, slot="context"), *extra]
        return rules.diagnose_miss(miss(question, {"pieces": pieces, "outcome": "result", "applied": [[9, 16]]}), evidence)
    a = traced("share of widgets")
    b = traced("share of gadgets")
    c = traced("share of gadgets in 2025", [piece("time", 20, 24, slot="period")])
    d = traced("share of gadgets in 2025", [piece("time", 20, 24, slot="period"), piece("operator", 0, 0 + 5, "desc")])
    assert edits.signature(a) == edits.signature(b)
    assert edits.signature(c) != edits.signature(a)
    assert edits.signature(d) != edits.signature(c)


def test_the_block_catalogue_pin():
    assert all(rules.expressible(name) for name in CATALOGUE)
    assert {op for op in standards.load().operations if not rules.expressible(op)} == PINNED_UNEXPRESSIBLE


def test_hand_over_through_the_unchanged_propose(evidence):
    share = rules.diagnose_miss(miss("share of widgets", {"pieces": [piece("operator", 0, 5, "share")], "outcome": "result", "applied": []},
                                     id="S-T"), evidence)
    cases = [rules.diagnose_miss(share_miss(id="S-P"), evidence), share,
             rules.diagnose_miss(miss("difference between a and b", {"pieces": [
                 piece("operator", 0, 10, "difference"), piece("name", 19, 20, slot="context"),
                 piece("name", 25, 26, slot="context")], "outcome": "result", "applied": [[0, 26]]}, id="S-M"), evidence),
             rules.diagnose_miss(miss("a plus b", {"pieces": [piece("operator", 2, 6, "add")], "outcome": "result", "applied": []}, id="S-B"),
                                 evidence),
             rules.diagnose_miss(miss("q", None, id="S-J", reason="no declared join path from a.t to b.u"), evidence)]
    out = edits.propose(cases, [], evidence, llm=None)
    notes = {n["id"]: n for n in out["notes"]}
    requests = {r["id"]: r for r in out["requests"]}
    assert set(notes) == {"S-P", "S-T", "S-M"} and set(requests) == {"S-B", "S-J"}
    assert "'hm'" in notes["S-P"]["why"]
    assert "s-share-ratio" in notes["S-T"]["why"] and "'share'" in notes["S-T"]["why"]
    assert "'plus'" in requests["S-B"]["why"]
    assert out["edits"] == []


def test_same_signature_template_misses_reach_the_template_branch(evidence):
    questions = ["share of widgets", "share of gadgets", "share of sprockets", "share of gears"]
    group = [rules.diagnose_miss(miss(q, {"pieces": [piece("operator", 0, 5, "share"), piece("name", 9, len(q), slot="context")],
                                          "outcome": "result", "applied": [[9, len(q)]]}, id=f"S-{k}"), evidence)
             for k, q in enumerate(questions)]
    assert {d["diagnosis"] for d in group} == {"missing_template"}
    pre = standards.Preload("1.1.0", {}, {}, {}, {}, operations=("share", "ratio"))
    out = edits.propose(group, [], evidence, llm=None, templates=True, preload=pre)
    assert any("share the ledger signature" in n["why"] for n in out["notes"])


def test_gather_helpers():
    assert gather.trace_of({"run_trace": {"ledger": {"pieces": [], "outcome": "result", "applied": []}}}) == {"pieces": [], "outcome": "result", "applied": []}
    assert gather.trace_of({"run_trace": {"stages": []}}) is None
    assert gather.trace_of({}) is None
    pre = standards.Preload("1.1.0", {}, {}, {}, {}, shapes={"base": {"id": "base"}})
    got = gather.shape_declarations({"shapes": [{"id": "pack-one"}, "junk", {"no": "id"}]}, pre)
    assert got == {"base": {"id": "base"}, "pack-one": {"id": "pack-one"}}


def test_a_decline_blames_only_the_spans_its_reading_could_not_read(evidence):
    q = "share of widgets by region"
    pieces = [piece("operator", 0, 5, "share"), piece("name", 9, 16, slot="context"), piece("name", 17, 26, "dimension", "dimension")]
    d = rules.diagnose_miss(miss(q, {"pieces": pieces, "applied": [], "unread": [[0, 5]], "outcome": "abstain",
                                     "reply": "abstain"}), evidence)
    assert (d["diagnosis"], d["hypothesis"]) == ("missing_template", False)
    assert [u["text"] for u in d["ledger"]["unapplied"]] == ["share"]
    assert d["ledger"]["localized"] is True


def test_a_decline_with_no_unread_span_is_a_building_block_hypothesis(evidence):
    q = "share of widgets"
    d = rules.diagnose_miss(miss(q, {"pieces": [piece("operator", 0, 5, "share")], "applied": [], "unread": [],
                                     "outcome": "refuse", "reply": "propose"}), evidence)
    assert (d["diagnosis"], d["outcome"], d["hypothesis"]) == ("missing_building_block", "request", True)
    assert d["ledger"]["localized"] is False and d["ledger"]["unapplied"] == []
    assert "named no unread span" in d["why"]


def test_an_unlocalized_decline_hands_over_only_its_unknown_words(evidence):
    q = "share of widgets hm"
    d = rules.diagnose_miss(miss(q, {"pieces": [piece("operator", 0, 5, "share"), piece("unknown", 17, 19)], "applied": [],
                                     "unread": [], "outcome": "abstain", "reply": "abstain"}), evidence)
    assert (d["diagnosis"], d["phrase"]) == ("missing_phrase", "hm")
    assert [u["text"] for u in d["ledger"]["unapplied"]] == ["hm"]


def test_a_follow_up_ledger_gives_hypotheses(evidence):
    q = "share of widgets"
    d = rules.diagnose_miss(miss(q, {"pieces": [piece("operator", 0, 5, "share")], "applied": [], "outcome": "result",
                                     "follow_up": True}), evidence)
    assert (d["diagnosis"], d["hypothesis"]) == ("missing_template", True)


def test_any_unread_word_naming_a_certified_term_makes_an_alias_note(evidence):
    q = "hm lease income"
    d = rules.diagnose_miss(miss(q, {"pieces": [piece("unknown", 0, 2), piece("unknown", 3, 15)], "applied": [],
                                     "outcome": "result"}), evidence)
    assert (d["diagnosis"], d["phrase"]) == ("missing_alias", "lease income")


def test_out_of_range_spans_never_mark_a_reason_rule_a_hypothesis(evidence):
    m = miss("lease income for June 2025", {"pieces": [piece("unknown", 80, 84)], "applied": [], "outcome": "result"},
             reason="No glossary term recognised in the question.")
    d = rules.diagnose_miss(m, evidence)
    assert (d["diagnosis"], d["outcome"], d["hypothesis"]) == ("missing_alias", "proposal", False)
