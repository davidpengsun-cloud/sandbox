"""The bounded rules interpreter emits data, never executable bypasses."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import replace
from types import SimpleNamespace

from kge.semantics.coverage import filter_scope, ledger, references, referenced_targets, operations, context_names, one_meaning
from kge.semantics.plan import Abstain, Binding, Clarify, Name, Propose, QueryPlan, Span
from kge.semantics.planhistory import nodes, semantic
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary
from kge.semantics.query import Context, Rule, Semantics, NUMERIC, bare, snapshot_grain
from kge.services.ask.engine.question import parse, restricted_asked, restricted_named, plural_forms, CURRENCY_NAMES, CURRENCY_SYMBOLS, UNPLACED_RANGE
from kge.services.ask.engine import planshapes


def name(identifier):
    return {'id': identifier, 'evidence': []}


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


def matched_contexts(engine, req, contexts):
    if not req.units:
        return contexts
    matched = [c for c in contexts if c.unit in req.units]
    scoped = matched or [c for c in contexts if any(s.table.split('.')[0] in req.units for s in c.selectors)
        and any(j.through and ({j.source.split('.')[0], j.target.split('.')[0]} & set(req.units)) for j in c.joins)]
    return scoped or ([] if len(req.units) == 1 and len(req.terms_found) > 1 and cohort_terms(engine, req) else contexts)


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
    for phrase, identifier in {**engine.UNIT_WORDS, **engine.SOURCE_SETS, **engine.ENTITY_WORDS}.items():
        aliases.append((phrase, identifier))
        if phrase and phrase[-1].isalpha():
            aliases.append((plural_forms(phrase)[1], identifier))
    aliases.extend((plural_forms(e['label'])[1], identifier) for identifier, e in sem.entities.items())
    aliases.extend((label, unit) for unit, label in engine.UNIT_LABEL.items())
    aliases.extend((phrase, column) for column, phrase in engine.g.domain().get('business', {}).items())
    aliases.extend((phrase, column) for phrase, column in engine.GROUP_BY.items())
    aliases.extend((phrase, column) for phrase, columns in engine.ACRONYMS.items() for column in columns)
    values = []
    for code in sorted({m.currency for m in sem.measure_specs.values() if m.currency}):
        values.append(Entry(code, 'currency', code, None))
        aliases.extend((form, code) for phrase in CURRENCY_NAMES.get(code, '').split('|') if phrase for form in plural_forms(phrase))
        aliases.extend((symbol, code) for symbol, target in CURRENCY_SYMBOLS.items() if target == code)
    for n, row in enumerate(engine.g.domain()['vocabulary']):
        params = row['params']
        if row['kind'] == 'value':
            for value in params.get('values', ()):
                column = params['column']
                values.append(Entry(f'value:{n}:{value}', 'value', str(value), column.split('.')[0], column=column, value=value))
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
    selected = [c for c in sem.contexts.values() if c.term in terms and c.bindings]
    bound_columns = ()
    allowed_tables = set(sem.tables)
    if selected:
        bindings = {a: b for c in selected for a, b in c.bindings.items()}
        bound_columns = tuple(bindings)
        retained = [c for c in sem.contexts.values() if c.term in terms] if history else selected
        allowed_tables = {c.fact for c in retained} | {t for c in retained for j in c.joins for t in (j.source, j.target)}
        allowed_tables.update(r.table for c in retained for r in (*c.filters, *c.selectors))
        aliases = [(phrase, bindings.get(identifier, identifier) if isinstance(identifier, str) else identifier) for phrase, identifier in aliases]
        values = [e for e in values if e.column not in bindings]
        sem = copy.copy(sem)
        sem.contexts = {i: c for i, c in sem.contexts.items() if c.fact in allowed_tables
                        and all(j.source in allowed_tables and j.target in allowed_tables and not j.through for j in c.joins)
                        and all(r.table in allowed_tables for r in (*c.filters, *c.selectors))}
        sem.d = copy.copy(sem.d)
        sem.d.GROUP_BY = {p: bindings.get(c, c) for p, c in sem.d.GROUP_BY.items()}
        sem.d.VOCABULARY = [row for row in sem.d.VOCABULARY if row['params'].get('column') not in bindings]
        words = lexical_vocabulary(sem.d.VOCABULARY)
        sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
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


def read(engine, req, vocab, cfg, turn='turn', cohort=False, base=None):
    contexts = [c for c in vocab.contexts.values() if c.term in req.terms_found]
    bindings = {a: b for c in contexts for a, b in (c.bindings or {}).items()}
    if bindings:
        req = copy.copy(req)
        req.group_by = [bindings.get(c, c) for c in req.group_by]
        scoped = parse(req.question, engine.term_names, vocab.semantics.d)
        req.filters, req.filtered = scoped.filters, scoped.filtered
        req.counted = bindings.get(req.counted, req.counted)
        req.entity = (bindings.get(req.entity[0], req.entity[0]), req.entity[1]) if req.entity else None
    pieces = ledger(req.question, turn, vocab)
    ytd = next((p for p in pieces if p.operation == 'ytd'), None) if contexts and all(c.ytd_calendar for c in contexts) else None
    endpoints = planshapes.periods(pieces)
    if ytd and req.problem == UNPLACED_RANGE and len(endpoints) == 1 and endpoints[0] and endpoints[0].kind == 'month':
        req = replace(req, period=endpoints[0], problem=None)
    if req.problem and len(endpoints) == 2 and all(endpoints) and (
            req.problem == UNPLACED_RANGE or req.problem.startswith('Please give a valid period:')):
        req = replace(req, period=endpoints[0], problem=None)
    if req.problem:
        return Abstain(kind='abstain', spans=(), missing=(req.problem,))
    sem = vocab.semantics
    runtime = snapshot(engine, cfg.graph)
    selected, counts = [], {}
    count_entities = {i for p in pieces for i in p.candidates if i in vocab.entities}
    counted = req.counted or (next(iter(count_entities)) if len(count_entities) == 1 else None)
    retained = {vocab.contexts[i].term for p in pieces for i in p.candidates if i in vocab.contexts}
    retained.update(ctx.term for p in pieces for ctx in vocab.contexts.values()
                    if (not (scoped := filter_scope(p, pieces, req.question, vocab)) or ctx.id in scoped) and (
                        set(p.candidates) & context_names(ctx, vocab) or
                        (p.kind == 'predicate' and ctx.term in req.terms_found and any(i.rsplit('.', 1)[0] == ctx.fact for i in p.candidates))))
    explicit = {p.candidates[0] for p in pieces if cfg.glossary and len(p.candidates) == 1
                and vocab.entries[p.candidates[0]].kind == 'context'}
    shared = planshapes.shared_contexts(pieces, vocab) if any(p.operation == 'comparison' for p in pieces) else ()
    explicit.update(shared)
    terms = list(dict.fromkeys([*(t for t in req.terms_found if t in retained),
                               *(vocab.contexts[i].term for i in sorted(explicit))]))
    terms.extend(vocab.contexts[i].term for i in shared if vocab.contexts[i].term not in terms)
    if base is not None:
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
            if req.agg == 'avg' and ctx.aggregation != 'avg':
                return Abstain(kind='abstain', spans=(), missing=('No certified average measure is declared for this term.',))
            if (req.agg == 'count' and ((not cohort and ctx.aggregation != 'count')
                    or (cohort and mapping_role(runtime.output_metadata.get(ctx.id.removeprefix('cohort:'), {})) != 'measure'))):
                entity = counted
                metadata = runtime.output_metadata.get(ctx.id.removeprefix('cohort:'), {})
                if entity is None and runtime.measure_specs.get(ctx.measure) and runtime.measure_specs[ctx.measure].population in vocab.entities:
                    entity = runtime.measure_specs[ctx.measure].population
                if entity is None and metadata.get('dtype') == 'BOOLEAN':
                    entity = population(engine, sem, unit)
                if entity is None and cohort:
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
        membership = {'kind': 'membership', 'id': 'membership', 'entity': name(entity),
                      'relation': name(relation.id), 'context': name('context:count:' + entity), 'filters': [], 'evidence': []}
    if not selected and any(p.operation == 'membership' for p in pieces):
        entities = list(dict.fromkeys(i for p in pieces for i in p.candidates if i in vocab.entities))
        if len(entities) == 2:
            near, far = (vocab.contexts['context:count:' + i] for i in entities)
            relations = [j for j in near.joins if j.through and j.target == far.fact and j.id in vocab.entries]
            if len(relations) == 1:
                selected.append(near)
                counts[near.id] = entities[0]
                membership = {'kind': 'membership', 'id': 'membership', 'entity': name(entities[1]),
                              'relation': name(relations[0].id), 'context': name(far.id), 'filters': [], 'evidence': []}
    if not selected and req.agg == 'count' and counted in vocab.entities:
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
    if not selected:
        if not any(p.kind == 'unknown' for p in pieces):
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
    for n, ctx in enumerate(selected):
        item = {'kind': 'distinct_count' if ctx.id in counts else 'measure', 'id': ('c' + str(sum(c.id in counts for c in selected[:n + 1])) if ctx.id in counts else 'm' + str(sum(c.id not in counts for c in selected[:n + 1]))) if cohort else 'm' + str(n),
                'context': name(ctx.id), 'mode': req.time_mode, 'filters': []}
        item['entity' if ctx.id in counts else 'measure'] = name(counts.get(ctx.id, ctx.measure))
        measures.append(item)
    if not cfg.graph and req.period and req.period.kind == 'calendar_quarter' and any(
            'fiscal_quarter' in vocab.tables[c.fact] for c in selected):
        return Abstain(kind='abstain', spans=(), missing=('The schema calendar heuristic does not establish the requested quarter.',))
    calendar = engine.PERIODS.get(selected[0].fact, {}).get('calendar') or next(iter(vocab.calendars), None)
    if req.period and req.period.kind == 'fiscal_quarter':
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
        for i in p.candidates if vocab.entries[i].kind == 'column')]))
    if any(p.operation in ('asc', 'desc') for p in pieces):
        grouped = [i for p in pieces if p.operation == 'dimension' for i in p.candidates if vocab.entries[i].kind == 'column']
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
    for n, column in enumerate(grouping):
        bindings = []
        for ctx in selected:
            path = path_to(ctx, column.rsplit('.', 1)[0])
            if path is None:
                return Abstain(kind='abstain', spans=(), missing=('No declared role reaches the requested dimension.',))
            bindings.append({'context': name(ctx.id), 'column': name(column), 'path': [name(p) for p in path], 'level': None})
        dimensions.append({'kind': 'column', 'id': 'd' + str(n), 'bindings': bindings})
    data = {'schema': 'query-plan/v1', 'layout': 'aligned', 'measures': measures, 'dimensions': dimensions,
            'filters': [membership] if membership else [], 'time': time_of(req, calendar), 'comparisons': [], 'derived': [], 'order': [], 'limit': None, 'obligations': []}
    if problem := planshapes.compose(data, req, pieces, vocab):
        return Abstain(kind='abstain', spans=(), missing=(problem,))
    expanded = (data['comparisons'] or data['derived'] or data['order'] or any(d['kind'] == 'time' for d in data['dimensions'])
                or data['time']['kind'] == 'range')
    if ytd and req.period and req.period.kind == 'month':
        data['time'] = {'kind': 'ytd', 'through': {'kind': 'month', 'value': req.period.value[:7], 'calendar': name(calendar)}, 'calendar': name(calendar)}
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
    for n, ((column, op, value), qualified) in enumerate(zip(req.filters, req.filtered)):
        lexical = [p for p in pieces if p.kind == 'predicate' and qualified in p.candidates and p.value == value]
        if re.search(r'\b(?:inflows?|outflows?)\b', req.question, re.I) and not lexical:
            continue
        if (lexical and all(re.fullmatch(r'(?:inflows?|outflows?)', p.text, re.I) for p in lexical)
                and all(qualified.rsplit('.', 1)[0] != c.fact for c in selected)):
            continue
        from kge.semantics.coverage import separate_filter_parts
        if any(re.fullmatch(r'(?:inflows?|outflows?)', p.text, re.I) and separate_filter_parts(p, pieces, req.question) for p in lexical):
            continue
        if qualified not in vocab.entries:
            return Abstain(kind='abstain', spans=(), missing=('A requested predicate has no declared readable column.',))
        paths = {c.id: path_to(c, qualified.rsplit('.', 1)[0]) for c in selected}
        if any(p is None for p in paths.values()):
            return Abstain(kind='abstain', spans=(), missing=(f'A predicate on {qualified} has no declared role for every selected context.',))
        pred = {'id': 'f' + str(n), 'left': {'kind': 'column', 'name': name(qualified), 'path': [name(p) for p in next(iter(paths.values()))]}, 'evidence': []}
        if op == 'IS NULL':
            pred.update(kind='null', is_null=True)
        else:
            if op == '= TRUE':
                value = True
            scalar = {'kind': 'boolean' if isinstance(value, bool) else 'integer' if isinstance(value, int) else 'text', 'value': value}
            pred.update(kind='compare', op={'=': 'eq', '= TRUE': 'eq', '>': 'gt', '<': 'lt', '>=': 'ge', '<=': 'le'}[op], value=scalar)
        scoped = set().union(*(filter_scope(p, pieces, req.question, vocab) for p in pieces
            if p.kind == 'predicate' and qualified in p.candidates and p.value == value))
        if scoped or len(set(paths.values())) != 1:
            for output in data['measures']:
                path = paths[output['context']['id']]
                if not scoped or output['context']['id'] in scoped:
                    output['filters'].append({**pred, 'id': pred['id'] + '-' + output['id'],
                        'left': {**pred['left'], 'path': [name(p) for p in path]}})
        else:
            data['filters'].append(pred)
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
            elif piece.kind == 'predicate' and set(piece.candidates) & references(node, vocab) and piece.operation in operations(node, vocab, plan):
                applied.append(identifier)
            elif piece.kind == 'time' and identifier == 'time':
                applied.append(identifier)
            elif piece.kind == 'operator' and piece.operation in operations(node, vocab, plan):
                applied.append(identifier)
            elif piece.kind == 'operator' and piece.operation == 'dimension' and identifier in {d.id for d in plan.dimensions}:
                applied.append(identifier)
            elif piece.kind == 'literal' and identifier == 'time' and piece.text in plan.time.model_dump_json():
                applied.append(identifier)
        if piece.kind == 'name' and set(piece.candidates) & referenced_targets(list(plan.measures), vocab) and not applied:
            applied = [m.id for m in plan.measures]
        data['obligations'].append({'id': 'o' + str(i), 'spans': [piece.span.model_dump()], 'kind': 'name',
                                    'targets': applied, 'state': 'applied' if applied else 'unresolved'})
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
