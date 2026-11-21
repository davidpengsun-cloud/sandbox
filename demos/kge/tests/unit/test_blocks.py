"""The platform's building-block catalogue (kge/semantics/blocks.py) and the compiler's recipe and plan checks on it
(kge/compiler/plans.py), on synthetic recipes and plans only.

    .venv/bin/python -m pytest tests/unit/test_blocks.py -q

The catalogue is generated from plan.py's node kinds, so the pinned list below changes only with a reviewed change to
the query plan. A recipe composes those blocks with typed parameters and slot references; recipe_problems is S34's
schema check, and expansion with placeholder slots must give a plan QueryPlan accepts. plan_problems and plan_names are
S35's: a worked example's plan validated as query-plan/v1, and every name in it listed with its kind.
"""
from __future__ import annotations

from typing import get_args, get_origin

import pytest
from pydantic import BaseModel

from kge.compiler import plans
from kge.semantics import blocks, plan

BLOCKS = ["measure", "distinct_count", "column", "hierarchy", "time", "band", "compare", "in", "null", "all", "any", "not",
          "membership", "single", "range", "ytd", "latest_released", "all_available", "time_mode", "previous_period",
          "previous_year", "explicit_period", "contexts", "ratio", "percent_change", "difference", "share",
          "running_total", "moving_average", "order", "limit"]
PERIOD = {"kind": "month", "value": "2025-01", "calendar": "cal"}
COLUMN = {"kind": "column", "name": "src.facts.amount", "path": []}
BINDING = {"context": "certified", "column": "src.facts.region", "path": [], "level": None}
M1 = {"block": "measure", "id": "m1", "measure": "flows", "context": "certified", "mode": "current", "filters": []}
M2 = {**M1, "id": "m2", "context": "reported"}
G = {"block": "compare", "id": "g", "left": COLUMN, "op": "gt", "value": {"kind": "integer", "value": 5}}
D = {"block": "column", "id": "d", "bindings": [BINDING]}
T = {"block": "time", "id": "t", "calendar": "cal", "grain": "month"}


def recipe(*items) -> dict:
    return {"id": "synthetic", "blocks": [dict(i) for i in items]}


MINIMAL = {
    "measure": [M1],
    "distinct_count": [{"block": "distinct_count", "id": "n", "entity": "account", "context": "certified", "mode": "current",
                        "filters": [], "declaration": "accounts"}],
    "column": [D],
    "hierarchy": [{"block": "hierarchy", "id": "h", "bindings": [{**BINDING, "level": 2}]}],
    "time": [T],
    "band": [{"block": "band", "id": "b", "band": "size", "bindings": [BINDING]}],
    "compare": [G],
    "in": [{"block": "in", "id": "f", "left": COLUMN, "values": [{"kind": "text", "value": "north"}]}],
    "null": [{"block": "null", "id": "f", "left": COLUMN, "is_null": True}],
    "all": [{"block": "all", "id": "f", "children": ["g"]}, G],
    "any": [{"block": "any", "id": "f", "children": ["g"]}, G],
    "not": [{"block": "not", "id": "f", "child": "g"}, G],
    "membership": [{"block": "membership", "id": "f", "entity": "account", "relation": "holds", "context": "certified",
                    "filters": []}],
    "single": [{"block": "single", "id": "w", "period": PERIOD}],
    "range": [{"block": "range", "id": "w", "first": PERIOD, "last": {**PERIOD, "value": "2025-03"}}],
    "ytd": [{"block": "ytd", "id": "w", "through": PERIOD, "calendar": "cal"}],
    "latest_released": [{"block": "latest_released", "id": "w", "calendar": "cal"}],
    "all_available": [{"block": "all_available", "id": "w", "calendar": "cal"}],
    "time_mode": [M1, {"block": "time_mode", "id": "c", "inputs": ["m1"], "base": "current", "other": "as_reported"}],
    "previous_period": [M1, {"block": "previous_period", "id": "c", "inputs": ["m1"]}],
    "previous_year": [M1, {"block": "previous_year", "id": "c", "inputs": ["m1"]}],
    "explicit_period": [M1, {"block": "explicit_period", "id": "c", "inputs": ["m1"],
                             "other": {"kind": "single", "period": PERIOD}}],
    "contexts": [M1, M2, {"block": "contexts", "id": "c", "left": "m1", "right": "m2"}],
    "ratio": [M1, M2, {"block": "ratio", "id": "x", "numerator": "m1", "denominator": "m2"}],
    "percent_change": [M1, M2, {"block": "percent_change", "id": "x", "numerator": "m1", "denominator": "m2"}],
    "difference": [M1, M2, {"block": "difference", "id": "x", "numerator": "m1", "denominator": "m2"}],
    "share": [M1, D, {"block": "share", "id": "x", "input": "m1", "remove_dimensions": ["d"], "remove_filters": []}],
    "running_total": [M1, T, {"block": "running_total", "id": "x", "input": "m1", "partition_by": [], "order_by": "t"}],
    "moving_average": [M1, T, {"block": "moving_average", "id": "x", "input": "m1", "partition_by": [], "order_by": "t",
                               "length": 3}],
    "order": [M1, {"block": "order", "id": "o", "output": "m1", "direction": "desc", "nulls": "last"}],
    "limit": [{"block": "limit", "id": "n", "value": 10}],
}


def test_the_catalogue_is_every_node_kind_of_the_query_plan_plus_order_and_limit():
    kinds = set()
    for section in blocks.UNION_SECTIONS:
        annotation = blocks.bare(plan.QueryPlan.model_fields[section].annotation)
        if get_origin(annotation) is tuple:
            annotation = get_args(annotation)[0]
        kinds |= {k for m in blocks.members(annotation) for k in get_args(m.model_fields["kind"].annotation)}
    assert set(blocks.CATALOGUE) == kinds | {"order", "limit"}
    assert list(blocks.CATALOGUE) == BLOCKS, "a change to plan.py's node kinds is reviewed here"
    assert plans.CATALOGUE is blocks.CATALOGUE
    assert {b.section for b in blocks.CATALOGUE.values()} == set(blocks.SECTIONS)
    assert all(not {"kind", "id", "evidence"} & set(b.params) for b in blocks.CATALOGUE.values())


def test_each_parameter_is_typed_from_its_model_s_annotation():
    c = blocks.CATALOGUE
    assert c["measure"].params["measure"] == blocks.Param("measure", "name", True)
    assert c["measure"].params["filters"] == blocks.Param("filters", "predicate", True, True)
    assert c["measure"].params["aggregation"].values == ("sum", "count", "avg", "min", "max", "median")
    assert not c["measure"].params["aggregation"].required
    assert c["compare"].params["left"].type == "operand" and c["compare"].params["value"].type == "scalar"
    assert c["in"].params["values"] == blocks.Param("values", "scalar", True, True)
    assert c["column"].params["bindings"] == blocks.Param("bindings", "binding", True, True)
    assert c["explicit_period"].params["other"].type == "time" and c["single"].params["period"].type == "period"
    assert c["contexts"].params["left"] == blocks.Param("left", "ref", True)
    assert c["moving_average"].params["length"].type == "positive"
    assert c["null"].params["is_null"].type == "bool" and not c["compare"].params["magnitude"].required
    assert c["not"].params["child"] == blocks.Param("child", "predicate", True)
    assert c["order"].params["partition_by"] == blocks.Param("partition_by", "ref", False, True)
    assert c["limit"].params == {"value": blocks.Param("value", "positive", True)}


def test_two_node_kinds_of_one_name_are_refused_when_the_catalogue_is_built(monkeypatch):
    monkeypatch.setattr(blocks, "UNION_SECTIONS", ("measures", "measures"))
    with pytest.raises(ValueError, match="every node kind is one building block"):
        blocks._catalogue()


@pytest.mark.parametrize("block", BLOCKS)
def test_every_block_with_minimal_valid_parameters_expands_to_a_valid_plan(block):
    r = recipe(*MINIMAL[block])
    assert plans.recipe_problems(r) == []
    doc = plans.expand(r)
    assert plans.plan_problems(doc) == []
    assert any(i["block"] == block for i in r["blocks"])


def test_expansion_nests_a_named_predicate_and_fills_the_skeleton():
    doc = plans.expand(recipe({**M1, "filters": ["g"]}, G, {"block": "order", "id": "o", "output": "m1", "direction": "desc",
                                                              "nulls": "last"}, {"block": "limit", "id": "n", "value": 5}))
    assert doc["filters"] == [] and doc["measures"][0]["filters"][0]["id"] == "g"
    assert doc["measures"][0]["measure"] == {"id": "flows", "evidence": []}
    assert doc["order"] == [{"output": "m1", "direction": "desc", "nulls": "last", "evidence": []}] and doc["limit"] == 5
    assert doc["time"] == {"kind": "all_available", "calendar": {"id": "placeholder", "evidence": []}}
    assert (doc["schema"], doc["layout"], doc["obligations"]) == ("query-plan/v1", "aligned", [])
    assert plans.plan_problems(doc) == []
    assert plans.contributions(recipe({**M1, "filters": ["g"]}, G)) == {("measures", "measure")}
    empty = plans.expand(recipe(T))
    assert [m["id"] for m in empty["measures"]] == ["placeholder"] and plans.plan_problems(empty) == []


TWO_CONTEXT_DIFFERENCE = recipe({"block": "contexts", "id": "cmp", "left": "$context[0]", "right": "$context[1]"},
                                {"block": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other"})


def test_a_two_context_difference_expands_with_placeholder_contexts_and_validates():
    assert plans.recipe_problems(TWO_CONTEXT_DIFFERENCE) == []
    doc = plans.expand(TWO_CONTEXT_DIFFERENCE)
    assert [m["id"] for m in doc["measures"]] == ["slot:context:0", "slot:context:1"]
    assert doc["comparisons"] == [{"kind": "contexts", "id": "cmp", "left": "slot:context:0", "right": "slot:context:1",
                                   "evidence": []}]
    assert doc["derived"] == [{"kind": "difference", "id": "d", "numerator": "cmp/base", "denominator": "cmp/other",
                               "evidence": []}]
    assert plans.plan_problems(doc) == []
    assert [(at, n, i, b) for at, n, i, b, _ in plans.slot_refs(TWO_CONTEXT_DIFFERENCE)] == [
        ("blocks[0].left", "context", 0, "contexts"), ("blocks[0].right", "context", 1, "contexts")]
    assert plans.contributions(TWO_CONTEXT_DIFFERENCE) == {("comparisons", "contexts"), ("derived", "difference")}


def test_slot_placeholders_follow_the_parameter_type_and_the_slot_kind():
    r = recipe({"block": "measure", "id": "m", "measure": "$measure", "context": "$context", "mode": "current",
                "filters": ["f"]},
               {"block": "compare", "id": "f", "left": COLUMN, "op": "$comparison_op", "value": "$number"},
               {"block": "single", "id": "w", "period": "$period"},
               {"block": "running_total", "id": "x", "input": "m", "partition_by": [], "order_by": "$dimension"},
               {"block": "order", "id": "o", "output": "m", "direction": "$direction", "nulls": "last"},
               {"block": "limit", "id": "n", "value": "$number"})
    assert plans.recipe_problems(r) == []
    doc = plans.expand(r)
    m = next(x for x in doc["measures"] if x["id"] == "m")
    assert m["measure"] == {"id": "slot:measure:0", "evidence": []}
    assert m["filters"][0]["op"] == "gt" and m["filters"][0]["value"] == {"kind": "integer", "value": 1}
    assert doc["time"] == {"kind": "single", "period": {"kind": "month", "value": "2000-01",
                                                         "calendar": {"id": "placeholder", "evidence": []}}}
    assert [d["id"] for d in doc["dimensions"]] == ["slot:dimension:0"]
    assert doc["order"][0]["direction"] == "desc" and doc["limit"] == 1
    assert plans.plan_problems(doc) == []
    ranged = plans.expand(recipe({"block": "explicit_period", "id": "c", "inputs": "$measure", "other": "$range"}))
    assert ranged["comparisons"][0]["other"]["kind"] == "range" and ranged["comparisons"][0]["inputs"] == ["slot:measure:0"]
    assert plans.plan_problems(ranged) == []
    named = plans.expand(recipe({"block": "explicit_period", "id": "c", "inputs": "$m", "other": "$w"}), {"w": "range"})
    assert named["comparisons"][0]["other"]["kind"] == "range"


@pytest.mark.parametrize("items, where, says", [
    ([{"block": "per_entity", "id": "x"}], "blocks[0].block",
     "per_entity is not a building block the platform's catalogue lists (kge/semantics/blocks.py)"),
    ([{**M1, "colour": "red"}], "blocks[0].colour", "is not a parameter of measure: its parameters are measure, context"),
    ([{k: v for k, v in M1.items() if k != "mode"}], "blocks[0].mode", "is required by measure"),
    ([{**G, "value": "five"}], "blocks[0].value", "is a scalar, not 'five'"),
    ([M1, {**M2, "id": "m1"}], "blocks[1].id", "m1 is blocks[0]'s id already"),
    ([{**M1, "id": "time"}], "blocks[0].id", "time is reserved: a block id is not time or limit and holds no /"),
    ([{**M1, "id": "a/b"}], "blocks[0].id", "a/b is reserved"),
    ([M1, {"block": "difference", "id": "x", "numerator": "m1", "denominator": "m9"}], "blocks[1].denominator",
     "m9 is not a block of this recipe or a slot"),
    ([{"block": "difference", "id": "a", "numerator": "b", "denominator": "c"},
      {"block": "difference", "id": "b", "numerator": "a", "denominator": "a"}], "blocks[0]",
     "its references form a cycle: a -> b -> a"),
    ([{"block": "not", "id": "a", "child": "b"}, {"block": "not", "id": "b", "child": "a"}], "blocks[0]",
     "its references form a cycle: a -> b -> a"),
    ([{"block": "range", "id": "w", "first": {**PERIOD, "value": "2025-03"}, "last": PERIOD}], "",
     "does not expand into a valid query-plan/v1: time: Value error, range endpoints must be ordered"),
    ([{**M1, "filters": ["m2"]}, M2], "blocks[0].filters[0]", "is a predicate, not 'm2'"),
    ([{**M1, "filters": "g"}, G], "blocks[0].filters", "is a list of predicate, not 'g'"),
    ([{**M1, "mode": "live"}], "blocks[0].mode", "is one of current, as_reported, not 'live'"),
    ([{**M1, "measure": ""}], "blocks[0].measure", "is a name, not ''"),
    ([{"block": "limit", "id": "n", "value": 0}], "blocks[0].value", "is a positive, not 0"),
    ([{"block": "single", "id": "w", "period": PERIOD}, {"block": "single", "id": "v", "period": PERIOD}], "blocks[1]",
     "gives the plan's time, which blocks[0] gives already: a plan has one"),
    ([{"block": "measure"}], "blocks[0]", "is a mapping with a block and an id"),
])
def test_a_recipe_that_breaks_the_catalogue_is_refused_where_it_breaks(items, where, says):
    found = plans.recipe_problems(recipe(*items))
    assert any(at == where and says in msg for at, msg in found), found


def test_an_empty_recipe_is_refused():
    assert plans.recipe_problems({"id": "x", "blocks": []}) == [("blocks", "is a list of one or more blocks")]


def test_plan_problems_name_the_place_in_the_document():
    doc = plans.expand(recipe(M1))
    doc["measures"][0]["context"] = {"id": "", "evidence": []}
    assert plans.plan_problems(doc)[0] == ("measures[0].context.id", "String should have at least 1 character")
    doc = plans.expand(recipe(M1))
    doc["measures"][0]["colour"] = "red"
    assert plans.plan_problems(doc)[0] == ("measures[0].colour", "Extra inputs are not permitted")
    doc = plans.expand(recipe(M1, {"block": "ratio", "id": "x", "numerator": "m1", "denominator": "m1",
                                   "declaration": "per-client"}))
    doc["derived"][0]["declaration"] = {"id": 7, "evidence": []}
    assert plans.plan_problems(doc)[0][0] == "derived[0].declaration.id"
    assert plans.plan_problems({"schema": "query-plan/v1"})[0][0] == "layout"


SYNTHETIC_PLAN = {
    "schema": "query-plan/v1", "layout": "aligned",
    "measures": [
        {"kind": "measure", "id": "m", "measure": {"id": "flows", "evidence": []}, "context": {"id": "certified", "evidence": []},
         "mode": "current", "filters": [
             {"kind": "membership", "id": "f", "entity": {"id": "account", "evidence": []},
              "relation": {"id": "holds", "evidence": []}, "context": {"id": "holdings", "evidence": []},
              "filters": [], "evidence": []}]},
        {"kind": "distinct_count", "id": "n", "entity": {"id": "client", "evidence": []},
         "context": {"id": "reported", "evidence": []}, "mode": "current", "filters": [],
         "declaration": {"id": "clients", "evidence": []}}],
    "dimensions": [{"kind": "band", "id": "b", "band": {"id": "size", "evidence": []}, "bindings": [
        {"context": {"id": "certified", "evidence": []}, "column": {"id": "src.accounts.size", "evidence": []},
         "path": [{"id": "facts-to-accounts", "evidence": []}], "level": None}]}],
    "filters": [{"kind": "compare", "id": "g", "left": {"kind": "column", "name": {"id": "src.facts.amount", "evidence": []},
                                                        "path": []}, "op": "gt", "value": {"kind": "integer", "value": 5},
                 "evidence": []}],
    "time": {"kind": "ytd", "through": {"kind": "month", "value": "2025-03", "calendar": {"id": "fiscal", "evidence": []}},
             "calendar": {"id": "fiscal", "evidence": []}},
    "comparisons": [],
    "derived": [{"kind": "ratio", "id": "x", "numerator": "m", "denominator": "n", "evidence": [],
                 "declaration": {"id": "flow-per-client", "evidence": []}}],
    "order": [], "limit": None, "obligations": []}


def test_plan_names_lists_every_name_with_its_kind():
    assert plans.plan_problems(SYNTHETIC_PLAN) == []
    names = plans.plan_names(SYNTHETIC_PLAN)
    assert {k for _, k, _ in names} == {"measure", "context", "entity", "relation", "calendar", "band", "declaration",
                                        "column", "path"}
    assert set(names) == {
        ("measures[0].measure", "measure", "flows"), ("measures[0].context", "context", "certified"),
        ("measures[0].filters[0].entity", "entity", "account"), ("measures[0].filters[0].relation", "relation", "holds"),
        ("measures[0].filters[0].context", "context", "holdings"), ("measures[1].entity", "entity", "client"),
        ("measures[1].context", "context", "reported"), ("measures[1].declaration", "declaration", "clients"),
        ("dimensions[0].band", "band", "size"), ("dimensions[0].bindings[0].context", "context", "certified"),
        ("dimensions[0].bindings[0].column", "column", "src.accounts.size"),
        ("dimensions[0].bindings[0].path[0]", "path", "facts-to-accounts"),
        ("filters[0].left.name", "column", "src.facts.amount"), ("time.through.calendar", "calendar", "fiscal"),
        ("time.calendar", "calendar", "fiscal"), ("derived[0].declaration", "declaration", "flow-per-client")}


def test_every_name_field_of_the_query_plan_has_a_kind():
    seen, todo, named = set(), [plan.QueryPlan], set()
    while todo:
        model = todo.pop()
        if model in seen:
            continue
        seen.add(model)
        for field, info in model.model_fields.items():
            annotation = blocks.bare(info.annotation)
            if get_origin(annotation) is tuple:
                annotation = blocks.bare(get_args(annotation)[0])
            for m in blocks.members(annotation) or (annotation,):
                if m is plan.Name:
                    named.add(field)
                elif isinstance(m, type) and issubclass(m, BaseModel):
                    todo.append(m)
    assert named == set(plans.NAME_KINDS)


@pytest.mark.parametrize("kind, fits, refuses", [
    ("measure", ["name", "ref"], ["period", "scalar", "operand"]),
    ("context", ["name", "ref"], ["time", "binding"]),
    ("entity", ["name"], ["ref", "scalar"]),
    ("dimension", ["name", "ref"], ["period"]),
    ("period", ["period", "time"], ["name", "ref"]),
    ("range", ["time"], ["period", "name"]),
    ("number", ["scalar", "positive", "int"], ["name", "bool"]),
])
def test_which_slot_kinds_fill_which_parameter_types(kind, fits, refuses):
    assert all(plans.fits(kind, blocks.Param("p", t, True)) for t in fits)
    assert not any(plans.fits(kind, blocks.Param("p", t, True)) for t in refuses)


def test_a_comparison_operator_and_a_direction_fill_the_enums_that_offer_them():
    c = blocks.CATALOGUE
    assert plans.fits("comparison_op", c["compare"].params["op"])
    assert not plans.fits("comparison_op", c["order"].params["direction"])
    assert plans.fits("direction", c["order"].params["direction"])
    assert not plans.fits("direction", c["compare"].params["op"])
    assert not plans.fits("colour", c["compare"].params["op"])
