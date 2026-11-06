"""The commands on the object store, where an installed deployment keeps what is a client's, never in the image (issue
#56); the stores themselves are kge/adapters/objstore/'s.

    python3 -m kge.pipeline.objstore before <command>   # the entry script, before a command, when STORE_URL is set
    python3 -m kge.pipeline.objstore after <command>    # publish successful work and release the lease
    python3 -m kge.pipeline.objstore failed <command>   # release the lease; onboard publishes its diagnostics only
    python3 -m kge.pipeline.objstore put-example        # EXAMPLE, FROM: example content, a pack, put into the store
    python3 -m kge.pipeline.objstore releases           # the client's releases, current marked

STORE_URL names the store (kge/adapters/objstore/ open_store): a local folder
(/abs/path or file:///abs/path) or a Google Cloud Storage bucket
(gs://bucket[/prefix]); s3:// is refused, not supported yet. Its keys:

  deploy/profiles.yaml                                  setup-client's registry entry
  deploy/<client>/identities.yaml                       optional migration seed
  deploy/<client>/passwords-secret                      live password secret reference
  access/<client>/access-grants.json, access-audit.jsonl live access, outside work and release archives
  clients/<client>/...                                   the pack, its lock and baselines (put-example, or the installer)
  work/<client>.tar.gz                                   the job's build/ between executions, no landing and no secret
  releases/<client>/<hash>/<release>.tar.gz              each release, its artifact, warehouses and estate files
  releases/<client>/<hash>.tar.gz                        the latest release of a hash, for older readers
  releases/<client>/current                              text, "<hash>\\n": what serve serves, put after its tarball
  releases/<client>/identity.json                        the release's identity (Profile.identity: hash, graph
                                                         generation, chunks table), put after current: a release of
                                                         the same hash at a new generation, which a service follows
  secrets/<client>/passwords.json, passwords.txt         the passwords command's: the hashes, and the passwords to
                                                         fetch once and delete

The client's landing is LANDING_URL, else <STORE_URL>/landing/<client>, laid
out as kge/landing/zone.py lays it out: its current is a text object there,
"releases/<release_id>", and a symlink on disk. A store never holds a symlink
or relies on an object's metadata. A push puts every file first, then current,
then each manifest.json, the landing's trigger in QA and PROD.

With STORE_URL set the entry script runs a command between before and after:
before pulls clients/ and deploy/ (mirrored), and for a job command the work
and the landing; serve instead fetches the current release into build/, and
the hashed passwords where SIGNIN_PASSWORDS is unset. After a job command
succeeds its work goes back up; seed's landing too, and onboard's when certified; baseline's baselines/ go
up beside the pack; release publishes the released graph and puts current. Cloud Run's job executions share no disk, so
the work is how onboard sees what seed built, and a rollback reaches an old
hash. Run one job at a time.

It prints keys and counts, never what an object holds. Exit 2 is a refusal, naming why; exit 3 a store that
failed, naming the key and the URL. Names no client: the store's registry does. The stores themselves are the
adapter's (kge/adapters/objstore/): a local folder, or a GCS bucket through the JSON API.

APP is /app in the image, with clients/, deploy/ and build/ beside the code.
A secret, or a process's own file (its pid, its address), is never in the work
or a release, and the work leaves out the landing, which has a store of its own.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path
from datetime import datetime, timezone

import yaml

from kge.pipeline import access_table
from kge.pipeline.access import may_improve_unit, permissions

from kge.adapters import released
from kge.adapters.warehouse import estate_of
from kge.adapters.objstore import ObjectStore, StoreError, check, check_prefix, open_store, open_store_if_url, under
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.pipeline.profile import CURRENT, HASH, SLUG, Profile, ProfileError, resolve
from kge.pipeline.settings import ROOT, settings

APP = ROOT
REGISTRY = "deploy/profiles.yaml"
MANIFEST = "manifest.json"
PRIVATE = frozenset({"tokens.json", "passwords.json", "passwords.txt", "session_secret", "signed_out.json", "api.pid", "api.json", "lease.json", "access-grants.json", "access-audit.jsonl", "ROSTER"})
NOT_WORK = ("landing", "node", "demo-script", "kit")
JOBS = ("seed", "reload", "lock", "onboard", "improve", "sign-off", "release", "eval", "eval-pre", "tagger-eval", "baseline", "passwords")
CANDIDATE_MARKERS = frozenset({"LOADED", "GATE", "SIGNOFF", "RELEASED", "CHANGES", "RELOADED", released.PRE_EVAL, "graph.bigquery", "chunks.bigquery"})
UNIT = re.compile(r"[a-z][a-z0-9_-]{0,23}")


class Refused(RuntimeError):
    """A command this store will not run, and why."""


PENDING_PROPOSAL = "A proposal is awaiting review: accept, certify, run checks, sign-off and release it first."
UNREADABLE_PROPOSAL = "A proposal could not be read: have an operator repair its record before improving."


def read_proposal_record(store, name: str) -> dict:
    """Read a proposal conservatively; unreadable records must never be replaced by improvement."""
    try:
        record = yaml.safe_load(store.read(name))
    except (OSError, ValueError, RecursionError, yaml.YAMLError, StoreError):
        raise ValueError(UNREADABLE_PROPOSAL) from None
    if not isinstance(record, dict) or not record or not isinstance(record.get("kept", False), bool):
        raise ValueError(UNREADABLE_PROPOSAL)
    for field in ("items", "notes", "requests"):
        entries = record.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError(UNREADABLE_PROPOSAL)
    for item in record.get("items", []):
        if not isinstance(item.get("id"), str):
            raise ValueError(UNREADABLE_PROPOSAL)
        if any(item.get(field) and not isinstance(item[field], dict) for field in ("pack", "expect")):
            raise ValueError(UNREADABLE_PROPOSAL)
        pack = item.get("pack") or {}
        for field in ("sources", "glossary", "measures", "mappings", "ontology_mappings", "policies.restricted_attributes"):
            entries = pack.get(field) or []
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError(UNREADABLE_PROPOSAL)
    try:
        json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError(UNREADABLE_PROPOSAL) from None
    return record


def proposal_pending(record: dict, released_hash: str | None) -> bool:
    """Kept proposals await review or release; a held-out regression is replaceable, never reviewable."""
    return record.get("kept") is True and not record.get("heldout_fell") and not (
        record.get("state") == "certified" and released_hash
        and record.get("candidate_hash") == released_hash)


def pull(store: ObjectStore, prefix: str, folder: Path, links: tuple = ()) -> int:
    """The objects under prefix mirrored into folder: emptied first, then every object written, keyed relative to
    prefix; a name in links becomes a symlink to its text. How many it wrote."""
    check_prefix(prefix)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    n = 0
    for key in store.list(prefix):
        rel = check(key[len(prefix):])
        data = store.get(key)
        if data is None:
            continue
        dest = folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel in links:
            os.symlink(check(data.decode().strip()), dest)
        else:
            dest.write_bytes(data)
        n += 1
    return n


def push(folder: Path, store: ObjectStore, prefix: str, links: tuple = ()) -> int:
    """Every regular file under folder put under prefix (a name starting with . skipped); a symlink named in links goes
    up as its target's text, after every file, and each manifest.json last. How many it put."""
    check_prefix(prefix)
    folder = Path(folder)
    files, linked, last = [], [], []
    for root, dirs, names in os.walk(folder):
        here = Path(root)
        for name in sorted(dirs + names):
            p = here / name
            rel = p.relative_to(folder).as_posix()
            if name.startswith("."):
                continue
            if p.is_symlink():
                if rel in links:
                    linked.append((rel, os.readlink(p).encode() + b"\n"))
            elif p.is_file():
                (last if name == MANIFEST else files).append((rel, p))
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (here / d).is_symlink())
    for rel, p in files:
        store.put(prefix + rel, p.read_bytes())
    for rel, text in linked:
        store.put(prefix + rel, text)
    for rel, p in last:
        store.put(prefix + rel, p.read_bytes())
    return len(files) + len(linked) + len(last)


def tarball(app: Path, paths: list[Path]) -> bytes:
    """A gzipped tar of paths, each named relative to app, which must be under build/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in paths:
            name = p.relative_to(app).as_posix()
            if not name.startswith("build/"):
                raise Refused(f"{name} is not under build/: a profile from setup-client keeps everything it builds there")
            tar.add(p, arcname=name, recursive=False)
    return buf.getvalue()


def local_files(prof: Profile) -> bool:
    """Whether the profile's warehouses are files on this machine (DuckDB's), so its work and releases carry them; where
    they are tables in BigQuery (the estate's warehouse port, kge/adapters/warehouse/), no warehouse file is packed or
    unpacked, a stale one from an older work or release among them."""
    return estate_of(prof).holds_files()


def is_warehouse_file(name: str) -> bool:
    return name.endswith((".duckdb", ".duckdb.part", ".duckdb.wal"))


def unpack(data: bytes, app: Path, warehouse_files: bool = True, *, candidate_root: Path | None = None) -> int:
    """A tarball extracted into app, as the data filter allows (no absolute name, no link out, no device), every member
    under build/; Refused, with nothing extracted, if one is not, or is not a folder, a file or a link. Without
    warehouse_files a DuckDB warehouse a tarball holds is left out (local_files). Each file is written under a temporary
    name in its own folder and renamed over the old (in_place), so whoever reads one while the work is pulled (a status
    poll's switch reading a release record, kge/adapters/released.py) reads the old file or the new, never part of
    one: folders first, then files, then links, so a link such as the client's current pointer never names a folder
    before its files are there. A work snapshot's candidate_root also removes absent candidate markers, only after
    extraction succeeds and only from that client's hash folders represented in the snapshot. Release archives
    do not mirror markers; process files, overlays and release identities are never removed. How many members."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if warehouse_files or not is_warehouse_file(m.name)]
        outside = [m.name for m in members if m.name.startswith("/") or ".." in m.name.split("/")
                   or not m.name.startswith("build/")]
        if outside:
            raise Refused(f"the tarball holds {len(outside)} member(s) outside build/, {outside[0]!r} first: nothing extracted")
        unkept = [m.name for m in members if not (m.isdir() or m.isreg() or m.issym() or m.islnk())]
        if unkept:
            raise Refused(f"the tarball's member {unkept[0]!r} is not a folder, a file or a link: nothing extracted")
        filtered = [data_filtered(m, app) for m in members]
        for m in sorted(filtered, key=lambda m: 0 if m.isdir() else 1 if m.isreg() else 2):
            m = data_filtered(m, app)
            target = app / m.name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.issym():
                in_place(target, lambda tmp, m=m: os.symlink(m.linkname, tmp))
            elif m.islnk():
                in_place(target, lambda tmp, m=m: os.link(app / m.linkname, tmp))
            else:
                in_place(target, lambda tmp, m=m: written(tar, m, tmp))
        if candidate_root is not None:
            prefix = candidate_root.relative_to(app)
            names = {Path(m.name) for m in filtered}
            folders = {name.relative_to(prefix).parts[0] for name in names
                       if name.is_relative_to(prefix) and len(name.relative_to(prefix).parts) >= 2
                       and HASH.fullmatch(name.relative_to(prefix).parts[0])}
            for model_hash in folders:
                folder = candidate_root / model_hash
                if folder.is_symlink() or not folder.is_dir():
                    continue
                for name in CANDIDATE_MARKERS:
                    if prefix / model_hash / name not in names:
                        (folder / name).unlink(missing_ok=True)
    return len(members)


def data_filtered(member: tarfile.TarInfo, app: Path) -> tarfile.TarInfo:
    """member as the data filter passes it into app (its mode made safe), as it stands on disk now, so a link an
    earlier member made is followed; Refused if the filter refuses it."""
    try:
        return tarfile.data_filter(member, str(app))
    except tarfile.FilterError as exc:
        raise Refused(f"the tarball's member {member.name!r} is refused ({type(exc).__name__}): nothing extracted") from None


def written(tar: tarfile.TarFile, member: tarfile.TarInfo, tmp: Path) -> None:
    """member's bytes written to tmp, with the mode the data filter gave it and its time."""
    source = tar.extractfile(member)
    with source, open(tmp, "xb") as out:
        shutil.copyfileobj(source, out)
    if member.mode is not None:
        os.chmod(tmp, member.mode)
    os.utime(tmp, (member.mtime, member.mtime))


def in_place(target: Path, make) -> None:
    """target made whole under a temporary name in its folder by make, then renamed over the old in one step; the
    temporary name removed if it fails."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.unpack.{secrets.token_hex(4)}")
    try:
        make(tmp)
        os.replace(tmp, target)
    finally:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()


def work_paths(app: Path, warehouse_files: bool = True) -> list[Path]:
    """The job's work: all of build/ but its landing, Node's packages, the demo script's and the kit's, every secret
    and process file (PRIVATE), a graph of another store than the one the job runs on (GRAPH_STORE), as one made
    before a switch: graph.<store> and the files beside it are the store's own alone; and, without warehouse_files, no
    DuckDB warehouse either (local_files)."""
    from kge.adapters.graphstore import store
    own = f"graph.{store().name}"
    build, out = app / "build", []
    for root, dirs, names in os.walk(build):
        here = Path(root)
        if here == build:
            dirs[:] = [d for d in dirs if d not in NOT_WORK]
        for name in sorted(dirs + names):
            p = here / name
            if name in PRIVATE or (p.is_dir() and not p.is_symlink()) or (not warehouse_files and is_warehouse_file(name)):
                continue
            if name.startswith("graph.") and not (name == own or name.startswith(own + ".")):
                continue
            out.append(p)
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink())
    return out


def files_under(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*") if (p.is_file() or p.is_symlink()) and p.name not in PRIVATE)


def release_paths(prof: Profile, model_hash: str) -> list[Path]:
    """What serve needs of a release: its artifact, its graph with the graph's markers, the run state's files but the
    secret and process ones, the warehouses where they are files (local_files: none where they are BigQuery's), and the
    estate's own files (its summary, question sets, document index). The graph is the graph store's (GRAPH_STORE): its
    file and the files it keeps beside it, graph.ladybug on the local stack, graph.bigquery, the pointer to its
    tables, on BigQuery, with the release record a release wrote (kge/adapters/released.py, or the separate records
    before it), which serving opens; another store's graph left in the folder, as one made before a switch, is never
    shipped."""
    from kge.adapters import released
    from kge.adapters.graphstore import sidecars
    run = prof.run_dir()
    graph = prof.at(model_hash).graph_path()
    own = {graph, *sidecars(graph), *(p for p in released.paths(graph.parent) if p.is_file())}
    held = [p for p in files_under(prof.store / prof.client / model_hash) if not p.name.startswith("graph.") or p in own]
    return [*files_under(prof.artifacts / prof.client / model_hash), *held,
            *sorted(p for p in run.glob("*") if p.is_file() and p.name not in PRIVATE),
            *(files_under(prof.warehouses) if local_files(prof) else []),
            *sorted(p for p in prof.estate().glob("*") if p.is_file() and not p.is_symlink() and
                    (local_files(prof) or not is_warehouse_file(p.name)))]


def point(prof: Profile, model_hash: str) -> None:
    """The client's current pointer at model_hash: a link under a temporary name, renamed over the old (kge/graph/release.py)."""
    home = prof.artifacts / prof.client
    tmp = home / f".{CURRENT}.{os.getpid()}.{secrets.token_hex(4)}"
    os.symlink(model_hash, tmp)
    try:
        os.replace(tmp, home / CURRENT)
    except BaseException:
        os.unlink(tmp)
        raise


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600)


def say(line: str) -> None:
    print(f"store: {line}", flush=True)


def the_store() -> ObjectStore:
    return open_store(settings().STORE_URL or "")


def pull_config(store: ObjectStore, app: Path) -> Profile:
    """clients/ and deploy/ pulled, mirrored; the profile PROFILE names, else the registry's default."""
    for prefix in ("clients/", "deploy/"):
        say(f"{prefix} pulled into {prefix}, {pull(store, prefix, app / prefix.rstrip('/'))} object(s)")
    if not (app / REGISTRY).is_file():
        raise Refused(f"no {REGISTRY} in the store {store.url}: run setup-client")
    try:
        return resolve(registry=app / REGISTRY)
    except ProfileError as exc:
        raise Refused(str(exc)) from None


def work_key(prof: Profile) -> str:
    return f"work/{prof.client}.tar.gz"


def pull_work(store: ObjectStore, app: Path, prof: Profile) -> None:
    data = store.get(work_key(prof))
    if data is None:
        say(f"{work_key(prof)}: none yet")
        return
    count = unpack(data, app, local_files(prof), candidate_root=prof.store / prof.client)
    say(f"{work_key(prof)} extracted into build/, {count} member(s)")


def landing_store(store: ObjectStore, prof: Profile) -> ObjectStore:
    url = (settings().LANDING_URL or "").strip()
    return open_store(url) if url else open_store(under(store.url, "landing", prof.client))


def landing_folder(prof: Profile) -> Path:
    return prof.landing / prof.client


def release_lease(store, prof) -> None:
    from kge.pipeline import lease
    path = prof.run_dir() / lease.LEASE_FILE
    if path.is_file():
        try:
            lease.release(store, lease.Lease(**json.loads(path.read_text())))
        except (StoreError, ValueError) as exc:
            say(f"warning: could not release the finished job's lease: {exc}")
        finally:
            path.unlink(missing_ok=True)


def before(command: str, app: Path = APP) -> None:
    from kge.pipeline import lease, uploads
    store = the_store()
    prof = pull_config(store, app)
    if command == "serve":
        if store.get(f"releases/{prof.client}/{CURRENT}") is None:
            say(f"nothing released for {prof.client}: release first")
        else:
            fetch(store, app, prof)
        if not (settings().SIGNIN_PASSWORDS or "").strip():
            key = f"secrets/{prof.client}/passwords.json"
            data = store.get(key)
            if data is not None:
                write_private(prof.run_dir() / "passwords.json", data)
                say(f"{key} written to the run state")
        return
    held = lease.take(store, prof.client, command) if command in JOBS else None
    try:
        pull_work(store, app, prof)
        if held:
            write_private(prof.run_dir() / lease.LEASE_FILE, json.dumps(asdict(held)).encode())
        if command == "release":
            (prof.run_dir() / "rollback.json").unlink(missing_ok=True)
        if command == "onboard":
            for name in (ONBOARD_RECORD, ONBOARD_RUN, uploads.UPLOAD_RECORD):
                (prof.run_dir() / name).unlink(missing_ok=True)
        if command in JOBS:
            n = pull(landing_store(store, prof), "", landing_folder(prof), links=(CURRENT,))
            say(f"the landing pulled into {landing_folder(prof).relative_to(app)}/, {n} object(s)")
        if command == "passwords":
            data = store.get(f"secrets/{prof.client}/passwords.json")
            path = prof.run_dir() / "passwords.json"
            if data is None:
                path.unlink(missing_ok=True)
            else:
                write_private(path, data)
    except BaseException:
        if held:
            lease.release(store, held)
        raise


ONBOARD_RECORD = "onboard-changes.json"
ONBOARD_RUN = "onboard-run.json"


def onboard_changes(prof: Profile) -> dict | None:
    """Only a committed execution can request its landing and work to be published."""
    path = prof.run_dir() / ONBOARD_RECORD
    return json.loads(path.read_text()) if path.is_file() else None


def onboard_run_key(prof, record) -> str:
    stamp = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%S%fZ")
    return f"runs/{prof.client}/onboard/{stamp}"


def put_onboard_run(store, prof) -> None:
    path = prof.run_dir() / ONBOARD_RUN
    if not path.is_file():
        return
    record = json.loads(path.read_text())
    key = onboard_run_key(prof, record)
    store.put(key + ".json", path.read_bytes())
    run_id = (record.get("gate") or {}).get("run_id")
    if run_id:
        report = prof.paths()["runs"] / run_id / "report.md"
        if report.is_file():
            store.put(key + "/report.md", report.read_bytes())
    say(f"{key}.json put")


def failed(command: str, app: Path = APP) -> None:
    """A failed onboard publishes only its diagnostic record, never its working copy."""
    store, prof = the_store(), resolve(registry=app / REGISTRY)
    try:
        if command == "onboard":
            put_onboard_run(store, prof)
    finally:
        release_lease(store, prof)


def after(command: str, app: Path = APP, *, release_job: bool = True) -> None:
    from kge.pipeline import uploads
    store, prof = the_store(), resolve(registry=app / REGISTRY)
    try:
        if command == "passwords":
            for name in ("passwords.json", "passwords.txt"):
                store.put(f"secrets/{prof.client}/{name}", (prof.run_dir() / name).read_bytes())
                say(f"secrets/{prof.client}/{name} put")
            return
        changes = onboard_changes(prof) if command == "onboard" else None
        if command == "onboard":
            run_path = prof.run_dir() / ONBOARD_RUN
            if not run_path.is_file() or json.loads(run_path.read_text())["outcome"] not in ("passed", "current already"):
                raise Refused("onboard has no successful run to publish")
            plan = prof.run_dir() / uploads.UPLOAD_RECORD
            if plan.is_file():
                uploads.publish(prof.clients / prof.client, store, prof.client, json.loads(plan.read_text()))
        if command == "seed" or (changes and changes.get("landed")):
            n = push(landing_folder(prof), landing_store(store, prof), "", links=(CURRENT,))
            say(f"the landing pushed, {n} object(s)")
        if command in ("lock", "onboard"):
            lock = prof.clients / prof.client / "pack.lock"
            if lock.is_file():
                store.put(f"clients/{prof.client}/pack.lock", lock.read_bytes())
                say(f"clients/{prof.client}/pack.lock put")
        if command == "baseline":
            baselines = prof.clients / prof.client / "baselines"
            n = push(baselines, store, f"clients/{prof.client}/baselines/") if baselines.is_dir() else 0
            say(f"clients/{prof.client}/baselines/ put, {n} object(s)")
        if command == "improve":
            proposals = prof.clients / prof.client / "proposals"
            if proposals.is_dir():
                n = push(proposals, store, f"clients/{prof.client}/proposals/")
                say(f"clients/{prof.client}/proposals/ put, {n} object(s)")
        paths = work_paths(app, local_files(prof))
        store.put(work_key(prof), tarball(app, paths))
        say(f"{work_key(prof)} put, {len(paths)} member(s)")
        if command == "onboard":
            put_onboard_run(store, prof)
        if command == "release":
            publish(store, app, prof)
    finally:
        if release_job:
            release_lease(store, prof)


def publish(store: ObjectStore, app: Path, prof: Profile) -> str:
    """The released graph's tarball put, then current (and versions.jsonl beside it)."""
    h = prof.model_hash()
    if h is None:
        raise Refused(f"nothing released for {prof.client}: its current pointer names no artifact")
    identity = prof.at(h).serve().identity()
    ledger_path = prof.store / prof.client / "kept.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else []
    entry = next((item for item in ledger if item["identity"] == identity), None)
    key = entry["tarball"] if entry else f"releases/{prof.client}/{h}.tar.gz"
    rollback_path = prof.run_dir() / "rollback.json"
    rollback = json.loads(rollback_path.read_text()) if rollback_path.is_file() else None
    restoring = rollback is not None and rollback.get("hash") == h and rollback.get("release") == identity.get("release")
    if (settings().PREVIOUS or "") == "yes" or restoring:
        if entry is None or store.get(key) is None:
            raise Refused("the release tarball is not held: rollback cannot publish")
        if rollback is None:
            raise Refused("rollback has no restored-input record")
        for item in rollback["restored"]:
            store.put(item["key"], Path(item["file"]).read_bytes())
        for removed in rollback["removed"]:
            store.delete(removed)
    else:
        data = tarball(app, release_paths(prof, h))
        store.put(key, data)
        latest = f"releases/{prof.client}/{h}.tar.gz"
        if latest != key:
            store.put(latest, data)
    evaluation = released.evaluation(prof.at(h).graph_path().parent, "post")
    if evaluation is not None:
        store.put(released.evaluation_key(prof, identity), (json.dumps(evaluation) + "\n").encode())
    if ledger_path.is_file():
        store.put(f"releases/{prof.client}/ledger.json", ledger_path.read_bytes())
    hist = prof.artifacts / prof.client / "versions.jsonl"
    if hist.is_file():
        store.put(f"releases/{prof.client}/versions.jsonl", hist.read_bytes())
    store.put(identity_key(prof), (json.dumps(identity, sort_keys=True) + "\n").encode())
    store.put(f"releases/{prof.client}/{CURRENT}", f"{h}\n".encode())
    say(f"{key} held; releases/{prof.client}/{CURRENT} names {h}")
    return h


def identity_key(prof: Profile) -> str:
    return f"releases/{prof.client}/identity.json"


def released_identity(store: ObjectStore, prof: Profile, model_hash: str) -> dict | None:
    """The identity of the release current names (model_hash), as its publish put it; None where the store holds none
    for that hash (a release published before identities were), or one that cannot be read: a follower then fetches
    the release and compares the identity its release record names (kge/services/onboard/jobs.py follow_release)."""
    data = store.get(identity_key(prof))
    try:
        named = json.loads(data) if data is not None else None
    except ValueError:
        return None
    return released.identity(named) if isinstance(named, dict) and named.get("hash") == model_hash else None


def published_records(prof: Profile) -> list[dict] | None:
    """The stored release floor and advertised identity; local installations have no remote floor."""
    if not (settings().STORE_URL or "").strip():
        return None
    store = the_store()
    prefix = f"releases/{prof.client}/"
    data = store.get(prefix + "ledger.json")
    ledger = json.loads(data) if data is not None else []
    records = [{**entry["identity"], **{key: entry[key] for key in ("inputs", "snapshot", "tarball") if key in entry}}
               for entry in ledger[:2]]
    current = store.get(prefix + CURRENT)
    if current is not None:
        model_hash = current.decode().strip()
        identity = released_identity(store, prof, model_hash)
        if identity is not None:
            record = next((item for item in records if released.identity(item) == identity), None)
            if record is None:
                rid = identity.get("release")
                record = {**identity,
                          "snapshot": f"versions/{model_hash}/{rid}/" if rid else f"versions/{model_hash}/",
                          "tarball": prefix + f"{model_hash}/{rid}.tar.gz" if rid else prefix + f"{model_hash}.tar.gz"}
            records.append(record)
        elif not any(record.get("hash") == model_hash for record in records):
            data = store.get(prefix + model_hash + ".tar.gz")
            if data is None:
                return None
            folder = f"build/stores/{prof.client}/{model_hash}/"
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                names = set(archive.getnames())
                if folder + released.RECORD in names:
                    record = json.load(archive.extractfile(folder + released.RECORD))
                else:
                    record = {"hash": model_hash, "warehouses": {}, "release": None}
                    for part, name in released.LEGACY.items():
                        path = next((folder + candidate for candidate in (name, name.removesuffix(".released"))
                                     if folder + candidate in names), None)
                        record[part] = json.load(archive.extractfile(path)) if path else None
            rid = record.get("release")
            records.append({**record, "snapshot": f"versions/{model_hash}/{rid}/" if rid else f"versions/{model_hash}/",
                            "tarball": prefix + model_hash + ".tar.gz"})
    return records


def fetch(store: ObjectStore, app: Path, prof: Profile) -> str:
    """The current release extracted into build/, and the client's current pointer at it."""
    text = store.get(f"releases/{prof.client}/{CURRENT}")
    if text is None:
        raise Refused(f"nothing released for {prof.client}: run release")
    h = text.decode(errors="replace").strip()
    if not HASH.fullmatch(h):
        raise Refused(f"releases/{prof.client}/{CURRENT} names no model hash")
    identity = released_identity(store, prof, h) or {}
    rid = identity.get("release")
    key = f"releases/{prof.client}/{h}/{check(rid)}.tar.gz" if rid else f"releases/{prof.client}/{h}.tar.gz"
    data = store.get(key)
    if data is None:
        raise Refused(f"releases/{prof.client}/{CURRENT} names {h}, and the store has no {key}")
    n = unpack(data, app, local_files(prof))
    point(prof, h)
    hist_data = store.get(f"releases/{prof.client}/versions.jsonl")
    if hist_data is not None:
        hist_path = prof.artifacts / prof.client / "versions.jsonl"
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        hist_path.write_bytes(hist_data)
    say(f"{key} extracted into build/, {n} member(s); serving {h}")
    return h


def format_setup_client_line(client: str, baseline: str, asker: str | None) -> tuple[str, str | None]:
    """A runnable registry update preserving the profile's querying role."""
    asker_arg = f" ASKER={asker}" if asker else ""
    return f"setup-client CLIENT={client}{asker_arg} BASELINE={baseline}", None


def print_setup_client_line(baseline: str) -> None:
    """Print the next setup-client command for the selected profile."""
    prof = resolve()
    line, _ = format_setup_client_line(prof.client, baseline, prof.asker())
    print(f"  {line}")


def put_example() -> None:
    """FROM/EXAMPLE/** put under the same keys, every one under clients/."""
    example, source = os.getenv("EXAMPLE", "").strip(), os.getenv("FROM", "").strip() or "/kit/examples"
    if not SLUG.fullmatch(example):
        raise Refused(f"EXAMPLE={example!r} is not an example's name ({SLUG.pattern})")
    remote = open_store_if_url(source)
    if remote is not None:
        prefix = f"{example}/"
        keys = remote.list(prefix)
        outside = [k for k in keys if not k.startswith(prefix + "clients/")]
        if outside or not keys:
            raise Refused(f"example {example}: {outside[0] + ' is outside clients/' if outside else 'holds no file'}: nothing put")
        store = the_store()
        for key in keys:
            data = remote.get(key)
            if data is None:
                raise Refused(f"example {example}: {key} disappeared while it was read: nothing else put")
            store.put(check(key[len(prefix):]), data)
        say(f"example {example}: {len(keys)} object(s) put under clients/")
        return
    src = Path(source)
    folder = src / example
    if not folder.is_dir():
        raise Refused(f"no example {example} in {src}")
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("."))
    keys = [p.relative_to(folder).as_posix() for p in files]
    outside = [k for k in keys if not k.startswith("clients/")]
    if outside or not keys:
        raise Refused(f"example {example}: {outside[0] + ' is outside clients/' if outside else 'holds no file'}: nothing put")
    store = the_store()
    for k, p in zip(keys, files):
        store.put(check(k), p.read_bytes())
    say(f"example {example}: {len(keys)} object(s) put under clients/")


def put_baseline() -> None:
    """The kit's single sample, branded as the installed profile, retained as the client's editable pack."""
    from kge.pipeline.branding import SAMPLE, branded_file, display_name
    client = (settings().PROFILE or "").strip()
    if not CLIENT_ID.fullmatch(client):
        raise Refused(f"PROFILE must name the install client: {CLIENT_ID_RULE}")
    name = display_name(client, settings().NAME)
    source = (settings().FROM or "").strip() or "/kit/examples"
    remote = open_store_if_url(source) or open_store(str(Path(source).resolve()))
    sample = SAMPLE["client"]
    prefix = f"{sample}/clients/{sample}/"
    keys = remote.list(f"{sample}/")
    if not keys or any(not key.startswith(prefix) for key in keys):
        raise Refused("the sample must contain only its client's pack files")
    files = {}
    for key in keys:
        data = remote.get(key)
        if data is None:
            raise Refused("a sample file disappeared while reading it")
        rel = check(key[len(prefix):])
        files[rel.replace(sample, client)] = branded_file(rel, data, client, name)
    store = the_store()
    if store.list(f"clients/{client}/"):
        raise Refused("the client already holds a pack")
    for rel, data in files.items():
        store.put(f"clients/{client}/{rel}", data)
    say(f"baseline: {len(files)} object(s) put under clients/{client}/")


def releases(app: Path = APP) -> None:
    store = the_store()
    prof = pull_config(store, app)
    prefix = f"releases/{prof.client}/"
    hashes = [k[len(prefix):-len(".tar.gz")] for k in store.list(prefix) if k.endswith(".tar.gz") and "/" not in k[len(prefix):]]
    if not hashes:
        raise Refused(f"nothing released for {prof.client}: run release")
    cur = (store.get(prefix + CURRENT) or b"").decode(errors="replace").strip()
    say(f"{len(hashes)} release(s) of {prof.client}")
    for h in hashes:
        print(f"  {h}{'  current' if h == cur else ''}", flush=True)


def client(app: Path = APP) -> None:
    """Print only the resolved client id for shell-side scoping."""
    print(resolve(registry=app / REGISTRY).client)


def released_roles(store: ObjectStore, prof: Profile, reader: access_table.Access | None = None) -> dict[str, dict]:
    """Live people resolved against the current release's immutable role templates."""
    current_key = f"releases/{prof.client}/{CURRENT}"
    model_hash = (store.get(current_key) or b"").decode(errors="replace").strip()
    if not HASH.fullmatch(model_hash):
        raise Refused(f"{current_key} names no model hash")
    identity = released_identity(store, prof, model_hash) or {}
    rid = identity.get("release")
    release_key = (f"releases/{prof.client}/{model_hash}/{check(rid)}.tar.gz" if rid else
                   f"releases/{prof.client}/{model_hash}.tar.gz")
    data = store.get(release_key)
    if data is None:
        raise Refused("the current release is unavailable")
    roles_name = f"build/artifacts/{prof.client}/{model_hash}/access/roles.json"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = archive.getmember(roles_name)
            stream = archive.extractfile(member)
            released = json.loads(stream.read() if stream else b"")
        roles = {r["id"]: r for r in released["roles"]}
        people = reader if reader is not None else access_table.Access.for_profile(prof)
        return {username: people.role_of_person(username, roles) for username in people.people()}
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, yaml.YAMLError) as exc:
        raise Refused(f"the current release's roles or access table cannot be read ({type(exc).__name__})") from None


def page_request_running(store, prof) -> str | None:
    from kge.adapters import appstate
    raw = store.get(f"clients/{prof.client}/onboard/request.json")
    if raw and raw.strip():
        return "a page onboarding request is pending: finish it before applying FROM"
    backend = appstate.job_backend(prof)
    jobs = [backend.job()] if backend is not None else []
    raw = store.get(f"clients/{prof.client}/onboard/job.json")
    if raw:
        jobs.append(json.loads(raw))
    for job in jobs:
        if not job or job.get("state") != "running":
            continue
        try:
            started = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
            stale = (datetime.now(timezone.utc) - started).total_seconds() >= 185 * 60
        except (KeyError, TypeError, ValueError):
            stale = False
        if not stale:
            return "a page onboarding job is running: finish it before applying FROM"
    return None


def onboard_request(app: Path = APP) -> None:
    """Validate and execute the page request for this job's fixed profile. Only the request this execution read is
    consumed: a newer one that arrived meanwhile stays for its own execution."""
    from kge.pipeline.attempt import retry_refusal
    from kge.pipeline.roles import candidate_permissions
    if reason := retry_refusal("onboard-request"):
        raise Refused(reason)
    store = the_store()
    prof = pull_config(store, app)
    from kge.adapters import appstate
    state_backend = appstate.job_backend(prof)

    def save_job(value: dict) -> None:
        """Keep the candidate's changes as its steward signs off and releases it, including failed attempts."""
        if (value.get("step") in ("sign-off", "release") and value.get("hash") and not value.get("previous")
                and "changes" not in value):
            if state_backend is not None:
                held = state_backend.job()
            else:
                raw_job = store.get(f"clients/{prof.client}/onboard/job.json")
                held = json.loads(raw_job) if raw_job else None
            if (held and held.get("hash") == value["hash"] and "changes" in held and not held.get("previous")
                    and not (held.get("step") in ("release", "rollback") and held.get("state") == "done")):
                value["changes"] = held["changes"]
        if state_backend is not None:
            state_backend.save_job(value)
        else:
            store.put(f"clients/{prof.client}/onboard/job.json", (json.dumps(value, indent=2) + "\n").encode())

    key = f"clients/{prof.client}/onboard/request.json"
    raw, request_version = store.get_version(key)
    try:
        request = json.loads(raw) if raw is not None else None
    except (ValueError, TypeError):
        store.put_version(key, b"", request_version)
        failed = {"id": None, "step": None, "by": None, "state": "failed", "started_at": None,
                  "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "hash": None,
                  "checked_hash": None, "checks_id": None, "message": "request refused: request is not valid JSON"}
        save_job(failed)
        raise Refused(failed["message"]) from None
    if not isinstance(request, dict):
        raise Refused(f"{key} holds no request")
    command = request.get("command")
    principal = request.get("principal")
    model_hash = request.get("hash")
    previous = request.get("previous", False)
    step = {"onboard": "checks"}.get(command, command)

    job_id = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")

    def refuse(reason: str) -> None:
        """Make this exact refused request terminal before reporting it."""
        store.put_version(key, b"", request_version)
        failed = {"id": request.get("id") if isinstance(request.get("id"), str) and job_id.fullmatch(request["id"]) else None,
                  "step": step if step in ("checks", "improve", "sign-off", "release") else None,
                  "by": principal if isinstance(principal, str) and not access_table.check_username(principal) else None,
                  "state": "failed", "started_at": None,
                  "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "hash": model_hash if isinstance(model_hash, str) and HASH.fullmatch(model_hash) else None,
                  "checked_hash": None, "checks_id": None,
                  "message": f"request refused: {reason}"}
        save_job(failed)
        raise Refused(failed["message"])

    if command not in ("onboard", "improve", "sign-off", "release"):
        refuse("command is not onboard, improve, sign-off or release")
    if not isinstance(principal, str) or access_table.check_username(principal):
        refuse("principal is not valid")
    principal = access_table.normalize_username(principal)
    try:
        datetime.strptime(request.get("timestamp", ""), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        refuse("timestamp is not UTC in YYYY-MM-DDTHH:MM:SSZ form")
    if not isinstance(previous, bool):
        refuse("previous must be a boolean")
    if previous and command != "release":
        refuse("previous is only supported for release")
    if previous and model_hash is not None:
        refuse("previous and hash cannot be supplied together")
    if command in ("sign-off", "release") and not previous and (not isinstance(model_hash, str) or not HASH.fullmatch(model_hash)):
        refuse("sign-off and release require a model hash")
    action = {"onboard": "run", "improve": "improve", "sign-off": "sign_off", "release": "release"}[command]
    roles = {}
    reader = access_table.Access.for_profile(prof)
    try:
        if command == "sign-off" or command == "release" and model_hash:
            pull_work(store, app, prof)
            allowed = candidate_permissions(prof, model_hash, principal, reader=reader)[action]
        else:
            roles = released_roles(store, prof, reader=reader)
            allowed = permissions(roles.get(principal, {}))[action]
    except (Refused, ProfileError):
        refuse("the candidate roles or released roles are unavailable")
    if not allowed:
        refuse(f"{principal} may not run {step}" if command in ("onboard", "improve") else
               f"{principal} may not {step} {model_hash[:12] if model_hash else 'the previous release'}: its roles do not let them certify")
    unit = request.get("unit")
    if unit is not None:
        if command != "improve":
            refuse("unit is only supported for improve")
        if not isinstance(unit, str) or not UNIT.fullmatch(unit):
            refuse("unit is not valid")
        if not may_improve_unit(roles.get(principal, {}), unit):
            refuse(f"{principal} may not improve unit {unit}")
    if not isinstance(request.get("id"), str) or not job_id.fullmatch(request["id"]):
        refuse("id is not valid")
    for field, pattern in (("hash", HASH), ("checked_hash", HASH), ("checks_id", job_id)):
        value = request.get(field)
        if value is not None and (not isinstance(value, str) or not pattern.fullmatch(value)):
            refuse(f"{field} is not valid")
    if bool(request.get("checked_hash")) != bool(request.get("checks_id")):
        refuse("checked_hash and checks_id must be supplied together")
    if command == "improve":
        from kge import packstore
        pstore = packstore.for_profile(prof)
        for name in pstore.list("proposals"):
            if name.endswith("/proposal.yaml") and name.count("/") == 2:
                try:
                    proposal = read_proposal_record(pstore, name)
                except ValueError as exc:
                    refuse(f"409: {exc}")
                released = store.get(f"releases/{prof.client}/current")
                released_hash = released.decode().strip() if released else None
                if proposal_pending(proposal, released_hash):
                    refuse(f"409: {PENDING_PROPOSAL}")
        model_hash = None
    job = {"id": request.get("id"), "step": {"onboard": "checks"}.get(command, command), "by": principal,
           "state": "running", "started_at": request.get("timestamp"), "finished_at": None,
           "hash": model_hash, "checked_hash": request.get("checked_hash") if command in ("sign-off", "release") else None,
           "checks_id": request.get("checks_id") if command in ("sign-off", "release") else None, "message": "started"}
    if previous:
        job.update(previous=True, checked_hash=None, checks_id=None)
    if command == "improve" and unit is not None:
        job["unit"] = unit
    save_job(job)
    args = [command]
    if previous:
        args.append("PREVIOUS=yes")
    if command == "improve" and unit is not None:
        args.append(f"UNIT={unit}")
    if command in ("improve", "sign-off"):
        args.append(f"PRINCIPAL={principal}")
    if model_hash and command in ("sign-off", "release"):
        args.append(f"HASH={model_hash}")
    completed = subprocess.run(["sh", str(app / "entrypoint.sh"), *args], cwd=app, text=True,
                               capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    if command == "onboard" and completed.returncode == 0:
        try:
            model_hash = Path(subprocess.check_output(
                [sys.executable, "-m", "kge.compiler.compile", "--check"], cwd=app, text=True).strip()).name
        except (OSError, subprocess.SubprocessError):
            model_hash = None
    elif command == "improve" and completed.returncode == 0:
        try:
            from kge import packstore
            pstore = packstore.for_profile(prof)
            if pstore.exists("proposals/open/proposal.yaml"):
                prop = yaml.safe_load(pstore.read("proposals/open/proposal.yaml"))
                model_hash = prop.get("candidate_hash") if isinstance(prop, dict) else None
            else:
                model_hash = None
        except Exception:
            model_hash = None
    if previous and completed.returncode == 0:
        current = store.get(f"releases/{prof.client}/current")
        model_hash = current.decode().strip() if current else None
    lines = (completed.stdout + completed.stderr).splitlines()
    job.update(state="done" if completed.returncode == 0 else "failed",
               finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), hash=model_hash,
               message=("job execution completed" if completed.returncode == 0 else
                        "job execution failed"))
    if command == "onboard" and completed.returncode:
        candidates = [line.removeprefix("onboard: candidate ") for line in lines
                      if line.startswith("onboard: candidate ")]
        model_hash = next((value for value in reversed(candidates) if HASH.fullmatch(value)), None)
        job["hash"] = model_hash
    if command == "onboard":
        job["changes"] = None
    if command in ("onboard", "improve") and model_hash:
        from kge.graph.release import state
        try:
            candidate_state = state(prof, model_hash)
            gate = candidate_state.get("gate") or {}
            if command == "onboard":
                job["changes"] = None if "onboard: current already" in lines else candidate_state.get("changes")
        except (OSError, ValueError):
            gate = {}
        job["gate"] = {**gate, "lines": [line for line in lines if re.match(r"^  V0[1-5] ", line)]}
        run_id = gate.get("run_id")
        job["report"] = f"/ab/{run_id}/report.html" if run_id else None
        if completed.returncode == 0 and gate.get("passed") is True:
            job["checked_hash"], job["checks_id"] = model_hash, job["id"]
    if command == "onboard" and completed.returncode:
        job["report"], job["gate"], job["changes"] = None, None, None
        run_path = prof.run_dir() / ONBOARD_RUN
        if run_path.is_file():
            record = json.loads(run_path.read_text())
            job["gate"] = record.get("gate")
            run_id = (record.get("gate") or {}).get("run_id")
            if run_id and (prof.paths()["runs"] / run_id / "report.md").is_file():
                job["message"] += f"; report: {onboard_run_key(prof, record)}/report.md"
    save_job(job)
    store.put_version(key, b"", request_version)
    if completed.returncode:
        raise Refused(job["message"])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    commands = {"put-example": put_example, "put-baseline": put_baseline, "releases": releases, "client": client,
                "onboard-request": onboard_request}
    try:
        if len(args) == 2 and args[0] in ("before", "after", "failed"):
            {"before": before, "after": after, "failed": failed}[args[0]](args[1])
        elif len(args) == 2 and args[0] == "setup-client-line":
            print_setup_client_line(args[1])
        elif len(args) == 1 and args[0] in commands:
            commands[args[0]]()
        else:
            print("usage: objstore.py before|after|failed <command> | setup-client-line <baseline> | put-example | "
                  "releases | client | onboard-request", file=sys.stderr)
            return 2
    except (Refused, ValueError) as exc:
        print(f"store: {exc}", file=sys.stderr)
        return 2
    except StoreError as exc:
        print(f"store: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
