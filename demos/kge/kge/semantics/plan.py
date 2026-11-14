"""Versioned, SQL-free interpretation records shared by numeric planners."""
from __future__ import annotations

import datetime
import re
from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

Id = Annotated[str, Field(min_length=1)]
Positive = Annotated[int, Field(gt=0)]
Grain = Literal['day', 'month', 'calendar_quarter', 'fiscal_quarter', 'year', 'fiscal_year']
Mode = Literal['current', 'as_reported']


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, populate_by_name=True)

    @model_validator(mode='before')
    @classmethod
    def arrays(cls, value):
        if isinstance(value, dict):
            return {k: tuple(v) if isinstance(v, list) else v for k, v in value.items()}
        return value


class Span(Record):
    turn: Id
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]

    @model_validator(mode='after')
    def nonempty(self):
        if self.end <= self.start:
            raise ValueError('empty evidence span')
        return self


class Name(Record):
    id: Id
    evidence: tuple[Span, ...]


class Text(Record):
    kind: Literal['text']
    value: str


class Boolean(Record):
    kind: Literal['boolean']
    value: bool


class Integer(Record):
    kind: Literal['integer']
    value: int


class DecimalValue(Record):
    kind: Literal['decimal']
    value: Annotated[str, Field(pattern=r'-?(?:0|[1-9]\d*)(?:\.\d+)?$')]

    @field_validator('value')
    @classmethod
    def finite(cls, value):
        if not re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?', value) or not Decimal(value).is_finite():
            raise ValueError('a finite canonical decimal string is required')
        return value


class DateValue(Record):
    kind: Literal['date']
    value: str

    @field_validator('value')
    @classmethod
    def iso(cls, value):
        if datetime.date.fromisoformat(value).isoformat() != value:
            raise ValueError('ISO date required')
        return value


class Null(Record):
    kind: Literal['null']


Scalar = Annotated[Union[Text, Boolean, Integer, DecimalValue, DateValue, Null], Field(discriminator='kind')]


class Period(Record):
    kind: Grain
    value: str
    calendar: Name

    @model_validator(mode='after')
    def form(self):
        if self.kind == 'day':
            if datetime.date.fromisoformat(self.value).isoformat() != self.value:
                raise ValueError('ISO date required')
        elif self.kind == 'month':
            if not re.fullmatch(r'\d{4}-\d{2}', self.value):
                raise ValueError('year-month required')
            datetime.date.fromisoformat(self.value + '-01')
        elif self.kind in ('calendar_quarter', 'fiscal_quarter'):
            pattern = r'(?:\d{4}|FY\d{2,4})-Q[1-4]' if self.kind == 'fiscal_quarter' else r'\d{4}-Q[1-4]'
            if not re.fullmatch(pattern, self.value) or int(self.value.split('-')[0].removeprefix('FY')) < 1:
                raise ValueError('year-quarter required')
        elif not re.fullmatch(r'(?:\d{4}|FY\d{2,4})' if self.kind == 'fiscal_year' else r'\d{4}', self.value) or int(self.value.removeprefix('FY')) < 1:
            raise ValueError('year required')
        return self


class Single(Record):
    kind: Literal['single']
    period: Period


class Range(Record):
    kind: Literal['range']
    first: Period
    last: Period

    @model_validator(mode='after')
    def ordered(self):
        if (self.first.kind, self.first.calendar.id) != (self.last.kind, self.last.calendar.id) or self.first.value > self.last.value:
            raise ValueError('range endpoints must be ordered at one grain and calendar')
        return self


class Ytd(Record):
    kind: Literal['ytd']
    through: Period
    calendar: Name

    @model_validator(mode='after')
    def same_calendar(self):
        if self.through.calendar.id != self.calendar.id:
            raise ValueError('YTD calendar mismatch')
        return self


class Unbounded(Record):
    kind: Literal['latest_released', 'all_available']
    calendar: Name


Time = Annotated[Union[Single, Range, Ytd, Unbounded], Field(discriminator='kind')]


class DimensionBinding(Record):
    context: Name
    column: Name
    path: tuple[Name, ...]
    level: Positive | None


class BoundDimension(Record):
    kind: Literal['column', 'hierarchy']
    id: Id
    bindings: Annotated[tuple[DimensionBinding, ...], Field(min_length=1)]

    @model_validator(mode='after')
    def levels(self):
        if any((b.level is None) != (self.kind == 'column') for b in self.bindings):
            raise ValueError('hierarchies require a level; columns require null level')
        if len({b.context.id for b in self.bindings}) != len(self.bindings):
            raise ValueError('duplicate dimension binding')
        return self


class TimeDimension(Record):
    kind: Literal['time']
    id: Id
    calendar: Name
    grain: Grain


Dimension = Annotated[Union[BoundDimension, TimeDimension], Field(discriminator='kind')]


class ColumnOperand(Record):
    kind: Literal['column']
    name: Name
    path: tuple[Name, ...]


class OutputOperand(Record):
    kind: Literal['output']
    id: Id


Operand = Annotated[Union[ColumnOperand, OutputOperand], Field(discriminator='kind')]


class Compare(Record):
    kind: Literal['compare']
    id: Id
    left: Operand
    op: Literal['eq', 'ne', 'lt', 'le', 'gt', 'ge']
    value: Scalar
    evidence: tuple[Span, ...]


class In(Record):
    kind: Literal['in']
    id: Id
    left: Operand
    values: Annotated[tuple[Scalar, ...], Field(min_length=1)]
    evidence: tuple[Span, ...]


class IsNull(Record):
    kind: Literal['null']
    id: Id
    left: Operand
    is_null: bool
    evidence: tuple[Span, ...]


class Junction(Record):
    kind: Literal['all', 'any']
    id: Id
    children: Annotated[tuple['Predicate', ...], Field(min_length=1)]
    evidence: tuple[Span, ...]


class Not(Record):
    kind: Literal['not']
    id: Id
    child: 'Predicate'
    evidence: tuple[Span, ...]


class Membership(Record):
    kind: Literal['membership']
    id: Id
    entity: Name
    relation: Name
    context: Name
    filters: tuple['Predicate', ...]
    evidence: tuple[Span, ...]


Predicate = Annotated[Union[Compare, In, IsNull, Junction, Not, Membership], Field(discriminator='kind')]
for _model in (Junction, Not, Membership):
    _model.model_rebuild()


class PlanMeasure(Record):
    kind: Literal['measure']
    id: Id
    measure: Name
    context: Name
    mode: Mode
    filters: tuple[Predicate, ...]


class DistinctCount(Record):
    kind: Literal['distinct_count']
    id: Id
    entity: Name
    context: Name
    mode: Mode
    filters: tuple[Predicate, ...]


MeasureOutput = Annotated[Union[PlanMeasure, DistinctCount], Field(discriminator='kind')]


class TimeModeComparison(Record):
    kind: Literal['time_mode']
    id: Id
    inputs: Annotated[tuple[Id, ...], Field(min_length=1)]
    base: Mode
    other: Mode
    evidence: tuple[Span, ...]


class RelativeComparison(Record):
    kind: Literal['previous_period', 'previous_year']
    id: Id
    inputs: Annotated[tuple[Id, ...], Field(min_length=1)]
    evidence: tuple[Span, ...]


class ExplicitComparison(Record):
    kind: Literal['explicit_period']
    id: Id
    inputs: Annotated[tuple[Id, ...], Field(min_length=1)]
    other: Time
    evidence: tuple[Span, ...]


class ContextComparison(Record):
    kind: Literal['contexts']
    id: Id
    left: Id
    right: Id
    evidence: tuple[Span, ...]


Comparison = Annotated[Union[TimeModeComparison, RelativeComparison, ExplicitComparison, ContextComparison], Field(discriminator='kind')]


class Arithmetic(Record):
    kind: Literal['ratio', 'percent_change', 'difference']
    id: Id
    numerator: Id
    denominator: Id
    evidence: tuple[Span, ...]


class Share(Record):
    kind: Literal['share']
    id: Id
    input: Id
    remove_dimensions: tuple[Id, ...]
    remove_filters: tuple[Id, ...]
    evidence: tuple[Span, ...]


class RunningTotal(Record):
    kind: Literal['running_total']
    id: Id
    input: Id
    partition_by: tuple[Id, ...]
    order_by: Id
    evidence: tuple[Span, ...]


Derived = Annotated[Union[Arithmetic, Share, RunningTotal], Field(discriminator='kind')]


class Order(Record):
    output: Id
    direction: Literal['asc', 'desc', 'both']
    nulls: Literal['last']
    evidence: tuple[Span, ...]


class Obligation(Record):
    id: Id
    spans: Annotated[tuple[Span, ...], Field(min_length=1)]
    kind: Literal['name', 'filter', 'time', 'mode', 'dimension', 'count', 'comparison', 'derived', 'order', 'limit', 'membership']
    targets: tuple[Id, ...]
    state: Literal['applied', 'unresolved', 'ambiguous', 'removed']


def predicate_nodes(pred):
    yield pred
    for child in getattr(pred, 'children', ()):
        yield from predicate_nodes(child)
    if pred.kind == 'not':
        yield from predicate_nodes(pred.child)
    for child in getattr(pred, 'filters', ()):
        yield from predicate_nodes(child)


class QueryPlan(Record):
    schema_version: Literal['query-plan/v1'] = Field(alias='schema')
    layout: Literal['aligned', 'separate']
    measures: Annotated[tuple[MeasureOutput, ...], Field(min_length=1)]
    dimensions: tuple[Dimension, ...]
    filters: tuple[Predicate, ...]
    time: Time
    comparisons: tuple[Comparison, ...]
    derived: tuple[Derived, ...]
    order: tuple[Order, ...]
    limit: Positive | None
    obligations: tuple[Obligation, ...]

    def nodes(self):
        yield from self.measures
        yield from self.dimensions
        yield from self.comparisons
        yield from self.derived
        for p in (*self.filters, *(p for m in self.measures for p in m.filters)):
            yield from predicate_nodes(p)

    def outputs(self):
        outputs = {m.id for m in self.measures}
        for c in self.comparisons:
            if c.kind == 'contexts':
                outputs.update((c.id + '/base', c.id + '/other'))
            else:
                outputs.update(f'{c.id}/{i}/{side}' for i in c.inputs for side in ('base', 'other'))
        return outputs | {d.id for d in self.derived}

    @model_validator(mode='after')
    def references(self):
        nodes = list(self.nodes())
        ids = [n.id for n in nodes]
        if len(set(ids)) != len(ids) or any(i in ('time', 'limit') or '/' in i for i in ids):
            raise ValueError('node ids must be unique and unreserved')
        measures = {m.id for m in self.measures}
        dimensions = {d.id for d in self.dimensions}
        outputs = self.outputs()
        dependencies = {}
        for c in self.comparisons:
            refs = (c.left, c.right) if c.kind == 'contexts' else c.inputs
            if not set(refs) <= measures:
                raise ValueError('comparison requires measure outputs')
        for d in self.derived:
            refs = (d.numerator, d.denominator) if d.kind in ('ratio', 'percent_change', 'difference') else (d.input,)
            if not set(refs) <= outputs:
                raise ValueError('missing derived input')
            dependencies[d.id] = refs
            if d.kind == 'share' and (not set(d.remove_dimensions) <= dimensions or not set(d.remove_filters) <= {p.id for p in self.filters}):
                raise ValueError('missing denominator scope')
            if d.kind == 'running_total' and (d.order_by not in dimensions or not set(d.partition_by) <= dimensions - {d.order_by}):
                raise ValueError('invalid window dimensions')
        def visit(i, seen):
            if i in seen:
                raise ValueError('derived cycle')
            for j in dependencies.get(i, ()):
                visit(j, seen | {i})
        for i in dependencies:
            visit(i, set())
        for n in nodes:
            left = getattr(n, 'left', None)
            if isinstance(left, OutputOperand) and left.id not in outputs:
                raise ValueError('missing predicate output')
        if any(o.output not in outputs | dimensions for o in self.order):
            raise ValueError('missing order output')
        targets = set(ids) | outputs | {'time', 'limit'} | {m.id + '/mode' for m in self.measures}
        if any(not set(o.targets) <= targets for o in self.obligations if o.state != 'removed'):
            raise ValueError('missing obligation target')
        return self


class Propose(Record):
    kind: Literal['propose']
    base_turn: Id | None
    plan: QueryPlan


class Clarify(Record):
    kind: Literal['clarify']
    spans: tuple[Span, ...]
    candidates: tuple[Name, ...]


class Abstain(Record):
    kind: Literal['abstain']
    spans: tuple[Span, ...]
    missing: tuple[str, ...]


PlanReply = Annotated[Union[Propose, Clarify, Abstain], Field(discriminator='kind')]
reply_adapter = TypeAdapter(PlanReply)


class Choice(Record):
    id: Id
    label: str


class Clarification(Record):
    spans: tuple[Span, ...]
    slot: Id
    choices: tuple[Choice, ...]


class Binding(Record):
    release_identity: str
    access_fingerprint: str
    products: tuple[str, ...]
    purpose: str


class HistoryEntry(Record):
    turn_id: Id
    question: str
    plan: QueryPlan | None
    clarification: Clarification | None
    binding: Binding


class ResultColumn(Record):
    id: Id
    kind: Literal['dimension', 'measure', 'count', 'derived', 'comparison']
    type: Literal['text', 'boolean', 'integer', 'decimal', 'date']
    currency: str | None
    lineage: tuple[str, ...]
    label: str | None = None
    format: Literal['percent', 'ratio'] | None = None
    display_order: float | None = None


class Cell(Record):
    value: Scalar
    citations: tuple[str, ...]
    null_reasons: tuple[str, ...]


class ResultRow(Record):
    panel: str | None
    dimensions: tuple[Scalar, ...]
    dimension_labels: tuple[str | None, ...] = ()
    cells: tuple[Cell, ...]
    end: Literal['top', 'bottom'] | None = None
    rank: Positive | None = None
    total: bool = False


class Result(Record):
    columns: tuple[ResultColumn, ...]
    rows: tuple[ResultRow, ...]
