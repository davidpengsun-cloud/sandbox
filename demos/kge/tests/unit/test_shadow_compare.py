"""The shadow comparison (#516, kge/services/ask/engine/shadow.py): what normalisation ignores and keeps, every comparison
outcome, coded-shape attribution one planted plan per signature, redaction of the evidence a record keeps, when evidence
is kept, and the observation's own checks. Synthetic plans only, built as dicts and validated as QueryPlan."""
from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters.shadowlog.record import MAX_LINE, line_of
from kge.compiler.standards import load
from kge.semantics.coverage import Piece
from kge.semantics.plan import Abstain, Clarify, Propose, QueryPlan, Span
from kge.services.ask.engine import shadow

SOURCE = Path(shadow.__file__)


def name(i, start=None):
    return {"id": i, "evidence": [] if start is None else [{"turn": "t", "start": start, "end": start + 2}]}


def period(value, kind="month"):
    return {"kind": kind, "value": value, "calendar": name("cal:c")}


def measure(i="m1", context="ctx:a", filters=(), measure_id="measure:x"):
    return {"kind": "measure", "id": i, "measure": name(measure_id), "context": name(context), "mode": "current",
            "filters": list(filters)}


def compare_on(i, column, value, op="eq", start=None):
    return {"kind": "compare", "id": i, "left": {"kind": "column", "name": name(column), "path": []}, "op": op,
            "value": value, "evidence": [] if start is None else [{"turn": "t", "start": start, "end": start + 3}]}


def text(value):
    return {"kind": "text", "value": value}


def column_dimension(i, contexts=("ctx:a",), column="col:t.d"):
    return {"kind": "column", "id": i, "bindings": [{"context": name(c), "column": name(column), "path": [], "level": None}
                                                   for c in contexts]}


def time_dimension(i="t0", grain="month"):
    return {"kind": "time", "id": i, "calendar": name("cal:c"), "grain": grain}


def plan(**parts):
    base = {"schema": "query-plan/v1", "layout": "aligned", "measures": [measure()], "dimensions": [], "filters": [],
            "time": {"kind": "single", "period": period("2026-01")}, "comparisons": [], "derived": [], "order": [],
            "limit": None, "obligations": []}
    base.update(parts)
    return QueryPlan.model_validate(base).model_dump(mode="json", by_alias=True)


def propose(data):
    return Propose.model_validate({"kind": "propose", "base_turn": None, "plan": data})


def clarify(*candidates, start=0):
    return Clarify.model_validate({"kind": "clarify", "spans": [{"turn": "t", "start": start, "end": start + 4}],
                                   "candidates": [name(c) for c in candidates]})


def abstain(*missing, start=0):
    return Abstain.model_validate({"kind": "abstain", "spans": [{"turn": "t", "start": start, "end": start + 4}],
                                   "missing": list(missing)})


def piece(kind, start, end, operation=None, text_="w", value=None):
    return Piece(span=Span(turn="t", start=start, end=end), text=text_, kind=kind, operation=operation, value=value)


VOCAB = SimpleNamespace(entries={
    "col:t.k": SimpleNamespace(kind="column", column="t.k", value=None),
    "col:t.s": SimpleNamespace(kind="column", column="t.s", value=None),
    "col:t.d": SimpleNamespace(kind="column", column="t.d", value=None),
    "val:t.s.open": SimpleNamespace(kind="value", column="t.s", value="OPEN"),
})


def rich(ids, permuted=False, offset=0, obligations=True):
    """One plan using every unordered list and every kind of reference (a not's child, a membership's filters, a
    share's removed dimensions and filters, a contexts comparison's sides, a derived path, an output operand), its node
    ids taken from ids, its unordered lists reversed when permuted and its evidence spans moved by offset."""
    flip = (lambda xs: list(reversed(xs))) if permuted else list
    f1 = compare_on(ids["f1"], "col:t.s", text("OPEN"), start=offset)
    f2 = compare_on(ids["f2"], "col:t.k", text("K-1"), start=offset + 5)
    f3 = compare_on(ids["f3"], "col:t.k", text("K-2"))
    junction = {"kind": "any", "id": ids["j"], "children": flip([f2, f3]), "evidence": []}
    in_filter = {"kind": "in", "id": ids["in"], "left": {"kind": "column", "name": name("col:t.s"), "path": []},
                 "values": flip([text("A"), text("B"), text("C")]), "evidence": []}
    negated = {"kind": "not", "id": ids["nt"], "child": compare_on(ids["nc"], "col:t.k", text("K-4")), "evidence": []}
    member = {"kind": "membership", "id": ids["mb"], "entity": name("ent:e"), "relation": name("rel:r"),
              "context": name("ctx:a"), "evidence": [],
              "filters": flip([compare_on(ids["mb1"], "col:t.s", text("OPEN")), compare_on(ids["mb2"], "col:t.k", text("K-5"))])}
    c, m, m2 = ids["c"], ids["m1"], ids["m2"]
    on_output = {"kind": "compare", "id": ids["oo"], "left": {"kind": "output", "id": f"{c}/{m}/base"}, "op": "gt",
                 "value": {"kind": "integer", "value": 5}, "evidence": []}
    m1 = measure(m, filters=flip([compare_on(ids["mf1"], "col:t.k", text("K-3")),
                                  compare_on(ids["mf2"], "col:t.s", text("OPEN"))]))
    dims = [column_dimension(ids["d1"], contexts=flip(["ctx:a", "ctx:b"])), column_dimension(ids["d2"], column="col:t.k"),
            time_dimension(ids["t"])]
    data = {
        "measures": [m1, measure(m2, context="ctx:b")],
        "dimensions": dims,
        "filters": flip([f1, junction, in_filter, negated, member, on_output]),
        "comparisons": [{"kind": "previous_period", "id": c, "inputs": [m], "evidence": []},
                        {"kind": "contexts", "id": ids["cc"], "left": m, "right": m2, "evidence": []}],
        "derived": [
            {"kind": "difference", "id": ids["x"], "numerator": f"{c}/{m}/base", "denominator": f"{c}/{m}/other",
             "evidence": []},
            {"kind": "running_total", "id": ids["rt"], "input": m, "partition_by": flip([ids["d1"], ids["d2"]]),
             "order_by": ids["t"], "evidence": []},
            {"kind": "share", "id": ids["sh"], "input": f"{ids['cc']}/base", "evidence": [],
             "remove_dimensions": flip([ids["d1"], ids["d2"]]), "remove_filters": flip([ids["f1"], ids["j"]])},
        ],
        "order": [{"output": m, "direction": "desc", "nulls": "last", "evidence": [],
                   "partition_by": flip([ids["d1"], ids["t"]])}],
        "limit": 5,
        "obligations": ([{"id": "o1", "spans": [{"turn": "t", "start": offset, "end": offset + 4}], "kind": "name",
                          "targets": [m], "state": "applied"}] if obligations else []),
    }
    return plan(**data)


IDS = {k: "id-" + k for k in ("f1", "f2", "f3", "j", "in", "nt", "nc", "mb", "mb1", "mb2", "oo", "m1", "m2", "mf1", "mf2", "d1",
                      "d2", "t", "c", "cc", "x", "rt", "sh")}
RENAMED = {k: "z-" + k + "-q" for k in IDS}


def raw_ids(normal, ids):
    """Every raw id from ids that survives in a normal form, as a whole string or a path segment."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            found.update(part for part in node.split("/") if part in ids)
    walk(normal)
    return found


def test_normalisation_ignores_ids_evidence_obligations_and_unordered_lists():
    a = rich(IDS)
    b = rich(RENAMED, permuted=True, offset=11, obligations=False)
    assert a != b
    assert shadow.normalise(a) == shadow.normalise(b)
    normal = shadow.normalise(a)
    assert "obligations" not in normal
    assert "evidence" not in json.dumps(normal)
    assert raw_ids(normal, set(IDS.values())) == set()
    labels = {n["id"] for key in ("measures", "dimensions", "comparisons", "derived", "filters") for n in normal[key]}
    assert all(label.startswith("#") and len(label) == 13 for label in labels)
    difference = normal["derived"][0]
    c, m, side = difference["numerator"].split("/")
    assert (c, m, side) == (normal["comparisons"][0]["id"], normal["measures"][0]["id"], "base")


def cyclic(share, flt, start=0):
    """A share whose remove_filters names a filter on that share's own output: a cycle QueryPlan allows."""
    on_share = {"kind": "compare", "id": flt, "left": {"kind": "output", "id": share}, "op": "gt",
                "value": {"kind": "integer", "value": 1}, "evidence": [{"turn": "t", "start": start, "end": start + 2}]}
    return plan(dimensions=[column_dimension("d1")], filters=[on_share],
                derived=[{"kind": "share", "id": share, "input": "m1", "remove_dimensions": ["d1"],
                          "remove_filters": [flt], "evidence": []}])


def test_a_reference_cycle_keeps_no_raw_id_and_ignores_ids():
    a, b = shadow.normalise(cyclic("s", "f")), shadow.normalise(cyclic("share-q", "filter-q", start=4))
    assert a == b
    assert raw_ids(a, {"s", "f", "m1", "d1"}) == set()
    assert shadow.CYCLE in json.dumps(a)


def test_a_cyclic_plan_never_agrees_even_with_itself():
    assert shadow.compare(propose(cyclic("s", "f")), propose(cyclic("s", "f"))) == "plans_differ"


TWO = [measure("m1"), measure("m2", context="ctx:b")]
RATIOS = [{"kind": "ratio", "id": "r1", "numerator": "m1", "denominator": "m2", "evidence": []},
          {"kind": "ratio", "id": "r2", "numerator": "m2", "denominator": "m1", "evidence": []}]
DIMS = [column_dimension("d1"), column_dimension("d2", column="col:t.k")]
ORDERS = [{"output": "m1", "direction": "desc", "nulls": "last", "evidence": []},
          {"output": "m2", "direction": "asc", "nulls": "last", "evidence": []}]
PREV = {"kind": "previous_period", "id": "c", "inputs": ["m1", "m2"], "evidence": []}
YEAR = {"kind": "previous_year", "id": "y", "inputs": ["m1"], "evidence": []}
SIDES = {"kind": "contexts", "id": "k", "left": "m1", "right": "m2", "evidence": []}
DIFF = {"kind": "difference", "id": "x", "numerator": "c/m1/base", "denominator": "c/m1/other", "evidence": []}
ANY = {"kind": "any", "id": "j", "evidence": [], "children": [compare_on("j1", "col:t.s", text("A")),
                                                              compare_on("j2", "col:t.s", text("B"))]}
MOVED = compare_on("mf", "col:t.s", text("OPEN"))

KEEPS = {
    "measure_order": (dict(measures=TWO), dict(measures=TWO[::-1])),
    "derived_order": (dict(measures=TWO, derived=RATIOS), dict(measures=TWO, derived=RATIOS[::-1])),
    "op": (dict(filters=[MOVED]), dict(filters=[{**MOVED, "op": "ne"}])),
    "period": (dict(), dict(time={"kind": "single", "period": period("2026-02")})),
    "limit": (dict(limit=3), dict(limit=4)),
    "dimension_order": (dict(dimensions=DIMS), dict(dimensions=DIMS[::-1])),
    "order_list": (dict(measures=TWO, order=ORDERS), dict(measures=TWO, order=ORDERS[::-1])),
    "comparison_inputs": (dict(measures=TWO, comparisons=[PREV]),
                          dict(measures=TWO, comparisons=[{**PREV, "inputs": ["m2", "m1"]}])),
    "comparison_order": (dict(measures=TWO, comparisons=[PREV, YEAR]), dict(measures=TWO, comparisons=[YEAR, PREV])),
    "contexts_sides": (dict(measures=TWO, comparisons=[SIDES]),
                       dict(measures=TWO, comparisons=[{**SIDES, "left": "m2", "right": "m1"}])),
    "derived_sides": (dict(measures=TWO, comparisons=[PREV], derived=[DIFF]),
                      dict(measures=TWO, comparisons=[PREV],
                           derived=[{**DIFF, "numerator": "c/m1/other", "denominator": "c/m1/base"}])),
    "junction_kind": (dict(filters=[ANY]), dict(filters=[{**ANY, "kind": "all"}])),
    "ratio_sides": (dict(measures=TWO, derived=RATIOS[:1]),
                    dict(measures=TWO, derived=[{**RATIOS[0], "numerator": "m2", "denominator": "m1"}])),
    "filter_scope": (dict(measures=[measure("m1", filters=[MOVED]), TWO[1]]), dict(measures=TWO, filters=[MOVED])),
}


@pytest.mark.parametrize("change", sorted(KEEPS))
def test_normalisation_keeps_what_changes_the_answer(change):
    base, other = KEEPS[change]
    assert shadow.normalise(plan(**copy.deepcopy(base))) != shadow.normalise(plan(**copy.deepcopy(other)))


P = propose(plan())
Q = propose(plan(limit=7))


@pytest.mark.parametrize("graph, coded, expected", [
    (P, propose(plan()), "agree"),
    (P, Q, "plans_differ"),
    (P, clarify("ctx:a"), "propose_decline"),
    (abstain("x"), P, "propose_decline"),
    (P, abstain("x"), "propose_decline"),
    (clarify("ctx:a"), clarify("ctx:b", "ctx:c"), "agree"),
    (abstain("one"), abstain("two"), "agree"),
    (clarify("ctx:a"), abstain("x"), "declines_differ"),
])
def test_every_comparison_outcome(graph, coded, expected):
    assert shadow.compare(graph, coded) == expected
    assert shadow.outcome_of(graph) == graph.kind


def test_comparison_reads_the_unredacted_plans():
    a = propose(plan(filters=[compare_on("f", "col:t.k", text("ACC-0001"))]))
    b = propose(plan(filters=[compare_on("f", "col:t.k", text("ACC-0002"))]))
    redacted = [shadow.review_evidence(a, b, [], VOCAB)[side]["plan"] for side in ("graph", "coded")]
    assert redacted[0] == redacted[1]
    assert shadow.compare(a, b) == "plans_differ"


TWO_TIMES = [piece("time", 0, 4), piece("time", 8, 12)]
TWO_MEASURES = [measure("m1"), measure("m2", context="ctx:b")]
PREVIOUS = {"kind": "previous_period", "id": "c", "inputs": ["m1"], "evidence": []}
RANGE = {"kind": "range", "first": period("2026-01"), "last": period("2026-03")}

SIGNATURE_PLANS = {
    "planshapes.compose:time_grain": (dict(dimensions=[time_dimension()]), [], frozenset()),
    "planshapes.compose:range": (dict(time=RANGE), TWO_TIMES, frozenset()),
    "planshapes.compose:explicit_period": (dict(comparisons=[{"kind": "explicit_period", "id": "c", "inputs": ["m1"],
        "other": {"kind": "single", "period": period("2025-01")}, "evidence": []}]), TWO_TIMES, frozenset()),
    "planshapes.compose:from_to": (dict(comparisons=[{"kind": "explicit_period", "id": "c", "inputs": ["m1"],
        "other": {"kind": "single", "period": period("2025-01")}, "evidence": []}]), TWO_TIMES, frozenset({"from_to"})),
    "planshapes.compose:previous_period": (dict(comparisons=[PREVIOUS]), [], frozenset()),
    "planshapes.compose:previous_year": (dict(comparisons=[{**PREVIOUS, "kind": "previous_year"}]), [], frozenset()),
    "planshapes.compose:time_mode": (dict(comparisons=[{"kind": "time_mode", "id": "c", "inputs": ["m1"],
        "base": "current", "other": "as_reported", "evidence": []}]), [], frozenset()),
    "planshapes.compose:contexts": (dict(measures=TWO_MEASURES, comparisons=[{"kind": "contexts", "id": "c",
        "left": "m1", "right": "m2", "evidence": []}]), [], frozenset()),
    "planshapes.compose:derived": (dict(comparisons=[PREVIOUS], derived=[{"kind": "percent_change", "id": "x",
        "numerator": "c/m1/base", "denominator": "c/m1/other", "evidence": []}]), [], frozenset()),
    "planshapes.compose:ratio": (dict(measures=TWO_MEASURES, derived=[{"kind": "ratio", "id": "r", "numerator": "m1",
        "denominator": "m2", "evidence": []}]), [], frozenset()),
    "planshapes.compose:share": (dict(dimensions=[column_dimension("d1")], derived=[{"kind": "share", "id": "s",
        "input": "m1", "remove_dimensions": ["d1"], "remove_filters": [], "evidence": []}]), [], frozenset()),
    "planshapes.compose:running_total": (dict(dimensions=[time_dimension("t")], derived=[{"kind": "running_total",
        "id": "w", "input": "m1", "partition_by": [], "order_by": "t", "evidence": []}]), [], frozenset()),
    "planshapes.compose:moving_average": (dict(dimensions=[time_dimension("t")], derived=[{"kind": "moving_average",
        "id": "w", "input": "m1", "partition_by": [], "order_by": "t", "length": 3, "evidence": []}]), [], frozenset()),
    "planshapes.compose:ranking": (dict(dimensions=[column_dimension("d1")], order=[{"output": "m1",
        "direction": "desc", "nulls": "last", "evidence": []}], limit=3), [], frozenset()),
    "planshapes.parts": (dict(measures=[measure("m1", filters=[compare_on("p1", "col:t.s", text("OPEN"))]),
        measure("m2", filters=[compare_on("p2", "col:t.s", text("SHUT"))])]), [], frozenset()),
}

EXPECTED = {
    "planshapes.compose:derived": ("planshapes.compose:previous_period", "planshapes.compose:derived"),
    "planshapes.compose:running_total": ("planshapes.compose:time_grain", "planshapes.compose:running_total"),
    "planshapes.compose:moving_average": ("planshapes.compose:time_grain", "planshapes.compose:moving_average"),
}


@pytest.mark.parametrize("builder", sorted(SIGNATURE_PLANS))
def test_one_planted_plan_per_signature(builder):
    parts, pieces, markers = SIGNATURE_PLANS[builder]
    assert shadow.coded_shapes(plan(**parts), pieces, markers) == EXPECTED.get(builder, (builder,))


def test_every_signature_has_a_planted_plan():
    assert set(SIGNATURE_PLANS) == shadow.SIGNED


def test_signatures_leave_out_what_the_coded_builders_did_not_make():
    assert shadow.coded_shapes(plan(time=RANGE), [piece("time", 0, 4)]) == ()
    declared = {"kind": "ratio", "id": "r", "numerator": "m1", "denominator": "m2", "evidence": [],
                "declaration": name("metric:r")}
    assert shadow.coded_shapes(plan(measures=TWO_MEASURES, derived=[declared]), []) == ()
    unpaired = {"kind": "difference", "id": "x", "numerator": "m1", "denominator": "m2", "evidence": []}
    assert shadow.coded_shapes(plan(measures=TWO_MEASURES, derived=[unpaired]), []) == ()
    assert shadow.coded_shapes(None, TWO_TIMES) == ()


def test_a_combined_plan_counts_towards_every_builder():
    data = plan(time=RANGE, dimensions=[time_dimension(), column_dimension("d1")],
                order=[{"output": "m1", "direction": "asc", "nulls": "last", "evidence": []}], limit=2)
    assert shadow.coded_shapes(data, TWO_TIMES) == (
        "planshapes.compose:time_grain", "planshapes.compose:range", "planshapes.compose:ranking")
    assert shadow.attributed(("shape.b", "shape.a"), ("planshapes.compose:range", "shape.a")) == (
        "planshapes.compose:range", "shape.a", "shape.b")


def test_mirrors_rename_a_builder():
    parts, pieces, markers = SIGNATURE_PLANS["planshapes.compose:range"]
    assert shadow.coded_shapes(plan(**parts), pieces, markers, {"planshapes.compose:range": "shape.range"}) == (
        "shape.range",)


def test_signed_is_the_preloads_coded_builders():
    assert shadow.SIGNED == set(load().coded_builders)


def observe(graph, coded, **overrides):
    arguments = dict(release="rel-1", graph=graph, coded=coded, graph_shapes=("shape.a",), served="coded", pieces=[],
                     vocab=VOCAB)
    arguments.update(overrides)
    return shadow.observe(**arguments)


def test_evidence_keeps_no_entity_key_question_word_or_missing():
    graph = propose(plan(filters=[
        compare_on("f1", "col:t.k", text("ACC-0042"), start=3),
        compare_on("f2", "col:t.s", text("OPEN")),
        {"kind": "compare", "id": "f3", "left": {"kind": "output", "id": "m1"}, "op": "gt",
         "value": {"kind": "integer", "value": 987654}, "evidence": []}]))
    coded = abstain("Zyzzyva", start=9)
    pieces = [piece("name", 9, 22, text_="Acme Holdings", value="Acme Holdings"), piece("time", 30, 34, "month")]
    observation = observe(graph, coded, pieces=pieces)
    line = line_of(observation)
    for word in (b"ACC-0042", b"Acme", b"Holdings", b"Zyzzyva"):
        assert word not in line
    assert b"OPEN" in line and b"987654" in line
    evidence = json.loads(line)["evidence"]
    assert evidence["ledger"] == [{"kind": "name", "operation": None, "start": 9, "end": 22},
                                  {"kind": "time", "operation": "month", "start": 30, "end": 34}]
    assert evidence["graph"]["applied"] == [[3, 6]]
    assert evidence["coded"] == {"outcome": "abstain", "plan": None, "candidates": [], "applied": [[9, 13]]}


def test_a_word_or_date_on_an_output_never_reaches_the_line():
    graph = propose(plan(filters=[
        {"kind": "compare", "id": "f1", "left": {"kind": "output", "id": "m1"}, "op": "eq", "value": text("SECRETWORD"),
         "evidence": []},
        {"kind": "compare", "id": "f2", "left": {"kind": "output", "id": "m1"}, "op": "ge",
         "value": {"kind": "date", "value": "2031-07-19"}, "evidence": []},
        {"kind": "in", "id": "f3", "left": {"kind": "output", "id": "m1"}, "evidence": [],
         "values": [text("OTHERWORD"), {"kind": "date", "value": "2032-08-21"}, {"kind": "integer", "value": 424242}]}]))
    line = line_of(observe(graph, abstain("x")))
    for word in (b"SECRETWORD", b"OTHERWORD", b"2031-07-19", b"2032-08-21"):
        assert word not in line
    assert b"424242" in line


def test_applied_keeps_only_applied_obligations():
    data = plan(obligations=[
        {"id": f"o{n}", "spans": [{"turn": "t", "start": 10 * n, "end": 10 * n + 3}], "kind": "name", "targets": ["m1"],
         "state": state} for n, state in enumerate(("applied", "unresolved", "ambiguous", "removed"), 1)],
        measures=[measure("m1")], filters=[compare_on("f", "col:t.s", text("OPEN"), start=50)])
    data["measures"][0]["measure"]["evidence"] = [{"turn": "t", "start": 60, "end": 62}]
    evidence = shadow.review_evidence(propose(data), abstain("x", start=70), [], VOCAB)
    assert evidence["graph"]["applied"] == [[10, 13], [50, 53], [60, 62]]
    assert evidence["coded"]["applied"] == [[70, 74]]
    assert shadow.review_evidence(clarify("ctx:a", start=5), P, [], VOCAB)["graph"]["applied"] == [[5, 9]]


def test_a_line_too_long_without_plans_drops_the_ledger_and_positions():
    pieces = [piece("name", n, n + 1, "dimension") for n in range(6000)]
    observation = observe(P, Q, pieces=pieces)
    line = line_of(observation)
    assert len(line) <= MAX_LINE
    evidence = observation.evidence
    assert evidence["ledger"] == []
    for side in ("graph", "coded"):
        assert evidence[side] == {"outcome": "propose", "plan": None, "candidates": [], "applied": [],
                                  "truncated": True}


def test_a_clarifications_candidate_ids_are_kept_sorted():
    evidence = shadow.review_evidence(clarify("ctx:b", "ctx:a"), P, [], VOCAB)
    assert evidence["graph"]["candidates"] == ["ctx:a", "ctx:b"]


def test_a_clarification_between_spaced_member_values_is_recorded():
    candidates = ("value:t.s:North Region", "value:t.r:North Region")
    observation = observe(propose(plan()), clarify(*candidates))
    assert observation.comparison == "propose_decline"
    assert json.loads(line_of(observation))["evidence"]["coded"]["candidates"] == sorted(candidates)


def test_a_long_plan_collapses_then_truncates():
    values = [text(f"V{n:05d}") for n in range(400)]
    graph = propose(plan(filters=[{"kind": "in", "id": "f", "left": {"kind": "column", "name": name("col:t.s"),
                                                                       "path": []}, "values": values, "evidence": []}]))
    whole = shadow.review_evidence(graph, P, [], VOCAB)
    size = len(json.dumps(whole).encode())
    collapsed = shadow.review_evidence(graph, P, [], VOCAB, room=size // 2)
    assert collapsed["graph"]["plan"]["filters"][0]["values"] == [{"kind": "text", "count": 400}]
    assert "truncated" not in collapsed["graph"]
    dropped = shadow.review_evidence(graph, P, [], VOCAB, room=200)
    assert dropped["graph"]["plan"] is None and dropped["coded"]["plan"] is None
    assert dropped["graph"]["truncated"] is True and dropped["coded"]["truncated"] is True


@pytest.mark.parametrize("graph, coded, served, takeover, kept", [
    (P, propose(plan()), "coded", False, False),
    (P, Q, "coded", False, True),
    (P, abstain("x"), "coded", False, True),
    (clarify("ctx:a"), abstain("x"), "coded", False, True),
    (P, abstain("x"), "graph", True, True),
])
def test_evidence_is_kept_for_a_disagreement_or_a_takeover(graph, coded, served, takeover, kept):
    observation = observe(graph, coded, served=served, takeover=takeover)
    assert (observation.evidence is not None) is kept
    assert observation.takeover is takeover


def test_observations_are_fresh_utc_and_checked():
    first, second = observe(P, Q), observe(P, Q)
    assert first.id != second.id
    parsed = datetime.fromisoformat(first.time)
    assert parsed.utcoffset() == timedelta(0) and len(first.time.split(".")[1]) == len("000+00:00")
    fixed = observe(P, Q, now=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc), new_id=lambda: "ab" * 16)
    assert (fixed.id, fixed.time) == ("ab" * 16, "2026-01-02T03:04:05.678+00:00")
    assert observe(P, propose(plan()), served="model").served == "model"
    with pytest.raises(ValueError):
        observe(P, propose(plan()), served="graph", takeover=True)
    with pytest.raises(ValueError):
        observe(P, abstain("x"), served="graph", fallback=True, takeover=True)
    assert observe(P, Q, new_phrase=True).new_phrase is True
    assert observe(P, Q).new_phrase is False


def test_observation_carries_shapes_and_outcomes():
    parts, pieces, _ = SIGNATURE_PLANS["planshapes.compose:range"]
    observation = observe(P, propose(plan(**parts)), pieces=pieces, graph_shapes=("shape.z",))
    assert observation.coded_shapes == ("planshapes.compose:range",)
    assert observation.shapes == ("planshapes.compose:range", "shape.z")
    assert (observation.graph_outcome, observation.coded_outcome, observation.comparison) == (
        "propose", "propose", "plans_differ")


def keys(node, found):
    if isinstance(node, dict):
        for key, child in node.items():
            found.append(key)
            keys(child, found)
    elif isinstance(node, list):
        for child in node:
            keys(child, found)
    return found


def test_the_line_holds_no_evidence_or_obligations_key_inside_its_evidence():
    graph = propose(rich(IDS))
    line = json.loads(line_of(observe(graph, clarify("ctx:a", start=2), pieces=TWO_TIMES)))
    found = keys(line, [])
    assert found.count("evidence") == 1 and "evidence" in line
    assert "obligations" not in found and "spans" not in found and "text" not in found


def test_shadow_imports_no_adapter_backend_warehouse_or_model():
    """What shadow.py's source imports and names, read as text: the shadow log's record rules are its only adapter
    import, and it names no warehouse, model, store or graph call. Importing the record rules does load the shadow log
    package's own imports; this checks the module's source, not what it loads."""
    tree = ast.parse(SOURCE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    adapters = {module for module in imported if module.startswith("kge.adapters")}
    assert adapters <= {"kge.adapters.shadowlog", "kge.adapters.shadowlog.record"}
    assert not {module for module in imported if module.startswith(("kge.pipeline", "duckdb", "google", "ladybug"))}
    source = SOURCE.read_text()
    for call in ("open_warehouses", "model_for", "open_store", "open_retrieval", "graph_q", "connect("):
        assert call not in source
