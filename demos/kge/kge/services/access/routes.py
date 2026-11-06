"""HTTP adapters for administrator-only access capabilities."""
from fastapi import APIRouter, Depends, HTTPException

from kge.adapters.appstate import StateConflict, StateUnavailable
from kge.pipeline.profile import ProfileError
from kge.ports import StoreError
from kge.services.access import service
from kge.services.access.models import AccessResponse, AuditEntry, AuditResponse, GrantBody, Limit
from kge.services.http import admin, served

router = APIRouter(prefix="/access", tags=["internal: governance"])
SERVICE_FUNCTIONS = {name: getattr(service, name) for name in
                     ("list_access", "grant_access", "revoke_access", "access_audit")}


def service_call(call, *args, **kwargs):
    try:
        return call(served.PROFILE, *args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except StateConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (StateUnavailable, StoreError, ProfileError) as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("", response_model=AccessResponse)
def list_access(who: str = Depends(admin)) -> AccessResponse:
    """Internal: list people, administrators, served roles and outside changes."""
    return service_call(service.list_access)


@router.get("/audit", response_model=AuditResponse)
def access_audit(limit: Limit = 200, who: str = Depends(admin)) -> AuditResponse:
    """Internal: read the newest access changes and report outside changes."""
    return service_call(service.access_audit, limit)


@router.put("/{email:path}", response_model=AuditEntry)
def grant_access(email: str, body: GrantBody, who: str = Depends(admin)) -> AuditEntry:
    """Internal: replace a person's ordinary roles; access_admin is reserved to the job."""
    return service_call(service.grant_access, email, body.roles, body.name, by=who)


@router.delete("/{email:path}", response_model=AuditEntry)
def revoke_access(email: str, who: str = Depends(admin)) -> AuditEntry:
    """Internal: remove ordinary roles, preserving operator-set administrator membership."""
    return service_call(service.revoke_access, email, by=who)
