"""Client profile entries and baseline pointers; people live in the access table."""
from __future__ import annotations

import argparse
import re
import sys

import yaml

from kge.adapters.objstore import StoreError, check
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.objstore import REGISTRY, Refused, say, the_store
from kge.pipeline.profile import ProfileError
from kge.pipeline.access_table import ACCESS_ADMIN, check_pack_roles, querying_role
from kge.pipeline.settings import settings

ROLE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")


def _registry(store) -> tuple[dict, str]:
    raw, version = store.get_version(REGISTRY)
    registry = yaml.safe_load(raw) if raw else {"profiles": {}}
    if not isinstance(registry, dict) or not isinstance(registry.get("profiles"), dict):
        raise Refused(f"the store's {REGISTRY} holds no profiles")
    return registry, version


def _registry_bytes(registry: dict) -> bytes:
    """Serialize the stored document without substituting a legacy reader's fields."""
    return yaml.safe_dump(registry, sort_keys=False).encode()


def _baseline(client: str, path: str) -> str | None:
    if not path:
        return None
    try:
        check(path)
    except ValueError:
        raise Refused(f"BASELINE={path!r} is not a path under clients/{client}/") from None
    return f"../clients/{client}/{path}"


def setup_client() -> None:
    """Set a client's profile, preserving omitted asker and baseline settings."""
    env = settings()
    client = (env.CLIENT or "").strip()
    if not CLIENT_ID.fullmatch(client):
        raise Refused(f"CLIENT={client!r} is refused: {CLIENT_ID_RULE}")
    store = the_store()
    registry, version = _registry(store)
    previous = registry["profiles"].get(client, {})
    asker = (env.ASKER.strip() or None) if env.ASKER is not None else previous.get("asker")
    baseline = _baseline(client, env.BASELINE.strip()) if env.BASELINE is not None else previous.get("baseline")
    if asker is not None and (not isinstance(asker, str) or not ROLE.fullmatch(asker) or asker == ACCESS_ADMIN):
        raise Refused("ASKER must name a querying role")
    raw_pack = store.get(f"clients/{client}/pack.yaml")
    if raw_pack is not None:
        pack = yaml.safe_load(raw_pack)
        if not isinstance(pack, dict):
            raise Refused(f"clients/{client}/pack.yaml is not a pack")
        roles = {role["id"]: role for role in (pack.get("access") or {}).get("roles", [])}
        findings = check_pack_roles(roles)
        if findings:
            raise Refused("; ".join(findings))
        if asker is not None and not querying_role(asker, roles):
            raise Refused(f"ASKER={asker!r} is not a querying role of the pack")
        if env.ASKER is None and "asker" not in previous:
            asker = next((rid for rid in roles if querying_role(rid, roles)), None)
    else:
        say("no pack in the store yet: roles not checked")
    registry.setdefault("default", client)
    registry["profiles"][client] = {
        "client": client, "clients": "../clients", "artifacts": "../build/artifacts", "store": "../build/stores",
        "landing": "../build/landing", "warehouses": f"../build/{client}/warehouses",
        **previous, "asker": asker, "baseline": baseline}
    store.put_version(REGISTRY, _registry_bytes(registry), version)
    say(f"{REGISTRY} put, profile {client}; asker {asker or 'first querying role'}")


def set_baseline(path: str, profile_id: str | None = None) -> None:
    """Change only the selected stored profile's baseline; people and every other field stay as held."""
    store = the_store()
    registry, version = _registry(store)
    pid = profile_id or settings().PROFILE or settings().CLIENT or registry.get("default")
    entry = registry["profiles"].get(pid)
    if entry is None:
        raise Refused(f"the store's {REGISTRY} has no profile {pid}")
    entry["baseline"] = _baseline(entry["client"], path)
    store.put_version(REGISTRY, _registry_bytes(registry), version)
    say(f"profile {pid}: baseline {path or 'cleared'}; people and asker kept")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", help="the stored profile set-baseline changes (default: PROFILE, else CLIENT, else the registry's default); setup-client names its client by CLIENT")
    commands = ap.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-client")
    commands.add_parser("set-baseline").add_argument("path")
    args = ap.parse_args()
    try:
        setup_client() if args.command == "setup-client" else set_baseline(args.path, args.profile)
    except (Refused, ProfileError, StoreError, yaml.YAMLError) as exc:
        print(f"registry: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
