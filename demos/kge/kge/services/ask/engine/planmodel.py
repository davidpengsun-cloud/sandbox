"""Versioned interpretation through the model port; no executable text crosses it."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from pydantic import ValidationError

from kge.adapters.model import NotJSON, UnavailablePlan, no_fallback
from kge.semantics.coverage import ledger, names_in, name_supported, one_meaning, referenced_targets, typed_scalar
from kge.semantics.plan import Abstain, Name, QueryPlan, Span, reply_adapter
from kge.semantics.plancheck import CheckedPlan, check_plan, role
from kge.semantics.planhistory import MAX_HISTORY_TURNS, MAX_HISTORY_CHARS, validate, clarified_vocabulary, inherited
from kge.semantics.planvocabulary import denial_pattern
from kge.services.ask.engine import runtrace

VERSION = 'query-plan-v1'
MAX_REPLY_ITEMS = 256
MAX_REPLY_CHARS = 120000
INSTRUCTIONS = (
    'Interpret the whole question as a checked plan. Copy identifiers only from the vocabulary. '
    'Return one PlanReply JSON object matching the schema. Never return SQL, expressions, figures or result cells. '
    'Do not invent names, drop constraints or choose between ambiguous meanings. Clarify ambiguity; abstain for '
    'missing declarations. A follow-up proposes a full replacement with base_turn; preserve unchanged node ids '
    'and obligations. A new question has base_turn null. Every meaningful span needs an applied obligation. '
    'For top N and bottom N together use one order with direction both and limit N per end; '
    'bind both top and bottom to that output and both numbers to limit.'
)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def sanitized(value, vocabulary):
    denied = denial_pattern(tuple(sorted(vocabulary.denied, key=lambda p: (-len(p), p.casefold()))))

    def clean(item):
        if isinstance(item, str):
            return denied.sub('(withheld)', item) if denied else item
        if isinstance(item, dict):
            return {clean(k): clean(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        return item

    return clean(value)


def bundle(vocabulary):
    products = [p for p in vocabulary.binding.products if p.casefold() in {t.casefold() for t in vocabulary.tables}]
    return sanitized({**({'products': products} if products else {}),
            **({'default_units': list(vocabulary.default_units)} if vocabulary.default_units else {}), 'names': vocabulary.public(),
            'phrases': dict(vocabulary.phrases),
            'contexts': [{'id': c.id, 'label': c.label, 'definition': c.description, 'fact': c.fact,
                          'measure': c.measure, 'grain': c.grain,
                          **({'ytd_calendar': c.ytd_calendar} if c.ytd_calendar else {}),
                          **({'version_dates': dict(c.version_dates)} if c.version_dates else {}),
                          'paths': [{'id': j.id, 'source': j.source, 'target': j.target, 'alias': j.alias, 'kind': j.kind}
                                    for j in c.joins if j.id in vocabulary.entries]}
                         for c in vocabulary.contexts.values()],
            'calendars': {k: dict(v) for k, v in vocabulary.calendars.items()}}, vocabulary)


def reply_schema(removals=False):
    def bound(value):
        if isinstance(value, dict):
            if value.get('type') == 'array':
                value['maxItems'] = MAX_REPLY_ITEMS
            for child in value.values():
                bound(child)
        elif isinstance(value, list):
            for child in value:
                bound(child)

    schema = reply_adapter.json_schema()
    if not removals:
        schema['$defs']['Obligation']['properties']['state']['enum'].remove('removed')
    bound(schema)
    return schema


def payload(question, vocabulary, history=(), turn='turn'):
    previous = [h.model_dump(mode='json', by_alias=True) for h in history]
    if len(history) > MAX_HISTORY_TURNS or len(encoded(previous)) + len(question) > MAX_HISTORY_CHARS:
        raise ValueError('Conversation history exceeds the interpretation bounds.')
    previous = sanitized(previous, vocabulary)
    public = bundle(vocabulary)
    key = encoded([VERSION, ' '.join(question.casefold().split()), digest(public), digest(previous)])
    return {'version': VERSION, 'recording_key': key, 'question': question, 'turn_id': turn,
            'history': previous, 'vocabulary': public, 'schema': reply_schema(removals=any(h.plan is not None for h in history)),
            'obligations': [{'span': p.span.model_dump(), 'text': p.text, 'kind': p.kind,
                             'candidates': p.candidates, 'operation': p.operation}
                            for p in ledger(question, turn, vocabulary)]}


def bounded(value):
    if isinstance(value, (list, tuple)):
        return len(value) <= MAX_REPLY_ITEMS and all(bounded(v) for v in value)
    if isinstance(value, dict):
        return all(bounded(v) for v in value.values())
    return True


def _valid_span(value):
    try:
        Span.model_validate(value)
        return True
    except (ValidationError, TypeError):
        return False


def _rewrite(data, key, value, rule, path, reason, attempt, spans=()):
    if key not in data or data[key] != value:
        before = data.get(key)
        data[key] = value
        runtrace.plan_rewrite(rule, path + '/' + key, reason, before, value, attempt, spans)
        return True
    return False


def _normalize_empty_obligations(raw, attempt):
    if not isinstance(raw, dict) or raw.get('kind') != 'propose' or not isinstance(raw.get('plan'), dict):
        return raw
    obligations = raw['plan'].get('obligations')
    if isinstance(obligations, (list, tuple)):
        kept = [o for o in obligations if not (isinstance(o, dict)
                and isinstance(o.get('spans'), (list, tuple)) and not o['spans'])]
        if len(kept) != len(obligations):
            _rewrite(raw['plan'], 'obligations', kept, 'empty-obligation-evidence', '/plan',
                     'An empty citation supplies no evidence; selections still require checked ledger coverage', attempt)
    return raw


def _name_grounding(raw, obligations, vocabulary, history):
    if not isinstance(raw, dict) or raw.get('kind') != 'propose':
        return {}, ()

    def structural(value):
        if isinstance(value, dict):
            if set(value) == {'id', 'evidence'} and isinstance(value['evidence'], (list, tuple)):
                return {**value, 'evidence': [s for s in value['evidence'] if _valid_span(s)]}
            return {k: structural(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [structural(v) for v in value]
        return value

    try:
        plan = reply_adapter.validate_python(structural(raw)).plan
        inherited_pieces = inherited(plan, history, vocabulary)
        pieces = [*obligations, *({'span': p.span.model_dump(), 'candidates': p.candidates, 'kind': p.kind} for p in inherited_pieces)]
        nodes = {n.id: n for n in plan.nodes()} | {'time': plan.time}
        grounded = {}
        for o in plan.obligations:
            if o.state != 'applied' or len(o.spans) != 1:
                continue
            matches = [p for p in pieces if p['span'] == o.spans[0].model_dump()]
            if (len(matches) != 1 or matches[0]['kind'] != 'name'
                    or sum(o.spans[0] in other.spans for other in plan.obligations) != 1):
                continue
            selected = [nodes[t] for t in o.targets if t in nodes]
            candidates = set(matches[0]['candidates']) & referenced_targets(selected, vocabulary)
            if not candidates or not one_meaning(candidates, vocabulary):
                continue
            for node in selected:
                for name in names_in(node):
                    if name_supported(name.id, candidates, vocabulary):
                        grounded.setdefault((getattr(node, 'id', 'time'), name.id), []).append(o.spans[0].model_dump())
        return grounded, inherited_pieces
    except (ValidationError, ValueError, KeyError, TypeError):
        return {}, ()


def _normalize_name_evidence(raw, obligations, vocabulary, history=(), attempt=0):
    grounded, previous = _name_grounding(raw, obligations, vocabulary, history)
    supported = [*obligations, *({'span': p.span.model_dump(), 'candidates': p.candidates} for p in previous)]
    schema = reply_schema()
    fields = {k for definition in schema['$defs'].values() for k in definition.get('properties', {})}

    def clean(value, path, owner=None):
        if isinstance(value, dict):
            if 'kind' in value and isinstance(value.get('id'), str):
                owner = value['id']
            elif path == '/plan/time':
                owner = 'time'
            if set(value) == {'id', 'evidence'} and isinstance(value['id'], str) and isinstance(value['evidence'], (list, tuple)):
                value = dict(value)
                evidence = []
                for i, span in enumerate(value['evidence']):
                    malformed = not _valid_span(span)
                    if malformed and (owner, value['id']) not in grounded:
                        evidence.append(span)
                        continue
                    if not malformed and any(p['span'] == span and name_supported(value['id'], p.get('candidates') or (), vocabulary) for p in supported):
                        evidence.append(span)
                        continue
                    entry = vocabulary.entries.get(value['id'])
                    if not malformed and (entry is None or entry.kind not in ('measure', 'context')
                            or not any(p['span'] == span and p.get('candidates') for p in supported)):
                        evidence.append(span)
                        continue
                    runtrace.plan_rewrite('name-structure' if malformed else 'name-candidate', path + '/evidence/' + str(i),
                                          'Checked obligation grounds the name' if malformed else 'Span does not support this name',
                                          span, None, attempt, grounded[owner, value['id']] if malformed else (span,))
                value['evidence'] = evidence
                return value
            return {k: clean(v, path + '/' + k, owner) if k in fields else v for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v, path + '/' + str(i), owner) for i, v in enumerate(value)]
        return value

    return clean(raw, '')


def _path_to(context, table, vocabulary, level=None):
    paths = [(context.fact, context.fact, (), {context.fact})]
    found = []
    for current, reference, path, visited in paths:
        if current == table:
            if level is None or sum(j.kind == 'hierarchy' for j in context.joins if j.id in path) == level:
                try:
                    role(vocabulary, context.id, table + '.column', tuple(Name(id=p, evidence=()) for p in path), level)
                    found.append(path)
                    if len(found) > 1:
                        return None
                except (ValueError, KeyError):
                    pass
        for join in context.joins:
            if (join.source in (current, reference, current.rsplit('.', 1)[-1], reference.rsplit('.', 1)[-1])
                    and not join.through and join.id not in path and join.id in vocabulary.entries
                    and join.target in vocabulary.tables and join.target.split('.')[0] == context.unit):
                target = join.alias or join.target
                if target not in visited:
                    paths.append((join.target, target, (*path, join.id), visited | {target}))
    return found[0] if len(found) == 1 else None


def _fill_path(binding, key, context, column, vocabulary, path, attempt, rule, level=None, spans=()):
    if key not in binding or (binding[key] == [] and column.rsplit('.', 1)[0] != context.fact):
        route = _path_to(context, column.rsplit('.', 1)[0], vocabulary, level)
        if route is not None:
            _rewrite(binding, key, [{'id': p, 'evidence': []} for p in route], rule, path,
                     'Exactly one declared route is eligible', attempt, spans)


def _items(value):
    return value if isinstance(value, (list, tuple)) else ()


def _normalize_propose(raw, question, vocabulary, turn, attempt=0):
    if not isinstance(raw, dict) or raw.get('kind') != 'propose' or not isinstance(raw.get('plan'), dict) or raw.get('base_turn') is not None:
        return raw
    try:
        validated = reply_adapter.validate_python(raw)
        if isinstance(check_plan(validated.plan, vocabulary, question, turn), CheckedPlan):
            return raw
    except (ValidationError, ValueError, KeyError, TypeError):
        pass
    plan = deepcopy(raw['plan'])
    pieces = ledger(question, turn, vocabulary)
    root = '/plan'
    time = plan.get('time')
    if isinstance(time, dict) and time.get('kind') == 'single' and isinstance(time.get('period'), dict):
        period = time['period']
        value = period.get('value')
        if period.get('kind') == 'fiscal_quarter' and isinstance(value, str) and re.fullmatch(r'\d{4}-Q[1-4]', value):
            calendars = [c for c, spec in vocabulary.calendars.items() if spec['quarter_label'].startswith('FY')]
            for piece in pieces:
                matched = re.fullmatch(r'FY(\d{2}|\d{4})-Q([1-4])', piece.text.upper()) if piece.kind == 'time' else None
                if (matched and isinstance(period.get('calendar'), dict) and calendars == [period['calendar'].get('id')]
                        and int(value[:4]) == int(matched[1]) + (2000 if len(matched[1]) == 2 else 0)
                        and value[-1] == matched[2]):
                    _rewrite(period, 'value', piece.text.upper(), 'fiscal-format', root + '/time/period',
                             'Equivalent fiscal year and quarter on the declared FY calendar', attempt, (piece.span.model_dump(),))
                    break
    measures = _items(plan.get('measures'))
    if not any(p.operation in ('current', 'as_reported') for p in pieces):
        for i, measure in enumerate(measures):
            if isinstance(measure, dict) and measure.get('mode') == 'as_reported':
                _rewrite(measure, 'mode', 'current', 'default-mode', root + '/measures/' + str(i), 'No time mode was requested', attempt)
    if not any(p.operation == 'separate' for p in pieces) and plan.get('layout') == 'separate':
        _rewrite(plan, 'layout', 'aligned', 'default-layout', root, 'Separate figures were not requested', attempt)
    dimensions = plan.get('dimensions')
    if not any(p.operation == 'dimension' for p in pieces):
        if isinstance(dimensions, (list, tuple)) and dimensions:
            _rewrite(plan, 'dimensions', [], 'ungrouped', root, 'No grouping operation was requested', attempt)
    elif isinstance(dimensions, (list, tuple)):
        referenced = set()

        def refs(value):
            if isinstance(value, str):
                referenced.add(value)
            elif isinstance(value, dict):
                for k, v in value.items():
                    if k not in ('id', 'evidence'):
                        refs(v)
            elif isinstance(value, (list, tuple)):
                for v in value:
                    refs(v)

        for field in ('comparisons', 'derived', 'order'):
            refs(plan.get(field))
        if plan.get('comparisons'):
            referenced.update(d.get('id') for d in dimensions if isinstance(d, dict) and d.get('kind') == 'time')
        candidates = {c for p in pieces if p.operation == 'dimension' and p.kind == 'name' for c in p.candidates
                      if c in vocabulary.entries and vocabulary.entries[c].kind == 'column'}
        columns = [d for d in dimensions if isinstance(d, dict) and d.get('kind') == 'column']
        kept = []
        for i, dimension in enumerate(dimensions):
            path = root + '/dimensions/' + str(i)
            if (isinstance(dimension, dict) and dimension.get('kind') == 'time' and dimension.get('id') not in referenced
                    and not any(p.operation == dimension.get('grain') or (p.operation == 'dimension' and p.value == dimension.get('grain')) for p in pieces)):
                runtrace.plan_rewrite('unused-time-dimension', path, 'Unrequested grain has no comparison, derived or order reference', dimension, None, attempt)
                continue
            if isinstance(dimension, dict) and dimension.get('kind') in ('column', 'hierarchy'):
                for j, binding in enumerate(_items(dimension.get('bindings'))):
                    if not isinstance(binding, dict) or not isinstance(binding.get('context'), dict) or not isinstance(binding.get('column'), dict):
                        continue
                    ctx = vocabulary.contexts.get(binding['context'].get('id'))
                    if ctx is None:
                        continue
                    bp = path + '/bindings/' + str(j)
                    if (dimension.get('kind') == 'column' and len(columns) == 1 and len(candidates) == 1
                            and isinstance(binding['column'].get('evidence'), (list, tuple))
                            and all(_valid_span(s) for s in binding['column']['evidence'])):
                        candidate = vocabulary.entries[next(iter(candidates))]
                        route = _path_to(ctx, candidate.column.rsplit('.', 1)[0], vocabulary)
                        if candidate.context in (None, ctx.id) and route is not None and binding['column'].get('id') != candidate.id:
                            spans = [p.span.model_dump() for p in pieces if candidate.id in p.candidates and p.operation == 'dimension']
                            _rewrite(binding, 'column', {**binding['column'], 'id': candidate.id, 'evidence': spans}, 'grouping-column', bp,
                                     'One requested column fits this binding context', attempt, spans)
                    entry = vocabulary.entries.get(binding['column'].get('id'))
                    if entry is not None and entry.kind == 'column':
                        spans = [p.span.model_dump() for p in pieces if entry.id in p.candidates]
                        _fill_path(binding, 'path', ctx, entry.column, vocabulary, bp, attempt, 'dimension-path', binding.get('level'), spans)
            kept.append(dimension)
        plan['dimensions'] = kept
    ids = set()
    missing_memberships = []

    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get('id'), str):
                ids.add(value['id'])
            if value.get('kind') == 'membership' and not value.get('id'):
                missing_memberships.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(plan)
    contexts = [vocabulary.contexts.get(m.get('context', {}).get('id')) for m in measures if isinstance(m, dict) and isinstance(m.get('context'), dict)]
    if len(contexts) == 1 and contexts[0] is not None:
        ctx = contexts[0]
        protected = {f for d in _items(plan.get('derived')) if isinstance(d, dict) and d.get('kind') == 'share'
                     for f in _items(d.get('remove_filters')) if isinstance(f, str)}

        def denominator_scope(value):
            if isinstance(value, dict):
                return (isinstance(value.get('id'), str) and value['id'] in protected
                        or any(denominator_scope(child) for child in value.values()))
            if isinstance(value, (list, tuple)):
                return any(denominator_scope(child) for child in value)
            return False

        def predicate(pred, path):
            if not isinstance(pred, dict):
                return False
            built = False
            if pred.get('kind') == 'membership':
                if not pred.get('id') and 'membership' not in ids and len(missing_memberships) == 1:
                    _rewrite(pred, 'id', 'membership', 'membership-id', path, 'Unique structural predicate id', attempt)
                    ids.add('membership')
                relation = pred.get('relation')
                if isinstance(relation, dict) and 'entity' not in pred:
                    rel = next((j for j in ctx.joins if j.id == relation.get('id') and j.through), None)
                    evidence = [p.span.model_dump() for p in pieces if rel is not None and rel.id in p.candidates]
                    entities = [eid for eid, e in vocabulary.entities.items() if rel is not None and e['table'] == rel.target]
                    if evidence and len(entities) == 1:
                        built |= _rewrite(pred, 'entity', {'id': entities[0], 'evidence': []}, 'membership-entity', path,
                                          'Question-grounded relation has one declared far entity', attempt, evidence)
            elif pred.get('kind') == 'compare' and isinstance(pred.get('left'), dict) and pred['left'].get('kind') == 'column':
                left = pred['left']
                entry = vocabulary.entries.get(left.get('name', {}).get('id')) if isinstance(left.get('name'), dict) else None
                if entry is None or entry.kind != 'column':
                    return False
                if not all(isinstance(e, (list, tuple)) for e in (pred.get('evidence', ()), left['name'].get('evidence', ()))):
                    return False
                matches = [p for p in pieces if p.kind == 'predicate' and entry.id in p.candidates and p.operation != 'null']
                spans = list(pred.get('evidence') or ()) + list(left['name'].get('evidence') or ())
                spans += [s for o in _items(plan.get('obligations')) if isinstance(o, dict) and pred.get('id') in _items(o.get('targets'))
                          for s in _items(o.get('spans'))]
                linked = [p for p in matches if p.span.model_dump() in spans]
                matches = linked or matches
                if (len(matches) == 1 and isinstance(left['name'].get('evidence'), (list, tuple))
                        and all(_valid_span(s) for s in spans)):
                    piece = matches[0]
                    scalar = typed_scalar(piece.value, entry.column, vocabulary)
                    if scalar is not None:
                        evidence = [piece.span.model_dump()]
                        built |= _rewrite(pred, 'op', piece.operation, 'predicate-op', path, 'Exactly one predicate piece matches this node', attempt, evidence)
                        built |= _rewrite(pred, 'value', scalar, 'predicate-scalar', path, 'Matched scalar uses the declared column type', attempt, evidence)
                        _rewrite(left['name'], 'evidence', evidence, 'predicate-evidence', path + '/left/name',
                                 'Matched predicate span supports this column candidate', attempt, evidence)
                _fill_path(left, 'path', ctx, entry.column, vocabulary, path + '/left', attempt, 'predicate-path',
                           spans=[p.span.model_dump() for p in pieces if entry.id in p.candidates])
            return built

        for i, pred in enumerate(_items(plan.get('filters'))):
            predicate(pred, root + '/filters/' + str(i))
        for i, measure in enumerate(measures):
            if not isinstance(measure, dict) or not isinstance(measure.get('filters'), (list, tuple)):
                continue
            moved = 0
            for j, pred in enumerate(tuple(measure['filters'])):
                index = j - moved
                path = root + '/measures/' + str(i) + '/filters/' + str(index)
                built = predicate(pred, path)
                if built and not denominator_scope(pred) and isinstance(plan.get('filters'), list):
                    identifier = pred['relation']['id'] if pred['kind'] == 'membership' else pred['left']['name']['id']
                    spans = [p.span.model_dump() for p in pieces if identifier in p.candidates]
                    _rewrite(measure, 'filters', [*measure['filters'][:index], *measure['filters'][index + 1:]],
                             'hoist-built-filter', root + '/measures/' + str(i),
                             'Remove the built single-measure filter outside denominator scope', attempt, spans)
                    _rewrite(plan, 'filters', [*plan['filters'], pred], 'hoist-built-filter', root,
                             'Apply the built single-measure filter globally', attempt, spans)
                    moved += 1
    _normalize_name_targets(plan, pieces, vocabulary, attempt)
    return {**raw, 'plan': plan}


def _normalize_name_targets(plan, pieces, vocabulary, attempt):
    try:
        parsed = QueryPlan.model_validate(plan)
    except ValidationError:
        return
    nodes = {n.id: n for n in parsed.nodes()} | {'time': parsed.time}
    for i, obligation in enumerate(_items(plan.get('obligations'))):
        if not isinstance(obligation, dict) or obligation.get('kind') != 'name' or obligation.get('state') != 'applied' or obligation.get('targets'):
            continue
        matches = [p for p in pieces if p.kind == 'name' and obligation.get('spans') == [p.span.model_dump()]]
        if len(matches) != 1 or not one_meaning(set(matches[0].candidates), vocabulary):
            continue
        piece = matches[0]
        candidates = set(piece.candidates)
        explicit = [identifier for identifier, node in nodes.items() if candidates & {name.id for name in names_in(node)}]
        rule, targets = 'name-target', explicit if len(explicit) == 1 else []
        if not explicit and len(candidates) == 1:
            entry = vocabulary.entries[next(iter(candidates))]
            if entry.kind == 'source_set':
                units = set(entry.value)
                supported = [m for m in parsed.measures if m.context.id in vocabulary.contexts and vocabulary.contexts[m.context.id].unit in units]
                by_unit = {u: [m.id for m in supported if vocabulary.contexts[m.context.id].unit == u] for u in units}
                if all(len(ms) == 1 for ms in by_unit.values()):
                    targets = [m.id for m in supported]
                    rule = 'source-set-target'
        if targets:
            _rewrite(obligation, 'targets', targets, rule, '/plan/obligations/' + str(i),
                     'Unique explicit typed meaning' if rule == 'name-target' else 'Minimal uniquely supported source measure set',
                     attempt, (piece.span.model_dump(),))


def read(model, question, vocabulary, history=(), turn='turn', *, grounding=False, on_failure=None):
    if vocabulary.denial(question):
        return Abstain(kind='abstain', spans=(), missing=('The requested read is not permitted.',))
    if problem := validate(history, vocabulary, question, turn):
        return Abstain(kind='abstain', spans=(), missing=problem.missing)
    vocabulary = clarified_vocabulary(history, vocabulary, question)
    data = payload(question, vocabulary, history, turn)
    instructions = INSTRUCTIONS
    if any(h.plan is not None for h in history):
        instructions += (' For an explicit deletion, omit the nodes from the full replacement and use an obligation '
            'with state removed, targeting their previous node ids and current deletion spans. Deletion evidence '
            'must name the removed meaning: drop a declared name, without the filter when unambiguous, or '
            'just the total to remove all breakdowns. Preserve every other checked meaning.')
    if vocabulary.default_units:
        instructions += ' With no source named in the question, prefer these default units for this attempt.'
    errors = []
    for attempt in range(2):
        prompt = VERSION + '\n' + encoded({**data, 'instructions': instructions, 'errors': errors})
        try:
            with runtrace.prompting(VERSION, digest(prompt), digest(data['vocabulary'])):
                raw = model.json(prompt)
            if not bounded(raw) or len(encoded(raw)) > MAX_REPLY_CHARS:
                raise ValueError('reply_bounds')
            if grounding:
                raw = _normalize_empty_obligations(raw, attempt)
            raw = _normalize_name_evidence(raw, data['obligations'], vocabulary, history, attempt)
            raw = _normalize_propose(raw, question, vocabulary, turn, attempt)
            return reply_adapter.validate_python(raw)
        except UnavailablePlan as exc:
            if on_failure is not None:
                on_failure()
            return Abstain(kind='abstain', spans=(), missing=(str(exc),))
        except (ValidationError, ValueError, NotJSON) as exc:
            if attempt == 0:
                errors = ([{'code': e['type']} for e in exc.errors(include_input=False)[:20]]
                          if isinstance(exc, ValidationError) else [{'code': 'invalid_reply'}])
                continue
            no_fallback('interpreting the query plan', exc)
        except Exception as exc:
            no_fallback('interpreting the query plan', exc)
        if on_failure is not None:
            on_failure()
        return Abstain(kind='abstain', spans=(), missing=('The configured model could not provide a valid query plan.',))
