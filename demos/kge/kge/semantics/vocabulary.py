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
                      A modifier predicate applies immediately before one of its with phrases; its label names the
                      resulting measure basis.
                      An in predicate's phrase sets one IN over its values (a group's name). Two or more of a column's
                      eq values said as a list, joined by and, or or commas, with the noun its phrases share once or
                      with each value ("A and B <nouns>"), set one IN too (list_filters); values of
                      different columns are never merged, nor a list that reads either way (coordinated).
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
from dataclasses import dataclass
from typing import NamedTuple

from kge.compiler.vocabulary import INTENTS, OPS, PATTERNS

__all__ = ["INTENTS", "OPS", "PATTERNS", "Listed", "Vocabulary", "compiled", "coordinated", "filter_value", "within_list"]
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
    """A predicate's filter: its condition, matched on the question whatever the case; an in predicate's literal is its
    values, in the pack's order."""
    said = rf"{START}(?:{alternatives(p['phrases'])}){END}"
    if p.get("with"):
        with_ = rf"{START}(?:{alternatives(p['with'])}){END}"
        said = rf"{said}\s+{with_}" if p.get("modifier") else f"(?:{said}.*{with_}|{with_}.*{said})"
    return re.compile(said, re.I), column_name(p["column"]), OPS[p["op"]], None, tuple(p["values"]) if p["op"] in ("in", "not_in") else p.get("value")


@dataclass(frozen=True)
class Listed:
    """The values a coordinated list of a column's declared values names: each value's words, in lower case, and the
    value they stand for. Not a tuple, so a frozen copy of the domain keeps it whole."""
    words: tuple[tuple[str, object], ...]
    excluded: bool = False

    def values(self, said: str) -> tuple:
        """The values said, in the order the question says them, each once."""
        lookup = dict(self.words)
        rx = re.compile(START + f"({alternatives(w for w, _ in self.words)})" + END, re.I)
        return tuple(dict.fromkeys(lookup[m.group(1).casefold()] for m in rx.finditer(said)))


SEPARATOR = r"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
EXCLUSION = r"(?:excluding|exclude|excluded|except|other\s+than|not\s+in|outside|apart\s+from|without)"


@dataclass(frozen=True)
class Excluded:
    """A single declared value read after an exclusion cue."""
    literal: object


def exclusion_filters(filters):
    """Apply the exclusion grammar once to every declared equality and membership filter."""
    from dataclasses import replace
    return [(re.compile(START + '(?i:' + EXCLUSION + r"\s+(?:the\s+)?)" + rx.pattern, rx.flags), col, "NOT IN", group,
             replace(value, excluded=True) if isinstance(value, Listed) else Excluded(value))
            for rx, col, op, group, value in filters if op in ("=", "IN")]


def noun_forms(noun: tuple[str, ...]) -> tuple[str, str]:
    """A filter noun's words as a pattern, singular and plural (its last word's plural)."""
    from kge.services.ask.engine.question import plural_forms
    head = [re.escape(w) for w in noun[:-1]]
    return tuple(r"\s+".join([*head, re.escape(form)]) for form in plural_forms(noun[-1]))


def shared_nouns(predicates: list[dict]) -> dict[tuple[str, str, tuple[str, ...]], dict[str, object]]:
    """Each column's filter nouns: the words its eq phrases share before (noun first) or after (noun last) the words that
    name a value, where two or more values share them and each value's words name one value; a noun another of the
    column's nouns extends on the same side is left out. Keyed (column, side, noun), each the value words and their
    values."""
    groups: dict[tuple[str, str, tuple[str, ...]], dict[str, set]] = {}
    for p in predicates:
        if p["op"] != "eq" or p.get("with") or p.get("modifier") or isinstance(p.get("value"), bool):
            continue
        for phrase in p["phrases"]:
            words = phrase.casefold().split()
            for k in range(1, len(words)):
                for side, noun, said in (("first", words[:k], words[k:]), ("last", words[k:], words[:k])):
                    groups.setdefault((p["column"], side, tuple(noun)), {}).setdefault(" ".join(said), set()).add(p["value"])
    kept = {key: {w: next(iter(v)) for w, v in said.items()} for key, said in groups.items()
            if all(len(v) == 1 for v in said.values()) and len({next(iter(v)) for v in said.values()}) >= 2}

    def extended(key):
        column, side, noun = key
        return any(c == column and s == side and len(n) > len(noun) and (n[:len(noun)] if side == "first" else n[-len(noun):]) == noun
                   for c, s, n in kept)
    return {key: said for key, said in kept.items() if not extended(key)}


def list_filters(predicates: list[dict]) -> list[tuple[tuple[re.Pattern, str, str, None, Listed], str]]:
    """The filters a coordinated list of one column's declared values sets, IN those values, each with its column: two or
    more value words joined by and, or or commas, with the column's noun once for all (singular or plural) or with
    each value ("A and B <nouns>", "A or B <noun>", "A <noun> and B <noun>", "<nouns> A and B")."""
    out = []
    for (column, side, noun), said in shared_nouns(predicates).items():
        value = f"(?:{alternatives(said)})"
        singular, plural = noun_forms(noun)
        nouns = f"(?:{plural}|{singular})"
        if side == "last":
            each = rf"{value}(?:\s+{singular})?"
            rx = rf"{START}{each}(?:{SEPARATOR}{each})*{SEPARATOR}{value}\s+{nouns}{END}"
        else:
            each = rf"(?:{singular}\s+)?{value}"
            rx = rf"{START}{nouns}\s+{value}(?:{SEPARATOR}{each})+{END}"
        listed = Listed(tuple(sorted(said.items(), key=lambda item: (-len(item[0]), item[0]))))
        out.append(((re.compile(rx, re.I), column_name(column), "IN", None, listed), column))
    return out


def coordinated(text: str, filters, columns=(), eligible=None) -> frozenset[tuple[int, int, int]]:
    """The coordinated lists a question names, (filter index, start, end): each list filter's matches, less any that
    overlaps another list without one holding the other, which reads as two columns' values ("<noun> A and B
    <other noun>"), so neither is merged. With eligible, which says whether a column (<source>.<table>.<column>, as
    columns gives each filter's) may be read here, a list is also left out where a filter of another readable column
    matches inside it ("A and B <noun>" where A alone filters another column): the words read as that column's filter
    and the next value, as they did before lists were read."""
    found = [(i, *m.span()) for i, (rx, _, _, _, literal) in enumerate(filters) if isinstance(literal, Listed)
             for m in rx.finditer(text)]
    kept = [f for f in found if not any(
        a < f[2] and f[1] < b and not (a <= f[1] and f[2] <= b) and not (f[1] <= a and b <= f[2]) for _, a, b in found)]
    if eligible is None:
        return frozenset(kept)

    def column(i):
        return columns[i] if i < len(columns) else None

    def crossed(f):
        listed = {column(i) for i, a, b in kept if (a, b) == f[1:]}
        return any(not isinstance(literal, Listed) and column(j) not in listed and eligible(column(j))
                   and any(f[1] <= m.start() and m.end() <= f[2] for m in rx.finditer(text))
                   for j, (rx, _, _, _, literal) in enumerate(filters))
    return frozenset(f for f in kept if not crossed(f))


def within_list(span: tuple[int, int], lists) -> bool:
    """Whether a filter's match lies inside a coordinated list, and is not the list itself: the list's one IN filter
    stands for each value it names."""
    return any(start <= span[0] and span[1] <= end and span != (start, end) for _, start, end in lists)


def filter_value(match: re.Match, group: int | None, literal):
    """What a matched filter sets the column to: the value captured, the values a coordinated list names, or the
    filter's literal."""
    if isinstance(literal, Listed):
        return literal.values(match.group(0))
    if isinstance(literal, Excluded):
        value = match.group(group) if group else literal.literal
        return value if isinstance(value, tuple) else (value,)
    return match.group(group) if group else literal


def intent_pattern(intent: str, phrases) -> re.Pattern:
    """A definitional question opens with one of the phrases; a count or an average says one anywhere."""
    said = rf"(?:{alternatives(phrases)}){END}"
    return re.compile(("^" if intent == "definitional" else START) + said, re.I)


def column_name(column: str) -> str:
    return column.rsplit(".", 1)[-1]


def compiled(rows: list[dict]) -> Vocabulary:
    """The parser's patterns from the Vocabulary entries ({kind, params}), each kind in the pack's order."""
    of = lambda kind: [r["params"] for r in rows if r["kind"] == kind]  # noqa: E731
    listed = list_filters(of("predicate"))
    filters = [value_filter(v) for v in of("value")] + [predicate_filter(p) for p in of("predicate")] + [f for f, _ in listed]
    intents = {i["intent"]: intent_pattern(i["intent"], i["phrases"]) for i in of("intent")}
    columns = tuple(x["column"] for x in [*of("value"), *of("predicate")]) + tuple(c for _, c in listed)
    excluded = [(f, c) for f, c in zip(filters, columns) if f[2] in ("=", "IN")]
    filters += exclusion_filters([f for f, _ in excluded])
    columns += tuple(c for _, c in excluded)
    return Vocabulary(filters, intents, frozenset(m["mailbox"] for m in of("mailbox")), columns)
