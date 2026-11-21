"""Finite adjacent half-open ranges, declared as data and rendered as CASE."""
from decimal import Decimal

from sqlglot import exp


def band_problems(ranges):
    """All numeric values have exactly one label; NULL remains its own empty bucket."""
    if not isinstance(ranges, (list, tuple)) or not ranges:
        return ['requires a nonempty ordered list of labelled ranges']
    problems, labels, previous = [], set(), None
    for i, item in enumerate(ranges):
        label = item['label'].strip().casefold()
        if not label or label in labels:
            problems.append('range labels must be nonempty and unique')
        labels.add(label)
        bounds = []
        for bound in (item['min'], item['max']):
            if bound is None:
                bounds.append(None)
            elif isinstance(bound, (int, float, Decimal)) and not isinstance(bound, bool) and Decimal(str(bound)).is_finite():
                bounds.append(Decimal(str(bound)))
            else:
                problems.append('range bounds must be finite numbers or null')
                bounds.append(None)
        low, high = bounds
        if (i == 0 and low is not None) or (i > 0 and (low is None or low != previous)):
            problems.append('ranges must be sorted and contiguous, without gaps or overlaps, starting at null')
        if low is not None and high is not None and low >= high:
            problems.append('range minimum must be less than its maximum')
        if (i < len(ranges) - 1 and high is None) or (i == len(ranges) - 1 and high is not None):
            problems.append('only the last range ends at null')
        previous = high
    return list(dict.fromkeys(problems))


def band_expression(column, ranges):
    """Build each declared boundary explicitly so NULL never falls into an unbounded range."""
    choices = []
    for item in ranges:
        tests = []
        if item['min'] is not None:
            tests.append(exp.GTE(this=column.copy(), expression=exp.Literal.number(str(item['min']))))
        if item['max'] is not None:
            tests.append(exp.LT(this=column.copy(), expression=exp.Literal.number(str(item['max']))))
        condition = exp.and_(*tests) if tests else exp.Not(this=exp.Is(this=column.copy(), expression=exp.Null()))
        choices.append(exp.If(this=condition, true=exp.Literal.string(item['label'])))
    return exp.Case(ifs=choices, default=exp.Null())
