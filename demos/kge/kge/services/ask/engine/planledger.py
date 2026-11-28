"""The run trace's ledger (#531): where each piece the rules read sits in the question, and which spans the plan applied.

The improvement job (kge/improve/rules.py) reads it to give a miss one diagnosis, with the spans the plan left unread
and the templates the ledger matches or nearly matches. It holds positions only: each piece's kind (name, time,
operator, predicate, literal or unknown), its operation where it has one, the slot kind it fills where it fills one,
and its start and end in the question; and the [start, end] of every span the executed plan applied. Never the
question's text, a candidate id, a value or the plan: improve has the question from the harness record and slices it.

A piece's slot kind is one of the preload's (kge/compiler/standards.py, SLOT_KINDS): a name read as a context, a
measure, an entity or a dimension; a time as a period; a literal as a number; a predicate's or operator's ordering
comparison as a comparison_op, its range as a range, and an ascending or descending order as a direction. Anything
else fills none. The markers (from_to, asks_one, measure_part) are the graph planner's to bind (#518) and are not
read here.

The applied spans are the executed plan's: every evidence span the plan of a result names in this turn, its
obligations' spans only where the obligation is applied; an answer that declined applied none. The unread spans are the
reading's own account of what it could not read: a Clarify's or an Abstain's spans, or a Propose's unresolved and
ambiguous obligations. The block also names the planning outcome, the reading's kind and whether the question is a
follow-up, whose earlier turns' pieces it leaves out. Reading the ledger costs no warehouse job and no model
call: the rules read it already, and the vocabulary keeps it.
"""
from __future__ import annotations

from typing import Any

from kge.semantics.coverage import ledger

COMPARISON_OPS = frozenset({"gt", "ge", "lt", "le"})
OPERATION_SLOTS = {"asc": "direction", "desc": "direction", "range": "range"}


def slot(piece, vocabulary) -> str | None:
    """The slot kind a ledger piece fills, or None."""
    if piece.kind == "time":
        return "period"
    if piece.kind == "literal":
        return "number"
    if piece.operation in COMPARISON_OPS:
        return "comparison_op"
    if piece.kind in ("operator", "predicate") and piece.operation in OPERATION_SLOTS:
        return OPERATION_SLOTS[piece.operation]
    if piece.kind != "name":
        return None
    if piece.operation == "dimension":
        return "dimension"
    ids = set(piece.candidates)
    for kind, held in (("context", getattr(vocabulary, "contexts", {})), ("measure", getattr(vocabulary, "measures", {})),
                       ("entity", getattr(vocabulary, "entities", {}))):
        if ids & set(held):
            return kind
    return None


def pieces(question: str, turn: str, vocabulary) -> list[dict[str, Any]]:
    """The ledger's pieces of this turn, in question order, as {kind, operation, slot, start, end}."""
    read = sorted((p for p in ledger(question, turn, vocabulary) if p.span.turn == turn), key=lambda p: (p.span.start, p.span.end))
    return [{"kind": p.kind, "operation": p.operation, "slot": slot(p, vocabulary), "start": p.span.start, "end": p.span.end}
            for p in read]


def _spans(value: Any, turn: str, out: set[tuple[int, int]], states: frozenset = frozenset({"applied"})) -> None:
    """Every span of turn that value names, at any depth, skipping obligations whose state is not in states."""
    if isinstance(value, dict):
        if {"turn", "start", "end"} <= set(value) and value.get("turn") == turn:
            out.add((int(value["start"]), int(value["end"])))
            return
        if "state" in value and "spans" in value and value.get("state") not in states:
            return
        for v in value.values():
            _spans(v, turn, out, states)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _spans(v, turn, out, states)


def _sorted(found: set[tuple[int, int]]) -> list[list[int]]:
    return [list(s) for s in sorted(found)]


def applied(result: dict | None, turn: str) -> list[list[int]]:
    """The sorted, unique [start, end] of every span of turn the executed plan applied: the plan of a result, its
    evidence and its applied obligations; [] for any other outcome."""
    if not isinstance(result, dict) or result.get("kind") != "result" or not isinstance(result.get("plan"), dict):
        return []
    found: set[tuple[int, int]] = set()
    _spans(result["plan"], turn, found)
    return _sorted(found)


def unread(reply, turn: str) -> list[list[int]]:
    """The spans of turn the reply says it could not read: a Clarify's or an Abstain's own spans, a Propose's
    unresolved and ambiguous obligations' spans; [] for no reply."""
    kind = getattr(reply, "kind", None)
    found: set[tuple[int, int]] = set()
    if kind in ("clarify", "abstain"):
        found = {(s.start, s.end) for s in reply.spans if s.turn == turn}
    elif kind == "propose":
        obligations = [o.model_dump(mode="json") for o in reply.plan.obligations if o.state in ("unresolved", "ambiguous")]
        _spans(obligations, turn, found, frozenset({"unresolved", "ambiguous"}))
    return _sorted(found)


def block(question: str, turn: str, vocabulary, reply, result: dict | None, follow_up: bool) -> dict[str, Any]:
    """The run trace's ledger block: {pieces, applied, unread, outcome, reply, follow_up}, where outcome is the
    answer's planning outcome (result, or the kind of its decline) and reply the kind of the reading it came from."""
    return {"pieces": pieces(question, turn, vocabulary), "applied": applied(result, turn), "unread": unread(reply, turn),
            "outcome": result.get("kind") if isinstance(result, dict) else None,
            "reply": getattr(reply, "kind", None), "follow_up": bool(follow_up)}
