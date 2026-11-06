"""Stale and private suggestions cannot enter the review queue or replay; review text is escaped."""
import copy
import json
import subprocess

import pytest

from kge.pipeline import overlay
from kge.services.ask.engine.graphview import Graph
from tests.unit.test_page import PAGE, _function, _uncommented


@pytest.mark.parametrize("readiness,text,term,visible", [
    ("citable", "cleared passage", "Fiscal Year", True),
    ("retrieval-internal", "cleared passage", "Fiscal Year", True),
    ("embed-prohibited", "cleared passage", "Fiscal Year", False),
    (None, "cleared passage", "Fiscal Year", False),
    ("citable", "changed passage", "Fiscal Year", False),
    ("citable", "cleared passage", "Removed term", False),
])
def test_queue_excludes_held_changed_and_missing_term_suggestions(readiness, text, term, visible):
    row = {"tag": "suggestion", "family": "suggested_mention", "value": term, "confidence": .81,
           "evidence": overlay.text_sha256("cleared passage"), "doc": "D1", "shelf": "certified",
           "chunk": "D1#0", "passage": text, "readiness": readiness, "unit": ""}
    graph = object.__new__(Graph)
    graph.readable_units = lambda principal: {"retail"}
    graph.q = lambda query, params=None: ([{"name": "Fiscal Year", "definition": "Reporting year"}]
                                        if query.startswith("MATCH (t:Term)") else [] if "MENTIONS" in query else [copy.deepcopy(row)])
    result = graph.chunk_queue("steward")
    assert bool(result) is visible
    if visible:
        assert result[0]["similarity"] == .81
        assert result[0]["passage"] == text
        assert result[0]["definition"] == "Reporting year"
        assert "must decide" in result[0]["evidence"]
        assert "readiness" not in result[0]


@pytest.mark.parametrize("readiness,term_exists,reason", [
    ("citable", True, None),
    ("retrieval-internal", True, None),
    ("embed-prohibited", True, "no longer cleared"),
    (None, True, "no longer cleared"),
    ("citable", False, "no longer in the graph"),
])
def test_replay_refuses_held_chunks_and_removed_terms(readiness, term_exists, reason):
    rec = {"machine": {"family": "suggested_mention", "value": "Fiscal Year"},
           "chunk": {"id": "D1#0", "doc": "D1", "text_sha256": overlay.text_sha256("text")}}

    def query(cypher, params):
        if cypher.startswith("MATCH (d:Document) WHERE"):
            return [{"d": "D1"}]
        if "HAS_CHUNK" in cypher:
            return [{"doc": "D1", "text": "text"}]
        if cypher.startswith("MATCH (t:Term)"):
            return [{"n": "Fiscal Year"}] if term_exists else []
        return [{"readiness": readiness}]

    result = overlay.gone(query, rec)
    assert result is None if reason is None else reason in result


def test_suggestion_review_escapes_term_definition_passage_and_evidence():
    code = _uncommented(PAGE.read_text().rsplit("<script>", 1)[1].split("</script>", 1)[0])
    args, body = _function(code, "said")
    functions = next(line for line in code.splitlines() if line.startswith("const esc =")) + "\n"
    functions += f"function said({', '.join(args)}) {body}"
    attack = '<img src=x onerror="alert(1)">'
    row = {"family": "suggested_mention", "value": attack, "definition": attack, "passage": attack, "evidence": attack}
    result = subprocess.run(["node", "-e", functions + f"\nconsole.log(said({json.dumps(row)}));"], capture_output=True, text=True, check=True)
    assert "<img" not in result.stdout
    assert result.stdout.count("&lt;img") == 4


def test_queue_excludes_a_suggestion_that_already_has_a_link():
    row = {"tag": "suggestion", "family": "suggested_mention", "value": "Fiscal Year", "confidence": .81,
           "evidence": overlay.text_sha256("text"), "doc": "D1", "shelf": "certified",
           "chunk": "D1#0", "passage": "text", "readiness": "citable", "unit": ""}
    graph = object.__new__(Graph)
    graph.readable_units = lambda principal: {"retail"}

    def query(cypher, params=None):
        if "MENTIONS" in cypher:
            return [{"chunk": "D1#0", "term": "Fiscal Year"}]
        if cypher.startswith("MATCH (t:Term)"):
            return [{"name": "Fiscal Year", "definition": "Reporting year"}]
        return [row]

    graph.q = query
    assert graph.chunk_queue("steward") == []


def test_reject_request_cannot_be_used_to_decide_a_suggestion():
    graph = object.__new__(Graph)
    graph.readable_units = lambda principal: {"retail"}
    with pytest.raises(ValueError, match="decision must be one of accept, reject"):
        graph.steward_decide("suggestion", "steward", "reject_requested")


def test_replay_applies_prior_privacy_decision_before_checking_suggestion(monkeypatch):
    ready = False
    seen = []
    privacy = {"decision": "reject", "machine": {"id": "privacy", "family": "privacy"},
               "tag": {"id": "privacy/human"}, "chunk": {"id": "C1"}}
    suggestion = {"decision": "reject", "machine": {"id": "suggestion", "family": "suggested_mention"},
                  "tag": {"id": "suggestion/human"}, "chunk": {"id": "C1"}}

    def gone(query, record):
        if record is suggestion:
            assert ready, "prior privacy corrections must be derived before checking this decision"
        return None

    def rederive(chunk, reason):
        nonlocal ready
        ready = True
        seen.append(chunk)

    monkeypatch.setattr(overlay, "gone", gone)
    monkeypatch.setattr(overlay, "restore", lambda *args: True)
    monkeypatch.setattr(overlay, "has_tag", lambda *args: True)
    monkeypatch.setattr(overlay, "tagged", lambda *args: False)
    result = overlay.replay(lambda *args: [{"n": "human"}], [privacy, suggestion], rederive)
    assert result == {"replayed": 2, "present": 0, "orphans": []}
    assert seen == ["C1", "C1"]


@pytest.mark.parametrize("principal,unit,visible", [
    ("steward", "retail", True),
    ("steward", "corporate", False),
    ("steward", "", True),
    ("steward", None, False),
    (None, "", False),
])
def test_suggestion_passage_requires_the_stewards_unit_access(principal, unit, visible):
    row = {"tag": "suggestion", "family": "suggested_mention", "value": "Fiscal Year", "confidence": .81,
           "evidence": overlay.text_sha256("text"), "doc": "D1", "shelf": "certified",
           "chunk": "D1#0", "passage": "text", "readiness": "citable", "unit": unit}
    graph = object.__new__(Graph)
    graph.readable_units = lambda who: {"retail"}
    graph.q = lambda query, params=None: ([{"name": "Fiscal Year", "definition": "Reporting year"}]
                                        if query.startswith("MATCH (t:Term)") else [] if "MENTIONS" in query else [row])
    assert bool(graph.chunk_queue(principal)) is visible
