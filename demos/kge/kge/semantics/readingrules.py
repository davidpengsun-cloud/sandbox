"""Generic reading rules over a pack's own declarations (#707): names, aliases, grouping phrases, filter phrases and
the start of the year a year label names. Nothing here names a client, a term, a dimension or a value: each rule is tested on a synthetic
pack beside the demo client's own (tests/unit/test_reading_rules.py).

  meanings            the terms a name resolves to: the term an alias names, and every term whose name or alias ends in
                      the name's words (#707 requirement 2, map_term and the term item)
  grouping_options    the declared grouping dimensions an unread grouping word names: the one a declared phrase it spells
                      with other separators names, else those whose declared phrases or names hold it word-bounded as
                      the rules match a grouping phrase, hyphens and spaces alike (decision 20); never a description
  column_label        a column's business label for every reply: business name, else description, else its name
  filter_phrases      the declared filter phrases a query names (requirement 1, search)
  labelled_year_window  a year label's first and last day from the month the pack's labelled year starts in
                        (decision 19)
"""
from __future__ import annotations

import datetime
import re

WORD = re.compile(r"[^\W_]+")


def phrase_words(text: str) -> tuple[str, ...]:
    """A phrase's words, case folded, as the search and the rules compare phrases."""
    return tuple(WORD.findall(str(text or "").casefold()))


def meanings(phrase: str, names: dict[str, list[str]]) -> list[dict]:
    """The terms a name resolves to, by the pack's own names and aliases alone, case folded. names: each readable
    term's name and its aliases. A term is the phrase's own when the phrase is its name or one of its aliases (an
    alias's term is the default the alias binds: default_alias); and a term is a further meaning when its name or one
    of its aliases ends in the phrase's words and is longer than it. A phrase that names no term and ends none
    resolves to nothing. The phrase's own terms come first, then the others by name."""
    asked = phrase_words(phrase)
    if not asked:
        return []
    found: dict[str, dict] = {}
    for name, aliases in names.items():
        alias = any(phrase_words(a) == asked for a in aliases)
        own = alias or phrase_words(name) == asked
        ends = any(len(words) > len(asked) and words[-len(asked):] == asked
                   for words in map(phrase_words, (name, *aliases)))
        if own or ends:
            found[name] = {"term": name, "default_alias": alias, "named": own}
    return sorted(found.values(), key=lambda item: (not item["named"], item["term"].casefold()))


def spaced(text: str) -> str:
    """A phrase with its hyphens read as spaces, case folded: day-part and day part alike."""
    return " ".join(phrase_words(text))


def joined(text: str) -> str:
    """A phrase with no separators at all, case folded: day-part, day part and daypart alike."""
    return "".join(phrase_words(text))


def word_in(word: str, phrase: str) -> bool:
    """Whether word stands in phrase as whole words, as the rules match a grouping phrase in a question
    (r"\b" + phrase + r"\b"), with hyphens and spaces alike."""
    return bool(spaced(word)) and re.search(r"\b" + re.escape(spaced(word)) + r"\b", spaced(phrase)) is not None


def grouping_options(word: str, phrases: dict[str, str], labels: dict[str, str], operator: str = "",
                     names: dict[str, str] | None = None) -> list[dict]:
    """The declared grouping dimensions an unread grouping word names (decision 20). phrases: each declared grouping
    phrase and the dimension it groups by; labels: each dimension's label, shown and never matched; operator: the
    grouping word before it (by); names: each dimension's declared name, where the pack gives one.

    A declared phrase or name the words spell with other separators (day part or daypart for a declared day-part,
    with or without the operator) names its dimension alone. Otherwise every dimension one of whose declared phrases,
    or whose declared name, holds the words as whole words, hyphens and spaces alike. One option a dimension, with its
    label and its declared phrases, by label."""
    names = names or {}
    said = {joined(word), joined(f"{operator} {word}")} - {""}
    declared = {**{p: d for p, d in phrases.items()}, **{n: d for d, n in names.items() if n and d in phrases.values()}}
    spelled = {dimension for phrase, dimension in declared.items() if joined(phrase) in said}
    found = spelled or {dimension for phrase, dimension in declared.items() if word_in(word, phrase)}
    options = []
    for dimension in found:
        own = sorted({p for p, d in phrases.items() if d == dimension})
        options.append({"dimension": dimension, "label": labels.get(dimension) or (own[0] if own else dimension),
                        "phrases": own})
    return sorted(options, key=lambda option: (option["label"].casefold(), option["dimension"]))


def column_label(column: str, business: dict[str, str], descriptions: dict[str, str]) -> str:
    """A column's business label, one rule for every reply: the pack's business name for it, else its catalogue
    description, else its bare name."""
    return business.get(column) or descriptions.get(column) or column.rsplit(".", 1)[-1]


def filter_phrases(query: str, rows: list[dict]) -> list[dict]:
    """The declared filter phrases a query names: each row is {phrase, column, value}; a phrase matches when its words
    are the query's (score 1), or one holds the other's words in order (.9). The best match of each row's phrase."""
    asked = " ".join(phrase_words(query))
    if not asked:
        return []
    hits = []
    for row in rows:
        said = " ".join(phrase_words(row["phrase"]))
        if not said:
            continue
        if said == asked:
            score = 1.0
        elif f" {said} " in f" {asked} " or f" {asked} " in f" {said} ":
            score = .9
        else:
            continue
        hits.append({**row, "score": score})
    return hits


def labelled_year_window(value: str, start_month: int) -> tuple[datetime.date, datetime.date]:
    """A year label's first and last day, from the month the pack's labelled year starts in (its calendar whose quarters
    carry an FY label). value is the label as the parser gives it: FY25, FY2025 or 2025. A year that starts in January
    is its calendar year; one that starts later is named for the calendar year it ends in, as a quarter label is
    (FY26-Q1 on a year from April is 2025-Q2)."""
    digits = str(value).upper().removeprefix("FY")
    year = 2000 + int(digits) if len(digits) == 2 else int(digits)
    first = datetime.date(year - (start_month > 1), start_month, 1)
    after = datetime.date(first.year + 1, first.month, 1)
    return first, after - datetime.timedelta(days=1)
