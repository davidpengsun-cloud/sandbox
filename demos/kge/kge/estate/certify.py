"""Upstream AI certification, as a stand-in: it grades every document, then lands what it certified.

    python3 -m kge.estate.certify [--profile <id>] [--as-of 2025-07-01]
    python3 -m kge.estate.certify --build build --client <client> [--clients clients] [--landing build/landing] [--as-of 2025-07-01]

The estate, the client and the landing zone are the profile's
(kge/pipeline/profile.py: --profile, else PROFILE, else the registry's default):
its estate (Profile.estate, build/ in the demo), its client, and its landing.
--build, --client and --landing name them instead, the landing then
<build>/landing unless given. The client's functional mailboxes, which masking
keeps in clear, are its pack's (terminology.functional_mailboxes, in
<clients>/<client>/: the profile's clients, or --clients, the demo's clients/
unless given).

AI certification runs before the graph and is an application of its own. It is
the only reader of raw text: it reads the estate's documents (build/documents.json
and the text on both shelves) and each warehouse's catalogue, never the graph,
and grades one row per document. A document with a complete, valid upstream
certification block keeps that row instead. Any other block is ignored and the
stand-in grades the document; prohibited personal data takes that path too:

  asset_id        the document
  ai_ready        yes or no
  tier            gold, silver or bronze when ai_ready is yes; null otherwise
  evidence        why, in words; a finding is named, never quoted
  policy_id       the policy the row was derived under, and its version
  version
  effective_from  when the document came into force: its publication, or this
                  run's date for a document never published
  effective_to    its last day in force, the day before its successor was
                  published; null while nothing supersedes it

The rules:

  ai_ready  no if any chunk carries personal data the gate prohibits (the
            pipeline's own chunking, detectors and readiness rule), or the
            title or kind is missing
  gold      a certified-shelf document with an owner, a version and a
            publication date, reviewed within the review window
  silver    the same, with its review overdue
  bronze    everything else that is AI-ready: for reasoning and reference

A superseded document keeps the tier it held while in force: its review is
judged on its last day in force, not on this run's date. Which period a
document may be cited for is its effective window's business, not its tier's.

Grades are taken on the raw text. Then all of it lands, as one release in the
landing zone, build/landing/<client>/releases/<release_id>/, with current
pointing at it (kge/landing/zone.py): the rows (certification.json); every
document's metadata (documents/index.json, without the golden set's planted
expectations or any source path); every document and every chunk masked
(documents/<id>.md, documents/chunks.jsonl), each chunk cut on the raw text, so
its id and bounds are the pipeline's, then masked, with what was found in it,
redacted; and each warehouse's catalogue, never its rows (structured/<source>.json).
A cross-reference's matches (a table that holds a match's certification
status, method and confidence) that are proposed, or below the confidence
floor, land for a steward as their ids, methods, confidences and certification
statuses only (proposed, or rejected upstream), never the ids they match
(review/<source>.json): R2 lands structured sources as metadata only.
Masking is kge/pipeline/privacy.py's five detectors, with the client's functional
mailboxes kept in clear, plus a rule of certification's own for customer names
a document labels as such (NAMES: "Name First Last" in an email, an export's
customer_name column): each such name is masked wherever it stands in the estate, and makes no tag. Staff names in
sign-offs and From lines stay in clear, as business contacts, as a role mailbox
does, unless a document labels the same name as a customer's: then it is a
customer's name, and it goes there too.

Certification keeps its own run record in its own store, in the estate it
certified (--build, else the profile's estate, the folder that holds its
warehouses: build/ for the demo's generated estate),
<estate>/certification/<client>/runs/<release_id>.json: the inputs' hashes, the
grades, and each finding as its kind and offsets, never the text. Nothing in
kge/graph/ or kge/pipeline/ reads it. The same estate lands the same release, and a
second run writes nothing new. Deterministic for the documents and warehouses
on disk and the --as-of date (default 2025-07-01, the day after the estate's
last month).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from kge.pipeline.settings import ROOT
from kge.compiler import pack as packs
from kge.landing.zone import LandingError, Zone
from kge.adapters.warehouse import open_estate
from kge.pipeline import warehouse_versions
from kge.pipeline.chunking import document_chunks
from kge.pipeline.privacy import DETECTORS as PRIVACY, REDACTED, mask
from kge.pipeline.tagger import RuleTagger, derive_readiness
from kge.ports import EstateWarehouses, WarehouseRefused, WarehouseSession

DEMO = ROOT
RUNS = Path("certification")
POLICY_ID = "ai-certification"
POLICY_VERSION = "1.0"
REVIEW_WINDOW_DAYS = 90
AS_OF = dt.date(2025, 7, 1)
TAGGER = RuleTagger([])
INDEX_FIELDS = ("title", "shelf", "kind", "unit", "owner", "version", "published_at", "superseded_by")
MATCH = {"status": "certification_status", "method": "match_method", "confidence": "match_confidence", "current": "is_current"}
PROPOSED = "PROPOSED"
CONFIDENCE_FLOOR = 0.9


class CsvColumn:
    """The values of one named column in comma-separated rows: the rows that follow a header naming it, as long as
    each has the header's number of fields. A detector pattern for mask: finditer yields each value's match."""

    def __init__(self, name: str):
        self.name = name

    def finditer(self, text: str):
        at, width, index = 0, None, None
        for line in text.splitlines(keepends=True):
            fields = line.rstrip("\r\n").split(",")
            if width is not None and len(fields) != width:
                width = index = None
            if width is None and self.name in fields:
                width, index = len(fields), fields.index(self.name)
            elif width is not None:
                start = at + sum(len(f) + 1 for f in fields[:index])
                yield Span(text, start, start + len(fields[index]))
            at += len(line)


class Span:
    """A match, as mask reads one: its span and its text."""

    def __init__(self, text: str, start: int, end: int):
        self.text, self.start_, self.end_ = text, start, end

    def span(self, group=0) -> tuple[int, int]:
        return self.start_, self.end_

    def group(self, group=0) -> str:
        return self.text[self.start_:self.end_]


NAME_LABEL = re.compile(r"\bName (?P<v>[A-Z][a-z]+(?: [A-Z][a-z]+)+)")
NAMES = ((NAME_LABEL, "personal_data:name"), (CsvColumn("customer_name"), "personal_data:name"))
TAGGED = {value for _, value in PRIVACY}


class NoPack(ValueError):
    """The client's pack cannot be read for its functional mailboxes."""


CERTIFICATION_REQUIRED = ("ai_ready", "tier", "policy_id", "policy_version", "effective_from")
CERTIFICATION_OPTIONAL = ("effective_to", "evidence")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


def upstream_certification(doc: dict, texts: list[str]) -> tuple[dict | None, str | None]:
    """The validated upstream row and no reason, or no row and the short reason it was ignored."""
    if "certification" not in doc or doc.get("certification") is None:
        return None, None
    held = doc["certification"]
    def ignored(key, why):
        return None, f"{key} {why}"
    if not isinstance(held, dict):
        return ignored("certification", "is not a mapping")
    unknown = sorted(set(held) - set(CERTIFICATION_REQUIRED) - set(CERTIFICATION_OPTIONAL))
    if unknown:
        return ignored(unknown[0], "is not allowed")
    missing = [key for key in CERTIFICATION_REQUIRED if key not in held]
    if missing:
        return ignored(missing[0], "is missing")
    ai_ready = held["ai_ready"]
    if not isinstance(ai_ready, str) or ai_ready not in ("yes", "no"):
        return ignored("ai_ready", "must be 'yes' or 'no'")
    if ai_ready == "yes" and doc.get("shelf") != "certified":
        return ignored("ai_ready", "cannot be yes on the uncertified shelf")
    tier = held["tier"]
    if ai_ready == "yes" and tier not in ("gold", "silver", "bronze"):
        return ignored("tier", "must be gold, silver or bronze when ai_ready is yes")
    if ai_ready == "no" and tier is not None:
        return ignored("tier", "must be null when ai_ready is no")
    for key in ("policy_id", "policy_version"):
        if not isinstance(held[key], str) or not IDENTIFIER.fullmatch(held[key]):
            return ignored(key, "is not a sane identifier")
    dates = {}
    for key in ("effective_from", "effective_to"):
        value = held.get(key)
        if value is None and key == "effective_to":
            dates[key] = None
            continue
        if not isinstance(value, str):
            return ignored(key, "must be an ISO date")
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError:
            return ignored(key, "must be an ISO date")
        if parsed.isoformat() != value:
            return ignored(key, "must be an ISO date")
        dates[key] = value
    if dates["effective_to"] and dates["effective_to"] < dates["effective_from"]:
        return ignored("effective_to", "must not precede effective_from")
    evidence = held.get("evidence")
    if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
        return ignored("evidence", "must be non-empty text")
    found = [(f"{doc['doc_id']}#{i}", kinds) for i, text in enumerate(texts)
             if (kinds := prohibited(text, doc["shelf"], doc.get("kind") or ""))]
    if found:
        return None, "personal data prohibited"
    return {"asset_id": doc["doc_id"], "ai_ready": ai_ready, "tier": tier,
            "evidence": evidence.strip() if evidence else f"certified upstream under {held['policy_id']} {held['policy_version']}",
            "policy_id": held["policy_id"], "version": held["policy_version"], **dates}, None


def mailboxes(pack: Path) -> frozenset[str]:
    """The client's functional mailboxes, its pack's terminology.functional_mailboxes (pack.yaml and the files it
    includes): an address at one is no person's, so masking keeps it in clear. None if the pack lists none. NoPack,
    naming why, if the pack cannot be read."""
    pack = Path(pack)
    held = packs.read(pack.parent.parent, pack.parent.name, lock=False)
    if held.findings or not isinstance(held.data, dict):
        raise NoPack(f"the client's pack {pack} cannot be read for its functional mailboxes: "
                     + "; ".join(str(f) for f in held.findings[:3]))
    t = held.data.get("terminology")
    return frozenset((t.get("functional_mailboxes") if isinstance(t, dict) else None) or [])


def labelled_names(texts) -> list[str]:
    """Every customer name the texts label as one (NAMES), longest first."""
    names = set()
    for text in texts:
        names |= {n for f in mask(text, NAMES)[1] if (n := text[f["start"]:f["end"]].strip()) and not REDACTED.fullmatch(n)}
    return sorted(names, key=lambda n: (-len(n), n))


def detectors(names) -> tuple:
    """What certification masks with: the five detectors, and each labelled customer name, wherever it stands. A name
    one document labels is a customer's in every document, so a staff sign-off or From line with that name goes too."""
    known = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)") if names else re.compile(r"(?!)")
    return PRIVACY + ((known, "personal_data:name"),)


def chunks(build: Path, doc: dict) -> list[str]:
    """The document's chunks, raw, cut as the pipeline cuts them, so the same detectors see the same text."""
    return document_chunks(doc.get("title"), (build / doc["path"]).read_text())


def prohibited(text: str, shelf: str, kind: str) -> list[str]:
    """What the gate prohibits in one chunk, by kind of finding: empty if it may be embedded."""
    proposals = TAGGER.propose(text, shelf, kind or "")
    if derive_readiness(proposals, shelf)[0] != "embed-prohibited":
        return []
    return sorted({p.value for p in proposals if p.family == "privacy"}) or ["no classification"]


def certify(doc: dict, texts: list[str], as_of: dt.date = AS_OF, in_force_until: str | None = None) -> dict:
    """One document's row. texts: its chunks. in_force_until: its last day in force, if superseded."""
    upstream, _ = upstream_certification(doc, texts)
    if upstream is not None:
        return upstream
    row = {"asset_id": doc["doc_id"], "ai_ready": "no", "tier": None, "evidence": "", "policy_id": POLICY_ID, "version": POLICY_VERSION,
           "effective_from": doc.get("published_at") or as_of.isoformat(), "effective_to": in_force_until}
    missing = [f for f in ("title", "kind") if not doc.get(f)]
    if missing:
        return {**row, "evidence": f"not consumable: no {' or '.join(missing)}"}
    found = [(f"{doc['doc_id']}#{i}", kinds) for i, t in enumerate(texts) if (kinds := prohibited(t, doc["shelf"], doc["kind"]))]
    if found:
        return {**row, "evidence": "personal data the gate prohibits: " + "; ".join(f"{c} ({', '.join(k)})" for c, k in found)}
    certified = doc["shelf"] == "certified" and all(doc.get(k) for k in ("owner", "version", "published_at"))
    if not certified:
        why = "uncertified shelf" if doc["shelf"] != "certified" else "no " + " or ".join(k for k in ("owner", "version", "published_at") if not doc.get(k))
        return {**row, "ai_ready": "yes", "tier": "bronze", "evidence": f"AI-ready; for reasoning and reference ({why})"}
    judged = min(as_of, dt.date.fromisoformat(in_force_until)) if in_force_until else as_of
    reviewed = doc.get("reviewed_at")
    current = bool(reviewed) and 0 <= (judged - dt.date.fromisoformat(reviewed)).days <= REVIEW_WINDOW_DAYS
    on = f"{judged.isoformat()}{', its last day in force' if in_force_until and judged < as_of else ''}"
    base = f"certified shelf; owner {doc['owner']}; version {doc['version']}; published {doc['published_at']}"
    if current:
        return {**row, "ai_ready": "yes", "tier": "gold", "evidence": f"{base}; reviewed {reviewed}, within {REVIEW_WINDOW_DAYS} days of {on}"}
    return {**row, "ai_ready": "yes", "tier": "silver",
            "evidence": f"{base}; review overdue: last reviewed {reviewed or 'never'}, more than {REVIEW_WINDOW_DAYS} days before {on}"}


def certification_path(doc: dict, texts: list[str]):
    """The manifest value: the path used, with the short reason when an asserted block was ignored."""
    _, reason = upstream_certification(doc, texts)
    return ({"path": "stand-in", "ignored": reason} if reason else
            "upstream" if doc.get("certification") is not None else "stand-in")


def certify_estate(build: Path, as_of: dt.date = AS_OF) -> list[dict]:
    docs = json.loads((build / "documents.json").read_text())
    published = {d["doc_id"]: d.get("published_at") for d in docs}
    rows = []
    for d in docs:
        successor = published.get(d.get("superseded_by") or "")
        until = (dt.date.fromisoformat(successor) - dt.timedelta(days=1)).isoformat() if successor else None
        rows.append(certify(d, chunks(build, d), as_of, until))
    return rows


def catalogue(session: WarehouseSession, source: str) -> dict:
    """One warehouse's catalogue, never its rows: its tables in name order, each with its columns (name, type,
    comment, ordinal), its primary key and its foreign keys with every column, from the warehouse's own catalogue (the
    estate's warehouse port: DuckDB's, or the declared tables BigQuery keeps beside its own)."""
    tables = []
    for name in session.tables():
        found = session.describe(name)
        tables.append({"name": name, "columns": [{"name": c["name"], "type": c["type"], "comment": c["comment"],
                                                  "ordinal": c["ordinal"]} for c in found["columns"]],
                       "primary_key": list(found["primary_key"]), "foreign_keys": found["foreign_keys"]})
    return {"source": source, "tables": tables}


def catalogue_of(estate: EstateWarehouses, unit: str, version: int | None = None) -> dict:
    """catalogue of the unit's warehouse (version, an onboarded unit's), opened through the estate's warehouse port."""
    session = estate.open(unit, version)
    try:
        return catalogue(session, unit)
    finally:
        session.close()


def review(session: WarehouseSession, source: str, cat: dict) -> dict | None:
    """A cross-reference's matches for a steward: each current match that is proposed, or below the confidence floor,
    as its id (the table's key), method, confidence and certification status (upstream's: proposed, or rejected); never
    the ids it matches. None for a warehouse with none."""
    table = next((t for t in cat["tables"] if {MATCH["status"], MATCH["method"], MATCH["confidence"]} <= {c["name"] for c in t["columns"]}
                  and t["primary_key"]), None)
    if table is None:
        return None
    key, cols = table["primary_key"][0], {c["name"] for c in table["columns"]}
    current = f" AND {MATCH['current']}" if MATCH["current"] in cols else ""
    rows = session.execute(f"SELECT {key}, {MATCH['method']}, {MATCH['confidence']}, {MATCH['status']} FROM {table['name']} "
                           f"WHERE ({MATCH['status']} = ? OR {MATCH['confidence']} < ?){current} ORDER BY {key}",
                           [PROPOSED, CONFIDENCE_FLOOR]).fetchall()
    return {"source": source, "table": table["name"], "floor": CONFIDENCE_FLOOR,
            "matches": [{"id": k, "method": m, "confidence": float(c), "status": st} for k, m, c, st in rows]}


def chunk_findings(found: list[dict]) -> list[dict]:
    """What lands with a chunk of what masking found in it: one finding per value the pipeline tags, in the detectors'
    order, its evidence the first match's, redacted. A masked name lands none."""
    order = [value for _, value in PRIVACY]
    first: dict[str, dict] = {}
    for f in found:
        if f["value"] in TAGGED:
            first.setdefault(f["value"], {k: f[k] for k in ("family", "value", "evidence", "confidence")})
    return sorted(first.values(), key=lambda f: order.index(f["value"]))


def canonical(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True) + "\n"


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def land(build: Path, client: str, landing: Path | None = None, as_of: dt.date = AS_OF, role_mailboxes=frozenset(),
         onboarded: dict[str, int] | None = None, estate: EstateWarehouses | None = None):
    """Grade the estate in build, land it for client in landing (default build/landing), masked with role_mailboxes (the
    client's functional mailboxes) kept in clear, and keep the run record. Returns the release, the rows and the run
    record's path. onboarded: the version of each onboarded unit's warehouse its draft names (kge/pipeline/warehouse_versions.py
    live_versions), catalogued as the unit's source beside the estate's own warehouses, never an established one an
    older layout left. estate: the client's warehouses (the factory's, open_estate, for build's unless given)."""
    build = Path(build)
    estate = estate or open_estate(build / "warehouses", client)
    zone = Zone(Path(landing) if landing else build / "landing", client)
    docs = json.loads((build / "documents.json").read_text())
    rows = certify_estate(build, as_of)
    onboarded = onboarded or {}
    warehouses = sorted([(u, None) for u in estate.established() if u not in onboarded] + list(onboarded.items()))
    inputs = {"documents.json": sha256((build / "documents.json").read_bytes())}
    findings, masked_chunks, lines = [], 0, []
    paths = {d["doc_id"]: certification_path(d, chunks(build, d)) for d in docs}
    files = {"certification.json": json.dumps(rows, indent=1) + "\n",
             "certification-paths.json": canonical(paths),
             "documents/index.json": json.dumps([{"id": d["doc_id"], **{k: d.get(k) for k in INDEX_FIELDS}} for d in docs], indent=1) + "\n"}
    texts = {d["doc_id"]: (build / d["path"]).read_text() for d in docs}
    masking = detectors(labelled_names(texts.values()))
    for d in docs:
        text = texts[d["doc_id"]]
        inputs[d["path"]] = sha256(text)
        files[f"documents/{d['doc_id']}.md"] = mask(text, masking, role_mailboxes)[0]
        for i, raw in enumerate(chunks(build, d)):
            cid = f"{d['doc_id']}#{i}"
            masked, found = mask(raw, masking, role_mailboxes)
            masked_chunks += masked != raw
            findings += [{"chunk": cid, "kind": f["value"], "start": f["start"], "end": f["end"]} for f in found]
            lines.append(json.dumps({"id": cid, "doc_id": d["doc_id"], "ordinal": i, "text": masked, "findings": chunk_findings(found)}) + "\n")
    files["documents/chunks.jsonl"] = "".join(lines)
    tables, matches = 0, 0
    for unit, version in warehouses:
        session = estate.open(unit, version)
        try:
            cat = catalogue(session, unit)
            held = review(session, unit, cat)
        finally:
            session.close()
        files[f"structured/{unit}.json"] = canonical(cat)
        where = estate.label(unit, version)
        place = Path(where)
        if place.is_relative_to(build):
            where = place.relative_to(build).as_posix()
        elif place.is_absolute():
            where = f"warehouses/{place.name}"
        inputs[f"{where}#catalogue"] = sha256(files[f"structured/{unit}.json"])
        tables += len(cat["tables"])
        if held:
            files[f"review/{unit}.json"] = canonical(held)
            matches += len(held["matches"])
    counts = {"documents": len(docs), "chunks": len(lines), "masked_chunks": masked_chunks, "sources": len(warehouses), "tables": tables,
              "matches_for_review": matches}
    release = zone.land_files({rel: text.encode() for rel, text in files.items()},
                              {"policy": POLICY_ID, "version": POLICY_VERSION, "as_of": as_of.isoformat(), "counts": counts,
                               "certification_paths": paths})
    record = {"client": client, "release_id": release.id, "policy": POLICY_ID, "version": POLICY_VERSION, "as_of": as_of.isoformat(),
              "inputs": inputs, "grades": [{k: r[k] for k in ("asset_id", "ai_ready", "tier")} for r in rows], "findings": findings}
    path = build / RUNS / client / "runs" / f"{release.id}.json"
    text = canonical(record)
    if not path.is_file() or path.read_text() != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text)
        tmp.replace(path)
    return release, rows, path


def main() -> int:
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose estate, client and landing zone (default: PROFILE, else the registry's default)")
    ap.add_argument("--build", help="the estate to certify, instead of the profile's (the landing then <build>/landing)")
    ap.add_argument("--client", help="whose estate this is, instead of the profile's client")
    ap.add_argument("--landing", help="the landing zone to land in, instead of the profile's")
    ap.add_argument("--clients", help="the folder that holds the client's pack, instead of the profile's (the demo's clients/ with "
                                      "--build and --client)")
    ap.add_argument("--as-of", default=AS_OF.isoformat(), help="the date reviews are judged on (YYYY-MM-DD)")
    args = ap.parse_args()
    prof = None
    if not (args.build and args.client):
        try:
            prof = resolve(args.profile)
        except ProfileError as exc:
            print(f"certify: {exc}", file=sys.stderr)
            return 2
    build = Path(args.build) if args.build else prof.estate()
    client = args.client or prof.client
    landing = Path(args.landing) if args.landing else (build / "landing" if args.build else prof.landing)
    clients = Path(args.clients) if args.clients else prof.clients if prof is not None else DEMO / "clients"
    try:
        estate = open_estate(build / "warehouses", client)
        onboarded = warehouse_versions.live_versions(clients / client)
        for unit, version in onboarded.items():
            if version not in estate.versions(unit):
                raise warehouse_versions.MissingWarehouse(f"unit {unit}'s draft names warehouse v{version}, not held in "
                                                          f"{estate.label(unit, version)}: make onboard builds it")
        release, rows, record = land(build, client, landing, dt.date.fromisoformat(args.as_of), mailboxes(clients / client / packs.PACK),
                                     onboarded, estate)
    except (LandingError, NoPack, warehouse_versions.MissingWarehouse, WarehouseRefused) as exc:
        print(f"certify: {exc}", file=sys.stderr)
        return 2
    tiers = Counter(r["tier"] for r in rows if r["ai_ready"] == "yes")
    print(json.dumps({"documents": len(rows), "ai_ready": sum(tiers.values()), **{t: tiers[t] for t in ("gold", "silver", "bronze")},
                      "not_ready": [r["asset_id"] for r in rows if r["ai_ready"] == "no"], "release": release.id, "new": release.new,
                      "landed": str(release.path), "masked_chunks": release.manifest["counts"]["masked_chunks"], "run_record": str(record)},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
