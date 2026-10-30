"""The landing zone: where AI certification lands what the graph may ingest, one release at a time.

    <root>/<client>/releases/<release_id>/   a landed release, never changed once landed
    <root>/<client>/current                  -> releases/<release_id>, swapped whole

Certification runs before the graph, as an application with its own store, and
lands all the content it certified here: documents masked or redacted,
structured sources as metadata only. The graph's loader and the pipeline read
a landed release and nothing else. A release holds:

  manifest.json             client, release_id, policy and version, as_of,
                            files {path: sha256}, counts and each document's certification path; no timestamps
  certification.json        one row per document: AI-ready or not, tier, evidence
  certification-paths.json  each document's upstream or stand-in certification path; an ignored assertion also says why
  documents/index.json      every document's metadata, both shelves
  documents/<id>.md         every document, masked
  documents/chunks.jsonl    one row per chunk: id, doc_id, ordinal, text (masked)
                            and findings {family, value, evidence (redacted), confidence}
  structured/<source>.json  one source's catalogue: tables, columns, keys; never rows
  review/<source>.json      a curated source's matches certification did not certify (a cross-reference's
                            proposed or low-confidence ones), for a steward: each match's id, method,
                            confidence and certification status, and the table they are in; never what
                            they match

Zone(root, client).land(staging, about) hashes the staging folder, writes its
manifest, renames it to releases/<release_id>/ and swaps current with
os.replace; land_files does the same from files in memory. The id is the first
16 hex characters of sha256 over the client and the files' hashes, so the same
content lands under the same id, and landing it again writes nothing new. Zone.open(release) checks every file against the
manifest before anything is read from it, and refuses an id or a path outside
the client's releases folder. Names no client: the client is the caller's.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

MANIFEST = "manifest.json"
CERTIFICATION = "certification.json"
INDEX = "documents/index.json"
CHUNKS = "documents/chunks.jsonl"
REVIEW = "review"
RELEASE_ID = re.compile(r"[0-9a-f]{16}")
CLIENT_ID = re.compile(r"[a-z][a-z0-9-]{0,15}")
CLIENT_ID_RULE = ("a client id is a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at "
                  "most, so that each of its service accounts' ids (kge-<client>-svc, -job and -improve) fits "
                  "Google's 30 characters")
SOURCE = re.compile(r"[a-z][a-z0-9_-]*")
FILE = re.compile(r"[\w.-]+(?:/[\w.-]+)*")


class LandingError(RuntimeError):
    """A release that has not landed, is not whole, or is not the client's."""


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def release_id(client: str, files: dict[str, str]) -> str:
    """The first 16 hex characters of sha256 over the client and each file's path and hash, in path order."""
    h = hashlib.sha256(f"{client}\n".encode())
    for path, digest in sorted(files.items()):
        h.update(f"{path}\0{digest}\n".encode())
    return h.hexdigest()[:16]


class Zone:
    """One client's landing: its releases and the current pointer."""

    def __init__(self, root: Path, client: str):
        if not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
            raise LandingError(f"client {client!r} is refused: {CLIENT_ID_RULE}")
        self.root, self.client = Path(root), client
        self.home = self.root / client
        self.releases = self.home / "releases"
        self.current = self.home / "current"

    def stage(self) -> Path:
        """A new, empty folder to land from: under the zone's root, so landing it is a rename on one file system."""
        path = self.root / f".staging-{self.client}-{uuid.uuid4().hex[:12]}"
        path.mkdir(parents=True)
        return path

    def land(self, staging: Path, about: dict) -> Release:
        """Land the staging folder as a release and point current at it. about: policy, version, as_of and counts.
        A release already landed with the same files is kept as it is, and the staging folder goes either way."""
        staging = Path(staging)
        try:
            files = {}
            for p in sorted(staging.rglob("*")):
                rel = p.relative_to(staging).as_posix()
                if p.is_symlink():
                    raise LandingError(f"{rel} in {staging} is a link: a release holds its own files")
                if rel == MANIFEST:
                    raise LandingError(f"{staging} holds a {MANIFEST}: landing writes it")
                if p.is_file():
                    files[rel] = sha256(p)
            rid = release_id(self.client, files)
            manifest = {"client": self.client, "release_id": rid, **about, "files": files}
            target = self.releases / rid
            if target.exists() or target.is_symlink():
                release = self.landed(manifest)
                shutil.rmtree(staging)
            else:
                (staging / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
                self.releases.mkdir(parents=True, exist_ok=True)
                os.rename(staging, target)
                release = self.open(rid)
                release.new = True
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.point(rid)
        return release

    def land_files(self, files: dict[str, bytes], about: dict) -> Release:
        """land, from files (path -> content) in memory: staged only if no release holds them yet, so the same content
        landed again writes nothing at all."""
        hashes = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}
        manifest = {"client": self.client, "release_id": release_id(self.client, hashes), **about, "files": hashes}
        if (self.releases / manifest["release_id"]).exists():
            release = self.landed(manifest)
            self.point(release.id)
            return release
        staging = self.stage()
        try:
            for rel, data in files.items():
                if not FILE.fullmatch(rel) or ".." in rel.split("/") or rel == MANIFEST:
                    raise LandingError(f"{rel!r} is not a path a release may hold")
                (staging / rel).parent.mkdir(parents=True, exist_ok=True)
                (staging / rel).write_bytes(data)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.land(staging, about)

    def landed(self, manifest: dict) -> Release:
        """The release already landed with manifest's files, checked whole; refused if its manifest says otherwise."""
        release = self.open(manifest["release_id"])
        if release.manifest != manifest:
            raise LandingError(f"{release.path} holds the same files under another manifest: refused")
        return release

    def point(self, rid: str) -> bool:
        """current -> releases/<rid>, swapped with os.replace, so a reader finds the old release or the new one, never
        neither. False if it already points there: nothing is written."""
        link = f"releases/{rid}"
        if self.current.is_symlink() and os.readlink(self.current) == link:
            return False
        tmp = self.home / f".current-{uuid.uuid4().hex[:12]}"
        os.symlink(link, tmp)
        os.replace(tmp, self.current)
        return True

    def open(self, release: str | None = None) -> Release:
        """A landed release, by its id, or current (None), with every file checked against its manifest."""
        rid = release or "current"
        if rid == "current":
            if not self.current.is_symlink():
                raise LandingError(f"nothing has landed for {self.client} in {self.root}: no {self.current}. "
                                   "Run make certify (kge/estate/certify.py)")
            link = os.readlink(self.current)
            m = re.fullmatch(r"releases/([0-9a-f]{16})", link)
            if not m:
                raise LandingError(f"{self.current} points outside {self.releases} ({link}): refused")
            rid = m.group(1)
        if not isinstance(rid, str) or not RELEASE_ID.fullmatch(rid):
            raise LandingError(f"{rid!r} is not a release id, 16 lower-case hex characters: refused")
        path = self.releases / rid
        if not path.is_dir():
            raise LandingError(f"no release {rid} for {self.client} in {self.releases}")
        if path.resolve().parent != self.releases.resolve():
            raise LandingError(f"{path} lies outside {self.releases}: refused")
        try:
            manifest = json.loads((path / MANIFEST).read_text())
        except (OSError, ValueError) as exc:
            raise LandingError(f"{path / MANIFEST} cannot be read ({type(exc).__name__}): the release is not whole") from None
        if not isinstance(manifest, dict) or manifest.get("client") != self.client or manifest.get("release_id") != rid:
            raise LandingError(f"{path / MANIFEST} is not {self.client}'s release {rid}: refused")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise LandingError(f"{path / MANIFEST} lists no files")
        top = path.resolve()
        for rel in files:
            if not FILE.fullmatch(rel) or ".." in rel.split("/") or not (path / rel).resolve().is_relative_to(top):
                raise LandingError(f"{path / MANIFEST} names {rel!r}, a path outside the release: refused")
        on_disk = {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file() or p.is_symlink()} - {MANIFEST}
        if on_disk - set(files):
            raise LandingError(f"{path} holds {', '.join(sorted(on_disk - set(files)))}, not in its manifest: refused")
        for rel, digest in sorted(files.items()):
            p = path / rel
            if p.is_symlink() or not p.is_file():
                raise LandingError(f"{path} has no {rel}, which its manifest lists: the release is not whole")
            if sha256(p) != digest:
                raise LandingError(f"{path / rel} does not match its manifest: changed since it landed, refused")
        if release_id(self.client, files) != rid:
            raise LandingError(f"{path / MANIFEST} lists files whose hashes do not give its release id {rid}: refused")
        return Release(self, rid, path, manifest)


class Release:
    """One landed release, checked whole when opened. Its files are read through it."""

    def __init__(self, zone: Zone, rid: str, path: Path, manifest: dict):
        self.zone, self.id, self.path, self.manifest = zone, rid, path, manifest
        self.client = zone.client
        self.new = False

    def __repr__(self) -> str:
        return f"release {self.id} of {self.client}"

    def about(self) -> dict:
        """What the graph records of the release it was loaded from: its Release node."""
        return {"id": self.id, "client": self.client, "policy": self.manifest.get("policy"), "as_of": self.manifest.get("as_of")}

    def has(self, rel: str) -> bool:
        return rel in self.manifest["files"]

    def text(self, rel: str) -> str:
        if not self.has(rel):
            raise LandingError(f"{self} lands no {rel}")
        return (self.path / rel).read_text()

    def certification(self) -> list[dict]:
        return json.loads(self.text(CERTIFICATION))

    def documents(self) -> list[dict]:
        return json.loads(self.text(INDEX))

    def chunks(self) -> list[dict]:
        return [json.loads(line) for line in self.text(CHUNKS).splitlines() if line.strip()]

    def sources(self) -> list[str]:
        return sorted(rel[len("structured/"):-len(".json")] for rel in self.manifest["files"]
                      if rel.startswith("structured/") and rel.endswith(".json") and rel.count("/") == 1)

    def structured(self, source: str) -> dict:
        if not isinstance(source, str) or not SOURCE.fullmatch(source):
            raise LandingError(f"{source!r} is not a source's name: refused")
        return json.loads(self.text(f"structured/{source}.json"))

    def reviews(self) -> dict[str, dict]:
        """Each curated source's matches for a steward, by source: {table, floor, matches: [{id, method, confidence, status}]}."""
        return {rel[len(REVIEW) + 1:-len(".json")]: json.loads(self.text(rel)) for rel in sorted(self.manifest["files"])
                if rel.startswith(REVIEW + "/") and rel.endswith(".json") and rel.count("/") == 1}
