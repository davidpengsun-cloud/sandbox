"""The stage selector, kge/services/ask/engine/shapestage.py (#519, #450 requirement 5): given both rules planners'
replies, the shapes each used, the clarification rules that fired, the shape declarations and the release event's
evidence, it serves one whole plan at the coupled group's stage (Q14), marks fallbacks at graph_first (Q5), grants a
shadow takeover only on Q15's four conditions, marks review for a born-in-graph shape's first two release events (Q2)
and holds a suspended shape's takeovers until a release event whose tests include the reviewed question.

    .venv/bin/python -m pytest tests/unit/test_shapestage.py -q

Written tests first from the build plan on #519 (package B's nineteen planted cases), against package A's API.
Synthetic declarations and stub replies only: shapes b and b2 are born in the graph and m is a mirror, all three in
group b; x is born in the graph and alone in group x; q is declared nowhere. Propose replies carry the smallest plan
the plan model accepts (tests/plan_fixtures.py). No question set, no fixture, no pack. Test 18 builds its
declarations from emit.shape_rows over a three-shape synthetic pack whose groups do not depend on position
constraints, so #510's F9 change to overlap cannot move them.
"""
from __future__ import annotations

import ast
import itertools
import sys
from pathlib import Path

import pytest

from kge.compiler.emit import shape_rows
from kge.semantics.plan import Abstain, Clarify, Propose
from kge.services.ask.engine import shapestage
from kge.services.ask.engine.shapestage import (
    REVIEW_EVENTS,
    Declarations,
    Evidence,
    Selection,
    ShapeDeclaration,
    ShapeEvidence,
    Suspension,
    lifted,
    select,
    takeover_holds,
)
from tests.plan_fixtures import model

P1 = Propose(kind="propose", base_turn=None, plan=model())
P2 = Propose(kind="propose", base_turn=None, plan=model(limit=5))
ABSTAIN = Abstain(kind="abstain", spans=(), missing=("x",))
CLARIFY = Clarify(kind="clarify", spans=(), candidates=())
MIRRORED = "planshapes.compose:ratio"
SHAPES = {"b": ("b", None), "b2": ("b", None), "m": ("b", MIRRORED), "x": ("x", None)}


class Agree:
    """The caller's plan comparison, stubbed: it records each call and returns a fixed answer."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.calls: list = []

    def __call__(self, coded_plan, graph_plan):
        self.calls.append((coded_plan, graph_plan))
        return self.answer


def rows(default: str = "shadow", **stages: str) -> list[dict]:
    """Shape rows with the keys emit.shape_rows writes, every shape at default unless named."""
    return [{"id": sid, "label": None, "stage": stages.get(sid, default), "replaces": replaces, "coupled_group": group,
             "defined_in": "pack"} for sid, (group, replaces) in SHAPES.items()]


def decls(default: str = "shadow", **stages: str) -> Declarations:
    return Declarations.of(rows(default, **stages))


def ev(event: str = "e2", **overrides) -> ShapeEvidence:
    """One shape's evidence, perfect by default: 4 of 4 passed, 0 wrong, 4 agreed, examples replayed, item t1, event 1."""
    fields = {"release_event": event, "tests_passed": 4, "tests_total": 4, "wrong_figures": 0, "agreed": 4,
              "examples_replayed": True, "items": frozenset({"t1"}), "event_number": 1}
    fields.update(overrides)
    return ShapeEvidence(**fields)


def evidence(suspensions: tuple = (), event: str = "e2", **per_shape) -> Evidence:
    """Current evidence in release event e2, perfect for every declared shape; a shape given None has none, a dict
    overrides its fields and a ShapeEvidence replaces it."""
    shapes = {}
    for sid in SHAPES:
        given = per_shape.get(sid, {})
        if given is None:
            continue
        shapes[sid] = given if isinstance(given, ShapeEvidence) else ev(event, **given)
    return Evidence(event, shapes, tuple(suspensions))


PERFECT = evidence()


def run(coded, graph, *, graph_shapes=(), coded_shapes=(), rules_fired=(), declarations=None, evidence=PERFECT,
        agree=None) -> tuple[Selection, Agree]:
    agree = agree if agree is not None else Agree()
    selection = select(coded, graph, graph_shapes=tuple(graph_shapes), coded_shapes=tuple(coded_shapes),
                       rules_fired=tuple(rules_fired), declarations=declarations if declarations is not None else decls(),
                       evidence=evidence, agree=agree)
    return selection, agree


def holds(coded, graph, *, graph_shapes=(), coded_shapes=(), rules_fired=(), declarations=None, evidence=PERFECT):
    return takeover_holds(coded, graph, graph_shapes=tuple(graph_shapes), coded_shapes=tuple(coded_shapes),
                          rules_fired=tuple(rules_fired),
                          declarations=declarations if declarations is not None else decls(), evidence=evidence)


def expect(selection: Selection, *, served, reply, stage, group, fallback=None, takeover=False, review=False, held=()):
    assert selection.reply is reply
    assert (selection.served, selection.stage, selection.group, selection.fallback, selection.takeover,
            selection.review, selection.held) == (served, stage, group, fallback, takeover, review, held)


@pytest.mark.parametrize("coded", [P1, ABSTAIN, CLARIFY], ids=["coded_propose", "coded_abstain", "coded_clarify"])
@pytest.mark.parametrize("graph", [None, P2, ABSTAIN], ids=["graph_none", "graph_propose", "graph_abstain"])
def test_01_no_shape_on_either_side_serves_coded_unmarked(coded, graph):
    """B2's live case: no shape used, so no stage, and coded is served with nothing marked."""
    selection, agree = run(coded, graph)
    expect(selection, served="coded", reply=coded, stage=None, group=None)
    assert agree.calls == []


def test_02_specify_serves_coded_and_never_takes_over():
    specify = decls("specify")
    selection, agree = run(P1, P2, graph_shapes=("b",), declarations=specify)
    expect(selection, served="coded", reply=P1, stage="specify", group="b")
    selection, agree2 = run(ABSTAIN, P2, graph_shapes=("b",), declarations=specify)
    expect(selection, served="coded", reply=ABSTAIN, stage="specify", group="b")
    assert agree.calls == [] and agree2.calls == []


@pytest.mark.parametrize("answer", [True, False])
def test_03_shadow_with_coded_proposing_serves_coded_without_comparing(answer):
    selection, agree = run(P1, P2, graph_shapes=("b", "m"), agree=Agree(answer))
    expect(selection, served="coded", reply=P1, stage="shadow", group="b", held=("coded_proposed",))
    assert agree.calls == []


@pytest.mark.parametrize("coded, graph, answer, served, fallback, calls", [
    (P1, P2, True, "graph", None, 1),
    (ABSTAIN, P2, True, "graph", None, 0),
    (CLARIFY, P2, True, "graph", None, 0),
    (P1, P2, False, "coded", "disagree", 1),
    (P1, ABSTAIN, True, "coded", "graph_declined", 0),
    (ABSTAIN, CLARIFY, True, "coded", "graph_declined", 0),
    (P1, None, True, "coded", "graph_declined", 0),
    (ABSTAIN, None, True, "coded", "graph_declined", 0),
], ids=["a_agree", "b_coded_abstains", "c_coded_clarifies", "d_disagree", "e_graph_abstains",
        "f_both_decline", "g_graph_not_run", "g_graph_not_run_coded_abstains"])
def test_04_graph_first_serves_graph_when_it_proposes_and_coded_agrees_or_declines(coded, graph, answer, served,
                                                                                    fallback, calls):
    selection, agree = run(coded, graph, graph_shapes=("b",), declarations=decls("graph_first"), agree=Agree(answer))
    expect(selection, served=served, reply=graph if served == "graph" else coded, stage="graph_first", group="b",
           fallback=fallback, review=served == "graph")
    assert len(agree.calls) == calls
    if calls:
        assert agree.calls[0][0] is P1.plan and agree.calls[0][1] is P2.plan


def test_05_retired_serves_graph_whatever_it_replies_and_coded_only_when_graph_did_not_run():
    retired = decls("retired")
    selection, _ = run(P1, P2, graph_shapes=("b",), declarations=retired)
    expect(selection, served="graph", reply=P2, stage="retired", group="b", review=True)
    selection, _ = run(P1, ABSTAIN, graph_shapes=("b",), declarations=retired)
    expect(selection, served="graph", reply=ABSTAIN, stage="retired", group="b", review=True)
    selection, _ = run(P1, None, coded_shapes=("b",), declarations=retired)
    expect(selection, served="coded", reply=P1, stage="retired", group="b", fallback="graph_not_run")
    selection, agree = run(P1, P2, graph_shapes=("m",), declarations=retired)
    expect(selection, served="graph", reply=P2, stage="retired", group="b")
    assert agree.calls == []


def test_06_a_mixed_group_is_served_at_its_lowest_stage():
    mixed = decls(b="graph_first", m="shadow", b2="graph_first")
    assert mixed.group_stage("b") == "shadow"
    selection, agree = run(P1, P2, graph_shapes=("b", "m"), declarations=mixed)
    expect(selection, served="coded", reply=P1, stage="shadow", group="b", held=("coded_proposed",))
    assert agree.calls == []
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"), declarations=mixed)
    expect(selection, served="graph", reply=P2, stage="shadow", group="b", takeover=True, review=True)


def test_06_shapes_from_two_groups_take_the_lowest_stage_and_never_take_over():
    two = decls(b="shadow", m="shadow", x="graph_first")
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "x"), declarations=two)
    expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group=None, held=("mixed_groups",))
    selection, _ = run(ABSTAIN, P2, graph_shapes=("x",), coded_shapes=("m",), declarations=decls(x="retired"))
    assert (selection.stage, selection.group, selection.takeover) == ("shadow", None, False)
    assert "mixed_groups" in selection.held
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "x"), declarations=decls("graph_first"))
    expect(selection, served="graph", reply=P2, stage="graph_first", group=None, review=True)


def test_06_an_unknown_shape_counts_as_specify_and_unknown_shapes_alone_give_no_stage():
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "q"))
    expect(selection, served="coded", reply=ABSTAIN, stage="specify", group="b")
    assert holds(ABSTAIN, P2, graph_shapes=("b", "q")) == ("unknown_shape:q",)
    for stage in ("graph_first", "retired"):
        selection, agree = run(ABSTAIN, P2, graph_shapes=("b", "q"), declarations=decls(stage))
        expect(selection, served="coded", reply=ABSTAIN, stage="specify", group="b")
        assert agree.calls == []
    selection, _ = run(ABSTAIN, P2, graph_shapes=("q",), coded_shapes=("r",))
    expect(selection, served="coded", reply=ABSTAIN, stage=None, group=None)


@pytest.mark.parametrize("coded", [ABSTAIN, CLARIFY], ids=["coded_abstains", "coded_clarifies"])
def test_07_takeover_is_granted_when_all_four_conditions_hold(coded):
    selection, agree = run(coded, P2, graph_shapes=("b", "m"))
    expect(selection, served="graph", reply=P2, stage="shadow", group="b", takeover=True, review=True)
    assert agree.calls == []
    assert holds(coded, P2, graph_shapes=("b", "m")) == ()


def test_08_condition_1_coded_must_decline():
    selection, _ = run(P1, P2, graph_shapes=("b", "m"))
    assert (selection.served, selection.takeover, selection.held) == ("coded", False, ("coded_proposed",))
    assert holds(P1, P2, graph_shapes=("b", "m")) == ("coded_proposed",)


def test_09_condition_2a_the_graph_plan_uses_a_born_in_graph_shape():
    selection, _ = run(ABSTAIN, P2, graph_shapes=("m",))
    expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b", held=("no_born_shape",))


@pytest.mark.parametrize("shape_evidence, held", [
    ({"tests_passed": 3}, "born_tests:b"),
    ({"wrong_figures": 1}, "born_wrong:b"),
    ({"tests_passed": 0, "tests_total": 0}, "born_tests:b"),
    (None, "no_evidence:b"),
    (ev("e1"), "no_evidence:b"),
], ids=["three_of_four", "one_wrong_figure", "no_tests", "no_evidence", "earlier_release_event"])
def test_10_condition_2b_every_born_shape_passes_all_its_tests_with_no_wrong_figure_in_this_release_event(
        shape_evidence, held):
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"), evidence=evidence(b=shape_evidence))
    expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b", held=(held,))


@pytest.mark.parametrize("shape_evidence, held", [
    ({"agreed": 3}, "mirror_agreement:m"),
    ({"wrong_figures": 1}, "mirror_wrong:m"),
    ({"examples_replayed": False}, "mirror_examples:m"),
    ({"agreed": 0, "tests_passed": 0, "tests_total": 0}, "mirror_agreement:m"),
    (None, "no_evidence:m"),
    (ev("e1"), "no_evidence:m"),
], ids=["m1_below_100", "m2_above_0", "v06_not_replayed", "no_tests", "no_evidence", "earlier_release_event"])
def test_11_condition_3_every_mirror_meets_m1_m2_and_v06(shape_evidence, held):
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"), evidence=evidence(m=shape_evidence))
    expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b", held=(held,))


def test_12_condition_4_no_clarification_fires():
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"), rules_fired=("r1",))
    expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b", held=("clarification",))


def test_13_graph_declining_or_not_running_at_shadow_is_held():
    for graph in (ABSTAIN, CLARIFY):
        selection, _ = run(ABSTAIN, graph, graph_shapes=("b", "m"))
        expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b", held=("graph_declined",))
    selection, _ = run(ABSTAIN, None, coded_shapes=("m",))
    expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b",
           held=("graph_not_run", "no_born_shape"))


def test_14_several_failures_are_all_listed_in_the_fixed_order():
    selection, _ = run(P1, P2, graph_shapes=("b", "m"), rules_fired=("r1",),
                       evidence=evidence(b={"wrong_figures": 1}, m={"examples_replayed": False}))
    expect(selection, served="coded", reply=P1, stage="shadow", group="b",
           held=("coded_proposed", "clarification", "born_wrong:b", "mirror_examples:m"))


def test_14_every_hold_kind_at_once_in_the_fixed_order():
    worst = evidence(suspensions=(Suspension("x", "o1", None),), b=None,
                     m={"agreed": 3, "wrong_figures": 1, "examples_replayed": False},
                     x={"tests_passed": 3, "wrong_figures": 2})
    expected = ("coded_proposed", "graph_declined", "clarification", "unknown_shape:q", "mixed_groups",
                "born_tests:x", "born_wrong:x", "no_evidence:b",
                "mirror_agreement:m", "mirror_wrong:m", "mirror_examples:m", "suspended:x")
    arguments = {"graph_shapes": ("x", "q", "m", "b"), "rules_fired": ("r1",), "evidence": worst}
    selection, _ = run(P1, ABSTAIN, **arguments)
    expect(selection, served="coded", reply=P1, stage="specify", group=None)
    assert holds(P1, ABSTAIN, **arguments) == expected
    selection, _ = run(P1, ABSTAIN, **{**arguments, "graph_shapes": ("x", "m", "b")})
    expect(selection, served="coded", reply=P1, stage="shadow", group=None,
           held=tuple(h for h in expected if h != "unknown_shape:q"))


@pytest.mark.parametrize("event_number, review", [(1, True), (2, True), (3, False), (REVIEW_EVENTS + 1, False)])
def test_15_a_takeover_is_marked_for_review_in_the_born_shapes_first_two_release_events(event_number, review):
    assert REVIEW_EVENTS == 2
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"),
                       evidence=evidence(b={"event_number": event_number}, m={"event_number": 9}))
    expect(selection, served="graph", reply=P2, stage="shadow", group="b", takeover=True, review=review)


def test_15_review_holds_while_any_born_shape_is_in_its_first_two_release_events():
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "b2"),
                       evidence=evidence(b={"event_number": 3}, b2={"event_number": 2}))
    assert (selection.takeover, selection.review) == (True, True)
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "b2"),
                       evidence=evidence(b={"event_number": 3}, b2={"event_number": 3}))
    assert (selection.takeover, selection.review) == (True, False)


def test_15_review_at_graph_first_follows_the_born_shapes_and_coded_service_is_never_marked():
    graph_first = decls("graph_first")
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b",), declarations=graph_first)
    expect(selection, served="graph", reply=P2, stage="graph_first", group="b", review=True)
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b",), declarations=graph_first, evidence=Evidence.NONE)
    expect(selection, served="graph", reply=P2, stage="graph_first", group="b", review=True)
    selection, _ = run(ABSTAIN, P2, graph_shapes=("m",), declarations=graph_first)
    expect(selection, served="graph", reply=P2, stage="graph_first", group="b", review=False)
    selection, _ = run(P1, P2, graph_shapes=("b",), declarations=graph_first, agree=Agree(False))
    expect(selection, served="coded", reply=P1, stage="graph_first", group="b", fallback="disagree", review=False)
    selection, _ = run(P1, P2, graph_shapes=("b",))
    assert (selection.served, selection.review) == ("coded", False)
    selection, _ = run(P1, P2, graph_shapes=("b",), declarations=decls("specify"))
    assert (selection.served, selection.review) == ("coded", False)


@pytest.mark.parametrize("suspensions, shape_evidence, held", [
    ((Suspension("b", "o1", None),), {}, ("suspended:b",)),
    ((Suspension("b", "o1", "t9"),), {}, ("suspended:b",)),
    ((Suspension("b", "o1", "t9"),), {"items": frozenset({"t1", "t9"})}, ()),
    ((Suspension("b", "o1", "t9"),), ev("e1", items=frozenset({"t1", "t9"})), ("no_evidence:b", "suspended:b")),
    ((Suspension("x", "o1", None),), {}, ()),
    ((Suspension("b", "o1", None), Suspension("b", "o2", "t9")), {}, ("suspended:b",)),
    ((Suspension("b", "o1", "t9"), Suspension("b", "o2", "t8")), {"items": frozenset({"t1", "t9"})}, ("suspended:b",)),
    ((Suspension("m", "o1", None),), {}, ("suspended:m",)),
], ids=["no_item_yet", "item_not_in_this_event", "item_in_this_event_lifts", "item_only_in_an_earlier_event",
        "unused_shape", "two_suspensions_once", "one_lifted_one_not", "a_suspended_mirror"])
def test_16_a_suspended_shape_takes_over_again_only_after_a_release_event_whose_tests_include_the_question(
        suspensions, shape_evidence, held):
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"), evidence=evidence(suspensions, b=shape_evidence))
    if held:
        expect(selection, served="coded", reply=ABSTAIN, stage="shadow", group="b", held=held)
    else:
        expect(selection, served="graph", reply=P2, stage="shadow", group="b", takeover=True, review=True)


def test_10_a_born_shape_is_held_neither_to_agreement_nor_to_replayed_examples_and_a_mirror_not_to_passing():
    selection, _ = run(ABSTAIN, P2, graph_shapes=("b", "m"),
                       evidence=evidence(b={"agreed": 0, "examples_replayed": False}, m={"tests_passed": 3}))
    expect(selection, served="graph", reply=P2, stage="shadow", group="b", takeover=True, review=True)


def test_19_declarations_and_evidence_copy_what_they_are_given():
    given = {"b": ev(items={"t1"})}
    held = Evidence("e2", given, [Suspension("b", "o1", None)])
    given["m"] = ev()
    assert set(held.shapes) == {"b"} and held.suspensions == (Suspension("b", "o1", None),)
    assert isinstance(held.shapes["b"].items, frozenset)
    shapes = {"b": ShapeDeclaration("b", "shadow", "b", True)}
    declared = Declarations(shapes)
    shapes["m"] = ShapeDeclaration("m", "shadow", "b", False)
    assert set(declared.shapes) == {"b"}


def test_16_lifted_reads_only_the_current_release_events_items():
    current = evidence(b={"items": frozenset({"t1", "t9"})})
    assert lifted(Suspension("b", "o1", "t9"), current) is True
    assert lifted(Suspension("b", "o1", "t8"), current) is False
    assert lifted(Suspension("b", "o1", None), current) is False
    assert lifted(Suspension("b", "o1", "t9"), evidence(b=ev("e1", items=frozenset({"t9"})))) is False
    assert lifted(Suspension("b", "o1", "t9"), evidence(b=None)) is False
    assert lifted(Suspension("b", "o1", "t9"), Evidence.NONE) is False


def test_17_with_no_evidence_nothing_ever_takes_over_and_specify_and_shadow_serve_coded():
    assert Evidence.NONE == Evidence(None, {}, ())
    assert Evidence.NONE.current("b") is None
    shape_sets = [(), ("b",), ("m",), ("b", "m"), ("b", "x"), ("q",)]
    for stage, coded, graph, shapes in itertools.product(
            ("specify", "shadow", "graph_first", "retired"), (P1, ABSTAIN, CLARIFY), (None, P2, ABSTAIN, CLARIFY),
            shape_sets):
        selection, _ = run(coded, graph, graph_shapes=shapes, declarations=decls(stage), evidence=Evidence.NONE)
        assert selection.takeover is False, (stage, coded.kind, graph, shapes)
        if selection.stage in (None, "specify", "shadow"):
            assert selection.served == "coded" and selection.reply is coded, (stage, coded.kind, graph, shapes)


def test_17_evidence_is_current_only_in_its_own_release_event():
    stale = ev("e1")
    mixed = Evidence("e2", {"b": ev(), "m": stale}, ())
    assert mixed.current("b") == ev()
    assert mixed.current("m") is None
    assert mixed.current("q") is None
    assert Evidence(None, {"b": ev()}, ()).current("b") is None


def test_18_declarations_read_the_compiled_shape_rows():
    data = {"shapes": [
        {"id": "pb", "stage": "shadow", "requires": ["difference"],
         "slots": [{"name": "measure", "kind": "measure", "count": 1}, {"name": "context", "kind": "context", "count": 2}]},
        {"id": "pm", "stage": "graph_first", "replaces": MIRRORED, "requires": ["ratio"],
         "slots": [{"name": "measure", "kind": "measure", "count": 1}]},
        {"id": "px", "stage": "specify", "requires": ["count"], "forbids": ["difference", "ratio"],
         "slots": [{"name": "measure", "kind": "measure"}]},
    ]}
    compiled = [r for r in shape_rows(data, {}) if r["id"] in {"pb", "pm", "px"}]
    assert len(compiled) == 3
    declarations = Declarations.of(compiled)
    assert dict(declarations.shapes) == {
        "pb": ShapeDeclaration("pb", "shadow", "pb", True),
        "pm": ShapeDeclaration("pm", "graph_first", "pb", False),
        "px": ShapeDeclaration("px", "specify", "px", True),
    }
    assert declarations.group_stage("pb") == "shadow"
    assert declarations.group_stage("px") == "specify"
    assert len(Declarations.EMPTY.shapes) == 0


@pytest.mark.parametrize("broken", [
    [{"id": "b", "stage": "live", "replaces": None, "coupled_group": "b"}],
    [{"id": "b", "stage": "shadow", "replaces": None, "coupled_group": "b"},
     {"id": "b", "stage": "shadow", "replaces": None, "coupled_group": "b"}],
    [{"id": "b", "stage": "shadow", "replaces": None}],
], ids=["unknown_stage", "duplicate_id", "no_coupled_group"])
def test_18_declarations_refuse_rows_the_checks_would_have_failed(broken):
    with pytest.raises(ValueError):
        Declarations.of(broken)


def test_19_select_is_pure():
    declarations = decls()
    current = evidence((Suspension("b", "o1", "t1"),))
    arguments = {"graph_shapes": ("b", "m"), "coded_shapes": (), "rules_fired": (), "declarations": declarations,
                 "evidence": current}
    before = (ABSTAIN.model_dump(), P2.model_dump(), dict(declarations.shapes), dict(current.shapes),
              current.suspensions)
    first = select(ABSTAIN, P2, agree=Agree(), **arguments)
    second = select(ABSTAIN, P2, agree=Agree(), **arguments)
    assert first == second
    assert first.takeover is True
    assert (ABSTAIN.model_dump(), P2.model_dump(), dict(declarations.shapes), dict(current.shapes),
            current.suspensions) == before


def test_19_the_module_imports_only_the_plan_model_the_stage_vocabulary_and_the_standard_library():
    allowed = {"__future__", "kge.semantics.plan", "kge.compiler.standards"}
    tree = ast.parse(Path(shapestage.__file__).read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            imported.append(node.module)
    assert imported
    outside = [name for name in imported if name not in allowed and name.split(".")[0] not in sys.stdlib_module_names]
    assert outside == []
