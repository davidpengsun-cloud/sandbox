"""The platform's meta-model (meta-model.yaml, 1.5.0), and the DDL kge/graph/schema.py builds from it.

    .venv/bin/python -m pytest tests/unit/test_meta_model.py -q

Every type has a logical name, the name it is loaded under, a key (a node
type's), a layer, an origin and typed properties. The loaded graph keeps
today's names and keys, so the runtime's Cypher runs unchanged: the DDL built
for each type the graph had before issue #39 is the frozen one the legacy
model digest is taken over (kge/graph/legacy.py's LEGACY_DDL), and Term's new id
slug is the one property 1.0.0 adds to them. The new types are Measure,
Calendar, Alias, Policy and GraphModel, with their edges; 1.1.0 adds the
query-semantics layer (tests/unit/test_query_semantics.py), and 1.2.0 the rest of the
parser's vocabulary and the pack's title (tests/integration/test_vocabulary.py), and 1.3.0 the preloaded standards' concepts and the
SKOS mappings to them (tests/unit/test_standards.py). Version 1.4.0 adds optional Alias source sets, and 1.5.0 adds the runtime subgraph. The DDL loads on the
store this run is on; run the suite once per store.

Builds a throwaway graph in a temporary folder. One test reads the demo's own
graph, to hold the README to what the compiled pack fills of 1.0.0.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from kge.graph import legacy, schema
from kge.adapters.model import DIM

DEMO = Path(__file__).resolve().parents[2]
LAYERS = {"harvested", "curated", "access", "runtime"}
ORIGINS = {"lock", "pack", "landing", "deployment", "platform", "compiler", "pipeline"}
TYPES = re.compile(r"^(STRING|BOOLEAN|INT64|DOUBLE|STRING\[\]|FLOAT\[\{embedding_dim\}\])$")
LOADED = {"Source": "Unit", "Table": "Tbl", "Column": "Col", "Term": "Term", "Document": "Document", "Chunk": "Chunk",
          "Tag": "Tag", "Principal": "Principal", "Process": "Process", "Release": "Release"}
ADDED = {"Term": ["id"], "Unit": ["ordinal"]}
RELEASE = "CREATE NODE TABLE Release(id STRING, client STRING, policy STRING, as_of STRING, PRIMARY KEY (id))"


@pytest.fixture(scope="module")
def meta() -> dict:
    return schema.meta_model()


def table_name(stmt: str) -> str:
    return re.match(r"CREATE (?:NODE|REL) TABLE (?:IF NOT EXISTS )?(\w+)\(", stmt).group(1)


def test_the_meta_model_is_semver_1_5_0_and_names_no_client(meta):
    assert meta["version"] == "1.5.0"
    text = (DEMO / "meta-model.yaml").read_text().lower()
    for noun in ("meridian", "retail", "corporate", "wealth", "sme", "household", "revenue", "xref"):
        assert not re.search(rf"\b{noun}\b", text), noun


def test_every_type_has_its_names_key_layer_origin_and_typed_properties(meta):
    nodes, edges = meta["node_types"], meta["edge_types"]
    for name, t in nodes.items():
        assert {"loaded_as", "key", "layer", "origin", "properties"} <= set(t), name
        assert t["layer"] in LAYERS and t["origin"] in ORIGINS and t["key"] in t["properties"], name
        assert all(TYPES.match(schema.prop(p)["type"]) for p in t["properties"].values()), name
    for name, e in edges.items():
        assert {"loaded_as", "from", "to", "layer", "origin", "properties"} <= set(e), name
        assert e["layer"] in LAYERS and e["origin"] in ORIGINS, name
        assert all(end in nodes for end in schema.ends(e["from"]) + schema.ends(e["to"])), name
        assert all(TYPES.match(schema.prop(p)["type"]) for p in (e["properties"] or {}).values()), name
    loaded = [t["loaded_as"] for t in nodes.values()] + [e["loaded_as"] for e in edges.values()]
    assert len(loaded) == len(set(loaded))


def test_the_graph_keeps_today_s_names_and_keys(meta):
    nodes = meta["node_types"]
    assert {n: nodes[n]["loaded_as"] for n in LOADED} == LOADED
    assert nodes["Term"]["key"] == "name" and "id" in nodes["Term"]["properties"]
    assert nodes["Chunk"]["properties"]["embedding"] == "FLOAT[{embedding_dim}]"
    assert list(nodes["Process"]["constants"]) == ["tagging", "certification", "querying"]


def test_1_3_0_only_adds_the_standards_properties_and_a_policy_end(meta):
    """1.3.0 is a minor version: OntologyConcept keeps 1.1.0's three properties first and adds optional ones after them,
    and MAPPED_TO adds a third end, Policy, and its SKOS relation and note."""
    nodes, edges = meta["node_types"], meta["edge_types"]
    assert list(nodes["OntologyConcept"]["properties"]) == ["id", "label", "iri", "definition", "sub_graph", "standard",
                                                            "standard_version", "licence", "licence_url", "source"]
    assert edges["MAPPED_TO"]["from"] == ["Entity", "Term", "Policy"] and edges["MAPPED_TO"]["to"] == "OntologyConcept"
    assert edges["MAPPED_TO"]["properties"] == {"relation": "STRING", "note": "STRING"}


def test_the_new_types_and_edges(meta):
    """The pack's order is kept where it means something (#39 item 6): a source's fixes the pairs' names, an alias's
    breaks a tie in length, and a restricted column's is the order the treatment is told them in."""
    nodes, edges = meta["node_types"], meta["edge_types"]
    assert {"Measure", "Calendar", "Alias", "Policy", "GraphModel"} <= set(nodes)
    assert list(nodes["Alias"]["properties"]) == ["phrase", "kind", "target", "ordinal", "sources"] and nodes["Alias"]["key"] == "phrase"
    assert list(nodes["Source"]["properties"]) == ["id", "label", "key_column", "ordinal"]
    assert edges["GOVERNS"]["properties"] == {"ordinal": "INT64"}
    assert list(nodes["Policy"]["properties"]) == ["id", "kind", "params", "version"]
    assert list(nodes["GraphModel"]["properties"]) == ["id", "client", "pack_version", "meta_model_version", "title"], "title since 1.2.0"
    assert list(edges["PERIOD_COLUMN"]["properties"]) == ["kind", "calendar", "rollup_table", "rollup_column"]
    pairs = {n: (schema.ends(e["from"]), schema.ends(e["to"])) for n, e in edges.items()}
    assert pairs["MEASURE_OF"] == (["Measure"], ["Term"]) and pairs["COMPUTED_FROM"] == (["Measure"], ["Column"])
    assert pairs["IN_CALENDAR"] == (["Measure"], ["Calendar"]) and pairs["PERIOD_COLUMN"] == (["Table"], ["Column"])
    assert pairs["ALIAS_OF"][0] == ["Alias"] and pairs["ALIAS_OF"][1][:2] == ["Term", "Source"] and pairs["GOVERNS"] == (["Policy"], ["Column"])


def test_column_keeps_the_measure_fields_denormalised_from_measure(meta):
    """So the runtime's Cypher, which reads them on Col, runs unchanged. A measure's calendar is its IN_CALENDAR edge,
    not a property."""
    col, measure = meta["node_types"]["Column"]["properties"], meta["node_types"]["Measure"]["properties"]
    for p in ("grain", "currency", "calendar", "conversion"):
        assert schema.prop(col[p]).get("denormalised_from") == "Measure", p
        assert p in measure or p == "calendar", p


def test_the_ddl_is_built_from_the_meta_model(meta):
    assert schema.DDL == schema.ddl(meta, DIM)
    assert f"embedding FLOAT[{DIM}]" in next(s for s in schema.DDL if table_name(s) == "Chunk")


def test_each_type_the_graph_had_before_39_is_declared_as_it_was(meta):
    """The frozen DDL (kge/graph/legacy.py's LEGACY_DDL), but for IF NOT EXISTS and the properties ADDED names; and the
    release as item 1 declared it. ADDED is Term's stable slug, beside its name key, and a source's place in the pack's
    list, which the runtime reads its order from (#39 item 6)."""
    built = {table_name(s): s for s in schema.DDL}
    for frozen in legacy.LEGACY_DDL:
        name = table_name(frozen)
        stmt = built[name]
        for p in ADDED.get(name, []):
            stmt = re.sub(rf", {p} [A-Z0-9\[\]]+(?=, PRIMARY KEY|\)$)", "", stmt, count=1)
        assert stmt == frozen.replace(" IF NOT EXISTS", ""), name
    assert built["Release"] == RELEASE
    assert len(legacy.LEGACY_DDL) == 25 and len(schema.DDL) == len(meta["node_types"]) + len(meta["edge_types"])


def test_the_ddl_loads_on_this_store(tmp_path, meta):
    """Every table, the rel groups among them (ALIAS_OF reaches a term and a unit, and since 1.1.0 a column and an
    entity)."""
    from kge.adapters.graphstore import connect, open_db
    db = open_db(tmp_path / "graph")
    conn = connect(db)
    try:
        for stmt in schema.DDL:
            conn.execute(stmt)
        tables = {r[1] for r in conn.execute("CALL show_tables() RETURN *").get_all()}
        assert tables == {table_name(s) for s in schema.DDL}
        conn.execute("CREATE (:Alias {phrase: 'p', kind: 'term', target: 't'}), (:Alias {phrase: 'q', kind: 'source', target: 'u'})")
        conn.execute("CREATE (:Term {name: 'T', id: 't'}), (:Unit {id: 'u'})")
        conn.execute("MATCH (a:Alias), (t:Term) WHERE a.phrase = 'p' CREATE (a)-[:ALIAS_OF]->(t)")
        conn.execute("MATCH (a:Alias), (u:Unit) WHERE a.phrase = 'q' CREATE (a)-[:ALIAS_OF]->(u)")
        rows = conn.execute("MATCH (a:Alias)-[:ALIAS_OF]->(x) RETURN a.phrase, label(x) ORDER BY a.phrase").get_all()
        assert rows == [["p", "Term"], ["q", "Unit"]]
    finally:
        conn.close()
        db.close()


def test_the_readme_says_term_s_id_and_the_new_types_are_filled_from_the_compiled_pack(eng):
    """make graph loads the compiled artifact (kge/graph/load.py --artifact): every term the pack declares carries its id,
    the new types hold the pack's rows, and one GraphModel node names the artifact. The README says so, and the demo's
    graph agrees."""
    readme = " ".join((DEMO / "README.md").read_text().split())
    assert "`Term` keyed by its name, with an `id` slug declared beside it" in readme
    assert "Term's id and those types are filled from the compiled pack" in readme
    assert "not filled yet" not in readme and "stable `id` beside it" not in readme
    [terms] = eng.g.q("MATCH (t:Term) RETURN count(t) AS n, count(t.id) AS ids")
    assert terms["n"] >= 53 and terms["ids"] == 53, "#398/#402 add four certified NNA reference meanings"
    held = {t: eng.g.q(f"MATCH (n:{t}) RETURN count(n) AS n")[0]["n"] for t in ("Measure", "Calendar", "Alias", "Policy", "GraphModel")}
    assert held == {"Measure": 21, "Calendar": 2, "Alias": 203, "Policy": 11, "GraphModel": 1}, \
        "#398/#402 add NNA reference measures, aliases and release policies; installation-specific phrases stay in client packs"


def _strip_1_5_0(meta_copy: dict) -> dict:
    for n in ("Runtime", "RtTable", "RtColumn"):
        assert n in meta_copy["node_types"]
        meta_copy["node_types"].pop(n)
    for e in ("HOLDS", "HAS", "CERTIFIED_IN"):
        assert e in meta_copy["edge_types"]
        meta_copy["edge_types"].pop(e)
    assert meta_copy["edge_types"]["IMPLEMENTS"]["from"] == ["Join", "RtTable", "RtColumn"]
    assert meta_copy["edge_types"]["IMPLEMENTS"]["to"] == ["Relation", "Table", "Column"]
    meta_copy["edge_types"]["IMPLEMENTS"]["from"] = "Join"
    meta_copy["edge_types"]["IMPLEMENTS"]["to"] = "Relation"
    meta_copy["version"] = "1.4.0"
    return meta_copy


def test_1_4_0_only_adds_optional_alias_sources(meta):
    """Remove the later #398/#402 context properties before comparing the frozen alias-source change."""
    from copy import deepcopy
    import yaml

    prior = _strip_1_5_0(deepcopy(meta))
    prior["version"] = "1.3.0"
    assert prior["node_types"]["Alias"]["properties"].pop("sources") == {"type": "STRING[]", "required": False}
    for name in ("ytd_calendar", "version_dates", "bindings"):
        assert prior["node_types"]["Context"]["properties"].pop(name) == "STRING"
    frozen = yaml.safe_load((DEMO / "tests" / "fixtures" / "meta-model-1.3.0.yaml").read_text())
    assert prior == frozen


def test_1_5_0_only_adds_runtime_subgraph(meta):
    from copy import deepcopy

    prior = _strip_1_5_0(deepcopy(meta))
    assert len(prior["node_types"]) == 26
    assert len(prior["edge_types"]) == 45
    assert len(meta["node_types"]) == 29
    assert len(meta["edge_types"]) == 48
