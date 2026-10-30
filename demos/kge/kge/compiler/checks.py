"""The structural checks a pack passes before it compiles (../../docs/use-cases/kge/platform/pack-contract.md, 5.1), against the platform's
meta-model and the pack's own lock.

  S00  the manifest's client (present, a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most,
       the client folder's name) and its meta_model_version
       (present, semver): the meta-model version the pack was written for
  S01  each section's shape, from the field tables below, and the lock's, which is the pack's own and holds the
       catalogue of each source the pack harvests; no value is the literal \\N, the artifact's NULL; the manifest's
       gates, the thresholds the post-load check holds a loaded candidate to (kge/eval/margin.py), a share from 0 to 1
       for each kind of question the golden scorer knows
  S02  ids: unique per kind and slugs; a term's name unique too, since the graph keys a term by it
  S03  the meta-model and this compiler satisfy the ranges the manifest's platform names; the meta-model is of the
       pack's major version, at the pack's minor or later; and this compiler fills every type the meta-model gives the
       pack or the lock to fill
  S04  extensions only add, and are namespaced x_<client>_ (a hyphen in the client id is an underscore there)
  S05  each foreign key in the lock references a table of its own source, column for column, with compatible types; a
       composite key is reported unless a declared join covers it, since a JOINS edge holds one column pair
  S06  no_join: two declared, distinct sources; each pair once
  S07  mappings: a column in the lock and a declared term; a certified one says by whom and when
  S08  measures: a declared term, a column in the lock carrying no other measure, and one read-only aggregate over the
       rows of that column's table, as sqlglot parses it, which agrees with the aggregation and names the measure's
       column; it is built of aggregates, operators, FILTER, CASE, IF, CAST, COALESCE and NULLIF alone, and of that
       table's columns, so it reads no file and no other table
  S09  calendars and period bindings: a year that starts in a month, bindings to columns in the lock, one a table, on
       declared calendars, rolling up within their source; each measure's calendar declared and its table bound
  S10  currency codes from ISO 4217; a conversion from another currency
  S11  aliases: each names a declared term, a declared source, or a candidate the glossary does not hold, or (since
       meta-model 1.1) a column in the lock to group by, a declared entity, a time mode or a period the platform knows,
       or a column in the lock it is the business name of; one target a phrase, whatever its case
  S14  roles: reads name declared sources, writes are within terms and mappings, processes are the platform's
  S22  restricted columns are columns in the lock; a phrase a policy gives for one is for a column it restricts, once
  S23  declared joins: tables in the lock, and a condition that is a conjunction of read-only conditions, as sqlglot
       parses it, on their columns alone, equating a column of the one with a column of the other (through the curated
       table a cross-reference joins through), types that join, and no restricted column; a table joined to itself
       is aliased; a join across two sources is a cross-reference through a curated source's table; a join implements
       a declared relation between its tables' entities
  S24  filters and row selectors: a table in the lock, a read-only condition on its columns (a filter's on its table's
       alone; a row selector's on its table's and the facts' it is matched against), no restricted column, and
       conflicts declared both ways, with declared filters and row selectors
  S25  contexts: a declared term, a declared measure on its fact table, declared filters, row selectors and joins; each
       join follows from the fact or a join before it; each filter's table, and each table a row selector matches
       against, is the fact or one a join it always renders reaches, or, for a row selector on another source's table,
       the far table of a cross-reference into that source the context reaches (the selector applies across it only);
       its golden questions are items of a set the pack names
  S26  acronyms, entities, relations, lineage, columns not joinable and ontology concepts name declared terms and
       entities and tables and columns in the lock; a column not joinable to a table is of another source with which
       its source has a no-join fact
  S27  certified joins: two declared, distinct sources that are not a no-join pair, each pair once, through a declared
       cross-reference between them; and every cross-reference is a certified join's
  S28  time policies: an scd2 policy's and a release driver's table and columns are in the lock, one of each kind a
       table
  S29  the rest of the parser's vocabulary (since meta-model 1.2): a value vocabulary's and a predicate's column is in
       the lock and no restricted column; a value vocabulary gives values or a pattern the platform knows, not both,
       and a prefix only with a pattern; a predicate gives a value where its operator takes one, a number where it
       compares, and none otherwise; each intent once; each functional mailbox a mailbox's local part, once
  S30  ontology mappings (since meta-model 1.3): each maps one declared term, entity or restricted-data policy to a
       concept the platform preloads (standards/preload.yaml), by its IRI, with a SKOS relation, certified by a
       steward on a date; a subject maps to a concept once; and a concept the pack declares itself (ontology) takes
       no preloaded concept's id. A mapping to an IRI the preload does not hold is refused: widening the preload is a
       platform update, never a pack's

A check runs on what holds its shape: an S01 finding stops the checks that read its section, or the lock, and no
other, so a stray field in the manifest hides no mapping's finding.

Not checked here: S12 (documents land at load, and are checked there, fail closed), S13 and S15 (the pack names its
question sets, not their questions) and S16 to S21 (tiers and readiness, the plan's Phase 4).

Beside the findings, the foreign keys the lock harvested that no declared join covers are join candidates
(candidates): harvest proposes, and the pack declares.

Names no client: the client is the pack's own.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import NamedTuple

import sqlglot
from sqlglot import exp

from kge.pipeline.access import process_vocabulary
from kge.compiler import standards as preloaded
from kge.compiler.pack import LOCK, LOCK_FORMAT, Finding, Pack
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.compiler.vocabulary import COMPARES, INTENTS, MAILBOX, NEEDS_VALUE, OPS, PATTERNS

NOT_CHECKED = ("not checked here: S12 (documents land at load, and are checked there, fail closed), S13 and S15 (the pack "
               "names its question sets, not their questions), S16-S21 (tiers and readiness, the plan's Phase 4)")
NULL = "\\N"


STR, INT, BOOL, DATE, ANY, READS = "text", "whole number", "true or false", "date (YYYY-MM-DD)", "anything", "reads"
SHARES = "shares"
KINDS = ("within", "cross", "shelf", "abstain")


class F(NamedTuple):
    """A field: its kind (one above, a tuple of the values it may take, [kind] for a list of it, or a dict of fields for
    a mapping) and whether it is required."""
    kind: object
    required: bool = True


def opt(kind) -> F:
    return F(kind, False)


CALENDAR_KINDS = ("month", "fiscal_quarter", "snapshot", "event_date")
JOIN_KINDS = ("key", "calendar_rollup", "hierarchy", "cross_reference")
ALIAS_KINDS = ("term", "source", "candidate", "group_by", "entity", "time_mode", "period", "column")
TIME_MODES = ("as_reported", "restated")
PERIODS = ("latest",)
AGGREGATIONS = ("sum", "count", "count_distinct", "avg", "ratio", "last_snapshot")
GRAINS = ("day", "month", "quarter", "quarter_end", "year", "as_of")
SECTIONS: dict[str, object] = {
    "manifest": {"client": opt(ANY), "meta_model_version": opt(ANY), "id": F(STR), "version": F(STR), "title": F(STR),
                 "owners": opt([STR]), "platform": F({"meta_model": F(STR), "compiler": F(STR)}),
                 "compile": F({"store": F(("kuzu",)), "embedding_dim": F(INT), "id_scheme": F(("v1",))}),
                 "includes": opt([STR]), "gates": opt({"golden_min_pass": opt(SHARES), "control_margin": opt(SHARES)})},
    "schema": {"meta_model": F(STR), "extensions": opt([ANY])},
    "sources": [{"id": F(STR), "label": F(STR), "entity_key": F(STR), "connection": F({"dialect": F(STR), "ref": F(STR)}),
                 "harvest": opt(BOOL)}],
    "curated_sources": [{"id": F(STR), "label": F(STR), "connection": F({"dialect": F(STR), "ref": F(STR)}), "harvest": opt(BOOL)}],
    "no_join": [{"between": F([STR]), "asserted_by": F(STR), "asserted_on": F(DATE), "note": F(STR)}],
    "certified_joins": [{"between": F([STR]), "join": F(STR), "asserted_by": F(STR), "asserted_on": F(DATE), "note": F(STR)}],
    "not_joinable": [{"column": F(STR), "table": F(STR), "asserted_by": F(STR), "asserted_on": F(DATE), "note": F(STR)}],
    "glossary": [{"id": F(STR), "name": F(STR), "domain": F(STR), "steward": F(STR), "definition": F(STR),
                  "status": F(("certified", "candidate")), "defined_by": opt([STR]), "version": opt(STR),
                  "effective_from": opt(DATE)}],
    "terminology": {"aliases": F([{"phrase": F(STR), **{k: opt(STR) for k in ALIAS_KINDS}}]),
                    "value_vocabularies": opt([{"column": F(STR), "values": opt([STR]), "pattern": opt(tuple(PATTERNS)),
                                                "prefix": opt(STR), "after": opt([STR]), "before": opt([STR])}]),
                    "predicates": opt([{"phrases": F([STR]), "with": opt([STR]), "column": F(STR), "op": F(tuple(OPS)),
                                        "value": opt(ANY)}]),
                    "intents": opt([{"phrases": F([STR]), "intent": F(INTENTS)}]),
                    "functional_mailboxes": opt([STR])},
    "calendars": [{"id": F(STR), "year_start_month": F(INT), "quarter_label": F(STR)}],
    "period_bindings": [{"table": F(STR), "column": F(STR), "kind": F(CALENDAR_KINDS), "calendar": F(STR),
                         "rollup": opt({"table": F(STR), "column": F(STR)})}],
    "measures": [{"id": F(STR), "term": F(STR), "column": F(STR), "expression": F(STR), "aggregation": F(AGGREGATIONS),
                  "grain": F(GRAINS), "currency": F(STR), "calendar": F(STR), "conversion": F(STR), "snapshot": opt(BOOL),
                  "population": opt(STR)}],
    "mappings": [{"column": F(STR), "term": F(STR), "certified": F(BOOL), "certified_by": opt(STR), "certified_on": opt(DATE)}],
    "policies": {"restricted_attributes": opt([{"id": F(STR), "action": F(("refuse",)), "cite": F(STR), "columns": F([STR]),
                                                "version": opt(STR), "phrases": opt([{"column": F(STR), "phrases": F([STR])}])}]),
                 "scd2": opt([{"id": F(STR), "table": F(STR), "valid_from": F(STR), "valid_to": F(STR), "is_current": F(STR),
                               "valid_to_inclusive": F(BOOL), "version": opt(STR)}]),
                 "release_driver": opt([{"id": F(STR), "table": F(STR), "period": F(STR), "release_date": F(STR),
                                         "freeze_date": F(STR), "released": F(STR), "version": opt(STR)}])},
    "access": {"purposes": F([STR]), "roles": F([{"id": F(STR), "label": F(STR), "reads": F(READS), "writes": opt([STR]),
                                                  "processes": F([STR])}])},
    "tests": {"sets": F([{"name": F(STR), "version": F(STR), "items": opt([STR])}])},
    "acronyms": [{"id": F(STR), "expansion": F(STR), "terms": opt([STR]), "columns": opt([STR])}],
    "entities": [{"id": F(STR), "label": F(STR), "table": F(STR), "key": F([STR])}],
    "relations": [{"id": F(STR), "label": F(STR), "from": F(STR), "to": F(STR)}],
    "ontology": [{"id": F(STR), "label": F(STR), "iri": opt(STR), "entities": opt([STR]), "terms": opt([STR])}],
    "ontology_mappings": [{**{k: opt(STR) for k in ("term", "entity", "policy")}, "iri": F(STR),
                           "relation": F(preloaded.RELATIONS), "note": opt(STR), "certified_by": F(STR), "certified_on": F(DATE)}],
    "lineage": [{"table": F(STR), "derived_from": F([STR]), "note": opt(STR)}],
    "filters": [{"id": F(STR), "label": F(STR), "table": F(STR), "predicate": F(STR), "conflicts_with": opt([STR])}],
    "row_selectors": [{"id": F(STR), "label": F(STR), "table": F(STR), "mode": F(("exclude", "include")), "predicate": F(STR),
                       "conflicts_with": opt([STR])}],
    "joins": [{"id": F(STR), "kind": F(JOIN_KINDS), "from": F(STR), "to": F(STR), "condition": F(STR), "alias": opt(STR),
               "through": opt(STR), "implements": opt(STR)}],
    "contexts": [{"id": F(STR), "label": F(STR), "description": F(STR), "term": F(STR), "measure": F(STR), "fact": F(STR),
                  "filters": opt([STR]), "row_selectors": opt([STR]), "joins": opt([STR]), "reaches": opt([STR]),
                  "golden": opt({"set": F(STR), "items": F([STR])})}],
}
REQUIRED = ("manifest", "schema", "sources", "glossary", "terminology", "measures", "mappings", "tests")
FILLS = {"sources": "Source", "glossary": "Term", "measures": "Measure", "calendars": "Calendar",
         "terminology.aliases": "Alias", "policies.restricted_attributes": "Policy", "policies.scd2": "Policy",
         "policies.release_driver": "Policy", "curated_sources": "CuratedSource", "contexts": "Context", "filters": "Filter",
         "row_selectors": "RowSelector", "joins": "Join", "acronyms": "Acronym", "entities": "Entity", "relations": "Relation",
         "ontology": "OntologyConcept"}


def is_date(value) -> bool:
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def shape(value, kind, at: str, out: list[tuple[str, str]]) -> None:
    """Every way value is not of kind, as (location, message). A field named x_... is left to S04."""
    if kind == ANY:
        return
    if isinstance(kind, dict):
        if not isinstance(value, dict):
            out.append((at, f"is a mapping with {', '.join(kind)}"))
            return
        for name, f in kind.items():
            if name in value:
                shape(value[name], f.kind, f"{at}.{name}", out)
            elif f.required:
                out.append((f"{at}.{name}", "is required"))
        for name in value:
            if name not in kind and not (isinstance(name, str) and name.startswith("x_")):
                out.append((f"{at}.{name}", f"is not a field here: the fields are {', '.join(kind)}"))
        return
    if isinstance(kind, list):
        if not isinstance(value, list):
            out.append((at, "is a list"))
            return
        for i, v in enumerate(value):
            shape(v, kind[0], f"{at}[{i}]", out)
        return
    if isinstance(kind, tuple):
        if not isinstance(value, str) or value not in kind:
            out.append((at, f"is one of {', '.join(kind)}, not {value!r}"))
        return
    if kind == READS:
        if value != "all" and not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
            out.append((at, f"is all, or a list of source ids, not {value!r}"))
        return
    if kind == SHARES:
        if not isinstance(value, dict):
            out.append((at, f"is a mapping of kinds of question ({', '.join(KINDS)}) to a share from 0 to 1"))
            return
        for k, v in value.items():
            if k not in KINDS:
                out.append((f"{at}.{k}", f"is not a kind of question: the kinds are {', '.join(KINDS)}"))
            elif isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                out.append((f"{at}.{k}", f"is a share from 0 to 1, not {v!r}"))
        return
    ok = {STR: isinstance(value, str) and value.strip() != "", INT: isinstance(value, int) and not isinstance(value, bool),
          BOOL: isinstance(value, bool), DATE: is_date(value)}[kind]
    if not ok:
        out.append((at, f"is a {kind}, not {value!r}"))


def nulls(value, at: str):
    """The locations in value of a string that is the literal \\N."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from nulls(v, f"{at}.{k}" if at else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from nulls(v, f"{at}[{i}]")
    elif value == NULL:
        yield at


PLAIN = "is not plain YAML: text, numbers, true or false, lists, and mappings keyed by text"


def unplain(value, at: str):
    """(location, message) for anything in value but text, numbers, true or false, lists and mappings keyed by text."""
    if isinstance(value, dict):
        for k, v in value.items():
            where = f"{at}.{k}" if at else str(k)
            if isinstance(k, str):
                yield from unplain(v, where)
            elif isinstance(k, bool):
                yield where, (f"is a key YAML reads as {str(k).lower()}, not as text: unquoted, on, off, yes and no are true or "
                              "false, so quote it ('on')")
            else:
                yield where, PLAIN
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from unplain(v, f"{at}[{i}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        yield at, PLAIN


def sections(pack: Pack) -> list[Finding]:
    """S01 for the pack's sections."""
    out: list[Finding] = []
    data = pack.data
    for name in data:
        if name not in SECTIONS:
            out.append(Finding("S01", pack.at(str(name)), f"is not a section of a pack: the sections are {', '.join(SECTIONS)}"))
    for name in REQUIRED:
        if name not in data:
            out.append(Finding("S01", pack.at(name), "is required"))
    if isinstance(data.get("sources"), list) and len(data["sources"]) > 1 and "no_join" not in data:
        out.append(Finding("S01", pack.at("no_join"), "is required with more than one source: a pair is said not to join, never assumed"))
    for name, kind in SECTIONS.items():
        if name in data:
            found: list[tuple[str, str]] = []
            shape(data[name], kind, name, found)
            out += [Finding("S01", pack.at(where), message) for where, message in found]
    aliases = (data.get("terminology") or {}).get("aliases") if isinstance(data.get("terminology"), dict) else None
    for i, a in enumerate(aliases if isinstance(aliases, list) else []):
        if isinstance(a, dict) and sum(k in a for k in ALIAS_KINDS) != 1:
            out.append(Finding("S01", pack.at(f"terminology.aliases[{i}]"), f"names one of {', '.join(ALIAS_KINDS)}"))
    for where in nulls(data, ""):
        out.append(Finding("S01", pack.at(where), "is the literal \\N, which the artifact writes for NULL: it cannot be a value"))
    for where, message in unplain(data, ""):
        out.append(Finding("S01", pack.at(where), message))
    return out


def ident(name) -> bool:
    """A table's or a column's name: it ends a dotted id, so it holds no dot."""
    return isinstance(name, str) and name != "" and "." not in name


def lock_shape(pack: Pack, sources: bool = True) -> list[Finding]:
    """S01 for the lock: the pack's own, holding a catalogue for each source the pack harvests (with sources, when the
    pack's sources hold their shape), and nothing else."""
    lock = pack.lock
    if lock is None:
        return []
    out: list[Finding] = []

    def bad(where: str, message: str) -> None:
        out.append(Finding("S01", f"{LOCK}:{where}" if where else LOCK, message))
    if not isinstance(lock, dict) or not isinstance(lock.get("sources"), dict):
        bad("", "is not a lock: it has no sources. Run make lock")
        return out
    if lock.get("client") != pack.client:
        bad("client", f"is {lock.get('client')!r}, not {pack.client!r}: a lock is its own client's. Run make lock")
    if lock.get("format") != LOCK_FORMAT:
        bad("format", f"is {lock.get('format')!r}; this compiler reads format {LOCK_FORMAT}. Run make lock")
    harvested = ([s["id"] for s in pack.data["sources"] + pack.data.get("curated_sources", []) if s.get("harvest") is True]
                 if sources else list(lock["sources"]))
    missing = [s for s in harvested if s not in lock["sources"]]
    extra = [s for s in lock["sources"] if s not in harvested]
    if missing or extra:
        said = [f"holds no catalogue for {', '.join(missing)}, which the pack harvests"] if missing else []
        said += [f"holds {', '.join(extra)}, which the pack does not harvest"] if extra else []
        bad("sources", f"{'; '.join(said)}. Run make lock")
    for s, held in lock["sources"].items():
        tables = held.get("tables") if isinstance(held, dict) else None
        if not isinstance(tables, list):
            bad(f"sources.{s}", "has no tables list")
            continue
        names: set[str] = set()
        for i, t in enumerate(tables):
            at = f"sources.{s}.{t.get('name') if isinstance(t, dict) and ident(t.get('name')) else f'tables[{i}]'}"
            if not isinstance(t, dict) or not ident(t.get("name")) or t["name"] in names:
                bad(at, "is a table with a name of its own, holding no dot")
                continue
            names.add(t["name"])
            cols = t.get("columns")
            if not isinstance(cols, list) or not all(
                    isinstance(c, dict) and ident(c.get("name")) and isinstance(c.get("type"), str) and c["type"]
                    and "description" in c and (c["description"] is None or isinstance(c["description"], str))
                    and isinstance(c.get("ordinal"), int) for c in cols):
                bad(f"{at}.columns", "is a list of columns {name, type, description, ordinal}, each name holding no dot")
                continue
            column_names = [c["name"] for c in cols]
            if len(set(column_names)) != len(column_names):
                bad(f"{at}.columns", "names a column twice")
            pk = t.get("primary_key")
            if not isinstance(pk, list) or not all(c in column_names for c in pk):
                bad(f"{at}.primary_key", "is a list of the table's columns")
            fks = t.get("foreign_keys")
            if not isinstance(fks, list) or not all(
                    isinstance(k, dict) and isinstance(k.get("ref_table"), str) and all(
                        isinstance(k.get(f), list) and all(isinstance(c, str) for c in k[f]) for f in ("columns", "ref_columns"))
                    for k in fks):
                bad(f"{at}.foreign_keys", "is a list of {columns, ref_table, ref_columns}")
    for where in nulls(lock, ""):
        bad(where, "is the literal \\N, which the artifact writes for NULL: it cannot be a value")
    return out


SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")
RANGE = re.compile(r"(\^|~|>=|<=|>|<|=)?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def semver(text) -> tuple[int, int, int] | None:
    m = SEMVER.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def satisfies(version: tuple[int, int, int], spec: str) -> bool | None:
    """Whether version is in spec, a semver range: comparators joined by spaces (>=0.1 <1), ^ and ~ as npm reads them,
    and a partial version as every version it starts. None if spec is not a range."""
    tokens = spec.split() if isinstance(spec, str) else []
    if not tokens:
        return None
    ok = True
    for tok in tokens:
        m = RANGE.fullmatch(tok)
        if not m:
            return None
        op, given = m.group(1) or "=", [int(x) for x in m.groups()[1:] if x is not None]
        low = tuple(given + [0] * (3 - len(given)))
        if op == "^":
            high = (low[0] + 1, 0, 0) if low[0] or len(given) == 1 else (0, low[1] + 1, 0) if low[1] or len(given) == 2 else (0, 0, low[2] + 1)
        elif op == "~":
            high = (low[0] + 1, 0, 0) if len(given) == 1 else (low[0], low[1] + 1, 0)
        elif op == "=":
            high = (low[0] + 1, 0, 0) if len(given) == 1 else (low[0], low[1] + 1, 0) if len(given) == 2 else (low[0], low[1], low[2] + 1)
        if op in ("^", "~", "="):
            ok = ok and low <= version < high
        else:
            ok = ok and {">=": version >= low, ">": version > low, "<=": version <= low, "<": version < low}[op]
    return ok


def identity(pack: Pack) -> list[Finding]:
    """S00: the manifest names the client, which is the folder's name, and the meta-model version it was written for."""
    m = pack.data.get("manifest")
    if not isinstance(m, dict):
        return [Finding("S00", pack.at("manifest"), "is required: a mapping that names the client and the meta-model version")]
    out = []
    client = m.get("client")
    if client is None:
        out.append(Finding("S00", pack.at("manifest.client"), f"is required: the client this pack is, {pack.client!r}, its folder's name"))
    elif not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
        also = "" if client == pack.client else f", and not the folder's name, {pack.client}"
        out.append(Finding("S00", pack.at("manifest.client"),
                           f"manifest client {client!r} is refused: {CLIENT_ID_RULE}{also}"))
    elif client != pack.client:
        out.append(Finding("S00", pack.at("manifest.client"), f"is {client!r}, not the folder's name, {pack.client}: a pack is its own client's"))
    version = m.get("meta_model_version")
    if version is None:
        out.append(Finding("S00", pack.at("manifest.meta_model_version"), "is required: the meta-model version the pack was written for"))
    elif semver(version) is None:
        out.append(Finding("S00", pack.at("manifest.meta_model_version"), f"{version!r} is not a semver version (major.minor.patch)"))
    return out


SLUG = re.compile(r"[a-z0-9][a-z0-9_.-]*")
SOURCE_ID = re.compile(r"[a-z][a-z0-9_-]*")
ACRONYM = re.compile(r"[A-Z][A-Z0-9_]*")


ID_SECTIONS = ("manifest", "sources", "glossary", "calendars", "measures", "policies", "access", "tests", "curated_sources",
               "contexts", "filters", "row_selectors", "joins", "acronyms", "entities", "relations", "ontology")
POLICY_KINDS = ("restricted_attributes", "scd2", "release_driver")


def ids(pack: Pack, held=ID_SECTIONS) -> list[Finding]:
    """S02, in the sections held names: those whose shapes hold. Every kind of policy shares one id namespace, since a
    Policy node is keyed by its id, and filters and row selectors share another, since a conflict names either."""
    d, out = pack.data, []

    def unique(entries: list, field: str, section: str, pattern=SLUG, why: str = "") -> None:
        seen: dict[str, int] = {}
        for i, e in enumerate(entries):
            v = e[field]
            at = pack.at(f"{section}[{i}].{field}")
            if pattern is not None and not pattern.fullmatch(v):
                out.append(Finding("S02", at, f"{v!r} is not an id (^{pattern.pattern}$)"))
            if v in seen:
                out.append(Finding("S02", at, f"{v!r} is {section}[{seen[v]}]'s already: unique{why}"))
            else:
                seen[v] = i
    if "manifest" in held and not SLUG.fullmatch(d["manifest"]["id"]):
        out.append(Finding("S02", pack.at("manifest.id"), f"{d['manifest']['id']!r} is not an id (^{SLUG.pattern}$)"))
    if "sources" in held:
        unique(d["sources"], "id", "sources", SOURCE_ID)
    if "curated_sources" in held:
        unique(d.get("curated_sources", []), "id", "curated_sources", SOURCE_ID)
        units = {s["id"] for s in d["sources"]} if "sources" in held else set()
        for i, c in enumerate(d.get("curated_sources", [])):
            if c["id"] in units:
                out.append(Finding("S02", pack.at(f"curated_sources[{i}].id"), f"{c['id']!r} is a source's id already: a curated "
                                   "source is no source, and its tables' ids start with its own"))
    if "glossary" in held:
        unique(d["glossary"], "id", "glossary")
        unique(d["glossary"], "name", "glossary", None, ", as the graph keys a term by its name")
        for i, g in enumerate(d["glossary"]):
            docs = g.get("defined_by", [])
            for j, doc in enumerate(docs):
                if doc in docs[:j]:
                    out.append(Finding("S02", pack.at(f"glossary[{i}].defined_by[{j}]"), f"{doc} is listed already"))
    if "calendars" in held:
        unique(d.get("calendars", []), "id", "calendars")
    if "measures" in held:
        unique(d["measures"], "id", "measures")
    if "policies" in held:
        seen_policy: dict[str, str] = {}
        for kind in POLICY_KINDS:
            for i, x in enumerate((d.get("policies") or {}).get(kind, [])):
                at = f"policies.{kind}[{i}]"
                if not SLUG.fullmatch(x["id"]):
                    out.append(Finding("S02", pack.at(f"{at}.id"), f"{x['id']!r} is not an id (^{SLUG.pattern}$)"))
                if x["id"] in seen_policy:
                    out.append(Finding("S02", pack.at(f"{at}.id"), f"{x['id']!r} is {seen_policy[x['id']]}'s already: unique"))
                else:
                    seen_policy[x["id"]] = at
    if "access" in held:
        unique((d.get("access") or {}).get("roles", []), "id", "access.roles")
        for i, p in enumerate((d.get("access") or {}).get("purposes", [])):
            if p in d["access"]["purposes"][:i]:
                out.append(Finding("S02", pack.at(f"access.purposes[{i}]"), f"{p!r} is listed already"))
    if "tests" in held:
        unique(d["tests"]["sets"], "name", "tests.sets")
        for i, s in enumerate(d["tests"]["sets"]):
            items = s.get("items", [])
            out += [Finding("S02", pack.at(f"tests.sets[{i}].items[{j}]"), f"{x} is listed already") for j, x in enumerate(items)
                    if x in items[:j]]
    for section in ("contexts", "joins", "entities", "relations", "ontology"):
        if section in held:
            unique(d.get(section, []), "id", section)
    if "filters" in held and "row_selectors" in held:
        seen: dict[str, str] = {}
        for section in ("filters", "row_selectors"):
            for i, x in enumerate(d.get(section, [])):
                at = pack.at(f"{section}[{i}].id")
                if not SLUG.fullmatch(x["id"]):
                    out.append(Finding("S02", at, f"{x['id']!r} is not an id (^{SLUG.pattern}$)"))
                if x["id"] in seen:
                    out.append(Finding("S02", at, f"{x['id']!r} is {seen[x['id']]}'s already: unique among the filters and row selectors"))
                else:
                    seen[x["id"]] = f"{section}[{i}]"
    if "acronyms" in held:
        unique(d.get("acronyms", []), "id", "acronyms", ACRONYM)
    return out


def compatibility(pack: Pack, meta: dict, compiler_version: str) -> list[Finding]:
    """S03, for the manifest and the schema."""
    m, out = pack.data["manifest"], []
    installed = semver(meta["version"])
    for key, have, what in (("meta_model", installed, f"the meta-model, {meta['version']}"),
                            ("compiler", semver(compiler_version), f"this compiler, {compiler_version}")):
        spec = m["platform"][key]
        ok = satisfies(have, spec)
        if ok is None:
            out.append(Finding("S03", pack.at(f"manifest.platform.{key}"), f"{spec!r} is not a semver range (such as ^1.0 or >=0.1)"))
        elif not ok:
            out.append(Finding("S03", pack.at(f"manifest.platform.{key}"), f"{spec!r} is not satisfied by {what}"))
    written = semver(m.get("meta_model_version"))
    if written and (written[0] != installed[0] or written > installed):
        out.append(Finding("S03", pack.at("manifest.meta_model_version"),
                           f"the pack was written for meta-model {m['meta_model_version']}, and the meta-model is {meta['version']}: "
                           "a pack compiles against its own major version, at its minor or later"))
    pinned = pack.data["schema"]["meta_model"]
    if written and pinned != f"{written[0]}.{written[1]}":
        out.append(Finding("S03", pack.at("schema.meta_model"), f"pins {pinned!r}, but the manifest's meta_model_version is {m['meta_model_version']}"))
    return out


def fillable(meta: dict, compiler_version: str, fills: set[str]) -> list[Finding]:
    """S03, for the meta-model: this compiler fills every type it gives the pack or the lock to fill."""
    out = []
    for kind in ("node_types", "edge_types"):
        for name, t in meta[kind].items():
            if t.get("origin") in ("lock", "pack") and name not in fills:
                out.append(Finding("S03", f"meta-model.yaml:{kind}.{name}",
                                   f"this compiler ({compiler_version}) cannot fill {name}, which the meta-model gives the "
                                   f"{t['origin']} to fill: compile with a compiler that can"))
    return out


EXTENSION_TYPES = ("STRING", "BOOLEAN", "INT64", "DOUBLE")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
EXTENSION_FIELDS = {"node_type": {"node_type", "key", "properties", "layer"}, "edge_type": {"edge_type", "from", "to", "properties", "layer"},
                    "property": {"property", "extends", "type"}}


def namespace(client: str) -> str:
    return f"x_{client.replace('-', '_')}_"


def extensions(pack: Pack, meta: dict) -> list[Finding]:
    """S04: each extension adds a namespaced node type, edge type or property, curated, of the meta-model's types, and
    takes no name the meta-model or another extension has; an entry carries only the properties its type was given."""
    out, ns = [], namespace(pack.client)
    nodes, edges = meta["node_types"], meta["edge_types"]
    taken = set(nodes) | set(edges) | {t["loaded_as"] for t in [*nodes.values(), *edges.values()]}
    exts = pack.data["schema"].get("extensions") or []
    ext_nodes = {e["node_type"] for e in exts if isinstance(e, dict) and isinstance(e.get("node_type"), str)}
    added: dict[str, dict[str, str]] = {}

    def typed(props, at: str) -> None:
        if not isinstance(props, dict):
            out.append(Finding("S04", pack.at(at), "is a mapping of property names to types"))
            return
        for p, t in props.items():
            if not isinstance(p, str) or not NAME.fullmatch(p):
                out.append(Finding("S04", pack.at(f"{at}.{p}"), "is not a property name"))
            elif t not in EXTENSION_TYPES:
                out.append(Finding("S04", pack.at(f"{at}.{p}"), f"is a {t!r}: an extension's property is one of {', '.join(EXTENSION_TYPES)}"))
    for i, e in enumerate(exts):
        at = f"schema.extensions[{i}]"
        kinds = [k for k in EXTENSION_FIELDS if isinstance(e, dict) and k in e]
        if len(kinds) != 1:
            out.append(Finding("S04", pack.at(at), "adds one node_type, edge_type or property"))
            continue
        kind = kinds[0]
        for f in e:
            if f not in EXTENSION_FIELDS[kind]:
                out.append(Finding("S04", pack.at(f"{at}.{f}"), f"is not a field of an extension that adds a {kind.replace('_', ' ')}"))
        name = e[kind]
        if not isinstance(name, str) or not name.startswith(ns) or not NAME.fullmatch(name):
            out.append(Finding("S04", pack.at(f"{at}.{kind}"), f"{name!r} is not namespaced: an extension's name starts {ns}"))
            continue
        if name in taken:
            out.append(Finding("S04", pack.at(f"{at}.{kind}"), f"{name} is taken already: an extension only adds"))
            continue
        if e.get("layer", "curated") != "curated":
            out.append(Finding("S04", pack.at(f"{at}.layer"), "is curated: an extension may not write to the harvested, access or runtime layers"))
        if kind == "node_type":
            taken.add(name)
            typed(e.get("properties"), f"{at}.properties")
            if not isinstance(e.get("properties"), dict) or not isinstance(e.get("key"), str) or e["key"] not in e["properties"]:
                out.append(Finding("S04", pack.at(f"{at}.key"), "names one of the type's properties"))
        elif kind == "edge_type":
            taken.add(name)
            typed(e.get("properties", {}), f"{at}.properties")
            for end in ("from", "to"):
                if not isinstance(e.get(end), str) or (e[end] not in nodes and e[end] not in ext_nodes):
                    out.append(Finding("S04", pack.at(f"{at}.{end}"), f"{e.get(end)!r} is not a node type of the meta-model or an extension"))
        else:
            extends = e.get("extends")
            target = nodes.get(extends) if isinstance(extends, str) else None
            if target is None or target.get("origin") != "pack" or target.get("layer") not in ("harvested", "curated"):
                types = ", ".join(n for n, t in nodes.items() if t.get("origin") == "pack" and t.get("layer") in ("harvested", "curated"))
                out.append(Finding("S04", pack.at(f"{at}.extends"), f"{extends!r} is not a type the pack fills: a property extends one of {types}"))
            elif e.get("type") not in EXTENSION_TYPES:
                out.append(Finding("S04", pack.at(f"{at}.type"), f"is one of {', '.join(EXTENSION_TYPES)}"))
            else:
                added.setdefault(extends, {})[name] = e["type"]
            taken.add(name)
    kinds = {"STRING": (str,), "INT64": (int,), "DOUBLE": (int, float), "BOOLEAN": (bool,)}
    for section, data in pack.data.items():
        if section == "schema" or section not in SECTIONS:
            continue
        for where, key, value in x_fields(data, section):
            entry = re.sub(r"\[\d+\]$", "", where)
            declared = added.get(FILLS.get(entry, ""), {})
            if entry not in FILLS or where == entry:
                out.append(Finding("S04", pack.at(f"{where}.{key}"), "is where no extension adds a property: one sits on an entry "
                                   f"of {', '.join(FILLS)}"))
            elif key not in declared:
                out.append(Finding("S04", pack.at(f"{where}.{key}"), f"is not declared in schema.extensions as a property of {FILLS[entry]}"))
            elif not isinstance(value, kinds[declared[key]]) or (declared[key] != "BOOLEAN" and isinstance(value, bool)):
                out.append(Finding("S04", pack.at(f"{where}.{key}"), f"is a {declared[key]}, as schema.extensions declares it"))
    return out


def x_fields(value, at: str):
    """(location, key, value) for every field named x_... in value, at any depth."""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.startswith("x_"):
                yield at, k, v
            else:
                yield from x_fields(v, f"{at}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from x_fields(v, f"{at}[{i}]")


FAMILIES = {"integer": {"TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                        "UBIGINT", "UHUGEINT", "INT1", "INT2", "INT4", "INT8"},
            "decimal": {"DECIMAL", "NUMERIC"}, "float": {"FLOAT", "REAL", "DOUBLE", "FLOAT4", "FLOAT8"},
            "text": {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR"}}


def family(sql_type: str) -> str:
    """A column type's family: two keys of one family can join."""
    base = sql_type.upper().split("(")[0].strip()
    return next((f for f, members in FAMILIES.items() if base in members), base)


def foreign_keys(pack: Pack, cover: set[tuple[str, str]] = frozenset()) -> list[Finding]:
    """S05. cover: the column pairs declared joins equate (covered)."""
    out = []
    for s, held in pack.lock["sources"].items():
        tables = {t["name"]: t for t in held["tables"]}
        for t in held["tables"]:
            cols = {c["name"]: c for c in t["columns"]}
            for i, k in enumerate(t["foreign_keys"]):
                at = f"{LOCK}:sources.{s}.{t['name']}.foreign_keys[{i}]"
                ref = tables.get(k["ref_table"])
                if ref is None:
                    out.append(Finding("S05", at, f"references {k['ref_table']}, which is not a table of {s}: a key joins within its source"))
                    continue
                refs = {c["name"]: c for c in ref["columns"]}
                if not k["columns"] or len(k["columns"]) != len(k["ref_columns"]):
                    out.append(Finding("S05", at, "pairs each of its columns with one column it references"))
                    continue
                missing = [f"{t['name']}.{c}" for c in k["columns"] if c not in cols]
                missing += [f"{ref['name']}.{c}" for c in k["ref_columns"] if c not in refs]
                if missing:
                    out.append(Finding("S05", at, f"names {', '.join(missing)}, not a column in the lock"))
                    continue
                for a, b in zip(k["columns"], k["ref_columns"]):
                    if family(cols[a]["type"]) != family(refs[b]["type"]):
                        out.append(Finding("S05", at, f"joins {t['name']}.{a} ({cols[a]['type']}) to {ref['name']}.{b} "
                                           f"({refs[b]['type']}), types that do not join"))
                pairs = [(f"{s}.{t['name']}.{a}", f"{s}.{ref['name']}.{b}") for a, b in zip(k["columns"], k["ref_columns"])]
                if len(k["columns"]) > 1 and not all(p in cover for p in pairs):
                    out.append(Finding("S05", at, f"is a composite key, ({', '.join(k['columns'])}) -> {ref['name']} "
                                       f"({', '.join(k['ref_columns'])}): a JOINS edge holds one column pair, so it is reported "
                                       "until a declared join (joins) equates every pair of it"))
    return out


def locate(lock: dict, ref, depth: int = 3):
    """The lock's table (depth 2, source.table) or column (depth 3, source.table.column) ref names, or None."""
    parts = ref.split(".") if isinstance(ref, str) else []
    if len(parts) != depth or parts[0] not in lock["sources"]:
        return None
    table = next((t for t in lock["sources"][parts[0]]["tables"] if t["name"] == parts[1]), None)
    if depth == 2 or table is None:
        return table
    return next((c for c in table["columns"] if c["name"] == parts[2]), None)


def no_join(pack: Pack) -> list[Finding]:
    """S06."""
    declared = [s["id"] for s in pack.data["sources"]]
    out, seen = [], {}
    for i, f in enumerate(pack.data.get("no_join") or []):
        at = pack.at(f"no_join[{i}].between")
        pair = f["between"]
        if len(pair) != 2:
            out.append(Finding("S06", at, "names two sources"))
            continue
        unknown = [s for s in pair if s not in declared]
        if unknown:
            out.append(Finding("S06", at, f"names {', '.join(unknown)}, not a declared source"))
        if pair[0] == pair[1]:
            out.append(Finding("S06", at, f"names {pair[0]} twice: a no-join fact is between two sources"))
            continue
        key = frozenset(pair)
        if key in seen:
            out.append(Finding("S06", at, f"asserts {pair[0]}-{pair[1]} again: no_join[{seen[key]}] does"))
        else:
            seen[key] = i
    return out


def mappings(pack: Pack) -> list[Finding]:
    """S07."""
    terms = {g["id"] for g in pack.data["glossary"]}
    out, seen = [], {}
    for i, m in enumerate(pack.data["mappings"]):
        if locate(pack.lock, m["column"]) is None:
            out.append(Finding("S07", pack.at(f"mappings[{i}].column"), f"{m['column']} is not a column in pack.lock (source.table.column)"))
        if m["term"] not in terms:
            out.append(Finding("S07", pack.at(f"mappings[{i}].term"), f"{m['term']} is not a term the glossary declares"))
        if m["certified"] and not (m.get("certified_by") and m.get("certified_on")):
            out.append(Finding("S07", pack.at(f"mappings[{i}]"), "is certified: it says by whom and when (certified_by, certified_on)"))
        key = (m["column"], m["term"])
        if key in seen:
            out.append(Finding("S07", pack.at(f"mappings[{i}]"), f"maps {m['column']} to {m['term']} again: mappings[{seen[key]}] does"))
        else:
            seen[key] = i
    return out


READ_ONLY = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean,
             exp.Star, exp.Distinct, exp.Paren, exp.Neg, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.EQ, exp.NEQ, exp.GT,
             exp.GTE, exp.LT, exp.LTE, exp.Is, exp.In, exp.Between, exp.Like, exp.ILike, exp.Filter, exp.Where, exp.And,
             exp.Or, exp.Not, exp.Case, exp.If, exp.Cast, exp.DataType, exp.DataTypeParam, exp.Coalesce, exp.Nullif)


def selects(c: exp.Column) -> bool:
    """Whether a column is read only to choose a CASE's branch (its WHEN condition, or the operand it compares), not
    for a value the aggregate adds up."""
    node = c
    while node.parent is not None and not isinstance(node.parent, exp.AggFunc):
        parent = node.parent
        if (isinstance(parent, exp.If) and parent.args.get("this") is node) or (isinstance(parent, exp.Case) and parent.args.get("this") is node):
            return True
        node = parent
    return False


def expression(text: str, column: str, lock: dict) -> tuple[list[str], dict[str, str], str]:
    """A measure's expression, read with sqlglot: what is wrong with it, the lock columns it reads ({id: value or
    filter}, a filter's being those under FILTER (WHERE ...) or a CASE's condition) and the aggregation it is (sum, count, count_distinct, avg,
    ratio, or another's name). column is the measure's column: its source and table are the expression's own, and the
    only ones it may read, since it has no FROM and no join, only its table's rows. It may be built only of what
    READ_ONLY holds (aggregates, its table's columns and literals, arithmetic and comparisons, FILTER (WHERE ...), CASE,
    IF, CAST, COALESCE and NULLIF): no query, window, statement or other function, so nothing that reads a file or the
    host."""
    source, table = column.split(".")[:2]
    try:
        statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        return [f"does not parse as SQL ({str(exc).splitlines()[0]})"], {}, ""
    if len(statements) != 1:
        return [f"is one expression, not {len(statements)} statements"], {}, ""
    root = statements[0]
    problems, cols = [], {}
    for node in root.walk():
        if type(node) not in READ_ONLY:
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name().lower() if isinstance(node, exp.Func) else node.key
            problems.append(f"reads only its rows: {name} is not an aggregate, an operator, FILTER, CASE, IF, CAST, COALESCE "
                            "or NULLIF, which are all a measure may use")
            return problems, {}, ""
    if not any(root.find_all(exp.AggFunc)):
        problems.append("is an aggregate over the rows, such as sum(table.column)")
    for agg in root.find_all(exp.AggFunc):
        if agg.find_ancestor(exp.AggFunc, exp.Where):
            problems.append(f"{agg.sql()} is an aggregate inside an aggregate")
    for c in root.find_all(exp.Column):
        if c.find_ancestor(exp.AggFunc, exp.Filter) is None:
            problems.append(f"{c.sql()} is outside an aggregate: a measure is one value over the rows")
        if c.catalog or (c.db and c.db != source) or (c.table and c.table != table):
            problems.append(f"{c.sql()} is in another table: a measure reads the rows of its own table, {source}.{table}")
            continue
        ref = f"{source}.{table}.{c.name}"
        if locate(lock, ref) is None:
            problems.append(f"{c.sql()} is not a column of {source}.{table} in pack.lock")
        else:
            cols[ref] = "filter" if c.find_ancestor(exp.Where) or selects(c) else cols.get(ref, "value")
    top = root.this if isinstance(root, exp.Filter) else root
    kind = ("sum" if isinstance(top, exp.Sum) else "avg" if isinstance(top, exp.Avg) else "ratio" if isinstance(top, exp.Div)
            else ("count_distinct" if top.find(exp.Distinct) else "count") if isinstance(top, exp.Count) else top.key)
    return problems, cols, kind


def measures(pack: Pack) -> list[Finding]:
    """S08."""
    terms = {g["id"] for g in pack.data["glossary"]}
    out, by_column = [], {}
    for i, x in enumerate(pack.data["measures"]):
        if x["term"] not in terms:
            out.append(Finding("S08", pack.at(f"measures[{i}].term"), f"{x['term']} is not a term the glossary declares"))
        if locate(pack.lock, x["column"]) is None:
            out.append(Finding("S08", pack.at(f"measures[{i}].column"), f"{x['column']} is not a column in pack.lock (source.table.column)"))
            continue
        if x["column"] in by_column:
            out.append(Finding("S08", pack.at(f"measures[{i}].column"), f"carries measures[{by_column[x['column']]}] already: a column "
                               "carries one measure, whose grain, currency, calendar and conversion it is given"))
        else:
            by_column[x["column"]] = i
        problems, cols, kind = expression(x["expression"], x["column"], pack.lock)
        out += [Finding("S08", pack.at(f"measures[{i}].expression"), p) for p in problems]
        if problems:
            continue
        if x["column"] not in cols:
            out.append(Finding("S08", pack.at(f"measures[{i}].column"), f"is not in the measure's expression, {x['expression']}"))
        if x["aggregation"] != "last_snapshot" and x["aggregation"] != kind:
            out.append(Finding("S08", pack.at(f"measures[{i}].aggregation"), f"is {x['aggregation']}, and the expression is {kind or 'none'}"))
    return out


def calendars(pack: Pack) -> list[Finding]:
    """S09."""
    d, lock, out = pack.data, pack.lock, []
    declared = {c["id"] for c in d.get("calendars", [])}
    for i, c in enumerate(d.get("calendars", [])):
        if not 1 <= c["year_start_month"] <= 12:
            out.append(Finding("S09", pack.at(f"calendars[{i}].year_start_month"), f"is {c['year_start_month']}: a month, 1 to 12"))
        if "{n}" not in c["quarter_label"]:
            out.append(Finding("S09", pack.at(f"calendars[{i}].quarter_label"), "names the quarter's number, {n}"))
    bound: dict[str, int] = {}
    for i, b in enumerate(d.get("period_bindings", [])):
        at = f"period_bindings[{i}]"
        table = locate(lock, b["table"], 2)
        if table is None:
            out.append(Finding("S09", pack.at(f"{at}.table"), f"{b['table']} is not a table in pack.lock (source.table)"))
        else:
            if b["table"] in bound:
                out.append(Finding("S09", pack.at(f"{at}.table"), f"is bound already, by period_bindings[{bound[b['table']]}]: a table "
                                   "has one period column"))
            else:
                bound[b["table"]] = i
            if b["column"] not in {c["name"] for c in table["columns"]}:
                out.append(Finding("S09", pack.at(f"{at}.column"), f"{b['column']} is not a column of {b['table']} in pack.lock"))
        if b["calendar"] not in declared:
            out.append(Finding("S09", pack.at(f"{at}.calendar"), f"{b['calendar']} is not a declared calendar"))
        rollup = b.get("rollup")
        if rollup:
            into = locate(lock, rollup["table"], 2)
            if into is None:
                out.append(Finding("S09", pack.at(f"{at}.rollup.table"), f"{rollup['table']} is not a table in pack.lock (source.table)"))
            elif rollup["table"].split(".")[0] != b["table"].split(".")[0]:
                out.append(Finding("S09", pack.at(f"{at}.rollup.table"), "rolls up within the table's own source"))
            elif rollup["column"] not in {c["name"] for c in into["columns"]}:
                out.append(Finding("S09", pack.at(f"{at}.rollup.column"), f"{rollup['column']} is not a column of {rollup['table']} in pack.lock"))
    for i, x in enumerate(d["measures"]):
        if x["calendar"] not in declared:
            out.append(Finding("S09", pack.at(f"measures[{i}].calendar"), f"{x['calendar']} is not a declared calendar"))
        table = ".".join(x["column"].split(".")[:2])
        if locate(lock, x["column"]) is not None and x["grain"] in GRAINS and table not in bound:
            out.append(Finding("S09", pack.at(f"measures[{i}].column"), f"{table} has no period binding, and a measure of grain "
                               f"{x['grain']} is read by period"))
    return out


ISO_4217 = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF
CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD
HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB
RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN
UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW
ZWG ZWL
""".split())
CONVERSION = re.compile(r"[a-z][a-z0-9_]*_from_([A-Z]{3})")


def currencies(pack: Pack) -> list[Finding]:
    """S10."""
    out = []
    for i, x in enumerate(pack.data["measures"]):
        if x["currency"] not in ISO_4217:
            out.append(Finding("S10", pack.at(f"measures[{i}].currency"), f"{x['currency']} is not an ISO 4217 currency code"))
        if x["conversion"] == "none":
            continue
        m = CONVERSION.fullmatch(x["conversion"])
        if not m:
            out.append(Finding("S10", pack.at(f"measures[{i}].conversion"), "is none, or <rate>_from_<currency>, such as period_end_rate_from_EUR"))
        elif m.group(1) not in ISO_4217:
            out.append(Finding("S10", pack.at(f"measures[{i}].conversion"), f"converts from {m.group(1)}, not an ISO 4217 currency code"))
        elif m.group(1) == x["currency"]:
            out.append(Finding("S10", pack.at(f"measures[{i}].conversion"), f"converts from {x['currency']}, the measure's own currency: "
                               "a conversion is from another"))
    return out


def aliases(pack: Pack) -> list[Finding]:
    """S11."""
    terms = {g["id"] for g in pack.data["glossary"]}
    names = {g["name"].lower(): g["id"] for g in pack.data["glossary"]}
    sources = {s["id"] for s in pack.data["sources"]}
    entities = {e["id"] for e in pack.data.get("entities", [])}
    out, seen = [], {}
    for i, a in enumerate(pack.data["terminology"]["aliases"]):
        at = f"terminology.aliases[{i}]"
        kind = next(k for k in ALIAS_KINDS if k in a)
        if kind == "term" and a["term"] not in terms:
            out.append(Finding("S11", pack.at(f"{at}.term"), f"{a['term']} is not a term the glossary declares"))
        if kind == "source" and a["source"] not in sources:
            out.append(Finding("S11", pack.at(f"{at}.source"), f"{a['source']} is not a declared source"))
        if kind == "candidate" and a["candidate"].lower() in names:
            out.append(Finding("S11", pack.at(f"{at}.candidate"), f"{a['candidate']} is a declared term: name it, term: "
                               f"{names[a['candidate'].lower()]}"))
        for k in ("group_by", "column"):
            if kind == k and locate(pack.lock, a[k]) is None:
                out.append(Finding("S11", pack.at(f"{at}.{k}"), f"{a[k]} is not a column in pack.lock (source.table.column)"))
        if kind == "entity" and a["entity"] not in entities:
            out.append(Finding("S11", pack.at(f"{at}.entity"), f"{a['entity']} is not a declared entity"))
        for k, known in (("time_mode", TIME_MODES), ("period", PERIODS)):
            if kind == k and a[k] not in known:
                out.append(Finding("S11", pack.at(f"{at}.{k}"), f"{a[k]!r} is not a {k.replace('_', ' ')} the platform knows "
                                   f"({', '.join(known)})"))
        phrase = a["phrase"].strip().lower()
        if phrase in seen:
            j, target = seen[phrase]
            said = "is listed already" if target == (kind, a[kind]) else f"names {kind} {a[kind]} here and {target[0]} {target[1]} there"
            out.append(Finding("S11", pack.at(f"{at}.phrase"), f"{a['phrase']!r} is terminology.aliases[{j}]'s phrase too, and {said}: "
                               "a phrase has one target"))
        else:
            seen[phrase] = (i, (kind, a[kind]))
    return out


WRITES = ("terms", "mappings")


def roles(pack: Pack, meta: dict) -> list[Finding]:
    """S14."""
    sources = {s["id"] for s in pack.data["sources"]}
    processes = sorted(process_vocabulary((meta["node_types"].get("Process") or {}).get("constants")))
    out = []
    for i, r in enumerate((pack.data.get("access") or {}).get("roles", [])):
        at = f"access.roles[{i}]"
        if r["reads"] != "all":
            out += [Finding("S14", pack.at(f"{at}.reads[{j}]"), f"{s} is not a declared source") for j, s in enumerate(r["reads"]) if s not in sources]
        out += [Finding("S14", pack.at(f"{at}.writes[{j}]"), f"{w}: a role writes {' or '.join(WRITES)}, and nothing else")
                for j, w in enumerate(r.get("writes", [])) if w not in WRITES]
        out += [Finding("S14", pack.at(f"{at}.processes[{j}]"), f"{p} is not one of the platform's processes ({', '.join(processes)})")
                for j, p in enumerate(r["processes"]) if p not in processes]
    return out


def restricted(pack: Pack) -> list[Finding]:
    """S22."""
    out = []
    for i, r in enumerate((pack.data.get("policies") or {}).get("restricted_attributes", [])):
        for j, c in enumerate(r["columns"]):
            at = pack.at(f"policies.restricted_attributes[{i}].columns[{j}]")
            if locate(pack.lock, c) is None:
                out.append(Finding("S22", at, f"{c} is not a column in pack.lock (source.table.column)"))
            elif c in r["columns"][:j]:
                out.append(Finding("S22", at, f"{c} is listed already"))
        said: set[str] = set()
        for j, x in enumerate(r.get("phrases") or []):
            at = f"policies.restricted_attributes[{i}].phrases[{j}]"
            if x["column"] not in r["columns"]:
                out.append(Finding("S22", pack.at(f"{at}.column"), f"{x['column']} is not a column this policy restricts"))
            for k, phrase in enumerate(x["phrases"]):
                words = " ".join(w for w in re.split(r"[\s_-]+", phrase.lower()) if w)
                if not words:
                    out.append(Finding("S22", pack.at(f"{at}.phrases[{k}]"), f"{phrase!r} names no word"))
                elif words in said:
                    out.append(Finding("S22", pack.at(f"{at}.phrases[{k}]"), f"{phrase} is given already: a phrase asks for one column"))
                said.add(words)
    return out


def vocabulary(pack: Pack) -> list[Finding]:
    """S29."""
    t = pack.data["terminology"]
    blocked = {c for r in (pack.data.get("policies") or {}).get("restricted_attributes", []) for c in r["columns"]}
    out = []

    def column(at: str, c: str) -> None:
        if locate(pack.lock, c) is None:
            out.append(Finding("S29", pack.at(f"{at}.column"), f"{c} is not a column in pack.lock (source.table.column)"))
        elif c in blocked:
            out.append(Finding("S29", pack.at(f"{at}.column"), f"{c} is restricted personal data: no question filters on it"))
    for i, v in enumerate(t.get("value_vocabularies") or []):
        at = f"terminology.value_vocabularies[{i}]"
        column(at, v["column"])
        if bool(v.get("values")) == bool(v.get("pattern")):
            out.append(Finding("S29", pack.at(at), "gives the values a question names, or a pattern the platform knows "
                               f"({', '.join(PATTERNS)}): one of them"))
        elif v.get("prefix") and not v.get("pattern"):
            out.append(Finding("S29", pack.at(f"{at}.prefix"), "a prefix goes with a pattern: the values are written whole"))
    for i, x in enumerate(t.get("predicates") or []):
        at = f"terminology.predicates[{i}]"
        column(at, x["column"])
        value = x.get("value")
        if x["op"] in NEEDS_VALUE and value is None:
            out.append(Finding("S29", pack.at(f"{at}.value"), f"is required: {x['op']} compares the column with a value"))
        elif x["op"] not in NEEDS_VALUE and "value" in x:
            out.append(Finding("S29", pack.at(f"{at}.value"), f"{x['op']} takes no value"))
        elif x["op"] in COMPARES and (isinstance(value, bool) or not isinstance(value, (int, float))):
            out.append(Finding("S29", pack.at(f"{at}.value"), f"{x['op']} compares with a number, not {value!r}"))
        elif x["op"] == "eq" and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
            out.append(Finding("S29", pack.at(f"{at}.value"), f"is text or a number, not {value!r}"))
    seen: dict[str, int] = {}
    for i, x in enumerate(t.get("intents") or []):
        if x["intent"] in seen:
            out.append(Finding("S29", pack.at(f"terminology.intents[{i}].intent"), f"{x['intent']} is terminology.intents"
                               f"[{seen[x['intent']]}]'s: give each intent's phrases once"))
        seen.setdefault(x["intent"], i)
    held: dict[str, int] = {}
    for i, m in enumerate(t.get("functional_mailboxes") or []):
        at = pack.at(f"terminology.functional_mailboxes[{i}]")
        if not MAILBOX.fullmatch(m):
            out.append(Finding("S29", at, f"{m!r} is not a mailbox's local part (lower case, {MAILBOX.pattern})"))
        elif m in held:
            out.append(Finding("S29", at, f"{m} is listed already"))
        held.setdefault(m, i)
    return out


PREDICATE = (exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean, exp.Paren, exp.Neg, exp.Add, exp.Sub, exp.Mul,
             exp.Div, exp.Mod, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is, exp.In, exp.Between, exp.Like,
             exp.ILike, exp.And, exp.Or, exp.Not, exp.Case, exp.If, exp.Cast, exp.DataType, exp.DataTypeParam, exp.Coalesce,
             exp.Nullif, exp.LastDay, exp.DateTrunc, exp.TimestampTrunc, exp.Interval, exp.Var)


class Condition(NamedTuple):
    """A condition as sqlglot reads it: what is wrong with it; the lock columns it names (source.table.column) and their
    tables; its conjuncts, as SQL; each conjunct that equates two columns, as ((name, column), (name, column)), name
    being what the column's table goes by there; and for each conjunct that equates two values, the names each side
    reads (last_day(fact.event_date) = cal.period_end links fact to cal)."""
    problems: list[str]
    columns: list[str]
    tables: set[str]
    atoms: list[str]
    equal: list[tuple[tuple[str, str], tuple[str, str]]]
    links: list[tuple[frozenset, frozenset]]


def condition(text: str, lock: dict, names: dict[str, str], tables: set[str], within: str | None = None) -> Condition:
    """text, a SQL condition, against the lock. It reads the tables given ({source}.{table}), each named table.column by
    the name names gives it (its own, or an alias), or source.table.column; within, a source, lets it read any other
    table of that source by its name too (a row selector matches the rows of the facts it is used with). It may be built
    only of what PREDICATE holds (columns and values, arithmetic and comparisons, AND, OR, NOT, IN, BETWEEN, CASE, IF,
    CAST, COALESCE, NULLIF and the date functions a calendar rollup needs): no aggregate, query or other function,
    since a condition reads the rows it is on and nothing else."""

    try:
        statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        return Condition([f"does not parse as SQL ({str(exc).splitlines()[0]})"], [], set(), [], [], [])
    if len(statements) != 1:
        return Condition([f"is one condition, not {len(statements)} statements"], [], set(), [], [], [])
    root = statements[0]
    for node in root.walk():
        if type(node) not in PREDICATE:
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name().lower() if isinstance(node, exp.Func) else node.key
            return Condition([f"is a read-only condition on columns: {name} is not a column, a value, an operator, CASE, IF, CAST, "
                              "COALESCE, NULLIF or a date function, which are all a condition may use"], [], set(), [], [], [])
    problems, columns, used, seen = [], [], set(), {}
    reads = ", ".join(sorted(tables))
    for c in root.find_all(exp.Column):
        if c.catalog or not c.table:
            problems.append(f"{c.sql()} names no table: a column is table.column, or source.table.column")
            continue
        if c.db:
            table, name = f"{c.db}.{c.table}", f"{c.db}.{c.table}"
            if table not in tables and not (within == c.db and locate(lock, table, 2) is not None):
                problems.append(f"{c.sql()} is of {table}, not a table this reads ({reads})")
                continue
        elif c.table in names:
            table, name = names[c.table], c.table
        elif within and locate(lock, f"{within}.{c.table}", 2) is not None:
            table, name = f"{within}.{c.table}", c.table
        else:
            problems.append(f"{c.sql()} is of {c.table}, not a table this reads ({reads})")
            continue
        col = f"{table}.{c.name}"
        if locate(lock, col) is None:
            problems.append(f"{c.sql()} is not a column of {table} in pack.lock")
            continue
        seen[id(c)] = (name, col)
        columns.append(col)
        used.add(table)
    atoms = conjuncts(root)
    eqs = [a for a in map(exp.Expression.unnest, atoms) if isinstance(a, exp.EQ)]
    equal = [(seen[id(a.left)], seen[id(a.right)]) for a in eqs
             if isinstance(a.left, exp.Column) and isinstance(a.right, exp.Column) and id(a.left) in seen and id(a.right) in seen]

    def names(side: exp.Expression) -> frozenset:
        return frozenset(seen[id(c)][0] for c in side.find_all(exp.Column) if id(c) in seen)
    links = [(names(a.left), names(a.right)) for a in eqs]
    return Condition(problems, columns, used, [a.sql(dialect="duckdb") for a in atoms], equal, links)


def conjuncts(e: exp.Expression) -> list[exp.Expression]:
    """The conditions e is the AND of, however it is bracketed."""
    inner = e.unnest()
    return conjuncts(inner.left) + conjuncts(inner.right) if isinstance(inner, exp.And) else [e]


def table_of(ref: str) -> str:
    """source.table of a column's id (source.table.column)."""
    return ref.rsplit(".", 1)[0]


def restricted_columns(pack: Pack) -> set[str]:
    return {c for r in (pack.data.get("policies") or {}).get("restricted_attributes", []) for c in r["columns"]}


def join_condition(j: dict, lock: dict) -> tuple[Condition, dict[str, str]]:
    """A declared join's condition read against its tables, and which side each name it reads them by is: from, to or
    through. A name two tables of two sources share names neither: each is read source.table.column, and a table joined
    to itself is its from side."""
    sides = {"from": j["from"], "to": j["to"], **({"through": j["through"]} if j.get("through") else {})}
    names: dict[str, set[str]] = {}
    side_of: dict[str, str] = {}
    for side, table in sides.items():
        name = j["alias"] if side == "to" and j.get("alias") else table.split(".", 1)[1]
        names.setdefault(name, set()).add(table)
        side_of[name] = side
        side_of.setdefault(table, side)
    unique = {name: next(iter(tables)) for name, tables in names.items() if len(tables) == 1}
    return condition(j["condition"], lock, unique, set(sides.values())), side_of


def joins(pack: Pack) -> list[Finding]:
    """S23."""
    d, lock, out = pack.data, pack.lock, []
    curated = {c["id"] for c in d.get("curated_sources", [])}
    relations = {r["id"]: r for r in d.get("relations", [])}
    entities = {e["id"]: e for e in d.get("entities", [])}
    restricted = restricted_columns(pack)
    for i, j in enumerate(d.get("joins", [])):
        at = f"joins[{i}]"
        missing = [end for end in ("from", "to", "through") if end in j and locate(lock, j[end], 2) is None]
        out += [Finding("S23", pack.at(f"{at}.{end}"), f"{j[end]} is not a table in pack.lock (source.table)") for end in missing]
        if missing:
            continue
        a, b = j["from"].split(".")[0], j["to"].split(".")[0]
        if j["kind"] == "cross_reference":
            if "through" not in j:
                out.append(Finding("S23", pack.at(f"{at}.through"), "is required: a cross-reference joins two sources through a "
                                   "curated source's table"))
                continue
            if j["through"].split(".")[0] not in curated:
                out.append(Finding("S23", pack.at(f"{at}.through"), f"{j['through']} is not a curated source's table: a "
                                   "cross-reference joins through the curated layer, not through either source"))
            if a == b:
                out.append(Finding("S23", pack.at(f"{at}.kind"), f"joins two tables of {a}: a cross-reference joins two sources"))
        else:
            if "through" in j:
                out.append(Finding("S23", pack.at(f"{at}.through"), "is a cross-reference's: no other join goes through a table"))
            if a != b:
                out.append(Finding("S23", pack.at(f"{at}.kind"), f"joins {a} to {b}: a join between two sources is a certified "
                                   "cross-reference (kind cross_reference) through a curated source's table"))
        if j["from"] == j["to"] and not j.get("alias"):
            out.append(Finding("S23", pack.at(f"{at}.alias"), f"is required: the join takes {j['from']} to itself, so its to side "
                               "goes by an alias in the condition"))
            continue
        if j.get("alias") and (not NAME.fullmatch(j["alias"]) or j["alias"] in {t.split(".", 1)[1] for t in (j["from"], j.get("through"))
                                                                                  if t}):
            out.append(Finding("S23", pack.at(f"{at}.alias"), f"{j['alias']!r} is not a name of its own for the to side"))
            continue
        cond, side_of = join_condition(j, lock)
        out += [Finding("S23", pack.at(f"{at}.condition"), p) for p in cond.problems]
        if cond.problems:
            continue
        out += [Finding("S23", pack.at(f"{at}.condition"), f"names {c}, a restricted column: never a join or match key")
                for c in dict.fromkeys(cond.columns) if c in restricted]
        links = {frozenset((side_of[next(iter(x))], side_of[next(iter(y))])) for x, y in cond.links if len(x) == 1 and len(y) == 1}
        need = [("from", "through"), ("through", "to")] if j.get("through") else [("from", "to")]
        if not all(frozenset(n) in links for n in need):
            out.append(Finding("S23", pack.at(f"{at}.condition"), f"equates no column of {j['from']} with one of {j['to']}"
                               + (f" through {j['through']}" if j.get("through") else "") + ": a join's condition equates their keys"))
        for (_, x), (_, y) in cond.equal:
            tx, ty = locate(lock, x)["type"], locate(lock, y)["type"]
            if family(tx) != family(ty):
                out.append(Finding("S23", pack.at(f"{at}.condition"), f"equates {x} ({tx}) with {y} ({ty}), types that do not join"))
        if "implements" in j:
            r = relations.get(j["implements"])
            if r is None:
                out.append(Finding("S23", pack.at(f"{at}.implements"), f"{j['implements']} is not a declared relation"))
            elif r["from"] in entities and r["to"] in entities and (entities[r["from"]]["table"], entities[r["to"]]["table"]) != (j["from"], j["to"]):
                out.append(Finding("S23", pack.at(f"{at}.implements"), f"{r['id']} relates {entities[r['from']]['table']} to "
                                   f"{entities[r['to']]['table']}, not the join's tables, {j['from']} and {j['to']}"))
    return out


def covered(pack: Pack) -> set[tuple[str, str]]:
    """The column pairs the declared joins equate between two tables (each way), a cross-reference's aside: what a
    harvested foreign key needs, every pair of it, to be one a declared join covers."""
    out = set()
    for j in pack.data.get("joins", []):
        if j.get("through") or any(locate(pack.lock, j[end], 2) is None for end in ("from", "to")):
            continue
        cond, _ = join_condition(j, pack.lock)
        out |= {p for (_, x), (_, y) in cond.equal for p in ((x, y), (y, x))}
    return out


def harvested_key(s: str, t: dict, k: dict) -> str:
    return f"{s}.{t['name']} ({', '.join(k['columns'])}) -> {s}.{k['ref_table']} ({', '.join(k['ref_columns'])})"


def candidates(pack: Pack) -> list[str]:
    """The foreign keys the lock harvested that no declared join covers: candidates for one. Harvest proposes; the pack
    declares."""
    cover = covered(pack)
    return [harvested_key(s, t, k) for s, held in sorted(pack.lock["sources"].items()) for t in held["tables"] for k in t["foreign_keys"]
            if not all((f"{s}.{t['name']}.{a}", f"{s}.{k['ref_table']}.{b}") in cover for a, b in zip(k["columns"], k["ref_columns"]))]


def conditions(pack: Pack) -> list[Finding]:
    """S24."""
    d, lock, out = pack.data, pack.lock, []
    restricted = restricted_columns(pack)
    conflicts = {x["id"]: x.get("conflicts_with", []) for section in ("filters", "row_selectors") for x in d.get(section, [])}
    for section, what in (("filters", "filter"), ("row_selectors", "row selector")):
        for i, x in enumerate(d.get(section, [])):
            at = f"{section}[{i}]"
            if locate(lock, x["table"], 2) is None:
                out.append(Finding("S24", pack.at(f"{at}.table"), f"{x['table']} is not a table in pack.lock (source.table)"))
            else:
                source, name = x["table"].split(".", 1)
                cond = condition(x["predicate"], lock, {name: x["table"]}, {x["table"]}, None if section == "filters" else source)
                problems = [p + (": a filter is a condition on its own table's rows" if " not a table this reads" in p else "")
                            for p in cond.problems]
                if not problems and x["table"] not in cond.tables:
                    problems.append(f"names no column of its table, {x['table']}: a {what} is a condition on its rows")
                problems += [f"names {c}, a restricted column: refused in every arm" for c in dict.fromkeys(cond.columns) if c in restricted]
                out += [Finding("S24", pack.at(f"{at}.predicate"), p) for p in problems]
            for k, other in enumerate(x.get("conflicts_with", [])):
                where = pack.at(f"{at}.conflicts_with[{k}]")
                if other not in conflicts:
                    out.append(Finding("S24", where, f"{other} is not a declared filter or row selector"))
                elif other == x["id"]:
                    out.append(Finding("S24", where, f"is {x['id']} itself: a condition does not conflict with itself"))
                elif x["id"] not in conflicts[other]:
                    out.append(Finding("S24", where, f"{other} does not say it conflicts with {x['id']}: a conflict is declared both ways"))
    return out


def contexts(pack: Pack) -> list[Finding]:
    """S25. A context reaches another source's table only across a cross-reference into that source."""
    d, lock, out = pack.data, pack.lock, []

    terms = {g["id"] for g in d["glossary"]}
    measures = {m["id"]: m for m in d["measures"]}
    pools = {"filters": {x["id"]: x for x in d.get("filters", [])}, "row_selectors": {x["id"]: x for x in d.get("row_selectors", [])},
             "joins": {x["id"]: x for x in d.get("joins", [])}}
    sets = {s["name"]: s for s in d["tests"]["sets"]}
    for i, c in enumerate(d.get("contexts", [])):
        at = f"contexts[{i}]"
        if c["term"] not in terms:
            out.append(Finding("S25", pack.at(f"{at}.term"), f"{c['term']} is not a term the glossary declares"))
        fact = locate(lock, c["fact"], 2) is not None
        if not fact:
            out.append(Finding("S25", pack.at(f"{at}.fact"), f"{c['fact']} is not a table in pack.lock (source.table)"))
        m = measures.get(c["measure"])
        if m is None:
            out.append(Finding("S25", pack.at(f"{at}.measure"), f"{c['measure']} is not a declared measure"))
        elif fact and table_of(m["column"]) != c["fact"]:
            out.append(Finding("S25", pack.at(f"{at}.measure"), f"is on {table_of(m['column'])}, not on the context's fact table, "
                               f"{c['fact']}"))
        for key, what in (("filters", "filter"), ("row_selectors", "row selector"), ("joins", "join"), ("reaches", "join")):
            out += [Finding("S25", pack.at(f"{at}.{key}[{k}]"), f"{x} is not a declared {what}")
                    for k, x in enumerate(c.get(key, [])) if x not in pools["joins" if key == "reaches" else key]]
        reached, always = {c["fact"]}, {c["fact"]}
        for key in ("joins", "reaches"):
            for k, jid in enumerate(c.get(key, [])):
                j = pools["joins"].get(jid)
                if j is None:
                    continue
                ends = {j["from"], j["to"]}
                if not ends & reached:
                    out.append(Finding("S25", pack.at(f"{at}.{key}[{k}]"), f"joins {j['from']} to {j['to']}, and the context reaches "
                                       f"neither before it: each join follows from its fact, {c['fact']}, or a join before it"))
                    continue
                ends |= {j["through"]} if j.get("through") else set()
                reached |= ends
                if key == "joins":
                    always |= ends
        for k, fid in enumerate(c.get("filters", [])):
            f = pools["filters"].get(fid)
            if f is not None and f["table"] not in always:
                out.append(Finding("S25", pack.at(f"{at}.filters[{k}]"), f"{fid} is on {f['table']}, which the context does not reach: "
                                   f"a filter is on its fact or on a table a join it always renders reaches ({', '.join(sorted(always))})"))
        far: dict[str, set[str]] = {}
        for jid in c.get("reaches", []):
            j = pools["joins"].get(jid)
            if j is not None and j.get("through"):
                for end in (j["from"], j["to"]):
                    if end.split(".", 1)[0] != c["fact"].split(".", 1)[0]:
                        far.setdefault(end.split(".", 1)[0], set()).add(end)
        for k, sid in enumerate(c.get("row_selectors", [])):
            s = pools["row_selectors"].get(sid)
            if s is None or locate(lock, s["table"], 2) is None:
                continue
            source, name = s["table"].split(".", 1)
            matched = condition(s["predicate"], lock, {name: s["table"]}, {s["table"]}, source).tables - {s["table"]}
            if source != c["fact"].split(".", 1)[0]:
                if source not in far:
                    out.append(Finding("S25", pack.at(f"{at}.row_selectors[{k}]"), f"{sid} is on {s['table']}, of {source}, which the "
                                       "context reaches through no cross-reference: a row selector on another source applies only "
                                       "across a certified cross-reference the context reaches"))
                elif matched - far[source]:
                    out.append(Finding("S25", pack.at(f"{at}.row_selectors[{k}]"), f"{sid} matches rows of "
                                       f"{', '.join(sorted(matched - far[source]))}, which no cross-reference the context reaches "
                                       f"ends at: across, a row selector matches the far table ({', '.join(sorted(far[source]))})"))
                continue
            beyond = matched - always
            if beyond:
                out.append(Finding("S25", pack.at(f"{at}.row_selectors[{k}]"), f"{sid} matches rows of {', '.join(sorted(beyond))}, "
                                   "which the context does not reach: its fact, or a table a join it always renders reaches"))
        g = c.get("golden")
        if g:
            named = sets.get(g["set"])
            if named is None:
                out.append(Finding("S25", pack.at(f"{at}.golden.set"), f"{g['set']} is not a question set the pack names (tests.sets)"))
            elif "items" not in named:
                out.append(Finding("S25", pack.at(f"{at}.golden.set"), f"{g['set']} names no items in tests.sets: a context's golden "
                                   "questions are items a named set lists, by id"))
            else:
                out += [Finding("S25", pack.at(f"{at}.golden.items[{k}]"), f"{item} is not an item of {g['set']} {named['version']}")
                        for k, item in enumerate(g["items"]) if item not in named["items"]]
    return out


def references(pack: Pack) -> list[Finding]:
    """S26."""
    d, lock, out = pack.data, pack.lock, []
    terms = {g["id"] for g in d["glossary"]}
    entities = {e["id"] for e in d.get("entities", [])}
    no_join = {frozenset(f["between"]) for f in d.get("no_join") or []}

    def column(ref: str, at: str) -> None:
        if locate(lock, ref) is None:
            out.append(Finding("S26", pack.at(at), f"{ref} is not a column in pack.lock (source.table.column)"))

    def table(ref: str, at: str) -> bool:
        if locate(lock, ref, 2) is None:
            out.append(Finding("S26", pack.at(at), f"{ref} is not a table in pack.lock (source.table)"))
            return False
        return True

    def declared(ids: list[str], pool: set[str], what: str, at: str) -> None:
        out.extend(Finding("S26", pack.at(f"{at}[{k}]"), f"{x} is not a declared {what}") for k, x in enumerate(ids) if x not in pool)
    for i, a in enumerate(d.get("acronyms", [])):
        at = f"acronyms[{i}]"
        if not a.get("terms") and not a.get("columns"):
            out.append(Finding("S26", pack.at(at), "names the terms it abbreviates or the columns whose names it spells"))
        declared(a.get("terms", []), terms, "term", f"{at}.terms")
        for k, ref in enumerate(a.get("columns", [])):
            column(ref, f"{at}.columns[{k}]")
    for i, e in enumerate(d.get("entities", [])):
        at = f"entities[{i}]"
        if not table(e["table"], f"{at}.table"):
            continue
        names = {c["name"] for c in locate(lock, e["table"], 2)["columns"]}
        if not e["key"]:
            out.append(Finding("S26", pack.at(f"{at}.key"), "names the columns of the entity's key"))
        out += [Finding("S26", pack.at(f"{at}.key[{k}]"), f"{x} is not a column of {e['table']} in pack.lock")
                for k, x in enumerate(e["key"]) if x not in names]
    for i, r in enumerate(d.get("relations", [])):
        out += [Finding("S26", pack.at(f"relations[{i}].{end}"), f"{r[end]} is not a declared entity") for end in ("from", "to")
                if r[end] not in entities]
    for i, x in enumerate(d.get("lineage", [])):
        at = f"lineage[{i}]"
        table(x["table"], f"{at}.table")
        for k, ref in enumerate(x["derived_from"]):
            if table(ref, f"{at}.derived_from[{k}]") and ref == x["table"]:
                out.append(Finding("S26", pack.at(f"{at}.derived_from[{k}]"), "is the table itself: a table derives from others"))
    for i, x in enumerate(d.get("not_joinable", [])):
        at = f"not_joinable[{i}]"
        if locate(lock, x["column"]) is None:
            column(x["column"], f"{at}.column")
            continue
        if not table(x["table"], f"{at}.table"):
            continue
        a, b = x["column"].split(".")[0], x["table"].split(".")[0]
        if a == b:
            out.append(Finding("S26", pack.at(f"{at}.table"), f"is of {a}, the column's own source: a column not joinable refines "
                               "the no-join fact between two sources"))
        elif frozenset((a, b)) not in no_join:
            out.append(Finding("S26", pack.at(f"{at}.table"), f"{a} and {b} are not a no-join pair: a column not joinable refines "
                               "one"))
    for i, o in enumerate(d.get("ontology", [])):
        declared(o.get("entities", []), entities, "entity", f"ontology[{i}].entities")
        declared(o.get("terms", []), terms, "term", f"ontology[{i}].terms")
    return out


MAPPED_SUBJECTS = ("term", "entity", "policy")
ONTOLOGY_MAPPINGS_SINCE = (1, 3, 0)


def ontology_mappings(pack: Pack, preload: preloaded.Preload | None = None) -> list[Finding]:
    """S30."""
    d, out = pack.data, []
    preload = preload or preloaded.load()
    for i, o in enumerate(d.get("ontology", [])):
        if o["id"] in preload.concepts:
            out.append(Finding("S30", pack.at(f"ontology[{i}].id"), f"{o['id']!r} is a preloaded concept's id: a concept the "
                               "pack declares takes an id of its own"))
    entries = d.get("ontology_mappings") or []
    if not entries:
        return out
    written = semver(d["manifest"].get("meta_model_version"))
    if written and written < ONTOLOGY_MAPPINGS_SINCE:
        out.append(Finding("S30", pack.at("ontology_mappings"), f"needs meta-model 1.3.0 or later, and the pack is written for "
                           f"{d['manifest']['meta_model_version']}: declare manifest.meta_model_version 1.3.0"))
    declared = {"term": {g["id"] for g in d["glossary"]}, "entity": {e["id"] for e in d.get("entities", [])},
                "policy": {r["id"] for r in (d.get("policies") or {}).get("restricted_attributes", [])}}
    what = {"term": "a declared term (glossary id)", "entity": "a declared entity",
            "policy": "a declared restricted-data policy (policies.restricted_attributes)"}
    seen: dict[tuple, int] = {}
    for i, m in enumerate(entries):
        at = f"ontology_mappings[{i}]"
        named = [k for k in MAPPED_SUBJECTS if k in m]
        if len(named) != 1:
            out.append(Finding("S30", pack.at(at), f"maps one of {', '.join(MAPPED_SUBJECTS)}"))
            continue
        kind = named[0]
        if m[kind] not in declared[kind]:
            out.append(Finding("S30", pack.at(f"{at}.{kind}"), f"{m[kind]} is not {what[kind]}"))
        if m["iri"] not in preload.by_iri:
            out.append(Finding("S30", pack.at(f"{at}.iri"), f"{m['iri']} is not a concept the platform preloads "
                               f"(standards/preload.yaml, version {preload.version}): onboarding maps only to preloaded "
                               "concepts, and widening them is a platform update"))
        key = (kind, m[kind], m["iri"])
        if key in seen:
            out.append(Finding("S30", pack.at(at), f"maps {m[kind]} to {m['iri']} again: ontology_mappings[{seen[key]}] does"))
        seen.setdefault(key, i)
    return out


def certified(pack: Pack) -> list[Finding]:
    """S27."""
    d, out = pack.data, []
    sources = [s["id"] for s in d["sources"]]
    no_join = {frozenset(f["between"]): i for i, f in enumerate(d.get("no_join") or []) if len(f["between"]) == 2}
    joins_by_id = {j["id"]: j for j in d.get("joins", [])}
    seen: dict[frozenset, int] = {}
    for i, f in enumerate(d.get("certified_joins", [])):
        at = f"certified_joins[{i}]"
        pair = f["between"]
        if len(pair) != 2 or pair[0] == pair[1]:
            out.append(Finding("S27", pack.at(f"{at}.between"), "names two distinct sources"))
            continue
        unknown = [s for s in pair if s not in sources]
        if unknown:
            out.append(Finding("S27", pack.at(f"{at}.between"), f"names {', '.join(unknown)}, not a declared source"))
        key = frozenset(pair)
        if key in no_join:
            out.append(Finding("S27", pack.at(f"{at}.between"), f"{pair[0]}-{pair[1]} is no_join[{no_join[key]}]'s pair too: two "
                               "sources join through a certified join, or do not join"))
        if key in seen:
            out.append(Finding("S27", pack.at(f"{at}.between"), f"asserts {pair[0]}-{pair[1]} again: certified_joins[{seen[key]}] does"))
        seen.setdefault(key, i)
        j = joins_by_id.get(f["join"])
        if j is None or j["kind"] != "cross_reference":
            out.append(Finding("S27", pack.at(f"{at}.join"), f"{f['join']} is not a declared cross-reference (a join of kind "
                               "cross_reference)"))
        elif {j["from"].split(".")[0], j["to"].split(".")[0]} != set(pair):
            out.append(Finding("S27", pack.at(f"{at}.join"), f"joins {j['from']} to {j['to']}, not {pair[0]} to {pair[1]}"))
    for i, j in enumerate(d.get("joins", [])):
        a, b = j["from"].split(".")[0], j["to"].split(".")[0]
        if j["kind"] == "cross_reference" and a != b and frozenset((a, b)) not in seen:
            out.append(Finding("S27", pack.at(f"joins[{i}]"), f"joins {a} to {b}, a pair no certified join names: a cross-reference "
                               "is used only through a certified join fact (certified_joins)"))
    return out


TIME_POLICIES = {"scd2": ("valid_from", "valid_to", "is_current"), "release_driver": ("period", "release_date", "freeze_date", "released")}


def time_policies(pack: Pack) -> list[Finding]:
    """S28."""
    lock, out = pack.lock, []
    for kind, fields in TIME_POLICIES.items():
        seen: dict[str, int] = {}
        for i, x in enumerate((pack.data.get("policies") or {}).get(kind, [])):
            at = f"policies.{kind}[{i}]"
            table = locate(lock, x["table"], 2)
            if table is None:
                out.append(Finding("S28", pack.at(f"{at}.table"), f"{x['table']} is not a table in pack.lock (source.table)"))
                continue
            if x["table"] in seen:
                out.append(Finding("S28", pack.at(f"{at}.table"), f"has a {kind} policy already, policies.{kind}[{seen[x['table']]}]: "
                                   "one a table"))
            seen.setdefault(x["table"], i)
            names = {c["name"] for c in table["columns"]}
            out += [Finding("S28", pack.at(f"{at}.{f}"), f"{x[f]} is not a column of {x['table']} in pack.lock") for f in fields
                    if x[f] not in names]
    return out


LOCKED = "lock"


def part(f: Finding) -> str:
    """The part of the pack a finding is in: its section's name, or the lock."""
    file, _, location = f.path.partition(":")
    return LOCKED if file == LOCK else re.match(r"[^.\[]*", location).group(0)


def check(pack: Pack, meta: dict, compiler_version: str, fills: set[str]) -> list[Finding]:
    """Every finding, in rule order. A check runs on what holds its shape: an S01 finding in a section, or in the
    lock, stops each check that reads that section or the lock, and no other, so a stray field in the manifest hides
    no mapping's finding. A lock that cannot be read stops the checks that read the lock; pack.yaml or a file it
    includes that cannot be read stops every check but S00, since it may hold any section. S00 stops nothing: the
    later checks go by the folder's name, and S03 leaves the version S00 refused to S00."""
    if any(f.path.partition(":")[0] != LOCK for f in pack.findings):
        return (identity(pack) if pack.data else []) + pack.findings
    found = identity(pack)
    said = {f.path for f in found}
    shapes = sections(pack)
    shapes += lock_shape(pack, sources={"sources", "curated_sources"}.isdisjoint({part(f) for f in shapes})) + pack.findings
    found += [f for f in shapes if f.path not in said]
    broken = {part(f) for f in shapes}
    later = (
        ((), lambda: ids(pack, [s for s in ID_SECTIONS if s not in broken])),
        (("manifest", "schema"), lambda: compatibility(pack, meta, compiler_version)),
        ((), lambda: fillable(meta, compiler_version, fills)),
        (("schema",), lambda: extensions(pack, meta)),
        ((LOCKED,), lambda: foreign_keys(pack, covered(pack) if "joins" not in broken else frozenset())),
        (("sources", "no_join"), lambda: no_join(pack)),
        (("glossary", "mappings", LOCKED), lambda: mappings(pack)),
        (("glossary", "measures", LOCKED), lambda: measures(pack)),
        (("calendars", "period_bindings", "measures", LOCKED), lambda: calendars(pack)),
        (("measures",), lambda: currencies(pack)),
        (("glossary", "sources", "terminology", "entities", LOCKED), lambda: aliases(pack)),
        (("terminology", "policies", LOCKED), lambda: vocabulary(pack)),
        (("sources", "access"), lambda: roles(pack, meta)),
        (("policies", LOCKED), lambda: restricted(pack)),
        (("joins", "relations", "entities", "curated_sources", "policies", LOCKED), lambda: joins(pack)),
        (("filters", "row_selectors", "policies", LOCKED), lambda: conditions(pack)),
        (("contexts", "glossary", "measures", "filters", "row_selectors", "joins", "tests", LOCKED), lambda: contexts(pack)),
        (("acronyms", "entities", "relations", "lineage", "not_joinable", "ontology", "glossary", "no_join", LOCKED),
         lambda: references(pack)),
        (("certified_joins", "sources", "no_join", "joins"), lambda: certified(pack)),
        (("ontology_mappings", "ontology", "glossary", "entities", "policies", "manifest"), lambda: ontology_mappings(pack)),
        (("policies", LOCKED), lambda: time_policies(pack)),
    )
    for reads, run in later:
        if broken.isdisjoint(reads):
            found += run()
    return found
