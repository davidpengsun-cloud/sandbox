"""Onboarding's industry-ontology and governance sub-graphs (issue #57): a new unit's terms are mapped to the platform's
preloaded standards, and its personal columns drafted as candidate restricted-data policies.

    .venv/bin/python -m pytest tests/unit/test_onboard_standards.py -q

The standards step (kge/estate/draftflow.py standards) maps each drafted term only to a preloaded concept: on rules,
lexically (labels, aliases and definitions, against a documented threshold) and by one structural rule (a measure in a
currency is an amount of money); on a model, ranked and checked by code (an IRI the preload does not hold is refused and
retried). The governance step drafts each column the privacy detectors flag. Both are items like any other: accepted by
the business team, certified by the steward, and only then in the pack, the graph and the SQL gate.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kge.pipeline import sqlguard
from kge.compiler import checks, emit, pack as packs, standards
from kge.estate import ddl, draftflow, onboard
from kge.pipeline.profile import Profile

DEMO = Path(__file__).resolve().parents[2]
FIBO = "https://spec.edmcouncil.org/fibo/ontology/"
ASK = "Confirm there's no industry equivalent for {}, or name the nearest."


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    clients = tmp_path / "clients"
    (clients / "meridian").mkdir(parents=True)
    shutil.copy(DEMO / "clients/meridian/pack.yaml", clients / "meridian/pack.yaml")
    return Profile("meridian", "meridian", clients, tmp_path / "artifacts", tmp_path / "stores", tmp_path / "landing",
                   tmp_path / "warehouses", DEMO / "deploy/meridian/identities.yaml", None, tmp_path / "profiles.yaml")


def bancassurance(prof) -> dict:
    onboard.create(prof, "bancassurance", semantic_dir=DEMO / "examples/bancassurance-osi", label="Bancassurance",
                   as_="knowledge_engineer")
    return onboard.draft(prof, "bancassurance", "knowledge_engineer", "2026-10-19")


def by_kind(items: list[dict], kind: str) -> dict[str, dict]:
    return {x["id"]: x for x in items if x["kind"] == kind}


def test_a_new_unit_s_terms_get_candidate_mappings_to_preloaded_concepts_only(prof):
    """Premiums match no preloaded concept by name, so the steward is asked to confirm there is none or to name the
    nearest."""
    drafted = bancassurance(prof)
    mappings = by_kind(drafted["items"], "mapping")
    assert list(mappings) == ["ontology-written_premium-fibo-monetary-amount", "ontology-in_force_premium-fibo-monetary-amount"]
    m = mappings["ontology-written_premium-fibo-monetary-amount"]
    assert (m["term"], m["relation"], m["iri"], m["standard"], m["made_by"]) == (
        "written_premium", "broadMatch", f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount", "fibo", "rules")
    assert m["from"].startswith("structural: a figure in GBP is an amount of money")
    assert m["pack"] == {"ontology_mappings": [{"term": "written_premium", "iri": m["iri"], "relation": "broadMatch",
                                                "note": m["from"]}]}
    preload = standards.load()
    assert all(x["iri"] in preload.by_iri for x in mappings.values()), "only to preloaded concepts"
    unit = onboard.show(prof)["units"][0]
    asks = {r["source"]: r for r in unit["requests"] if r["sub_graph"] == "ontology"}
    assert {s: r["ask"] for s, r in asks.items()} == {"written_premium": ASK.format("Written Premium"),
                                                      "in_force_premium": ASK.format("In Force Premium")}
    assert [s["id"] for s in unit["standards"]] == ["fibo", "dpv"] and {s["preload"] for s in unit["standards"]} == {"1.0.0"}
    steps = [h for h in unit["history"] if h["step"] == "draft-standards"]
    assert len(steps) == 1 and steps[0]["detail"]["made_by"] == "rules" and steps[0]["detail"]["reason"] == \
        "no model configured for onboard"


def test_the_rule_path_drafts_identically_from_identical_inputs():
    subjects = [{"id": "market-value", "label": "Market Value", "aliases": [], "definition": "Value of a holding.",
                 "currency": "USD"}]
    one, two = draftflow.rule_standards(subjects, standards.load()), draftflow.rule_standards(subjects, standards.load())
    assert one == two and [(m["concept"], m["relation"]) for m in one["mappings"]] == [
        ("fibo-market-value", "closeMatch"), ("fibo-monetary-amount", "broadMatch")]


def preload_with(*extra: dict) -> standards.Preload:
    """The platform's preload with extra concepts, for the matching rules alone."""
    p = standards.load()
    concepts = {**p.concepts, **{c["id"]: c for c in extra}}
    return standards.Preload(p.version, p.standards, concepts, {c["iri"]: c for c in concepts.values()}, p.defaults)


AUM_CONCEPT = {"id": "fibo-assets-under-management", "standard": "fibo", "iri": f"{FIBO}X/AssetsUnderManagement",
               "label": "assets under management (AUM)", "definition": "market value of the assets a firm manages for clients",
               "definition_text": "verbatim", "sub_graph": "metrics", "source": "https://example.com/x"}


@pytest.mark.parametrize("subject, wanted", [
    ({"id": "mv", "label": "Market Value", "aliases": [], "definition": "x"}, [("fibo-market-value", "closeMatch")]),
    ({"id": "ncf", "label": "Net Cash Flow", "aliases": [], "definition": "x"}, [("fibo-cash-flow", "broadMatch")]),
    ({"id": "aua", "label": "Assets Under Advice", "aliases": ["AUA", "aua", "assets advised"],
      "definition": "assets on which the bank advises clients"}, []),
    ({"id": "policies", "label": "Policy Count", "aliases": [], "definition": "Number of insurance contracts in force"}, []),
])
def test_lexical_matching_keeps_wrong_concepts_out(subject, wanted):
    """A name that is the concept's label is close, never exact on rules, since exactMatch is transitive and the
    steward's; AUA shares half of AUM's label and no acronym, and a policy count shares only a word with a governance
    policy, so neither is a candidate."""
    got = draftflow.rule_standards([subject], preload_with(AUM_CONCEPT))
    assert [(m["concept"], m["relation"]) for m in got["mappings"]] == wanted
    assert bool(got["gaps"]) == (not wanted)
    assert not [m for m in got["mappings"] if m["relation"] == "exactMatch"]


def test_assets_under_management_matches_its_own_concept_and_never_exactly():
    aum = {"id": "aum", "label": "Assets Under Management", "aliases": ["AUM"], "definition": "x"}
    got = draftflow.rule_standards([aum], preload_with(AUM_CONCEPT))["mappings"]
    assert [(m["concept"], m["relation"]) for m in got] == [("fibo-assets-under-management", "closeMatch")], \
        "the concept's label less its abbreviation is the term's name: close, and exactMatch left to the steward"


class Stub:
    """A model that answers the standards step with the replies given, in turn, and the last again."""
    name, tier, mode = "stub:stub", None, None

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def json(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


SUBJECTS = [{"id": "written_premium", "label": "Written Premium", "aliases": [], "definition": "Premium written.", "currency": "GBP"}]


def test_a_model_s_iri_outside_the_preload_is_refused_and_retried_with_the_error():
    """The preload is the platform's: listed to the model, never fetched."""
    bad = {"mappings": [{"term": "written_premium", "iri": f"{FIBO}FND/Insurance/Premium", "relation": "broadMatch", "why": "x"}]}
    good = {"mappings": [{"term": "written_premium", "iri": f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount",
                          "relation": "broadMatch", "why": "A premium is an amount of money."}]}
    llm = Stub(bad, good)
    got = draftflow.standards(SUBJECTS, "bancassurance", llm=llm)
    assert got["outcome"]["made_by"] == "model" and got["outcome"]["tries"] == 2
    assert "is not one of the platform's preloaded concepts" in got["outcome"]["reason"]
    assert "is not one of the platform's preloaded concepts" in llm.prompts[1] and draftflow.FEEDBACK in llm.prompts[1]
    assert [(m["concept"], m["made_by"], m["model"]) for m in got["mappings"]] == [("fibo-monetary-amount", "model", "stub:stub")]
    assert got["gaps"] == []
    assert f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount | monetary amount" in llm.prompts[0]


@pytest.mark.parametrize("row, says", [
    ({"term": "written_premium", "iri": f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount", "relation": "exactMatch",
      "why": "x"}, "exactMatch is a steward's to assert"),
    ({"term": "written_premium", "iri": f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount", "relation": "sameAs",
      "why": "x"}, "must be closeMatch"),
    ({"term": "nope", "iri": f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount", "relation": "broadMatch", "why": "x"},
     "not a drafted term"),
    ({"term": "written_premium", "iri": f"{FIBO}FND/Accounting/CurrencyAmount/MonetaryAmount", "relation": "broadMatch",
      "why": "two\nlines"}, "one line"),
])
def test_the_model_s_reply_is_held_to_the_skos_relations_and_the_drafted_terms(row, says):
    with pytest.raises(ValueError, match=says):
        draftflow.check_standards({"mappings": [row]}, SUBJECTS, standards.load())


def test_a_model_that_never_names_a_preloaded_concept_falls_back_to_rules():
    bad = {"mappings": [{"term": "written_premium", "iri": "https://example.com/nope", "relation": "broadMatch", "why": "x"}]}
    got = draftflow.standards(SUBJECTS, "bancassurance", llm=Stub(bad))
    assert got["outcome"]["made_by"] == "rules" and got["outcome"]["reason"].startswith("retries exhausted after 3 replies")
    assert [m["concept"] for m in got["mappings"]] == ["fibo-monetary-amount"] and got["mappings"][0]["made_by"] == "rules"


def test_the_mock_model_s_scripted_mistake_is_caught_and_corrected(prof, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    drafted = bancassurance(prof)
    step = next(h for h in onboard.show(prof)["units"][0]["history"] if h["step"] == "draft-standards")["detail"]
    assert (step["made_by"], step["model"], step["tries"]) == ("model", "mock:mock", 2)
    assert "https://example.com/ontology/not-preloaded" in step["reason"]
    assert {(x["id"], x["made_by"]) for x in by_kind(drafted["items"], "mapping").values()} == {
        ("ontology-written_premium-fibo-monetary-amount", "model"), ("ontology-in_force_premium-fibo-monetary-amount", "model")}


def test_a_personal_column_is_a_candidate_policy_mapped_to_the_privacy_vocabulary(prof):
    drafted = bancassurance(prof)
    policies = by_kind(drafted["items"], "policy")
    assert list(policies) == ["policy-policyholder-name"], "the holder's name; no key, status, date or amount"
    p = policies["policy-policyholder-name"]
    assert (p["column"], p["family"], p["made_by"]) == ("bancassurance.policyholder.name", "personal_data:name", "rules")
    assert p["pack"] == {
        "policies.restricted_attributes": [{"id": "bancassurance-policyholder-name-personal-data", "action": "refuse",
                                            "cite": "POL-DATA-CLASS", "columns": ["bancassurance.policyholder.name"]}],
        "ontology_mappings": [{"policy": "bancassurance-policyholder-name-personal-data", "iri": "https://w3id.org/dpv#PersonalData",
                               "relation": "broadMatch", "note": p["pack"]["ontology_mappings"][0]["note"]}]}


def test_uncertified_mappings_and_policies_never_reach_the_pack_and_certified_ones_reach_the_gate(prof):
    bancassurance(prof)
    onboard.accept(prof, "bancassurance", "all", "business_team", "2026-10-19")
    accepted = packs.read(prof.clients, prof.client, lock=False)
    assert not accepted.findings
    assert not [m for m in accepted.data["ontology_mappings"] if m.get("term") in ("written_premium", "in_force_premium")]
    restricted = {c for r in accepted.data["policies"]["restricted_attributes"] for c in r["columns"]}
    assert "bancassurance.policyholder.name" not in restricted, "accepted alone: not a policy yet"
    unit = onboard.show(prof)["units"][0]
    assert unit["progress"]["ontology"] == {"items": 2, "accepted": 2, "certified": 0}
    assert unit["progress"]["governance"] == {"items": 1, "accepted": 1, "certified": 0}

    onboard.certify(prof, "bancassurance", "all", "steward_glossary", "2026-10-19")
    pack = packs.read(prof.clients, prof.client, lock=False)
    assert not pack.findings and not checks.ontology_mappings(pack)
    mine = [m for m in pack.data["ontology_mappings"] if m.get("term", "").endswith("premium") or
            str(m.get("policy", "")).startswith("bancassurance-")]
    assert {(m.get("term") or m.get("policy"), m["relation"], m["certified_by"], m["certified_on"]) for m in mine} == {
        ("written_premium", "broadMatch", "steward_glossary", "2026-10-19"),
        ("in_force_premium", "broadMatch", "steward_glossary", "2026-10-19"),
        ("bancassurance-policyholder-name-personal-data", "broadMatch", "steward_glossary", "2026-10-19")}
    edges = emit.mapped_to(pack.data, None)
    assert ("Policy", "OntologyConcept", "bancassurance-policyholder-name-personal-data", "dpv-personal-data") in [e[:4] for e in edges]
    assert ("Term", "OntologyConcept", "Written Premium", "fibo-monetary-amount") in [e[:4] for e in edges]
    governed = [e for e in emit.governs(pack.data, None) if e[3] == "bancassurance.policyholder.name"]
    assert governed and governed[0][2] == "bancassurance-policyholder-name-personal-data"
    assert [e[4]["ordinal"] for e in emit.governs(pack.data, None)][:7] == list(range(1, 8)), "Meridian's columns keep their places"
    model = ddl.parse((prof.clients / prof.client / "units/bancassurance/schema.sql").read_text(), "bancassurance")
    from kge.adapters.warehouse import estate_of
    con = estate_of(prof).open("bancassurance", onboard.ensure_warehouse(prof, "bancassurance"))
    try:
        cat = ddl.catalogue(con, model)
    finally:
        con.close()
    allow = onboard._golden_allowlist(pack.data, cat, "bancassurance")
    assert sqlguard.check("SELECT name FROM policyholder", allow)
    assert not sqlguard.check("SELECT count(policyholder_id) FROM policyholder", allow)
    unit = onboard.show(prof)["units"][0]
    assert unit["progress"]["ontology"] == {"items": 2, "accepted": 2, "certified": 2}
    assert unit["progress"]["governance"] == {"items": 1, "accepted": 1, "certified": 1}
    assert unit["items_by_sub_graph"]["ontology"] == ["ontology-written_premium-fibo-monetary-amount",
                                                      "ontology-in_force_premium-fibo-monetary-amount"]
    assert unit["items_by_sub_graph"]["governance"] == ["policy-policyholder-name"]
    assert not [r for r in unit["requests"] if r["sub_graph"] == "ontology"], "certifying a term's mapping answers its request"


def test_a_unit_maps_only_its_own_terms_and_policies(prof):
    bancassurance(prof)
    record = prof.clients / prof.client / "units/bancassurance/draft.yaml"
    import yaml
    draft = yaml.safe_load(record.read_text())
    item = next(x for x in draft["items"] if x["kind"] == "mapping")
    item["pack"]["ontology_mappings"][0]["term"] = "net-new-assets"
    item.update(accepted_by="business_team", accepted_on="2026-10-19", certified_by="steward_glossary", certified_on="2026-10-19")
    record.write_text(yaml.safe_dump(draft, sort_keys=False))
    found = packs.read(prof.clients, prof.client, lock=False).findings
    assert any("must belong to unit bancassurance" in str(f) for f in found), found


def test_the_rule_path_drafts_ddl_units_too(prof):
    onboard.create(prof, "leasing", DEMO / "examples/leasing/schema.sql", "Asset Finance", as_="knowledge_engineer")
    drafted = onboard.draft(prof, "leasing", "knowledge_engineer", "2026-10-19")
    assert [x["id"] for x in drafted["items"] if x["kind"] in ("mapping", "policy")] == [
        "ontology-lease-rentals-fibo-monetary-amount"], "lessee, lease and rental hold no personal column"


def test_the_standards_step_is_a_checkpointed_node_whose_finished_thread_is_dropped(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    saver = InMemorySaver()
    got = draftflow.standards(SUBJECTS, "bancassurance", checkpointer=saver)
    assert got["outcome"] == {"node": "standards", "made_by": "rules", "model": None, "tries": 0,
                              "reason": "no model configured for onboard"}
    assert list(saver.list(None)) == [], "a finished run's checkpoints are dropped, as the drafting flow's are"
