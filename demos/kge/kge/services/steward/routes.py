"""The steward service's routes: the queue and the decisions."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kge.pipeline.overlay import OverlayError
from kge.services.ask import DECISIONS
from kge.services.http import Body, gate, may, principal, served
from kge.services.steward import service

router = APIRouter()


def curating(who: str = Depends(principal), request: Request = None) -> str:
    """The role matrix's curate row (cerebro.md §8.1, §8.4 change 8) in place of today's `certification` check: any
    Cerebro steward, the business team among them, reads the queue and the gaps; the matrix's blank cells are its 403,
    each audited (kge/services/http.py gate)."""
    return gate(request, who, "curate", lambda person: person)


def deciding(who: str = Depends(principal), request: Request = None) -> str:
    """The curate row with today's `certification` check behind it, the check the decision's graph write makes
    (kge/services/ask/engine/graphview.py steward_decide): a Cerebro steward without certification, the business team,
    is refused here and audited refused, never audited allowed and then refused by the graph."""
    return gate(request, who, "curate", lambda person: may(person, "certification"))


class Decide(Body):
    tag: str
    decision: str
    value: str | None = None
    definition: str | None = None


class Gap(BaseModel):
    text: str
    count: int
    last_at: str


class Gaps(BaseModel):
    since: str
    days: int
    total: int
    gaps: list[Gap]


@router.get("/steward/gaps", tags=["internal: governance"], response_model=Gaps, response_model_exclude_unset=True)
def steward_gaps(who: str = Depends(curating)):
    """Internal: gaps people hit: the questions the page and the API could not place in the last seven days, read from the
    unmatched-question log, grouped by their text with a count and when last asked. Who asked is never returned."""
    return service.gaps(served.PROFILE)


@router.get("/steward/queue", tags=["internal: governance"])
def steward_queue(who: str = Depends(curating), status: Literal["pending", "decided"] = "pending"):
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
def steward_decide(d: Decide, who: str = Depends(deciding)):
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
