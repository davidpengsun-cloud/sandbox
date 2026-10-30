"""Meta-model 1.1.0, the query-semantics layer, and Meridian's pack 1.1.0 (#39 item 7c, Option B).

    .venv/bin/python -m pytest tests/unit/test_query_semantics.py -q

Meta-model 1.1.0 only adds to 1.0.0 (tests/fixtures/meta-model-1.0.0.yaml, as
item 2 wrote it): Context, Filter, RowSelector, Join (loaded as JoinRule: JOIN
is a keyword to both stores' Cypher), Acronym, Entity, Relation,
OntologyConcept and Golden, which carries ids only; a curated-source type with
its own table edge, so no query of the units sees it; and their edges, with
JOIN_FROM, JOIN_TO, REL_FROM and REL_TO for the logical FROM and TO. Policy
kinds scd2 and release_driver carry time semantics.

Meridian's pack 1.1.0 fills them for the Wealth expansion and the client
cross-reference: the cross-reference as a curated source, its catalogue in the
lock; eight glossary terms (AUM added since, by pack 1.3.0: issue #40's pack-only test);
acronyms; the contexts nna and nna_client_directed; the filters, row selectors
and declared joins they use; entities, a relation, lineage, one column not
joinable; aliases for metric variants, time modes, the latest month, group-bys
and entity ids; and retail-wealth's no-join fact replaced by a certified join
fact, the other five kept. Harvested foreign keys still load as JOINS, and the
compiler lists each one no declared join covers as a candidate. New checks
(S23 to S28) hold each new section to the lock and to the rest of the pack.
The 1.0.0 pack (tests/fixtures/meridian-pack-1.0.0.yaml) still compiles
against today's meta-model (1.2.0 since #39 item 11), and today's pack holds
every row 1.0.0 held, but for the no-join fact Option B replaced, the column the
new measure is on and the restricted attributes' policy.

Compiles into temporary folders, from the committed pack, the 1.0.0 pack and
scratch copies; one test loads an artifact into a throwaway graph, and two read
the demo's graph: run the suite once per store.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from kge.estate import names, sage
from tests.unit.test_spec_port import physical, sage_restricted
from tests.unit.test_compiler import (CLIENT, CLIENTS, DEMO, committed, compile_args, compile_in, findings, load, manifest_of,
                                 printed, rows, run, scratch, table)

FIXTURES = DEMO / "tests" / "fixtures"
META_1_0 = FIXTURES / "meta-model-1.0.0.yaml"
PACK_1_0 = FIXTURES / "meridian-pack-1.0.0.yaml"
WEALTH_BEFORE_1_5_0 = json.loads((FIXTURES / "wealth-lock-36bc02f.json").read_text())["wealth"]
NEW_NODES = {"CuratedSource": "CuratedSource", "Context": "Context", "Filter": "Filter", "RowSelector": "RowSelector",
             "Join": "JoinRule", "Acronym": "Acronym", "Entity": "Entity", "Relation": "Relation", "OntologyConcept": "OntologyConcept",
             "Golden": "Golden"}
NEW_EDGES = {"HAS_CURATED_TABLE": (["CuratedSource"], ["Table"]), "RESOLVES_TO": (["Term"], ["Context"]),
             "SPECIALIZES": (["Context"], ["Measure"]), "USES_FACT": (["Context"], ["Table"]), "CERTIFIED_BY": (["Context"], ["Golden"]),
             "REQUIRES_FILTER": (["Context"], ["Filter"]), "SELECTS_ROWS_WITH": (["Context"], ["RowSelector"]),
             "TARGETS": (["Filter", "RowSelector"], ["Table"]), "CONFLICTS_WITH": (["Filter", "RowSelector"], ["Filter", "RowSelector"]),
             "REQUIRES_JOIN": (["Context"], ["Join"]), "JOIN_FROM": (["Join"], ["Table"]), "JOIN_TO": (["Join"], ["Table"]),
             "IMPLEMENTS": (["Join"], ["Relation"]), "ABBREVIATED_AS": (["Term"], ["Acronym"]), "SPELLED_WITH": (["Column"], ["Acronym"]),
             "REPRESENTS": (["Table"], ["Entity"]), "IDENTIFIED_BY": (["Entity"], ["Column"]), "REL_FROM": (["Relation"], ["Entity"]),
             "REL_TO": (["Relation"], ["Entity"]), "MAPPED_TO": (["Entity", "Term", "Policy"], ["OntologyConcept"]),
             "DERIVED_FROM": (["Table"], ["Table"]), "NOT_JOINABLE_TO": (["Column"], ["Table"]), "CERTIFIED_JOIN": (["Source"], ["Source"])}
NEW_TERMS = {"net-new-assets": "Net New Assets", "client-directed-nna": "Client-Directed NNA", "test-account": "Test Account",
             "nna-eligibility": "NNA Eligibility", "exclusion": "Exclusion", "month-end-release-freeze": "Month-End Release / Freeze",
             "source-system": "Source System", "division": "Division"}
JOINS = ["flow_month_end", "sage_account_client_code", "sage_account_office", "flow_account", "account_household", "household_advisor",
         "advisor_office", "office_region", "region_division", "aum_month_end_rollup", "aum_household", "client_code_segment",
         "segment_parent", "flow_nna_reference", "client_xref"]
SINCE_1_5_0 = ("period_bindings", "measures", "mappings", "acronyms", "entities", "lineage", "filters", "joins")


def renamed(ref: str) -> str:
    """A pack's reference to a Wealth table or column as 1.1.0 wrote it, under the physical name 1.5.0 gives it
    (kge/estate/names.py): a kept table's and its columns', or a dropped table's business name, SAGE's table for it."""
    unit, _, rest = ref.partition(".")
    if unit != "wealth":
        return ref
    table, _, column = rest.partition(".")
    if column:
        return f"wealth.{names.table(table)}.{names.column(table, column)}"
    return f"wealth.{names.table(table)}"
OTHER_PAIRS = [["retail", "sme"], ["retail", "corporate"], ["sme", "corporate"], ["sme", "wealth"], ["corporate", "wealth"]]
CERTIFIED_JOIN = {"between": ["retail", "wealth"], "join": "client_xref", "asserted_by": "Data Architecture Board",
                  "asserted_on": "2026-10-10", "note": "certified client cross-reference"}


@pytest.fixture(scope="module")
def pack() -> dict:
    return committed()[0]


@pytest.fixture(scope="module")
def artifact(tmp_path_factory) -> Path:
    return printed(run(*compile_args(tmp_path_factory.mktemp("artifacts"))))


def by_id(entries: list[dict], key: str = "id") -> dict[str, dict]:
    return {e[key]: e for e in entries}


def csv_rows(folder: Path, path: str) -> list[dict]:
    header, *body = rows(folder, path)
    return [dict(zip(header, r)) for r in body]


def test_meta_model_1_1_0_only_adds_to_1_0_0():
    """Every type 1.0.0 had is in 1.1.0 as it was: names, key, layer, origin, properties in order. A rel group may gain
    ends (ALIAS_OF reaches a column and an entity too); nothing is renamed, retyped or taken away."""
    old, new = yaml.safe_load(META_1_0.read_text()), yaml.safe_load((FIXTURES / "meta-model-1.1.0.yaml").read_text())
    assert (old["version"], new["version"]) == ("1.0.0", "1.1.0")
    for kind in ("node_types", "edge_types"):
        assert list(new[kind])[:len(old[kind])] == list(old[kind]), f"{kind}: the 1.0.0 types first, in their order"
        for name, t in old[kind].items():
            n = new[kind][name]
            for k in t:
                if k in ("from", "to") and isinstance(n[k], list):
                    ends = t[k] if isinstance(t[k], list) else [t[k]]
                    assert n[k][:len(ends)] == ends, (name, k)
                elif k != "properties":
                    assert n[k] == t[k], (name, k)
            assert list(n["properties"] or {})[:len(t["properties"] or {})] == list(t["properties"] or {}), name
            assert all((n["properties"] or {})[p] == v for p, v in (t["properties"] or {}).items()), name
    assert new["edge_types"]["ALIAS_OF"]["to"] == ["Term", "Source", "Column", "Entity"]


def test_the_query_semantics_types_and_edges():
    from kge.graph import schema
    meta = schema.meta_model()
    nodes, edges = meta["node_types"], meta["edge_types"]
    assert {n: nodes[n]["loaded_as"] for n in NEW_NODES} == NEW_NODES, "Join is loaded as JoinRule: JOIN is a Cypher keyword"
    assert {n: (schema.ends(edges[n]["from"]), schema.ends(edges[n]["to"])) for n in NEW_EDGES} == NEW_EDGES
    assert all(nodes[n]["origin"] == "pack" and nodes[n]["layer"] == "curated" for n in NEW_NODES)
    assert list(nodes["Golden"]["properties"]) == ["id", "item", "set", "version"], "a golden item's ids only: no SQL, figure or question"
    assert list(nodes["Join"]["properties"]) == ["id", "kind", "condition", "alias", "through"]
    assert {e: edges[e].get("logical") for e in ("JOIN_FROM", "JOIN_TO", "REL_FROM", "REL_TO")} == {
        "JOIN_FROM": "FROM", "JOIN_TO": "TO", "REL_FROM": "FROM", "REL_TO": "TO"}
    assert edges["REQUIRES_JOIN"]["properties"] == {"ordinal": "INT64", "required": "BOOLEAN"}
    assert edges["CERTIFIED_JOIN"]["properties"] == {"asserted_by": "STRING", "asserted_on": "STRING", "note": "STRING",
                                                     "declared_join": "STRING"}
    assert edges["HAS_CURATED_TABLE"]["origin"] == "lock" and edges["HAS_TABLE"]["from"] == "Source"
    for kind in ("scd2", "release_driver"):
        assert kind in (DEMO / "meta-model.yaml").read_text(), f"Policy kind {kind}"


def test_the_pack_is_written_for_the_meta_model_since_1_1_0(pack):
    """1.1.0 filled the query-semantics layer for meta-model 1.1.0; 1.2.0, for meta-model 1.2.0, adds the parser's
    vocabulary (tests/integration/test_vocabulary.py) and changes none of it; 1.3.0, at the same meta-model, adds assets under
    management (issue #40's pack-only onboarding test) and changes nothing else except issue #52's business-team role,
    added without a pack version bump by the owner's decision; 1.4.0, for meta-model 1.3.0, adds only its mappings to the
    platform's preloaded standards (ontology_mappings, tests/unit/test_standards.py); 1.5.0, at the same meta-model, takes
    in the SAGE wealth data: SAGE's tables and columns under their physical names, Meridian's kept Wealth tables under
    physical names in SAGE's style, every Wealth name a business name in the curated layer (entities, column aliases), and
    the sections that read Wealth's columns (SINCE_1_5_0, the contexts' filters, row selectors and policies) reading
    them; its mappings, period bindings and lineage are 1.1.0's renamed (renamed), but net new assets' measure and
    period, on SAGE's TRAN_FACT_1, and AUM's, 1.3.0's."""
    m = pack["manifest"]
    assert (m["client"], m["meta_model_version"], m["version"]) == ("meridian", "1.3.0", "1.5.0")
    assert m["platform"] == {"meta_model": "^1.3", "compiler": ">=0.4"} and pack["schema"] == {"meta_model": "1.3", "extensions": []}
    then = yaml.safe_load((FIXTURES / "meridian-pack-1.1.0.yaml").read_text())
    since = ("manifest", "schema", "terminology", "row_selectors", "contexts", "policies", "ontology_mappings", *SINCE_1_5_0)
    aum = {"assets-under-management", "wealth.assets-under-management", "AUM", "aum_month_end_rollup", "aum_household"}

    def before_aum(k: str, v):
        if k == "sources":
            return [{**x, "entity_key": "household_id"} if x["id"] == "wealth" and x["entity_key"] == names.column("household", "household_id")
                    else x for x in v]
        if k == "period_bindings":
            return [b for b in v if b["table"] != "wealth.aum_month_end"]
        if k == "access":
            return {**v, "roles": [r for r in v["roles"] if r["id"] not in {"business_team", "knowledge_engineer"}]}
        if k in ("glossary", "measures", "acronyms", "joins"):
            return [x for x in v if x.get("id") not in aum
                    and not (k == "acronyms" and x.get("id") in {name for aliases in sage.ALIASES.values() for name in aliases.values()})
                    and not (k == "joins" and x.get("id", "").startswith("sage_"))]
        return v
    assert {k: before_aum(k, v) for k, v in pack.items() if k not in since} == {k: v for k, v in then.items() if k not in since}
    assert pack["mappings"] == [{**m, "column": renamed(m["column"])} for m in then["mappings"]]
    assert pack["lineage"] == [{**x, "table": renamed(x["table"]), "derived_from": [renamed(t) for t in x["derived_from"]]}
                               for x in then["lineage"]]
    old = {renamed(b["table"]): b for b in then["period_bindings"] if b["table"] != "wealth.flow"}
    now = {b["table"]: b for b in pack["period_bindings"] if b["table"] not in ("wealth.TRAN_FACT_1", "wealth.AUM_MNTH_END_FACT_1")}
    assert set(now) == set(old)
    for bound, b in now.items():
        if bound.startswith("wealth."):
            business = {p: n for n, (p, _) in names.MERIDIAN.items()}[bound.split(".")[1]]
            assert b["column"] == names.column(business, old[bound]["column"]), b
        else:
            assert b == old[bound], b
    (was,) = then["policies"]["restricted_attributes"]
    assert pack["policies"]["restricted_attributes"] == [
        {**was, "version": "1.2.0",
         "columns": [".".join(parts) for parts in sage_restricted(physical([name.split(".") for name in was["columns"]]))],
         "phrases": [
            {"column": "retail.customer.date_of_birth",
             "phrases": ["birth", "birthdate", "birthday", "born", "age", "aged", "older than", "younger than"]},
            {"column": "retail.customer.email", "phrases": ["e-mail"]},
            {"column": "retail.customer.first_name", "phrases": ["customer name", "client name", "their name"]}]}]
    assert pack["row_selectors"][2:] == [{"id": "banks_with_retail", "label": "Banks with Retail: an open Retail account", "table": "retail.account",
                                       "mode": "include", "predicate": "account.customer_id = customer.customer_id AND account.status_code = 'OPEN'"}]
    assert [c["id"] for c in pack["contexts"][:-1]] == [c["id"] for c in then["contexts"]]
    aliases = [a for a in pack["terminology"]["aliases"] if a.get("term") != "assets-under-management" and a["phrase"] != "deposits"
               and "column" not in a]
    assert [a["phrase"] for a in aliases[:40]] == [a["phrase"] for a in then["terminology"]["aliases"]]
    assert [a for a in aliases[:40] if "group_by" not in a] == [a for a in then["terminology"]["aliases"] if "group_by" not in a]
    assert aliases[40:42] == [{"phrase": "account", "entity": "wealth_account"}, {"phrase": "excluded", "term": "exclusion"}], \
        "since the review round: an account counted by its whole key (W05), and the exclusion a question names (W08)"
    assert aliases[42:] == [{"phrase": p, "term": "month-end-release-freeze"} for p in (
        "month-end release", "month-end freeze", "month end release", "month end freeze")], \
        "since round 2: either half of Month-End Release / Freeze's name, which no question says whole"


def test_the_cross_reference_is_a_curated_source_and_no_unit(pack):
    """It lives with the curated layer, not inside either unit's harvest: a source of its own kind, its catalogue
    locked beside the units'."""
    assert pack["curated_sources"] == [{"id": "xref", "label": "Certified client cross-reference",
                                        "connection": {"dialect": "duckdb", "ref": "warehouses/xref.duckdb"}, "harvest": True}]
    assert "xref" not in {s["id"] for s in pack["sources"]}
    lock = committed()[1]
    assert list(lock["sources"]) == ["corporate", "retail", "sme", "wealth", "xref"]
    assert [t["name"] for t in lock["sources"]["xref"]["tables"]] == ["client_xref"]


def test_retail_wealth_s_no_join_fact_is_replaced_by_a_certified_join_and_the_other_five_stay(pack):
    """The #39 Option B update: replaced, not deleted silently; all other pairs keep their no-join rules."""
    assert [f["between"] for f in pack["no_join"]] == OTHER_PAIRS
    assert pack["certified_joins"] == [CERTIFIED_JOIN]
    assert by_id(pack["joins"])["client_xref"]["kind"] == "cross_reference"
    text = (CLIENTS / CLIENT / "pack.yaml").read_text()
    assert "replaces the no-join fact" in " ".join(re.sub(r"\n\s*#", " ", text).split()), "the pack says what the certified join replaced"
    (refined,) = pack["not_joinable"]
    assert (refined["column"], refined["table"]) == ("corporate.contact.customer_id", "retail.customer")


def test_the_glossary_s_wealth_additions_and_aum_added_by_a_pack_edit(pack):
    """Deposits are Retail's, so a question pairing them with AUM names both units, through the certified join."""
    terms = {g["id"]: g for g in pack["glossary"]}
    assert len(terms) == 49 and {i: terms[i]["name"] for i in NEW_TERMS} == NEW_TERMS
    assert list(terms)[-9:-1] == list(NEW_TERMS), "appended after the forty"
    assert all(terms[i]["status"] == "certified" and terms[i]["domain"] == "Wealth" and "defined_by" not in terms[i] for i in NEW_TERMS)
    aum = terms["assets-under-management"]
    assert list(terms)[-1] == "assets-under-management" and aum["name"] == "Assets Under Management"
    assert (aum["status"], aum["domain"], aum["steward"]) == ("certified", "Wealth", "Wealth Data Steward") and "defined_by" not in aum
    assert pack["manifest"]["version"] == "1.5.0" and pack["manifest"]["meta_model_version"] == "1.3.0"
    aliases = pack["terminology"]["aliases"]
    assert {"phrase": "aum", "term": "assets-under-management"} in aliases
    assert {"phrase": "assets under management", "term": "assets-under-management"} in aliases
    assert aliases.index({"phrase": "deposits", "source": "retail"}) == aliases.index({"phrase": "advisory", "source": "wealth"}) + 1
    assert {"table": "wealth.AUM_MNTH_END_FACT_1", "column": "CLDR_EOM_DT", "kind": "snapshot", "calendar": "calendar"} in pack["period_bindings"]
    m = by_id(pack["measures"])["wealth.assets-under-management"]
    assert (m["term"], m["column"], m["expression"], m["aggregation"], m["grain"], m["currency"], m["calendar"], m["conversion"]) == \
        ("assets-under-management", "wealth.AUM_MNTH_END_FACT_1.AUM_AM", "sum(AUM_MNTH_END_FACT_1.AUM_AM)", "sum", "quarter_end", "USD",
         "fiscal_april", "none")
    assert not [x for x in pack["mappings"] if x.get("term") == "assets-under-management"], "certified through its context, as NNA"
    joins = by_id(pack["joins"])
    assert (joins["aum_month_end_rollup"]["kind"], joins["aum_month_end_rollup"]["to"]) == ("calendar_rollup", "wealth.MONTH_DIM_1")
    assert joins["aum_household"]["kind"] == "key"
    assert joins["aum_household"]["to"] == "wealth.HSHLD_DIM_1"


def test_acronyms_entities_relation_and_lineage(pack):
    """Meridian declares no ontology concept of its own: since pack 1.4.0 it maps its terms, an entity and a policy to
    the platform's preloaded concepts (tests/unit/test_standards.py)."""
    acr = by_id(pack["acronyms"])
    assert list(acr)[:6] == ["NNA", "AUA", "AUM", "NNA_ELIG_FLG", "SRC_SYS_CD", "CLNT_DIR_IND"]
    assert set(list(acr)[6:]) == {name for aliases in sage.ALIASES.values() for name in aliases.values()}
    assert all(acr[a]["columns"] and all(c.split(".")[-1] == a for c in acr[a]["columns"]) for a in list(acr)[6:]), \
        "SAGE's physical names, each an acronym of the columns it is the name of"
    assert acr["TRAN_AM"]["columns"] == ["wealth.TRAN_FACT_1.TRAN_AM"]
    assert acr["AUM"] == {"id": "AUM", "expansion": "Assets Under Management", "terms": ["assets-under-management"]}
    assert acr["NNA_ELIG_FLG"]["columns"] == ["wealth.TRAN_FACT_1.NNA_IN"]
    assert acr["CLNT_DIR_IND"]["columns"] == ["wealth.F_NNA_FIN.CLIENT_DIRECTED_NNA_IN"]
    assert "wealth.TRAN_FACT_1.ACCT_SRCE_SYS_ID" in acr["SRC_SYS_CD"]["columns"] and acr["NNA"]["terms"] == ["net-new-assets"]
    ents = by_id(pack["entities"])
    assert ents["wealth_account"] == {"id": "wealth_account", "label": "Wealth account", "table": "wealth.ACCT_DIM2_1",
                                      "key": ["DW_ACCT_ID", "SRCE_SYS_ID"]}, "the account's composite key"
    assert {e["table"] for e in ents.values() if e["table"].startswith("wealth.")} == {f"wealth.{t}" for t in names.tables().values()}, \
        "every Wealth table represents the entity its business name names"
    for s in pack["sources"]:
        assert any(e["table"].startswith(f"{s['id']}.") and e["key"] == [s["entity_key"]] for e in ents.values()), s["id"]
    assert pack["relations"] == [{"id": "same_client", "label": "Same client", "from": "retail_customer", "to": "wealth_household"}]
    assert [(x["table"], x["derived_from"]) for x in pack["lineage"]] == [("wealth.AUM_MNTH_END_FACT_1", ["wealth.HLDG_FACT_1"])]
    assert "ontology" not in pack and len(pack["ontology_mappings"]) == 13


def test_the_contexts_and_what_they_apply(pack):
    ctx = by_id(pack["contexts"])
    assert list(ctx) == ["nna", "nna_client_directed", "aum"]
    aum = ctx["aum"]
    assert (aum["term"], aum["measure"], aum["fact"]) == ("assets-under-management", "wealth.assets-under-management", "wealth.AUM_MNTH_END_FACT_1")
    assert (aum["filters"], aum["joins"], aum["golden"]) == (["released"], ["aum_month_end_rollup"], {"set": "golden_wealth", "items": ["W16"]})
    assert aum["reaches"] == ["aum_household", "household_advisor", "advisor_office", "office_region", "region_division", "client_xref"]
    nna, directed = ctx["nna"], ctx["nna_client_directed"]
    assert (nna["term"], nna["measure"], nna["fact"]) == ("net-new-assets", "wealth.net-new-assets", "wealth.TRAN_FACT_1")
    assert (directed["term"], directed["measure"], directed["fact"]) == ("client-directed-nna", "wealth.net-new-assets", "wealth.TRAN_FACT_1")
    assert nna["filters"] == ["eligible", "released"] and directed["filters"] == ["eligible", "released", "client_directed"]
    assert nna["row_selectors"] == directed["row_selectors"] == ["not_test_account", "not_excluded", "banks_with_retail"]
    assert nna["joins"] == ["flow_month_end"] and directed["joins"] == ["flow_month_end", "flow_nna_reference"]
    assert "client_xref" in nna["reaches"] and nna["reaches"] == directed["reaches"]
    assert nna["golden"]["set"] == "golden_wealth" and directed["golden"]["items"] == ["W02"]
    assert {"W01", "W03", "W09", "W10", "W12", "W13", "XB3", "XB4"} <= set(nna["golden"]["items"])
    sets = {s["name"]: s for s in pack["tests"]["sets"]}
    assert sets["golden_wealth"]["version"] == "v1"
    assert sets["golden_wealth"]["items"] == [f"W{n:02d}" for n in range(1, 21)] + [f"XB{n}" for n in range(1, 7)] + [
        "XB1-R", "XB3-W", "XB-DOB", "XB-EMAIL"], "and the refusals the owner names beside XB6 (#39 item 7e)"
    f, s = by_id(pack["filters"]), by_id(pack["row_selectors"])
    assert {k: (v["table"], v["predicate"]) for k, v in f.items()} == {
        "eligible": ("wealth.TRAN_FACT_1", "TRAN_FACT_1.NNA_IN = 'Y'"),
        "released": ("wealth.MONTH_DIM_1", "MONTH_DIM_1.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0"),
        "client_directed": ("wealth.F_NNA_FIN", "F_NNA_FIN.CLIENT_DIRECTED_NNA_IN = 'Y'")}
    assert (s["not_test_account"]["table"], s["not_excluded"]["table"]) == ("wealth.ACCT_DIM2_1", "wealth.NNA_EXCL_1")
    assert (s["banks_with_retail"]["table"], s["banks_with_retail"]["mode"]) == ("retail.account", "include"), "the far side's, XB3's"
    assert f["eligible"]["conflicts_with"] == ["not_excluded"] and s["not_excluded"]["conflicts_with"] == ["eligible"]
    measure = by_id(pack["measures"])["wealth.net-new-assets"]
    assert (measure["column"], measure["expression"], measure["term"]) == (
        "wealth.TRAN_FACT_1.TRAN_AM", "sum(CASE WHEN TRAN_FACT_1.DW_TRAN_DIRCTN_CD = 'O' THEN -TRAN_FACT_1.TRAN_AM ELSE TRAN_FACT_1.TRAN_AM END)",
        "net-new-assets"), "a magnitude signed by its direction"


def test_the_declared_joins(pack):
    j = by_id(pack["joins"])
    assert list(j) == JOINS
    assert j["flow_account"]["condition"] == ("TRAN_FACT_1.DW_ACCT_ID = ACCT_DIM2_1.DW_ACCT_ID AND TRAN_FACT_1.ACCT_SRCE_SYS_ID = "
                                             "ACCT_DIM2_1.SRCE_SYS_ID")
    assert j["flow_month_end"]["kind"] == "calendar_rollup" and j["segment_parent"]["alias"] == "parent_segment"
    xref = j["client_xref"]
    assert (xref["from"], xref["to"], xref["through"], xref["implements"]) == ("retail.customer", "wealth.HSHLD_DIM_1", "xref.client_xref",
                                                                                "same_client")
    assert "xref.client_xref.certification_status = 'CERTIFIED'" in xref["condition"]
    assert not re.search(r"date_of_birth|email", json.dumps(pack["joins"])), "a restricted column is never a match key"
    pol = pack["policies"]
    assert [p["table"] for p in pol["scd2"]] == ["wealth.ACCT_DIM2_1", "wealth.TRAN_FACT_1", "wealth.BRCH_DIM_1", "wealth.RGN_DIM_1",
                                                 "wealth.DIVSN_DIM_1", "xref.client_xref"]
    assert [p["table"] for p in pol["release_driver"]] == ["wealth.MONTH_DIM_1"]


def test_the_aliases_name_variants_time_modes_the_latest_month_group_bys_and_entities(pack):
    """And, since #39 item 7d, advisory as a word for Wealth, so the owner's cross-unit questions name both units; since the
    review round, the Wealth account as an entity a question counts, and excluded as a word for the exclusion; since round 2,
    either half of Month-End Release / Freeze's name, so a question on what it means names it; since pack 1.3.0 (issue
    #40's pack-only test), aum and assets under management, and deposits as a word for Retail."""
    aliases = [a for a in pack["terminology"]["aliases"] if "column" not in a]
    assert len(aliases) == 49 and [a["phrase"] for a in aliases[:26]] == [a["phrase"] for a in yaml.safe_load(PACK_1_0.read_text())
                                                                           ["terminology"]["aliases"]], "appended"
    kinds = {a["phrase"]: next(k for k in a if k != "phrase") for a in aliases[26:]}
    assert kinds == {"nna": "term", "client directed nna": "term", "aum": "term", "assets under management": "term", "as reported": "time_mode", "restated": "time_mode",
                     "latest month": "period", "by division": "group_by", "by region": "group_by", "by office": "group_by",
                     "by advisor office": "group_by", "by parent segment": "group_by", "by source system": "group_by",
                     "household": "entity", "customer": "entity", "advisory": "source", "deposits": "source", "account": "entity",
                     "excluded": "term",
                     "month-end release": "term", "month-end freeze": "term", "month end release": "term", "month end freeze": "term"}
    assert aliases[-8:-6] == [{"phrase": "advisory", "source": "wealth"}, {"phrase": "deposits", "source": "retail"}]


def test_the_artifact_holds_the_query_semantics_rows(artifact):
    man = manifest_of(artifact)
    nodes, edges = man["counts"]["nodes"], man["counts"]["edges"]
    assert {t: nodes[t] for t in NEW_NODES.values()} == {
        "CuratedSource": 1, "Context": 3, "Filter": 3, "RowSelector": 3, "JoinRule": 15, "Acronym": 26, "Entity": 35, "Relation": 1,
        "OntologyConcept": 23, "Golden": 17}
    assert edges["NO_JOIN"] == 5 and edges["CERTIFIED_JOIN"] == 1 and edges["HAS_TABLE"] == 59 and edges["HAS_CURATED_TABLE"] == 1
    assert csv_rows(artifact, "data/edges/CERTIFIED_JOIN__Unit__Unit.csv") == [
        {"from": "retail", "to": "wealth", "asserted_by": "Data Architecture Board", "asserted_on": "2026-10-10",
         "note": "certified client cross-reference", "declared_join": "client_xref"}]
    assert csv_rows(artifact, "data/edges/HAS_CURATED_TABLE__CuratedSource__Tbl.csv") == [{"from": "xref", "to": "xref.client_xref"}]
    golden = csv_rows(artifact, "data/nodes/Golden.csv")
    assert {g["id"] for g in golden} >= {"golden_wealth_v1:W01", "golden_wealth_v1:W02"} and all(set(g) == {"id", "item", "set", "version"}
                                                                                               for g in golden)
    conflicts = {(r["from"], r["to"]) for p in ("Filter__RowSelector", "RowSelector__Filter")
                 for r in csv_rows(artifact, f"data/edges/CONFLICTS_WITH__{p}.csv")}
    assert conflicts == {("eligible", "not_excluded"), ("not_excluded", "eligible")}
    req = {(r["from"], r["to"]): (r["ordinal"], r["required"]) for r in csv_rows(artifact, "data/edges/REQUIRES_JOIN__Context__JoinRule.csv")}
    assert req[("nna", "flow_month_end")] == ("1", "true") and req[("nna", "flow_account")] == ("2", "false")
    assert req[("nna", "client_xref")] == ("11", "false"), "the last of the context's reaches"
    joins = by_id(csv_rows(artifact, "data/nodes/JoinRule.csv"))
    assert joins["client_xref"]["through"] == "xref.client_xref" and joins["flow_account"]["through"] == "\\N"
    assert csv_rows(artifact, "data/edges/JOIN_FROM__JoinRule__Tbl.csv")[0] == {"from": "account_household", "to": "wealth.ACCT_DIM2_1"}
    ident = [(r["from"], r["to"], r["ordinal"]) for r in csv_rows(artifact, "data/edges/IDENTIFIED_BY__Entity__Col.csv")]
    assert ("wealth_account", "wealth.ACCT_DIM2_1.DW_ACCT_ID", "1") in ident and ("wealth_account", "wealth.ACCT_DIM2_1.SRCE_SYS_ID", "2") in ident
    assert csv_rows(artifact, "data/edges/NOT_JOINABLE_TO__Col__Tbl.csv")[0]["to"] == "retail.customer"
    assert csv_rows(artifact, "data/edges/DERIVED_FROM__Tbl__Tbl.csv")[0]["to"] == "wealth.HLDG_FACT_1"
    policies = {r["id"]: r for r in csv_rows(artifact, "data/nodes/Policy.csv")}
    assert sorted({r["kind"] for r in policies.values()}) == ["release_driver", "restricted_attribute", "scd2"]
    assert json.loads(policies["scd2-xref-client-xref"]["params"]) == {"table": "xref.client_xref", "valid_from": "valid_from",
                                                                       "valid_to": "valid_to", "is_current": "is_current",
                                                                       "valid_to_inclusive": True}
    governs = csv_rows(artifact, "data/edges/GOVERNS__Policy__Col.csv")
    assert [(r["to"], r["ordinal"]) for r in governs if r["from"] == "wealth-month-end-release"] == [
        ("wealth.MONTH_DIM_1.CLDR_EOM_DT", "1"), ("wealth.MONTH_DIM_1.DW_BRKRG_MNTH_FRM_TODAY_CT", "4"),
        ("wealth.MONTH_DIM_1.FIN_MTRC_RLSE_DT", "2"), ("wealth.MONTH_DIM_1.FIN_MTRC_RLSE_DT", "3")], \
        "SAGE records no freeze date: a month as reported is read as at its release date"
    resolves = {(r["from"], r["to"]) for r in csv_rows(artifact, "data/edges/RESOLVES_TO__Term__Context.csv")}
    assert resolves == {("Net New Assets", "nna"), ("Client-Directed NNA", "nna_client_directed"), ("Assets Under Management", "aum")}


def test_graph_model_yaml_maps_join_and_rel_ends_to_the_logical_from_and_to(artifact):
    gm = yaml.safe_load((artifact / "graph-model.yaml").read_text())
    assert gm["node_types"]["Join"]["loaded_as"] == "JoinRule"
    assert {e: gm["edge_types"][e]["logical"] for e in ("JOIN_FROM", "JOIN_TO", "REL_FROM", "REL_TO")} == {
        "JOIN_FROM": "FROM", "JOIN_TO": "TO", "REL_FROM": "FROM", "REL_TO": "TO"}
    assert "logical" not in gm["edge_types"]["HAS_TABLE"]


def test_a_harvested_foreign_key_no_declared_join_covers_is_a_candidate(tmp_path, artifact):
    """Harvest proposes and the pack declares: every foreign key still loads as JOINS (the 183 rely on them), and the
    compiler lists each no declared join covers, in graph-model.yaml and on stderr."""
    lock = committed()[1]
    fks = [f"{s}.{t['name']} ({', '.join(k['columns'])}) -> {s}.{k['ref_table']} ({', '.join(k['ref_columns'])})"
           for s, held in sorted(lock["sources"].items()) for t in held["tables"] for k in t["foreign_keys"]]
    covered = {"wealth.HSHLD_DIM_1 (PRIM_ADV_ID) -> wealth.ADV_DIM_1 (ADV_ID)",
               "wealth.AUM_MNTH_END_FACT_1 (HSHLD_DK) -> wealth.HSHLD_DIM_1 (HSHLD_DK)",
               "wealth.AUM_MNTH_END_FACT_1 (CLDR_EOM_DT) -> wealth.MONTH_DIM_1 (CLDR_EOM_DT)"}
    gm = yaml.safe_load((artifact / "graph-model.yaml").read_text())
    assert covered <= set(fks) and gm["join_candidates"] == [k for k in fks if k not in covered]
    assert manifest_of(artifact)["counts"]["edges"]["JOINS"] == len(fks) == 44
    r = run(*compile_args(tmp_path / "out"))
    assert f"compile: {len(fks) - len(covered)} of {len(fks)} harvested foreign keys no declared join covers" in r.stderr, r.stderr


def _composite(p: dict, l: dict) -> None:
    table(l, "wealth", "TRAN_FACT_1")["foreign_keys"].append({"columns": ["DW_ACCT_ID", "ACCT_SRCE_SYS_ID"], "ref_table": "ACCT_DIM2_1",
                                                              "ref_columns": ["DW_ACCT_ID", "SRCE_SYS_ID"]})


def test_a_composite_foreign_key_a_declared_join_covers_is_carried_by_the_join(tmp_path, capsys):
    """S05 reports a composite key until a typed join covers it (meta-model 1.1): flow_account does, so it compiles,
    with no JOINS edge for it, which holds one column pair. Without the join it is S05 again."""
    art = printed(run(*compile_args(tmp_path / "ok", scratch(tmp_path / "ok", _composite))))
    assert manifest_of(art)["counts"]["edges"]["JOINS"] == 44

    def uncovered(p, l):
        _composite(p, l)
        p["joins"] = [j for j in p["joins"] if j["id"] != "flow_account"]
        for c in p["contexts"]:
            c["reaches"] = [x for x in c["reaches"] if x != "flow_account"]
    code, _, err = compile_in(capsys, scratch(tmp_path / "no", uncovered), tmp_path / "out")
    assert code == 2 and ("S05", "pack.lock:sources.wealth.TRAN_FACT_1.foreign_keys[3]") in [(r, w) for r, w, _ in findings(err)], err


def _j(p: dict, i: str) -> dict:
    return by_id(p["joins"])[i]


def _no_xref_join_fact(p: dict, l: dict) -> None:
    p.pop("certified_joins")


SEMANTICS = [
    ("join-kind-unknown", "S01", "pack.yaml:joins[0].kind", lambda p, l: p["joins"][0].update(kind="fuzzy")),
    ("selector-mode-unknown", "S01", "pack.yaml:row_selectors[0].mode", lambda p, l: p["row_selectors"][0].update(mode="keep")),
    ("curated-source-id-a-source-s", "S02", "pack.yaml:curated_sources[0].id", lambda p, l: p["curated_sources"][0].update(id="retail")),
    ("context-id-twice", "S02", "pack.yaml:contexts[1].id", lambda p, l: p["contexts"][1].update(id="nna")),
    ("acronym-id-not-an-acronym", "S02", "pack.yaml:acronyms[0].id", lambda p, l: p["acronyms"][0].update(id="nna")),
    ("lock-without-the-curated-source", "S01", "pack.lock:sources", lambda p, l: l["sources"].pop("xref")),
    ("alias-group-by-not-a-column", "S11", "pack.yaml:terminology.aliases[33].group_by",
     lambda p, l: p["terminology"]["aliases"][33].update(group_by="wealth.division.nope")),
    ("alias-entity-undeclared", "S11", "pack.yaml:terminology.aliases[39].entity",
     lambda p, l: p["terminology"]["aliases"][39].update(entity="family")),
    ("alias-unknown-time-mode", "S11", "pack.yaml:terminology.aliases[30].time_mode",
     lambda p, l: p["terminology"]["aliases"][30].update(time_mode="yesterday")),
    ("join-to-a-missing-column", "S23", "pack.yaml:joins[3].condition",
     lambda p, l: _j(p, "flow_account").update(condition="flow.account_id = account.nope")),
    ("join-to-a-table-not-in-the-lock", "S23", "pack.yaml:joins[3].to", lambda p, l: _j(p, "flow_account").update(to="wealth.nope")),
    ("join-condition-a-subquery", "S23", "pack.yaml:joins[3].condition",
     lambda p, l: _j(p, "flow_account").update(condition="flow.account_id IN (SELECT account_id FROM account)")),
    ("join-condition-joins-nothing", "S23", "pack.yaml:joins[3].condition",
     lambda p, l: _j(p, "flow_account").update(condition="flow.amount_usd > 0 AND account.is_current")),
    ("join-across-sources-not-a-cross-reference", "S23", "pack.yaml:joins[4].kind",
     lambda p, l: _j(p, "account_household").update(to="retail.customer", condition="account.household_id = customer.customer_id")),
    ("join-of-a-table-to-itself-without-an-alias", "S23", "pack.yaml:joins[12].alias", lambda p, l: _j(p, "segment_parent").pop("alias")),
    ("join-implements-an-undeclared-relation", "S23", "pack.yaml:joins[14].implements",
     lambda p, l: _j(p, "client_xref").update(implements="same_person")),
    ("cross-reference-without-its-table", "S23", "pack.yaml:joins[14].through", lambda p, l: _j(p, "client_xref").pop("through")),
    ("join-on-a-restricted-column", "S23", "pack.yaml:joins[14].condition",
     lambda p, l: _j(p, "client_xref").update(condition=_j(p, "client_xref")["condition"].replace(
         "retail.customer.customer_id = xref.client_xref.retail_customer_id",
         "retail.customer.date_of_birth = xref.client_xref.valid_from"))),
    ("filter-on-a-missing-column", "S24", "pack.yaml:filters[0].predicate", lambda p, l: p["filters"][0].update(predicate="flow.nope")),
    ("filter-reads-another-table", "S24", "pack.yaml:filters[0].predicate",
     lambda p, l: p["filters"][0].update(predicate="month_end.is_released")),
    ("filter-an-aggregate", "S24", "pack.yaml:filters[0].predicate",
     lambda p, l: p["filters"][0].update(predicate="sum(flow.amount_usd) > 0")),
    ("selector-on-a-missing-column", "S24", "pack.yaml:row_selectors[0].predicate",
     lambda p, l: p["row_selectors"][0].update(predicate="account.account_id = flow.account_id AND account.nope")),
    ("conflict-declared-one-way", "S24", "pack.yaml:row_selectors[1].conflicts_with[0]", lambda p, l: p["filters"][0].pop("conflicts_with")),
    ("conflict-with-an-undeclared-filter", "S24", "pack.yaml:filters[0].conflicts_with[0]",
     lambda p, l: p["filters"][0].update(conflicts_with=["nope"])),
    ("context-filter-on-a-table-it-does-not-reach", "S25", "pack.yaml:contexts[0].filters[1]", lambda p, l: p["contexts"][0].update(joins=[])),
    ("context-join-not-reached-from-the-fact", "S25", "pack.yaml:contexts[0].reaches[0]",
     lambda p, l: p["contexts"][0].update(reaches=["region_division", *p["contexts"][0]["reaches"]])),
    ("context-measure-not-on-its-fact", "S25", "pack.yaml:contexts[0].measure", lambda p, l: p["contexts"][0].update(measure="wealth.revenue")),
    ("context-undeclared-term", "S25", "pack.yaml:contexts[0].term", lambda p, l: p["contexts"][0].update(term="nope")),
    ("context-undeclared-filter", "S25", "pack.yaml:contexts[0].filters[0]", lambda p, l: p["contexts"][0]["filters"].__setitem__(0, "nope")),
    ("golden-item-not-in-its-set", "S25", "pack.yaml:contexts[0].golden.items[0]",
     lambda p, l: p["contexts"][0]["golden"]["items"].__setitem__(0, "W99")),
    ("golden-set-not-named", "S25", "pack.yaml:contexts[0].golden.set", lambda p, l: p["contexts"][0]["golden"].update(set="golden_retail")),
    ("context-selector-on-a-source-no-cross-reference-reaches", "S25", "pack.yaml:contexts[0].row_selectors[2]",
     lambda p, l: p["contexts"][0].update(reaches=[x for x in p["contexts"][0]["reaches"] if x != "client_xref"])),
    ("context-selector-matches-a-table-the-cross-reference-does-not-reach", "S25", "pack.yaml:contexts[0].row_selectors[2]",
     lambda p, l: by_id(p["row_selectors"])["banks_with_retail"].update(
         predicate="account.product_id = product.product_id AND account.status_code = 'OPEN'")),
    ("acronym-column-not-in-the-lock", "S26", "pack.yaml:acronyms[3].columns[0]",
     lambda p, l: p["acronyms"][3]["columns"].__setitem__(0, "wealth.flow.nope")),
    ("acronym-undeclared-term", "S26", "pack.yaml:acronyms[0].terms[0]", lambda p, l: p["acronyms"][0].update(terms=["nope"])),
    ("entity-key-not-a-column-of-its-table", "S26", "pack.yaml:entities[4].key[1]",
     lambda p, l: by_id(p["entities"])["wealth_account"].update(key=["DW_ACCT_ID", "nope"])),
    ("relation-to-an-undeclared-entity", "S26", "pack.yaml:relations[0].to", lambda p, l: p["relations"][0].update(to="nope")),
    ("lineage-from-a-table-not-in-the-lock", "S26", "pack.yaml:lineage[0].derived_from[0]",
     lambda p, l: p["lineage"][0].update(derived_from=["wealth.nope"])),
    ("not-joinable-within-its-own-source", "S26", "pack.yaml:not_joinable[0].table",
     lambda p, l: p["not_joinable"][0].update(table="corporate.counterparty")),
    ("certified-pair-also-a-no-join-pair", "S27", "pack.yaml:certified_joins[0].between",
     lambda p, l: p["no_join"].append({"between": ["retail", "wealth"], "asserted_by": "Data Architecture Board",
                                       "asserted_on": "2024-03-12", "note": "No mapping table exists."})),
    ("certified-join-not-a-cross-reference", "S27", "pack.yaml:certified_joins[0].join",
     lambda p, l: p["certified_joins"][0].update(join="flow_account")),
    ("cross-reference-with-no-certified-join", "S27", "pack.yaml:joins[14]", lambda p, l: _no_xref_join_fact(p, l)),
    ("scd2-column-not-in-the-lock", "S28", "pack.yaml:policies.scd2[0].is_current",
     lambda p, l: p["policies"]["scd2"][0].update(is_current="current_flag")),
    ("scd2-table-twice", "S28", "pack.yaml:policies.scd2[1].table", lambda p, l: p["policies"]["scd2"][1].update(table="wealth.ACCT_DIM2_1")),
    ("release-driver-table-not-in-the-lock", "S28", "pack.yaml:policies.release_driver[0].table",
     lambda p, l: p["policies"]["release_driver"][0].update(table="wealth.nope")),
]


@pytest.mark.parametrize("rule, where, edit", [pytest.param(*m[1:], id=m[0]) for m in SEMANTICS])
def test_each_new_check_names_its_rule_exits_2_and_writes_nothing(tmp_path, capsys, rule, where, edit):
    out = tmp_path / "artifacts"
    code, stdout, err = compile_in(capsys, scratch(tmp_path, edit), out)
    assert code == 2 and stdout == "", err
    assert (rule, where) in [(r, w) for r, w, _ in findings(err)], err
    assert "nothing was written" in err and not out.exists()


def test_the_new_checks_are_listed_in_the_compiler_s_docstring_and_the_readme():
    from kge.compiler import checks
    readme = " ".join((DEMO / "README.md").read_text().split())
    for rule in ("S23", "S24", "S25", "S26", "S27", "S28"):
        assert re.search(rf"^  {rule}  ", checks.__doc__, re.M), rule
        assert rule in readme, rule


def pack_1_0(tmp_path: Path) -> Path:
    """A clients folder holding the 1.0.0 pack, as item 2 wrote it, and its lock: the units' catalogue, which 1.1.0's
    lock holds as it was, without the curated source 1.0.0 did not harvest, and Wealth's catalogue as it was before pack
    1.5.0 renamed it physically (WEALTH_BEFORE_1_5_0)."""
    clients = tmp_path / "clients"
    (clients / CLIENT).mkdir(parents=True)
    (clients / CLIENT / "pack.yaml").write_text(PACK_1_0.read_text())
    lock = committed()[1]
    lock["sources"].pop("xref")
    lock["sources"]["wealth"] = WEALTH_BEFORE_1_5_0
    (clients / CLIENT / "pack.lock").write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")
    return clients


def test_the_1_0_0_pack_compiles_against_meta_model_1_1_0_and_loads(tmp_path, artifact):
    """Meridian's pack stays valid: written for 1.0.0, it compiles against today's meta-model, its new types empty, and
    loads on this store. Today's pack holds every row 1.0.0 held, but for retail-wealth's no-join fact, which Option B
    replaced, the flow amount and AUM's column (pack 1.3.0), the columns the new measures are on, which carry their
    fields, and the restricted attributes' policy, which since the review round gives the phrases people use for its
    columns. Wealth's rows are under the physical names pack 1.5.0 gives its tables (tests/integration/test_sage_names.py),
    so the rows compared here are the other units'."""
    old = printed(run(*compile_args(tmp_path / "out", pack_1_0(tmp_path))))
    man = manifest_of(old)
    assert (man["pack"]["version"], man["meta_model_version"]) == ("1.0.0", "1.3.0"), "the legacy pack compiles against 1.3.0"
    assert all(man["counts"]["nodes"][t] == (23 if t == "OntologyConcept" else 0) for t in NEW_NODES.values())
    assert man["counts"]["edges"]["NO_JOIN"] == 6 and man["counts"]["edges"]["CERTIFIED_JOIN"] == 0
    moved = []
    for step in man["load_order"]:
        then = rows(old, step["path"])[1:]
        now = rows(artifact, step["path"])[1:]
        moved += [(step["path"], r[:2]) for r in then if r not in now and not any(str(x).startswith("wealth.") for x in r)]
    assert sorted(moved) == [("data/edges/NO_JOIN__Unit__Unit.csv", ["retail", "wealth"]),
                             ("data/nodes/Policy.csv", ["restricted-personal-data", "restricted_attribute"]),
                             ("data/nodes/Unit.csv", ["wealth", "Wealth Management"])], moved
    db, conn = load(old, tmp_path / "graph")
    try:
        assert conn.execute("MATCH (x:JoinRule) RETURN count(x)").get_all() == [[0]]
        assert conn.execute("MATCH ()-[n:NO_JOIN]->() RETURN count(n)").get_all() == [[6]]
    finally:
        conn.close()
        db.close()


def test_the_demo_graph_holds_the_layer_and_no_unit_query_sees_the_curated_source(eng):
    q = eng.g.q
    assert [r["n"] for r in q("MATCH (u:Unit) RETURN count(u) AS n")] == [4]
    assert q("MATCH (c:CuratedSource)-[:HAS_CURATED_TABLE]->(t:Tbl)-[:HAS_COLUMN]->(x:Col) RETURN c.id AS c, t.id AS t, count(x) AS n") == [
        {"c": "xref", "t": "xref.client_xref", "n": 13}]
    assert q("MATCH (u:Unit)-[:HAS_TABLE]->(t:Tbl) WHERE t.unit = 'xref' RETURN count(t) AS n") == [{"n": 0}]
    assert q("MATCH (:Principal)-[:CAN_READ]->(u:Unit) WHERE u.id = 'xref' RETURN count(*) AS n") == [{"n": 0}]
    assert q("MATCH (a:Unit)-[c:CERTIFIED_JOIN]->(b:Unit) RETURN a.id AS a, b.id AS b, c.declared_join AS j") == [
        {"a": "retail", "b": "wealth", "j": "client_xref"}]
    assert q("MATCH (:Unit)-[n:NO_JOIN]->(:Unit) RETURN count(n) AS n") == [{"n": 5}]
    assert q("MATCH (t:Term)-[:RESOLVES_TO]->(c:Context)-[:SPECIALIZES]->(m:Measure) WHERE c.id = 'nna' RETURN t.name AS t, m.id AS m") == [
        {"t": "Net New Assets", "m": "wealth.net-new-assets"}]
    chain = q("MATCH (c:Context)-[r:REQUIRES_JOIN]->(j:JoinRule)-[:JOIN_TO]->(t:Tbl) WHERE c.id = 'nna' "
              "RETURN j.id AS j, r.required AS required, t.id AS t ORDER BY r.ordinal")
    nna = by_id(committed()[0]["contexts"])["nna"]
    assert [x["j"] for x in chain] == nna["joins"] + nna["reaches"] and [x["required"] for x in chain] == [True] + [False] * len(nna["reaches"])
    assert q("MATCH (j:JoinRule)-[:IMPLEMENTS]->(r:Relation)-[:REL_TO]->(e:Entity)<-[:REPRESENTS]-(t:Tbl) RETURN j.id AS j, t.id AS t") == [
        {"j": "client_xref", "t": "wealth.HSHLD_DIM_1"}]


def test_the_runtime_refuses_only_the_pairs_a_no_join_fact_keeps_apart(eng):
    """Since #39 item 7d the runtime reads the certified join (tests/integration/test_cross_unit.py): Engine.answer lists the five
    pairs with a no-join fact among the joins it refuses, and no longer retail-wealth."""
    a = eng.answer("Total revenue across the bank for the quarter to 30 June 2025", "analyst_enterprise")
    assert "retail-wealth" not in a["joins_refused"] and len(a["joins_refused"]) == 5, a.get("joins_refused")


def test_across_retail_and_wealth_the_certified_join_answers_and_a_no_join_pair_quotes_its_fact(eng):
    """Which of Retail's customers Wealth has too is counted through the certified join; a pair that keeps its no-join fact
    is refused, quoting it."""
    a = eng.answer("Which retail customers are also wealth households?", "analyst_enterprise")
    assert a["behaviour"] == "figure" and a["certified_join"]["declared_join"] == "client_xref" and "joins_refused" not in a
    b = eng.answer("Which SME clients are also wealth households?", "analyst_enterprise")
    assert (b["joins_refused"], b["reason"]) == (["sme-wealth"], "No join path between sme and wealth. Separate customer populations and "
                                                 "separate key domains by design. No mapping table exists.")


def test_the_readme_says_what_pack_1_1_0_adds():
    readme = " ".join((DEMO / "README.md").read_text().split())
    for needle in ("`meta-model.yaml` (version 1.3.0", "pytest tests/unit/test_query_semantics.py -q", "`JoinRule`", "certified join fact",
                   "`curated_sources`", "join candidates", "the owner's Question 1", "Five curated assertions",
                   "The runtime answers from this layer as the next section says"):
        assert needle in readme, needle
    assert "Nothing the runtime answers moves yet" not in readme and "Six curated assertions" not in readme
    version = yaml.safe_load((DEMO / "meta-model.yaml").read_text())["version"]
    assert f"The 1.0.0 pack still compiles against today's meta-model, {version}," in readme
    assert "compiles against meta-model 1.1.0" not in readme
    said = readme.split("The 1.0.0 pack still compiles against today's meta-model", 1)[1].split(". ", 1)[0]
    for row in ("the replaced fact", "the flow amount and AUM's column, which the new measures are on", "the restricted attributes' policy"):
        assert row in said, row
    assert "No figure or behaviour the rule presets give moves yet" not in readme
