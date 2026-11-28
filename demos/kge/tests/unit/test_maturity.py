"""Synthetic maturity boundaries, release histories and coupled-shape decisions."""
from dataclasses import replace

import pytest

from kge import ports
from kge.eval.maturity import (
    Event, Live, Review, ShapeEvent, Tests as ShapeTests, assess, meets, readiness,
    selector_evidence, suspensions, tally, review_counters,
)
from kge.services.ask.engine.shapestage import Declarations, ShapeDeclaration, Suspension, select
from kge.semantics.plan import Abstain, Propose
from tests.plan_fixtures import model


def decl(sid="m", stage="shadow", group="g", born=False):
    return ShapeDeclaration(sid, stage, group, born)


def shape_tests(**changes):
    return replace(ShapeTests(4, 4, 0, 4, 4, True, items=frozenset({"t1"})), **changes)


def live_ok(**changes):
    compared = changes.get("compared", 100)
    disagreements = sum(changes.get(k, 0) for k in ("plans_differ", "propose_decline", "declines_differ"))
    changes.setdefault("agreed", compared - disagreements)
    return Live(**({"compared": compared} | changes))


def ev(eid="e1", *, stage="shadow", tests=None, live=None, rollback=False):
    current = live or live_ok()
    return ShapeEvent(Event(eid, "r-" + eid, rollback), stage, tests or shape_tests(), current, current)


def rev(obs="o1", event="e1", shapes=("m",), outcome="graph_right", takeover=False, item="t1"):
    return Review(obs, event, shapes, takeover, outcome, item)


def metrics(result):
    return tuple(f.metric for f in result.events[-1].failures)


def evaluated(*events, declaration=None, reviews=()):
    return assess(declaration or decl(), events, reviews)


def test_input_contracts():
    assert Live.KEYS[2:5] == ports.SHADOW_COMPARISONS[1:]
    assert ports.SHADOW_GRAPH_WRONG == {"coded_right", "both_wrong"}
    assert ports.SHADOW_GRAPH_WRONG <= set(ports.SHADOW_REVIEW_OUTCOMES)
    good = {k: getattr(live_ok(), k) for k in Live.KEYS}
    for bad in (good | {"extra": 0}, {k: v for k, v in good.items() if k != Live.KEYS[0]},
                good | {"compared": -1}, good | {"agreed": True}, good | {"agreed": 99},
                good | {"graph_declines": 101}):
        with pytest.raises(ValueError):
            Live.of(bad)
    for args in ((5, 4, 0, 4, 4, True), (4, 4, 0, 5, 4, True), (4, 4, 0, 4, 5, True)):
        with pytest.raises(ValueError):
            ShapeTests(*args)
    with pytest.raises(ValueError):
        rev(outcome="maybe")


@pytest.mark.parametrize("agreed,compared,expected", [(4, 4, True), (3, 4, False), (0, 0, False), (3, 3, True)])
def test_m1_test_agreement(agreed, compared, expected):
    m = evaluated(ev(tests=shape_tests(agreed=agreed, compared=compared)))
    assert ("M1" not in metrics(m)) is expected
    if compared == 3:
        assert not m.takeover_ready


@pytest.mark.parametrize("born", [False, True])
def test_m2_rejects_any_wrong_figure(born):
    d = decl(born=born)
    assert "M2" not in metrics(evaluated(ev(), declaration=d))
    assert "M2" in metrics(evaluated(ev(tests=shape_tests(wrong=1)), declaration=d))


@pytest.mark.parametrize("compared,disagreements,passes", [(100, 1, True), (100, 2, False), (199, 2, False), (200, 2, True)])
def test_m3_exact_integer_boundary_and_review_does_not_erase_disagreement(compared, disagreements, passes):
    reviews = tuple(rev(f"o{i}") for i in range(disagreements))
    m = evaluated(ev(live=live_ok(compared=compared, plans_differ=disagreements)), reviews=reviews)
    assert ("M3" not in metrics(m)) is passes


@pytest.mark.parametrize("reviews,passes", [
    ((rev("o1"), rev("o2")), True),
    ((rev("o1"), rev("o2", outcome=None)), False),
    ((rev("o1"), rev("o2", outcome="needs_test")), False),
    ((rev("o1"),), False),
    ((rev("o1"), rev("o2"), rev("o3", outcome=None)), False),
])
def test_m4_requires_every_disagreement_and_record_reviewed(reviews, passes):
    m = evaluated(ev(live=live_ok(compared=200, plans_differ=2)), reviews=reviews)
    assert ("M4" not in metrics(m)) is passes


def test_m5_volume_and_open_event_do_not_break_previous_run():
    first = ev("e1")
    short = ev("e2", live=live_ok(compared=99))
    m = evaluated(first, short)
    assert "M5" in metrics(m)
    assert m.events[-1].pending and m.held == 1
    assert any(f.detail.startswith("not enough evidence") for f in m.events[-1].failures if f.metric == "M5")
    closed = evaluated(first, short, ev("e3"))
    assert closed.held == 1
    assert not evaluated(short).enough
    assert evaluated(first).enough


def test_m6_declines_and_new_phrase_exclusion():
    a = evaluated(ev(live=live_ok(graph_declines=3, coded_declines=3)))
    b = evaluated(ev(live=live_ok(graph_declines=4, coded_declines=3)))
    c = evaluated(ev(live=live_ok(graph_declines=3, coded_declines=3, new_phrase=50)))
    assert "M6" not in metrics(a)
    assert "M6" in metrics(b)
    assert a.events[-1].qualifies == c.events[-1].qualifies


def test_m7_two_events_rollback_and_pending():
    assert evaluated(ev("e1"), ev("e2")).held == 2
    assert evaluated(ev("e1"), ev("e2", rollback=True), ev("e3")).held == 1
    rolled_back = evaluated(ev("e1"), ev("e2", rollback=True))
    assert rolled_back.held == 0 and not rolled_back.events[-1].pending
    assert evaluated(ev("e1"), ev("e2", tests=shape_tests(agreed=3))).held == 0
    assert meets(evaluated(ev("e1")), "graph_first") == ()
    assert any(x.metric == "M7" for x in meets(evaluated(ev("e1")), "retired"))


@pytest.mark.parametrize("outcome,held", [("graph_right", 2), ("coded_right", 0), ("both_wrong", 0)])
def test_graph_wrong_breaks_event_run(outcome, held):
    m = evaluated(ev("e1"), ev("e2"), reviews=(rev("o1", "e2", outcome=outcome),))
    assert m.held == held


def test_m8_owner_signoff_for_retirement():
    d = decl(stage="graph_first")
    ds = Declarations({d.id: d})
    m = assessed = evaluated(ev("e1", stage="graph_first"), ev("e2", stage="graph_first"), declaration=d)
    assert assessed.held == 2
    unsigned = readiness(ds, "g", {d.id: m})
    assert unsigned.next == "retired" and not unsigned.ready
    assert [(b.shape, b.metric) for b in unsigned.blockers] == [("g", "M8")]
    assert readiness(ds, "g", {d.id: m}, signed_off=True).ready


@pytest.mark.parametrize("item,missing", [(None, True), ("t9", True), ("t1", False)])
def test_reviewed_disagreement_requires_reproduction(item, missing):
    m = evaluated(ev(), reviews=(rev(item=item),))
    assert ("o1" in m.untested_reviews) is missing
    assert any(x.metric == "M4" and "o1" in x.detail for x in meets(m, "graph_first")) is missing


def test_born_shape_skips_shadow_and_requires_complete_tests():
    d = decl("b", "specify", born=True)
    ds = Declarations({"b": d})
    m = evaluated(ev(stage="specify"), declaration=d)
    r = readiness(ds, "g", {"b": m})
    assert r.next == "graph_first" and r.ready
    assert m.event_number == 0
    for t, metric in ((shape_tests(passed=3), "tests"), (ShapeTests(0, 0, 0, 0, 0, True), "tests"),
                      (shape_tests(wrong=1), "M2")):
        assert metric in {b.metric for b in meets(evaluated(ev(stage="specify", tests=t), declaration=d), "graph_first")}


def test_born_review_window_and_group_terminal_stage():
    d = decl("b", "graph_first", born=True)
    first = ev("e1", stage="graph_first")
    r = rev(event="e1", shapes=("b",), outcome=None)
    assert "Q2" in metrics(evaluated(first, declaration=d, reviews=(r,)))
    third = evaluated(first, ev("e2", stage="graph_first"), ev("e3", stage="graph_first"),
                      declaration=d, reviews=(rev(event="e3", shapes=("b",), outcome=None),))
    assert third.event_number == 3 and "Q2" not in metrics(third)
    result = readiness(Declarations({"b": d}), "g", {"b": third})
    assert result.next is None and not result.ready


def test_takeover_suspension_and_lifting():
    b, m = decl("b", born=True), decl("m")
    ds = Declarations({"b": b, "m": m})
    wrong = rev(shapes=("b", "m"), outcome="coded_right", takeover=True, item="t9")
    assert suspensions((wrong,), ds) == (Suspension("b", "o1", "t9"),)
    result = evaluated(ev(), declaration=b, reviews=(wrong,))
    assert result.suspended and not result.takeover_ready
    assert any(x.metric == "Q15" for x in meets(result, "graph_first"))
    lifted = evaluated(ev("e1"), ev("e2", tests=shape_tests(items=frozenset({"t1", "t9"}))),
                       declaration=b, reviews=(wrong,))
    assert not lifted.suspended and lifted.takeover_ready
    assert suspensions((replace(wrong, outcome="graph_right"),), ds) == ()
    assert suspensions((replace(wrong, takeover=False),), ds) == ()


def test_coupled_group_and_lowering():
    a, b, c = decl("m"), decl("m2"), decl("b", born=True)
    ds = Declarations({d.id: d for d in (a, b, c)})
    values = {d.id: evaluated(ev(), declaration=d) for d in (a, b, c)}
    assert readiness(ds, "g", values).ready
    values["m2"] = evaluated(ev(live=live_ok(plans_differ=2)), declaration=b,
                              reviews=(rev("o1", shapes=("m2",)), rev("o2", shapes=("m2",))))
    result = readiness(ds, "g", values)
    assert not result.ready and [(x.shape, x.metric) for x in result.blockers] == [("m2", "M3")]
    assert meets(values["m2"], "specify") == ()
    assert meets(values["m2"], "shadow") == ()
    with pytest.raises(ValueError):
        meets(values["m2"], "unknown")
    with pytest.raises(ValueError):
        readiness(ds, "g", {"m": values["m"]})


@pytest.mark.parametrize("born_tests,mirror_tests,expected", [
    (shape_tests(), shape_tests(), True),
    (shape_tests(passed=3), shape_tests(), False),
    (shape_tests(), shape_tests(agreed=3), False),
    (shape_tests(), shape_tests(examples_replayed=False), False),
])
def test_selector_evidence_agrees_with_takeover_decision(born_tests, mirror_tests, expected):
    b, m = decl("b", born=True), decl("m")
    declarations = Declarations({"b": b, "m": m})
    histories = {"b": (ev(tests=born_tests),), "m": (ev(tests=mirror_tests),)}
    maturities = {s.id: assess(s, histories[s.id], ()) for s in (b, m)}
    evidence = selector_evidence(maturities, histories, (), declarations)
    result = select(Abstain(kind="abstain", spans=(), missing=("x",)),
                    Propose(kind="propose", base_turn=None, plan=model()),
                    graph_shapes=("b", "m"), coded_shapes=(), rules_fired=(),
                    declarations=declarations, evidence=evidence, agree=lambda a, b: True)
    assert result.takeover is expected
    assert result.takeover == all(x.takeover_ready for x in maturities.values())


def test_tally_separates_phrase_and_term_buckets_and_does_not_copy_payload():
    rows = [
        {"comparison": "agree", "graph_outcome": "propose", "coded_outcome": "propose", "private": "secret"},
        {"comparison": "plans_differ", "graph_outcome": "propose", "coded_outcome": "propose", "via_terms": True},
        {"comparison": "propose_decline", "graph_outcome": "propose", "coded_outcome": "abstain",
         "new_phrase": True, "via_terms": True, "fallback": True, "takeover": True},
    ]
    original = [dict(row) for row in rows]
    totals = tally(rows)
    assert rows == original
    assert set(totals) == {"live", "new_phrase", "via_terms"}
    assert totals["live"].compared == totals["live"].agreed == 1
    assert totals["via_terms"].plans_differ == 1
    assert totals["via_terms"].coded_declines == 0
    assert totals["new_phrase"].propose_decline == totals["new_phrase"].coded_declines == 1
    assert totals["new_phrase"].fallbacks == totals["new_phrase"].takeovers == 1
    assert "secret" not in repr(totals)
    with pytest.raises(ValueError):
        tally([{"comparison": "unexpected"}])


def test_release_events_remain_distinct_with_same_release_id():
    one = ev("e1")
    two = replace(ev("e2"), event=Event("e2", one.event.release, False))
    assert evaluated(one, two).held == 2
    with pytest.raises(ValueError):
        evaluated(one, one)
    with pytest.raises(ValueError):
        evaluated(one, reviews=(rev(event="e2"),))


def test_mixed_group_born_retirement_waits_for_two_release_events():
    b = decl("b", "graph_first", born=True)
    one = evaluated(ev("e1", stage="graph_first"), declaration=b)
    two = evaluated(ev("e1", stage="graph_first"), ev("e2", stage="graph_first"), declaration=b)
    assert any(x.metric == "Q2" for x in meets(one, "retired"))
    assert not any(x.metric == "Q2" for x in meets(two, "retired"))


def test_pending_event_reports_the_prior_failed_event_when_no_event_qualified():
    closed = ev("e1", live=live_ok(plans_differ=2))
    pending = ev("e2", live=live_ok(compared=99))
    reviews = (rev("o1", event="e1"), rev("o2", event="e1"))
    blockers = meets(evaluated(closed, pending, reviews=reviews), "graph_first")
    assert any(x.metric == "M3" for x in blockers)


def test_takeover_is_reviewed_once_with_its_disagreement():
    rows = [
        {"comparison": "plans_differ", "takeover": False, "graph_outcome": "propose", "coded_outcome": "propose"},
        {"comparison": "propose_decline", "takeover": True, "graph_outcome": "propose", "coded_outcome": "abstain"},
    ]
    live = tally(rows)["live"]
    assert live.disagreements == 2 and live.takeovers == 1
    d = decl("b", "shadow", born=True)
    one_event = ev(live=live)
    one_review = (rev("o1", shapes=("b",)),)
    assert "Q2" in metrics(evaluated(one_event, declaration=d, reviews=one_review))
    all_reviews = (*one_review, rev("o2", shapes=("b",), takeover=True))
    assert "Q2" not in metrics(evaluated(one_event, declaration=d, reviews=all_reviews))


def test_duplicate_review_cannot_count_twice():
    d = decl("b", "shadow", born=True)
    one = rev("o1", shapes=("b",))
    with pytest.raises(ValueError):
        evaluated(ev(live=live_ok(compared=100, plans_differ=2)), declaration=d, reviews=(one, one))


def test_mirror_new_phrase_disagreement_still_requires_review():
    event = replace(ev(), total_live=live_ok(compared=101, plans_differ=1))
    m = evaluated(event)
    assert "M4" in metrics(m)
    assert "M3" not in metrics(m) and "M5" not in metrics(m)
    reviewed = evaluated(event, reviews=(rev(),))
    assert "M4" not in metrics(reviewed)


def test_wrong_takeover_requires_reproduction_in_a_later_release():
    b = decl("b", born=True)
    declarations = Declarations({"b": b})
    wrong = rev("o1", event="e1", shapes=("b",), outcome="coded_right", takeover=True, item="t1")
    history = (ev("e1"),)
    maturity = assess(b, history, (wrong,))
    assert maturity.suspended and not maturity.takeover_ready
    evidence = selector_evidence({"b": maturity}, {"b": history}, (wrong,), declarations)
    result = select(Abstain(kind="abstain", spans=(), missing=("x",)),
                    Propose(kind="propose", base_turn=None, plan=model()),
                    graph_shapes=("b",), coded_shapes=(), rules_fired=(),
                    declarations=declarations, evidence=evidence, agree=lambda a, b: True)
    assert not result.takeover
    subsequent = (*history, ev("e2"))
    lifted = assess(b, subsequent, (wrong,))
    assert not lifted.suspended and lifted.takeover_ready
    next_evidence = selector_evidence({"b": lifted}, {"b": subsequent}, (wrong,), declarations)
    assert select(Abstain(kind="abstain", spans=(), missing=("x",)),
                  Propose(kind="propose", base_turn=None, plan=model()),
                  graph_shapes=("b",), coded_shapes=(), rules_fired=(),
                  declarations=declarations, evidence=next_evidence, agree=lambda a, b: True).takeover


@pytest.mark.parametrize("changes", [
    {"graph_outcome": None}, {"coded_outcome": None},
    {"graph_outcome": "unknown"}, {"coded_outcome": "unknown"},
    {"comparison": "propose_decline"}, {"comparison": "declines_differ"},
    {"new_phrase": 1}, {"via_terms": "yes"}, {"fallback": 1}, {"takeover": 1},
    {"takeover": True},
])
def test_tally_rejects_malformed_outcomes_and_flags(changes):
    row = {"comparison": "agree", "graph_outcome": "propose", "coded_outcome": "propose"}
    with pytest.raises(ValueError):
        tally([row | changes])


@pytest.mark.parametrize("missing", ["graph_outcome", "coded_outcome"])
def test_tally_rejects_missing_outcomes(missing):
    row = {"comparison": "agree", "graph_outcome": "propose", "coded_outcome": "propose"}
    del row[missing]
    with pytest.raises(ValueError):
        tally([row])


def test_review_counters_default_missing_keys_and_validate_values():
    empty = review_counters({})
    assert empty and all(value == 0 for value in empty.values())
    partial = review_counters({"queued": 2})
    assert partial["queued"] == 2
    assert all(value == 0 for key, value in partial.items() if key != "queued")
    for value in (-1, True, "1"):
        with pytest.raises(ValueError):
            review_counters({"queued": value})


def test_queued_review_counts_require_records_and_graph_wrong_breaks_run():
    queued = replace(ev("e2"), reviews={"queued": 1, "reviewed": 1})
    m = evaluated(ev("e1"), queued)
    assert "M4" in metrics(m)
    assert m.events[-1].pending and m.held == 1
    completed = evaluated(ev("e1"), queued, reviews=(rev(event="e2"),))
    assert "M4" not in metrics(completed) and completed.held == 2
    wrong = replace(ev("e2"), reviews={"graph_wrong": 1})
    reset = evaluated(ev("e1"), wrong)
    assert "M7" in metrics(reset) and reset.held == 0 and not reset.events[-1].pending


def test_incomplete_totals_cannot_certify_excluded_disagreements():
    baseline = live_ok()
    with pytest.raises(ValueError):
        ShapeEvent(Event("e1", "r-e1", False), "shadow", shape_tests(), baseline, None)
    inconsistent = live_ok(compared=99)
    with pytest.raises(ValueError):
        ShapeEvent(Event("e1", "r-e1", False), "shadow", shape_tests(), baseline, inconsistent)


@pytest.mark.parametrize("bucket", ["new_phrase", "via_terms"])
def test_excluded_bucket_disagreement_is_reviewed_before_maturity(bucket):
    rows = [{"comparison": "agree", "graph_outcome": "propose", "coded_outcome": "propose"}] * 100
    rows.append({"comparison": "plans_differ", "graph_outcome": "propose", "coded_outcome": "propose", bucket: True})
    buckets = tally(rows)
    combined = live_ok(compared=101, plans_differ=1)
    event = replace(ev(live=buckets["live"]), total_live=combined)
    assert "M4" in metrics(evaluated(event))
    assert "M4" not in metrics(evaluated(event, reviews=(rev(),)))
