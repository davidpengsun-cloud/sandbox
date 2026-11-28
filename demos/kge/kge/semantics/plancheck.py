"""The only constructor of executable plans: names, coverage, roles and policies."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import sqlglot
from sqlglot import exp
from kge.compiler.vocabulary import PATTERNS
from kge.ports import AUTHORED

from kge.semantics.coverage import coverage, ledger, names_in, name_supported, referenced_targets, references, predicate_value_supported, member_value, one_meaning, removal_spans, remaining_pieces, period_binding, canonical_calendars, counted_names, ranking_scope, output_aggregations
from kge.semantics.plan import QueryPlan, predicate_nodes
from kge.pipeline.textmatch import TEXT_MATCHES, text_value_problem
from kge.semantics.planperiods import GRAIN_ORDER, time_bounds, time_calendar, periods, shifted_mapping
from kge.semantics.coverage import amount_columns, comparison_grains, period_candidates
from kge.pipeline.trace import graph_read
from kge.pipeline.sqlguard import temporal_rule
from kge.semantics.query import bare
from kge.semantics.planvocabulary import aggregate_expression, aggregation_of, output_spec, selected_count_metrics, count_filter_matches, formula_metrics, metric_identifier

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

    def retimed(self, time):
        """The same checked plan over another time window: only the compiler's re-anchoring of a current to-date window uses it."""
        return CheckedPlan(_TOKEN, self.plan.model_copy(update={'time': time}), self.vocabulary, self.coverage, self.units)


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


def validate_predicate(node, vocab, ctx, contexts, count_filters=()):
    if node.kind in ('all', 'any', 'not'):
        phase(node)
        for child in node.children if node.kind != 'not' else (node.child,):
            validate_predicate(child, vocab, ctx, contexts, count_filters)
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
    if getattr(node, 'op', None) in TEXT_MATCHES:
        if operand.kind != 'column' or node.value.kind != 'text' or text_value_problem(node.value.value):
            raise ValueError('text matching requires a declared text column and literal value')
        column = vocab.entries[operand.name.id].column
        if not any(row['kind'] == 'predicate' and row['params'].get('column') == column
                   and row['params'].get('op') == node.op and row['params'].get('value') == node.value.value
                   for row in getattr(vocab.semantics.d, 'VOCABULARY', ())) and not any(
                       count_filter_matches(node, declaration, vocab, ctx) for declaration in count_filters):
            raise ValueError('undeclared text matching operator, column or value')
    if getattr(node, 'magnitude', False) and (operand.kind != 'column' or operand.path
                                              or vocab.entries[operand.name.id].column not in amount_columns(vocab, {ctx.fact})):
        raise ValueError('a size threshold needs a declared amount column of its context')
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


def cohort_counts(plan, pieces, vocabulary, requested_aggregations, turn):
    """A named cohort step retains the requested count of its declared population. A count an earlier turn asked for was
    checked against that turn's own words, so a follow-up keeps it without reading them again."""
    def owns(measure, operation):
        for piece in pieces:
            if piece.operation != operation or not any(
                    obligation.state == 'applied' and measure.id in obligation.targets and piece.span in obligation.spans
                    for obligation in plan.obligations):
                continue
            if operation == 'count':
                return piece.span.turn != turn or 'count' in requested_aggregations.get(measure.context.id, ())
            following = [head for head in pieces if head.kind == 'name' and head.span.turn == piece.span.turn
                         and head.span.start >= piece.span.end and set(head.candidates) & vocabulary.contexts.keys()]
            if following and measure.context.id in min(following, key=lambda head: head.span.start).candidates:
                return True
        return False
    named = {identifier for piece in pieces if piece.kind == 'name' for identifier in piece.candidates}
    counts = [measure for measure in plan.measures if measure.kind == 'distinct_count'
              and measure.context.id.startswith('cohort:') and measure.context.id in named
              and vocabulary.contexts[measure.context.id].aggregation == 'count'
              and requested_aggregations.get(measure.context.id, set()) <= {'count'}
              and sum(other.kind == 'distinct_count' and other.context.id == measure.context.id for other in plan.measures) == 1]
    requested = [measure for measure in counts if owns(measure, 'count')]
    return {measure.id for measure in counts if owns(measure, 'membership') and any(
        measure.entity.id == anchor.entity.id and measure.mode == anchor.mode and measure.filters == anchor.filters
        and any(selector.id == anchor.context.id and selector.mode == 'include'
                for selector in vocabulary.contexts[measure.context.id].selectors)
        for anchor in requested)}


def check_plan(plan, vocabulary, text, turn='turn', inherited=(), removed=None, *, words=True):
    """The plan's checks. Without words (a model's plan in AI-Generated mode) the plan alone is checked: its names in
    the vocabulary, declared measures and contexts, allowed joins and a valid period, never the question's words (no
    unknown-piece refusal, no evidence, coverage or to-date wording check, no named-member guard). A deletion's evidence
    is still checked against the follow-up's words, as the conversation history checks it on the next turn."""
    record_matches(vocabulary, ledger(text, turn, vocabulary))
    if vocabulary.denial(text):
        return Decision('refuse', ('The requested read is not permitted. Contact the access administrator.',))
    try:
        spans = removal_spans(plan, text, turn, vocabulary, removed or {})
    except ValueError as exc:
        return Decision('abstain', (str(exc),))
    if not words:
        text, inherited, spans = '', (), ()
    pieces = (*remaining_pieces(ledger(text, turn, vocabulary), spans), *inherited)
    plan = canonical_calendars(plan, pieces, vocabulary)
    lexical_names = {piece.span: piece.candidates for piece in ledger(text, turn, vocabulary)}
    lexical_ids = {identifier for piece in pieces if piece.kind == 'name' for identifier in piece.candidates}
    lexical_facts = {context.fact for context in vocabulary.contexts.values() if context.id in lexical_ids
                     or context.measure in lexical_ids and context.term.casefold() == vocabulary.entries[context.measure].label.casefold()}
    def candidates(piece):
        identifiers = piece.candidates if piece.span.turn != turn else lexical_names.get(piece.span, ())
        if piece.kind == 'predicate':
            at_fact = tuple(identifier for identifier in identifiers
                            if vocabulary.entries[identifier].kind == 'column'
                            and vocabulary.entries[identifier].column.rsplit('.', 1)[0] in lexical_facts)
            identifiers = at_fact or identifiers
        return tuple(vocabulary.entries[identifier] for identifier in identifiers)
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
            available = set(period_candidates((entry.id for entry in permitted), vocabulary,
                plan.time.kind != 'all_available' or any(d.kind == 'time' for d in plan.dimensions)))
            permitted = tuple(entry for entry in permitted if entry.id in available)
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
        for span in name.evidence if words else ():
            if not any(p.span == span and name_supported(name.id, p.candidates, vocabulary) for p in pieces):
                return Decision('abstain', ('A name has unrelated evidence.',))
    named_contexts = {c for p in pieces if p.kind == 'name' for c in p.candidates if c in vocabulary.contexts}
    for measure in plan.measures:
        if measure.kind != 'measure' or not words:
            continue
        for span in measure.measure.evidence:
            if not any(p.span == span and name_supported(measure.measure.id, p.candidates, vocabulary,
                       measure.context.id, named_contexts) for p in pieces):
                return Decision('abstain', ('A name has unrelated evidence.',))
    deferred_count_problems = []
    try:
        named_formulas = {i for p in pieces for i in p.candidates if i in vocabulary.derived_measures}
        if any(sum(getattr(d, 'declaration', None) is not None and d.declaration.id == i for d in plan.derived) != 1
               for i in named_formulas):
            raise ValueError('A named derived measure requires its declared formula output.')
        default_formula_inputs = set()
        outputs = {measure.id: measure for measure in plan.measures}
        for derived in plan.derived:
            declared = getattr(derived, 'declaration', None)
            if declared is None or declared.id not in named_formulas:
                continue
            row = vocabulary.derived_measures[declared.id]
            operands = (('numerator', derived.input),) if derived.kind == 'share' else (
                ('numerator', derived.numerator), ('denominator', derived.denominator))
            for operand_role, identifier in operands:
                output = outputs.get(identifier)
                if output is None or output.kind != 'measure':
                    continue
                context = vocabulary.contexts.get(output.context.id)
                if (context is not None and output.measure.id == row['formula'][operand_role]
                        and aggregation_of(output, context) == context.aggregation
                        and row.get('contexts', {}).get(operand_role, context.id) == context.id):
                    default_formula_inputs.add(identifier)
        if any(o.direction == 'both' for o in plan.order) and (
                len(plan.order) != 1 or plan.limit is None or not plan.dimensions
                or plan.order[0].output not in plan.outputs()):
            raise ValueError('both ends require one output ordering, a limit per end and a breakdown')
        cal = time_calendar(plan.time)
        if cal not in vocabulary.calendars:
            raise ValueError('missing declared calendar')
        time_bounds(plan.time, vocabulary.calendars)
        from kge.semantics.coverage import ytd_problem
        if words and (problem := ytd_problem(plan, text, vocabulary)):
            raise ValueError(problem)
        contexts, membership_contexts = set(), set()
        implicit_count_filters = set()
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
            requested_aggregations = output_aggregations(pieces, text, vocabulary).get(ctx.id, set())
            if m.kind == 'measure' and m.id not in default_formula_inputs and requested_aggregations and aggregation_of(m, ctx) not in requested_aggregations:
                raise ValueError('The output must use its requested aggregation.')
            if m.kind == 'measure' and m.aggregation is not None:
                allowed = vocabulary.measures[ctx.measure].aggregations or (ctx.aggregation,)
                if m.aggregation not in allowed:
                    raise ValueError(f'The measure does not declare {m.aggregation} aggregation.')
                aggregate_expression(ctx, m.aggregation)
                if words and m.aggregation != ctx.aggregation and not any(p.operation == m.aggregation or (
                        m.aggregation == 'sum' and p.operation == 'aggregate') for p in pieces):
                    raise ValueError('An aggregation change needs the question to request it.')
            binding = vocabulary.periods.get(ctx.fact)
            if plan.time.kind != 'all_available' or any(d.kind == 'time' for d in plan.dimensions):
                if not binding or (binding['calendar'] != cal and plan.time.kind == 'latest_released'):
                    raise ValueError('missing compatible period binding')
            if m.kind == 'distinct_count':
                declarations = selected_count_metrics(vocabulary, ctx, m.entity.id,
                    m.declaration.id if m.declaration else None, m.filters)
                if len(declarations) != 1:
                    deferred_count_problems.append('No unambiguous distinct-count metric is declared for this entity and population.')
                declaration = declarations[0] if len(declarations) == 1 else {}
                for predicate in declaration.get('filters', ()):
                    matches = [f for f in m.filters if count_filter_matches(f, predicate, vocabulary, ctx)]
                    if len(matches) != 1:
                        raise ValueError('The count metric requires its declared local filters.')
                    implicit_count_filters.add(matches[0].id)
                named_counts = counted_names([p for p in pieces if p.span.turn == turn], text, vocabulary)
                if named_counts and m.entity.id not in named_counts:
                    raise ValueError('count must use the requested entity and its complete key')
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
                order = GRAIN_ORDER
                from kge.semantics.calendarattributes import observation_grains
                observation_grain = {'quarter': 'calendar_quarter', 'quarter_end': 'calendar_quarter', 'as_of': 'day'}.get(ctx.grain, ctx.grain)
                if any(order.get(observation_grain, 0) > order[grain] for grain in observation_grains(text, vocabulary)):
                    raise ValueError('requested calendar attribute is finer than the declared observations')
                if any(d.kind == 'time' and order.get(binding['kind'], 0) > order[d.grain] for d in plan.dimensions):
                    raise ValueError('requested time grain is finer than the declared observations')
                observed = {'quarter': 'calendar_quarter', 'quarter_end': 'calendar_quarter'}.get(ctx.grain, ctx.grain)
                if any(d.kind == 'time' and d.grain in ('day', 'week') and order.get(observed, 0) > order[d.grain] for d in plan.dimensions):
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
            for local, predicates in ((False, plan.filters), (True, m.filters)):
                for pred in predicates:
                    if local and phase(pred) != 'row':
                        raise ValueError('local predicates must narrow rows')
                    count_filters = declaration.get('filters', ()) if local and m.kind == 'distinct_count' else ()
                    validate_predicate(pred, vocabulary, ctx, membership_contexts, count_filters)
        requested_outputs = output_aggregations(pieces, text, vocabulary)
        for context, aggregations in requested_outputs.items():
            if len(aggregations) > 1:
                actual = [aggregation_of(m, vocabulary.contexts[context]) for m in plan.measures
                          if m.kind == 'measure' and m.context.id == context
                          and (m.id not in default_formula_inputs or aggregation_of(m, vocabulary.contexts[context]) in aggregations)]
                if set(actual) != aggregations or len(actual) != len(aggregations):
                    raise ValueError('Each requested aggregation needs its own output.')
        for d in plan.dimensions:
            if d.kind == 'time':
                if d.calendar.id not in vocabulary.calendars:
                    raise ValueError('missing declared dimension calendar')
                continue
            if d.kind == 'band':
                declaration = vocabulary.entries.get(d.band.id)
                if declaration is None or declaration.kind != 'band' or any(
                        b.column.id != declaration.column for b in d.bindings):
                    raise ValueError('band dimension requires its exact declared column')
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
        if words:
            partition_columns, partition_grains = ranking_scope(pieces)
            requested_partitions = {d.id for d in plan.dimensions if (d.kind == 'time' and d.grain in partition_grains)
                                    or (d.kind != 'time' and {b.column.id for b in d.bindings} <= partition_columns)}
        else:
            requested_partitions = {p for o in plan.order for p in o.partition_by}
            if not requested_partitions <= {d.id for d in plan.dimensions}:
                raise ValueError('ranking partitions must be dimensions of the plan')
        if any(set(o.partition_by) != requested_partitions for o in plan.order) or (requested_partitions and not plan.order):
            raise ValueError('ranking must partition by every requested partition dimension')
        if requested_partitions and (len(plan.order) != 1 or len(plan.dimensions) - len(requested_partitions) != 1):
            raise ValueError('partitioned ranking requires one ranked breakdown and one ordering')
        units = tuple(sorted({vocabulary.contexts[c].unit for c in contexts | membership_contexts}))
        for i, a in enumerate(units):
            for b in units[i + 1:]:
                independent = (not plan.dimensions and not plan.comparisons and not plan.derived
                               and len({vocabulary.contexts[m.context.id].term for m in plan.measures}) == 1
                               and not membership_contexts)
                if frozenset((a, b)) in vocabulary.no_join and (plan.layout != 'separate' or plan.derived) and not independent:
                    raise PlanDenied('no-join policy blocks alignment or arithmetic')
        specs = {m.id: (replace(vocabulary.measures[vocabulary.contexts[m.context.id].measure], currency=None, additive=False)
                        if m.kind == 'distinct_count' else output_spec(m, vocabulary.contexts[m.context.id], vocabulary)) for m in plan.measures}
        output_specs = dict(specs)
        change_pairs = set()
        comparison_occurrences = set()
        implicit_measures = implicit_count_filters | {m.id for m in plan.measures if any(
            c.kind == 'contexts' and m.id in (c.left, c.right) for c in plan.comparisons) and any(
                p.span.turn != turn and not p.candidates and p.operation == m.mode and any(
                    o.state == 'applied' and p.span in o.spans and {m.id, m.id + '/mode'} & set(o.targets)
                    for o in plan.obligations) for p in pieces)}
        implicit_measures.update(cohort_counts(plan, pieces, vocabulary, requested_outputs, turn))
        for c in plan.comparisons:
            if c.kind == 'previous_period' and (named_grains := comparison_grains(pieces)):
                dims = [d for d in plan.dimensions if d.kind == 'time']
                period = getattr(plan.time, 'period', getattr(plan.time, 'first', getattr(plan.time, 'through', None)))
                grain = min(dims, key=lambda d: GRAIN_ORDER[d.grain]).grain if dims else getattr(period, 'kind', 'month')
                if any(GRAIN_ORDER[named] != GRAIN_ORDER[grain] for named in named_grains):
                    raise ValueError('comparison grain disagrees with the question; give a matching time breakdown')
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
                declared_formula = derived.declaration is not None
                if derived.kind == 'ratio' and not declared_formula:
                    sources = {m.id: m for m in plan.measures}
                    declarations = formula_metrics(vocabulary, 'ratio', *(metric_identifier(sources[i], vocabulary) if i in sources else None
                                                     for i in (derived.numerator, derived.denominator)), contexts={
                        operand_role: sources[i].context.id for operand_role, i in (('numerator', derived.numerator), ('denominator', derived.denominator)) if i in sources})
                    if len(declarations) != 1:
                        raise ValueError('No unambiguous ratio metric is declared for these inputs.')
                    if any(getattr(sources[i], 'aggregation', None) not in (None, vocabulary.contexts[sources[i].context.id].aggregation)
                           for i in (derived.numerator, derived.denominator)):
                        raise ValueError('The ratio metric requires its declared input aggregations.')
                if declared_formula:
                    declaration = vocabulary.derived_measures.get(derived.declaration.id)
                    formula = declaration['formula'] if declaration else {}
                    inputs = {m.id: m for m in plan.measures}
                    if (formula.get('kind') != derived.kind or any(
                            i not in inputs or metric_identifier(inputs[i], vocabulary) != formula.get(operand_role)
                            or getattr(inputs[i], 'aggregation', None) not in (None, vocabulary.contexts[inputs[i].context.id].aggregation)
                            for operand_role, i in (('numerator', derived.numerator), ('denominator', derived.denominator)))):
                        raise ValueError('The derived measure must use its declared formula and input aggregations.')
                    for operand_role, operand in (('numerator', derived.numerator), ('denominator', derived.denominator)):
                        metric = metric_identifier(inputs[operand], vocabulary)
                        bound = declaration.get('contexts', {}).get(operand_role)
                        if bound is not None:
                            choices = [vocabulary.contexts[bound]] if bound in vocabulary.contexts else []
                        elif metric in vocabulary.count_metrics:
                            count = vocabulary.count_metrics[metric]
                            choices = [vocabulary.contexts[count.get('context') or 'context:count:' + count['entity']]]
                        else:
                            choices = [c for c in vocabulary.contexts.values() if c.measure == metric]
                        choices = [c for c in choices if c.id in named_contexts] or choices if bound is None else choices
                        if len(choices) != 1 or choices[0].id != inputs[operand].context.id:
                            raise ValueError('The derived measure input needs an explicit context.')
                    implicit_measures.update((derived.numerator, derived.denominator))
                a, b = output_specs.get(derived.numerator), output_specs.get(derived.denominator)
                if a is None or b is None or a.population != b.population or (a.currency != b.currency
                        and not (derived.kind == 'ratio' and b.currency is None)):
                    raise ValueError('incompatible currencies or populations')
                if not declared_formula and derived.kind in ('percent_change', 'difference') and a.additive != b.additive:
                    raise ValueError('incompatible observation grains')
                if not declared_formula and derived.kind in ('percent_change', 'difference') and (derived.numerator, derived.denominator) not in change_pairs:
                    raise ValueError(derived.kind.replace('_', ' ') + ' requires the comparison base as numerator and its other output as denominator')
                if derived.kind == 'ratio' and not declared_formula:
                    ratio_direction(derived, plan, text, turn, pieces, vocabulary)
                output_specs[derived.id] = replace(a, currency=a.currency if derived.kind == 'difference' or (derived.kind == 'ratio' and b.currency is None) else None, additive=False)
            else:
                spec = output_specs.get(derived.input)
                if spec is None:
                    raise ValueError('unresolved derived order')
                if derived.kind in ('running_total', 'moving_average'):
                    dimension = next(d for d in plan.dimensions if d.id == derived.order_by)
                    if dimension.kind != 'time' or derived.kind == 'running_total' and not spec.additive:
                        raise ValueError('running total requires additive observations and time order' if derived.kind == 'running_total'
                                         else 'moving average requires time order')
                    if set(derived.partition_by) != {d.id for d in plan.dimensions if d.id != derived.order_by}:
                        raise ValueError(derived.kind.replace('_', ' ') + ' must restart for every other requested breakdown')
                    if derived.kind == 'moving_average':
                        from kge.semantics.planvocabulary import moving_average_lengths
                        if derived.length not in moving_average_lengths(vocabulary, spec.id):
                            raise ValueError('moving average length is not declared for this measure')
                        declared = {p.value for p in pieces if p.operation == 'moving_average' and isinstance(p.value, tuple)}
                        if words and declared != {(derived.length, dimension.grain)}:
                            raise ValueError('moving average must use the requested length and time grain')
                if derived.kind in ('share', 'running_total', 'moving_average'):
                    from kge.semantics.coverage import shape_contexts, shape_inputs
                    requested = shape_contexts(derived.kind, pieces, text, vocabulary)
                    inputs = shape_inputs(derived.kind, pieces, text, vocabulary,
                                          [(m.id, m.context.id, m.kind) for m in plan.measures])
                    if requested and derived.input not in inputs:
                        raise ValueError('derived input must use its named measure')
                if derived.kind == 'share' and any(phase(p) != 'row' for p in plan.filters if p.id in derived.remove_filters):
                    raise ValueError('denominator scope can remove only row filters')
                if derived.kind == 'share':
                    source = next((m for m in plan.measures if m.id == derived.input), None)
                    metric = metric_identifier(source, vocabulary) if source else None
                    declarations = formula_metrics(vocabulary, 'share', metric, metric,
                                                   contexts=dict.fromkeys(('numerator', 'denominator'), source.context.id) if source else {})
                    if len(declarations) != 1 or (derived.declaration is not None and derived.declaration.id != declarations[0]):
                        raise ValueError('No unambiguous share metric is declared for this input.')
                    if source.kind == 'measure' and aggregation_of(source, vocabulary.contexts[source.context.id]) != vocabulary.contexts[source.context.id].aggregation:
                        raise ValueError('The share metric requires its declared input aggregation.')
                    if derived.declaration is not None:
                        declaration = vocabulary.derived_measures[derived.declaration.id]
                        bound = declaration.get('contexts', {}).get('numerator')
                        if bound is not None:
                            choices = [vocabulary.contexts[bound]] if bound in vocabulary.contexts else []
                        elif metric in vocabulary.count_metrics:
                            count = vocabulary.count_metrics[metric]
                            choices = [vocabulary.contexts[count.get('context') or 'context:count:' + count['entity']]]
                        else:
                            choices = [c for c in vocabulary.contexts.values() if c.measure == metric]
                        choices = [c for c in choices if c.id in named_contexts] or choices if bound is None else choices
                        if len(choices) != 1 or choices[0].id != source.context.id:
                            raise ValueError('The share measure input needs an explicit context.')
                        implicit_measures.add(derived.input)
                    if words and derived.remove_filters and not share_removal_requested(derived, plan, pieces, vocabulary):
                        raise ValueError('share denominator must retain every named filter unless a whole-population share is requested')
                    total = any(p.operation == 'share' and ('total' in p.text.casefold().split()
                                or p.value != 'whole_population' and re.match(r'\s+of\b', text[p.span.end:], re.I)) for p in pieces)
                    if ((plan.dimensions and not derived.remove_dimensions)
                            or (total and set(derived.remove_dimensions) != {d.id for d in plan.dimensions})):
                        raise ValueError('share denominator must remove the requested breakdown dimensions')
                output_specs[derived.id] = (replace(spec, currency=None, additive=False) if derived.kind == 'share' else
                                            replace(spec, additive=False) if derived.kind == 'moving_average' else spec)
        from kge.semantics.coverage import shape_contexts, shape_inputs
        for kind in ('share', 'running_total', 'moving_average'):
            requested = shape_contexts(kind, pieces, text, vocabulary)
            inputs = shape_inputs(kind, pieces, text, vocabulary,
                                  [(m.id, m.context.id, m.kind) for m in plan.measures])
            actual = [d.input for d in plan.derived if d.kind == kind]
            if requested and (set(actual) != set(inputs) or len(actual) != len(inputs)):
                raise ValueError('each requested measure needs exactly one ' + kind.replace('_', ' '))
        mode_comparisons = [c for c in plan.comparisons if c.kind == 'time_mode']
        if mode_comparisons:
            requested = shape_contexts('comparison', pieces, text, vocabulary)
            inputs = {m.id for m in plan.measures if m.context.id in requested}
            if inputs and set().union(*(set(c.inputs) for c in mode_comparisons)) != inputs:
                raise ValueError('time mode comparison must include every requested measure')
            for kind in ('difference', 'percent_change'):
                if not any(p.operation == kind for p in pieces):
                    continue
                pairs = {(f'{c.id}/{m}/base', f'{c.id}/{m}/other') for c in mode_comparisons for m in c.inputs}
                actual = {(d.numerator, d.denominator) for d in plan.derived if d.kind == kind}
                if not pairs <= actual:
                    raise ValueError('each compared measure needs its requested ' + kind.replace('_', ' '))
    except PlanDenied as exc:
        return Decision('refuse', (str(exc),))
    except (ValueError, KeyError) as exc:
        return Decision('abstain', (str(exc),))
    missing = coverage(plan, text, turn, vocabulary, inherited, removed, implicit_measures) if words else ()
    if missing:
        return Decision('abstain', missing)
    if deferred_count_problems:
        return Decision('abstain', tuple(dict.fromkeys(deferred_count_problems)))
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
                    from kge.semantics.coverage import unqualified_contexts
                    contexts = unqualified_contexts(other, vocabulary)
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
    if comparison.kind == 'time_mode':
        ordered = sorted((p for p in pieces if p.span.turn == turn), key=lambda p: p.span.start)
        if not any(p.operation == 'comparison' for p in ordered):
            return
        occurrence = None
        for index, piece in enumerate(ordered):
            if piece.operation != 'comparison' or piece.span in consumed:
                continue
            before = [p for p in ordered[:index] if p.operation in ('current', 'as_reported')]
            after = [p for p in ordered[index + 1:] if p.operation in ('current', 'as_reported')]
            modes = after[:2] if piece.text.casefold() == 'compare' else [*before[-1:], *after[:1]]
            if len(modes) == 2:
                occurrence = piece.span, tuple(p.operation for p in modes)
                break
        if (occurrence is None or comparison.base == comparison.other
                or occurrence[1] != (comparison.base, comparison.other)):
            raise ValueError('comparison base and other must follow the question time mode order')
        consumed.add(occurrence[0])
        return
    if comparison.kind in ('previous_period', 'previous_year'):
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
        r'(?:versus|vs|with|compare(?:d)? (?:with|to)|against|compare)',
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
    elif re.fullmatch(r'(?:versus|vs|with|compare(?:d)? (?:with|to)|against|compare)', between):
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
        if row['kind'] == 'count_metric' and any(p['column'] == column and p.get('value') == value for p in params.get('filters', ())):
            return True
        if params.get('column') != column:
            continue
        if row['kind'] == 'value':
            if value in params.get('values', ()):
                return True
            if params.get('pattern') and re.fullmatch(re.escape(params.get('prefix') or '') + PATTERNS[params['pattern']], value):
                return True
        if row['kind'] == 'predicate' and (params.get('value') == value or value in params.get('values', ())):
            return True
    return False


def check_request(vocabulary, text, turn='turn'):
    pieces = ledger(text, turn, vocabulary)
    record_matches(vocabulary, pieces)
    if vocabulary.denial(text):
        return Decision('refuse', ('The requested read is not permitted. Contact the access administrator.',))
    from kge.semantics.filtergrammar import boolean_groups
    _, problem = boolean_groups(pieces, text)
    if problem:
        return Decision('clarify', (problem,))
    units = {e.id for p in pieces for e in vocabulary.candidates(p.text) if e.kind == 'unit'}
    units.update(e.unit for p in pieces for e in vocabulary.candidates(p.text) if e.kind == 'entity' and e.unit)
    units.update(vocabulary.entries[i].unit for p in pieces if p.kind == 'predicate' for i in p.candidates
                 if vocabulary.entries[i].unit)
    if any(p.operation == 'membership' for p in pieces):
        if any(pair <= units for pair in vocabulary.no_join):
            return Decision('refuse', ('no-join policy blocks the requested relationship.',))
    return None
