"""HTTP adapter for read-only assistance."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kge.services import ask as governed
from kge.services.assist import service
from kge.services.assist.models import Assist, AssistResponse
from kge.services.http import needs, served, tool_limits

router = APIRouter()
querying = needs("querying")
tool_limits("assist", assist=Assist)
SERVICE_FUNCTIONS = {"assist": service.assist}


def invoke(body, who):
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
    return service.assist(c, served.PROFILE, body.question, who, body.purpose, body.arm, cfg, products=body.products, history=body.history)


@router.post("/assist", tags=["external: query"], response_model=AssistResponse, response_model_exclude_unset=True)
def assist(body: Assist, who: str = Depends(querying)):
    """Answer from readable catalogue metadata, Search or governed figures and definitions."""
    return invoke(body, who)
