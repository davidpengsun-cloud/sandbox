"""The question parser's vocabulary beyond its aliases, as a client's pack gives it (meta-model 1.2).

A pack's terminology says, beside the phrases that name a term or a unit:

  intents             the phrases that mark a question's intent: a definitional question opens with one
                      (definitional); a count or an average says one anywhere (count, avg)
  value_vocabularies  a column's values a question filters on: one of the values listed, as written, or a code of a
                      pattern the platform knows (PATTERNS), after a prefix the value begins with; after one of the
                      phrases given and a space ("region NW"), or before a space and one of them ("GROWTH risk
                      profile")
  predicates          a condition one of the phrases sets on a column (OPS), whatever the case; with "with", only where
                      one of those words is in the question too, before or after the phrase
  functional_mailboxes
                      the local parts of the mailboxes that name a function, not a person (kge/pipeline/privacy.py)

The compiler checks them (S29) and emits each as a Vocabulary node, its params the entry as the pack gives it; the
runtime reads them from the graph once (kge/services/ask/engine/graphview.py Graph.domain) and compiled() turns them into the patterns the
parser reads (kge/services/ask/engine/question.py parse). A phrase or a value is text, never a regular expression: it is matched as written,
the longest first, with no letter, digit or underscore either side of it (on word boundaries, for one that begins and
ends with a word's character, as the code's patterns had it). The patterns, operators and intents are the platform's
closed set, which the compiler checks a pack against (kge/compiler/vocabulary.py). A filter names a column,
<source>.<table>.<column>; the parser gives the column's name, and the planner filters on the column of that name in
the unit it plans, as it always has (a context's query, only in the unit of the column the filter names: columns). The
value filters come first, in the pack's order, then the predicates, in theirs.

Names no client: every word here is the pack's.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from kge.compiler.vocabulary import INTENTS, OPS, PATTERNS

__all__ = ["INTENTS", "OPS", "PATTERNS", "Vocabulary", "compiled"]
START, END = r"(?<!\w)", r"(?!\w)"


class Vocabulary(NamedTuple):
    """The compiled vocabulary: each filter as (regex, column, operator, capture group or None, literal); by intent, a
    pattern that matches a definitional question at its start; the functional mailboxes; and the column each filter
    names, as <source>.<table>.<column>."""
    filters: list[tuple[re.Pattern, str, str, int | None, object]]
    intents: dict[str, re.Pattern]
    mailboxes: frozenset[str]
    columns: tuple[str, ...] = ()


def alternatives(phrases) -> str:
    """Phrases as one alternation, each matched as written, the longest first."""
    return "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))


def value_filter(v: dict) -> tuple[re.Pattern, str, str, int, None]:
    """A value vocabulary's filter: the value captured, matched on the question as written (case counts)."""
    if v.get("values"):
        value = alternatives(v["values"])
    else:
        value = re.escape(v.get("prefix") or "") + PATTERNS[v["pattern"]]
    rx = START + (rf"(?:{alternatives(v['after'])}) " if v.get("after") else "") + f"({value})" + \
         (rf" (?:{alternatives(v['before'])})" if v.get("before") else "") + END
    return re.compile(rx), column_name(v["column"]), "=", 1, None


def predicate_filter(p: dict) -> tuple[re.Pattern, str, str, None, object]:
    """A predicate's filter: its condition, matched on the question whatever the case."""
    said = rf"{START}(?:{alternatives(p['phrases'])}){END}"
    if p.get("with"):
        with_ = rf"{START}(?:{alternatives(p['with'])}){END}"
        said = f"(?:{said}.*{with_}|{with_}.*{said})"
    return re.compile(said, re.I), column_name(p["column"]), OPS[p["op"]], None, p.get("value")


def intent_pattern(intent: str, phrases) -> re.Pattern:
    """A definitional question opens with one of the phrases; a count or an average says one anywhere."""
    said = rf"(?:{alternatives(phrases)}){END}"
    return re.compile(("^" if intent == "definitional" else START) + said, re.I)


def column_name(column: str) -> str:
    return column.rsplit(".", 1)[-1]


def compiled(rows: list[dict]) -> Vocabulary:
    """The parser's patterns from the Vocabulary entries ({kind, params}), each kind in the pack's order."""
    of = lambda kind: [r["params"] for r in rows if r["kind"] == kind]  # noqa: E731
    filters = [value_filter(v) for v in of("value")] + [predicate_filter(p) for p in of("predicate")]
    intents = {i["intent"]: intent_pattern(i["intent"], i["phrases"]) for i in of("intent")}
    columns = tuple(x["column"] for x in [*of("value"), *of("predicate")])
    return Vocabulary(filters, intents, frozenset(m["mailbox"] for m in of("mailbox")), columns)
