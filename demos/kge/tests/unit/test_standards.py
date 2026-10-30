"""The platform's preloaded industry standards (standards/preload.yaml, meta-model 1.3.0) and Meridian's mappings to them.

    .venv/bin/python -m pytest tests/unit/test_standards.py -q

The preload is platform data, client-free and versioned, with the licence notice each standard asks for beside it
(standards/NOTICES.md). The compiler loads it into every client's graph as reference OntologyConcepts; a pack only maps
to it (ontology_mappings), and S30 refuses a mapping to an IRI it does not hold. Meridian's pack 1.4.0 maps net new
assets, assets under management and five other terms, the Wealth account and the restricted personal data, each
certified by its steward. Mappings reach no answer: the context packages and parity are held elsewhere
(tests/test_context.py, tests/test_agent_loop.py, make parity).
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest
import yaml

from kge.compiler import checks, emit, standards
from kge.compiler import compile as compiler
from kge.compiler import pack as packs

DEMO = Path(__file__).resolve().parents[2]
FIXTURES = DEMO / "tests" / "fixtures"
FIBO = "https://spec.edmcouncil.org/fibo/ontology/"
CLIENT_NOUNS = ("meridian", "thornbury", "fellmoor", "retail", "wealth", "household", "nna", "aum")


@pytest.fixture(scope="module")
def preload() -> standards.Preload:
    return standards.load()


def build(client: str) -> emit.Artifact:
    meta = packs.meta_model(DEMO / "meta-model.yaml")
    pack = packs.read(DEMO / "clients", client)
    assert not checks.check(pack, meta, compiler.VERSION, emit.FILLS), client
    return emit.build(pack, meta, compiler.VERSION)


def csv_rows(artifact: emit.Artifact, path: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(artifact.files[path].decode())))


def test_the_preload_is_versioned_client_free_and_covers_the_six_sub_graphs(preload):
    assert preload.version == "1.0.0"
    assert list(preload.standards) == ["fibo", "bian", "schemaorg", "skos", "dpv", "dcterms", "prov", "dcat", "ossie"]
    assert len(preload.concepts) == 23
    assert "iso20022" not in preload.standards and not any("iso20022" in c["iri"] for c in preload.concepts.values()), \
        "ISO 20022 is left out: its terms do not plainly allow its text to ship under the platform's licence"
    assert {c["sub_graph"] for c in preload.concepts.values()} == set(standards.SUB_GRAPHS)
    assert not any(c["standard"] == "ossie" for c in preload.concepts.values()), "a format, with no concept"
    text = (DEMO / "standards" / "preload.yaml").read_text().lower()
    for noun in CLIENT_NOUNS:
        assert not re.search(rf"\b{noun}\b", text), noun


def test_each_concept_s_id_is_its_standard_and_its_iri_s_local_name(preload):
    """So two standards' concepts of one local name never collide."""
    for cid, c in preload.concepts.items():
        assert cid == standards.concept_id(c["standard"], c["iri"]) and checks.SLUG.fullmatch(cid), cid
        assert c["source"].startswith("https://") and c["definition"].strip(), cid
    assert {"fibo-monetary-amount", "schemaorg-monetary-amount", "fibo-policy", "dcterms-policy", "dpv-pd-financial"} <= set(preload.concepts)


def test_schema_org_definitions_are_our_own_words(preload):
    """schema.org's text is CC BY-SA: only its IRIs and labels ship, with definitions in our own words."""
    ours = [c for c in preload.concepts.values() if c["standard"] == "schemaorg"]
    assert ours and all(c["definition_text"] == "own wording" for c in ours)
    assert all(c["definition_text"] == "verbatim" for c in preload.concepts.values() if c["standard"] != "schemaorg")


NOTICE = {"fibo": ("## FIBO", "Copyright (c) 2020 Enterprise Data Management Council", "Permission is hereby granted"),
          "bian": ("## BIAN", "Apache License", "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION"),
          "schemaorg": ("## schema.org", "CC BY-SA", "our own words"),
          "skos": ("## SKOS and PROV-O", "Copyright © 2009 W3C®", "https://www.w3.org/copyright/document-license-2023/"),
          "prov": ("## SKOS and PROV-O", "Copyright © 2011-2013 W3C®", "https://www.w3.org/copyright/document-license-2023/"),
          "dcat": ("## DCAT", "Copyright © 2024 World Wide Web", "https://www.w3.org/copyright/software-license-2023/"),
          "dpv": ("## DPV", "W3C Document License", "https://w3id.org/dpv"),
          "dcterms": ("## DCMI Metadata Terms", "Creative Commons Attribution 4.0", "issued 2020-01-20"),
          "ossie": ("## Apache Ossie", "Apache License, Version 2.0", "supplies no concept")}


def test_every_shipped_standard_has_its_licence_notice_beside_the_preload(preload):
    """The FIBO repository's MIT licence among them, verbatim, as the release ships it."""
    notices = standards.NOTICES.read_text()
    flat = " ".join(notices.split())
    assert set(NOTICE) == set(preload.standards), "a notice for every standard the preload names"
    for sid, needles in NOTICE.items():
        for needle in needles:
            assert " ".join(needle.split()) in flat, (sid, needle)
    assert "The above copyright notice and this permission notice shall be included in all\ncopies or substantial portions" \
        in notices
    assert "ISO 20022 is not preloaded" in notices


def test_a_preload_that_breaks_its_own_rules_is_refused(preload):
    raw = yaml.safe_load(standards.PRELOAD.read_text())

    def broken(change) -> str:
        data = yaml.safe_load(yaml.safe_dump(raw))
        change(data)
        with pytest.raises(ValueError) as e:
            standards.check(data)
        return str(e.value)
    assert "the id its IRI gives" in broken(lambda d: d["concepts"][4].update(id="fibo-mv"))
    assert "not listed" in broken(lambda d: d["concepts"][0].update(standard="iso20022"))
    assert "given twice" in broken(lambda d: d["concepts"].append(dict(d["concepts"][0])))
    assert "SKOS relation" in broken(lambda d: d["defaults"]["currency_measure"].update(relation="sameAs"))
    assert "sub_graph" in broken(lambda d: d["concepts"][0].update(sub_graph="industry-ontology"))


def test_meta_model_1_3_0_only_adds_to_1_2_0():
    old, new = (yaml.safe_load(p.read_text()) for p in (FIXTURES / "meta-model-1.2.0.yaml", DEMO / "meta-model.yaml"))
    assert (old["version"], new["version"]) == ("1.2.0", "1.3.0")
    for kind in ("node_types", "edge_types"):
        assert list(new[kind]) == list(old[kind]), "no type added or moved"
        for name, t in old[kind].items():
            n = new[kind][name]
            for k in t:
                if k in ("from", "to") and isinstance(n[k], list):
                    was = t[k] if isinstance(t[k], list) else [t[k]]
                    assert n[k][:len(was)] == was, (name, k)
                elif k != "properties":
                    assert n[k] == t[k], (name, k)
            assert list(n["properties"] or {})[:len(t["properties"] or {})] == list(t["properties"] or {}), name
    changed = {name for kind in ("node_types", "edge_types") for name in old[kind] if old[kind][name] != new[kind][name]}
    assert changed == {"OntologyConcept", "MAPPED_TO"}


@pytest.mark.parametrize("client", ["meridian", "thornbury", "fellmoor"])
def test_every_client_s_graph_holds_the_platform_s_preload(client, preload):
    art = build(client)
    concepts = csv_rows(art, "data/nodes/OntologyConcept.csv")
    assert [c["id"] for c in concepts] == sorted(preload.concepts)
    fibo = next(c for c in concepts if c["id"] == "fibo-market-value")
    assert fibo == {"id": "fibo-market-value", "label": "market value", "iri": f"{FIBO}FND/Arrangements/Assessments/MarketValue",
                    "definition": "price an asset would sell for in the market", "sub_graph": "metrics", "standard": "fibo",
                    "standard_version": "master_2026Q2 (released 2026-07-14)", "licence": "MIT",
                    "licence_url": "https://github.com/edmcouncil/fibo/blob/master_2026Q2/LICENSE",
                    "source": "https://github.com/edmcouncil/fibo/blob/master_2026Q2/FND/Arrangements/Assessments.rdf"}
    if client != "meridian":
        assert art.manifest["counts"]["edges"]["MAPPED_TO"] == 0, "a client maps nothing until its steward certifies a mapping"


def test_meridian_maps_nna_and_aum_to_the_preloaded_standards():
    """No standard defines NNA, AUM or AUA: nothing is an exact match, and AUM and AUA share no exact or close one."""
    art = build("meridian")
    terms = csv_rows(art, "data/edges/MAPPED_TO__Term__OntologyConcept.csv")
    by = {}
    for r in terms:
        by.setdefault(r["from"], set()).add((r["to"], r["relation"]))
    assert by["Net New Assets"] == {("fibo-cash-flow", "broadMatch"), ("fibo-monetary-amount", "broadMatch"),
                                    ("schemaorg-money-transfer", "relatedMatch")}
    assert by["Assets Under Management"] == {("fibo-market-value", "broadMatch"), ("fibo-monetary-amount", "broadMatch"),
                                             ("fibo-holding", "relatedMatch")}
    assert not [r for r in terms if r["relation"] == "exactMatch"]
    def close(term):
        return {t for t, rel in by.get(term, ()) if rel in ("exactMatch", "closeMatch")}
    assert not close("Assets Under Management") & close("Assets Under Advice")
    assert len(terms) == 11 and all(r["note"] for r in terms)
    assert csv_rows(art, "data/edges/MAPPED_TO__Entity__OntologyConcept.csv")[0]["to"] == "fibo-investment-account"
    (policy,) = csv_rows(art, "data/edges/MAPPED_TO__Policy__OntologyConcept.csv")
    assert (policy["from"], policy["to"], policy["relation"]) == ("restricted-personal-data", "dpv-personal-data", "broadMatch")


def scratch(tmp_path: Path, edit) -> Path:
    clients = tmp_path / "clients"
    (clients / "meridian").mkdir(parents=True)
    pack = yaml.safe_load((DEMO / "clients" / "meridian" / "pack.yaml").read_text())
    edit(pack)
    (clients / "meridian" / "pack.yaml").write_text(yaml.safe_dump(pack, sort_keys=False, allow_unicode=True))
    (clients / "meridian" / "pack.lock").write_bytes((DEMO / "clients" / "meridian" / "pack.lock").read_bytes())
    return clients


def found(tmp_path: Path, edit) -> list[tuple[str, str, str]]:
    meta = packs.meta_model(DEMO / "meta-model.yaml")
    pack = packs.read(scratch(tmp_path, edit), "meridian")
    return [(f.rule, f.path, f.message) for f in checks.check(pack, meta, compiler.VERSION, emit.FILLS)]


def test_s30_refuses_a_mapping_to_an_iri_the_platform_does_not_preload(tmp_path):
    iso = "https://www.iso20022.org/standardsrepository/type/PortfolioValuation"
    got = found(tmp_path, lambda p: p["ontology_mappings"][0].update(iri=iso))
    assert [(r, path) for r, path, _ in got] == [("S30", "pack.yaml:ontology_mappings[0].iri")], got
    assert "not a concept the platform preloads" in got[0][2] and "platform update" in got[0][2]


@pytest.mark.parametrize("edit, rule, where, says", [
    (lambda p: p["ontology_mappings"][0].update(relation="sameAs"), "S01", "ontology_mappings[0].relation", ""),
    (lambda p: p["ontology_mappings"][0].pop("certified_by"), "S01", "ontology_mappings[0].certified_by", ""),
    (lambda p: p["ontology_mappings"][0].update(term="nope"), "S30", "ontology_mappings[0].term", "not a declared term"),
    (lambda p: p["ontology_mappings"][0].update(entity="wealth_account"), "S30", "ontology_mappings[0]", "maps one of"),
    (lambda p: p["ontology_mappings"][-1].update(policy="scd2-wealth-flow"), "S30", "ontology_mappings[12].policy",
     "restricted-data policy"),
    (lambda p: p["ontology_mappings"].append(dict(p["ontology_mappings"][0])), "S30", "ontology_mappings[13]", "again"),
    (lambda p: p.update(ontology=[{"id": "fibo-holding", "label": "x"}]), "S30", "ontology[0].id", "preloaded concept's id"),
    (lambda p: (p["manifest"].update(meta_model_version="1.2.0"), p["schema"].update(meta_model="1.2")), "S30",
     "ontology_mappings", "needs meta-model 1.3.0"),
])
def test_s30_holds_each_mapping_to_what_the_pack_declares(tmp_path, edit, rule, where, says):
    got = found(tmp_path, edit)
    assert any(r == rule and p == f"pack.yaml:{where}" and says in m for r, p, m in got), got


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The API on a scratch copy of the demo's build, as tests/integration/test_cross_unit.py serves it."""
    from fastapi.testclient import TestClient
    import kge.app as appmod
    from kge.services.identity.service import token_for
    from tests.scratch import profile_of, scratch_build
    build = scratch_build(tmp_path / "build")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(profile_of(build).run_dir(), who)}"}
        yield c
    appmod._engine.close() if appmod._engine else None


def test_the_map_of_nna_and_aum_lists_their_industry_standards(client):
    """The Map tab's industry standards: each concept, its relation, its standard and version, and its IRI. A term
    with no mapping lists none; the answer itself carries none."""
    who = client.as_("analyst_enterprise")
    nna = client.get("/map/Net New Assets", headers=who).json()["standards"]
    assert [(s["concept"], s["relation"], s["standard_name"]) for s in nna] == [
        ("fibo-cash-flow", "broadMatch", "Financial Industry Business Ontology (FIBO)"),
        ("fibo-monetary-amount", "broadMatch", "Financial Industry Business Ontology (FIBO)"),
        ("schemaorg-money-transfer", "relatedMatch", "Schema.org")]
    assert nna[0]["iri"] == f"{FIBO}FND/Accounting/CashFlows/CashFlow" and nna[0]["version"].startswith("master_2026Q2")
    aum = client.get("/map/Assets Under Management", headers=who).json()["standards"]
    assert {(s["concept"], s["relation"]) for s in aum} == {("fibo-market-value", "broadMatch"),
                                                            ("fibo-monetary-amount", "broadMatch"), ("fibo-holding", "relatedMatch")}
    assert client.get("/map/Revenue", headers=who).json()["standards"] == []
    answer = client.post("/ask", json={"question": "What is NNA?", "arm": "graph"}, headers=who).json()
    assert "OntologyConcept" not in str(answer) and "fibo" not in str(answer).lower(), "mappings reach no answer"
