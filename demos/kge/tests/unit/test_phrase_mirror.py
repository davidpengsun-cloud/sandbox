"""The ledger mirror (kge/eval/phrasemirror.py) and its place in grounding-eval, on synthetic vocabularies only: the plan
fixtures' semantics with a phrase table planted in its graph vocabulary, and the invented operation phrase tally."""
import threading
from types import SimpleNamespace

import pytest

from kge.eval import grounding
from kge.eval.grounding import JOB_KINDS, compare, passed, summarize, write
from kge.eval.phrasemirror import Mirror, MirrorRead, first_difference
from kge.semantics import coverage
from kge.semantics.phrasetable import PhraseTable
from tests import bigquery_fake
from tests.plan_fixtures import semantics, vocabulary

QUESTION = 'tally of value by month'


def planted(*rows):
    """The plan fixtures' vocabulary over a graph whose phrase table is baseline v1 plus rows."""
    sem = semantics()
    sem.graph_vocabulary = SimpleNamespace(phrases=PhraseTable.from_rows([*coverage.BASELINE_PHRASES.rows(), *rows]))
    return vocabulary(sem)


def tallied():
    return planted({'phrase': 'tally', 'operation': 'count'})


def test_a_planted_operation_phrase_is_one_mismatch_at_its_piece_with_no_question_text():
    vocab = tallied()
    with Mirror() as watched:
        coverage.ledger(QUESTION, 'turn', vocab)
    [read] = watched.mismatches()
    assert (read.source, read.index) == ('graph', 0)
    assert read.graph.startswith('operator count') and read.constant.startswith('unknown')
    summary = watched.summary()
    assert summary == {'reads': 1, 'raised_reads': 0, 'constant_reads': 0, 'mismatches': [
        {'turn': 'turn', 'index': 0, 'graph': read.graph, 'constant': read.constant}]}
    assert not any(word in repr(summary) for word in QUESTION.split())


def test_the_baseline_table_from_the_graph_gives_no_mismatch():
    vocab = planted()
    with Mirror() as watched:
        coverage.ledger(QUESTION, 'turn', vocab)
        coverage.ledger('what is the total value by month', 'turn', vocab)
    assert [read.source for read in watched.results()] == ['graph', 'graph']
    assert watched.mismatches() == []
    assert watched.summary() == {'reads': 2, 'raised_reads': 0, 'constant_reads': 0, 'mismatches': []}


def test_a_graph_with_no_phrase_table_is_a_constant_read():
    with Mirror() as watched:
        coverage.ledger(QUESTION, 'turn', vocabulary(semantics()))
    assert watched.summary() == {'reads': 1, 'raised_reads': 0, 'constant_reads': 1, 'mismatches': []}


def test_a_read_is_recorded_once_and_reads_after_exit_are_not():
    vocab = tallied()
    with Mirror() as watched:
        coverage.ledger(QUESTION, 'turn', vocab)
        coverage.ledger(QUESTION, 'turn', vocab)
        coverage.ledger(QUESTION, 'other', vocab)
    coverage.ledger('what is the total value by month', 'turn', vocab)
    assert [(read.turn, read.equal) for read in watched.results()] == [('turn', False), ('other', False)]
    assert coverage._WATCHERS == ()


def test_a_stub_vocabulary_is_recorded_as_none_and_not_compared():
    mirror = Mirror()
    mirror._thread = threading.get_ident()
    mirror._seen(QUESTION, 'turn', SimpleNamespace())
    [read] = mirror.results()
    assert (read.source, read.equal) == ('none', True)
    assert mirror.summary() == {'reads': 0, 'raised_reads': 0, 'constant_reads': 0, 'mismatches': []}


@pytest.mark.parametrize('threads, recorded', [('current', 0), ('all', 1)])
def test_current_records_only_its_own_thread_and_all_records_every_thread(threads, recorded):
    vocab = tallied()
    with Mirror(threads=threads) as watched:
        other = threading.Thread(target=coverage.ledger, args=(QUESTION, 'turn', vocab))
        other.start()
        other.join()
    assert len(watched.results()) == recorded


def test_threads_is_current_or_all():
    with pytest.raises(ValueError):
        Mirror(threads='some')


def test_the_first_difference_counts_a_length_difference():
    assert first_difference((1, 2), (1, 2)) is None
    assert first_difference((1, 2), (1, 3)) == 1
    assert first_difference((1, 2), (1,)) == 1


def mirrored(vocab):
    """One item compared with the mirror, every arm reading the question through vocab on the item's thread."""
    item = {'id': 'one', 'question': QUESTION, 'expect': {'figure': 4}, 'reply': {}}
    def ask(arm, item):
        if vocab is not None:
            coverage.ledger(item['question'], 'turn', vocab)
        return {'behaviour': 'figure', 'figure': 4}
    return compare([item], ask, with_b=True, measure=lambda arm, item, call: (call(), dict.fromkeys(JOB_KINDS, 0)),
                   mirror=True)


def test_grounding_rows_carry_the_mirror_and_a_baseline_graph_passes(tmp_path):
    report = mirrored(planted())
    assert report['rows'][0]['ledger_mirror'] == {'reads': 1, 'raised_reads': 0, 'constant_reads': 0, 'mismatches': []}
    assert (report['ledger_reads'], report['ledger_mismatches'], report['ledger_constant_reads']) == (1, [], [])
    assert passed({'sample': report})
    write({'sample': report}, tmp_path)
    assert ('Phrase baseline v1: 1 ledger reads, graph and constant ledgers identical on every one'
            in (tmp_path / 'grounding-eval.md').read_text())


def test_a_mismatch_fails_the_gate_and_names_the_item(tmp_path):
    report = mirrored(tallied())
    [mismatch] = report['ledger_mismatches']
    assert (mismatch['id'], mismatch['index']) == ('one', 0)
    assert not passed({'sample': report})
    write({'sample': report}, tmp_path)
    assert 'graph and constant ledgers differ on: one' in (tmp_path / 'grounding-eval.md').read_text()


def test_a_read_that_fell_back_to_the_constant_fails_the_gate():
    report = mirrored(vocabulary(semantics()))
    assert report['ledger_constant_reads'] == ['one']
    assert not passed({'sample': report})


def test_a_mirrored_set_with_no_reads_reports_none_and_is_judged_as_before(tmp_path):
    report = mirrored(None)
    assert (report['ledger_reads'], report['ledger_mismatches'], report['ledger_constant_reads']) == (0, [], [])
    assert passed({'sample': report})
    write({'sample': report}, tmp_path)
    assert 'Phrase baseline v1: no ledger reads were recorded, so no ledger was checked' in (
        tmp_path / 'grounding-eval.md').read_text()


def test_a_served_set_with_no_reads_checked_fails_the_gate():
    report = {**mirrored(None), 'ledger_unchecked': True}
    assert not passed({'sample': report})


def test_rows_without_the_mirror_leave_the_report_as_before():
    item = {'id': 'one', 'question': QUESTION, 'expect': {'figure': 4}, 'reply': {}}
    report = compare([item], lambda arm, item: {'behaviour': 'figure', 'figure': 4}, with_b=True,
                     measure=lambda arm, item, call: (call(), dict.fromkeys(JOB_KINDS, 0)))
    assert 'ledger_mirror' not in report['rows'][0]
    assert not {'ledger_reads', 'ledger_mismatches', 'ledger_constant_reads'} & set(report)
    assert passed({'sample': report})
    assert not {'ledger_reads'} & set(summarize(report['rows']))


def test_evaluate_and_the_pilot_twin_worker_ask_with_the_mirror(monkeypatch, tmp_path):
    import json
    from contextlib import nullcontext

    seen = []
    def compared(*args, **kwargs):
        seen.append(kwargs.get('mirror'))
        return {'rows': [{}]}
    monkeypatch.setattr(grounding, 'compare', compared)
    engine = SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'all'), UNIT_ORDER=['u'],
                             g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda who: {'u'}), w=None)
    path = tmp_path / 'sample.json'
    path.write_text(json.dumps([{'id': 'one', 'question': 'one', 'reply': {}, 'expect': {}}]))
    grounding.evaluate(engine, [path], ask=lambda arm, item: {})
    monkeypatch.setattr(bigquery_fake, '_grounding_worker', SimpleNamespace(ask=None, measure=None))
    bigquery_fake.grounding_worker({'id': 'one'})
    assert seen == [True, True]


def test_a_read_shown_in_a_message_holds_no_question_text():
    read = MirrorRead('the held out words', 'turn', 'graph', False, 0, 'operator count at 0-5 on -', 'unknown - at 0-5 on -')
    assert 'held out' not in repr(read) and 'held out' not in str(read)


def refusing(monkeypatch, refuses):
    """coverage's lexer refusing every read for which refuses(table) holds."""
    lexer = coverage._ledger
    def lex(text, turn, vocabulary, table=None):
        if refuses(table):
            raise RuntimeError('refused')
        return lexer(text, turn, vocabulary, table)
    monkeypatch.setattr(coverage, '_ledger', lex)


def test_a_read_both_sides_refuse_is_not_counted_as_checked(monkeypatch):
    vocab = planted()
    with Mirror() as watched:
        coverage.ledger(QUESTION, 'turn', vocab)
    refusing(monkeypatch, lambda table: True)
    [read] = watched.results()
    assert (read.source, read.equal) == ('raised', True)
    summary = watched.summary()
    assert (summary['reads'], summary['raised_reads'], summary['mismatches']) == (0, 1, [])


def test_a_read_only_one_side_refuses_is_a_mismatch(monkeypatch):
    vocab = planted()
    with Mirror() as watched:
        coverage.ledger(QUESTION, 'turn', vocab)
    refusing(monkeypatch, lambda table: table is not coverage.BASELINE_PHRASES)
    [read] = watched.mismatches()
    assert (read.source, read.index, read.graph) == ('graph', None, 'raises RuntimeError')
    assert watched.summary()['reads'] == 1
