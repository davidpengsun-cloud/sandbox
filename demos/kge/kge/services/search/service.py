"""Deterministic discovery using certified term mappings, lineage and governed retrieval."""
from __future__ import annotations

import re

from kge.pipeline.settings import search_passage_min_similarity
from kge.services import identity
from kge.services.ask import Engine
from kge.services.search.models import Hit, Search, SearchResponse, Why


def words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def contains(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def passage_text(text: str, query: str, width: int = 240) -> str:
    """Show the matching phrase or token, or the opening of a semantic-only match."""
    tokens = re.findall(r"[^\W_]+", query)
    patterns = [r"[\W_]+".join(map(re.escape, tokens)), *map(re.escape, tokens)]
    matches = [match for pattern in patterns if pattern
               if (match := re.search(r"\b" + pattern + r"\b", text, re.IGNORECASE))]
    start = max(0, matches[0].start() - 60) if matches else 0
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")


def search(engine: Engine, who: str, request: Search) -> SearchResponse:
    """Highest evidence wins per kind/unit/id; ties sort by kind, unit and id.

    Exact terms score 1, synonyms .95, partial phrases .9, metadata .8,
    one lineage hop .6 of its source, and positive passages at most .7.
    Terms shared across units appear once per readable unit. Documents must
    belong to a readable unit, even when retrieval permits unscoped documents.
    """
    units = identity.readable_units(engine, who)
    if not units or request.kinds == []:
        return SearchResponse(hits=[])
    g = engine.g
    domain = g.domain()
    restricted = {".".join(column) for column in domain["restricted"]}
    query = words(request.query)
    if not query:
        return SearchResponse(hits=[])
    kinds = set(request.kinds or ("product", "term", "metric", "column", "document"))
    hits: dict[tuple[str, str, str], Hit] = {}

    def add(kind, unit, identifier, title, score, why):
        if unit not in units:
            return
        hit = Hit(kind=kind, unit=unit, id=identifier, title=title, score=round(score, 6), why=why,
                  restricted=kind == "column" and identifier in restricted)
        key = kind, unit, identifier
        old = hits.get(key)
        if old is None or (-hit.score, hit.why.kind != "name", hit.why.model_dump_json()) < (-old.score, old.why.kind != "name", old.why.model_dump_json()):
            hits[key] = hit

    tables = {row["id"]: row for row in g.q(
        "MATCH (t:Tbl) WHERE t.unit IN $units RETURN t.id AS id, t.unit AS unit, t.name AS title ORDER BY t.id",
        {"units": units})}
    columns = g.q(
        "MATCH (t:Tbl)-[:HAS_COLUMN]->(c:Col) WHERE t.unit IN $units AND c.unit IN $units "
        "RETURN c.id AS id, c.unit AS unit, c.name AS title, c.description AS description, t.id AS product ORDER BY c.id",
        {"units": units})
    for row in [*tables.values(), *columns]:
        for reason, text in (("name", row["title"]), ("description", row.get("description") or "")):
            if contains(words(text), query):
                add("column" if "product" in row else "product", row["unit"], row["id"], row["title"], .8,
                    Why(kind=reason, text=text))

    terms = g.q("MATCH (t:Term) RETURN t.id AS id, t.name AS name ORDER BY t.name")
    aliases = domain["aliases"]
    for term in terms:
        name = term["name"]
        evidence = []
        for phrase in sorted({name, *(phrase for phrase, target in aliases.items() if target == name)}):
            normalized = words(phrase)
            if contains(query, normalized) or contains(normalized, query):
                synonym = normalized != words(name)
                score = (.95 if synonym else 1.0) if query == normalized else .9
                evidence.append((score, phrase, synonym))
        if not evidence:
            continue
        score, phrase, synonym = sorted(evidence, key=lambda item: (-item[0], item[2], item[1]))[0]
        why = Why(kind="synonym" if synonym else "term", text=phrase, term=name)
        for column in g.implementations(name):
            unit = column["unit"]
            if unit not in units:
                continue
            product = f"{unit}.{column['tbl']}"
            identifier = f"{product}.{column['col']}"
            add("term", unit, term["id"] or name, name, score, why)
            add("column", unit, identifier, column["col"], score, why)
            if product in tables:
                add("product", unit, product, tables[product]["title"], score,
                    why.model_copy(update={"via": [identifier]}))
        measures = g.q(
            "MATCH (m:Measure)-[:MEASURE_OF]->(t:Term {name: $term}), (m)-[:COMPUTED_FROM]->(c:Col) "
            "RETURN m.id AS id, c.unit AS unit, c.id AS col ORDER BY m.id, c.id", {"term": name})
        grouped: dict[str, list[dict]] = {}
        for row in measures:
            grouped.setdefault(row["id"], []).append(row)
        for identifier, sources in grouped.items():
            if any(row["unit"] not in units for row in sources):
                continue
            for unit in sorted({row["unit"] for row in sources}):
                add("metric", unit, identifier, name, score, why)
                add("term", unit, term["id"] or name, name, score, why)
        for context in engine.semantics.contexts_of(name):
            required = {context.unit, *(table.split(".", 1)[0] for join in context.joins
                                       for table in (join.source, join.target)),
                        *(rule.table.split(".", 1)[0] for rule in (*context.filters, *context.selectors))}
            if not required <= set(units):
                continue
            add("term", context.unit, term["id"] or name, name, score, why)
            add("metric", context.unit, context.measure, name, score, why)
            if context.fact in tables:
                add("product", context.unit, context.fact, tables[context.fact]["title"], score,
                    why.model_copy(update={"via": [context.id]}))

    direct = dict(hits)
    for link in g.q(
        "MATCH (a:Tbl)-[:DERIVED_FROM]->(b:Tbl) WHERE a.unit IN $units AND b.unit IN $units "
        "RETURN a.id AS derived, b.id AS source ORDER BY a.id, b.id", {"units": units}):
        for source, target in ((link["source"], link["derived"]), (link["derived"], link["source"])):
            if source not in tables or target not in tables:
                continue
            origin = direct.get(("product", tables[source]["unit"], source))
            if origin:
                row = tables[target]
                add("product", row["unit"], target, row["title"], origin.score * .6,
                    origin.why.model_copy(update={"via": [*origin.why.via, source]}))

    if "document" in kinds:
        passages = g.retrieve(request.query, who, "analysis", k=100)["exact"]
        documents = g.certification([row["doc"] for row in passages])
        floor = search_passage_min_similarity()
        passages = [row for row in passages if row["doc"] in documents
                    and documents[row["doc"]]["unit"] in units and row["score"] > 0 and row["score"] >= floor]
        texts = {row["id"]: row["text"] for row in g.q(
            "MATCH (c:Chunk) WHERE c.id IN $ids RETURN c.id AS id, c.text AS text",
            {"ids": [row["chunk"] for row in passages]})} if passages else {}
        for row in passages:
            document = documents[row["doc"]]
            add("document", document["unit"], row["doc"], row["title"], min(row["score"], 1) * .7,
                Why(kind="passage", text=passage_text(texts.get(row["chunk"]) or row["excerpt"], request.query),
                    chunk=row["chunk"], similarity=row["score"]))
    ranked = sorted((hit for hit in hits.values() if hit.kind in kinds),
                    key=lambda hit: (-hit.score, hit.kind, hit.unit, hit.id))
    return SearchResponse.model_validate({"hits": [hit.model_dump() for hit in ranked[:request.limit]]})
