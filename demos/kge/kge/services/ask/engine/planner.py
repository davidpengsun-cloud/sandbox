"""Numeric coordination shared by public Ask and its rules baseline."""
from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal, localcontext

from kge.adapters.deadline import RequestDeadlineExceeded, request_remaining
from kge.services.ask.engine import runtrace
from kge.pipeline.sqlguard import gate
from kge.pipeline import questionrun
from kge.pipeline.settings import ask_model_corrections
from kge.pipeline.trace import transient_failure, graph_read, graph_recording, warehouse_statement, QueryCancelled
from kge.ports import AUTHORED, WarehouseSet
from kge.semantics.compile import total_outputs, total_scopes, warehouse_totals, compile_plan, resolve_releases, latest_released_offer, comparison_aliases
from kge.semantics.planperiods import empty_to_date_reason, release_months
from kge.semantics.coverage import GRAIN_WORDS, RANK_VERBS, SINGULAR_RANK_WORDS, ledger, ground_plan, currency_candidates, period_candidates, grouping_grains, declared_whole
from kge.semantics.plan import Cell, Result, ResultRow
from kge.semantics.planhistory import validate, replacement, clarified_vocabulary, removed_nodes
from kge.semantics.plancheck import CheckedPlan, Decision, check_plan, check_request
from kge.semantics.planvocabulary import predicate_contexts
from kge.services.ask.engine import planmodel
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, dry_run_problems, forget_dry_run, query_fault, warehouse_guard
from kge.services.ask.engine.question import GRAIN_NAMES, period_words, plural_forms


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
    ranked = any(o.direction == 'both' or o.partition_by for o in compiled.checked.plan.order)
    outside = compiled.answer is not None and compiled.answer.outside_calendar
    if outside:
        rows = [tuple(row) for row in rows]
        flagged = any(bool(row[-1]) for row in rows)
        rows = [row[:-1] for row in rows]
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
        ranking = dict(end=row[offset], rank=int(row[offset + 1])) if ranked else {}
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
    if outside and flagged:
        return Result(columns=compiled.columns, rows=tuple(decoded), outside_calendar=True)
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
    for alias, original in comparison_aliases(compiled.checked.plan).items():
        if original in values:
            values.setdefault(alias, values[original])
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


DERIVED_NAMES = {'ratio': 'Ratio', 'difference': 'Difference', 'percent_change': '% change', 'share': 'Share',
                 'running_total': 'Running total', 'moving_average': 'Moving average'}


def plan_period_words(time, calendars):
    """A plan's single period in words, or None for any other time."""
    if time is None or time.kind != 'single':
        return None
    return period_words(time.period.kind, time.period.value, calendars.get(time.period.calendar.id))


def fixed_period_words(plan, calendars):
    """The one fixed period a plan reads, in words, for an answer's heading: None where the plan breaks down by a period
    (a time dimension gives each period its own row) or reads anything but a single period."""
    if any(d.kind == 'time' for d in plan.dimensions):
        return None
    return plan_period_words(plan.time, calendars)


def as_written(text):
    return text[:1].upper() + text[1:]


def in_sentence(label):
    """A label as it reads after a word: each capitalised word in lower case, an acronym (KPI) kept."""
    return ' '.join(w.lower() if w[:1].isupper() and w[1:].islower() else w for w in label.split(' '))


def measure_label(measure, plan, vocabulary, question, turn, as_asked=True):
    """A measure's name as asked: a declared modifier's label, the time mode the question puts before the measure's own
    name (current KPI), else its context's label; with as_asked false, never the time mode."""
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
    if not as_asked:
        return vocabulary.contexts[measure.context.id].label
    evidence = set(getattr(measure.context, 'evidence', ()))
    modes = [p for p in pieces if getattr(measure, 'mode', None) and p.operation == measure.mode and not p.candidates]
    for name in (p for p in names if not evidence or p.span in evidence):
        said = [m for m in modes if m.span.turn == name.span.turn and m.span.end <= name.span.start
                and not question[m.span.end:name.span.start].strip()]
        if said:
            return as_written(question[said[-1].span.start:name.span.end])
    own = {s for o in getattr(plan, 'obligations', ()) if measure.id in o.targets for s in o.spans}
    if not names and any(m.span in own for m in modes):
        mode = next(m for m in modes if m.span in own)
        said = sorted((phrase for phrase, ids in vocabulary.phrases.items() if measure.context.id in ids
                       and re.search(r'(?<![\w-])' + re.escape(phrase) + r'(?![\w-])', question, re.I)), key=len)
        if said:
            found = re.search(r'(?<![\w-])' + re.escape(said[-1]) + r'(?![\w-])', question, re.I)
            return as_written(mode.text + ' ' + found.group(0))
    return vocabulary.contexts[measure.context.id].label


def counted(measure, vocabulary, question, spans=()):
    """What a count counts, as the question says it (Accounts), else its declared entity in the plural."""
    named = [question[s.start:s.end] for s in spans if any(e.kind == 'entity' and e.id == measure.entity.id
                                                           for e in vocabulary.candidates(question[s.start:s.end]))]
    return as_written(named[0] if named else plural_forms(vocabulary.entities[measure.entity.id]['label'].replace('_', ' '))[1])


def count_label(measure, vocabulary, question, turn, spans=()):
    """A count named for what it counts: the entity as asked, with the measure it is counted by (Accounts with net new
    assets); an entity count that has no measure of its own, or whose measure is the entity's own key, is the entity alone."""
    entity = counted(measure, vocabulary, question, spans)
    ctx = vocabulary.contexts[measure.context.id]
    declared = vocabulary.entities[measure.entity.id]
    keyed = set(getattr(ctx, 'values', ())) == {f"{declared.get('table')}.{key}" for key in declared.get('key', ())}
    return entity if entity_only(ctx, vocabulary) or keyed else entity + ' with ' + in_sentence(ctx.label)


def entity_only(ctx, vocabulary):
    """Whether a count's context counts its entity alone, with no measure of its own to count it by."""
    return ctx.id.startswith('context:count:') or ctx.measure not in vocabulary.entries


def unresolved_phrases(question, plan, turn, readings=()):
    """The phrases of this turn the model's own reply leaves unresolved, as written: an obligation it marks unresolved
    whose phrase none of its readings reads as a vocabulary id or as filler. The model decides what a phrase means: one
    it reads as filler is filler. A filler reading covers the phrase it names, never a longer one around it. The
    model's self-report, never a check of the question's words."""
    read = [r['phrase'].casefold() for r in readings if r.get('id') and r['id'] != 'filler' and r['phrase'].strip()]
    filler = [r['phrase'].casefold().strip() for r in readings if r.get('id') == 'filler' and r['phrase'].strip()]
    def mapped(phrase):
        return any(p in phrase or phrase in p for p in read) or any(phrase.strip() in p for p in filler)
    phrases = (question[s.start:s.end] for o in plan.obligations if o.state == 'unresolved' for s in o.spans if s.turn == turn)
    return list(dict.fromkeys(p for p in phrases if p.strip() and not mapped(p.casefold())))


RANKED = r'(?:' + '|'.join(sorted({*SINGULAR_RANK_WORDS, 'top', 'bottom', 'first', 'last'})) + r')'
ORDERING_PHRASE = re.compile(r'\b(?:' + '|'.join(sorted(SINGULAR_RANK_WORDS)) + r')\s+(?:to\s+(?:the\s+)?(?:'
                             + '|'.join(sorted(SINGULAR_RANK_WORDS)) + r')|first|last)\b', re.I)
"""Words that order every group rather than pick one: "highest to lowest", "largest first"."""
SINGULAR_ASK = re.compile(r'\b(?:ranks?|ranked|ranking)\s+(?:as\s+(?:the\s+)?)?' + RANKED + r'\b', re.I)
"""A ranking verb that asks for the one group: "which ... ranked highest", "ranks first"."""
PERIOD_OPERATIONS = frozenset((*GRAIN_WORDS.values(), 'latest_released', 'previous_period', 'previous_year'))
"""A number beside these counts periods ("the last 3 months"), never ranked groups."""


def unasked_limit(question, reply, turn, vocabulary, history=()):
    """Why the model's plan may not keep its limit, or None (#593), on the model route, where the plan is otherwise
    checked without the question's words. Only a ranking asked in the plural is read: a ranking verb ("rank the days",
    "categories ranked by ...") or an ordering phrase ("highest to lowest", "largest first") asks for every group in
    rank order, so it owns no limit unless the question states a count of groups (not of periods, "the last 3 months"),
    or, for a limit of 1, its ranking verb asks for the one group ("ranked highest", "ranks first") without an ordering
    phrase. Any other question keeps the model's
    limit as before, and a follow-up keeps the limit of the turn it continues. The rules route is not read here: its
    plans pass the word check itself."""
    plan = reply.plan
    if plan.limit is None:
        return None
    base = next((h for h in history if h.turn_id == reply.base_turn), None)
    if base is not None and base.plan is not None and base.plan.limit == plan.limit:
        return None
    pieces = sorted((p for p in ledger(question, turn, vocabulary) if p.span.turn == turn), key=lambda p: p.span.start)

    def count(index, piece):
        value = piece.value if piece.value is not None else piece.text
        if piece.kind != 'literal' or not str(value).isdecimal() or piece.text.casefold() in ('first', 'last'):
            return None
        periods = any(near.kind == 'time' or near.operation in PERIOD_OPERATIONS
                      for near in pieces[max(0, index - 1):index] + pieces[index + 1:index + 2])
        return None if periods else int(value)

    if plan.limit in {count(i, piece) for i, piece in enumerate(pieces)}:
        return None
    ordering = ORDERING_PHRASE.search(question) is not None
    if not ordering and not any(p.kind == 'operator' and p.text.casefold() in RANK_VERBS for p in pieces):
        return None
    if plan.limit == 1 and not ordering and SINGULAR_ASK.search(question):
        return None
    return (f'unrequested limit: no word of the question asks for {plan.limit} ranked result'
            f"{'' if plan.limit == 1 else 's'}. A ranking asked in the plural returns every group in rank order: give "
            'no limit unless the question states a count, or asks for the single highest or lowest.')


def labelled_result(result, plan, vocabulary, question, turn):
    """Attach display names, periods in words and question order without changing output ids or cell alignment; a
    column that holds the same figure as another (a comparison's side and the measure it compares) names that column in
    repeats, so a reader shows each figure once."""
    def mentions(ids):
        return sorted((s.start, question[s.start:s.end]) for o in plan.obligations
                      if set(o.targets) & set(ids) for s in o.spans if s.turn == turn)

    measures = {m.id: m for m in plan.measures}
    derived = {d.id: d for d in plan.derived}
    pieces = ledger(question, turn, vocabulary) if plan.dimensions else ()
    labels, positions, basis_free = {}, {}, {}
    filtered = {}
    member_columns = {row['params'][key] for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                      if row['kind'] == 'code_name_pair' for key in ('code', 'name')}
    for m in plan.measures:
        modifiers = {phrase.casefold() for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                     if row['kind'] == 'predicate' and row['params'].get('modifier') for phrase in row['params']['phrases']}
        local = [p for p in m.filters if not (
            p.kind == 'compare' and p.left.kind == 'column' and p.left.name.id in member_columns)]
        named = [p.id for p in local if (p.kind == 'in' and all(v.kind == 'text' for v in p.values))
                 or (p.kind == 'compare' and p.op == 'eq' and p.value.kind == 'text')]
        parts = [p for p in mentions(named) if p[1].casefold() not in modifiers]
        qualifiers = [p for p in mentions([p.id for p in local if p.id not in named]) if p[1].casefold() not in modifiers]
        own = mentions([m.id])
        if m.kind == 'distinct_count':
            names = [p for p in own if any(e.kind == 'entity' and e.id == m.entity.id
                                          for e in vocabulary.candidates(p[1]))]
            label = count_label(m, vocabulary, question, turn, [s for o in plan.obligations if m.id in o.targets
                                                                for s in o.spans if s.turn == turn])
            own = names or own
        else:
            def composed(qualified):
                text = parts[0][1].capitalize() if parts else qualified
                if parts and qualified != vocabulary.contexts[m.context.id].label:
                    text = qualified + ' · ' + text
                if m.aggregation is not None and m.aggregation != vocabulary.contexts[m.context.id].aggregation:
                    text = {'sum': 'Sum', 'count': 'Count', 'avg': 'Average', 'min': 'Minimum', 'max': 'Maximum',
                            'median': 'Median'}[m.aggregation] + ' · ' + text
                if qualifiers:
                    text = text + ' · ' + question[qualifiers[0][0]:max(start + len(said) for start, said in qualifiers)]
                return text
            label = composed(measure_label(m, plan, vocabulary, question, turn))
            basis_free[m.id] = composed(measure_label(m, plan, vocabulary, question, turn, as_asked=False))
        labels[m.id] = label
        positions[m.id] = (sorted(parts + qualifiers) or own or [(len(question), '')])[0][0]
        if parts or qualifiers:
            filtered.setdefault(m.context.id, []).append(m.id)
    def split_columns(m):
        return {p.left.name.id for p in m.filters if p.kind == 'compare' and p.left.kind == 'column'}
    for m in plan.measures:
        siblings = [s for s in filtered.get(m.context.id, []) if s != m.id and measures[s].kind == 'measure']
        split = [split_columns(measures[s]) - split_columns(m) for s in siblings]
        wholes = {declared_whole(c, vocabulary) for columns in split for c in columns}
        if m.kind == 'measure' and len(siblings) > 1 and all(len(c) == 1 for c in split) and len(wholes) == 1 and None not in wholes:
            labels[m.id] = basis_free[m.id] = whole = wholes.pop()
            net = [p for p in mentions([m.id]) if p[1].casefold() == whole.casefold()]
            positions[m.id] = net[0][0] if net else max(positions[i] for i in siblings) + .5
    repeats, shown = {}, {}
    for c in plan.comparisons:
        if c.kind == 'contexts':
            for side, source in (('base', c.left), ('other', c.right)):
                repeats[f'{c.id}/{side}'] = source
                labels[f'{c.id}/{side}'] = labels[source]
            continue
        for i in c.inputs:
            own_mode = getattr(measures.get(i), 'mode', None)
            for side in ('base', 'other'):
                figure = ((i, c.base if side == 'base' else c.other) if c.kind == 'time_mode'
                          else (i, own_mode) if side == 'base' else (c.id, i))
                column_id = f'{c.id}/{i}/{side}'
                if figure in shown:
                    repeats[column_id] = shown[figure]
                else:
                    shown[figure] = column_id
            if (i, own_mode) in shown:
                repeats[i] = shown[(i, own_mode)]
            named = labels.get(i) or (DERIVED_NAMES[derived[i].kind] if i in derived else i)
            if c.kind == 'time_mode' and i in basis_free:
                named = basis_free[i]
            for side in ('base', 'other'):
                if c.kind == 'time_mode':
                    suffix = (c.base if side == 'base' else c.other).replace('_', ' ')
                elif side == 'base':
                    suffix = plan_period_words(plan.time, vocabulary.calendars) or 'this period'
                elif c.kind == 'explicit_period':
                    suffix = plan_period_words(c.other, vocabulary.calendars) or 'the other period'
                else:
                    suffix = c.kind.replace('_', ' ')
                labels[f'{c.id}/{i}/{side}'] = named + ' · ' + suffix
    aliases = comparison_aliases(plan)
    for alias, original in aliases.items():
        if alias in labels:
            labels[original] = labels[alias]
    repeats = {column_id: aliases.get(source, source) for column_id, source in repeats.items()}
    repeats = {column_id: source for column_id, source in repeats.items() if source != column_id}
    columns = []
    for column in result.columns:
        d = derived.get(column.id)
        label, fmt = labels.get(column.id), None
        position = positions.get(column.id)
        if d:
            label = DERIVED_NAMES[d.kind]
            if d.kind == 'moving_average':
                label = str(d.length) + '-period ' + label.lower()
            if getattr(d, 'declaration', None) is not None:
                label = vocabulary.entries[d.declaration.id].label
            fmt = 'percent' if d.kind in ('percent_change', 'share') else 'ratio' if d.kind == 'ratio' and column.currency is None else None
            own = mentions([d.id])
            position = own[0][0] if own else None
        if column.kind == 'dimension':
            dim = next(d for d in plan.dimensions if d.id == column.id)
            said = [] if dim.kind == 'time' else [re.sub(r'(?i)^by\s+', '', p.text) for p in pieces
                                                   if set(p.candidates) & {b.column.id for b in dim.bindings}]
            label = (GRAIN_NAMES[dim.grain] if dim.kind == 'time' else as_written(said[0]) if said
                     else vocabulary.entries[dim.bindings[0].column.id].label)
        metadata = {'label': label or column.id}
        if fmt:
            metadata['format'] = fmt
        if position is not None and not plan.comparisons:
            metadata['display_order'] = float(position)
        if column.id in repeats:
            metadata['repeats'] = repeats[column.id]
        columns.append(column.model_copy(update=metadata))
    times = [next((d for d in plan.dimensions if d.id == c.id and d.kind == 'time'), None)
             for c in result.columns if c.kind == 'dimension']

    def words(row):
        text = tuple(period_words(d.grain, v.value, vocabulary.calendars.get(d.calendar.id))
                     if d is not None and v.kind != 'null' else None for d, v in zip(times, row.dimensions))
        return {'dimension_text': text} if any(t is not None for t in text) else {}
    rows = tuple(r.model_copy(update=words(r)) for r in result.rows)
    return result.model_copy(update={'columns': tuple(columns), 'rows': rows})


def display_outputs(result):
    """The figures a reader is shown, each once, in display order, with each one's index among the result's cells."""
    outputs = [(c, i) for i, c in enumerate(c for c in result['columns'] if c['kind'] != 'dimension') if not c.get('repeats')]
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


MIN_CORRECTION_SECONDS = 30
"""The request time a correction needs left before it is sent; with less, the rules' floor answers instead."""
RETRIED_PHASES = ('compile', 'release')


def retryable(result):
    """Whether a refusal of a checked model plan goes back to the model as a correction: a static SQL-gate failure that
    is not restricted personal data, a dry-run failure whose warehouse words name a fault in the query itself
    (errors.query_fault), or a compiler or release error. Never a refusal of access, no data for the period, no
    permitted runtime, the cost gate, or a dry run the warehouse failed for its own reasons (no access, an outage). A
    partition, runtime, implements, cost-gate or execute failure never goes back."""
    guard = result.get('guard')
    if guard is not None:
        if guard.get('failed_at') == 'static':
            return not any('restricted personal data' in str(p) for p in guard.get('problems') or ())
        return guard.get('failed_at') == 'dry run' and query_fault(dry_run_problems())
    return result.get('phase') in RETRIED_PHASES


def told_model(refusal):
    """The problems a correction tells the model: a dry run's in the warehouse's own words, which the reply never
    carries; any other refusal's as the reply states them."""
    guard = refusal.get('guard')
    if guard is not None and guard.get('failed_at') == 'dry run' and (words := dry_run_problems()):
        return words
    return tuple(refusal.get('missing') or ())


def unread_reply(reading):
    """Why a model reply could not be read: for one cut off at its token budget, that it was cut off, with the budget,
    the thinking it took and the setting that raises it; otherwise the problems found in it."""
    if reading.truncated:
        return tuple(f"The model's reply was cut off: {p}." for p in reading.problems) or (
            "The model's reply was cut off at its token budget.",)
    return reading.problems


class Planner:
    """Interpretation is injected; the coordinator never chooses a provider, a warehouse backend or a rules adapter. A
    rules reply, whichever adapter of the rules-planner port (rulesport) gave it, is a proposal or a floor like any
    other: check_plan, the compiler and the SQL gate here take it as they take a model's."""

    def __init__(self, vocabulary, warehouses: WarehouseSet, hidden, citations=None, runtimes=None, readable_units=None, on_plan=None):
        self.vocabulary, self.warehouses, self.hidden = vocabulary, warehouses, hidden
        self.citations = citations or {}
        self.runtimes = runtimes
        self.readable_units = readable_units
        self.on_plan = on_plan

    def safe(self, value, key=None):
        if isinstance(value, str):
            if key in ('kind', 'schema', 'layout', 'mode', 'op', 'state', 'direction', 'nulls', 'end', 'grain',
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

    def answer_model(self, question, read, floor, turn='turn', history=(), release_periods=None, corrections=None):
        """AI-Generated mode: the model's plan, checked as a plan alone (its names, rights, check_plan without the
        question's words, the compiler and the SQL gate). A failed attempt goes back to the model as a correction that
        continues the conversation, up to corrections times (ASK_MODEL_CORRECTIONS when None, so at most three calls):
        a reply that fails its contract; one cut off at its budget, only while thinking less leaves the contract more
        room; a check_plan problem that is not a refusal; a compiler or release error; and a static SQL-gate failure
        other than restricted personal data, or a dry-run failure the query itself caused, told in the warehouse's own
        words (retryable, told_model). An access, restricted or no-join
        refusal, no data for the period, the cost gate, an outage and the model's own abstain or clarify are never sent
        back, and no correction starts with less than MIN_CORRECTION_SECONDS of the request left. A limit no word of the
        question asks for (unasked_limit) is a check_plan problem like any other. Then the rules' best reading (floor);
        where that gives no figure and the model saw two declared meanings, the model's clarify with its choices, each a
        declared id and its label, for the person to pick through the follow-up path (#595); else the floor's decline,
        its own clarify included. A model that declines to
        plan at all is a decline, as on the model's own path. read(corrections) gives one planmodel.Reading, for the
        tuple of corrections sent so far."""
        graph_read('plan_start', None)
        limit = ask_model_corrections() if corrections is None else corrections
        done, reading, refusal = (), None, None
        for attempt in range(limit + 1):
            reading = read(done)
            if reading.reply is None:
                refusal = {**self.decision(Decision('abstain', unread_reply(reading))),
                           'phase': 'truncated' if reading.truncated else 'contract'}
                retry = not reading.truncated or reading.more_room
            elif reading.unavailable:
                refusal = self.prepare(question, reading.reply, turn, history)
                graph_read('plan_refusal', ({**refusal, 'fallback': None}, 'model', 'plan', None))
                return {**refusal, 'model_refusal': {**refusal, 'fallback': None}, 'schema': reading.schema}
            elif reading.reply.kind != 'propose':
                refusal = self.prepare(question, reading.reply, turn, history)
                break
            elif unread := unresolved_phrases(question, reading.reply.plan, turn, reading.readings):
                refusal = {**self.decision(Decision('abstain', (
                    f"The model could not read {' or '.join(repr(p) for p in unread)} as a declared meaning. "
                    'Ask again in the declared terms.',))), 'phase': 'readings'}
                graph_read('plan_refusal', ({**refusal, 'fallback': None}, 'model', 'plan', None))
                return {**refusal, 'model_refusal': {**refusal, 'fallback': None}, 'schema': reading.schema}
            else:
                forget_dry_run()
                checked = self.prepare(question, reading.reply, turn, history, words=False)
                if isinstance(checked, CheckedPlan) and (asked := unasked_limit(question, reading.reply, turn, self.vocabulary, history)):
                    checked = self.decision(Decision('abstain', (asked,)))
                if isinstance(checked, CheckedPlan):
                    result = self.execute_checked(question, checked, turn, release_periods)
                    if result['kind'] == 'result':
                        return {**result, 'readings': list(reading.readings), 'schema': reading.schema}
                    refusal, retry = result, retryable(result)
                else:
                    refusal, retry = checked, checked['kind'] != 'refuse'
            missing = tuple(refusal.get('missing') or ())
            runtrace.model_attempt('plan', attempt, refusal.get('phase') or refusal['kind'], missing)
            if not retry or attempt == limit:
                break
            remaining = request_remaining()
            if remaining is not None and remaining < MIN_CORRECTION_SECONDS:
                refusal = {**refusal, 'phase': 'deadline'}
                break
            done = (*done, planmodel.correction(reading, told_model(refusal), self.vocabulary))
        result = self.answer(question, floor, turn, history, release_periods=release_periods)
        refusal = {**refusal, 'fallback': 'rules' if result['kind'] == 'result' else None}
        graph_read('plan_refusal', (refusal, 'model', 'plan', refusal['fallback']))
        if result['kind'] != 'result' and refusal['kind'] == 'clarify' and refusal.get('choices'):
            result = {key: value for key, value in refusal.items() if key != 'fallback'}
        return {**result, 'model_refusal': refusal, 'schema': reading.schema if reading is not None else ''}

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
        compile_plan(checked, release_periods(compiled.needs, selected['runtime_id']))

    def prepare(self, question, proposal, turn='turn', history=(), grounding=False, *, rules_floor=False, words=True):
        if decision := check_request(self.vocabulary, question, turn):
            return self.decision(decision)
        if problem := validate(history, self.vocabulary, question, turn, words):
            return self.decision(problem)
        vocabulary = clarified_vocabulary(history, self.vocabulary, question)
        reply = proposal(question, vocabulary) if callable(proposal) else proposal
        if reply.kind != 'propose':
            candidates = tuple(n.id for n in reply.candidates) if reply.kind == 'clarify' else ()
            if any(c not in vocabulary.entries for c in candidates):
                return self.decision(Decision('abstain', ('A proposed alternative is unavailable.',)))
            if reply.kind == 'clarify':
                from kge.services.ask.engine.question import requested_currencies, read_period
                pieces = ledger(question, turn, vocabulary)
                active = period_candidates(candidates, vocabulary, False)
                if candidates and not active:
                    return self.decision(Decision('abstain', ('A proposed alternative is unavailable.',)))
                candidates = active
                candidates = currency_candidates(candidates, vocabulary, set(requested_currencies(question)))
                spans = reply.spans or tuple(p.span for p in pieces if set(p.candidates) & set(candidates))
                matching = [p for p in pieces if any(s.turn == p.span.turn and s.start <= p.span.start and p.span.end <= s.end for s in spans)]
                def grounded(span):
                    inside = [p for p in matching if p.span.turn == span.turn and span.start <= p.span.start and p.span.end <= span.end]
                    return (bool(inside) and inside[0].span.start == span.start and inside[-1].span.end == span.end
                            and (len(inside) == 1 or all(p.kind == 'name' and all(
                                vocabulary.entries[i].kind in ('unit', 'entity', 'source_set') for i in p.candidates) for p in inside)))
                predicate_spans = {piece.span for piece in pieces if piece.kind == 'predicate'}
                contextual = ({context.id for context in predicate_contexts(vocabulary, pieces)}
                              if predicate_spans <= {piece.span for piece in matching} else set())
                if (not candidates or not spans or not all(grounded(s) for s in spans)
                        or set(candidates) - ({i for p in matching for i in p.candidates} | contextual)):
                    return self.decision(Decision('abstain', ('The proposed clarification has no grounded alternatives.',)))
                available = period_candidates(candidates, vocabulary,
                    read_period(question)[0] is not None or bool(grouping_grains(pieces, question))
                    or any(piece.kind == 'time' or piece.operation in ('latest_released', 'ytd', 'mtd', 'qtd', 'previous_period')
                           for piece in pieces))
                if candidates and not available:
                    return self.decision(Decision('abstain', ('missing compatible period binding',)))
                candidates = available
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
        checked = check_plan(reply.plan, vocabulary, question, turn, inherited, removed, words=words)
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
            preview, previewed = compiled.answer, checked.plan
            if self.on_plan is not None and compiled.release and release_periods is not None:
                try:
                    with graph_recording(None):
                        previewing = compile_plan(checked, release_periods(compiled.needs, chosen_rt))
                    preview, previewed = previewing.answer, previewing.checked.plan
                except ValueError:
                    preview = None

            def ready(guard, meta):
                nonlocal announced
                if self.on_plan is not None and not announced and preview is not None:
                    sql = self.warehouses.statement_sql(
                        {'unit': preview.units[0] if preview.units else '', 'sql': preview.sql, 'allow': preview.allow}, chosen_rt)
                    self.on_plan(previewed, sql, guard)
                    announced = True

            skipped_rts = sel.get('skipped') or []
            if compiled.release:
                guard, rows, rel_meta = execute_on_runtime(self.warehouses, compiled.release, chosen_rt, measures,
                                                          ready if self.on_plan is not None else None)
                if rows is None:
                    return self.guard_decision(guard)
                executed.append(rel_meta.get('sql') or compiled.release.sql)
                compiled = compile_plan(checked, rows)
                latest = resolve_releases(compiled, rows)
                months = sorted({p for need in compiled.needs for p in ([latest] if need.latest else release_months(need.window))})
                preview = compiled.answer
        except ValueError as exc:
            return {**self.decision(Decision('abstain', (str(exc),))), 'sql_run': executed,
                    'phase': 'release' if compiled and compiled.release else 'compile'}
        guard, rows, meta = execute_on_runtime(self.warehouses, compiled.answer, chosen_rt, measures,
                                               ready if self.on_plan is not None else None)
        if rows is None:
            return self.guard_decision(guard)
        answered = compiled.checked.plan
        result = labelled_result(result_of(compiled, rows, self.citations), answered, vocabulary, question, turn)
        if empty := empty_to_date_reason(answered.time, vocabulary.calendars, result.rows):
            return {**self.decision(Decision('abstain', (empty,))), 'sql_run': [*executed, meta['sql']], 'phase': 'observations'}
        return self.safe({'kind': 'result', 'sql': meta['sql'], 'reference_sql': meta['reference_sql'],
                          'sql_run': [*executed, meta['sql']],
                          'months': [m.isoformat() for m in months], 'guard': guard, 'result': result.model_dump(mode='json', exclude_unset=True),
                          'plan': checked.plan.model_dump(mode='json', by_alias=True), 'turn_id': turn,
                          **({'answered_time': answered.time.model_dump(mode='json')} if answered.time != checked.plan.time else {}),
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
