"""The landing zone: AI certification lands masked documents and catalogues, and the graph ingests only from there.

    .venv/bin/python -m pytest tests/integration/test_landing.py -q

AI certification (kge/estate/certify.py) is the only reader of raw text. It grades
on the raw text as before, then lands one release under build/landing/<client>/
(kge/landing/zone.py): its rows, every document's metadata, each document and each
chunk masked (kge/pipeline/privacy.py), and each warehouse's catalogue, never its
rows. Its run record, <estate>/certification/<client>/runs/<release_id>.json in
the profile's estate (build/ for Meridian), is certification's own. The graph loader and the pipeline read a landed release
and nothing else of the estate (the loader reads the compiled pack beside it),
so the estate can be moved away and the graph still builds.
A customer name a document labels is masked wherever it stands, so no
detector match and no labelled name lands anywhere. Masking changes chunks the
gate already prohibited, and on the demo estate three emails signed with a
labelled customer's name (SIGNED), whose vectors are the masked text's; every
other vector is the raw text's, as before. Staff sign or send those emails, but
the generator draws customers and staff from one pool of invented names.

MAILBOXES are the client's functional mailboxes, from its pack (#39 item 11):
certification keeps an address at one in clear. CLIENT is the client of the
registry's default profile.

Opens the demo's graph and builds scratch graphs from the landing: stop the API first.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from kge.services.ask.engine import runtrace
from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.estate import certify
from kge.landing.zone import LandingError, Zone, release_id
from kge.pipeline import privacy
from kge.pipeline.chunking import chunk_text, strip_header
from kge.adapters.model import make_embedder
from kge.adapters.graphstore import connect, open_db
from kge.pipeline.tagger import DEPENDENT_EVIDENCE, RuleTagger, derive_readiness
from tests import before
from tests.scratch import BUILD, graph_of, load_artifact, profile_of, scratch_build, script, write_registry

DEMO = BUILD.parent
CLIENT = "meridian"
LANDING = BUILD / "landing"
ABOUT = {"policy": "test-policy", "version": "1.0", "as_of": "2025-07-01", "counts": {}}
SIGNED = {"EM-003#0", "EM-005#0", "EM-009#0"}


def run(path: str, *args, build: Path = BUILD) -> subprocess.CompletedProcess:
    """A demo script, run for build's profile (tests/scratch.py)."""
    return script(path, *args, build=build)


class Store:
    """A graph opened directly, as runtrace.fingerprint reads one: a connection per read, and no write stamp."""

    def __init__(self, path: Path):
        self.db = open_db(path)
        self.writes = None

    def reader(self):
        return connect(self.db)

    def rows(self, cypher: str, params: dict | None = None) -> list[list]:
        with self.reader() as conn:
            return conn.execute(cypher, params or {}).get_all()

    def close(self) -> None:
        self.db.close()


def stage(zone: Zone, files: dict[str, str]) -> Path:
    s = zone.stage()
    for rel, text in files.items():
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        (s / rel).write_text(text)
    return s


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """root and every path under it, links included, by inode and modification time: what a write would change."""
    return {p.relative_to(root).as_posix(): (os.lstat(p).st_ino, os.lstat(p).st_mtime_ns) for p in [root, *sorted(root.rglob("*"))]}


def scratch_landing(build: Path, edit=None):
    """A scratch build's landing as a folder of its own, holding the demo's current release, edited in staging by edit."""
    (build / "landing").unlink()
    src = Zone(LANDING, CLIENT).open()
    zone = Zone(build / "landing", CLIENT)
    s = zone.stage()
    for rel in src.manifest["files"]:
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src.path / rel, s / rel)
    if edit:
        edit(s)
    return zone.land(s, {k: v for k, v in src.manifest.items() if k not in ("client", "release_id", "files")})


def edit_chunks(s: Path, change) -> None:
    path = s / "documents" / "chunks.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(change(r)) + "\n" for r in rows))


@pytest.fixture(scope="module")
def release():
    return Zone(LANDING, CLIENT).open()


@pytest.fixture(scope="module")
def raw() -> dict[str, dict]:
    """Each document as the estate holds it, raw: its metadata, its text, and its chunks cut as certification cuts them."""
    docs = json.loads((BUILD / "documents.json").read_text())
    return {d["doc_id"]: {**d, "text": (BUILD / d["path"]).read_text(), "chunks": certify.chunks(BUILD, d)} for d in docs}


MAILBOXES = certify.mailboxes(DEMO / "clients" / CLIENT / "pack.yaml")


def personal(raw: dict) -> set[tuple[str, str]]:
    """Every detector match on the raw shelves that is personal data, as (value, matched). A role mailbox is not: it stays."""
    out = set()
    for d in raw.values():
        for rx, value in privacy.DETECTORS:
            out |= {(value, m.group(0)) for m in rx.finditer(d["text"]) if privacy.redact(value, m.group(0), MAILBOXES) != m.group(0)}
    return out


def labelled(raw: dict) -> dict[str, dict[str, int]]:
    """Each customer name a document labels as one, and how often each document labels it."""
    out: dict[str, dict[str, int]] = {}
    for doc, d in raw.items():
        for f in privacy.mask(d["text"], certify.NAMES)[1]:
            name = d["text"][f["start"]:f["end"]]
            out.setdefault(name, {}).setdefault(doc, 0)
            out[name][doc] += 1
    return out


@pytest.fixture(scope="module")
def masking(raw) -> tuple:
    """What certification masks the demo's estate with: the five detectors, and every labelled name wherever it stands."""
    return certify.detectors(certify.labelled_names(d["text"] for d in raw.values()))


def landed_files(release) -> dict[str, str]:
    return {p.relative_to(release.path).as_posix(): p.read_text() for p in sorted(release.path.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def landed_only(tmp_path_factory, compiled):
    """A build with the estate moved away (no warehouses, raw documents, document index, certification store or the
    curated files the estate writes beside them): the landing only. make graph, the compiled pack loaded, then make
    pipeline, on it. Its registry is the demo's profile with the build moved here, and current points at the loaded
    artifact, as make graph's release does."""
    build = tmp_path_factory.mktemp("landed") / "build"
    build.mkdir()
    (build / "landing").symlink_to(LANDING)
    write_registry(build)
    loaded = load_artifact(build, compiled)
    assert loaded.returncode == 0, loaded.stderr
    piped = run("kge/pipeline/run.py", build=build)
    assert piped.returncode == 0, piped.stderr
    return build, json.loads(loaded.stdout), json.loads(piped.stdout)


def test_a_with_the_estate_moved_away_the_graph_and_the_pipeline_still_build(landed_only, release, eng):
    build, counts, stats = landed_only
    assert not any((build / n).exists() for n in ("warehouses", "docs", "documents.json", "certification", "glossary.csv", "units.json"))
    assert counts["release"] == release.id and counts["documents"] == len(release.documents()) and counts["ai_ready"] > 0
    assert stats["documents"] == len(release.documents()) and stats["chunks"] == len(release.chunks())
    g = Store(graph_of(build))
    try:
        fp = runtrace.fingerprint(g)
        node = g.rows("MATCH (r:Release) RETURN r.id, r.client, r.policy, r.as_of")
    finally:
        g.close()
    base = json.loads((DEMO / "clients" / CLIENT / "baselines" / "issue-38.json").read_text())["graph"]
    assert fp["legacy_model"] == runtrace.fingerprint(eng.g)["legacy_model"], "the model layers, the Release aside, are the demo build's"
    g = Store(graph_of(build))
    try:
        assert before.legacy_model(g) == base["model"], "and those of the graph built from the warehouses, the Wealth expansion aside"
    finally:
        g.close()
    assert node == [[release.id, CLIENT, release.manifest["policy"], release.manifest["as_of"]]]
    assert fp["overlay"] == runtrace.fingerprint(eng.g)["overlay"], "the pipeline on the landing alone adds what it adds on the demo's build"


def test_a_each_landed_catalogue_is_its_warehouses_own_and_holds_no_rows(release):
    sources = sorted(p.stem for p in (BUILD / "warehouses").glob("*.duckdb"))
    assert release.sources() == sources
    for s in sources:
        cat = release.structured(s)
        assert cat == certify.catalogue_of(DuckDBEstate(BUILD / "warehouses"), s)
        assert set(cat) == {"source", "tables"} and cat["tables"]
        for t in cat["tables"]:
            assert set(t) == {"name", "columns", "primary_key", "foreign_keys"}
            assert all(set(c) == {"name", "type", "comment", "ordinal"} for c in t["columns"])
            assert all(set(k) == {"columns", "ref_table", "ref_columns"} and len(k["columns"]) == len(k["ref_columns"]) for k in t["foreign_keys"])


def test_a_the_loader_and_the_pipeline_read_no_warehouse_raw_document_or_certification_store():
    for f in ("kge/graph/load.py", "kge/pipeline/run.py"):
        src = (DEMO / f).read_text()
        assert not re.search(r"^\s*(?:import|from)\s+duckdb\b", src, re.M), f
        for name in ('"warehouses"', '"docs"', '"documents.json"'):
            assert name not in src, (f, name)
    assert '"glossary.csv"' not in (DEMO / "kge" / "pipeline" / "run.py").read_text(), "the pipeline's terms are the graph's"
    for f in [*(DEMO / "kge" / "graph").glob("*.py"), *(DEMO / "kge" / "pipeline").glob("*.py")]:
        assert not re.search(r"""/\s*["']certification["']""", f.read_text()), f"{f.name} reads certification's own store"


def test_a_with_no_release_the_loader_stops_naming_make_certify_and_deletes_nothing(tmp_path, compiled):
    build = scratch_build(tmp_path / "build")
    (build / "landing").unlink()
    (build / "landing").mkdir()
    graph = graph_of(build)
    before = os.lstat(graph).st_ino
    r = run("kge/graph/load.py", "--artifact", compiled, build=build)
    assert r.returncode == 2 and "make certify" in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert os.lstat(graph).st_ino == before


def test_a_a_damaged_release_stops_the_loader_naming_the_file(tmp_path, compiled):
    build = scratch_build(tmp_path / "build")
    rel = scratch_landing(build)
    path = rel.path / "certification.json"
    path.write_text(path.read_text().replace('"gold"', '"silver"', 1))
    r = run("kge/graph/load.py", "--artifact", compiled, build=build)
    assert r.returncode == 2 and "certification.json" in r.stderr and "Traceback" not in r.stderr, r.stderr


def test_b_no_detector_match_on_the_raw_shelves_lands_or_reaches_a_chunk_or_a_tag(release, raw, eng):
    found = personal(raw)
    assert {v for v, _ in found} >= {"personal_data:email", "personal_data:date_of_birth", "personal_data:national_id", "payment_card"}
    files = landed_files(release)
    chunks = {r["id"]: r["text"] for r in eng.g.q("MATCH (c:Chunk) RETURN c.id AS id, c.text AS text")}
    evidence = [r["e"] for r in eng.g.q("MATCH (t:Tag) RETURN t.evidence AS e") if r["e"]]
    places = [*files.items(), *chunks.items(), *(("Tag.evidence", e) for e in evidence)]
    for value, match in found:
        hit = [where for where, text in places if match in text]
        assert not hit, f"a raw {value} match is in {hit}"


def test_b_no_labelled_customer_name_lands_anywhere_or_reaches_a_chunk_or_a_tag(release, raw, eng):
    names = labelled(raw)
    assert names and {d for where in names.values() for d in where} == {"EM-006", "XLS-001"}
    elsewhere = {(n, doc) for n, where in names.items() for doc, d in raw.items() if d["text"].count(n) > where.get(doc, 0)}
    assert {doc for _, doc in elsewhere} == {c.split("#")[0] for c in SIGNED}, "a labelled name also signs other emails, raw"
    files = landed_files(release)
    chunks = {r["id"]: r["text"] for r in eng.g.q("MATCH (c:Chunk) RETURN c.id AS id, c.text AS text")}
    evidence = [r["e"] for r in eng.g.q("MATCH (t:Tag) RETURN t.evidence AS e") if r["e"]]
    places = [*files.items(), *chunks.items(), *(("Tag.evidence", e) for e in evidence)]
    for name in names:
        hit = [where for where, text in places if name in text]
        assert not hit, f"the labelled name {name!r} is in {hit}"
    landed = {c["id"]: c["text"] for c in release.chunks()}
    assert all("[name redacted]" in chunks[c] == landed[c] for c in SIGNED)


def test_b_a_staff_name_no_document_labels_stays_in_clear(release, raw):
    """Question 7's default: a staff name in a sign-off or a From line is a business contact, as a role mailbox is, and
    stays in clear, unless a document labels the same name as a customer's."""
    person = r"[A-Z][a-z]+ [A-Z][a-z]+"
    staff = {(doc, m.group(1)) for doc, d in raw.items() if d["kind"] == "email"
             for m in re.finditer(rf"^(?:From: )?({person})$", d["text"], re.M)}
    names = labelled(raw)
    files = landed_files(release)
    kept = {(doc, s) for doc, s in staff if s not in names}
    assert kept and {doc for doc, s in staff if s in names} == {c.split("#")[0] for c in SIGNED}
    for doc, s in kept:
        assert s in files[f"documents/{doc}.md"], (doc, s)


def test_b_a_role_mailbox_lands_in_clear_as_the_queue_shows_it(release, eng):
    assert any("records@example.com" in text for text in landed_files(release).values())
    tag = next(r for r in eng.g.steward_queue() if r["doc"] == "POL-RETENTION" and r["family"] == "privacy")
    assert tag["evidence"] == "records@example.com" and tag["assessment"] == "likely_functional"


def test_b_every_privacy_tag_holds_its_evidence_redacted(eng):
    tags = eng.g.q("MATCH (t:Tag {family: 'privacy'}) RETURN t.value AS v, t.evidence AS e")
    assert tags and all(privacy.redact(t["v"], t["e"], MAILBOXES) == t["e"] for t in tags)


def test_c_every_landed_chunk_is_its_raw_chunk_masked(release, raw, masking):
    landed = release.chunks()
    assert [c["id"] for c in landed] == [f"{doc}#{i}" for doc, d in raw.items() for i in range(len(d["chunks"]))]
    for c in landed:
        text = raw[c["doc_id"]]["chunks"][c["ordinal"]]
        masked, found = privacy.mask(text, masking, MAILBOXES)
        assert c["text"] == masked and c["findings"] == certify.chunk_findings(found), c["id"]
        assert set(c) == {"id", "doc_id", "ordinal", "text", "findings"}
        assert all(set(f) == {"family", "value", "evidence", "confidence"} for f in c["findings"])
        assert privacy.mask(c["text"], masking, MAILBOXES)[0] == c["text"], f"{c['id']}: masking is idempotent"
    files = landed_files(release)
    for doc, d in raw.items():
        assert files[f"documents/{doc}.md"] == privacy.mask(d["text"], masking, MAILBOXES)[0], doc
    assert release.manifest["counts"]["masked_chunks"] == sum(c["text"] != raw[c["doc_id"]]["chunks"][c["ordinal"]] for c in landed)


def test_c_masking_and_redaction_are_idempotent():
    """The card number is assembled at run time, so the source holds no card-shaped run."""
    card = " ".join(["4000"] * 4)
    ident = "1" * 9
    samples = [f"write to jane.doe@example.com or records@example.com, card {card}",
               f"date of birth 12 May 1971, national identity number {ident}",
               "the client was diagnosed with something; another suffers from it",
               "Name Ada Byron, contact a.b@example.com\n\nAda Byron",
               "dispute_ref,customer_name,amount\nD1,Ada Byron,10.00\nD2,Bea Cole,20.00\n\nRegards,\nBea Cole"]
    mask = lambda text: privacy.mask(text, certify.detectors(certify.labelled_names([text])), MAILBOXES)  # noqa: E731
    for text in samples:
        once, found = mask(text)
        assert found and mask(once)[0] == once, text
        for f in found:
            assert privacy.redact(f["value"], f["evidence"], MAILBOXES) == f["evidence"]
    masked = mask(samples[0])[0]
    assert "records@example.com" in masked and "jane.doe" not in masked and card not in masked
    names = mask(samples[3])[0]
    assert "Ada Byron" not in names and names.count("[name redacted]") == 2, "the labelled name goes, and the sign-off with it"
    table = mask(samples[4])[0]
    assert "D1,[name redacted],10.00" in table and table.endswith("Regards,\n[name redacted]") and "customer_name" in table
    health = mask(samples[2])[0]
    assert "[health phrase: diagnosed with]" in health and mask(health)[0] == health


def test_c_a_labelled_name_is_masked_in_every_document_and_a_name_no_document_labels_stays():
    texts = ["dispute_ref,customer_name,amount\nD1,Ada Byron,10.00\n", "Name Bea Cole, by phone.",
             "Regards,\nAda Byron", "From: Bea Cole\nTo: Team", "Regards,\nAda Byronson, for Cy Dunn"]
    names = certify.labelled_names(texts)
    assert names == ["Ada Byron", "Bea Cole"]
    masked = [privacy.mask(t, certify.detectors(names))[0] for t in texts]
    assert masked[2:4] == ["Regards,\n[name redacted]", "From: [name redacted]\nTo: Team"]
    assert masked[4] == texts[4], "a longer name, and a name no document labels, stay"
    assert certify.labelled_names(masked) == [] and privacy.mask("Ada Byron", certify.detectors([]))[0] == "Ada Byron"


def test_c_the_pipeline_takes_the_landed_findings_with_its_own_and_keeps_evidence_redacted():
    t = RuleTagger(["Revenue"])
    landed = [{"family": "privacy", "value": "personal_data:email", "evidence": "j*******@example.com", "confidence": 0.9}]
    got = [p for p in t.propose("Revenue, from j*******@example.com and records@example.com", "certified", "policy", findings=landed)
           if p.family == "privacy"]
    assert [(p.value, p.evidence, p.confidence) for p in got] == [("personal_data:email", "j*******@example.com", 0.9)], "one per value"
    own = [p for p in t.propose("from jane.doe@example.com", "certified", "policy") if p.family == "privacy"]
    assert [p.evidence for p in own] == ["j*******@example.com"], "evidence is kept redacted"
    ready = t.propose("Revenue is booked monthly.", "certified", "policy", findings=[])
    assert derive_readiness(ready, "certified")[0] == "citable"


def test_d_the_same_content_lands_under_the_same_id_and_writes_nothing(tmp_path):
    zone = Zone(tmp_path / "landing", "acme")
    files = {"certification.json": "[]", "structured/a.json": "{}"}
    first = zone.land(stage(zone, files), ABOUT)
    assert first.new and re.fullmatch(r"[0-9a-f]{16}", first.id) and first.id == release_id("acme", first.manifest["files"])
    assert os.readlink(zone.current) == f"releases/{first.id}" and zone.open().id == first.id
    assert first.manifest == {"client": "acme", "release_id": first.id, **ABOUT, "files": first.manifest["files"]}
    before = snapshot(zone.home)
    again = zone.land(stage(zone, files), ABOUT)
    assert again.id == first.id and not again.new
    assert snapshot(zone.home) == before and not list(zone.root.glob(".staging-*")), "nothing new is written, and the staging folder goes"
    before = snapshot(zone.root)
    again = zone.land_files({rel: text.encode() for rel, text in files.items()}, ABOUT)
    assert again.id == first.id and not again.new and snapshot(zone.root) == before, "from memory, nothing is written at all"
    other = zone.land(stage(zone, {**files, "certification.json": "[{}]"}), ABOUT)
    assert other.id != first.id and zone.open().id == other.id and zone.open(first.id).id == first.id


@pytest.fixture(scope="module")
def certified(tmp_path_factory):
    """kge/estate/certify.py as make certify runs it, on the demo's estate (read only), into a build of its own."""
    build = tmp_path_factory.mktemp("certified") / "build"
    build.mkdir()
    for name in ("documents.json", "docs", "warehouses"):
        (build / name).symlink_to(BUILD / name)
    r = run("kge/estate/certify.py", "--build", build, "--client", CLIENT)
    assert r.returncode == 0, r.stderr
    return build, json.loads(r.stdout)


def test_d_the_same_estate_lands_the_same_release_and_a_second_run_writes_nothing(certified, release):
    build, out = certified
    assert out["release"] == release.id and out["new"] is True, "the demo's estate, landed elsewhere: the same id"
    mine = Zone(build / "landing", CLIENT).open()
    assert landed_files(mine) == landed_files(release)
    assert mine.manifest["certification_paths"] == {d["id"]: "stand-in" for d in mine.documents()}
    assert json.loads(mine.text("certification-paths.json")) == mine.manifest["certification_paths"]
    before = {**snapshot(build / "landing"), **{f"certification/{k}": v for k, v in snapshot(build / "certification").items()}}
    r = run("kge/estate/certify.py", "--build", build, "--client", CLIENT)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["release"] == release.id and json.loads(r.stdout)["new"] is False
    after = {**snapshot(build / "landing"), **{f"certification/{k}": v for k, v in snapshot(build / "certification").items()}}
    assert after == before


def test_d_certification_grades_as_before_and_keeps_its_own_run_record(certified, release, raw):
    build, out = certified
    assert release.certification() == certify.certify_estate(BUILD), "graded on the raw text, as before"
    rec = json.loads((build / "certification" / CLIENT / "runs" / f"{out['release']}.json").read_text())
    assert (rec["client"], rec["release_id"], rec["policy"], rec["as_of"]) == (CLIENT, out["release"], certify.POLICY_ID, "2025-07-01")
    assert "documents.json" in rec["inputs"] and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in rec["inputs"].values())
    assert {g["asset_id"]: (g["ai_ready"], g["tier"]) for g in rec["grades"]} == \
           {r["asset_id"]: (r["ai_ready"], r["tier"]) for r in release.certification()}
    assert rec["findings"] and all(set(f) == {"chunk", "kind", "start", "end"} for f in rec["findings"])
    said = json.dumps(rec)
    assert not [v for v, m in personal(raw) if m in said] and not [n for n in labelled(raw) if n in said], "kinds and offsets, never the text"
    assert "personal_data:name" in {f["kind"] for f in rec["findings"]}


def upstream(**change):
    held = {"ai_ready": "yes", "tier": "gold", "policy_id": "m365-ai", "policy_version": "2.1",
            "effective_from": "2025-01-02"}
    held.update(change)
    return held


def upstream_doc(certification) -> dict:
    return {"doc_id": "UP-001", "shelf": "certified", "title": "Upstream document", "kind": "guide",
            "certification": certification}


def test_d_a_complete_upstream_row_is_taken_as_given_and_ai_ready_no_stays_not_ready():
    row = certify.certify(upstream_doc(upstream(tier="silver", effective_to="2025-06-30")), ["Safe text."])
    assert row == {"asset_id": "UP-001", "ai_ready": "yes", "tier": "silver",
                   "evidence": "certified upstream under m365-ai 2.1", "policy_id": "m365-ai", "version": "2.1",
                   "effective_from": "2025-01-02", "effective_to": "2025-06-30"}
    refused = certify.certify(upstream_doc(upstream(ai_ready="no", tier=None, evidence="source policy refused it")), ["Safe text."])
    assert refused["ai_ready"] == "no" and refused["tier"] is None


@pytest.mark.parametrize("held, key", [
    ({"ai_ready": "yes"}, "tier"),
    (upstream(tier="platinum"), "tier"),
    (upstream(ai_ready="yes", tier=None), "tier"),
    (upstream(ai_ready=True), "ai_ready"),
])
def test_d_a_partial_or_invalid_upstream_block_is_ignored_and_graded(held, key):
    doc = upstream_doc(held)
    row = certify.certify(doc, ["Safe text."])
    assert row["policy_id"] == certify.POLICY_ID and row["tier"] == "bronze"
    assert certify.upstream_certification(doc, ["Safe text."])[1].startswith(key)
    assert certify.certification_path(doc, ["Safe text."]) == {
        "path": "stand-in", "ignored": certify.upstream_certification(doc, ["Safe text."])[1]}


def test_d_upstream_gold_cannot_turn_an_uncertified_shelf_into_a_certified_one():
    doc = {**upstream_doc(upstream()), "shelf": "uncertified"}
    row = certify.certify(doc, ["Safe text."])
    assert row["policy_id"] == certify.POLICY_ID and row["tier"] == "bronze"


def test_d_upstream_certification_does_not_bypass_the_privacy_gate():
    row = certify.certify(upstream_doc(upstream()), ["Customer email jane@example.com"])
    assert row["policy_id"] == certify.POLICY_ID and row["ai_ready"] == "no" and row["tier"] is None


def test_d_a_new_as_of_lands_a_new_release_and_swaps_current(tmp_path, release):
    build = tmp_path / "build"
    build.mkdir()
    for name in ("documents.json", "docs", "warehouses"):
        (build / name).symlink_to(BUILD / name)
    landing = tmp_path / "elsewhere"
    ids = []
    for as_of in ("2025-07-01", "2026-07-01"):
        r = run("kge/estate/certify.py", "--build", build, "--client", CLIENT, "--landing", landing, "--as-of", as_of)
        assert r.returncode == 0, r.stderr
        ids.append(json.loads(r.stdout)["release"])
    zone = Zone(landing, CLIENT)
    assert ids[0] == release.id and ids[1] != ids[0]
    assert zone.open().id == ids[1] and zone.open(ids[0]).id == ids[0], "current swapped; the old release stays"
    assert zone.open().manifest["as_of"] == "2026-07-01"


def test_d_a_tampered_release_is_refused(tmp_path):
    zone = Zone(tmp_path / "landing", "acme")
    rel = zone.land(stage(zone, {"certification.json": "[]", "documents/chunks.jsonl": ""}), ABOUT)
    path = rel.path / "certification.json"
    path.write_text('[{"asset_id": "X", "ai_ready": "yes", "tier": "gold"}]')
    with pytest.raises(LandingError, match="certification.json"):
        zone.open()
    path.write_text("[]")
    zone.open()
    (rel.path / "extra.json").write_text("{}")
    with pytest.raises(LandingError, match="extra.json"):
        zone.open()
    (rel.path / "extra.json").unlink()
    (rel.path / "documents" / "chunks.jsonl").unlink()
    with pytest.raises(LandingError, match="chunks.jsonl"):
        zone.open()
    (rel.path / "documents" / "chunks.jsonl").write_text("x")
    manifest = json.loads((rel.path / "manifest.json").read_text())
    manifest["files"]["documents/chunks.jsonl"] = __import__("hashlib").sha256(b"x").hexdigest()
    (rel.path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(LandingError, match="release id"):
        zone.open()


def test_d_an_id_or_a_path_outside_the_clients_releases_is_refused(tmp_path):
    root = tmp_path / "landing"
    acme, other = Zone(root, "acme"), Zone(root, "other")
    with pytest.raises(LandingError, match="nothing has landed"):
        acme.open()
    theirs = other.land(stage(other, {"certification.json": "[]"}), ABOUT)
    for bad in (f"../other/releases/{theirs.id}", f"{theirs.id}/..", "/etc", str(theirs.path), "0123", theirs.id.upper()):
        with pytest.raises(LandingError):
            acme.open(bad)
    with pytest.raises(LandingError, match="no release"):
        acme.open(theirs.id)
    acme.releases.mkdir(parents=True)
    os.symlink(f"../../other/releases/{theirs.id}", acme.current)
    with pytest.raises(LandingError, match="outside"):
        acme.open()
    acme.current.unlink()
    os.symlink(theirs.path, acme.releases / theirs.id)
    with pytest.raises(LandingError):
        acme.open(theirs.id)
    (acme.releases / theirs.id).unlink()
    files = {"../../../other/releases/x.json": "0" * 64}
    rid = release_id("acme", files)
    (acme.releases / rid).mkdir()
    (acme.releases / rid / "manifest.json").write_text(json.dumps({"client": "acme", "release_id": rid, **ABOUT, "files": files}))
    with pytest.raises(LandingError, match="outside"):
        acme.open(rid)
    for client in ("Acme", "../acme", "", "a/b"):
        with pytest.raises(LandingError):
            Zone(root, client)
    with pytest.raises(LandingError):
        other.open().structured("../certification")
    s = acme.stage()
    os.symlink(theirs.path / "certification.json", s / "certification.json")
    with pytest.raises(LandingError, match="link"):
        acme.land(s, ABOUT)
    assert not s.exists(), "a staging folder that cannot land is removed"


def test_e_a_confirmed_reject_of_planted_personal_data_embeds_the_masked_text(tmp_path, raw):
    from kge.services.ask.engine.core import Engine
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        tags = [r for r in e.g.steward_queue() if r["doc"] == "POL-COMPLAINTS" and r["family"] == "privacy"]
        assert tags and all(t["assessment"] == "likely_personal" and t["second_check_required_to_reject"] for t in tags)
        for t in tags:
            assert e.g.steward_decide(t["tag"], "steward", "reject")["status"] == "pending_second_check"
            e.g.steward_decide(t["tag"], "steward_glossary", "reject")
        row = e.g.q("MATCH (c:Chunk {id: $c}) RETURN c.text AS text, c.embedding AS v, c.readiness AS r", {"c": tags[0]["chunk"]})[0]
        assert row["r"] == "citable" and row["v"] is not None
        assert np.allclose(row["v"], e.g.embedder.embed(row["text"]), atol=1e-6), "the vector is the landed, masked text's"
        assert not [v for v, m in personal(raw) if m in row["text"]] and "date of birth [redacted]" in row["text"]
    finally:
        e.close()


def test_f_masking_changes_chunks_the_gate_prohibited_and_the_signed_emails_and_no_other_vector(eng, release, raw):
    """Masking changes the chunks the gate already prohibited, which have no vector, and, because a labelled customer
    name is masked wherever it stands, the three emails a staff member signs with such a name (SIGNED). Those three
    change by the name alone, keep their readiness and embed their masked text. Every other vector is the raw text's,
    as the graph built from the warehouses has it."""
    tagger = RuleTagger(sorted(eng.g.terms()))
    embedder = make_embedder()
    names = labelled(raw)
    rows = {r["id"]: r for r in eng.g.q("MATCH (c:Chunk) RETURN c.id AS id, c.readiness AS readiness, c.embedding AS v")}
    changed, allowed = set(), set()
    for c in release.chunks():
        d = raw[c["doc_id"]]
        text = d["chunks"][c["ordinal"]]
        before = derive_readiness(tagger.propose(text, d["shelf"], d["kind"]), d["shelf"])[0]
        got = rows[c["id"]]
        assert got["readiness"] == before, f"{c['id']}: the gate decides as it did on the raw text"
        if c["text"] != text:
            changed.add(c["id"])
        if before == "embed-prohibited":
            assert got["v"] is None, c["id"]
            continue
        if c["text"] != text:
            allowed.add(c["id"])
            only_names = re.sub("|".join(map(re.escape, sorted(names, key=len, reverse=True))), "[name redacted]", text)
            assert c["text"] == only_names, f"{c['id']}: changed by a labelled name alone"
            assert np.allclose(got["v"], embedder.embed(c["text"]), atol=1e-6), f"{c['id']}: the masked text's vector"
        else:
            assert np.allclose(got["v"], embedder.embed(text), atol=1e-6), f"{c['id']}: the raw text's vector, as the warehouse-built graph has it"
    assert allowed == SIGNED, "masking changed a chunk the gate allows, other than the signed emails"
    assert changed - SIGNED == {"POL-COMPLAINTS#0", "EM-006#0", "XLS-001#0"}


def test_a_chunk_that_landed_without_its_findings_fails_closed(tmp_path, compiled):
    build = scratch_build(tmp_path / "build")
    scratch_landing(build, lambda s: edit_chunks(s, lambda r: {k: v for k, v in r.items() if k != "findings"} if r["id"] == "MD-REVENUE#0" else r))
    for path, more in (("kge/graph/load.py", ["--artifact", compiled]), ("kge/pipeline/run.py", [])):
        r = run(path, *more, build=build)
        assert r.returncode == 0, r.stderr
    g = Store(graph_of(build))
    try:
        chunk = g.rows("MATCH (c:Chunk {id: 'MD-REVENUE#0'}) RETURN c.readiness, c.embedding IS NULL")
        restricted = g.rows("MATCH (c:Chunk {id: 'MD-REVENUE#0'})-[:TAGGED]->(t:Tag {family: 'classification', value: 'Restricted'}) "
                            "RETURN t.evidence")
        others = g.rows("MATCH (c:Chunk {id: 'MD-REVENUE#1'}) RETURN c.readiness")
    finally:
        g.close()
    assert chunk == [["embed-prohibited", True]] and len(restricted) == 1 and restricted[0][0] != DEPENDENT_EVIDENCE
    assert others == [["citable"]]


def test_the_pipeline_reads_the_release_the_graph_loaded_not_current(tmp_path, compiled):
    build = scratch_build(tmp_path / "build")
    first = scratch_landing(build)
    assert load_artifact(build, compiled).returncode == 0
    zone = Zone(build / "landing", CLIENT)
    s = zone.stage()
    for rel in first.manifest["files"]:
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(first.path / rel, s / rel)
    edit_chunks(s, lambda r: {**r, "text": r["text"] + " (a later release)"} if r["id"] == "WIKI-004#0" else r)
    later = zone.land(s, {k: v for k, v in first.manifest.items() if k not in ("client", "release_id", "files")})
    assert later.id != first.id and zone.open().id == later.id
    r = run("kge/pipeline/run.py", build=build)
    assert r.returncode == 0, r.stderr
    g = Store(graph_of(build))
    try:
        text = g.rows("MATCH (c:Chunk {id: 'WIKI-004#0'}) RETURN c.text")[0][0]
        node = g.rows("MATCH (r:Release) RETURN r.id")
    finally:
        g.close()
    assert node == [[first.id]] and "(a later release)" not in text


def test_the_ab_harness_reads_the_tiers_of_the_release_the_graph_loaded(eng, release):
    from kge.eval import harness, metrics
    about = harness.Arms(eng, llm=None).about()
    assert about["release"] == eng.g.release() == release.about() == {"id": release.id, "client": CLIENT, "policy": certify.POLICY_ID,
                                                                       "as_of": release.manifest["as_of"]}
    assert metrics.load_landing(LANDING, about["release"]) == {r["asset_id"]: r for r in release.certification()}
    assert metrics.load_landing(LANDING, None) == {}


def test_the_release_is_in_the_model_hash_and_out_of_the_legacy_digest(tmp_path):
    """The release is the first layer past the legacy digest, and since #39 item 7d the curated types the runtime
    answers from follow it (tests/integration/test_cross_unit.py)."""
    from kge.services.ask.engine.core import Engine
    assert [n for n, _ in runtrace.MODEL_LAYERS if n not in runtrace.LEGACY_LAYERS][0] == "release"
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        before = runtrace.fingerprint(e.g)
        e.g.q("MATCH (r:Release) SET r.as_of = '2099-01-01'")
        after = runtrace.fingerprint(e.g)
    finally:
        e.close()
    assert after["model"] != before["model"] and (after["legacy_model"], after["overlay"]) == (before["legacy_model"], before["overlay"])


def test_privacy_and_chunking_are_shared_modules():
    import kge.services.ask.engine.core
    import kge.pipeline.run
    assert kge.services.ask.engine.core.redact is privacy.redact and kge.services.ask.engine.core.assess_privacy is privacy.assess_privacy
    assert kge.pipeline.run.chunk_text is chunk_text and kge.pipeline.run.strip_header is strip_header
    core, tagger = (DEMO / "kge" / "services" / "ask" / "engine" / "core.py").read_text(), (DEMO / "kge" / "pipeline" / "tagger.py").read_text()
    assert not re.search(r"^(ROLE_MAILBOXES =|def assess_privacy|def redact)", core, re.M)
    assert not re.search(r"^(EMAIL_RE|DOB_RE|NATIONAL_ID_RE|CARD_RE|HEALTH_RE) =", tagger, re.M)
    assert [v for _, v in privacy.DETECTORS] == ["personal_data:email", "personal_data:date_of_birth", "personal_data:national_id",
                                                 "payment_card", "special_category:health"]


def test_the_makefile_lands_for_the_profile_s_client_and_the_graph_waits_for_the_landing():
    make = (DEMO / "Makefile").read_text()
    assert not re.search(r"^CLIENT \?=", make, re.M) and re.search(r"^LANDED := \$\(call profile,landed\)$", make, re.M)
    import inspect

    from kge import cli
    from tests.unit.test_cli import planned
    assert inspect.getsource(cli.Commands.graph).split("self.ensure_landed()", 1)[1].count('"kge.compiler.lock", "--check"') == 1
    for target in ("estate", "certify"):
        assert make.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0].split() == ["$(KGE)", target, "$(KGE_ARGS)"], target
        steps = planned(target).splitlines()
        assert steps[-1].endswith(" -m kge.estate.certify") and not any("--client" in step for step in steps), (target, steps)


def test_the_readme_says_where_certification_lands_and_names_this_test():
    """Certification's store is in the profile's estate (build/ for Meridian, build/thornbury/ for the Thornbury
    fixture), and the README names each profile's, never build/certification/<client>/."""
    readme = (DEMO / "README.md").read_text()
    for needle in ("tests/integration/test_landing.py", "build/landing/<client>/releases/", "`<estate>/certification/<client>/runs/", "kge/landing/zone.py",
                   "kge/pipeline/privacy.py", "make certify", "records@example.com", *(c.split("#")[0] for c in sorted(SIGNED))):
        assert needle in readme, needle
    assert "one release per run" not in readme, "the same certified content lands once"
    from kge.pipeline import profile
    for p in profile.read(profile.REGISTRY)[1].values():
        runs = (p.estate() / certify.RUNS / p.client / "runs").relative_to(DEMO).as_posix()
        assert f"`{runs}/" in readme, runs
    assert "build/certification/<client>/" not in readme


def test_the_readme_shows_the_landed_release_and_its_masking_in_a_step_anyone_can_run():
    """The owner's bottom line: each capability is showable, not only testable."""
    readme = (DEMO / "README.md").read_text()
    section = readme.split("### The landing zone", 1)[1].split("\n## ", 1)[0]
    step = section[section.index("To see it"):]
    step = step[:step.index("holds all of this")]
    for needle in ("`make certify`", '`"new": false`', "`masked_chunks`", "header names", "`ls build/landing/meridian/current/`",
                   "build/docs", "build/landing/meridian/current/documents", "`m********@example.com`", "`records@example.com` in clear"):
        assert needle in step, needle
    assert not re.search(r"\b[0-9a-f]{16}\b", step), "the step prints the release id at run time and names none"
    script = readme.split("## Demo script", 1)[1].split("\n## ", 1)[0]
    assert "the landed release the graph was loaded from" in script and "(#the-landing-zone)" in script
