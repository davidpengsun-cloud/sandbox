"""Recipes and worked-example plans, checked against the query plan's own models (checks S33, S34 and S35).

A recipe composes building blocks from the platform's catalogue (kge/semantics/blocks.py, generated from plan.py), with
slot references (`$context[0]`), typed literals, ids the pack declares and other blocks' ids as parameters. Checking one
means typing each parameter against the block's own model and expanding the recipe, with placeholder slots, into a
query-plan/v1 document that QueryPlan accepts. A worked example's plan is validated as query-plan/v1 in the same way,
and its names listed for the compiler to resolve against the pack.

This is the only compiler module that imports kge.semantics.plan and kge.semantics.blocks: a named layer exception
(EXCEPTIONS in tests/unit/test_layers.py, and the kge-code skill's table). The compiler sits below core, where the query
plan's models live; validating with the models themselves runs their validators (range order, node references, a size
threshold's magnitude), which a copied schema would not. It holds until the query plan's models move below the
compiler.

Every location is relative to the recipe (`blocks[2].numerator`) or to the plan (`measures[0].context`); "" is the
recipe or the plan itself. Nothing here reads a pack, a lock or the warehouse: schema only (F9), never plan semantics.
"""
from __future__ import annotations

import re
from typing import get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

from kge.semantics import blocks, plan
from kge.semantics.blocks import Param

CATALOGUE = blocks.CATALOGUE
SLOT_REF = re.compile(r"\$([a-z][a-z0-9_]*)(?:\[(\d+)\])?")
RESERVED = ("time", "limit")
SINGULAR = ("time", "limit")
UNNAMED = ("time", "order", "limit")
SCHEMA = "query-plan/v1"
PLACEHOLDER = "placeholder"
DIMENSION_REFS = ("order_by", "partition_by", "remove_dimensions")
FITS = {"measure": ("name", "ref"), "context": ("name", "ref"), "entity": ("name",), "dimension": ("name", "ref"),
        "period": ("period", "time"), "range": ("time",), "number": ("scalar", "positive", "int")}
ENUM_FITS = {"comparison_op": "gt", "direction": "desc"}
LITERALS = {"scalar": plan.Scalar, "period": plan.Period, "time": plan.Time, "binding": plan.DimensionBinding,
            "operand": plan.Operand}
ADAPTERS = {t: TypeAdapter(a) for t, a in LITERALS.items()}
NAME_KINDS = {"measure": "measure", "context": "context", "entity": "entity", "relation": "relation", "calendar": "calendar",
              "band": "band", "declaration": "declaration", "column": "column", "name": "column", "path": "path"}
FLAT = {"bool": lambda v: isinstance(v, bool), "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "positive": lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0}
FLAT_PLACEHOLDERS = {"bool": False, "int": 1, "positive": 1}
MODEL_NAMES = frozenset(n for n, o in vars(plan).items() if isinstance(o, type) and issubclass(o, BaseModel))


def fits(slot_kind: str, param: Param) -> bool:
    """Whether a slot of this kind may fill this parameter: measures, contexts and dimensions fill a declared name or a
    node reference, an entity a name, a period a period or a time, a range a time, a number a scalar or a count; a
    comparison operator an enum that offers gt, a direction one that offers desc."""
    if slot_kind in ENUM_FITS:
        return param.type == "enum" and ENUM_FITS[slot_kind] in param.values
    return param.type in FITS.get(slot_kind, ())


def _items(recipe) -> list:
    blocks_ = recipe.get("blocks") if isinstance(recipe, dict) else None
    return blocks_ if isinstance(blocks_, list) else []


def _known(recipe) -> list[tuple[int, dict, blocks.Block]]:
    """(index, item, block) for each block of the recipe the catalogue lists."""
    return [(b, item, CATALOGUE[item["block"]]) for b, item in enumerate(_items(recipe))
            if isinstance(item, dict) and isinstance(item.get("block"), str) and item["block"] in CATALOGUE]


def catalogued(recipe) -> bool:
    """Whether every block the recipe lists is one the catalogue lists: where one is not, S34 refuses it and its slot
    references are not read, so a slot only that block would use is not reported unused as well."""
    return len(_known(recipe)) == len(_items(recipe))


def _values(param: Param, value, at: str) -> list[tuple[str, object]]:
    """(location, value) for each value a parameter gives: one per member of a many parameter's list."""
    if param.many and isinstance(value, list):
        return [(f"{at}[{k}]", v) for k, v in enumerate(value)]
    return [(at, value)]


def _slot(value):
    return SLOT_REF.fullmatch(value) if isinstance(value, str) else None


def slot_refs(recipe: dict) -> list[tuple[str, str, int | None, str, Param]]:
    """(location, slot name, index or None, block name, parameter) for every slot reference in the recipe."""
    out = []
    for b, item, block in _known(recipe):
        for p, param in block.params.items():
            if p in item:
                for at, v in _values(param, item[p], f"blocks[{b}].{p}"):
                    if m := _slot(v):
                        out.append((at, m[1], int(m[2]) if m[2] is not None else None, block.name, param))
    return out


def _nested(recipe) -> set[str]:
    """The ids of the predicate blocks another block names in a predicate parameter: they nest there, never in filters."""
    out = set()
    for _, item, block in _known(recipe):
        for p, param in block.params.items():
            if param.type == "predicate" and p in item:
                out |= {v for _, v in _values(param, item[p], "") if isinstance(v, str) and not _slot(v)}
    return out


def contributions(recipe: dict) -> set[tuple[str, str]]:
    """(section, block) for every top-level block: the plan nodes a recipe contributes, a nested predicate not counted."""
    nested = _nested(recipe)
    return {(block.section, block.name) for _, item, block in _known(recipe)
            if not (block.section == "filters" and item.get("id") in nested)}


def _plain(annotation, value):
    """A recipe's literal in the plan's own form: a name written as text becomes {id, evidence: []}."""
    annotation = blocks.bare(annotation)
    if annotation is plan.Name:
        return {"id": value, "evidence": []} if isinstance(value, str) else value
    if get_origin(annotation) is tuple:
        return [_plain(get_args(annotation)[0], v) for v in value] if isinstance(value, list) else value
    members = blocks.members(annotation)
    if members:
        if isinstance(value, dict):
            model = next((m for m in members if isinstance(m, type) and issubclass(m, BaseModel) and "kind" in m.model_fields
                          and value.get("kind") in get_args(m.model_fields["kind"].annotation)), None)
            return _plain(model, value) if model else value
        return _plain(plan.Name, value) if plan.Name in members else value
    if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
        fields = annotation.model_fields
        return {k: _plain(fields[k].annotation, v) if k in fields else v for k, v in value.items()}
    return value


def _literal(param: Param, value, at: str, ids: dict[str, int], kinds: dict[str, blocks.Block]) -> list[tuple[str, str]]:
    """Problems with one value a parameter gives."""
    if _slot(value):
        return []
    wrong = [(at, f"is a {param.type}, not {value!r}")]
    unknown = [(at, f"{value} is not a block of this recipe or a slot")]
    t = param.type
    if t == "ref":
        if not isinstance(value, str) or not value:
            return wrong
        return [] if value.split("/", 1)[0] in ids else unknown
    if t == "predicate":
        if not isinstance(value, str):
            return wrong
        if value not in ids:
            return unknown
        return [] if kinds.get(value) and kinds[value].section == "filters" else wrong
    if t == "operand" and isinstance(value, str):
        return [] if value.split("/", 1)[0] in ids else unknown
    if t == "name":
        return [] if isinstance(value, str) and value else wrong
    if t == "enum":
        return [] if value in param.values else [(at, f"is one of {', '.join(param.values)}, not {value!r}")]
    if t in FLAT:
        return [] if FLAT[t](value) else wrong
    try:
        ADAPTERS[t].validate_python(_plain(LITERALS[t], value))
    except ValidationError:
        return wrong
    return []


def _references(param: Param, value) -> list[str]:
    """The block ids a parameter's value names (a comparison output by its comparison's id)."""
    if param.type not in ("ref", "predicate", "operand"):
        return []
    return [v.split("/", 1)[0] for _, v in _values(param, value, "") if isinstance(v, str) and v and not _slot(v)]


def _cycles(recipe, ids: dict[str, int]) -> list[tuple[str, str]]:
    edges: dict[str, list[str]] = {}
    for _, item, block in _known(recipe):
        if isinstance(item.get("id"), str):
            edges.setdefault(item["id"], []).extend(
                r for p, param in block.params.items() if p in item for r in _references(param, item[p]) if r in ids)
    found, reported = [], set()

    def visit(node: str, path: list[str]):
        for nxt in edges.get(node, ()):
            if nxt in path:
                cycle = path[path.index(nxt):] + [nxt]
                if frozenset(cycle) not in reported:
                    reported.add(frozenset(cycle))
                    found.append((f"blocks[{ids[cycle[0]]}]", f"its references form a cycle: {' -> '.join(cycle)}"))
            elif nxt not in done:
                visit(nxt, path + [nxt])
        done.add(node)
    done: set[str] = set()
    for start in edges:
        if start not in done:
            visit(start, [start])
    return found


def recipe_problems(recipe: dict, kinds: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """S34's schema checks on one recipe, as (location, message): blocks the catalogue lists, with unique, unreserved
    ids; parameters each block declares, the required ones given, each of its type; references to the recipe's blocks
    or to slots, forming no cycle; and, when all of that holds, an expansion QueryPlan accepts. kinds maps a slot's name
    to its kind where a shape gives it (see expand)."""
    items = _items(recipe)
    if not items:
        return [("blocks", "is a list of one or more blocks")]
    problems: list[tuple[str, str]] = []
    ids: dict[str, int] = {}
    known: dict[str, blocks.Block] = {}
    for b, item in enumerate(items):
        at = f"blocks[{b}]"
        if not isinstance(item, dict) or not isinstance(item.get("block"), str) or not isinstance(item.get("id"), str) \
                or not item["id"]:
            problems.append((at, "is a mapping with a block and an id"))
            continue
        name, bid = item["block"], item["id"]
        if name not in CATALOGUE:
            problems.append((f"{at}.block", f"{name} is not a building block the platform's catalogue lists "
                                            "(kge/semantics/blocks.py)"))
        if bid in ids:
            problems.append((f"{at}.id", f"{bid} is blocks[{ids[bid]}]'s id already"))
            continue
        if bid in RESERVED or "/" in bid:
            problems.append((f"{at}.id", f"{bid} is reserved: a block id is not time or limit and holds no /"))
        ids[bid] = b
        if name in CATALOGUE:
            known[bid] = CATALOGUE[name]
    given: dict[str, int] = {}
    for b, item, block in _known(recipe):
        at = f"blocks[{b}]"
        if block.section in SINGULAR:
            if block.section in given:
                problems.append((at, f"gives the plan's {block.section}, which blocks[{given[block.section]}] gives "
                                     "already: a plan has one"))
            given.setdefault(block.section, b)
        for p, value in item.items():
            if p in ("block", "id"):
                continue
            param = block.params.get(p)
            if param is None:
                problems.append((f"{at}.{p}", f"is not a parameter of {block.name}: its parameters are "
                                              f"{', '.join(block.params) or 'none'}"))
            elif param.many and not isinstance(value, list) and not _slot(value):
                problems.append((f"{at}.{p}", f"is a list of {param.type}, not {value!r}"))
            else:
                for where, v in _values(param, value, f"{at}.{p}"):
                    problems += _literal(param, v, where, ids, known)
        problems += [(f"{at}.{p}", f"is required by {block.name}") for p, param in block.params.items()
                     if param.required and p not in item]
    problems += _cycles(recipe, ids)
    if problems:
        return problems
    expansion = plan_problems(expand(recipe, kinds))
    if expansion:
        loc, msg = expansion[0]
        return [("", f"does not expand into a valid {SCHEMA}: {loc + ': ' if loc else ''}{msg}")]
    return []


def _name(i: str = PLACEHOLDER) -> dict:
    return {"id": i, "evidence": []}


def _period(value: str = "2000-01") -> dict:
    return {"kind": "month", "value": value, "calendar": _name()}


def expand(recipe: dict, kinds: dict[str, str] | None = None) -> dict:
    """The recipe as a query-plan/v1 document, each slot reference a typed placeholder.

    Each block becomes a node, {kind: <block>} (but order and limit) with its id (but a time block, order and limit),
    its parameters resolved, and evidence: [] where its model has evidence. A predicate block another block names in a
    predicate parameter nests there and is not repeated in filters. A slot reference becomes a placeholder of its
    parameter's type: a name {id: slot:<name>:<i>}; a node reference that id, with a placeholder node of it (a
    dimension for a dimension slot or a dimension parameter, a filter for remove_filters, else a measure); a scalar the
    integer 1; a period month 2000-01; a time a range 2000-01 to 2000-02 for a range slot, else that single month; an
    enum gt or desc where it offers them, else its first value; a count 1; a flag false. A slot's kind is kinds[name]
    where given, else its name. A plan with no measure gets a placeholder one, with no time all_available."""
    kinds = dict(kinds or {})
    items = {item["id"]: item for _, item, _ in _known(recipe) if isinstance(item.get("id"), str)}
    nested = _nested(recipe)
    doc: dict = {"schema": SCHEMA, "layout": "aligned", "measures": [], "dimensions": [], "filters": [], "time": None,
                 "comparisons": [], "derived": [], "order": [], "limit": None, "obligations": []}
    extra: dict[str, dict[str, dict]] = {"measures": {}, "dimensions": {}, "filters": {}}

    def filter_node(i: str) -> dict:
        return {"kind": "null", "id": i, "left": {"kind": "column", "name": _name(), "path": []}, "is_null": True,
                "evidence": []}

    def measure_node(i: str) -> dict:
        return {"kind": "measure", "id": i, "measure": _name(), "context": _name(), "mode": "current", "filters": []}

    def placeholder(param: Param, slot: str, index: int):
        sid = f"slot:{slot}:{index}"
        kind = kinds.get(slot, slot)
        t = param.type
        if t == "name":
            return _name(sid)
        if t in ("ref", "operand"):
            if t == "ref" and (kind == "dimension" or kind not in ("measure", "context") and param.name in DIMENSION_REFS):
                extra["dimensions"].setdefault(sid, {"kind": "column", "id": sid, "bindings": [
                    {"context": _name(), "column": _name(), "path": [], "level": None}]})
            elif t == "ref" and kind not in ("measure", "context") and param.name == "remove_filters":
                extra["filters"].setdefault(sid, filter_node(sid))
            else:
                extra["measures"].setdefault(sid, measure_node(sid))
            return sid if t == "ref" else {"kind": "output", "id": sid}
        if t == "predicate":
            return filter_node(sid)
        if t == "scalar":
            return {"kind": "integer", "value": 1}
        if t == "period":
            return _period()
        if t == "time":
            if kind == "range":
                return {"kind": "range", "first": _period(), "last": _period("2000-02")}
            return {"kind": "single", "period": _period()}
        if t == "binding":
            return {"context": _name(), "column": _name(), "path": [], "level": None}
        if t == "enum":
            return next((v for v in ("gt", "desc") if v in param.values), param.values[0])
        return FLAT_PLACEHOLDERS[t]

    def one(param: Param, value, seen: frozenset):
        if m := _slot(value):
            return placeholder(param, m[1], int(m[2] or 0))
        t = param.type
        if t == "name":
            return _name(value) if isinstance(value, str) else value
        if t == "predicate":
            return node(items[value], seen) if isinstance(value, str) and value in items and value not in seen else value
        if t == "operand" and isinstance(value, str):
            return {"kind": "output", "id": value}
        if t in LITERALS:
            return _plain(LITERALS[t], value)
        return value

    def node(item: dict, seen: frozenset = frozenset()):
        block = CATALOGUE[item["block"]]
        seen = seen | {item.get("id")}
        if block.section == "limit":
            return one(block.params["value"], item.get("value"), seen)
        out = {} if block.section == "order" else {"kind": block.name}
        if block.section not in UNNAMED:
            out["id"] = item.get("id")
        for p, param in block.params.items():
            if p in item:
                value = item[p]
                if param.many:
                    out[p] = [one(param, v, seen) for v in value] if isinstance(value, list) else [one(param, value, seen)]
                else:
                    out[p] = one(param, value, seen)
        if "evidence" in getattr(block.model, "model_fields", {}):
            out["evidence"] = []
        return out
    for i, item in items.items():
        block = CATALOGUE[item["block"]]
        if block.section == "filters" and i in nested:
            continue
        built = node(item)
        if block.section in SINGULAR:
            doc[block.section] = built
        else:
            doc[block.section].append(built)
    for section, nodes in extra.items():
        doc[section] = [*nodes.values(), *doc[section]]
    if not doc["measures"]:
        doc["measures"].append(measure_node(PLACEHOLDER))
    if doc["time"] is None:
        doc["time"] = {"kind": "all_available", "calendar": _name()}
    return doc


def _where(doc, loc: tuple) -> str:
    """A pydantic error location as a path into the document: [i] for an index, and a union's tag (the node's kind, or a
    model's name) dropped where it comes, once at each place, since the document holds no such key."""
    out, here, tagged = "", doc, False
    for step in loc:
        if isinstance(step, int):
            out += f"[{step}]"
            here = here[step] if isinstance(here, (list, tuple)) and -len(here) <= step < len(here) else None
            tagged = False
        elif not tagged and here is not None and (step in MODEL_NAMES or not isinstance(here, dict)
                                                  or here.get("kind") == step):
            tagged = True
        elif isinstance(here, dict) and step in here:
            out += f"{'.' if out else ''}{step}"
            here, tagged = here[step], False
        else:
            out += f"{'.' if out else ''}{step}"
            here, tagged = None, False
    return out


def plan_problems(doc) -> list[tuple[str, str]]:
    """Each reason QueryPlan refuses doc, as (location, message); [] when it is a valid query-plan/v1."""
    try:
        plan.QueryPlan.model_validate(doc)
    except ValidationError as e:
        return [(_where(doc, tuple(err["loc"])), err["msg"]) for err in e.errors()]
    return []


def plan_names(doc) -> list[tuple[str, str, str]]:
    """For a valid plan, (location, kind, id) for every name in it, the kinds: measure, context, entity, relation,
    calendar, band, declaration, column (a binding's column and a column operand's name) and path (each join on a
    path). Raises ValidationError on a plan QueryPlan refuses: call plan_problems first."""
    out: list[tuple[str, str, str]] = []

    def walk(value, at: str, field: str):
        if isinstance(value, plan.Name):
            out.append((at, NAME_KINDS[field], value.id))
        elif isinstance(value, BaseModel):
            for f in type(value).model_fields:
                walk(getattr(value, f), f"{at}.{f}" if at else f, f)
        elif isinstance(value, tuple):
            for k, v in enumerate(value):
                walk(v, f"{at}[{k}]", field)
    walk(plan.QueryPlan.model_validate(doc), "", "")
    return out
