"""Slot binding the rules adapters share (docs/use-cases/kge/platform/question-shapes.md §3.1 and §4.3).

Binding a question's names to declarations stays platform code whichever rules adapter plans the question: contexts,
measures, units, entities, dimensions, calendars and filters are bound by `planrules.read`, and the pieces they are
bound from are the lexer's (`kge.semantics.coverage.ledger`). This module holds the parts of that binding a question
shape's template reads.

- `shared_contexts` binds a question's contexts in span order. A bare "current" side takes the unqualified noun of the
  other side. It is invoked whenever two contexts are needed: by the coded planner where a comparison word is present,
  as before this module held it, and with `paired=True` for a template that needs two context slots, so a template
  without "vs" binds both sides.
- `slots` reads the slot kinds a template counts (measure, context, entity, dimension, period, range, number,
  comparison_op, direction) in span order, binds a lexed window as one `range` slot, never two periods, and collects
  the lexer markers (`kge.semantics.coverage.MARKERS`) the pieces carry.
- `HELD` names each coded builder a marker cannot reproduce, with the reason (`kge.compiler.standards.HELD_BUILDERS`,
  which the preload's check and S33 read). Such a builder stays coded, keeps its fallback and is never retired: a shape
  that replaces it is refused at `retired`, and `retirable` says so here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

from kge.compiler.standards import HELD_BUILDERS
from kge.semantics.coverage import MARKERS, unqualified_contexts

SLOT_KINDS = ('measure', 'context', 'entity', 'dimension', 'period', 'range', 'number', 'comparison_op', 'direction')
COMPARISON_OPS = ('gt', 'ge', 'lt', 'le')
DIRECTIONS = ('asc', 'desc')

HELD = HELD_BUILDERS


def retirable(builder):
    """Whether a coded builder may ever be retired: False for a builder whose marker cannot reproduce it (HELD)."""
    return builder not in HELD


def _bound_contexts(pieces, vocabulary, paired):
    """Each context the pieces bind, with the span it was bound from, in span order."""
    contexts = []
    for piece in pieces:
        if piece.kind == 'name':
            contexts.extend((i, piece.span) for i in piece.candidates if i in vocabulary.contexts)
        elif piece.operation == 'current' and paired:
            heads = [p for p in pieces if p.kind == 'name' and any(i in vocabulary.contexts for i in p.candidates)]
            for head in heads:
                counterparts = unqualified_contexts(head, vocabulary)
                if len(counterparts) == 1:
                    contexts.extend((i, piece.span) for i in counterparts)
                    continue
                for split in re.finditer(r'\s+', head.text):
                    noun = head.text[split.end():]
                    matches = [e.id for e in vocabulary.candidates(noun) if e.id in vocabulary.contexts
                               and vocabulary.contexts[e.id].term.casefold() == vocabulary.entries[vocabulary.contexts[e.id].measure].label.casefold()]
                    if matches:
                        contexts.extend((i, piece.span) for i in matches)
                        break
    first = {}
    for identifier, span in contexts:
        first.setdefault(identifier, span)
    return list(first.items())


def shared_contexts(pieces, vocabulary, *, paired=None):
    """A bare current side uses the unqualified noun on the other side. paired says that two contexts are needed; left
    as None it is the coded reading, a comparison word among the pieces."""
    if paired is None:
        paired = any(p.operation == 'comparison' for p in pieces)
    return tuple(i for i, _ in _bound_contexts(pieces, vocabulary, paired))


@dataclass(frozen=True)
class Slot:
    """One bound slot: its kind, the spans it was read from (two for a range), what it binds to (declaration ids for
    a name, the period or periods for a period or range, the number for a number, the operation otherwise) and the
    marks of its pieces, such as a number's adjacent_direction."""
    kind: str
    spans: tuple
    value: object
    markers: tuple = ()


@dataclass(frozen=True)
class Slots:
    """A question's slots by kind, each kind in span order, and the named lexer markers (MARKERS) its pieces carry,
    which a template's `requires` and `forbids` read."""
    bound: MappingProxyType
    markers: frozenset

    def count(self, kind):
        return len(self.bound.get(kind, ()))

    def __getitem__(self, kind):
        return self.bound.get(kind, ())


def slots(pieces, vocabulary, *, two_contexts=False):
    """Bind the template slot kinds of a question's pieces in span order. A lexed window ("January to March", "between
    January and March") is one `range` slot; a period outside a window is a `period` slot. With two_contexts the context
    slots are shared_contexts(paired=True), so a bare "current" side binds the noun of the other side.

    For the graph planner (B6) to settle, not settled here: with two_contexts a question naming one qualified context
    after a bare "current" ("current certified ...") binds two; paired contexts are deduplicated and unpaired ones are
    not ("Metric vs Metric" is one slot paired, two unpaired); a ranking verb and a superlative are two direction slots;
    pieces from earlier turns are bound with this turn's, by span start alone; and planledger.slot classifies time pieces
    and predicate operators its own way."""
    from kge.services.ask.engine.question import read_period
    ordered = sorted(pieces, key=lambda p: p.span.start)
    bound = {kind: [] for kind in SLOT_KINDS}
    seen_windows = set()
    for piece in ordered:
        if piece.kind == 'time':
            period = piece.value or read_period(piece.text.casefold())[0]
            if piece.window:
                if piece.window in seen_windows:
                    continue
                seen_windows.add(piece.window)
                members = [p for p in ordered if p.kind == 'time' and p.window == piece.window]
                bound['range'].append(Slot('range', tuple(p.span for p in members),
                                           tuple(p.value or read_period(p.text.casefold())[0] for p in members)))
            else:
                bound['period'].append(Slot('period', (piece.span,), period))
        elif piece.kind == 'literal' and piece.value is not None and not isinstance(piece.value, bool):
            bound['number'].append(Slot('number', (piece.span,), piece.value, piece.markers))
        elif piece.kind == 'operator' and piece.operation in COMPARISON_OPS:
            bound['comparison_op'].append(Slot('comparison_op', (piece.span,), piece.operation))
        elif piece.kind == 'operator' and piece.operation in DIRECTIONS:
            bound['direction'].append(Slot('direction', (piece.span,), piece.operation))
        if piece.operation == 'dimension' and piece.candidates:
            bound['dimension'].append(Slot('dimension', (piece.span,), piece.candidates))
        elif piece.kind == 'name':
            entities = tuple(i for i in piece.candidates if i in vocabulary.entities)
            if entities:
                bound['entity'].append(Slot('entity', (piece.span,), entities))
    if two_contexts:
        bound['context'] = [Slot('context', (span,), (i,)) for i, span in _bound_contexts(pieces, vocabulary, True)]
    else:
        bound['context'] = [Slot('context', (p.span,), tuple(i for i in p.candidates if i in vocabulary.contexts))
                            for p in ordered if p.kind == 'name' and any(i in vocabulary.contexts for i in p.candidates)]
    bound['measure'] = [Slot('measure', slot.spans, tuple(dict.fromkeys(vocabulary.contexts[i].measure for i in slot.value)))
                        for slot in bound['context']]
    markers = frozenset(m for p in pieces for m in getattr(p, 'markers', ()) if m in MARKERS)
    return Slots(MappingProxyType({k: tuple(v) for k, v in bound.items()}), markers)
