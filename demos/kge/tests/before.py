"""What a graph held before the Wealth expansion, read off a graph built since: #38's A/B baseline's two hashes.

#38's A/B baseline (clients/meridian/baselines/issue-38.json) recorded the graph's model and overlay
hashes before issue #39 changed anything. The Wealth expansion (#39 item 7b)
adds to the estate, so a graph built since holds more: the expansion's tables
and columns, and the chunks of what it adds to two data dictionaries. Pack
1.1.0 (7c) adds eight Wealth terms, whose mentions and tags the pipeline finds,
and replaces one no-join fact, retail-wealth's, with a certified join (the
owner's Option B); pack 1.3.0 (issue #40's pack-only test) adds assets under
management, a term 4d9d5c9 did not have either. Taking the additions out of
what the graph holds, and putting the replaced fact back, must leave what the
baseline recorded, and these read it that way, so a test still holds a graph to
the baseline:

  legacy_model(g)  the legacy model digest (kge/services/ask/engine/runtrace.py), over the tables and
                   columns 4d9d5c9 had (clients/meridian/baselines/estate-4d9d5c9.json),
                   the terms it had (tests/fixtures/spec_4d9d5c9.json), and its
                   no-join facts, retail-wealth's from the same fixture
  overlay(g)       the overlay hash, over the chunks 4d9d5c9 had, each with the
                   raw text it had then (documents land masked since item 1;
                   the data dictionaries' first chunks name the cross-reference
                   since 7b), without the chunks 7b added, and without the
                   mentions and tags of the terms 7c and 1.3.0 added

Since pack 1.5.0 Wealth's tables hold physical names in SAGE's style (kge/estate/names.py); each reads what the graph
holds of 4d9d5c9's tables under the names they had then (business), so the baseline's hashes still hold.

Reads the graph and the build's raw documents; writes nothing.
"""
from __future__ import annotations

import json

from tests.scratch import BUILD

DEMO = BUILD.parent
SNAPSHOT = DEMO / "clients" / "meridian" / "baselines" / "estate-4d9d5c9.json"
FROZEN = DEMO / "tests" / "fixtures" / "spec_4d9d5c9.json"


def terms_then() -> set[str]:
    """The glossary's terms at 4d9d5c9, by name."""
    return {g["term"] for g in json.loads(FROZEN.read_text())["GLOSSARY"]}


def replaced_no_join() -> list[list]:
    """The no-join facts 4d9d5c9 had that a certified join replaced since (Option B: retail-wealth), as the legacy no_join
    layer reads a fact: from, to, asserted by, on, note."""
    return [[f["a"], f["b"], f["asserted_by"], f["asserted_on"], f["note"]] for f in json.loads(FROZEN.read_text())["NO_JOIN_FACTS"]
            if {f["a"], f["b"]} == {"retail", "wealth"}]


def business_names() -> tuple[dict[str, str], dict[str, str]]:
    """Wealth's kept tables and their columns, by the physical ids pack 1.5.0 gives them, under the ids 4d9d5c9 knew them
    by: ({unit.TABLE: unit.table}, {unit.TABLE.COLUMN: unit.table.column})."""
    from kge.estate.names import MERIDIAN
    tables = {f"wealth.{physical}": f"wealth.{business}" for business, (physical, _) in MERIDIAN.items()}
    columns = {f"wealth.{physical}.{c}": f"wealth.{business}.{b}" for business, (physical, cols) in MERIDIAN.items() for b, c in cols.items()}
    return tables, columns


def business(name: str, row: list) -> list:
    """A row of a legacy model layer, or an overlay's proposal, with Wealth's physical ids put back as 4d9d5c9 named
    them (business_names), so its digest is the one a graph before pack 1.5.0 gave."""
    tables, columns = business_names()
    t = lambda x: tables.get(x, x)  # noqa: E731
    c = lambda x: columns.get(x, x)  # noqa: E731
    short = {k.rsplit(".", 1)[1]: v.rsplit(".", 1)[1] for k, v in columns.items()}
    if name == "units":
        return [row[0], row[1], short.get(row[2], row[2]) if row[0] == "wealth" else row[2]]
    if name == "tables":
        return [row[0], t(row[1]), t(row[1]).rsplit(".", 1)[1] if row[1] in tables else row[2]]
    if name == "columns":
        return [t(row[0]), c(row[1]), *row[2:]]
    if name == "joins":
        return [t(row[0]), t(row[1]), columns.get(f"{row[0]}.{row[2]}", f"..{row[2]}").rsplit(".", 1)[1],
                columns.get(f"{row[1]}.{row[3]}", f"..{row[3]}").rsplit(".", 1)[1]]
    if name in ("mappings", "proposals"):
        return [row[0], c(row[1]), *row[2:]] if name == "proposals" else [c(row[0]), *row[1:]]
    return row


def tables_then() -> tuple[set[str], set[str]]:
    """The tables (unit.table) and columns (unit.table.column) of 4d9d5c9's estate."""
    snap = json.loads(SNAPSHOT.read_text())["tables"]
    return set(snap), {f"{k}.{c}" for k, t in snap.items() for c in t["columns"]}


def legacy_model(g) -> str:
    """The legacy model digest of what g holds of 4d9d5c9's tables, columns and terms, with the no-join fact Option B
    replaced put back: the baseline's model hash, if nothing it held has moved."""
    import kge.graph.legacy
    from kge.services.ask.engine import runtrace
    tables, columns = tables_then()
    terms = terms_then()
    principals = {p["principal"] for p in json.loads(FROZEN.read_text())["PRINCIPALS"]}
    keep = {"tables": lambda r: r[1] in tables, "columns": lambda r: r[1] in columns,
            "joins": lambda r: r[0] in tables and r[1] in tables, "terms": lambda r: r[0] in terms,
            "mappings": lambda r: r[0] in columns and r[1] in terms, "writes": lambda r: r[0] in principals and r[1] in terms,
            "principals": lambda r: r[0] in principals, "reads": lambda r: r[0] in principals, "processes": lambda r: r[0] in principals and r[1] not in {"drafting", "building", "improving"}}
    with g.reader() as conn:
        layers = [(name, [b for r in conn.execute(cypher).get_all() if keep.get(name, lambda r: True)(b := business(name, r))])
                  for name, cypher in runtrace.MODEL_LAYERS if name in runtrace.LEGACY_LAYERS]
    order = {"tables": lambda r: r[1], "columns": lambda r: r[1], "joins": lambda r: tuple(r[:4]), "mappings": lambda r: (r[0], r[1])}
    layers = [(name, sorted(rows, key=order[name]) if name in order else rows) for name, rows in layers]
    layers = [(name, sorted(rows + [r for r in replaced_no_join() if r not in rows], key=lambda r: (r[0], r[1])) if name == "no_join" else rows)
              for name, rows in layers]
    return runtrace.digest([("ddl", list(kge.graph.legacy.LEGACY_DDL)), ("layers", runtrace.digest(layers))])


def raw_chunks_then() -> dict[str, str]:
    """Every chunk 4d9d5c9 had, by id, with its raw text then: the build's raw documents, cut as certification cuts them,
    but the data dictionaries, as 4d9d5c9 wrote them (their tables then, and no word of a cross-reference)."""
    from kge.estate import certify, documents
    from kge.pipeline.chunking import document_chunks
    schemas = json.loads(FROZEN.read_text())["SCHEMAS"]
    raw = {}
    for d in json.loads((BUILD / "documents.json").read_text()):
        if d["kind"] == "data_dictionary":
            doc = next(x for x in documents.data_dictionaries() if x.doc_id == d["doc_id"])
            old = documents.Doc(doc.shelf, doc.doc_id, doc.title, doc.kind, doc.unit,
                                documents.dictionary_body(doc.unit, schemas[doc.unit], documents.NO_JOIN_SENTENCE),
                                doc.owner, doc.version, doc.published_at)
            texts = document_chunks(old.title, old.text())
        else:
            texts = certify.chunks(BUILD, d)
        raw.update({f"{d['doc_id']}#{i}": t for i, t in enumerate(texts)})
    return raw


def overlay(g) -> tuple[str, int]:
    """(the overlay hash of what g holds of 4d9d5c9's chunks, each with its raw text then, and of its terms; how many
    chunks' landed text differs from that): the baseline's overlay hash, if nothing it held has moved but the text. A
    tag's id starts with its chunk's, which is how a decision's chunks are found."""
    from kge.services.ask.engine import runtrace
    raw = raw_chunks_then()
    _, columns = tables_then()
    terms = terms_then()
    with g.reader() as conn:
        layers = [(name, [business(name, r) for r in conn.execute(cypher).get_all()]) for name, cypher in runtrace.OVERLAY_LAYERS]
    chunk = lambda tag: tag.split("/", 1)[0]  # noqa: E731
    keep = {"chunks": lambda r: r[1] in raw, "tags": lambda r: r[0] in raw and (r[2] != "business_meaning" or r[3] in terms),
            "mentions": lambda r: r[0] in raw and r[1] in terms,
            "decisions": lambda r: chunk(r[0]) in raw and chunk(r[1]) in raw,
            "proposals": lambda r: chunk(r[0]) in raw and r[1] in columns}
    layers = [(name, [r for r in rows if keep[name](r)]) for name, rows in layers]
    layers = [(name, sorted(rows, key=lambda r: (r[0], r[1])) if name == "proposals" else rows) for name, rows in layers]
    differs = sum(r[3] != raw[r[1]] for name, rows in layers if name == "chunks" for r in rows)
    layers = [(name, [[*r[:3], raw[r[1]], *r[4:]] for r in rows] if name == "chunks" else rows) for name, rows in layers]
    return runtrace.digest(layers), differs
