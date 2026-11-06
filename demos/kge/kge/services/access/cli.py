"""The job's access command: one operation, and BY=<email> for every write."""
import argparse
import json
import sys

import yaml

from kge.adapters.appstate import StateConflict, StateUnavailable
from kge.adapters.objstore import open_store
from kge.pipeline import access_table, registry
from kge.pipeline.objstore import REGISTRY, Refused
from kge.pipeline.profile import ProfileError, resolve
from kge.pipeline.settings import settings
from kge.ports import StoreError
from kge.services.access import service

OPERATIONS = ("LIST", "AUDIT", "GRANT", "REVOKE", "MIGRATE", "ADMINS")
OPTIONS = (*OPERATIONS, "NAME", "EMAILS", "BY")


def migration(prof, emails: str, by: str) -> dict:
    """The job operator seeds before ADMINS bootstrap; preserve the registry except for its asker role."""
    url = settings().STORE_URL
    store = open_store(url or str(prof.registry.parent))
    key = REGISTRY if url else prof.registry.name
    raw, version = store.get_version(key)
    document = yaml.safe_load(raw) if raw else None
    if not isinstance(document, dict) or prof.id not in document.get("profiles", {}):
        raise ValueError(f"{key} has no profile {prof.id}")
    registry._registry_bytes(document)
    if url:
        seed = store.get(f"deploy/{prof.client}/identities.yaml")
    else:
        path = prof.registry.parent / prof.client / "identities.yaml"
        seed = path.read_bytes() if path.is_file() else None
    if seed is None:
        raise ValueError(f"deploy/{prof.client}/identities.yaml is missing")
    result = access_table.migrate(prof, seed, emails, by=by)
    if result.get("migrated") and result.get("asker"):
        document["profiles"][prof.id]["asker"] = result["asker"]
        try:
            store.put_version(key, registry._registry_bytes(document), version)
        except (StoreError, Refused, ValueError, OSError) as exc:
            raise ValueError(f"access rows migrated, but the profile asker was not saved: {exc}; "
                             f"run setup-client CLIENT={prof.client} ASKER={result['asker']}") from None
    return {**result, "changed_outside": service.changed_outside(prof)}


def run(prof, options: dict):
    selected = [key for key in OPERATIONS if options.get(key) is not None]
    if len(selected) != 1:
        raise ValueError("choose exactly one of LIST, AUDIT, GRANT, REVOKE, MIGRATE or ADMINS")
    operation = selected[0]
    value = options[operation]
    if operation in ("LIST", "AUDIT", "MIGRATE") and value != "yes":
        raise ValueError(f"{operation} must be yes")
    if operation == "LIST":
        return service.list_access(prof)
    if operation == "AUDIT":
        return service.access_audit(prof)
    try:
        by = access_table.normalize_username(options.get("BY") or "")
    except ValueError:
        raise ValueError("every access write requires BY=<email>") from None
    if not access_table.EMAIL.fullmatch(by):
        raise ValueError("every access write requires BY=<email>")
    if operation == "ADMINS":
        entries = access_table.set_admins(prof, value, by)
        return {"entries": entries, "changed_outside": service.changed_outside(prof)}
    if operation == "MIGRATE":
        return migration(prof, options.get("EMAILS") or "", by)
    if operation == "REVOKE":
        if not value.strip():
            raise ValueError("REVOKE must name an email or migrated principal id")
        return service.revoke_access(prof, value, by=by, via="job")
    email, separator, roles = value.rpartition(":")
    if not separator:
        raise ValueError("GRANT must be <email>:<role>[,<role>]")
    return service.grant_access(prof, email, [role.strip() for role in roles.split(",")],
                                options.get("NAME") or "", by=by, via="job")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("options", nargs="*", help="NAME=value overrides of job environment settings")
    args = parser.parse_args(argv)
    try:
        env = settings()
        options = {key: getattr(env, key, None) for key in OPTIONS}
        for word in args.options:
            key, separator, value = word.partition("=")
            if not separator or key not in OPTIONS:
                raise ValueError(f"unknown access option {key!r}")
            options[key] = value
        result = run(resolve(args.profile), options)
        print(json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result, sort_keys=True))
    except (PermissionError, ValueError, LookupError, OSError, ProfileError, Refused,
            StateConflict, StateUnavailable, StoreError, yaml.YAMLError) as exc:
        print(f"access: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
