"""The only constructor of executable plans: names, coverage, roles and policies."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import sqlglot
from sqlglot import exp
from kge.compiler.vocabulary import PATTERNS
from kge.ports import AUTHORED

from kge.semantics.coverage import coverage, ledger, names_in, name_supported, referenced_targets, references, predicate_value_supported, member_value, one_meaning, removal_spans, remaining_pieces, period_binding, canonical_calendars
from kge.semantics.plan import QueryPlan, predicate_nodes
from kge.semantics.planperiods import time_bounds, time_calendar, periods, shifted_mapping
from kge.pipeline.trace import graph_read
from kge.pipeline.sqlguard import temporal_rule
from kge.semantics.query import bare

_TOKEN = object()


class PlanDenied(ValueError):
    """A declared policy forbids the requested relationship."""


@dataclass(frozen=True, init=False)
class CheckedPlan:
    plan: QueryPlan
    vocabulary: object
    coverage: tuple
    units: tuple[str, ...]

    def __init__(self, token, plan, vocabulary, certificate, units):
        if token is not _TOKEN:
            raise TypeError('only the plan checker constructs checked plans')
        for k, v in (('plan', plan), ('vocabulary', vocabulary), ('coverage', certificate), ('units', units)):
            object.__setattr__(self, k, v)


@dataclass(frozen=True)
class Decision:
    kind: str
    missing: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()


def phase(predicate):
    if predicate.kind in ('all', 'any'):
        phases = {phase(c) for c in predicate.children}
        if len(phases) != 1:
            raise ValueError('boolean predicate mixes row and output operands')
        return phases.pop()
    if predicate.kind == 'not':
        return phase(predicate.child)
    return 'row' if predicate.kind == 'membership' or predicate.left.kind == 'column' else 'output'


def role(vocab, context, column, path, level=None):
    ctx = vocab.contexts[context]
    table, _ = column.rsplit('.', 1)
    current, terminal = ctx.fact, ctx.fact
    hierarchy = 0
    keys = []
    for name in path:
        join = next((j for j in ctx.joins if j.id == name.id), None)
        if join is None or join.source not in (current, current.rsplit('.', 1)[-1], terminal, terminal.rsplit('.', 1)[-1]) or join.through:
            raise ValueError('missing declared role path')
        if join.target.split('.')[0] != ctx.unit:
            raise ValueError('cross-unit role requires certified membership')
        temporal_join(ctx, join, vocab)
        source, target = current.rsplit('.', 1)[-1].casefold(), (join.alias or bare(join.target)).casefold()
        pairs = []
        for predicate in sqlglot.parse_one(join.condition, read=AUTHORED).find_all(exp.EQ):
            left, right = predicate.this, predicate.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                if left.table.casefold() == target and right.table.casefold() == source:
                    left, right = right, left
                if left.table.casefold() == source and right.table.casefold() == target:
                    pairs.append((left.name.casefold(), right.name.casefold()))
        if not pairs:
            raise ValueError('role has no declared key alignment')
        keys.append((join.target, tuple(sorted(pairs))))
        terminal = join.target
        current = join.alias or join.target
        hierarchy += join.kind == 'hierarchy'
    if terminal != table or (level is not None and hierarchy != level):
        raise ValueError('role does not reach the requested dimension level')
    return column, hierarchy, tuple(keys)


def context_scope(ctx, vocab, reads_measure=True):
    if reads_measure and set(ctx.values) & {'.'.join(c) for c in vocab.semantics.d.RESTRICTED_COLUMNS}:
        raise PlanDenied('The requested read is not permitted. Contact the access administrator.')
    aliases = {j.alias: j.target for j in ctx.joins if j.alias}
    required = ({ctx.fact} | {aliases.get(t, t) for j in ctx.joins if j.required for t in (j.source, j.target)}
                | {f.table for f in ctx.filters} | {s.table for s in ctx.selectors if s.table.split('.')[0] == ctx.unit})
    if not required <= set(vocab.tables):
        raise ValueError('context requires data outside access or product scope')
    for join in ctx.joins:
        if join.required:
            temporal_join(ctx, join, vocab)


def temporal_join(ctx, join, vocab):
    sources = {bare(t): vocab.tables[t] for t in (ctx.fact, join.source, join.target, join.through) if t in vocab.tables}
    sources.update({j.alias: vocab.tables[j.target] for j in ctx.joins if j.alias and j.target in vocab.tables})
    for table in (join.target, join.through):
        policy = vocab.semantics.scd2.get(table)
        if policy is not None:
            try:
                temporal_rule(join.condition, join.alias if table == join.target and join.alias else bare(table), policy, sources)
            except ValueError as exc:
                raise ValueError(f'join {join.id}: {exc}') from exc


def membership_binding(node, vocab, ctx):
    far = vocab.contexts.get(node.context.id)
    relation = next((j for j in ctx.joins if j.id == node.relation.id and j.through), None)
    entity = vocab.entities.get(node.entity.id)
    if far is None or relation is None or entity is None or not entity['key']:
        raise ValueError('missing declared membership binding')
    context_scope(far, vocab, reads_measure=False)
    if relation.target != far.fact or entity['table'] != far.fact:
        raise ValueError('membership entity does not match the far context')
    if not {relation.source, relation.target, relation.through} <= set(vocab.tables):
        raise ValueError('membership requires data outside access or product scope')
    if frozenset((ctx.unit, far.unit)) in vocab.no_join:
        raise PlanDenied('no-join policy blocks membership')
    if ctx.unit != far.unit and not any(
            set(c['between']) == {ctx.unit, far.unit} and c['declared_join'] == relation.id
            for c in vocab.semantics.certified):
        raise ValueError('membership requires a certified relationship')
    reachable = {ctx.fact}
    for join in ctx.joins:
        if join.source in reachable and not join.through and not join.alias:
            reachable.add(join.target)
    if relation.source not in reachable:
        raise ValueError('membership source has no declared path')
    temporal_join(ctx, relation, vocab)
    return far, relation


def validate_predicate(node, vocab, ctx, contexts):
    if node.kind in ('all', 'any', 'not'):
        phase(node)
        for child in node.children if node.kind != 'not' else (node.child,):
            validate_predicate(child, vocab, ctx, contexts)
        return
    if node.kind == 'membership':
        far, _ = membership_binding(node, vocab, ctx)
        contexts.add(far.id)
        for child in node.filters:
            if phase(child) != 'row':
                raise ValueError('membership predicates must narrow rows')
            validate_predicate(child, vocab, far, contexts)
        return
    operand = node.left
    if operand.kind != 'column':
        if any(v.kind not in ('integer', 'decimal', 'null') for v in getattr(node, 'values', (getattr(node, 'value', None),)) if v is not None):
            raise ValueError('numeric output requires a numeric literal')
        return
    entry = vocab.entries[operand.name.id]
    if entry.kind != 'column':
        raise ValueError('column operand required')
    role(vocab, ctx.id, entry.column, operand.path)
    table, column = entry.column.rsplit('.', 1)
    dtype = vocab.tables[table][column].upper()
    for scalar in getattr(node, 'values', (getattr(node, 'value', None),)):
        if scalar is None or scalar.kind == 'null':
            continue
        if scalar.kind == 'text':
            if not any(t in dtype for t in ('CHAR', 'STRING', 'TEXT')):
                raise ValueError('text literal on nontext column')
            if not any(e.kind == 'value' and e.column == entry.column and e.value == scalar.value for e in vocab.entries.values()) and not declared_value(vocab, entry.column, scalar.value):
                raise ValueError('undeclared member value')
        elif scalar.kind in ('integer', 'decimal') and not any(t in dtype for t in ('INT', 'DECIMAL', 'NUMERIC', 'DOUBLE', 'FLOAT', 'REAL')):
            raise ValueError('numeric literal on nonnumeric column')
        elif scalar.kind == 'date' and 'DATE' not in dtype and 'TIME' not in dtype:
            raise ValueError('date literal on nondate column')
        elif scalar.kind == 'boolean' and dtype != 'BOOLEAN':
            raise ValueError('boolean literal on nonboolean column')


def comparison_mapping(plan, comparison, vocab):
    base = time_bounds(plan.time, vocab.calendars)
    other = time_bounds(comparison.other, vocab.calendars)
    if base is None or other is None or time_calendar(comparison.other) != time_calendar(plan.time):
        raise ValueError('explicit comparison needs bounded windows on one calendar')
    mapping = {}
    for dimension in plan.dimensions:
        if dimension.kind != 'time':
            continue
        cal = vocab.calendars[dimension.calendar.id]
        left, right = periods(base, dimension.grain, cal), periods(other, dimension.grain, cal)
        if len(left) != len(right):
            raise ValueError('explicit comparison needs a one-to-one period mapping')
        mapping[dimension.id] = tuple(zip(right, left))
    return mapping


def record_matches(vocabulary, pieces):
    """Record lexical matches before checking any proposed content, without consuming its rules."""
    graph_vocabulary = getattr(vocabulary.semantics, 'graph_vocabulary', None)
    contexts = {i for p in pieces if p.kind == 'name' for i in p.candidates if i in vocabulary.contexts}
    calendars = {vocabulary.periods[c.fact]['calendar'] for i in contexts
                 if (c := vocabulary.contexts[i]).fact in vocabulary.periods}
    for piece in pieces:
        identifiers = set(piece.candidates) if piece.kind == 'name' else set()
        if piece.kind == 'time':
            fiscal = getattr(piece.value, 'kind', '').startswith('fiscal_')
            identifiers = ({i for i, c in vocabulary.calendars.items() if c.get('quarter_label', '').startswith('FY')}
                           if fiscal else calendars or set(vocabulary.calendars))
        for identifier in sorted(identifiers):
            entry = vocabulary.entries.get(identifier)
            if entry is not None:
                word = piece.text
                if piece.operation == 'dimension' and word.casefold().startswith(('by ', 'each ')):
                    word = word.split(' ', 1)[1]
                graph_read('match', (entry, word, graph_vocabulary))


def check_plan(plan, vocabulary, text, turn='turn', inherited=(), removed=None):
    record_matches(vocabulary, ledger(text, turn, vocabulary))
    if vocabulary.denial(text):
        return Decision('refuse', ('The requested read is not permitted. Contact the access administrator.',))
    try:
        spans = removal_spans(plan, text, turn, vocabulary, removed or {})
    except ValueError as exc:
        return Decision('abstain', (str(exc),))
    pieces = (*remaining_pieces(ledger(text, turn, vocabulary), spans), *inherited)
    plan = canonical_calendars(plan, pieces, vocabulary)
    def candidates(piece):
        return (tuple(vocabulary.entries[i] for i in piece.candidates) if piece.span.turn != turn
                else vocabulary.candidates(piece.text))
    if any(p.operation == 'add' for p in pieces):
        explicit_sources = {e.id for p in pieces for e in candidates(p) if e.kind in ('unit', 'source_set')}
        sources = {vocabulary.contexts[m.context.id].unit for m in plan.measures if m.context.id in vocabulary.contexts}
        if not explicit_sources and any(pair <= sources for pair in vocabulary.no_join):
            return Decision('refuse', ('no-join policy blocks the requested cross-source addition.',))
        return Decision('abstain', ('Addition of different measures has no supported typed operation.',))
    unknown = tuple(p.text for p in pieces if p.kind == 'unknown')
    requested_units = {e.id for p in pieces for e in candidates(p) if e.kind == 'unit'} | {e.unit for p in pieces for e in candidates(p) if e.kind == 'entity' and e.unit}
    if not requested_units:
        requested_units = {u for p in pieces for e in candidates(p) if e.kind == 'source_set' for u in e.value}
    targets = {n.id: n for n in plan.nodes()}
    written = {e.id for piece in pieces if len(piece.candidates) == 1 for e in candidates(piece)}
    written_contexts = {i for i in written if vocabulary.entries[i].kind == 'context'}
    written_facts = {vocabulary.entities[i]['table'] for i in written if i in vocabulary.entities}
    entity_facts = written_facts | {vocabulary.contexts[i].fact for p in pieces if one_meaning(p.candidates, vocabulary)
                                    for i in p.candidates if i in vocabulary.contexts}
    written_columns = {vocabulary.entries[i].column for i in written if vocabulary.entries[i].kind == 'column'}
    for piece in pieces:
        permitted = tuple(e for e in candidates(piece) if not requested_units or e.unit is None or e.unit in requested_units)
        if member := member_value(tuple(e.id for e in permitted), vocabulary):
            permitted = (member,)
        mixed_scope = {'context', 'unit'} <= {vocabulary.entries[i].kind for i in piece.candidates}
        if mixed_scope:
            permitted = tuple(vocabulary.entries[i] for i in piece.candidates)
        if not requested_units and (vocabulary.default_units or vocabulary.binding.products):
            preferred = set(vocabulary.default_units) or {c.unit for c in vocabulary.contexts.values() if c.fact.casefold() in vocabulary.binding.products}
            preferred = preferred or {p.split('.')[0] for p in vocabulary.binding.products}
            narrowed = tuple(e for e in permitted if e.unit is None or e.unit in preferred)
            permitted = narrowed or permitted
        if len(permitted) > 1:
            selected = {n.id for n in names_in(plan) if piece.span in n.evidence}
            selected.update(i for o in plan.obligations if o.state == 'applied' and piece.span in o.spans
                            for i in referenced_targets([targets[t] for t in o.targets if t in targets], vocabulary))
            chosen = [e for e in permitted if e.id in selected]
            linked = len(chosen) == len(permitted) and one_meaning({e.id for e in chosen}, vocabulary)
            explicit = linked or (not mixed_scope and len(chosen) == 1 and (
                chosen[0].context in written_contexts
                or (chosen[0].kind == 'entity' and vocabulary.entities[chosen[0].id]['table'] in entity_facts)
                or (chosen[0].kind in ('value', 'column') and (chosen[0].column in written_columns
                    or chosen[0].column.rsplit('.', 1)[0] in written_facts))
                or (chosen[0].path and set(chosen[0].path) <= written)
                or any((chosen[0].kind == 'measure' and vocabulary.contexts[i].measure == chosen[0].id)
                       or (chosen[0].kind == 'unit' and vocabulary.contexts[i].unit == chosen[0].id) for i in written_contexts)))
            if not explicit:
                if unknown:
                    return Decision('abstain', unknown)
                return Decision('clarify', (piece.text,), tuple(e.id for e in permitted))
    for name in names_in(plan):
        entry = vocabulary.entries.get(name.id)
        if entry is None:
            return Decision('abstain', ('A requested name is absent from the readable vocabulary.',))
        if entry.kind == 'candidate':
            return Decision('abstain', ('The requested term needs steward certification.',))
        for span in name.evidence:
            if not any(p.span == span and name_supported(name.id, p.candidates, vocabulary) for p in pieces):
                return Decision('abstain', ('A name has unrelated evidence.',))
    named_contexts = {c for p in pieces if p.kind == 'name' for c in p.candidates if c in vocabulary.contexts}
    for measure in plan.measures:
        if measure.kind != 'measure':
            continue
        for span in measure.measure.evidence:
            if not any(p.span == span and name_supported(measure.measure.id, p.candidates, vocabulary,
                       measure.context.id, named_contexts) for p in pieces):
                return Decision('abstain', ('A name has unrelated evidence.',))
    try:
        if any(o.direction == 'both' for o in plan.order) and (
                len(plan.order) != 1 or plan.limit is None or not plan.dimensions
                or plan.order[0].output not in plan.outputs()):
            raise ValueError('both ends require one output ordering, a limit per end and a breakdown')
        cal = time_calendar(plan.time)
        if cal not in vocabulary.calendars:
            raise ValueError('missing declared calendar')
        time_bounds(plan.time, vocabulary.calendars)
        from kge.semantics.coverage import ytd_problem
        if problem := ytd_problem(plan, text, vocabulary):
            raise ValueError(problem)
        contexts, membership_contexts = set(), set()
        for m in plan.measures:
            ctx = vocabulary.contexts.get(m.context.id)
            if ctx is None:
                raise ValueError('missing readable context')
            contexts.add(ctx.id)
            context_scope(ctx, vocabulary)
            memberships = {n.relation.id for p in (*plan.filters, *m.filters) for n in predicate_nodes(p) if n.kind == 'membership'}
            if any(j.required and j.through and j.id not in memberships for j in ctx.joins):
                raise ValueError('missing required membership binding')
            if m.kind == 'measure' and ctx.measure != m.measure.id:
                raise ValueError('measure does not belong to context')
            binding = vocabulary.periods.get(ctx.fact)
            if plan.time.kind != 'all_available' or any(d.kind == 'time' for d in plan.dimensions):
                if not binding or (binding['calendar'] != cal and plan.time.kind == 'latest_released'):
                    raise ValueError('missing compatible period binding')
            if m.kind == 'distinct_count':
                entity = vocabulary.entities.get(m.entity.id)
                if entity is None or not entity['key']:
                    raise ValueError('missing complete entity key')
                reachable = {ctx.fact}
                for j in ctx.joins:
                    if j.source in reachable and not j.through and not j.alias:
                        reachable.add(j.target)
                if entity['table'] not in reachable:
                    raise ValueError('entity has no declared path')
            if binding:
                order = {'day': 0, 'month': 1, 'calendar_quarter': 2, 'fiscal_quarter': 2, 'year': 3, 'fiscal_year': 3}
                if any(d.kind == 'time' and order.get(binding['kind'], 0) > order[d.grain] for d in plan.dimensions):
                    raise ValueError('requested time grain is finer than the declared observations')
            if (m.mode == 'as_reported' or plan.time.kind == 'latest_released' or any(c.kind == 'time_mode' for c in plan.comparisons)) and not any(
                j.required and j.target in vocabulary.releases for j in ctx.joins):
                raise ValueError('missing release/freeze policy')
            part_info = (getattr(vocabulary.semantics, 'partitioned', None) or {}).get(ctx.fact)
            if part_info is not None and plan.time.kind == 'all_available':
                part_col_id = f"{ctx.fact}.{part_info['column']}"
                has_part_pred = any(
                    n.kind not in ('all', 'any', 'not', 'membership')
                    and getattr(n.left, 'kind', None) == 'column'
                    and vocabulary.entries.get(n.left.name.id) is not None
                    and vocabulary.entries[n.left.name.id].column == part_col_id
                    for pred in (*plan.filters, *m.filters)
                    for n in predicate_nodes(pred)
                )
                if not has_part_pred:
                    raise PlanDenied(f"table {ctx.fact} requires a period filter on partition column {part_info['column']}")
            for pred in (*plan.filters, *m.filters):
                if pred in m.filters and phase(pred) != 'row':
                    raise ValueError('local predicates must narrow rows')
                validate_predicate(pred, vocabulary, ctx, membership_contexts)
        for d in plan.dimensions:
            if d.kind == 'time':
                if d.calendar.id != cal:
                    raise ValueError('incompatible dimension calendar')
                continue
            bound = {b.context.id for b in d.bindings}
            if plan.layout == 'aligned' and bound != contexts:
                raise ValueError('dimension must bind every context')
            identities = set()
            for b in d.bindings:
                if b.context.id not in contexts:
                    raise ValueError('unselected dimension context')
                entry = vocabulary.entries[b.column.id]
                if entry.kind != 'column':
                    raise ValueError('dimension column required')
                identities.add(role(vocabulary, b.context.id, entry.column, b.path, b.level))
            if plan.layout == 'aligned' and len(identities) != 1:
                raise ValueError('no declared dimension role alignment')
        units = tuple(sorted({vocabulary.contexts[c].unit for c in contexts | membership_contexts}))
        for i, a in enumerate(units):
            for b in units[i + 1:]:
                independent = (not plan.dimensions and not plan.comparisons and not plan.derived
                               and len({vocabulary.contexts[m.context.id].term for m in plan.measures}) == 1
                               and not membership_contexts)
                if frozenset((a, b)) in vocabulary.no_join and (plan.layout != 'separate' or plan.derived) and not independent:
                    raise PlanDenied('no-join policy blocks alignment or arithmetic')
        specs = {m.id: (replace(vocabulary.measures[vocabulary.contexts[m.context.id].measure], currency=None, population=m.entity.id, additive=False)
                        if m.kind == 'distinct_count' else vocabulary.measures[vocabulary.contexts[m.context.id].measure]) for m in plan.measures}
        output_specs = dict(specs)
        change_pairs = set()
        comparison_occurrences = set()
        implicit_measures = {m.id for m in plan.measures if any(
            c.kind == 'contexts' and m.id in (c.left, c.right) for c in plan.comparisons) and any(
                p.span.turn != turn and not p.candidates and p.operation == m.mode and any(
                    o.state == 'applied' and p.span in o.spans and {m.id, m.id + '/mode'} & set(o.targets)
                    for o in plan.obligations) for p in pieces)}
        for c in plan.comparisons:
            implicit_measures.update(comparison_direction(c, plan, text, turn, pieces, vocabulary, comparison_occurrences) or ())
            if c.kind == 'contexts':
                output_specs[c.id + '/base'] = specs[c.left]
                output_specs[c.id + '/other'] = specs[c.right]
                change_pairs.update(((c.id + '/base', c.id + '/other'), (c.left, c.right)))
            else:
                if c.kind == 'explicit_period':
                    comparison_mapping(plan, c, vocabulary)
                if c.kind != 'time_mode' and plan.time.kind in ('all_available', 'latest_released'):
                    if plan.time.kind == 'all_available':
                        raise ValueError('time comparison needs bounded periods')
                if c.kind == 'previous_period' and plan.time.kind in ('range', 'ytd') and not any(d.kind == 'time' for d in plan.dimensions):
                    period = plan.time.first if plan.time.kind == 'range' else plan.time.through
                    if len(periods(time_bounds(plan.time, vocabulary.calendars), period.kind, vocabulary.calendars[cal])) > 1:
                        raise ValueError("The previous period of a multi-period range isn't declared.")
                if c.kind in ('previous_year', 'previous_period') and plan.time.kind != 'latest_released':
                    shifted_mapping(plan, c.kind, vocabulary.calendars)
                for i in c.inputs:
                    for side in ('base', 'other'):
                        output_specs[f'{c.id}/{i}/{side}'] = specs[i]
                    change_pairs.add((f'{c.id}/{i}/base', f'{c.id}/{i}/other'))
        for derived in plan.derived:
            if derived.kind in ('ratio', 'percent_change', 'difference'):
                a, b = output_specs.get(derived.numerator), output_specs.get(derived.denominator)
                if a is None or b is None or a.population != b.population or (a.currency != b.currency
                        and not (derived.kind == 'ratio' and b.currency is None)):
                    raise ValueError('incompatible currencies or populations')
                if derived.kind in ('percent_change', 'difference') and a.additive != b.additive:
                    raise ValueError('incompatible observation grains')
                if derived.kind in ('percent_change', 'difference') and (derived.numerator, derived.denominator) not in change_pairs:
                    raise ValueError(derived.kind.replace('_', ' ') + ' requires the comparison base as numerator and its other output as denominator')
                if derived.kind == 'ratio':
                    ratio_direction(derived, plan, text, turn, pieces, vocabulary)
                output_specs[derived.id] = replace(a, currency=a.currency if derived.kind == 'difference' or (derived.kind == 'ratio' and b.currency is None) else None, additive=False)
            else:
                spec = output_specs.get(derived.input)
                if spec is None:
                    raise ValueError('unresolved derived order')
                if derived.kind == 'running_total':
                    dimension = next(d for d in plan.dimensions if d.id == derived.order_by)
                    if not spec.additive or dimension.kind != 'time':
                        raise ValueError('running total requires additive observations and time order')
                if derived.kind == 'share' and any(phase(p) != 'row' for p in plan.filters if p.id in derived.remove_filters):
                    raise ValueError('denominator scope can remove only row filters')
                if derived.kind == 'share':
                    if derived.remove_filters and not share_removal_requested(derived, plan, pieces, vocabulary):
                        raise ValueError('share denominator must retain every named filter unless a whole-population share is requested')
                    total = re.search(r'\bshare\s+of\s+(?:the\s+)?total\b', text, re.I)
                    if ((plan.dimensions and not derived.remove_dimensions)
                            or (total and set(derived.remove_dimensions) != {d.id for d in plan.dimensions})):
                        raise ValueError('share denominator must remove the requested breakdown dimensions')
                output_specs[derived.id] = replace(spec, currency=None, additive=False) if derived.kind == 'share' else spec
    except PlanDenied as exc:
        return Decision('refuse', (str(exc),))
    except (ValueError, KeyError) as exc:
        return Decision('abstain', (str(exc),))
    missing = coverage(plan, text, turn, vocabulary, inherited, removed, implicit_measures)
    if missing:
        return Decision('abstain', missing)
    targets = {n.id: n for n in plan.nodes()} | {"time": plan.time}
    for piece in pieces:
        if piece.span.turn != turn:
            continue
        applied = {target for obligation in plan.obligations if obligation.state == "applied"
                   and any(span.turn == turn and span.start <= piece.span.start and span.end >= piece.span.end
                           for span in obligation.spans) for target in obligation.targets}
        if not applied:
            continue
        returned = referenced_targets([targets[t] for t in applied if t in targets], vocabulary)
        identifiers = set(piece.candidates) & returned if piece.kind == "name" else set()
        if piece.kind == "time" and "time" in applied:
            identifiers.add(time_calendar(plan.time))
        for identifier in sorted(identifiers):
            entry = vocabulary.entries.get(identifier)
            if entry is not None:
                word = piece.text
                if piece.operation == "dimension" and word.casefold().startswith(("by ", "each ")):
                    word = word.split(" ", 1)[1]
                graph_read("match", (entry, word, getattr(vocabulary.semantics, "graph_vocabulary", None), applied))
    return CheckedPlan(_TOKEN, plan, vocabulary, pieces, units)


def share_removal_requested(derived, plan, pieces, vocabulary):
    """A whole-population denominator belongs to the share of its lexical measure."""
    nodes = {node.id: node for node in plan.nodes()}
    source = nodes.get(derived.input)
    while getattr(source, 'kind', None) == 'running_total':
        source = nodes.get(source.input)
    if getattr(source, 'kind', None) not in ('measure', 'distinct_count'):
        return False
    owned = {span for obligation in plan.obligations if obligation.state == 'applied' and derived.id in obligation.targets
             for span in obligation.spans}
    for piece in pieces:
        if piece.operation != 'share' or piece.span not in owned or piece.value != 'whole_population':
            continue
        names = [p for p in pieces if p.span.turn == piece.span.turn and p.kind == 'name' and any(
            vocabulary.entries[i].kind in ('context', 'measure') for i in p.candidates)]
        if not names:
            continue
        head = min(names, key=lambda p: min(abs(p.span.end - piece.span.start), abs(p.span.start - piece.span.end)))
        contexts = set(head.candidates) & vocabulary.contexts.keys()
        if source.context.id in contexts or (not contexts and vocabulary.contexts[source.context.id].measure in head.candidates):
            return True
    return False


def comparison_direction(comparison, plan, text, turn, pieces, vocabulary, consumed):
    """Bind comparison sides to lexical operands, never obligation targets."""
    if comparison.kind == 'contexts':
        ordered = sorted((p for p in pieces if p.span.turn == turn), key=lambda p: p.span.start)
        if not any(p.operation == 'comparison' for p in ordered):
            return
        measures = {m.id: m for m in plan.measures}
        def named(measure, candidates):
            contexts = set(candidates) & vocabulary.contexts.keys()
            return measure.context.id in contexts if contexts else measure.measure.id in candidates if measure.kind == 'measure' else measure.entity.id in candidates
        def supports(measure, piece, other):
            if piece.operation in ('current', 'as_reported') and not piece.candidates:
                head = other.text
                for following in ordered[ordered.index(other) + 1:]:
                    if following.kind == 'time':
                        continue
                    if following.kind == 'name' and (set(following.candidates) & set(other.candidates) or any(
                            vocabulary.contexts[i].measure in following.candidates for i in other.candidates if i in vocabulary.contexts)):
                        head = following.text
                    break
                contexts = set()
                for noun in (*[head[split.end():] for split in re.finditer(r'\s+', head)], head):
                    candidates = {entry.id for entry in vocabulary.candidates(noun)}
                    contexts = {ctx.id for ctx in vocabulary.contexts.values() if ctx.id in candidates
                                or (ctx.measure in candidates and ctx.term.casefold() == noun.casefold())}
                    if contexts and contexts != set(other.candidates) & vocabulary.contexts.keys():
                        break
                    contexts = set()
                if not contexts:
                    measures_named = {vocabulary.contexts[i].measure for i in other.candidates if i in vocabulary.contexts}
                    contexts = {ctx.id for ctx in vocabulary.contexts.values() if ctx.measure in measures_named
                                and ctx.term == vocabulary.entries[ctx.measure].label}
                return (len(contexts) == 1 and measure.kind == 'measure' and measure.mode == piece.operation
                        and measure.context.id in contexts)
            return named(measure, piece.candidates)
        def names(group):
            result = [p for p in group if (p.kind == 'name' and any(
                vocabulary.entries[i].kind in ('context', 'measure', 'entity') for i in p.candidates))
                or (not p.candidates and p.operation in ('current', 'as_reported'))]
            return [p for i, p in enumerate(result) if not (p.operation in ('current', 'as_reported')
                and i + 1 < len(result) and result[i + 1].kind == 'name'
                and text[p.span.end:result[i + 1].span.start].isspace())]
        pairs = []
        for index, piece in enumerate(ordered):
            if piece.operation != 'comparison':
                continue
            before, after = names(ordered[:index]), names(ordered[index + 1:])
            if piece.text.casefold() == 'compare':
                if len(after) < 2:
                    continue
                left, right = after[:2]
                between = text[left.span.end:right.span.start]
                if any(p.operation == 'comparison' and left.span.end <= p.span.start < right.span.start for p in ordered):
                    continue
                if re.search(r'\b(?:with|to|and)\b', between, re.I):
                    pairs.append((piece.span, left, right))
            elif before and after:
                pairs.append((piece.span, before[-1], after[0]))
        occurrence = next((pair for pair in pairs if pair[0] not in consumed), None)
        if occurrence is None:
            raise ValueError('comparison base and other must follow the question context or measure order')
        span, left, right = occurrence
        if not (supports(measures[comparison.left], left, right) and supports(measures[comparison.right], right, left)
                and not (supports(measures[comparison.right], left, right) and supports(measures[comparison.left], right, left))):
            raise ValueError('comparison base and other must follow the question context or measure order')
        consumed.add(span)
        return {identifier for identifier, piece in ((comparison.left, left), (comparison.right, right))
                if not piece.candidates and piece.operation in ('current', 'as_reported')}
    if comparison.kind in ('time_mode', 'previous_period', 'previous_year'):
        return
    times = sorted((p for p in pieces if p.span.turn == turn and (p.kind == 'time' or
                    (p.kind == 'literal' and re.fullmatch(r'FY\d{2,4}(?:-Q[1-4])?|\d{4}-(?:\d{2}(?:-\d{2})?|Q[1-4])', p.text, re.I)))),
                   key=lambda p: p.span.start)
    if not times:
        return
    from kge.semantics.planperiods import bounds

    def window(piece):
        return bounds(period_binding(piece, pieces, vocabulary), vocabulary.calendars)

    if len(times) < 2:
        raise ValueError('comparison requires grounded periods in question order')
    boundaries = [index for index in range(1, len(times)) if re.fullmatch(
        r'(?:versus|vs|with|compared (?:with|to)|against|compare)',
        text[times[index - 1].span.end:times[index].span.start].strip(), re.I)]
    if len(times) == 2 and not boundaries:
        boundaries = [1]
    if len(boundaries) != 1:
        raise ValueError('comparison requires grounded periods in question order')
    index = boundaries[0]
    first, second = times[index - 1], times[index]
    between = text[first.span.end:second.span.start].strip().casefold()
    prefix = text[:times[0].span.start].casefold()

    def group_window(group):
        if len(group) == 1:
            return window(group[0])
        if len(group) != 2 or not re.fullmatch(r'(?:through|to)', text[group[0].span.end:group[1].span.start].strip(), re.I):
            raise ValueError('comparison requires grounded periods in question order')
        return window(group[0])[0], window(group[1])[1]

    left, right = group_window(times[:index]), group_window(times[index:])
    if re.fullmatch(r'(?:to|through)', between) and re.search(r'\bfrom\s*$', prefix):
        base, other = right, left
    elif re.fullmatch(r'(?:versus|vs|with|compared (?:with|to)|against|compare)', between):
        base, other = left, right
    elif re.fullmatch(r'(?:and|to)', between) and re.search(r'\bcompare\b', prefix):
        base, other = left, right
    else:
        raise ValueError('comparison requires grounded periods in question order')
    if comparison.kind != 'explicit_period' or (time_bounds(plan.time, vocabulary.calendars),
                                                time_bounds(comparison.other, vocabulary.calendars)) != (base, other):
        raise ValueError('comparison base and other must follow the question period order')


def ratio_direction(derived, plan, text, turn, pieces, vocabulary):
    """Bind explicit ratio operands independently of the model's obligation assignments."""
    nodes = {node.id: node for node in plan.nodes()}
    owned = {span for obligation in plan.obligations if obligation.state == 'applied' and derived.id in obligation.targets
             for span in obligation.spans if span.turn == turn}

    def supports(identifier, piece):
        node = nodes.get(identifier)
        if node is None:
            return False
        if piece.kind == 'predicate':
            return any(piece.operation == getattr(predicate, 'op', predicate.kind)
                       and set(piece.candidates) & references(predicate, vocabulary)
                       and predicate_value_supported(predicate, piece.value, vocabulary)
                       for root in getattr(node, 'filters', ()) for predicate in predicate_nodes(root))
        if getattr(node, 'kind', None) == 'distinct_count':
            return piece.kind == 'name' and node.entity.id in piece.candidates
        return piece.kind == 'name' and bool(set(piece.candidates) & references(node, vocabulary))

    ordered = sorted((p for p in pieces if p.span.turn == turn), key=lambda p: p.span.start)
    bound = False
    for index, operator in enumerate(ordered):
        if operator.operation != 'ratio' or operator.span not in owned:
            continue
        if operator.text.casefold() == 'share':
            numerator = next((p for p in reversed(ordered[:index]) if p.kind == 'name'), None)
            denominator = next((p for p in ordered[index + 1:] if p.kind == 'name'), None)
            if (numerator is None or denominator is None
                    or not re.fullmatch(r"(?:\s+as\s+(?:a\s+)?|['’]s\s+)", text[numerator.span.end:operator.span.start], re.I)
                    or not re.fullmatch(r'\s+of\s+(?:the\s+)?', text[operator.span.end:denominator.span.start], re.I)):
                raise ValueError('ratio requires grounded operands in question order')
        elif re.match(r'\s+of\b', text[operator.span.end:], re.I):
            operands = ordered[index + 1:]
            numerator = next((p for p in operands if p.kind in ('name', 'predicate')), None)
            if numerator is None or not re.fullmatch(r'\s+of\s+(?:the\s+)?', text[operator.span.end:numerator.span.start], re.I):
                raise ValueError('ratio requires grounded operands in question order')
            operands = [p for p in operands if p.span.start > numerator.span.start]
            denominator = next((p for p in operands if p.kind in ('name', 'predicate')), None)
        else:
            operands = [p for p in ordered[:index] if p.kind in ('name', 'predicate')]
            numerator, denominator = operands[-2:] if len(operands) >= 2 else (None, None)
            if denominator is not None and text[denominator.span.end:operator.span.start].strip():
                denominator = None
        if numerator is None or denominator is None:
            raise ValueError('ratio requires grounded operands in question order')
        separator = text[numerator.span.end:denominator.span.start]
        if operator.text.casefold() != 'share' and not re.fullmatch(r'(?:\s+to\s+(?:the\s+)?(?:count\s+)?|-to-)', separator, re.I):
            raise ValueError('ratio requires grounded operands in question order')
        if not supports(derived.numerator, numerator) or not supports(derived.denominator, denominator):
            raise ValueError('ratio requires the first named operand as numerator and the second as denominator')
        if (derived.numerator != derived.denominator and supports(derived.denominator, numerator)
                and supports(derived.numerator, denominator)):
            raise ValueError('ratio operands do not identify a unique order')
        bound = True
    if not bound and any(p.span.turn == turn and p.operation == 'ratio' for p in pieces):
        raise ValueError('ratio requires grounded operands in question order')


def declared_value(vocabulary, column, value):
    for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
        params = row['params']
        if params.get('column') != column:
            continue
        if row['kind'] == 'value':
            if value in params.get('values', ()):
                return True
            if params.get('pattern') and re.fullmatch(re.escape(params.get('prefix') or '') + PATTERNS[params['pattern']], value):
                return True
        if row['kind'] == 'predicate' and params.get('value') == value:
            return True
    return False


def check_request(vocabulary, text, turn='turn'):
    pieces = ledger(text, turn, vocabulary)
    record_matches(vocabulary, pieces)
    if vocabulary.denial(text):
        return Decision('refuse', ('The requested read is not permitted. Contact the access administrator.',))
    units = {e.id for p in pieces for e in vocabulary.candidates(p.text) if e.kind == 'unit'}
    units.update(e.unit for p in pieces for e in vocabulary.candidates(p.text) if e.kind == 'entity' and e.unit)
    units.update(vocabulary.entries[i].unit for p in pieces if p.kind == 'predicate' for i in p.candidates
                 if vocabulary.entries[i].unit)
    if any(p.operation == 'membership' for p in pieces):
        if any(pair <= units for pair in vocabulary.no_join):
            return Decision('refuse', ('no-join policy blocks the requested relationship.',))
    return None
