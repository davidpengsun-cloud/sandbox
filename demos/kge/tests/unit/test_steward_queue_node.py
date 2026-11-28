"""A steward queue item names the graph node Curate opens it on, by the graph read's id rule alone (cerebro.md §6.2)."""
from types import SimpleNamespace

from kge.services.graph import node_id
from kge.services.steward import service


def _queue(items):
    engine = SimpleNamespace(g=SimpleNamespace(steward_queue=lambda who: items))
    return service.queue(engine, "steward_glossary.meridian@example.com")


def test_a_document_item_opens_on_the_graph_reads_document_node():
    [row] = _queue([{"tag": "tag:1", "family": "document_tier", "doc": "pricing-policy", "chunk": "pricing-policy#3"}])
    assert row["node"] == node_id("document", "pricing-policy")
    assert row["kind"] == "documents"


def test_a_node_the_store_names_is_kept():
    [row] = _queue([{"tag": "tag:2", "family": "suggested_mention", "doc": "x", "node": "term:churn"}])
    assert row["node"] == "term:churn"


def test_a_cross_reference_match_an_off_passage_item_or_one_without_a_document_opens_on_nothing():
    rows = _queue([{"tag": "tag:3", "family": "xref_match", "doc": "retail.client", "chunk": None},
                   {"tag": "tag:4", "family": "candidate_term"},
                   {"tag": "tag:5", "family": "business_term", "doc": "retail draft", "chunk": None},
                   {"tag": "tag:6", "family": "suggested_mention", "doc": "retail.client.segment", "chunk": None}])
    assert ["node" in row for row in rows] == [False, False, False, False]
