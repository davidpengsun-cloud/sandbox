"""Compatibility presentation of checked numeric results for Ask and its baseline."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from kge.pipeline.trace import stream_event, streaming
from kge.pipeline.questionrun import active, memo
from kge.semantics.plan import reply_adapter
from kge.semantics.plan import Clarification, Choice, HistoryEntry, Span, QueryPlan
from kge.semantics.planhistory import clarified_vocabulary
from kge.semantics.query import Asked
from kge.services.ask.engine import planmodel, planrules, runtrace
from kge.services.ask.engine.planner import Planner, display_outputs, display_value, measure_label, dimension_value
from kge.services.ask.engine.question import not_joinable_named, pair
from kge.services.ask.engine.wording import Hidden, exact


VERDICTS = {None: {"behaviour": "sum_without_verdict"}, True: {"behaviour": "sum_with_verdict"},
            False: {"behaviour": "parts_with_reason"}}
DECISIONS = {'clarify': {"behaviour": "clarify"}, 'abstain': {"behaviour": "abstain"}, 'refuse': {"behaviour": "refuse"}}


def measure_question(question):
    return bool(re.search(r'\b(?:plan|budget|target|total|how much)\b', question, re.I))


def declared_measure_labels(req, vocab, default_units=()):
    pieces = planrules.ledger(req.question, 'turn', vocab)
    nearby = {vocab.entries[i].unit for p in pieces for i in p.candidates
              if vocab.entries[i].kind in ('context', 'measure', 'column', 'value', 'entity') and vocab.entries[i].unit}
    units = set(req.units or default_units) or nearby
    units = units or {c.unit for c in vocab.contexts.values() if c.term in req.terms_found}
    measures = {c.measure for c in vocab.contexts.values()
                if (not units or c.unit in units) and not c.id.startswith('context:count:')}
    return sorted({vocab.entries[m].label for m in measures if m in vocab.entries})


def release_decline(reason, tables=()):
    reason = re.sub(r'([^;]+?) (?:is|are) not booked in the release calendar', r'\1 is not released yet.', reason)
    reason = re.sub(r'([^;]+?) is not yet released(?::[^;]+)?', r'\1 is not released yet.', reason)
    reason = reason.replace('; The latest released month', ' The latest released month')
    return reason


def value(cell):
    scalar = cell['value']
    v = scalar.get('value')
    return round(float(v), 2) if scalar['kind'] == 'decimal' else v


def scope_requests(engine, req, principal, cfg, scope):
    """Default page units first, then other readable declarations; explicit sources stay fixed."""
    if scope.products is None or req.units or req.all_units or not req.terms_found:
        return [req]
    runtime = planrules.snapshot(engine, cfg.graph)
    readable = engine.g.readable_units(principal)
    sources = [{c.unit for c in runtime.contexts.values() if c.term == term}
               | {m['unit'] for m in runtime.mapping_declarations if m['term'] == term}
               for term in req.terms_found]
    sources = [units for units in sources if units]
    if not sources:
        return [req]
    cohort = cfg.graph and bool(planrules.cohort_terms(engine, req))
    candidates = set.intersection(*sources) if cohort else set.union(*sources)
    units = [u for u in engine.UNIT_ORDER if u in candidates & readable]
    preferred = [u for u in units if u in scope.units]
    if cohort:
        if not preferred and len(units) != 1:
            return [req]
        return [replace(req, units=[u], counted=planrules.population(engine, runtime, u) if req.counted else None)
                for u in [*preferred, *(u for u in units if u not in preferred)]]
    if not preferred:
        return [replace(req, units=units)] if units else [req]
    remaining = [u for u in units if u not in preferred]
    return [replace(req, units=preferred), *([replace(req, units=remaining)] if remaining else [])]


def answer(engine, req, principal, purpose, cfg, scope, out, history=(), combined=None, runtimes=None):
    requests = scope_requests(engine, req, principal, cfg, scope)
    for index, selected in enumerate(requests):
        if index or (scope.products and not req.units and not req.all_units and selected.units
                     and not set(selected.units) & set(scope.units)):
            out['trace'].append({'step': 'scope_fallback', 'reason': 'The preferred page unit cannot answer.',
                                 'units': selected.units})
        result = selected_answer(engine, selected, principal, purpose, cfg, scope, dict(out), history, combined,
                                 default_units=selected.units if selected is not req else (), runtimes=runtimes)
        if not engine.needs_scope_fallback(result):
            return result
    return result


def public_context(engine, req, cfg, history, plan, vocab, describe, turn):
    """The existing answer context, resolved before execution and reused after it."""
    if len(plan.measures) != 1 or plan.comparisons or plan.derived:
        return None
    measure = plan.measures[0]
    ctx = vocab.contexts[measure.context.id]
    authored = engine.semantics.contexts if cfg.graph else {}
    context = None
    if history and history[-1].clarification is not None and ctx.id in authored:
        context = {'context': ctx.id, 'id': ctx.id, 'label': ctx.label, 'term': ctx.term,
                   'fact': ctx.fact, 'measure': ctx.measure, 'time_mode': measure.mode}
    if ctx.id in authored and any(j.required for j in ctx.joins):
        context = {**describe(ctx, req).about(), 'id': ctx.id, 'label': ctx.label,
                   'count': measure.entity.id if measure.kind == 'distinct_count' else None, 'time_mode': measure.mode}
    label = measure_label(measure, plan, vocab, req.question, turn) if measure.kind != 'distinct_count' else ctx.label
    if label != ctx.label:
        context = {**(context or {}), 'term': label, 'label': label}
    return context


def selected_answer(engine, req, principal, purpose, cfg, scope, out, history=(), combined=None, default_units=(), runtimes=None):
    descriptions = {}
    def describe(context, request):
        asked = Asked.of(request)
        key = (context.id, repr(asked))
        if key not in descriptions:
            descriptions[key] = engine.semantics.plan(context, asked)
        return descriptions[key]
    contract = getattr(combined, 'plan_vocabulary', None)
    if contract is not None and not cfg.graph:
        vocab, runtime = planrules.control_vocabulary(engine, principal, purpose, scope, req)
    else:
        vocab = planrules.vocabulary(engine, principal, purpose, scope, cfg, req, history)
        runtime = planrules.snapshot(engine, cfg.graph)
    if contract is not None:
        allowed = {entry['id'] for entry in contract['names']}
        vocab = replace(vocab,
            entries={k: v for k, v in vocab.entries.items() if k in allowed},
            phrases={p: tuple(i for i in ids if i in allowed) for p, ids in vocab.phrases.items() if any(i in allowed for i in ids)},
            contexts={k: v for k, v in vocab.contexts.items() if k in allowed},
            measures={k: v for k, v in vocab.measures.items() if k in allowed},
            entities={k: v for k, v in vocab.entities.items() if k in allowed},
            calendars={k: v for k, v in vocab.calendars.items() if k in allowed})
    vocab = replace(vocab, default_units=tuple(default_units))
    cohort = cfg.graph and bool(planrules.cohort_terms(engine, req))
    hidden = Hidden.of(engine, principal)
    turn = hashlib.sha256(json.dumps([req.question, cfg.label, vocab.binding.model_dump(mode='json'),
        [entry.model_dump(mode='json', by_alias=True) for entry in history]], sort_keys=True).encode()).hexdigest()[:32]
    configured = combined is not None and any(cfg.uses_model(s) for s in ('resolution', 'sql_source'))
    model = combined.llm if configured else None
    source = 'model:' + model.name if model is not None else 'rules'
    runtrace.use(cfg, model)
    out.update(combined=False, versions={**out['versions'], 'planner': 'query-plan-v1', 'interpretation': source})
    if vocab.denial(req.question):
        return {**out, 'behaviour': 'refuse', 'reason': 'The requested read is not permitted. Contact the access administrator.'}
    interpreted = []
    failed = False
    grounding = model is not None and getattr(combined, 'plan_grounding', True)
    def failure():
        nonlocal failed
        failed = True
    def rules(question, vocabulary):
        return (planrules.followup(engine, req, vocabulary, cfg, history, turn)
                or planrules.read(engine, req, clarified_vocabulary(history, vocabulary, question), cfg, turn, cohort))
    def interpret(question, vocabulary):
        def generate():
            return (planmodel.read(model, question, vocabulary, history, turn, grounding=grounding, on_failure=failure) if model is not None
                    else rules(question, vocabulary))
        def compute():
            reply = generate()
            return {"reply": reply.model_dump(mode="json", by_alias=True, exclude_unset=True), "failed": failed}
        if active():
            held = memo("interpret:" + turn, compute)
            if held["failed"]:
                failure()
            reply = reply_adapter.validate_python(held["reply"])
        else:
            reply = generate()
        interpreted.append(reply)
        return reply
    def floor(question, vocabulary):
        return interpreted[0] if failed else rules(question, vocabulary)
    citations = {c.id: ('pack:' + c.term,) for c in vocab.contexts.values()}
    readable_units = engine.g.readable_units(principal) if principal else set(engine.UNIT_ORDER)
    def chosen(plan, sql, guard):
        public = {'sql': sql, 'sql_source': 'query-plan-v1', 'guard': guard}
        if len(plan.measures) == 1 and not plan.comparisons and not plan.derived:
            public['unit'] = vocab.contexts[plan.measures[0].context.id].unit
        stream_event({'event': 'plan', 'plan': planner.safe(public)})
        context = public_context(engine, req, cfg, history, plan, vocab, describe, turn)
        if context is None and req.term:
            context = {'term': req.term}
        stream_event({'event': 'context', 'context': planner.safe(context)})

    planner = Planner(vocab, engine.w, hidden, citations, runtimes=runtimes, readable_units=readable_units, on_plan=chosen if streaming() else None)
    runtrace.stage('plan')
    result = planner.answer(req.question, interpret, turn, history, grounding=grounding,
                            fallback=floor if grounding else None,
                            release_periods=lambda needs, runtime: engine.g.release_periods(needs, runtime))
    reply = interpreted[0] if interpreted else None
    if reply is not None and (refusal := result.get('model_refusal')):
        out['trace'].append({'step': 'model_plan_refused', 'outcome': refusal['kind'],
                             'reason': '; '.join(refusal.get('missing') or []) or 'Choose the intended declared meaning.',
                             'choices': refusal.get('choices', []), 'fallback': refusal.get('fallback')})
    executed_source = 'rules' if result.get('model_refusal', {}).get('fallback') == 'rules' else source
    out['trace'].append({'step': 'plan', 'planner': 'query-plan-v1', 'interpretation': executed_source, 'outcome': result['kind']})
    if result['kind'] != 'result':
        why = '; '.join(result.get('missing') or [])
        if result['kind'] == 'refuse' and cfg.graph:
            requested = set(req.units)
            if not requested and reply is not None and reply.kind == 'propose':
                requested = {vocab.contexts[m.context.id].unit for m in reply.plan.measures if m.context.id in vocab.contexts}
            pairs = [(a, b) for n, a in enumerate(engine.UNIT_ORDER) for b in engine.UNIT_ORDER[n + 1:]
                     if {a, b} <= requested and frozenset((a, b)) in vocab.no_join]
            if pairs and (req.special == 'key_name_join' or req.terms_found or req.counted
                          or not_joinable_named(req.question.casefold(), engine)):
                out['joins_refused'] = [a + '-' + b for a, b in pairs]
                out['fact'] = next({k: v for k, v in f.items() if k not in ('a', 'b')}
                                   for f in runtime.no_join_declarations if {f['a'], f['b']} == set(pairs[0]))
                a, b = pairs[0]
                context_units = {c.unit for c in vocab.contexts.values() if c.term in req.terms_found and any(j.required for j in c.joins)}
                if len(context_units) == 1 and b in context_units:
                    a, b = b, a
                why = f"No join path between {a} and {b}. {out['fact']['note']}"
        if len(req.units) > 1 and cohort:
            from kge.services.ask.engine.combined import across
            held = across(engine, req.units)
            out.update(joins_refused=[f['pair'] for f in held['facts']], certified_joins=held['certified'])
            why = held['reason']
        if result.get('guard'):
            out['guard'] = result['guard']
        if result.get('skipped_runtimes'):
            out['_skipped_runtimes'] = result['skipped_runtimes']
        if model is None and why and re.search(r'\bunrequested node\b', why):
            why = 'The request could not be matched to a certified measure.'
        out.update(DECISIONS[result['kind']], reason=why or 'Choose the intended declared meaning before a figure can be computed.')
        if model is None and cohort and result['kind'] == 'abstain' and not req.problem and len(req.units) == 1 and scope.products is None:
            unit = req.units[0]
            available = {c.term for c in runtime.contexts.values() if c.unit == unit}
            available.update(m['term'] for m in runtime.mapping_declarations if m['unit'] == unit)
            missing = sorted(set(req.terms_found) - available)
            if missing:
                out['reason'] = f"Not certified in {engine.UNIT_LABEL[unit]}: {', '.join(missing)}. The answer would rest on a guess."
        if (cfg.graph and result['kind'] == 'abstain' and req.term and not req.problem
                and not any(c.term in req.terms_found for c in vocab.contexts.values())
                and not any(m['term'] in req.terms_found for m in runtime.mapping_declarations)):
            out['reason'] = f"'{req.term}' has no certified implementation in any warehouse."
        if req.problem and result['kind'] == 'abstain' and model is None:
            out.update(engine.request_abstention(req, req.problem))
        if (result['kind'] == 'abstain' and result.get('phase') != 'release' and cfg.graph
                and result.get('missing') and all(vocab.candidates(m) or engine.ALIASES.get(m.casefold()) in req.terms_found
                    for m in result['missing'])):
            contexts = list({c.id: c for term in req.terms_found for c in engine.semantics.contexts_of(term)
                             if c.id in vocab.contexts and any(j.required for j in c.joins)}.values())
            if len(contexts) == 1:
                diagnostic = describe(contexts[0], req).decision
                if diagnostic and diagnostic['behaviour'] in ('abstain', 'abstain_with_reason'):
                    out['reason'] = planner.safe(diagnostic['reason'])
        if result.get('phase') == 'release':
            out['behaviour'] = 'abstain_with_reason'
            out['sql_run'] = result['sql_run']
            if reply is not None and reply.kind == 'propose' and len(reply.plan.measures) == 1:
                ctx = vocab.contexts[reply.plan.measures[0].context.id]
                tables = [j.target for j in ctx.joins if j.required and j.target in vocab.releases]
                if len(tables) == 1:
                    out['reason'] = release_decline(out['reason'], tables)
            if 'is not released yet.' not in out['reason']:
                out['reason'] = release_decline(out['reason'])
        elif (result['kind'] == 'abstain' and not req.problem and 'certified_joins' not in out
              and out['reason'] != 'No glossary term recognised in the question.' and not out['reason'].startswith('Not certified in ')
              and 'no figure' not in out['reason'].casefold() and 'none is given' not in out['reason'].casefold()):
            out['reason'] = out['reason'].rstrip('. ') + '. No figure is given.'
        if result['kind'] == 'clarify':
            if len(result['choices']) > 1:
                out['reason'] = 'Which meaning do you intend?'
            entities = [vocab.entities[c['id']] for c in result['choices'] if c['id'] in vocab.entities]
            if (len(entities) == len(result['choices']) == 1 and req.special is None
                    and reply is not None and reply.kind == 'clarify' and reply.spans):
                unit = entities[0]['table'].split('.')[0]
                written_units = {i: p.text for p in planrules.ledger(req.question, turn, vocab)
                                 for i in p.candidates if i in req.units}
                unresolved = [written_units.get(u, engine.UNIT_LABEL[u]) for u in req.units if u != unit]
                if unresolved:
                    label = written_units.get(unit, engine.UNIT_LABEL[unit])
                    noun = entities[0]['label'].removeprefix(label + ' ')
                    out['reason'] = (f"{' and '.join(unresolved)} {planrules.plural_forms(noun)[1]} is unresolved: "
                                     f"{noun} names only the declared {label} entity.")
            spans = tuple(reply.spans) if reply is not None and reply.kind == 'clarify' and reply.spans else tuple(
                p.span for p in planrules.ledger(req.question, turn, vocab) if p.text in result.get('missing', ()) or set(p.candidates) & {c['id'] for c in result.get('choices', ())})
            clarification = Clarification(spans=spans or (Span(turn=turn, start=0, end=len(req.question)),),
                slot='meaning', choices=tuple(Choice.model_validate(c) for c in result['choices']))
            out['clarification'] = planner.safe(clarification.model_dump(mode='json'))
            out['history'] = [h.model_dump(mode='json', by_alias=True) for h in history] + [HistoryEntry(turn_id=turn, question=req.question, plan=None,
                clarification=clarification, binding=vocab.binding).model_dump(mode='json', by_alias=True)]
        if (cfg.graph and result['kind'] == 'abstain' and not req.problem and result.get('phase') != 'release'
                and measure_question(req.question)
                and (not any(c.term in req.terms_found for c in vocab.contexts.values())
                     or any(p.kind == 'unknown' for p in planrules.ledger(req.question, turn, vocab)))):
            labels = declared_measure_labels(req, vocab, default_units)
            if 'has no certified implementation in any warehouse' in out['reason'] and any(
                    p.kind == 'unknown' for p in planrules.ledger(req.question, turn, vocab)):
                out['reason'] = 'The requested measure is not declared. No figure is given.'
            if labels:
                out['reason'] += ' Declared measures here are ' + planner.safe('; '.join(labels)) + '. Which would you like?'
        if (reply is not None and reply.kind == 'abstain' and result['kind'] == 'abstain'
                and req.period is not None and not req.problem
                and re.search(r'not (?:yet )?released|unreleased|not started|future', out['reason'], re.I)):
            release_plan = planrules.read(engine, req, vocab, cfg, turn, cohort)
            if release_plan.kind == 'propose' and (offer := planner.release_offer(
                    req.question, release_plan.plan, turn, engine.g.release_periods)):
                out['reason'] = out['reason'].rstrip('. ') + '. ' + offer
        if 'history' in out:
            out['history'] = planner.safe(out['history'])
        return out
    plan = QueryPlan.model_validate(result['plan'])
    out.update(result=result['result'], sql=result['sql'], guard=result['guard'], sql_source='query-plan-v1')
    if engine.w.runtimes_active(runtimes):
        for k in ('reference_sql', 'runtime', 'physical_tables', 'runtime_certified', 'certified_in', 'live_data', 'queried_at'):
            if k in result:
                out[k] = result[k]
    if result.get('skipped_runtimes'):
        out['_skipped_runtimes'] = result['skipped_runtimes']
    if plan.time.kind == 'ytd':
        from datetime import timedelta
        from kge.semantics.planperiods import time_bounds
        first, end = time_bounds(plan.time, vocab.calendars)
        last = end - timedelta(days=1)
        prefix = 'fiscal year to date' if vocab.calendars[plan.time.calendar.id]['year_start_month'] != 1 else 'year to date'
        start = first.strftime('%b %Y' if first.year != last.year else '%b')
        out['period_label'] = f"{prefix}, {start}–{last:%b %Y}"
    if result['months']:
        out['months'] = result['months']
    authored = engine.semantics.contexts if cfg.graph else {}
    if any(m.context.id in authored for m in plan.measures):
        out['sql_run'] = result['sql_run']
    out['history'] = [h.model_dump(mode='json', by_alias=True) for h in history] + [HistoryEntry(turn_id=turn, question=req.question, plan=plan,
        clarification=None, binding=vocab.binding).model_dump(mode='json', by_alias=True)]
    out['history'] = planner.safe(out['history'])
    rows = [r for r in result['result']['rows'] if not r.get('total')]
    contexts = [vocab.contexts[m.context.id] for m in plan.measures]
    outputs = [column for column in result['result']['columns'] if column['kind'] != 'dimension']
    observed = {source for row in rows for column, cell in zip(outputs, row['cells'])
                if cell['value']['kind'] != 'null' for source in column['lineage']}
    out['trace'].append({'step': 'sources', 'tables': sorted({c.fact for c in contexts if c.id in observed})})
    if contexts and all(c.id.startswith('cohort:') for c in contexts):
        return cohort_answer(engine, combined, req, principal, purpose, cfg, scope, out, plan, contexts, rows)
    if len(plan.measures) == 1 and not plan.comparisons and not plan.derived:
        ctx = contexts[0]
        count = plan.measures[0].kind == 'distinct_count'
        context = public_context(engine, req, cfg, history, plan, vocab, describe, turn)
        if context is not None:
            out['context'] = context
        out.update({"behaviour": "figure"}, unit=ctx.unit, column=ctx.values[0].split('.', 1)[1] if ctx.values else '',
                   join_path=[j.condition for j in ctx.joins if j.required], notes=[] if plan.time.kind != 'all_available' else ['no period asked: every month the filters keep'])
        if ctx.id in authored and any(j.required for j in ctx.joins):
            described = describe(ctx, req)
            about = described.about()
            out['join_path'], out['notes'] = about['joins'], list(dict.fromkeys([*out['notes'], *described.notes]))
            if plan.time.kind != 'all_available':
                out['notes'] = [n for n in out['notes'] if n != 'no period asked: every month the filters keep']
            if plan.measures[0].mode == 'as_reported':
                out['notes'].append('as reported: each version is read at the declared release freeze date')
            out['trace'].insert(1, {'step': 'context', **about})
            if req.latest and result['months']:
                from datetime import date
                out['notes'].append(date.fromisoformat(result['months'][-1]).strftime('%B %Y, the latest released month'))
        if ctx.aggregation == 'count':
            metadata = runtime.output_metadata.get(ctx.id, {})
            if metadata:
                out['column'] = metadata['tbl'] + '.' + metadata['col']
        if not any(j.required for j in ctx.joins):
            used = {n.id for d in plan.dimensions if d.kind == 'column' for b in d.bindings for n in b.path}
            used.update(n.id for p in plan.filters if getattr(getattr(p, 'left', None), 'kind', None) == 'column' for n in p.left.path)
            joins = [j.condition for j in ctx.joins if j.id in used]
            out['join_path'] = [re.sub(r'\s*=\s*', ' -> ', j) for j in joins]
            if req.period and req.period.kind in ('calendar_quarter', 'fiscal_quarter', 'year'):
                _, rollups, note = engine.period_clause(ctx.unit, ctx.fact.split('.', 1)[1], req.period, cfg, req.agg)
                out['join_path'][:0] = [m.group(1) + ' -> ' + m.group(2) for j in rollups
                    if (m := re.fullmatch(r'JOIN \w+ ON (\w+\.\w+) = (\w+\.\w+)', j))]
                if note and note not in out['notes']:
                    out['notes'].append(note)
        if count:
            entity = vocab.entities[plan.measures[0].entity.id]
            metadata = runtime.output_metadata.get(ctx.id, {})
            out['column'] = metadata['tbl'] + '.' + metadata['col'] if metadata else ', '.join(entity['table'].split('.', 1)[1] + '.' + key for key in entity['key'])
        if not plan.dimensions:
            out['figure'] = value(rows[0]['cells'][0]) if rows else None
        elif len(plan.dimensions) == 1:
            out.update(figure=None, rows={(f"{r['end']} {r['rank']}: " if r.get('end') else '') + str(r['dimensions'][0].get('value')): value(r['cells'][0]) for r in rows},
                       group_by=plan.dimensions[0].bindings[0].column.id.split('.', 1)[1] if plan.dimensions[0].kind != 'time' else plan.dimensions[0].id)
        if (len(plan.dimensions) == 1 and len(rows) == 1 and plan.dimensions[0].kind == 'column'
                and any(p.kind == 'compare' and p.op == 'eq' and p.left.kind == 'column'
                        and p.left.name.id == plan.dimensions[0].bindings[0].column.id for p in plan.filters)):
            out['figure'] = value(rows[0]['cells'][0])
            out.pop('rows', None)
            out.pop('group_by', None)
        memberships = [p for p in plan.filters if p.kind == 'membership']
        if len(memberships) == 1:
            predicate = memberships[0]
            far = vocab.contexts[predicate.context.id]
            fact = next(c for c in engine.semantics.certified if c['declared_join'] == predicate.relation.id)
            mine = vocab.entities[plan.measures[0].entity.id]['label'] if count else ctx.label
            theirs = vocab.entities[predicate.entity.id]['label']
            explanation = f"counts each {mine} a certified match links to a {theirs}, once" if count else f"aggregates {mine} only for members a certified match links to a {theirs}"
            out.update(certified_join=fact, warehouses_involved=sorted({ctx.unit, far.unit}),
                       join_path=list(dict.fromkeys([*out['join_path'], predicate.relation.id])), reason=f"{engine.UNIT_LABEL[ctx.unit]} and {engine.UNIT_LABEL[far.unit]} join through the certified join {fact['declared_join']} ({fact['note']}): the figure {explanation}.")
        elif ctx.id not in authored and not plan.dimensions:
            metadata = runtime.output_metadata.get(ctx.id, {})
            out['parts'] = [{k: metadata.get(k, '') if cfg.graph else '' for k in ('grain', 'currency', 'calendar', 'conversion')} | {
                'unit': ctx.unit, 'status': 'ok' if out['figure'] is not None else 'abstain',
                'reason': '; '.join(rows[0]['cells'][0]['null_reasons']) if rows else 'No observations matched the request.',
                'column': out['column'], 'value': out['figure'],
                'sql': out['sql'], 'guard': out['guard'], 'join_path': out['join_path'], 'notes': out['notes']}]
        label = measure_label(plan.measures[0], plan, vocab, req.question, turn) if not count else ctx.label
        if label != ctx.label:
            out['context'] = {**out.get('context', {}), 'term': label, 'label': label}
        return out
    same_term = len({c.term for c in contexts}) == 1
    if same_term and len({c.id for c in contexts}) > 1 and not plan.dimensions and not plan.comparisons and not plan.derived:
        parts = []
        metadata = runtime.output_metadata
        for n, ctx in enumerate(contexts):
            item = metadata.get(ctx.id, {}) if cfg.graph else {}
            parts.append({**item, 'unit': ctx.unit, 'status': 'ok' if rows and value(rows[0]['cells'][n]) is not None else 'abstain',
                          'reason': '; '.join(rows[0]['cells'][n]['null_reasons']) if rows else 'No observations matched the request.',
                          'value': value(rows[0]['cells'][n]) if rows else None,
                          'column': '.'.join(ctx.values[0].split('.')[1:]) if ctx.values else '',
                          'sql': result['sql'], 'guard': result['guard'],
                          'join_path': [j.condition for j in ctx.joins if j.required], 'notes': []})
        verdict, why = engine.commensurable(parts, req, cfg)
        missing = [p['unit'] for p in parts if p['value'] is None]
        if missing:
            verdict, why = False, 'incomplete: no observation for ' + ', '.join(missing) + '. A total without every part is not the total asked for'
        out.update(VERDICTS[verdict],
                   parts={p['unit']: p['value'] for p in parts}, plan=parts, commensurable=verdict, reason=why, missing_parts=missing,
                   joins_refused=[pair(a['unit'], b['unit'], engine) for n, a in enumerate(parts) for b in parts[n + 1:]
                                  if cfg.graph and frozenset((a['unit'], b['unit'])) in vocab.no_join])
        if verdict is not False:
            out['figure'] = round(sum(p['value'] for p in parts if p['value'] is not None), 2)
        return out
    out.update({"behaviour": "combined"}, combined=True, figures=[], citations=[], not_cited=[], withheld=[],
               answer_text=(out['period_label'] + ': ' if out.get('period_label') else '') + render(result['result']), phrased_by='template')
    return out


def render(result, labelled_breakdown=False):
    if not result['rows']:
        return 'No observations matched the checked request.'
    outputs = display_outputs(result)
    def shown_value(column, cell):
        if labelled_breakdown and cell['value']['kind'] in ('decimal', 'integer') and not column.get('format'):
            return (column.get('currency') + ' ' if column.get('currency') else '') + exact(value(cell))
        return display_value(column, cell)
    dimensions = [c for c in result['columns'] if c['kind'] == 'dimension']
    if len(outputs) > 1 and any(c.get('label') for c, _ in outputs):
        headers = [c.get('label') or c['id'] for c in dimensions] + [c.get('label') or c['id'] for c, _ in outputs]
        ranked = any(r.get('end') for r in result['rows'])
        if ranked:
            headers = ['End', 'Rank', *headers]
        def escaped(value):
            return str(value).replace('|', '\\|').replace('\n', ' ')
        lines = [' | '.join(escaped(v) for v in headers), ' | '.join('---' for _ in headers)]
        for row in result['rows']:
            dims = [dimension_value(row, i) for i in range(len(row['dimensions']))]
            if row.get('total') and any(d['kind'] == 'null' for d in row['dimensions']):
                dims[next(i for i, d in enumerate(row['dimensions']) if d['kind'] == 'null')] = 'Total'
            values = []
            for column, index in outputs:
                cell = row['cells'][index]
                shown = '' if row.get('total') and cell['value']['kind'] == 'null' and not cell['null_reasons'] else shown_value(column, cell)
                if cell['null_reasons']:
                    shown += ' (' + '; '.join(cell['null_reasons']) + ')'
                values.append(shown)
            lines.append(' | '.join(escaped(v) for v in ([row.get('end', ''), row.get('rank', '')] if ranked else []) + dims + values))
        return '\n'.join(lines)
    lines = []
    for row in result['rows']:
        dimensions = ', '.join(str(dimension_value(row, i)) for i, d in enumerate(row['dimensions']) if d['kind'] != 'null') + ' Total' if row.get('total') else ', '.join(str(dimension_value(row, i)) for i in range(len(row['dimensions'])))
        dimensions = dimensions.strip()
        if row.get('end'):
            dimensions = f"{row['end']} {row['rank']}: " + dimensions
        cells = []
        for column, index in outputs:
            cell = row['cells'][index]
            if row.get('total') and cell['value']['kind'] == 'null' and not cell['null_reasons']:
                continue
            shown = shown_value(column, cell)
            if cell['null_reasons']:
                shown += ' (' + '; '.join(cell['null_reasons']) + ')'
            cells.append((column.get('label') or column['id']) + ': ' + shown)
        lines.append((dimensions + ': ' if dimensions else '') + '; '.join(cells))
    return '\n'.join(lines) or 'No observations matched the checked request.'


def cohort_answer(engine, combined, req, principal, purpose, cfg, scope, out, plan, contexts, rows):
    from kge.services.ask.engine.combined import Combined, number_citations, period_words
    combined = combined or Combined(engine, llm=None)
    metadata = planrules.snapshot(engine).output_metadata
    figures = []
    for n, (measure, ctx) in enumerate(zip(plan.measures, contexts)):
        item = metadata[ctx.id.removeprefix('cohort:')]
        count = measure.kind == 'distinct_count'
        figures.append({'id': measure.id, 'label': ctx.term, 'term': ctx.term,
            'kind': 'count' if count else ctx.aggregation, 'role': planrules.mapping_role(item),
            'column': item['tbl'] + '.' + item['col'], 'currency': '' if count else item['currency'] or '',
            'certified_by': item['certified_by'], 'certified_on': item['certified_on'],
            'value': value(rows[0]['cells'][n]) if rows else None, 'cite': None})
    out['trace'].append({'step': 'map', 'columns': [{'term': f['term'], 'column': f['column'],
        'certified_by': f['certified_by'], 'certified_on': f['certified_on']} for f in figures]})
    unit = contexts[0].unit
    runtrace.stage('retrieve')
    docs = scope.documents({'question': req.question, **combined.sources(req.question, [c.term for c in contexts], principal, purpose, unit)})
    docs.pop('question')
    hits = docs.pop('vector_hits')
    out['trace'].append({'step': 'retrieve', 'vector_hits': hits, 'cited': [c['doc'] for c in docs['citations']]})
    runtrace.stage('bind')
    for figure in figures:
        figure['cite'] = next((c['doc'] for c in docs['citations'] if figure['term'] in c['terms']), None)
    bound = [tuple([f['cite']]) if f['cite'] else () for f in figures]
    for row in out['result']['rows']:
        for cell, refs in zip(row['cells'], bound):
            cell['citations'] = list(refs)
    number_citations(docs['citations'], figures)
    out.update({"behaviour": "combined"}, combined=True, figures=figures, unit=unit, unit_label=engine.UNIT_LABEL[unit],
               period=period_words(req.period), **docs)
    runtrace.stage('render')
    text, phrased_by = combined.phrase(out, cfg)
    return {**out, 'answer_text': text, 'phrased_by': phrased_by}
