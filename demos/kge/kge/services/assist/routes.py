"""HTTP adapter for read-only assistance."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kge.services import ask as governed
from kge.services.assist import service
from kge.services.assist.models import Assist, AssistResponse
from kge.services.http import engineer_view, matrix, needs, served, tool_limits, tool_matrix

router = APIRouter()
querying = needs("querying")
asking = matrix("ask")
"""The route's check: the role matrix's row 1 (cerebro.md §8.2: assist stays in row 1, since Discover calls it for
everyone), then querying."""
tool_check = tool_matrix("ask", "assist")
"""The MCP tool's check: the same row and today's check as the route's, audited as the tool."""
tool_limits("assist", assist=Assist)
SERVICE_FUNCTIONS = {"assist": service.assist}


def invoke(body, who):
    """The assistance as both transports return it: compact, as Ask's is (response_view), for anyone the role matrix
    does not give plan, SQL and trace (row 7); an engineer's whole."""
    c = served.combined()
    try:
        if body.arm not in governed.PRESETS:
            raise ValueError("Unknown arm")
        cfg = c.default_config(body.arm).with_stages(**(body.stages.model_dump(exclude_unset=True) if body.stages else {}))
        bad = cfg.problems("ask", True)
        if bad:
            raise ValueError("; ".join(bad))
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "problems": [str(exc)]}, status_code=400)
    out = service.assist(c, served.PROFILE, body.question, who, body.purpose, body.arm, cfg, products=body.products, history=body.history)
    return out if engineer_view(who) else governed.response_view(out, True)


@router.post("/assist", tags=["external: query"], response_model=AssistResponse, response_model_exclude_unset=True)
def assist(body: Assist, who: str = Depends(asking)):
    """Answer from readable catalogue metadata, Search or governed figures and definitions. Plan, SQL and trace are the
    engineer's (the role matrix's row 7): anyone else's reply is compact, as /ask's is."""
    return invoke(body, who)
