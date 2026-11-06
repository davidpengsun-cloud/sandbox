"""Live access grants, separate from every candidate and release.

Readers keep one Access instance. Writers open their own state, so authorization and
mutation share the port's optimistic read without sharing it with cached readers.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
import unicodedata
from bisect import bisect_right
from collections.abc import Callable, Iterable
from importlib.resources import files

import yaml

from kge.adapters.appstate import StateConflict, access_state
from kge.adapters.objstore import open_store
from kge.pipeline import access
from kge.pipeline.access import (
    ACCESS_ADMIN as ACCESS_ADMIN,
    ACCESS_ADMIN_LABEL as ACCESS_ADMIN_LABEL,
    check_pack_roles as check_pack_roles,
)
from kge.pipeline.profile import ProfileError
from kge.pipeline.settings import settings

REFRESH_SECONDS = 60
PRINCIPAL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
EMAIL = re.compile(r"[^\s@,=]+@[^\s@,=]+\.[^\s@,=]+")
UNASSIGNED = tuple(tuple(pair) for pair in json.loads(
    files(__package__).joinpath("unassigned.json").read_text(encoding="utf-8"))["ranges"])


def _refused_character(char: str) -> bool:
    """Refuse the portal's Unicode 15.0 unassigned ranges and runtime controls."""
    point = ord(char)
    index = bisect_right(UNASSIGNED, (point, 0x110000)) - 1
    return (index >= 0 and point <= UNASSIGNED[index][1]) or unicodedata.category(char) in {"Cc", "Cf", "Cn"}


def normalize_username(value: str) -> str:
    """The portal's canonical username, or a refusal before any write or sign-in."""
    if not isinstance(value, str):
        raise ValueError("username must be text")
    if any(_refused_character(c) for c in value):
        raise ValueError("username must not contain control, format or unassigned characters")
    username = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value.strip()).casefold())
    if not username or len(username) > 254:
        raise ValueError("username must contain between 1 and 254 characters")
    if any(_refused_character(c) or c.isspace() for c in username):
        raise ValueError("username must not contain control, format or unassigned characters or whitespace")
    if username == ACCESS_ADMIN or username.startswith("role:"):
        raise ValueError("username is reserved for internal access")
    return username


def check_username(value: str) -> list[str]:
    """Free-form usernames exclude controls, whitespace and internal identifiers."""
    try:
        normalize_username(value)
    except ValueError as exc:
        return [str(exc)]
    return []


_username = normalize_username


def _bootstrap() -> frozenset[str]:
    valid, invalid = set(), 0
    for word in (settings().ACCESS_ADMINS or "").split(","):
        if not word.strip():
            continue
        try:
            valid.add(_username(word))
        except ValueError:
            invalid += 1
    if invalid:
        logging.getLogger(__name__).warning("ACCESS_ADMINS: skipped %d invalid entries", invalid)
    return frozenset(valid)


def _admins(rows: list[dict]) -> frozenset[str]:
    return _bootstrap() | frozenset(row["username"] for row in rows if ACCESS_ADMIN in row["roles"])


class Access:
    """A client's access reader, refreshed at most sixty seconds after a change."""

    def __init__(self, state, *, clock: Callable[[], float] = time.monotonic):
        self.state = state
        self.clock = clock
        self._rows = None
        self._read_at = 0.0

    @classmethod
    def for_profile(cls, prof, *, clock: Callable[[], float] = time.monotonic):
        return cls(access_state(prof), clock=clock)

    def grants(self) -> list[dict]:
        now = self.clock()
        if self._rows is None or now - self._read_at >= REFRESH_SECONDS:
            rows = self.state.access_grants()
            self._rows, self._read_at = rows, now
        return copy.deepcopy(self._rows)

    def admins(self) -> frozenset[str]:
        return _admins(self.grants())

    def is_admin(self, username: str) -> bool:
        return _username(username) in self.admins()

    def roles_of(self, username: str) -> set[str]:
        username = _username(username)
        rows = self.grants()
        held = {role for row in rows if row["username"] == username for role in row["roles"]}
        return held | ({ACCESS_ADMIN} if username in _admins(rows) else set())

    def name_of(self, username: str) -> str:
        username = _username(username)
        return next((row["name"] or username for row in self.grants() if row["username"] == username), username)

    def people(self) -> set[str]:
        rows = self.grants()
        return {row["username"] for row in rows if row["roles"]} | _admins(rows)

    def role_of_person(self, username: str, roles: dict[str, dict]) -> dict:
        """Access administration never supplies pack permissions."""
        held = [roles[role] for role in sorted(self.roles_of(username) - {ACCESS_ADMIN}) if role in roles]
        return {field: "all" if any(role.get(field) == "all" for role in held) else
                sorted({value for role in held for value in role.get(field, [])})
                for field in ("reads", "writes", "processes")}

    def permissions(self, username: str, roles: dict[str, dict]) -> dict[str, bool]:
        return access.permissions(self.role_of_person(username, roles))

    def has_process(self, username: str, process: str, roles: dict[str, dict]) -> bool:
        return access.has_process(self.role_of_person(username, roles), process)

    def readable_units(self, username: str, roles: dict[str, dict]) -> list[str] | str:
        return self.role_of_person(username, roles)["reads"]


def artifact_roles(prof, model_hash: str) -> dict[str, dict]:
    candidate = prof.at(model_hash)
    path = candidate.artifacts / candidate.client / model_hash / "access/roles.json"
    try:
        rows = json.loads(path.read_text())["roles"]
        if not isinstance(rows, list):
            raise ValueError("roles must be a list")
        roles = {row["id"]: row for row in rows}
        if len(roles) != len(rows) or any(not isinstance(role, str) or not role for role in roles):
            raise ValueError("roles need unique ids")
        return roles
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ProfileError(f"{model_hash[:12]}'s roles cannot be read ({type(exc).__name__})") from None


def served_roles(prof) -> dict[str, dict]:
    served = prof.serve()
    model_hash = served.model_hash()
    if model_hash is None:
        return {}
    return artifact_roles(served, model_hash)


def admins(prof, reader: Access | None = None) -> frozenset[str]:
    return (reader if reader is not None else Access.for_profile(prof)).admins()


def is_admin(prof, username: str, reader: Access | None = None) -> bool:
    return (reader if reader is not None else Access.for_profile(prof)).is_admin(username)


def role_of_person(prof, username: str, roles: dict[str, dict], reader: Access | None = None) -> dict:
    return (reader if reader is not None else Access.for_profile(prof)).role_of_person(username, roles)


def permissions(prof, username: str, roles: dict[str, dict], reader: Access | None = None) -> dict[str, bool]:
    return (reader if reader is not None else Access.for_profile(prof)).permissions(username, roles)


def has_process(prof, username: str, process: str, roles: dict[str, dict], reader: Access | None = None) -> bool:
    return (reader if reader is not None else Access.for_profile(prof)).has_process(username, process, roles)


def readable_units(prof, username: str, roles: dict[str, dict], reader: Access | None = None) -> list[str] | str:
    return (reader if reader is not None else Access.for_profile(prof)).readable_units(username, roles)


def querying_role(role_id: str, roles: dict[str, dict]) -> bool:
    return role_id != ACCESS_ADMIN and "querying" in roles.get(role_id, {}).get("processes", [])


def check_grant(username: str, role_ids: list[str], roles: dict[str, dict], *, existing: Iterable[str] = ()) -> list[str]:
    findings = check_username(username)
    if not isinstance(role_ids, list) or not role_ids:
        return [*findings, "roles must be a non-empty list"]
    for role in role_ids:
        if role == ACCESS_ADMIN:
            findings.append(f"{ACCESS_ADMIN} is reserved: use the job's access ADMINS=... BY=... command")
        elif not isinstance(role, str) or role not in roles:
            findings.append(f"unknown role {role!r}: not in the served pack")
    return findings


def _authorized(state, by: str, via: str) -> list[dict]:
    if via not in ("api", "job"):
        raise ValueError("access changes must use api or job")
    rows = state.access_grants()
    if not by or _username(by) not in _admins(rows):
        raise PermissionError(f"{by or 'BY'} is not an admin (access ADMINS or ACCESS_ADMINS)")
    return rows


def grant(prof, username: str, role_ids: list[str], name: str, *, by: str, via: str) -> dict:
    from kge.pipeline.roles import authorization_roles
    state = access_state(prof)
    rows = _authorized(state, by, via)
    findings = check_grant(username, role_ids, authorization_roles(prof), existing={row["username"] for row in rows})
    if not isinstance(name, str):
        findings.append("name must be text")
    if findings:
        raise ValueError("; ".join(findings))
    username = _username(username)
    previous = next((row for row in rows if row["username"] == username), {})
    held = set(role_ids) | ({ACCESS_ADMIN} if ACCESS_ADMIN in previous.get("roles", []) else set())
    return state.access_put({"username": username, "name": name, "roles": sorted(held)}, by=_username(by), via=via)


def revoke(prof, username: str, *, by: str, via: str) -> dict | None:
    state = access_state(prof)
    rows = _authorized(state, by, via)
    if findings := check_username(username):
        raise ValueError("; ".join(findings))
    username = _username(username)
    previous = next((row for row in rows if row["username"] == username), {})
    if ACCESS_ADMIN in previous.get("roles", []):
        return state.access_put({**previous, "roles": [ACCESS_ADMIN]}, by=_username(by), via=via)
    return state.access_delete(username, by=_username(by), via=via)


def set_admins(prof, emails: Iterable[str] | str, by: str) -> list[dict]:
    """Operator-only job entry point. The caller's job permission authorizes bootstrap.

    Ordinary grant/revoke never add or remove access administration. Each changed row is audited;
    a concurrent write refuses the remaining work, and rerunning is idempotent.
    """
    if not by or not EMAIL.fullmatch(by.strip()):
        raise ValueError("access ADMINS requires BY=<operator email>")
    by = _username(by)
    words = [word for word in emails.replace(",", " ").split(" ") if word] if isinstance(emails, str) else list(emails)
    if any(not isinstance(word, str) or not EMAIL.fullmatch(word.strip()) for word in words):
        raise ValueError("ADMINS must contain email addresses")
    wanted = {_username(word) for word in words}
    state = access_state(prof)
    rows = {row["username"]: row for row in state.access_grants()}
    changes = []
    for username in sorted(wanted | {name for name, row in rows.items() if ACCESS_ADMIN in row["roles"]}):
        before = rows.get(username, {"username": username, "name": username, "roles": []})
        held = set(before["roles"]) - {ACCESS_ADMIN}
        if username in wanted:
            held.add(ACCESS_ADMIN)
        if held == set(before["roles"]):
            continue
        changes.append(state.access_put({**before, "roles": sorted(held)}, by=_username(by), via="job") if held else
                       state.access_delete(username, by=_username(by), via="job"))
        _advance(state, changes[-1])
    return changes


def _advance(state, entry: dict) -> None:
    if state.access_digest() != entry["digest"]:
        raise StateConflict("access changed by someone else; reload")


def changed_outside(prof) -> bool:
    state = access_state(prof)
    digest = state.access_digest()
    audit = state.access_audit(1)
    return bool(audit[0].get("outside") or digest != audit[0]["digest"]) if audit else bool(state.access_grants())


def parse_emails(value: str) -> dict[str, str]:
    emails = {}
    for word in value.split():
        principal, separator, email = word.partition("=")
        if not separator or not PRINCIPAL.fullmatch(principal) or not EMAIL.fullmatch(email):
            raise ValueError("EMAILS must be principal=email pairs, space-separated")
        if principal in emails:
            raise ValueError(f"EMAILS names {principal} twice")
        emails[principal] = _username(email)
    return emails


def migrate(prof, identities_yaml: bytes, emails: dict[str, str] | str | None = None, *, by: str = "migration",
            roles: dict[str, dict] | None = None) -> dict:
    """Seed an empty, never-initialized table once, preserving ordinary pack bindings.

    The returned asker is a role id for the registry writer. No released artifact or
    access administrator is required to read a deployment's trusted seed. The result
    includes written rows and principal ids still missing an email for the job report.
    Supplied roles pin validation to the caller's pack snapshot; otherwise read the pack.
    """
    state = access_state(prof)
    rows = state.access_grants()
    if rows or state.access_audit(1):
        return {"migrated": False, "rows": len(rows), "message": f"already migrated: {len(rows)} rows",
                "written": [], "missing_emails": sorted(row["username"] for row in rows if not EMAIL.fullmatch(row["username"]))}
    emails = parse_emails(emails) if isinstance(emails, str) else dict(emails or {})
    by = _username(by)
    if any(not isinstance(key, str) or not PRINCIPAL.fullmatch(key) or not isinstance(value, str) or not EMAIL.fullmatch(value)
           for key, value in emails.items()):
        raise ValueError("EMAILS must map principal ids to email addresses")
    try:
        ids = yaml.safe_load(identities_yaml)
        if ids.get("client", prof.client) != prof.client:
            raise ValueError(f"the seed names client {ids.get('client')!r}, but this profile is {prof.client}'s")
        bindings = ids["principals"]
        if not isinstance(bindings, list):
            raise ValueError("principals must be a list")
        if roles is None:
            from kge.pipeline.roles import pack_roles
            roles = pack_roles(prof)
        if findings := check_pack_roles(roles):
            raise ValueError("; ".join(findings))
        pending, missing_emails, asker_roles = {}, set(), []
        for binding in bindings:
            principal, role = binding["principal"], binding["role"]
            if findings := check_username(principal):
                raise ValueError("; ".join(findings))
            if not isinstance(role, str) or not PRINCIPAL.fullmatch(role):
                raise ValueError("invalid role")
            if role == ACCESS_ADMIN:
                raise ValueError(f"{ACCESS_ADMIN} is reserved: use access ADMINS")
            if role not in roles:
                raise ValueError(f"unknown role {role!r}: not in the pack")
            username = _username(emails.get(principal, principal))
            if principal not in emails and not EMAIL.fullmatch(username):
                missing_emails.add(principal)
            row = pending.setdefault(username, {"username": username, "name": principal, "roles": []})
            row["roles"] = sorted(set(row["roles"]) | {role})
            if principal == ids.get("asker"):
                asker_roles.append(role)
        if set(emails) - {binding["principal"] for binding in bindings}:
            raise ValueError("EMAILS names an unknown principal")
        asker = None
        if ids.get("asker") is not None:
            asker = next((role for role in asker_roles if querying_role(role, roles)), None)
            if asker is None:
                raise ValueError(f"asker {ids['asker']!r} has no querying role of the pack")
    except (OSError, yaml.YAMLError, AttributeError, TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"identities.yaml cannot be migrated: {exc}") from None
    state.access_migrate(list(pending.values()), by=by)
    return {"migrated": True, "rows": len(pending), "asker": asker,
            "written": list(pending.values()), "missing_emails": sorted(missing_emails)}


def seed(prof, *, roles: dict[str, dict] | None = None) -> dict:
    """Read deploy/<client>/identities.yaml only for an untouched table."""
    state = access_state(prof)
    rows = state.access_grants()
    if rows or state.access_audit(1):
        return {"migrated": False, "rows": len(rows)}
    url = settings().STORE_URL
    if url:
        raw = open_store(url).get(f"deploy/{prof.client}/identities.yaml")
    else:
        path = prof.registry.parent / prof.client / "identities.yaml"
        raw = path.read_bytes() if path.is_file() else None
    return migrate(prof, raw, roles=roles) if raw is not None else {"migrated": False, "rows": 0}
