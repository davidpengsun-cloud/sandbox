"""Calendar windows and one-to-one comparison mappings without warehouse reads."""
from __future__ import annotations

import calendar
import datetime

from kge.semantics.query import month_end


def add_months(date, count):
    y, m = divmod(date.year * 12 + date.month - 1 + count, 12)
    return datetime.date(y, m + 1, min(date.day, calendar.monthrange(y, m + 1)[1]))


def bounds(period, calendars):
    cal = calendars[period.calendar.id]
    value, kind = period.value, period.kind
    if kind == 'day':
        first = datetime.date.fromisoformat(value)
        return first, first + datetime.timedelta(days=1)
    if kind == 'month':
        first = datetime.date.fromisoformat(value + '-01')
        return first, add_months(first, 1)
    year_text = value.split('-')[0].removeprefix('FY')
    year = int(year_text) + (2000 if len(year_text) == 2 else 0)
    fiscal = kind in ('fiscal_quarter', 'fiscal_year')
    start = cal['year_start_month'] if fiscal else 1
    if fiscal and start != 1 and cal['quarter_label'].startswith('FY'):
        year -= 1
    first = datetime.date(year, start, 1)
    if kind in ('calendar_quarter', 'fiscal_quarter'):
        first = add_months(first, (int(value[-1]) - 1) * 3)
        return first, add_months(first, 3)
    return first, add_months(first, 12)


def time_bounds(time, calendars):
    if time.kind in ('all_available', 'latest_released'):
        return None
    if time.kind == 'single':
        return bounds(time.period, calendars)
    if time.kind == 'range':
        return bounds(time.first, calendars)[0], bounds(time.last, calendars)[1]
    lo, hi = bounds(time.through, calendars)
    start = calendars[time.calendar.id]['year_start_month']
    return datetime.date(lo.year - (lo.month < start), start, 1), hi


def time_calendar(time):
    return time.period.calendar.id if time.kind == 'single' else time.first.calendar.id if time.kind == 'range' else time.calendar.id


def periods(window, grain, cal):
    if window is None:
        return ()
    lo, hi = window
    if grain == 'day':
        return tuple(lo + datetime.timedelta(days=i) for i in range((hi - lo).days))
    start = cal['year_start_month'] if grain in ('fiscal_quarter', 'fiscal_year') else 1
    step = 1 if grain == 'month' else 3 if grain in ('calendar_quarter', 'fiscal_quarter') else 12
    anchor = datetime.date(lo.year - (lo.month < start), start, 1)
    while add_months(anchor, step) <= lo:
        anchor = add_months(anchor, step)
    result = []
    while anchor < hi:
        result.append(anchor)
        anchor = add_months(anchor, step)
    return tuple(result)


def release_months(window):
    if window is None:
        return ()
    lo, hi = window
    out = []
    while lo < hi:
        out.append(month_end(lo.year, lo.month))
        lo = add_months(lo.replace(day=1), 1)
    return tuple(out)


def shifted_mapping(plan, kind, calendars, base=None):
    base = base or time_bounds(plan.time, calendars)
    dimensions = [d for d in plan.dimensions if d.kind == 'time']
    period = getattr(plan.time, 'period', getattr(plan.time, 'first', getattr(plan.time, 'through', None)))
    order = {'day': 0, 'month': 1, 'calendar_quarter': 2, 'fiscal_quarter': 2, 'year': 3, 'fiscal_year': 3}
    grain = min(dimensions, key=lambda d: order[d.grain]).grain if dimensions else getattr(period, 'kind', 'month')
    shift = -12 if kind == 'previous_year' else -3 if grain in ('calendar_quarter', 'fiscal_quarter') else -12 if grain in ('year', 'fiscal_year') else -1

    def move(day, count):
        return day + datetime.timedelta(days=count) if kind == 'previous_period' and grain == 'day' else add_months(day, count)

    other = tuple(move(day, shift) for day in base)
    cal = calendars[time_calendar(plan.time)]
    left, right = periods(base, grain, cal), periods(other, grain, cal)
    mapped = tuple(move(day, -shift) for day in right)

    def keys(day):
        window = day, day + datetime.timedelta(days=1)
        return tuple(periods(window, d.grain, calendars[d.calendar.id])[0] for d in dimensions) if dimensions else (day,)

    targets = tuple(keys(day) for day in mapped)
    if len(left) != len(right) or len(set(targets)) != len(targets) or set(targets) != {keys(day) for day in left}:
        raise ValueError('comparison needs a one-to-one period mapping')
    return other
