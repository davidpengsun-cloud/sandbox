"""The question parser's vocabulary beyond its aliases, as a pack's terminology gives it (meta-model 1.2): the closed
set the platform knows, which the compiler checks a pack against (S29), and the Vocabulary rows it emits.

  intents             the phrases that mark a question's intent, each intent once (INTENTS)
  value_vocabularies  a column's values: those listed, or a code of a pattern the platform knows (PATTERNS)
  predicates          a condition a phrase sets on a column, by an operator the platform knows (OPS)
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
OPS = {"eq": "=", "gt": ">", "lt": "<", "is_null": "IS NULL", "is_true": "= TRUE"}
NEEDS_VALUE = ("eq", "gt", "lt")
COMPARES = ("gt", "lt")
INTENTS = ("definitional", "count", "avg")
MAILBOX = re.compile(r"[a-z0-9][a-z0-9._+-]*")
KINDS = ("intent", "value", "predicate", "mailbox")


def entries(terminology: dict) -> list[dict]:
    """The pack's terminology as Vocabulary entries, {id, kind, params, ordinal}, each kind in the pack's order: what the
    compiler emits, and the graph holds."""
    out = []
    for kind, slot in (("intent", "intents"), ("value", "value_vocabularies"), ("predicate", "predicates")):
        out += [{"id": f"{kind}-{n}", "kind": kind, "params": dict(x), "ordinal": n} for n, x in enumerate(terminology.get(slot) or [], 1)]
    out += [{"id": f"mailbox-{n}", "kind": "mailbox", "params": {"mailbox": m}, "ordinal": n}
            for n, m in enumerate(terminology.get("functional_mailboxes") or [], 1)]
    return out
