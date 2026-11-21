"""The run trace's ledger (#531): positions, kinds, operations and slot kinds of the pieces the rules read, and the
spans the served plan applied, never the question's text.

    .venv/bin/python -m pytest tests/unit/test_plan_ledger.py -q

A stub vocabulary with hand-built pieces and plan dumps, and the synthetic plan_fixtures vocabulary through the real
rules planner: no pack, no graph.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kge.improve import rules
from kge.pipeline import trace as pipetrace
from kge.semantics.coverage import Piece
from kge.semantics.plan import Span
from kge.services.ask.engine import planledger, planrules, runtrace
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, metric_vocabulary

QUESTION = "share of widgets above 5 by region in 2025 hm"
VOCAB = SimpleNamespace(contexts={"ctx:widgets": object()}, measures={"m:widgets": object()}, entities={"e:shop": object()})


def p(kind, start, end, candidates=(), operation=None, turn="t1"):
    return Piece(Span(turn=turn, start=start, end=end), QUESTION[start:end], kind, tuple(candidates), operation)


PIECES = (p("operator", 0, 5, operation="share"), p("name", 9, 16, ("ctx:widgets",)), p("predicate", 17, 24, operation="gt"),
          p("literal", 23, 24), p("name", 25, 34, ("geo.region",), "dimension"), p("time", 38, 42), p("unknown", 43, 45),
          p("name", 0, 3, ("e:shop",), turn="earlier"))


def span(start, end, turn="t1"):
    return {"turn": turn, "start": start, "end": end}


def test_pieces_are_positions_kinds_operations_and_slots(monkeypatch):
    monkeypatch.setattr(planledger, "ledger", lambda *a: PIECES)
    got = planledger.pieces(QUESTION, "t1", VOCAB)
    assert [(x["kind"], x["operation"], x["slot"], x["start"], x["end"]) for x in got] == [
        ("operator", "share", None, 0, 5), ("name", None, "context", 9, 16), ("predicate", "gt", "comparison_op", 17, 24),
        ("literal", None, "number", 23, 24), ("name", "dimension", "dimension", 25, 34), ("time", None, "period", 38, 42),
        ("unknown", None, None, 43, 45)]
    assert set(got[0]) == {"kind", "operation", "slot", "start", "end"}


def test_slot_kinds_of_names_directions_and_ranges():
    assert planledger.slot(Piece(Span(turn="t", start=0, end=1), "x", "name", ("m:widgets",)), VOCAB) == "measure"
    assert planledger.slot(Piece(Span(turn="t", start=0, end=1), "x", "name", ("e:shop",)), VOCAB) == "entity"
    assert planledger.slot(Piece(Span(turn="t", start=0, end=1), "x", "name", ("retail",)), VOCAB) is None
    assert planledger.slot(Piece(Span(turn="t", start=0, end=1), "x", "operator", (), "desc"), VOCAB) == "direction"
    assert planledger.slot(Piece(Span(turn="t", start=0, end=1), "x", "operator", (), "range"), VOCAB) == "range"


def test_applied_spans_are_the_executed_plans_and_skip_unapplied_obligations_and_other_turns():
    dumped = {"measures": [{"context": {"id": "c", "evidence": [span(9, 16)]}}],
              "time": {"period": {"evidence": [span(38, 42), span(38, 42)]}},
              "obligations": [{"id": "o0", "spans": [span(0, 5)], "state": "applied"},
                              {"id": "o1", "spans": [span(43, 45)], "state": "unresolved"},
                              {"id": "o2", "spans": [span(17, 24)], "state": "ambiguous"}],
              "filters": [{"evidence": [span(0, 3, "earlier")]}]}
    assert planledger.applied({"kind": "result", "plan": dumped}, "t1") == [[0, 5], [9, 16], [38, 42]]
    assert planledger.applied({"kind": "abstain", "plan": dumped}, "t1") == []
    assert planledger.applied(None, "t1") == []


def test_unread_spans_are_the_readings_own():
    abstain = SimpleNamespace(kind="abstain", spans=(Span(turn="t1", start=0, end=5), Span(turn="t0", start=1, end=2)))
    assert planledger.unread(abstain, "t1") == [[0, 5]]
    assert planledger.unread(None, "t1") == []


def test_the_trace_carries_the_block_without_question_text(monkeypatch):
    monkeypatch.setattr(planledger, "ledger", lambda *a: PIECES)
    with runtrace.start("ask") as t:
        result = {"kind": "result", "plan": {"evidence": [span(9, 16)]}}
        pipetrace.plan_ledger(planledger.block(QUESTION, "t1", VOCAB, None, result, True))
        out = t.finish({})
    assert {k: out["ledger"][k] for k in ("applied", "unread", "outcome", "reply", "follow_up")} == {
        "applied": [[9, 16]], "unread": [], "outcome": "result", "reply": None, "follow_up": True}
    text = json.dumps(out["ledger"])
    for word in ("widgets", "region", "hm", "ctx:widgets", "geo.region"):
        assert word not in text
    with runtrace.start("ask") as t:
        assert "ledger" not in t.finish({})


def test_recording_with_no_open_trace_does_nothing():
    pipetrace.plan_ledger({"pieces": [], "applied": []})


@pytest.fixture
def declared(monkeypatch):
    sem = semantics()
    sem.d.GROUP_BY = {'by category': 'u.events.category', 'by source': 'u.events.source'}
    sem.measure_declarations = {'u.metric': {}}
    sem.output_metadata = {}
    eng = SimpleNamespace(semantics=sem, ALIASES={'metric': 'Metric'}, UNIT_WORDS={}, ACRONYMS={},
                          GROUP_BY=sem.d.GROUP_BY, PERIODS=sem.d.PERIODS, CALENDARS=sem.d.CALENDARS)
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    return eng, metric_vocabulary(sem, aliases=[('Metric', 'ctx'), ('by category', 'u.events.category'),
                                               ('by source', 'u.events.source')])


def through(declared, text):
    """text read by the real rules planner, its plan taken as executed when it proposes, and the block read back."""
    eng, v = declared
    reply = planrules.read(eng, parse(text, ['Metric'], eng), v, PRESETS['graph'], 't1')
    result = ({'kind': 'result', 'plan': reply.plan.model_dump(mode='json', by_alias=True)} if reply.kind == 'propose'
              else {'kind': reply.kind})
    return reply, rules.read_ledger(planledger.block(text, 't1', v, reply, result, False), text)


def test_a_real_plan_applies_every_piece_it_read(declared):
    reply, read = through(declared, 'Metric for Q1 2025 by category')
    assert reply.kind == 'propose'
    assert read == {'names': ['dimension'], 'slots': {'context': 1, 'dimension': 1, 'period': 1}, 'unknown': False,
                    'unapplied': [], 'localized': True, 'follow_up': False}


def test_a_real_plan_leaves_an_unknown_word_unread(declared):
    _, read = through(declared, 'Metric for Q1 2025 zork')
    assert [(u['text'], u['kind']) for u in read['unapplied']] == [('zork', 'unknown')]
    assert read['unknown'] is True


def test_a_real_decline_with_no_spans_is_not_localized(declared):
    reply, read = through(declared, 'average Metric by source in 2025')
    assert reply.kind == 'abstain'
    assert read['localized'] is False and read['names'] == ['avg', 'dimension'] and read['unapplied'] == []
