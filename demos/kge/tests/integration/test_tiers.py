"""Tier-aware retrieval: what upstream AI certification landed decides what is retrieved and cited.

    .venv/bin/python -m pytest tests/integration/test_tiers.py -q

AI certification runs before the graph, with its own store (kge/estate/certify.py),
and lands one row per document, AI-ready yes or no and a tier, in the landing
zone (certification.json in build/landing/<client>/releases/<release_id>/). The
graph reads the landed rows into each Document, fails closed on a missing one,
and never writes back. Retrieval can be held to AI-ready
documents of named tiers. The tier rule (citation_rule=tiers, the treatment's)
cites gold as the authority, silver with attribution and bronze never; the
presets keep citing by shelf.

CLIENT is the client the demo's build is for, the registry's default profile's.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.core import PRESETS, STAGES, TIERS, Engine, asked_window, in_force, parse
from kge.estate import certify
from kge.estate.documents import all_documents
from kge.landing.zone import Zone
from kge.eval import sets
from kge.eval.run_golden import score, uncertified_ids
from kge.adapters.graphstore import connect, open_db
from tests.scratch import BUILD, RUN_STATE, graph_of, load_artifact, profile_of, scratch_build, write_registry

DEMO = Path(__file__).resolve().parents[2]
CLIENT = "meridian"
TREAT = PRESETS["graph"].with_stages(citation_rule="tiers")
FIELDS = {"asset_id", "ai_ready", "tier", "evidence", "policy_id", "version", "effective_from", "effective_to"}
PII_DOCS = {"POL-COMPLAINTS", "POL-RETENTION", "EM-006", "XLS-001"}
OVERDUE = {"STD-FISCAL-CAL", "MD-PERIOD-END-FX-RATE", "PROC-STEWARD"}


@pytest.fixture(scope="module")
def landed() -> dict[str, dict]:
    return {r["asset_id"]: r for r in Zone(BUILD / "landing", CLIENT).open().certification()}


@pytest.fixture(scope="module")
def golden() -> list[dict]:
    return sets.items(BUILD / "golden.json")


def item(golden, gid):
    return next(i for i in golden if i["id"] == gid)


def test_every_document_lands_one_row_with_the_agreed_fields(landed):
    docs = json.loads((BUILD / "documents.json").read_text())
    assert sorted(landed) == sorted(d["doc_id"] for d in docs)
    for r in landed.values():
        assert set(r) == FIELDS, r
        assert r["ai_ready"] in ("yes", "no")
        assert (r["tier"] is None) == (r["ai_ready"] == "no") and r["tier"] in (None, *TIERS)
        assert r["policy_id"] == certify.POLICY_ID and r["version"] == certify.POLICY_VERSION and r["evidence"]


def test_a_document_with_personal_data_the_gate_prohibits_is_not_ai_ready(landed):
    assert {d for d, r in landed.items() if r["ai_ready"] == "no"} == PII_DOCS
    for d in PII_DOCS:
        ev = landed[d]["evidence"]
        assert f"{d}#" in ev and ("personal_data" in ev or "payment_card" in ev), ev
        assert "@" not in ev and not re.search(r"\d{3,}", ev.replace(d, "")), "evidence names the finding, never the data"


def test_a_missing_title_or_kind_is_not_ai_ready():
    doc = {"doc_id": "X-1", "shelf": "certified", "title": "Something", "kind": "policy", "owner": "Office", "version": "1.0",
           "published_at": "2025-06-01", "reviewed_at": "2025-06-01"}
    assert certify.certify(doc, ["clean text"], dt.date(2025, 7, 1))["tier"] == "gold"
    for missing in ("title", "kind"):
        r = certify.certify({**doc, missing: None}, ["clean text"], dt.date(2025, 7, 1))
        assert (r["ai_ready"], r["tier"]) == ("no", None) and missing in r["evidence"]


def test_the_tiers_follow_shelf_owner_version_publication_and_review(landed):
    """A certified-shelf document without an owner is not gold or silver, whatever its review."""
    tiers = {d: r["tier"] for d, r in landed.items()}
    assert {d for d, t in tiers.items() if t == "silver"} == OVERDUE
    for d in ("MD-ACTIVE-CUSTOMER", "POL-DATA-CLASS", "DD-CORPORATE", "STD-FX"):
        assert tiers[d] == "gold"
    docs = {d["doc_id"]: d for d in json.loads((BUILD / "documents.json").read_text())}
    assert all(tiers[d] == "bronze" for d, x in docs.items() if x["shelf"] == "uncertified" and d not in PII_DOCS)
    doc = {**docs["STD-FX"], "owner": None}
    assert certify.certify(doc, ["clean text"], dt.date(2025, 7, 1))["tier"] == "bronze"
    assert "overdue" in landed["STD-FISCAL-CAL"]["evidence"] and "within" in landed["STD-FX"]["evidence"]


def test_the_superseded_definition_keeps_the_tier_it_held_while_in_force(landed):
    v1, v2 = landed["MD-ACTIVE-CUSTOMER-V1"], landed["MD-ACTIVE-CUSTOMER"]
    assert (v1["tier"], v1["effective_from"], v1["effective_to"]) == ("gold", "2023-06-01", "2024-12-31")
    assert (v2["tier"], v2["effective_from"], v2["effective_to"]) == ("gold", "2025-01-01", None)
    assert "last day in force" in v1["evidence"]


def test_review_is_judged_on_the_as_of_date_and_the_run_is_deterministic():
    rows = certify.certify_estate(BUILD)
    assert rows == certify.certify_estate(BUILD)
    later = {r["asset_id"]: r["tier"] for r in certify.certify_estate(BUILD, dt.date(2026, 7, 1))}
    assert {d for d, t in later.items() if t == "silver"} > OVERDUE, "a year on, every review is overdue"
    assert later["MD-ACTIVE-CUSTOMER-V1"] == "gold", "a withdrawn document is judged on its last day in force"


def test_review_dates_change_no_document_text():
    docs = all_documents()
    certified = [d for d in docs if d.shelf == "certified"]
    assert all(d.reviewed_at for d in certified) and not any(d.reviewed_at for d in docs if d.shelf == "uncertified")
    for d in docs:
        assert d.text() == dataclasses.replace(d, reviewed_at=None).text()
    on_disk = {d["doc_id"]: d for d in json.loads((BUILD / "documents.json").read_text())}
    assert all(on_disk[d.doc_id]["reviewed_at"] == d.reviewed_at for d in docs)
    for d in docs:
        assert (BUILD / on_disk[d.doc_id]["path"]).read_text() == d.text()


def test_certification_reads_the_estate_and_never_the_graph(tmp_path, landed):
    build = tmp_path / "build"
    build.mkdir()
    for name in ("documents.json", "docs", "warehouses"):
        (build / name).symlink_to(BUILD / name)
    r = subprocess.run([sys.executable, "-m", "kge.estate.certify", "--build", str(build), "--client", CLIENT],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert {r["asset_id"]: r for r in Zone(build / "landing", CLIENT).open().certification()} == landed
    assert not list(build.glob("graph.*"))
    assert json.loads(r.stdout)["documents"] == len(json.loads((BUILD / "documents.json").read_text()))


def test_the_graph_holds_what_certification_landed(eng, landed):
    rows = eng.g.q("MATCH (d:Document) RETURN d.id AS id, d.ai_ready AS ai_ready, d.tier AS tier, d.cert_evidence AS evidence, "
                   "d.cert_policy AS policy, d.cert_version AS version, d.cert_from AS cert_from, d.cert_to AS cert_to")
    assert len(rows) == len(landed)
    for r in rows:
        want = landed[r["id"]]
        assert r["ai_ready"] is (want["ai_ready"] == "yes") and r["tier"] == want["tier"]
        assert (r["evidence"], r["policy"], r["version"]) == (want["evidence"], want["policy_id"], want["version"])
        assert (r["cert_from"], r["cert_to"] or None) == (want["effective_from"], want["effective_to"])


def landed_release(root: Path, files: dict[str, str]):
    """A release of a client of its own, landed under root, holding files (path -> text)."""
    zone = Zone(root, "acme")
    s = zone.stage()
    for rel, text in files.items():
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        (s / rel).write_text(text)
    return zone.land(s, {"policy": certify.POLICY_ID, "version": certify.POLICY_VERSION, "as_of": "2025-07-01", "counts": {}})


def test_a_missing_landing_file_fails_closed_with_a_warning(tmp_path):
    from kge.graph.load import read_certification
    props, warnings = read_certification(None, ["MD-REVENUE"])
    assert props["MD-REVENUE"]["ai_ready"] is False and props["MD-REVENUE"]["tier"] is None
    assert warnings and "ai_ready = false" in warnings[0] and "make certify" in warnings[0]
    props, warnings = read_certification(landed_release(tmp_path / "a", {"documents/index.json": "[]"}), ["MD-REVENUE"])
    assert props["MD-REVENUE"]["ai_ready"] is False and "ai_ready = false" in warnings[0] and "make certify" in warnings[0]
    props, warnings = read_certification(landed_release(tmp_path / "b", {"certification.json": "[]"}), ["MD-REVENUE"])
    assert props["MD-REVENUE"]["ai_ready"] is False and any("MD-REVENUE" in w for w in warnings), "an empty landing is no landing"


def test_a_row_landed_ready_without_a_valid_tier_loads_with_no_tier_and_a_warning(tmp_path):
    from kge.graph.load import read_certification
    rows = [{"asset_id": "A", "ai_ready": "yes", "tier": "platinum"}, {"asset_id": "B", "ai_ready": "no", "tier": "gold"},
            {"asset_id": "Z", "ai_ready": "yes", "tier": "gold"}]
    props, warnings = read_certification(landed_release(tmp_path, {"certification.json": json.dumps(rows)}), ["A", "B"])
    assert (props["A"]["ai_ready"], props["A"]["tier"]) == (True, None) and (props["B"]["ai_ready"], props["B"]["tier"]) == (False, None)
    assert any("no valid tier" in w and "A" in w for w in warnings) and any("Z" in w and "ignored" in w for w in warnings)


def test_a_document_with_no_landed_row_loads_not_ready_and_the_loader_says_so(tmp_path, landed, compiled):
    build = tmp_path / "build"
    build.mkdir()
    for p in BUILD.iterdir():
        if p.name not in ("landing", "stores", "artifacts", *RUN_STATE) and not p.name.startswith("graph."):
            (build / p.name).symlink_to(p)
    write_registry(build)
    src, zone = Zone(BUILD / "landing", CLIENT).open(), Zone(build / "landing", CLIENT)
    s = zone.stage()
    for rel in src.manifest["files"]:
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        (s / rel).write_bytes((src.path / rel).read_bytes())
    (s / "certification.json").write_text(json.dumps([r for r in landed.values() if r["asset_id"] != "MD-REVENUE"]))
    rel = zone.land(s, {k: v for k, v in src.manifest.items() if k not in ("client", "release_id", "files")})
    before = hashlib.sha256((rel.path / "certification.json").read_bytes()).hexdigest()
    r = load_artifact(build, compiled)
    assert r.returncode == 0, r.stderr
    assert "WARNING" in r.stderr and "MD-REVENUE" in r.stderr and "ai_ready = false" in r.stderr
    db = open_db(graph_of(build))
    conn = connect(db)
    got = {row[0]: (row[1], row[2]) for row in conn.execute("MATCH (d:Document) RETURN d.id, d.ai_ready, d.tier").get_all()}
    conn.close()
    db.close()
    assert got["MD-REVENUE"] == (False, None) and got["MD-ACTIVE-CUSTOMER"] == (True, "gold")
    assert hashlib.sha256((rel.path / "certification.json").read_bytes()).hexdigest() == before, "the graph never writes back"


def test_the_model_hash_covers_the_certification(tmp_path):
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        before = runtrace.fingerprint(e.g)
        e.g.q("MATCH (d:Document {id: 'STD-FX'}) SET d.tier = 'silver'")
        after = runtrace.fingerprint(e.g)
        assert after["model"] != before["model"] and after["overlay"] == before["overlay"]
    finally:
        e.close()


def test_retrieval_with_tiers_returns_only_ai_ready_documents_of_those_tiers(eng, landed):
    for tiers in (("bronze",), ("gold", "silver"), TIERS):
        hits = eng.g.retrieve("active customer definition login", "analyst_enterprise", "analysis", k=8, tiers=tiers)
        assert hits["exact"] and all(h["ai_ready"] is True and h["tier"] in tiers for h in hits["exact"])
        assert all(landed[h["doc"]]["tier"] == h["tier"] for h in hits["exact"])
        assert all(h["tier"] in tiers and h["ai_ready"] for h in hits["post_filtered"])
    untiered = eng.g.retrieve("active customer definition login", "analyst_enterprise", "analysis", k=8)
    assert all(h["tier"] == landed[h["doc"]]["tier"] for h in untiered["exact"]), "every row says what landed, filter or not"


@pytest.mark.parametrize("tiers", [("platinum",), (), ("gold", "Gold")])
def test_an_unknown_or_empty_tier_list_is_an_error(eng, tiers):
    with pytest.raises(ValueError, match="tiers"):
        eng.g.retrieve("revenue", "analyst_enterprise", "analysis", tiers=tiers)


def test_retrieval_breaks_a_tie_in_score_by_chunk_so_a_context_is_the_same_every_run(eng):
    """WIKI-004 and WIKI-005 hold the same words, so they score the same, and their order must not change from run to
    run."""
    q = "SME repayments more than 30 days past due in 2025"
    runs = {tuple((h["chunk"], h["score"]) for h in eng.g.retrieve(q, "analyst_enterprise", "analysis", k=3, tiers=("bronze",))["exact"])
            for _ in range(12)}
    assert len(runs) == 1, runs
    hits = runs.pop()
    assert {"WIKI-004#0", "WIKI-005#0"} <= {c for c, _ in hits}, "the tie is there to break"
    assert list(hits) == sorted(hits, key=lambda h: (-h[1], h[0]))


def test_a_document_not_ai_ready_upstream_stays_out_even_once_the_graph_embeds_it(tmp_path):
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        tag = next(r for r in e.g.steward_queue() if r["doc"] == "POL-RETENTION" and r["family"] == "privacy")
        assert e.g.steward_decide(tag["tag"], "steward_glossary", "reject").get("embedded_now")
        q = "records retention policy for customer communications"
        assert "POL-RETENTION" in {h["doc"] for h in e.g.retrieve(q, "analyst_enterprise", "analysis", k=5)["exact"]}
        assert "POL-RETENTION" not in {h["doc"] for h in e.g.retrieve(q, "analyst_enterprise", "analysis", k=5, tiers=TIERS)["exact"]}
        assert e.g.q("MATCH (d:Document {id: 'POL-RETENTION'}) RETURN d.ai_ready AS r")[0]["r"] is False, "a steward decision writes nothing upstream"
    finally:
        e.close()


@pytest.mark.parametrize("question, window", [
    ("Which version of the active customer definition applies to 2024 reporting?", ("2024-01-01", "2024-12-31")),
    ("What is the definition of an active customer?", None),
    ("How many retail customers were active in June 2025?", ("2025-06-01", "2025-06-30")),
    ("Wealth fee revenue for FY26-Q1", ("2025-04-01", "2025-06-30")),
    ("Number of open retail complaints as at 30 June 2025", ("2025-06-30", "2025-06-30")),
])
def test_the_window_a_question_asks_about(eng, question, window):
    assert asked_window(parse(question, ["Active Customer"], eng), eng) == window


def test_in_force_reads_the_effective_window():
    v1 = {"cert_from": "2023-06-01", "cert_to": "2024-12-31"}
    v2 = {"cert_from": "2025-01-01", "cert_to": ""}
    assert in_force(v1, ("2024-01-01", "2024-12-31")) and not in_force(v2, ("2024-01-01", "2024-12-31"))
    assert in_force(v2, None) and not in_force(v1, None), "with no period asked, only what is in force now"
    assert in_force(v1, ("2024-12-31", "2025-01-31")) and in_force(v2, ("2024-12-31", "2025-01-31"))


def test_tiers_is_a_citation_rule_the_presets_do_not_use():
    assert "tiers" in STAGES["citation_rule"]
    assert [c.citation_rule for c in PRESETS.values()] == ["retrieval", "retrieval", "shelves"]
    assert TREAT.problems("answer", has_model=False) == [] and TREAT.label == "graph[citation_rule=tiers]"
    assert any("citation_rule" in p for p in TREAT.problems("ask", has_model=False)), "/ask cites by shelf"


def test_gold_is_the_authority_silver_is_attributed_and_bronze_is_never_cited(eng, landed):
    a = eng.answer("What is the definition of an active customer?", config=TREAT)
    assert a["behaviour"] == "cite_certified" and a["arm"] == "graph[citation_rule=tiers]"
    assert a["cite"] == [c["doc"] for c in a["citations"]] and [c["n"] for c in a["citations"]] == list(range(1, len(a["cite"]) + 1))
    first = a["citations"][0]
    assert (first["doc"], first["tier"], first["cited_as"]) == ("MD-ACTIVE-CUSTOMER", "gold", "authority") and first["chunk"].startswith("MD-ACTIVE-CUSTOMER#")
    assert all(landed[c["doc"]]["tier"] in ("gold", "silver") for c in a["citations"])
    assert a["references"] and all(r["tier"] == "bronze" and r["doc"] not in a["cite"] for r in a["references"])
    assert {"EM-001", "DRAFT-002", "WIKI-003"} & {r["doc"] for r in a["references"]}
    assert "MD-ACTIVE-CUSTOMER-V1" in a["superseded_not_cited"]
    cal = eng.answer("Which calendar does Wealth Management report on?", config=TREAT)
    std = next(c for c in cal["citations"] if c["doc"] == "STD-FISCAL-CAL")
    assert (std["tier"], std["cited_as"], std["attribution"]) == ("silver", "attributed", "Group Finance, version 2.1")
    assert all("attribution" not in c for c in cal["citations"] if c["tier"] == "gold")


def test_g040_cites_the_version_in_force_for_2024_and_the_current_one(eng, golden):
    g040 = item(golden, "G040")
    a = eng.answer(g040["question"], config=TREAT)
    ok, why = score(g040, a, uncertified_ids(BUILD))
    assert ok, why
    v1 = next(c for c in a["citations"] if c["doc"] == "MD-ACTIVE-CUSTOMER-V1")
    assert v1["in_force"] == "2023-06-01 to 2024-12-31" and a["in_force_for"] == {"from": "2024-01-01", "to": "2024-12-31"}
    assert score(g040, eng.answer(g040["question"], arm_name="graph"), uncertified_ids(BUILD))[0], "the preset still passes it by shelf"


def test_g041_routes_to_the_steward_with_no_tier_filter(eng, golden):
    g041 = item(golden, "G041")
    treated, preset = (eng.answer(g041["question"], config=cfg) for cfg in (TREAT, PRESETS["graph"]))
    for a in (treated, preset):
        assert score(g041, a, uncertified_ids(BUILD))[0], a
    assert "EM-004" in treated["candidate_sources"] and treated["candidate_sources"] == preset["candidate_sources"], "bronze, and still routed"


def test_every_golden_shelf_item_passes_under_the_tier_rule(eng, golden):
    unc = uncertified_ids(BUILD)
    for it in [i for i in golden if i["kind"] == "shelf"]:
        a = eng.answer(it["question"], config=TREAT)
        ok, why = score(it, a, unc)
        assert ok, f"{it['id']}: {why}"


def test_the_tier_rule_retrieves_nothing_that_is_not_ai_ready(eng, golden):
    for it in [i for i in golden if i["kind"] == "shelf"]:
        a = runtrace.run("answer", lambda: eng.answer(it["question"], config=TREAT), graph=eng.g)
        rows = a["run_trace"]["retrieved"]
        assert rows and all(r["ai_ready"] is True and r["tier"] in TIERS for r in rows), (it["id"], rows)
        assert not PII_DOCS & ({r["doc"] for r in rows} | set(a["cite"]))


def test_a_term_defined_only_by_a_bronze_source_abstains(tmp_path):
    e = Engine(profile_of(scratch_build(tmp_path / "build")))
    try:
        cand = next(r for r in e.g.steward_queue() if r["family"] == "candidate_term" and r["doc"] == "EM-004")
        e.g.steward_decide(cand["tag"], "steward_glossary", "accept", definition="logins per active customer per month")
        e.refresh()
        a = e.answer("What is the Digital Engagement Score?", config=TREAT)
        assert a["behaviour"] == "abstain" and a["cite"] == [] and "EM-004" in a["reason"] and "bronze" in a["reason"]
        assert "EM-004" in {r["doc"] for r in a["references"]}
    finally:
        e.close()


def test_the_presets_cite_by_shelf_as_before(eng, golden):
    for it in [i for i in golden if i["kind"] == "shelf"]:
        a = eng.answer(it["question"], arm_name="graph")
        assert "references" not in a and "citations" not in a


@pytest.mark.parametrize("arm", ["schema", "catalog"])
def test_a_definition_with_no_shelf_distinction_says_which_shelf_each_cited_document_is_on(eng, arm):
    """The weaker arms cite what search returned, so the page shows each by its shelf and the uncertified ones show."""
    a = eng.answer("What is the definition of an active customer?", "analyst_enterprise", arm_name=arm)
    assert a["behaviour"] == "cite" and a["cite"]
    shelf = {d["doc_id"]: d["shelf"] for d in json.loads((BUILD / "documents.json").read_text())}
    assert [d["doc"] for d in a["cited_docs"]] == a["cite"]
    assert all(d["shelf"] == shelf[d["doc"]] and d["title"] for d in a["cited_docs"])
    assert {d["shelf"] for d in a["cited_docs"]} == {"certified", "uncertified"}


def test_every_trace_row_says_what_landed(eng, landed):
    for cfg in (*PRESETS.values(), TREAT):
        a = runtrace.run("answer", lambda: eng.answer("Which calendar does Wealth Management report on?", config=cfg), graph=eng.g)
        rows = a["run_trace"]["retrieved"]
        assert rows and all(r["ai_ready"] is (landed[r["doc"]]["ai_ready"] == "yes") and r["tier"] == landed[r["doc"]]["tier"] for r in rows)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kge.app as appmod
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        from kge.services.identity.service import token_for
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_answer_takes_the_tier_rule_and_presets_list_it(client):
    who = client.as_("analyst_enterprise")
    a = client.post("/answer", json={"question": "Which calendar does Wealth Management report on?", "stages": {"citation_rule": "tiers"}}, headers=who).json()
    assert a["arm"] == "graph[citation_rule=tiers]" and any(c["tier"] == "silver" for c in a["citations"])
    assert [(c["doc"], c["n"]) for c in a["run_trace"]["citations"]] == [(c["doc"], c["n"]) for c in a["citations"]]
    assert "tiers" in client.get("/presets").json()["stages"]["citation_rule"]


def test_retrieve_takes_tiers(client):
    who = client.as_("analyst_enterprise")
    r = client.post("/retrieve", json={"query": "active customer", "purpose": "analysis", "k": 6, "tiers": ["bronze"]}, headers=who)
    assert r.status_code == 200 and all(h["tier"] == "bronze" for h in r.json()["exact"])
    bad = client.post("/retrieve", json={"query": "active customer", "tiers": ["platinum"]}, headers=who)
    assert bad.status_code == 400 and "tiers" in bad.json()["detail"]


def test_make_estate_certifies_and_make_certify_runs_alone():
    """The demo's client's estate is generated, while a client with a fixture has its own built from its files (#39 item
    11); the graph needs the profile's current landed release, LANDED."""
    from tests.unit.test_cli import planned
    make = (DEMO / "Makefile").read_text()
    for target in ("estate", "certify", "graph"):
        assert make.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0].split() == ["$(KGE)", target, "$(KGE_ARGS)"], target
    for pack, built in (("clients/meridian", "kge.estate.generate"), ("clients/thornbury", "kge.estate.from_files")):
        estate = [step.split(" -m ", 1)[1] for step in planned("estate", f"PACK={pack}").splitlines()]
        assert estate == [built, "kge.estate.onboard build", "kge.estate.certify"], (pack, estate)
    assert "certify" in make.split(".PHONY:", 1)[1].split("\n", 1)[0]
    summary = str(DEMO / "Makefile")
    assert [s.split(" -m ", 1)[1] for s in planned("certify", f"SUMMARY={summary}").splitlines()] == ["kge.estate.certify"]
    landed = str(DEMO / "build" / "no-such-landed-release")
    graph = [s.split(" -m ", 1)[1] for s in planned("graph", f"LANDED={landed}", f"SUMMARY={summary}").splitlines()]
    assert graph[0] == "kge.estate.certify" and graph[1] == "kge.compiler.lock --check", graph
    assert "kge.estate.certify" not in planned("graph", f"LANDED={DEMO}")
    assert "\nLANDED := $(call profile,landed)\n" in make


def test_the_readme_explains_certification_and_the_tier_rule():
    readme = (DEMO / "README.md").read_text()
    for needle in ("make certify", "kge/estate/certify.py", "build/landing/<client>/releases/", "certification.json", "`tiers`", "citation_rule"):
        assert needle in readme, needle
