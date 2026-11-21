"""Compose declared outputs from bounded lexical shapes without reading data. Slot binding, shared_contexts among it,
is the shared binding module's (binding.py); the lexer markers the pieces carry are never read here."""
from __future__ import annotations

import copy
import re

from kge.semantics.coverage import (RANK_VERBS, SHARE_COMPLEMENTS, SINGULAR_RANK_WORDS,
                                    grouping_calendar, grouping_grains, shape_inputs, ranked_outputs, ranking_scope)
from kge.services.ask.engine.binding import shared_contexts
from kge.services.ask.engine.question import read_period
from kge.semantics.planvocabulary import formula_metrics, metric_identifier


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
    if 'separate' in ops:
        data['layout'] = 'separate'
    mode_pieces = [p for p in pieces if p.operation in ('current', 'as_reported')]
    modes = list(dict.fromkeys(p.operation for p in mode_pieces))
    mode_comparison = 'comparison' in ops and len(modes) == 2 and not any(
        p.kind == 'name' and set(p.candidates) & vocabulary.contexts.keys()
        and mode_pieces[0].span.end <= p.span.start < mode_pieces[-1].span.start for p in pieces)
    context_comparison = not mode_comparison and len(shared_contexts(pieces, vocabulary)) == 2 and any(
        p.operation == 'comparison' and (not temporal or p.span.end <= temporal[0].span.start) for p in pieces)
    ranged = len(temporal) == 2 and bool(temporal[0].window and temporal[0].window == temporal[1].window)
    if context_comparison and (ops & {'previous_period', 'previous_year'} or len(temporal) > 1 and not ranged):
        return 'Please identify whether to compare the contexts or their periods.'
    for grain in sorted(grouping_grains(pieces, req.question)):
        data['dimensions'].append({'kind': 'time', 'id': 't' + str(len(data['dimensions'])),
            'calendar': {'id': grouping_calendar(grain, pieces, vocabulary), 'evidence': []}, 'grain': grain})
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
    elif not data['comparisons'] and mode_comparison:
        for measure in data['measures']:
            measure['mode'] = modes[0]
        data['comparisons'].append({'kind': 'time_mode', 'id': 'c', 'inputs': [m['id'] for m in data['measures']],
            'base': modes[0], 'other': modes[1], 'evidence': []})
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
        kinds = (('difference', *(['percent_change'] if 'percent_change' in ops else [])) if comparison['kind'] == 'time_mode' else
                 ('difference', 'percent_change') if comparison['kind'] != 'contexts' else tuple(k for k in ('difference', 'percent_change') if k in ops))
        for a, b in pairs:
            for kind in kinds:
                data['derived'].append({'kind': kind, 'id': 'x' + str(len(data['derived'])), 'numerator': a, 'denominator': b, 'evidence': []})
    named_formulas = {vocabulary.derived_measures[i]['formula']['kind'] for piece in pieces
                      if piece.kind == 'name' for i in piece.candidates if i in vocabulary.derived_measures}
    if 'ratio' in ops and 'ratio' not in named_formulas:
        ordered = [i for p in pieces if p.kind == 'name' for i in p.candidates if i in vocabulary.contexts]
        if len(ordered) != 2:
            return 'Please identify the numerator and denominator.'
        outputs = []
        for context in ordered:
            matches = [m['id'] for m in data['measures'] if m['kind'] == 'measure' and m['context']['id'] == context
                       and m.get('aggregation', vocabulary.contexts[context].aggregation) == vocabulary.contexts[context].aggregation]
            if len(matches) != 1:
                return 'Please identify the numerator and denominator.'
            outputs.append(matches[0])
        measures = {m['id']: m for m in data['measures']}
        declared = formula_metrics(vocabulary, 'ratio', *(measures[i].get('measure', {}).get('id') for i in outputs),
                                   contexts=dict(zip(('numerator', 'denominator'), (measures[i]['context']['id'] for i in outputs))))
        if len(declared) != 1:
            return 'No unambiguous ratio metric is declared for these inputs.'
        data['derived'].append({'kind': 'ratio', 'id': 'ratio', 'numerator': outputs[0], 'denominator': outputs[1], 'evidence': []})
    for kind in ('share', 'running_total', 'moving_average'):
        if kind not in ops or kind in named_formulas:
            continue
        if kind == 'share' and any(p.operation == 'share' and p.text.casefold() in SHARE_COMPLEMENTS and p.value != 'split' for p in pieces):
            return 'Please identify the numerator and denominator for the remaining share.'
        inputs = shape_inputs(kind, pieces, req.question, vocabulary,
                              [(m['id'], m['context']['id'], m['kind']) for m in data['measures']])
        if kind == 'share' and not data['dimensions']:
            return 'Please identify the breakdown for the share of total.'
        temporal_dimensions = [d for d in data['dimensions'] if d['kind'] == 'time']
        if kind in ('running_total', 'moving_average') and len(temporal_dimensions) != 1:
            return 'Please identify one time breakdown for the ' + kind.replace('_', ' ') + '.'
        lengths = {p.value for p in pieces if p.operation == 'moving_average' and isinstance(p.value, tuple)}
        if kind == 'moving_average' and len(lengths) != 1:
            return 'Please declare one moving average length and time grain.'
        for source in inputs:
            derived = {'kind': kind, 'id': kind + '-' + source, 'input': source, 'evidence': []}
            if kind == 'share':
                measure = next(m for m in data['measures'] if m['id'] == source)
                from kge.semantics.plan import QueryPlan
                metric = metric_identifier(next(m for m in QueryPlan.model_validate(data).measures if m.id == source), vocabulary)
                declarations = formula_metrics(vocabulary, 'share', metric, metric,
                                              contexts=dict.fromkeys(('numerator', 'denominator'), measure['context']['id']))
                if len(declarations) != 1:
                    return 'No unambiguous share metric is declared for this input.'
                derived.update(remove_dimensions=[d['id'] for d in data['dimensions']], remove_filters=[])
            else:
                ordered = temporal_dimensions[0]['id']
                derived.update(order_by=ordered, partition_by=[d['id'] for d in data['dimensions'] if d['id'] != ordered])
                if kind == 'moving_average':
                    length, grain = next(iter(lengths))
                    if length < 1 or grain != temporal_dimensions[0]['grain']:
                        return 'The moving average length must be positive and use the requested time grain.'
                    derived['length'] = length
            data['derived'].append(derived)
    directions = ops & {'asc', 'desc'}
    if directions:
        partition_columns, partition_grains = ranking_scope(pieces)
        partitions = [d['id'] for d in data['dimensions'] if (d['kind'] == 'time' and d['grain'] in partition_grains)
                      or (d['kind'] != 'time' and {b['column']['id'] for b in d['bindings']} <= partition_columns)]
        candidates = [(m['id'], m['context']['id'], m['kind']) for m in data['measures']]
        ranked = sorted(ranked_outputs(directions, pieces, req.question, vocabulary, candidates))
        if len(ranked) != 1 or len(data['dimensions']) - len(partitions) != 1:
            return 'Please identify the measure and breakdown to rank.'
        ordered = sorted(pieces, key=lambda p: p.span.start)
        ranked_columns = {binding['column']['id'] for d in data['dimensions'] if d['kind'] in ('column', 'hierarchy') for binding in d['bindings']}
        rankings = {int(piece.value if piece.value is not None else piece.text)
                    for i, piece in enumerate(ordered) if piece.kind == 'literal'
                    and str(piece.value if piece.value is not None else piece.text).isdecimal()
                    and any((neighbor.operation in ('asc', 'desc') or neighbor.operation == 'dimension' and ranked_columns.intersection(neighbor.candidates))
                            and not req.question[min(piece.span.end, neighbor.span.end):max(piece.span.start, neighbor.span.start)].strip()
                            for neighbor in ordered[max(0, i - 1):i] + ordered[i + 1:i + 2])}
        if len(rankings) > 1:
            return 'Please give the same number of top and bottom results.'
        if rankings:
            data['limit'] = rankings.pop()
        elif any(p.kind == 'operator' and p.operation in directions and p.text.casefold() in SINGULAR_RANK_WORDS for p in pieces):
            data['limit'] = 1 if len(directions) == 2 or partitions else None
        elif any(p.kind == 'operator' and p.text.casefold() in RANK_VERBS | {'ascending', 'descending'} for p in pieces):
            data['limit'] = None
        else:
            return 'Please give the number of ranked results.'
        if data['limit'] == 0:
            return 'Please give a positive number of ranked results.'
        data['order'] = [{'output': ranked[0], 'direction': 'both' if len(directions) == 2 else next(iter(directions)), 'nulls': 'last', 'evidence': []}]
        if partitions:
            data['order'][0]['partition_by'] = partitions
    return None


def parts(data, pieces, vocabulary, text):
    """Coordinated declared predicates form separate outputs on their own contexts."""
    from kge.semantics.coverage import separate_filter_parts, typed_scalar, declared_part, declared_whole
    def part(piece):
        return piece.kind == 'predicate' and separate_filter_parts(piece, pieces, text)
    if not any(part(p) for p in pieces):
        return
    if data['comparisons'] or data['derived']:
        from kge.semantics.plan import QueryPlan
        plan = QueryPlan.model_validate(data)
        if all(any(declared_part(piece, measure, vocabulary) for measure in plan.measures) for piece in pieces if part(piece)):
            return
        return 'Please identify the measure parts used by the comparison or ratio.'
    outputs = []
    for measure in data['measures']:
        context = vocabulary.contexts[measure['context']['id']]
        predicates = [p for p in pieces if part(p)
                      and any(i.rsplit('.', 1)[0] == context.fact for i in p.candidates)]
        if not predicates or measure['kind'] != 'measure':
            outputs.append(measure)
            continue
        wholes = {declared_whole(i, vocabulary) for p in predicates for i in p.candidates if i.rsplit('.', 1)[0] == context.fact}
        whole = wholes.pop() if len(wholes) == 1 else None
        net = next((p for p in pieces if whole and p.text.casefold() == whole.casefold()), None)
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
        if whole and net is None:
            outputs.append(measure)
    data['measures'] = outputs
    return None
