"""Versioned interpretation through the model port; no executable text crosses it."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy

from pydantic import RootModel, ValidationError

from kge import packstore
from kge.adapters.model import NotJSON, UnavailablePlan, no_fallback
from kge.pipeline.settings import ask_model_corrections, ask_unknown_word, ask_vocabulary_selector, ask_vocabulary_warn_tokens
from kge.semantics.coverage import ledger, names_in, name_supported, one_meaning, referenced_targets, typed_scalar
from kge.semantics.plan import Abstain, Name, PlanReply, QueryPlan, Span, reply_adapter
from kge.semantics.plancheck import CheckedPlan, check_plan, role
from kge.semantics.planvocabulary import value_id
from kge.semantics.planhistory import MAX_HISTORY_TURNS, MAX_HISTORY_CHARS, validate, clarified_vocabulary, inherited
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.modelcall import (FILLER, MAX_ALLOWED, Correction, FieldError, contract_schema, nearest,
                                              structured_call)
from kge.services.ask.engine.plancontract import (FIELD_KINDS, PlanContract, at_pointer, bind_ids, contract_pointer,
                                                  field_key, fill, id_error, lean, locate, reply_pointer)

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
    'bind both top and bottom to that output and both numbers to limit. '
    'For top N within each breakdown, order.partition_by names the partition dimensions. '
    'A moving_average derived node names its length, time order_by and every other dimension as partition_by; '
    'Use only its context\'s explicitly declared moving_average_lengths; without that declaration no moving average is allowed.'
)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def sanitized(value, vocabulary):
    def withheld(text):
        out, end = [], 0
        for match in vocabulary.denials(text):
            out.extend((text[end:match.start()], '(withheld)'))
            end = match.end()
        return ''.join(out) + text[end:]

    def clean(item):
        if isinstance(item, str):
            return withheld(item)
        if isinstance(item, Mapping):
            return {clean(k): clean(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        return item

    return clean(value)


def bundle(vocabulary):
    products = [p for p in vocabulary.binding.products if p.casefold() in {t.casefold() for t in vocabulary.tables}]
    calendars = {}
    for identifier, declaration in vocabulary.calendars.items():
        calendar = {k: v for k, v in declaration.items() if k != 'spine'}
        spine = declaration.get('spine')
        if spine and spine['table'] in vocabulary.tables and spine['table'] + '.' + spine['column'] in vocabulary.entries:
            calendar['spine'] = {k: v for k, v in spine.items() if k not in ('attributes', 'recurrences')}
            if 'attributes' in spine:
                calendar['spine']['attributes'] = {k: v for k, v in spine['attributes'].items() if v in vocabulary.entries}
            if 'recurrences' in spine:
                calendar['spine']['recurrences'] = [entry for entry in spine['recurrences']
                    if entry['attribute'] in calendar['spine'].get('attributes', {})]
        calendars[identifier] = calendar
    return sanitized({**({'products': products} if products else {}),
            **({'default_units': list(vocabulary.default_units)} if vocabulary.default_units else {}), 'names': vocabulary.public(),
            'phrases': dict(vocabulary.phrases),
            'contexts': [{'id': c.id, 'label': c.label, 'definition': c.description, 'fact': c.fact,
                          'measure': c.measure, 'grain': c.grain,
                          **({'aggregations': list(vocabulary.measures[c.measure].aggregations)}
                             if vocabulary.measures[c.measure].aggregations else {}),
                          **({'moving_average_lengths': list(row['params']['lengths'])}
                             if (row := next((r for r in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                                 if r['kind'] == 'moving_average' and r['params']['measure'] == c.measure), None)) else {}),
                          **({'ytd_calendar': c.ytd_calendar} if c.ytd_calendar else {}),
                          **({'version_dates': dict(c.version_dates)} if c.version_dates else {}),
                          'paths': [{'id': j.id, 'source': j.source, 'target': j.target, 'alias': j.alias, 'kind': j.kind}
                                    for j in c.joins if j.id in vocabulary.entries]}
                         for c in vocabulary.contexts.values()],
            'calendars': calendars,
            **({'derived_measures': [{'id': i, 'formula': dict(d['formula']), 'label': d['label']}
                                    for i, d in vocabulary.derived_measures.items()]} if vocabulary.derived_measures else {})}, vocabulary)


def reply_schema(removals=False, vocabulary=None):
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
    if vocabulary is not None:
        if not any(spec.aggregations for spec in vocabulary.measures.values()):
            schema['$defs']['PlanMeasure']['properties'].pop('aggregation')
        if not vocabulary.derived_measures:
            schema['$defs']['Arithmetic']['properties'].pop('declaration')
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
    obligations = [{'span': p.span.model_dump(), 'text': p.text, 'kind': p.kind,
                    'candidates': p.candidates, 'operation': p.operation}
                   for p in ledger(question, turn, vocabulary)]
    return {'version': VERSION, 'recording_key': key, 'question': question, 'turn_id': turn,
            'history': previous, 'vocabulary': public, 'schema': reply_schema(removals=any(h.plan is not None for h in history), vocabulary=vocabulary),
            'obligations': obligations}


def select_vocabulary(question, obligations, permitted):
    """Extension point before the existing planner selection: all adds no further narrowing.

    Only the prompt's public vocabulary passes this seam; validation retains the original permitted vocabulary.
    """
    ask_vocabulary_selector()
    return permitted


def vocabulary_measurement(public):
    """Numeric size of the vocabulary actually sent, estimated at four serialized characters per token."""
    tokens = (len(encoded(public)) + 3) // 4
    threshold = ask_vocabulary_warn_tokens()
    return {'vocabulary_entries': len(public['names']), 'vocabulary_tokens_estimated': tokens,
            'vocabulary_warn_tokens': threshold, 'vocabulary_exceeded': tokens > threshold}


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


def _normalize_propose(raw, question, vocabulary, turn, attempt=0, *, breakdowns=False):
    """The rules' normalisation of a proposed plan; with breakdowns (the AI-Generated path, where the model decides what
    the question means) it never drops a breakdown the plan asks for, nor resets its time mode or layout, nor rewrites a
    comparison's operator, value or evidence or a breakdown's grouping column from the question's words, worded in the
    question or not."""
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
    if not breakdowns and not any(p.operation in ('current', 'as_reported') for p in pieces):
        for i, measure in enumerate(measures):
            if isinstance(measure, dict) and measure.get('mode') == 'as_reported':
                _rewrite(measure, 'mode', 'current', 'default-mode', root + '/measures/' + str(i), 'No time mode was requested', attempt)
    if not breakdowns and not any(p.operation == 'separate' for p in pieces) and plan.get('layout') == 'separate':
        _rewrite(plan, 'layout', 'aligned', 'default-layout', root, 'Separate figures were not requested', attempt)
    dimensions = plan.get('dimensions')
    if not any(p.operation == 'dimension' for p in pieces):
        if isinstance(dimensions, (list, tuple)) and dimensions and not breakdowns:
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
            if (not breakdowns and isinstance(dimension, dict) and dimension.get('kind') == 'time' and dimension.get('id') not in referenced
                    and not any(p.operation == dimension.get('grain') or (p.operation == 'dimension' and p.value == dimension.get('grain')) for p in pieces)):
                runtrace.plan_rewrite('unused-time-dimension', path, 'Unrequested grain has no comparison, derived or order reference', dimension, None, attempt)
                continue
            if isinstance(dimension, dict) and dimension.get('kind') in ('column', 'hierarchy', 'band'):
                for j, binding in enumerate(_items(dimension.get('bindings'))):
                    if not isinstance(binding, dict) or not isinstance(binding.get('context'), dict) or not isinstance(binding.get('column'), dict):
                        continue
                    ctx = vocabulary.contexts.get(binding['context'].get('id'))
                    if ctx is None:
                        continue
                    bp = path + '/bindings/' + str(j)
                    if (not breakdowns and dimension.get('kind') == 'column' and len(columns) == 1 and len(candidates) == 1
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
                if (not breakdowns and len(matches) == 1 and isinstance(left['name'].get('evidence'), (list, tuple))
                        and all(_valid_span(s) for s in spans)):
                    piece = matches[0]
                    scalar = typed_scalar(piece.value, entry.column, vocabulary)
                    if scalar is not None:
                        evidence = [piece.span.model_dump()]
                        sized = (piece.operation or '').startswith('magnitude_')
                        built |= _rewrite(pred, 'op', piece.operation.removeprefix('magnitude_') if sized else piece.operation,
                                          'predicate-op', path, 'Exactly one predicate piece matches this node', attempt, evidence)
                        if sized and 'magnitude' not in pred:
                            built |= _rewrite(pred, 'magnitude', True, 'predicate-magnitude', path,
                                              'The matched piece is a size threshold the model left unmarked; one it marked '
                                              'otherwise stays, for the word check to refuse', attempt, evidence)
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
    data['vocabulary'] = select_vocabulary(question, data['obligations'], data['vocabulary'])
    instructions = INSTRUCTIONS
    if any(h.plan is not None for h in history):
        instructions += (' For an explicit deletion, omit the nodes from the full replacement and use an obligation '
            'with state removed, targeting their previous node ids and current deletion spans. Deletion evidence '
            'must name the removed meaning: drop a declared name, without the filter when unambiguous, or '
            'just the total to remove all breakdowns. Preserve every other checked meaning.')
    if vocabulary.default_units:
        instructions += ' With no source named in the question, prefer these default units for this attempt.'
    sections = {'Vocabulary': data['vocabulary'], "The rules' readings": rules_readings(data['obligations']),
                'Conversation': data['history'], 'This turn': {'turn_id': turn}}
    errors, corrections = [], []
    attempts = 1 + ask_model_corrections()
    for attempt in range(attempts):
        prompt = VERSION + '\n' + encoded({**data, 'instructions': instructions, 'errors': errors})
        text, replay = '', None
        try:
            with runtrace.prompting(VERSION, digest(prompt), digest(data['vocabulary']),
                                   vocabulary=vocabulary_measurement(data['vocabulary'])):
                outcome = structured_call(model, 'plan', version=VERSION, instructions=instructions, data=sections,
                                          question=question, contract=FullReply, schema=data['schema'], validate=False,
                                          legacy_prompt=prompt, corrections=tuple(corrections), json_mode=True)
            text, replay = outcome.text, outcome.replay
            if outcome.truncated:
                if outcome.more_room and attempt + 1 < attempts:
                    errors = [{'code': 'truncated'}]
                    corrections.append(Correction(truncated=True))
                    continue
                no_fallback('interpreting the query plan', outcome.cause or outcome.problem)
                break
            if outcome.value is None:
                raise outcome.cause if isinstance(outcome.cause, NotJSON) else NotJSON('model reply held no JSON object')
            raw = outcome.value
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
            if attempt + 1 < attempts:
                if isinstance(exc, ValidationError):
                    found = exc.errors(include_input=False)[:20]
                    errors = [{'code': e['type']} for e in found]
                    failed = tuple(FieldError('/' + '/'.join(map(str, e['loc'])) if e['loc'] else '',
                                              f"{e['msg']} ({e['type']})") for e in found)
                else:
                    errors = [{'code': 'invalid_reply'}]
                    failed = (FieldError('', 'The reply is not one PlanReply JSON object within the reply bounds.'),)
                corrections.append(Correction(previous=text, replay=replay, errors=failed))
                continue
            no_fallback('interpreting the query plan', exc)
        except Exception as exc:
            no_fallback('interpreting the query plan', exc)
        break
    if on_failure is not None:
        on_failure()
    return Abstain(kind='abstain', spans=(), missing=('The configured model could not provide a valid query plan.',))


class FullReply(RootModel[PlanReply]):
    """query-plan-v1's reply: the whole PlanReply, which read() normalises and validates itself, never this model. It is
    asked for in JSON mode (structured_call's json_mode), its schema in the system instruction rather than the provider's
    structured output: the PlanReply's recursive schema, unrolled, is too large for Vertex AI to flatten (#561)."""


def rules_readings(obligations):
    """The rules' ledger as the model is shown it: each phrase the rules read as some declared meaning, its kind, its
    candidate ids and its operation."""
    return [{'phrase': o['text'], 'kind': o['kind'], 'candidates': list(o['candidates']), 'operation': o['operation']}
            for o in obligations if o['candidates']]


ON_VERSION = 'query-plan-on-v1'
UNKNOWN = {
    'closest': ('A word the vocabulary does not list: read it as the closest declared meaning and give that reading; '
                'abstain, naming the word in unread, only when no declared meaning fits.'),
    'abstain': 'A word the vocabulary does not list: abstain, naming the word in unread.',
}
"""The one sentence on a word the vocabulary does not list, by ASK_UNKNOWN_WORD."""
FOLLOW_UP = ('This question follows the conversation: set continues true to replace the earlier plan in full, keeping '
             'unchanged node ids; list in removed each node it deletes, with the words that delete it.')


def on_instructions(rule=None):
    """AI-Generated mode's system instruction, with the sentence on unknown words that rule (ASK_UNKNOWN_WORD when
    None) chooses."""
    rule = ask_unknown_word() if rule is None else rule
    return ('You read one business question into a plan over a declared vocabulary, and reply with the contract alone. '
            'decision is propose for a plan, clarify between declared alternatives, or abstain. Use only the '
            "vocabulary's ids; a member value is its declared code, never its display name. The rules' readings are the "
            'declared meanings the rules found for some words: keep them. readings gives each word or phrase you '
            'interpreted with the id you read it as, or filler for words that set nothing. ' + UNKNOWN[rule] + ' The '
            'examples are verified question and plan pairs on this release: follow their shapes. Use short node ids '
            '(m1, f1, d1). Leave out what you do not decide: evidence, obligations, and the route of a breakdown or '
            'filter where one route is declared. Never return SQL, expressions, figures or result cells.')


ON_INSTRUCTIONS = on_instructions('closest')
ON_PROMPT_VERSION = ON_VERSION + '/' + hashlib.sha256(ON_INSTRUCTIONS.encode()).hexdigest()[:8]
MAX_DIMENSION_MEMBERS = 40
UNASKED = frozenset('the and for how what was were which with from per each our who whose this that than into its are '
                    'did does give show tell much many'.split())


def on_prompt_version():
    """The on-mode prompt's version as the versions note names it: ON_VERSION and the instruction's hash, which
    ASK_UNKNOWN_WORD moves."""
    return ON_VERSION + '/' + hashlib.sha256(on_instructions().encode()).hexdigest()[:8]


class Reading:
    """One on-mode model reading: the reply (None where it could not be read), each phrase's reading, the problems
    that stopped the reply, the schema version it was asked under, and whether the model declined to plan at all. For a
    correction it also keeps whether the reply was cut off at its budget and whether thinking less leaves more room, the
    contract as returned and the provider's record of it, the field errors found in it, the ids the model was shown and
    which attempt it was, and whether it was answered in JSON mode."""

    def __init__(self, reply, readings=(), problems=(), schema='', unavailable=False, *, truncated=False,
                 more_room=False, contract='', replay=None, errors=(), ids=(), attempt=0, json_mode=False):
        self.reply, self.readings, self.problems, self.schema = reply, tuple(readings), tuple(problems), schema
        self.unavailable = unavailable
        self.truncated, self.more_room, self.contract, self.replay = truncated, more_room, contract, replay
        self.errors, self.ids, self.attempt = tuple(dict(e) for e in errors), tuple(ids), attempt
        self.json_mode = json_mode

    def dump(self):
        return {'reply': self.reply.model_dump(mode='json', by_alias=True, exclude_unset=True) if self.reply is not None else None,
                'readings': [dict(r) for r in self.readings], 'problems': list(self.problems), 'schema': self.schema,
                'unavailable': self.unavailable, 'truncated': self.truncated, 'more_room': self.more_room,
                'contract': self.contract, 'replay': self.replay, 'errors': [dict(e) for e in self.errors],
                'ids': list(self.ids), 'attempt': self.attempt, **({'json_mode': True} if self.json_mode else {})}

    @classmethod
    def load(cls, held):
        reply = reply_adapter.validate_python(held['reply']) if held['reply'] is not None else None
        return cls(reply, held['readings'], held['problems'], held['schema'], held['unavailable'],
                   truncated=held.get('truncated', False), more_room=held.get('more_room', False),
                   contract=held.get('contract', ''), replay=held.get('replay'), errors=held.get('errors', ()),
                   ids=held.get('ids', ()), attempt=held.get('attempt', 0), json_mode=held.get('json_mode', False))


def stems(text):
    return {word.rstrip('s') for word in re.findall(r'[a-z0-9]+', (text or '').casefold())
            if len(word) > 2 and word not in UNASKED}


def declared_members(vocabulary):
    """The pack's declared member phrases this vocabulary may read: each predicate row on a column it holds, with the
    codes it filters on."""
    return [dict(row['params'], codes=tuple(row['params']['values']) if row['params'].get('values') else (row['params']['value'],))
            for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
            if row['kind'] == 'predicate' and row['params'].get('column') in vocabulary.entries
            and ('value' in row['params'] or row['params'].get('values'))]


def named_members(members, question, generic):
    """The declared members the question names, case-insensitively and word by word: a phrase said whole or every
    word of it said in any form, or every word of it that is not a generic word, one that names a column or dimension
    such as region, so "Alpha" names "Alpha region" and "Beta" names "region BETA"."""
    asked = stems(question)

    def said(phrase):
        own = stems(phrase) - generic
        return (bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', question or '', re.I))
                or len(stems(phrase)) > 1 and stems(phrase) <= asked or bool(own) and own <= asked)
    return [row for row in members if any(said(p) for p in row.get('phrases', ()))]


def relevant(public, vocabulary, question, turn):
    """The vocabulary the question can need: what the rules' ledger or a shared word names, the units those sit in,
    and there every declaration but the columns and values no word of the question names; a member it keeps keeps the
    column it filters, so the filter can be written. It also keeps every declared member the question names, the
    members of each dimension it names, up to MAX_DIMENSION_MEMBERS a column, and the declared groups of members, each
    with the phrases that name it."""
    asked = stems(question)
    lexical = {i for piece in ledger(question, turn, vocabulary) for i in piece.candidates}
    phrased = {i for phrase, ids in vocabulary.phrases.items() if stems(phrase) & asked for i in ids}
    named = {n['id'] for n in public['names'] if n['id'] in lexical | phrased or stems(n['label']) & asked}
    units = {vocabulary.entries[i].unit for i in named if vocabulary.entries[i].unit} | set(vocabulary.default_units)

    def readable(entry):
        return not (units and entry.unit and entry.unit not in units)
    by_column = {}
    for entry in vocabulary.entries.values():
        if entry.kind == 'value' and readable(entry):
            by_column.setdefault(entry.column, []).append(entry.id)
    dimensions = {vocabulary.entries[i].column for i in named if vocabulary.entries[i].kind == 'column'}
    members = [row for row in declared_members(vocabulary) if readable(vocabulary.entries[row['column']])
               and all(value_id(row['column'], code) in vocabulary.entries for code in row['codes'])]
    generic = {stem for phrase, found in vocabulary.phrases.items()
               if any(i in vocabulary.entries and vocabulary.entries[i].kind == 'column' for i in found) for stem in stems(phrase)}
    of_dimension = {}
    for row in members:
        if row['column'] in dimensions:
            of_dimension.setdefault(row['column'], []).append(row)
    chosen = {id(row): row for row in (*named_members(members, question, generic),
                                       *(row for rows in of_dimension.values() for row in rows[:MAX_DIMENSION_MEMBERS]),
                                       *(row for row in members if row['op'] in ('in', 'not_in')))}
    shown = (named | {value_id(row['column'], code) for row in chosen.values() for code in row['codes']}
             | {i for column in dimensions for i in by_column.get(column, ())[:MAX_DIMENSION_MEMBERS]})
    filtered = {vocabulary.entries[i].column for i in shown if vocabulary.entries[i].kind == 'value' and readable(vocabulary.entries[i])}

    def kept(name):
        entry = vocabulary.entries[name['id']]
        if not readable(entry):
            return False
        return (name['id'] in shown or entry.kind not in ('value', 'column')
                or entry.kind == 'column' and entry.column in filtered)
    names = [n for n in public['names'] if kept(n)]
    ids = {n['id'] for n in names}
    phrased_members = [{'phrases': phrases, 'column': row['column'], 'op': row['op'], 'values': list(row['codes'])}
                       for row in chosen.values()
                       if (phrases := [p for p in row.get('phrases', ()) if not vocabulary.denials(p)])
                       and all(value_id(row['column'], code) in ids for code in row['codes'])]
    return {**public, 'names': names,
            'phrases': {p: [i for i in found if i in ids] for p, found in public['phrases'].items()
                        if stems(p) & asked and any(i in ids for i in found)},
            **({'members': sanitized(phrased_members, vocabulary)} if phrased_members else {}),
            'contexts': [c for c in public['contexts'] if c['id'] in vocabulary.contexts
                         and (not units or vocabulary.contexts[c['id']].unit in units)]}


def on_schema(vocabulary, ids):
    """The on-mode reply: a PlanReply whose every name is one of ids, and the readings, each phrase an id or filler."""
    plan = reply_schema(vocabulary=vocabulary)
    plan['$defs']['Name']['properties']['id'] = {'type': 'string', 'enum': list(ids)}
    reading = {'type': 'object', 'additionalProperties': False, 'required': ['phrase', 'id'],
               'properties': {'phrase': {'type': 'string', 'minLength': 1}, 'id': {'type': 'string', 'enum': [*ids, FILLER]}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['reply', 'readings'],
            'properties': {'reply': {'$ref': '#/$defs/PlanReply'},
                           'readings': {'type': 'array', 'maxItems': MAX_REPLY_ITEMS, 'items': reading}},
            '$defs': {**plan['$defs'], 'PlanReply': {'oneOf': plan['oneOf'], 'discriminator': plan['discriminator']}}}


def on_payload(question, vocabulary, history=(), turn='turn', examples=()):
    """The on-mode prompt's data: the question, the rules' readings of its phrases, the vocabulary it can need, the
    verified examples and the schema. Its recording key is the query-plan-v1 key of the same question and release."""
    full = payload(question, vocabulary, history, turn)
    selected = select_vocabulary(question, full['obligations'], full['vocabulary'])
    shown = relevant(selected, vocabulary, question, turn)
    ids = sorted({n['id'] for n in shown['names']})
    return {'version': ON_VERSION, 'recording_key': full['recording_key'], 'question': question, 'turn_id': turn,
            'history': full['history'], 'vocabulary': shown, 'obligations': full['obligations'],
            'examples': [dict(e) for e in examples], 'schema': on_schema(vocabulary, ids)}


def bound_names(raw, vocabulary):
    """A name or a member value the model gave as written words, bound to its declared id or code by the lookup the
    rules use (the declared phrases and labels); one that binds to no single declaration is left for the checks."""
    def code(column, value):
        if not isinstance(value, dict) or value.get('kind') != 'text' or not isinstance(value.get('value'), str):
            return value
        words = value['value'].casefold()
        members = [e for e in vocabulary.entries.values() if e.kind == 'value' and e.column == column]
        found = {e.value for e in members if e in vocabulary.candidates(words) or (e.label or '').casefold() == words}
        if value['value'] in {e.value for e in members} or len(found) != 1:
            return value
        return {**value, 'value': found.pop()}

    def walk(node):
        if isinstance(node, list):
            return [walk(child) for child in node]
        if not isinstance(node, dict):
            return node
        node = {key: walk(child) for key, child in node.items()}
        if 'evidence' in node and isinstance(node.get('id'), str) and node['id'] not in vocabulary.entries:
            found = {e.id for e in vocabulary.candidates(node['id'])}
            if len(found) == 1:
                node['id'] = found.pop()
        left = node.get('left')
        if node.get('kind') in ('compare', 'in') and isinstance(left, dict) and left.get('kind') == 'column':
            entry = vocabulary.entries.get((left.get('name') or {}).get('id'))
            if entry is not None and entry.kind == 'column':
                if 'value' in node:
                    node['value'] = code(entry.column, node['value'])
                if isinstance(node.get('values'), list):
                    node['values'] = [code(entry.column, v) for v in node['values']]
        return node
    return walk(raw)


def read_on(model, question, vocabulary, history=(), turn='turn', *, examples=(), corrections=()):
    """AI-Generated mode's model call: the pack's prompt (the verified examples, the vocabulary the question can need and
    the rules' readings of its words, the question last) sent as a structured call whose reply is the decisions-only
    PlanContract over the ids shown; the contract filled into a PlanReply (fill), names bound to their declared codes
    and routes filled where one is declared. corrections continue the conversation after a failed attempt (correction).
    A full PlanReply, or the {reply, readings} envelope, from a model that replays recordings, is read as before."""
    if vocabulary.denial(question):
        return Reading(Abstain(kind='abstain', spans=(), missing=('The requested read is not permitted.',)))
    if problem := validate(history, vocabulary, question, turn, words=False):
        return Reading(Abstain(kind='abstain', spans=(), missing=problem.missing))
    vocabulary = clarified_vocabulary(history, vocabulary, question)
    data = on_payload(question, vocabulary, history, turn, [e for e in examples if readable_example(e, vocabulary)])
    ids = tuple(sorted({n['id'] for n in data['vocabulary']['names']}))
    schema = contract_schema(PlanContract, ids)
    tag = digest(schema)[:12]
    instructions = on_instructions()
    if any(h.plan is not None for h in history):
        instructions += ' ' + FOLLOW_UP
    corrections = tuple(corrections)
    attempt = len(corrections)
    last = corrections[-1].errors if corrections else ()
    prompt = ON_VERSION + '\n' + encoded({**data, 'schema': schema, 'instructions': instructions,
                                          'errors': [{'problem': e.error} for e in last]})
    sections = {'Vocabulary': data['vocabulary'],
                'Examples': [{'question': e['question'], 'plan': lean(e['plan'])} for e in data['examples']],
                "The rules' readings": rules_readings(data['obligations']),
                'Conversation': [{'turn_id': h['turn_id'], 'question': h['question'], 'plan': lean(h['plan'])}
                                 for h in data['history']]}
    allowed = {*ids, FILLER}
    try:
        with runtrace.prompting(ON_VERSION, digest(prompt), digest(data['vocabulary']),
                               vocabulary=vocabulary_measurement(data['vocabulary'])):
            outcome = structured_call(model, 'plan', version=ON_VERSION, instructions=instructions, data=sections,
                                      question=question, contract=PlanContract, ids=ids, schema=schema,
                                      legacy_prompt=prompt, corrections=corrections,
                                      prepare=lambda raw: bind_ids(raw, vocabulary))
    except UnavailablePlan as exc:
        return Reading(Abstain(kind='abstain', spans=(), missing=(str(exc),)), schema=tag, unavailable=True)
    except Exception as exc:
        no_fallback('interpreting the query plan', exc)
        return Reading(Abstain(kind='abstain', spans=(), missing=('The configured model could not provide a valid query plan.',)),
                       schema=tag, unavailable=True)
    kept = {'schema': tag, 'ids': ids, 'attempt': attempt, 'json_mode': outcome.json_mode}
    if outcome.truncated:
        return Reading(None, problems=(outcome.problem,), truncated=True, more_room=outcome.more_room, **kept)
    kept.update(contract=outcome.text, replay=outcome.replay)
    if outcome.errors:
        errors = narrowed(outcome.errors, outcome.text, vocabulary, ids)
        return Reading(None, problems=tuple(error_line(e) for e in errors), errors=[e.dump() for e in errors], **kept)
    value, reply = outcome.value, None
    try:
        if isinstance(value, PlanContract):
            if not bounded(value.model_dump(mode='json')) or len(outcome.text) > MAX_REPLY_CHARS:
                raise ValueError('reply_bounds')
            readings = [{'phrase': r.phrase, 'id': r.id} for r in value.readings if r.id in allowed]
            reply = bound_names(fill(value, question, vocabulary, turn, history), vocabulary)
            if reply.get('kind') == 'propose':
                _routes(reply['plan'], vocabulary)
        else:
            if not bounded(value) or len(encoded(value)) > MAX_REPLY_CHARS:
                raise ValueError('reply_bounds')
            envelope = value if 'reply' in value else {'reply': value, 'readings': []}
            readings = [{'phrase': r['phrase'], 'id': r['id']} for r in envelope.get('readings') or ()
                        if isinstance(r, dict) and isinstance(r.get('phrase'), str) and r.get('id') in allowed]
            reply = bound_names(envelope['reply'], vocabulary)
        reply = _normalize_propose(reply, question, vocabulary, turn, breakdowns=True)
        return Reading(reply_adapter.validate_python(reply), readings, **kept)
    except ValidationError as exc:
        found = exc.errors(include_input=False)[:20]
        if not isinstance(value, PlanContract):
            return Reading(None, problems=tuple(f"{'/'.join(map(str, e['loc']))}: {e['type']}" for e in found), **kept)
        errors = []
        written = value.model_dump(mode='json')
        for e in found:
            where = reply_pointer(e['loc'], reply)
            errors.append(id_error(where, e['msg'], field_key(where), ids, vocabulary, at_pointer(written, where)))
        return Reading(None, problems=tuple(error_line(e) for e in errors), errors=[e.dump() for e in errors], **kept)
    except ValueError:
        return Reading(None, problems=('The reply is not one JSON object matching the schema.',), **kept)


EMPTIED = 'after validation, not 0'
"""Pydantic's note that a list whose every item failed is empty, given beside those items' own errors."""


def error_line(error):
    """A field error as one problem line: the field, then what is wrong there."""
    return f'{error.field}: {error.error}' if error.field else error.error


READING_ID = re.compile(r'/readings/\d+/id')


def refused_reading(where, error, data):
    """A reading whose id is not a declared one, told so that the next attempt can read it (#593): the correction names
    the phrase and the id refused and offers the two readings a phrase may have, a declared id or filler, filler first
    among the choices. Which it is stays the model's: code never reads a word as filler itself, and a phrase the model
    still leaves unresolved fails as unresolved. None for any other error."""
    if not READING_ID.fullmatch(where) or 'not a declared id' not in error.error:
        return None
    reading = at_pointer(data, where.rsplit('/', 1)[0])
    phrase = reading.get('phrase') if isinstance(reading, dict) else None
    if not isinstance(phrase, str) or not phrase.strip():
        return None
    allowed = (FILLER, *(a for a in error.allowed if a != FILLER))
    return FieldError(where, f"the phrase {phrase.strip()!r} was read as {str(reading.get('id'))!r}, which is not a "
                      f"declared id. Read {phrase.strip()!r} as one of the declared ids, or as {FILLER} if it sets "
                      'nothing in the plan.', allowed, error.of)


def unread_phrases(reading, unread):
    """The correction for a reply that left phrases of this turn unread (#656): each phrase named, with the two
    readings a phrase may have offered, filler first, then the declared ids spelled nearest the phrase. Which it is
    stays the model's: code never reads a word as filler itself, and a phrase the model still leaves unresolved fails
    as unresolved."""
    ids = tuple(i for i in reading.ids if i != FILLER)
    errors = []
    for phrase in unread:
        nearest_ids = nearest(ids, phrase, MAX_ALLOWED - 1)
        errors.append(FieldError('/readings', f'the phrase {phrase!r} has no reading. Add a reading of {phrase!r} as one '
                                 f'of the declared ids, or as {FILLER} if it sets nothing in the plan.',
                                 (FILLER, *nearest_ids), len(ids) + 1 if len(nearest_ids) < len(ids) else 0))
    return Correction(previous=reading.contract, replay=reading.replay, errors=tuple(errors), json_mode=reading.json_mode)


def narrowed(errors, text, vocabulary, ids):
    """The contract's validation errors, each at its field in the reply as written (pydantic's union tags left out),
    and where the field names vocabulary ids, with the ids shown of the kinds that field may name as its choices."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    out = []
    for error in errors:
        where = error.field
        if data is not None and where:
            where = contract_pointer([int(p) if p.isdigit() else p for p in where.split('/')[1:]], data)
        key = field_key(where)
        out.append(id_error(where, error.error, key, ids, vocabulary, at_pointer(data, where)) if key in FIELD_KINDS
                   else refused_reading(where, error, data) or FieldError(where, error.error, error.allowed, error.of))
    return tuple(e for e in out if not (EMPTIED in e.error and any(o.field.startswith(e.field + '/') for o in out)))


def correction(reading, problems, vocabulary):
    """The correction that answers a failed reading: for one cut off at its budget, a note to think less; otherwise the
    contract it returned with its field errors and each problem the checks found in it, at the field it names where it
    names an id or a node."""
    if reading.truncated:
        return Correction(truncated=True, json_mode=reading.json_mode)
    errors = [FieldError.load(e) for e in reading.errors]
    lines = {error_line(e) for e in errors}
    errors += [locate(p, reading.contract, vocabulary, reading.ids) for p in problems if p not in lines]
    return Correction(previous=reading.contract, replay=reading.replay, errors=tuple(errors), json_mode=reading.json_mode)


def _routes(plan, vocabulary, attempt=0):
    """Each breakdown binding and column filter the contract left without a route, given the one route declared from
    its context to the column's table, if there is exactly one; one with none or several is left for the checks."""
    if not isinstance(plan, dict):
        return

    def column_of(name):
        entry = vocabulary.entries.get(name.get('id')) if isinstance(name, dict) else None
        return entry.column if entry is not None and entry.kind == 'column' else None

    def predicate(pred, ctx, path):
        if not isinstance(pred, dict):
            return
        left = pred.get('left')
        if ctx is not None and isinstance(left, dict) and left.get('kind') == 'column' and 'path' not in left:
            if (column := column_of(left.get('name'))) is not None:
                _fill_path(left, 'path', ctx, column, vocabulary, path + '/left', attempt, 'contract-path')
        for i, child in enumerate(_items(pred.get('children'))):
            predicate(child, ctx, path + '/children/' + str(i))
        if isinstance(pred.get('child'), dict):
            predicate(pred['child'], ctx, path + '/child')
        inner = vocabulary.contexts.get((pred.get('context') or {}).get('id')) if pred.get('kind') == 'membership' else ctx
        for i, child in enumerate(_items(pred.get('filters'))):
            predicate(child, inner, path + '/filters/' + str(i))

    for i, dimension in enumerate(_items(plan.get('dimensions'))):
        for j, binding in enumerate(_items(dimension.get('bindings') if isinstance(dimension, dict) else ())):
            if not isinstance(binding, dict) or 'path' in binding:
                continue
            ctx = vocabulary.contexts.get((binding.get('context') or {}).get('id'))
            if ctx is not None and (column := column_of(binding.get('column'))) is not None:
                _fill_path(binding, 'path', ctx, column, vocabulary, '/plan/dimensions/' + str(i) + '/bindings/' + str(j),
                           attempt, 'contract-path', binding.get('level'))
    measures = [m for m in _items(plan.get('measures')) if isinstance(m, dict)]
    contexts = {(m.get('context') or {}).get('id') for m in measures}
    shared = vocabulary.contexts.get(next(iter(contexts))) if len(contexts) == 1 else None
    for i, pred in enumerate(_items(plan.get('filters'))):
        predicate(pred, shared, '/plan/filters/' + str(i))
    for i, measure in enumerate(measures):
        ctx = vocabulary.contexts.get((measure.get('context') or {}).get('id'))
        for j, pred in enumerate(_items(measure.get('filters'))):
            predicate(pred, ctx, '/plan/measures/' + str(i) + '/filters/' + str(j))


EXAMPLES = 'baselines/examples.json'
_examples = {}


def verified_examples(engine):
    """The client pack's verified examples, each a golden question and the plan the rules certify for it
    (clients/<client>/baselines/examples.json, written by make examples), read through the pack store once per release
    the engine serves; none where the pack has none yet, and a miss is not kept, so the next question reads the store
    again once the release job has written the file."""
    profile = getattr(engine, 'profile', None)
    if profile is None:
        return ()
    key = (profile.client, encoded(engine.identity))
    if key not in _examples:
        try:
            held = tuple(json.loads(packstore.for_profile(profile).read(EXAMPLES)))
        except (FileNotFoundError, ValueError):
            return ()
        _examples.clear()
        _examples[key] = held
    return _examples[key]


def readable_example(example, vocabulary):
    """Whether every name in an example's plan is one the asker's vocabulary holds, so the prompt carries no other."""
    def names(value):
        if isinstance(value, dict):
            if isinstance(value.get('id'), str) and 'evidence' in value:
                yield value['id']
            for child in value.values():
                yield from names(child)
        elif isinstance(value, list):
            for child in value:
                yield from names(child)
    return all(i in vocabulary.entries for i in names(example.get('plan')))
