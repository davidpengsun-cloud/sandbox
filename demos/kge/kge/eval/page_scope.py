"""Generated asset-page questions from the served artifact's certified mappings and query semantics."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import yaml

from kge.compiler.emit import NULL
from kge.eval.asking import each


def questions(artifact: Path) -> list[dict]:
    """Two questions per certified term and implementing or supporting table, without changing any frozen set.

    Read the served compiled artifact, so HTTP evaluation need not open the API's graph store. Every declared
    context join counts, including optional reaches and the supporting joins of another context of the same measure.
    """
    artifact = Path(artifact)
    model = yaml.safe_load((artifact / "graph-model.yaml").read_text())

    def rows(path):
        with (artifact / path).open(newline="") as stream:
            return list(csv.DictReader(stream))

    def nodes(name):
        return rows(model["node_types"][name]["file"])

    def edges(name):
        return [row for file in model["edge_types"][name]["files"] for row in rows(file["file"])]

    terms = {t["name"]: t for t in nodes("Term")}
    columns = {c["id"]: c for c in nodes("Column")}
    assets = {}
    implementations = {}
    for mapping in edges("MAPS_TO"):
        if mapping["certified"].lower() == "true":
            col = columns[mapping["from"]]
            table = f"{col['unit']}.{col['tbl']}"
            assets.setdefault(mapping["to"], set()).add(table)
            implementations[(mapping["to"], table)] = col
    context_terms = {e["to"]: e["from"] for e in edges("RESOLVES_TO")}
    measures = {e["from"]: e["to"] for e in edges("SPECIALIZES")}
    facts = {e["from"]: e["to"] for e in edges("USES_FACT")}
    join_tables = {}
    for edge in [*edges("JOIN_FROM"), *edges("JOIN_TO")]:
        join_tables.setdefault(edge["from"], set()).add(edge["to"])
    for join in nodes("Join"):
        if join.get("through") not in (None, "", NULL):
            join_tables.setdefault(join["id"], set()).add(join["through"])
    support = {ctx: {table} for ctx, table in facts.items()}
    for edge in edges("REQUIRES_JOIN"):
        support.setdefault(edge["from"], set()).update(join_tables.get(edge["to"], ()))
    for ctx, term in context_terms.items():
        if term in terms:
            for other, tables in support.items():
                if measures.get(other) == measures.get(ctx):
                    assets.setdefault(term, set()).update(tables)
    periods = {edge["from"]: edge["kind"] for edge in edges("PERIOD_COLUMN")}
    release_tables = {json.loads(p['params'])['table'] for p in nodes('Policy') if p['kind'] == 'release_driver'}
    reported = {edge['from'] for edge in edges('REQUIRES_JOIN') if edge['required'].lower() == 'true'
                and join_tables.get(edge['to'], set()) & release_tables}
    out = []
    for term, tables in sorted(assets.items()):
        for table in sorted(tables):
            unit = table.split(".", 1)[0]
            contexts = [ctx for ctx, name in context_terms.items() if name == term]
            context = next((ctx for ctx in contexts if facts[ctx] == table), None)
            context = context or next((ctx for ctx in contexts if table in support[ctx]), None)
            context = context or next(iter(contexts), None)
            fact = facts[context] if context else table
            period = ("March 2025 as reported" if context in reported else
                      "Q2 2025" if periods.get(fact) == "fiscal_quarter" else "June 2025")
            if context:
                unit = facts[context].split(".", 1)[0]
            column = implementations.get((term, table)) or implementations.get((term, fact))
            count = column and not re.search(r"INT|DECIMAL|DOUBLE|FLOAT|NUMERIC|REAL|BOOL", column["data_type"], re.I)
            window = "" if context not in reported and periods.get(fact) in (None, "event_date") else f" in {period}"
            figure = f"{'How many ' if count else ''}{term} for {unit}{window}?"
            name = terms[term]["id"]
            for kind, question in (("definition", f"What is {term}?"), ("figure", figure)):
                out.append({"id": f"page:{table}:{name}:{kind}", "kind": kind, "question": question,
                            "products": [table], "term": term})
    return out


def evaluate(items, ask, assistance=None, workers=None):
    """Require every generated definition and figure on Ask and, when provided, Assist."""
    outcomes = []
    for transport, call in (("ask", ask), ("assist", assistance)):
        if call is None:
            continue
        answers = each(call, items, lambda item: item["id"], workers)
        for item, answer in zip(items, answers):
            if item["kind"] == "figure":
                passed = answer.get("behaviour") == "figure" and answer.get("figure") is not None
            else:
                defined = any(definition.get("term") == item["term"] for definition in answer.get("certified_definitions", ()))
                glossary = (answer.get("behaviour") == "glossary_definition" and answer.get("term") == item["term"]
                            and answer.get("definition_from") == "the pack's glossary" and bool(answer.get("definition"))
                            and answer["definition"] in answer.get("answer_text", ""))
                passed = (answer.get("behaviour") in ("cite_certified", "glossary_definition") and defined) or glossary
            outcomes.append({"id": item["id"], "transport": transport, "passed": passed,
                             **({} if passed else {"behaviour": answer.get("behaviour"), "reason": answer.get("reason")})})
    return {"passed": all(row["passed"] for row in outcomes), "answered": sum(row["passed"] for row in outcomes),
            "total": len(outcomes), "failures": [row for row in outcomes if not row["passed"]]}
