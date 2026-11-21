"""The question lexer's phrase table (kge/semantics/phrasetable.py) and the lexer reading it (kge/semantics/coverage.py).

    .venv/bin/python -m pytest tests/unit/test_phrase_table.py -q

Baseline v1 is the coded table frozen in the platform preload. A graph whose table equals it lexes every question as the
constant does, piece for piece, whatever order the graph returns its rows in; the coded rules (planrules.read) read the
constant whatever the graph holds; coverage and the readable-words check read the graph's. Every question and phrase here
is synthetic: the plan fixtures' own names, and the invented words tally and kindly.
"""
from __future__ import annotations

import ast
import inspect
import random
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.compiler import standards
from kge.semantics import coverage
from kge.semantics.coverage import BASELINE_PHRASES, ledger, mirror_ledgers, new_phrase, phrase_table, watching
from kge.semantics.phrasetable import PhraseTable
from tests.plan_fixtures import semantics, vocabulary

QUESTIONS = (
    "metric by month",
    "top 3 entities by metric",
    "which entities have metric above 5",
    "metric year to date vs prior year",
    "share of metric by category",
    "metric for their entities",
    "how many entities have metric over 10",
    "metric between January 2025 and March 2025",
    "average metric by month",
    "which entities have the highest metric",
    "break down metric by category, show me the lowest 2",
)
TALLY = {"phrase": "tally", "operation": "count"}
KINDLY = {"phrase": "kindly", "filler": True}


def table(*extra, without=()) -> PhraseTable:
    return PhraseTable.from_rows([*(r for r in BASELINE_PHRASES.rows() if r["phrase"] not in without), *extra])


def graph_vocabulary(phrases, sem=None):
    sem = sem or semantics()
    sem.graph_vocabulary = SimpleNamespace(phrases=phrases)
    return vocabulary(sem)


def readings(pieces):
    return [(p.text, p.kind, p.operation) for p in pieces]


def test_the_constant_and_its_rows_are_one_table():
    rows = BASELINE_PHRASES.rows()
    assert [r["phrase"] for r in rows] == sorted(r["phrase"] for r in rows)
    graph = PhraseTable.from_rows(rows)
    assert graph == BASELINE_PHRASES and hash(graph) == hash(BASELINE_PHRASES)
    assert graph.rows() == rows
    assert (graph.source, BASELINE_PHRASES.source) == ("graph", "constant")
    assert not graph.extended and not BASELINE_PHRASES.extended
    assert BASELINE_PHRASES.rank_pattern == coverage.RANK_PATTERN
    assert BASELINE_PHRASES.rank_words == coverage.RANK_WORDS
    assert set(graph.rank_words) == set(coverage.RANK_WORDS)
    for word in coverage.RANK_WORDS:
        assert re.fullmatch(graph.rank_pattern, word) and re.fullmatch(BASELINE_PHRASES.rank_pattern, word), word
    assert BASELINE_PHRASES.operator_phrases == frozenset(coverage.OPERATORS)
    assert BASELINE_PHRASES.filler == coverage.SCAFFOLD
    assert BASELINE_PHRASES.filler_phrases == coverage.SCAFFOLD_PHRASES


def test_the_constant_keeps_the_coded_orders():
    assert list(BASELINE_PHRASES.operators) == list(coverage.OPERATORS)
    assert BASELINE_PHRASES.operators is not coverage.OPERATORS
    with pytest.raises(TypeError):
        BASELINE_PHRASES.operators["tally"] = "count"


def test_the_preloads_phrases_are_baseline_v1():
    rows = list(standards.load().phrases.values())
    graph = PhraseTable.from_rows(rows)
    assert graph == BASELINE_PHRASES
    assert not graph.extended
    assert len(rows) == len(BASELINE_PHRASES.rows())


@pytest.mark.parametrize("rows, refusal", [
    ([{"phrase": "Tally", "operation": "count"}], "lower-case"),
    ([{"phrase": "", "filler": True}], "lower-case"),
    ([{"phrase": "tally ", "operation": "count"}], "lower-case"),
    ([TALLY, {"phrase": "tally", "operation": "aggregate"}], "given twice"),
    ([{"phrase": "tally"}], "operation or is filler"),
    ([{"phrase": "tally", "operation": None, "filler": False}], "operation or is filler"),
])
def test_a_table_breaking_the_preloads_rules_is_refused(rows, refusal):
    with pytest.raises(ValueError, match=refusal):
        PhraseTable.from_rows(rows)


def test_a_planted_phrase_is_new_and_filler_phrases_are_kept_longest_first():
    planted = PhraseTable.from_rows([TALLY, KINDLY, {"phrase": "if you would", "filler": True},
                                     {"phrase": "for me", "filler": True, "baseline": True}])
    assert planted.new == {"tally", "kindly", "if you would"} and planted.extended
    assert planted.filler == {"kindly"} and planted.filler_phrases == ("if you would", "for me")
    assert planted.rank_pattern == r"(?!)"
    assert table(TALLY) != BASELINE_PHRASES and table(TALLY).new == {"tally"}


def test_the_rows_order_moves_nothing_the_lexer_reads():
    """The lexer's own patterns are order-free: the phrase alternation is sorted, the rank and filler-phrase alternations
    are longest first, and no filler phrase begins another at a word's end. So a graph table whose operators are in
    another order than the constant's lexes every baseline phrase as the constant does."""
    rows = list(BASELINE_PHRASES.rows())
    random.Random(514).shuffle(rows)
    graph = PhraseTable.from_rows(rows)
    assert graph == BASELINE_PHRASES and PhraseTable.from_rows(reversed(rows)).operators == graph.operators
    assert list(graph.operators) != list(BASELINE_PHRASES.operators)
    phrases = BASELINE_PHRASES.filler_phrases
    assert not any(a != b and b.startswith(a + " ") for a in phrases for b in phrases)
    vocab = vocabulary()
    for row in BASELINE_PHRASES.rows():
        text = f"which entities have metric {row['phrase']} 5"
        assert coverage._ledger(text, "turn", vocab, graph) == coverage._ledger(text, "turn", vocab, BASELINE_PHRASES), text


def test_a_baseline_graph_table_reads_as_the_constant_with_no_added_lexing():
    vocab = graph_vocabulary(PhraseTable.from_rows(BASELINE_PHRASES.rows()))
    assert vocab.phrase_table.source == "graph"
    assert vocab.baseline_ledger is vocab.lexical_ledger
    for text in QUESTIONS:
        graph, constant = mirror_ledgers(text, "turn", vocab)
        assert graph == constant, text
        assert not new_phrase(text, "turn", vocab)
        assert ledger(text, "turn", vocab) == ledger(text, "turn", vocab, baseline=True)


def test_a_planted_operation_phrase_reads_from_the_graph_and_the_coded_rules_read_the_constant():
    vocab = graph_vocabulary(table(TALLY))
    text = "tally of entities by month"
    assert vocab.baseline_ledger is not vocab.lexical_ledger
    assert ("tally", "operator", "count") in readings(ledger(text, "turn", vocab))
    assert ("tally", "unknown", None) in readings(ledger(text, "turn", vocab, baseline=True))
    assert ledger(text, "turn", vocab, baseline=True) == ledger(text, "turn", vocabulary())
    assert new_phrase(text, "turn", vocab)
    assert not new_phrase("metric by month", "turn", vocab)
    graph, constant = mirror_ledgers(text, "turn", vocab)
    assert graph == ledger(text, "turn", vocab) and constant == ledger(text, "turn", vocab, baseline=True)


def test_planted_filler_is_read_past():
    vocab = graph_vocabulary(table(KINDLY))
    text = "kindly show metric by month"
    assert not any(p.kind == "unknown" for p in ledger(text, "turn", vocab))
    assert ("kindly", "unknown", None) in readings(ledger(text, "turn", vocab, baseline=True))


def readable_vocabulary(phrases):
    """A hidden column of another unit, named by aliases made of a readable word and a word the table may hold."""
    sem = semantics()
    sem.tables["v.things"] = {"amount": "DECIMAL"}
    sem.types["v.things.amount"] = "DECIMAL"
    if phrases is not None:
        sem.graph_vocabulary = SimpleNamespace(phrases=phrases)
    return vocabulary(sem, aliases=[("kindly metric", "v.things.amount"), ("tally metric", "v.things.amount")])


def test_a_planted_filler_word_makes_a_denied_phrase_readable():
    assert {"kindly metric", "tally metric"} <= set(readable_vocabulary(None).denied)
    assert {"kindly metric", "tally metric"} <= set(readable_vocabulary(BASELINE_PHRASES).denied)
    planted = readable_vocabulary(table(KINDLY)).denied
    assert "kindly metric" not in planted and "tally metric" in planted


def test_which_beside_a_rank_word_falls_back_to_filler():
    """With a rank word in the question the lexer sets which's operation aside; the baseline's filler reading of which
    then reads it past. Without the filler reading it is left unread: the dual reading is what holds."""
    text = "which entities have the highest metric"
    assert not any(p.kind == "unknown" for p in ledger(text, "turn", graph_vocabulary(table())))
    single = table({"phrase": "which", "operation": "count", "baseline": True}, without=("which",))
    assert ("which", "unknown", None) in readings(ledger(text, "turn", graph_vocabulary(single)))
    assert ("which", "operator", "count") in readings(ledger("which entities have metric above 5", "turn", graph_vocabulary(single)))


def test_their_reads_as_membership():
    assert ("their", "operator", "membership") in readings(ledger("metric for their entities", "turn", graph_vocabulary(table())))


@pytest.mark.parametrize("held", [None, SimpleNamespace(phrases=None), SimpleNamespace()])
def test_a_graph_without_phrase_rows_reads_the_constant(held):
    sem = semantics()
    sem.graph_vocabulary = held
    assert phrase_table(sem) is BASELINE_PHRASES and phrase_table(sem).source == "constant"
    vocab = vocabulary(sem)
    assert vocab.phrase_table is BASELINE_PHRASES
    assert vocab.baseline_ledger is vocab.lexical_ledger
    assert phrase_table(object()) is BASELINE_PHRASES


def ledger_calls(function: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ledger"]


def baseline_flag(call: ast.Call) -> bool:
    return any(k.arg == "baseline" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)


def test_the_coded_rules_read_the_constant_and_nothing_else_in_planrules_does():
    from kge.services.ask.engine import planrules
    tree = ast.parse(Path(inspect.getsourcefile(planrules)).read_text())
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    read = next(f for f in functions if f.name == "read")
    calls = ledger_calls(read)
    assert len(calls) == 2, "the question's reading and the predicate fallback's"
    assert all(baseline_flag(c) for c in calls)
    others = [(f.name, c.lineno) for f in functions if f.name != "read" for c in ledger_calls(f) if baseline_flag(c)]
    assert others == []


def test_a_watcher_sees_each_read_while_it_is_active():
    vocab = vocabulary()
    seen, nested = [], []
    with watching(lambda *read: seen.append(read)):
        ledger("metric by month", "t1", vocab)
        with watching(lambda *read: nested.append(read)):
            ledger("metric by month", "t2", vocab, baseline=True)
        ledger("metric by month", "t3", vocab)
    ledger("metric by month", "t4", vocab)
    assert seen == [("metric by month", "t1", vocab), ("metric by month", "t2", vocab), ("metric by month", "t3", vocab)]
    assert nested == [("metric by month", "t2", vocab)]
    assert coverage._WATCHERS == ()


def test_a_watcher_sees_reads_on_other_threads():
    vocab = vocabulary()
    seen = []
    with watching(lambda *read: seen.append(read[1])):
        worker = threading.Thread(target=ledger, args=("metric by month", "elsewhere", vocab))
        worker.start()
        worker.join()
    assert seen == ["elsewhere"]
