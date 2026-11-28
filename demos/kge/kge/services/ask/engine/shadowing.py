"""CPU-only comparison of the two rules adapters for a single request.

The caller owns request lifetime, the checked-plan function and any durable observation. This module does not execute
the plan, open a warehouse or append to the shadow log. The coded reply returned to the caller is the unmodified one:
the checked copies are solely comparison evidence and cannot change what the existing rules path serves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from kge.semantics.plan import Abstain, Clarify, PlanReply, Propose
from kge.services.ask.engine import graphplanner, rulesport, shadow


@dataclass(frozen=True)
class Comparison:
    """The replies as produced, the checked replies for comparison, and graph shape ids in their match order."""

    coded: PlanReply
    graph: PlanReply
    checked_coded: PlanReply
    checked_graph: PlanReply
    graph_shapes: tuple[str, ...]

    @property
    def outcome(self) -> str:
        """The comparison of the checked plans, not of the answers or their SQL."""
        return shadow.compare(self.checked_graph, self.checked_coded)

    def observe(self, *, release, served, pieces, vocab, fallback=False, takeover=False, new_phrase=False,
                markers=frozenset(), mirrors=None, now=None, new_id=None):
        """Make one bounded, redacted observation; the caller decides if and where to append it."""
        return shadow.observe(release=release, graph=self.checked_graph, coded=self.checked_coded,
                              graph_shapes=self.graph_shapes, served=served, fallback=fallback, takeover=takeover,
                              new_phrase=new_phrase, pieces=pieces, vocab=vocab, markers=markers,
                              mirrors={} if mirrors is None else mirrors, now=now, new_id=new_id)


class Shadowing:
    """Read both planners once, checking their replies without executing them.

    A previously obtained coded reply can be supplied so that comparison does not run the coded planner twice. A
    caller-supplied `check` applies the same plan checks as the serving path and returns a plan reply; it must never
    execute a warehouse query. The graph adapter returns its matched shape ids with its reply.
    """

    def __init__(self, coded=None, graph=None):
        self.coded = coded if coded is not None else rulesport.rules_planner('coded')
        self.graph = graph if graph is not None else graphplanner.GRAPH

    def read(self, question, *, check: Callable[[PlanReply], PlanReply], coded=None) -> Comparison:
        coded_reply = rulesport.planned(self.coded, question) if coded is None else coded
        if not isinstance(coded_reply, (Propose, Clarify, Abstain)):
            raise TypeError('The coded rules planner must return a plan reply.')
        graph_reply, used = self.graph.read(question)
        if not isinstance(graph_reply, (Propose, Clarify, Abstain)):
            raise TypeError('The graph rules planner must return a plan reply.')
        checked_coded, checked_graph = check(coded_reply), check(graph_reply)
        if not isinstance(checked_coded, (Propose, Clarify, Abstain)) or not isinstance(checked_graph, (Propose, Clarify, Abstain)):
            raise TypeError('The plan check must return a plan reply for each adapter.')
        return Comparison(coded_reply, graph_reply, checked_coded, checked_graph, tuple(dict.fromkeys(used)))
