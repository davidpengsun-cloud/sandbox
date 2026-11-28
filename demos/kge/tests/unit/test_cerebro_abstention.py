"""Cerebro's agent recognises Ask's catalogue decline by its exact words: the literal in the portal's agent.js is KGE's own."""

import re

from kge.pipeline.settings import ROOT
from kge.services.ask.engine.question import CATALOGUE_ABSTENTION

AGENT = ROOT / "portal" / "server" / "src" / "cerebro" / "agent.js"


def test_the_agent_names_asks_catalogue_decline_in_kges_own_words():
    """agent.js's CATALOGUE_ABSTENTION is one plain string literal, equal to KGE's."""
    found = re.findall(r'^export const CATALOGUE_ABSTENTION = "((?:[^"\\]|\\.)*)";$', AGENT.read_text(encoding="utf-8"), re.M)
    assert len(found) == 1
    assert found[0] == CATALOGUE_ABSTENTION
