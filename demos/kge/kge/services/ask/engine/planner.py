"""Numeric coordination shared by public Ask and its rules baseline."""
from __future__ import annotations

import datetime
import json
from decimal import Decimal, localcontext

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.services.ask.engine import runtrace
from kge.pipeline.sqlguard import gate
from kge.pipeline import questionrun
from kge.pipeline.trace import transient_failure, graph_read, graph_recording, warehouse_statement, QueryCancelled
from kge.ports import AUTHORED, WarehouseSet
from kge.semantics.compile import total_outputs, total_scopes, warehouse_totals, compile_plan, resolve_releases, latest_released_offer
from kge.semantics.planperiods import release_months
from kge.semantics.coverage import ledger, ground_plan
from kge.semantics.plan import Cell, Result, ResultRow, ResultColumn, Text
from kge.semantics.planhistory import validate, replacement, clarified_vocabulary, removed_nodes
from kge.semantics.plancheck import CheckedPlan, Decision, check_plan, check_request
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, warehouse_guard


def execute(warehouses: WarehouseSet, statement, *, on_ready=None, on_progress=None):
    """The existing session, gate, deadline and transient-error contract, once per statement."""
    try:
        progress = {'on_progress': on_progress} if on_progress is not None else {}
        session = (warehouses.attached(list(statement.units), AUTHORED, **progress) if len(statement.units) > 1
                   else warehouses.cursor(statement.units[0], AUTHORED, **progress))
        with session as con:
            runtrace.stage('gate')
            guard = warehouse_guard(gate(con, statement.sql, statement.allow))
            if not guard['passed']:
                return guard, None
            if on_ready is not None:
                on_ready(guard)
            runtrace.stage('execute')
            return guard, con.execute(statement.sql).fetchall()
    except (QueryCancelled, RequestDeadlineExceeded):
        raise
    except Exception as exc:
        transient_failure(exc)
        return {'passed': False, 'failed_at': 'execute', 'problems': [WAREHOUSE_PROBLEM]}, None


def execute_on_runtime(warehouses: WarehouseSet, statement, runtime_id: str, measures: tuple[str, ...], on_ready=None):
    stmt_dict = {'unit': statement.units[0] if statement.units else '', 'units': list(statement.units),
                 'sql': statement.sql, 'allow': statement.allow}

    def ready(guard, meta):
        graph_read("runtime", (meta, warehouse_guard(guard)))
        if on_ready is not None:
            on_ready(warehouse_guard(guard), meta)

    if questionrun.active():
        guard, rows, meta = questionrun.execute(warehouses, stmt_dict, runtime_id, measures, ready, runtrace.stage)
        guard = warehouse_guard(guard)
        graph_read("runtime", (meta, guard))
        return guard, rows, meta

    try:
        with warehouse_statement() as progress:
            if runtime_id == 'reference':
                part_guard, meta = warehouses.reference_runtime_meta(stmt_dict, measures=measures)
                if part_guard is not None:
                    graph_read('runtime', (meta, warehouse_guard(part_guard)))
                    return warehouse_guard(part_guard), None, meta
                events = ({'on_ready': lambda guard: ready(guard, meta), 'on_progress': progress}
                          if on_ready is not None or progress is not None else {})
                guard, rows = execute(warehouses, statement, **events)
            else:
                guard, rows, meta = warehouses.execute_statement(
                    stmt_dict, runtime_id=runtime_id, measures=measures, on_stage=runtrace.stage,
                    on_ready=ready, **({'on_progress': progress} if progress is not None else {}))
        guard = warehouse_guard(guard)
        graph_read("runtime", (meta, guard))
        if not guard['passed']:
            return guard, None, meta
        return guard, rows, meta
    except (QueryCancelled, RequestDeadlineExceeded):
        raise
    except Exception as exc:
        transient_failure(exc)
        return {'passed': False, 'failed_at': 'execute', 'problems': [WAREHOUSE_PROBLEM]}, None, {'sql': statement.sql}


def scalar(value):
    if value is None:
        return {'kind': 'null'}
    if isinstance(value, bool):
        return {'kind': 'boolean', 'value': value}
    if isinstance(value, int):
        return {'kind': 'integer', 'value': value}
    if isinstance(value, (float, Decimal)):
        return {'kind': 'decimal', 'value': format(Decimal(str(value)), 'f')}
    if isinstance(value, datetime.datetime):
        return {'kind': 'text', 'value': value.isoformat()}
    if isinstance(value, datetime.date):
        return {'kind': 'date', 'value': value.isoformat()}
    return {'kind': 'text', 'value': str(value)}


def result_of(compiled, rows, citations):
    dimensions = tuple(c for c in compiled.columns if c.kind == 'dimension')
    outputs = tuple(c for c in compiled.columns if c.kind != 'dimension')
    decoded, totals = [], {}
    summed = total_outputs(compiled.checked)
    scopes = total_scopes(compiled.checked.plan) if summed else ()
    hidden_totals = warehouse_totals(compiled.checked)
    both = any(o.direction == 'both' for o in compiled.checked.plan.order)
    for row in rows:
        panel = row[0] if compiled.checked.plan.layout == 'separate' else None
        row = row[1:] if compiled.checked.plan.layout == 'separate' else row
        cells = []
        for i, output in enumerate(outputs):
            offset = len(dimensions) + i
            reason = row[len(dimensions) + len(outputs) + i]
            refs = tuple(dict.fromkeys(ref for source in output.lineage for ref in citations.get(source, ())))
            cells.append(Cell(value=scalar(row[offset]), citations=refs, null_reasons=(reason,) if reason else ()))
        offset = len(dimensions) + 2 * len(outputs)
        for scope in scopes if hidden_totals else ():
            dims = tuple(row[i] if d.id in scope else None for i, d in enumerate(dimensions))
            totals[panel, scope, dims] = dict(zip(summed, zip(row[offset:offset + 2 * len(summed):2], row[offset + 1:offset + 2 * len(summed):2])))
            offset += 2 * len(summed)
        ranking = dict(end=row[offset], rank=int(row[offset + 1])) if both else {}
        values, labels = dimension_values(compiled, row)
        metadata = {'dimension_labels': tuple(labels)} if any(v is not None for v in labels) else {}
        decoded.append(ResultRow(panel=panel, dimensions=tuple(values), cells=tuple(cells), **metadata, **ranking))
    if not hidden_totals:
        for panel in dict.fromkeys(r.panel for r in decoded):
            members = [r for r in decoded if r.panel == panel]
            if not scopes:
                continue
            values = {}
            for i, output in enumerate(outputs):
                if output.id not in summed:
                    continue
                if any(r.cells[i].value.kind == 'null' for r in members):
                    values[output.id] = None, 'missing_contributing_observation'
                else:
                    amounts = [Decimal(str(r.cells[i].value.value)) for r in members]
                    with localcontext() as context:
                        context.prec = max(v.adjusted() for v in amounts) - min(v.as_tuple().exponent for v in amounts) + len(str(len(amounts))) + 2
                        total = sum(amounts, Decimal(0))
                    values[output.id] = int(total) if output.type == 'integer' else total, None
            totals[panel, (), tuple(None for _ in dimensions)] = values
    for panel in dict.fromkeys(r.panel for r in decoded):
        for scope in scopes:
            for (p, s, dims), values in totals.items():
                if p == panel and s == scope:
                    decoded.append(total_row(compiled, outputs, panel, dims, dict(values), citations))
    return Result(columns=compiled.columns, rows=tuple(decoded))


def dimension_values(compiled, raw_values):
    dimensions = tuple(c for c in compiled.columns if c.kind == 'dimension')
    values, labels = [], []
    for dim, raw in zip(dimensions, raw_values):
        binding = next(d for d in compiled.checked.plan.dimensions if d.id == dim.id)
        paired = binding.kind != 'time' and any(r['kind'] == 'code_name_pair'
            and r['params']['code'] == binding.bindings[0].column.id
            and r['params']['name'] in compiled.checked.vocabulary.entries
            for r in getattr(compiled.checked.vocabulary.semantics.d, 'VOCABULARY', ()))
        item = json.loads(raw, parse_float=Decimal) if paired and raw is not None else None
        value = item['code'] if item else raw
        if item and value is not None:
            if dim.type == 'integer':
                value = int(value)
            elif dim.type == 'decimal':
                value = Decimal(str(value))
            elif dim.type == 'date':
                value = datetime.date.fromisoformat(value)
        values.append(scalar(value))
        labels.append(item['name'] if item else None)
    return values, labels


def total_row(compiled, outputs, panel, dims, values, citations):
    for derived in compiled.checked.plan.derived:
        if derived.kind in ('ratio', 'difference', 'percent_change', 'share'):
            first = derived.input if derived.kind == 'share' else derived.numerator
            second = derived.id + '/denominator' if derived.kind == 'share' else derived.denominator
            numerator, nr = values.get(first, (None, None))
            denominator, dr = values.get(second, (None, None))
            reason = nr or dr
            value = None
            if numerator is not None and denominator is not None:
                numerator, denominator = Decimal(str(numerator)), Decimal(str(denominator))
                with localcontext() as context:
                    context.prec = max(38, max(numerator.adjusted(), denominator.adjusted()) - min(numerator.as_tuple().exponent, denominator.as_tuple().exponent) + 2)
                    value = numerator - denominator if derived.kind == 'difference' else None
                    if derived.kind != 'difference':
                        if denominator:
                            value = (numerator - denominator if derived.kind == 'percent_change' else numerator) / (abs(denominator) if derived.kind in ('ratio', 'percent_change') else denominator)
                        else:
                            reason = 'zero_denominator'
            values[derived.id] = value, reason
    cells = []
    for output in outputs:
        value, reason = values.get(output.id, (None, None))
        refs = tuple(dict.fromkeys(ref for source in output.lineage for ref in citations.get(source, ())))
        cells.append(Cell(value=scalar(value), citations=refs if output.id in values else (), null_reasons=(reason,) if reason else ()))
    values, labels = dimension_values(compiled, dims)
    metadata = {'dimension_labels': tuple(labels)} if any(v is not None for v in labels) else {}
    return ResultRow(panel=panel, dimensions=tuple(values), cells=tuple(cells), total=True, **metadata)


def dimension_value(row, index):
    value = row['dimensions'][index].get('value')
    labels = row.get('dimension_labels', ())
    label = labels[index] if index < len(labels) else None
    return f'{value} · {label}' if label is not None else value


def measure_label(measure, plan, vocabulary, question, turn):
    pieces = ledger(question, turn, vocabulary)
    names = [p for p in pieces if p.kind == 'name' and measure.context.id in p.candidates]
    for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
        rule = row['params']
        if row['kind'] != 'predicate' or not rule.get('modifier') or not rule.get('label'):
            continue
        nouns = [noun for noun in rule.get('with', ()) if measure.context.id in {
            e.id for e in vocabulary.candidates(noun) if e.kind == 'context'}]
        if nouns and any(f.kind == 'compare' and f.op == 'eq' and f.left.kind == 'column'
                         and f.left.name.id == rule['column'] and f.value.value == rule.get('value')
                         for f in (*plan.filters, *measure.filters)):
            return rule['label'] + ' ' + (names[0].text if names else nouns[0])
    return vocabulary.contexts[measure.context.id].label


def labelled_result(result, plan, vocabulary, question, turn):
    """Attach display names and question order without changing output ids or cell alignment."""
    if sum(c.kind != 'dimension' for c in result.columns) < 2 and not any(r.dimension_labels for r in result.rows):
        return result
    def mentions(ids):
        return sorted((s.start, question[s.start:s.end]) for o in plan.obligations
                      if set(o.targets) & set(ids) for s in o.spans if s.turn == turn)

    measures = {m.id: m for m in plan.measures}
    derived = {d.id: d for d in plan.derived}
    labels, positions = {}, {}
    filtered = {}
    member_columns = {row['params'][key] for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                      if row['kind'] == 'code_name_pair' for key in ('code', 'name')}
    for m in plan.measures:
        modifiers = {phrase.casefold() for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                     if row['kind'] == 'predicate' and row['params'].get('modifier') for phrase in row['params']['phrases']}
        parts = [p for p in mentions([p.id for p in m.filters if not (
            p.kind == 'compare' and p.left.kind == 'column' and p.left.name.id in member_columns)])
                 if p[1].casefold() not in modifiers]
        own = mentions([m.id])
        if m.kind == 'distinct_count':
            names = [p for p in own if any(e.kind == 'entity' and e.id == m.entity.id
                                          for e in vocabulary.candidates(p[1]))]
            label = names[0][1].capitalize() if names else vocabulary.entities[m.entity.id]['label']
            own = names or own
        else:
            qualified = measure_label(m, plan, vocabulary, question, turn)
            label = parts[0][1].capitalize() if parts else qualified
            if parts and qualified != vocabulary.contexts[m.context.id].label:
                label = qualified + ' · ' + label
        labels[m.id] = label
        positions[m.id] = (parts or own or [(len(question), '')])[0][0]
        if parts:
            filtered.setdefault(m.context.id, []).append(m.id)
    for m in plan.measures:
        siblings = filtered.get(m.context.id, [])
        if m.kind == 'measure' and not m.filters and {labels[i].casefold() for i in siblings} == {'inflows', 'outflows'}:
            labels[m.id] = 'Net'
            net = [p for p in mentions([m.id]) if p[1].casefold() == 'net']
            positions[m.id] = net[0][0] if net else max(positions[i] for i in siblings) + .5
    for c in plan.comparisons:
        pairs = [(c.id + '/base', c.left, 'Base'), (c.id + '/other', c.right, 'Other')] if c.kind == 'contexts' else [
            (f'{c.id}/{i}/{side}', i, side.capitalize()) for i in c.inputs for side in ('base', 'other')]
        for identifier, source, side in pairs:
            period = plan.time if side == 'Base' else c.other if c.kind == 'explicit_period' else None
            suffix = period.period.value if period is not None and period.kind == 'single' else side
            labels[identifier] = labels[source] + ' · ' + suffix
    columns = []
    for column in result.columns:
        d = derived.get(column.id)
        label, fmt = labels.get(column.id), None
        position = positions.get(column.id)
        if d:
            label = {'ratio': 'Ratio', 'difference': 'Difference', 'percent_change': '% change',
                     'share': 'Share', 'running_total': 'Running total'}[d.kind]
            fmt = 'percent' if d.kind == 'percent_change' else 'ratio' if d.kind == 'ratio' and column.currency is None else None
            own = mentions([d.id])
            position = own[0][0] if own else None
        if column.kind == 'dimension':
            dim = next(d for d in plan.dimensions if d.id == column.id)
            label = dim.grain.replace('_', ' ').capitalize() if dim.kind == 'time' else vocabulary.entries[dim.bindings[0].column.id].label
        metadata = {'label': label or column.id}
        if fmt:
            metadata['format'] = fmt
        if position is not None and not plan.comparisons:
            metadata['display_order'] = float(position)
        columns.append(column.model_copy(update=metadata))
    if not plan.dimensions and len(measures) > 1 and plan.time.kind == 'single' and plan.time.period.kind == 'month':
        columns.insert(0, ResultColumn(id='/period', label='Month', kind='dimension', type='text', currency=None, lineage=()))
        rows = tuple(r.model_copy(update={'dimensions': (Text(kind='text', value=plan.time.period.value),)}) for r in result.rows)
    else:
        rows = result.rows
    return Result(columns=tuple(columns), rows=rows)


def display_outputs(result):
    outputs = [(c, i) for i, c in enumerate(c for c in result['columns'] if c['kind'] != 'dimension')]
    return sorted(outputs, key=lambda item: item[0].get('display_order') if item[0].get('display_order') is not None else float('inf'))


def display_value(column, cell):
    value = cell['value'].get('value')
    if value is None:
        return 'unknown'
    if column.get('format') == 'percent':
        return f'{Decimal(str(value)) * 100:,.2f}%'
    if column.get('format') == 'ratio':
        return f'{Decimal(str(value)):,.2f} : 1'
    return (column['currency'] + ' ' if column.get('currency') else '') + str(value)


class Planner:
    """Interpretation is injected; the coordinator never chooses a provider or warehouse backend."""

    def __init__(self, vocabulary, warehouses: WarehouseSet, hidden, citations=None, runtimes=None, readable_units=None, on_plan=None):
        self.vocabulary, self.warehouses, self.hidden = vocabulary, warehouses, hidden
        self.citations = citations or {}
        self.runtimes = runtimes
        self.readable_units = readable_units
        self.on_plan = on_plan

    def safe(self, value, key=None):
        if isinstance(value, str):
            if key in ('kind', 'schema', 'layout', 'mode', 'op', 'state', 'direction', 'nulls', 'end',
                       'id', 'dialect', 'location', 'certified_in', 'queried_at', 'sql', 'reference_sql'):
                return value
            return self.hidden.withheld(value)
        if isinstance(value, dict):
            return {k: self.safe(v, k) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.safe(v, key) for v in value]
        return value

    def answer(self, question, proposal, turn='turn', history=(), grounding=False, fallback=None, release_periods=None):
        graph_read('plan_start', None)
        checked = self.prepare(question, proposal, turn, history, grounding, rules_floor=fallback is not None)
        refusal = None
        if not isinstance(checked, CheckedPlan) and fallback is not None:
            refusal = checked
            try:
                checked = self.prepare(question, fallback, turn, history)
                if isinstance(checked, CheckedPlan) and release_periods is not None:
                    with graph_recording(None):
                        self.check_releases(checked, release_periods)
                if not isinstance(checked, CheckedPlan):
                    checked = refusal
            except (ValueError, KeyError):
                checked = refusal
            refusal = {**refusal, 'fallback': 'rules' if isinstance(checked, CheckedPlan) else None}
            graph_read('plan_refusal', (refusal, 'model', 'plan', refusal['fallback']))
        result = (self.execute_checked(question, checked, turn, release_periods) if isinstance(checked, CheckedPlan) else checked)
        if result['kind'] != 'result' and (refusal is None or refusal['fallback'] == 'rules'):
            check = result.get('phase') or result.get('guard', {}).get('failed_at') or 'plan'
            graph_read('plan_refusal', (result, 'rules' if refusal else 'query', check, None))
        return {**result, **({'model_refusal': refusal} if refusal is not None else {})}

    def check_releases(self, checked, release_periods):
        """A rules floor must be released before it can replace a zero-query model refusal."""
        compiled = compile_plan(checked)
        if compiled.release is None:
            return
        statement = compiled.release
        from kge.semantics.compile import Builder
        answer = compiled.answer if compiled.answer is not None else Builder(checked, None).build()[0]
        measures = tuple(self.vocabulary.contexts[m.context.id].measure for m in checked.plan.measures)
        selected = self.warehouses.select_runtime(
            [{'unit': s.units[0] if s.units else '', 'sql': s.sql, 'allow': s.allow} for s in (statement, answer)],
            runtimes=self.runtimes, readable_units=self.readable_units, measures=measures)
        if not selected.get('ok'):
            raise ValueError('No permitted runtime can answer the rules plan.')
        resolve_releases(compiled, release_periods(compiled.needs, selected['runtime_id']))

    def prepare(self, question, proposal, turn='turn', history=(), grounding=False, *, rules_floor=False):
        if decision := check_request(self.vocabulary, question, turn):
            return self.decision(decision)
        if problem := validate(history, self.vocabulary, question, turn):
            return self.decision(problem)
        vocabulary = clarified_vocabulary(history, self.vocabulary, question)
        reply = proposal(question, vocabulary) if callable(proposal) else proposal
        if reply.kind != 'propose':
            candidates = tuple(n.id for n in reply.candidates) if reply.kind == 'clarify' else ()
            if any(c not in self.vocabulary.entries for c in candidates):
                return self.decision(Decision('abstain', ('A proposed alternative is unavailable.',)))
            if reply.kind == 'clarify':
                pieces = ledger(question, turn, vocabulary)
                spans = reply.spans or tuple(p.span for p in pieces if set(p.candidates) & set(candidates))
                matching = [p for p in pieces if any(s.turn == p.span.turn and s.start <= p.span.start and p.span.end <= s.end for s in spans)]
                def grounded(span):
                    inside = [p for p in matching if p.span.turn == span.turn and span.start <= p.span.start and p.span.end <= span.end]
                    return (bool(inside) and inside[0].span.start == span.start and inside[-1].span.end == span.end
                            and (len(inside) == 1 or all(p.kind == 'name' and all(
                                vocabulary.entries[i].kind in ('unit', 'entity', 'source_set') for i in p.candidates) for p in inside)))
                if (not candidates or not spans or not all(grounded(s) for s in spans)
                        or set(candidates) - {i for p in matching for i in p.candidates}):
                    return self.decision(Decision('abstain', ('The proposed clarification has no grounded alternatives.',)))
            return self.decision(Decision(reply.kind, tuple(reply.missing) if reply.kind == 'abstain' else (), candidates))
        if grounding:
            reply = reply.model_copy(update={'plan': ground_plan(reply.plan, question, turn, vocabulary, rules_floor=rules_floor)})
        inherited, removed = (), {}
        if reply.base_turn is not None:
            problem, inherited = replacement(reply, history, vocabulary, question, turn)
            if problem:
                return self.decision(problem)
            base = next((h for h in history if h.turn_id == reply.base_turn), None)
            if base is not None and base.plan is not None:
                removed = removed_nodes(reply.plan, base.plan)
        checked = check_plan(reply.plan, vocabulary, question, turn, inherited, removed)
        if not isinstance(checked, CheckedPlan):
            return self.decision(checked)
        return checked

    def execute_checked(self, question, checked, turn, release_periods=None):
        vocabulary = checked.vocabulary
        measures = tuple(
            self.vocabulary.contexts[m.context.id].measure
            for m in checked.plan.measures
            if m.context.id in self.vocabulary.contexts
        )
        compiled, executed = None, []
        try:
            compiled = compile_plan(checked)
            executed, months = [], []
            from kge.semantics.compile import Builder
            preflight_stmt = compiled.answer if compiled.answer is not None else Builder(checked, None).build()[0]
            stmt_dicts = [
                {'unit': s.units[0] if s.units else '', 'sql': s.sql, 'allow': s.allow}
                for s in ([compiled.release, preflight_stmt] if compiled.release else [preflight_stmt])
            ]
            sel = self.warehouses.select_runtime(
                stmt_dicts,
                runtimes=self.runtimes,
                readable_units=self.readable_units,
                measures=measures,
            )
            if not sel.get('ok'):
                return {
                    **self.decision(Decision('refuse', (sel.get('reason') or 'no runtime can answer',))),
                    'skipped_runtimes': self.safe(sel.get('skipped') or []),
                }
            chosen_rt = sel['runtime_id']
            announced = False
            preview = compiled.answer
            if self.on_plan is not None and compiled.release and release_periods is not None:
                try:
                    with graph_recording(None):
                        preview = compile_plan(checked, release_periods(compiled.needs, chosen_rt)).answer
                except ValueError:
                    preview = None

            def ready(guard, meta):
                nonlocal announced
                if self.on_plan is not None and not announced and preview is not None:
                    sql = self.warehouses.statement_sql(
                        {'unit': preview.units[0] if preview.units else '', 'sql': preview.sql, 'allow': preview.allow}, chosen_rt)
                    self.on_plan(checked.plan, sql, guard)
                    announced = True

            skipped_rts = sel.get('skipped') or []
            if compiled.release:
                guard, rows, rel_meta = execute_on_runtime(self.warehouses, compiled.release, chosen_rt, measures,
                                                          ready if self.on_plan is not None else None)
                if rows is None:
                    return self.guard_decision(guard)
                executed.append(rel_meta.get('sql') or compiled.release.sql)
                latest = resolve_releases(compiled, rows)
                months = sorted({p for need in compiled.needs for p in ([latest] if need.latest else release_months(need.window))})
                compiled = compile_plan(checked, rows)
                preview = compiled.answer
        except ValueError as exc:
            return {**self.decision(Decision('abstain', (str(exc),))), 'sql_run': executed,
                    'phase': 'release' if compiled and compiled.release else 'compile'}
        guard, rows, meta = execute_on_runtime(self.warehouses, compiled.answer, chosen_rt, measures,
                                               ready if self.on_plan is not None else None)
        if rows is None:
            return self.guard_decision(guard)
        result = labelled_result(result_of(compiled, rows, self.citations), checked.plan, vocabulary, question, turn)
        return self.safe({'kind': 'result', 'sql': meta['sql'], 'reference_sql': meta['reference_sql'],
                          'sql_run': [*executed, meta['sql']],
                          'months': [m.isoformat() for m in months], 'guard': guard, 'result': result.model_dump(mode='json', exclude_unset=True),
                          'plan': checked.plan.model_dump(mode='json', by_alias=True), 'turn_id': turn,
                          'binding': self.vocabulary.binding.model_dump(mode='json'),
                          'runtime': meta['runtime'], 'physical_tables': meta['physical_tables'],
                          'runtime_certified': meta['runtime_certified'], 'certified_in': meta['certified_in'],
                          'live_data': meta['live_data'], 'skipped_runtimes': skipped_rts,
                          **({'queried_at': meta['queried_at']} if meta.get('queried_at') else {})})

    def release_offer(self, question, plan, turn, release_periods):
        checked = check_plan(plan, self.vocabulary, question, turn)
        if not isinstance(checked, CheckedPlan):
            return ''
        try:
            compiled = compile_plan(checked)
            if compiled.release is None:
                return ''
            statement = compiled.release
            measures = tuple(self.vocabulary.contexts[m.context.id].measure for m in plan.measures)
            selected = self.warehouses.select_runtime(
                [{'unit': statement.units[0], 'sql': statement.sql, 'allow': statement.allow}],
                runtimes=self.runtimes, readable_units=self.readable_units, measures=measures)
            if not selected.get('ok'):
                return ''
            return self.safe(latest_released_offer(compiled.needs, release_periods(compiled.needs, selected['runtime_id'])))
        except ValueError:
            return ''

    def decision(self, decision):
        return self.safe({'kind': decision.kind, 'missing': list(decision.missing),
                          'choices': [{'id': i, 'label': self.vocabulary.entries[i].label} for i in decision.candidates]})

    def guard_decision(self, guard):
        return {**self.decision(Decision('refuse' if guard['failed_at'] in ('static', 'partition') else 'abstain', tuple(guard['problems']))),
                'guard': self.safe(guard)}
