from fastapi import APIRouter, Depends, Request

from kge.services.catalogue.models import CatalogueIdentity, CatalogueResponse
from kge.services.catalogue.service import get, identity as release_identity
from kge.services.http import principal, served, trusted_portal
from kge.services import identity

router = APIRouter()


def catalogue_for(who: str) -> CatalogueResponse:
    engine = served.engine()
    return get(engine.profile, identity.readable_units(engine, who), engine.g.catalogue().figures)


def authenticate_portal(request: Request) -> None:
    trusted_portal(request, "the whole catalogue is available only to the trusted portal service")


def whole_catalogue(request: Request) -> CatalogueResponse:
    """The whole released catalogue for the co-deployed portal service."""
    authenticate_portal(request)
    engine = served.engine()
    return get(engine.profile, list(engine.g.domain()["units"]), engine.g.catalogue().figures, portal=True)


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
    engine = served.engine()
    return release_identity(engine.profile, engine.g.catalogue().figures)
