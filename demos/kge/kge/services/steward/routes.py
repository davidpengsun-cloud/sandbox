"""The steward service's routes: the queue and the decisions."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from kge.pipeline.overlay import OverlayError
from kge.services.ask import DECISIONS
from kge.services.http import Body, needs, served
from kge.services.steward import service

router = APIRouter()


class Decide(Body):
    tag: str
    decision: str
    value: str | None = None
    definition: str | None = None


@router.get("/steward/queue", tags=["internal: governance"])
def steward_queue(who: str = Depends(needs("certification")), status: Literal["pending", "decided"] = "pending"):
    """Internal: the queue: tags to decide, and the cross-reference matches certification did not certify, for one who reads every
    unit a match links. status=decided reads past accept/reject decisions on suggested passage–term links,
    with who and when, by the journal's model release hash, limited to readable passages."""
    if status == "decided":
        try:
            return service.decided(served.engine(), who)
        except OverlayError:
            raise HTTPException(500, "The steward decision history could not be read.") from None
    return service.queue(served.engine(), who)


@router.post("/steward/decide", tags=["internal: governance"])
def steward_decide(d: Decide, who: str = Depends(needs("certification"))):
    """Internal: a decision on a queue item. It is the runtime overlay: written to the graph, then journaled in the client's graph
    store (kge/pipeline/overlay.py), so the next make graph and make pipeline replay it. A decision the journal did not take
    is a 500 that says so."""
    if d.decision not in DECISIONS:
        raise HTTPException(400, f"decision must be one of {', '.join(DECISIONS)}")
    try:
        return service.decide(served.engine(), d.tag, who, d.decision, d.value, d.definition)
    except OverlayError as exc:
        raise HTTPException(500, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except LookupError as exc:
        raise HTTPException(409 if "not waiting" in str(exc) else 404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
