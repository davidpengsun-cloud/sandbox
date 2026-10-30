"""The runtime overlay: what stewards decide is journaled in the client's graph store, and outlives the graph it was made on.

    .venv/bin/python -m pytest tests/integration/test_overlay.py -q

Every load builds the graph from scratch (kge/graph/load.py), from the compiled pack
and the landed release. What a steward decides on top of it is the runtime
overlay: each decision is appended, once its graph writes commit, to an
append-only journal in the client's graph store,
build/stores/<client>/overlay/decisions.jsonl (kge/pipeline/overlay.py), keyed by
stable ids: the human tag the decision wrote (or the reject request), a
snapshot of the machine tag it decided, the chunk and the sha256 of its masked
text, and for an accepted candidate term the term and its defining document.
Chunk text, vectors, certification and access are not in it: each comes back
from the landing, the pack or the pipeline. kge/graph/load.py replays the terms
once the documents have loaded, and kge/pipeline/run.py the decisions at its end,
so a recompiled pack or a reload keeps every decision under its
id, and the overlay hash with them. A record whose document, chunk, term or
match is gone, or whose chunk's text changed, is an orphan: never attached,
and reported in orphans.json, on stderr and in pipeline_stats.json. A journal
holding another client's records is refused, an append that fails is a 500
that says so, and make reset-overlay sets the journal aside, for a fresh queue
and the 183 as before.

The walkthrough here is the README's: accept the Digital Engagement Score,
reject the functional mailbox's privacy tag, leave a reject of planted personal
data waiting for a second steward; and, since #39 item 7d, decide a
cross-reference match. Then make pipeline, as the README has it, and the graph
that gives is what every later load and pipeline must give back.

The walkthrough starts from a graph of the module's own, which no steward has
touched: the pack as committed, compiled and loaded into a fresh store with no
journal, then make pipeline, as make reset-overlay graph pipeline leaves the
demo's. Only the demo's landing and flat files are read, never its graph or its
overlay, so the module runs whatever the demo's build holds: the decisions a
walkthrough left in it, or a journal make reset-overlay set aside.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.core import Engine
from kge.landing.zone import Zone
from kge.pipeline import overlay
from kge.adapters.graphstore import connect, open_db
from tests.scratch import BUILD, CLIENT, compile_pack, graph_of, load_artifact, profile_of, run_of, scratch_build, script

DEMO = BUILD.parent
TERM = "Digital Engagement Score"
DEFINITION = "logins per active customer per month, capped at 30"
ALIAS = "    - {phrase: takings, term: revenue}\n"
SNAPSHOT = DEMO / "clients" / CLIENT / "baselines" / "parity-4d9d5c9.json"
STATED = DEMO / "clients" / CLIENT / "baselines" / "parity-stated.json"


def journal_of(build: Path) -> Path:
    return profile_of(build).overlay_dir() / "decisions.jsonl"


def records(build: Path) -> list[dict]:
    return [json.loads(line) for line in journal_of(build).read_text().splitlines()]


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


def held(build) -> dict:
    """What a graph holds of the walkthrough, as JSON: its hashes, every human decision (its id, what it supersedes and
    the chunk it is on), the reject requests, the accepted term and what hangs on it, and the readiness of the chunk whose
    privacy tag was rejected."""
    g = Store(graph_of(Path(build)))
    try:
        fp = runtrace.fingerprint(g)
        out = {k: fp[k] for k in ("model", "overlay", "artifact")}
        out["humans"] = g.rows("MATCH (h:Tag)-[:SUPERSEDES]->(o:Tag) WHERE h.generator = 'human' OPTIONAL MATCH (c:Chunk)-[:TAGGED]->(h) "
                               "RETURN h.id, h.family, h.value, h.model, o.id, c.id ORDER BY h.id")
        out["requests"] = g.rows("MATCH (c:Chunk)-[:TAGGED]->(r:Tag) WHERE r.family = 'privacy_review' RETURN r.id, r.value, r.model, c.id "
                                 "ORDER BY r.id")
        out["term"] = g.rows("MATCH (t:Term) WHERE t.name = $n RETURN t.name, t.domain, t.steward, t.definition", {"n": TERM})
        out["defines"] = g.rows("MATCH (d:Document)-[:DEFINES]->(t:Term) WHERE t.name = $n RETURN d.id ORDER BY d.id", {"n": TERM})
        out["writers"] = g.rows("MATCH (p:Principal)-[:CAN_WRITE]->(t:Term) WHERE t.name = $n RETURN p.id ORDER BY p.id", {"n": TERM})
        out["retention"] = g.rows("MATCH (c:Chunk) WHERE c.id = 'POL-RETENTION#0' RETURN c.readiness, c.embedding IS NOT NULL")
        out["chunks"] = g.rows("MATCH (c:Chunk) RETURN count(c)")[0][0]
    finally:
        g.close()
    return out


def pack_copy(clients: Path) -> Path:
    """The client's pack folder copied into clients, the clients root returned: as committed, with the README's pack edit
    taken out if a walkthrough left it in, so the module's own pack edits start from the pack the walkthrough's graph has."""
    shutil.copytree(DEMO / "clients" / CLIENT, clients / CLIENT)
    pack = clients / CLIENT / "pack.yaml"
    pack.write_text(pack.read_text().replace(ALIAS, ""))
    return clients


def untouched(root: Path) -> SimpleNamespace:
    """A scratch build under root that no steward has touched, whatever the demo's build holds: the pack compiled
    (pack_copy), loaded into a fresh store, with no journal, then make pipeline. Neither the demo's graph nor its graph
    store (a journal, an archive) is copied."""
    art = compile_pack(root / "artifacts", pack_copy(root / "clients"))
    build = scratch_build(root / "build", graph=False)
    assert not (build / "stores").exists(), "a fresh store: no journal and no archive"
    loaded = load_artifact(build, art)
    assert loaded.returncode == 0, loaded.stderr
    piped = script("kge/pipeline/run.py", build=build)
    assert piped.returncode == 0, piped.stderr
    return SimpleNamespace(build=build, artifact=art, held=held(build))


def walk(build: Path) -> dict[str, str]:
    """The README's walkthrough on build's graph, as the API makes it: the machine tag each decision is on, by name."""
    e = Engine(profile_of(build))
    try:
        queue = e.g.steward_queue("steward")
        on = {"term": next(r for r in queue if r["family"] == "candidate_term" and r["value"] == TERM),
              "retention": next(r for r in queue if r["doc"] == "POL-RETENTION" and r["family"] == "privacy"),
              "complaints": next(r for r in queue if r["doc"] == "POL-COMPLAINTS" and r["value"] == "personal_data:email"),
              "match": next(r for r in queue if r["family"] == "xref_match")}
        assert e.g.steward_decide(on["term"]["tag"], "steward_glossary", "accept", definition=DEFINITION)["term_created"] == TERM
        assert e.g.steward_decide(on["retention"]["tag"], "steward_glossary", "reject")["readiness"] == "citable"
        assert e.g.steward_decide(on["complaints"]["tag"], "steward", "reject")["status"] == "pending_second_check"
        assert e.g.steward_decide(on["match"]["tag"], "steward", "accept")["written_back"] is False
    finally:
        e.close()
    return {k: v["tag"] for k, v in on.items()}


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    """The module's graph before the walkthrough: one no steward has touched (untouched)."""
    return untouched(tmp_path_factory.mktemp("fresh"))


@pytest.fixture(scope="module")
def walked(fresh, tmp_path_factory):
    """A copy of fresh after the walkthrough and then make pipeline: the state every later load must give back, from the
    artifact it was loaded from."""
    build = copy_of(fresh, tmp_path_factory.mktemp("walked") / "build")
    on = walk(build)
    piped = script("kge/pipeline/run.py", build=build)
    assert piped.returncode == 0, piped.stderr
    return SimpleNamespace(build=build, artifact=fresh.artifact, on=on, before=held(build), journal=journal_of(build).read_bytes(),
                           stats=json.loads(piped.stdout))


def copy_of(state, dest: Path) -> Path:
    """state's build again (fresh or walked), its graph and graph store copied and everything else linked as it was: a test
    decides on it or reloads it."""
    shutil.copytree(state.build, dest, symlinks=True)
    return dest


def reload(build: Path, artifact: Path, env: dict | None = None) -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess]:
    """make graph (the artifact loaded, and current pointed at it) then make pipeline, on build; each must succeed."""
    loaded = load_artifact(build, artifact, env=env)
    assert loaded.returncode == 0, loaded.stderr
    piped = script("kge/pipeline/run.py", build=build, env=env)
    assert piped.returncode == 0, piped.stderr
    return loaded, piped


def same_decisions(now: dict, then: dict) -> None:
    for k in ("humans", "requests", "term", "defines", "writers", "retention", "chunks"):
        assert now[k] == then[k], k


def test_each_decision_is_journaled_once_its_graph_writes_commit(walked):
    recs = [json.loads(line) for line in walked.journal.decode().splitlines()]
    assert [(r["decision"], r["machine"]["id"]) for r in recs] == [("accept", walked.on["term"]), ("reject", walked.on["retention"]),
                                                                     ("reject_requested", walked.on["complaints"]),
                                                                     ("accept", walked.on["match"])]
    artifact = walked.before["artifact"]
    assert all(r["client"] == CLIENT and r["model_hash"] == artifact and r["at"] for r in recs)
    assert [r["who"] for r in recs] == ["steward_glossary", "steward_glossary", "steward", "steward"]
    humans = {h[0]: h for h in walked.before["humans"]}
    term, retention, request, match = recs
    for r in (term, retention, match):
        assert re.fullmatch(re.escape(r["machine"]["id"]) + r"/h[0-9a-f]{12}", r["tag"]["id"]), "the human tag: {tag}/h<uuid>"
        assert humans[r["tag"]["id"]][4] == r["machine"]["id"], "the tag it wrote supersedes the machine tag it decided"
        assert r["tag"]["generator"] == "human" and r["tag"]["confidence"] == 1.0 and r["tag"]["model"] == r["who"]
    assert request["tag"]["id"] == f"{walked.on['complaints']}/reject-request" and request["tag"]["value"] == "reject_requested"
    assert [r[0] for r in walked.before["requests"]] == [request["tag"]["id"]]
    g = Store(graph_of(walked.build))
    try:
        for r in (term, retention, request):
            t = g.rows("MATCH (t:Tag) WHERE t.id = $id RETURN t.family, t.value, t.generator, t.confidence, t.model, t.prompt_version",
                       {"id": r["machine"]["id"]})[0]
            assert [r["machine"][k] for k in ("family", "value", "generator", "confidence", "model", "prompt_version")] == t
            text = g.rows("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.id = $c RETURN d.id, c.text", {"c": r["chunk"]["id"]})[0]
            assert r["chunk"] == {"id": r["machine"]["id"].split("/")[0], "doc": text[0], "text_sha256": overlay.text_sha256(text[1])}
            assert text[1] not in walked.journal.decode(), "never the chunk's text"
    finally:
        g.close()
    assert term["term"] == {"name": TERM, "domain": "Customer", "steward": "steward_glossary", "definition": DEFINITION,
                            "defined_by": "EM-004"}
    assert all(r["term"] is None for r in (retention, request, match))
    assert match["chunk"] is None and match["machine"]["family"] == "xref_match"
    flat = walked.journal.decode()
    for left_out in ('"text"', "embedding", "ai_ready", "tier", "cert_", "CAN_WRITE", "readiness"):
        assert left_out not in flat, left_out


def test_make_pipeline_on_the_graph_the_decisions_were_made_on_finds_each_one_there(walked):
    stats = walked.stats["overlay"]
    assert stats["journal"] == str(journal_of(walked.build))
    assert (stats["records"], stats["replayed"], stats["present"], stats["orphans"]) == (4, 0, 4, [])
    assert journal_of(walked.build).read_bytes() == walked.journal, "replay never appends"
    assert walked.before["term"] == [[TERM, "Customer", "steward_glossary", DEFINITION]] and walked.before["defines"] == [["EM-004"]]
    assert walked.before["retention"] == [["citable", True]] and len(walked.before["humans"]) == 3


def test_the_journal_appends_whole_lines_under_a_lock_and_syncs_each(tmp_path, monkeypatch):
    j = overlay.Journal(tmp_path, "acme")
    synced, real = [], os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])

    def write(n: int) -> None:
        for i in range(25):
            j.append({"client": "acme", "n": n, "i": i, "pad": "x" * 2000})
    threads = [threading.Thread(target=write, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = j.records()
    assert sorted((r["n"], r["i"]) for r in got) == [(n, i) for n in range(16) for i in range(25)], "every line whole, none lost"
    assert len(synced) >= 400, "each append is synced to disk before it returns"
    assert j.path == tmp_path / "decisions.jsonl"
    with pytest.raises(overlay.OverlayError, match="other"):
        j.append({"client": "other", "n": 0})


def test_a_recompiled_pack_with_an_alias_keeps_every_decision_under_its_id_and_the_overlay_hash(walked, tmp_path):
    """The acceptance run: the walkthrough, then an alias added to the pack and make graph pipeline."""
    clients = pack_copy(tmp_path / "clients")
    pack = clients / CLIENT / "pack.yaml"
    line = "    - {phrase: revenue, term: revenue}\n"
    assert pack.read_text().count(line) == 1
    pack.write_text(pack.read_text().replace(line, line + ALIAS))
    art = compile_pack(tmp_path / "artifacts", clients)
    assert art.name != walked.before["artifact"], "a new artifact: the pack changed"
    build = copy_of(walked, tmp_path / "build")
    loaded, piped = reload(build, art)
    after = held(build)
    same_decisions(after, walked.before)
    assert after["overlay"] == walked.before["overlay"], "the overlay hash is unchanged"
    assert after["artifact"] == art.name and after["model"] != walked.before["model"], "the model moved by the alias alone"
    assert "1 accepted term" in loaded.stderr and "WARNING" not in loaded.stderr, loaded.stderr
    stats = json.loads(piped.stdout)["overlay"]
    assert (stats["records"], stats["replayed"], stats["present"], stats["orphans"]) == (4, 4, 0, [])
    assert json.loads((journal_of(build).parent / "orphans.json").read_text()) == {"kge/graph/load.py": [], "kge/pipeline/run.py": []}
    assert journal_of(build).read_bytes() == walked.journal, "replay never appends"
    e = Engine(profile_of(build))
    try:
        queue = {r["tag"]: r for r in e.g.steward_queue("steward")}
        assert not {walked.on[k] for k in ("term", "retention", "match")} & set(queue), "decided items do not come back"
        assert queue[walked.on["complaints"]]["reject_requested_by"] == "steward", "the request still waits for a second steward"
        said = e.answer(f"What was the {TERM} for June 2025?")
        assert said["behaviour"] == "abstain" and "no certified implementation" in said["reason"], "the accepted term is a term"
        assert e.answer("Total retail takings for June 2025")["figure"] == 13287.67, "and the pack edit took"
    finally:
        e.close()


def test_a_term_the_pack_has_since_taken_in_keeps_the_pack_s_definition_and_gains_the_edge(walked, tmp_path):
    clients = pack_copy(tmp_path / "clients")
    pack = clients / CLIENT / "pack.yaml"
    text = pack.read_text()
    candidate = "    - {phrase: digital engagement score, candidate: Digital Engagement Score}\n"
    assert text.count("glossary:\n") == 1 and text.count(candidate) == 1
    entry = ("glossary:\n  - id: digital-engagement-score\n    name: Digital Engagement Score\n    domain: Customer\n"
             "    steward: Customer Data Steward\n    status: certified\n    definition: The pack's own definition.\n    defined_by: []\n")
    pack.write_text(text.replace("glossary:\n", entry).replace(candidate, ""))
    build = copy_of(walked, tmp_path / "build")
    loaded, piped = reload(build, compile_pack(tmp_path / "artifacts", clients))
    after = held(build)
    assert after["term"] == [[TERM, "Customer", "Customer Data Steward", "The pack's own definition."]], "the pack's definition wins"
    assert after["defines"] == [["EM-004"]], "and the steward's defining document is attached"
    assert after["humans"] == walked.before["humans"] and json.loads(piped.stdout)["overlay"]["orphans"] == []


def test_replay_is_idempotent(walked, tmp_path):
    build = copy_of(walked, tmp_path / "build")
    _, piped = reload(build, walked.artifact)
    first = held(build)
    assert json.loads(piped.stdout)["overlay"]["replayed"] == 4
    again = script("kge/pipeline/run.py", build=build)
    assert again.returncode == 0, again.stderr
    stats = json.loads(again.stdout)["overlay"]
    assert (stats["replayed"], stats["present"], stats["orphans"]) == (0, 4, [])
    assert held(build) == first, "a second pipeline changes nothing"
    reload(build, walked.artifact)
    assert held(build) == first == walked.before, "nor a second load: the graph the decisions were made on, and make pipeline"
    assert len({h[0] for h in first["humans"]}) == len(first["humans"]) == 3, "each decision once"
    assert journal_of(build).read_bytes() == walked.journal


def land_edited(build: Path, edit) -> None:
    """build's landing as a folder of its own: the demo's current release, edited in staging by edit, landed as current."""
    (build / "landing").unlink()
    src = Zone(BUILD / "landing", CLIENT).open()
    zone = Zone(build / "landing", CLIENT)
    s = zone.stage()
    for rel in src.manifest["files"]:
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src.path / rel, s / rel)
    edit(s)
    zone.land(s, {k: v for k, v in src.manifest.items() if k not in ("client", "release_id", "files")})


def without(doc: str, changed: str):
    """An edit that removes doc from a staged release (its row, index entry, chunks and text) and changes chunk changed's
    text by a word."""
    def edit(s: Path) -> None:
        for rel, key in (("certification.json", "asset_id"), ("documents/index.json", "id")):
            rows = json.loads((s / rel).read_text())
            (s / rel).write_text(json.dumps([r for r in rows if r[key] != doc], indent=1))
        path = s / "documents" / "chunks.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows = [{**r, "text": r["text"] + " Reviewed."} if r["id"] == changed else r for r in rows if r["doc_id"] != doc]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        (s / "documents" / f"{doc}.md").unlink()
    return edit


def test_a_decided_document_gone_from_the_landing_and_a_changed_text_are_orphans_never_attached(walked, tmp_path):
    build = copy_of(walked, tmp_path / "build")
    land_edited(build, without("EM-004", "POL-RETENTION#0"))
    loaded, piped = reload(build, walked.artifact)
    recs = records(build)
    term, retention = recs[0]["tag"]["id"], recs[1]["tag"]["id"]
    assert re.search(rf"WARNING: overlay orphan {re.escape(term)} .*EM-004.*gone", loaded.stderr), loaded.stderr
    for tag, why in ((term, "EM-004"), (retention, "changed")):
        assert re.search(rf"WARNING: overlay orphan {re.escape(tag)} .*{why}", piped.stderr), piped.stderr
    stats = json.loads(piped.stdout)["overlay"]
    assert (stats["records"], stats["replayed"]) == (4, 2) and [o["tag"] for o in stats["orphans"]] == [term, retention]
    assert json.loads((run_of(build) / "pipeline_stats.json").read_text())["overlay"]["orphans"] == stats["orphans"]
    held_out = json.loads((journal_of(build).parent / "orphans.json").read_text())
    assert [o["tag"] for o in held_out["kge/graph/load.py"]] == [term] and held_out["kge/pipeline/run.py"] == stats["orphans"]
    assert all(o["why"] and o["decision"] and o["who"] and o["machine"] for o in stats["orphans"])
    after = held(build)
    assert after["term"] == after["defines"] == after["writers"] == []
    assert {h[0] for h in after["humans"]} == {recs[3]["tag"]["id"]}, "only the match's decision is attached"
    assert [r[0] for r in after["requests"]] == [recs[2]["tag"]["id"]], "and the request, whose chunk is as it was"
    assert after["retention"] == [["embed-prohibited", False]]
    e = Engine(profile_of(build))
    try:
        assert walked.on["retention"] in {r["tag"] for r in e.g.steward_queue()}, "the changed chunk's tag waits in the queue again"
        assert TERM not in e.g.terms()
    finally:
        e.close()
    assert journal_of(build).read_bytes() == walked.journal, "an orphan stays in the journal: the record is the record"


def test_a_match_no_longer_landed_for_review_is_an_orphan(walked, tmp_path):
    build = copy_of(walked, tmp_path / "build")
    match = walked.on["match"].rsplit("/", 1)[1]

    def certified_upstream(s: Path) -> None:
        for path in (s / "review").glob("*.json"):
            held_ = json.loads(path.read_text())
            path.write_text(json.dumps({**held_, "matches": [m for m in held_["matches"] if str(m["id"]) != match]}, indent=1))
    land_edited(build, certified_upstream)
    _, piped = reload(build, walked.artifact)
    stats = json.loads(piped.stdout)["overlay"]
    assert [o["tag"] for o in stats["orphans"]] == [records(build)[3]["tag"]["id"]] and "review" in stats["orphans"][0]["why"]
    assert len(held(build)["humans"]) == 2


def test_a_journal_holding_another_client_s_records_is_refused_and_nothing_moves(walked, tmp_path):
    build = copy_of(walked, tmp_path / "build")
    with journal_of(build).open("a") as f:
        f.write(json.dumps({**records(build)[1], "client": "other-client"}) + "\n")
    then = held(build)
    graph_file = graph_of(build)
    stamp = graph_file.stat().st_mtime_ns
    loaded = load_artifact(build, walked.artifact)
    assert loaded.returncode == 2 and str(journal_of(build)) in loaded.stderr and "other-client" in loaded.stderr, loaded.stderr
    assert "Nothing was loaded" in loaded.stderr and graph_file.stat().st_mtime_ns == stamp, "refused before the graph is touched"
    piped = script("kge/pipeline/run.py", build=build)
    assert piped.returncode == 2 and "other-client" in piped.stderr, piped.stderr
    assert held(build) == then
    with pytest.raises(overlay.OverlayError, match="other-client"):
        overlay.Journal(profile_of(build).overlay_dir(), CLIENT).records()


def test_an_append_that_fails_is_a_500_that_says_so(fresh, tmp_path, monkeypatch):
    """The journal's path is made a folder, so appending to it fails as a full disk would."""
    import kge.app as appmod
    from kge.services.identity.service import token_for
    build = copy_of(fresh, tmp_path / "build")
    journal_of(build).mkdir(parents=True)
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        steward = {"authorization": f"Bearer {token_for(run_of(build), 'steward_glossary')}"}
        tag = next(t for t in c.get("/steward/queue", headers=steward).json() if t["doc"] == "POL-RETENTION" and t["family"] == "privacy")
        r = c.post("/steward/decide", json={"tag": tag["tag"], "decision": "reject"}, headers=steward)
        assert r.status_code == 500, r.text
        said = r.json()["detail"]
        assert str(journal_of(build)) in said and "journal" in said and "make graph" in said, said
        assert tag["tag"] not in {t["tag"] for t in c.get("/steward/queue", headers=steward).json()}, "it is in the graph, as it says"
    appmod._engine.close() if appmod._engine else None
    e = Engine(profile_of(build))
    try:
        other = next(t for t in e.g.steward_queue() if t["family"] == "candidate_term")
        with pytest.raises(overlay.OverlayError, match="journal"):
            e.g.steward_decide(other["tag"], "steward_glossary", "accept")
    finally:
        e.close()


def test_reset_overlay_sets_the_journal_aside_and_a_fresh_load_holds_the_183(walked, fresh, tmp_path):
    build = copy_of(walked, tmp_path / "build")
    archive = journal_of(build).parent / overlay.ARCHIVE
    earlier = set(archive.glob("*.jsonl"))
    (journal_of(build).parent / "orphans.json").write_text("{}")
    r = script("kge/pipeline/overlay.py", "--reset", build=build)
    assert r.returncode == 0, r.stderr
    archived = sorted(set(archive.glob("*.jsonl")) - earlier)
    assert len(archived) == 1 and re.fullmatch(r"\d{8}T\d{6}Z\.jsonl", archived[0].name), "the reset adds one archive"
    assert archived[0].read_bytes() == walked.journal and str(archived[0]) in r.stdout
    assert not journal_of(build).exists() and not (journal_of(build).parent / "orphans.json").exists()
    _, piped = reload(build, walked.artifact)
    assert json.loads(piped.stdout)["overlay"]["records"] == 0
    now = held(build)
    assert now["humans"] == now["requests"] == now["term"] == []
    assert now["overlay"] == fresh.held["overlay"], "the overlay of a build no steward has touched"
    parity = script("kge/eval/parity.py", "--snapshot", SNAPSHOT, "--stated", STATED, build=build)
    assert parity.returncode == 0, parity.stdout + parity.stderr
    assert "passed: 164 of 183 results unchanged, 19 moved as stated" in parity.stdout
    again = script("kge/pipeline/overlay.py", "--reset", build=build)
    assert again.returncode == 0 and "nothing to set aside" in again.stdout
    assert set(archive.glob("*.jsonl")) - earlier == set(archived)


def after_a_walkthrough_and_a_reset(walked, dest: Path) -> Path:
    """A demo's build as the README's acceptance runs leave it: the walkthrough's decisions in its graph and its journal,
    and beside them a journal an earlier make reset-overlay set aside."""
    build = copy_of(walked, dest)
    archive = journal_of(build).parent / overlay.ARCHIVE
    archive.mkdir()
    (archive / "20261013T212412Z.jsonl").write_bytes(walked.journal)
    return build


def test_the_scratch_build_copies_the_overlay_with_the_graph_and_never_links_it(walked, tmp_path, monkeypatch):
    import tests.scratch as scratch
    monkeypatch.setattr(scratch, "BUILD", after_a_walkthrough_and_a_reset(walked, tmp_path / "demo"))
    with_graph = scratch.scratch_build(tmp_path / "with")
    assert journal_of(with_graph).read_bytes() == walked.journal and not (with_graph / "stores").is_symlink()
    assert not (journal_of(with_graph).parent / overlay.ARCHIVE).exists(), "the archive is the demo's history, not the graph's"
    assert not (scratch.scratch_build(tmp_path / "without", graph=False) / "stores").exists(), "a fresh store has no overlay"


def test_the_module_walks_a_graph_no_steward_has_touched_whatever_the_demo_s_build_holds(walked, fresh, tmp_path, monkeypatch):
    """The acceptance runs this module after the walkthrough, with its decisions in the demo's graph and journal, and again
    after make reset-overlay: neither reaches the graph the module walks."""
    import tests.scratch as scratch
    monkeypatch.setattr(scratch, "BUILD", after_a_walkthrough_and_a_reset(walked, tmp_path / "demo"))
    again = untouched(tmp_path / "again")
    ov = journal_of(again.build).parent
    assert not journal_of(again.build).exists() and not (ov / overlay.ARCHIVE).exists(), "neither the demo's journal nor its archive"
    assert again.artifact.name == fresh.artifact.name and again.held == fresh.held
    assert again.held["humans"] == again.held["requests"] == again.held["term"] == []


def test_make_reset_overlay_runs_the_reset_for_the_client():
    mk = (DEMO / "Makefile").read_text()
    assert "reset-overlay" in re.search(r"^\.PHONY:(.*)$", mk, re.M).group(1).split()
    assert mk.split("\nreset-overlay:", 1)[1].split("\n\n", 1)[0].split() == ["$(KGE)", "reset-overlay", "$(KGE_ARGS)"]
    from tests.unit.test_cli import planned
    rule = planned("reset-overlay")
    assert rule.endswith(" -m kge.pipeline.overlay --reset\n") and "--client" not in rule, "the profile's client, PROFILE exported"


def test_the_readme_says_decisions_survive_make_graph_and_how_to_set_them_aside():
    """make test needs the decisions set aside because the other tests read the demo's graph, which a kept decision
    moves; this module loads its own."""
    readme = (DEMO / "README.md").read_text()
    assert "discards steward decisions" not in readme and "`make graph` discards" not in readme
    assert "pytest tests/integration/test_overlay.py -q" in readme
    walk_ = readme.split("## The steward walkthrough", 1)[1].split("\n## ", 1)[0]
    for needed in ("build/stores/meridian/overlay/decisions.jsonl", "`make reset-overlay`", "orphans.json", "G041", "H014",
                   "same ids", "overlay hash"):
        assert needed in walk_, needed
    assert re.search(r"`make test`[^.]*`make reset-overlay graph pipeline`", walk_), "make test needs the decisions set aside"
    assert re.search(r"pytest tests/integration/test_overlay\.py -q`[^.]*graph of its own[^.]*whatever", walk_), "test_overlay runs either way"
    running = readme.split("## Running it", 1)[1].split("\n## ", 1)[0]
    assert "make reset-overlay" in running.split("```", 2)[1]
    make_test = next(line for line in running.split("```", 2)[1].splitlines() if line.startswith("make test "))
    assert "make reset-overlay graph pipeline" in make_test, make_test
    suite = next(line for line in readme.splitlines() if line.startswith(".venv/bin/python -m pytest tests -q "))
    assert "make reset-overlay graph pipeline" in suite, suite
