"""The question lexer's phrase table: each operation phrase with the operation it marks, and the filler words and
phrases the lexer reads past.

Baseline v1 is the coded table (kge/semantics/coverage.py OPERATORS, SCAFFOLD and SCAFFOLD_PHRASES) frozen in the
platform preload (standards/preload.yaml), every row marked `baseline: true` (question-shapes §8.1). The compiler loads
it into every graph as Phrase rows, and the engine reads them once per release (Graph.vocabulary().phrases).

Until B7 (#455) the coded rules (planrules.read) read a table equal to the constant baseline, never one a graph
extends beyond it; the graph rules, coverage and the readable-words check read the graph's table, a pack's phrases
included. The rules that depend on the question (`which` set aside beside a rank
word, `net`, `between`, `lowest`/`highest` as an aggregation, the ranking partitions) stay in the lexer and work on a
copy of the table, never on the table itself.

Known limit until B7: the period readers in kge/services/ask/engine/question.py (previous-period masking, the to-date
cue and period evidence) still build their patterns from coverage.OPERATORS. A later non-baseline phrase marking ytd,
mtd, qtd, previous_period or previous_year would read as an operator piece on the graph side and be missed by them, so
such a phrase waits for those readers to take the table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Iterable, Mapping


@dataclass(frozen=True, eq=False)
class PhraseTable:
    """One phrase table. operators maps each operation phrase to its operation, in the order the lexer reads them;
    filler holds single filler words and filler_phrases the filler phrases of more than one word; baseline holds the
    phrases of baseline v1. source says where the table was read ("constant" or "graph") and is no part of equality."""
    operators: Mapping[str, str]
    filler: frozenset
    filler_phrases: tuple
    baseline: frozenset
    source: str = "constant"

    @classmethod
    def constant(cls, operators: Mapping[str, str], scaffold: Iterable[str], scaffold_phrases: Iterable[str]) -> PhraseTable:
        """The coded table, every phrase in baseline v1, keeping the given orders so the lexer's patterns are the same
        bytes as the coded ones."""
        operators = dict(operators)
        filler, phrases = frozenset(scaffold), tuple(scaffold_phrases)
        return cls(MappingProxyType(operators), filler, phrases, frozenset(operators) | filler | frozenset(phrases), "constant")

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping], source: str = "graph") -> PhraseTable:
        """A table from Phrase rows (phrase, operation, filler, baseline), whatever their order. A row breaking the
        preload's rules (an empty or upper-case phrase, a phrase given twice, a row neither marking an operation nor
        filler) is a platform defect and raises ValueError. Operations keep the phrases' sorted order; filler phrases of
        more than one word are kept longest first."""
        seen: dict[str, Mapping] = {}
        for row in rows:
            phrase = row.get("phrase")
            if not isinstance(phrase, str) or not phrase.strip() or phrase != phrase.lower() or phrase != phrase.strip():
                raise ValueError(f"phrase {phrase!r} is lower-case text")
            if phrase in seen:
                raise ValueError(f"phrase {phrase!r} is given twice")
            operation = row.get("operation")
            if operation is not None and (not isinstance(operation, str) or not operation):
                raise ValueError(f"phrase {phrase!r} names an operation by its name")
            if operation is None and row.get("filler") is not True:
                raise ValueError(f"phrase {phrase!r} marks an operation or is filler")
            seen[phrase] = row
        operators, filler, phrases, baseline = {}, set(), [], set()
        for phrase in sorted(seen):
            row = seen[phrase]
            if row.get("operation") is not None:
                operators[phrase] = row["operation"]
            if row.get("filler") is True and re.search(r"\s", phrase):
                phrases.append(phrase)
            elif row.get("filler") is True:
                filler.add(phrase)
            if row.get("baseline") is True:
                baseline.add(phrase)
        return cls(MappingProxyType(operators), frozenset(filler), tuple(sorted(phrases, key=lambda p: (-len(p), p))),
                   frozenset(baseline), source)

    def rows(self) -> tuple[dict, ...]:
        """The table as Phrase rows, sorted by phrase: {phrase, operation?, filler?: True, baseline?: True}."""
        every = set(self.operators) | self.filler | set(self.filler_phrases)
        return tuple({"phrase": p, **({"operation": self.operators[p]} if p in self.operators else {}),
                      **({"filler": True} if p in self.filler or p in self.filler_phrases else {}),
                      **({"baseline": True} if p in self.baseline else {})} for p in sorted(every))

    @cached_property
    def operator_phrases(self) -> frozenset:
        """Every operation phrase."""
        return frozenset(self.operators)

    @cached_property
    def rank_words(self) -> tuple[str, ...]:
        """The ranking phrases (asc, then desc), in the table's order, as coverage.RANK_WORDS is built."""
        return tuple(phrase for operation in ("asc", "desc") for phrase, op in self.operators.items() if op == operation)

    @cached_property
    def rank_pattern(self) -> str:
        """The ranking phrases as one alternation, the longest first, as coverage.RANK_PATTERN is built; a table with no
        ranking phrase matches nothing."""
        if not self.rank_words:
            return r"(?!)"
        return r"(?:" + "|".join(re.escape(word) for word in sorted(self.rank_words, key=len, reverse=True)) + r")"

    @cached_property
    def new(self) -> frozenset:
        """Every phrase not in baseline v1."""
        return (frozenset(self.operators) | self.filler | frozenset(self.filler_phrases)) - self.baseline

    @property
    def extended(self) -> bool:
        """Whether the table holds a phrase beyond baseline v1."""
        return bool(self.new)

    @cached_property
    def key(self) -> tuple:
        """What the table reads as, without its orders or its source: what equality compares."""
        return (frozenset(self.operators.items()), self.filler, frozenset(self.filler_phrases), self.baseline)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, PhraseTable):
            return NotImplemented
        return self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __repr__(self) -> str:
        return (f"PhraseTable(source={self.source!r}, operators={len(self.operators)}, filler={len(self.filler)}, "
                f"filler_phrases={len(self.filler_phrases)}, new={len(self.new)})")
