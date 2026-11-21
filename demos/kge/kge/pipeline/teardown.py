"""Remove an installed client's durable data, with an inventory, store backup and record."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kge.adapters import graphstore, retrieval
from kge.adapters.objstore import open_store, under
from kge.adapters.warehouse import estate_of
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline import lease
from kge.pipeline.attempt import retry_refusal
from kge.pipeline.profile import profile, resolve
from kge.pipeline.settings import ROOT, settings

REGISTRY = "deploy/profiles.yaml"
NOTES = (
    "Versioned buckets keep removed objects as noncurrent versions for 30 days.",
    "The tmp bucket's tmp/<client>/ is left to its 7-day lifecycle.",
    "The landing's objects and BigQuery tables are not copied.",
)


class Refused(ValueError):
    """A teardown that cannot safely start."""


def registry(store) -> tuple[dict, str]:
    raw, version = store.get_version(REGISTRY)
    if raw is None:
        return {"profiles": {}}, version
    try:
        saved = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise Refused(f"cannot read {REGISTRY}: repair the registry") from None
    if not isinstance(saved, dict) or not isinstance(saved.get("profiles"), dict):
        raise Refused(f"cannot read {REGISTRY}: repair the registry")
    return saved, version


@contextmanager
def installed_profile(client: str, saved: dict):
    """Inventory needs only this client's pointers, even after its registry entry was removed.

    Graph inventory resolves the process profile itself. Give it the same temporary registry as the other ports,
    without pulling or changing the job's working copy or relying on a stale registry left there.
    """
    entry = saved["profiles"].get(client, {
        "client": client, "clients": "../clients", "artifacts": "../build/artifacts", "store": "../build/stores",
        "landing": "../build/landing", "warehouses": f"../build/{client}/warehouses",
        "identities": f"{client}/identities.yaml",
    })
    problems = []
    prof = profile(ROOT / REGISTRY, client, entry, problems)
    if problems or prof is None or prof.client != client:
        raise Refused(f"{REGISTRY}: profile {client} must name this install's client and valid pointers")
    pointers = {name: str(getattr(prof, name)) for name in
                ("clients", "artifacts", "store", "landing", "warehouses")}
    with tempfile.TemporaryDirectory(prefix="kge-teardown-") as tmp:
        path = Path(tmp) / "profiles.yaml"
        path.write_text(yaml.safe_dump({"default": client, "profiles": {client: {"client": client, **pointers}}}))
        previous = os.environ.get("PROFILES")
        os.environ["PROFILES"] = str(path)
        try:
            yield resolve(client, path)
        finally:
            if previous is None:
                os.environ.pop("PROFILES", None)
            else:
                os.environ["PROFILES"] = previous


def inventory(store, landing, prof, remove_people: bool) -> tuple[dict, list]:
    client = prof.client
    prefixes = [f"clients/{client}/", f"work/{client}.tar.gz", f"releases/{client}/", f"secrets/{client}/",
                f"tmp/{client}/", f"uploads/{client}/", f"readings/{client}/"]
    if remove_people:
        prefixes.append(f"deploy/{client}/")
    keys = {prefix: sorted(set(store.list(prefix))) if prefix.endswith("/") else
            ([prefix] if store.get(prefix) is not None else []) for prefix in prefixes}
    estate = estate_of(prof)
    owners = [(graphstore.clear, graphstore.inventory),
              (lambda objects: retrieval.clear(prof, objects), lambda: retrieval.inventory(prof)),
              (estate.clear, estate.inventory)]
    held, batches = set(), []
    for clear, read in owners:
        selected = sorted(obj for obj in set(read()) - held if not obj.name.startswith(("_portal_", "access_")))
        batches.append((clear, read, selected))
        held.update(selected)
    return {"store": keys, "landing": sorted(set(landing.list())),
            "objects": [obj._asdict() for obj in sorted(held)]}, batches


def show(found: dict, client: str, remove_profile: bool) -> None:
    for prefix, keys in found["store"].items():
        print(f"teardown: {prefix}: {len(keys)} object(s)")
        for key in keys:
            print(f"teardown: store {key}")
    print(f"teardown: landing: {len(found['landing'])} object(s)")
    for key in found["landing"]:
        print(f"teardown: landing {key}")
    print(f"teardown: BigQuery: {len(found['objects'])} object(s)")
    for obj in found["objects"]:
        print(f"teardown: {obj['kind']} {obj['dataset']}.{obj['name']}")
    if remove_profile:
        print(f"teardown: profile {client} in {REGISTRY}")


def counts(removed: dict) -> dict:
    return {"store": {prefix: len(keys) for prefix, keys in removed["store"].items()},
            "landing": len(removed["landing"]), "objects": len(removed["objects"])}


def save_record(store, record: dict) -> None:
    record["counts"] = counts(record["removed"])
    key = f"runs/{record['client']}/teardown/{record['time']}"
    store.put(key + ".json", (json.dumps(record, indent=2) + "\n").encode())
    lines = [f"# Teardown: {record['client']}", "", f"{record['time']}: {record['outcome']}.", "",
             f"Store backup: {record['backup'] or 'none'}.", "", "## Removed", ""]
    for prefix, keys in record["removed"]["store"].items():
        lines += [f"{prefix}: {len(keys)} object(s).", "", *[f"- {key}" for key in keys], ""]
    lines += [f"Landing: {record['counts']['landing']} object(s).", "",
              *[f"- {key}" for key in record["removed"]["landing"]], "",
              f"BigQuery: {record['counts']['objects']} object(s).", ""]
    lines += [f"- {obj['kind']} {obj['dataset']}.{obj['name']}" for obj in record["removed"]["objects"]]
    if record["unconfirmed"]:
        lines += ["", "Removal could not be confirmed for:", ""]
        lines += [f"- {obj['kind']} {obj['dataset']}.{obj['name']}" for obj in record["unconfirmed"]]
    if record["profile_removed"]:
        lines += ["", f"Removed profile {record['client']} from {REGISTRY}."]
    lines += ["", *record["notes"], ""]
    store.put(key + ".md", "\n".join(lines).encode())
    print(f"teardown: record {key}.json and .md")


def remove(store, landing, prof, saved: dict, version: str, remove_people: bool) -> None:
    client = prof.client
    found, batches = inventory(store, landing, prof, remove_people)
    remove_profile = remove_people and client in saved["profiles"]
    show(found, client, remove_profile)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    record = {"client": client, "time": stamp, "outcome": "failed", "backup": None,
              "removed": {"store": {prefix: [] for prefix in found["store"]}, "landing": [], "objects": []},
              "profile_removed": False, "unconfirmed": [], "notes": [note.replace("<client>", client) for note in NOTES]}
    keys = [key for group in found["store"].values() for key in group]
    try:
        if not keys and not found["landing"] and not found["objects"] and not remove_profile:
            print(f"teardown: nothing left for {client}")
            record["outcome"] = "nothing left"
            return
        backup_keys = [key for key in keys if key != f"secrets/{client}/passwords.txt"]
        if remove_profile:
            backup_keys.append(REGISTRY)
        if backup_keys:
            backup = f"backups/{client}/{stamp}/"
            record["backup"] = backup
            for key in backup_keys:
                data = store.get(key)
                if data is None:
                    raise Refused(f"{key} disappeared before its backup: run teardown again")
                store.put(backup + key, data)
            print(f"teardown: store part backed up to {backup}; the landing's objects and BigQuery tables are not copied")
        else:
            print("teardown: no store keys to back up; the landing's objects and BigQuery tables are not copied")
        for clear, read, objects in batches:
            if objects:
                record["unconfirmed"] = [obj._asdict() for obj in objects]
                try:
                    dropped = clear(objects)
                except (ValueError, RuntimeError, OSError):
                    remaining = set(read())
                    record["removed"]["objects"].extend(obj._asdict() for obj in objects if obj not in remaining)
                    record["unconfirmed"] = []
                    raise
                record["removed"]["objects"].extend(item._asdict() for item in dropped)
                record["unconfirmed"] = []
        for key in found["landing"]:
            landing.delete(key)
            record["removed"]["landing"].append(key)
        deploy = f"deploy/{client}/"
        for prefix, group in found["store"].items():
            if prefix != deploy:
                for key in group:
                    store.delete(key)
                    record["removed"]["store"][prefix].append(key)
        if remove_profile:
            del saved["profiles"][client]
            if saved.get("default") == client:
                saved["default"] = next(iter(saved["profiles"]), None)
            store.put_version(REGISTRY, yaml.safe_dump(saved, sort_keys=False).encode(), version)
            if not saved["profiles"]:
                store.delete(REGISTRY)
            record["profile_removed"] = True
        for key in found["store"].get(deploy, []):
            store.delete(key)
            record["removed"]["store"][deploy].append(key)
        record["outcome"] = "removed"
    finally:
        save_record(store, record)


def main() -> int:
    current = settings()
    client = (current.PROFILE or "").strip()
    try:
        if not client or current.CONFIRM != client:
            raise Refused(f"teardown CONFIRM={client or '<client>'}: CONFIRM must name this install's client, {client or '<client>'}")
        if not CLIENT_ID.fullmatch(client):
            raise Refused(f"PROFILE must name the install client: {CLIENT_ID_RULE}")
        reason = retry_refusal("teardown")
        if reason:
            raise Refused(reason)
        if current.PREVIEW not in (None, "", "yes"):
            raise Refused("PREVIEW must be yes or unset")
        if current.PEOPLE not in (None, "", "remove"):
            raise Refused("PEOPLE must be remove or unset")
        if not (current.STORE_URL or "").strip():
            raise Refused("STORE_URL is unset: set the job's object store")
        store = open_store(current.STORE_URL)
        landing = open_store((current.LANDING_URL or "").strip() or under(store.url, "landing", client))
        remove_people = current.PEOPLE == "remove"
        if current.PREVIEW == "yes":
            saved, _ = registry(store)
            with installed_profile(client, saved) as prof:
                found, _ = inventory(store, landing, prof, remove_people)
                show(found, client, remove_people and client in saved["profiles"])
            print("teardown: preview only: nothing removed")
            return 0
        held = lease.take(store, client, "teardown")
        stop = lease.heartbeat(store, held)
        try:
            saved, version = registry(store)
            with installed_profile(client, saved) as prof:
                remove(store, landing, prof, saved, version, remove_people)
        finally:
            stop.set()
            lease.release(store, held)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"teardown: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
