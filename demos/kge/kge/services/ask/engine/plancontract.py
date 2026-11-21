"""The planner's decisions-only contract (#561), and the code that fills in the rest.

AI-Generated mode asks the model for the decisions a plan needs and nothing the code can work out itself: which declared
ids the question means, the plan's shape over them, how it read each word, what it could not read, and, in a
conversation, whether it continues the earlier plan and which nodes the follow-up removes. The contract mirrors
kge/semantics/plan.py: every name is a bare id from the vocabulary shown (VocabId), every evidence field is gone, a
breakdown's or a filter's route may be left out where one route is declared, and a plan carries no schema or
obligations. fill() turns a contract into the PlanReply the planner checks: each id with its evidence spans from the
rules' ledger, each node's evidence from the model's readings, the obligations, the turn ids and the base turn. A full
PlanReply, or the older {reply, readings} envelope, is still accepted as it is (PlanContract.legacy), so recordings and
the stand-in models that reply in the full shape replay unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, Union

from pydantic import Field, ValidationError, model_validator

from kge.semantics import plan as declared_plan
from kge.semantics.coverage import ledger
from kge.semantics.plan import Grain, Id, Mode, Positive, QueryPlan, Record, Scalar
from kge.semantics.planhistory import nodes as plan_nodes, retained
from kge.semantics.planvocabulary import value_id
from kge.services.ask.engine.modelcall import FILLER, Contract, FieldError, ReadingId, VocabId

PLAN_SCHEMA = 'query-plan/v1'
ANY_KIND = ()
FIELD_KINDS = {'measure': ('measure',), 'context': ('context',), 'column': ('column',), 'name': ('column',),
               'calendar': ('calendar',), 'entity': ('entity',), 'relation': ('path',), 'band': ('band',),
               'path': ('path',), 'declaration': ('count_metric', 'derived_measure'), 'candidates': ANY_KIND}
"""Each contract field that names vocabulary ids, and the kinds of vocabulary entry it may name (ANY_KIND: any)."""
NAME_FIELDS = frozenset(FIELD_KINDS) - {'candidates'}
"""The plan's fields that hold a name: an id in the contract, {id, evidence} in the plan."""
PREDICATES = frozenset(('compare', 'in', 'null', 'all', 'any', 'not'))
DIMENSIONS = frozenset(('column', 'hierarchy', 'band', 'time'))
COMPARISONS = frozenset(('time_mode', 'previous_period', 'previous_year', 'explicit_period', 'contexts'))
DERIVED = frozenset(('ratio', 'percent_change', 'difference', 'share', 'running_total', 'moving_average'))
RANKING = frozenset(('asc', 'desc'))
MODES = frozenset(('current', 'as_reported'))


class Period(Record):
    kind: Grain
    value: str
    calendar: VocabId


class Single(Record):
    kind: Literal['single']
    period: Period


class Range(Record):
    kind: Literal['range']
    first: Period
    last: Period
    anchor: Literal['stated', 'current'] = Field('stated', description=declared_plan.ANCHOR_DESCRIPTION)
    to_date: Literal['month', 'quarter'] | None = Field(None, description='the to-date grain of a current-anchored range')


class Ytd(Record):
    kind: Literal['ytd']
    through: Period
    calendar: VocabId
    anchor: Literal['stated', 'current'] = Field('stated', description=declared_plan.ANCHOR_DESCRIPTION)


class Unbounded(Record):
    kind: Literal['latest_released', 'all_available']
    calendar: VocabId


Time = Annotated[Union[Single, Range, Ytd, Unbounded], Field(discriminator='kind')]


class DimensionBinding(Record):
    context: VocabId
    column: VocabId
    path: tuple[VocabId, ...] | None = Field(None, description='the joins from the context to the column; leave it out '
                                                                'where one route is declared')
    level: Positive | None = None


class BoundDimension(Record):
    kind: Literal['column', 'hierarchy']
    id: Id
    bindings: Annotated[tuple[DimensionBinding, ...], Field(min_length=1)]


class TimeDimension(Record):
    kind: Literal['time']
    id: Id
    calendar: VocabId
    grain: Grain


class BandDimension(Record):
    kind: Literal['band']
    id: Id
    band: VocabId
    bindings: Annotated[tuple[DimensionBinding, ...], Field(min_length=1)]


Dimension = Annotated[Union[BoundDimension, TimeDimension, BandDimension], Field(discriminator='kind')]


class ColumnOperand(Record):
    kind: Literal['column']
    name: VocabId
    path: tuple[VocabId, ...] | None = Field(None, description='the joins from the context to the column; leave it out '
                                                               'where one route is declared')


class OutputOperand(Record):
    kind: Literal['output']
    id: Id


Operand = Annotated[Union[ColumnOperand, OutputOperand], Field(discriminator='kind')]


class Compare(Record):
    kind: Literal['compare']
    id: Id
    left: Operand
    op: Literal['eq', 'ne', 'lt', 'le', 'gt', 'ge', 'contains', 'starts_with']
    value: Scalar
    magnitude: bool = Field(False, description='compares the absolute amount: a size threshold such as "transactions of $250K or more"')


class In(Record):
    kind: Literal['in']
    id: Id
    left: Operand
    values: Annotated[tuple[Scalar, ...], Field(min_length=1)]


class IsNull(Record):
    kind: Literal['null']
    id: Id
    left: Operand
    is_null: bool


class Junction(Record):
    kind: Literal['all', 'any']
    id: Id
    children: Annotated[tuple['Predicate', ...], Field(min_length=1)]


class Not(Record):
    kind: Literal['not']
    id: Id
    child: 'Predicate'


class Membership(Record):
    kind: Literal['membership']
    id: Id
    entity: VocabId
    relation: VocabId
    context: VocabId
    filters: tuple['Predicate', ...] = ()


Predicate = Annotated[Union[Compare, In, IsNull, Junction, Not, Membership], Field(discriminator='kind')]
for _model in (Junction, Not, Membership):
    _model.model_rebuild()


class PlanMeasure(Record):
    kind: Literal['measure']
    id: Id
    measure: VocabId
    context: VocabId
    mode: Mode = 'current'
    filters: tuple[Predicate, ...] = ()
    aggregation: Literal['sum', 'count', 'avg', 'min', 'max', 'median'] | None = None


class DistinctCount(Record):
    kind: Literal['distinct_count']
    id: Id
    entity: VocabId
    context: VocabId
    mode: Mode = 'current'
    filters: tuple[Predicate, ...] = ()
    declaration: VocabId | None = None


MeasureOutput = Annotated[Union[PlanMeasure, DistinctCount], Field(discriminator='kind')]


class TimeModeComparison(Record):
    kind: Literal['time_mode']
    id: Id
    inputs: Annotated[tuple[Id, ...], Field(min_length=1)]
    base: Mode
    other: Mode


class RelativeComparison(Record):
    kind: Literal['previous_period', 'previous_year']
    id: Id
    inputs: Annotated[tuple[Id, ...], Field(min_length=1)]


class ExplicitComparison(Record):
    kind: Literal['explicit_period']
    id: Id
    inputs: Annotated[tuple[Id, ...], Field(min_length=1)]
    other: Time


class ContextComparison(Record):
    kind: Literal['contexts']
    id: Id
    left: Id
    right: Id


Comparison = Annotated[Union[TimeModeComparison, RelativeComparison, ExplicitComparison, ContextComparison],
                       Field(discriminator='kind')]


class Arithmetic(Record):
    kind: Literal['ratio', 'percent_change', 'difference']
    id: Id
    numerator: Id
    denominator: Id
    declaration: VocabId | None = None


class Share(Record):
    kind: Literal['share']
    id: Id
    input: Id
    remove_dimensions: tuple[Id, ...]
    remove_filters: tuple[Id, ...]
    declaration: VocabId | None = None


class RunningTotal(Record):
    kind: Literal['running_total']
    id: Id
    input: Id
    partition_by: tuple[Id, ...]
    order_by: Id


class MovingAverage(Record):
    kind: Literal['moving_average']
    id: Id
    input: Id
    partition_by: tuple[Id, ...]
    order_by: Id
    length: Positive


Derived = Annotated[Union[Arithmetic, Share, RunningTotal, MovingAverage], Field(discriminator='kind')]


class Order(Record):
    output: Id
    direction: Literal['asc', 'desc', 'both']
    nulls: Literal['last'] = 'last'
    partition_by: tuple[Id, ...] = ()


class ContractPlan(Record):
    layout: Literal['aligned', 'separate'] = 'aligned'
    measures: Annotated[tuple[MeasureOutput, ...], Field(min_length=1)]
    dimensions: tuple[Dimension, ...] = ()
    filters: tuple[Predicate, ...] = ()
    time: Time
    comparisons: tuple[Comparison, ...] = ()
    derived: tuple[Derived, ...] = ()
    order: tuple[Order, ...] = ()
    limit: Positive | None = None


class ContractReading(Record):
    phrase: Annotated[str, Field(min_length=1)]
    id: ReadingId


class ContractRemoval(Record):
    node: Id
    phrase: Annotated[str, Field(min_length=1)]


class ContractClarify(Record):
    phrase: str
    candidates: Annotated[tuple[VocabId, ...], Field(min_length=2)]


class PlanContract(Contract):
    """What the model decides about one question: propose a plan, clarify between declared alternatives, or abstain;
    whether a follow-up continues the earlier plan; the words it could not read (unread) or that name nothing it can
    plan (missing); each word's reading; and, in a follow-up, the nodes it removes with the words that remove them."""

    decision: Literal['propose', 'clarify', 'abstain']
    continues: bool = False
    plan: ContractPlan | None = None
    clarify: ContractClarify | None = None
    missing: tuple[str, ...] = ()
    unread: tuple[str, ...] = ()
    readings: tuple[ContractReading, ...] = ()
    removed: tuple[ContractRemoval, ...] = ()

    @model_validator(mode='after')
    def decided(self):
        if self.decision == 'propose' and self.plan is None:
            raise ValueError('a proposal needs its plan')
        if self.decision == 'clarify' and self.clarify is None:
            raise ValueError('a clarification needs clarify')
        return self

    @classmethod
    def legacy(cls, raw: Any) -> bool:
        """A full PlanReply (any reply with a kind) or the older {reply, readings} envelope."""
        return isinstance(raw, dict) and ('reply' in raw or 'kind' in raw)


MIRRORS = {model: getattr(declared_plan, model.__name__) for model in (
    Period, Single, Range, Ytd, Unbounded, DimensionBinding, BoundDimension, TimeDimension, BandDimension, ColumnOperand,
    OutputOperand, Compare, In, IsNull, Junction, Not, Membership, PlanMeasure, DistinctCount, TimeModeComparison,
    RelativeComparison, ExplicitComparison, ContextComparison, Arithmetic, Share, RunningTotal, MovingAverage, Order)}
MIRRORS[ContractPlan] = QueryPlan
"""Each contract record and its plan.py counterpart, the class of the same name."""


def _kinds_allow(kinds, entry):
    return entry is not None and (kinds == ANY_KIND or entry.kind in kinds)


def id_choices(key: str, ids, vocabulary) -> tuple[str, ...]:
    """Every id shown that a contract field may name, by the kinds FIELD_KINDS gives it."""
    kinds = FIELD_KINDS.get(key)
    if kinds is None:
        return ()
    return tuple(i for i in sorted(ids) if _kinds_allow(kinds, vocabulary.entries.get(i)))


def id_error(pointer: str, problem: str, key: str, ids, vocabulary, near=None) -> FieldError:
    """A field error at an id field, with the ids of its kinds as its choices, those nearest near when there are more
    than MAX_ALLOWED, and how many there are."""
    return FieldError.choosing(pointer, problem, id_choices(key, ids, vocabulary), near)


def at_pointer(data, pointer: str):
    """The value at a JSON pointer in parsed data, or None."""
    node = data
    for part in (p for p in pointer.split('/') if p):
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def field_key(pointer: str) -> str:
    """The last named part of a JSON pointer: /plan/dimensions/0/bindings/0/path/1 is path."""
    return next((p for p in reversed(pointer.split('/')) if p and not p.isdigit()), '')


def bind_ids(raw, vocabulary):
    """A contract whose name field holds words rather than a declared id, bound to the one declared id of a kind the
    field may name that those words are a phrase for, the lookup the rules use; words that bind to none, or to more than
    one, are left for the contract's check. A legacy reply is left as it is: bound_names binds it."""
    if PlanContract.legacy(raw):
        return raw

    def bound(key, value):
        if not isinstance(value, str) or value in vocabulary.entries:
            return value
        found = {e.id for e in vocabulary.candidates(value) if _kinds_allow(FIELD_KINDS[key], e)}
        return found.pop() if len(found) == 1 else value

    def walk(node):
        if isinstance(node, list):
            return [walk(child) for child in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, child in node.items():
            if key in FIELD_KINDS and isinstance(child, str):
                out[key] = bound(key, child)
            elif key in FIELD_KINDS and isinstance(child, list) and all(isinstance(c, str) for c in child):
                out[key] = [bound(key, c) for c in child]
            else:
                out[key] = walk(child)
        return out
    return walk(raw)


def contract_nodes(plan: ContractPlan):
    """The contract plan's nodes with ids, as QueryPlan.nodes() gives the plan's: measures, dimensions, comparisons,
    derived outputs and every predicate, nested ones included."""
    def predicates(pred):
        yield pred
        for child in getattr(pred, 'children', ()):
            yield from predicates(child)
        if pred.kind == 'not':
            yield from predicates(pred.child)
        for child in getattr(pred, 'filters', ()):
            yield from predicates(child)
    yield from plan.measures
    yield from plan.dimensions
    yield from plan.comparisons
    yield from plan.derived
    for pred in (*plan.filters, *(p for m in plan.measures for p in m.filters)):
        yield from predicates(pred)


def _phrase_span(question, phrase, turn):
    text = (phrase or '').strip()
    if not text:
        return None
    found = re.search(r'(?<!\w)' + re.escape(text) + r'(?!\w)', question, re.I)
    return {'turn': turn, 'start': found.start(), 'end': found.end()} if found else None


def _obligation_kind(node_id, node):
    if node_id == 'time':
        return 'time'
    if node_id == 'limit':
        return 'limit'
    if node_id.endswith('/mode'):
        return 'mode'
    kind = getattr(node, 'kind', '')
    if kind == 'measure':
        return 'name'
    if kind == 'distinct_count':
        return 'count'
    if kind == 'membership':
        return 'membership'
    if kind in PREDICATES:
        return 'filter'
    if kind in DIMENSIONS:
        return 'dimension'
    if kind in COMPARISONS:
        return 'comparison'
    if kind in DERIVED:
        return 'derived'
    return 'name'


def fill(contract: PlanContract, question: str, vocabulary, turn: str, history=()) -> dict:
    """The PlanReply a contract stands for, as a dict for reply_adapter: each id with the spans of the rules' ledger
    pieces that can mean it; each node's evidence from the readings of the ids it holds; obligations o1, o2, ... for the
    ledger's names and times the plan uses, the readings no name piece covers, the unread phrases (unresolved), every
    node a follow-up adds or changes, and every node it removes; the turn ids; and the base turn of a follow-up."""
    pieces = ledger(question, turn, vocabulary)

    def span_of(phrase):
        return _phrase_span(question, phrase, turn)

    def spans_for(identifier):
        found = []
        for piece in pieces:
            span = piece.span.model_dump()
            if identifier in piece.candidates and span not in found:
                found.append(span)
        return found

    def name(identifier):
        return {'id': identifier, 'evidence': spans_for(identifier)}

    def dedupe(spans):
        out = []
        for span in spans:
            if span is not None and span not in out:
                out.append(span)
        return out

    def reading_spans(identifiers):
        return dedupe(span_of(r.phrase) for r in contract.readings if r.id != FILLER and r.id in identifiers)

    if contract.decision == 'clarify':
        span = span_of(contract.clarify.phrase)
        return {'kind': 'clarify', 'spans': [span] if span else [],
                'candidates': [{'id': c, 'evidence': []} for c in contract.clarify.candidates]}
    unread = dedupe(span_of(w) for w in contract.unread)
    if contract.decision == 'abstain':
        missing = (list(contract.missing) or [f"'{w}' matches no declared meaning" for w in contract.unread]
                   or ['The model could not plan the question.'])
        return {'kind': 'abstain', 'spans': unread, 'missing': missing}

    def ids_in(value):
        """Every vocabulary id a contract record holds, nested records included, and the declared member a column's
        compared value is."""
        if isinstance(value, tuple):
            for item in value:
                yield from ids_in(item)
            return
        if not isinstance(value, Record) or type(value) not in MIRRORS:
            return
        for field in type(value).model_fields:
            child = getattr(value, field)
            if field in NAME_FIELDS and child is not None:
                yield from (child if isinstance(child, tuple) else (child,))
            else:
                yield from ids_in(child)
        left = getattr(value, 'left', None)
        if getattr(left, 'kind', None) == 'column' and left.name in vocabulary.entries:
            column = vocabulary.entries[left.name].column
            for scalar in (getattr(value, 'value', None), *getattr(value, 'values', ())):
                if scalar is not None and hasattr(scalar, 'value'):
                    yield value_id(column, scalar.value)

    def converted(value):
        if isinstance(value, tuple):
            return [converted(v) for v in value]
        if isinstance(value, Record) and type(value) in MIRRORS:
            out = {}
            for field in type(value).model_fields:
                child = getattr(value, field)
                if field in NAME_FIELDS:
                    if child is None and field == 'path':
                        continue
                    out[field] = (None if child is None else [name(c) for c in child] if isinstance(child, tuple)
                                  else name(child))
                else:
                    out[field] = converted(child)
            if 'evidence' in MIRRORS[type(value)].model_fields:
                out['evidence'] = reading_spans(set(ids_in(value)))
            return out
        if isinstance(value, Record):
            return value.model_dump(mode='json', by_alias=True)
        return value

    plan = contract.plan
    body = converted(plan)
    found = {getattr(n, 'id'): n for n in contract_nodes(plan)}
    held = {i: set(ids_in(n)) for i, n in found.items()}
    calendars = set(ids_in(plan.time))
    obligations = []

    def holding(identifiers):
        targets = [i for i, names in held.items() if names & identifiers]
        return targets + (['time'] if calendars & identifiers else [])

    def covered(span):
        return any(s['start'] <= span['start'] and span['end'] <= s['end'] for o in obligations for s in o['spans'])

    def oblige(spans, kind, targets, state='applied'):
        obligations.append({'id': 'o' + str(len(obligations) + 1), 'spans': spans, 'kind': kind,
                            'targets': list(dict.fromkeys(targets)), 'state': state})

    for piece in pieces:
        if piece.kind == 'name' and piece.candidates and (targets := holding(set(piece.candidates))):
            oblige([piece.span.model_dump()], 'name', targets)
    for piece in pieces:
        if piece.kind == 'time':
            oblige([piece.span.model_dump()], 'time', ['time'])
    for reading in contract.readings:
        span = span_of(reading.phrase)
        if reading.id != FILLER and span is not None and not covered(span) and (targets := holding({reading.id})):
            oblige([span], 'name', targets)
    for span in unread:
        oblige([span], 'name', [], 'unresolved')
    base = _base(contract, history)
    body['schema'] = PLAN_SCHEMA
    body['obligations'] = obligations
    if base is not None and base.plan is not None:
        _follow_up(body, base.plan, pieces, held, calendars, reading_spans, spans_for, oblige, obligations)
        before = plan_nodes(base.plan)
        for removal in contract.removed:
            if (span := span_of(removal.phrase)) is not None:
                oblige([span], _obligation_kind(removal.node, before.get(removal.node)), [removal.node], 'removed')
    return {'kind': 'propose', 'base_turn': base.turn_id if base is not None else None, 'plan': body}


def _base(contract, history):
    """The history entry a follow-up continues: the last, where it holds a clarification, else the newest with a plan;
    None for a new question."""
    if not contract.continues or not history:
        return None
    if history[-1].clarification is not None:
        return history[-1]
    return next((h for h in reversed(history) if h.plan is not None), None)


def _follow_up(body, base_plan, pieces, held, calendars, reading_spans, spans_for, oblige, obligations):
    """An applied obligation for each node a follow-up adds or changes against the plan it continues and no obligation
    targets yet, citing the words that read as the ids it holds, else the ledger's pieces for them, else the operator
    words of its kind; a node no words support is left for the conversation's check to refuse."""
    try:
        after = plan_nodes(QueryPlan.model_validate(body))
    except (ValidationError, ValueError, TypeError):
        return
    before = plan_nodes(base_plan)
    targeted = {t for o in obligations if o['state'] == 'applied' for t in o['targets']}
    for identifier, node in after.items():
        if identifier in targeted or retained(before.get(identifier), node):
            continue
        names = calendars if identifier == 'time' else held.get(identifier, set())
        spans = reading_spans(names) or [s for i in sorted(names) for s in spans_for(i)]
        if not spans:
            spans = [p.span.model_dump() for p in pieces if _operator_for(identifier, node, p)]
        unique = []
        for span in spans:
            if span not in unique:
                unique.append(span)
        if unique:
            oblige(unique, _obligation_kind(identifier, node), [identifier])


def _operator_for(identifier, node, piece):
    """Whether a ledger piece is a word for a node with no names of its own: a time, a limit, a time mode, a
    comparison or a derived output."""
    if identifier == 'time':
        return piece.kind == 'time'
    if identifier == 'limit':
        return piece.operation in RANKING or piece.kind == 'literal'
    if identifier.endswith('/mode'):
        return piece.operation in MODES
    kind = getattr(node, 'kind', '')
    return piece.kind == 'operator' and (piece.operation == kind or kind in COMPARISONS and piece.operation == 'comparison')


def _pointers(value, path=''):
    """Every (pointer, value) in a parsed contract, depth first."""
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _pointers(child, path + '/' + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _pointers(child, path + '/' + str(index))


def _mentions(text, identifier):
    return re.search(r'(?<![\w.:-])' + re.escape(identifier) + r'(?![\w.:-])', text) is not None


def locate(problem: str, contract_text: str, vocabulary, ids) -> FieldError:
    """A check's problem as a field error on the contract: where the problem names an id, or a node id the contract
    holds, the field is the first contract field holding it, with the ids shown that field may name; otherwise the
    problem alone."""
    try:
        data = json.loads(contract_text) if contract_text else None
    except ValueError:
        data = None
    if data is None:
        return FieldError('', problem)
    pointers = list(_pointers(data))
    for identifier in sorted({i for i in ids if isinstance(i, str)}, key=len, reverse=True):
        if not _mentions(problem, identifier):
            continue
        for pointer, value in pointers:
            if value == identifier and pointer:
                return id_error(pointer, problem, field_key(pointer), ids, vocabulary, identifier)
    node_ids = [(pointer.rsplit('/', 1)[0], value) for pointer, value in pointers
                if pointer.endswith('/id') and isinstance(value, str) and '/readings/' not in pointer]
    for pointer, value in node_ids:
        if _mentions(problem, value):
            return FieldError(pointer, problem)
    return FieldError('', problem)


def contract_pointer(loc, data) -> str:
    """A validation error's location as a JSON pointer into the data it was raised on, the union tags pydantic adds to
    a location left out: a part equal to the kind of the record it is at, before the field inside it."""
    node, out, tagged = data, [], False
    for part in loc:
        if isinstance(node, dict) and not tagged and part == node.get('kind'):
            tagged = True
            continue
        out.append(str(part))
        if isinstance(node, dict) and part in node:
            node, tagged = node[part], False
        elif isinstance(node, list) and isinstance(part, int) and part < len(node):
            node, tagged = node[part], False
        else:
            node, tagged = None, False
    return '/' + '/'.join(out) if out else ''


def reply_pointer(loc, reply) -> str:
    """A PlanReply validation error's location as a pointer into the contract it was filled from: the reply's own kind
    left out, a name's id is the contract's id field, and base_turn is continues."""
    pointer = contract_pointer(loc, reply)
    parts = pointer.split('/')[1:] if pointer else []
    if parts and parts[-1] == 'id':
        node = reply
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) and part.isdigit() else node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict) and set(node) == {'id', 'evidence'}:
            parts = parts[:-1]
    if parts[:1] == ['base_turn']:
        parts = ['continues']
    return '/' + '/'.join(parts) if parts else ''


def lean(plan):
    """A plan in the contract's shape, as the examples and the conversation show it: each name its id, without
    evidence, obligations or the schema version."""
    if isinstance(plan, list):
        return [lean(v) for v in plan]
    if not isinstance(plan, dict):
        return plan
    if set(plan) == {'id', 'evidence'} and isinstance(plan['id'], str):
        return plan['id']
    return {k: lean(v) for k, v in plan.items() if k not in ('evidence', 'obligations', 'schema')}
