"""The structural checks a pack passes before it compiles (../../docs/use-cases/kge/platform/pack-contract.md, 5.1), against the platform's
meta-model and the pack's own lock.

  S00  the manifest's client (present, a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most,
       the client folder's name) and its meta_model_version
       (present, semver): the meta-model version the pack was written for
  S01  each section's shape, from the field tables below, and the lock's, which is the pack's own and holds the
       catalogue of each source the pack harvests; no value is the literal \\N, the artifact's NULL; the manifest's
       gates, the thresholds the post-load check holds a loaded candidate to (kge/eval/margin.py), a share from 0 to 1
       for each kind of question the golden scorer knows; since meta-model 1.6 a term may carry is_a and broader (term
       ids, each once) and codes ({column, code}, each pair once; several codes on one column are alternatives),
       refused on an earlier pack
  S02  ids: unique per kind and slugs; a term's name unique too, since the graph keys a term by it
  S03  the meta-model and this compiler satisfy the ranges the manifest's platform names; the meta-model is of the
       pack's major version, at the pack's minor or later; and this compiler fills every type the meta-model gives the
       pack or the lock to fill
  S04  extensions only add, and are namespaced x_<client>_ (a hyphen in the client id is an underscore there)
  S05  each foreign key in the lock references a table of its own source, column for column, with compatible types; a
       composite key is reported unless a declared join covers it, since a JOINS edge holds one column pair
  S06  no_join: two declared, distinct sources; each pair once
  S07  mappings: a column in the lock and a declared term; a certified one says by whom and when
  S08  measures: a declared term, a column in the lock with consistent measure metadata, and one read-only aggregate over the
       rows of that column's table, as sqlglot parses it, which agrees with the aggregation and names the measure's
       column; it is built of aggregates, operators, FILTER, CASE, IF, CAST, COALESCE and NULLIF alone, and of that
       table's columns, so it reads no file and no other table
  S09  calendars and period bindings: a year that starts in a month, bindings to columns in the lock, one a table, on
       declared calendars, rolling up within their source; each measure's calendar declared and its table bound
  S10  currency codes from ISO 4217; a conversion from another currency
  S11  aliases: each names a declared term, a declared source, or a candidate the glossary does not hold, or (since
       meta-model 1.1) a column in the lock to group by, a declared entity, a time mode or a period the platform knows,
       or a column in the lock it is the business name of, or (since 1.4) a non-empty set of distinct declared
       sources; one target or source set a phrase, whatever its case
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
  S28  time policies: an scd2 policy's, a release driver's and (since meta-model 1.4) a partitioned policy's table and
       columns are in the lock, one of each kind a table; a partitioned column is a date or timestamp
  S29  the rest of the parser's vocabulary (since meta-model 1.2): a value vocabulary's and a predicate's column is in
       the lock and no restricted column; a value vocabulary gives values or a pattern the platform knows, not both,
       and a prefix only with a pattern; a comparison predicate gives one text or numeric value, and a unary predicate
       gives none; an in or not_in predicate names two or more values, all text or all numbers, each once, and
       no other operator takes values; each intent once; each functional mailbox a mailbox's local part, once; part only
       since 1.6
  S30  ontology mappings (since meta-model 1.3): each maps one declared term, entity or restricted-data policy to a
       concept the platform preloads (standards/preload.yaml), by its IRI, with a SKOS relation, certified by a
       steward on a date; a subject maps to a concept once; and a concept the pack declares itself (ontology) takes
       no preloaded concept's id. A mapping to an IRI the preload does not hold is refused: widening the preload is a
       platform update, never a pack's
  S31  runtimes and default_runtimes (since meta-model 1.5): each runtime maps reference tables and columns in the lock
       to valid table and column names and types, or marks reference tables absent, without naming the internal
       reference runtime; default_runtimes is a non-empty ordered list of unique declared runtime ids (or reference)
  S32  phrases (since meta-model 1.6): each lower case, with no leading or trailing space, and given once, marking an
       operation the platform lists or filler; not a preloaded phrase, nor an alias, an acronym or a grouping phrase of
       the pack
  S33  shapes and shape tests (since 1.6): slot kinds, markers and position constraints from the platform's lists; no
       name, nor every name of a one_of, both required and forbidden; a declared recipe whose slot references name
       declared slots of a kind that fits, within their counts, every slot used but one counted 0; replaces names a
       builder the preload lists, which no other shape replaces, and a shape replacing a builder no lexer marker
       reproduces (standards.HELD_BUILDERS) is never retired; an id of its own; for every shape declared or used,
       test items of a set the pack names, each cited once per shape, and a worked example; shapes that can match one
       question contribute disjoint plan nodes or are separated by a forbids, slot counts, a clarification rule or
       before constraints in reverse order on operands both require; the coupled groups listed
  S34  recipes (since 1.6): blocks from the platform's catalogue with unique, unreserved ids and parameters of the types
       each declares, acyclic references, expanding into a valid query-plan/v1; an id of its own; schema only: what a
       plan means is checked after load (V06)
  S35  worked examples (since 1.6): a declared shape; a valid query-plan/v1 whose names the pack declares; approved_by
       and approved_on above specify (a draft at specify counts towards no metric)
  S36  clarification rules (since 1.6): conditions on the platform's lists; a clarification text the platform lists; an
       id unique across the preload and the pack
  S37  business terms' structure (since 1.6): each is_a and broader names another declared term and each code a column
       in the lock; is_a and broader form no cycle; within one classification (a broader term's is_a class, or none) a
       term has one broader term; no code is on a restricted column; a member's coded column maps to the term's class or
       a broader ancestor's; a term and its broader term coded on different tables have a declared hierarchy or key join
       between them; no member's name is a value vocabulary's value or a predicate's phrase; and no code contradicts a
       code/name pair or another term

A check runs on what holds its shape: an S01 finding stops the checks that read its section, or the lock, and no
other, so a stray field in the manifest hides no mapping's finding.

Not checked here: S12 (documents land at load, and are checked there, fail closed), S13 and S15 (the pack names its
question sets, not their questions) and S16 to S21 (tiers and readiness, the plan's Phase 4).

Beside the findings, the foreign keys the lock harvested that no declared join covers are join candidates
(candidates): harvest proposes, and the pack declares; and the shapes, the preload's and the pack's, fall into coupled
groups (coupled), the shapes some question can match together that no forbids, slot counts, clarification rule or
reversed before constraints separate.

Names no client: the client is the pack's own.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from decimal import Decimal
from typing import NamedTuple

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from kge.pipeline.access import process_vocabulary
from kge.compiler import plans
from kge.compiler import standards as preloaded
from kge.compiler import terms
from kge.compiler.terms import FIELDS as TERM_FIELDS, ROW_JOINS, TERM_LINKS
from kge.compiler.inputs import declared_findings
from kge.compiler.pack import LOCK, LOCK_FORMAT, Finding, Pack
from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE
from kge.compiler.vocabulary import AGGREGATION_CHOICES, ALIAS_KINDS, ARITHMETIC, INTENTS, JOIN_CARDINALITIES, JOIN_TYPES, MAILBOX, NEEDS_VALUE, NEEDS_VALUES, OPS, PATTERNS, alias_declarations
from kge.pipeline.sqlguard import canonical as canonical_predicate
from kge.pipeline.textmatch import TEXT_MATCHES, text_value_problem
from kge.ports import REFERENCE
from kge.compiler.bands import band_problems

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
TIME_MODES = ("as_reported", "restated")
PERIODS = ("latest",)
AGGREGATIONS = ("sum", "count", "count_distinct", "avg", "ratio", "last_snapshot", "min", "max", "median")
GRAINS = ("day", "week", "month", "quarter", "quarter_end", "year", "as_of")
TIME_GRAINS = ("day", "week", "month", "calendar_quarter", "fiscal_quarter", "year", "fiscal_year")
SECTIONS: dict[str, object] = {
    "manifest": {"client": opt(ANY), "meta_model_version": opt(ANY), "id": F(STR), "version": F(STR), "title": F(STR),
                 "owners": opt([STR]), "platform": F({"meta_model": F(STR), "compiler": F(STR)}),
                 "compile": F({"store": F(("kuzu",)), "embedding_dim": F(INT), "id_scheme": F(("v1",))}),
                 "includes": opt([STR]), "inputs": opt(ANY),
                 "gates": opt({"golden_min_pass": opt(SHARES), "control_margin": opt(SHARES)})},
    "schema": {"meta_model": F(STR), "extensions": opt([ANY])},
    "sources": [{"id": F(STR), "label": F(STR), "entity_key": F(STR), "connection": F({"dialect": F(STR), "ref": F(STR)}),
                 "harvest": opt(BOOL)}],
    "curated_sources": [{"id": F(STR), "label": F(STR), "connection": F({"dialect": F(STR), "ref": F(STR)}), "harvest": opt(BOOL)}],
    "no_join": [{"between": F([STR]), "asserted_by": F(STR), "asserted_on": F(DATE), "note": F(STR)}],
    "certified_joins": [{"between": F([STR]), "join": F(STR), "asserted_by": F(STR), "asserted_on": F(DATE), "note": F(STR)}],
    "not_joinable": [{"column": F(STR), "table": F(STR), "asserted_by": F(STR), "asserted_on": F(DATE), "note": F(STR)}],
    "glossary": [{"id": F(STR), "name": F(STR), "domain": F(STR), "steward": F(STR), "definition": F(STR),
                  "status": F(("certified", "candidate")), "defined_by": opt([STR]), "version": opt(STR),
                  "effective_from": opt(DATE), "is_a": opt([STR]), "broader": opt([STR]),
                  "codes": opt([{"column": F(STR), "code": F(STR)}])}],
    "terminology": {"aliases": F([{"phrase": F(STR), **{k: opt([STR] if k == "sources" else STR) for k in ALIAS_KINDS}}]),
                    "concepts": opt([{"id": F(STR), "wordings": F([STR]),
                                      "qualified_terms": opt([{"noun": F(STR), "term": F(STR)}]),
                                      **{k: opt([STR] if k == "sources" else STR) for k in ALIAS_KINDS}}]),
                    "code_name_pairs": opt([{"code": F(STR), "name": F(STR), "members": F([{"code": F(STR), "name": F(STR)}])}]),
                    "value_vocabularies": opt([{"column": F(STR), "values": opt([STR]), "pattern": opt(tuple(PATTERNS)),
                                                "prefix": opt(STR), "after": opt([STR]), "before": opt([STR]), "whole": opt(STR)}]),
                    "predicates": opt([{"label": opt(STR), "modifier": opt(BOOL), "phrases": F([STR]), "with": opt([STR]), "column": F(STR), "op": F(tuple(OPS)),
                                        "value": opt(ANY), "values": opt([ANY]), "part": opt(BOOL)}]),
                    "intents": opt([{"phrases": F([STR]), "intent": F(INTENTS)}]),
                    "functional_mailboxes": opt([STR])},
    "calendars": [{"id": F(STR), "year_start_month": F(INT), "quarter_label": F(STR), "current_period": opt(DATE),
                    "week_start": opt(tuple(day.casefold() for day in calendar.day_name)),
                    "spine": opt({"table": F(STR), "column": F(STR),
                                  "attributes": opt(ANY),
                                  "recurrences": opt([{"attribute": F(STR), "grain": F(TIME_GRAINS), "cycle": F(TIME_GRAINS),
                                                        "values": F([{"value": F(ANY), "phrases": F([STR])}])}])})}],
    "period_bindings": [{"table": F(STR), "column": F(STR), "kind": F(CALENDAR_KINDS), "calendar": F(STR),
                         "rollup": opt({"table": F(STR), "column": F(STR)})}],
    "measures": [{"id": F(STR), "term": F(STR), "column": F(STR), "expression": F(STR), "aggregation": F(AGGREGATIONS),
                  "grain": F(GRAINS), "currency": F(STR), "calendar": F(STR), "conversion": F(STR), "snapshot": opt(BOOL),
                  "population": opt(STR), "certified_in": opt(STR), "aggregations": opt([AGGREGATION_CHOICES]), "moving_average_lengths": opt([INT]),
                  "empty": opt(("zero", "null"))}],
    "derived_measures": [{"id": F(STR), "term": F(STR),
                           "formula": F({"kind": F(ARITHMETIC), "numerator": F(STR), "denominator": F(STR)}),
                           "contexts": opt({"numerator": opt(STR), "denominator": opt(STR)})}],
    "count_metrics": [{"id": F(STR), "term": F(STR), "entity": F(STR), "context": opt(STR),
                       "filters": opt([{"column": F(STR), "op": F((*NEEDS_VALUE, "is_null", "is_not_null")), "value": opt(ANY)}])}],
    "banded_dimensions": [{"id": F(STR), "label": F(STR), "column": F(STR),
                            "ranges": F([{"label": F(STR), "min": F(ANY), "max": F(ANY)}])}],
    "mappings": [{"column": F(STR), "term": F(STR), "certified": F(BOOL), "certified_by": opt(STR), "certified_on": opt(DATE)}],
    "policies": {"restricted_attributes": opt([{"id": F(STR), "action": F(("refuse",)), "cite": F(STR), "columns": F([STR]),
                                                "version": opt(STR), "phrases": opt([{"column": F(STR), "phrases": F([STR])}])}]),
                 "scd2": opt([{"id": F(STR), "table": F(STR), "valid_from": F(STR), "valid_to": F(STR), "is_current": F(STR),
                               "valid_to_inclusive": F(BOOL), "version": opt(STR)}]),
                 "release_driver": opt([{"id": F(STR), "table": F(STR), "period": F(STR), "release_date": F(STR),
                                         "freeze_date": F(STR), "released": F(STR), "version": opt(STR)}]),
                 "partitioned": opt([{"id": F(STR), "table": F(STR), "column": F(STR), "version": opt(STR)}])},
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
               "cardinality": F(JOIN_CARDINALITIES),
               "type": opt(JOIN_TYPES), "validity_window": opt({"date": F(STR), "effective": F(STR), "expiry": F(STR)}),
               "through": opt(STR), "implements": opt(STR)}],
    "contexts": [{"id": F(STR), "label": F(STR), "description": F(STR), "term": F(STR), "measure": F(STR), "fact": F(STR),
                  "filters": opt([STR]), "row_selectors": opt([STR]), "joins": opt([STR]), "reaches": opt([STR]),
                  "ytd_calendar": opt(STR), "bindings": opt(ANY), "version_dates": opt(ANY),
                  "golden": opt({"set": F(STR), "items": F([STR])})}],
    "runtimes": [{"id": F(STR), "label": F(STR),
                  "connection": F({"dialect": F(STR), "location": F(STR), "execution_identity": opt(STR), "billing_project": opt(STR)}),
                  "tables": opt(ANY), "absent": opt([STR])}],
    "default_runtimes": [STR],
    "phrases": [{"phrase": F(STR), "operation": opt(STR), "filler": opt(BOOL)}],
    "shapes": [{"id": F(STR), "label": F(STR), "slots": F([{"name": F(STR), "kind": F(preloaded.SLOT_KINDS), "count": opt(ANY)}]),
                "requires": F([ANY]), "forbids": opt([STR]), "constraints": opt([ANY]), "recipe": F(STR), "replaces": opt(STR),
                "stage": F(preloaded.STAGES)}],
    "recipes": [{"id": F(STR), "blocks": F([ANY])}],
    "clarifications": [{"id": F(STR), "when": F({"requires": F([ANY]), "forbids": opt([STR]), "slots": opt(ANY),
                                                  "constraints": opt([ANY])}),
                        "message": F(STR)}],
    "shape_tests": [{"shape": F(STR), "tests": F({"set": F(STR), "items": F([STR])})}],
    "examples": [{"id": F(STR), "shape": F(STR), "question": F(STR), "plan": F(ANY), "approved_by": opt(STR), "approved_on": opt(DATE)}],
}
REQUIRED = ("manifest", "schema", "sources", "glossary", "terminology", "measures", "mappings", "tests")
FILLS = {"sources": "Source", "glossary": "Term", "measures": "Measure", "calendars": "Calendar",
         "terminology.aliases": "Alias", "policies.restricted_attributes": "Policy", "policies.scd2": "Policy",
         "policies.release_driver": "Policy", "policies.partitioned": "Policy", "curated_sources": "CuratedSource",
         "contexts": "Context", "filters": "Filter", "row_selectors": "RowSelector", "joins": "Join",
         "acronyms": "Acronym", "entities": "Entity", "relations": "Relation", "ontology": "OntologyConcept",
         "runtimes": "Runtime", "phrases": "Phrase", "shapes": "Shape", "recipes": "Recipe", "clarifications": "ClarificationRule",
         "examples": "WorkedExample"}


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
    terminology = data.get("terminology") if isinstance(data.get("terminology"), dict) else {}
    for slot in ("aliases", "concepts"):
        rows = terminology.get(slot)
        for i, a in enumerate(rows if isinstance(rows, list) else []):
            if isinstance(a, dict) and sum(k in a for k in ALIAS_KINDS) != 1:
                out.append(Finding("S01", pack.at(f"terminology.{slot}[{i}]"), f"names one of {', '.join(ALIAS_KINDS)}"))
    out += [Finding("S01", pack.at(where), message) for where, message in question_shape_fields(data)]
    out += [Finding("S01", pack.at(where), message) for where, message in term_fields(data, before_shapes(pack))]
    for where in nulls(data, ""):
        out.append(Finding("S01", pack.at(where), "is the literal \\N, which the artifact writes for NULL: it cannot be a value"))
    for where, message in unplain(data, ""):
        out.append(Finding("S01", pack.at(where), message))
    return out


COUNT_RULE = "is a whole number, or {min, max} with min at most max"
REQUIRES_RULE = "is an operation or marker name, or {one_of: [two or more names]}"


def whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def count_ok(value) -> bool:
    """A slot count: a whole number, or {min, max} of whole numbers with at least one bound and min at most max."""
    if whole(value):
        return True
    if not isinstance(value, dict) or not value or not set(value) <= {"min", "max"} or not all(whole(v) for v in value.values()):
        return False
    return value.get("min", 0) <= value.get("max", value.get("min", 0))


def requires_ok(entry) -> bool:
    """A requires entry: an operation or marker name, or {one_of: [two or more distinct names]}."""
    if isinstance(entry, str):
        return entry.strip() != ""
    if not isinstance(entry, dict) or set(entry) != {"one_of"} or not isinstance(entry["one_of"], list):
        return False
    names = entry["one_of"]
    return len(names) >= 2 and all(isinstance(n, str) and n.strip() for n in names) and len(set(names)) == len(names)


def constraint_ok(entry) -> bool:
    """A position constraint: one of the platform's constraints, over exactly two texts."""
    if not isinstance(entry, dict) or len(entry) != 1:
        return False
    op, operands = next(iter(entry.items()))
    return (op in preloaded.CONSTRAINTS and isinstance(operands, list) and len(operands) == 2
            and all(isinstance(x, str) and x.strip() for x in operands))


def condition_fields(value: dict, at: str):
    """(location, message) for a shape's or a clarification rule's requires and constraints that do not hold their form."""
    requires = value.get("requires")
    for k, entry in enumerate(requires if isinstance(requires, list) else []):
        if not requires_ok(entry):
            yield f"{at}.requires[{k}]", REQUIRES_RULE
    constraints = value.get("constraints")
    for k, entry in enumerate(constraints if isinstance(constraints, list) else []):
        if not constraint_ok(entry):
            yield f"{at}.constraints[{k}]", f"is one of {{{'|'.join(preloaded.CONSTRAINTS)}}}: [a, b]"


def question_shape_fields(data: dict):
    """S01's rules for the question-shape sections that a field table cannot say: a phrase marks an operation or is
    filler, slot counts, requires entries, position constraints, a clarification rule's slots and a recipe's blocks."""
    def rows(section):
        value = data.get(section)
        return [(i, x) for i, x in enumerate(value) if isinstance(x, dict)] if isinstance(value, list) else []
    for i, p in rows("phrases"):
        if ("operation" in p) == ("filler" in p):
            yield f"phrases[{i}]", "names one of operation or filler: true"
        if isinstance(p.get("filler"), bool) and p["filler"] is not True:
            yield f"phrases[{i}].filler", "is true or absent: a phrase marks an operation or is filler"
    for i, s in rows("shapes"):
        slots = s.get("slots")
        for j, slot in enumerate(slots if isinstance(slots, list) else []):
            if isinstance(slot, dict) and "count" in slot and not count_ok(slot["count"]):
                yield f"shapes[{i}].slots[{j}].count", COUNT_RULE
        yield from condition_fields(s, f"shapes[{i}]")
    for i, c in rows("clarifications"):
        when = c.get("when")
        if not isinstance(when, dict):
            continue
        yield from condition_fields(when, f"clarifications[{i}].when")
        if "slots" not in when:
            continue
        if not isinstance(when["slots"], dict):
            yield f"clarifications[{i}].when.slots", f"is a mapping of slot kinds ({', '.join(preloaded.SLOT_KINDS)}) to counts"
            continue
        for kind, count in when["slots"].items():
            if kind not in preloaded.SLOT_KINDS:
                yield f"clarifications[{i}].when.slots.{kind}", f"is not a slot kind: the kinds are {', '.join(preloaded.SLOT_KINDS)}"
            elif not count_ok(count):
                yield f"clarifications[{i}].when.slots.{kind}", COUNT_RULE
    for i, r in rows("recipes"):
        blocks = r.get("blocks")
        for j, b in enumerate(blocks if isinstance(blocks, list) else []):
            if not isinstance(b, dict) or not all(isinstance(b.get(k), str) and b[k].strip() for k in ("block", "id")):
                yield f"recipes[{i}].blocks[{j}]", "is a mapping with a block and an id"


TERM_LISTS = {"is_a": "is a non-empty list of term ids (glossary ids)", "broader": "is a non-empty list of term ids (glossary ids)",
              "codes": "is a non-empty list of {column, code}"}


def term_fields(data: dict, before: bool):
    """S01's rules for a term's is_a, broader and codes (meta-model 1.6.0) that the field table cannot say: none on a
    pack written for an earlier meta-model, no empty list, and no id, or column and code, given twice in one list.
    Whether a reference resolves is S37's."""
    for i, g in rows_of(data, "glossary"):
        for field in TERM_FIELDS:
            if field not in g:
                continue
            value = g[field]
            if before:
                yield f"glossary[{i}].{field}", SHAPES_NEEDED
            if value == []:
                yield f"glossary[{i}].{field}", TERM_LISTS[field]
        for field in TERM_LINKS:
            seen: set[str] = set()
            for j, x in enumerate(g[field] if isinstance(g.get(field), list) else []):
                if isinstance(x, str) and x in seen:
                    yield f"glossary[{i}].{field}[{j}]", f"names {x} once"
                if isinstance(x, str):
                    seen.add(x)
        pairs: set[tuple[str, str]] = set()
        for j, c in enumerate(g["codes"] if isinstance(g.get("codes"), list) else []):
            if not (isinstance(c, dict) and isinstance(c.get("column"), str) and isinstance(c.get("code"), str)):
                continue
            pair = (c["column"], c["code"])
            if pair in pairs:
                yield (f"glossary[{i}].codes[{j}]",
                       f"gives {pair[0]} {pair[1]} once: several codes on one column are alternatives, each given once")
            pairs.add(pair)


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


ID_SECTIONS = ("manifest", "sources", "glossary", "terminology", "calendars", "measures", "derived_measures", "count_metrics", "banded_dimensions", "policies", "access", "tests", "curated_sources",
               "contexts", "filters", "row_selectors", "joins", "acronyms", "entities", "relations", "ontology", "runtimes",
               "shapes", "recipes", "clarifications", "examples")
POLICY_KINDS = ("restricted_attributes", "scd2", "release_driver", "partitioned")


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
    if "terminology" in held:
        unique(d["terminology"].get("concepts", []), "id", "terminology.concepts")
    if "calendars" in held:
        unique(d.get("calendars", []), "id", "calendars")
    if "measures" in held:
        unique(d["measures"], "id", "measures")
    if "derived_measures" in held:
        unique(d.get("derived_measures", []), "id", "derived_measures")
    if "banded_dimensions" in held:
        unique(d.get("banded_dimensions", []), "id", "banded_dimensions")
    if "count_metrics" in held:
        unique(d.get("count_metrics", []), "id", "count_metrics")
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
    for section in ("contexts", "joins", "entities", "relations", "ontology", "runtimes", "shapes", "recipes", "clarifications", "examples"):
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
            out.append(Finding("S07", pack.at(f"mappings[{i}]"), "is certified: it names the certifying role or office and date (certified_by, certified_on)"))
        key = (m["column"], m["term"])
        if key in seen:
            out.append(Finding("S07", pack.at(f"mappings[{i}]"), f"maps {m['column']} to {m['term']} again: mappings[{seen[key]}] does"))
        else:
            seen[key] = i
    return out


READ_ONLY = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Median, exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean,
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
    manifest = pack.data.get("manifest")
    written = semver(manifest.get("meta_model_version")) if isinstance(manifest, dict) else None
    declared_runtimes = {REFERENCE} | {
        r["id"] for r in (pack.data.get("runtimes") or []) if isinstance(r, dict) and isinstance(r.get("id"), str)
    }
    out, by_column = [], {}
    for i, x in enumerate(pack.data["measures"]):
        for problem in aggregation_problems(x):
            out.append(Finding("S08", pack.at(f"measures[{i}].aggregations"), problem))
        if "moving_average_lengths" in x:
            lengths = x["moving_average_lengths"]
            if (not lengths or any(type(n) is not int or n < 1 for n in lengths)
                    or len(set(lengths)) != len(lengths)):
                out.append(Finding("S08", pack.at(f"measures[{i}].moving_average_lengths"),
                                   "names distinct positive integer window lengths in periods"))
        if x["term"] not in terms:
            out.append(Finding("S08", pack.at(f"measures[{i}].term"), f"{x['term']} is not a term the glossary declares"))
        if "certified_in" in x:
            if written and written < (1, 5, 0):
                out.append(Finding("S08", pack.at(f"measures[{i}].certified_in"), "needs meta-model 1.5.0 or later"))
            if x["certified_in"] not in declared_runtimes:
                out.append(Finding("S08", pack.at(f"measures[{i}].certified_in"), f"{x['certified_in']} is not a declared runtime"))
        if locate(pack.lock, x["column"]) is None:
            out.append(Finding("S08", pack.at(f"measures[{i}].column"), f"{x['column']} is not a column in pack.lock (source.table.column)"))
            continue
        if x["column"] in by_column:
            prior = pack.data["measures"][by_column[x["column"]]]
            if any(x.get(k, "") != prior.get(k, "") for k in ("grain", "currency", "calendar", "conversion")):
                out.append(Finding("S08", pack.at(f"measures[{i}].column"), f"carries measures[{by_column[x['column']]}] already: "
                                   "measures on one column must agree on grain, currency, calendar and conversion"))
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


def aggregation_problems(measure: dict) -> list[str]:
    """Alternate aggregations retain one declared row expression and its optional FILTER."""
    choices = measure.get("aggregations")
    if choices is None:
        return []
    out = []
    if not choices:
        out.append("names at least one allowed aggregation")
    if len(choices) != len(set(choices)):
        out.append("names each allowed aggregation once")
    if measure["aggregation"] not in choices:
        out.append("includes the measure's default aggregation, " + measure["aggregation"])
    if any(choice != measure["aggregation"] for choice in choices):
        try:
            root = sqlglot.parse_one(measure["expression"], read="duckdb")
        except sqlglot.errors.SqlglotError:
            return out
        top = root.this if isinstance(root, exp.Filter) else root
        if (not isinstance(top, (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Median))
                or len(list(root.find_all(exp.AggFunc))) != 1
                or isinstance(top.this, (exp.Distinct, exp.Star))):
            out.append("alternate aggregations require one aggregate of a declared row expression, optionally with FILTER")
    return out


def derived_measures(pack: Pack) -> list[Finding]:
    """S08: a named arithmetic formula binds two declared physical measures, never SQL text."""
    terms = {g["id"] for g in pack.data["glossary"]}
    physical = {m["id"] for m in pack.data["measures"]} | {m["id"] for m in pack.data.get("count_metrics", [])}
    reserved = {}
    for section in ("sources", "curated_sources", "contexts", "entities", "calendars", "joins"):
        rows = pack.data.get(section)
        if isinstance(rows, list):
            reserved.update((row["id"], section) for row in rows
                            if isinstance(row, dict) and isinstance(row.get("id"), str))
    out = []
    for i, measure in enumerate(pack.data.get("derived_measures", [])):
        at = f"derived_measures[{i}]"
        if measure["id"] in physical:
            out.append(Finding("S08", pack.at(at + ".id"), "is a physical measure's id already"))
        elif measure["id"] in reserved:
            out.append(Finding("S08", pack.at(at + ".id"),
                               f"is already declared in {reserved[measure['id']]}: a derived measure needs a distinct vocabulary id"))
        elif locate(pack.lock, measure["id"]) is not None:
            out.append(Finding("S08", pack.at(at + ".id"), "is a qualified warehouse column id already"))
        if measure["term"] not in terms:
            out.append(Finding("S08", pack.at(at + ".term"), "is not a term the glossary declares"))
        if measure["formula"]["kind"] == "share" and measure["formula"]["numerator"] != measure["formula"]["denominator"]:
            out.append(Finding("S08", pack.at(at + ".formula.denominator"), "a share's denominator is its numerator metric at total scope"))
        for operand in ("numerator", "denominator"):
            if measure["formula"][operand] not in physical:
                out.append(Finding("S08", pack.at(at + ".formula." + operand), "names a declared physical measure or count metric"))
        bound = measure.get("contexts", {})
        if measure["formula"]["kind"] == "share" and bound.get("numerator") != bound.get("denominator"):
            out.append(Finding("S08", pack.at(at + ".contexts"), "a share uses the same input population at total scope"))
        def by_id(section):
            rows = pack.data.get(section)
            return {row["id"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)} if isinstance(rows, list) else {}
        contexts, counts, entities = (by_id(section) for section in ("contexts", "count_metrics", "entities"))
        for role, context_id in bound.items():
            context = contexts.get(context_id)
            operand = measure["formula"][role]
            count = counts.get(operand)
            if context is None:
                out.append(Finding("S08", pack.at(at + ".contexts." + role), "names a declared input context"))
            elif count is None and context["measure"] != operand:
                out.append(Finding("S08", pack.at(at + ".contexts." + role), "the context does not declare this input measure"))
            elif count is not None:
                own = count.get("context")
                entity = entities.get(count["entity"], {})
                compatible = (own == context_id if own else context["fact"] == entity.get("table") and not any(
                    context.get(k) for k in ("filters", "row_selectors", "joins", "bindings", "version_dates")))
                if not compatible:
                    out.append(Finding("S08", pack.at(at + ".contexts." + role), "cannot override a count metric's declared population"))
    return out


def count_metrics(pack: Pack) -> list[Finding]:
    """S08: a distinct entity key at a declared population, with local filters only."""
    terms = {g["id"] for g in pack.data["glossary"]}
    entities = {e["id"]: e for e in pack.data.get("entities", [])}
    contexts = {c["id"]: c for c in pack.data.get("contexts", [])}
    joins = {j["id"]: j for j in pack.data.get("joins", [])}
    reserved = {row["id"] for section in ("measures", "derived_measures", "banded_dimensions", "contexts", "entities", "sources", "curated_sources", "joins", "calendars", "filters", "row_selectors")
                for row in pack.data.get(section, [])}
    reserved.update(p["id"] for rows in pack.data.get("policies", {}).values() for p in rows
                    if isinstance(p, dict) and isinstance(p.get("id"), str))
    reserved.update(prefix + entity for entity in entities for prefix in ("count:", "context:count:"))
    restricted = {c for p in pack.data.get("policies", {}).get("restricted_attributes", []) for c in p["columns"]}
    out = []
    for i, metric in enumerate(pack.data.get("count_metrics", [])):
        at = f"count_metrics[{i}]"
        if metric["id"] in reserved or locate(pack.lock, metric["id"]) is not None:
            out.append(Finding("S08", pack.at(at + ".id"), "a count metric needs a distinct vocabulary id"))
        if metric["term"] not in terms:
            out.append(Finding("S08", pack.at(at + ".term"), "is not a term the glossary declares"))
        entity = entities.get(metric["entity"])
        if entity is None:
            out.append(Finding("S08", pack.at(at + ".entity"), "names a declared entity and its complete key"))
        context = contexts.get(metric.get("context"))
        if "context" in metric and context is None:
            out.append(Finding("S08", pack.at(at + ".context"), "names a declared population context"))
        if entity is not None and context is not None:
            reachable = {context["fact"]}
            edges = [joins[j] for j in (*context.get("joins", []), *context.get("reaches", [])) if j in joins]
            while True:
                expanded = reachable | {j["to"] for j in edges if j["from"] in reachable}
                if expanded == reachable:
                    break
                reachable = expanded
            if entity["table"] not in reachable:
                out.append(Finding("S08", pack.at(at + ".entity"), "the context has no declared path to this entity's key"))
        for j, predicate in enumerate(metric.get("filters", [])):
            location = at + f".filters[{j}]"
            column = predicate["column"]
            if locate(pack.lock, column) is None:
                out.append(Finding("S08", pack.at(location + ".column"), "names a column in pack.lock"))
            elif column in restricted:
                out.append(Finding("S08", pack.at(location + ".column"), "is restricted personal data"))
            elif entity is not None and table_of(column) != entity["table"]:
                out.append(Finding("S08", pack.at(location + ".column"), "a count's local filter reads its entity's table"))
            if predicate["op"] in TEXT_MATCHES:
                out.extend(Finding("S08", pack.at(location + "." + field), problem)
                           for field, problem in text_filter_problems(pack.lock, column, predicate.get("value")))
            if predicate["op"] in NEEDS_VALUE:
                value = predicate.get("value")
                descriptor = locate(pack.lock, column)
                if not count_filter_value(value, (descriptor or {}).get("type", "")):
                    out.append(Finding("S08", pack.at(location + ".value"), "a comparison requires text or a number compatible with the column type"))
            elif "value" in predicate:
                out.append(Finding("S08", pack.at(location + ".value"), "a null test takes no value"))
    return out


def text_filter_problems(lock: dict, column: str, value) -> list[tuple[str, str]]:
    """Text operations share one literal and column-type contract across pack declarations."""
    out = []
    if problem := text_value_problem(value):
        out.append(("value", problem))
    target = locate(lock, column)
    if target is not None and type_family(target["type"]) != "text":
        out.append(("column", "text matching requires a text column"))
    return out


def count_filter_value(value, data_type):
    """The scalar types supported by a count's typed, local comparison."""
    family = type_family(data_type)
    try:
        if family == "numeric":
            return type(value) in (str, int, float) and Decimal(str(value)).is_finite()
        if family == "text":
            return isinstance(value, str)
        if family == "boolean":
            return isinstance(value, bool) or isinstance(value, str) and value.casefold() in ("true", "false")
        if family == "temporal":
            return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except (ValueError, ArithmeticError):
        return False
    return False


def calendars(pack: Pack) -> list[Finding]:
    """S09."""
    d, lock, out = pack.data, pack.lock, []
    declared = {c["id"] for c in d.get("calendars", [])}
    recurring_meanings = {}
    for i, c in enumerate(d.get("calendars", [])):
        if not 1 <= c["year_start_month"] <= 12:
            out.append(Finding("S09", pack.at(f"calendars[{i}].year_start_month"), f"is {c['year_start_month']}: a month, 1 to 12"))
        if "{n}" not in c["quarter_label"]:
            out.append(Finding("S09", pack.at(f"calendars[{i}].quarter_label"), "names the quarter's number, {n}"))
        spine = c.get("spine")
        if spine:
            at = f"calendars[{i}].spine"
            table = locate(lock, spine["table"], 2)
            column = locate(lock, spine["table"] + "." + spine["column"])
            if table is None:
                out.append(Finding("S09", pack.at(at + ".table"), "names a table in pack.lock (source.table)"))
            elif column is None or str(column["type"]).upper() != "DATE":
                out.append(Finding("S09", pack.at(at + ".column"), "names a DATE column of the spine table in pack.lock"))
            elif [spine["column"]] != table.get("primary_key"):
                out.append(Finding("S09", pack.at(at + ".column"), "is the spine table's single-column primary key"))
            attributes = spine.get("attributes", {})
            if not isinstance(attributes, dict):
                out.append(Finding("S09", pack.at(at + ".attributes"), "is a mapping from attribute names to column references"))
                continue
            for attribute, ref in attributes.items():
                if not isinstance(attribute, str) or not attribute.strip() or not isinstance(ref, str):
                    out.append(Finding("S09", pack.at(at + ".attributes"), "maps nonempty attribute names to qualified column names"))
                    continue
                held = locate(lock, ref)
                where = pack.at(at + ".attributes." + attribute)
                if held is None:
                    out.append(Finding("S09", where, "names a column in pack.lock (source.table.column)"))
                elif ref.split(".")[0] != spine["table"].split(".")[0]:
                    out.append(Finding("S09", where, "belongs to the spine's own source"))
                elif str(held["type"]).upper().split("(")[0] not in (*TEXT_TYPES, *NUMERIC_TYPES):
                    out.append(Finding("S09", where, "names a text or numeric calendar attribute column"))
            out.extend(calendar_recurrences(pack, i, spine, recurring_meanings))
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


def calendar_recurrences(pack: Pack, index: int, spine: dict, shared_meanings=None) -> list[Finding]:
    """Named calendar attributes and their value words are data, with a declared recurrence cycle."""
    out, attributes, seen, words = [], spine.get("attributes", {}), set(), {}
    shared_meanings = {} if shared_meanings is None else shared_meanings
    rank = {"day": 0, "week": 1, "month": 2, "calendar_quarter": 3, "fiscal_quarter": 3,
            "year": 4, "fiscal_year": 4}
    for n, recurrence in enumerate(spine.get("recurrences", ())):
        at = f"calendars[{index}].spine.recurrences[{n}]"
        attribute = recurrence["attribute"]
        if attribute not in attributes:
            out.append(Finding("S09", pack.at(at + ".attribute"), "names an attribute declared on this calendar spine"))
        if attribute in seen:
            out.append(Finding("S09", pack.at(at + ".attribute"), "declares each recurring attribute once"))
        seen.add(attribute)
        if rank[recurrence["grain"]] >= rank[recurrence["cycle"]]:
            out.append(Finding("S09", pack.at(at + ".cycle"), "is coarser than the recurring attribute's observation grain"))
        values = set()
        held = locate(pack.lock, attributes.get(attribute))
        dtype = str(held["type"]).upper().split("(")[0] if held else None
        if not recurrence["values"]:
            out.append(Finding("S09", pack.at(at + ".values"), "names at least one recurring attribute value"))
        for j, member in enumerate(recurrence["values"]):
            scalar = member["value"]
            if isinstance(scalar, bool) or not isinstance(scalar, (int, float, str)):
                out.append(Finding("S09", pack.at(at + f".values[{j}].value"), "is a text or numeric calendar attribute value"))
            elif scalar in values:
                out.append(Finding("S09", pack.at(at + f".values[{j}].value"), "declares each attribute value once"))
            else:
                values.add(scalar)
                if (dtype in NUMERIC_TYPES and not isinstance(scalar, (int, float))
                        or dtype in TEXT_TYPES and not isinstance(scalar, str)):
                    out.append(Finding("S09", pack.at(at + f".values[{j}].value"), "matches the declared attribute column's scalar type"))
                if isinstance(scalar, (int, float)) and not Decimal(str(scalar)).is_finite():
                    out.append(Finding("S09", pack.at(at + f".values[{j}].value"), "is a finite calendar attribute value"))
            if not member["phrases"]:
                out.append(Finding("S09", pack.at(at + f".values[{j}].phrases"), "names at least one wording of this value"))
            for phrase in member["phrases"]:
                normalized = phrase.strip().casefold()
                if not normalized:
                    out.append(Finding("S09", pack.at(at + f".values[{j}].phrases"), "names a nonempty calendar wording"))
                    continue
                meaning = (attributes.get(attribute), scalar, recurrence["grain"], recurrence["cycle"])
                if normalized in words:
                    out.append(Finding("S09", pack.at(at + f".values[{j}].phrases"), "declares each calendar wording once"))
                if normalized in shared_meanings and shared_meanings[normalized] != meaning:
                    out.append(Finding("S09", pack.at(at + f".values[{j}].phrases"),
                                       "has different calendar meanings across calendars"))
                words[normalized] = meaning
                shared_meanings.setdefault(normalized, meaning)
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
    manifest = pack.data.get("manifest")
    written = semver(manifest.get("meta_model_version")) if isinstance(manifest, dict) else None
    out, seen, qualified_locations = [], {}, set()
    for i, concept in enumerate(pack.data["terminology"].get("concepts", [])):
        if not concept["wordings"]:
            out.append(Finding("S11", pack.at(f"terminology.concepts[{i}].wordings"), "a concept names at least one wording"))
        for j, qualified in enumerate(concept.get("qualified_terms", ())):
            qualified_at = f"terminology.concepts[{i}].qualified_terms[{j}]"
            qualified_locations.add(qualified_at)
            if not qualified["noun"].strip():
                out.append(Finding("S11", pack.at(qualified_at + ".noun"),
                                   "a qualified term names a non-empty noun"))
            if qualified["term"] not in terms:
                out.append(Finding("S11", pack.at(qualified_at + ".term"),
                                   f"{qualified['term']} is not a term the glossary declares"))
    for at, phrase_at, a in alias_declarations(pack.data["terminology"]):
        kind = next(k for k in ALIAS_KINDS if k in a)
        if kind == "term" and a["term"] not in terms and at not in qualified_locations:
            out.append(Finding("S11", pack.at(f"{at}.term"), f"{a['term']} is not a term the glossary declares"))
        if kind == "source" and a["source"] not in sources:
            out.append(Finding("S11", pack.at(f"{at}.source"), f"{a['source']} is not a declared source"))
        if kind == "sources":
            if written and written < (1, 4, 0):
                out.append(Finding("S11", pack.at(f"{at}.sources"), "needs meta-model 1.4.0 or later"))
            if not a["sources"]:
                out.append(Finding("S11", pack.at(f"{at}.sources"), "is a non-empty list of declared sources"))
            if len(a["sources"]) != len(set(a["sources"])):
                out.append(Finding("S11", pack.at(f"{at}.sources"), "contains duplicate sources"))
            for j, source in enumerate(a["sources"]):
                if source not in sources:
                    out.append(Finding("S11", pack.at(f"{at}.sources[{j}]"), f"{source} is not a declared source"))
        if kind == "candidate" and a["candidate"].lower() in names:
            out.append(Finding("S11", pack.at(f"{at}.candidate"), f"{a['candidate']} is a declared term: name it, term: "
                               f"{names[a['candidate'].lower()]}"))
        for k in ("group_by", "column", "column_wording"):
            if kind == k and locate(pack.lock, a[k]) is None:
                out.append(Finding("S11", pack.at(f"{at}.{k}"), f"{a[k]} is not a column in pack.lock (source.table.column)"))
        if kind == "entity" and a["entity"] not in entities:
            out.append(Finding("S11", pack.at(f"{at}.entity"), f"{a['entity']} is not a declared entity"))
        for k, known in (("time_mode", TIME_MODES), ("period", PERIODS)):
            if kind == k and a[k] not in known:
                out.append(Finding("S11", pack.at(f"{at}.{k}"), f"{a[k]!r} is not a {k.replace('_', ' ')} the platform knows "
                                   f"({', '.join(known)})"))
        phrase = a["phrase"].strip().lower()
        if not phrase:
            out.append(Finding("S11", pack.at(phrase_at), "a wording is non-empty text"))
        if phrase in seen:
            earlier, target = seen[phrase]
            said = "is listed already" if target == (kind, a[kind]) else f"names {kind} {a[kind]} here and {target[0]} {target[1]} there"
            out.append(Finding("S11", pack.at(phrase_at), f"{a['phrase']!r} is {earlier}'s phrase too, and {said}: "
                               "a phrase has one target"))
        else:
            seen[phrase] = (at, (kind, a[kind]))
    return out


WRITES = ("terms", "mappings")


def roles(pack: Pack, meta: dict) -> list[Finding]:
    """S14."""
    from kge.pipeline.access import check_pack_roles
    sources = {s["id"] for s in pack.data["sources"]}
    processes = sorted(process_vocabulary((meta["node_types"].get("Process") or {}).get("constants")))
    out = []
    for i, r in enumerate((pack.data.get("access") or {}).get("roles", [])):
        at = f"access.roles[{i}]"
        out += [Finding("S14", pack.at(f"{at}.id"), message) for message in check_pack_roles([r["id"]])]
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


def listed_values_problems(predicate: dict) -> list[str]:
    """What is wrong with a predicate's values: in and not_in name two or more values, all text or all numbers, each once; any
    other operator takes none."""
    op, values = predicate["op"], predicate.get("values")
    if op not in NEEDS_VALUES:
        return [f"{op} takes no values: only in and not_in name a list"] if "values" in predicate else []
    if not isinstance(values, list) or len(values) < 2:
        return [f"{op} names two or more values, not {values!r}"]
    texts = all(isinstance(v, str) for v in values)
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    if not texts and not numbers:
        return [f"are all text or all numbers, not {values!r}"]
    keys = [v if texts else Decimal(str(v)) for v in values]
    if len(set(keys)) != len(keys):
        return [f"names each value once, not {values!r}"]
    return []


SHAPES_SINCE = (1, 6, 0)
SHAPES_NEEDED = "needs meta-model 1.6.0 or later"


def before_shapes(pack: Pack) -> bool:
    """Whether the pack is written for a meta-model before 1.6.0, which has no question-shape sections."""
    manifest = pack.data.get("manifest")
    written = semver(manifest.get("meta_model_version")) if isinstance(manifest, dict) else None
    return bool(written) and written < SHAPES_SINCE


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
        if "whole" in v and (not v.get("values") or not v["whole"].strip()):
            out.append(Finding("S29", pack.at(f"{at}.whole"), "names the whole a measure split by the listed values is shown "
                               "beside its parts: a non-empty label, with values listed"))
    seen_pairs = set()
    for i, pair in enumerate(t.get("code_name_pairs") or []):
        at = f"terminology.code_name_pairs[{i}]"
        for role in ("code", "name"):
            column(at + "." + role, pair[role])
        columns = (pair["code"], pair["name"])
        if columns[0] == columns[1] or columns[0].rsplit(".", 1)[0] != columns[1].rsplit(".", 1)[0]:
            out.append(Finding("S29", pack.at(at), "pairs distinct columns on the same table"))
        if columns in seen_pairs:
            out.append(Finding("S29", pack.at(at), "declares the code/name pair twice"))
        seen_pairs.add(columns)
        codes = [m["code"] for m in pair["members"]]
        if not codes or len(set(codes)) != len(codes):
            out.append(Finding("S29", pack.at(at), "declares each member code once"))
    for i, x in enumerate(t.get("predicates") or []):
        at = f"terminology.predicates[{i}]"
        column(at, x["column"])
        if "part" in x and before_shapes(pack):
            out.append(Finding("S29", pack.at(f"{at}.part"), SHAPES_NEEDED))
        value = x.get("value")
        if x['op'] in TEXT_MATCHES:
            out.extend(Finding("S29", pack.at(at + "." + field), problem)
                       for field, problem in text_filter_problems(pack.lock, x["column"], value))
        if x["op"] in NEEDS_VALUE and value is None:
            out.append(Finding("S29", pack.at(f"{at}.value"), f"is required: {x['op']} compares the column with a value"))
        elif x["op"] not in NEEDS_VALUE and "value" in x:
            out.append(Finding("S29", pack.at(f"{at}.value"), f"{x['op']} takes no value"))
        elif x["op"] in NEEDS_VALUE and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
            out.append(Finding("S29", pack.at(f"{at}.value"), f"is text or a number, not {value!r}"))
        out += [Finding("S29", pack.at(f"{at}.values"), problem) for problem in listed_values_problems(x)]
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


def banded_dimensions(pack: Pack) -> list[Finding]:
    """S29. A band labels every numeric value once, using one unrestricted column."""
    out, labels = [], set()
    reserved = {item['id'] for section in ('sources', 'curated_sources', 'contexts', 'measures', 'derived_measures', 'count_metrics',
                                          'calendars', 'entities', 'joins')
                if isinstance(pack.data.get(section), list) for item in pack.data[section]
                if isinstance(item, dict) and isinstance(item.get('id'), str)}
    reserved.update(f'{source}.{table["name"]}.{column["name"]}' for source, held in pack.lock['sources'].items()
                    for table in held['tables'] for column in table['columns'])
    restricted = {c for p in (pack.data.get('policies') or {}).get('restricted_attributes', ()) for c in p['columns']}
    for i, declaration in enumerate(pack.data.get('banded_dimensions', ())):
        at = f'banded_dimensions[{i}]'
        if declaration['id'] in reserved:
            out.append(Finding('S29', pack.at(at + '.id'), 'band id must not replace an existing vocabulary name'))
        column = locate(pack.lock, declaration['column'])
        if column is None or type_family(column['type']) != 'numeric' or declaration['column'] in restricted:
            out.append(Finding('S29', pack.at(at + '.column'), 'requires a declared unrestricted numeric column'))
        label = declaration['label'].strip().casefold()
        if not label or label in labels:
            out.append(Finding('S29', pack.at(at + '.label'), 'band labels must be nonempty and unique'))
        labels.add(label)
        out.extend(Finding('S29', pack.at(at + '.ranges'), problem) for problem in band_problems(declaration['ranges']))
    return out


PREDICATE = (exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean, exp.Paren, exp.Neg, exp.Add, exp.Sub, exp.Mul,
             exp.Div, exp.Mod, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is, exp.In, exp.Between, exp.Like,
             exp.ILike, exp.And, exp.Or, exp.Not, exp.Case, exp.If, exp.Cast, exp.DataType, exp.DataTypeParam, exp.Coalesce,
             exp.Upper, exp.Nullif, exp.LastDay, exp.DateTrunc, exp.TimestampTrunc, exp.Interval, exp.Var)


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
    CAST, COALESCE, NULLIF, UPPER and the date functions a calendar rollup needs): no aggregate, query or other function,
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
                              "COALESCE, NULLIF, UPPER or a date function, which are all a condition may use"], [], set(), [], [], [])
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
    seen = {}
    for i, j in enumerate(d.get("joins", [])):
        at = f"joins[{i}]"
        out += [Finding("S23", pack.at(f"{at}.validity_window"), problem) for problem in join_window_problems(j, lock, restricted)]
        try:
            predicate = j["condition"]
            window = j.get("validity_window") or {}
            if set(window) == {"date", "effective", "expiry"} and all(isinstance(v, str) for v in window.values()):
                predicate += f" AND {window['date']} BETWEEN {window['effective']} AND {window['expiry']}"
            parsed_condition = sqlglot.parse_one(predicate, read="duckdb")
            parsed_condition = sqlglot.parse_one(parsed_condition.sql("duckdb", comments=False), read="duckdb")
            parsed_condition = normalize_identifiers(parsed_condition, dialect="duckdb")
            endpoints = {j[end].casefold() for end in ("from", "to", "through") if j.get(end)}
            for column in parsed_condition.find_all(exp.Column):
                if column.db:
                    target = column.db + "." + column.table
                elif column.table == (j.get("alias") or "").casefold():
                    continue
                else:
                    matches = [table for table in endpoints if table.split(".")[-1] == column.table]
                    target = matches[0] if len(matches) == 1 else None
                if target in endpoints or (target and locate(lock, target, 2) is not None):
                    column.set("table", exp.to_identifier(target.replace(".", "__")))
                    column.set("db", None)
            condition = tuple(sorted(canonical_predicate(atom, {})
                                     for atom in conjuncts(parsed_condition)))
        except sqlglot.errors.SqlglotError:
            condition = j["condition"]
        signature = (j["from"], j["to"], j.get("type", "inner"), condition, j.get("alias"),
                     j.get("through"))
        if signature in seen:
            out.append(Finding("S23", pack.at(at), f"duplicates join {seen[signature]}: each type, condition and validity window is declared once"))
        seen[signature] = j["id"]
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


def join_window_problems(join: dict, lock: dict, restricted: set[str] = frozenset()) -> list[str]:
    """A window anchors declared date columns to one joined side's inclusive effective and expiry bounds."""
    window = join.get("validity_window")
    if window is None:
        return []
    if not isinstance(window, dict) or set(window) != {"date", "effective", "expiry"}:
        return ["names exactly date, effective and expiry columns"]
    out = []
    for role, column in window.items():
        found = locate(lock, column)
        if found is None:
            out.append(f"{role} {column} is not a column in pack.lock (source.table.column)")
        elif not re.fullmatch(r"DATE|TIMESTAMP(?: WITH(?:OUT)? TIME ZONE)?|TIMESTAMPTZ", found["type"].upper().split("(")[0].strip()):
            out.append(f"{role} {column} is not a date column ({found['type']})")
        if column in restricted:
            out.append(f"{role} {column} is restricted personal data")
    columns = list(window.values())
    if len(set(columns)) != len(columns):
        out.append("names each date column once")
    if all(isinstance(c, str) for c in columns):
        bounds = {table_of(window[role]) for role in ("effective", "expiry")}
        if len(bounds) != 1 or not bounds <= {join["from"], join["to"]}:
            out.append("effective and expiry name columns of the same joined side")
        elif table_of(window["date"]) in bounds:
            out.append("date names a different table from the effective and expiry bounds")
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
        if c.get("ytd_calendar") and c["ytd_calendar"] not in {v["id"] for v in d.get("calendars", [])}:
            out.append(Finding("S25", pack.at(f"{at}.ytd_calendar"), "is not a declared calendar"))
        bindings = c.get("bindings", {})
        entity_ids = {e["id"] for e in d.get("entities", [])}
        if not isinstance(bindings, dict) or any(not isinstance(a, str) or not isinstance(b, str)
                or (a not in entity_ids and locate(lock, a, 3) is None)
                or (b not in entity_ids and locate(lock, b, 3) is None) for a, b in bindings.items()):
            out.append(Finding("S25", pack.at(f"{at}.bindings"), "must bind declared columns or entities"))
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
                window = j.get("validity_window") or {}
                anchors = {table_of(column) for column in window.values()}
                if not anchors <= reached | ends:
                    out.append(Finding("S25", pack.at(f"{at}.{key}[{k}]"),
                                       "validity window names a table the context has not reached before this join"))
                reached |= ends
                if key == "joins":
                    always |= ends
        versions = c.get("version_dates", {})
        if not isinstance(versions, dict) or any(
                table not in reached or table not in {p["table"] for p in (d.get("policies") or {}).get("scd2", [])}
                or not isinstance(date, str) or locate(lock, date, 3) is None
                or date.rsplit(".", 1)[0] not in reached or date in restricted_columns(pack)
                or "DATE" not in locate(lock, date, 3)["type"].upper()
                for table, date in versions.items()):
            out.append(Finding("S25", pack.at(f"{at}.version_dates"), "must date reachable history tables by reachable unrestricted columns"))
        if isinstance(bindings, dict):
            entities = {e["id"]: e for e in d.get("entities", [])}
            restricted = {column for rule in d.get("policies", {}).get("restricted_attributes", []) for column in rule["columns"]}
            for source, target in bindings.items():
                if not isinstance(target, str):
                    continue
                table = entities[target]["table"] if target in entities else table_of(target)
                if table not in reached or (source in entity_ids) != (target in entity_ids) or source in restricted or target in restricted:
                    out.append(Finding("S25", pack.at(f"{at}.bindings"), "must preserve column/entity kind and bind unrestricted names within the context reach"))
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


TIME_POLICIES = {
    "scd2": ("valid_from", "valid_to", "is_current"),
    "release_driver": ("period", "release_date", "freeze_date", "released"),
    "partitioned": ("column",),
}
PARTITION_TYPES = ("DATE", "TIMESTAMP", "DATETIME", "TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITHOUT TIME ZONE")
NUMERIC_TYPES = (
    "INT",
    "INT64",
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "BYTEINT",
    "NUMERIC",
    "BIGNUMERIC",
    "DECIMAL",
    "DEC",
    "NUMBER",
    "FLOAT",
    "FLOAT64",
    "DOUBLE",
    "REAL",
)
TEXT_TYPES = ("STRING", "VARCHAR", "CHAR", "CHARACTER", "TEXT", "NVARCHAR", "NCHAR")
BOOL_TYPES = ("BOOL", "BOOLEAN")


def is_partition_type(data_type: str) -> bool:
    norm = str(data_type).strip().upper().split("(")[0].strip()
    return norm in PARTITION_TYPES or norm.startswith(("DATE", "TIMESTAMP"))


def type_family(data_type: str) -> str:
    norm = str(data_type).strip().upper().split("(")[0].strip()
    if is_partition_type(norm):
        return "temporal"
    if norm in NUMERIC_TYPES or norm.startswith(("INT", "DEC", "NUM", "FLOAT", "DOUBLE")):
        return "numeric"
    if norm in TEXT_TYPES or norm.startswith(("VARCHAR", "CHAR", "TEXT", "STRING")):
        return "text"
    if norm in BOOL_TYPES:
        return "boolean"
    return norm


def types_compatible(ref_type: str, runtime_type: str) -> bool:
    return type_family(ref_type) == type_family(runtime_type)


def time_policies(pack: Pack) -> list[Finding]:
    """S28."""
    lock, out = pack.lock, []
    manifest = pack.data.get("manifest")
    written = semver(manifest.get("meta_model_version")) if isinstance(manifest, dict) else None
    for kind, fields in TIME_POLICIES.items():
        seen: dict[str, int] = {}
        entries = (pack.data.get("policies") or {}).get(kind, [])
        if kind == "partitioned" and entries and written and written < (1, 5, 0):
            out.append(Finding("S28", pack.at("policies.partitioned"), "needs meta-model 1.5.0 or later"))
        for i, x in enumerate(entries):
            at = f"policies.{kind}[{i}]"
            table = locate(lock, x["table"], 2)
            if table is None:
                out.append(Finding("S28", pack.at(f"{at}.table"), f"{x['table']} is not a table in pack.lock (source.table)"))
                continue
            if x["table"] in seen:
                out.append(Finding("S28", pack.at(f"{at}.table"), f"has a {kind} policy already, policies.{kind}[{seen[x['table']]}]: "
                                   "one a table"))
            seen.setdefault(x["table"], i)
            cols_by_name = {c["name"]: c for c in table["columns"]}
            for f in fields:
                if x[f] not in cols_by_name:
                    out.append(Finding("S28", pack.at(f"{at}.{f}"), f"{x[f]} is not a column of {x['table']} in pack.lock"))
                elif kind == "partitioned" and not is_partition_type(cols_by_name[x[f]]["type"]):
                    out.append(
                        Finding(
                            "S28",
                            pack.at(f"{at}.{f}"),
                            f"{x['table']}.{x[f]} has type {cols_by_name[x[f]]['type']}, not a date or timestamp",
                        )
                    )
    return out


def _normalize_runtime_entry(rt: dict, lock: dict, pack: Pack | None = None, idx: int = 0) -> tuple[dict, list[Finding]]:
    out: list[Finding] = []
    at = f"runtimes[{idx}]"

    def add(where: str, msg: str, rule: str = "S31") -> None:
        if pack is not None:
            out.append(Finding(rule, pack.at(where), msg))

    if rt["id"] == REFERENCE:
        add(f"{at}.id", "'reference' is the internal reference runtime's id: the internal reference runtime needs no entry")
    absent_refs: set[str] = set()
    seen_tables: set[str] = set()
    for j, ref in enumerate(rt.get("absent") or []):
        t_at = f"{at}.absent[{j}]"
        if locate(lock, ref, 2) is None:
            add(t_at, f"{ref} is not a table in pack.lock (source.table)")
            continue
        if ref in seen_tables:
            add(t_at, f"{ref} is listed already")
            continue
        seen_tables.add(ref)
        absent_refs.add(ref)

    raw_tables = rt.get("tables")
    table_entries: list[tuple[str, object, str]] = []
    if raw_tables is not None:
        if isinstance(raw_tables, dict):
            for ref, spec in raw_tables.items():
                table_entries.append((str(ref), spec, f"{at}.tables.{ref}"))
        elif isinstance(raw_tables, list):
            for j, spec in enumerate(raw_tables):
                t_at = f"{at}.tables[{j}]"
                if not isinstance(spec, dict) or not isinstance(spec.get("table"), str) or not spec["table"].strip():
                    add(t_at, "is a mapping with table (source.table)", "S01")
                    continue
                table_entries.append((spec["table"], spec, t_at))
        else:
            add(f"{at}.tables", "is a mapping of reference tables or a list of table mappings", "S01")

    mapped_tables: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {}
    for ref, spec, t_at in table_entries:
        ref_tbl = locate(lock, ref, 2)
        if ref_tbl is None:
            add(t_at, f"{ref} is not a table in pack.lock (source.table)")
            continue
        if ref in seen_tables:
            add(t_at, f"{ref} is listed already")
            continue
        seen_tables.add(ref)
        if spec == "absent" or (isinstance(spec, dict) and spec.get("absent") is True):
            if isinstance(spec, dict) and any(k in spec for k in ("name", "columns")):
                add(t_at, f"{ref} is marked absent and cannot map a name or columns")
            absent_refs.add(ref)
            continue
        if isinstance(spec, str):
            target_name = spec
            raw_cols = None
        elif isinstance(spec, dict):
            allowed = {"table", "name", "columns", "absent"}
            for k in spec:
                if k not in allowed and not (isinstance(k, str) and k.startswith("x_")):
                    add(f"{t_at}.{k}", f"is not a field here: the fields are {', '.join(sorted(allowed))}", "S01")
            target_name = spec.get("name", ref_tbl["name"])
            raw_cols = spec.get("columns")
        else:
            add(t_at, "is a table mapping or absent", "S01")
            continue
        if not isinstance(target_name, str) or not re.fullmatch(
            r"(?:(?:[A-Za-z_][A-Za-z0-9_-]*\.)?[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*", target_name
        ):
            add(f"{t_at}.name", f"{target_name!r} must be TABLE, dataset.TABLE or project.dataset.TABLE; "
                "each part starts with a letter or underscore and contains letters, digits or underscores; only the project may contain hyphens")
            continue
        ref_cols = {c["name"]: c for c in ref_tbl["columns"]}
        col_entries: list[tuple[str, object, str]] = []
        if raw_cols is not None:
            if isinstance(raw_cols, dict):
                for c_ref, c_spec in raw_cols.items():
                    col_entries.append((str(c_ref), c_spec, f"{t_at}.columns.{c_ref}"))
            elif isinstance(raw_cols, list):
                for k, c_spec in enumerate(raw_cols):
                    c_at = f"{t_at}.columns[{k}]"
                    if not isinstance(c_spec, dict) or not isinstance(c_spec.get("column"), str) or not c_spec["column"].strip():
                        add(c_at, "is a mapping with column", "S01")
                        continue
                    col_entries.append((c_spec["column"], c_spec, c_at))
            else:
                add(f"{t_at}.columns", "is a mapping or list of column mappings", "S01")
        mapped_cols: dict[str, tuple[str, str]] = {}
        seen_cols: set[str] = set()
        for c_ref, c_spec, c_at in col_entries:
            short_col = c_ref[len(ref) + 1:] if c_ref.startswith(f"{ref}.") else c_ref
            if short_col not in ref_cols:
                add(c_at, f"{short_col} is not a column of {ref} in pack.lock")
                continue
            if short_col in seen_cols:
                add(c_at, f"{short_col} is listed already")
                continue
            seen_cols.add(short_col)
            if isinstance(c_spec, str):
                c_name, c_type = c_spec, ref_cols[short_col]["type"]
            elif isinstance(c_spec, dict):
                c_allowed = {"column", "name", "type"}
                for k in c_spec:
                    if k not in c_allowed and not (isinstance(k, str) and k.startswith("x_")):
                        add(f"{c_at}.{k}", f"is not a field here: the fields are {', '.join(sorted(c_allowed))}", "S01")
                c_name = c_spec.get("name", short_col)
                c_type = c_spec.get("type", ref_cols[short_col]["type"])
            else:
                add(c_at, "is a column mapping", "S01")
                continue
            if not ident(c_name):
                add(f"{c_at}.name", f"{c_name!r} is not a column name (holds no dot)")
                continue
            if not isinstance(c_type, str) or not c_type.strip():
                add(f"{c_at}.type", f"{c_type!r} is not a data type")
                continue
            if not types_compatible(ref_cols[short_col]["type"], c_type):
                add(
                    f"{c_at}.type",
                    f"{c_type!r} is not compatible with {ref}.{short_col}'s type {ref_cols[short_col]['type']!r}",
                )
                continue
            mapped_cols[short_col] = (c_name, c_type)
        mapped_tables[ref] = (target_name, mapped_cols)

    resolved_tables: list[dict] = []
    for s, held in sorted((lock or {}).get("sources", {}).items()):
        seen_rt_table_names: dict[str, str] = {}
        for t in sorted(held.get("tables", []), key=lambda x: x["name"]):
            ref = f"{s}.{t['name']}"
            if ref in absent_refs:
                continue
            t_name, col_map = mapped_tables.get(ref, (t["name"], {}))
            if t_name in seen_rt_table_names:
                add(f"{at}.tables", f"{ref} and {seen_rt_table_names[t_name]} both map to {t_name!r} in {rt['id']}")
            seen_rt_table_names[t_name] = ref
            seen_rt_col_names: dict[str, str] = {}
            cols: list[dict] = []
            for c in t["columns"]:
                c_name, c_type = col_map.get(c["name"], (c["name"], c["type"]))
                if c_name in seen_rt_col_names:
                    add(f"{at}.tables", f"{ref}.{c['name']} and {ref}.{seen_rt_col_names[c_name]} both map to {c_name!r}")
                seen_rt_col_names[c_name] = c["name"]
                cols.append({"ref_column": c["name"], "name": c_name, "type": c_type})
            resolved_tables.append({"ref_table": ref, "name": t_name, "columns": cols})

    conn = rt["connection"]
    pinned = {
        "label": rt["label"],
        "dialect": conn["dialect"],
        "location": conn["location"],
        **({"execution_identity": conn["execution_identity"]} if conn.get("execution_identity") else {}),
        **({"billing_project": conn["billing_project"]} if conn.get("billing_project") else {}),
        "absent": sorted(absent_refs),
        "tables": resolved_tables,
    }
    return pinned, out


def pin_runtimes(pack_data: dict, lock_data: dict) -> dict[str, dict]:
    """Resolve and pin every declared runtime in pack_data against lock_data."""
    out: dict[str, dict] = {}
    for i, rt in enumerate(pack_data.get("runtimes") or []):
        pinned, _ = _normalize_runtime_entry(rt, lock_data, pack=None, idx=i)
        out[rt["id"]] = pinned
    return out


def runtimes(pack: Pack) -> list[Finding]:
    """S31."""
    d, lock, out = pack.data, pack.lock, []
    manifest = d.get("manifest")
    written = semver(manifest.get("meta_model_version")) if isinstance(manifest, dict) else None
    rt_entries = d.get("runtimes") or []
    if "runtimes" in d and written and written < (1, 5, 0):
        out.append(Finding("S31", pack.at("runtimes"), "needs meta-model 1.5.0 or later"))
    if "default_runtimes" in d and written and written < (1, 5, 0):
        out.append(Finding("S31", pack.at("default_runtimes"), "needs meta-model 1.5.0 or later"))

    pinned_by_id: dict[str, dict] = {}
    for i, rt in enumerate(rt_entries):
        pinned, findings = _normalize_runtime_entry(rt, lock, pack=pack, idx=i)
        out.extend(findings)
        pinned_by_id[rt["id"]] = pinned

    for i, m in enumerate(d.get("measures") or []):
        cid = m.get("certified_in")
        if cid and cid in pinned_by_id:
            table_ref = ".".join(m["column"].split(".")[:2])
            if table_ref in pinned_by_id[cid]["absent"]:
                out.append(
                    Finding(
                        "S08",
                        pack.at(f"measures[{i}].certified_in"),
                        f"runtime {cid} marks {table_ref} as absent and cannot certify {m['id']}",
                    )
                )

    if "default_runtimes" in d:
        defaults = d["default_runtimes"]
        if not defaults:
            out.append(Finding("S31", pack.at("default_runtimes"), "is a non-empty list of runtime ids"))
        declared_ids = {REFERENCE} | {r["id"] for r in rt_entries}
        for j, rid in enumerate(defaults):
            if rid not in declared_ids:
                out.append(Finding("S31", pack.at(f"default_runtimes[{j}]"), f"{rid} is not a declared runtime"))
            elif rid in defaults[:j]:
                out.append(Finding("S31", pack.at(f"default_runtimes[{j}]"), f"{rid} is listed already"))
    return out


def gate(pack: Pack, rule: str, names: tuple[str, ...]) -> list[Finding]:
    """The finding for each of names the pack holds when it is written for a meta-model before 1.6.0."""
    if not before_shapes(pack):
        return []
    return [Finding(rule, pack.at(name), SHAPES_NEEDED) for name in names if name in pack.data]


def rows_of(data: dict, section: str) -> list[tuple[int, dict]]:
    value = data.get(section)
    return [(i, x) for i, x in enumerate(value) if isinstance(x, dict)] if isinstance(value, list) else []


def phrases(pack: Pack, preload: preloaded.Preload | None = None) -> list[Finding]:
    """S32."""
    d = pack.data
    out = gate(pack, "S32", ("phrases",))
    rows = rows_of(d, "phrases")
    if not rows:
        return out
    preload = preload or preloaded.load()
    platform = {p.casefold() for p in preload.phrases}
    aliases: dict[str, tuple[str, dict]] = {}
    for _, where, alias in alias_declarations(d.get("terminology") or {}):
        if isinstance(alias.get("phrase"), str):
            aliases.setdefault(alias["phrase"].casefold(), (where, alias))
    acronyms = {a["id"].casefold(): k for k, a in rows_of(d, "acronyms") if isinstance(a.get("id"), str)}
    seen: dict[str, int] = {}
    for i, x in rows:
        at, p = f"phrases[{i}]", x["phrase"]
        key = p.casefold()
        if p != p.lower():
            out.append(Finding("S32", pack.at(f"{at}.phrase"), f"{p} is not lower case"))
        if p != p.strip():
            out.append(Finding("S32", pack.at(f"{at}.phrase"), f"{p!r} has leading or trailing space: a phrase is its words "
                               "alone"))
        if key in seen:
            out.append(Finding("S32", pack.at(f"{at}.phrase"), f"{p} is phrases[{seen[key]}]'s already"))
        seen.setdefault(key, i)
        if "operation" in x and x["operation"] not in preload.operations:
            out.append(Finding("S32", pack.at(f"{at}.operation"), f"{x['operation']} is not an operation the platform knows "
                               "(standards/preload.yaml operations)"))
        if key in platform:
            out.append(Finding("S32", pack.at(f"{at}.phrase"), f"{p} is a preloaded phrase: a pack extends the platform's phrases "
                               "and never redefines one"))
        if key in aliases:
            where, alias = aliases[key]
            what = "a grouping phrase" if "group_by" in alias else "an alias"
            out.append(Finding("S32", pack.at(f"{at}.phrase"), f"{p} is also {what} ({where}): its meaning would depend on which "
                               "table is read first"))
        if key in acronyms:
            out.append(Finding("S32", pack.at(f"{at}.phrase"), f"{p} is also an acronym (acronyms[{acronyms[key]}])"))
    return out


SLOT_NAME = re.compile(r"[a-z][a-z0-9_]*")
INF = float("inf")


class Asks(NamedTuple):
    """What a shape or a clarification rule asks of a parsed question: the names it requires (plain, and one_of groups),
    the names it forbids, a count range per slot kind it constrains, and its position constraints."""
    plain: frozenset
    groups: frozenset
    forbids: frozenset
    ranges: dict
    constraints: frozenset


def count_range(count) -> tuple[int, float]:
    """A count as a range: n is [n, n], {min, max} is [min or 0, max or unbounded], and an absent count is [1, 1]."""
    if count is None:
        return 1, 1
    if isinstance(count, dict):
        return count.get("min", 0), count.get("max", INF)
    return count, count


def asks_of(requires, forbids, ranges: dict, constraints) -> Asks:
    requires = requires if isinstance(requires, list) else []
    plain = frozenset(r for r in requires if isinstance(r, str))
    groups = frozenset(frozenset(n for n in r["one_of"] if isinstance(n, str)) for r in requires
                       if isinstance(r, dict) and isinstance(r.get("one_of"), list))
    pairs = frozenset((op, *operands) for c in (constraints if isinstance(constraints, list) else []) if isinstance(c, dict)
                      for op, operands in c.items() if isinstance(operands, list) and len(operands) == 2)
    return Asks(plain, groups, frozenset(f for f in (forbids if isinstance(forbids, list) else []) if isinstance(f, str)),
                     ranges, pairs)


def shape_condition(shape: dict) -> Asks:
    """A shape's condition: a slot kind it declares is counted, and one it does not is unconstrained."""
    ranges: dict[str, tuple] = {}
    for slot in shape.get("slots") or []:
        if isinstance(slot, dict) and isinstance(slot.get("kind"), str) and count_ok(slot.get("count", 1)):
            lo, hi = count_range(slot.get("count"))
            low, high = ranges.get(slot["kind"], (0, 0))
            ranges[slot["kind"]] = (low + lo, high + hi)
    return asks_of(shape.get("requires"), shape.get("forbids"), ranges, shape.get("constraints"))


def rule_condition(rule: dict) -> Asks:
    """A clarification rule's condition, from its when: a slot kind it names is counted, as given."""
    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    slots = when.get("slots") if isinstance(when.get("slots"), dict) else {}
    ranges = {k: count_range(v) for k, v in slots.items() if isinstance(k, str) and count_ok(v)}
    return asks_of(when.get("requires"), when.get("forbids"), ranges, when.get("constraints"))


def required(x: Asks) -> frozenset:
    """What every question that matches x carries: each plain name it requires, and each slot kind it counts from one
    up (a declared slot with no count counts one). A one_of member is never required."""
    return x.plain | frozenset(k for k, (lo, _) in x.ranges.items() if lo >= 1)


def ordered_apart(a: Asks, b: Asks) -> bool:
    """Whether one shape's before[x, y] and the other's before[y, x] keep them from matching one question (F9). It holds
    only when both shapes require both x and y, since before[x, y] also holds where the question has no y. after and
    adjacent never separate two shapes."""
    both = required(a) & required(b)
    return any(op == "before" and x != y and {x, y} <= both and ("before", y, x) in b.constraints
               for op, x, y in a.constraints)


def overlap(a: Asks, b: Asks) -> bool:
    """Whether some parsed question can match both: neither forbids a name the other requires, nor every name of one of
    the other's one_of groups, and their count ranges meet for every slot kind both constrain. Of the position
    constraints, only before separates: one shape's before[x, y] and the other's before[y, x], where both require x and
    y (F9)."""
    if a.forbids & b.plain or b.forbids & a.plain:
        return False
    if any(g <= b.forbids for g in a.groups) or any(g <= a.forbids for g in b.groups):
        return False
    if ordered_apart(a, b):
        return False
    return all(max(a.ranges[k][0], b.ranges[k][0]) <= min(a.ranges[k][1], b.ranges[k][1]) for k in a.ranges.keys() & b.ranges.keys())


def separates(rule: Asks, a: Asks, b: Asks) -> bool:
    """Whether a clarification rule fires on every question that matches both a and b, and so is evaluated first."""
    have = a.plain | b.plain
    if not rule.plain <= have or not rule.forbids <= a.forbids | b.forbids or not rule.constraints <= a.constraints | b.constraints:
        return False
    if not all(g & have or g in a.groups or g in b.groups for g in rule.groups):
        return False
    for kind, (lo, hi) in rule.ranges.items():
        (alo, ahi), (blo, bhi) = a.ranges.get(kind, (0, INF)), b.ranges.get(kind, (0, INF))
        if not (lo <= max(alo, blo) and min(ahi, bhi) <= hi):
            return False
    return True


def shape_declarations(data: dict, preload: preloaded.Preload) -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    """The shapes, recipes and clarification rules in force: the preload's, then the pack's, by id."""
    shapes, recipes = dict(preload.shapes), dict(preload.recipes)
    for _, s in rows_of(data, "shapes"):
        if isinstance(s.get("id"), str):
            shapes.setdefault(s["id"], s)
    for _, r in rows_of(data, "recipes"):
        if isinstance(r.get("id"), str):
            recipes.setdefault(r["id"], r)
    rules = [*preload.clarifications.values(), *(c for _, c in rows_of(data, "clarifications"))]
    return shapes, recipes, rules


def coupled_pairs(shapes: dict[str, dict], rules: list[dict]) -> list[tuple[str, str]]:
    """The pairs of shape ids, in declaration order, that can match one question and that no clarification rule separates."""
    conditions = {sid: shape_condition(s) for sid, s in shapes.items()}
    guards = [rule_condition(r) for r in rules]
    ids = list(conditions)
    return [(a, b) for n, a in enumerate(ids) for b in ids[n + 1:]
            if overlap(conditions[a], conditions[b]) and not any(separates(g, conditions[a], conditions[b]) for g in guards)]


def coupled_groups(data: dict, preload: preloaded.Preload | None = None) -> list[list[str]]:
    """The coupled groups over every shape, the preload's and the pack's (data): the connected components of the
    coupled pairs, singletons included, each a sorted list of ids, sorted by first id."""
    preload = preload or preloaded.load()
    shapes, _, rules = shape_declarations(data, preload)
    parent = {sid: sid for sid in shapes}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in coupled_pairs(shapes, rules):
        parent[root(a)] = root(b)
    groups: dict[str, list[str]] = {}
    for sid in shapes:
        groups.setdefault(root(sid), []).append(sid)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def coupled(pack: Pack, preload: preloaded.Preload | None = None) -> list[list[str]]:
    """The pack's coupled shape groups (coupled_groups): what graph-model.yaml lists as shape_groups."""
    return coupled_groups(pack.data, preload)


def takes_some(slot: dict) -> bool:
    """Whether a slot may take a word: one with a count of at most 0 says the shape matches no question with that kind,
    so the recipe has nothing to use and it is not reported unused."""
    return not count_ok(slot.get("count", 1)) or count_range(slot.get("count"))[1] > 0


def clashing(hit: tuple[str, str]) -> str:
    """How a clash reads: the plan's time or limit both set, or the block both contribute to a section of the plan."""
    section, block = hit
    if section in plans.SINGULAR:
        return f"both set the plan's {section}"
    return f"both contribute a {block} block to the plan's {section} section"


def clash(a: set, b: set) -> tuple[str, str] | None:
    """The first plan node two recipes' contributions both give: a shared (section, block), or a time or a limit each sets."""
    shared = sorted(a & b)
    if shared:
        return shared[0]
    for section in ("time", "limit"):
        mine = sorted(p for p in a if p[0] == section)
        if mine and any(p[0] == section for p in b):
            return mine[0]
    return None


def names_known(value: dict, at: str, preload: preloaded.Preload, rule: str) -> list[Finding]:
    """The requires and forbids names (each one_of member too) the platform does not know, and the constraint operands
    that are not an operation, a marker or a slot kind."""
    out, known = [], preload.names()
    for k, entry in enumerate(value.get("requires") or []):
        named = [(f"{at}.requires[{k}]", entry)] if isinstance(entry, str) else [
            (f"{at}.requires[{k}].one_of[{m}]", n) for m, n in enumerate(entry.get("one_of") or [])]
        out += [Finding(rule, where, f"{n} is not an operation or a marker the platform knows") for where, n in named if n not in known]
    for k, n in enumerate(value.get("forbids") or []):
        if n not in known:
            out.append(Finding(rule, f"{at}.forbids[{k}]", f"{n} is not an operation or a marker the platform knows"))
    for k, c in enumerate(value.get("constraints") or []):
        for op, operands in c.items():
            out += [Finding(rule, f"{at}.constraints[{k}].{op}[{m}]", f"{x} is not an operation, a marker or a slot kind")
                    for m, x in enumerate(operands) if not preload.operand(x)]
    return out


def located(pack: Pack, found: list[Finding]) -> list[Finding]:
    return [Finding(f.rule, pack.at(f.path), f.message) for f in found]


def set_items(pack: Pack, rule: str, cited: dict, at: str) -> list[Finding]:
    """S25's check of a {set, items} citation, for rule at at: a set the pack names, which lists its items, and each item
    one of them."""
    sets = {s["name"]: s for s in pack.data["tests"]["sets"]}
    named = sets.get(cited["set"])
    if named is None:
        return [Finding(rule, pack.at(f"{at}.set"), f"{cited['set']} is not a question set the pack names (tests.sets)")]
    if "items" not in named:
        return [Finding(rule, pack.at(f"{at}.set"), f"{cited['set']} names no items in tests.sets: a shape's test items are items "
                        "a named set lists, by id")]
    return [Finding(rule, pack.at(f"{at}.items[{k}]"), f"{item} is not an item of {cited['set']} {named['version']}")
            for k, item in enumerate(cited["items"]) if item not in named["items"]]


def shapes(pack: Pack, preload: preloaded.Preload | None = None) -> list[Finding]:
    """S33."""
    d = pack.data
    out = gate(pack, "S33", ("shapes", "shape_tests"))
    if not any(s in d for s in ("shapes", "shape_tests", "examples")):
        return out
    preload = preload or preloaded.load()
    declared, in_force, rules = shape_declarations(d, preload)
    tests = rows_of(d, "shape_tests")
    worked = rows_of(d, "examples")
    tested = {t["shape"] for _, t in tests if t["tests"]["items"]}
    exemplified = {e["shape"] for _, e in worked}
    pack_shapes = rows_of(d, "shapes")
    preloaded_by = {sh["replaces"]: sid for sid, sh in preload.shapes.items()
                    if isinstance(sh, dict) and isinstance(sh.get("replaces"), str)}
    replaced = {builder: f"the preloaded shape {sid}" for builder, sid in preloaded_by.items()}
    for i, s in pack_shapes:
        at = f"shapes[{i}]"
        if s["id"] in preload.shapes:
            out.append(Finding("S33", pack.at(f"{at}.id"), f"{s['id']} is a preloaded shape's id: a shape the pack declares takes "
                               "an id of its own"))
        slots: dict[str, dict] = {}
        kinds: dict[str, int] = {}
        for j, slot in enumerate(s["slots"]):
            if not SLOT_NAME.fullmatch(slot["name"]):
                out.append(Finding("S33", pack.at(f"{at}.slots[{j}].name"), f"{slot['name']!r} is not a slot name (^{SLOT_NAME.pattern}$)"))
            elif slot["name"] in slots:
                out.append(Finding("S33", pack.at(f"{at}.slots[{j}].name"), f"{slot['name']} is a slot of this shape already"))
            if slot["kind"] in kinds:
                out.append(Finding("S33", pack.at(f"{at}.slots[{j}].kind"), f"{slot['kind']} is slots[{kinds[slot['kind']]}]'s kind "
                                   "already: a shape declares each slot kind once, and its count says how many"))
            slots.setdefault(slot["name"], {**slot, "at": j})
            kinds.setdefault(slot["kind"], j)
        out += located(pack, names_known(s, at, preload, "S33"))
        plain = [r for r in s["requires"] if isinstance(r, str)]
        out += [Finding("S33", pack.at(f"{at}.forbids[{k}]"), f"{n} is both required and forbidden")
                for k, n in enumerate(s.get("forbids") or []) if n in plain]
        forbidden = set(s.get("forbids") or [])
        for k, entry in enumerate(s["requires"]):
            members = entry.get("one_of") if isinstance(entry, dict) else None
            if isinstance(members, list) and members and set(members) <= forbidden:
                out.append(Finding("S33", pack.at(f"{at}.requires[{k}]"), f"one_of [{', '.join(map(str, members))}] is both "
                                   "required and forbidden: the shape forbids every name of it, so it matches no question"))
        builder = s.get("replaces")
        if builder is not None and builder not in preload.coded_builders:
            out.append(Finding("S33", pack.at(f"{at}.replaces"), f"{builder} is not a coded builder the platform lists"))
        elif builder is not None:
            if builder in replaced and preloaded_by.get(builder) != s["id"]:
                out.append(Finding("S33", pack.at(f"{at}.replaces"), f"{builder} is replaced by {replaced[builder]} already: "
                                   "one shape mirrors each coded builder"))
            replaced.setdefault(builder, f"{s['id']} (shapes[{i}])")
            if builder in preloaded.HELD_BUILDERS and s.get("stage") == "retired":
                out.append(Finding("S33", pack.at(f"{at}.stage"), f"{builder} stays coded and is never retired: "
                                   f"{preloaded.HELD_BUILDERS[builder]}"))
        recipe = in_force.get(s["recipe"])
        if recipe is None:
            out.append(Finding("S33", pack.at(f"{at}.recipe"), f"{s['recipe']} is not a recipe the pack or the platform declares"))
        else:
            used: set[str] = set()
            for _, slot_name, index, block, param in plans.slot_refs(recipe):
                used.add(slot_name)
                slot = slots.get(slot_name)
                r = s["recipe"]
                if slot is None:
                    out.append(Finding("S33", pack.at(f"{at}.recipe"), f"the recipe {r} uses ${slot_name}, which this shape does not declare"))
                    continue
                hi = count_range(slot.get("count"))[1] if count_ok(slot.get("count", 1)) else INF
                if (0 if index is None else index) >= hi:
                    given = slot_name if index is None else f"{slot_name}[{index}]"
                    out.append(Finding("S33", pack.at(f"{at}.recipe"), f"the recipe {r} uses ${given}, and the shape takes "
                                       f"at most {hi} {slot['kind']}"))
                if index is None and hi > 1 and not param.many:
                    out.append(Finding("S33", pack.at(f"{at}.recipe"), f"the recipe {r} gives ${slot_name} without an index, and the "
                                       "shape may take more than one"))
                if not plans.fits(slot["kind"], param):
                    out.append(Finding("S33", pack.at(f"{at}.recipe"), f"the recipe {r} gives ${slot_name} ({slot['kind']}) to "
                                       f"{block}.{param.name}, which takes {param.type}"))
            if plans.catalogued(recipe):
                out += [Finding("S33", pack.at(f"{at}.slots[{slot['at']}]"), f"is declared and the recipe {s['recipe']} never "
                                "uses it") for name, slot in slots.items() if name not in used and takes_some(slot)]
        if s["id"] not in tested:
            out.append(Finding("S33", pack.at(at), "has no shape_tests: every shape the pack declares or uses cites at least one "
                               "test item"))
        if s["id"] not in exemplified:
            out.append(Finding("S33", pack.at(at), "has no worked example in this pack (examples)"))
    own = {s["id"] for _, s in pack_shapes}
    citing: dict[str, str] = {}
    for section, rows in (("shape_tests", tests), ("examples", worked)):
        for k, x in rows:
            if x["shape"] in preload.shapes and x["shape"] not in own:
                citing.setdefault(x["shape"], f"{section}[{k}]")
    for sid, at in citing.items():
        if sid not in tested:
            out.append(Finding("S33", pack.at(at), f"cites {sid}, a preloaded shape, which has no shape_tests here: every shape the "
                               "pack declares or uses cites at least one test item"))
        if sid not in exemplified:
            out.append(Finding("S33", pack.at(at), f"cites {sid}, a preloaded shape, which has no worked example in this pack (examples)"))
    cited: dict[tuple[str, str, str], str] = {}
    for t, x in tests:
        at = f"shape_tests[{t}]"
        if x["shape"] not in declared:
            out.append(Finding("S33", pack.at(f"{at}.shape"), f"{x['shape']} is not a shape the pack or the platform declares"))
        if not x["tests"]["items"]:
            out.append(Finding("S33", pack.at(f"{at}.tests.items"), "cites no item"))
        out += set_items(pack, "S33", x["tests"], f"{at}.tests")
        for k, item in enumerate(x["tests"]["items"]):
            key, where = (x["shape"], x["tests"]["set"], item), f"{at}.tests.items[{k}]"
            if key in cited:
                out.append(Finding("S33", pack.at(where), f"{item} of {x['tests']['set']} is cited for {x['shape']} already "
                                   f"({cited[key]}): a shape cites each test item once"))
            cited.setdefault(key, where)
    position = {s["id"]: i for i, s in pack_shapes}
    for a, b in coupled_pairs(declared, rules):
        later = max((sid for sid in (a, b) if sid in position), key=lambda sid: position[sid], default=None)
        if later is None:
            continue
        other = b if later == a else a
        recipe_a, recipe_b = in_force.get(declared[later].get("recipe")), in_force.get(declared[other].get("recipe"))
        if recipe_a is None or recipe_b is None:
            continue
        hit = clash(plans.contributions(recipe_a), plans.contributions(recipe_b))
        if hit:
            out.append(Finding("S33", pack.at(f"shapes[{position[later]}]"), f"can match the same questions as {other} and "
                               f"{clashing(hit)}: separate them with a forbids, slot counts, a clarification rule or before constraints in "
                               "reverse order, or make their contributions disjoint"))
    return out


NAMED_IN = ("measures", "contexts", "entities", "calendars", "banded_dimensions", "derived_measures", "count_metrics", "joins", "relations")
FORM_AGGREGATIONS = ("sum", "avg", "count")
CONTROL_CALENDAR = "schema_calendar"


def plan_names_resolver(pack: Pack):
    """Whether an id resolves as a name of a kind in a plan over this pack: a declared id, or an id the runtime makes from
    declarations and the lock (kge/services/ask/engine/planrules.py: snapshot, _vocabulary and _control_vocabulary;
    kge/semantics/planvocabulary.py: count_metrics). The forms are pack-contract.md 4.14's."""
    d, lock = pack.data, pack.lock

    def ids(section: str) -> set[str]:
        return {x["id"] for _, x in rows_of(d, section) if isinstance(x.get("id"), str)}
    contexts, measures, entities = ids("contexts"), ids("measures"), ids("entities")
    joins, calendars, bands = ids("joins"), ids("calendars"), ids("banded_dimensions")
    declarations = ids("derived_measures") | ids("count_metrics")

    def column(x: str) -> bool:
        return isinstance(lock, dict) and locate(lock, x) is not None

    def table(x: str) -> bool:
        return isinstance(lock, dict) and locate(lock, x, 2) is not None

    def formed(x: str, prefix: str) -> bool:
        """<prefix><lock column>:<sum|avg|count>."""
        if not x.startswith(prefix):
            return False
        col, _, agg = x.removeprefix(prefix).rpartition(":")
        return agg in FORM_AGGREGATIONS and column(col)

    def control_context(x: str) -> bool:
        if formed(x, "schema:"):
            return True
        parts = x.removeprefix("schema:").split(":") if x.startswith("schema:") else []
        return len(parts) > 1 and parts[-1] == "count" and all(column(p) for p in parts[:-1])

    def measure(x: str) -> bool:
        return (x in measures or formed(x, "mapped:") or x.removeprefix("count:") in entities and x.startswith("count:")
                or x.endswith("/measure") and control_context(x.removesuffix("/measure")))

    def context(x: str, cohort: bool = True) -> bool:
        if x in contexts or control_context(x):
            return True
        if x.startswith("context:") and measure(x.removeprefix("context:")) and not x.removeprefix("context:").endswith("/measure"):
            return True
        return cohort and x.startswith("cohort:") and context(x.removeprefix("cohort:"), False)
    return {
        "context": context,
        "measure": measure,
        "entity": lambda x: x in entities or x.startswith("schema:entity:") and table(x.removeprefix("schema:entity:")),
        "relation": lambda x: x in joins,
        "calendar": lambda x: x in calendars or x == CONTROL_CALENDAR,
        "band": lambda x: x in bands,
        "declaration": lambda x: x in declarations,
        "column": column,
        "path": lambda x: x in joins,
    }


def recipes(pack: Pack, preload: preloaded.Preload | None = None) -> list[Finding]:
    """S34. Schema only: a recipe's blocks, parameters, references and expansion; what a plan means is checked after load."""
    d = pack.data
    out = gate(pack, "S34", ("recipes",))
    rows = rows_of(d, "recipes")
    if not rows:
        return out
    preload = preload or preloaded.load()
    resolves = plan_names_resolver(pack)
    declared = set().union(*({x["id"] for _, x in rows_of(d, s) if isinstance(x.get("id"), str)} for s in NAMED_IN))
    for r, recipe in rows:
        at = f"recipes[{r}]"
        if recipe["id"] in preload.recipes:
            out.append(Finding("S34", pack.at(f"{at}.id"), f"{recipe['id']} is a preloaded recipe's id: a recipe the pack declares "
                               "takes an id of its own"))
        out += [Finding("S34", pack.at(f"{at}.{loc}" if loc else at), msg) for loc, msg in plans.recipe_problems(recipe)]
        for b, block in enumerate(recipe["blocks"]):
            known = plans.CATALOGUE.get(block["block"])
            for p, value in block.items():
                param = known.params.get(p) if known is not None else None
                if param is None or param.type != "name":
                    continue
                given = [(f"{at}.blocks[{b}].{p}[{m}]", v) for m, v in enumerate(value)] if isinstance(value, list) else [
                    (f"{at}.blocks[{b}].{p}", value)]
                for where, v in given:
                    if not isinstance(v, str) or plans.SLOT_REF.fullmatch(v):
                        continue
                    if p in resolves and not resolves[p](v):
                        out.append(Finding("S34", pack.at(where), f"{v} is not a {p} the pack declares"))
                    elif p not in resolves and v not in declared:
                        out.append(Finding("S34", pack.at(where), f"{v} is not an id the pack declares"))
    return out


def examples(pack: Pack, preload: preloaded.Preload | None = None) -> list[Finding]:
    """S35."""
    d = pack.data
    out = gate(pack, "S35", ("examples",))
    rows = rows_of(d, "examples")
    if not rows:
        return out
    preload = preload or preloaded.load()
    stages = {sid: s.get("stage") for sid, s in preload.shapes.items()}
    stages.update({s["id"]: s["stage"] for _, s in rows_of(d, "shapes")})
    resolves = plan_names_resolver(pack)
    for e, x in rows:
        at = f"examples[{e}]"
        if x["shape"] not in stages:
            out.append(Finding("S35", pack.at(f"{at}.shape"), f"{x['shape']} is not a shape the pack or the platform declares"))
        problems = plans.plan_problems(x["plan"])
        out += [Finding("S35", pack.at(f"{at}.plan.{loc}" if loc else f"{at}.plan"), msg) for loc, msg in problems]
        if not problems:
            out += [Finding("S35", pack.at(f"{at}.plan.{loc}"), f"{name} is not a {kind} the pack declares")
                    for loc, kind, name in plans.plan_names(x["plan"]) if not resolves[kind](name)]
        given = [k for k in ("approved_by", "approved_on") if k in x]
        stage = stages.get(x["shape"])
        if len(given) == 1:
            missing = "approved_on" if given == ["approved_by"] else "approved_by"
            out.append(Finding("S35", pack.at(at), f"names {given[0]} without {missing}"))
        elif not given and stage is not None and stage != "specify":
            out.append(Finding("S35", pack.at(at), f"is for {x['shape']}, at stage {stage}: above specify a worked example names "
                               "approved_by and approved_on"))
    return out


def clarifications(pack: Pack, preload: preloaded.Preload | None = None) -> list[Finding]:
    """S36. Ids unique within the pack are S02's."""
    d = pack.data
    out = gate(pack, "S36", ("clarifications",))
    rows = rows_of(d, "clarifications")
    if not rows:
        return out
    preload = preload or preloaded.load()
    for c, rule in rows:
        at = f"clarifications[{c}]"
        out += located(pack, names_known(rule["when"], f"{at}.when", preload, "S36"))
        if rule["message"] not in preload.clarification_texts:
            out.append(Finding("S36", pack.at(f"{at}.message"), f"{rule['message']} is not a clarification text the platform lists"))
        if rule["id"] in preload.clarifications:
            out.append(Finding("S36", pack.at(f"{at}.id"), f"{rule['id']} is a preloaded clarification rule's id: ids are unique "
                               "across the preload and the pack"))
    return out


def path_round(links: dict[str, list[str]], start: str) -> list[str] | None:
    """The shortest way round from start back to start by links, as the terms on it from start, or None. A term's link
    to itself is left out: S37 reports it as naming the term itself."""
    back: dict[str, str] = {}
    todo = [start]
    while todo:
        here = todo.pop(0)
        for there in links.get(here, []):
            if there == here:
                continue
            if there == start:
                way = [here]
                while way[-1] != start:
                    way.append(back[way[-1]])
                return way[::-1]
            if there not in back:
                back[there] = here
                todo.append(there)
    return None


def above(broader: dict[str, list[str]], term: str) -> list[str]:
    """Every term above term by broader links, nearest first, each once; a cycle ends the walk."""
    seen, order, todo = {term}, [], list(broader.get(term, []))
    while todo:
        here = todo.pop(0)
        if here in seen:
            continue
        seen.add(here)
        order.append(here)
        todo += broader.get(here, [])
    return order


def column_at(code: terms.Code) -> str:
    """The place of a code's column, beside the code's own."""
    return code.at.rsplit(".", 1)[0] + ".column"


def term_structure(pack: Pack) -> list[Finding]:
    """S37: the structure a glossary's is_a, broader and codes (meta-model 1.6.0) declare, against the glossary, the
    mappings, the joins, the vocabulary and the lock. A list that repeats an id is S01's, so each link counts once. A
    repeated term id is S02's: a term's name is its first declaration's, while its links and codes are those of every
    declaration. A cycle is reported once for the terms on it, the shortest from the first term in glossary order; a
    cycle through terms already reported shows once those are fixed. A term with an unresolved link, or of no class of
    its own or a broader ancestor's, has its coded columns' mappings left unchecked: F3 holds a member's coded column."""
    d, lock, out = pack.data, pack.lock, []
    declared: dict[str, dict] = {}
    for _, g in terms.glossary(d):
        term = terms.term_id(g)
        if term and term not in declared:
            declared[term] = g
    name = {t: g["name"] if terms.text(g.get("name")) else t for t, g in declared.items()}

    unresolved: set[str] = set()

    def resolved(rows, child, parent, field: str) -> dict[str, list[tuple[str, str]]]:
        links: dict[str, list[tuple[str, str]]] = {}
        for x in rows:
            a, b = getattr(x, child), getattr(x, parent)
            if any(b == y for y, _ in links.get(a, [])):
                continue
            if b == a:
                unresolved.add(a)
                out.append(Finding("S37", pack.at(x.at), f"names {a}, the term itself: a term's {field} names another term"))
            elif b not in declared:
                unresolved.add(a)
                out.append(Finding("S37", pack.at(x.at), f"{b} is not a term the glossary declares"))
            else:
                links.setdefault(a, []).append((b, x.at))
        return links
    is_a = resolved(terms.is_a(d), "member", "of", "is_a")
    broader = resolved(terms.broader(d), "child", "parent", "broader")
    class_of = {t: [c for c, _ in v] for t, v in is_a.items()}
    parents = {t: [p for p, _ in v] for t, v in broader.items()}
    for edge, links, plain in (("is_a", is_a, class_of), ("broader", broader, parents)):
        round_: set[str] = set()
        for term in declared:
            if term in round_ or not (way := path_round(plain, term)):
                continue
            round_ |= set(way)
            at = dict(links[term])[way[1]]
            out.append(Finding("S37", pack.at(at), f"closes a cycle of {edge}: {' -> '.join([*way, term])}; "
                               f"{edge} runs one way only"))
    for term, links in broader.items():
        held: dict[str | None, str] = {}
        for parent, at in links:
            kinds = class_of.get(parent) or [None]
            if shared := [c for c in kinds if c in held]:
                c = shared[0]
                where = f"{c}'s classification" if c else "no classification (neither has is_a)"
                out.append(Finding("S37", pack.at(at), f"{parent} is a second broader term for {term} in {where}: "
                                   f"{held[c]} is already; a term has one broader term in a classification"))
            for c in kinds:
                held.setdefault(c, parent)
    maps: dict[str, set[str]] = {}
    for m in d.get("mappings") or []:
        maps.setdefault(m["column"], set()).add(m["term"])
    coded: dict[str, list[terms.Code]] = {}
    blocked = restricted_columns(pack)
    for c in terms.codes(d):
        if locate(lock, c.column) is None:
            out.append(Finding("S37", pack.at(column_at(c)), f"{c.column} is not a column in pack.lock (source.table.column)"))
            continue
        if c.column in blocked:
            out.append(Finding("S37", pack.at(column_at(c)), f"{c.column} is restricted personal data: no term is coded on it"))
            continue
        coded.setdefault(c.term, []).append(c)
    for term, held in coded.items():
        line = [term, *above(parents, term)]
        kinds = [k for t in line for k in class_of.get(t, [])]
        if not kinds or unresolved & set(line):
            continue
        for c in held:
            if maps.get(c.column, set()) & set(kinds):
                continue
            mapped = ", ".join(sorted(maps.get(c.column, ()))) or "no term"
            out.append(Finding("S37", pack.at(column_at(c)), f"{c.column} maps to {mapped}, not to {term}'s class or a "
                               f"broader term's ({', '.join(dict.fromkeys(kinds))}): a coded column maps to the class its "
                               "codes are members of"))
    joined = {frozenset((j["from"], j["to"])) for j in d.get("joins") or [] if j["kind"] in ROW_JOINS}
    for term, links in broader.items():
        for parent, at in links:
            pairs = {(c.column.rsplit(".", 1)[0], p.column.rsplit(".", 1)[0]) for c in coded.get(term, [])
                     for p in coded.get(parent, [])}
            for low, high in sorted(pairs):
                if low != high and frozenset((low, high)) not in joined:
                    out.append(Finding("S37", pack.at(at), f"{term} is coded on {low} and {parent} on {high}, with no "
                                       "declared hierarchy or key join between them: the rows cannot show which is under which"))
    t = d.get("terminology") or {}
    words: dict[str, str] = {}
    for j, v in enumerate(t.get("value_vocabularies") or []):
        for value in v.get("values") or []:
            words.setdefault(value.casefold(), f"a value of terminology.value_vocabularies[{j}] ({value})")
    for j, x in enumerate(t.get("predicates") or []):
        for phrase in x["phrases"]:
            words.setdefault(phrase.casefold(), f"a phrase of terminology.predicates[{j}] ({phrase})")
    for i, g in terms.glossary(d):
        term = terms.term_id(g)
        if term in is_a and declared.get(term) is g and (said := words.get(name[term].casefold())):
            out.append(Finding("S37", pack.at(f"glossary[{i}].name"), f"{name[term]} is a member of a class and also {said}: "
                               "a member's name is the term's alone"))
    called: dict[str, set[str]] = {term: {name[term].casefold()} for term in declared}
    for a in t.get("aliases") or []:
        if a.get("term") in called:
            called[a["term"]].add(a["phrase"].casefold())
    first: dict[tuple[str, str], terms.Code] = {}
    for term, held in coded.items():
        for c in held:
            other = first.setdefault((c.column, c.code), c)
            if other.term != term:
                out.append(Finding("S37", pack.at(c.at), f"{c.column} {c.code} is {other.term}'s already ({other.at}): "
                                   "one code on one column names one term"))
    for j, pair in enumerate(t.get("code_name_pairs") or []):
        for term, held in coded.items():
            mine = {c.code: c for c in held if c.column == pair["code"]}
            if not mine:
                continue
            for k, m in enumerate(pair["members"]):
                at = f"terminology.code_name_pairs[{j}].members[{k}]"
                same_name = m["name"].casefold() in called[term]
                if m["code"] in mine and not same_name:
                    out.append(Finding("S37", pack.at(mine[m["code"]].at), f"{pair['code']} {m['code']} is {m['name']} at "
                                       f"{at}: {name[term]}, or an alias of it, is the name for that code"))
                elif same_name and m["code"] not in mine:
                    out.append(Finding("S37", pack.at(f"{at}.code"), f"{m['name']} is {term}, which codes "
                                       f"{', '.join(sorted(mine))} on {pair['code']}, not {m['code']}"))
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
    declarations = declared_findings(pack.folder)
    if any(f.path.partition(":")[0] != LOCK for f in pack.findings):
        return (identity(pack) if pack.data else []) + pack.findings + declarations
    found = identity(pack) + declarations
    said = {f.path for f in found}
    shaped = sections(pack)
    shaped += lock_shape(pack, sources={"sources", "curated_sources"}.isdisjoint({part(f) for f in shaped})) + pack.findings
    found += [f for f in shaped if f.path not in said]
    broken = {part(f) for f in shaped}
    later = (
        ((), lambda: ids(pack, [s for s in ID_SECTIONS if s not in broken])),
        (("manifest", "schema"), lambda: compatibility(pack, meta, compiler_version)),
        ((), lambda: fillable(meta, compiler_version, fills)),
        (("schema",), lambda: extensions(pack, meta)),
        ((LOCKED,), lambda: foreign_keys(pack, covered(pack) if "joins" not in broken else frozenset())),
        (("sources", "no_join"), lambda: no_join(pack)),
        (("glossary", "mappings", LOCKED), lambda: mappings(pack)),
        (("glossary", "measures", LOCKED), lambda: measures(pack)),
        (("glossary", "measures", "derived_measures", "count_metrics", "sources", "curated_sources", "contexts", "entities", "calendars", "joins", LOCKED),
         lambda: derived_measures(pack)),
        (("glossary", "measures", "derived_measures", "count_metrics", "banded_dimensions", "contexts", "entities", "sources", "curated_sources", "calendars", "joins", "filters", "row_selectors", "policies", LOCKED),
         lambda: count_metrics(pack)),
        (("calendars", "period_bindings", "measures", LOCKED), lambda: calendars(pack)),
        (("measures",), lambda: currencies(pack)),
        (("glossary", "sources", "terminology", "entities", LOCKED), lambda: aliases(pack)),
        (("terminology", "policies", LOCKED), lambda: vocabulary(pack)),
        (("banded_dimensions", "policies", LOCKED), lambda: banded_dimensions(pack)),
        (("sources", "access"), lambda: roles(pack, meta)),
        (("policies", LOCKED), lambda: restricted(pack)),
        (("joins", "relations", "entities", "curated_sources", "policies", LOCKED), lambda: joins(pack)),
        (("filters", "row_selectors", "policies", LOCKED), lambda: conditions(pack)),
        (("contexts", "glossary", "measures", "filters", "row_selectors", "joins", "tests", "calendars", "entities", "policies", LOCKED), lambda: contexts(pack)),
        (("acronyms", "entities", "relations", "lineage", "not_joinable", "ontology", "glossary", "no_join", LOCKED),
         lambda: references(pack)),
        (("certified_joins", "sources", "no_join", "joins"), lambda: certified(pack)),
        (("ontology_mappings", "ontology", "glossary", "entities", "policies", "manifest"), lambda: ontology_mappings(pack)),
        (("policies", LOCKED), lambda: time_policies(pack)),
        (("runtimes", "default_runtimes", "measures", "manifest", LOCKED), lambda: runtimes(pack)),
        (("phrases", "terminology", "acronyms", "manifest"), lambda: phrases(pack)),
        (("shapes", "shape_tests", "recipes", "clarifications", "examples", "tests", "manifest"), lambda: shapes(pack)),
        (("recipes", "manifest", *NAMED_IN, LOCKED), lambda: recipes(pack)),
        (("examples", "shapes", "manifest", *NAMED_IN, LOCKED), lambda: examples(pack)),
        (("clarifications", "manifest"), lambda: clarifications(pack)),
        (("glossary", "mappings", "joins", "terminology", "policies", LOCKED), lambda: term_structure(pack)),
    )
    for reads, run in later:
        if broken.isdisjoint(reads):
            found += run()
    return found
