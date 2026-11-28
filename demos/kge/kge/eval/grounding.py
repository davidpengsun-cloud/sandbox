from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from threading import Condition, RLock

from kge.adapters.model.errors import NoFallback
from kge.ports import REFERENCE
from kge.eval.asking import each
from kge.eval.harness import missing_recording
from kge.eval.phrasemirror import Mirror
from kge.eval.run_golden import APART, RATIO, answered_on, asked_as, asking_runtimes, close, same, score, stated
from kge.eval.sets import approved, reference_sql
from kge.eval.sets import items as set_items
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.ports import QueryRefused
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import Engine


class Replay:
    name = 'grounding-replay'

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def json(self, prompt, max_tokens=800):
        self.calls += 1
        if prompt.startswith(('query-plan-v1\n', 'query-plan-on-v1\n')):
            payload = json.loads(prompt.split('\n', 1)[1])
            spans = payload.get('obligations') or []
            turn = payload.get('turn_id') or (spans[0]['span']['turn'] if spans else None)
            def current(value):
                if isinstance(value, dict):
                    return {k: turn if k == 'turn' and v == 'turn' and turn else current(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [current(v) for v in value]
                return value
            return current(self.reply)
        return {'terms': [], 'period': None}


def parse_reply(reply):
    return json.loads(reply) if isinstance(reply, str) else reply


def load(path, recordings=None):
    raw = json.loads(Path(path).read_text())
    if isinstance(raw, list) and all('question' in entry and ('raw_model_reply' in entry or 'raw model reply' in entry) for entry in raw):
        questions = [{**entry, 'id': entry.get('id', f'capture-{index + 1:03}'), 'kind': entry.get('kind', 'figure'),
                      'reply': parse_reply(entry.get('raw_model_reply', entry.get('raw model reply')))}
                     for index, entry in enumerate(raw)]
    else:
        questions = set_items(Path(path))
    replies = {}
    if recordings:
        for entry in json.loads(Path(recordings).read_text()):
            if entry.get('arm') == 'treatment':
                parts = entry['case'].split(':')
                if len(parts) == 3:
                    replies[(parts[0], parts[1], entry['question'])] = entry['reply']
    name = Path(path).stem
    result = []
    for item in questions:
        item = dict(item)
        key = (name, item['id'], item['question'])
        if 'reply' not in item and key in replies:
            item['reply'] = replies[key]
        result.append(item)
    return result


def result_rows(result, *, total=False, string_dimensions=False):
    rows = []
    for row in result.get('rows', []):
        if bool(row.get('total')) != total:
            continue
        dimensions = tuple(d.get('value') for d in row.get('dimensions', []))
        if string_dimensions:
            dimensions = tuple(str(value) if value is not None else None for value in dimensions)
        cells = tuple(c.get('value', {}).get('value') for c in row.get('cells', []))
        rows.append(dimensions + cells)
    return rows


def same_value(a, b, tolerance, quantum=None):
    if a == b:
        return True
    if not close(a, b, tolerance):
        return False
    if quantum is None:
        return True
    try:
        return Decimal(str(a)).quantize(quantum, rounding=ROUND_HALF_UP) == Decimal(str(b)).quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return False


def same_rows(got, want, tolerances=(), dimensions=0, quantums=()):
    remaining = list(want)
    for row in got:
        match = next((i for i, expected in enumerate(remaining) if len(row) == len(expected) and
                      all(a == b if j < dimensions else same_value(
                          a, b, tolerances[j] if j < len(tolerances) else 0.01,
                          quantums[j] if j < len(quantums) else None)
                          for j, (a, b) in enumerate(zip(row, expected)))), None)
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


def share_total_expected(node, plan, columns, dimensions, truth, scope, matching, tolerance, quantum, context_metrics):
    """Rebuild a retained-population share from independent SQL input cells."""
    positions = {column.get('id'): index for index, column in enumerate(columns)}
    source = positions.get(node.get('input'))
    owner = next((measure for measure in plan.get('measures', []) if measure.get('id') == node.get('input')), None)
    aggregation = owner.get('aggregation') if owner else None
    if owner and aggregation is None:
        declared = context_metrics.get(owner.get('context', {}).get('id'), {})
        if declared.get('measure') == owner.get('measure', {}).get('id'):
            aggregation = declared.get('aggregation')
    identifiers = [dimension.get('id') for dimension in dimensions]
    removed = node.get('remove_dimensions')
    def output_filter(value):
        if isinstance(value, dict):
            return value.get('kind') == 'output' or any(output_filter(child) for child in value.values())
        return isinstance(value, list) and any(output_filter(child) for child in value)
    if (source is None or columns[source].get('kind') != 'measure' or not owner
            or owner.get('kind') != 'measure' or aggregation != 'sum'
            or not isinstance(removed, (list, tuple)) or not set(removed) <= set(identifiers)
            or len(set(identifiers)) != len(identifiers)
            or set(identifiers) != {dimension.get('id') for dimension in plan.get('dimensions', [])}
            or node.get('remove_filters') or plan.get('limit') is not None
            or plan.get('layout', 'aligned') != 'aligned' or output_filter(plan.get('filters', []))):
        raise ValueError('Share denominator cannot be reconstructed from the SQL detail population.')
    retained = [index for index, identifier in enumerate(identifiers) if identifier not in removed]
    offset = len(dimensions)
    def denominator(keys, *, total=False):
        rows = [row for row in truth if all((total and keys[index] is None) or keys[index] == row[index] for index in retained)]
        value = sum((Decimal(str(row[offset + source])) for row in rows), Decimal(0))
        if not rows or not value.is_finite() or value == 0:
            raise ValueError('Share denominator has no independently checked nonzero value.')
        return value
    share = positions[node['id']]
    for row in matching:
        expected = Decimal(str(row[offset + source])) / denominator(row[:offset])
        if not same_value(row[offset + share], expected, tolerance, quantum):
            raise ValueError('SQL share cells do not establish the declared denominator scope.')
    numerator = sum((Decimal(str(row[offset + source])) for row in matching), Decimal(0))
    return numerator / denominator(scope, total=True)


def sql_result_matches(result, truth, *, string_dimensions=False, plan=None, context_metrics=None):
    detail = result_rows(result, string_dimensions=string_dimensions)
    totals = result_rows(result, total=True, string_dimensions=string_dimensions)
    dimensions = len(result.get('rows', [{}])[0].get('dimensions', [])) if result.get('rows') else 0
    columns = [column for column in result.get('columns', []) if column.get('kind') != 'dimension']
    derived = plan.get('derived', []) if isinstance(plan, dict) else []
    arithmetic = {node['id']: node for node in derived if isinstance(node, dict)
                  and node.get('kind') in ('difference', 'ratio', 'percent_change') and node.get('id')}
    shares = {node['id']: node for node in derived if isinstance(node, dict)
              and node.get('kind') == 'share' and node.get('id')}
    positions = {column.get('id'): index for index, column in enumerate(columns)}
    comparisons = plan.get('comparisons', []) if isinstance(plan, dict) else []
    sides = {f"{c['id']}/{side}": c[measure] for c in comparisons
             if isinstance(c, dict) and c.get('kind') == 'contexts' and c.get('id')
             for side, measure in (('base', 'left'), ('other', 'right')) if c.get(measure) in positions}
    tolerances = [0.01] * dimensions + [RATIO / 2 if column.get('format') == 'percent' else
                  RATIO if column.get('kind') == 'derived' and not column.get('currency') else 0.005
                  for column in columns]
    quantums = [None] * dimensions + [Decimal('0.0001') if column.get('format') == 'percent' else Decimal('0.01')
                                    for column in columns]
    if not same_rows(detail, truth, tolerances, dimensions, quantums):
        return False
    for total in totals:
        scope = total[:dimensions]
        matching = [row for row in truth if len(row) == len(total) and
                    all(key is None or key == value for key, value in zip(scope, row[:dimensions]))]
        if not matching or len(columns) != len(total) - dimensions:
            return False
        for index, (value, column) in enumerate(zip(total[dimensions:], columns)):
            if value is None:
                if column.get('id') in shares:
                    return False
                continue
            if column.get('kind') not in ('measure', 'count', 'comparison') and not (
                    column.get('kind') == 'derived' and column.get('id') in arithmetic.keys() | shares.keys()):
                return False
            try:
                values = [Decimal(str(row[dimensions + index])) for row in matching]
                expected = sum(values, Decimal(0))
                if column.get('kind') == 'derived' and column.get('id') in shares:
                    expected = share_total_expected(shares[column['id']], plan, columns,
                        [column for column in result.get('columns', []) if column.get('kind') == 'dimension'],
                        truth, scope, matching, tolerances[dimensions + index], quantums[dimensions + index], context_metrics or {})
                elif column.get('kind') == 'derived':
                    node = arithmetic[column['id']]
                    first, second = (sides.get(i, i) for i in (node.get('numerator'), node.get('denominator')))
                    if first not in positions or second not in positions:
                        return False
                    if any(columns[positions[i]].get('kind') not in ('measure', 'count', 'comparison') for i in (first, second)):
                        return False
                    numerator = sum((Decimal(str(row[dimensions + positions[first]])) for row in matching), Decimal(0))
                    denominator = sum((Decimal(str(row[dimensions + positions[second]])) for row in matching), Decimal(0))
                    if node['kind'] == 'difference':
                        if expected != numerator - denominator:
                            return False
                    else:
                        if denominator == 0:
                            return False
                        expected = (numerator - denominator if node['kind'] == 'percent_change' else numerator) / abs(denominator)
            except (InvalidOperation, ValueError, TypeError):
                return False
            if not same_value(value, expected, tolerances[dimensions + index], quantums[dimensions + index]):
                return False
    return True


def golden_result_matches(result, exp, answer):
    if not isinstance(result, dict) or not isinstance(result.get('rows'), list):
        return False
    if 'rows' in exp:
        return sql_result_matches(result, list(exp['rows'].items()), string_dimensions=True)
    parts = exp.get('parts', answer.get('parts'))
    columns = [column for column in result.get('columns', []) if column.get('kind') != 'dimension']
    detail = []
    for row in result['rows']:
        if row.get('total'):
            continue
        for index, cell in enumerate(row.get('cells', [])):
            value = cell.get('value', {}).get('value')
            if isinstance(parts, dict):
                lineage = columns[index].get('lineage', []) if index < len(columns) else []
                owners = {source.removeprefix('context:').removeprefix('mapped:').split('.', 1)[0]
                          for source in lineage} & parts.keys()
                if len(owners) != 1:
                    return False
                detail.append((owners.pop(), value))
            else:
                detail.append((value,))
    expected = list(parts.items()) if isinstance(parts, dict) else [(exp['figure'],)]
    dimensions = int(isinstance(parts, dict))
    if not same_rows(detail, expected, dimensions=dimensions, quantums=(None,) * dimensions + (Decimal('0.01'),)):
        return False
    if exp.get('commensurable') is False and any(row.get('total') for row in result['rows']):
        return False
    return sql_result_matches(result, result_rows(result))


ANSWER_BEHAVIOURS = frozenset(('figure', 'combined', 'sum_with_verdict', 'sum_without_verdict',
                               'parts_with_reason', 'cite', 'cite_certified', 'glossary_definition',
                               'catalogue', 'text'))
DECLINES = frozenset(('abstain', 'abstain_with_reason', 'refuse', 'blocked_access', 'abstain_route_steward'))


def has_values(answer):
    return (answer.get('figure') is not None or isinstance(answer.get('parts'), dict) or
            isinstance(answer.get('rows'), dict) or bool(answer.get('result')) or bool(answer.get('figures')))


def no_truth_reason(item):
    exp = item.get('expect') or {}
    if 'ground_truth_error' in exp:
        return str(exp['ground_truth_error'])
    if item.get('_truth_rows') is not None or any(k in exp for k in
            ('figure', 'rows', 'parts', 'cite', 'answer', 'answer_text', 'required_signals', 'no_figure')):
        return None
    if exp.get('behaviour') in (*DECLINES, 'clarify'):
        return None
    return 'No golden answer, citation, text expectation or reference SQL truth is available.'


def citation_errors(item, answer):
    exp = item.get('expect') or {}
    cited = set(answer.get('cite') or [])
    return {'missing': sorted(set(exp.get('cite') or []) - cited),
            'forbidden': sorted(set(exp.get('must_not_cite') or []) & cited)}


def checked_outcome(item, answer, checks):
    if not checks or not all(checks):
        return 'wrong'
    return 'wrong_citation' if any(citation_errors(item, answer).values()) else 'correct'


def outcome(item, answer, rules=None):
    exp = item.get('expect') or {}
    if 'ground_truth_error' in exp:
        return 'no_truth'
    behaviour = answer.get('behaviour')
    values = has_values(answer)
    if behaviour == 'clarify' and not values:
        return 'asked'
    if behaviour not in ANSWER_BEHAVIOURS and not values:
        return 'declined'
    if exp.get('no_figure') or exp.get('behaviour') in (*DECLINES, 'clarify'):
        return 'wrong'
    if no_truth_reason(item):
        return 'no_truth'
    if answer.get('figure') is not None and 'figure' not in exp:
        return 'wrong'
    if answer.get('ratio') is not None and 'ratio' not in exp:
        return 'wrong'
    checks = []
    for key in ('parts', 'rows'):
        if key in exp:
            checks.append(isinstance(answer.get(key), dict) and same(answer[key], exp[key]))
    result = answer.get('result')
    if result is not None and item.get('_truth_rows') is None and any(key in exp for key in ('figure', 'rows', 'parts')):
        checks.append(golden_result_matches(result, exp, answer))
    if 'figure' in exp:
        checks.append(close(answer.get('figure'), exp['figure']))
    if 'ratio' in exp:
        checks.append(close(answer.get('ratio'), exp['ratio'], 0.0001))
    if item.get('_truth_rows') is not None:
        history = answer.get('history') or []
        plan = history[-1].get('plan') if history and isinstance(history[-1], dict) else None
        checks.append(isinstance(answer.get('result'), dict) and isinstance(answer['result'].get('rows'), list) and
                      sql_result_matches(answer['result'], item['_truth_rows'], plan=plan,
                                         context_metrics=item.get('_context_metrics')))
    if isinstance(answer.get('rows'), dict) and 'rows' not in exp:
        truth = item.get('_truth_rows')
        checks.append(truth is not None and same_rows(list(answer['rows'].items()),
            [(str(row[0]) if row[0] is not None else None, *row[1:]) for row in truth], dimensions=1,
            quantums=(None, Decimal('0.01'))))
    if checks:
        if 'behaviour' in exp:
            checks.append(behaviour == exp['behaviour'])
        if 'commensurable' in exp:
            checks.append(answer.get('commensurable') is exp['commensurable'])
        if isinstance(answer.get('parts'), dict) and 'parts' not in exp:
            try:
                checks.append('figure' in exp and close(sum(Decimal(str(v)) for v in answer['parts'].values()), exp['figure']))
            except (InvalidOperation, ValueError, TypeError):
                checks.append(False)
        expected = [exp[key] for key in ('figure', 'ratio') if key in exp]
        expected.extend(value for key in ('parts', 'rows') for value in exp.get(key, {}).values())
        if item.get('_truth_rows') is not None:
            rows = result.get('rows') if isinstance(result, dict) else None
            dimensions = len(rows[0].get('dimensions', [])) if rows else 0
            expected.extend(value for row in item['_truth_rows'] for value in row[dimensions:])
        checks.append(all(isinstance(figure, dict) and any(close(figure.get('value'), value) for value in expected)
                          for figure in answer.get('figures', [])))
        return checked_outcome(item, answer, checks)
    if values:
        return 'wrong'
    if 'cite' in exp:
        checks.append(True)
    if 'answer_text' in exp:
        checks.append(answer.get('answer_text') == exp['answer_text'])
    if 'answer' in exp and 'cite' not in exp:
        checks.append(str(exp['answer']).casefold() in str(answer.get('answer_text', '')).casefold())
    if 'behaviour' in exp:
        checks.append(behaviour == exp['behaviour'])
    if exp.get('required_signals') or exp.get('forbidden_signals'):
        checked, _ = score({**item, 'kind': 'point', 'expect': exp}, answer)
        checks.append(checked)
    return checked_outcome(item, answer, checks)


def truth_put(item, truth):
    """The item to score with the truth its reference SQL gave put on it: a single figure as its figure, else the
    rows as its truth rows; an error as its ground truth error. A copy; the item is not changed."""
    exp = {key: value for key, value in (item.get('expect') or {}).items()
           if key not in ('figure', 'rows', 'parts', 'ground_truth_error')}
    scored = {key: value for key, value in item.items() if key != '_truth_rows'}
    scored['expect'] = exp
    if 'error' in truth:
        exp['ground_truth_error'] = truth['error']
    elif truth.get('figure') is not None and (truth.get('rows') is None or
            len(truth['rows']) == 1 and len(truth['rows'][0]) == 1):
        exp['figure'] = truth['figure']
    else:
        scored['_truth_rows'] = truth['rows']
    return scored


def truth_for(item, answer):
    """The truth one answer is scored against, by the rule every eval follows (issue #565): the item, the status it
    is reported apart under (draft or unavailable, else None) and why.

    An item with approved reference SQL is scored against that SQL's result on the runtime the answer ran on
    (else the runtime its asking list selected), resolved at the eval; where it could not run there, it is
    unavailable. On a runtime other than the reference warehouse, an item without approved reference SQL that states
    a figure or has reference SQL is a draft. On the reference warehouse an item is scored as before."""
    rt = answered_on(answer)
    truths = item.get('_truths')
    if truths is not None:
        selected = item.get('_truth')
        entry = selected if rt is None or not truths else truths.get(rt)
        on = rt or (entry or {}).get('runtime') or REFERENCE
        if entry is None:
            return item, 'unavailable', f'expectation not available on {on}: its reference SQL was not run there'
        if 'error' in entry:
            return item, 'unavailable', f"expectation not available on {on}: {entry['error']}"
        return truth_put(item, entry), None, None
    if rt is not None and rt != REFERENCE and (stated(item) or reference_sql(item)):
        why = 'its reference SQL is not approved' if reference_sql(item) else 'it has no approved reference SQL'
        return item, 'draft', f'draft: {why}, and its expectation was set on the {REFERENCE} warehouse, not on {rt}'
    return item, None, None


def judged(item, answer, rules=None):
    """One answer's outcome and, where it is reported apart (a draft, or an expectation not available on its
    runtime), why; a decline or a clarification stays one."""
    scored, status, why = truth_for(item, answer)
    result = outcome(scored, answer, rules)
    if status is not None and result not in ('declined', 'asked'):
        return status, why
    return result, None


JOB_KINDS = ('graph', 'retrieval', 'warehouse', 'dry', 'catalogue')


def percentiles(samples):
    samples = sorted(samples)
    if not samples:
        return None
    return {key: value if math.isfinite(value) else None for key, value in
            (('median', statistics.median(samples)), ('p95', samples[math.ceil(len(samples) * 0.95) - 1]))}


def compare_latency(rows):
    split = dict.fromkeys(('A_answered', 'A_declined', 'C_answered'), 0)
    ratios, deltas, unpaired = [], [], []
    for row in rows:
        a, b, c = (row.get(f'{arm}_kind') for arm in ('A', 'B', 'C'))
        baseline = 'A' if a is not None and a == b else 'C' if b == c == 'answered' and a == 'declined' else None
        if baseline is None:
            unpaired.append(row['id'])
            continue
        split[f'{baseline}_{b}'] += 1
        before, after = (row['latency_seconds'][arm] for arm in (baseline, 'B'))
        ratio = after / before if before else 1 if not after else math.inf
        delta = after - before
        ratios.append(ratio)
        deltas.append(delta)
        row['latency_comparison'] = {'baseline': baseline, 'kind': b, 'baseline_seconds': before,
                                     'B_seconds': after, 'ratio': ratio if math.isfinite(ratio) else None,
                                     'delta_seconds': delta}
    ratios, deltas = percentiles(ratios), percentiles(deltas)
    passed = bool(ratios and not unpaired and all(
        (ratios[key] is not None and ratios[key] <= 1.2) or deltas[key] <= 0.25 for key in ('median', 'p95')))
    return {'split': split, 'ratios': ratios, 'delta_seconds': deltas, 'unpaired': unpaired}, passed


def plan_of(answer):
    history = (answer or {}).get('history') or []
    return history[-1].get('plan') if history and isinstance(history[-1], dict) else None


def plan_reading(plan):
    """A plan without where its words were read: evidence, spans and turn ids, base_turn among them, differ between two plans that read the
    question the same way."""
    if isinstance(plan, dict):
        return {key: plan_reading(value) for key, value in plan.items() if key not in ('evidence', 'spans', 'turn', 'base_turn')}
    if isinstance(plan, list):
        return [plan_reading(value) for value in plan]
    return plan


def compare(items, ask, grounded_ask=None, *, with_b=False, measure=None, mirror=False):
    """Arms C (rules alone), A (the model's plan first), B (A grounded) and, with B, R (the served order: rules first,
    the model where the rules do not fully match). With mirror, every question-lexer read an item's arms make on its
    thread is recorded and, once every item has answered, so outside every item's timed calls, lexed with the graph's
    phrase table and with the coded constant (kge/eval/phrasemirror.py): the row's ledger_mirror."""
    include_b = grounded_ask is not None or with_b
    def one(item):
        if not mirror:
            return row_of(item), None
        with Mirror() as watched:
            row = row_of(item)
        return row, watched
    def row_of(item):
        jobs = {}
        elapsed = {}
        def call(arm, function):
            samples = []
            def timed():
                start = time.perf_counter()
                try:
                    return function()
                finally:
                    samples.append(time.perf_counter() - start)
            if measure is None:
                answer = timed()
            else:
                answer, counts = measure(arm, item, timed)
                if set(counts) != set(JOB_KINDS) or any(type(counts[k]) is not int or counts[k] < 0 for k in JOB_KINDS):
                    raise ValueError('job counts must contain nonnegative integers for every job kind')
                jobs[arm] = {**counts, 'total': sum(counts.values())}
            elapsed[arm] = samples[0]
            return answer
        apart = {}
        def scored(arm, answer):
            if answer is None:
                return 'missing_reply'
            result, why = judged(item, answer, rules)
            if why is not None:
                apart[arm] = why
            return result
        rules = call('C', lambda: ask('C', item))
        model = call('A', lambda: ask('A', item)) if 'reply' in item else None
        c = scored('C', rules)
        a = scored('A', model)
        row = {'id': item['id'], 'question': item['question'], 'C': c, 'A': a,
                'C_behaviour': rules.get('behaviour'),
                'A_behaviour': model.get('behaviour') if model is not None else None,
                'C_figure': rules.get('figure'), 'A_figure': model.get('figure') if model is not None else None,
                'C_values': [rules.get('rows'), rules.get('parts'), rules.get('result')],
                'A_values': [model.get('rows'), model.get('parts'), model.get('result')] if model is not None else None}
        if include_b:
            grounded = call('B', lambda: grounded_ask(item) if grounded_ask is not None else ask('B', item)) if 'reply' in item else None
            row['B'] = scored('B', grounded)
            row['B_behaviour'] = grounded.get('behaviour') if grounded is not None else None
            row['B_figure'] = grounded.get('figure') if grounded is not None else None
            row['B_values'] = [grounded.get('rows'), grounded.get('parts'), grounded.get('result')] if grounded is not None else None
            row['expected_outcome'] = item.get('expected_outcome')
            served = call('R', lambda: ask('R', item)) if 'reply' in item else None
            row['R'] = scored('R', served)
            row['R_behaviour'] = served.get('behaviour') if served is not None else None
            row['R_figure'] = served.get('figure') if served is not None else None
            row['R_values'] = [served.get('rows'), served.get('parts'), served.get('result')] if served is not None else None
            row['R_answered_by'] = served.get('answered_by') if served is not None else None
            row['R_answer_mode'] = served.get('answer_mode') if served is not None else None
        answers = {'C': rules, 'A': model, **({'B': grounded, 'R': served} if include_b else {})}
        for arm, answer in answers.items():
            row[f'{arm}_kind'] = ('answered' if answer.get('behaviour') in ANSWER_BEHAVIOURS or has_values(answer)
                                 else 'declined') if answer is not None else None
            row[f'{arm}_citation_errors'] = citation_errors(item, answer) if row[f'{arm}_kind'] == 'answered' else {}
            row[f'{arm}_model_calls'] = answer.get('model_calls') if answer is not None else None
            if answer is not None and missing_recording(answer):
                row[arm] = 'missing_reply'
                row[f'{arm}_missing_recording'] = True
        if include_b and row['B_kind'] == row['C_kind'] == 'answered' and plan_reading(plan_of(grounded)) != plan_reading(plan_of(rules)):
            row['plans'] = {'B': plan_of(grounded), 'C': plan_of(rules)}
        for arm in ARMS if include_b else ('C', 'A'):
            if row[arm] == 'no_truth':
                row[f'{arm}_no_truth_reason'] = no_truth_reason(item)
            if row[arm] in APART:
                row[f'{arm}_apart_reason'] = apart[arm]
        expected = (item.get('expect') or {}).get('behaviour')
        row['preserve_refusal'] = expected in (*DECLINES, 'clarify') or bool((item.get('expect') or {}).get('no_figure'))
        row['jobs'] = jobs if measure is not None else None
        row['latency_seconds'] = elapsed
        return row
    answered = each(one, items, lambda item: item['id'])
    for row, watched in answered:
        if watched is not None:
            row['ledger_mirror'] = watched.summary()
    rows = [row for row, _ in answered]
    return summarize(rows, include_b=include_b, measured=measure is not None)


def preserves_refusal(row):
    """Approved alternatives can change a refusal only to another refusal."""
    if row['C'] not in ('declined', 'asked') or row['B'] not in ('declined', 'asked'):
        return False
    approved = row.get('expected_outcome')
    return row['B'] == row['C'] if not approved else row['C'] in approved and row['B'] in approved


ARMS = ('C', 'A', 'B', 'R')


def served_rules(row):
    """Whether arm R served the rules: an answer the rules gave, or a certified decline."""
    return ((row['R_kind'] == 'answered' and row.get('R_answered_by') == 'rules') or
            (row['R_kind'] == 'declined' and row.get('R_answer_mode') == 'certified'))


def served_job_failures(row):
    """Arm R's jobs over its budget: C's where it served the rules, else C's and A's together; retrieval C's always."""
    jobs = row['jobs']
    rules = served_rules(row)
    over = [kind for kind in (*JOB_KINDS, 'total')
            if jobs['R'][kind] > jobs['C'][kind] + (0 if rules else jobs['A'][kind])]
    if jobs['R']['retrieval'] > jobs['C']['retrieval'] and 'retrieval' not in over:
        over.append('retrieval')
    return over


def ledger_mirror(rows):
    """The ledger mirror's findings over rows that carry one: the reads compared, the first mismatch of each item whose
    graph and constant ledgers differ, and the items with a read that fell back to the constant table; none without."""
    mirrored = [row for row in rows if 'ledger_mirror' in row]
    if not mirrored:
        return {}
    return {'ledger_reads': sum(row['ledger_mirror']['reads'] for row in mirrored),
            'ledger_mismatches': [{'id': row['id'], **row['ledger_mirror']['mismatches'][0]}
                                  for row in mirrored if row['ledger_mirror']['mismatches']],
            'ledger_constant_reads': [row['id'] for row in mirrored if row['ledger_mirror']['constant_reads']]}


def phrase_baseline_line(result):
    """The report's line for the ledger mirror on one set."""
    differ = ', '.join(row['id'] for row in result['ledger_mismatches'])
    constant = ', '.join(result['ledger_constant_reads'])
    if not result['ledger_reads']:
        return "Phrase baseline v1: no ledger reads were recorded, so no ledger was checked"
    if not differ and not constant:
        return (f"Phrase baseline v1: {result['ledger_reads']} ledger reads, graph and constant ledgers identical on "
                "every one")
    return (f"Phrase baseline v1: {result['ledger_reads']} ledger reads; graph and constant ledgers differ on: "
            f"{differ or 'none'}; read the constant table: {constant or 'none'}")


def summarize(rows, *, include_b=True, measured=True):
    counts = {}
    for arm in ARMS if include_b else ('C', 'A'):
        counts[arm] = {key: sum(row[arm] == key for row in rows) for key in
                       ('correct', 'wrong', 'wrong_citation', 'declined', 'asked', 'no_truth', 'unscored', 'missing_reply')}
        counts[arm].update({key: n for key in APART if (n := sum(row[arm] == key for row in rows))})
        counts[arm]['answered'] = sum(counts[arm].get(key, 0) for key in ('correct', 'wrong', 'wrong_citation', 'no_truth', *APART))
    losses = [row['id'] for row in rows if row['C'] == 'correct' and row.get('B') not in ('correct', *APART)] if include_b else []
    failures = [row['id'] for row in rows if row.get('expected_outcome') and row.get('B') not in APART
                and row.get('B') not in row['expected_outcome']]
    served_losses = [row['id'] for row in rows if row['B'] == 'correct' and row.get('R') not in ('correct', *APART)] if include_b else []
    served_wrong = [row['id'] for row in rows if row.get('R') == 'wrong'] if include_b else []
    model_calls = [row['id'] for row in rows if include_b and row['C_kind'] == 'answered' and row.get('R_model_calls')
                   and row.get('R_answered_by') is not None]
    off_plan = [row['id'] for row in rows if include_b and row['C_kind'] == 'answered' and row.get('R_model_calls')
                and row.get('R_answered_by') is None]
    served_jobs = []
    jobs = None
    job_failures = []
    if measured:
        jobs = {arm: {kind: {'total': sum(row['jobs'][arm][kind] for row in rows if arm in row['jobs']),
                             'max': max((row['jobs'][arm][kind] for row in rows if arm in row['jobs']), default=0)}
                      for kind in (*JOB_KINDS, 'total')} for arm in counts}
        if include_b:
            job_failures = [row['id'] for row in rows if 'B' in row['jobs'] and
                            any(row['jobs']['B'][kind] > max(row['jobs']['C'][kind], row['jobs']['A'][kind])
                                for kind in (*JOB_KINDS, 'total'))]
            served_jobs = [{'id': row['id'], 'budget': 'C' if served_rules(row) else 'C+A', 'over': over}
                           for row in rows if 'R' in row['jobs'] and (over := served_job_failures(row))]
    latency = {}
    for arm in counts:
        latency[arm] = percentiles(row['latency_seconds'][arm] for row in rows if arm in row['latency_seconds'])
    latency_comparison, latency_pass = compare_latency(rows) if include_b else (None, False)
    citation_failures = [row['id'] for row in rows if include_b and
                         sum(map(len, row['B_citation_errors'].values())) > sum(map(len, row['C_citation_errors'].values()))]
    apart = [{'id': row['id'], 'arm': arm, 'status': row[arm], 'reason': row[f'{arm}_apart_reason']}
             for row in rows for arm in counts if row[arm] in APART]
    return {**({'apart': apart} if apart else {}), **ledger_mirror(rows), 'counts': counts, 'latency_seconds': latency, 'latency_pass': latency_pass,
            'latency_comparison': latency_comparison, 'citation_budget_failures': citation_failures,
            'wrong_citations': [{'id': row['id'], 'arm': arm, **row[f'{arm}_citation_errors']}
                                for row in rows for arm in counts if any(row[f'{arm}_citation_errors'].values())],
            'no_truth': [{'id': row['id'], 'arm': arm, 'reason': row[f'{arm}_no_truth_reason']}
                         for row in rows for arm in counts if row[arm] == 'no_truth'],
            'refusal_failures': [row['id'] for row in rows if include_b and row['preserve_refusal'] and
                                 not preserves_refusal(row)],
            'jobs_per_request': jobs, 'job_budget_failures': job_failures, 'b_loses_to_c': losses, 'r_loses_to_b': served_losses, 'r_wrong': served_wrong,
            'r_model_call_failures': model_calls, 'r_model_calls_off_plan': off_plan, 'r_job_budget_failures': served_jobs, 'expected_outcome_failures': failures, 'a_loses_to_c': sum(row['C'] == 'correct' and row['A'] in ('wrong', 'wrong_citation', 'declined', 'asked') for row in rows),
            'differences': [row for row in rows if any(
                (row[arm], row[f'{arm}_behaviour'], row[f'{arm}_figure'], row[f'{arm}_values']) !=
                (row['C'], row['C_behaviour'], row['C_figure'], row['C_values'])
                for arm in (('A', 'B', 'R') if include_b else ('A',)))], 'rows': rows}


def write(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'grounding-eval.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    lines = ['# Grounding evaluation', '',
             'Each worker reuses one engine per arm. Release initialization finishes before job counting; '
             'per-request caches are reset to their initial state between items. Truth queries are outside the request.', '',
             'Latency is informational: local runs make no model or BigQuery calls; latency is checked on the pilot. '
             'One timing per request.', '',
             '| Set | Arm | Items | Answered | Correct | Wrong figure | Wrong citation | Declined | Clarify | No truth | Unscored | Missing reply | Jobs total | Jobs max |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    uat = [result for name, result in report.items() if name.startswith('uat-') and result.get('captured_replies')]
    lines[2:2] = [f"UAT: {'run' if uat else 'not run, no captures'}",
                  f"Reference: {len(report['reference']['rows']) if 'reference' in report else 0} of 25 items; "
                  f"shortfall: {max(0, 25 - len(report['reference']['rows'])) if 'reference' in report else 25}.", '']
    for name, result in report.items():
        for arm, counts in result['counts'].items():
            jobs = (result['jobs_per_request'] or {}).get(arm, {}).get('total', {})
            lines.append(f"| {name} | {arm} | {len(result['rows'])} | {counts['answered']} | {counts['correct']} | {counts['wrong']} | {counts['wrong_citation']} | {counts['declined']} | {counts['asked']} | {counts['no_truth']} | {counts['unscored']} | {counts['missing_reply']} | {jobs.get('total', 'not measured')} | {jobs.get('max', 'not measured')} |")
        lines.extend(['', '| Arm | Median seconds | p95 seconds |', '|---|---:|---:|'])
        for arm, latency in result['latency_seconds'].items():
            lines.append(f"| {arm} | {latency['median']:.6f} | {latency['p95']:.6f} |" if latency else f'| {arm} | not measured | not measured |')
        comparison = result['latency_comparison']
        if comparison is not None:
            lines.extend(['', '| Per-item latency baseline | Items |', '|---|---:|'])
            for split, count in comparison['split'].items():
                lines.append(f"| {split} | {count} |")
            lines.extend(['', '| Per-item comparison | Median | p95 |', '|---|---:|---:|'])
            for key in ('ratios', 'delta_seconds'):
                values = comparison[key]
                rendered = [f'{values[stat]:.6f}' if values and values[stat] is not None else 'unavailable'
                            for stat in ('median', 'p95')]
                lines.append(f"| {key} | {' | '.join(rendered)} |")
            lines.append(f"Unpaired latency items: {', '.join(comparison['unpaired']) or 'none'}")
        if result['jobs_per_request'] is None:
            lines.extend(['', 'BigQuery and graph-store jobs per request: not measured.'])
        else:
            lines.extend(['', '| Arm | Job kind | Total | Max per request |', '|---|---|---:|---:|'])
            for arm, kinds in result['jobs_per_request'].items():
                for kind, values in kinds.items():
                    lines.append(f"| {arm} | {kind} | {values['total']} | {values['max']} |")
            lines.append(f"B job budget failures: {', '.join(result['job_budget_failures']) or 'none'}")
            lines.append("R job budget failures: " + (', '.join(
                f"{row['id']} (over {row['budget']}: {', '.join(row['over'])})"
                for row in result.get('r_job_budget_failures', [])) or 'none'))
        lines.extend(['', f"B citation budget failures: {', '.join(result['citation_budget_failures']) or 'none'}", '', 'Wrong citations:'])
        lines.extend(f"- {row['id']} ({row['arm']}): missing {', '.join(row['missing']) or 'none'}; forbidden {', '.join(row['forbidden']) or 'none'}"
                     for row in result['wrong_citations'])
        if not result['wrong_citations']:
            lines.append('- none')
        lines.extend(['', 'Missing recordings:'])
        missing = [f"- {row['id']} ({arm}): missing recording" for row in result['rows'] for arm in result['counts']
                   if row.get(f'{arm}_missing_recording')]
        lines.extend(missing or ['- none'])
        if 'ledger_reads' in result:
            lines.extend(['', phrase_baseline_line(result)])
        lines.extend(['', f"B losses to C: {', '.join(result['b_loses_to_c']) or 'none'}",
                      f"R losses to B: {', '.join(result.get('r_loses_to_b', [])) or 'none'}",
                      f"R wrong figures: {', '.join(result.get('r_wrong', [])) or 'none'}",
                      f"R model calls on C-answered items: {', '.join(result.get('r_model_call_failures', [])) or 'none'}",
                      f"R model calls on C-answered items off the plan path, not gated: {', '.join(result.get('r_model_calls_off_plan', [])) or 'none'}",
                      f"Expected outcome failures: {', '.join(result['expected_outcome_failures']) or 'none'}",
                      f"Refusal failures: {', '.join(result['refusal_failures']) or 'none'}", '', 'No truth:'])
        lines.extend(f"- {row['id']} ({row['arm']}): {row['reason']}" for row in result['no_truth'])
        if not result['no_truth']:
            lines.append('- none')
        if result.get('apart'):
            lines.extend(['', 'Reported apart, neither passed nor failed (items): ' + ', '.join(
                f"{key} {len({row['id'] for row in result['apart'] if row['status'] == key})}" for key in APART
                if any(row['status'] == key for row in result['apart']))])
            lines.extend(f"- {row['id']} ({row['arm']}): {row['reason']}" for row in result['apart'])
        lines.extend(['', f'## {name}: differences', ''])
        for row in result['differences']:
            values = "; ".join(f"{arm}: {row[arm]}" for arm in ARMS if arm in row)
            lines.append(f"- {row['id']}: {row['question']} — {values}")
            if row.get('plans'):
                lines.extend(f"  - {arm} plan: `{json.dumps(row['plans'][arm], sort_keys=True)}`" for arm in ('B', 'C'))
        lines.append('')
    (directory / 'grounding-eval.md').write_text('\n'.join(lines) + '\n')


def default_sets(directory):
    return [ROOT / 'build/golden.json', ROOT / 'build/heldout.json',
            *sorted(path for path in Path(directory).glob('*.json')
                    if not path.stem.startswith('uat-'))]


ORDERS = {'A': 'model_first', 'B': 'model_first', 'R': 'rules_first'}


class Order:
    """ASK_ORDER held for every call that runs under it: calls under one order run side by side, a call under another
    waits until they finish, and the setting is put back when the last one does."""

    def __init__(self):
        self.changed = Condition()
        self.value = None
        self.running = 0
        self.before = None

    @contextmanager
    def held(self, order):
        with self.changed:
            self.changed.wait_for(lambda: self.running == 0 or self.value == order)
            if self.running == 0:
                self.value, self.before = order, os.environ.get('ASK_ORDER')
                os.environ['ASK_ORDER'] = order
            self.running += 1
        try:
            yield
        finally:
            with self.changed:
                self.running -= 1
                if self.running == 0:
                    if self.before is None:
                        os.environ.pop('ASK_ORDER', None)
                    else:
                        os.environ['ASK_ORDER'] = self.before
                    self.value = None
                    self.changed.notify_all()


ORDER = Order()


def ask_with(engine, arm, item, principal):
    """One arm's answer on the served Ask path, with the number of model calls it made: C with no model, A and B
    with the recorded reply under model_first (B grounded), R with it under rules_first, the served order. Each is
    asked on the item's runtime list (asking_runtimes), the list its truth is computed on."""
    model = None if arm == 'C' else Replay(item['reply'])
    combined = Combined(engine, llm=model)
    cfg = combined.default_config('graph')
    if model is not None:
        combined.plan_grounding = arm != 'A'
        cfg = cfg.with_stages(sql_source='planner+model', renderer='template')
    runtimes = asking_runtimes(item)
    asked = {'runtimes': runtimes} if runtimes is not None else {}
    try:
        if model is None:
            answer = combined.ask(item['question'], asked_as(item, principal), config=cfg, **asked)
        else:
            with ORDER.held(ORDERS[arm]):
                answer = combined.ask(item['question'], asked_as(item, principal), config=cfg, **asked)
    except NoFallback as exc:
        answer = {'behaviour': 'abstain_with_reason', 'reason': str(exc)}
    return {**answer, 'model_calls': model.calls if model is not None else 0}


def request_reset(engine):
    """Restore the graph's request caches to the state immediately after release initialization."""
    objects = [(engine.g, ('_term_reads', '_chunk_counts', '_definition_readiness', '_scoped_vocabularies',
                           '_role_templates', '_model_hash')),
               (engine.semantics, ('plan_snapshot', 'baseline_snapshot'))]
    held = [(obj, name, name in vars(obj), deepcopy(getattr(obj, name, None))) for obj, names in objects for name in names]
    def reset():
        for obj, name, present, value in held:
            if present:
                setattr(obj, name, deepcopy(value))
            else:
                vars(obj).pop(name, None)
    return reset


class RequestEngines:
    """One initialized engine per arm, with equally cold request caches and no shared concurrent engine."""

    def __init__(self, opened, principal, reset=request_reset):
        self.principal = principal
        self.engines = {arm: opened() for arm in ARMS}
        self.resets = {arm: reset(engine) for arm, engine in self.engines.items()}
        self.locks = {arm: RLock() for arm in self.engines}

    def ask(self, arm, item):
        engine = self.engines[arm]
        with self.locks[arm]:
            self.resets[arm]()
            with engine.g.as_roles():
                return ask_with(engine, arm, item, self.principal)

    def close(self):
        for engine in self.engines.values():
            engine.close()


def resolve_truth(w, cache, sql, unit, readable, runtimes, runtime_id=None):
    """A reference SQL's result through the warehouse's rendering and SQL gate, on runtime_id (else the runtime the
    list runtimes selects), or the refusal that kept it from running there; cached by runtime, SQL, unit, access and
    list. Any other error is the run's, not the item's: it propagates and is not cached, as judge in run_golden does."""
    key = (runtime_id, sql, unit, tuple(sorted(readable)), tuple(runtimes or ()))
    if key not in cache:
        scope = {'runtime_id': runtime_id} if runtime_id is not None else {}
        try:
            truth = w.resolve_reference_sql(sql, unit=unit, readable_units=readable, runtimes=runtimes, **scope)
            ran = truth.get('runtime') if isinstance(truth, dict) else None
            cache[key] = {**truth, 'runtime': ran.get('id') if isinstance(ran, dict) else runtime_id}
        except QueryRefused as exc:
            cache[key] = {'error': str(exc), 'runtime': runtime_id}
    return cache[key]


def candidate_runtimes(w, runtimes):
    """The runtimes an answer asked on the list runtimes can run on: the list, else the deployment's default list;
    none where no runtime is declared, requested or configured, where the reference warehouse answers everything."""
    if not w.runtimes_active(runtimes):
        return []
    return list(dict.fromkeys(runtimes or w.default_runtime_list()))


def evaluate(engine, paths, *, recordings=None, ask=None, measure=None, compare_items=None):
    """Every set's comparison of the arms, each item scored against its truth on the runtime each answer ran on.

    Each item is asked, arm by arm, on one runtime list (asking_runtimes), and its reference SQL is run on that same
    list, outside the requests, before the items are asked: an item with approved reference SQL on each runtime an
    answer can run on, an item without it as before, on the runtime the list selects. Scoring then takes the
    truth of the runtime each answer ran on (truth_for), so a worker process scores with no warehouse of its own."""
    principal = engine.profile.asking(None)
    requests = RequestEngines(lambda: Engine.serving(engine.profile), principal) if ask is None and compare_items is None else None
    report = {}
    try:
        with engine.g.as_roles():
            semantics = getattr(engine, 'semantics', None)
            context_metrics = {context.id: dict(measure=context.measure, aggregation=context.aggregation)
                               for context in semantics.contexts.values()} if semantics is not None else {}
            for path in paths:
                questions = load(path, recordings)
                table_truth = {}
                for item in questions:
                    item['_context_metrics'] = context_metrics
                    exp = item.setdefault('expect', {})
                    sql = reference_sql(item)
                    if not sql:
                        continue
                    unit = item.get('unit') or exp.get('unit') or (engine.UNIT_ORDER[0] if engine.UNIT_ORDER else None)
                    readable = engine.g.readable_units(asked_as(item, principal))
                    runtimes = asking_runtimes(item)
                    if approved(item):
                        item['_truth'] = resolve_truth(engine.w, table_truth, sql, unit, readable, runtimes)
                        item['_truths'] = {rt: resolve_truth(engine.w, table_truth, sql, unit, readable, runtimes, rt)
                                           for rt in candidate_runtimes(engine.w, runtimes)}
                        continue
                    if any(key in exp for key in ('figure', 'rows', 'parts')):
                        continue
                    put = truth_put(item, resolve_truth(engine.w, table_truth, sql, unit, readable, runtimes))
                    item['expect'] = put['expect']
                    if '_truth_rows' in put:
                        item['_truth_rows'] = put['_truth_rows']
                report[Path(path).stem] = (compare_items(questions) if compare_items is not None else
                    compare(questions, ask or requests.ask, with_b=True, measure=measure, mirror=True))
                if report[Path(path).stem].get('ledger_reads') == 0 and report[Path(path).stem]['rows']:
                    report[Path(path).stem]['ledger_unchecked'] = True
                report[Path(path).stem]['captured_replies'] = sum(
                    'raw_model_reply' in item or 'raw model reply' in item for item in questions)
    finally:
        if requests is not None:
            requests.close()
    return report


def passed(report):
    """Whether every set passes the gate; a set the ledger mirror watched also needs no read whose graph and constant
    ledgers differ, none that fell back to the constant table and at least one read checked (ledger_unchecked, which
    evaluate sets on any mirrored set with rows and no read), so the check never passes on nothing."""
    return bool(report) and all(result['rows'] and result['jobs_per_request'] is not None and
               not any(result[key] for key in ('job_budget_failures', 'citation_budget_failures', 'b_loses_to_c', 'expected_outcome_failures',
                                                'refusal_failures', 'no_truth', 'r_loses_to_b', 'r_wrong', 'r_model_call_failures',
                                                'r_job_budget_failures')) and
               result['counts']['B']['correct'] >= result['counts']['C']['correct'] and
               result['counts']['B']['answered'] >= result['counts']['C']['answered'] and
               result['counts']['B']['wrong'] == 0 and
               all(counts['unscored'] == counts['missing_reply'] == 0 for counts in result['counts'].values()) and
               not result.get('ledger_mismatches') and not result.get('ledger_constant_reads') and
               not result.get('ledger_unchecked')
               for result in report.values())


def main(measure=None, ask=None, argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('sets', nargs='*', type=Path)
    parser.add_argument('--recordings', type=Path, required=True)
    parser.add_argument('--regressions', type=Path)
    parser.add_argument("--profile")
    parser.add_argument('--out', type=Path, default=Path('build'))
    args = parser.parse_args(argv)
    if not args.sets and args.regressions is None:
        parser.error('provide evaluation sets or --regressions')
    engine = Engine.serving(resolve(args.profile).serve())
    try:
        report = evaluate(engine, args.sets or default_sets(args.regressions), recordings=args.recordings, ask=ask, measure=measure)
    finally:
        engine.close()
    write(report, args.out)
    return int(not passed(report))


if __name__ == '__main__':
    raise SystemExit(main())
