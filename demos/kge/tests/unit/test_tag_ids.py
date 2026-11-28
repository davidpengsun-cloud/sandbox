"""Proposal IDs retain their byte identity when SHA-1 is marked non-security."""
from types import SimpleNamespace

from kge.pipeline.run import tag_id


def test_proposal_id_is_byte_identical():
    proposal = SimpleNamespace(family="business_meaning", value="Gross Premium", generator="rules", model="none")
    assert tag_id("doc/0", proposal) == "doc/0/business_meaning/d3408bfcf3"
