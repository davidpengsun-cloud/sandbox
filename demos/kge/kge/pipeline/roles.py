"""Live table grants evaluated against candidate or served role templates."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

import yaml

from kge.adapters.objstore import open_store
from kge.pipeline import access_table
from kge.pipeline.profile import ProfileError
from kge.pipeline.settings import settings


def candidate_roles(prof, model_hash: str) -> dict[str, dict]:
    """The candidate's role definitions, with no roster or people compiled into them."""
    return access_table.artifact_roles(prof, model_hash)


def candidate_permissions(prof, model_hash: str, who: str, reader: access_table.Access | None = None) -> dict[str, bool]:
    """Check live grants against the immutable candidate templates."""
    templates = candidate_roles(prof, model_hash)
    return access_table.permissions(prof, who, templates, reader=reader)


def served_roles(prof) -> dict[str, dict]:
    return access_table.served_roles(prof)


def served_permissions(prof, who: str, reader: access_table.Access | None = None) -> dict[str, bool]:
    return access_table.permissions(prof, who, served_roles(prof), reader=reader)


def pack_roles(prof) -> dict[str, dict]:
    """Read current templates and declared includes, with the compiler's section and path rules."""
    url = settings().STORE_URL
    store = open_store(url) if url else None
    folder = prof.clients / prof.client
    def read(name):
        if store is not None:
            raw = store.get(f"clients/{prof.client}/{name}")
        else:
            path = folder / name
            if name != "pack.yaml" and folder.resolve() not in path.resolve().parents:
                raise ValueError(f"{name} leads outside the client's folder")
            raw = path.read_bytes()
        value = yaml.safe_load(raw) if raw is not None else None
        if not isinstance(value, dict):
            raise ValueError(f"{name} must exist and contain a mapping of sections")
        return value
    try:
        pack = read("pack.yaml")
        includes = (pack.get("manifest") or {}).get("includes", [])
        if not isinstance(includes, list):
            raise ValueError("manifest.includes must be a list")
        for name in includes:
            if (not isinstance(name, str) or not re.fullmatch(r"[\w.-]+(?:/[\w.-]+)*\.ya?ml", name)
                    or ".." in PurePosixPath(name).parts):
                raise ValueError(f"{name!r} is not an include: a .yaml file in the client's folder")
            for section, value in read(name).items():
                if section == "manifest":
                    raise ValueError(f"{name}:manifest: the manifest is kept in pack.yaml")
                if section in pack:
                    raise ValueError(f"{name}:{section}: a section is kept in one file")
                pack[section] = value
        rows = (pack.get("access") or {}).get("roles", [])
        roles = {row["id"]: row for row in rows}
        if len(roles) != len(rows) or any(not isinstance(role, str) or not role for role in roles):
            raise ValueError("roles need unique ids")
        if findings := access_table.check_pack_roles(roles):
            raise ValueError("; ".join(findings))
        return roles
    except (OSError, AttributeError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ProfileError(f"{prof.client}'s pack roles cannot be read: {exc}") from None


def authorization_roles(prof) -> dict[str, dict]:
    """Before the first release use the current pack; afterwards use served templates."""
    return pack_roles(prof) if prof.current() is None else served_roles(prof)
