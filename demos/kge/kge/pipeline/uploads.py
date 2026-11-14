"""Stage a complete people-owned upload, restore it after failure, and publish it only after a successful gate.

Ownership is independent of declarations: an undeclared file can be staged and then listed by the up-front check.
Job and page files never enter the mirror, even when a downloaded client folder is uploaded whole.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from kge.adapters.objstore import check, open_store_if_url
from kge.ports import ObjectStore

JOB_OWNED = ("pack.lock", "baselines/", "versions/", "proposals/")
PAGE_OWNED = ("units/", "onboard/")
UPLOAD_RECORD = "onboard-upload.json"


class Refused(ValueError):
    """An upload that cannot be mirrored without changing another owner's files."""


@dataclass
class Plan:
    source: str
    files: dict[str, bytes]
    write: list[str]
    remove: list[str]
    ignored: list[str]
    sha256: str


def _owned(name: str) -> bool:
    check(name)
    return any(p.startswith(".") for p in name.split("/")) or any(
        name == p.rstrip("/") or name.startswith(p if p.endswith("/") else p + "/")
        for p in (*JOB_OWNED, *PAGE_OWNED))


def _local(folder: Path) -> tuple[dict[str, bytes], list[str]]:
    files, ignored = {}, []
    for path in sorted(folder.rglob("*")):
        name = path.relative_to(folder).as_posix()
        if _owned(name):
            if not path.is_dir() or path.is_symlink():
                ignored.append(name)
        elif path.is_symlink():
            raise Refused(f"{path}: an input must be a file inside its folder, not a symbolic link")
        elif path.is_file():
            files[name] = path.read_bytes()
    return files, ignored


def digest(files: dict[str, bytes]) -> str:
    """The sorted path, NUL, bytes, NUL digest, independent of file times and sizes."""
    hashed = hashlib.sha256()
    for name, data in sorted(files.items()):
        hashed.update(name.encode() + b"\0" + data + b"\0")
    return hashed.hexdigest()


def plan(source: str, pack_dir: Path, client: str, store_url: str | None) -> Plan:
    """Read and compare only; store uploads must be beneath this client's uploads prefix."""
    refusal = f"FROM must be under {store_url or '<store>'}/uploads/{client}/ or an absolute folder"
    check(client)
    if store_url:
        base_url = Path(store_url).as_uri() if Path(store_url).is_absolute() else store_url
        base, incoming = urlsplit(base_url.rstrip("/") + f"/uploads/{client}/"), urlsplit(source)
        if (incoming.scheme, incoming.netloc) != (base.scheme, base.netloc) or incoming.query or incoming.fragment \
                or not (incoming.path.rstrip("/") + "/").startswith(base.path) \
                or any(p in (".", "..") for p in incoming.path.split("/")):
            raise Refused(refusal)
        store = open_store_if_url(source)
        if store is None:
            raise Refused(refusal)
        files, ignored = {}, []
        for name in store.list():
            if _owned(name):
                ignored.append(name)
            else:
                data = store.get(name)
                if data is None:
                    raise Refused(f"FROM={source}: {name} disappeared: nothing was applied")
                files[name] = data
    else:
        folder = Path(source)
        if not folder.is_absolute():
            raise Refused(refusal)
        files, ignored = _local(folder)
    if "people.yaml" in files:
        raise Refused("FROM contains people.yaml: manage people through the access command")
    if "pack.yaml" not in files:
        raise Refused(f"FROM={source} holds no pack.yaml: nothing was applied")
    previous, _ = _local(pack_dir)
    write = sorted(name for name, data in files.items()
                   if name not in previous or hashlib.sha256(data).digest() != hashlib.sha256(previous[name]).digest())
    return Plan(source, files, write, sorted(previous.keys() - files.keys()), sorted(ignored), digest(files))


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _remove(folder: Path, name: str) -> None:
    path = folder / name
    path.unlink(missing_ok=True)
    parent = path.parent
    while parent != folder and parent.is_relative_to(folder):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def apply(plan: Plan, pack_dir: Path, backup: Path) -> None:
    """Back up the prior input mirror before changing it; the caller restores on any failure."""
    if backup.exists():
        raise Refused(f"{backup}: an upload backup already exists: nothing was applied")
    previous, _ = _local(pack_dir)
    for name, data in previous.items():
        _write(backup / "files" / name, data)
    _write(backup / "before.json", json.dumps({"files": sorted(previous)}).encode())
    for name in plan.remove:
        _remove(pack_dir, name)
    for name in plan.write:
        _write(pack_dir / name, plan.files[name])


def restore(pack_dir: Path, backup: Path) -> None:
    """Restore bytes and absences, including after an interrupted apply; owned paths are left alone."""
    record = backup / "before.json"
    if not record.is_file():
        return
    before = json.loads(record.read_bytes())
    current, _ = _local(pack_dir)
    for name in sorted(current, reverse=True):
        _remove(pack_dir, name)
    for name in before["files"]:
        _write(pack_dir / name, (backup / "files" / name).read_bytes())


def publish(pack_dir: Path, store: ObjectStore, client: str, plan_json: dict) -> int:
    """Put fixture, includes, catalogue and pack, then remove stale inputs.

The record is UPLOAD_RECORD's JSON shape, without the in-memory bytes. Read the staged mirror so a file unchanged
locally is still put when it was missing from the durable store. The returned count includes puts and deletions.
"""
    files, _ = _local(pack_dir)
    data = yaml.safe_load(files["pack.yaml"]) or {}
    inputs = (data.get("manifest") or {}).get("inputs") or {}
    fixture = (inputs.get("estate") or {}).get("fixture") or "fixture"
    catalogue = inputs.get("catalogue") or "catalogue.yaml"

    def order(name: str) -> tuple[int, str]:
        return (3 if name == "pack.yaml" else 2 if name == catalogue else
                0 if name.startswith(fixture.rstrip("/") + "/") else 1, name)

    prefix = check(f"clients/{client}") + "/"
    remove = set(plan_json["remove"])
    remove.update(k[len(prefix):] for k in store.list(prefix) if k[len(prefix):] not in files)
    remove = sorted(name for name in remove if not _owned(name) and name not in files)
    for name in sorted(files, key=order):
        store.put(prefix + name, files[name])
    for name in remove:
        store.delete(prefix + name)
    return len(files) + len(remove)
