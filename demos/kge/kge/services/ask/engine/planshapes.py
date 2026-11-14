"""Compose declared outputs from bounded lexical shapes without reading data."""
from __future__ import annotations

import copy
import re

from kge.semantics.coverage import grouping_grains
from kge.services.ask.engine.question import read_period


def shared_contexts(pieces, vocabulary):
    """A bare current side uses the unqualified noun on the other side."""
    contexts = []
    for piece in pieces:
        if piece.kind == 'name':
            contexts.extend(i for i in piece.candidates if i in vocabulary.contexts)
        elif piece.operation == 'current' and any(p.operation == 'comparison' for p in pieces):
            heads = [p for p in pieces if p.kind == 'name' and any(i in vocabulary.contexts for i in p.candidates)]
            for head in heads:
                for split in re.finditer(r'\s+', head.text):
                    noun = head.text[split.end():]
                    matches = [e.id for e in vocabulary.candidates(noun) if e.id in vocabulary.contexts
                               and vocabulary.contexts[e.id].term.casefold() == vocabulary.entries[vocabulary.contexts[e.id].measure].label.casefold()]
                    if matches:
                        contexts.extend(matches)
                        break
    return tuple(dict.fromkeys(contexts))


def periods(pieces):
    return [p.value or read_period(p.text.casefold())[0] for p in pieces if p.kind == 'time']


def selection(period, calendar):
    return {'kind': 'single', 'period': {'kind': period.plan_kind,
        'value': period.value[:7] if period.plan_kind == 'month' else period.value,
        'calendar': {'id': calendar, 'evidence': []}}}


def compose(data, req, pieces, vocabulary):
    """Return a reason when a shape leaves an operand or ranking ambiguous."""
    if any(p.kind == 'unknown' for p in pieces):
        return None
    calendar = (data['time'].get('calendar') or data['time'].get('period', {}).get('calendar'))['id']
    times = periods(pieces)
    temporal = [p for p in pieces if p.kind == 'time']
    ops = {p.operation for p in pieces}
    context_comparison = len(shared_contexts(pieces, vocabulary)) == 2 and any(
        p.operation == 'comparison' and (not temporal or p.span.end <= temporal[0].span.start) for p in pieces)
    ranged = len(temporal) == 2 and bool(temporal[0].window and temporal[0].window == temporal[1].window)
    if context_comparison and (ops & {'previous_period', 'previous_year'} or len(temporal) > 1 and not ranged):
        return 'Please identify whether to compare the contexts or their periods.'
    for grain in sorted(grouping_grains(pieces, req.question)):
        data['dimensions'].append({'kind': 'time', 'id': 't' + str(len(data['dimensions'])),
            'calendar': {'id': calendar, 'evidence': []}, 'grain': grain})
    if len(times) == 2 and all(times):
        first, last = times
        between = req.question[temporal[0].span.end:temporal[1].span.start].strip().casefold().rstrip('.')
        prefix = req.question[:temporal[0].span.start]
        change = bool(ops & {'difference', 'percent_change'})
        if ranged or between in ('to', 'through') and not change and 'comparison' not in ops:
            if first.plan_kind != last.plan_kind or first.value > last.value:
                return 'Please give an ordered range at one period grain.'
            data['time'] = {'kind': 'range', 'first': selection(first, calendar)['period'], 'last': selection(last, calendar)['period']}
        else:
            if between in ('to', 'through') and re.search(r'\bfrom\s*$', prefix, re.I):
                first, last = last, first
            data['time'] = selection(first, calendar)
            data['comparisons'].append({'kind': 'explicit_period', 'id': 'c', 'inputs': [m['id'] for m in data['measures']],
                'other': selection(last, calendar), 'evidence': []})
    if not data['comparisons'] and ops & {'previous_period', 'previous_year'}:
        if len(ops & {'previous_period', 'previous_year'}) != 1:
            return 'Please identify one comparison period.'
        data['comparisons'].append({'kind': next(iter(ops & {'previous_period', 'previous_year'})), 'id': 'c',
            'inputs': [m['id'] for m in data['measures']], 'evidence': []})
    elif not data['comparisons'] and 'comparison' in ops:
        ordered = shared_contexts(pieces, vocabulary)
        measures = data['measures']
        if len(measures) != 2 or len(ordered) != 2 or set(ordered) != {m['context']['id'] for m in measures}:
            return 'Please identify both comparison contexts.'
        measures.sort(key=lambda m: ordered.index(m['context']['id']))
        data['comparisons'].append({'kind': 'contexts', 'id': 'c', 'left': measures[0]['id'], 'right': measures[1]['id'], 'evidence': []})
    for comparison in data['comparisons']:
        pairs = ([(comparison['id'] + '/base', comparison['id'] + '/other')] if comparison['kind'] == 'contexts' else
                 [(comparison['id'] + '/' + m + '/base', comparison['id'] + '/' + m + '/other') for m in comparison['inputs']])
        kinds = ('difference', 'percent_change') if comparison['kind'] != 'contexts' else tuple(k for k in ('difference', 'percent_change') if k in ops)
        for a, b in pairs:
            for kind in kinds:
                data['derived'].append({'kind': kind, 'id': 'x' + str(len(data['derived'])), 'numerator': a, 'denominator': b, 'evidence': []})
    if 'ratio' in ops:
        ordered = [i for p in pieces if p.kind == 'name' for i in p.candidates if i in vocabulary.contexts]
        if len(ordered) != 2 or len(data['measures']) != 2:
            return 'Please identify the numerator and denominator.'
        outputs = []
        for context in ordered:
            matches = [m['id'] for m in data['measures'] if m['context']['id'] == context]
            if len(matches) != 1:
                return 'Please identify the numerator and denominator.'
            outputs.append(matches[0])
        data['derived'].append({'kind': 'ratio', 'id': 'ratio', 'numerator': outputs[0], 'denominator': outputs[1], 'evidence': []})
    directions = ops & {'asc', 'desc'}
    if directions:
        if len(directions) != 1 or len(data['measures']) != 1 or len(data['dimensions']) != 1:
            return 'Please identify the measure and breakdown to rank.'
        ranking = re.search(r'\b(?:top|bottom)\s+(\d+)\b', req.question, re.I)
        if ranking:
            data['limit'] = int(ranking[1])
        elif re.search(r'\bwhich\b.*\b(?:most|least|highest|lowest)\b', req.question, re.I):
            data['limit'] = 1
        else:
            return 'Please give the number of ranked results.'
        if not data['limit']:
            return 'Please give a positive number of ranked results.'
        data['order'] = [{'output': data['measures'][0]['id'], 'direction': next(iter(directions)), 'nulls': 'last', 'evidence': []}]
    return None


def parts(data, pieces, vocabulary, text):
    """Coordinated declared predicates form separate outputs on their own contexts."""
    from kge.semantics.coverage import separate_filter_parts, typed_scalar
    def part(piece):
        return (piece.kind == 'predicate' and re.fullmatch(r'(?:inflows?|outflows?)', piece.text, re.I)
                and separate_filter_parts(piece, pieces, text))
    if not any(part(p) for p in pieces):
        return
    if data['comparisons'] or data['derived']:
        return 'Please identify the measure parts used by the comparison or ratio.'
    outputs = []
    for measure in data['measures']:
        context = vocabulary.contexts[measure['context']['id']]
        predicates = [p for p in pieces if part(p)
                      and any(i.rsplit('.', 1)[0] == context.fact for i in p.candidates)]
        if not predicates:
            outputs.append(measure)
            continue
        net = next((p for p in pieces if p.text.casefold() == 'net'), None)
        ordered = sorted([*predicates, *([net] if net else [])], key=lambda p: p.span.start)
        for piece in ordered:
            if piece is net:
                outputs.append(measure)
                continue
            columns = [i for i in piece.candidates if i.rsplit('.', 1)[0] == context.fact]
            if len(columns) != 1:
                return 'Please identify the declared predicate for this measure part.'
            column, = columns
            scalar = typed_scalar(piece.value, column, vocabulary)
            if scalar is None:
                return 'A measure part requires a value of its declared column type.'
            output = copy.deepcopy(measure)
            output['id'] = 'part' + str(len(outputs))
            for predicate in output['filters']:
                predicate['id'] += '-' + output['id']
            output['filters'].append({'kind': 'compare', 'id': 'pf' + str(len(outputs)),
                'left': {'kind': 'column', 'name': {'id': column, 'evidence': []}, 'path': []},
                'op': piece.operation, 'value': scalar, 'evidence': []})
            outputs.append(output)
    data['measures'] = outputs
    return None
