"""HTTP adapter for the knowledge search capability."""
from fastapi import APIRouter, Depends

from kge.services.http import may, principal, served
from kge.services.search import service
from kge.services.search.models import Search, SearchResponse

router = APIRouter()


def querying(who: str = Depends(principal)) -> str:
    return may(who, "querying")


@router.post("/search", response_model=SearchResponse, response_model_exclude_unset=True,
             tags=["external: knowledge"])
def search(body: Search, who: str = Depends(querying)) -> SearchResponse:
    return service.search(served.engine(), who, body)
