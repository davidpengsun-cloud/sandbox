"""Pure proposal of pack additions from one safely-read unit catalogue.

This module names no client and writes nothing.  It makes conservative
proposals: only currency-shaped numeric columns with a date in the same table,
and never changes an alias or term already present.
"""
from __future__ import annotations

import re

from kge.compiler.checks import ISO_4217


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def words(values: list[str]) -> str:
    return "none" if not values else values[0] if len(values) == 1 else ", ".join(values[:-1]) + " or " + values[-1]


def _sql_value(value) -> str:
    if isinstance(value, str): return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool): return "TRUE" if value else "FALSE"
    return str(value)


def _predicate(recipe: dict) -> str:
    left, op, value = f'{recipe["table"]}.{recipe["column"]}', recipe["op"], recipe.get("value")
    if op in {"IS NULL", "IS NOT NULL"}: return f"{left} {op}"
    if op in {"IN", "NOT IN"}: return f"{left} {op} (" + ", ".join(_sql_value(x) for x in value) + ")"
    return f"{left} {op} {_sql_value(value)}"


def propose(pack: dict, catalogue: dict, label: str, connection: dict, figures: dict[str, dict] | None = None,
            hints: dict | None = None) -> dict:
    """The proposed draft items for one unit. A model may word a question's subject (kge/estate/draftflow.py); the period,
    SQL and figure stay code's, keyed by column so a term renamed for a collision keeps its subject. A table has one
    period column, so a second measure on it shares the binding the first one made. connection: the unit's source's,
    as the estate's warehouses name it (EstateWarehouses.connection)."""
    unit, tables = catalogue["unit"], catalogue["tables"]

    aliases = (pack.get("terminology") or {}).get("aliases") or []
    phrases = {a["phrase"].lower() for a in aliases}
    calendars = [c for c in pack.get("calendars", []) if c.get("year_start_month") == 1]
    calendar = calendars[0]["id"] if calendars else None
    referenced = {t["name"]: 0 for t in tables}
    for table in tables:
        for fk in table.get("foreign_keys", []):
            referenced[fk["ref_table"]] = referenced.get(fk["ref_table"], 0) + 1
    entity_table = max(tables, key=lambda t: (referenced.get(t["name"], 0), -tables.index(t)))
    entity_key = entity_table["primary_key"][0] if len(entity_table.get("primary_key", [])) == 1 else None
    source_pack = {
        "sources": [{"id": unit, "label": label, "entity_key": entity_key,
                     "connection": dict(connection), "harvest": True}],
        "no_join": [{"between": [s["id"], unit],
                     "note": f"Drafted from the DDL: no foreign key in it references {s['label']}'s tables, and no mapping table exists."}
                    for s in pack.get("sources", [])
                    if frozenset((s["id"], unit)) not in {frozenset(n.get("between", [])) for n in pack.get("no_join", [])}],
        "period_bindings": [], "terminology.aliases": []}
    phrase = label.lower()
    if phrase not in phrases:
        source_pack["terminology.aliases"].append({"phrase": phrase, "source": unit})
    source = {"id": "source", "kind": "source",
              "title": f"{label} ({unit}): {len(tables)} tables, no join to " + words([s["label"] for s in pack.get("sources", [])]),
              "from": hints.get("from") if hints else "the DDL's CREATE TABLE statements and their keys", "pack": source_pack}
    items, not_drafted = [source], []
    known_ids = {g["id"] for g in pack.get("glossary", [])}
    known_names = {g["name"].lower() for g in pack.get("glossary", [])}
    terms = []
    hinted = {(m["table"], m["column"]): m for m in (hints or {}).get("measures", [])}
    subjects = {(m["table"], m["column"]): s["subject"] for s in (hints or {}).get("golden", [])
                for m in (hints or {}).get("measures", []) if m["term"] == s["term"]}
    for table in tables:
        dates = [c for c in table["columns"] if c["type"].upper() == "DATE"]
        keys = set(table.get("primary_key", [])) | {c for f in table.get("foreign_keys", []) for c in f["columns"]}
        for column in table["columns"]:
            declared = hinted.get((table["name"], column["name"]))
            match = re.fullmatch(r"(.+)_([a-z]{3})", column["name"])
            numeric = column["type"].upper().startswith(("DECIMAL", "NUMERIC", "DOUBLE", "REAL", "FLOAT"))
            if not numeric or column["name"] in keys or (hints is None and not match) or (hints is not None and not declared):
                continue
            currency = declared["currency"] if declared else match.group(2).upper()
            if currency not in ISO_4217:
                continue
            bound = next((c for c in dates if declared and c["name"] == declared.get("time")), dates[0] if dates else None)
            if not bound or calendar is None:
                not_drafted.append({"column": f"{unit}.{table['name']}.{column['name']}",
                                    "why": "the table has no DATE column" if not dates else "the pack has no calendar-year calendar"})
                continue
            name = declared["name"] if declared else match.group(1).replace("_", " ").title()
            term = declared["term"] if declared else slug(match.group(1))
            if term in known_ids or name.lower() in known_names:
                term, name = f"{unit}-{term}", f"{label} {name}"
            alias = name.lower()
            if alias in phrases:
                alias = f"{label.lower()} {alias}"
            column_id = f"{unit}.{table['name']}.{column['name']}"
            grain = (declared or {}).get("grain") or "day"
            if grain in {"month" + "_end", "period_end"}:
                grain = "as_of"
            measure = {"id": f"{unit}.{term}", "term": term, "column": column_id,
                       "expression": f"sum({table['name']}.{column['name']})", "aggregation": "sum", "grain": "day",
                       "currency": currency, "calendar": calendar, "conversion": "none"}
            measure["grain"] = grain
            if grain in {"as_of", "quarter_end"}:
                measure["snapshot"] = True
            binding = {"table": f"{unit}.{table['name']}", "column": bound["name"], "kind": "event_date", "calendar": calendar}
            if binding not in source_pack["period_bindings"]:
                source_pack["period_bindings"].append(binding)
            recipes = (declared or {}).get("filters", [])
            filters, selectors = [], []
            for n, recipe in enumerate(recipes, 1):
                rid = f"{unit}-{term}-{n}"
                if recipe["table"] == table["name"]:
                    filters.append({"id":rid,"label":f"{name} condition {n}","table":f"{unit}.{table['name']}","predicate":_predicate(recipe)})
                else:
                    remote = next((t for t in tables if t["name"] == recipe["table"]), None)
                    fk = next((f for f in table.get("foreign_keys", []) if f["ref_table"] == recipe["table"]), None)
                    reverse = next(((t, f) for t in tables for f in t.get("foreign_keys", []) if t["name"] == recipe["table"] and f["ref_table"] == table["name"]), None)
                    if fk:
                        links = [f'{recipe["table"]}.{b} = {table["name"]}.{a}' for a,b in zip(fk["columns"],fk["ref_columns"])]
                    elif reverse:
                        rt, rf = reverse; links = [f'{rt["name"]}.{a} = {table["name"]}.{b}' for a,b in zip(rf["columns"],rf["ref_columns"])]
                    else:
                        not_drafted.append({"metric":term,"why":f"condition table {recipe['table']} has no declared relationship to {table['name']}"}); filters=[]; selectors=[]; break
                    selectors.append({"id":rid,"label":f"{name} condition {n}","table":f"{unit}.{remote['name']}","mode":"include",
                                      "predicate":" AND ".join(links + [_predicate(recipe)])})
            context = None
            if (recipes and (filters or selectors)) or grain != "day":
                qid = f"{unit.upper()}-D{len(terms)+1:02d}"
                context = {"id":f"{unit}-{term}","label":name,"description":f"{name}, with the semantic file's standing conditions.",
                           "term":term,"measure":f"{unit}.{term}","fact":f"{unit}.{table['name']}",
                           "filters":[x["id"] for x in filters],"row_selectors":[x["id"] for x in selectors],
                           "golden":{"set":f"golden_{unit}","items":[qid]}}
            entry = {"id": term, "kind": "term", "title": name, "from": f"{column_id} and its comment",
                     "pack": {"glossary": [{"id": term, "name": name, "domain": label, "steward": f"{label} Data Steward",
                                              "definition": (declared or {}).get("definition") or column.get("comment") or name}],
                              "terminology.aliases": [{"phrase": alias, "term": term}], "measures": [measure],
                              "mappings": [{"column": column_id, "term": term}]}}
            if context:
                entry["pack"].update(filters=filters, row_selectors=selectors, contexts=[context])
            for candidate in (declared or {}).get("aliases", []):
                phrase_candidate = candidate.lower()
                if phrase_candidate in phrases or any(a["phrase"] == phrase_candidate for a in entry["pack"]["terminology.aliases"]):
                    not_drafted.append({"alias": candidate, "why": "alias collides with an existing phrase"})
                else:
                    entry["pack"]["terminology.aliases"].append({"phrase": phrase_candidate, "term": term})
            items.append(entry)
            terms.append((entry, table, column, bound, alias))
    for i, (term, table, column, bound, alias) in enumerate(terms, 1):
        qid = f"{unit.upper()}-D{i:02d}"
        calculated = (figures or {}).get(term["id"], {})
        month = calculated.get("month")
        subject = subjects.get((table["name"], column["name"]))
        lead = subject[0].upper() + subject[1:] if subject else alias.capitalize()
        question = f"{lead} for {month}" if month else f"{lead} for the latest month with rows"
        sql = calculated.get("sql") or (f"SELECT round(sum({column['name']}), 2) FROM {table['name']} WHERE {bound['name']} >= "
                                         "DATE '2025-06-01' AND " + bound["name"] + " < DATE '2025-07-01'")
        items.append({"id": qid, "kind": "question", "title": question, "from": "the drafted measure and its bound period",
                      "set": f"golden_{unit}", "terms": [term["id"]], "question": question,
                      "expect": {"figure": calculated.get("figure"), "unit": unit,
                                 "columns": [term["pack"]["measures"][0]["column"].removeprefix(f"{unit}.")],
                                 "join_path": []}, "sql": sql, **({"needs_rows": True} if not month else {}),
                      **({"worded_by": "model"} if subject else {})})
    recipe_questions = [x["id"] for x in items if x.get("kind") == "question" and any(
        t.get("id") in x.get("terms", []) and "contexts" in t.get("pack", {}) for t in items if t.get("kind") == "term")]
    if recipe_questions:
        source_pack["tests.sets"] = [{"name":f"golden_{unit}","version":"v1","items":recipe_questions}]
    return {"items": items, "not_drafted": list((hints or {}).get("not_drafted", [])) + not_drafted}
