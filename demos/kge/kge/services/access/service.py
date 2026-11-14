"""Access capabilities over the live table's port-backed authority."""
import logging

from kge.adapters.appstate import access_state
from kge.pipeline import access_table
from kge.pipeline.roles import authorization_roles
from kge.services.access.models import AccessResponse, AuditEntry, AuditResponse

log = logging.getLogger(__name__)


def changed_outside(prof) -> bool:
    changed = access_table.changed_outside(prof)
    if changed:
        log.warning("Access for %s changed outside KGE", prof.client)
    return changed


def list_access(prof) -> AccessResponse:
    people = access_table.Access.for_profile(prof)
    roles = authorization_roles(prof)
    return AccessResponse(people=people.grants(), admins=sorted(people.admins()),
                          changed_outside=changed_outside(prof),
                          roles=[{"id": rid, "label": role.get("label") or rid}
                                 for rid, role in sorted(roles.items()) if rid != "access_admin"])


def grant_access(prof, email: str, roles: list[str], name: str, *, by: str, via: str = "api") -> AuditEntry:
    entry = access_table.grant(prof, email, roles, name, by=by, via=via)
    return AuditEntry(**entry, changed_outside=changed_outside(prof))


def revoke_access(prof, email: str, *, by: str, via: str = "api") -> AuditEntry:
    entry = access_table.revoke(prof, email, by=by, via=via)
    if entry is None:
        raise LookupError(f"{email} has no access entry")
    return AuditEntry(**entry, changed_outside=changed_outside(prof))


def access_audit(prof, limit: int = 200) -> AuditResponse:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    return AuditResponse(entries=access_state(prof).access_audit(limit), changed_outside=changed_outside(prof))
