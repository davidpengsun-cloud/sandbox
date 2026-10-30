"""The identity service's routes: a session opened with a token, a password or, in demo mode, a name;
sign-out; who the caller is; and who exists."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from kge.services.http import Body, demo_allowed, principal, served
from kge.services.identity import service
from kge.services.identity.models import BearerToken, ReadableUnits
from kge.services.identity.service import person

router = APIRouter()


class SignIn(Body):
    token: str


class PasswordSignIn(Body):
    username: str = Field(max_length=128)
    password: str = Field(max_length=256)


class DemoSignIn(Body):
    principal: str


@router.post("/session", tags=["external: identity"])
def sign_in(s: SignIn | PasswordSignIn, request: Request):
    if isinstance(s, PasswordSignIn):
        return password_sign_in(s, request)
    opened = served.ids().open_session(s.token)
    if opened is None:
        raise HTTPException(401, "unknown token")
    return signed_in(request, *opened)


def signed_in(request: Request, sid: str, who: str) -> JSONResponse:
    """whoami for who, with the session's cookie."""
    resp = JSONResponse(whoami_for(who))
    resp.set_cookie(served.COOKIE, sid, **cookie_flags(request))
    return resp


def cookie_flags(request: Request) -> dict:
    """The session cookie's flags: HttpOnly, SameSite strict, and Secure when the request came over HTTPS (behind a proxy
    that ends TLS, the scheme uvicorn takes from its X-Forwarded-Proto, with --proxy-headers and --forwarded-allow-ips)."""
    return {"httponly": True, "samesite": "strict", "secure": request.url.scheme == "https"}


CALLER_TOKEN = "x-caller-token"
CLIENT_ADDRESS = "x-client-address"


def client_address(request: Request) -> str:
    """The address a sign-in is counted against: the one a trusted caller names in X-Client-Address, where the request
    carries a caller token (X-Caller-Token) the trusted caller port verifies (served.TRUSTED_CALLER: the discovery portal
    on the pilot, which counts each browser's sign-ins by the address Cloud Run gives it); else the caller's own, as
    uvicorn gives it, with or without either header."""
    own = request.client.host if request.client else ""
    named = request.headers.get(CLIENT_ADDRESS, "").strip()
    token = request.headers.get(CALLER_TOKEN, "").strip()
    if named and len(named) <= 64 and token and served.TRUSTED_CALLER.trusts(token):
        return named
    return own


def password_principal(s: PasswordSignIn, request: Request) -> str:
    """Pilot sign-in (SIGNIN=passwords): a person's username and password, then the session opened through its own token,
    as demo mode opens one. A machine principal has no password. Failed attempts are limited per address and principal (Throttle):
    a failed sign-in over the limits is a 429 that says how long to wait, as Retry-After does; a wrong password,
    or a name with none, is a 401, which says so too if it locked them. Unset, the body is no sign-in (a 422, as before).
    The address is client_address's."""
    if not served.PASSWORDS:
        raise HTTPException(422, "sign in with a token: password sign-in is off (SIGNIN=passwords turns it on)")
    key = f"{client_address(request)}|{s.username}"
    wait = served.THROTTLE.attempt(key)
    if wait:
        raise HTTPException(429, f"too many sign-in attempts: try again in {wait} s", headers={"Retry-After": str(wait)})
    if not (served.ids().check_password(s.username, s.password) and person(served.engine().g.processes(s.username))):
        wait = served.THROTTLE.failed(key)
        raise HTTPException(401, "wrong username or password" + (f": too many failures, try again in {wait} s" if wait else ""),
                            headers={"Retry-After": str(wait)} if wait else None)
    if s.username not in served.ids().known:
        raise HTTPException(401, "wrong username or password")
    served.THROTTLE.passed(key)
    return s.username


def password_sign_in(s: PasswordSignIn, request: Request) -> JSONResponse:
    who = password_principal(s, request)
    opened = served.ids().demo_session(who)
    if opened is None:
        raise HTTPException(401, "wrong username or password")
    return signed_in(request, *opened)


@router.post("/session/token", tags=["external: identity"], response_model=BearerToken, response_model_exclude_unset=True)
def password_token(s: PasswordSignIn, request: Request, response: Response):
    who = password_principal(s, request)
    token = served.ids().bearer_token(who)
    if token is None:
        raise HTTPException(401, "password bearer sign-in is unavailable")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return token


@router.post("/session/demo", tags=["internal: platform"])
def demo_sign_in(s: DemoSignIn, request: Request):
    """Internal: sign in by name in local demo mode."""
    if not demo_allowed(request):
        raise HTTPException(404, "demo mode is off: start the server with DEMO_MODE=1 and call it from this machine, or sign in with a token")
    opened = served.ids().demo_session(s.principal)
    if opened is None:
        raise HTTPException(404, "unknown principal")
    return signed_in(request, *opened)


@router.delete("/session", tags=["external: identity"])
def sign_out(request: Request):
    served.ids().close_session(request.cookies.get(served.COOKIE))
    resp = JSONResponse({"signed_out": True})
    resp.delete_cookie(served.COOKIE, **cookie_flags(request))
    return resp


def whoami_for(who: str) -> dict:
    return service.whoami(served.engine(), who)


@router.get("/whoami", tags=["external: identity"])
def whoami(who: str = Depends(principal)):
    return whoami_for(who)


def readable_units_for(who: str) -> ReadableUnits:
    return ReadableUnits(units=service.readable_units(served.engine(), who))


@router.get("/readable-units", tags=["external: identity"], response_model=ReadableUnits, response_model_exclude_unset=True)
def readable_units(who: str = Depends(principal)):
    """The unit ids the signed-in person may read; none for a principal absent from the graph."""
    return readable_units_for(who)


@router.get("/principals", tags=["internal: platform"])
def principals(request: Request):
    """Internal: signed-in roster, also available to the local demo's sign-in picker."""
    if not demo_allowed(request):
        principal(request)
    return served.engine().g.principals()
