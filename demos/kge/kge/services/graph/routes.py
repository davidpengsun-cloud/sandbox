"""The graph read's HTTP adapter."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from kge.services.graph import service
from kge.services.graph.models import Explore, ExploreQuestion, GraphPage
from kge.services.http import matrix, may, principal, served, tool_matrix

router = APIRouter()
reading = matrix("graph")


def querying(who: str = Depends(principal)) -> str:
    """Today's process check, behind the role matrix's, the same on HTTP and MCP."""
    return may(who, "querying")


def tool_check(tool_name: str):
    """An MCP tool's check: the role matrix's row 2, then querying, as the routes'."""
    return tool_matrix("graph", tool_name)


def graph_page(who: str, node: str | None, cursor: str | None, limit: int) -> GraphPage:
    """The page, or the HTTP error a route and a tool both give: 400 for a cursor this service did not give, 404 for a
    node the person may not see, worded as for one that does not exist."""
    try:
        return service.read(served.engine(), who, node=node, cursor=cursor, limit=limit)
    except service.BadCursor as exc:
        raise HTTPException(400, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


def explore_node(who: str, node: str) -> Explore:
    """The node's summary and deep link, or the 404 the graph read gives for a node the person may not see."""
    try:
        return service.explore(served.engine(), who, node=node)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


def explore_question(who: str, question: str) -> Explore:
    """The question's summary and deep link. The question travels in a body, never a URL, so no access log holds it."""
    return service.explore(served.engine(), who, question=question)


@router.get("/graph", tags=["external: knowledge"], response_model=GraphPage, response_model_exclude_unset=True)
def graph(who: str = Depends(reading),
          node: Annotated[str | None, Query(min_length=1, max_length=2000)] = None,
          cursor: Annotated[str | None, Query(min_length=1, max_length=4000)] = None,
          limit: Annotated[int, Query(ge=1, le=service.MAX_LIMIT)] = service.DEFAULT_LIMIT) -> GraphPage:
    """The graph's terms, tables, columns and documents the signed-in person may read, and the links between them, each
    certified, proposed or inferred with the reason it exists, with the domains, communities and people the catalogue names
    for them; no figures. Paged by a cursor, absent on the last page;
    with node, that node's neighbourhood."""
    return graph_page(who, node, cursor, limit)


@router.get("/graph/explore", tags=["external: knowledge"], response_model=Explore, response_model_exclude_unset=True)
def explore(node: Annotated[str, Query(min_length=1, max_length=2000)],
            who: str = Depends(reading)) -> Explore:
    """A short summary of a graph node's neighbourhood the signed-in person may read, with no figures, and a deep link
    that opens the graph on the node. A question's goes by POST /graph/explore."""
    return explore_node(who, node)


@router.post("/graph/explore", tags=["external: knowledge"], response_model=Explore, response_model_exclude_unset=True)
def explore_asked(body: ExploreQuestion, who: str = Depends(reading)) -> Explore:
    """A short summary of a question's neighbourhood the signed-in person may read, with no figures, and a deep link that
    opens the graph on the node the question names best. The question is in the body, so it never reaches an access
    log."""
    return explore_question(who, body.question)
