"""Shadow comparison: one question's graph rules plan set beside its coded plan, kept as one redacted observation
(question-shapes.md §4.4, #450 requirements 6 and 7).

What is compared: each side's reply after the existing plan checks. Two proposals agree when their normal forms are
equal (normalise): no evidence, obligation or span, node ids replaced by labels made from each node's content, and the
lists whose order cannot change a figure sorted. Two declines agree when they are of one kind; a proposal and a decline
never agree. The comparison reads the plans before any redaction, so redaction never makes two plans agree.

What is kept: the time, the served release, the shapes each side used (the graph's as matched, the coded side's by
node-kind signature, coded_shapes), each side's outcome, the comparison, who served and the selector's flags. Review
evidence is kept only for a disagreement or a takeover: each side's plan as a receipt keeps it
(planpublic.receipt_plan), then normalised, a clarification's candidate ids, the positions of the spans each side
applied, and the ledger's pieces as kind, operation and positions.

What is never kept: the question's text or any word of it, the principal, SQL, rows, figures or answer text, a piece's
text, value or candidates, an abstention's missing list (it can quote the question), and any compared value on a
column that is not one of that column's declared members, an entity key among them, which keeps only its type.

The module is CPU only: no warehouse, model or graph call. It reads planpublic as a module attribute at call time, so
planpublic may import this module without an import cycle. It reaches the shadow log's record rules (line_of and
MAX_LINE in kge/adapters/shadowlog/record.py), which do no input or output; importing them loads that package's
factory and its imports, but this module calls no other adapter."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

from kge.adapters.shadowlog import record
from kge.ports import ShadowObservation
from kge.semantics.plan import Abstain, Clarify, Propose
from kge.services.ask.engine import planpublic

if TYPE_CHECKING:
    from kge.semantics.coverage import Piece

Reply = Propose | Clarify | Abstain

DROPPED = frozenset({"evidence", "obligations", "spans"})
"""Keys normalise removes wherever they appear: they point into the question, never into what is computed."""

UNORDERED = frozenset({"filters", "children", "values", "remove_dimensions", "remove_filters", "partition_by", "bindings"})
"""The plan fields whose list order cannot change a figure, sorted by canonical JSON before two plans are compared:

- filters (the plan's own, each measure's and a membership's) and a junction's children are AND or OR sets;
- an IN filter's values are set membership;
- a share's remove_dimensions and remove_filters, a running total's, a moving average's and an order's partition_by
  are sets of dimension or filter ids that key a partition;
- a bound or band dimension's bindings are keyed by context, one each.

Every other list keeps its order, because its order is part of the answer: measures, dimensions, comparisons, a
comparison's inputs, derived and order. Propose.base_turn is not compared."""

SIDES = frozenset({"base", "other"})
"""The path segments that name a side of a compared output, not a node."""

CANONICAL = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


def canonical(value) -> str:
    """value as canonical JSON: sorted keys, no spaces."""
    return json.dumps(value, **CANONICAL)


def _stripped(node):
    """node with every DROPPED key removed, at any depth; a Name becomes {"id": ...}."""
    if isinstance(node, dict):
        return {key: _stripped(child) for key, child in node.items() if key not in DROPPED}
    if isinstance(node, (list, tuple)):
        return [_stripped(child) for child in node]
    return node


def _predicates(predicates):
    """Every predicate node in predicates, its junction's children, a not's child and a membership's filters among them."""
    for predicate in predicates or ():
        if not isinstance(predicate, dict):
            continue
        yield predicate
        yield from _predicates(predicate.get("children"))
        if isinstance(predicate.get("child"), dict):
            yield from _predicates([predicate["child"]])
        yield from _predicates(predicate.get("filters"))


def _nodes(plan):
    """Every node of a dumped plan: a dict with a kind and a string id under measures, dimensions, filters (with each
    measure's), comparisons and derived."""
    measures = plan.get("measures") or []
    candidates = [*measures, *(plan.get("dimensions") or []), *(plan.get("comparisons") or []),
                  *(plan.get("derived") or []), *_predicates(plan.get("filters")),
                  *(p for m in measures if isinstance(m, dict) for p in _predicates(m.get("filters")))]
    return [n for n in candidates if isinstance(n, dict) and "kind" in n and isinstance(n.get("id"), str)]


def _references(node, index) -> set[str]:
    """The ids of the nodes node names: by a reference (planpublic.NODE_REFERENCES, a path's segments among them, and an
    output operand's id) or as a child predicate."""
    found = set()
    for key, value in node.items():
        if key == "left" and isinstance(value, dict):
            value = value.get("id") if value.get("kind") == "output" else None
        elif key in ("children", "filters") and isinstance(value, list):
            found.update(c["id"] for c in value if isinstance(c, dict) and c.get("id") in index)
            continue
        elif key == "child" and isinstance(value, dict):
            value = value.get("id") if value.get("id") in index else None
            found.update([value] if value else [])
            continue
        elif key not in planpublic.NODE_REFERENCES:
            continue
        for ref in value if isinstance(value, list) else [value]:
            if isinstance(ref, str):
                found.update(part for part in ref.split("/") if part in index)
    return found


def _cycles(index) -> dict[str, frozenset[str]]:
    """For each node on a reference cycle, the nodes on a cycle with it (itself included); a node on none is left out."""
    edges = {raw: _references(node, index) for raw, node in index.items()}
    reach = {}
    for raw in index:
        seen, stack = set(), list(edges[raw])
        while stack:
            other = stack.pop()
            if other not in seen:
                seen.add(other)
                stack.extend(edges[other])
        reach[raw] = seen
    return {raw: frozenset(o for o in reach[raw] if raw in reach[o]) for raw in index if raw in reach[raw]}


CYCLE = "#cycle"
"""What normalise writes for a reference that closes a cycle, in place of a label."""


def normalise(plan: dict) -> dict:
    """The plan's normal form, which two plans share exactly when they compute the same answer by these rules.

    plan is QueryPlan.model_dump(mode="json", by_alias=True). Every evidence, obligations and spans key is dropped.
    Each node's id becomes a label, "#" and the first 12 hex digits of the SHA-256 of its canonical content: the node
    without its id, with every reference (planpublic.NODE_REFERENCES, and an output operand's id) already replaced by
    the label of the node it names. A path keeps its side: c/m1/base becomes #<c>/#<m1>/base. The lists in UNORDERED
    are sorted by canonical JSON before a label is made, so their order changes no label either.

    Two sibling nodes with the same content get the same label. That is harmless: they compile to the same SQL.
    QueryPlan refuses cycles among derived values only, so a predicate on an output can close one (a share whose
    remove_filters names a filter on that share). A reference from a node to another on a cycle with it is written
    CYCLE, whichever node is reached first, so no raw id survives and the labels do not depend on plan order."""
    stripped = _stripped(plan)
    index = {node["id"]: node for node in _nodes(stripped)}
    cycles = _cycles(index)
    memo: dict[str, dict] = {}

    def ref(value, owner):
        if not isinstance(value, str):
            return value
        parts = value.split("/")
        side = [parts.pop()] if len(parts) > 1 and parts[-1] in SIDES else []
        return "/".join([*(label(part, owner) for part in parts), *side])

    def label(raw, owner):
        if raw not in index:
            return raw
        if owner is not None and raw in cycles.get(owner, ()):
            return CYCLE
        return normal(raw)["id"]

    def child(node, owner):
        if not (isinstance(node, dict) and node.get("id") in index):
            return node
        if owner is not None and node["id"] in cycles.get(owner, ()):
            return {"id": CYCLE}
        return normal(node["id"])

    def field(key, value, owner=None):
        if key == "left" and isinstance(value, dict):
            if value.get("kind") == "output" and isinstance(value.get("id"), str):
                return {**value, "id": ref(value["id"], owner)}
            return value
        if key in ("children", "filters") and isinstance(value, list):
            value = [child(c, owner) for c in value]
        elif key == "child" and isinstance(value, dict):
            value = child(value, owner)
        elif key in planpublic.NODE_REFERENCES:
            value = [ref(v, owner) for v in value] if isinstance(value, list) else ref(value, owner)
        if key in UNORDERED and isinstance(value, list):
            value = sorted(value, key=canonical)
        return value

    def normal(raw):
        if raw not in memo:
            content = {key: field(key, value, raw) for key, value in index[raw].items() if key != "id"}
            digest = hashlib.sha256(canonical(content).encode("utf-8")).hexdigest()[:12]
            memo[raw] = {**content, "id": "#" + digest}
        return memo[raw]

    out = {}
    for key, value in stripped.items():
        if key in ("measures", "dimensions", "comparisons", "derived", "filters") and isinstance(value, list):
            value = [normal(n["id"]) if isinstance(n, dict) and n.get("id") in index else n for n in value]
        elif key == "order" and isinstance(value, list):
            value = [{k: field(k, v) for k, v in entry.items()} if isinstance(entry, dict) else entry for entry in value]
        if key in UNORDERED and isinstance(value, list):
            value = sorted(value, key=canonical)
        out[key] = value
    return out


def _dump(reply) -> dict:
    """A proposal's plan as JSON, its aliases used."""
    return reply.plan.model_dump(mode="json", by_alias=True)


def outcome_of(reply: Reply) -> str:
    """The reply's kind: propose, clarify or abstain."""
    return reply.kind


def compare(graph: Reply, coded: Reply) -> str:
    """How the two replies compare: agree, plans_differ, propose_decline or declines_differ.

    Two proposals agree when their unredacted normal forms are equal. Two declines agree when they are of one kind; an
    abstention's missing list and a clarification's candidates are not compared. A plan whose normal form holds CYCLE
    never agrees: its labels cannot tell the cycle's nodes apart (the plan checks refuse such plans before they are
    compared, so this only keeps a false agreement impossible)."""
    proposing = (graph.kind == "propose", coded.kind == "propose")
    if all(proposing):
        left, right = normalise(_dump(graph)), normalise(_dump(coded))
        cyclic = CYCLE in json.dumps([left, right])
        return "agree" if left == right and not cyclic else "plans_differ"
    if any(proposing):
        return "propose_decline"
    return "agree" if graph.kind == coded.kind else "declines_differ"


def _comparisons(plan, kind):
    return any(isinstance(c, dict) and c.get("kind") == kind for c in plan.get("comparisons") or [])


def _undeclared(plan, kinds):
    return [d for d in plan.get("derived") or [] if isinstance(d, dict) and d.get("kind") in kinds and not d.get("declaration")]


def _time_pieces(pieces):
    return sum(1 for p in pieces if p.kind == "time")


def _time_grain(plan, pieces, markers):
    return any(isinstance(d, dict) and d.get("kind") == "time" for d in plan.get("dimensions") or [])


def _range(plan, pieces, markers):
    return (plan.get("time") or {}).get("kind") == "range" and _time_pieces(pieces) == 2


def _explicit_period(plan, pieces, markers):
    return _comparisons(plan, "explicit_period") and "from_to" not in markers


def _from_to(plan, pieces, markers):
    return _comparisons(plan, "explicit_period") and "from_to" in markers


def _comparison(kind):
    def test(plan, pieces, markers):
        return _comparisons(plan, kind)
    test.__doc__ = f"A comparison of kind {kind}."
    return test


def _derived(plan, pieces, markers):
    compared = {c.get("id") for c in plan.get("comparisons") or [] if isinstance(c, dict)}
    return any(isinstance(d.get("numerator"), str) and "/" in d["numerator"] and d["numerator"].split("/")[0] in compared
               for d in _undeclared(plan, ("difference", "percent_change")))


def _derived_kind(kind):
    def test(plan, pieces, markers):
        return bool(_undeclared(plan, (kind,)))
    test.__doc__ = f"A derived value of kind {kind} with no declaration."
    return test


def _ranking(plan, pieces, markers):
    return bool(plan.get("order"))


def _parts(plan, pieces, markers):
    keys = [((m.get("measure") or {}).get("id"), (m.get("context") or {}).get("id"))
            for m in plan.get("measures") or [] if isinstance(m, dict) and m.get("kind") == "measure"]
    return len(keys) != len(set(keys))


Signature = Callable[[dict, "Iterable[Piece]", "frozenset[str]"], bool]

CODED_SIGNATURES: tuple[tuple[str, Signature], ...] = (
    ("planshapes.compose:time_grain", _time_grain),
    ("planshapes.compose:range", _range),
    ("planshapes.compose:explicit_period", _explicit_period),
    ("planshapes.compose:from_to", _from_to),
    ("planshapes.compose:previous_period", _comparison("previous_period")),
    ("planshapes.compose:previous_year", _comparison("previous_year")),
    ("planshapes.compose:time_mode", _comparison("time_mode")),
    ("planshapes.compose:contexts", _comparison("contexts")),
    ("planshapes.compose:derived", _derived),
    ("planshapes.compose:ratio", _derived_kind("ratio")),
    ("planshapes.compose:share", _derived_kind("share")),
    ("planshapes.compose:running_total", _derived_kind("running_total")),
    ("planshapes.compose:moving_average", _derived_kind("moving_average")),
    ("planshapes.compose:ranking", _ranking),
    ("planshapes.parts", _parts),
)
"""Each coded builder (standards/preload.yaml coded_builders) and the test that tells, from the coded plan, the
ledger's pieces and the marker names, that the builder shaped the plan:

- compose:time_grain: a dimension of kind time;
- compose:range: a range period and exactly two time pieces in the ledger, which leaves out the planner's own all-time
  snapshot range;
- compose:explicit_period: an explicit_period comparison with no from_to marker; compose:from_to: the same with one;
- compose:previous_period, previous_year, time_mode and contexts: a comparison of that kind;
- compose:derived: a difference or percent_change with no declaration whose numerator is a comparison's output path;
- compose:ratio, share, running_total and moving_average: a derived value of that kind with no declaration (a declared
  formula is slot binding, not a shape);
- compose:ranking: a non-empty order;
- parts: two or more measure outputs of kind measure on one (measure, context)."""

SIGNED = frozenset(builder for builder, _ in CODED_SIGNATURES)
"""Every builder a signature can attribute: the preload's coded_builders, exactly."""


def coded_shapes(plan: dict | None, pieces: Iterable[Piece], markers: frozenset[str] = frozenset(),
                 mirrors: Mapping[str, str] = MappingProxyType({})) -> tuple[str, ...]:
    """The coded builders whose signature the coded plan dict holds, in CODED_SIGNATURES order, each renamed by mirrors
    (a builder to the shape id that replaces it); () for a decline (plan None)."""
    if plan is None:
        return ()
    pieces = list(pieces)
    markers = frozenset(markers)
    found = (mirrors.get(builder, builder) for builder, test in CODED_SIGNATURES if test(plan, pieces, markers))
    return tuple(dict.fromkeys(found))


def attributed(graph_shapes: Iterable[str], coded: Iterable[str]) -> tuple[str, ...]:
    """Every shape either side used, sorted: a combined question counts towards each."""
    return tuple(sorted(set(graph_shapes) | set(coded)))


def _spans(node, found):
    """Every span's [start, end] at any depth of a dumped reply."""
    if isinstance(node, dict):
        if set(node) == {"turn", "start", "end"}:
            found.add((node["start"], node["end"]))
        for child in node.values():
            _spans(child, found)
    elif isinstance(node, (list, tuple)):
        for child in node:
            _spans(child, found)
    return found


def _applied(reply: Reply) -> list[list[int]]:
    """The sorted positions of every span the reply applied: a plan's evidence, its applied obligations' spans (not an
    unresolved, ambiguous or removed one's), and a clarification's or an abstention's spans. Positions alone: a
    follow-up's spans from its earlier turn are merged with the question's, which review evidence can bear."""
    dump = reply.model_dump(mode="json", by_alias=True)
    found: set[tuple[int, int]] = set()
    if reply.kind == "propose":
        plan = dict(dump["plan"])
        obligations = plan.pop("obligations", None) or []
        _spans(plan, found)
        _spans([o for o in obligations if isinstance(o, dict) and o.get("state") == "applied"], found)
    else:
        _spans(dump.get("spans"), found)
    return [list(span) for span in sorted(found)]


TYPED_ON_OUTPUTS = frozenset({"text", "date"})
"""The scalar kinds whose value evidence drops where a compare or IN tests an output, not a column: a word or a date
can come from the question, while a numeric threshold is the plan's stated figure and stays."""


def _typed_outputs(node):
    """node with each text or date value of a compare or IN on an output replaced by its kind alone, at any depth."""
    if isinstance(node, list):
        return [_typed_outputs(child) for child in node]
    if not isinstance(node, dict):
        return node
    node = {key: _typed_outputs(child) for key, child in node.items()}
    left = node.get("left")
    if node.get("kind") in ("compare", "in") and isinstance(left, dict) and left.get("kind") == "output":
        def typed(value):
            if isinstance(value, dict) and value.get("kind") in TYPED_ON_OUTPUTS:
                return {k: v for k, v in value.items() if k != "value"}
            return value
        if "value" in node:
            node["value"] = typed(node["value"])
        if isinstance(node.get("values"), list):
            node["values"] = [typed(v) for v in node["values"]]
    return node


def _side(reply: Reply, vocab) -> dict:
    """What evidence keeps of one side's reply: never an abstention's missing list.

    The plan is redacted before it is normalised, so each label is made from redacted content (a label made from an
    entity key could be matched by hashing every likely key): planpublic.receipt_plan, with no renaming, types a value
    on a column that is not one of its declared members, and _typed_outputs a text or date value on an output."""
    plan = (normalise(_typed_outputs(planpublic.receipt_plan(_dump(reply), vocab))) if reply.kind == "propose"
            else None)
    candidates = sorted({name.id for name in reply.candidates}) if reply.kind == "clarify" else []
    return {"outcome": reply.kind, "plan": plan, "candidates": candidates, "applied": _applied(reply)}


def _size(value) -> int:
    return len(canonical(value).encode("utf-8"))


def review_evidence(graph: Reply, coded: Reply, pieces: Iterable[Piece], vocab, room: int = record.MAX_LINE) -> dict:
    """The review evidence for one disagreement or takeover: {"graph": side, "coded": side, "ledger": [...]}.

    A side is its outcome, its plan as a receipt keeps it (planpublic.receipt_plan, and text or date values on an
    output typed) in normal form, or None for a decline, a clarification's sorted candidate ids, and the sorted
    positions of every span the reply applied. The ledger is each piece's kind, operation and positions, never its
    text, value, candidates or window.

    Where the evidence would be longer than room bytes, each IN list is collapsed to kinds and counts
    (planpublic.collapsed_values); where it still would, both plans are dropped and each side marked truncated; where
    it still would, the ledger and both sides' applied positions are emptied too. What is left is bounded by the
    candidates alone, and record.line_of refuses a line that is still too long."""
    evidence = {"graph": _side(graph, vocab), "coded": _side(coded, vocab),
                "ledger": [{"kind": p.kind, "operation": p.operation, "start": p.span.start, "end": p.span.end}
                           for p in pieces]}
    if _size(evidence) <= room:
        return evidence
    for name in ("graph", "coded"):
        if evidence[name]["plan"] is not None:
            evidence[name]["plan"] = planpublic.collapsed_values(evidence[name]["plan"])
    if _size(evidence) <= room:
        return evidence
    for name in ("graph", "coded"):
        evidence[name].update(plan=None, truncated=True)
    if _size(evidence) <= room:
        return evidence
    evidence["ledger"] = []
    for name in ("graph", "coded"):
        evidence[name]["applied"] = []
    return evidence


def observe(*, release: str, graph: Reply, coded: Reply, graph_shapes: Iterable[str], served: str,
            fallback: bool = False, takeover: bool = False, new_phrase: bool = False, pieces: Iterable[Piece], vocab,
            markers: frozenset[str] = frozenset(), mirrors: Mapping[str, str] = MappingProxyType({}),
            now: datetime | None = None, new_id: Callable[[], str] | None = None) -> ShadowObservation:
    """One comparison as an observation, checked by the shadow log's record rules (record.line_of), whose ValueError
    propagates. Evidence is kept for a disagreement or a takeover and only then. The time is written in UTC whatever
    zone now is in, and a shape the graph matched twice is kept once."""
    pieces = list(pieces)
    graph_shapes = tuple(dict.fromkeys(graph_shapes))
    coded_names = coded_shapes(_dump(coded) if coded.kind == "propose" else None, pieces, markers, mirrors)
    comparison = compare(graph, coded)
    observation = ShadowObservation(
        id=new_id() if new_id else secrets.token_hex(16),
        time=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        release=release, graph_shapes=graph_shapes, coded_shapes=coded_names,
        shapes=attributed(graph_shapes, coded_names), graph_outcome=outcome_of(graph), coded_outcome=outcome_of(coded),
        comparison=comparison, served=served, fallback=fallback, takeover=takeover, new_phrase=new_phrase,
        evidence=None)
    if comparison != "agree" or takeover:
        rest = _size(asdict(observation)) + 1 - len("null")
        observation = replace(observation, evidence=review_evidence(graph, coded, pieces, vocab, room=record.MAX_LINE - rest))
    record.line_of(observation)
    return observation
