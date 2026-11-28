"""The platform's catalogue of building blocks: every node kind a recipe may compose, with its typed parameters.

The catalogue is generated from the query plan's own models (kge/semantics/plan.py) when this module loads, so it
never drifts from what the planner and the SQL compiler accept: one block for every value of the `kind` of every member
of QueryPlan's node unions (measures, dimensions, filters, time, comparisons, derived), plus `order` and `limit`. Each
block's parameters are its model's fields but `kind`, `id` and `evidence`, typed as the recipe writes them.

A pack composes these blocks and never adds one (question-shapes design, section 7): a recipe names blocks from this
catalogue, with slot references, typed literals, ids the pack declares and other blocks' ids as parameters. A new block
is a platform change to plan.py (and to the plan checks and the SQL compiler), released with the platform; the
catalogue then lists it without a change here.

Parameter types:
  ref        another node's id or output (plan.Id): a block of the recipe, `<block id>/<...>` for a comparison's output
  name       a declared id (plan.Name)
  scalar     a typed scalar, {kind, value} (plan.Scalar)
  period     a period, {kind, value, calendar} (plan.Period)
  time       a time selection (plan.Time)
  operand    a predicate's left side: a block's id or output, or a column {kind: column, name, path}
  predicate  a predicate block of the recipe, nested where it is named
  binding    a dimension binding, {context, column, path, level} (plan.DimensionBinding)
  enum       one of a Literal's values
  bool, int, positive
"""
from __future__ import annotations

from typing import Annotated, Literal, NamedTuple, Union, get_args, get_origin

from pydantic import BaseModel

from kge.semantics import plan


class Param(NamedTuple):
    name: str
    type: str
    required: bool
    many: bool = False
    values: tuple[str, ...] = ()


class Block(NamedTuple):
    name: str
    section: str
    model: type
    params: dict[str, Param]


SECTIONS = ("measures", "dimensions", "filters", "time", "comparisons", "derived", "order", "limit")
UNION_SECTIONS = SECTIONS[:6]
DROPPED = ("kind", "id", "evidence")


def bare(annotation):
    """The annotation without its Annotated metadata."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def members(annotation) -> tuple:
    """The members of a union annotation (None dropped), or () when it is not a union."""
    annotation = bare(annotation)
    if get_origin(annotation) in (Union, type(int | None)):
        return tuple(bare(a) for a in get_args(annotation) if a is not type(None))
    return ()


UNIONS = {frozenset(members(plan.Scalar)): "scalar", frozenset(members(plan.Time)): "time",
          frozenset(members(plan.Operand)): "operand", frozenset(members(plan.Predicate)): "predicate"}
MODELS = {plan.Name: "name", plan.Period: "period", plan.DimensionBinding: "binding"}


def _typed(name: str, annotation, metadata, required: bool) -> Param:
    """A model field as a block parameter: tuple[X, ...] is many X, and X | None is X (its requiredness is the field's)."""
    annotation = bare(annotation)
    many = get_origin(annotation) is tuple
    if many:
        annotation = bare(get_args(annotation)[0])
    union = members(annotation)
    if frozenset(union) in UNIONS:
        return Param(name, UNIONS[frozenset(union)], required, many)
    if len(union) == 1:
        annotation = union[0]
    if get_origin(annotation) is Literal:
        return Param(name, "enum", required, many, tuple(get_args(annotation)))
    if annotation in MODELS:
        return Param(name, MODELS[annotation], required, many)
    if annotation is bool:
        return Param(name, "bool", required, many)
    if annotation is int:
        return Param(name, "positive" if any(getattr(m, "gt", None) == 0 for m in metadata) else "int", required, many)
    if annotation is str:
        return Param(name, "ref", required, many)
    raise TypeError(f"{name}: {annotation!r} is not a parameter type the block catalogue knows")


def _params(model: type[BaseModel]) -> dict[str, Param]:
    return {name: _typed(name, field.annotation, field.metadata, field.is_required())
            for name, field in model.model_fields.items() if name not in DROPPED}


def _catalogue() -> dict[str, Block]:
    blocks: dict[str, Block] = {}

    def add(block: Block):
        if block.name in blocks:
            raise ValueError(f"block {block.name} is given by {blocks[block.name].model.__name__} and {block.model.__name__}: "
                             "every node kind is one building block")
        blocks[block.name] = block
    for section in UNION_SECTIONS:
        annotation = bare(plan.QueryPlan.model_fields[section].annotation)
        if get_origin(annotation) is tuple:
            annotation = get_args(annotation)[0]
        for model in members(annotation):
            params = _params(model)
            for kind in get_args(model.model_fields["kind"].annotation):
                add(Block(kind, section, model, params))
    add(Block("order", "order", plan.Order, _params(plan.Order)))
    add(Block("limit", "limit", int, {"value": Param("value", "positive", True)}))
    return blocks


CATALOGUE: dict[str, Block] = _catalogue()
