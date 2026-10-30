"""The steward service's logic: the queue a steward reads, and a decision, which is the runtime overlay."""
from __future__ import annotations

from kge.pipeline.overlay import OverlayError
from kge.services.ask import DECISIONS, Engine


def queue(engine: Engine, who: str) -> list[dict]:
    """The queue: tags to decide, and the cross-reference matches certification did not certify, for one who reads every
    unit a match links."""
    return engine.g.steward_queue(who)


def decide(engine: Engine, tag: str, who: str, decision: str, value: str | None, definition: str | None) -> dict:
    """A decision on a queue item, written to the graph and journaled in the client's graph store
    (kge/pipeline/overlay.py), then the engine refreshed. A decision not in DECISIONS is a ValueError; one the journal did
    not take is the overlay's error, the engine refreshed all the same; the graph's own refusals are raised as they are."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    try:
        result = engine.g.steward_decide(tag, who, decision, value, definition)
    except OverlayError:
        engine.refresh()
        raise
    engine.refresh()
    return result
