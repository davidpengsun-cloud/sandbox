"""The question parser's vocabulary beyond its aliases, as a pack's terminology gives it (meta-model 1.2): the closed
set the platform knows, which the compiler checks a pack against (S29), and the Vocabulary rows it emits.

  intents             the phrases that mark a question's intent, each intent once (INTENTS)
  value_vocabularies  a column's values: those listed, or a code of a pattern the platform knows (PATTERNS)
  predicates          a condition a phrase sets on a column, by an operator the platform knows (OPS): in or not_in, with values,
                      names a group of two or more values, all text or all numbers, each once
  functional_mailboxes
                      the local parts of the mailboxes that name a function, not a person (MAILBOX), in lower case
                      as the privacy rules compare them

kge/semantics/vocabulary.py turns them into the patterns the runtime's parser reads, and a predicate's operator into what the
planner writes (kge/services/ask/engine/core.py plan_unit). A pack gives words, never a regular expression: a value pattern is a code the
platform names. Names no client.

"""
from __future__ import annotations

import re

PATTERNS = {"upper_2": "[A-Z]{2}", "upper_2_3": "[A-Z]{2,3}", "upper_4_plus": "[A-Z]{4,}", "upper_letter": "[A-Z]"}
OPS = {"eq": "=", "ne": "<>", "gt": ">", "lt": "<", "ge": ">=", "le": "<=", "is_null": "IS NULL",
       "is_not_null": "IS NOT NULL", "is_true": "= TRUE", "in": "IN", "not_in": "NOT IN",
       "contains": "CONTAINS", "starts_with": "STARTS WITH"}
NEEDS_VALUE = ("eq", "ne", "gt", "lt", "ge", "le", "contains", "starts_with")
NEEDS_VALUES = ("in", "not_in")
INTENTS = ("definitional", "count", "avg")
MAILBOX = re.compile(r"[a-z0-9][a-z0-9._+-]*")
KINDS = ("intent", "value", "predicate", "mailbox", "code_name_pair", "measure_options", "derived_measure", "moving_average", "count_metric", "join_options", "calendar_options", "band_options")
AGGREGATION_CHOICES = ("sum", "count", "avg", "min", "max", "median")
ARITHMETIC = ("ratio", "difference", "percent_change", "share")
JOIN_TYPES = ("inner", "left", "right", "full")
JOIN_CARDINALITIES = ("one-to-one", "one-to-many")
DEFAULT_MOVING_AVERAGE_LENGTHS = (2, 3, 6, 12)
ALIAS_KINDS = ("term", "source", "candidate", "group_by", "entity", "time_mode", "period", "column", "column_wording", "sources")


def band_entries(pack: dict) -> list[dict]:
    """Range declarations travel through the existing extensible vocabulary."""
    return [{'id': 'band-options:' + band['id'], 'kind': 'band_options', 'ordinal': n, 'params': dict(band)}
            for n, band in enumerate(pack.get('banded_dimensions', ()), 1)]


def join_entries(pack: dict) -> list[dict]:
    """Join contracts share the existing extensible vocabulary without changing graph record fields."""
    return [{"id": "join-options:" + join["id"], "kind": "join_options", "ordinal": n,
             "params": {"join": join["id"], **{k: join[k] for k in ("type", "validity_window", "cardinality") if k in join}}}
            for n, join in enumerate(pack.get("joins", []), 1)
            if any(k in join for k in ("type", "validity_window", "cardinality"))]


def alias_declarations(terminology: dict):
    """Every phrase's single target, with its declaration and wording locations.

    A concept declares its target once and gives all the wordings that name it.
    Expanding it here gives the existing Alias graph the same complete vocabulary
    as individual aliases, without a second parser or a new graph node type.
    """
    for i, alias in enumerate(terminology.get("aliases") or []):
        at = f"terminology.aliases[{i}]"
        yield at, at + ".phrase", alias
    for i, concept in enumerate(terminology.get("concepts") or []):
        at = f"terminology.concepts[{i}]"
        target = {kind: concept[kind] for kind in ALIAS_KINDS if kind in concept}
        for j, phrase in enumerate(concept["wordings"]):
            yield at, f"{at}.wordings[{j}]", {"phrase": phrase, **target}
        for j, qualified in enumerate(concept.get("qualified_terms", ())):
            qualified_at = f"{at}.qualified_terms[{j}]"
            if not qualified["noun"].strip():
                continue
            for phrase in concept["wordings"]:
                yield qualified_at, qualified_at + ".noun", {
                    "phrase": phrase.strip() + " " + qualified["noun"].strip(), "term": qualified["term"]}


def alias_entries(terminology: dict) -> list[dict]:
    """The expanded Alias rows in deterministic declaration order."""
    return [alias for _, _, alias in alias_declarations(terminology)]


def entries(terminology: dict, measures=()) -> list[dict]:
    """Terminology and per-measure window policies as Vocabulary entries.

    Window policies have measure-based names, independent of terminology ordinals.
    """
    out = []
    for kind, slot in (("intent", "intents"), ("value", "value_vocabularies"), ("predicate", "predicates"), ("code_name_pair", "code_name_pairs")):
        out += [{"id": f"{kind}-{n}", "kind": kind, "params": dict(x), "ordinal": n} for n, x in enumerate(terminology.get(slot) or [], 1)]
    out += [{"id": f"mailbox-{n}", "kind": "mailbox", "params": {"mailbox": m}, "ordinal": n}
            for n, m in enumerate(terminology.get("functional_mailboxes") or [], 1)]
    out += [{"id": "moving-average:" + m["id"], "kind": "moving_average",
             "params": {"measure": m["id"], "lengths": m["moving_average_lengths"]}, "ordinal": n}
            for n, m in enumerate(measures, 1) if "moving_average_lengths" in m]
    return out


def measure_entries(pack: dict) -> list[dict]:
    """Optional measure declarations use stable Vocabulary ids without moving terminology entries."""
    names = {g["id"]: g["name"] for g in pack.get("glossary", [])}
    out = [{"id": "measure-options:" + m["id"], "kind": "measure_options", "ordinal": n,
            "params": {"measure": m["id"], **{k: m[k] for k in ('aggregations', 'empty') if k in m}}}
           for n, m in enumerate(pack.get("measures", []), 1) if "aggregations" in m or "empty" in m]
    out += [{"id": "derived-measure:" + m["id"], "kind": "derived_measure", "ordinal": n,
             "params": {**m, "label": names[m["term"]]}}
            for n, m in enumerate(pack.get("derived_measures", []), 1)]
    out += [{"id": "count-metric:" + m["id"], "kind": "count_metric", "ordinal": n,
             "params": {**m, "label": names[m["term"]]}}
            for n, m in enumerate(pack.get("count_metrics", []), 1)]
    return out


def calendar_entries(pack: dict) -> list[dict]:
    """Calendar policies travel through the existing extensible vocabulary on every graph adapter."""
    return [{'id': 'calendar-options:' + c['id'], 'kind': 'calendar_options', 'ordinal': n,
             'params': {'calendar': c['id'], **{k: c[k] for k in ('week_start', 'spine') if k in c}}}
            for n, c in enumerate(pack.get('calendars', []), 1) if 'week_start' in c or 'spine' in c]
