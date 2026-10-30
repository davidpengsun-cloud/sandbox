"""The tools' routes: the page, the client's logo, the health check, the onboarding guide, the sign-in mode, the presets,
the summary, the profile, the versions and the SQL check."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from kge.adapters.brand import LOGO_POLICY
from kge.pipeline.settings import ROOT
from kge.services.http import Body, demo_allowed, needs, principal, served
from kge.services.tools import service

router = APIRouter()


class SQLCheck(Body):
    query: str = Field(max_length=20_000)


class StatusResponse(BaseModel):
    status: str


class HealthResponse(StatusResponse):
    version: str


@router.get("/", tags=["internal: platform"])
def root():
    """Internal: serve the KGE team's page."""
    return FileResponse(ROOT / "ui" / "index.html")


@router.get("/brand/logo", tags=["internal: platform"], response_class=Response)
@router.get("/brand/logo.svg", tags=["internal: platform"], response_class=Response)
def brand_logo():
    """Internal: the client's logo, for the page's bar, without sign-in: KGE_LOGO's file, or the portal's logo when
    PORTAL_URL names a portal configured with one; 404 when there is none, and the page shows the client's name."""
    logo = served.BRAND.logo
    if logo is None:
        raise HTTPException(404, "No logo is configured: set KGE_LOGO, or PORTAL_URL to a portal that has one.")
    return Response(logo.content, media_type=logo.media_type, headers={
        "Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff", "X-Brand-Logo": "configured",
        "Content-Security-Policy": LOGO_POLICY})


@router.get("/health", tags=["internal: platform"], response_model=StatusResponse, response_model_exclude_unset=True)
def health():
    """Internal: report service liveness."""
    return {"status": "ok"}


@router.get("/healthz", tags=["internal: platform"], response_model=HealthResponse, response_model_exclude_unset=True)
def healthz():
    """Internal: report service health and version."""
    return {"status": "ok", "version": served.VERSION}


@router.head("/health", tags=["internal: platform"])
@router.head("/healthz", tags=["internal: platform"])
def healthz_head():
    """Internal: the health check for a probe that asks with HEAD: 200, with no body, as GET is."""
    return Response(status_code=200)


@router.get("/onboarding-guide", tags=["internal: platform"], response_class=HTMLResponse)
def onboarding_guide():
    """Internal: serve the onboarding guide."""
    return service.onboarding_guide()


@router.get("/mode", tags=["internal: platform"])
def mode(request: Request):
    """Internal: whether this caller may sign in by name (demo mode), and, with SIGNIN=passwords, that one signs in with a password.
    The page hides the token box for either, and shows the password box for the second."""
    return {"demo_mode": demo_allowed(request), **({"passwords": True} if served.PASSWORDS else {})}


@router.get("/presets", tags=["internal: platform"])
def presets():
    """Internal: the arms, as run configs, and the values each stage may take. paths: the endpoints a preset runs on as it is.
    agent_arms: the reference agent's two, which run on /agent, and what runs them (rules, mock or model)."""
    return service.presets(served.combined().llm is not None, served.agent(), served.DEFAULT_ARM, served.SIDES)


@router.get("/summary", tags=["internal: platform"])
def summary():
    """Internal: summarise the served profile."""
    return service.summary(served.PROFILE)


@router.get("/profile", tags=["internal: platform"])
def profile():
    """Internal: whom this process serves, as the page names it: the profile and its client, the pack's title, the client's
    units in the pack's order, the artifact whose graph it serves, and the landed release that graph was loaded from
    (its id and as-of date; None if it names none); and the shared bar's shell: whether a logo is served at /brand/logo
    and the discovery portal's address (PORTAL_URL), if one is set."""
    result = service.served_profile(served.PROFILE, served.engine())
    result["shell"] = served.BRAND.shell()
    if served.SERVE_HASH:
        result["preview"] = {"hash": served.SERVE_HASH, "released": served.SERVE_RELEASED,
                             "banner": f"Artifact {served.SERVE_HASH}, released" if served.SERVE_RELEASED else f"Candidate {served.SERVE_HASH}, not released"}
    return result


@router.get("/versions", tags=["internal: platform"])
def versions(_who: str = Depends(principal)):
    """Internal: the released versions of the graph in order: each version's hash, pack version, release date, sign-off principal,
    learnable score, held-out score, and change note."""
    return service.versions(served.PROFILE)


@router.post("/sql/check", tags=["internal: platform"])
def sql_check(body: SQLCheck, who: str = Depends(needs("querying"))):
    """Internal: the SQL gate alone, as the caller: parse, one statement, read-only, the allowlist of each warehouse the principal
    may read, restricted columns never, then the warehouse's plan (EXPLAIN, or BigQuery's dry run). The query is never
    run. Input that does not parse is a refusal with its reason, not an error."""
    return service.check_sql(served.engine(), body.query, who)
