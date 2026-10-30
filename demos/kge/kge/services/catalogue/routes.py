import hmac

from fastapi import APIRouter, Depends, HTTPException, Request

from kge.services.catalogue.models import CatalogueIdentity, CatalogueResponse
from kge.services.catalogue.service import get, identity as release_identity
from kge.services.http import principal, served
from kge.services import identity
from kge.pipeline.settings import settings

router = APIRouter()


def catalogue_for(who: str) -> CatalogueResponse:
    engine = served.engine()
    return get(engine.profile, identity.readable_units(engine, who))


def authenticate_portal(request: Request) -> None:
    current = settings()
    caller_token = request.headers.get("x-caller-token", "").strip()
    if current.TRUSTED_CALLER_AUDIENCE:
        authenticated = bool(caller_token and served.TRUSTED_CALLER.trusts(caller_token))
    elif current.PORTAL_SYNC_TOKEN:
        supplied = request.headers.get("x-portal-sync-token", "")
        try:
            authenticated = hmac.compare_digest(supplied.encode("utf-8"), current.PORTAL_SYNC_TOKEN.encode("utf-8"))
        except UnicodeError:
            authenticated = False
    else:
        raise HTTPException(503, "the portal sync is not configured")
    if not authenticated:
        raise HTTPException(403, "the whole catalogue is available only to the trusted portal service")


def whole_catalogue(request: Request) -> CatalogueResponse:
    """The whole released catalogue for the co-deployed portal service."""
    authenticate_portal(request)
    engine = served.engine()
    return get(engine.profile, list(engine.g.domain()["units"]))


@router.get("/catalogue", tags=["external: knowledge"], response_model=CatalogueResponse, response_model_exclude_unset=True)
def catalogue(who: str = Depends(principal)) -> CatalogueResponse:
    """The governed products the signed-in person may discover."""
    return catalogue_for(who)


@router.get("/catalogue/portal", tags=["internal: platform"], response_model=CatalogueResponse, response_model_exclude_unset=True)
def portal_catalogue(request: Request) -> CatalogueResponse:
    """Internal: the whole released catalogue for the trusted portal service."""
    return whole_catalogue(request)


@router.get("/catalogue/release", tags=["internal: platform"], response_model=CatalogueIdentity, response_model_exclude_unset=True)
def portal_catalogue_release(request: Request) -> CatalogueIdentity:
    """Internal: the released catalogue identity for the trusted portal service."""
    authenticate_portal(request)
    return release_identity(served.engine().profile)
