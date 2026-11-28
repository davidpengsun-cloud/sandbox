"""The graph adapter of the rules-planner port (docs/use-cases/kge/platform/question-shapes.md §3.1 and §4.3, #450
requirement 1, #518).

It reads a question as the coded adapter does, through `planrules.read`, so slot binding (contexts, measures, units,
entities, dimensions, calendars, filters) stays the platform's, shared by both adapters; only the two shape steps are
its own (`Matched`). Those read the release's question shapes and clarification rules from the graph
(`graphview.Graph.templates`, read once per release) and the question's ledger, read with the graph's full phrase table
(`ledger(..., baseline=False)`), never by word patterns and never by pack code:

1. Clarification rules run first, in id order, before any template is matched: the first whose condition holds is the
   reply, an `Abstain` with its platform text, as the coded builders decline with the same texts.
2. A template matches on the ledger alone: the operations and markers it requires and forbids, how many slots of each
   kind the question binds (`binding.slots`, a lexed window one `range` slot; contexts bound in pairs by
   `shared_contexts`, for every condition alike, where any condition of the release counts two contexts or more), and
   its closed position constraints
   (`before`, `after`, `adjacent`). Only a shape at a live stage (`shadow`, `graph_first`, `retired`) is matched at
   request time: a `specify` template runs at build alone (`GraphPlanner(build=True)`).
3. Every matching template contributes its recipe's nodes, its slot references bound to this question, and all of them
   are applied in the platform's fixed block order: dimensions, time, comparisons, derived values, order and limit at
   the coded `compose` step; then measure parts (a recipe's filters and measures) at the coded `parts` step.

The reply is a `Propose`, a `Clarify` or an `Abstain`, which the port hands to the plan checks, the compiler and the SQL
gate as it hands the coded one. A slot a recipe names that the question cannot bind, two nodes with one id, a time or
limit two templates give differently, or nodes that do not make a valid plan decline with the reason. As in the coded
compose, a question with an unknown word is not shaped.

The adapter is CPU only: it asks the engine's graph for the per-release templates and nothing else, calls no model and
runs no warehouse job, and writes nothing. It is selectable (`rulesport.rules_planner('graph')`) but served by
nothing: the port's default is the coded adapter, so no served answer changes.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from decimal import Decimal

from pydantic import ValidationError

from kge.compiler import plans
from kge.compiler import standards
from kge.compiler.checks import rule_condition, shape_condition
from kge.semantics.plan import Abstain
from kge.semantics.planhistory import clarified_vocabulary
from kge.services.ask.engine import binding, planrules

LIVE = ('shadow', 'graph_first', 'retired')
COMPOSED = ('dimensions', 'time', 'comparisons', 'derived', 'order', 'limit')
PARTS = ('filters', 'measures')
SINGULAR = ('time', 'limit')


class Unbound(ValueError):
    """A recipe's slot reference this question does not bind, or nodes the templates cannot put in one plan."""


class Declined(ValueError):
    """A platform clarification a recipe's own block raises, such as an unordered range."""


@dataclass(frozen=True)
class Reading:
    """A question as the conditions read it: the ledger's pieces in span order, the operations and markers they carry,
    and the slots they bind (binding.slots). The context slots are read one way for every condition of the question:
    paired by shared_contexts (a bare "current" side taking the other side's noun) where any condition the release
    holds counts two context slots or more, else each context as named, so a clarification rule and a template never
    count one question's contexts differently."""
    pieces: tuple
    names: frozenset
    slots: binding.Slots

    @classmethod
    def of(cls, pieces, vocabulary, *, paired=False):
        ordered = tuple(sorted(pieces, key=lambda p: (p.span.start, p.span.end)))
        slots = binding.slots(ordered, vocabulary, two_contexts=paired)
        names = frozenset(p.operation for p in ordered if p.operation) | slots.markers
        return cls(ordered, names, slots)

    def positions(self, operand):
        """The spans of an operand of a position constraint, in span order: a slot kind's slots (each by its first
        span), else a marker's or an operation's pieces. A name that is both a slot kind and an operation (range,
        dimension) is read as the slot kind."""
        if operand in binding.SLOT_KINDS:
            spans = [slot.spans[0] for slot in self.slots[operand] if slot.spans]
        else:
            spans = [p.span for p in self.pieces if operand in p.markers or p.operation == operand]
        return sorted(spans, key=lambda s: (s.start, s.end))

    def between(self, left, right):
        """Whether a ledger piece sits between two spans."""
        low, high = sorted((left, right), key=lambda s: s.start)
        return any(low.end <= p.span.start and p.span.end <= high.start for p in self.pieces)


def _placed(op, a, b, reading):
    """A position constraint, as design §3.1 gives it: before[a, b] holds where the first piece of a ends before the
    first piece of b starts, or the question has no b; after[a, b] where the first piece of a starts after the first
    piece of b ends, or the question has no b; adjacent[a, b] where some piece of a and some piece of b have no piece
    between them. With no a, before and after fail where there is a b."""
    first, second = reading.positions(a), reading.positions(b)
    if op == 'adjacent':
        return any(x != y and not reading.between(x, y) for x in first for y in second)
    if not second:
        return True
    if not first:
        return False
    return first[0].end <= second[0].start if op == 'before' else second[0].end <= first[0].start


def holds(asks, reading) -> bool:
    """Whether a shape's or a clarification rule's condition (kge.compiler.checks.Asks) holds on a question."""
    if not asks.plain <= reading.names or asks.forbids & reading.names:
        return False
    if any(not group & reading.names for group in asks.groups):
        return False
    if any(not lo <= reading.slots.count(kind) <= hi for kind, (lo, hi) in asks.ranges.items()):
        return False
    return all(_placed(op, a, b, reading) for op, a, b in asks.constraints)


def conditions(templates, stages=LIVE):
    """Every clarification rule's condition, in id order, then every shape's at one of stages, in id order."""
    return ([rule_condition(rule) for _, rule in sorted(templates.clarifications.items())]
            + [shape_condition(shape) for _, shape in sorted(templates.shapes.items()) if shape.get('stage') in stages])


def paired(templates, stages=LIVE) -> bool:
    """Whether any condition counts two context slots or more: then a question's contexts are bound in pairs."""
    return any(c.ranges.get('context', (0, 0))[1] >= 2 for c in conditions(templates, stages))


def fired(templates, reading):
    """The first clarification rule, in id order, whose condition holds, or None."""
    return next((rule for _, rule in sorted(templates.clarifications.items()) if holds(rule_condition(rule), reading)), None)


def matched(templates, reading, stages=LIVE):
    """The ids of the shapes at one of stages whose condition holds, in id order."""
    return tuple(sid for sid, shape in sorted(templates.shapes.items())
                 if shape.get('stage') in stages and holds(shape_condition(shape), reading))


def message(rule, texts):
    """A fired rule's text: as the graph holds it, else the platform's text for its message id, else a fixed decline;
    never an empty text, which would read as no decline."""
    return rule.get('text') or texts.get(rule.get('message')) or 'A clarification rule asks for a clarification.'


def _platform_texts():
    return standards.load().clarification_texts


class Bound:
    """One template's slot references resolved against this question and the plan binding has built so far. A named
    slot starts after the slots of its kind declared before it: in `slots: [{name: left, kind: context}, {name: right,
    kind: context}]`, `$right` is the question's second context. A slot after one of its kind with a ranged count has no
    fixed start, and is refused."""

    def __init__(self, shape, reading, data, vocabulary, calendar, *, texts=_platform_texts):
        self.starts, self.counts, taken = {}, {}, {}
        for s in shape.get('slots') or []:
            if not isinstance(s, dict) or not isinstance(s.get('name'), str) or not isinstance(s.get('kind'), str):
                continue
            kind, count = s['kind'], s.get('count', 1)
            self.starts[s['name']] = (kind, taken.get(kind, 0))
            self.counts[s['name']] = count
            before = taken.get(kind, 0)
            taken[kind] = before + count if isinstance(before, int) and isinstance(count, int) else None
        self.slots = reading.slots
        self.data, self.vocabulary, self.calendar = data, vocabulary, calendar
        self.texts = texts

    def position(self, name, index):
        """The kind and the absolute position of a named slot's index-th slot."""
        kind, start = self.starts.get(name, (name, 0))
        if start is None:
            raise Unbound(f'The question shape\'s slot {name} follows a ranged slot of its kind: its place is not fixed.')
        return kind, start + index

    def slot(self, name, index):
        kind, at = self.position(name, index)
        found = self.slots[kind]
        if at >= len(found):
            raise Unbound(f'The question binds no {kind} slot {at} for a question shape.')
        return kind, found[at], at

    def every(self, name):
        """The positions a slot reference with no index covers in a list parameter: the declared count's slots, or,
        for a ranged count, every bound slot of its kind from its start."""
        kind, start = self.position(name, 0)
        count = self.counts.get(name, 1)
        stop = start + count if isinstance(count, int) else len(self.slots[kind])
        return range(stop - start)

    def one(self, kind, slot):
        ids = tuple(dict.fromkeys(slot.value))
        if len(ids) != 1:
            raise Unbound(f'A {kind} slot of a question shape binds {len(ids)} declarations, not one.')
        return ids[0]

    def output(self, kind, slot, at):
        """The plan node a measure, context or dimension slot names: the one output, or dimension, bound to it. A
        measure slot is read through the context slot at its position, which binding builds it from, so two contexts
        of one measure name two outputs."""
        if kind == 'dimension':
            ids = set(slot.value)
            found = [d['id'] for d in self.data['dimensions']
                     if {b['column']['id'] for b in d.get('bindings', ())} & ids]
        else:
            contexts = set(self.slots['context'][at].value) if kind == 'measure' and at < len(self.slots['context']) else (
                set(slot.value) if kind == 'context' else set())
            found = [m['id'] for m in self.data['measures'] if (m.get('context') or {}).get('id') in contexts]
        if len(found) != 1:
            raise Unbound(f'A {kind} slot of a question shape names {len(found)} plan nodes, not one.')
        return found[0]

    def period(self, value):
        return {'kind': value.plan_kind, 'value': value.value[:7] if value.plan_kind == 'month' else value.value,
                'calendar': {'id': self.calendar, 'evidence': []}}

    def value(self, param, name, index):
        kind, slot, at = self.slot(name, index)
        t = param.type
        if t == 'name' and kind in ('measure', 'context', 'entity', 'dimension'):
            return {'id': self.one(kind, slot), 'evidence': []}
        if t in ('ref', 'operand') and kind in ('measure', 'context', 'dimension'):
            node = self.output(kind, slot, at)
            return node if t == 'ref' else {'kind': 'output', 'id': node}
        if t == 'period' and kind == 'period':
            return self.period(slot.value)
        if t == 'time' and kind == 'period':
            return {'kind': 'single', 'period': self.period(slot.value)}
        if t == 'time' and kind == 'range':
            first, last = slot.value[0], slot.value[-1]
            if first.plan_kind != last.plan_kind or first.value > last.value:
                raise Declined(self.texts()['ordered-range'])
            return {'kind': 'range', 'first': self.period(first), 'last': self.period(last)}
        if kind == 'number' and t in ('scalar', 'positive', 'int'):
            return self.number(param, slot.value)
        if t == 'enum' and kind in ('comparison_op', 'direction') and slot.value in param.values:
            return slot.value
        raise Unbound(f'A {kind} slot cannot fill a question shape\'s {param.type} parameter {param.name}.')

    def number(self, param, value):
        number = Decimal(str(value))
        if param.type == 'scalar':
            if number == number.to_integral_value():
                return {'kind': 'integer', 'value': int(number)}
            return {'kind': 'decimal', 'value': format(number, 'f')}
        if number != number.to_integral_value() or (param.type == 'positive' and number <= 0):
            raise Unbound(f'A number slot cannot fill a question shape\'s {param.type} parameter {param.name}.')
        return int(number)


def nodes(shape, bound):
    """The plan nodes a shape's recipe contributes, by section: each block a node with its parameters bound, a
    predicate block another block names nested there and not repeated in filters."""
    recipe = shape.get('recipe') or {}
    blocks = [item for item in recipe.get('blocks') or [] if isinstance(item, dict) and item.get('block') in plans.CATALOGUE]
    if any(not isinstance(item.get('id'), str) or not item['id'] for item in blocks):
        raise Unbound('A block of a question shape\'s recipe has no id.')
    items = {item['id']: copy.deepcopy(item) for item in blocks}
    nested = {v for item in items.values() for p, param in plans.CATALOGUE[item['block']].params.items()
              if param.type == 'predicate' and p in item for v in (item[p] if isinstance(item[p], list) else [item[p]])
              if isinstance(v, str) and not plans.SLOT_REF.fullmatch(v)}

    def one(param, value, seen):
        if isinstance(value, str) and (m := plans.SLOT_REF.fullmatch(value)):
            return bound.value(param, m[1], int(m[2] or 0))
        if param.type == 'name':
            return {'id': value, 'evidence': []} if isinstance(value, str) else value
        if param.type == 'predicate':
            if isinstance(value, str) and value in items and value not in seen:
                return node(items[value], seen)
            raise Unbound(f'A question shape names {value!r}, which is not a predicate block of its recipe.')
        if param.type == 'operand' and isinstance(value, str):
            return {'kind': 'output', 'id': value}
        if param.type in plans.LITERALS:
            return plans._plain(plans.LITERALS[param.type], value)
        return value

    def spread(param, value, seen):
        """A list parameter's values: a slot reference with no index gives one value for each slot it covers."""
        if isinstance(value, str) and (m := plans.SLOT_REF.fullmatch(value)) and m[2] is None:
            return [bound.value(param, m[1], i) for i in bound.every(m[1])]
        return [one(param, value, seen)]

    def node(item, seen=frozenset()):
        block = plans.CATALOGUE[item['block']]
        seen = seen | {item['id']}
        if block.section == 'limit':
            return one(block.params['value'], item.get('value'), seen)
        out = {} if block.section == 'order' else {'kind': block.name}
        if block.section not in plans.UNNAMED:
            out['id'] = item['id']
        for p, param in block.params.items():
            if p in item:
                value = item[p]
                out[p] = ([v for each in (value if isinstance(value, list) else [value]) for v in spread(param, each, seen)]
                          if param.many else one(param, value, seen))
        if 'evidence' in getattr(block.model, 'model_fields', {}):
            out['evidence'] = []
        return out
    found = {}
    for bid, item in items.items():
        section = plans.CATALOGUE[item['block']].section
        if section == 'filters' and bid in nested:
            continue
        found.setdefault(section, []).append(node(item))
    return found


def apply(data, contributed, sections):
    """Put each contribution into the plan, section by section in the platform's fixed order, shapes in id order. A
    node whose id the plan already holds (binding's own, such as m0, or another shape's, even an equal node), or a time
    or limit two shapes give differently, cannot be one plan."""
    ids = {n['id'] for key in ('measures', 'dimensions', 'filters', 'comparisons', 'derived') for n in data[key]}
    given = {}
    for section in sections:
        for sid, found in contributed:
            for value in found.get(section, ()):
                if section in SINGULAR:
                    if section in given and given[section] != value:
                        raise Unbound(f'Two question shapes give the plan\'s {section} differently.')
                    given[section] = value
                    data[section] = value
                elif section == 'order':
                    data['order'].append(value)
                else:
                    if value['id'] in ids:
                        raise Unbound(f'Two plan nodes would share the id {value["id"]}.')
                    ids.add(value['id'])
                    data[section].append(value)


class Matched:
    """The graph adapter's two shape steps of `planrules.read`, on one release's templates, for one question: compose
    fires the first clarification rule, else matches the live templates and applies all but their measure parts;
    parts applies those. The pieces read with the graph's phrase table (baseline False). Where the ledger holds an
    unknown word, neither step shapes the plan, as the coded compose does not. `used` names the shapes whose condition
    held, for the comparison slice to attribute the question to: also where their nodes could not make the plan and the
    reply declines, and on a follow-up, where the previous plan replaces what they composed."""
    baseline = False

    def __init__(self, templates, stages=LIVE, *, texts=_platform_texts):
        self.templates, self.stages = templates, stages
        self.texts = texts
        self.used: tuple = ()
        self.contributed: list = []

    def compose(self, data, req, pieces, vocab):
        if any(p.kind == 'unknown' for p in pieces):
            return None
        reading = Reading.of(pieces, vocab, paired=paired(self.templates, self.stages))
        rule = fired(self.templates, reading)
        if rule is not None:
            return message(rule, self.texts())
        self.used = matched(self.templates, reading, self.stages)
        calendar = (data['time'].get('calendar') or data['time'].get('period', {}).get('calendar') or {}).get('id')
        try:
            self.contributed = [(sid, nodes(self.templates.shapes[sid],
                                            Bound(self.templates.shapes[sid], reading, data, vocab, calendar, texts=self.texts)))
                                for sid in self.used]
            apply(data, self.contributed, COMPOSED)
        except (Unbound, Declined) as exc:
            return str(exc)
        return None

    def parts(self, data, pieces, vocab, text):
        try:
            apply(data, self.contributed, PARTS)
        except Unbound as exc:
            return str(exc)
        return None


class GraphPlanner:
    """The graph adapter (`rulesport.RulesPlanner`): `planrules.followup`, else `planrules.read` on the vocabulary a
    clarification's choice narrows, as the coded adapter reads, with `Matched`'s shape steps in place of the coded
    builders. templates, when given, is the release's (graphview.Templates); otherwise the engine's graph gives it,
    kept per release. build=True also matches shapes at `specify`, for the build's replay alone."""
    name = 'graph'

    def __init__(self, templates=None, *, build=False):
        self.templates, self.stages = templates, ('specify', *LIVE) if build else LIVE

    def plan(self, question):
        return self.read(question)[0]

    def read(self, question):
        """The reply and the ids of the shapes it matched, in id order (none for a clarification rule's decline)."""
        member_inputs = getattr(question.engine, 'member_plan_inputs', None)
        if member_inputs is not None:
            request, vocabulary = member_inputs
            question = replace(question, request=request, vocabulary=vocabulary)
        templates = self.templates if self.templates is not None else question.engine.g.templates()
        texts = getattr(question.engine, 'shadow_clarification_texts', _platform_texts)
        shapes = Matched(templates, self.stages, texts=texts)
        try:
            reply = (planrules.followup(question.engine, question.request, question.vocabulary, question.cfg,
                                        question.history, question.turn, shapes=shapes)
                     or planrules.read(question.engine, question.request,
                                       clarified_vocabulary(question.history, question.vocabulary, question.question),
                                       question.cfg, question.turn, question.cohort, shapes=shapes))
        except ValidationError:
            reply = Abstain(kind='abstain', spans=(), missing=('The matched question shapes do not make a valid plan.',))
        return reply, shapes.used


GRAPH = GraphPlanner()
