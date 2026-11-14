"""Explicit live grants and served role artifacts for isolated workflow tests."""
from __future__ import annotations

import json

import yaml

from kge.adapters import appstate
from kge.pipeline import access_table


def person(role: str, client: str = "meridian") -> str:
    return f"{role}.{client}@example.com"


def seed_access(prof, roles=None):
    """Seed a test deployment once, retaining any real artifact already built by its fixture."""
    explicit = roles is not None
    if roles is None:
        pack = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text())
        roles = pack["access"]["roles"]
    roles = list(roles.values()) if isinstance(roles, dict) else list(roles)
    try:
        templates = access_table.served_roles(prof)
    except (OSError, ValueError):
        templates = {}
    if explicit or not templates:
        folder = prof.artifacts / prof.client / ("f" * 64)
        (folder / "access").mkdir(parents=True, exist_ok=True)
        (folder / "access/roles.json").write_text(json.dumps({"roles": roles}))
        current = folder.parent / "current"
        if not current.exists() and not current.is_symlink():
            current.symlink_to(folder.name)
        elif current.resolve() != folder:
            target = current.resolve() / "access"
            target.mkdir(parents=True, exist_ok=True)
            (target / "roles.json").write_text(json.dumps({"roles": roles}))
    state = appstate.access_state(prof)
    for role in roles:
        state.access_put({"username": person(role["id"], prof.client), "name": role.get("label", role["id"]),
                          "roles": [role["id"]]}, by="operator@example.com", via="migration")
    return prof
