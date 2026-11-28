"""Calendar windows and one-to-one comparison mappings without warehouse reads."""
from __future__ import annotations

import calendar
import datetime

from sqlglot import exp

from kge.semantics.query import month_end

GRAIN_ORDER = {'day': 0, 'week': 1, 'month': 2, 'calendar_quarter': 3, 'fiscal_quarter': 3, 'year': 4, 'fiscal_year': 4}
WEEKDAYS = tuple(day.casefold() for day in calendar.day_name)


def week_start(cal):
    """The first ISO weekday is the default; a calendar may declare another start."""
    return WEEKDAYS.index(cal['week_start']) if 'week_start' in cal else 0


def bucket(base, grain, cal):
    """Derive period starts from dates, including declared week starts and fiscal offsets."""
    if grain == 'week':
        anchor = datetime.date.min + datetime.timedelta(days=week_start(cal))
        days = exp.Mod(this=exp.DateDiff(this=exp.cast(base.copy(), 'DATE'),
            expression=exp.cast(exp.Literal.string(anchor.isoformat()), 'DATE'), unit=exp.Var(this='DAY')),
            expression=exp.Literal.number(7))
        return exp.cast(exp.DateSub(this=exp.cast(base, 'DATE'), expression=days, unit=exp.Var(this='DAY')), 'DATE')
    offset = cal['year_start_month'] - 1 if grain in ('fiscal_quarter', 'fiscal_year') else 0
    if offset:
        base = exp.Sub(this=base, expression=exp.Interval(this=exp.Literal.string(str(offset)), unit=exp.Var(this='MONTH')))
    trunc = 'quarter' if grain in ('calendar_quarter', 'fiscal_quarter') else 'year' if grain in ('year', 'fiscal_year') else grain
    result = exp.cast(exp.DateTrunc(unit=exp.Literal.string(trunc), this=base), 'DATE')
    if offset:
        result = exp.Add(this=result, expression=exp.Interval(this=exp.Literal.string(str(offset)), unit=exp.Var(this='MONTH')))
    return result


def moved(day, count, unit):
    return day + datetime.timedelta(days=count * (7 if unit == 'WEEK' else 1)) if unit in ('DAY', 'WEEK') else add_months(day, count)


def add_months(date, count):
    y, m = divmod(date.year * 12 + date.month - 1 + count, 12)
    return datetime.date(y, m + 1, min(date.day, calendar.monthrange(y, m + 1)[1]))


def bounds(period, calendars):
    cal = calendars[period.calendar.id]
    value, kind = period.value, period.kind
    if kind == 'day':
        first = datetime.date.fromisoformat(value)
        return first, first + datetime.timedelta(days=1)
    if kind == 'week':
        if week_start(cal) != 0:
            raise ValueError('ISO week periods require the default week start; use date bounds for another start')
        first = datetime.date.fromisocalendar(int(value[:4]), int(value[-2:]), 1)
        return first, first + datetime.timedelta(days=7)
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
    _, hi = bounds(time.through, calendars)
    anchor = hi - datetime.timedelta(days=1)
    start = calendars[time.calendar.id]['year_start_month']
    return datetime.date(anchor.year - (anchor.month < start), start, 1), hi


def to_date_window(operation, through, calendars, anchor='stated'):
    """The to-date window ('ytd', 'mtd' or 'qtd') that ends with the period `through`, in its calendar."""
    from kge.semantics.plan import Range, Ytd
    if operation == 'ytd':
        return Ytd(kind='ytd', through=through, calendar=through.calendar, anchor=anchor)
    cal = calendars[through.calendar.id]
    _, hi = bounds(through, calendars)
    last = hi - datetime.timedelta(days=1)
    month = last.month
    if operation == 'qtd':
        month -= (last.month - cal['year_start_month']) % 3
    first = datetime.date(last.year - (month < 1), (month - 1) % 12 + 1, 1)
    from kge.semantics.plan import Period
    current = {'anchor': 'current', 'to_date': 'month' if operation == 'mtd' else 'quarter'} if anchor == 'current' else {}
    return Range(kind='range', first=Period(kind='day', value=first.isoformat(), calendar=through.calendar),
                 last=Period(kind='day', value=last.isoformat(), calendar=through.calendar), **current)


def to_date_operation(time):
    """The to-date operation a current-anchored window was read from, or None for a stated window."""
    if getattr(time, 'anchor', 'stated') != 'current':
        return None
    return 'ytd' if time.kind == 'ytd' else {'month': 'mtd', 'quarter': 'qtd'}[time.to_date]


def empty_to_date_reason(time, calendars, rows):
    """The stated refusal for a current to-date window that holds no observation, or '' when it holds one.

    A current to-date window answers whatever observations it holds; one with none would be a figure of empty cells,
    so it is refused instead.
    """
    operation = to_date_operation(time)
    if operation is None:
        return ''
    cells = [cell for row in rows for cell in row.cells]
    if any(cell.value.kind != 'null' or 'missing_observation' not in cell.null_reasons for cell in cells):
        return ''
    first, end = time_bounds(time, calendars)
    last = end - datetime.timedelta(days=1)
    window = {'ytd': 'year', 'mtd': 'month', 'qtd': 'quarter'}[operation] + ' to date'
    return (f'No observations are recorded in the {window}, {first.day} {first:%B %Y} to {last.day} {last:%B %Y}, '
            'so no figure is given. Please name a period with observations.')


def time_calendar(time):
    return time.period.calendar.id if time.kind == 'single' else time.first.calendar.id if time.kind == 'range' else time.calendar.id


def periods(window, grain, cal):
    if window is None:
        return ()
    lo, hi = window
    if grain == 'day':
        return tuple(lo + datetime.timedelta(days=i) for i in range((hi - lo).days))
    if grain == 'week':
        first = lo - datetime.timedelta(days=(lo.weekday() - week_start(cal)) % 7)
        return tuple(first + datetime.timedelta(days=i) for i in range(0, (hi - first).days, 7))
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


def iso_year_shift(day, years):
    """Keep the ISO week and weekday; an absent week 53 has no annual counterpart."""
    year, week, weekday = day.isocalendar()
    return datetime.date.fromisocalendar(year + years, week, weekday)


def shifted_mapping(plan, kind, calendars, base=None):
    base = base or time_bounds(plan.time, calendars)
    dimensions = [d for d in plan.dimensions if d.kind == 'time']
    period = getattr(plan.time, 'period', getattr(plan.time, 'first', getattr(plan.time, 'through', None)))
    order = GRAIN_ORDER
    grain = min(dimensions, key=lambda d: order[d.grain]).grain if dimensions else getattr(period, 'kind', 'month')
    shift = -12 if kind == 'previous_year' else -7 if grain == 'week' else -3 if grain in ('calendar_quarter', 'fiscal_quarter') else -12 if grain in ('year', 'fiscal_year') else -1

    def move(day, count):
        if kind == 'previous_year' and grain == 'week':
            return iso_year_shift(day, count // 12)
        return day + datetime.timedelta(days=count) if kind == 'previous_period' and grain in ('day', 'week') else add_months(day, count)

    other = (move(base[0], shift), move(base[1] - datetime.timedelta(days=1), shift) + datetime.timedelta(days=1)) if grain == 'week' and kind == 'previous_year' else tuple(move(day, shift) for day in base)
    cal = calendars[min(dimensions, key=lambda d: order[d.grain]).calendar.id if dimensions else time_calendar(plan.time)]
    left, right = periods(base, grain, cal), periods(other, grain, cal)
    mapped = tuple(move(day, -shift) for day in right)

    def keys(day):
        window = day, day + datetime.timedelta(days=1)
        return tuple(periods(window, d.grain, calendars[d.calendar.id])[0] for d in dimensions) if dimensions else (day,)

    targets = tuple(keys(day) for day in mapped)
    if len(left) != len(right) or len(set(targets)) != len(targets) or set(targets) != {keys(day) for day in left}:
        raise ValueError('comparison needs a one-to-one period mapping')
    return other


def fiscal_quarter(year: int, quarter: int, year_start_month: int) -> tuple[int, int]:
    """Calendar quarter `quarter` of `year` as (fiscal year, fiscal quarter) on a fiscal year that starts in
    year_start_month, the first month of a calendar quarter (1, 4, 7 or 10). A fiscal year is named for the calendar
    year it ends in; one that starts in January is the calendar year."""
    month = 3 * quarter - 2
    start = year if month >= year_start_month else year - 1
    return start + (year_start_month > 1), (month - year_start_month) % 12 // 3 + 1


def quarter_label(cq: str, calendar: dict) -> str:
    """A calendar quarter (2025-Q2) as a calendar labels its quarters: its quarter_label ({yyyy}-Q{n}, FY{yy}-Q{n})
    filled in with the fiscal year and quarter on its year_start_month."""
    fy, n = fiscal_quarter(int(cq[:4]), int(cq[-1]), calendar["year_start_month"])
    return calendar["quarter_label"].format(yyyy=fy, yy=f"{fy % 100:02d}", n=n)
