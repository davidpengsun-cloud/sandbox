"""The compiled artifact: what the compiler writes for a pack that passed its checks, and how it writes it.

    <out>/<client>/<model_hash>/
      graph-model.yaml                     the schema: each type's logical and loaded names, key, layer, origin and
                                           typed properties, what fills it and its rows; the pack's extensions; the
                                           join candidates (harvested foreign keys no declared join covers); counts
      ddl/kuzu.cypher                      CREATE NODE and REL TABLE, one statement a line, in the Cypher dialect
                                           named kuzu, which Ladybug loads
      data/nodes/<Type>.csv                one per node type the pack or its lock fills, under its loaded name
      data/edges/<EDGE>__<From>__<To>.csv  one per edge type the pack or its lock fills and pair of ends (a rel group
                                           has one a pair), under the loaded names
      access/roles.json                    the role templates, reads resolved: not a graph table; the deployment's
                                           identities are bound to them at load
      gates.json                           the pack's post-load gates (manifest.gates), where it declares them: not a
                                           graph table; the post-load check (kge/eval/margin.py) holds the loaded graph to
                                           them, so a candidate is checked against its own pack's thresholds
      manifest.json                        format, client, title (the pack's), pack {id, version, hash}, lock_hash,
                                           meta_model_version, compiler_version, target, params, files {path: sha256},
                                           counts, load_order and model_hash

A type is filled here when the meta-model gives the pack or the lock as its
origin. A type the landing, the deployment, the platform's constants or the
compiled model itself fill is filled at load, and the pipeline's by the
pipeline, as graph-model.yaml says. DEFINES is the pack's, but its documents
are the landing's: its rows are written here, and load_order stages them for
the load to attach to the documents that landed (S12, fail closed). A foreign
key the lock harvested loads as a JOINS edge on its one column pair, as before;
a composite one, which S05 lets through only where a declared join covers it,
is that join's. A curated source's tables hang off it (HAS_CURATED_TABLE), not
off a unit.

Determinism. Rows are sorted by key (an edge's by its ends, then its
properties), and properties come in the meta-model's order. csv.writer writes
with \\n line ends; booleans as true and false, floats as repr, '' as ''. NULL
is \\N in a STRING column and an empty field in any other: loaded with
null_strings=['\\N'], both stores read \\N as NULL and keep '' in a STRING
column, but neither reads \\N as NULL in an INT64, DOUBLE or BOOLEAN column,
where an empty field can only be NULL. JSON is canonical with a trailing
newline, YAML is yaml.safe_dump with fixed options, and no file carries a
timestamp or an absolute path. model_hash is the sha256 of the files' sha256sum
listing ("<sha256>  <path>" a line, in path order) over every file but the
manifest, which is made of what those files hold, so one hash names one set of
bytes. The artifact is built in memory, then written to a temporary folder
renamed into place; a folder already under its hash must hold exactly its
bytes, or nothing is written.

The DDL is built here from the meta-model, for kge/graph/schema.py too; so is the BigQuery graph store's
(bigquery_ddl), from the same model, at load, never into the artifact.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable, NamedTuple

import yaml

from kge.compiler import standards as preloaded
from kge.compiler.checks import ALIAS_KINDS, TIME_POLICIES, candidates, expression
from kge.compiler.pack import Pack, canonical
from kge.compiler.vocabulary import entries as vocabulary
from kge.pipeline.access import process_vocabulary

FORMAT = "graph-model/v1"
MANIFEST = "manifest.json"
NULL = "\\N"
EMITTED = ("lock", "pack")
FILLED_BY = {"landing": "load", "deployment": "load", "platform": "load", "compiler": "load", "pipeline": "pipeline"}
YAML = {"sort_keys": False, "default_flow_style": False, "allow_unicode": True, "width": 4096, "indent": 2, "line_break": "\n"}


def prop(p) -> dict:
    """A property's declaration: a bare type, or {type, ...}."""
    return {"type": p} if isinstance(p, str) else p


def ends(x) -> list[str]:
    """An edge's from or to: one type, or a list of them (a rel group)."""
    return list(x) if isinstance(x, list) else [x]


def ddl(meta: dict, embedding_dim: int) -> list[str]:
    """CREATE statements for every node type, then every edge type, in the meta-model's order and its loaded names."""
    nodes = meta["node_types"]

    def props(ps: dict) -> list[str]:
        return [f"{name} {prop(p)['type'].replace('{embedding_dim}', str(embedding_dim))}" for name, p in (ps or {}).items()]

    out = [f"CREATE NODE TABLE {t['loaded_as']}({', '.join(props(t['properties']))}, PRIMARY KEY ({t['key']}))"
           for t in nodes.values()]
    for e in meta["edge_types"].values():
        pairs = [f"FROM {nodes[a]['loaded_as']} TO {nodes[b]['loaded_as']}" for a in ends(e["from"]) for b in ends(e["to"])]
        out.append(f"CREATE REL TABLE {e['loaded_as']}({', '.join(pairs + props(e['properties']))})")
    return out


def bigquery_ddl(model: dict, embedding_dim: int | None = None) -> list[str]:
    """The BigQuery DDL of the same compiled model the Cypher DDL is built from: CREATE OR REPLACE TABLE for every node
    type and every pair of ends of every edge type, then CREATE OR REPLACE PROPERTY GRAPH over them (NODE TABLES with KEY,
    LABEL and PROPERTIES; EDGE TABLES with KEY, SOURCE KEY ... REFERENCES, DESTINATION KEY ... REFERENCES, LABEL and
    PROPERTIES), as the BigQuery graph store names its tables (kge/adapters/graphstore/gql.py). model is the merged
    meta-model with embedding_dim (what ddl() takes), or, with none, an artifact's graph-model.yaml, which describes it.
    Where the tables go is the graph's, not the model's, so the statements name the project, the dataset and the model
    hash's table prefix as ${project}, ${dataset} and ${prefix}, which the store fills when it makes them. Not a file of
    the artifact: the model hash, and so every compiled artifact, is the same whichever store it is loaded into."""
    from kge.adapters.graphstore.gql import TEMPLATE, Schema, ddl as graph_ddl
    schema = Schema.from_graph_model(model) if embedding_dim is None else Schema.from_meta(model, embedding_dim)
    return graph_ddl(schema, TEMPLATE)


def merged(meta: dict, extensions: list[dict]) -> dict:
    """The meta-model with the pack's extensions (S04 has checked them): new node and edge types after the meta-model's,
    loaded under their own names, curated and the pack's; a new property after a type's own."""
    model = {"version": meta["version"],
             "node_types": {n: {**t, "properties": dict(t.get("properties") or {})} for n, t in meta["node_types"].items()},
             "edge_types": {n: {**t, "properties": dict(t.get("properties") or {})} for n, t in meta["edge_types"].items()}}
    for e in extensions:
        if "node_type" in e:
            model["node_types"][e["node_type"]] = {"loaded_as": e["node_type"], "key": e["key"], "layer": "curated", "origin": "pack",
                                                   "properties": dict(e["properties"])}
        elif "edge_type" in e:
            model["edge_types"][e["edge_type"]] = {"loaded_as": e["edge_type"], "from": e["from"], "to": e["to"], "layer": "curated",
                                                   "origin": "pack", "properties": dict(e.get("properties") or {})}
        else:
            model["node_types"][e["extends"]]["properties"][e["property"]] = e["type"]
    return model


def extra(entry: dict) -> dict:
    """The properties an extension adds that an entry carries (S04 has checked each is declared)."""
    return {k: v for k, v in entry.items() if k.startswith("x_")}


def tables(lock: dict) -> list[tuple[str, dict]]:
    return [(s, t) for s, held in lock["sources"].items() for t in held["tables"]]


def term_names(p: dict) -> dict[str, str]:
    """Each term's name by its id: the graph keys a term by its name."""
    return {g["id"]: g["name"] for g in p["glossary"]}


def restricted(p: dict) -> list[dict]:
    return (p.get("policies") or {}).get("restricted_attributes", [])


def alias_kind(a: dict) -> str:
    return next(k for k in ALIAS_KINDS if k in a)


def columns(p: dict, lock: dict) -> list[dict]:
    """Each column the lock holds, with its certified flag and the fields of the measure on it. A column the catalogue
    gave no comment, and one no measure is on, take '' for them, as the graph has always given them."""
    certified = {m["column"] for m in p["mappings"] if m["certified"]}
    measured = {m["column"]: m for m in p["measures"]}
    out = []
    for s, t in tables(lock):
        for c in t["columns"]:
            cid = f"{s}.{t['name']}.{c['name']}"
            m = measured.get(cid, {})
            out.append({"id": cid, "unit": s, "tbl": t["name"], "name": c["name"], "data_type": c["type"],
                        "description": c.get("description") or "", "is_key": c["name"] in t["primary_key"], "certified": cid in certified,
                        **{k: m.get(k, "") for k in ("grain", "currency", "calendar", "conversion")}})
    return out


def first_columns(lock: dict) -> list[tuple[str, str, str, str, str]]:
    """Each foreign key of one column, as JOINS holds it. A composite key is the declared join's that covers it: S05
    refuses one no declared join covers."""
    return [(s, t["name"], k["columns"][0], k["ref_table"], k["ref_columns"][0]) for s, t in tables(lock) for k in t["foreign_keys"]
            if len(k["columns"]) == 1]


def units(p: dict) -> set[str]:
    return {s["id"] for s in p["sources"]}


ALIAS_ENDS = {"term": "Term", "source": "Source", "group_by": "Column", "entity": "Entity", "column": "Column"}


def alias_edges(p: dict, lock: dict) -> list[tuple]:
    names = term_names(p)
    out = []
    for a in p["terminology"]["aliases"]:
        kind = alias_kind(a)
        if kind in ALIAS_ENDS:
            out.append(("Alias", ALIAS_ENDS[kind], a["phrase"], names[a["term"]] if kind == "term" else a[kind], {}))
    return out


def mapping_edges(p: dict, lock: dict) -> list[tuple]:
    names = term_names(p)
    return [("Column", "Term", m["column"], names[m["term"]], {"certified": m["certified"], "certified_by": m.get("certified_by"),
             "certified_on": m.get("certified_on"), "generator": "human", "confidence": 1.0}) for m in p["mappings"]]


def measure_edges(p: dict, lock: dict) -> list[tuple]:
    names = term_names(p)
    return [("Measure", "Term", m["id"], names[m["term"]], {}) for m in p["measures"]]


def computed_from(p: dict, lock: dict) -> list[tuple]:
    """Each column a measure's expression reads: value, or filter for one under FILTER (WHERE ...) or a CASE's condition."""
    return [("Measure", "Column", m["id"], col, {"role": role}) for m in p["measures"]
            for col, role in expression(m["expression"], m["column"], lock)[1].items()]


def policies(p: dict, lock: dict) -> list[dict]:
    """The restricted attributes, then the time policies: scd2 (a table's versions) and release_driver (when its
    periods were frozen and released), each with its columns as its params. A policy without its own version is
    versioned with the pack that declares it."""

    out = [{"id": r["id"], "kind": "restricted_attribute",
            "params": canonical({"action": r["action"], "cite": r["cite"],
                                 **({"phrases": {x["column"]: x["phrases"] for x in r["phrases"]}} if r.get("phrases") else {})}),
            "version": r.get("version", p["manifest"]["version"]), **extra(r)} for r in restricted(p)]
    for kind, fields in TIME_POLICIES.items():
        for x in (p.get("policies") or {}).get(kind, []):
            params = {"table": x["table"], **{f: x[f] for f in fields}, **({"valid_to_inclusive": x["valid_to_inclusive"]} if kind == "scd2" else {})}
            out.append({"id": x["id"], "kind": kind, "params": canonical(params), "version": x.get("version", p["manifest"]["version"]),
                        **extra(x)})
    return out


def governs(p: dict, lock: dict) -> list[tuple]:
    """A restricted column's place among every restricted column; a time policy's columns, in its fields' order."""
    out = [("Policy", "Column", policy, c, {"ordinal": n})
           for n, (policy, c) in enumerate([(r["id"], c) for r in restricted(p) for c in r["columns"]], 1)]
    for kind, fields in TIME_POLICIES.items():
        for x in (p.get("policies") or {}).get(kind, []):
            out += [("Policy", "Column", x["id"], f"{x['table']}.{x[f]}", {"ordinal": n}) for n, f in enumerate(fields, 1)]
    return out


def ontology_concepts(p: dict, lock: dict) -> list[dict]:
    """The platform's preloaded reference concepts (standards/preload.yaml), which every client's graph holds, then the
    concepts the pack declares itself (S30 keeps their ids apart)."""
    preload = preloaded.load()
    return ([preload.concept_row(c) for c in preload.concepts.values()]
            + [{"id": o["id"], "label": o["label"], "iri": o.get("iri"), **extra(o)} for o in p.get("ontology", [])])


MAPPED_FROM = {"term": "Term", "entity": "Entity", "policy": "Policy"}


def mapped_to(p: dict, lock: dict) -> list[tuple]:
    """The pack's own concepts' terms and entities, with no relation; then its certified mappings to preloaded concepts
    (ontology_mappings, S30), each with its SKOS relation and note, a term by its name, an entity and a policy by id."""
    names, preload = term_names(p), preloaded.load()
    out = [*(("Entity", "OntologyConcept", e, o["id"], {}) for o in p.get("ontology", []) for e in o.get("entities", [])),
           *(("Term", "OntologyConcept", names[t], o["id"], {}) for o in p.get("ontology", []) for t in o.get("terms", []))]
    for m in p.get("ontology_mappings") or []:
        kind = next(k for k in MAPPED_FROM if k in m)
        out.append((MAPPED_FROM[kind], "OntologyConcept", names[m[kind]] if kind == "term" else m[kind],
                    preload.by_iri[m["iri"]]["id"], {"relation": m["relation"], "note": m.get("note")}))
    return out


def golden(p: dict) -> dict[tuple[str, str], dict]:
    """The golden questions the contexts cite, by (set, item): ids only, the set's version the one tests.sets names."""
    versions = {s["name"]: s["version"] for s in p["tests"]["sets"]}
    out = {}
    for c in p.get("contexts", []):
        g = c.get("golden") or {"items": []}
        for item in g["items"]:
            out[(g["set"], item)] = {"id": f"{g['set']}_{versions[g['set']]}:{item}", "item": item, "set": g["set"], "version": versions[g["set"]]}
    return out


def context_edges(p: dict, key: str, to: str) -> list[tuple]:
    """A context's filters or row selectors, in its order."""
    return [("Context", to, c["id"], x, {"ordinal": n}) for c in p.get("contexts", []) for n, x in enumerate(c.get(key, []), 1)]


def requires_join(p: dict, lock: dict) -> list[tuple]:
    """A context's joins, those it always renders first, then those it reaches a dimension through, in its order."""
    return [("Context", "Join", c["id"], j, {"ordinal": n, "required": required})
            for c in p.get("contexts", [])
            for n, (j, required) in enumerate([*((j, True) for j in c.get("joins", [])), *((j, False) for j in c.get("reaches", []))], 1)]


def conflicts(p: dict, lock: dict) -> list[tuple]:
    kinds = {x["id"]: kind for section, kind in (("filters", "Filter"), ("row_selectors", "RowSelector")) for x in p.get(section, [])}
    return [(kinds[x["id"]], kinds[o], x["id"], o, {}) for section in ("filters", "row_selectors") for x in p.get(section, [])
            for o in x.get("conflicts_with", [])]


def period_columns(p: dict, lock: dict) -> list[tuple]:
    return [("Table", "Column", b["table"], f"{b['table']}.{b['column']}",
             {"kind": b["kind"], "calendar": b["calendar"], "rollup_table": (b.get("rollup") or {}).get("table"),
              "rollup_column": (b.get("rollup") or {}).get("column")}) for b in p.get("period_bindings", [])]


NODES: dict[str, Callable[[dict, dict], list[dict]]] = {
    "Source": lambda p, l: [{"id": s["id"], "label": s["label"], "key_column": s["entity_key"], "ordinal": n, **extra(s)}
                            for n, s in enumerate(p["sources"], 1)],
    "Table": lambda p, l: [{"id": f"{s}.{t['name']}", "unit": s, "name": t["name"]} for s, t in tables(l)],
    "Column": columns,
    "Term": lambda p, l: [{"name": g["name"], "domain": g["domain"], "steward": g["steward"], "definition": g["definition"],
                           "id": g["id"], **extra(g)} for g in p["glossary"]],
    "Measure": lambda p, l: [{**{k: m[k] for k in ("id", "expression", "aggregation", "grain", "currency", "conversion")},
                              "snapshot": m.get("snapshot"), "population": m.get("population"), **extra(m)} for m in p["measures"]],
    "Calendar": lambda p, l: [{"id": c["id"], "year_start_month": c["year_start_month"], "quarter_label": c["quarter_label"], **extra(c)}
                              for c in p.get("calendars", [])],
    "Alias": lambda p, l: [{"phrase": a["phrase"], "kind": alias_kind(a), "target": a[alias_kind(a)], "ordinal": n, **extra(a)}
                           for n, a in enumerate(p["terminology"]["aliases"], 1)],
    "Policy": policies,
    "CuratedSource": lambda p, l: [{"id": s["id"], "label": s["label"], "ordinal": n, **extra(s)}
                                   for n, s in enumerate(p.get("curated_sources", []), 1)],
    "Context": lambda p, l: [{"id": c["id"], "label": c["label"], "description": c["description"], **extra(c)} for c in p.get("contexts", [])],
    "Filter": lambda p, l: [{"id": f["id"], "label": f["label"], "predicate": f["predicate"], **extra(f)} for f in p.get("filters", [])],
    "RowSelector": lambda p, l: [{"id": s["id"], "label": s["label"], "mode": s["mode"], "predicate": s["predicate"], **extra(s)}
                                 for s in p.get("row_selectors", [])],
    "Join": lambda p, l: [{"id": j["id"], "kind": j["kind"], "condition": j["condition"], "alias": j.get("alias"), "through": j.get("through"),
                           **extra(j)} for j in p.get("joins", [])],
    "Acronym": lambda p, l: [{"id": a["id"], "expansion": a["expansion"], **extra(a)} for a in p.get("acronyms", [])],
    "Entity": lambda p, l: [{"id": e["id"], "label": e["label"], **extra(e)} for e in p.get("entities", [])],
    "Relation": lambda p, l: [{"id": r["id"], "label": r["label"], **extra(r)} for r in p.get("relations", [])],
    "OntologyConcept": ontology_concepts,
    "Golden": lambda p, l: list(golden(p).values()),
    "Vocabulary": lambda p, l: [{**v, "params": canonical(v["params"])} for v in vocabulary(p["terminology"])],
}
EDGES: dict[str, Callable[[dict, dict], list[tuple]]] = {
    "HAS_TABLE": lambda p, l: [("Source", "Table", s, f"{s}.{t['name']}", {}) for s, t in tables(l) if s in units(p)],
    "HAS_COLUMN": lambda p, l: [("Table", "Column", f"{s}.{t['name']}", f"{s}.{t['name']}.{c['name']}", {})
                                for s, t in tables(l) for c in t["columns"]],
    "JOINS": lambda p, l: [("Column", "Column", f"{s}.{t}.{c}", f"{s}.{rt}.{rc}", {"via": "declared foreign key"})
                           for s, t, c, rt, rc in first_columns(l)],
    "JOINS_TABLE": lambda p, l: [("Table", "Table", f"{s}.{t}", f"{s}.{rt}", {"from_table": t, "from_column": c, "to_table": rt,
                                                                             "to_column": rc}) for s, t, c, rt, rc in first_columns(l)],
    "MAPS_TO": mapping_edges,
    "NO_JOIN": lambda p, l: [("Source", "Source", f["between"][0], f["between"][1],
                              {"asserted_by": f["asserted_by"], "asserted_on": f["asserted_on"], "note": f["note"]}) for f in p.get("no_join", [])],
    "DEFINES": lambda p, l: [("Document", "Term", doc, g["name"], {}) for g in p["glossary"] for doc in g.get("defined_by", [])],
    "MEASURE_OF": measure_edges,
    "COMPUTED_FROM": computed_from,
    "IN_CALENDAR": lambda p, l: [("Measure", "Calendar", m["id"], m["calendar"], {}) for m in p["measures"]],
    "PERIOD_COLUMN": period_columns,
    "ALIAS_OF": alias_edges,
    "GOVERNS": governs,
    "HAS_CURATED_TABLE": lambda p, l: [("CuratedSource", "Table", s, f"{s}.{t['name']}", {}) for s, t in tables(l) if s not in units(p)],
    "RESOLVES_TO": lambda p, l: [("Term", "Context", term_names(p)[c["term"]], c["id"], {}) for c in p.get("contexts", [])],
    "SPECIALIZES": lambda p, l: [("Context", "Measure", c["id"], c["measure"], {}) for c in p.get("contexts", [])],
    "USES_FACT": lambda p, l: [("Context", "Table", c["id"], c["fact"], {}) for c in p.get("contexts", [])],
    "CERTIFIED_BY": lambda p, l: [("Context", "Golden", c["id"], golden(p)[(c["golden"]["set"], item)]["id"], {})
                                  for c in p.get("contexts", []) if c.get("golden") for item in c["golden"]["items"]],
    "REQUIRES_FILTER": lambda p, l: context_edges(p, "filters", "Filter"),
    "SELECTS_ROWS_WITH": lambda p, l: context_edges(p, "row_selectors", "RowSelector"),
    "TARGETS": lambda p, l: [(kind, "Table", x["id"], x["table"], {}) for section, kind in (("filters", "Filter"), ("row_selectors", "RowSelector"))
                             for x in p.get(section, [])],
    "CONFLICTS_WITH": conflicts,
    "REQUIRES_JOIN": requires_join,
    "JOIN_FROM": lambda p, l: [("Join", "Table", j["id"], j["from"], {}) for j in p.get("joins", [])],
    "JOIN_TO": lambda p, l: [("Join", "Table", j["id"], j["to"], {}) for j in p.get("joins", [])],
    "IMPLEMENTS": lambda p, l: [("Join", "Relation", j["id"], j["implements"], {}) for j in p.get("joins", []) if j.get("implements")],
    "ABBREVIATED_AS": lambda p, l: [("Term", "Acronym", term_names(p)[t], a["id"], {}) for a in p.get("acronyms", []) for t in a.get("terms", [])],
    "SPELLED_WITH": lambda p, l: [("Column", "Acronym", c, a["id"], {}) for a in p.get("acronyms", []) for c in a.get("columns", [])],
    "REPRESENTS": lambda p, l: [("Table", "Entity", e["table"], e["id"], {}) for e in p.get("entities", [])],
    "IDENTIFIED_BY": lambda p, l: [("Entity", "Column", e["id"], f"{e['table']}.{k}", {"ordinal": n}) for e in p.get("entities", [])
                                   for n, k in enumerate(e["key"], 1)],
    "REL_FROM": lambda p, l: [("Relation", "Entity", r["id"], r["from"], {}) for r in p.get("relations", [])],
    "REL_TO": lambda p, l: [("Relation", "Entity", r["id"], r["to"], {}) for r in p.get("relations", [])],
    "MAPPED_TO": mapped_to,
    "DERIVED_FROM": lambda p, l: [("Table", "Table", x["table"], src, {"note": x.get("note")}) for x in p.get("lineage", [])
                                  for src in x["derived_from"]],
    "NOT_JOINABLE_TO": lambda p, l: [("Column", "Table", x["column"], x["table"], {k: x[k] for k in ("asserted_by", "asserted_on", "note")})
                                     for x in p.get("not_joinable", [])],
    "CERTIFIED_JOIN": lambda p, l: [("Source", "Source", f["between"][0], f["between"][1],
                                     {"asserted_by": f["asserted_by"], "asserted_on": f["asserted_on"], "note": f["note"],
                                      "declared_join": f["join"]}) for f in p.get("certified_joins", [])],
}
FILLS = set(NODES) | set(EDGES)


def cell(value, sql_type: str) -> str:
    """One CSV field of a column of sql_type."""
    if value is None:
        return NULL if sql_type == "STRING" else ""
    if sql_type == "STRING" and isinstance(value, str):
        if value == NULL:
            raise ValueError("the literal \\N cannot be a value: S01 refuses it")
        return value
    if sql_type == "BOOLEAN" and isinstance(value, bool):
        return "true" if value else "false"
    if sql_type == "INT64" and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if sql_type == "DOUBLE" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(float(value))
    raise ValueError(f"{value!r} is not a {sql_type}")


def csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def json_bytes(value) -> bytes:
    """Canonical JSON: sorted keys, one space of indent, a trailing newline."""
    return (json.dumps(value, indent=1, sort_keys=True) + "\n").encode()


def described(t: dict, embedding_dim: int, edge: bool) -> dict:
    """A type as graph-model.yaml gives it: its loaded name, key or ends, layer, origin and typed properties."""
    out = {"loaded_as": t["loaded_as"], **({"logical": t["logical"]} if "logical" in t else {}),
           **({"from": t["from"], "to": t["to"]} if edge else {"key": t["key"]}),
           "layer": t["layer"], "origin": t["origin"],
           "properties": [{"name": name, **{k: v.replace("{embedding_dim}", str(embedding_dim)) if k == "type" else v
                                            for k, v in prop(p).items()}} for name, p in t["properties"].items()]}
    if "constants" in t:
        out["constants"] = dict(t["constants"])
    return out


def roles(p: dict) -> dict:
    """The pack's purposes and role templates, as access/roles.json holds them: each role's reads resolved (all is every
    source, in the pack's order), its writes and its processes, the roles by id."""
    access = p.get("access") or {}
    sources = [s["id"] for s in p["sources"]]
    return {"purposes": list(access.get("purposes", [])),
            "roles": sorted(({"id": r["id"], "label": r["label"], "reads": list(sources) if r["reads"] == "all" else list(r["reads"]),
                              "writes": list(r.get("writes", [])), "processes": list(r["processes"])} for r in access.get("roles", [])),
                            key=lambda r: r["id"])}


class Artifact(NamedTuple):
    files: dict[str, bytes]
    manifest: dict

    @property
    def model_hash(self) -> str:
        return self.manifest["model_hash"]

    def tree(self) -> dict[str, bytes]:
        return {**self.files, MANIFEST: json_bytes(self.manifest)}


def build(pack: Pack, meta: dict, compiler_version: str, catalogue: dict | None = None) -> Artifact:
    """The artifact for pack, which has passed its checks, against meta, in memory."""
    p, lock = pack.data, pack.lock
    model = merged(meta, p["schema"].get("extensions") or [])
    model["node_types"]["Process"]["constants"] = process_vocabulary(model["node_types"]["Process"].get("constants"))
    nodes, edges = model["node_types"], model["edge_types"]
    dim, id_scheme = p["manifest"]["compile"]["embedding_dim"], p["manifest"]["compile"]["id_scheme"]
    about = {"format": FORMAT, "client": pack.client, "title": p["manifest"]["title"],
             "pack": {"id": p["manifest"]["id"], "version": p["manifest"]["version"], "hash": pack.hash},
             "lock_hash": pack.lock_hash, "meta_model_version": meta["version"], "compiler_version": compiler_version,
             "target": p["manifest"]["compile"]["store"], "params": {"embedding_dim": dim, "id_scheme": id_scheme}}
    files: dict[str, bytes] = {}
    order: list[dict] = []
    counts: dict = {"node_types": len(nodes), "edge_types": len(edges), "nodes": {}, "edges": {}}
    keys: dict[str, set[str]] = {}
    schema: dict = {"node_types": {}, "edge_types": {}}
    for name, t in nodes.items():
        entry = described(t, dim, edge=False)
        if t["origin"] in EMITTED:
            props = list(t["properties"])
            types = [prop(t["properties"][x])["type"] for x in props]
            rows = [[cell(r.get(x), ty) for x, ty in zip(props, types)] for r in NODES.get(name, lambda p, l: [])(p, lock)]
            k = props.index(t["key"])
            rows.sort(key=lambda r: (r[k], r))
            keys[name] = {r[k] for r in rows}
            if len(keys[name]) != len(rows):
                raise ValueError(f"{name}: a key is given twice (S02 refuses it)")
            path = f"data/nodes/{t['loaded_as']}.csv"
            files[path] = csv_bytes(props, rows)
            order.append({"path": path, "table": t["loaded_as"], "rows": len(rows), "stage": "copy"})
            counts["nodes"][t["loaded_as"]] = len(rows)
            entry.update(file=path, rows=len(rows))
        else:
            entry["filled_by"] = FILLED_BY[t["origin"]]
        schema["node_types"][name] = entry
    for name, e in edges.items():
        entry = described(e, dim, edge=True)
        if e["origin"] not in EMITTED:
            entry["filled_by"] = FILLED_BY[e["origin"]]
            schema["edge_types"][name] = entry
            continue
        props = list(e["properties"])
        types = [prop(e["properties"][x])["type"] for x in props]
        built = EDGES.get(name, lambda p, l: [])(p, lock)
        entry["files"], total = [], 0
        for a in ends(e["from"]):
            for b in ends(e["to"]):
                rows = sorted([cell(r[2], "STRING"), cell(r[3], "STRING"), *(cell(r[4].get(x), ty) for x, ty in zip(props, types))]
                              for r in built if (r[0], r[1]) == (a, b))
                unfilled = [x for x in (a, b) if x not in keys]
                stage = nodes[unfilled[0]]["origin"] if unfilled else "copy"
                if not unfilled and any(r[0] not in keys[a] or r[1] not in keys[b] for r in rows):
                    raise ValueError(f"{name}: an edge ends at a node the artifact does not hold (the checks refuse it)")
                path = f"data/edges/{e['loaded_as']}__{nodes[a]['loaded_as']}__{nodes[b]['loaded_as']}.csv"
                files[path] = csv_bytes(["from", "to", *props], rows)
                order.append({"path": path, "table": e["loaded_as"], "from": nodes[a]["loaded_as"], "to": nodes[b]["loaded_as"],
                              "rows": len(rows), "stage": stage})
                entry["files"].append({"from": nodes[a]["loaded_as"], "to": nodes[b]["loaded_as"], "file": path, "rows": len(rows),
                                       "stage": stage})
                total += len(rows)
        if total != len(built):
            raise ValueError(f"{name}: an edge joins types the meta-model does not pair")
        counts["edges"][e["loaded_as"]] = total
        schema["edge_types"][name] = entry
    files["ddl/kuzu.cypher"] = "".join(f"{s};\n" for s in ddl(model, dim)).encode()
    files["access/roles.json"] = json_bytes(roles(p))
    from kge.compiler.catalogue import bytes_of
    files["catalogue.yaml"] = bytes_of(catalogue)
    if pack.drafts:
        files["drafts.json"] = json_bytes({"format": 1, "items": pack.drafts,
                                           **({"warehouses": dict(sorted(pack.warehouses.items()))} if pack.warehouses else {})})
    if p["manifest"].get("gates") is not None:
        files["gates.json"] = json_bytes(p["manifest"]["gates"])
    files["graph-model.yaml"] = yaml.safe_dump({**about, **schema, "extensions": list(p["schema"].get("extensions") or []),
                                                "join_candidates": candidates(pack), "counts": counts}, **YAML).encode()
    digests = {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())}
    listing = "".join(f"{digest}  {path}\n" for path, digest in digests.items())
    return Artifact(files, {**about, "files": digests, "counts": counts, "load_order": order,
                            "model_hash": hashlib.sha256(listing.encode()).hexdigest()})


class Clash(RuntimeError):
    """A folder is already under the artifact's hash, holding other bytes."""


def differences(folder: Path, want: dict[str, bytes]) -> list[str]:
    """How folder differs from the files want holds: missing, extra or other bytes."""
    have = {p.relative_to(folder).as_posix(): p for p in sorted(folder.rglob("*")) if p.is_file()}
    return ([f"{path} is missing" for path in sorted(set(want) - set(have))]
            + [f"{path} is not one of its files" for path in sorted(set(have) - set(want))]
            + [f"{path} differs" for path in sorted(set(want) & set(have)) if have[path].read_bytes() != want[path]])


def write(artifact: Artifact, out: Path, client: str) -> tuple[Path, bool]:
    """The artifact at <out>/<client>/<model_hash>/, and whether this call wrote it: False when it was there already,
    or a compile beside this one wrote the same bytes first.

    Clash, and nothing written, if a folder is there under that hash with other bytes."""
    folder = Path(out) / client / artifact.model_hash
    want = artifact.tree()
    if folder.exists() or folder.is_symlink():
        differ = differences(folder, want) if folder.is_dir() else ["it is not a folder"]
        if differ:
            more = "; and more" if len(differ) > 6 else ""
            raise Clash(f"{folder} is there already and does not hold what this compile writes ({'; '.join(differ[:6])}{more}): "
                        "nothing was written. Move it aside to compile it again")
        return folder, False
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = folder.parent / f".{artifact.model_hash}.{uuid.uuid4().hex[:12]}"
    try:
        for path, data in want.items():
            (tmp / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp / path).write_bytes(data)
        try:
            os.rename(tmp, folder)
        except OSError:
            if folder.is_dir() and not differences(folder, want):
                return folder, False
            raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return folder, True
