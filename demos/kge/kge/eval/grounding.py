from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from threading import RLock

from kge.adapters.model.errors import NoFallback
from kge.eval.asking import each
from kge.eval.harness import missing_recording
from kge.eval.run_golden import RATIO, asked_as, close, same, score
from kge.eval.sets import items as set_items
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import Engine


class Replay:
    name = 'grounding-replay'

    def __init__(self, reply):
        self.reply = reply

    def json(self, prompt, max_tokens=800):
        if prompt.startswith('query-plan-v1\n'):
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


def sql_result_matches(result, truth, *, string_dimensions=False, plan=None):
    detail = result_rows(result, string_dimensions=string_dimensions)
    totals = result_rows(result, total=True, string_dimensions=string_dimensions)
    dimensions = len(result.get('rows', [{}])[0].get('dimensions', [])) if result.get('rows') else 0
    columns = [column for column in result.get('columns', []) if column.get('kind') != 'dimension']
    derived = plan.get('derived', []) if isinstance(plan, dict) else []
    arithmetic = {node['id']: node for node in derived if isinstance(node, dict)
                  and node.get('kind') in ('difference', 'ratio', 'percent_change') and node.get('id')}
    positions = {column.get('id'): index for index, column in enumerate(columns)}
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
                continue
            if column.get('kind') not in ('measure', 'comparison') and not (
                    column.get('kind') == 'derived' and column.get('id') in arithmetic):
                return False
            try:
                values = [Decimal(str(row[dimensions + index])) for row in matching]
                expected = sum(values, Decimal(0))
                if column.get('kind') == 'derived':
                    node = arithmetic[column['id']]
                    first, second = node.get('numerator'), node.get('denominator')
                    if first not in positions or second not in positions:
                        return False
                    if any(columns[positions[i]].get('kind') not in ('measure', 'comparison') for i in (first, second)):
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
                      sql_result_matches(answer['result'], item['_truth_rows'], plan=plan))
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


def compare(items, ask, grounded_ask=None, *, with_b=False, measure=None):
    include_b = grounded_ask is not None or with_b
    def one(item):
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
        rules = call('C', lambda: ask('C', item))
        model = call('A', lambda: ask('A', item)) if 'reply' in item else None
        c = outcome(item, rules, rules)
        a = outcome(item, model, rules) if model is not None else 'missing_reply'
        row = {'id': item['id'], 'question': item['question'], 'C': c, 'A': a,
                'C_behaviour': rules.get('behaviour'),
                'A_behaviour': model.get('behaviour') if model is not None else None,
                'C_figure': rules.get('figure'), 'A_figure': model.get('figure') if model is not None else None,
                'C_values': [rules.get('rows'), rules.get('parts'), rules.get('result')],
                'A_values': [model.get('rows'), model.get('parts'), model.get('result')] if model is not None else None}
        if include_b:
            grounded = call('B', lambda: grounded_ask(item) if grounded_ask is not None else ask('B', item)) if 'reply' in item else None
            row['B'] = outcome(item, grounded, rules) if grounded is not None else 'missing_reply'
            row['B_behaviour'] = grounded.get('behaviour') if grounded is not None else None
            row['B_figure'] = grounded.get('figure') if grounded is not None else None
            row['B_values'] = [grounded.get('rows'), grounded.get('parts'), grounded.get('result')] if grounded is not None else None
            row['expected_outcome'] = item.get('expected_outcome')
        answers = {'C': rules, 'A': model, **({'B': grounded} if include_b else {})}
        for arm, answer in answers.items():
            row[f'{arm}_kind'] = ('answered' if answer.get('behaviour') in ANSWER_BEHAVIOURS or has_values(answer)
                                 else 'declined') if answer is not None else None
            row[f'{arm}_citation_errors'] = citation_errors(item, answer) if row[f'{arm}_kind'] == 'answered' else {}
            if answer is not None and missing_recording(answer):
                row[arm] = 'missing_reply'
                row[f'{arm}_missing_recording'] = True
        for arm in ('C', 'A', 'B') if include_b else ('C', 'A'):
            if row[arm] == 'no_truth':
                row[f'{arm}_no_truth_reason'] = no_truth_reason(item)
        expected = (item.get('expect') or {}).get('behaviour')
        row['preserve_refusal'] = expected in (*DECLINES, 'clarify') or bool((item.get('expect') or {}).get('no_figure'))
        row['jobs'] = jobs if measure is not None else None
        row['latency_seconds'] = elapsed
        return row
    rows = each(one, items, lambda item: item['id'])
    return summarize(rows, include_b=include_b, measured=measure is not None)


def summarize(rows, *, include_b=True, measured=True):
    counts = {}
    for arm in ('C', 'A', 'B') if include_b else ('C', 'A'):
        counts[arm] = {key: sum(row[arm] == key for row in rows) for key in
                       ('correct', 'wrong', 'wrong_citation', 'declined', 'asked', 'no_truth', 'unscored', 'missing_reply')}
        counts[arm]['answered'] = sum(counts[arm][key] for key in ('correct', 'wrong', 'wrong_citation', 'no_truth'))
    losses = [row['id'] for row in rows if row['C'] == 'correct' and row.get('B') != 'correct'] if include_b else []
    failures = [row['id'] for row in rows if row.get('expected_outcome') and row.get('B') not in row['expected_outcome']]
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
    latency = {}
    for arm in counts:
        latency[arm] = percentiles(row['latency_seconds'][arm] for row in rows if arm in row['latency_seconds'])
    latency_comparison, latency_pass = compare_latency(rows) if include_b else (None, False)
    citation_failures = [row['id'] for row in rows if include_b and
                         sum(map(len, row['B_citation_errors'].values())) > sum(map(len, row['C_citation_errors'].values()))]
    return {'counts': counts, 'latency_seconds': latency, 'latency_pass': latency_pass,
            'latency_comparison': latency_comparison, 'citation_budget_failures': citation_failures,
            'wrong_citations': [{'id': row['id'], 'arm': arm, **row[f'{arm}_citation_errors']}
                                for row in rows for arm in counts if any(row[f'{arm}_citation_errors'].values())],
            'no_truth': [{'id': row['id'], 'arm': arm, 'reason': row[f'{arm}_no_truth_reason']}
                         for row in rows for arm in counts if row[arm] == 'no_truth'],
            'refusal_failures': [row['id'] for row in rows if include_b and row['preserve_refusal'] and
                                 (row['C'] not in ('declined', 'asked') or row['B'] != row['C'])],
            'jobs_per_request': jobs, 'job_budget_failures': job_failures, 'b_loses_to_c': losses, 'expected_outcome_failures': failures, 'a_loses_to_c': sum(row['C'] == 'correct' and row['A'] in ('wrong', 'wrong_citation', 'declined', 'asked') for row in rows),
            'differences': [row for row in rows if any(
                (row[arm], row[f'{arm}_behaviour'], row[f'{arm}_figure'], row[f'{arm}_values']) !=
                (row['C'], row['C_behaviour'], row['C_figure'], row['C_values'])
                for arm in (('A', 'B') if include_b else ('A',)))], 'rows': rows}


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
        lines.extend(['', f"B citation budget failures: {', '.join(result['citation_budget_failures']) or 'none'}", '', 'Wrong citations:'])
        lines.extend(f"- {row['id']} ({row['arm']}): missing {', '.join(row['missing']) or 'none'}; forbidden {', '.join(row['forbidden']) or 'none'}"
                     for row in result['wrong_citations'])
        if not result['wrong_citations']:
            lines.append('- none')
        lines.extend(['', 'Missing recordings:'])
        missing = [f"- {row['id']} ({arm}): missing recording" for row in result['rows'] for arm in result['counts']
                   if row.get(f'{arm}_missing_recording')]
        lines.extend(missing or ['- none'])
        lines.extend(['', f"B losses to C: {', '.join(result['b_loses_to_c']) or 'none'}",
                      f"Expected outcome failures: {', '.join(result['expected_outcome_failures']) or 'none'}",
                      f"Refusal failures: {', '.join(result['refusal_failures']) or 'none'}", '', 'No truth:'])
        lines.extend(f"- {row['id']} ({row['arm']}): {row['reason']}" for row in result['no_truth'])
        if not result['no_truth']:
            lines.append('- none')
        lines.extend(['', f'## {name}: differences', ''])
        for row in result['differences']:
            values = "; ".join(f"{arm}: {row[arm]}" for arm in ("C", "A", "B") if arm in row)
            lines.append(f"- {row['id']}: {row['question']} — {values}")
        lines.append('')
    (directory / 'grounding-eval.md').write_text('\n'.join(lines) + '\n')


def default_sets(directory):
    return [ROOT / 'build/golden.json', ROOT / 'build/heldout.json',
            *sorted(path for path in Path(directory).glob('*.json')
                    if not path.stem.startswith('uat-'))]


def ask_with(engine, arm, item, principal):
    combined = Combined(engine, llm=None if arm == 'C' else Replay(item['reply']))
    cfg = combined.default_config('graph')
    if arm in ('A', 'B'):
        combined.plan_grounding = arm == 'B'
        cfg = cfg.with_stages(sql_source='planner+model', renderer='template')
    try:
        return combined.ask(item['question'], asked_as(item, principal), config=cfg)
    except NoFallback as exc:
        return {'behaviour': 'abstain_with_reason', 'reason': str(exc)}


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
        self.engines = {arm: opened() for arm in ('C', 'A', 'B')}
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


def evaluate(engine, paths, *, recordings=None, ask=None, measure=None, compare_items=None):
    principal = engine.profile.asking(None)
    requests = RequestEngines(lambda: Engine.serving(engine.profile), principal) if ask is None and compare_items is None else None
    report = {}
    try:
        with engine.g.as_roles():
            for path in paths:
                questions = load(path, recordings)
                table_truth = {}
                for item in questions:
                    exp = item.setdefault('expect', {})
                    sql = item.get('reference_sql') or exp.get('reference_sql')
                    if not sql or any(key in exp for key in ('figure', 'rows', 'parts')):
                        continue
                    unit = item.get('unit') or exp.get('unit') or (engine.UNIT_ORDER[0] if engine.UNIT_ORDER else None)
                    readable = engine.g.readable_units(asked_as(item, principal))
                    key = (sql, unit, tuple(sorted(readable)), tuple(item.get('runtimes') or ()))
                    if key not in table_truth:
                        try:
                            table_truth[key] = engine.w.resolve_reference_sql(sql, unit=unit, readable_units=readable,
                                                                             runtimes=item.get('runtimes'))
                        except Exception as exc:
                            table_truth[key] = {'error': str(exc)}
                    truth = table_truth[key]
                    if 'error' in truth:
                        exp['ground_truth_error'] = truth['error']
                    elif truth.get('figure') is not None and (truth.get('rows') is None or
                            len(truth['rows']) == 1 and len(truth['rows'][0]) == 1):
                        exp['figure'] = truth['figure']
                    else:
                        item['_truth_rows'] = truth['rows']
                report[Path(path).stem] = (compare_items(questions) if compare_items is not None else
                    compare(questions, ask or requests.ask, with_b=True, measure=measure))
                report[Path(path).stem]['captured_replies'] = sum(
                    'raw_model_reply' in item or 'raw model reply' in item for item in questions)
    finally:
        if requests is not None:
            requests.close()
    return report


def passed(report):
    return bool(report) and all(result['rows'] and result['jobs_per_request'] is not None and
               not any(result[key] for key in ('job_budget_failures', 'citation_budget_failures', 'b_loses_to_c', 'expected_outcome_failures',
                                                'refusal_failures', 'no_truth')) and
               result['counts']['B']['correct'] >= result['counts']['C']['correct'] and
               result['counts']['B']['answered'] >= result['counts']['C']['answered'] and
               result['counts']['B']['wrong'] == 0 and
               all(counts['unscored'] == counts['missing_reply'] == 0 for counts in result['counts'].values())
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
