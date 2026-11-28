"""Question shapes as pack data (meta-model 1.6.0): S01's field tables for the six sections, S32 to S36, the coupled
shape groups, and check-pack leaving the sections to the compiler. Issue #510 (B1.3) adds the owner's F9 to F11: before
constraints in reverse order on operands both shapes require separate two shapes, S34 stays a schema check, a shape
cites each test item once, and no two shapes replace one coded builder.

    .venv/bin/python -m pytest tests/unit/test_question_shapes.py -q

Small synthetic packs only, built in memory or in a temporary folder, with a synthetic preload: no question set file,
nothing tied to a sample client's data. The end-to-end cases compile a scratch copy of a committed pack with the new
sections added, using only that pack's own declared ids.
"""
from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kge.compiler import checks, emit, plans, standards
from kge.compiler import compile as compiler
from kge.compiler import pack as packs
from kge.compiler.pack import Finding, Pack
from kge.estate import upfront

DEMO = Path(__file__).resolve().parents[2]
CLIENTS = DEMO / "clients"
SAMPLE = "thornbury"

OPERATIONS = ("asc", "comparison", "count", "desc", "difference", "ge", "gt", "le", "lt", "percent_change", "previous_period",
              "previous_year", "ratio")
TEXTS = {"contexts-or-periods": "Please identify whether to compare the contexts or their periods.",
         "both-comparison-contexts": "Please identify both comparison contexts."}

LOCK = {"sources": {"u": {"tables": [
    {"name": "events", "columns": [{"name": "amount", "type": "DOUBLE"}, {"name": "account", "type": "INTEGER"},
                                   {"name": "booked", "type": "DATE"}]},
    {"name": "accounts", "columns": [{"name": "id", "type": "INTEGER"}, {"name": "region", "type": "VARCHAR"}]}]}}}


def name(i: str) -> dict:
    return {"id": i, "evidence": []}


def measure(i: str, context: str, measure_id: str = "amount") -> dict:
    return {"kind": "measure", "id": i, "measure": name(measure_id), "context": name(context), "mode": "current", "filters": []}


def plan(**over) -> dict:
    doc = {"schema": "query-plan/v1", "layout": "aligned",
           "measures": [measure("m1", "booked"), measure("m2", "settled")], "dimensions": [], "filters": [],
           "time": {"kind": "single", "period": {"kind": "month", "value": "2025-01", "calendar": name("fiscal")}},
           "comparisons": [{"kind": "contexts", "id": "cmp", "left": "m1", "right": "m2", "evidence": []}],
           "derived": [{"kind": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other", "evidence": []}],
           "order": [], "limit": None, "obligations": []}
    doc.update(over)
    return doc


RECIPE = {"id": "two-context-difference", "blocks": [
    {"block": "contexts", "id": "cmp", "left": "$context[0]", "right": "$context[1]"},
    {"block": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other"},
    {"block": "single", "id": "t", "period": "$period"}]}
SHAPE = {"id": "two-context-difference", "label": "Two contexts and their difference",
         "slots": [{"name": "context", "kind": "context", "count": 2}, {"name": "period", "kind": "period", "count": {"min": 0, "max": 1}}],
         "requires": ["difference"], "forbids": ["comparison"], "recipe": "two-context-difference", "stage": "specify"}
CLARIFICATION = {"id": "contexts-or-periods-2", "message": "contexts-or-periods",
                 "when": {"requires": ["comparison"], "slots": {"context": 2, "period": 2},
                          "constraints": [{"before": ["comparison", "period"]}]}}
SECTIONS = {
    "phrases": [{"phrase": "minus", "operation": "difference"}, {"phrase": "kindly", "filler": True}],
    "recipes": [RECIPE],
    "shapes": [SHAPE],
    "shape_tests": [{"shape": "two-context-difference", "tests": {"set": "shapes_synthetic", "items": ["s1"]}}],
    "examples": [{"id": "booked-vs-settled", "shape": "two-context-difference",
                  "question": "booked amount vs settled amount for January 2025, difference", "plan": plan()}],
    "clarifications": [CLARIFICATION],
}


def pack(tmp_path, version: str = "1.6.0", **sections) -> Pack:
    """A synthetic pack: a tiny glossary, measures, contexts, entities, a calendar, a join, an alias, a grouping
    phrase, an acronym and one synthetic question set listing three items, with the positive question-shape sections
    unless sections replaces them (None drops one)."""
    data = {
        "manifest": {"meta_model_version": version},
        "glossary": [{"id": "amount", "name": "Amount"}],
        "terminology": {"aliases": [{"phrase": "takings", "term": "amount"}, {"phrase": "by region", "group_by": "u.accounts.region"}],
                        "predicates": [{"phrases": ["inflows"], "column": "u.events.amount", "op": "gt", "value": 0}]},
        "measures": [{"id": "amount"}],
        "contexts": [{"id": "booked"}, {"id": "settled"}],
        "entities": [{"id": "account"}],
        "calendars": [{"id": "fiscal"}],
        "joins": [{"id": "events_accounts"}],
        "derived_measures": [{"id": "booked_less_settled"}],
        "banded_dimensions": [{"id": "amount_band"}],
        "acronyms": [{"id": "NNA"}],
        "tests": {"sets": [{"name": "shapes_synthetic", "version": "1", "items": ["s1", "s2", "s3"]},
                           {"name": "loose", "version": "2"}]},
        **deepcopy(SECTIONS),
    }
    for key, value in sections.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = deepcopy(value)
    return Pack("example", tmp_path, data=data, lock=deepcopy(LOCK))


def preload(**over) -> standards.Preload:
    """A synthetic preload: thirteen operations, one coded builder and two clarification texts, with phrases, shapes,
    recipes and clarification rules where over gives them."""
    return standards.Preload("1.1.0", {}, {}, {}, {}, operations=OPERATIONS, coded_builders=("planshapes.compose:contexts",),
                             clarification_texts=dict(TEXTS), **over)


RULES = {"sections": checks.sections, "phrases": checks.phrases, "shapes": checks.shapes, "recipes": checks.recipes,
         "examples": checks.examples, "clarifications": checks.clarifications, "vocabulary": checks.vocabulary,
         "ids": lambda p, _: checks.ids(p, ["shapes", "recipes", "clarifications", "examples"])}


def found(rule: str, p: Pack, pre: standards.Preload) -> list:
    run = RULES[rule]
    return run(p) if rule in ("sections", "vocabulary") else run(p, pre)


def test_the_positive_pack_holds_every_question_shape_rule(tmp_path):
    p, pre = pack(tmp_path), preload()
    shaped = [f for f in checks.sections(p) if checks.part(f) in SECTIONS]
    assert shaped == []
    assert checks.ids(p, ["shapes", "recipes", "clarifications", "examples"]) == []
    for rule in ("phrases", "shapes", "recipes", "examples", "clarifications"):
        assert found(rule, p, pre) == [], rule
    assert checks.coupled(p, pre) == [["two-context-difference"]]
    assert checks.coupled_groups(p.data, pre) == [["two-context-difference"]]


def test_a_pack_with_no_shape_sections_has_no_shape_findings_and_no_groups(tmp_path):
    p = pack(tmp_path, version="1.2.0", **{s: None for s in SECTIONS})
    pre = preload()
    assert [found(rule, p, pre) for rule in ("phrases", "shapes", "recipes", "examples", "clarifications")] == [[]] * 5
    assert checks.coupled(p, pre) == []


def edit(data: dict, path: str, value) -> None:
    """Sets the value at a dotted path with [i] indexes in data, appending where the index is the list's length; None
    drops a section."""
    parts = [int(x) if x.isdigit() else x for x in path.replace("[", ".").replace("]", "").split(".")]
    here = data
    for step in parts[:-1]:
        here = here[step]
    if value is None:
        here.pop(parts[-1], None)
    elif isinstance(here, list) and parts[-1] == len(here):
        here.append(deepcopy(value))
    else:
        here[parts[-1]] = deepcopy(value)


def case(rule: str, changes: dict, path: str, stem: str, version: str = "1.6.0", **pre):
    return pytest.param(rule, changes, path, stem, version, pre, id=f"{rule}:{stem[:48]}")


CASES = [
    case("sections", {"phrases[0].filler": True}, "phrases[0]", "names one of operation or filler: true"),
    case("sections", {"phrases[0]": {"phrase": "minus"}}, "phrases[0]", "names one of operation or filler: true"),
    case("sections", {"phrases[1].filler": False}, "phrases[1].filler", "is true or absent"),
    case("sections", {"shapes[0].slots[0].kind": "galaxy"}, "shapes[0].slots[0].kind", "is one of measure, context"),
    case("sections", {"shapes[0].stage": "live"}, "shapes[0].stage", "is one of specify, shadow, graph_first, retired"),
    case("sections", {"shapes[0].slots[0].count": {"min": 3, "max": 1}}, "shapes[0].slots[0].count", "is a whole number, or {min, max}"),
    case("sections", {"shapes[0].constraints": [{"near": ["difference", "period"]}]}, "shapes[0].constraints[0]",
         "is one of {before|after|adjacent}: [a, b]"),
    case("sections", {"shapes[0].requires": [{"one_of": ["gt"]}]}, "shapes[0].requires[0]",
         "is an operation or marker name, or {one_of: [two or more names]}"),
    case("sections", {"shapes[0].sideways": True}, "shapes[0].sideways", "is not a field here"),
    case("sections", {"recipes[0].blocks[0]": {"id": "cmp", "left": "$context[0]"}}, "recipes[0].blocks[0]",
         "is a mapping with a block and an id"),
    case("sections", {"examples[0].approved_on": "18/11/2026"}, "examples[0].approved_on", "is a date (YYYY-MM-DD)"),
    case("sections", {"clarifications[0].when.slots": {"galaxy": 2}}, "clarifications[0].when.slots.galaxy", "is not a slot kind"),
    case("sections", {"clarifications[0].when.slots": {"context": -1}}, "clarifications[0].when.slots.context", "is a whole number"),
    case("sections", {"shape_tests[0].tests": {"set": "shapes_synthetic"}}, "shape_tests[0].tests.items", "is required"),
    case("ids", {"shapes": [SHAPE, SHAPE]}, "shapes[1].id", "'two-context-difference' is shapes[0]'s already"),
    case("ids", {"recipes[0].id": "Two-Context"}, "recipes[0].id", "'Two-Context' is not an id"),
    case("phrases", {"phrases[0].phrase": "Minus"}, "phrases[0].phrase", "Minus is not lower case"),
    case("phrases", {"phrases[0].phrase": " minus "}, "phrases[0].phrase", "' minus ' has leading or trailing space"),
    case("phrases", {"phrases[0].phrase": "minus\t"}, "phrases[0].phrase", "'minus\\t' has leading or trailing space"),
    case("phrases", {"phrases[0].operation": "sideways"}, "phrases[0].operation", "sideways is not an operation the platform knows"),
    case("phrases", {}, "phrases[0].phrase", "minus is a preloaded phrase", phrases={"minus": {"phrase": "minus", "operation": "difference"}}),
    case("phrases", {"phrases[1]": {"phrase": "minus", "operation": "difference"}}, "phrases[1].phrase", "minus is phrases[0]'s already"),
    case("phrases", {"phrases[0].phrase": "takings"}, "phrases[0].phrase", "takings is also an alias (terminology.aliases[0].phrase)"),
    case("phrases", {"phrases[0].phrase": "by region"}, "phrases[0].phrase", "by region is also a grouping phrase"),
    case("phrases", {"phrases[0].phrase": "nna"}, "phrases[0].phrase", "nna is also an acronym (acronyms[0])"),
    case("phrases", {}, "phrases", "needs meta-model 1.6.0 or later", version="1.5.0"),
    case("vocabulary", {"terminology": {"aliases": [], "predicates": [
        {"phrases": ["inflows"], "column": "u.events.amount", "op": "gt", "value": 0, "part": True}]}},
         "terminology.predicates[0].part", "needs meta-model 1.6.0 or later", version="1.5.0"),
    case("shapes", {"shapes[0].requires": ["difference", "sideways"]}, "shapes[0].requires[1]",
         "sideways is not an operation or a marker the platform knows"),
    case("shapes", {"shapes[0].requires": [{"one_of": ["gt", "sideways"]}, "difference"]}, "shapes[0].requires[0].one_of[1]",
         "sideways is not an operation or a marker"),
    case("shapes", {"shapes[0].forbids": ["comparison", "difference"]}, "shapes[0].forbids[1]", "difference is both required and forbidden"),
    case("shapes", {"shapes[0].requires": [{"one_of": ["gt", "ge"]}, "difference"], "shapes[0].forbids": ["comparison", "ge", "gt"]},
         "shapes[0].requires[0]", "one_of [gt, ge] is both required and forbidden: the shape forbids every name of it"),
    case("shapes", {"shapes[0].constraints": [{"before": ["difference", "sideways"]}]}, "shapes[0].constraints[0].before[1]",
         "sideways is not an operation, a marker or a slot kind"),
    case("shapes", {"shapes[0].recipe": "nowhere"}, "shapes[0].recipe", "nowhere is not a recipe the pack or the platform declares"),
    case("shapes", {"recipes[0].blocks[0].left": "$entity"}, "shapes[0].recipe",
         "the recipe two-context-difference uses $entity, which this shape does not declare"),
    case("shapes", {"recipes[0].blocks[0].right": "$context[2]"}, "shapes[0].recipe",
         "the recipe two-context-difference uses $context[2], and the shape takes at most 2 context"),
    case("shapes", {"recipes[0].blocks[0].right": "$context"}, "shapes[0].recipe",
         "the recipe two-context-difference gives $context without an index"),
    case("shapes", {"shapes[0].slots[1].count": 0}, "shapes[0].recipe",
         "the recipe two-context-difference uses $period, and the shape takes at most 0 period"),
    case("shapes", {"shapes[0].slots[1].count": {"max": 0}}, "shapes[0].recipe",
         "the recipe two-context-difference uses $period, and the shape takes at most 0 period"),
    case("shapes", {"shapes[0].slots[0].count": 0}, "shapes[0].recipe",
         "the recipe two-context-difference uses $context[0], and the shape takes at most 0 context"),
    case("shapes", {"shapes[0].slots[2]": {"name": "number", "kind": "number"}}, "shapes[0].slots[2]",
         "is declared and the recipe two-context-difference never uses it"),
    case("shapes", {"recipes[0].blocks[0].left": "$period"}, "shapes[0].recipe",
         "the recipe two-context-difference gives $period (period) to contexts.left, which takes ref"),
    case("shapes", {"shapes[0].slots[1].name": "Period"}, "shapes[0].slots[1].name", "'Period' is not a slot name"),
    case("shapes", {"shapes[0].slots[1].kind": "context"}, "shapes[0].slots[1].kind", "context is slots[0]'s kind already"),
    case("shapes", {"shapes[0].replaces": "planshapes.nowhere"}, "shapes[0].replaces",
         "planshapes.nowhere is not a coded builder the platform lists"),
    case("shapes", {"shapes[0].replaces": "planshapes.compose:contexts",
                    "shapes[1]": {**SHAPE, "id": "again", "replaces": "planshapes.compose:contexts"}}, "shapes[1].replaces",
         "planshapes.compose:contexts is replaced by two-context-difference (shapes[0]) already: one shape mirrors each coded builder"),
    case("shapes", {"shapes[0].replaces": "planshapes.compose:contexts"}, "shapes[0].replaces",
         "planshapes.compose:contexts is replaced by the preloaded shape mirror already",
         shapes={"mirror": {**SHAPE, "id": "mirror", "replaces": "planshapes.compose:contexts"}},
         recipes={"two-context-difference": RECIPE}),
    case("shapes", {}, "shapes[0].id", "two-context-difference is a preloaded shape's id",
         shapes={"two-context-difference": SHAPE}, recipes={"two-context-difference": RECIPE}),
    case("shapes", {"shape_tests": None}, "shapes[0]", "has no shape_tests"),
    case("shapes", {"examples": None}, "shapes[0]", "has no worked example in this pack (examples)"),
    case("shapes", {"shape_tests[1]": {"shape": "nowhere", "tests": {"set": "shapes_synthetic", "items": ["s2"]}}},
         "shape_tests[1].shape", "nowhere is not a shape the pack or the platform declares"),
    case("shapes", {"shape_tests[0].tests.set": "nowhere"}, "shape_tests[0].tests.set", "nowhere is not a question set the pack names"),
    case("shapes", {"shape_tests[0].tests.set": "loose"}, "shape_tests[0].tests.set", "loose names no items in tests.sets"),
    case("shapes", {"shape_tests[0].tests.items": ["s1", "s9"]}, "shape_tests[0].tests.items[1]", "s9 is not an item of shapes_synthetic 1"),
    case("shapes", {"shape_tests[0].tests.items": []}, "shape_tests[0].tests.items", "cites no item"),
    case("shapes", {"shape_tests[0].tests.items": ["s1", "s1"]}, "shape_tests[0].tests.items[1]",
         "s1 of shapes_synthetic is cited for two-context-difference already (shape_tests[0].tests.items[0]): a shape cites "
         "each test item once"),
    case("shapes", {"shape_tests[1]": {"shape": "two-context-difference", "tests": {"set": "shapes_synthetic", "items": ["s2", "s1"]}}},
         "shape_tests[1].tests.items[1]", "s1 of shapes_synthetic is cited for two-context-difference already (shape_tests[0]"),
    case("shapes", {"examples[0].shape": "borrowed"}, "examples[0]", "cites borrowed, a preloaded shape, which has no shape_tests here",
         shapes={"borrowed": {**SHAPE, "id": "borrowed", "requires": ["ratio"]}}),
    case("shapes", {"shape_tests[0].shape": "borrowed"}, "shape_tests[0]", "cites borrowed, a preloaded shape, which has no worked example",
         shapes={"borrowed": {**SHAPE, "id": "borrowed", "requires": ["ratio"]}}),
    case("shapes", {}, "shapes", "needs meta-model 1.6.0 or later", version="1.5.0"),
    case("shapes", {}, "shape_tests", "needs meta-model 1.6.0 or later", version="1.5.0"),
    case("recipes", {"recipes[0].blocks[0].block": "nowhere"}, "recipes[0].blocks[0].block", "nowhere is not a building block"),
    case("recipes", {"recipes[0].blocks[0].sideways": 1}, "recipes[0].blocks[0].sideways", "is not a parameter of contexts"),
    case("recipes", {"recipes[0].blocks[3]": {"block": "compare", "id": "f", "left": "d", "op": "gt", "value": "ten"}},
         "recipes[0].blocks[3].value", "is a scalar, not 'ten'"),
    case("recipes", {"recipes[0].blocks[0]": {"block": "contexts", "id": "cmp", "left": "$context[0]"}}, "recipes[0].blocks[0].right",
         "is required by contexts"),
    case("recipes", {"recipes[0].blocks[1].id": "cmp"}, "recipes[0].blocks[1].id", "cmp is blocks[0]'s id already"),
    case("recipes", {"recipes[0].blocks[1].id": "a/b"}, "recipes[0].blocks[1].id", "a/b is reserved"),
    case("recipes", {"recipes[0].blocks[1].id": "time"}, "recipes[0].blocks[1].id", "time is reserved"),
    case("recipes", {"recipes[0].blocks[1].numerator": "nowhere"}, "recipes[0].blocks[1].numerator",
         "nowhere is not a block of this recipe or a slot"),
    case("recipes", {"recipes[0].blocks": [
        {"block": "contexts", "id": "cmp", "left": "$context[0]", "right": "$context[1]"},
        {"block": "difference", "id": "a", "numerator": "b", "denominator": "cmp/base"},
        {"block": "difference", "id": "b", "numerator": "a", "denominator": "cmp/other"}]}, "recipes[0].blocks[1]",
         "its references form a cycle: a -> b -> a"),
    case("recipes", {"recipes[0].blocks[2]": {"block": "range", "id": "t",
                                              "first": {"kind": "month", "value": "2025-03", "calendar": "fiscal"},
                                              "last": {"kind": "month", "value": "2025-01", "calendar": "fiscal"}}},
         "recipes[0]", "does not expand into a valid query-plan/v1"),
    case("recipes", {}, "recipes[0].id", "two-context-difference is a preloaded recipe's id", recipes={"two-context-difference": RECIPE}),
    case("recipes", {"recipes[0].blocks[3]": {"block": "measure", "id": "m", "measure": "nowhere", "context": "booked",
                                              "mode": "current", "filters": []}},
         "recipes[0].blocks[3].measure", "nowhere is not a measure the pack declares"),
    case("recipes", {"recipes[0].blocks[3]": {"block": "measure", "id": "m", "measure": "amount", "context": "amount",
                                              "mode": "current", "filters": []}},
         "recipes[0].blocks[3].context", "amount is not a context the pack declares"),
    case("recipes", {}, "recipes", "needs meta-model 1.6.0 or later", version="1.5.0"),
    case("examples", {"examples[0].plan.sideways": 1}, "examples[0].plan.sideways", "Extra inputs are not permitted"),
    case("examples", {"examples[0].plan.measures[0].context.id": "nowhere"}, "examples[0].plan.measures[0].context",
         "nowhere is not a context the pack declares"),
    case("examples", {"examples[0].plan.measures[0].measure.id": "count:nowhere"}, "examples[0].plan.measures[0].measure",
         "count:nowhere is not a measure the pack declares"),
    case("examples", {"examples[0].plan.time.period.calendar.id": "gregorian"}, "examples[0].plan.time.period.calendar",
         "gregorian is not a calendar the pack declares"),
    case("examples", {"examples[0].shape": "nowhere"}, "examples[0].shape", "nowhere is not a shape the pack or the platform declares"),
    case("examples", {"shapes[0].stage": "shadow"}, "examples[0]",
         "is for two-context-difference, at stage shadow: above specify a worked example names approved_by and approved_on"),
    case("examples", {"examples[0].approved_by": "steward"}, "examples[0]", "names approved_by without approved_on"),
    case("examples", {"examples[0].approved_on": "2026-11-18"}, "examples[0]", "names approved_on without approved_by"),
    case("examples", {}, "examples", "needs meta-model 1.6.0 or later", version="1.5.0"),
    case("clarifications", {"clarifications[0].when.requires": ["sideways"]}, "clarifications[0].when.requires[0]",
         "sideways is not an operation or a marker the platform knows"),
    case("clarifications", {"clarifications[0].when.forbids": ["sideways"]}, "clarifications[0].when.forbids[0]",
         "sideways is not an operation or a marker the platform knows"),
    case("clarifications", {"clarifications[0].when.constraints": [{"after": ["comparison", "sideways"]}]},
         "clarifications[0].when.constraints[0].after[1]", "sideways is not an operation, a marker or a slot kind"),
    case("clarifications", {"clarifications[0].message": "nowhere"}, "clarifications[0].message",
         "nowhere is not a clarification text the platform lists"),
    case("clarifications", {}, "clarifications[0].id", "contexts-or-periods-2 is a preloaded clarification rule's id",
         clarifications={"contexts-or-periods-2": CLARIFICATION}),
    case("clarifications", {}, "clarifications", "needs meta-model 1.6.0 or later", version="1.5.0"),
]


@pytest.mark.parametrize("rule,changes,path,stem,version,pre", CASES)
def test_each_planted_invalid_declaration_is_refused_by_its_rule_at_its_place(tmp_path, rule, changes, path, stem, version, pre):
    p = pack(tmp_path, version=version)
    for where, value in changes.items():
        edit(p.data, where, value)
    expected = {"sections": "S01", "ids": "S02", "phrases": "S32", "vocabulary": "S29", "shapes": "S33", "recipes": "S34",
                "examples": "S35", "clarifications": "S36"}[rule]
    got = [(f.rule, f.path, f.message) for f in found(rule, p, preload(**pre))]
    assert any(r == expected and at == f"pack.yaml:{path}" and stem in message for r, at, message in got), got


def two_shapes(second: dict, clarifications: list | None = None, recipes: list | None = None) -> dict:
    """Two shapes that both read a comparison of two contexts, the first also requiring a difference."""
    first = {"id": "change-a", "label": "A", "slots": [{"name": "context", "kind": "context", "count": 2}],
             "requires": ["comparison", "difference"], "recipe": "compare-and-subtract", "stage": "specify"}
    base = {"id": "change-b", "label": "B", "slots": [{"name": "context", "kind": "context", "count": 2}],
            "requires": ["comparison"], "recipe": "compare-and-subtract", "stage": "specify"}
    subtract = {"id": "compare-and-subtract", "blocks": [
        {"block": "contexts", "id": "cmp", "left": "$context[0]", "right": "$context[1]"},
        {"block": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other"}]}
    return {"shapes": [first, {**base, **second}], "recipes": [subtract, *(recipes or [])],
            "clarifications": clarifications or [], "shape_tests": [], "examples": []}


def coupling(tmp_path, sections: dict) -> tuple[list, list]:
    p, pre = pack(tmp_path, **sections), preload()
    return [f for f in checks.shapes(p, pre) if "can match the same questions" in f.message], checks.coupled(p, pre)


def test_two_unseparated_shapes_that_both_contribute_a_difference_are_refused(tmp_path):
    clashes, groups = coupling(tmp_path, two_shapes({}))
    assert [(f.rule, f.path) for f in clashes] == [("S33", "pack.yaml:shapes[1]")]
    assert clashes[0].message.startswith("can match the same questions as change-a and both contribute a contexts block to the "
                                         "plan's comparisons section: separate them")
    assert groups == [["change-a", "change-b"]]


def test_a_forbids_separates_two_shapes(tmp_path):
    clashes, groups = coupling(tmp_path, two_shapes({"forbids": ["difference"]}))
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


def test_a_clarification_rule_covering_both_shapes_separates_them(tmp_path):
    rule = {"id": "which-comparison", "message": "both-comparison-contexts", "when": {"requires": ["comparison"], "slots": {"context": 2}}}
    clashes, groups = coupling(tmp_path, two_shapes({}, [rule]))
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


def test_a_clarification_rule_that_misses_some_shared_questions_does_not_separate(tmp_path):
    rule = {"id": "which-comparison", "message": "both-comparison-contexts",
            "when": {"requires": ["comparison", "ratio"], "slots": {"context": 2}}}
    clashes, groups = coupling(tmp_path, two_shapes({}, [rule]))
    assert len(clashes) == 1 and groups == [["change-a", "change-b"]]


def test_overlapping_shapes_with_disjoint_contributions_form_one_coupled_group(tmp_path):
    change = {"id": "percent-change", "blocks": [
        {"block": "percent_change", "id": "p", "numerator": "$context[0]", "denominator": "$context[1]"}]}
    clashes, groups = coupling(tmp_path, two_shapes({"recipe": "percent-change"}, recipes=[change]))
    assert clashes == [] and groups == [["change-a", "change-b"]]


def test_slot_counts_that_do_not_meet_separate_two_shapes(tmp_path):
    three = {"slots": [{"name": "context", "kind": "context", "count": {"min": 3}}]}
    clashes, groups = coupling(tmp_path, two_shapes(three))
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


def test_a_one_of_group_the_other_shape_forbids_whole_separates_them(tmp_path):
    sections = two_shapes({"requires": ["comparison", {"one_of": ["gt", "ge"]}]})
    sections["shapes"][0]["forbids"] = ["gt", "ge"]
    clashes, groups = coupling(tmp_path, sections)
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


def test_preload_shapes_join_the_groups_and_count_as_used_when_cited(tmp_path):
    borrowed = {**SHAPE, "id": "borrowed", "forbids": [], "requires": ["difference"]}
    p = pack(tmp_path, shape_tests=SECTIONS["shape_tests"] + [{"shape": "borrowed", "tests": {"set": "shapes_synthetic", "items": ["s2"]}}],
             examples=SECTIONS["examples"] + [{**SECTIONS["examples"][0], "id": "borrowed-example", "shape": "borrowed"}])
    pre = preload(shapes={"borrowed": borrowed}, recipes={"two-context-difference": RECIPE})
    p.data["recipes"] = []
    assert checks.shapes(p, pre) == [
        Finding("S33", "pack.yaml:shapes[0]", "can match the same questions as borrowed and both contribute a contexts block to "
                       "the plan's comparisons section: separate them with a forbids, slot counts, a clarification rule or before "
                       "constraints in reverse order, or make their contributions disjoint")]
    assert checks.coupled(p, pre) == [["borrowed", "two-context-difference"]]


def test_two_shapes_that_both_set_the_plan_s_time_read_as_setting_it(tmp_path):
    single = {"id": "single-period", "blocks": [{"block": "single", "id": "t", "period": "$period"}]}
    span = {"id": "span", "blocks": [{"block": "range", "id": "t", "first": "$period[0]", "last": "$period[1]"}]}
    sections = two_shapes({"recipe": "span", "slots": [{"name": "period", "kind": "period", "count": 2}]}, recipes=[single, span])
    sections["shapes"][0].update(recipe="single-period", slots=[{"name": "period", "kind": "period", "count": {"max": 2}}])
    clashes, _ = coupling(tmp_path, sections)
    assert [f.message.split(":")[0] for f in clashes] == ["can match the same questions as change-a and both set the plan's time"]


def reversed_before(first: list, second: list, **change_b) -> dict:
    """two_shapes with change-a's constraints first and change-b's second, change-b changed as change_b gives."""
    sections = two_shapes({**change_b, "constraints": second})
    sections["shapes"][0]["constraints"] = first
    return sections


def test_before_constraints_in_reverse_order_on_operands_both_require_separate_two_shapes(tmp_path):
    """F9 (#449, #510): one shape's before[comparison, difference] and the other's before[difference, comparison],
    where both require both, cannot match one question."""
    sections = reversed_before([{"before": ["comparison", "difference"]}], [{"before": ["difference", "comparison"]}],
                               requires=["comparison", "difference"])
    clashes, groups = coupling(tmp_path, sections)
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


def test_reversed_before_constraints_do_not_separate_where_one_shape_does_not_require_an_operand(tmp_path):
    """F9's "only when": change-b does not require difference, so a question without one meets its before and
    change-a's both."""
    sections = reversed_before([{"before": ["comparison", "difference"]}], [{"before": ["difference", "comparison"]}])
    clashes, groups = coupling(tmp_path, sections)
    assert [(f.rule, f.path) for f in clashes] == [("S33", "pack.yaml:shapes[1]")]
    assert groups == [["change-a", "change-b"]]


def test_a_slot_kind_both_shapes_count_from_one_is_required_and_separates_in_reverse_order(tmp_path):
    """EM decision (2026-11-18): a slot kind a shape declares with a count of at least 1 is a required operand."""
    sections = reversed_before([{"before": ["comparison", "context"]}], [{"before": ["context", "comparison"]}])
    clashes, groups = coupling(tmp_path, sections)
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


@pytest.mark.parametrize("count", [{"min": 0, "max": 2}, {"max": 2}])
def test_a_slot_kind_counted_from_zero_is_not_required_and_does_not_separate(tmp_path, count):
    sections = reversed_before([{"before": ["comparison", "context"]}], [{"before": ["context", "comparison"]}],
                               slots=[{"name": "context", "kind": "context", "count": count}])
    clashes, groups = coupling(tmp_path, sections)
    assert len(clashes) == 1 and groups == [["change-a", "change-b"]]


def test_a_slot_kind_a_shape_does_not_declare_is_not_required(tmp_path):
    sections = reversed_before([{"before": ["comparison", "period"]}], [{"before": ["period", "comparison"]}])
    clashes, groups = coupling(tmp_path, sections)
    assert len(clashes) == 1 and groups == [["change-a", "change-b"]]


def test_before_constraints_in_the_same_order_do_not_separate(tmp_path):
    sections = reversed_before([{"before": ["comparison", "difference"]}], [{"before": ["comparison", "difference"]}],
                               requires=["comparison", "difference"])
    clashes, groups = coupling(tmp_path, sections)
    assert len(clashes) == 1 and groups == [["change-a", "change-b"]]


@pytest.mark.parametrize("second", [{"after": ["comparison", "difference"]}, {"adjacent": ["difference", "comparison"]},
                                    {"after": ["difference", "comparison"]}])
def test_after_and_adjacent_never_separate_two_shapes(tmp_path, second):
    sections = reversed_before([{"before": ["comparison", "difference"]}], [second], requires=["comparison", "difference"])
    clashes, groups = coupling(tmp_path, sections)
    assert len(clashes) == 1 and groups == [["change-a", "change-b"]]


@pytest.mark.parametrize("count", [{"min": 1, "max": 2}, {"min": 1}])
def test_a_slot_kind_counted_from_one_as_a_range_is_required_and_separates(tmp_path, count):
    sections = reversed_before([{"before": ["comparison", "context"]}], [{"before": ["context", "comparison"]}],
                               slots=[{"name": "context", "kind": "context", "count": count}])
    clashes, groups = coupling(tmp_path, sections)
    assert clashes == [] and groups == [["change-a"], ["change-b"]]


def test_reversed_before_constraints_separate_a_pack_shape_from_a_preloaded_one(tmp_path):
    borrowed = {**SHAPE, "id": "borrowed", "forbids": [], "requires": ["difference"],
                "constraints": [{"before": ["context", "difference"]}]}
    p = pack(tmp_path, shape_tests=SECTIONS["shape_tests"] + [{"shape": "borrowed", "tests": {"set": "shapes_synthetic", "items": ["s2"]}}],
             examples=SECTIONS["examples"] + [{**SECTIONS["examples"][0], "id": "borrowed-example", "shape": "borrowed"}])
    p.data["shapes"][0]["constraints"] = [{"before": ["difference", "context"]}]
    pre = preload(shapes={"borrowed": borrowed}, recipes={"two-context-difference": RECIPE})
    p.data["recipes"] = []
    assert [f for f in checks.shapes(p, pre) if "can match the same questions" in f.message] == []
    assert checks.coupled(p, pre) == [["borrowed"], ["two-context-difference"]]


def test_one_item_id_in_two_sets_is_two_items_for_one_shape(tmp_path):
    p = pack(tmp_path)
    p.data["tests"]["sets"][1]["items"] = ["s1"]
    p.data["shape_tests"].append({"shape": "two-context-difference", "tests": {"set": "loose", "items": ["s1"]}})
    assert [f for f in checks.shapes(p, preload()) if "is cited for" in f.message] == []


def test_an_item_cited_three_times_is_refused_twice_each_at_its_first_citation(tmp_path):
    p = pack(tmp_path)
    p.data["shape_tests"][0]["tests"]["items"] = ["s1", "s1", "s1"]
    assert [(f.path, f.message.split(": ")[0]) for f in checks.shapes(p, preload()) if "is cited for" in f.message] == [
        (f"pack.yaml:shape_tests[0].tests.items[{k}]", "s1 of shapes_synthetic is cited for two-context-difference already "
         "(shape_tests[0].tests.items[0])") for k in (1, 2)]


@pytest.mark.parametrize("count", [0, {"max": 0}])
def test_a_slot_counted_0_excludes_its_kind_and_separates_from_a_shape_that_counts_it_from_one(tmp_path, count):
    sections = two_shapes({"slots": [{"name": "context", "kind": "context", "count": 2},
                                     {"name": "period", "kind": "period", "count": count}]})
    sections["shapes"][0]["slots"].append({"name": "period", "kind": "period"})
    p, pre = pack(tmp_path, **sections), preload()
    findings = checks.shapes(p, pre)
    assert [f for f in findings if "never uses it" in f.message and f.path.startswith("pack.yaml:shapes[1]")] == []
    assert [f for f in findings if "can match the same questions" in f.message] == []
    assert checks.coupled(p, pre) == [["change-a"], ["change-b"]]


def test_a_list_parameter_given_a_slot_counted_0_is_refused(tmp_path):
    previous = {"id": "previous-only", "blocks": [{"block": "previous_period", "id": "prev", "inputs": ["$measure"]}]}
    sections = two_shapes({"recipe": "previous-only", "slots": [{"name": "measure", "kind": "measure", "count": 0}]},
                          recipes=[previous])
    p = pack(tmp_path, **sections)
    assert "the recipe previous-only uses $measure, and the shape takes at most 0 measure" in [
        f.message for f in checks.shapes(p, preload())]


@pytest.mark.parametrize("stage, refused", [("retired", True), ("shadow", False)])
def test_a_pack_shape_replacing_a_builder_no_marker_reproduces_is_never_retired(tmp_path, stage, refused):
    """#515: S33 refuses retired for a shape that replaces a builder in standards.HELD_BUILDERS."""
    pre = preload()._replace(coded_builders=("planshapes.compose:contexts", "planshapes.parts"))
    p = pack(tmp_path)
    edit(p.data, "shapes[0].replaces", "planshapes.parts")
    edit(p.data, "shapes[0].stage", stage)
    messages = [f.message for f in checks.shapes(p, pre)]
    assert any(m.startswith("planshapes.parts stays coded and is never retired") for m in messages) is refused


def test_a_pack_shape_with_a_preloaded_id_and_its_builder_is_refused_once(tmp_path):
    pre = preload(shapes={"two-context-difference": {**SHAPE, "replaces": "planshapes.compose:contexts"}},
                  recipes={"two-context-difference": RECIPE})._replace(coded_builders=("planshapes.compose:contexts",))
    p = pack(tmp_path)
    edit(p.data, "shapes[0].replaces", "planshapes.compose:contexts")
    messages = [f.message for f in checks.shapes(p, pre)]
    assert any("is a preloaded shape's id" in m for m in messages) and not any("is replaced by" in m for m in messages)


def test_a_pack_shape_with_a_preloaded_id_and_another_preloaded_shape_s_builder_is_refused_for_both(tmp_path):
    pre = preload(shapes={"two-context-difference": {**SHAPE, "replaces": "planshapes.compose:contexts"},
                          "parts": {**SHAPE, "id": "parts", "replaces": "planshapes.parts"}},
                  recipes={"two-context-difference": RECIPE})._replace(coded_builders=("planshapes.compose:contexts",
                                                                                     "planshapes.parts"))
    p = pack(tmp_path)
    edit(p.data, "shapes[0].replaces", "planshapes.parts")
    messages = [f.message for f in checks.shapes(p, pre)]
    assert any("is a preloaded shape's id" in m for m in messages)
    assert "planshapes.parts is replaced by the preloaded shape parts already: one shape mirrors each coded builder" in messages


def test_a_one_of_member_is_not_required_and_does_not_separate(tmp_path):
    sections = reversed_before([{"before": ["comparison", "difference"]}], [{"before": ["difference", "comparison"]}],
                               requires=["comparison", {"one_of": ["difference", "ratio"]}])
    clashes, groups = coupling(tmp_path, sections)
    assert len(clashes) == 1 and groups == [["change-a", "change-b"]]


def test_one_item_may_test_two_shapes(tmp_path):
    """F10 refuses an item cited twice for one shape, never one item cited for two: a question counts towards every
    shape it uses."""
    borrowed = {**SHAPE, "id": "borrowed", "forbids": [], "requires": ["difference"]}
    p = pack(tmp_path, shape_tests=SECTIONS["shape_tests"] + [{"shape": "borrowed", "tests": {"set": "shapes_synthetic", "items": ["s1"]}}],
             examples=SECTIONS["examples"] + [{**SECTIONS["examples"][0], "id": "borrowed-example", "shape": "borrowed"}])
    pre = preload(shapes={"borrowed": borrowed}, recipes={"two-context-difference": RECIPE})
    assert [f for f in checks.shapes(p, pre) if "is cited for" in f.message] == []


def test_two_shapes_may_replace_different_builders(tmp_path):
    pre = preload()._replace(coded_builders=("planshapes.compose:contexts", "planshapes.parts"))
    p = pack(tmp_path)
    edit(p.data, "shapes[0].replaces", "planshapes.compose:contexts")
    edit(p.data, "shapes[1]", {**SHAPE, "id": "again", "replaces": "planshapes.parts"})
    assert [f for f in checks.shapes(p, pre) if "is replaced by" in f.message] == []


def test_a_block_s34_refuses_leaves_no_cascaded_unused_slot(tmp_path):
    """The recipe's time block (single), the only one that reads $period, is renamed to a block the catalogue does not
    list: S34 refuses it, and S33 does not also report period declared and never used, since an uncatalogued block's
    slot references are not read."""
    p, pre = pack(tmp_path), preload()
    edit(p.data, "recipes[0].blocks[2].block", "nowhere")
    assert [(f.rule, f.path) for f in checks.recipes(p, pre)][:1] == [("S34", "pack.yaml:recipes[0].blocks[2].block")]
    assert [f for f in checks.shapes(p, pre) if "never uses it" in f.message] == []


def test_s34_checks_the_schema_and_leaves_what_a_plan_means_to_later_checks(tmp_path):
    """F9 (#449, #510): S34 is a schema check. A previous_period comparison with no time block expands to a plan over
    all available time, which QueryPlan accepts and the plan checker refuses ('time comparison needs bounded periods',
    kge/semantics/plancheck.py). S34 passes it; V06 catches it after load. The compiler never imports the plan checker:
    EXCEPTIONS in tests/unit/test_layers.py lets kge.compiler.plans import only kge.semantics.plan and blocks."""
    previous = {"id": "previous-only", "blocks": [{"block": "previous_period", "id": "prev", "inputs": ["$measure"]}]}
    p = pack(tmp_path, recipes=[RECIPE, previous])
    expanded = plans.expand(previous)
    assert expanded["time"]["kind"] == "all_available" and expanded["comparisons"][0]["kind"] == "previous_period"
    assert [f for f in checks.recipes(p, preload()) if f.rule == "S34"] == []


@pytest.mark.parametrize("kind,ids", [
    ("context", ["booked", "context:amount", "context:count:account", "context:mapped:u.events.amount:sum", "cohort:booked",
                 "cohort:context:count:account", "schema:u.events.amount:avg", "schema:u.accounts.id:count"]),
    ("measure", ["amount", "count:account", "mapped:u.events.amount:count", "schema:u.events.amount:sum/measure"]),
    ("entity", ["account", "schema:entity:u.events"]),
    ("relation", ["events_accounts"]),
    ("calendar", ["fiscal", "schema_calendar"]),
    ("band", ["amount_band"]),
    ("declaration", ["booked_less_settled"]),
    ("column", ["u.events.amount"]),
    ("path", ["events_accounts"]),
])
def test_the_name_forms_a_worked_example_may_use_are_declared_ids_and_the_ids_the_runtime_makes_from_them(tmp_path, kind, ids):
    resolves = checks.plan_names_resolver(pack(tmp_path))
    assert [i for i in ids if not resolves[kind](i)] == []


@pytest.mark.parametrize("kind,wrong", [
    ("context", ["amount", "context:nowhere", "context:count:nowhere", "cohort:cohort:booked", "schema:u.events.nowhere:sum",
                 "schema:u.events.amount:median", "context:schema:u.events.amount:sum/measure"]),
    ("measure", ["booked", "count:nowhere", "mapped:u.events.amount:median", "booked/measure"]),
    ("entity", ["amount", "schema:entity:u.nowhere"]),
    ("relation", ["account"]),
    ("calendar", ["gregorian"]),
    ("band", ["amount"]),
    ("declaration", ["amount"]),
    ("column", ["u.events", "events.amount"]),
    ("path", ["account"]),
])
def test_a_name_neither_declared_nor_made_from_a_declaration_does_not_resolve(tmp_path, kind, wrong):
    resolves = checks.plan_names_resolver(pack(tmp_path))
    assert [i for i in wrong if resolves[kind](i)] == []


def sample(tmp_path, version: str = "1.6.0") -> tuple[Path, dict]:
    """A scratch copy of a committed pack, written for version, with the question-shape sections added over its own ids
    and one synthetic question set listing three items."""
    clients = tmp_path / "clients"
    shutil.copytree(CLIENTS / SAMPLE, clients / SAMPLE, ignore=shutil.ignore_patterns("fixture"))
    path = clients / SAMPLE / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["manifest"]["meta_model_version"] = version
    data["manifest"]["platform"]["meta_model"] = f"^{version.rsplit('.', 1)[0]}"
    data["schema"]["meta_model"] = version.rsplit(".", 1)[0]
    data["tests"]["sets"].append({"name": "shapes_synthetic", "version": "1", "items": ["s1", "s2", "s3"]})
    context, measure_id = data["contexts"][0]["id"], data["contexts"][0]["measure"]
    calendar = data["calendars"][0]["id"]
    worked = plan(measures=[measure("m1", context, measure_id), {**measure("m2", context, measure_id), "mode": "as_reported"}],
                  time={"kind": "single", "period": {"kind": "month", "value": "2025-01", "calendar": name(calendar)}})
    data.update(deepcopy(SECTIONS))
    data["phrases"] = [{"phrase": "less", "operation": "difference"}, {"phrase": "kindly", "filler": True}]
    data["examples"][0]["plan"] = worked
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return clients, data


def compile_sample(capsys, clients: Path, out: Path) -> tuple[int, str, str]:
    code = compiler.main(["--client", SAMPLE, "--clients", str(clients), "--out", str(out)])
    got = capsys.readouterr()
    return code, got.out, got.err


def test_a_pack_with_every_question_shape_section_compiles_and_lists_its_coupled_groups(tmp_path, capsys):
    clients, _ = sample(tmp_path)
    code, out, err = compile_sample(capsys, clients, tmp_path / "out")
    assert code == 0, err
    folder = Path(out.strip())
    if not folder.is_absolute():
        folder = Path.cwd() / folder
    model = yaml.safe_load((folder / "graph-model.yaml").read_text())
    groups = model["shape_groups"]
    assert ["two-context-difference"] in groups
    shapes = sum(len(g) for g in groups)
    assert f"compile: {len(groups)} coupled shape groups over {shapes} shapes, listed in graph-model.yaml (shape_groups)" in err


def test_the_same_sections_in_a_pack_written_before_meta_model_1_6_are_refused_by_their_rules(tmp_path, capsys):
    clients, _ = sample(tmp_path, version="1.2.0")
    code, out, err = compile_sample(capsys, clients, tmp_path / "out")
    assert code == 2 and out == ""
    gated = [line for line in err.splitlines() if line.endswith("needs meta-model 1.6.0 or later")]
    assert sorted(line.split()[0] for line in gated) == ["S32", "S33", "S33", "S34", "S35", "S36"], err
    assert sorted(line.split()[1].removesuffix(":") for line in gated) == [
        f"pack.yaml:{s}" for s in ("clarifications", "examples", "phrases", "recipes", "shape_tests", "shapes")]


def test_an_s01_finding_in_shapes_stops_s33_and_not_s32(tmp_path):
    clients, _ = sample(tmp_path)
    path = clients / SAMPLE / "pack.yaml"
    data = yaml.safe_load(path.read_text())
    data["shapes"][0]["stage"] = "live"
    data["phrases"][0]["phrase"] = "Less"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    p = packs.read(clients, SAMPLE)
    findings = checks.check(p, packs.meta_model(DEMO / "meta-model.yaml"), compiler.VERSION, emit.FILLS)
    rules = {f.rule for f in findings}
    assert any(f.rule == "S01" and f.path == "pack.yaml:shapes[0].stage" for f in findings), findings
    assert any(f.rule == "S32" and f.path == "pack.yaml:phrases[0].phrase" for f in findings), findings
    assert "S33" not in rules


def test_compile_refuses_a_builder_replaced_twice_and_an_item_cited_twice(tmp_path, capsys):
    """The planted pack through the compiler, as make check compile runs it: F11 against a builder the real preload
    lists, and F10."""
    clients, data = sample(tmp_path)
    data["shapes"][0]["replaces"] = "planshapes.compose:contexts"
    data["shapes"].append({**SHAPE, "id": "again", "replaces": "planshapes.compose:contexts"})
    data["shape_tests"].append({"shape": "again", "tests": {"set": "shapes_synthetic", "items": ["s2"]}})
    data["examples"].append({**data["examples"][0], "id": "again", "shape": "again"})
    data["shape_tests"][0]["tests"]["items"] = ["s1", "s1"]
    (clients / SAMPLE / "pack.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    code, out, err = compile_sample(capsys, clients, tmp_path / "out")
    assert code == 2 and out == ""
    lines = err.splitlines()
    assert any(line.startswith("S33 pack.yaml:shapes[1].replaces: planshapes.compose:contexts is replaced by "
                               "two-context-difference (shapes[0]) already") for line in lines), err
    assert any(line.startswith("S33 pack.yaml:shape_tests[0].tests.items[1]: s1 of shapes_synthetic is cited for "
                               "two-context-difference already") for line in lines), err


def test_check_pack_leaves_the_question_shape_sections_to_the_compiler(tmp_path):
    """A worked example's plan binds a dimension to a column by a Name mapping, and a recipe may name a column: neither
    is a fixture reference, so the up-front check reports no F03 for them."""
    folder = tmp_path / "clients" / "example"
    fixture = folder / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "schema.yaml").write_text(yaml.safe_dump({"warehouses": {"u": {"tables": [
        {"name": "events", "columns": [{"name": "amount", "type": "DOUBLE"}]}]}}}))
    binding = {"context": name("booked"), "column": name("u.events.amount"), "path": [name("events_accounts")], "level": None}
    worked = plan(dimensions=[{"kind": "column", "id": "by", "bindings": [binding]}])
    p = Pack("example", folder, data={
        "manifest": {"meta_model_version": "1.6.0"},
        "measures": [{"id": "amount", "column": "u.events.amount"}],
        "recipes": [{"id": "grouped", "blocks": [{"block": "column", "id": "by", "bindings": [binding]}]}],
        "examples": [{"id": "grouped", "shape": "grouped", "question": "amount by event", "plan": worked}]})
    ctx = upfront.Context(SimpleNamespace(estate=lambda: tmp_path / "estate"), p, None, fixture, None, None)
    assert upfront.references(ctx) == []
