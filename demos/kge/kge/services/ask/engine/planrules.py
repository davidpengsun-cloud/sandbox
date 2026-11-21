"""The bounded rules interpreter emits data, never executable bypasses."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from kge.semantics.coverage import filter_scope, ledger, references, referenced_targets, operations, context_names, one_meaning, typed_scalar, predicate_supported, literal_supported, counted_names, output_aggregation_positions, count_population_contexts, aggregation_owner
from kge.semantics.filtergrammar import boolean_groups, leaves
from kge.semantics.plan import Abstain, Binding, Clarify, Name, Propose, QueryPlan, Span
from kge.semantics.planhistory import clarified_vocabulary, nodes, semantic
from kge.semantics.vocabulary import compiled as lexical_vocabulary, filter_value
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary, value_entries, count_metrics, selected_count_metrics, count_filter_path, predicate_contexts
from kge.semantics.query import Context, Rule, Semantics, NUMERIC, bare, snapshot_grain
from kge.services.ask.engine.question import WithoutLists, parse, restricted_asked, restricted_named, plural_forms, CURRENCY_NAMES, CURRENCY_SYMBOLS, UNPLACED_RANGE
from kge.services.ask.engine import binding, planshapes


def name(identifier):
    return {'id': identifier, 'evidence': []}


def bound_domain(domain, bindings):
    """A context's local roles replace canonical columns before parsing or certification."""
    domain = copy.copy(domain)
    domain.GROUP_BY = {p: bindings.get(c, c) for p, c in domain.GROUP_BY.items()}
    domain.VOCABULARY = [row for row in getattr(domain, 'VOCABULARY', ()) if row['params'].get('column') not in bindings]
    words = lexical_vocabulary(domain.VOCABULARY)
    domain.FILTERS, domain.FILTER_COLUMNS = words.filters, words.columns
    return domain


def scoped_predicate(predicate, output, path):
    """Copy a complete predicate subtree to one output's declared role."""
    predicate = copy.deepcopy(predicate)
    def scope(node):
        node['id'] += '-' + output
        if 'left' in node:
            node['left']['path'] = [name(p) for p in path]
        for child in node.get('children', ()):
            scope(child)
        if 'child' in node:
            scope(node['child'])
    scope(predicate)
    return predicate


def join_filter_groups(data, pieces, groups, vocab):
    """Build the question's boolean groups over filters that share an output scope."""
    from kge.semantics.filtergrammar import exclusion
    plan = QueryPlan.model_validate(data)
    nodes = {node.id: node for node in plan.nodes()}
    predicates = [p for p in pieces if p.kind == 'predicate']
    for scope, container in enumerate([data, *data['measures']]):
        for group_index, tree in enumerate(groups):
            indexes = leaves(tree)
            if len(indexes) < 2:
                continue
            matched = {index: next((root for root in container['filters']
                       if root['id'] in nodes and (nodes[root['id']].kind not in ('all', 'any') or exclusion(nodes[root['id']]) is not None)
                       and predicate_supported(predicates[index], nodes[root['id']], vocab, plan)), None) for index in indexes}
            if any(root is None for root in matched.values()) or len({root['id'] for root in matched.values()}) != len(indexes):
                continue
            serial = 0
            def build(branch):
                nonlocal serial
                if isinstance(branch, int):
                    return matched[branch]
                serial += 1
                return {'kind': branch[0], 'id': f'filters-{scope}-{group_index}-{serial}',
                        'children': [build(child) for child in branch[1]], 'evidence': []}
            combined = build(tree)
            consumed = {root['id'] for root in container['filters'] if root['id'] in nodes and any(
                predicate_supported(predicates[index], nodes[root['id']], vocab, plan) for index in indexes)}
            container['filters'] = [root for root in container['filters'] if root['id'] not in consumed] + [combined]


def size_filters(data, pieces, vocab):
    """Each size threshold the ledger read (kge/semantics/coverage.py, magnitude_filters) narrows the rows every output
    reads, on the one amount column all the outputs' contexts declare: the measure and the count then share it."""
    from kge.semantics.coverage import amount_columns
    facts = {vocab.contexts[m['context']['id']].fact for m in data['measures'] if m['context']['id'] in vocab.contexts}
    declared = amount_columns(vocab, facts) if len(facts) == 1 else set()
    for n, piece in enumerate(p for p in pieces if p.kind == 'predicate' and (p.operation or '').startswith('magnitude_')):
        columns = list({vocab.entries[i].column: i for i in piece.candidates if vocab.entries[i].column in declared}.values())
        if not columns:
            return Abstain(kind='abstain', spans=(), missing=('A size threshold needs one declared amount column of every requested measure.',))
        if len(columns) > 1:
            return Clarify(kind='clarify', spans=(piece.span,), candidates=tuple(Name(id=i, evidence=(piece.span,)) for i in columns))
        scalar = typed_scalar(piece.value, vocab.entries[columns[0]].column, vocab)
        if scalar is None or scalar['kind'] not in ('integer', 'decimal'):
            return Abstain(kind='abstain', spans=(), missing=('A size threshold needs a number its amount column can hold.',))
        data['filters'].append({'kind': 'compare', 'id': 'size-' + str(n), 'left': {'kind': 'column', 'name': name(columns[0]), 'path': []},
                                'op': piece.operation.removeprefix('magnitude_'), 'value': scalar, 'magnitude': True, 'evidence': []})
    return None


def output_filters(data, pieces, text):
    """A numeric comparison immediately after a named output filters that output."""
    from kge.semantics.coverage import separate_filter_parts
    if data['comparisons'] or data['derived']:
        return
    for index, piece in enumerate(pieces):
        if piece.kind != 'operator' or piece.operation not in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'):
            continue
        if separate_filter_parts(piece, pieces, text):
            continue
        before_amount = index + 1 < len(pieces) and pieces[index + 1].kind == 'literal' and not text[piece.span.end:pieces[index + 1].span.start].strip()
        after_amount = index > 0 and pieces[index - 1].kind == 'literal' and not text[pieces[index - 1].span.end:piece.span.start].strip()
        if not (before_amount or after_amount):
            continue
        amount = pieces[index + 1] if before_amount else pieces[index - 1]
        before = next((p for p in reversed(pieces[:index if before_amount else index - 1]) if p.kind in ('name', 'predicate')), None)
        if before is None or before.kind != 'name':
            continue
        outputs = [m for m in data['measures'] if m['context']['id'] in before.candidates
                   or m.get('measure', {}).get('id') in before.candidates or m.get('entity', {}).get('id') in before.candidates]
        if len(outputs) != 1:
            continue
        try:
            value = Decimal(str(amount.value if amount.value is not None else amount.text))
        except ArithmeticError:
            continue
        scalar = {'kind': 'integer', 'value': int(value)} if value == int(value) else {'kind': 'decimal', 'value': format(value, 'f')}
        data['filters'].append({'kind': 'compare', 'id': 'threshold-' + str(index), 'left': {'kind': 'output', 'id': outputs[0]['id']},
                                'op': piece.operation, 'value': scalar, 'evidence': []})


def shadowed_filter(req, qualified, op, value, pieces, domain, *, names_only=False, predicate_exact=False,
                    canonical_domain=None, name_shadowing=True):
    """Canonical lexical meanings replace stale parser alternatives, retaining current ambiguities."""
    from kge.services.ask.engine.question import normalise_question
    text = normalise_question(req.question)
    operations = {'=': 'eq', '= TRUE': 'eq', '<>': 'ne', '!=': 'ne', '<': 'lt', '<=': 'le', '>': 'gt', '>=': 'ge',
                  'IS NULL': 'null', 'IS NOT NULL': 'not_null', 'IN': 'in', 'NOT IN': 'not_in',
                  'CONTAINS': 'contains', 'STARTS WITH': 'starts_with'}
    operation = operations.get(op, op)
    scalar = True if op == '= TRUE' else value
    predicates = [p for p in pieces if name_shadowing and p.kind == 'name' or not names_only and p.kind == 'predicate']
    def matches(declarations):
        return [(match, group) for (pattern, _, operator, group, literal), column in zip(
                    getattr(declarations, 'FILTERS', ()), getattr(declarations, 'FILTER_COLUMNS', ()))
                if column == qualified and operations.get(operator, operator) == operation
                for match in pattern.finditer(text if group else text.casefold())
                if (True if operator == '= TRUE' else filter_value(match, group, literal)) == scalar]
    raw = matches(domain)
    current = {match.span() for match, _ in matches(canonical_domain)} if canonical_domain is not None else None
    def covers(piece, match, group):
        enclosed = piece.span.start <= match.start() and match.end() <= piece.span.end
        if piece.kind == 'name' or not predicate_exact:
            return enclosed
        same_span = (piece.span.start, piece.span.end) == match.span()
        if same_span:
            return (qualified in piece.candidates and piece.value == scalar and piece.operation == operation
                    or current is not None and match.span() not in current)
        return group is not None and match.span(group) == match.span() and enclosed
    def straddled(match):
        """A reading made of the tail of one stated predicate and the head of the next ("<value> region <code> accounts"
        read as region <code>) belongs to neither: the adjoining predicates own every word of it."""
        if predicate_exact:
            return False
        touching = sorted((p for p in pieces if p.kind == 'predicate' and p.span.start < match.end() and match.start() < p.span.end),
                          key=lambda p: p.span.start)
        return (len(touching) >= 2 and touching[0].span.start <= match.start() and match.end() <= touching[-1].span.end
                and all(left.span.end <= right.span.start and not text[left.span.end:right.span.start].strip()
                        for left, right in zip(touching, touching[1:])))
    return bool(raw) and all(any(covers(piece, match, group) for piece in predicates) or straddled(match) for match, group in raw)


def compared_member(qualified, op, value, pieces):
    """The parser read a declared member alone as its column's equality, but the question compares the column with it
    ("category is below B"): the comparison piece owns the member, so the equality is not a filter of its own. An
    equality the question also states as its own piece stays."""
    if op != '=':
        return False
    compared = [p for p in pieces if p.kind == 'predicate' and qualified in p.candidates and p.value == value]
    return (any(p.operation in ('ne', 'lt', 'le', 'gt', 'ge') and p.text.endswith(str(value)) for p in compared)
            and not any(p.operation == 'eq' for p in compared))


def lexical_filters(req, pieces, contexts, vocabulary, *, time_required=False):
    """Complete parser filters from uniquely scoped lexical predicates and typed column comparisons."""
    from kge.semantics.coverage import separate_filter_parts
    operators = {'eq': '=', 'ne': '<>', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>=',
                 'null': 'IS NULL', 'not_null': 'IS NOT NULL', 'in': 'IN', 'not_in': 'NOT IN',
                 'contains': 'CONTAINS', 'starts_with': 'STARTS WITH'}
    normalized = {operator: operation for operation, operator in operators.items()} | {'!=': 'ne', '= TRUE': 'eq'}
    requested = [(column, op, value, qualified) for (column, op, value), qualified in zip(req.filters, req.filtered)
                 if not compared_member(qualified, op, value, pieces)]
    suggestions = [(piece, piece.operation, piece.value) for piece in pieces
                   if piece.kind == 'predicate' and piece.operation in operators
                   and not separate_filter_parts(piece, pieces, req.question)]
    for column, comparison, literal in zip(pieces, pieces[1:], pieces[2:]):
        if (column.kind != 'name' or comparison.kind != 'operator' or comparison.operation not in ('eq', 'ne', 'lt', 'le', 'gt', 'ge')
                or literal.kind != 'literal' or req.question[column.span.end:comparison.span.start].strip()
                or req.question[comparison.span.end:literal.span.start].strip()):
            continue
        entries = [vocabulary.entries[identifier] for identifier in column.candidates if identifier in vocabulary.entries]
        if entries and all(entry.kind == 'column' for entry in entries):
            suggestions.append((column, comparison.operation, literal.value if literal.value is not None else literal.text))
    origins = {}
    for piece, operation, value in suggestions:
        scope = filter_scope(piece, pieces, req.question, vocabulary)
        selected = [context for context in contexts if not scope or context.id in scope]
        candidates = [identifier for identifier in piece.candidates if identifier in vocabulary.entries
                      and vocabulary.entries[identifier].kind == 'column' and selected
                      and all(count_filter_path(context, identifier) is not None for context in selected)]
        if len(candidates) != 1:
            raw = [entry for entry in requested if entry[3] in piece.candidates and normalized.get(entry[1]) == operation
                   and (True if entry[1] == '= TRUE' else entry[2]) == value]
            if not candidates and raw:
                for entry in raw:
                    origins.setdefault(entry, []).append(piece)
                continue
            if time_required and any(context.fact not in vocabulary.periods for context in selected):
                return (), {}, 'missing compatible period binding'
            return (), {}, 'A requested predicate needs one declared readable column and an unambiguous role.'
        qualified, = candidates
        values = value if operation in ('in', 'not_in') else () if operation in ('null', 'not_null') else (value,)
        if any(typed_scalar(item, qualified, vocabulary) is None for item in values):
            return (), {}, 'A requested predicate needs a literal compatible with its declared column.'
        existing = next((entry for entry in requested if entry[3] == qualified and normalized.get(entry[1]) == operation
                         and (True if entry[1] == '= TRUE' else entry[2]) == value), None)
        if existing is None:
            existing = (qualified.rsplit('.', 1)[1], operators[operation], value, qualified)
            requested.append(existing)
        origins.setdefault(existing, []).append(piece)
    return tuple(dict.fromkeys(requested)), origins, None


def paired_filter(qualified, op, value, pieces, vocabulary):
    """A declared code/name member makes its two spellings one predicate."""
    if op != '=':
        return False
    for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
        if row['kind'] != 'code_name_pair':
            continue
        pair = row['params']
        side = next((key for key in ('code', 'name') if pair[key] == qualified), None)
        if side is None:
            continue
        other = 'name' if side == 'code' else 'code'
        if any(piece.operation == 'eq' and pair[other] in piece.candidates and any(
                member[side] == value and member[other] == piece.value for member in pair.get('members', ()))
               for piece in pieces if piece.kind == 'predicate'):
            return True
    return False


def path_to(context, table):
    paths = [(context.fact, ())]
    found = []
    for current, path in paths:
        if current == table:
            found.append(path)
            continue
        for join in context.joins:
            if join.source == current and not join.through and not join.alias and join.id not in path:
                visited = {context.fact, *(j.target for j in context.joins if j.id in path)}
                if join.target not in visited:
                    paths.append((join.target, (*path, join.id)))
    return min(found, key=len) if found else None


def population(engine, sem, unit):
    matches = [i for i, e in sem.entities.items() if e['table'].split('.')[0] == unit
               and tuple(e['key']) == (engine.UNIT_KEY.get(unit),)]
    return matches[0] if len(matches) == 1 else None


def matched_contexts(engine, req, contexts, others=True):
    """The term's contexts in the requested units, or that reach them; failing those, all of them unless others is
    False, as the access check asks, so a term mapped in the requested unit is read there, not in another unit."""
    if not req.units:
        return contexts
    matched = [c for c in contexts if c.unit in req.units]
    scoped = matched or [c for c in contexts if any(s.table.split('.')[0] in req.units for s in c.selectors)
        and any(j.through and ({j.source.split('.')[0], j.target.split('.')[0]} & set(req.units)) for j in c.joins)]
    if scoped or not others:
        return scoped
    return [] if len(req.units) == 1 and len(req.terms_found) > 1 and cohort_terms(engine, req) else contexts


def snapshot(engine, graph=True, declarations_only=False):
    sem = engine.semantics
    cached = getattr(sem, 'plan_snapshot' if graph else 'baseline_snapshot', None)
    part_stamp = tuple(sorted((getattr(sem, 'partitioned', None) or {}).keys()))
    if cached is not None and cached.plan_writes == (engine.g.writes, part_stamp):
        return cached
    runtime = copy.copy(sem)
    runtime.contexts = {c.id: Context(c.id, c.label, c.description, c.term, c.measure, c.expression,
        'count' if c.aggregation == 'count_distinct' else c.aggregation, c.grain, c.fact, c.values, c.filters, c.selectors,
        tuple(replace(j, source=j.target, target=j.source) if j.through and j.target.split('.')[0] == c.unit else j for j in c.joins), c.ytd_calendar, c.bindings, c.version_dates)
        for c in sem.contexts.values()} if graph else {}
    runtime.context_declarations = frozenset(runtime.contexts)
    mappings = [] if declarations_only else engine.g.q('MATCH (c:Col)-[m:MAPS_TO {certified: true}]->(t:Term) RETURN t.name AS term, c.id AS qualified, '
                          'c.unit AS unit, c.tbl AS tbl, c.name AS col, c.data_type AS dtype, c.grain AS grain, '
                          'c.currency AS currency, c.calendar AS calendar, c.conversion AS conversion, '
                          'm.certified_by AS certified_by, m.certified_on AS certified_on ORDER BY c.unit, c.id')
    mappings = sorted(mappings, key=lambda row: (row['unit'], row['qualified'], json.dumps(row, sort_keys=True, default=str)))
    if not graph:
        runtime.d = SimpleNamespace(**{k: v for k, v in vars(sem.d).items() if k.isupper()})
        runtime.d.PERIODS = engine.baseline_periods(sem.tables)
        runtime.scd2, runtime.releases = {}, {}
    runtime.plan_writes = (engine.g.writes, part_stamp)
    runtime.no_join_declarations = engine.g.q('MATCH (x:Unit)-[n:NO_JOIN]->(y:Unit) RETURN x.id AS a, y.id AS b, n.asserted_by AS asserted_by, n.asserted_on AS asserted_on, n.note AS note') if graph and not declarations_only else []
    runtime.mapping_declarations = mappings
    runtime.output_metadata = {}
    runtime.measure_specs = {}
    declarations = sem.measure_declarations
    for ctx in runtime.contexts.values():
        declaration = declarations.get(ctx.measure, {})
        runtime.measure_specs[ctx.measure] = MeasureSpec(ctx.measure, declaration.get('currency'),
            declaration.get('population') or ctx.fact, not declaration.get('snapshot') and not snapshot_grain(ctx.grain))
    for item in mappings:
        column, unit, table = item['qualified'], item['unit'], item['unit'] + '.' + item['tbl']
        existing = [c for c in runtime.contexts.values() if c.term == item['term'] and c.unit == unit]
        for ctx in existing:
            runtime.output_metadata[ctx.id] = item
        if existing:
            continue
        matching = {c.measure for c in sem.contexts.values() if c.term == item['term'] and c.fact == table}
        declared = next((d for d in declarations.values() if (d['col'] == column or d['measure'] in matching)
                         and (graph or d['aggregation'] in ('avg', 'count_distinct'))), None)
        for agg in (['count' if declared['aggregation'] == 'count_distinct' else declared['aggregation']] if declared else ['count' if item['dtype'] == 'BOOLEAN' else 'avg' if engine.INTENTS.get('avg') and engine.INTENTS['avg'].search(item['term']) else 'sum']):
            identifier = declared['measure'] if declared else 'mapped:' + column + ':' + agg
            expression = declared['expression'] if declared else f"{agg}({item['tbl']}.{item['col']})"
            if item['dtype'] == 'BOOLEAN' and not declared:
                at_fact = [i for i, e in sem.entities.items() if e['table'] == table]
                if len(at_fact) > 1:
                    continue
                entity_id = at_fact[0] if len(at_fact) == 1 else population(engine, sem, unit)
                if entity_id is None:
                    continue
                entity = sem.entities[entity_id]
                if not set(entity['key']) <= set(sem.tables[table]):
                    continue
                keys = ', '.join(item['tbl'] + '.' + k for k in entity['key'])
                expression = 'count(DISTINCT ' + (keys if len(entity['key']) == 1 else '(' + keys + ')') + ')'
            filters = (Rule('flag:' + column, '', f"{item['tbl']}.{item['col']}", table),) if item['dtype'] == 'BOOLEAN' else ()
            ctx = Context('context:' + identifier, item['term'], '', item['term'], identifier, expression, agg,
                          item['grain'] or 'day', table, (column,), filters, (), ())
            runtime.contexts[ctx.id] = ctx
            runtime.output_metadata[ctx.id] = item
            runtime.measure_specs[identifier] = MeasureSpec(identifier, item['currency'] or None,
                entity_id if item['dtype'] == 'BOOLEAN' and not declared else (declared or {}).get('population') or table,
                not (declared or {}).get('snapshot') and not snapshot_grain(item['grain']))
    for identifier, entity in sem.entities.items():
        table = entity['table']
        measure = 'count:' + identifier
        keys = ', '.join(bare(table) + '.' + k for k in entity['key'])
        expression = 'count(DISTINCT ' + (keys if len(entity['key']) == 1 else '(' + keys + ')') + ')'
        joins = tuple(replace(j, source=table, target=j.target if j.source == table else j.source)
                      for j in sem.declared.values() if j.through and table in (j.source, j.target)
                      and any(c['declared_join'] == j.id for c in sem.certified)) if graph else ()
        ctx = Context('context:' + measure, measure, '', measure, measure, expression, 'count', 'day', table,
                      tuple(table + '.' + k for k in entity['key']), (), (), joins)
        runtime.contexts[ctx.id] = ctx
        runtime.measure_specs[measure] = MeasureSpec(measure, None, identifier, False)
    if not declarations_only:
        setattr(sem, 'plan_snapshot' if graph else 'baseline_snapshot', runtime)
    return runtime


def declared_vocabulary(engine, principal, purpose, scope, cfg, req):
    """A metadata ambiguity check over declarations already read when the engine opened."""
    return _vocabulary(engine, principal, purpose, scope, cfg, req, declarations_only=True)


def vocabulary(engine, principal, purpose, scope, cfg, req=None, history=()):
    restored = _vocabulary(engine, principal, purpose, scope, cfg, req, history)
    if (cfg.graph and history and req is not None and (req.terms_found or req.counted or req.definitional or req.special)
            and not cohort_terms(engine, req)):
        return replace(_vocabulary(engine, principal, purpose, scope, cfg, req), history_vocabulary=restored)
    return restored


def _vocabulary(engine, principal, purpose, scope, cfg, req=None, history=(), declarations_only=False):
    writes = engine.g.writes
    sem = snapshot(engine, cfg.graph, declarations_only)
    if req is not None and cfg.graph and req.agg == 'sum':
        original = sem
        sem = copy.copy(sem)
        sem.contexts, sem.measure_specs = dict(sem.contexts), dict(sem.measure_specs)
        for ctx in original.contexts.values():
            item = original.output_metadata.get(ctx.id, {})
            if (ctx.term not in req.terms_found or ctx.aggregation != 'count' or item.get('dtype') == 'BOOLEAN' or not item.get('col')
                    or item['col'].lower().endswith(('_id', '_no', '_ref'))
                    or any(column == item['col'] for column, _, _ in req.filters)):
                continue
            measure = 'mapped:' + item['qualified'] + ':sum'
            del sem.contexts[ctx.id]
            converted = replace(ctx, id='context:' + measure, measure=measure, aggregation='sum', grain=ctx.grain,
                                ytd_calendar=ctx.ytd_calendar, bindings=ctx.bindings, version_dates=ctx.version_dates,
                                expression='sum(' + item['tbl'] + '.' + item['col'] + ')')
            sem.contexts[converted.id] = converted
            if ctx.id in sem.context_declarations:
                sem.context_declarations = sem.context_declarations | {converted.id}
            sem.measure_specs[measure] = replace(original.measure_specs[ctx.measure], id=measure, additive=True)
    if cfg.graph and not declarations_only:
        original = sem
        for request in [*(parse(h.question, engine.term_names, engine) for h in history), *([req] if req is not None else [])]:
            rebuilt = cohort_snapshot(engine, original, request)
            if rebuilt is original:
                continue
            sem = copy.copy(sem)
            sem.contexts = dict(sem.contexts)
            for identifier, ctx in rebuilt.contexts.items():
                if identifier.startswith('cohort:'):
                    sem.contexts.pop(identifier.removeprefix('cohort:'), None)
                    sem.contexts[identifier] = ctx
    readable = engine.g.readable_units(principal)
    if any(c.unit not in readable and c.id not in sem.context_declarations for c in sem.contexts.values()):
        sem = copy.copy(sem)
        sem.contexts = {c.id: Context(c.id, c.label if c.unit in readable or c.id in sem.context_declarations else c.id, c.description, c.term, c.measure,
            c.expression, c.aggregation, c.grain, c.fact, c.values, c.filters, c.selectors, c.joins, c.ytd_calendar, c.bindings, c.version_dates)
            for c in sem.contexts.values()}
    binding = Binding(release_identity=hashlib.sha256(json.dumps(engine.identity, sort_keys=True).encode()).hexdigest(),
                      access_fingerprint=hashlib.sha256(json.dumps([principal, sorted(readable), engine.g.may(principal, 'querying')]).encode()).hexdigest(),
                      products=tuple(scope.products or ()), purpose=purpose)
    aliases = [(ctx.term, ctx.id) for ctx in sem.contexts.values()]
    for phrase, term in engine.ALIASES.items():
        contexts = [c for c in sem.contexts.values() if c.term == term]
        for ctx in contexts:
            aliases.append((phrase, ctx.id))
        aliases.extend((phrase, row['params']['id']) for row in getattr(sem.d, 'VOCABULARY', ())
                       if row['kind'] in ('derived_measure', 'count_metric') and row['params']['label'] == term)
    for phrase, identifier in {**engine.UNIT_WORDS, **engine.SOURCE_SETS, **engine.ENTITY_WORDS}.items():
        aliases.append((phrase, identifier))
        if phrase and phrase[-1].isalpha():
            aliases.append((plural_forms(phrase)[1], identifier))
    aliases.extend((plural_forms(e['label'])[1], identifier) for identifier, e in sem.entities.items())
    aliases.extend((label, unit) for unit, label in engine.UNIT_LABEL.items())
    aliases.extend((phrase, column) for column, phrase in engine.g.domain().get('business', {}).items())
    aliases.extend(getattr(engine, 'COLUMN_WORDINGS', {}).items())
    aliases.extend((phrase, column) for phrase, column in engine.GROUP_BY.items())
    aliases.extend((phrase, column) for phrase, columns in engine.ACRONYMS.items() for column in columns)
    values = []
    for code in sorted({m.currency for m in sem.measure_specs.values() if m.currency}):
        values.append(Entry(code, 'currency', code, None))
        aliases.extend((form, code) for phrase in CURRENCY_NAMES.get(code, '').split('|') if phrase for form in plural_forms(phrase))
        aliases.extend((symbol, code) for symbol, target in CURRENCY_SYMBOLS.items() if target == code)
    values.extend(value_entries(engine.g.domain()['vocabulary']))
    denied = set()
    if req is not None:
        restricted = restricted_asked(req.question, engine)
        if restricted:
            contexts = [c for term in req.terms_found
                        for c in matched_contexts(engine, req, [c for c in sem.contexts.values() if c.term == term])]
            units = set(req.units) | {c.unit for c in contexts}
            independent = not req.units and not any(j.through for c in sem.contexts.values() if c.term in req.terms_found for j in c.joins)
            if req.term and units and not req.definitional and req.special is None and (len(units) == 1 or independent):
                restricted = [column for unit in sorted(units) for column in restricted_named(req.question, engine, unit)]
            if restricted:
                denied.add(req.question)
    if req is None:
        for column in restricted_asked(' '.join(engine.RESTRICTED_PHRASES), engine):
            denied.add(column)
        for unit, table, column in engine.RESTRICTED_COLUMNS:
            denied.update((f'{unit}.{table}.{column}', f'{table}.{column}', column, column.replace('_', ' ')))
            denied.update(engine.RESTRICTED_PHRASES.get(f'{unit}.{table}.{column}', ()))
    no_join = [(r['a'], r['b']) for r in sem.no_join_declarations]
    terms = set(req.terms_found) if req is not None else set()
    if not terms and history and history[-1].plan is not None:
        terms = {sem.contexts[m.context.id].term for m in history[-1].plan.measures if m.context.id in sem.contexts}
    terms.update(sem.contexts[m.context.id].term for entry in history if entry.plan is not None
                 for m in entry.plan.measures if m.context.id in sem.contexts)
    terms.update(sem.contexts[choice.id].term for entry in history if entry.clarification is not None
                 for choice in entry.clarification.choices if choice.id in sem.contexts)
    formulas = [row['params'] for row in getattr(sem.d, 'VOCABULARY', ())
                if row['kind'] == 'derived_measure' and row['params']['label'] in terms]
    formula_inputs = {declaration['formula'][role] for declaration in formulas for role in ('numerator', 'denominator')}
    for declaration in formulas:
        inputs = {declaration['formula'][role] for role in ('numerator', 'denominator')}
        terms.update(c.term for c in sem.contexts.values() if c.measure in inputs)
    count_contexts = {row['params'].get('context') or 'context:count:' + row['params']['entity']
                      for row in getattr(sem.d, 'VOCABULARY', ()) if row['kind'] == 'count_metric'
                      and (row['params']['label'] in terms or row['params']['id'] in formula_inputs)}
    terms.update(c.term for c in sem.contexts.values() if c.id in count_contexts)
    selected = [c for c in sem.contexts.values() if c.term in terms and c.bindings]
    bound_columns = ()
    allowed_tables = set(sem.tables)
    if selected:
        bindings = {a: b for c in selected for a, b in c.bindings.items()}
        bound_columns = tuple(bindings)
        retained = [c for c in sem.contexts.values() if c.term in terms] if history or formulas or count_contexts else selected
        allowed_tables = {c.fact for c in retained} | {t for c in retained for j in c.joins for t in (j.source, j.target)}
        allowed_tables.update(r.table for c in retained for r in (*c.filters, *c.selectors))
        aliases = [(phrase, bindings.get(identifier, identifier) if isinstance(identifier, str) else identifier) for phrase, identifier in aliases]
        values = [e for e in values if e.column not in bindings]
        sem = copy.copy(sem)
        sem.contexts = {i: c for i, c in sem.contexts.items() if c.fact in allowed_tables
                        and all(j.source in allowed_tables and j.target in allowed_tables and not j.through for j in c.joins)
                        and all(r.table in allowed_tables for r in (*c.filters, *c.selectors))}
        sem.d = bound_domain(sem.d, bindings)
    variant = (cfg.graph, declarations_only, sem.plan_writes, json.dumps(getattr(sem, 'partitioned', None), sort_keys=True),
        tuple((c, c.grain, c.ytd_calendar, json.dumps(c.bindings, sort_keys=True), json.dumps(c.version_dates, sort_keys=True))
              for c in sem.contexts.values()), tuple(sem.measure_specs.values()),
        tuple((phrase, tuple(identifier) if isinstance(identifier, list) else identifier) for phrase, identifier in aliases),
        tuple(values), frozenset(denied), frozenset(allowed_tables), bound_columns, req is not None)
    return engine.g.scoped_vocabulary(binding, variant, writes, lambda: build_vocabulary(
        sem, binding, readable, readable, list(sem.measure_specs.values()), aliases=aliases,
        values=values, denied_phrases=denied, no_join=no_join, scoped_tables=allowed_tables,
        qualified_policy_names=req is not None))


def time_of(req, calendar):
    cal = name(calendar)
    if req.latest:
        return {'kind': 'latest_released', 'calendar': cal}
    if req.period is None:
        return {'kind': 'all_available', 'calendar': cal}
    kind, value = req.period.plan_kind, req.period.value
    if kind in ('month', 'day'):
        value = value[:7] if kind == 'month' else value
    elif kind == 'quarter':
        kind = 'calendar_quarter'
    elif kind == 'fiscal_quarter':
        kind = 'fiscal_quarter'
    return {'kind': 'single', 'period': {'kind': kind, 'value': value, 'calendar': cal}}


def control_vocabulary(engine, principal, purpose, scope, request=None):
    """Schema-only experimental contexts need no live graph declarations or model-authored expressions."""
    writes = engine.g.writes
    readable = engine.g.readable_units(principal)
    binding = Binding(release_identity=hashlib.sha256(json.dumps(engine.identity, sort_keys=True).encode()).hexdigest(),
        access_fingerprint=hashlib.sha256(json.dumps([principal, sorted(readable), engine.g.may(principal, 'querying')]).encode()).hexdigest(),
        products=tuple(scope.products or ()), purpose=purpose)
    aggregation = request.agg if request is not None and request.agg in ('sum', 'avg') else 'sum'
    return engine.g.scoped_vocabulary(binding, ('control', aggregation), writes,
        lambda: _control_vocabulary(engine, readable, binding, aggregation))


def _control_vocabulary(engine, readable, binding, aggregation):
    sem = Semantics.__new__(Semantics)
    sem.context_ids, sem.measure_ids, sem.graph_rule_ids = frozenset(), frozenset(), frozenset()
    sem.d = SimpleNamespace(**{k: v for k, v in vars(engine).items() if k.isupper()})
    sem.d.BUSINESS = {}
    sem.g = engine.g
    sem.tables, sem.types, sem.entities, sem.contexts, sem.measure_specs, sem.output_metadata = {}, {}, {}, {}, {}, {}
    sem.scd2, sem.releases, sem.declared, sem.cites, sem.certified = {}, {}, {}, {}, ()
    sem.mapping_declarations, sem.no_join_declarations = (), ()
    restricted = {'.'.join(column) for column in engine.RESTRICTED_COLUMNS}
    for unit in engine.UNIT_ORDER:
        if unit not in readable:
            continue
        for table, column, dtype in engine.w.columns(unit):
            name = unit + '.' + table
            sem.tables.setdefault(name, {})[column] = dtype
            sem.types[name + '.' + column] = dtype
        for table, keys in engine.w.primary_keys(unit):
            name = unit + '.' + table
            if keys and set(keys) <= set(sem.tables.get(name, {})) and not any(name + '.' + key in restricted for key in keys):
                sem.entities['schema:entity:' + name] = {'label': table.replace('_', ' '), 'table': name, 'key': list(keys)}
    sem.d.PERIODS = {table: {**binding, 'calendar': 'schema_calendar'} for table, binding in engine.baseline_periods(sem.tables).items()
                     if binding['column'] != 'fiscal_quarter'}
    sem.d.CALENDARS = {'schema_calendar': {'year_start_month': 1, 'quarter_label': 'Q'}}
    for table, columns in sem.tables.items():
        for column, dtype in columns.items():
            qualified = table + '.' + column
            if qualified in restricted:
                continue
            if not NUMERIC.search(dtype) and dtype != 'BOOLEAN':
                continue
            unit, physical = table.split('.', 1)
            keys = sem.entities.get('schema:entity:' + table, {}).get('key', [])
            if dtype == 'BOOLEAN' and not keys:
                continue
            aggregations = ('count',) if dtype == 'BOOLEAN' else (aggregation,)
            for agg in aggregations:
                identifier = 'schema:' + qualified + ':' + agg
                measure = identifier + '/measure'
                filters = (Rule('flag:' + qualified, '', physical + '.' + column, table),) if dtype == 'BOOLEAN' else ()
                counted = ', '.join(physical + '.' + key for key in keys)
                expression = ('count(DISTINCT ' + (counted if len(keys) == 1 else '(' + counted + ')') + ')'
                              if dtype == 'BOOLEAN' else agg + '(' + physical + '.' + column + ')')
                sem.contexts[identifier] = Context(identifier, column.replace('_', ' '), '', column.replace('_', ' '),
                    measure, expression, agg, 'month' if table in sem.d.PERIODS else 'day', table,
                    (qualified,), filters, (), ())
                sem.measure_specs[measure] = MeasureSpec(measure, None, table, True)
                sem.output_metadata[identifier] = {'tbl': physical, 'col': column, 'dtype': dtype}
    for identifier, entity in sem.entities.items():
        values = tuple(entity['table'] + '.' + key for key in entity['key'])
        context = 'schema:' + ':'.join((*values, 'count'))
        measure = context + '/measure'
        keys = ', '.join(bare(entity['table']) + '.' + key for key in entity['key'])
        expression = 'count(DISTINCT ' + (keys if len(entity['key']) == 1 else '(' + keys + ')') + ')'
        sem.contexts[context] = Context(context, 'count ' + entity['label'], '', 'count ' + entity['label'], measure, expression, 'count',
            'month' if entity['table'] in sem.d.PERIODS else 'day', entity['table'], values, (), (), ())
        sem.measure_specs[measure] = MeasureSpec(measure, None, identifier, False)
        sem.output_metadata[context] = {'tbl': bare(entity['table']), 'col': entity['key'][0], 'dtype': sem.types[values[0]]}
    aliases = [(part, ctx.id) for ctx in sem.contexts.values() if not ctx.label.startswith('count ') for part in ctx.label.split()]
    aliases.extend((entity['label'] + 's', identifier) for identifier, entity in sem.entities.items())
    aliases.extend((label, unit) for unit, label in engine.UNIT_LABEL.items() if unit in readable)
    aliases.extend((phrase, unit) for phrase, unit in engine.UNIT_WORDS.items() if unit in readable)
    vocab = build_vocabulary(sem, binding, readable, readable, list(sem.measure_specs.values()), aliases=aliases,
        denied_phrases=tuple(engine.RESTRICTED_PHRASES), scoped_tables=set(sem.tables), qualified_policy_names=True)
    return vocab, sem


def uncounted():
    """The rules' decline for a count whose count context this vocabulary does not offer (a contract vocabulary, a
    unit the asker cannot read): never a KeyError; the model path, where configured, reads it instead."""
    return Abstain(kind='abstain', spans=(), missing=('The requested count is not offered in this vocabulary.',))


def read(engine, req, vocab, cfg, turn='turn', cohort=False, base=None):
    """The coded rules' reading of a question. Until B7 (#455) it reads a phrase table equal to the constant baseline
    (ledger(..., baseline=True)), never one a graph extends beyond it; coverage and the graph rules read the graph's."""
    pieces = ledger(req.question, turn, vocab, baseline=True)
    if req.time_mode != 'current' and not any(p.operation == req.time_mode for p in pieces):
        req = replace(req, time_mode='current')
    declared_counts = list(dict.fromkeys(i for p in pieces if p.kind == 'name' for i in p.candidates if i in vocab.count_metrics))
    declared_derived = list(dict.fromkeys(i for p in pieces if p.kind == 'name' for i in p.candidates if i in vocab.derived_measures))
    named_contexts = {i for p in pieces if p.kind == 'name' for i in p.candidates if i in vocab.contexts}
    count_roles = [(identifier, vocab.count_metrics[identifier].get('context') or 'context:count:' + vocab.count_metrics[identifier]['entity'])
                   for identifier in declared_counts]
    derived_contexts, formula_contexts = {}, {}
    for identifier in declared_derived:
        declaration = vocab.derived_measures[identifier]
        formula = declaration['formula']
        for role in ('numerator', 'denominator'):
            operand = formula[role]
            bound = declaration.get('contexts', {}).get(role)
            if operand in vocab.count_metrics:
                row = vocab.count_metrics[operand]
                context = bound or row.get('context') or 'context:count:' + row['entity']
                if (operand, context) not in count_roles:
                    count_roles.append((operand, context))
                if context not in vocab.contexts:
                    return uncounted()
                formula_contexts[identifier, role] = vocab.contexts[context]
                continue
            choices = [c for c in vocab.contexts.values() if c.measure == operand]
            choices = [c for c in choices if c.id == bound] if bound else [c for c in choices if c.id in named_contexts] or choices
            if len(choices) != 1:
                return Clarify(kind='clarify', spans=(), candidates=tuple(Name(id=c.id, evidence=()) for c in choices))
            formula_contexts[identifier, role] = choices[0]
            derived_contexts[choices[0].id] = choices[0]
    if any(context not in vocab.contexts for _, context in count_roles):
        return uncounted()
    contexts = [c for c in vocab.contexts.values() if c.term in req.terms_found]
    contexts = list({c.id: c for c in (*contexts, *derived_contexts.values(),
                    *(vocab.contexts[context] for _, context in count_roles))}.values())
    bindings = {a: b for c in contexts for a, b in (c.bindings or {}).items()}
    canonical_predicate_domain = bound_domain(vocab.semantics.d, bindings) if bindings else vocab.semantics.d
    predicate_domain = canonical_predicate_domain if bindings else engine
    if bindings:
        req = copy.copy(req)
        req.group_by = [bindings.get(c, c) for c in req.group_by]
        scoped = parse(req.question, engine.term_names, bound_domain(vocab.semantics.d, bindings))
        req.filters, req.filtered = scoped.filters, scoped.filtered
        req.counted = bindings.get(req.counted, req.counted)
        req.entity = (bindings.get(req.entity[0], req.entity[0]), req.entity[1]) if req.entity else None
    filter_groups, filter_problem = boolean_groups(pieces, req.question)
    if filter_problem:
        return Clarify(kind='clarify', spans=tuple(p.span for p in pieces if p.kind == 'predicate'), candidates=())
    listed = {p.value for p in pieces if p.kind == 'predicate' and p.operation == 'in'}
    if any(op == 'IN' and value not in listed for _, op, value in req.filters):
        req = copy.copy(req)
        unlisted = parse(req.question, engine.term_names, WithoutLists(vocab.semantics.d if bindings else engine))
        req.filters, req.filtered = unlisted.filters, unlisted.filtered
    from kge.semantics.calendarattributes import request_filters, masked_question
    recurring = request_filters(req.question, vocab)
    if recurring:
        parsed = parse(masked_question(req.question, vocab), engine.term_names, vocab.semantics.d)
        req = replace(req, period=parsed.period, problem=parsed.problem,
                      filters=[*req.filters, *(f for f, _ in recurring)],
                      filtered=[*req.filtered, *(c for _, c in recurring)])
    ytd = next((p for p in pieces if p.operation in ('ytd', 'mtd', 'qtd')), None)
    endpoints = planshapes.periods(pieces)
    if ytd and req.problem == UNPLACED_RANGE and len(endpoints) <= 1 and all(endpoints):
        req = replace(req, period=endpoints[0] if endpoints else None, problem=None)
    if req.problem and len(endpoints) == 2 and all(endpoints) and (
            req.problem == UNPLACED_RANGE or req.problem.startswith('Please give a valid period:')):
        req = replace(req, period=endpoints[0], problem=None)
    if req.problem:
        return Abstain(kind='abstain', spans=(), missing=(req.problem,))
    sem = vocab.semantics
    runtime = snapshot(engine, cfg.graph)
    selected, counts, aggregations = [], {}, {}
    named_counts = counted_names(pieces, req.question, vocab, coordinated=cfg.graph)
    aggregation_positions = output_aggregation_positions(pieces, req.question, vocab)
    per_output = {context: set(positions) for context, positions in aggregation_positions.items()}
    count_populations = count_population_contexts(pieces, req.question, vocab)
    count_entities = {i for p in pieces for i in p.candidates if i in vocab.entities}
    counted = (next(iter(named_counts)) if len(named_counts) == 1 else req.counted
               or (next(iter(count_entities)) if len(count_entities) == 1 else None))
    trusted_contexts = {identifier for piece in pieces if cfg.glossary and piece.kind == 'name'
                        and one_meaning(piece.candidates, vocab) for identifier in piece.candidates if identifier in vocab.contexts}
    retained = {vocab.contexts[i].term for p in pieces for i in p.candidates if i in vocab.contexts}
    retained.update(ctx.term for p in pieces for ctx in vocab.contexts.values()
                    if (not trusted_contexts or ctx.id in trusted_contexts)
                    and (not (scoped := filter_scope(p, pieces, req.question, vocab)) or ctx.id in scoped) and (
                        set(p.candidates) & context_names(ctx, vocab) or
                        (p.kind == 'predicate' and ctx.term in req.terms_found and any(i.rsplit('.', 1)[0] == ctx.fact for i in p.candidates))))
    explicit = set(trusted_contexts)
    shared = binding.shared_contexts(pieces, vocab) if any(p.operation == 'comparison' for p in pieces) else ()
    explicit.update(shared)
    terms = list(dict.fromkeys([*(t for t in req.terms_found if t in retained),
                               *(vocab.contexts[i].term for i in sorted(explicit))]))
    terms.extend(vocab.contexts[i].term for i in shared if vocab.contexts[i].term not in terms)
    for context in derived_contexts.values():
        explicit.add(context.id)
        if context.term not in terms:
            terms.append(context.term)
    if base is not None and not declared_derived:
        terms = req.terms_found
    units = req.units
    membership_units = {s.table.split('.')[0] for ctx in vocab.contexts.values() if ctx.term in terms
                        for s in ctx.selectors if s.table.split('.')[0] != ctx.unit
                        and any(j.through and j.target.split('.')[0] == s.table.split('.')[0] for j in ctx.joins)} & set(units)
    measure_units = [u for u in units if u not in membership_units]
    if not units and req.all_units:
        units = list(dict.fromkeys(u for p in pieces for i in p.candidates
                                  if vocab.entries[i].kind == 'source_set' for u in vocab.entries[i].value))
    baseline = None
    if not cfg.graph:
        if req.term is None and cfg.resolution != 'words':
            return Abstain(kind='abstain', spans=(), missing=('No glossary term recognised in the question.',))
        from kge.services.ask.engine.question import question_words
        impls = engine.guess(question_words(req.question, engine)) if cfg.resolution == 'words' else engine.resolve(req.term, cfg)
        baseline = {i['unit'] + '.' + i['tbl'] + '.' + i['col'] for i in impls}
        available = [c for c in vocab.contexts.values() if c.id in runtime.output_metadata and set(c.values) & baseline]
        terms = list(dict.fromkeys(c.term for c in available))
    for term in terms:
        contexts = [c for c in vocab.contexts.values() if c.term == term]
        if base is not None and not req.units:
            contexts = [c for c in contexts if c.id in {m.context.id for m in base.plan.measures}]
        if baseline is not None:
            contexts = [c for c in contexts if c.id in runtime.output_metadata and set(c.values) & baseline]
        if measure_units:
            contexts = [c for c in contexts if c.unit in measure_units]
        elif not req.all_units and vocab.binding.products:
            preferred = [c for c in contexts if c.fact.lower() in {p.lower() for p in vocab.binding.products}]
            contexts = preferred or [c for c in contexts if c.unit in {p.split('.')[0] for p in vocab.binding.products}] or contexts
        by_unit = {}
        for ctx in contexts:
            by_unit.setdefault(ctx.unit, []).append(ctx)
        for unit, options in by_unit.items():
            chosen = [c for c in options if c.id in explicit]
            options = chosen or options
            narrowed = [c for c in options if c.id.startswith('cohort:')]
            options = narrowed or options
            authored = [c for c in options if cfg.graph and c.id in engine.semantics.contexts]
            if authored:
                options = authored
            else:
                declaration = [c for c in options if c.measure in engine.semantics.measure_declarations]
                options = declaration or options
            if len(options) != 1:
                return Clarify(kind='clarify', spans=(), candidates=tuple(Name(id=c.id, evidence=()) for c in options))
            ctx = options[0]
            requested = per_output.get(ctx.id, set())
            requested_aggregation = next(iter(requested), None) if len(requested) == 1 else None
            aggregation = requested_aggregation or ('count' if ctx.id in count_populations else
                ctx.aggregation if named_counts or count_roles or per_output else req.agg)
            aggregations[ctx.id] = requested_aggregation
            if requested_aggregation and requested_aggregation != 'count' and requested_aggregation not in (vocab.measures[ctx.measure].aggregations or (ctx.aggregation,)):
                return Abstain(kind='abstain', spans=(), missing=(f'The measure does not declare {requested_aggregation} aggregation.',))
            if aggregation == 'avg' and ctx.aggregation != 'avg' and not requested_aggregation:
                return Abstain(kind='abstain', spans=(), missing=('No certified average measure is declared for this term.',))
            if (aggregation == 'count' and 'count' not in vocab.measures[ctx.measure].aggregations and ((not cohort and ctx.aggregation != 'count')
                    or (cohort and mapping_role(runtime.output_metadata.get(ctx.id.removeprefix('cohort:'), {})) != 'measure'))):
                entity = counted
                if named_counts:
                    reachable = {i for i in named_counts if path_to(ctx, vocab.entities[i]['table']) is not None}
                    at_fact = {i for i in reachable if vocab.entities[i]['table'] == ctx.fact}
                    matches = at_fact or reachable
                    if len(matches) != 1:
                        return Clarify(kind='clarify', spans=(), candidates=tuple(Name(id=i, evidence=()) for i in sorted(matches or named_counts)))
                    entity, = matches
                metadata = runtime.output_metadata.get(ctx.id.removeprefix('cohort:'), {})
                if entity is None and cohort:
                    entity = population(engine, sem, unit)
                if entity is None and runtime.measure_specs.get(ctx.measure) and runtime.measure_specs[ctx.measure].population in vocab.entities:
                    entity = runtime.measure_specs[ctx.measure].population
                if entity is None and metadata.get('dtype') == 'BOOLEAN':
                    entity = population(engine, sem, unit)
                if entity is None:
                    matches = [i for i, e in vocab.entities.items() if e['table'] == ctx.fact]
                    entity = matches[0] if len(matches) == 1 else None
                if entity is None:
                    return Abstain(kind='abstain', spans=(), missing=('A count requires a declared entity and its complete key.',))
                counts[ctx.id] = entity
            selected.append(ctx)
    membership = None
    if selected and membership_units:
        options = [(ctx, j, e) for ctx in selected for j in ctx.joins if j.through
                   for e, entity in vocab.entities.items() if entity['table'] == j.target and j.target.split('.')[0] in membership_units]
        if len(options) != 1:
            return Abstain(kind='abstain', spans=(), missing=('No unambiguous declared membership reaches the named source.',))
        ctx, relation, entity = options[0]
        if 'context:count:' + entity not in vocab.contexts:
            return uncounted()
        membership = {'kind': 'membership', 'id': 'membership', 'entity': name(entity),
                      'relation': name(relation.id), 'context': name('context:count:' + entity), 'filters': [], 'evidence': []}
    if not selected and any(p.operation == 'membership' for p in pieces):
        entities = list(dict.fromkeys(i for p in pieces for i in p.candidates if i in vocab.entities))
        if len(entities) == 2 and all('context:count:' + i in vocab.contexts for i in entities):
            near, far = (vocab.contexts['context:count:' + i] for i in entities)
            relations = [j for j in near.joins if j.through and j.target == far.fact and j.id in vocab.entries]
            if len(relations) == 1:
                selected.append(near)
                counts[near.id] = entities[0]
                membership = {'kind': 'membership', 'id': 'membership', 'entity': name(entities[1]),
                              'relation': name(relations[0].id), 'context': name(far.id), 'filters': [], 'evidence': []}
    if not selected and named_counts and counted not in named_counts:
        return Clarify(kind='clarify', spans=(), candidates=tuple(Name(id=i, evidence=()) for i in sorted(named_counts)))
    if (not selected and not count_roles and (req.agg == 'count' or named_counts) and counted in vocab.entities
            and 'context:count:' + counted in vocab.contexts):
        entity = vocab.entities[counted]
        if units and any(u != entity['table'].split('.')[0] for u in units):
            if req.special is not None:
                return Clarify(kind='clarify', spans=(), candidates=(Name(id=counted, evidence=()),))
            population_pieces = [p for p in pieces if counted in p.candidates or any(
                vocab.entries[i].kind in ('unit', 'source_set') for i in p.candidates)]
            span = Span(turn=turn, start=min(p.span.start for p in population_pieces),
                        end=max(p.span.end for p in population_pieces))
            if any(p.kind != 'name' or any(vocab.entries[i].kind not in ('unit', 'entity', 'source_set') for i in p.candidates)
                   for p in pieces if span.start <= p.span.start and p.span.end <= span.end):
                return Clarify(kind='clarify', spans=(), candidates=(Name(id=counted, evidence=()),))
            return Clarify(kind='clarify', spans=(span,), candidates=(Name(id=counted, evidence=()),))
        ctx = vocab.contexts['context:count:' + counted]
        selected.append(ctx)
        counts[ctx.id] = counted
    primary_contexts = {ctx.id for ctx in selected}
    for identifier, context in count_roles:
        context = vocab.contexts[context]
        if context not in selected:
            selected.append(context)
    if not selected:
        if not any(p.kind == 'unknown' for p in pieces):
            predicates = [piece for piece in pieces if piece.kind == 'predicate']
            choices = predicate_contexts(vocab, pieces)
            if choices:
                spans = tuple(predicate.span for predicate in predicates)
                return Clarify(kind='clarify', spans=spans,
                               candidates=tuple(Name(id=context.id, evidence=spans) for context in choices))
            for piece in pieces:
                candidates = tuple(i for i in piece.candidates if not units or vocab.entries[i].unit in (None, *units))
                if len(candidates) > 1 and not one_meaning(candidates, vocab):
                    return Clarify(kind='clarify', spans=(piece.span,), candidates=tuple(Name(id=i, evidence=(piece.span,)) for i in candidates))
        if req.term is None and not req.terms_found:
            return Abstain(kind='abstain', spans=(), missing=('No glossary term recognised in the question.',))
        return Abstain(kind='abstain', spans=(), missing=(
            f"I cannot apply '{req.question}': no certified measure or declared entity resolves the complete request. No figure is given.",))
    if units and set(units) - {c.unit for c in selected} - membership_units - ({vocab.contexts[membership['context']['id']].unit} if membership else set()):
        return Abstain(kind='abstain', spans=(), missing=('The requested measure is not certified for every named source.',))
    measures = []
    if cohort:
        selected.sort(key=lambda c: (mapping_role(runtime.output_metadata.get(c.id.removeprefix('cohort:'), {})) != 'flag', mapping_role(runtime.output_metadata.get(c.id.removeprefix('cohort:'), {})) == 'measure'))
    elif named_counts:
        selected.sort(key=lambda c: min((p.span.start for p in pieces if c.id in p.candidates), default=len(req.question)))
    for n, ctx in enumerate(selected):
        if ctx.id not in primary_contexts:
            continue
        requested = per_output.get(ctx.id, set())
        variants = sorted(requested, key=lambda aggregation: aggregation_positions[ctx.id][aggregation])
        for requested_aggregation in variants or [aggregations.get(ctx.id)]:
            item = {'kind': 'distinct_count' if ctx.id in counts else 'measure', 'id': ('c' + str(sum(c.id in counts for c in selected[:n + 1])) if ctx.id in counts else 'm' + str(sum(c.id not in counts for c in selected[:n + 1]))) if cohort else 'm' + str(len(measures)),
                    'context': name(ctx.id), 'mode': req.time_mode, 'filters': []}
            item['entity' if ctx.id in counts else 'measure'] = name(counts.get(ctx.id, ctx.measure))
            if item['kind'] == 'measure' and requested_aggregation and (
                    requested_aggregation != ctx.aggregation or vocab.measures[ctx.measure].aggregations):
                item['aggregation'] = requested_aggregation
            elif item['kind'] == 'measure' and not named_counts and not count_roles and not per_output and req.agg == 'count' and 'count' in vocab.measures[ctx.measure].aggregations:
                item['aggregation'] = 'count'
            measures.append(item)
    if named_counts:
        count_contexts = sorted(selected, key=lambda c: min((p.span.start for p in pieces if c.id in p.candidates), default=len(req.question)))
        for ctx in count_contexts[:1]:
            if ctx.id in counts or ctx.aggregation == 'count':
                continue
            reachable = {i for i in named_counts if path_to(ctx, vocab.entities[i]['table']) is not None}
            at_fact = {i for i in reachable if vocab.entities[i]['table'] == ctx.fact}
            matches = {entity for entity in reachable if count_metrics(vocab, ctx, entity)}
            if not matches:
                return Abstain(kind='abstain', spans=(), missing=('No unambiguous distinct-count metric is declared for this entity and population.',))
            for entity in sorted(matches, key=lambda identifier: min((p.span.start for p in pieces if identifier in p.candidates), default=len(req.question))):
                measures.append({'kind': 'distinct_count', 'id': 'n' + str(len(measures)), 'entity': name(entity),
                                 'context': name(ctx.id), 'mode': req.time_mode, 'filters': []})
    for identifier, context in count_roles:
        row = vocab.count_metrics[identifier]
        measures.append({'kind': 'distinct_count', 'id': 'n' + str(len(measures)), 'entity': name(row['entity']),
            'context': name(context), 'mode': req.time_mode, 'filters': [],
            'declaration': name(identifier)})
    for item in measures:
        if item['kind'] == 'distinct_count':
            explicit = item.get('declaration', {}).get('id')
            declarations = selected_count_metrics(vocab, vocab.contexts[item['context']['id']], item['entity']['id'], explicit)
            if len(declarations) != 1:
                return Abstain(kind='abstain', spans=(), missing=('No unambiguous distinct-count metric is declared for this entity and population.',))
            declaration = declarations[0]
            if explicit or declaration.get('filters'):
                item['declaration'] = name(declaration['id'])
            for index, predicate in enumerate(declaration.get('filters', ())):
                column = predicate['column']
                path = path_to(vocab.contexts[item['context']['id']], column.rsplit('.', 1)[0])
                if path is None:
                    return Abstain(kind='abstain', spans=(), missing=('A declared count filter has no role from its context.',))
                node = {'id': 'count-filter-' + item['id'] + '-' + str(index),
                        'left': {'kind': 'column', 'name': name(column), 'path': [name(i) for i in path]}, 'evidence': []}
                if predicate['op'] in ('is_null', 'is_not_null'):
                    node.update(kind='null', is_null=predicate['op'] == 'is_null')
                else:
                    node.update(kind='compare', op=predicate['op'], value=typed_scalar(predicate['value'], column, vocab))
                item['filters'].append(node)
    for identifier in declared_derived:
        formula = vocab.derived_measures[identifier]['formula']
        for role in ('numerator', 'denominator'):
            if formula[role] in vocab.count_metrics:
                continue
            context = formula_contexts[identifier, role]
            if not any(item['kind'] == 'measure' and item['measure']['id'] == formula[role]
                       and item['context']['id'] == context.id and item.get('aggregation', context.aggregation) == context.aggregation
                       for item in measures):
                measures.append({'kind': 'measure', 'id': 'm' + str(len(measures)), 'measure': name(formula[role]),
                    'context': name(context.id), 'mode': req.time_mode, 'filters': []})
    if not cfg.graph and req.period and req.period.kind == 'calendar_quarter' and any(
            'fiscal_quarter' in vocab.tables[c.fact] for c in selected):
        return Abstain(kind='abstain', spans=(), missing=('The schema calendar heuristic does not establish the requested quarter.',))
    calendar = engine.PERIODS.get(selected[0].fact, {}).get('calendar') or next(iter(vocab.calendars), None)
    if req.period and req.period.kind in ('fiscal_quarter', 'fiscal_year'):
        calendar = next((k for k, v in engine.CALENDARS.items() if v['year_start_month'] == engine.FISCAL_START), calendar)
    if ytd:
        if ytd.text.casefold().startswith('fiscal'):
            calendar = next((k for k, v in vocab.calendars.items() if v['quarter_label'].startswith('FY')), None)
        else:
            calendar = selected[0].ytd_calendar or calendar
    if calendar is None:
        return Abstain(kind='abstain', spans=(), missing=('No calendar is declared.',))
    dimensions = []
    grouping = list(dict.fromkeys([*req.group_by, *(i for p in pieces if p.operation == 'dimension'
        for i in p.candidates if vocab.entries[i].kind in ('column', 'band'))]))
    if any(p.operation in ('asc', 'desc') for p in pieces):
        grouped = [i for p in pieces if p.operation == 'dimension' for i in p.candidates if vocab.entries[i].kind in ('column', 'band')]
        grouping = list(dict.fromkeys(grouped))
    for piece in pieces:
        candidates = tuple(i for i in piece.candidates if i in req.group_by)
        at_fact = tuple(i for i in candidates if all(i.rsplit('.', 1)[0] == c.fact for c in selected))
        if len(at_fact) == 1:
            alternatives = set(engine.ACRONYMS.get(piece.text.upper(), piece.candidates))
            grouping = [c for c in grouping if c not in alternatives or c in at_fact]
        candidates = at_fact or candidates
        if len(candidates) > 1:
            return Clarify(kind='clarify', spans=(piece.span,), candidates=tuple(Name(id=i, evidence=(piece.span,)) for i in candidates))
    for n, identifier in enumerate(grouping):
        entry = vocab.entries.get(identifier)
        banded = entry is not None and entry.kind == 'band'
        column = entry.column if banded else identifier
        bindings = []
        for ctx in selected:
            path = path_to(ctx, column.rsplit('.', 1)[0])
            if path is None:
                return Abstain(kind='abstain', spans=(), missing=('No declared role reaches the requested dimension.',))
            level = sum(j.kind == 'hierarchy' for j in ctx.joins if j.id in path)
            bindings.append({'context': name(ctx.id), 'column': name(column), 'path': [name(p) for p in path], 'level': level or None})
        hierarchical = all(b['level'] is not None for b in bindings)
        if not hierarchical:
            bindings = [{**b, 'level': None} for b in bindings]
        if banded:
            dimensions.append({'kind': 'band', 'id': 'd' + str(n), 'band': name(identifier),
                               'bindings': [{**b, 'level': None} for b in bindings]})
        else:
            dimensions.append({'kind': 'hierarchy' if hierarchical else 'column', 'id': 'd' + str(n), 'bindings': bindings})
    data = {'schema': 'query-plan/v1', 'layout': 'aligned', 'measures': measures, 'dimensions': dimensions,
            'filters': [membership] if membership else [], 'time': time_of(req, calendar), 'comparisons': [], 'derived': [], 'order': [], 'limit': None, 'obligations': []}
    if problem := planshapes.compose(data, req, pieces, vocab):
        return Abstain(kind='abstain', spans=(), missing=(problem,))
    for identifier in declared_derived:
        declaration = vocab.derived_measures[identifier]
        formula = declaration['formula']
        operands = {}
        for role in ('numerator', 'denominator'):
            context = formula_contexts[identifier, role]
            outputs = [m['id'] for m in data['measures'] if m['context']['id'] == context.id and (
                m['kind'] == 'measure' and m['measure']['id'] == formula[role]
                and m.get('aggregation', context.aggregation) == context.aggregation
                or m['kind'] == 'distinct_count' and m.get('declaration', {}).get('id') == formula[role])]
            if len(outputs) != 1:
                return Abstain(kind='abstain', spans=(), missing=('A declared formula needs one output per input measure.',))
            operands[role] = outputs[0]
        if formula['kind'] == 'share':
            if not data['dimensions']:
                return Abstain(kind='abstain', spans=(), missing=('Please identify the breakdown for the share of total.',))
            data['derived'].append({'kind': 'share', 'id': 'formula' + str(len(data['derived'])), 'input': operands['numerator'],
                'remove_dimensions': [d['id'] for d in data['dimensions']], 'remove_filters': [],
                'declaration': name(identifier), 'evidence': []})
        else:
            data['derived'].append({'kind': formula['kind'], 'id': 'formula' + str(len(data['derived'])),
                                    **operands, 'declaration': name(identifier), 'evidence': []})
    expanded = (data['comparisons'] or data['derived'] or data['order'] or data['layout'] == 'separate' or any(d['kind'] == 'time' for d in data['dimensions'])
                or data['time']['kind'] == 'range' or {m['kind'] for m in measures} == {'measure', 'distinct_count'})
    if ytd:
        from kge.semantics.coverage import to_date_selection
        try:
            data['time'] = to_date_selection(pieces, vocab, req.question).model_dump(mode='json')
        except (ValueError, KeyError) as exc:
            return Abstain(kind='abstain', spans=(), missing=(str(exc),))
    if data['time']['kind'] == 'all_available' and any(c.fact in (getattr(sem, 'partitioned', None) or {}) for c in selected):
        default_np = (getattr(engine, 'NAMED_PERIODS', None) or {}).get('default')
        if default_np == 'latest':
            data['time'] = {'kind': 'latest_released', 'calendar': name(calendar)}
        elif default_np:
            p_kind = 'month' if len(str(default_np)) in (7, 10) else 'year'
            p_val = str(default_np)[:7] if p_kind == 'month' else str(default_np)
            data['time'] = {'kind': 'single', 'period': {'kind': p_kind, 'value': p_val, 'calendar': name(calendar)}}
    if req.period and req.period.plan_kind == 'day' and all(
            engine.semantics.measure_declarations.get(c.measure, {}).get('snapshot') and c.aggregation == 'count' for c in selected):
        data['time'] = {'kind': 'range', 'first': {'kind': 'day', 'value': '0001-01-01', 'calendar': name(calendar)},
                        'last': {'kind': 'day', 'value': req.period.value, 'calendar': name(calendar)}}
    entity_selection = None
    if req.entity:
        identifier, literal = req.entity
        entity = vocab.entities.get(identifier)
        if entity is not None and len(entity['key']) == 1:
            entity_paths = [path_to(context, entity['table']) for context in selected]
            if entity_paths and None not in entity_paths and len(set(entity_paths)) == 1:
                try:
                    entity_selection = (entity['table'] + '.' + entity['key'][0], entity_paths[0], Decimal(int(literal)))
                except (ValueError, TypeError):
                    pass
    predicates, origins, problem = lexical_filters(req, pieces, selected, vocab,
        time_required=data['time']['kind'] != 'all_available' or any(dimension['kind'] == 'time' for dimension in data['dimensions']))
    if problem:
        return Abstain(kind='abstain', spans=(), missing=(problem,))
    for n, (column, op, value, qualified) in enumerate(predicates):
        from kge.semantics.coverage import separate_filter_parts
        lexical = list(origins.get((column, op, value, qualified), ()))
        lexical.extend(p for p in pieces if p.kind == 'predicate' and qualified in p.candidates
                       and p.value == (True if op == '= TRUE' else value)
                       and separate_filter_parts(p, pieces, req.question) and p not in lexical)
        if not lexical and (shadowed_filter(req, qualified, op, value, pieces, predicate_domain, predicate_exact=True,
                canonical_domain=canonical_predicate_domain, name_shadowing=cfg.graph)
                or paired_filter(qualified, op, value, pieces, vocab)):
            continue
        if any(separate_filter_parts(p, pieces, req.question) for p in lexical):
            continue
        if qualified not in vocab.entries:
            from kge.services.ask.engine.question import normalise_question
            normalized = normalise_question(req.question)
            if shadowed_filter(req, qualified, op, value, ledger(normalized, turn, vocab, baseline=True), engine, name_shadowing=cfg.graph):
                continue
            return Abstain(kind='abstain', spans=(), missing=('A requested predicate has no declared readable column.',))
        paths = {c.id: path_to(c, qualified.rsplit('.', 1)[0]) for c in selected}
        if any(p is None for p in paths.values()):
            return Abstain(kind='abstain', spans=(), missing=(f'A predicate on {qualified} has no declared role for every selected context.',))
        pred = {'id': 'f' + str(n), 'left': {'kind': 'column', 'name': name(qualified), 'path': [name(p) for p in next(iter(paths.values()))]}, 'evidence': []}
        if op in ('IS NULL', 'IS NOT NULL'):
            pred.update(kind='null', is_null=op == 'IS NULL')
        elif op in ('IN', 'NOT IN'):
            scalars = [typed_scalar(v, qualified, vocab) for v in value]
            if len(scalars) < (1 if op == 'NOT IN' else 2) or None in scalars:
                return Abstain(kind='abstain', spans=(), missing=(f'A list of values on {qualified} needs two or more of its declared values.',))
            pred.update(kind='in', values=scalars)
            if op == 'NOT IN':
                identifier, left = pred['id'], pred['left']
                pred = {'kind': 'any', 'id': identifier, 'children': [
                    {'kind': 'not', 'id': identifier + '-not', 'child': {**pred, 'id': identifier + '-in'}, 'evidence': []},
                    {'kind': 'null', 'id': identifier + '-null', 'left': left, 'is_null': True, 'evidence': []}], 'evidence': []}
        else:
            if op == '= TRUE':
                value = True
            scalar = typed_scalar(value, qualified, vocab)
            if scalar is None:
                return Abstain(kind='abstain', spans=(), missing=(f'A predicate on {qualified} needs a compatible literal.',))
            pred.update(kind='compare', op={'=': 'eq', '<>': 'ne', '!=': 'ne', '= TRUE': 'eq', '>': 'gt', '<': 'lt', '>=': 'ge', '<=': 'le',
                                          'CONTAINS': 'contains', 'STARTS WITH': 'starts_with'}[op], value=scalar)
        if (entity_selection is not None and pred['kind'] == 'compare' and pred['op'] == 'eq'
                and (qualified, tuple(role['id'] for role in pred['left']['path'])) == entity_selection[:2]
                and pred['value']['kind'] in ('integer', 'decimal')
                and Decimal(str(pred['value']['value'])) == entity_selection[2]):
            continue
        scoped = set().union(*(filter_scope(p, pieces, req.question, vocab) for p in lexical))
        if scoped or len(set(paths.values())) != 1:
            for output in data['measures']:
                path = paths[output['context']['id']]
                if not scoped or output['context']['id'] in scoped:
                    output['filters'].append(scoped_predicate(pred, output['id'], path))
        else:
            data['filters'].append(pred)
    join_filter_groups(data, pieces, filter_groups, vocab)
    if problem := size_filters(data, pieces, vocab):
        return problem
    output_filters(data, pieces, req.question)
    if req.entity:
        entity, value = req.entity
        if entity not in vocab.entities or len(vocab.entities[entity]['key']) != 1:
            return Abstain(kind='abstain', spans=(), missing=('An entity selector requires its complete declared key.',))
        table = vocab.entities[entity]['table']
        paths = [path_to(c, table) for c in selected]
        if any(p is None for p in paths) or len(set(paths)) != 1:
            return Abstain(kind='abstain', spans=(), missing=('No declared entity selector path.',))
        data['filters'].append({'kind': 'compare', 'id': 'entity', 'left': {'kind': 'column',
            'name': name(table + '.' + vocab.entities[entity]['key'][0]), 'path': [name(p) for p in paths[0]]},
            'op': 'eq', 'value': {'kind': 'integer', 'value': int(value)}, 'evidence': []})
    if problem := planshapes.parts(data, pieces, vocab, req.question):
        return Abstain(kind='abstain', spans=(), missing=(problem,))
    if base is not None:
        previous = base.plan.model_dump(mode='json', by_alias=True)
        if req.units:
            previous['measures'] = [{**new, 'mode': old['mode'], 'filters': old['filters']}
                                    for new, old in zip(data['measures'], previous['measures'])]
            if len(data['measures']) != len(base.plan.measures):
                return Abstain(kind='abstain', spans=(), missing=('A source edit must preserve the previous outputs.',))
        if req.period or req.latest:
            previous['time'] = data['time']
        for predicate in data['filters']:
            column = predicate.get('left', {}).get('name', {}).get('id')
            matches = [p for p in previous['filters'] if p.get('left', {}).get('name', {}).get('id') == column]
            if len(matches) > 1:
                return Abstain(kind='abstain', spans=(), missing=('A filter edit must identify one previous predicate.',))
            old = matches[0] if matches else None
            predicate['id'] = old['id'] if old else 'f' + str(len(previous['filters']))
            previous['filters'] = [p for p in previous['filters'] if p is not old] + [predicate]
        data = {**previous, 'obligations': []}
    plan = QueryPlan.model_validate(data)
    if base is not None:
        before, after = nodes(base.plan), nodes(plan)
        data['obligations'] = [o.model_dump(mode='json') for o in base.plan.obligations if all(
            t in after and semantic(before.get(t)) == semantic(after[t]) for t in o.targets)]
    targets = {n.id: n for n in plan.nodes()}
    targets['time'] = plan.time
    for i, piece in enumerate(pieces):
        applied = []
        for identifier, node in targets.items():
            if piece.kind == 'name' and set(piece.candidates) & references(node, vocab):
                applied.append(identifier)
            elif piece.kind == 'predicate' and predicate_supported(piece, node, vocab, plan):
                applied.append(identifier)
            elif piece.kind == 'time' and identifier == 'time':
                applied.append(identifier)
            elif piece.kind == 'operator' and piece.operation in operations(node, vocab, plan) and aggregation_owner(piece, node, per_output, vocab):
                applied.append(identifier)
            elif piece.kind == 'operator' and piece.operation == 'dimension' and identifier in {d.id for d in plan.dimensions}:
                applied.append(identifier)
            elif piece.kind == 'literal' and identifier == 'time' and piece.text in plan.time.model_dump_json():
                applied.append(identifier)
            elif piece.kind == 'literal' and literal_supported(piece, [node]):
                applied.append(identifier)
        if piece.kind == 'name' and set(piece.candidates) & referenced_targets(list(plan.measures), vocab) and not applied:
            applied = [m.id for m in plan.measures]
        data['obligations'].append({'id': 'o' + str(i), 'spans': [piece.span.model_dump()], 'kind': 'name',
                                    'targets': applied, 'state': 'applied' if applied else 'unresolved'})
    if cohort and base is None and not (plan.comparisons or plan.derived or plan.order):
        count_words = [o['targets'] for piece, o in zip(pieces, data['obligations']) if piece.kind == 'operator' and piece.operation == 'count']
        counted_outputs = {t for targets in count_words for t in targets}
        unrequested = {m.id for m in plan.measures if m.kind == 'distinct_count' and m.id not in counted_outputs
                       and mapping_role(runtime.output_metadata.get(m.context.id.removeprefix('cohort:'), {})) == 'flag'}
        if unrequested and len(unrequested) < len(plan.measures) and len(count_words) <= len(counted_outputs):
            data['measures'][:] = [m for m in data['measures'] if m['id'] not in unrequested]
            for obligation in data['obligations']:
                obligation['targets'] = [t for t in obligation['targets'] if t not in unrequested]
                obligation['state'] = obligation['state'] if obligation['targets'] else 'unresolved'
    for i, obligation in enumerate(data['obligations']):
        obligation['id'] = 'o' + str(i)
    plan = QueryPlan.model_validate(data)
    if base is None and (expanded or len(data['measures']) != len(measures)):
        from kge.semantics.coverage import ground_plan
        plan = ground_plan(plan, req.question, turn, vocab)
    return Propose(kind='propose', base_turn=base.turn_id if base is not None else None, plan=plan)


def followup(engine, req, vocab, cfg, history, turn):
    if not history or history[-1].clarification is not None or req.terms_found or req.counted or req.definitional or req.special:
        return None
    base = next((h for h in reversed(history) if h.plan is not None), None)
    if base is None or not (req.period or req.latest or req.units or req.filters or req.entity):
        return None
    contexts = [vocab.contexts[m.context.id] for m in base.plan.measures]
    terms = list(dict.fromkeys(c.term for c in contexts))
    counts = [m for m in base.plan.measures if m.kind == 'distinct_count']
    merged = replace(req, term=terms[0], terms_found=terms, agg='count' if counts else req.agg,
                     counted=counts[0].entity.id if len(counts) == 1 else None)
    cohort = all(c.id.startswith('cohort:') for c in contexts)
    return read(engine, merged, vocab, cfg, turn, cohort=cohort, base=base)


class Coded:
    """The coded adapter of the rules-planner port (`rulesport`): a follow-up on the conversation's last plan where
    `followup` builds one, else `read` on the vocabulary a clarification's choice narrows, its shape steps the coded
    builders in `planshapes`. The reply is exactly the one public Ask read before the port."""
    name = 'coded'

    def plan(self, question):
        return (followup(question.engine, question.request, question.vocabulary, question.cfg, question.history, question.turn)
                or read(question.engine, question.request, clarified_vocabulary(question.history, question.vocabulary, question.question),
                        question.cfg, question.turn, question.cohort))


CODED = Coded()


def mapping_role(item):
    if item.get('dtype') == 'BOOLEAN':
        return 'flag'
    return 'event' if item.get('col', '').lower().endswith(('_id', '_no', '_ref')) else 'measure'


def cohort_terms(engine, req):
    mappings = snapshot(engine).mapping_declarations
    found = {t: [i for i in mappings if i['term'] == t] for t in req.terms_found}
    groups = [t for t, items in found.items() if items and any(mapping_role(i) != 'measure' for i in items)]
    return groups if groups and (req.counted or any(mapping_role(i) == 'flag' for t in groups for i in found[t])) and len([t for t, items in found.items() if items]) > 1 and not req.definitional and not req.special else []


def cohort_snapshot(engine, sem, req):
    groups = cohort_terms(engine, req)
    if not groups or len(req.units) != 1:
        return sem
    unit = req.units[0]
    entity_id = population(engine, sem, unit)
    if entity_id is None:
        return sem
    entity = sem.entities[entity_id]
    contexts = [c for c in sem.contexts.values() if c.unit == unit and c.term in req.terms_found and c.id in sem.output_metadata]
    joins = []
    for join in sem.declared.values():
        if not join.through and not join.alias and {join.source.split('.')[0], join.target.split('.')[0]} == {unit}:
            joins.extend((replace(join, required=False), replace(join, id=join.id + ':reverse', source=join.target, target=join.source, required=False)))
    contexts = [Context(c.id, c.label, c.description, c.term, c.measure, c.expression, c.aggregation, c.grain,
                        c.fact, c.values, c.filters, c.selectors, tuple({j.id: j for j in (*joins, *c.joins)}.values()),
                        c.ytd_calendar, c.bindings, c.version_dates)
                for c in contexts]
    group_contexts = sorted((c for c in contexts if c.term in groups), key=lambda c: mapping_role(sem.output_metadata[c.id]) != 'flag')
    if len(group_contexts) != len(groups):
        return sem
    narrowed = copy.copy(sem)
    narrowed.contexts = dict(sem.contexts)
    for ctx in contexts:
        path = path_to(ctx, entity['table'])
        binding = engine.PERIODS.get(ctx.fact)
        if path is None or binding is None:
            return sem
        joins = tuple(replace(next(j for j in ctx.joins if j.id == identifier), required=True) for identifier in path)
        selectors = []
        for other in group_contexts:
            if other.id == ctx.id:
                break
            column = sem.output_metadata[other.id]['col']
            other_binding = engine.PERIODS.get(other.fact)
            if other_binding is None or entity['key'][0] not in sem.tables[other.fact]:
                return sem
            path = path_to(other, entity['table'])
            if other.fact == entity['table']:
                predicate = f"{bare(other.fact)}.{entity['key'][0]} = {bare(entity['table'])}.{entity['key'][0]}"
            elif path is not None and len(path) == 1:
                predicate = next(j.condition for j in other.joins if j.id == path[0])
            else:
                return sem
            predicate += f" AND date_trunc('month', {bare(other.fact)}.{other_binding['column']}) = date_trunc('month', {bare(ctx.fact)}.{binding['column']})"
            if sem.output_metadata[other.id]['dtype'] == 'BOOLEAN':
                predicate += f' AND {bare(other.fact)}.{column}'
            selectors.append(Rule('cohort:' + other.id, other.term, predicate, other.fact, 'include'))
        narrowed.contexts.pop(ctx.id, None)
        narrowed.contexts['cohort:' + ctx.id] = Context('cohort:' + ctx.id, ctx.label, ctx.description, ctx.term, ctx.measure,
            ctx.expression, ctx.aggregation, ctx.grain, ctx.fact, ctx.values, ctx.filters, (*ctx.selectors, *selectors), joins,
            ctx.ytd_calendar, ctx.bindings, ctx.version_dates)
    return narrowed
