"""HTTP adapter for the knowledge search capability."""
from fastapi import APIRouter, Depends

from kge.services.http import matrix, may, principal, served, tool_matrix
from kge.services.search import service
from kge.services.search.models import Search, SearchResponse

router = APIRouter()
asking = matrix("ask")
"""The route's check: the role matrix's row 1 (cerebro.md §8.2: search stays in row 1, since Discover calls it for
everyone), then querying."""
tool_check = tool_matrix("ask", "search")
"""The MCP tool's check: the same row and today's check as the route's, audited as the tool."""


def querying(who: str = Depends(principal)) -> str:
    """Today's process check, behind the role matrix's, the same on HTTP and MCP."""
    return may(who, "querying")


@router.post("/search", response_model=SearchResponse, response_model_exclude_unset=True,
             tags=["external: knowledge"])
def search(body: Search, who: str = Depends(asking)) -> SearchResponse:
    """Rank products, terms, metrics, columns and documents the signed-in person may read, with the reason for each match."""
    return service.search(served.engine(), who, body)
