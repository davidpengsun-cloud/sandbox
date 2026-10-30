"""The per-row loader the graph was built with before it loaded the compiled pack, kept as the parity oracle.

    python3 -m kge.graph.legacy [--profile <id>] [--release current|<release_id>]

Only the parity test (tests/integration/test_parity.py) runs it. It builds the graph as
kge/graph/load.py did before issue #39 compiled the pack: row by row, with the DDL
of that time frozen here, from the same landed release (each unit's catalogue,
the document index and certification, read as kge/graph/load.py reads them) and
from the curated files the estate writes beside its warehouses (units.json,
glossary.csv, mappings.csv, measures.csv, no_join.json, principals.json: the
profile's estate(), kge/pipeline/profile.py), into the profile's graph: the one in
the folder of the artifact its current pointer names, whose LOADED entry for
this store goes first (kge/graph/load.py open_graph), since the graph built here is
not the artifact's. The graph the compiled artifact loads must hold the same
rows, every property, '' apart from NULL. A certified metric definition
defines the term its id spells (MD-<NAME>, a -V1 too), as it always did; the
artifact's DEFINES rows are the pack's defined_by instead.

NODE_TABLES and REL_TABLES are the DDL before issue #39, frozen: the run
trace's legacy model digest is taken over them (LEGACY_DDL). RELEASE_TABLES is
the Release node issue #39 added first, which the pipeline reads its chunks
from, so the pipeline runs on this graph as on the artifact's. In that DDL a
document's cert_from and cert_to bound the window it was in force ('' for
open), and the warehouse's declared foreign keys run referencing to referenced,
at column grain and again at table grain, so a join path is a walk over tables
with the columns it uses as edge properties.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

from kge.adapters.model import DIM as EMBED_DIM

NODE_TABLES = [
    "CREATE NODE TABLE Unit(id STRING, label STRING, key_column STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Tbl(id STRING, unit STRING, name STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Col(id STRING, unit STRING, tbl STRING, name STRING, data_type STRING, description STRING, "
    "is_key BOOLEAN, certified BOOLEAN, grain STRING, currency STRING, calendar STRING, conversion STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Term(name STRING, domain STRING, steward STRING, definition STRING, PRIMARY KEY (name))",
    "CREATE NODE TABLE Document(id STRING, title STRING, shelf STRING, kind STRING, unit STRING, owner STRING, "
    "version STRING, published_at STRING, ai_ready BOOLEAN, tier STRING, cert_evidence STRING, cert_policy STRING, "
    "cert_version STRING, cert_from STRING, cert_to STRING, PRIMARY KEY (id))",
    f"CREATE NODE TABLE Chunk(id STRING, doc_id STRING, ordinal INT64, text STRING, readiness STRING, purposes STRING[], "
    f"embedding FLOAT[{EMBED_DIM}], embedding_model STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Tag(id STRING, family STRING, value STRING, generator STRING, confidence DOUBLE, model STRING, "
    "prompt_version STRING, created_at STRING, evidence STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Principal(id STRING, role STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE IF NOT EXISTS Process(id STRING, description STRING, PRIMARY KEY (id))",
]

REL_TABLES = [
    "CREATE REL TABLE HAS_TABLE(FROM Unit TO Tbl)",
    "CREATE REL TABLE HAS_COLUMN(FROM Tbl TO Col)",
    "CREATE REL TABLE JOINS(FROM Col TO Col, via STRING)",
    "CREATE REL TABLE JOINS_TABLE(FROM Tbl TO Tbl, from_table STRING, from_column STRING, to_table STRING, to_column STRING)",
    "CREATE REL TABLE MAPS_TO(FROM Col TO Term, certified BOOLEAN, certified_by STRING, certified_on STRING, "
    "generator STRING, confidence DOUBLE)",
    "CREATE REL TABLE NO_JOIN(FROM Unit TO Unit, asserted_by STRING, asserted_on STRING, note STRING)",
    "CREATE REL TABLE CAN_READ(FROM Principal TO Unit)",
    "CREATE REL TABLE CAN_WRITE(FROM Principal TO Term)",
    "CREATE REL TABLE IF NOT EXISTS MAY_PERFORM(FROM Principal TO Process)",
    "CREATE REL TABLE HAS_CHUNK(FROM Document TO Chunk)",
    "CREATE REL TABLE DEFINES(FROM Document TO Term)",
    "CREATE REL TABLE SUPERSEDED_BY(FROM Document TO Document)",
    "CREATE REL TABLE MENTIONS(FROM Chunk TO Term, confidence DOUBLE, generator STRING)",
    "CREATE REL TABLE TAGGED(FROM Chunk TO Tag)",
    "CREATE REL TABLE SUPERSEDES(FROM Tag TO Tag)",
    "CREATE REL TABLE PROPOSES(FROM Tag TO Col, term STRING)",
]

LEGACY_DDL = NODE_TABLES + REL_TABLES
RELEASE_TABLES = ["CREATE NODE TABLE Release(id STRING, client STRING, policy STRING, as_of STRING, PRIMARY KEY (id))"]
DDL = LEGACY_DDL + RELEASE_TABLES


def physical(release, unit: str) -> tuple[list[dict], list[dict], list[tuple]]:
    """One unit's tables, columns and declared joins, from the catalogue its warehouse landed. A join is a foreign key's
    first column and the column it references."""
    cat = release.structured(unit)
    tables = [{"name": t["name"]} for t in cat["tables"]]
    columns = [{"table": t["name"], "name": c["name"], "type": c["type"], "description": c["comment"] or "",
                "is_key": c["name"] in t["primary_key"]} for t in cat["tables"] for c in t["columns"]]
    joins = [(t["name"], k["columns"][0], k["ref_table"], k["ref_columns"][0]) for t in cat["tables"] for k in t["foreign_keys"]]
    return tables, columns, joins


def main() -> int:
    from kge.graph.load import documents, landed_release, load_principals, open_graph
    from kge.landing.zone import LandingError
    from kge.pipeline.profile import ProfileError, resolve
    from kge.adapters.graphstore import GraphLocked
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose estate, landing and graph (default: PROFILE, else the registry's default)")
    ap.add_argument("--release", default="current", help="the landed release to load: its id, or current (the default)")
    args = ap.parse_args()
    try:
        prof = resolve(args.profile)
        build = prof.estate()
        release = landed_release(prof, args.release)
        conn = open_graph(prof.graph_path(), fresh=True, pidfile=prof.run_dir() / "api.pid")
    except (ProfileError, LandingError, GraphLocked) as exc:
        print(f"kge/graph/legacy.py: {exc}. Nothing was loaded.", file=sys.stderr)
        return 2
    for stmt in DDL:
        conn.execute(stmt)

    units = json.loads((build / "units.json").read_text())
    measures = {}
    with (build / "measures.csv").open() as f:
        for r in csv.DictReader(f):
            measures[(r["unit"], r["table"], r["column"])] = r
    certified = set()
    with (build / "mappings.csv").open() as f:
        mappings = list(csv.DictReader(f))
    for m in mappings:
        certified.add((m["unit"], m["table"], m["column"]))

    counts = {"units": 0, "tables": 0, "columns": 0, "joins": 0, "terms": 0, "mappings": 0, "no_join": 0,
              "principals": 0, "documents": 0, "ai_ready": 0, "defines": 0}

    for unit in units["units"]:
        conn.execute("CREATE (:Unit {id: $id, label: $label, key_column: $key})",
                     {"id": unit, "label": units["labels"][unit], "key": units["keys"][unit]})
        counts["units"] += 1
        tables, cols, joins = physical(release, unit)
        for t in tables:
            tid = f"{unit}.{t['name']}"
            conn.execute("CREATE (:Tbl {id: $id, unit: $unit, name: $name})", {"id": tid, "unit": unit, "name": t["name"]})
            conn.execute("MATCH (u:Unit {id: $unit}), (t:Tbl {id: $tid}) CREATE (u)-[:HAS_TABLE]->(t)", {"unit": unit, "tid": tid})
            counts["tables"] += 1
        for c in cols:
            cid = f"{unit}.{c['table']}.{c['name']}"
            md = measures.get((unit, c["table"], c["name"]), {})
            conn.execute(
                "CREATE (:Col {id: $id, unit: $unit, tbl: $tbl, name: $name, data_type: $dt, description: $description, "
                "is_key: $is_key, certified: $cert, grain: $grain, currency: $currency, calendar: $calendar, conversion: $conversion})",
                {"id": cid, "unit": unit, "tbl": c["table"], "name": c["name"], "dt": c["type"], "description": c["description"],
                 "is_key": c["is_key"], "cert": (unit, c["table"], c["name"]) in certified,
                 "grain": md.get("grain", ""), "currency": md.get("currency", ""), "calendar": md.get("calendar", ""),
                 "conversion": md.get("conversion", "")})
            conn.execute("MATCH (t:Tbl {id: $tid}), (c:Col {id: $cid}) CREATE (t)-[:HAS_COLUMN]->(c)",
                         {"tid": f"{unit}.{c['table']}", "cid": cid})
            counts["columns"] += 1
        for t, c, rt, rc in joins:
            conn.execute("MATCH (a:Col {id: $a}), (b:Col {id: $b}) CREATE (a)-[:JOINS {via: 'declared foreign key'}]->(b)",
                         {"a": f"{unit}.{t}.{c}", "b": f"{unit}.{rt}.{rc}"})
            conn.execute("MATCH (a:Tbl {id: $a}), (b:Tbl {id: $b}) "
                         "CREATE (a)-[:JOINS_TABLE {from_table: $ft, from_column: $fc, to_table: $tt, to_column: $tc}]->(b)",
                         {"a": f"{unit}.{t}", "b": f"{unit}.{rt}", "ft": t, "fc": c, "tt": rt, "tc": rc})
            counts["joins"] += 1

    with (build / "glossary.csv").open() as f:
        for g in csv.DictReader(f):
            conn.execute("CREATE (:Term {name: $name, domain: $domain, steward: $steward, definition: $definition})",
                         {"name": g["term"], "domain": g["domain"], "steward": g["steward"], "definition": g["definition"]})
            counts["terms"] += 1
    for m in mappings:
        conn.execute(
            "MATCH (c:Col {id: $cid}), (t:Term {name: $term}) "
            "CREATE (c)-[:MAPS_TO {certified: true, certified_by: $certified_by, certified_on: $certified_on, generator: 'human', confidence: 1.0}]->(t)",
            {"cid": f"{m['unit']}.{m['table']}.{m['column']}", "term": m["term"], "certified_by": m["certified_by"], "certified_on": m["certified_on"]})
        counts["mappings"] += 1

    for fact in json.loads((build / "no_join.json").read_text()):
        conn.execute("MATCH (a:Unit {id: $a}), (b:Unit {id: $b}) "
                     "CREATE (a)-[:NO_JOIN {asserted_by: $asserted_by, asserted_on: $asserted_on, note: $note}]->(b)",
                     {"a": fact["a"], "b": fact["b"], "asserted_by": fact["asserted_by"], "asserted_on": fact["asserted_on"], "note": fact["note"]})
        counts["no_join"] += 1

    counts["principals"] = load_principals(conn, json.loads((build / "principals.json").read_text()))

    from kge.adapters.graphstore import batch
    with batch(conn) as w:
        docs, counts["documents"], counts["ai_ready"] = documents(w, release)
    term_by_docid = {f"MD-{g['name'].upper().replace(' ', '-')}": g["name"]
                     for g in (conn.execute("MATCH (t:Term) RETURN t.name AS name").get_as_df().to_dict("records"))}
    for d in docs:
        term = term_by_docid.get(d["id"]) or term_by_docid.get(d["id"].replace("-V1", ""))
        if d["shelf"] == "certified" and d["kind"] == "metric_definition" and term:
            conn.execute("MATCH (d:Document {id: $d}), (t:Term {name: $t}) CREATE (d)-[:DEFINES]->(t)", {"d": d["id"], "t": term})
            counts["defines"] += 1

    conn.execute("CREATE (:Release {id: $id, client: $client, policy: $policy, as_of: $as_of})", release.about())
    print(json.dumps({**counts, "release": release.id, "client": release.client}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
