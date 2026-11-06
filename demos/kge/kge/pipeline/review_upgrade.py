"""One-time preservation of reviews written before people moved out of packs.

The trusted deployment seed and its migration audit identify historical roles.
Unresolved actors remain reviewed, explicitly marked, and appear in the upgrade
report; no current access grant is inferred from an old review.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json

import yaml

from kge.adapters import appstate
from kge.adapters.objstore import open_store
from kge.pipeline import access, profile
from kge.pipeline.settings import settings

LEGACY = "legacy:unmapped-review"
REPORT = "review-upgrade.json"


def profile_for(store):
    if getattr(store, "profile", None) is not None:
        return store.profile
    try:
        _, profiles = profile.read(profile.registry_path())
    except profile.ProfileError:
        return None
    return next((p for p in profiles.values() if (p.clients / p.client).resolve() == store.root.resolve()), None)


def _bindings(prof):
    found = {}
    if prof is None:
        return found
    url = settings().STORE_URL
    path = prof.registry.parent / prof.client / "identities.yaml"
    raw = open_store(url).get(f"deploy/{prof.client}/identities.yaml") if url else path.read_bytes() if path.is_file() else None
    if raw:
        seed = yaml.safe_load(raw) or {}
        for binding in seed.get("principals", []):
            who = access_name(binding["principal"])
            if who is not None:
                found.setdefault(who, {"by": who, "roles": set()})["roles"].add(binding["role"])
    for entry in reversed(appstate.access_state(prof).access_audit(1000000)):
        if entry.get("via") != "migration" or not entry.get("after"):
            continue
        row = entry["after"]
        for original in (row.get("name"), row["username"]):
            if original and access_name(original) is not None:
                found[access_name(original)] = {"by": row["username"], "roles": set(row["roles"])}
    return found


def access_name(who):
    from kge.pipeline.access_table import normalize_username
    try:
        return normalize_username(who)
    except ValueError:
        return None


def _roles(store):
    data = yaml.safe_load(store.read("pack.yaml")) or {}
    roles = (data.get("access") or {}).get("roles", [])
    for name in (data.get("manifest") or {}).get("includes", []):
        included = yaml.safe_load(store.read(name)) or {}
        roles += (included.get("access") or {}).get("roles", [])
    return {role["id"]: role for role in roles}


def read(store, name: str, record: dict, *, prof=None, roles=None) -> dict:
    """Upgrade only legacy reviews, preserving audit before removing names.

    A stable per-file/item/action key makes an interrupted upgrade resumable.
    The report is outside pack data; ordinary packs neither read people nor write.
    """
    items = record.get("items", []) if "items" in record else [record]
    if not any(isinstance(item, dict) and any(item.get(f"{step}_by") for step in ("accepted", "certified")) for item in items):
        return record
    prof = prof or profile_for(store)
    roles = _roles(store) if roles is None else roles
    bindings = _bindings(prof)
    upgraded = copy.deepcopy(record)
    items = upgraded.get("items", []) if "items" in upgraded else [upgraded]
    report = json.loads(store.read(REPORT)) if store.exists(REPORT) else {"format": 1, "entries": {}}
    state = appstate.for_profile(prof, store) if prof is not None else None
    for item in items:
        if not isinstance(item, dict):
            continue
        unit = item.get("unit") or record.get("unit")
        targets = [(unit, item.get("id"))] if unit else [(p["unit"], p["id"]) for p in (record.get("input") or {}).get("products", [])]
        scope = f"proposal:{name.split('/')[1]}" if name.startswith("proposals/") else f"metadata:{record['job']}" if name.startswith("portal-metadata/") else None
        for step, action in (("accepted", "accept"), ("certified", "certify")):
            who = item.pop(f"{step}_by", None)
            if not who:
                continue
            key = hashlib.sha256(f"{name}:{item.get('id', '')}:{step}:{who}".encode()).hexdigest()
            person = bindings.get(access_name(who), {"by": who, "roles": set()})
            valid = sorted(role for role in person["roles"] if access.permissions(roles.get(role, {})).get(action))
            role = item.get(f"{step}_as") or next(iter(valid), LEGACY)
            item[f"{step}_as"] = role
            if role == LEGACY:
                item.setdefault("legacy_review", {})[step] = True
            on = str(item.get(f"{step}_on") or item.get(f"{step}_at") or "")[:10]
            at = dt.datetime.now(dt.timezone.utc).isoformat()
            evidence = {"file": name, "item": item.get("id"), "step": step, "by": person["by"], "as": None if role == LEGACY else role,
                        "on": on, "unresolved": role == LEGACY}
            if state is not None:
                target_items = {}
                for target, item_id in targets:
                    target_items.setdefault(target, []).append(item_id)
                for target, item_ids in target_items.items():
                    existing = state.history(target)
                    if any((entry.get("detail") or {}).get("upgrade") == key for entry in existing):
                        continue
                    try:
                        manifest = state.manifest(target)
                    except FileNotFoundError:
                        manifest = {}
                    version = (manifest.get("update") or {}).get("version", manifest.get("version", 1)) if "/update/" in name else manifest.get("version", 1)
                    detail = {"items": item_ids, "as": evidence["as"], "on": on, "upgrade": key, **({"scope": scope} if scope else {})}
                    state.append_history(target, [
                        {"at": at, "by": person["by"], "step": action, "version": version, "detail": detail},
                        {"at": at, "by": person["by"], "step": "review-upgrade", "version": version, "detail": {**evidence, "upgrade": key}},
                    ])
            report["entries"][key] = evidence
    store.write(REPORT, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    store.write(name, (json.dumps(upgraded, indent=2) + "\n").encode() if name.endswith(".json") else yaml.safe_dump(upgraded, sort_keys=False).encode())
    store.review_upgraded = True
    return upgraded


def preserved(item, step):
    return item.get(f"{step}_as") == LEGACY and (item.get("legacy_review") or {}).get(step) is True
