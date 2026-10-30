"""Sync estate tables and chunk vectors into BigQuery datasets (Issue #70).

Syncs the estate's warehouses, where they are DuckDB's, into BigQuery dataset (where WAREHOUSE_BACKEND=bigquery made
them there already, nothing is copied: kge/adapters/warehouse/bigquery_estate.py):
  {project}.{dataset_prefix}_{client}_warehouses
Syncs chunk vectors into BigQuery dataset:
  {project}.{dataset_prefix}_{client}_graph.chunks

Usage:
  python3 -m kge.estate.load_bigquery [--project PROJECT] [--client CLIENT] [--dry-run]

Without --prefix, the BIGQUERY_DATASET_PREFIX that Terraform sets wins over the default.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from kge.adapters.bigquery import (
    BATCH_SIZE,
    BIGQUERY_COLUMN_MAX,
    BIGQUERY_TABLE_MAX,
    CLOUD_PLATFORM,
    DEFAULT_DATASET_PREFIX,
    UNIT_LABEL,
    VERSION,
    VERSION_LABEL,
    BigQueryLoader,
    _bigquery_identifier,
    _identifier,
    _positive_env,
    dataset_name,
    map_duckdb_type_to_bigquery,
    serialize_val,
)
from kge.adapters import building_candidate
from kge.adapters.retrieval import graph_holds_vectors, name_chunks, named_chunks, new_chunks_table
from kge.adapters.warehouse import estate_of, open_estate
from kge.pipeline.tagger import EMBEDDABLE
from kge.services.ask.engine.core import Graph
from kge.pipeline.profile import Profile, resolve

__all__ = ["BATCH_SIZE", "BIGQUERY_COLUMN_MAX", "BIGQUERY_TABLE_MAX", "CLOUD_PLATFORM", "DEFAULT_DATASET_PREFIX",
           "UNIT_LABEL", "VERSION", "VERSION_LABEL", "BigQueryLoader", "_bigquery_identifier", "_identifier",
           "_positive_env", "dataset_name", "map_duckdb_type_to_bigquery", "serialize_val", "sync_warehouses",
           "sync_graph", "main"]


def sync_warehouses(loader: BigQueryLoader, prof: Profile, client: str) -> dict:
    """Sync the client's warehouses into its BigQuery dataset, read through the estate's warehouse port (the factory's,
    for WAREHOUSE_BACKEND). Where they are that dataset's tables already (WAREHOUSE_BACKEND=bigquery: the estate and
    onboarding made them there) nothing is copied and nothing is read from disk: only labelled tables of a unit or
    version the dataset's catalogue no longer holds are dropped. Otherwise (DuckDB's warehouses, with only retrieval in
    BigQuery) each is copied: an established unit's as <unit>_<table>, and each onboarded unit's versions that retention
    keeps (any kept release's and the live one) each as their own tables, <unit>_v<N>_<table>, labelled with the unit and
    the version (VERSION_LABEL, kge/pipeline/warehouse_versions.py), so every hash that may be served or rolled back to
    finds its rows (kge/adapters/warehouse/bigquery.py BigQueryWarehouses)."""
    loader.account()
    dataset_id = dataset_name(loader.dataset_prefix, client, "warehouses")
    loader.ensure_dataset(dataset_id)

    estate = estate_of(prof)
    if estate.kept_in(dataset_id):
        if loader.dry_run:
            return {}
        held = estate.held()
        units = set(estate.established())
        if units or held:
            loader.prune_units(dataset_id, units, {u: {f"v{n}" for n in vs} for u, vs in held.items()})
        return {(f"{u}_v{n}" if n else u): {"tables": None, "rows": None, "in_place": True}
                for u, n in [*((u, 0) for u in sorted(units)), *((u, n) for u, vs in held.items() for n in vs)]}
    candidates = [estate, open_estate(prof.estate() / "warehouses", client), open_estate(Path("build/warehouses"), client)]
    estate = next((e for e in candidates if e.present()), None)
    if estate is None:
        raise FileNotFoundError(f"no warehouses found in {prof.warehouses} (or fallbacks)")

    targets = [(unit, None) for unit in estate.established()]
    for unit, found in estate.held().items():
        targets += [(unit, n) for n in found]

    report: dict[str, dict] = {}
    for unit, number in targets:
        _identifier(unit, "unit")
        version = f"v{number}" if number else None
        prefix = f"{unit}_{version}" if version else unit
        con = estate.open(unit, number)
        try:
            tables = con.tables()
            report[prefix] = {"tables": len(tables), "rows": 0}
            for tbl in tables:
                _bigquery_identifier(tbl, "table", BIGQUERY_TABLE_MAX)
                col_info = [(c["name"], c["type"]) for c in con.describe(tbl)["columns"]]
                for column, _ in col_info:
                    _bigquery_identifier(column, "column", BIGQUERY_COLUMN_MAX)
                schema_fields = [{"name": c, "type": map_duckdb_type_to_bigquery(t), "mode": "NULLABLE"} for c, t in col_info]
                col_names = [c for c, _ in col_info]
                db_rows = con.execute(f'SELECT * FROM "{tbl}"').fetchall()
                json_rows = [{col_names[idx]: serialize_val(val) for idx, val in enumerate(r)} for r in db_rows]
                bq_table = f"{prefix}_{tbl}"
                loader.create_table(dataset_id, bq_table, schema_fields, unit=unit, version=version)
                loader.load_rows(dataset_id, bq_table, schema_fields, json_rows)
                report[prefix]["rows"] += len(json_rows)
        finally:
            con.close()
    versions: dict[str, set[str]] = {}
    for unit, number in targets:
        if number:
            versions.setdefault(unit, set()).add(f"v{number}")
    loader.prune_units(dataset_id, {unit for unit, number in targets if not number}, versions)
    return report


def sync_graph(loader: BigQueryLoader, prof: Profile, client: str, embedder: Any = None) -> dict:
    """The graph's chunks (the hash prof is pinned to) written to a table of their own, in the client's graph dataset (or
    its candidates dataset, GRAPH_DATASET), which the graph's pointer then names (kge/adapters/retrieval new_chunks_table,
    name_chunks): never the table a served graph searches. A graph whose pointer names its chunks already (synced since
    its load, as a release's sync finds it) is left as it is: nothing is read, embedded or written. Where retrieval is BigQuery's
    (not graph_holds_vectors), the graph store holds no vectors (kge/pipeline/state.py rederive): each ready chunk, one
    whose readiness is citable or retrieval-internal (EMBEDDABLE; none yet, or any other, is never sent), is embedded here, with the graph's embedder (embedder, for a test), at its
    own width (EMBEDDING_DIM for Vertex AI, 768 unset), and a chunk that is not ready is never sent to it. A dry run
    embeds nothing and counts the chunks it would. Otherwise the vectors the graph store holds are copied, as before."""
    loader.account()
    held = named_chunks(prof) if getattr(prof, "pinned", None) else None
    if held is not None:
        return {"total_chunks": 0, "embedded": 0, "table": held, "kept": True}
    g = Graph(prof)
    embedder = embedder or g.embedder
    direct = not graph_holds_vectors()
    try:
        rows = g.q(
            "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) "
            "OPTIONAL MATCH (c)-[:MENTIONS]->(t:Term) "
            "RETURN d.id AS doc, d.title AS title, d.shelf AS shelf, d.unit AS unit, d.tier AS tier, "
            "d.ai_ready AS ai_ready, c.id AS chunk, c.readiness AS readiness, "
            "c.purposes AS purposes, c.embedding AS embedding, c.embedding_model AS embedding_model, "
            "collect(t.name) AS terms, c.text AS text, left(c.text, 200) AS excerpt "
            "ORDER BY chunk"
        )
    finally:
        g.close()

    schema_fields = [
        {"name": "chunk", "type": "STRING", "mode": "REQUIRED"},
        {"name": "doc", "type": "STRING", "mode": "NULLABLE"},
        {"name": "title", "type": "STRING", "mode": "NULLABLE"},
        {"name": "shelf", "type": "STRING", "mode": "NULLABLE"},
        {"name": "unit", "type": "STRING", "mode": "NULLABLE"},
        {"name": "tier", "type": "STRING", "mode": "NULLABLE"},
        {"name": "ai_ready", "type": "BOOL", "mode": "NULLABLE"},
        {"name": "readiness", "type": "STRING", "mode": "NULLABLE"},
        {"name": "purposes", "type": "STRING", "mode": "REPEATED"},
        {"name": "embedding", "type": "FLOAT64", "mode": "REPEATED"},
        {"name": "embedding_model", "type": "STRING", "mode": "NULLABLE"},
        {"name": "terms", "type": "STRING", "mode": "REPEATED"},
        {"name": "text", "type": "STRING", "mode": "NULLABLE"},
        {"name": "excerpt", "type": "STRING", "mode": "NULLABLE"},
    ]

    records = []
    embedded_count = 0
    for r in rows:
        emb, model = r.get("embedding"), r.get("embedding_model") or ""
        if direct:
            emb, model = None, ""
            if r.get("readiness") in EMBEDDABLE:
                model = embedder.name
                if loader.dry_run:
                    embedded_count += 1
                else:
                    emb = embedder.embed(r.get("text") or "")
        if emb is not None:
            emb_list = [float(x) for x in emb]
            embedded_count += 1
        else:
            emb_list = []
        raw_terms = r.get("terms") or []
        terms = [t for t in raw_terms if t]
        records.append({
            "chunk": r["chunk"],
            "doc": r.get("doc"),
            "title": r.get("title"),
            "shelf": r.get("shelf"),
            "unit": r.get("unit") or "",
            "tier": r.get("tier"),
            "ai_ready": bool(r.get("ai_ready")),
            "readiness": r.get("readiness"),
            "purposes": r.get("purposes") or [],
            "embedding": emb_list,
            "embedding_model": model,
            "terms": terms,
            "text": r.get("text") or "",
            "excerpt": r.get("excerpt") or "",
        })

    dataset_id, table = new_chunks_table(prof, getattr(prof, "pinned", None))
    loader.ensure_dataset(dataset_id)
    loader.create_table(dataset_id, table, schema_fields)
    loader.load_rows(dataset_id, table, schema_fields, records)
    if not loader.dry_run:
        name_chunks(prof, dataset_id, table)

    return {"total_chunks": len(records), "embedded": embedded_count, "table": f"{dataset_id}.{table}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync estate tables and chunk vectors into BigQuery.")
    parser.add_argument("--profile", help="Deployment profile (default: PROFILE env var, else registry default)")
    parser.add_argument("--project", help="Google Cloud project ID")
    parser.add_argument("--client", help="Client name (e.g. meridian)")
    parser.add_argument("--prefix", help="Dataset prefix (default: BIGQUERY_DATASET_PREFIX, else kge)")
    parser.add_argument("--hash", help="Model hash to sync graph from (defaults to HASH env var or candidate artifact)")
    parser.add_argument("--dry-run", action="store_true", help="Print sync actions without executing BigQuery API calls")
    args = parser.parse_args(argv)

    prof = resolve(args.profile)
    h = (args.hash or os.getenv("HASH", "")).strip() or None
    if not h:
        try:
            import subprocess
            art = subprocess.check_output([sys.executable, "-m", "kge.compiler.compile", "--check"]).decode().strip()
            if art:
                h = Path(art).name
        except Exception:
            pass
    if h:
        try:
            prof = prof.at(h)
        except Exception:
            pass

    client = args.client or prof.client
    loader = BigQueryLoader(project=args.project, dataset_prefix=args.prefix, dry_run=args.dry_run, client=client)
    _, project = loader.account()
    w_dataset = dataset_name(loader.dataset_prefix, client, "warehouses")

    print(f"Syncing estate to BigQuery (project={project}, client={client}, hash={prof.pinned or 'current'}):")
    w_report = {} if building_candidate() else sync_warehouses(loader, prof, client)
    total_tables = sum(u["tables"] or 0 for u in w_report.values())
    total_rows = sum(u["rows"] or 0 for u in w_report.values())
    print(f"  Warehouses: dataset `{w_dataset}` with {total_tables} tables across {len(w_report)} units, {total_rows} total rows")
    for u, stats in w_report.items():
        print(f"    - {u}: {stats['tables']} tables, {stats['rows']} rows")

    g_report = sync_graph(loader, prof, client)
    if g_report.get("kept"):
        print(f"  Graph: `{g_report['table']}` already holds this graph's chunks: nothing synced, nothing embedded")
    else:
        print(f"  Graph: `{g_report['table']}` with {g_report['total_chunks']} chunks ({g_report['embedded']} embedded)")
    if args.dry_run:
        print("  Dry-run complete: no network requests sent.")
    else:
        print("  Sync successfully complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
