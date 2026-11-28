"""The ledger mirror: how B2.1's acceptance (#514) is checked, on every phrasing an evaluation asks.

Baseline v1 is the coded phrase table frozen in the preload, so a graph that holds it must lex every question exactly as
the coded constant does, piece for piece. A Mirror watches the question lexer's reads while it is open
(coverage.watching): it only records each read's vocabulary, text and turn, once each, and does no lexing inside the
request it watches, so a timed request's latency and job counts stay clean. After the fact, results() lexes every
recorded read twice with coverage.mirror_ledgers, once with the vocabulary's own table and once with the coded constant,
and reports where the two ledgers first differ. A vocabulary with no phrase table, a test's stand-in, is recorded with
source "none" and not compared; a read both sides refuse alike is recorded with source "raised", and is no read
checked: it counts apart (raised_reads), so a set whose lexing always raises shows no read and is flagged. A read only
one side refuses is a mismatch.

The comparison is the vocabulary's whole table against the constant, which is right while a graph holds only baseline
v1: any phrase beyond it that an evaluated question uses is a mismatch. The first slice that adds a non-baseline phrase
compares the graph's baseline rows instead, or leaves a new_phrase read (coverage.new_phrase) apart.

Grounding-eval opens one per item on the item's own thread (threads "current"); the integration lane opens one per test
over every thread (threads "all"). A summary and a mismatch name the turn, the position and each piece's kind,
operation, place and candidates, never the question's words: the grounding row already carries the item's id."""
from __future__ import annotations

import threading
from contextlib import ExitStack
from typing import NamedTuple

from kge.semantics import coverage


class MirrorRead(NamedTuple):
    """One recorded read, compared: the table's source ("graph", "constant" or "none"), whether the two ledgers are equal
    and, where they are not, the first differing position and each side's piece there, described without its words."""
    text: str
    turn: str
    source: str
    equal: bool
    index: int | None
    graph: str | None
    constant: str | None

    def __repr__(self) -> str:
        """Without the question's words, so an assertion or a log line that shows a read never leaks a held-out
        phrasing."""
        return (f'MirrorRead(turn={self.turn!r}, source={self.source!r}, equal={self.equal}, index={self.index}, '
                f'graph={self.graph!r}, constant={self.constant!r})')


def described(piece) -> str:
    """A piece without its words: its kind, operation, place and candidates; "nothing" past a ledger's end."""
    if piece is None:
        return 'nothing'
    candidates = ','.join(map(str, piece.candidates)) or '-'
    return f"{piece.kind} {piece.operation or '-'} at {piece.span.start}-{piece.span.end} on {candidates}"


def first_difference(graph, constant) -> int | None:
    """The first position where two ledgers differ, a length difference included; None where they are equal."""
    for index in range(max(len(graph), len(constant))):
        if index >= len(graph) or index >= len(constant) or graph[index] != constant[index]:
            return index
    return None


def lexed(text, turn, vocabulary, table):
    """One side's ledger, or the refusal it raised, as ("pieces", ledger) or ("raises", its type and message). It lexes
    with coverage._ledger, the lexer itself, on purpose: mirror_ledgers lexes both sides at once and cannot say which
    one raised."""
    try:
        return 'pieces', tuple(coverage._ledger(text, turn, vocabulary, table))
    except Exception as exc:
        return 'raises', (type(exc).__name__, str(exc))


def compared(vocabulary, text, turn) -> MirrorRead:
    """One read lexed afresh with the vocabulary's table and with the constant. Where the lexer raises, each side is lexed
    on its own: two sides that raise alike are equal, and a side that raises alone is a difference at no position."""
    table = getattr(vocabulary, 'phrase_table', None)
    if table is None:
        return MirrorRead(text, turn, 'none', True, None, None, None)
    source = getattr(table, 'source', 'graph')
    try:
        graph, constant = coverage.mirror_ledgers(text, turn, vocabulary)
    except Exception:
        own, base = lexed(text, turn, vocabulary, table), lexed(text, turn, vocabulary, coverage.BASELINE_PHRASES)
        if own == base:
            return MirrorRead(text, turn, 'raised', True, None, None, None)
        def side(result):
            return f'raises {result[1][0]}' if result[0] == 'raises' else f'{len(result[1])} pieces'
        return MirrorRead(text, turn, source, False, None, side(own), side(base))
    index = first_difference(graph, constant)
    if index is None:
        return MirrorRead(text, turn, source, True, None, None, None)
    def at(pieces):
        return described(pieces[index] if index < len(pieces) else None)
    return MirrorRead(text, turn, source, False, index, at(graph), at(constant))


class Mirror:
    """Watches the question lexer's reads while open. threads "current" records only reads made on the thread that
    opened it; "all" records every thread's."""

    def __init__(self, *, threads: str = 'current'):
        if threads not in ('current', 'all'):
            raise ValueError(f'threads is "current" or "all", not {threads!r}')
        self.threads = threads
        self._thread = None
        self._lock = threading.Lock()
        self._reads = {}
        self._stack = None
        self._results = None

    def __enter__(self) -> Mirror:
        self._thread = threading.get_ident()
        self._stack = ExitStack()
        self._stack.enter_context(coverage.watching(self._seen))
        return self

    def __exit__(self, *exc) -> None:
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()

    def _seen(self, text, turn, vocabulary):
        """Records one read, once per vocabulary, text and turn, holding the vocabulary; it never lexes."""
        if self.threads == 'current' and threading.get_ident() != self._thread:
            return
        with self._lock:
            self._reads.setdefault((id(vocabulary), text, turn), (vocabulary, text, turn))

    def results(self) -> list[MirrorRead]:
        """Every recorded read, compared, in the order first read."""
        with self._lock:
            reads = list(self._reads.values())
        if self._results is None or len(self._results) != len(reads):
            self._results = [compared(vocabulary, text, turn) for vocabulary, text, turn in reads]
        return list(self._results)

    def mismatches(self) -> list[MirrorRead]:
        """The compared reads whose two ledgers differ."""
        return [read for read in self.results() if not read.equal]

    def summary(self) -> dict:
        """The reads compared, those that read the constant because the graph holds no phrase table, those both sides
        refused alike (raised_reads, not compared), and each mismatch by turn, position and pieces; no question text."""
        results = self.results()
        reads = [read for read in results if read.source not in ('none', 'raised')]
        return {'reads': len(reads), 'raised_reads': sum(read.source == 'raised' for read in results),
                'constant_reads': sum(read.source == 'constant' for read in reads),
                'mismatches': [{'turn': read.turn, 'index': read.index, 'graph': read.graph, 'constant': read.constant}
                               for read in reads if not read.equal]}
