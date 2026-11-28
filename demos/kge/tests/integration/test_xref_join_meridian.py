"""The certified client cross-reference (client_xref, pack.yaml's joins) on the built Meridian graph's read: it joins
retail.customer to wealth.HSHLD_DIM_1 through the curated xref.client_xref, which no person reads. One who reads Retail
and Wealth sees one certified joins link naming the rule; one who reads only one of them sees none. The rule shares its
id with the through table's own name, so the read names the rule, never xref.client_xref, and no xref node or link end.

Reads the demo's build (make graph pipeline). Stop the API first."""
from __future__ import annotations

import json

import pytest

from kge.services import graph as graph_read
from tests.scratch import open_engine

BOTH = "steward.meridian@example.com"
RETAIL_ONLY = "analyst_retail.meridian@example.com"
WEALTH_ONLY = "advisor_wealth.meridian@example.com"
ENDS = {"table:retail.customer", "table:wealth.HSHLD_DIM_1"}
XREF = ("table:xref.", "column:xref.")


@pytest.fixture(scope="module")
def eng():
    e = open_engine()
    yield e
    e.close()


def pages(eng, who: str, node: str | None = None) -> list:
    out, cursor = [], None
    while True:
        page = graph_read.read(eng, who, node=node, cursor=cursor, limit=1000)
        out.append(page)
        if not page.cursor:
            return out
        cursor = page.cursor


def test_one_who_reads_retail_and_wealth_sees_client_xref_as_one_certified_joins_link(eng):
    assert set(eng.g.readable_units(BOTH)) == {"retail", "wealth"}
    read = pages(eng, BOTH)
    links = [link for page in read for link in page.links]
    held = [link for link in links if link.kind == "joins" and {link.source, link.target} == ENDS]
    assert len(held) == 1, held
    assert held[0].status == "certified" and "client_xref" in held[0].why
    nodes = {node.id for page in read for node in page.nodes}
    assert ENDS <= nodes
    assert not any(node.startswith(XREF) for node in nodes)
    assert not any(end.startswith(XREF) for link in links for end in (link.source, link.target))
    near = {end: pages(eng, BOTH, end) for end in sorted(ENDS)}
    assert all(any({link.source, link.target} == ENDS for page in held_near for link in page.links)
               for held_near in near.values())
    text = json.dumps([page.model_dump() for page in read + sum(near.values(), [])])
    assert "xref.client_xref" not in text and "table:xref" not in text


@pytest.mark.parametrize("who, seen, unseen", [(RETAIL_ONLY, "retail", "wealth"), (WEALTH_ONLY, "wealth", "retail")])
def test_one_who_reads_one_end_alone_sees_no_trace_of_client_xref(eng, who, seen, unseen):
    assert set(eng.g.readable_units(who)) == {seen}
    end = next(e for e in ENDS if e.startswith(f"table:{seen}."))
    read = pages(eng, who) + pages(eng, who, end)
    text = json.dumps([page.model_dump() for page in read])
    assert "client_xref" not in text
    assert f"table:{unseen}." not in text and "table:xref" not in text
