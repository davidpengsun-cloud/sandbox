"""A definition question that names two declared terms, "the difference between X and Y", "how is X different from Y"
or "X vs Y definition" (#617): both definitions, each with its steward and citable source as a one-term definition gives
them, and how the two differ, built from what the pack declares and the release holds, with no model.

Nothing here names a term, a source, a rule or a client's wording. The two sides of the question are read against the
release's glossary, and each attribute compared comes from the term's contexts as the graph holds them (their fact, their
filters and row selectors, their release driver and period binding, their version dates and the scd2 tables they read,
their measure's grain and currency, their unit), with the released catalogue's names for tables and columns and the
release's warehouse for the months each term's data covers. The words below are the platform's: its English for asking
how two things differ, and its words for the meta-model's grains and period kinds. The summary is one template over the
attributes that differ.

What the person may not read is left out: a term defined only by documents of units they may not read is not defined
for them, and a context, filter or row selector on such a unit is not shown or compared. The engineer's view keeps the
ids each attribute came from (difference_ids); a compact answer drops them."""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, replace

from kge.compiler.catalogue_publish import artifact_products
from kge.semantics.query import Asked, holds, month_words
from kge.services.ask.engine.question import normalise_question, parse

BETWEEN = re.compile(r"\b(?:differences?|distinction|distinguish(?:es)?)\s+between\s+(?P<rest>.+)$", re.I)
DIFFERENT = re.compile(r"\b(?:is|are|does|do)\s+(?P<a>.+?)\s+(?:different|differ|distinct)\s+(?:from|to|than)\s+(?P<b>.+)$", re.I)
VERSUS = re.compile(r"^(?P<a>.+?)\s+(?:vs\.?|versus|compared\s+(?:with|to))\s+(?P<b>.+)$", re.I)
DEFINITION_ASKED = re.compile(r"^(?:what\s+(?:is|are)\s+)?(?:the\s+)?(?:definitions?|meanings?)\s+of\b|\b(?:definitions?|meanings?)[\s?.!]*$", re.I)
LEADING = re.compile(r"^(?:what\s+(?:is|are)\s+)?(?:the\s+)?(?:definitions?|meanings?)\s+of\s+", re.I)
TRAILING = re.compile(r"(?:[\s,]+(?:definitions?|meanings?|please))+[\s?.!]*$|[\s?.!]+$", re.I)
DEFINED = ("glossary_definition", "cite_certified", "cite")
ATTRIBUTES = (("source", "Source"), ("filters", "Filters and row selectors"), ("period", "Period rule"),
              ("versions", "Versions"), ("currency", "Currency"), ("unit", "Unit"))
SAID = {"source": "from {}", "filters": "{}", "period": "{}", "versions": "{}", "currency": "in {}", "unit": "in {}"}
GRAINS = {"day": "daily", "week": "weekly", "month": "monthly", "quarter": "quarterly", "year": "yearly"}
KINDS = {"event_date": "by the date each row happened", "snapshot": "as at each snapshot date", "month": "by calendar month",
         "fiscal_quarter": "by fiscal quarter"}
NO_FILTER = "no filter"
NO_VERSIONS = "no versioned table"


@dataclass(frozen=True)
class Side:
    """One side of the question: the person's own words for it, and the glossary term they name, if any."""
    words: str
    term: str | None


def sides(question: str) -> tuple[str, list[tuple[str, str]]] | None:
    """How a question asks two things' difference, and every way its words split into the two, in its own words: by
    "between" (each "and" in turn, since a term's own name may hold one), by "different from", or by "vs". None for any
    other question."""
    q = normalise_question(question).strip()
    if m := BETWEEN.search(q):
        parts = m.group("rest").split(" and ")
        splits = [(" and ".join(parts[:i]), " and ".join(parts[i:])) for i in range(1, len(parts))]
        kind = "between"
    elif m := DIFFERENT.search(q):
        splits, kind = [(m.group("a"), m.group("b"))], "different"
    elif m := VERSUS.search(q):
        splits, kind = [(m.group("a"), m.group("b"))], "versus"
    else:
        return None
    splits = [tuple(LEADING.sub("", TRAILING.sub("", side)).strip() for side in split) for split in splits]
    splits = [split for split in splits if all(split)]
    return (kind, splits) if splits else None


def resolved(question: str, e) -> tuple[str, tuple[Side, Side]] | None:
    """How the question asks, and its two sides with the glossary term each names (the first the parser finds in its
    words): the first split whose two sides name two different terms, else the first that names one. None where no
    split names a term."""
    if not (said := sides(question)):
        return None
    kind, splits = said
    pairs = [tuple(Side(words, parse(words, e.term_names, e).term) for words in split) for split in splits]
    both = [p for p in pairs if p[0].term and p[1].term and p[0].term != p[1].term]
    one = [p for p in pairs if bool(p[0].term) != bool(p[1].term)]
    return (kind, (both or one)[0]) if both or one else None


def asked(req, e) -> tuple[Side, Side] | None:
    """The two sides of a definitional question asking how two things differ, when at least one names a glossary term
    and the two are not one term. None otherwise."""
    if not req.definitional or not (found := resolved(req.question, e)):
        return None
    return found[1]


def request(req, e):
    """The request a definition is asked by. A question asking the difference between, or how one thing is different
    from another, where both sides name glossary terms, is definitional whatever it opens with; "X vs Y" is only where it
    asks for their definition or meaning by those words, first or last, and names a term. A question with a period asks
    for figures, and is never made one."""
    if req.definitional or req.period is not None or not (found := resolved(req.question, e)):
        return req
    kind, pair = found
    if kind == "versus" and not DEFINITION_ASKED.search(normalise_question(req.question).strip()):
        return req
    if kind != "versus" and not (pair[0].term and pair[1].term):
        return req
    return replace(req, definitional=True)


def answer(e, req, principal: str, purpose: str, cfg, pair: tuple[Side, Side]) -> dict:
    """Both definitions and how they differ; with one side naming no term the person may read, the one-term definition
    the question gives today, with a clarification that names the other side in the person's own words."""
    readable = e.g.readable_units(principal)
    known = [s for s in pair if s.term and e.term_readable(s.term, readable)]
    if len(known) == 2:
        both = [definition(e, req, s, principal, purpose, cfg) for s in known]
        if all(d is not None for d in both):
            return compared(e, known, both, readable, principal)
    single = e.one_definition(req, principal, purpose, cfg)
    if len(known) != 1:
        return single
    other = next(s for s in pair if s is not known[0])
    defined = f", so only {known[0].term} is defined" if single.get("behaviour") in DEFINED else ""
    return {**single, "difference": {"terms": [known[0].term], "unresolved": [other.words],
                                     "clarification": f"“{other.words}” names no glossary term you may read{defined}. "
                                                      f"Which term did you mean to compare {known[0].term} with?"}}


def definition(e, req, side: Side, principal: str, purpose: str, cfg) -> dict | None:
    """One term's definition as a one-term question on it gives it: its definition, its steward and what may be cited as
    its source (the documents that define it, or the pack's glossary). None where that question gives no definition."""
    one = e.one_definition(replace(req, question=side.term, term=side.term, terms_found=[side.term]), principal, purpose, cfg)
    if one["behaviour"] == "glossary_definition":
        return {"term": side.term, "asked": side.words, "definition": one["definition"], "steward": one.get("steward") or "",
                "documents": [], "source": one.get("definition_from") or ""}
    if one["behaviour"] not in ("cite_certified", "cite"):
        return None
    from kge.services.ask.engine.wording import certified_definitions
    shown = certified_definitions({"behaviour": "cite_certified", "question": side.term, "term": side.term,
                                   "cite": one.get("cite") or [], "principal": principal, "purpose": purpose}, e)
    entry = next((d for d in shown if d["term"] == side.term and d["documents"]), None)
    if entry is None:
        return None
    held = e.g.q("MATCH (t:Term) WHERE t.name = $t RETURN t.definition AS definition, t.steward AS steward", {"t": side.term})
    held = held[0] if held else {}
    return {"term": side.term, "asked": side.words, "definition": entry["definition"], "steward": held.get("steward") or "",
            "documents": entry["documents"], "source": ", ".join(entry["documents"])}


def compared(e, pair: list[Side], both: list[dict], readable: set[str], principal: str) -> dict:
    """The answer to a question on two readable terms: both definitions, the attributes on which their declarations
    differ (each by its business words), one summary line over those, and the months each term's data covers. The
    documents that define them are its citations, numbered, each for the term it defines."""
    products = artifact_products(e.profile)
    profiles = [profile(e, s.term, readable, products, principal) for s in pair]
    rows = [{"attribute": key, "label": label, "values": [p["values"].get(key, "") for p in profiles]}
            for key, label in ATTRIBUTES if profiles[0]["values"].get(key, "") != profiles[1]["values"].get(key, "")]
    terms = [{**d, "attributes": p["values"], "period_range": p["period_range"]} for d, p in zip(both, profiles)]
    cite = list(dict.fromkeys(doc for d in both for doc in d["documents"]))
    titles = {x["id"]: x.get("title") or "" for d in both for x in e.g.defining_docs(d["term"])}
    citations = [{"n": n, "doc": doc, "title": titles.get(doc, ""),
                  "why": "defines " + listed(d["term"] for d in both if doc in d["documents"])} for n, doc in enumerate(cite, 1)]
    return {"behaviour": "cite_certified", "term": pair[0].term, "terms": [s.term for s in pair], "cite": cite,
            "citations": citations,
            "certified_definitions": [{"term": d["term"], "definition": d["definition"], "documents": d["documents"],
                                       "steward": d["steward"], "source": d["source"]} for d in both],
            "difference": {"terms": terms, "rows": rows, "summary": summary(pair, rows, profiles)},
            "difference_ids": {"terms": [p["ids"] for p in profiles]},
            "reason": "Both terms' definitions, and how their declarations differ, from the pack and the release."}


def summary(pair: list[Side], rows: list[dict], profiles: list[dict]) -> str:
    """One plain line from the attributes that differ: each term with its own value of each, in one order. Where nothing
    differs, whether that is because the declarations agree or because nothing about a term may be read."""
    unread = [s.term for s, p in zip(pair, profiles) if not p["values"]]
    if not rows and unread:
        return f"Nothing you may read declares how {listed(unread)} {'is' if len(unread) == 1 else 'are'} measured, so only the definitions are compared."
    if not rows:
        return f"{pair[0].term} and {pair[1].term} differ in nothing the pack declares beyond their definitions."
    said = [", ".join(SAID[r["attribute"]].format(r["values"][i] or "nothing declared") for r in rows) for i in range(2)]
    return f"{pair[0].term}: {said[0]}. {pair[1].term}: {said[1]}."


def profile(e, term: str, readable: set[str], products: dict, principal: str) -> dict:
    """What the pack declares of a term's figure, over its contexts on units the person may read: each attribute in
    business words (values), the ids it came from (ids) and the months its data covers in this release (period_range)."""
    sem = e.semantics
    contexts = [c for c in sem.contexts_of(term) if c.unit in readable]
    each = [context_values(e, c, readable, products) for c in contexts]
    values = {key: "; ".join(dict.fromkeys(v["values"][key] for v in each)) for key, _ in ATTRIBUTES} if each else {}
    ranges = [r for r in (period_range(e, c, principal) for c in contexts) if r is not None]
    period = None
    if ranges:
        first, latest = min(r["first"] for r in ranges), max(r["latest"] for r in ranges)
        released = all(r["released"] for r in ranges)
        period = {"first": first.isoformat(), "latest": latest.isoformat(), "released_months": released,
                  "words": f"{month_words(first)} to {month_words(latest)}" if released
                           else f"{first.isoformat()} to {latest.isoformat()}"}
    return {"values": values, "period_range": period, "ids": {"term": term, "contexts": [v["ids"] for v in each]}}


def context_values(e, c, readable: set[str], products: dict) -> dict:
    """One context's attributes in business words, and their ids for the engineer."""
    sem = e.semantics
    rules = [r for r in c.filters if r.table.split(".")[0] in readable] + [
        r for r in c.selectors if r.table.split(".")[0] in readable and r.table.split(".")[0] == c.unit]
    measure = sem.measure_declarations.get(c.measure) or {}
    grain = measure.get("grain") or c.grain or ""
    binding = (getattr(e, "PERIODS", None) or {}).get(c.fact) or {}
    driver = next((j.target for j in c.joins if j.required and j.kind == "calendar_rollup" and j.target in sem.releases
                   and j.target.split(".")[0] in readable), None)
    period = ", ".join(w for w in (GRAINS.get(grain, grain.replace("_", " ")),
                                   KINDS.get(binding.get("kind") or "", (binding.get("kind") or "").replace("_", " ")),
                                   "over released months only" if driver else "") if w)
    read = [t for t in (c.fact, *(t for j in c.joins if j.required and not j.through for t in (j.source, j.target)))
            if t.split(".")[0] in readable]
    pinned: dict[str, list[str]] = {}
    for table, column in c.version_dates.items():
        if table.split(".")[0] in readable and column.split(".")[0] in readable:
            pinned.setdefault(column, []).append(table)
    current = [t for t in dict.fromkeys(read) if t in sem.scd2 and t not in c.version_dates]
    versions = [f"{listed(table_name(e, t, products) for t in tables)} as at {column_name(column, products)}"
                for column, tables in pinned.items()]
    if current:
        versions.append(f"{listed(table_name(e, t, products) for t in current)} as current now")
    values = {"source": table_name(e, c.fact, products),
              "filters": listed(r.label for r in rules if r.label) or NO_FILTER,
              "period": period, "versions": "; ".join(versions) or NO_VERSIONS,
              "currency": measure.get("currency") or "", "unit": e.UNIT_LABEL.get(c.unit, c.unit)}
    ids = {"context": c.id, "fact": c.fact, "measure": c.measure, "filters": [r.id for r in c.filters if r in rules],
           "row_selectors": [r.id for r in c.selectors if r in rules], "release_driver": driver,
           "period_binding": binding.get("column"), "current_versions": current,
           "version_dates": {t: column for t, column in c.version_dates.items() if t.split(".")[0] in readable
                             and column.split(".")[0] in readable}}
    return {"values": values, "ids": ids}


def period_range(e, c, principal: str) -> dict | None:
    """The first and latest period a context's rows cover in this release: over released months only where it rolls up
    to a release driver, through its own filters and row selectors, as its figure reads them; one query, through the SQL
    gate on the context's allowlist, once the person may read every unit it reads. It changes only with a release, so it
    is read once per release (the engine's identity) and context. None where the context plans no period (a partitioned
    fact with no default period among them), the person may not read it, or the warehouse gives no date."""
    plan = e.semantics.plan(c, Asked())
    if plan.decision is not None or not plan.period_on or plan.allow is None:
        return None
    if not e.g.join_access(principal, list(plan.units))["allowed"]:
        return None
    release = json.dumps(e.identity, sort_keys=True, default=str)
    held = vars(e).setdefault("_period_ranges", {})
    if held.get("release") != release:
        held.clear()
        held["release"] = release
    if c.id not in held:
        passed, found = read_range(e, plan)
        if not passed:
            return None
        held[c.id] = found
    return held[c.id]


def read_range(e, plan) -> tuple[bool, dict | None]:
    """Whether the query ran, and the first and latest period date the plan's rows hold, from the warehouse. A query
    the gate refused or the warehouse failed is not an answer to keep for the release."""
    parts = plan.parts(None)
    where = list(parts["where"])
    if plan.release is not None:
        r = plan.release
        where.append(holds(f"{r['alias']}.{r['released']}", e.semantics.tables.get(r["table"], {}).get(r["released"], "")))
    sql = (f"SELECT min({plan.period_on}), max({plan.period_on}) FROM {parts['from']}" + "".join(f" {j}" for j in parts["joins"])
           + (" WHERE " + " AND ".join(where) if where else ""))
    guard, rows = e.run_plan(plan, sql)
    if rows is None or not guard.get("passed"):
        return False, None
    if not rows or rows[0][0] is None or rows[0][1] is None:
        return True, None
    first, latest = (day(v) for v in rows[0])
    if first is None or latest is None:
        return True, None
    return True, {"first": first, "latest": latest, "released": plan.release is not None}


def day(value) -> datetime.date | None:
    """A period value as a date: a timestamp's date, a date as it is, None for anything else."""
    if isinstance(value, datetime.datetime):
        return value.date()
    return value if isinstance(value, datetime.date) else None


def table_name(e, table: str, products: dict) -> str:
    """A table in business words: the released catalogue's name for it, else the pack's entity label for it, else its
    unit's label."""
    if name := (products.get(table) or {}).get("name"):
        return str(name)
    entity = next((x["label"] for x in e.semantics.entities.values() if x.get("table") == table and x.get("label")), None)
    unit = e.UNIT_LABEL.get(table.split(".")[0], table.split(".")[0])
    return f"{unit} {entity}" if entity else f"a {unit} table"


def column_name(column: str, products: dict) -> str:
    """A column in business words: the released catalogue's business name for it, else "its date"."""
    table, name = column.rsplit(".", 1)
    field = next((f for f in (products.get(table) or {}).get("fields") or [] if f.get("name") == name), {})
    return str(field.get("business_name") or "its date")


def listed(words) -> str:
    """Words joined as a person lists them: "a", "a and b", "a, b and c"."""
    words = list(dict.fromkeys(words))
    return " and ".join(", ".join(words).rsplit(", ", 1)) if words else ""


def worded(a: dict) -> str:
    """The answer's text: each definition with its steward and source, the attributes that differ, the summary, and the
    months each term covers; a one-term answer's clarification. No id of a table or column: business words alone."""
    d = a.get("difference") or {}
    if len(d.get("terms") or []) != 2:
        return d.get("clarification") or ""
    cites = {c["doc"]: c.get("n") for c in a.get("citations") or [] if isinstance(c, dict)}
    lines = []
    for t in d["terms"]:
        marks = "".join(f" [{cites[doc]}]" for doc in t["documents"] if cites.get(doc) is not None)
        steward = f"steward: {t['steward']}; " if t.get("steward") else ""
        lines.append(f"{t['term']}: {t['definition']} ({steward}source: {t['source']}){marks}")
    names = [t["term"] for t in d["terms"]]
    if d["rows"]:
        lines.append("How they differ:")
        lines += [f"- {r['label']}: {r['values'][0] or 'nothing declared'} ({names[0]}) against "
                  f"{r['values'][1] or 'nothing declared'} ({names[1]})" for r in d["rows"]]
    lines.append(f"In short: {d['summary']}")
    covered = [f"{t['term']} {t['period_range']['words']}" for t in d["terms"] if t.get("period_range")]
    if covered:
        lines.append("Periods with data in this release: " + "; ".join(covered) + ".")
    return "\n".join(lines)
