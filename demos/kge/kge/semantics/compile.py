"""Relational compilation of checked plans, using only resolved declarations and ASTs."""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, replace

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope

from kge.pipeline.trace import graph_read
from kge.compiler.bands import band_expression
from kge.pipeline.textmatch import TEXT_MATCHES, text_match
from kge.ports import AUTHORED
from kge.pipeline.sqlguard import check, Allowlist, DataAllowlist, CheckedAllowlist, canonical, conjuncts, resolved, warehouse_names, table_name, scope_conditions, physical_scope_name, projection_signature, checked_output_shape
from kge.pipeline.sqlguard import join_contracts
from kge.semantics.plan import Period, ResultColumn, predicate_nodes
from kge.semantics.planvocabulary import aggregate_expression, aggregation_of, output_spec
from kge.semantics.filtergrammar import preserved_paths
from kge.semantics.plancheck import CheckedPlan, phase, membership_binding, comparison_mapping
from kge.semantics.planperiods import GRAIN_ORDER, add_months, bucket, moved, periods, quarter_label, release_months, time_bounds, shifted_mapping, to_date_operation, to_date_window
from kge.semantics.query import Asked, Plan, bare, holds, read_join


def parse(sql):
    return sqlglot.parse_one(sql, read=AUTHORED)


def column(name, table=None):
    return exp.column(name, table=table, quoted=True)


def label(node, name):
    return exp.alias_(node, name, quoted=True)


def literal(value):
    if value.kind == 'null':
        return exp.Null()
    if value.kind == 'boolean':
        return exp.Boolean(this=value.value)
    if value.kind in ('integer', 'decimal'):
        return exp.Literal.number(str(value.value))
    if value.kind == 'date':
        return exp.cast(exp.Literal.string(value.value), 'DATE')
    return exp.Literal.string(value.value)


def date(value):
    return exp.cast(exp.Literal.string(value.isoformat()), 'DATE')


def period_date(period, binding, vocabulary):
    if not binding or binding['kind'] not in ('fiscal_quarter', 'calendar_quarter'):
        return period.copy()
    fiscal = binding['kind'] == 'fiscal_quarter'
    cal = vocabulary.calendars[binding['calendar']]
    template = cal['quarter_label'] if fiscal else '{yyyy}-Q{n}'
    year_token = '{yyyy}' if '{yyyy}' in template else '{yy}'
    def pattern(capture):
        value = re.escape(template)
        for token, digits in (('{yyyy}', '[0-9]{4}'), ('{yy}', '[0-9]{2}'), ('{n}', '[1-4]')):
            value = value.replace(re.escape(token), '(' + digits + ')' if token == capture else digits)
        return '^' + value + '$'
    def extracted(token):
        return exp.cast(exp.Nullif(this=exp.RegexpExtract(this=period.copy(), expression=exp.Literal.string(pattern(token)),
            group=exp.Literal.number(1)), expression=exp.Literal.string('')), 'INT')
    year = extracted(year_token)
    if year_token == '{yy}':
        year = exp.Add(this=year, expression=exp.Literal.number(2000))
    start = cal['year_start_month'] if fiscal else 1
    if start > 1:
        year = exp.Sub(this=year, expression=exp.Literal.number(1))
    first = exp.DateFromParts(year=year, month=exp.Literal.number(start), day=exp.Literal.number(1))
    months = exp.Mul(this=exp.Paren(this=exp.Sub(this=extracted('{n}'), expression=exp.Literal.number(1))), expression=exp.Literal.number(3))
    return exp.cast(exp.Add(this=first, expression=exp.Interval(this=months, unit=exp.Var(this='MONTH'))), 'DATE')


def conjunction(nodes):
    return exp.and_(*nodes) if nodes else exp.true()


@dataclass(frozen=True)
class Statement:
    sql: str
    allow: Allowlist
    units: tuple[str, ...]
    outside_calendar: bool = False


@dataclass(frozen=True)
class AggregateSpec:
    id: str
    measure: object
    time: object
    mode: str
    shift: int = 0
    remove_dimensions: tuple[str, ...] = ()
    remove_filters: tuple[str, ...] = ()
    answer_scope: bool = False


@dataclass(frozen=True)
class ReleaseNeed:
    tag: str
    policy: object
    unit: str
    window: tuple | None
    latest: bool
    offset: int = 0
    offset_unit: str = 'MONTH'
    current: bool = False


@dataclass(frozen=True)
class Compiled:
    answer: Statement | None
    release: Statement | None
    needs: tuple[ReleaseNeed, ...]
    columns: tuple[ResultColumn, ...]
    checked: CheckedPlan


def branches(plan):
    out = [AggregateSpec(m.id, m, plan.time, m.mode) for m in plan.measures]
    by_id = {m.id: m for m in plan.measures}
    for c in plan.comparisons:
        if c.kind == 'contexts':
            out.extend((AggregateSpec(c.id + '/base', by_id[c.left], plan.time, by_id[c.left].mode),
                        AggregateSpec(c.id + '/other', by_id[c.right], plan.time, by_id[c.right].mode)))
            continue
        for i in c.inputs:
            m = by_id[i]
            out.append(AggregateSpec(f'{c.id}/{i}/base', m, plan.time, c.base if c.kind == 'time_mode' else m.mode))
            out.append(AggregateSpec(f'{c.id}/{i}/other', m, c.other if c.kind == 'explicit_period' else plan.time,
                              c.other if c.kind == 'time_mode' else m.mode,
                              -12 if c.kind == 'previous_year' else -1 if c.kind == 'previous_period' else 0))
    for d in plan.derived:
        if d.kind == 'share':
            source = next((b for b in out if b.id == d.input), None)
            if source is None:
                raise ValueError('share requires a base aggregate')
            out.append(replace(source, id=d.id + '/denominator', remove_dimensions=d.remove_dimensions,
                               remove_filters=d.remove_filters))
    return out


def comparison_aliases(plan):
    """Equivalent comparison operands share one displayed output and its totals."""
    selected = branches(plan)
    originals = {branch.id: branch for branch in selected if branch.id in {m.id for m in plan.measures}}
    aliases = {}
    for branch in selected:
        original = originals.get(branch.measure.id)
        if (branch.id not in originals and original is not None and not branch.shift
                and branch.time == original.time and branch.mode == original.mode
                and not branch.remove_dimensions and not branch.remove_filters
                and not branch.id.endswith('/denominator')):
            aliases[branch.id] = original.id
    return aliases


def policy_for(vocab, ctx):
    return next((vocab.releases[j.target] for j in ctx.joins if j.required and j.target in vocab.releases), None)


def comparison_grain(aggregate, plan):
    time_dims = [d for d in plan.dimensions if d.kind == 'time']
    period = getattr(aggregate.time, 'period', getattr(aggregate.time, 'first', getattr(aggregate.time, 'through', None)))
    order = GRAIN_ORDER
    return min(time_dims, key=lambda d: order[d.grain]).grain if time_dims else getattr(period, 'kind', 'month')


def shift_size(aggregate, plan):
    if aggregate.shift != -1:
        return aggregate.shift
    grain = comparison_grain(aggregate, plan)
    return -7 if grain == 'week' else -3 if grain in ('calendar_quarter', 'fiscal_quarter') else -12 if grain in ('year', 'fiscal_year') else -1


def shift_unit(aggregate, plan):
    if aggregate.shift == -12 and comparison_grain(aggregate, plan) == 'week':
        return 'ISO_YEAR'
    return 'DAY' if aggregate.shift == -1 and comparison_grain(aggregate, plan) in ('day', 'week') else 'MONTH'


def aggregate_window(aggregate, vocab, plan, latest=None):
    if aggregate.time.kind == 'latest_released':
        if latest is None:
            return None
        first = latest.replace(day=1)
        window = first, add_months(first, 1)
    else:
        window = time_bounds(aggregate.time, vocab.calendars)
    shift = shift_size(aggregate, plan)
    if window is not None and shift:
        asked_start = window[0]
        if aggregate.shift == -12 and comparison_grain(aggregate, plan) == 'week':
            window = shifted_mapping(plan, 'previous_year', vocab.calendars, window)
        else:
            window = tuple(moved(d, shift, shift_unit(aggregate, plan)) for d in window)
        if aggregate.shift == -1 and plan.time.kind in ('range', 'ytd') and any(d.kind == 'time' for d in plan.dimensions):
            window = (max(asked_start, window[0]), window[1])
    return window


def requirements(checked):
    plan, vocab = checked.plan, checked.vocabulary
    needs = []
    for aggregate in branches(plan):
        contexts = {aggregate.measure.context.id}
        for pred in (*plan.filters, *aggregate.measure.filters):
            if pred.id not in aggregate.remove_filters:
                contexts.update(n.context.id for n in predicate_nodes(pred) if n.kind == 'membership')
        for identifier in sorted(contexts):
            ctx = vocab.contexts[identifier]
            policy = policy_for(vocab, ctx)
            if policy is None or aggregate.time.kind == 'all_available':
                continue
            if (window := aggregate_window(aggregate, vocab, plan)) is not None and window[0] >= window[1]:
                continue
            need = ReleaseNeed('', policy, ctx.unit, aggregate_window(aggregate, vocab, plan), aggregate.time.kind == 'latest_released',
                               shift_size(aggregate, plan) if aggregate.time.kind == 'latest_released' else 0, shift_unit(aggregate, plan),
                               to_date_operation(aggregate.time) is not None and not shift_size(aggregate, plan))
            if not any(replace(n, tag='') == need for n in needs):
                needs.append(replace(need, tag=str(len(needs))))
    return tuple(needs)


def union(queries, distinct=False):
    result = queries[0]
    for query in queries[1:]:
        result = exp.Union(this=result, expression=query, distinct=distinct)
    return result


def additive_outputs(checked):
    """Only declared additive base observations can be summed across a breakdown."""
    if not checked.plan.dimensions:
        return ()
    def additive(branch):
        context = checked.vocabulary.contexts[branch.measure.context.id]
        expression = parse(aggregate_expression(context, aggregation_of(branch.measure, context)))
        ratio = any(d.find_ancestor(exp.AggFunc) is None and d.expression.find(exp.Column, exp.AggFunc) is not None
                    for d in expression.find_all(exp.Div))
        return (branch.measure.kind == 'measure' and output_spec(branch.measure, context, checked.vocabulary).additive
                and aggregation_of(branch.measure, context) in ('sum', 'count')
                and not ratio and not any(expression.find_all(exp.Distinct, exp.Avg)))
    return tuple(b.id for b in branches(checked.plan) if not b.id.endswith('/denominator') and additive(b))


def total_scopes(plan):
    if not plan.dimensions:
        return ()
    time = tuple(d.id for d in plan.dimensions if d.kind == 'time')
    return (time, ()) if time and len(time) < len(plan.dimensions) else ((),)


def total_outputs(checked):
    outputs = tuple(dict.fromkeys((*additive_outputs(checked), *(b.id for b in branches(checked.plan)
        if checked.plan.dimensions and b.measure.kind == 'distinct_count' and not b.id.endswith('/denominator')))))
    return (*outputs, *(d.id + '/denominator' for d in checked.plan.derived if d.kind == 'share')) if outputs else ()


def warehouse_totals(checked):
    plan = checked.plan
    return bool(total_outputs(checked) and (plan.limit is not None or len(total_scopes(plan)) > 1
        or any(m.kind == 'distinct_count' for m in plan.measures) or any(d.kind == 'share' for d in plan.derived)))


def release_statement(checked, needs):
    return release_calendar_statement(checked.vocabulary.tables, needs, checked.units)


def release_calendar_statement(schema, needs, units):
    if not needs:
        return None
    queries, tables, cols = [], set(), {}
    attached = len(units) > 1
    for need in needs:
        p = need.policy
        table = p['table'] if attached else bare(p['table'])
        tables.add(table)
        cols.setdefault(table, set()).update(p[k] for k in ('period', 'released', 'release_date'))
        alias = 'r'
        released = parse(holds(f'{alias}.{p["released"]}', schema[p['table']][p['released']]))
        q = exp.select(label(exp.Literal.string(need.tag), 'requirement'),
                       label(column(p['period'], alias), 'period'), label(released, 'released'),
                       label(column(p['release_date'], alias), 'release_date')).from_(exp.to_table(table).as_(alias))
        if not need.latest:
            lo, hi = need.window
            months = release_months((lo, hi))
            q = q.where(exp.or_(exp.Between(this=column(p['period'], alias), low=date(months[0]), high=date(months[-1])), released.copy()))
        queries.append(q)
    allow = Allowlist(' '.join(units), tables, cols, schema=cols, attached=attached)
    sql = union(queries).sql(AUTHORED)
    return Statement(sql, allow, units)


def latest_released_offer(needs, rows):
    released = {n.tag: set() for n in needs}
    for tag, period, ready, _ in rows:
        if tag in released and ready:
            released[tag].add(datetime.date.fromisoformat(str(period)[:10]))
    common = set.intersection(*released.values()) if released else set()
    return ('The latest released month is ' + max(common).strftime('%B %Y')
            + '. Would you like that month instead?') if common else ''


def reanchored(checked, needs, rows):
    """A current to-date window ends at the latest period released for every context it reads, on or before the calendar's current period.

    A window whose last month is released, or with no released month on or before it, is unchanged: the release check then answers or refuses it as stated.
    """
    operation = to_date_operation(checked.plan.time)
    current = [n for n in needs if n.current]
    if operation is None or not current:
        return checked
    released = [{datetime.date.fromisoformat(str(period)[:10]) for tag, period, ready, _ in rows if tag == n.tag and ready} for n in current]
    common = set.intersection(*released)
    _, end = time_bounds(checked.plan.time, checked.vocabulary.calendars)
    last_month = (end - datetime.timedelta(days=1)).replace(day=1)
    earlier = [p for p in common if p.replace(day=1) <= last_month]
    if last_month in {p.replace(day=1) for p in common} or not earlier:
        return checked
    cutoff = add_months(max(earlier).replace(day=1), 1) - datetime.timedelta(days=1)
    plan_time = checked.plan.time
    calendar = (plan_time.calendar if plan_time.kind == 'ytd' else plan_time.last.calendar)
    through = Period(kind='day', value=cutoff.isoformat(), calendar=calendar)
    return checked.retimed(to_date_window(operation, through, checked.vocabulary.calendars, 'current'))


def resolve_releases(compiled, rows):
    held = {n.tag: {} for n in compiled.needs}
    dates = {}
    for tag, period, released, released_on in rows:
        if tag in held:
            day = datetime.date.fromisoformat(str(period)[:10])
            held[tag][day] = bool(released)
            dates[tag, day] = released_on
    latest = None
    if any(n.latest for n in compiled.needs):
        sets = [{p for p, ok in held[n.tag].items() if ok} for n in compiled.needs if n.latest]
        common = set.intersection(*sets)
        if not common:
            raise ValueError('no common released period')
        latest = max(common)
    problems = []
    for need in compiled.needs:
        if need.latest:
            first = latest.replace(day=1)
            window = (first, add_months(first, 1))
            if need.offset_unit == 'ISO_YEAR':
                window = shifted_mapping(compiled.checked.plan, 'previous_year', compiled.checked.vocabulary.calendars, window)
            else:
                window = tuple(moved(d, need.offset, need.offset_unit) for d in window)
        else:
            window = need.window
        wanted = release_months(window)
        missing = [p.strftime('%B %Y') for p in wanted if p not in held[need.tag]]
        if missing:
            problems.append(', '.join(missing) + (' is' if len(missing) == 1 else ' are') + ' not booked in the release calendar')
        for period in wanted:
            if period in held[need.tag] and not held[need.tag][period]:
                on = dates[need.tag, period]
                problems.append(period.strftime('%B %Y') + ' is not yet released' + (f': its figures are released on {str(on)[:10]}' if on else ''))
    if problems:
        if offer := latest_released_offer(compiled.needs, rows):
            problems.append(offer)
        raise ValueError('; '.join(dict.fromkeys(problems)))
    return latest


class Builder:
    def __init__(self, checked, latest):
        if not isinstance(checked, CheckedPlan):
            raise TypeError('compiler requires a checked plan')
        self.checked, self.plan, self.vocab = checked, checked.plan, checked.vocabulary
        self.latest = latest
        self.memberships = 0
        self.checking_policy = False
        self.ctes, self.allows, self.requirements, self.selections = [], [], {}, {}
        self.date_series = {}
        self.calendar_scopes = set()
        self.outside_calendar = None
        self.outputs, self.reasons, self.columns, self.alignments = {}, {}, [], {}
        self.dimension_keys = {d.id: 'd' + str(i) for i, d in enumerate(self.plan.dimensions)}
        self.keys = ['k0', *(['panel'] if self.plan.layout == 'separate' else []), *self.dimension_keys.values()]

    def cte(self, name, query):
        self.ctes.append(exp.CTE(this=query, alias=exp.TableAlias(this=exp.to_identifier(name))))

    def dimension_order(self, identifier, source):
        value = column(self.dimension_keys[identifier], source)
        dim = next(d for d in self.plan.dimensions if d.id == identifier)
        if dim.kind not in ('time', 'band') and any(r['kind'] == 'code_name_pair'
                and r['params']['code'] == dim.bindings[0].column.id and r['params']['name'] in self.vocab.entries
                for r in getattr(self.vocab.semantics.d, 'VOCABULARY', ())):
            table, name = dim.bindings[0].column.id.rsplit('.', 1)
            value = exp.cast(exp.JSONExtractScalar(this=value, expression=exp.Literal.string('$.code')), self.vocab.tables[table][name])
        return value

    def constant_keys(self):
        contexts = tuple(dict.fromkeys(m.context.id for m in self.plan.measures)) if self.plan.layout == 'separate' else (None,)
        return union([exp.select(label(exp.Literal.number(1), 'k0'),
                                 *([label(exp.Literal.string(ctx), 'panel')] if ctx is not None else [])) for ctx in contexts])

    def require_policy(self, p, aggregate, extra=(), selectors=(), outer=None, condition=None, row_predicates=(), membership_start=0):
        """Derive gate obligations from declarations, independently of the emitted SQL AST."""
        if self.checking_policy:
            return
        ctx = p.context
        aliases = {p.fact: ctx.fact, **p.aliases}
        for join in ctx.joins:
            if join.required and not join.through:
                aliases.setdefault(join.alias or bare(join.target), join.target)
        names = {**(outer or {}), **{t: a for a, t in aliases.items()}, **{a: a for a in aliases}}
        predicates = [parse(p.filter(f, names)) for f in ctx.filters]
        predicates.extend(parse(p.selector(s, names)) for s in (*ctx.selectors, *selectors)
                          if outer is not None or s.table.split('.')[0] == ctx.unit)
        left_joins = {}
        typed_joins = {}
        for j in ctx.joins:
            if j.through or not (j.required or j.id in p.path):
                continue
            on = p.render(j.predicate, names)
            conditions = [parse(on)]
            alias = names.get(j.alias or j.target)
            version = p.version(alias, j.target, on)
            if version:
                conditions.append(parse(version))
            if j.typed:
                typed_joins[normalize_identifiers(exp.to_identifier(alias), dialect=AUTHORED).name] = {
                    'type': j.type, 'condition': frozenset(canonical(c, {}) for condition in conditions for c in conjuncts(condition))}
            if j.type != 'inner' or (not j.typed and j.id in p._preserve_joins and not j.required):
                if j.type == 'left' or not j.typed:
                    left_joins[normalize_identifiers(exp.to_identifier(alias), dialect=AUTHORED).name] = frozenset(
                        canonical(c, {}) for condition in conditions for c in conjuncts(condition))
            else:
                predicates.extend(conditions)
        version = p.version(p.fact, ctx.fact, condition)
        if version:
            predicates.append(parse(version))
        if condition is not None:
            targets = {j.target for j in ctx.joins if not j.through}
            for alias, table in aliases.items():
                if table == ctx.fact or table in targets:
                    continue
                version = p.version(alias, table, condition)
                if version:
                    predicates.append(parse(version))
        binding = None if outer is not None and ctx.id.startswith('context:count:') else self.vocab.periods.get(ctx.fact)
        r = policy_for(self.vocab, ctx)
        period = column(binding['column'], p.fact) if binding else column(r['period'], p.release['alias']) if r else None
        window = aggregate_window(aggregate, self.vocab, self.plan, self.latest)
        if window is not None and period is not None:
            if binding and binding['kind'] in ('fiscal_quarter', 'calendar_quarter'):
                cal = self.vocab.calendars[binding['calendar']]
                starts = periods(window, binding['kind'], cal)
                labels = [quarter_label(f'{d.year}-Q{(d.month - 1) // 3 + 1}', cal) if binding['kind'] == 'fiscal_quarter' else f'{d.year}-Q{(d.month - 1) // 3 + 1}' for d in starts]
                predicates.append(exp.In(this=period, expressions=[exp.Literal.string(v) for v in labels]))
            else:
                predicates.extend((exp.GTE(this=period.copy(), expression=date(window[0])), exp.LT(this=period, expression=date(window[1]))))
                part_info = (getattr(self.vocab.semantics, 'partitioned', None) or {}).get(ctx.fact)
                if part_info is not None and (not binding or binding['column'] != part_info['column']):
                    part_col = column(part_info['column'], p.fact)
                    predicates.extend((exp.GTE(this=part_col.copy(), expression=date(window[0])), exp.LT(this=part_col, expression=date(window[1]))))
        predicates.extend(extra)
        expected = Builder(self.checked, self.latest)
        expected.checking_policy = True
        expected.memberships = membership_start
        policy_plan = Plan(self.vocab.semantics, ctx, p.asked)
        policy_plan.units, policy_plan.fact = list(p.units), p.fact
        policy_plan.aliases, policy_plan.path = dict(aliases), list(p.path)
        policy_plan.release = dict(p.release) if p.release else None
        predicates.extend(expected.predicate(node, policy_plan, aggregate) for node in row_predicates)
        refs = dict(aliases)
        refs.update({a: t for t, a in (outer or {}).items() if t in self.vocab.tables})
        q = exp.select(exp.Literal.number(1))
        for alias, table in refs.items():
            source = parse('SELECT 1 FROM ' + p.ref(table, alias)).args['from_'].this.copy()
            q = q.join(source, join_type='CROSS') if q.args.get('from_') else q.from_(source)
        if predicates:
            q = q.where(conjunction(predicates))
        allow = self.vocab.semantics.allowlist(p, data_read=True)
        normalized_left = {}
        for alias, conditions in left_joins.items():
            expected_join = q.copy()
            expected_join.set('where', exp.Where(this=conjunction([parse(condition) for condition in conditions])))
            expected_join = resolved(warehouse_names(expected_join, allow), allow)
            normalized_left[alias] = frozenset(canonical(c, {}) for c in conjuncts(expected_join.args['where'].this))
        normalized_typed = {}
        for alias, contract in typed_joins.items():
            expected_join = q.copy()
            expected_join.set('where', exp.Where(this=conjunction([parse(c) for c in contract['condition']])))
            expected_join = resolved(warehouse_names(expected_join, allow), allow)
            normalized_typed[alias] = {'type': contract['type'], 'condition': frozenset(
                canonical(c, {}) for c in conjuncts(expected_join.args['where'].this))}
        scopes = list(traverse_scope(resolved(warehouse_names(q, allow), allow)))
        for scope in scopes:
            tables = {a: table_name(t, allow) for a, t in scope.sources.items() if isinstance(t, exp.Table)}
            if scope is scopes[-1]:
                tables = {normalize_identifiers(exp.to_identifier(a), dialect=AUTHORED).name: t if p.attached else bare(t)
                          for a, t in aliases.items()}
            self.requirements[str(len(self.requirements))] = {
                'scope': self.current_branch, 'tables': tables,
                'predicates': {canonical(c, {}) for c in scope_conditions(scope)},
                'left_joins': normalized_left if scope is scopes[-1] else {
                    join.this.alias_or_name: frozenset(canonical(c, {}) for c in conjuncts(join.args.get('on')))
                    for join in scope.expression.args.get('joins') or [] if join.side == 'LEFT' and join.this.alias_or_name in tables},
                **({'typed_joins': normalized_typed} if scope is scopes[-1] and normalized_typed else {})}

    def path(self, p, path):
        alias = p.fact
        for n in path:
            j = next(j for j in p.context.joins if j.id == n.id)
            if j.id in p.path:
                alias = next(a for a, t in p.aliases.items() if t == j.target and (not j.alias or a == j.alias))
            else:
                alias = p.add_join(j)
        return alias

    def predicate(self, node, p=None, aggregate=None):
        if node.kind in ('all', 'any'):
            children = [self.predicate(c, p, aggregate) for c in node.children]
            return exp.and_(*children) if node.kind == 'all' else exp.or_(*children)
        if node.kind == 'not':
            return exp.Not(this=self.predicate(node.child, p, aggregate))
        if node.kind == 'membership':
            return self.membership(node, p, aggregate)
        if node.left.kind == 'output':
            left = column(self.outputs[node.left.id])
        else:
            entry = self.vocab.entries[node.left.name.id]
            table, name = entry.column.rsplit('.', 1)
            alias = self.path(p, node.left.path)
            left = column(name, alias)
            p.applied.append((table, name))
        if getattr(node, 'magnitude', False):
            left = exp.Abs(this=left)
        if node.kind == 'in':
            return exp.In(this=left, expressions=[literal(v) for v in node.values])
        if node.kind == 'null':
            test = exp.Is(this=left, expression=exp.Null())
            return test if node.is_null else exp.Not(this=test)
        if node.op in TEXT_MATCHES:
            return text_match(left, node.op, node.value.value)
        cls = {'eq': exp.EQ, 'ne': exp.NEQ, 'lt': exp.LT, 'le': exp.LTE, 'gt': exp.GT, 'ge': exp.GTE}[node.op]
        return cls(this=left, expression=literal(node.value))

    def membership(self, node, outer, aggregate):
        ctx, relation = membership_binding(node, self.vocab, outer.context)
        graph_read("entity", (node.entity.id, self.vocab.entities[node.entity.id], "", node.entity.id))
        inner = Plan(self.vocab.semantics, ctx, outer.asked)
        inner._preserve_joins = preserved_paths(node.filters)
        inner.units = outer.units
        inner.fact = inner.name(ctx.fact, 'member_' + str(self.memberships))
        self.memberships += 1
        policy = policy_for(self.vocab, ctx)
        inner.release = {**policy, 'alias': bare(policy['table'])} if policy else outer.release
        near = outer.reach(relation.source)
        through = inner.name(relation.through)
        names = {**outer.outer(), relation.source: near, relation.through: through, ctx.fact: inner.fact}
        condition = parse(inner.render(relation.condition, names))
        joined, correlated = [], []
        for predicate in conjuncts(condition):
            (joined if near not in {c.table for c in predicate.find_all(exp.Column)} else correlated).append(predicate)
        inner.where.extend(c.sql(AUTHORED) for c in correlated)
        for join in ctx.joins:
            if join.required and not join.through:
                alias = inner.add_join(join)
                if policy is not None and join.target == policy['table']:
                    inner.release['alias'] = alias
        for alias, table in ((through, relation.through), (inner.fact, ctx.fact)):
            version = inner.version(alias, table, condition.sql(AUTHORED))
            if version:
                inner.where.append(version)
        inner.where.extend(inner.filter(f, inner.outer()) for f in ctx.filters)
        inner.where.extend(inner.selector(s, {**outer.outer(), **inner.outer()}) for s in (*ctx.selectors, *outer.across_selectors(ctx.fact)))
        membership_start = self.memberships
        inner.where.extend(self.predicate(p, inner, aggregate).sql(AUTHORED) for p in node.filters)
        binding = None if ctx.id.startswith('context:count:') else self.vocab.periods.get(ctx.fact)
        window = aggregate_window(aggregate, self.vocab, self.plan, self.latest)
        period = column(binding['column'], inner.fact) if binding else column(policy['period'], inner.release['alias']) if policy else None
        if period is not None and window is not None:
            inner.where.append(exp.and_(exp.GTE(this=period.copy(), expression=date(window[0])), exp.LT(this=period.copy(), expression=date(window[1]))).sql(AUTHORED))
            if binding:
                inner.applied.append((ctx.fact, binding['column']))
        q = exp.select(exp.Literal.number(1)).from_(parse('SELECT 1 FROM ' + inner.ref(relation.through, through)).args['from_'].this.copy())
        q = q.join(parse('SELECT 1 FROM ' + inner.ref(ctx.fact, inner.fact)).args['from_'].this.copy(), on=conjunction(joined))
        for join in inner.joins:
            q.append('joins', parse('SELECT 1 FROM ' + inner.ref(ctx.fact, inner.fact) + ' ' + join).args['joins'][0])
        q = q.where(conjunction([parse(w) for w in inner.where]))
        read_join(self.vocab.semantics, outer.context.id, relation)
        outer.across.append({'declared': relation})
        inner.across.append({'declared': relation})
        self.require_policy(inner, aggregate, extra=conjuncts(condition), selectors=outer.across_selectors(ctx.fact),
                            outer=outer.outer(), condition=condition.sql(AUTHORED), row_predicates=node.filters, membership_start=membership_start)
        self.allows.append(self.vocab.semantics.allowlist(inner, data_read=True))
        return exp.Exists(this=q)

    def aggregate(self, aggregate, index):
        """Removing a grouping retains its joins and the breakdown's row population."""
        m, vocab = aggregate.measure, self.vocab
        ctx = vocab.contexts[m.context.id]
        snapshot = m.kind == 'measure' and ctx.aggregation != 'count' and not vocab.measures[ctx.measure].additive
        self.current_branch = ('observations' if snapshot else 'r' if aggregate.answer_scope else 'b') + str(index)
        membership_start = self.memberships
        p = Plan(vocab.semantics, ctx, Asked(time_mode=aggregate.mode))
        p._preserve_joins = preserved_paths([predicate for predicate in (*self.plan.filters, *m.filters)
                                          if predicate.id not in aggregate.remove_filters])
        p.units = list(self.checked.units)
        p.fact = p.name(ctx.fact)
        r = policy_for(vocab, ctx)
        if r is not None:
            p.release = {**r, 'alias': bare(r['table'])}
        for j in ctx.joins:
            if j.required and not j.through:
                alias = p.add_join(j)
                if r is not None and j.target == r['table']:
                    p.release['alias'] = alias
        version = p.version(p.fact, ctx.fact)
        if version:
            p.where.append(version)
        p.where.extend(p.filter(f, p.outer()) for f in ctx.filters)
        p.where.extend(p.selector(s, p.outer()) for s in ctx.selectors if s.table.split('.')[0] == ctx.unit)
        for pred in (*self.plan.filters, *m.filters):
            if phase(pred) == 'row' and pred.id not in aggregate.remove_filters:
                p.where.append(self.predicate(pred, p, aggregate).sql(AUTHORED))
        binding = vocab.periods.get(ctx.fact)
        period = column(binding['column'], p.fact) if binding else column(r['period'], p.release['alias']) if r else None
        if binding:
            p.applied.append((ctx.fact, binding['column']))
        window = aggregate_window(aggregate, vocab, self.plan, self.latest)
        missing_grain = False
        if window and period is not None:
            if binding and binding['kind'] in ('fiscal_quarter', 'calendar_quarter'):
                cal = vocab.calendars[binding['calendar']]
                grain = binding['kind']
                starts = periods(window, grain, cal)
                if not starts or starts[0] != window[0] or add_months(starts[-1], 3) != window[1]:
                    independent_parts = (self.plan.layout == 'aligned' and len(self.plan.measures) > 1 and not self.plan.dimensions
                                         and not self.plan.comparisons and not self.plan.derived
                                         and len({vocab.contexts[m.context.id].unit for m in self.plan.measures}) == len(self.plan.measures)
                                         and len({vocab.contexts[m.context.id].term for m in self.plan.measures}) == 1)
                    if not independent_parts:
                        raise ValueError('requested period is finer than the declared quarter observations')
                    missing_grain = True
                    p.where.append('FALSE')
                    starts = starts or [window[0]]
                labels = [quarter_label(f'{d.year}-Q{(d.month - 1) // 3 + 1}', cal) if grain == 'fiscal_quarter'
                          else f'{d.year}-Q{(d.month - 1) // 3 + 1}' for d in starts]
                p.where.append(exp.In(this=period.copy(), expressions=[exp.Literal.string(v) for v in labels]).sql(AUTHORED))
            else:
                p.where.append(exp.and_(exp.GTE(this=period.copy(), expression=date(window[0])),
                                        exp.LT(this=period.copy(), expression=date(window[1]))).sql(AUTHORED))
                part_info = (getattr(vocab.semantics, 'partitioned', None) or {}).get(ctx.fact)
                if part_info is not None and (not binding or binding['column'] != part_info['column']):
                    part_col = column(part_info['column'], p.fact)
                    p.where.append(exp.and_(exp.GTE(this=part_col.copy(), expression=date(window[0])),
                                            exp.LT(this=part_col.copy(), expression=date(window[1]))).sql(AUTHORED))
                    p.applied.append((ctx.fact, part_info['column']))
        dimensions = [label(exp.Literal.number(1), 'k0')]
        if self.plan.layout == 'separate':
            dimensions.append(label(exp.Literal.string(ctx.id), 'panel'))
        groups, scope_matches = [], []
        for d in self.plan.dimensions:
            pair = None
            if d.kind == 'time':
                grain = d.grain
                cal = vocab.calendars[d.calendar.id]
                base = period_date(period, binding, vocab)
                shift = shift_size(aggregate, self.plan)
                if shift and shift_unit(aggregate, self.plan) != 'ISO_YEAR':
                    base = exp.Sub(this=base, expression=exp.Interval(this=exp.Literal.string(str(shift)), unit=exp.Var(this=shift_unit(aggregate, self.plan))))
                if aggregate.shift == -12 and comparison_grain(aggregate, self.plan) == 'week':
                    asked = aggregate_window(AggregateSpec('', m, self.plan.time, m.mode), vocab, self.plan, self.latest)
                    pairs = zip(periods(window, 'week', cal), periods(asked, 'week', cal), strict=True)
                    base = exp.Case(ifs=[exp.If(this=exp.and_(exp.GTE(this=base.copy(), expression=date(source)),
                        exp.LT(this=base.copy(), expression=date(source + datetime.timedelta(days=7)))),
                        true=exp.Add(this=base.copy(), expression=exp.Interval(this=exp.Literal.string(str((target - source).days)),
                            unit=exp.Var(this='DAY')))) for source, target in pairs], default=exp.Null())
                expr = bucket(base, grain, cal)
                comparison = next((c for c in self.plan.comparisons if c.kind == 'explicit_period' and aggregate.id.startswith(c.id + '/') and aggregate.id.endswith('/other')), None)
                if comparison is not None:
                    pairs = comparison_mapping(self.plan, comparison, vocab)[d.id]
                    expr = exp.Case(ifs=[exp.If(this=exp.EQ(this=expr.copy(), expression=date(source)), true=date(target)) for source, target in pairs], default=exp.Null())
            else:
                bound = next((b for b in d.bindings if b.context.id == ctx.id), None)
                if bound is None:
                    if self.plan.layout == 'separate':
                        dimensions.append(label(exp.Null(), self.dimension_keys[d.id]))
                        continue
                    raise ValueError('missing dimension binding')
                entry = vocab.entries[bound.column.id]
                table, name = entry.column.rsplit('.', 1)
                expr = column(name, self.path(p, bound.path))
                if d.kind == 'band':
                    expr = band_expression(expr, vocab.entries[d.band.id].value['ranges'])
                for identifier, entity in vocab.entities.items():
                    if entity['table'] == table and name in entity['key']:
                        graph_read("entity", (identifier, entity, entry.column, d.id))
                p.applied.append((table, name))
                pair = next((r['params'] for r in getattr(vocab.semantics.d, 'VOCABULARY', ())
                             if r['kind'] == 'code_name_pair' and r['params']['code'] == entry.column
                             and r['params']['name'] in vocab.entries and d.kind != 'band'), None)
                if pair:
                    companion = pair['name'].rsplit('.', 1)[1]
                    p.applied.append((table, companion))
                    expr = exp.cast(exp.Anonymous(this='TO_JSON', expressions=[exp.Struct(expressions=[
                        exp.PropertyEQ(this=exp.to_identifier('code'), expression=expr),
                        exp.PropertyEQ(this=exp.to_identifier('name'), expression=column(companion, expr.table))])]), 'TEXT')
            if aggregate.answer_scope:
                scope_matches.append((self.dimension_keys[d.id], expr.copy()))
            if d.id not in aggregate.remove_dimensions:
                dimensions.append(label(expr, self.dimension_keys[d.id]))
                groups.append(exp.Literal.number(len(dimensions)) if pair else expr.copy())
        if m.kind == 'distinct_count':
            entity = vocab.entities[m.entity.id]
            graph_read("entity", (m.entity.id, entity, "", m.entity.id))
            keys = p.key(entity)
            if keys is None:
                raise ValueError('unreachable entity key')
            key_nodes = [parse(k) for k in keys]
            value = exp.Count(this=exp.Distinct(expressions=key_nodes if len(keys) == 1 else [exp.Tuple(expressions=key_nodes)]))
            valid = exp.and_(*(exp.Not(this=exp.Is(this=k.copy(), expression=exp.Null())) for k in key_nodes))
            if len(keys) > 1:
                value = exp.Count(this=exp.Distinct(expressions=[exp.Case(ifs=[exp.If(this=valid, true=exp.Tuple(expressions=key_nodes))], default=exp.Null())]))
            p.applied.extend((entity['table'], k) for k in entity['key'])
        else:
            value = parse(p.render(aggregate_expression(ctx, aggregation_of(m, ctx)), p.outer()))
        if missing_grain:
            value = exp.Case(ifs=[exp.If(this=exp.EQ(this=exp.Count(this=exp.Star()), expression=exp.Literal.number(0)), true=exp.Null())], default=value)
        empty = output_spec(m, ctx, vocab).empty if m.kind == 'measure' else None
        if empty is not None or (not missing_grain and m.kind == 'measure' and output_spec(m, ctx, vocab).additive and r is not None and window is not None):
            value = exp.Case(ifs=[exp.If(this=exp.EQ(this=exp.Count(this=exp.Star()), expression=exp.Literal.number(0)),
                                       true=exp.Null() if empty == 'null' else exp.Literal.number(0))], default=value)
        q = exp.select(*dimensions, label(value, 'value')).from_(parse('SELECT 1 FROM ' + p.ref(ctx.fact, p.fact)).args['from_'].this.copy())
        for j in p.joins:
            q.append('joins', parse('SELECT 1 FROM ' + p.ref(ctx.fact, p.fact) + ' ' + j).args['joins'][0])
        if p.where:
            q = q.where(conjunction([parse(w) for w in p.where]))
        if groups and not aggregate.answer_scope:
            q = q.group_by(*groups)
        if aggregate.answer_scope:
            q, rows = self.scoped_rows(q, value, dimensions, scope_matches, key_nodes, 'r' + str(index))
        self.require_policy(p, aggregate, row_predicates=[node for node in (*self.plan.filters, *m.filters)
                            if phase(node) == 'row' and node.id not in aggregate.remove_filters], membership_start=membership_start)
        if snapshot:
            if period is None:
                raise ValueError('snapshot rollup requires a period binding')
            q = q.select(label(period.copy(), 'observed_at')).group_by(period.copy())
            observations = 'observations' + str(index)
            self.cte(observations, q)
            partition = [column(self.dimension_keys[d.id]) for d in self.plan.dimensions if d.kind == 'time']
            ranked = exp.select(*(column(e.alias) for e in dimensions), column('value'), column('observed_at'),
                                label(exp.Window(this=exp.Max(this=column('observed_at')), partition_by=partition), 'latest_at')).from_(observations)
            snapshots = 'snapshots' + str(index)
            self.cte(snapshots, ranked)
            q = exp.select(*(column(e.alias) for e in dimensions), column('value')).from_(snapshots).where(
                exp.EQ(this=column('observed_at'), expression=column('latest_at')))
            self.selections['b' + str(index)] = {canonical(parse(f'{snapshots}."observed_at" = {snapshots}."latest_at"'), {})}
        allow = vocab.semantics.allowlist(p, data_read=True)
        if any(contract.get('cardinality') == 'one-to-many' for contract in join_contracts(allow)):
            problems = check((rows if aggregate.answer_scope else q).sql(AUTHORED), allow)
            if problems:
                raise ValueError('SQL gate: ' + '; '.join(problems))
        self.allows.append(allow)
        name = 'b' + str(index)
        if aggregate.answer_scope:
            self.cte('r' + str(index), rows)
        self.cte(name, q)
        self.outputs[aggregate.id] = 'x' + str(index)
        self.reasons[aggregate.id] = 'z' + str(index)
        return name, [e.alias for e in dimensions]

    def scoped_rows(self, q, value, dimensions, scope_matches, key_nodes, rows):
        """A distinct count over the answer's rows alone: the base scope reads each row's keys, and an aligned scope
        joins them to the answer scope on its key columns, null-safe and in the columns' own collation, then counts."""
        keys = [e.alias for e in dimensions if e.alias in ('k0', 'panel')] + [k for k, _ in scope_matches]
        fields = {e.alias: e.this for e in dimensions if e.alias in ('k0', 'panel')} | dict(scope_matches)
        entity = {key.sql(AUTHORED): 'e' + str(i) for i, key in enumerate(key_nodes)}
        base = q.copy()
        base.set('expressions', [label(fields[k].copy(), k) for k in keys] + [label(key.copy(), entity[key.sql(AUTHORED)]) for key in key_nodes])
        counted = value.copy().transform(lambda node: column(entity[node.sql(AUTHORED)], rows)
                                         if isinstance(node, exp.Column) and node.sql(AUTHORED) in entity else node)
        read = dict(scope_matches)
        kept = [label(column(e.alias, rows), e.alias) if e.alias in read else e.copy() for e in dimensions]
        scope = 's' + rows
        scoped = exp.select(*kept, label(counted, 'value')).from_(rows).join(
            exp.to_table('answer_scope').as_(scope), on=conjunction([key_match(k, rows, scope) for k in keys]), join_type='INNER')
        grouped = [column(e.alias, rows) for e in dimensions if e.alias in read]
        if grouped:
            scoped = scoped.group_by(*grouped)
        self.alignments[scope] = set(keys)
        return scoped, base

    def calendar_spine(self, observed, dimensions, window):
        """Read only the declared day spine, bounding and certifying its complete physical scope.

        Undated, the spine starts at the earliest observation inside the declared calendar; observations dated outside
        it are left out and a last column of every row says some were, so the answer states it.
        """
        finest = min(dimensions, key=lambda d: GRAIN_ORDER[d.grain])
        declaration = self.vocab.calendars[finest.calendar.id].get('spine')
        if not declaration or declaration['table'].split('.')[0] not in self.checked.units:
            return None
        table, field = declaration['table'], declaration['column']
        if table not in self.vocab.tables or table + '.' + field not in self.vocab.entries:
            raise ValueError('The declared calendar spine is outside the readable scope.')
        if self.vocab.tables[table].get(field) != 'DATE':
            raise ValueError('The declared calendar spine needs a DATE column.')
        self.cte('observed_keys', observed)
        time_keys = {self.dimension_keys[d.id] for d in dimensions}
        other_keys = [key for key in self.keys if key not in time_keys]
        partitions = exp.select(*(column(key) for key in other_keys)).distinct().from_('observed_keys')
        if other_keys == ['k0', *(['panel'] if self.plan.layout == 'separate' else [])]:
            partitions = self.constant_keys()
        self.cte('partitions', partitions)
        attached = len(self.checked.units) > 1
        source = table if attached else bare(table)
        calendar_day = column(field, 'calendar_days')
        values = {self.dimension_keys[d.id]: bucket(calendar_day.copy(), d.grain,
            self.vocab.calendars[d.calendar.id]) for d in dimensions}
        ticks = exp.select(*(label(value, key) for key, value in values.items())).distinct().from_(
            exp.to_table(source).as_('calendar_days'))
        if window is not None:
            predicates = [exp.GTE(this=calendar_day.copy(), expression=date(window[0])),
                          exp.LT(this=calendar_day.copy(), expression=date(window[1]))]
            ticks = ticks.where(conjunction(predicates))
        else:
            key = self.dimension_keys[finest.id]
            first = exp.select(exp.Min(this=column(key))).from_('observed_keys').subquery()
            last = exp.select(exp.Max(this=column(key))).from_('observed_keys').subquery()
            ticks = ticks.where(exp.Between(this=values[key].copy(), low=first, high=last))
        allow = DataAllowlist(' '.join(self.checked.units), {source}, {source: {field}},
                              schema={source: {field}}, attached=attached)
        required = ticks.copy()
        if window is None:
            required.set('where', None)
        normalized = resolved(warehouse_names(parse(required.sql(AUTHORED)), allow), allow)
        scope, = list(traverse_scope(normalized))
        self.requirements[str(len(self.requirements))] = {
            'scope': 'calendar_ticks', 'tables': {alias: table_name(physical, allow)
                for alias, physical in scope.sources.items() if isinstance(physical, exp.Table)},
            'predicates': {canonical(predicate, {}) for predicate in scope_conditions(scope)}, 'left_joins': {}}
        self.allows.append(allow)
        self.cte('calendar_ticks', ticks)
        self.calendar_scopes.update(('calendar_ticks', 'partitions', 'spine'))
        self.alignments['calendar_ticks'] = set()
        spine = exp.select(*(column(key, 'partitions' if key in other_keys else 'calendar_ticks')
                             for key in self.keys)).from_('partitions').join('calendar_ticks', join_type='CROSS')
        if window is None:
            key = self.dimension_keys[finest.id]
            ticks_keys = exp.select(column(key, 'calendar_ticks')).from_('calendar_ticks')
            inside = exp.select(exp.Min(this=column(key, 'o'))).from_(exp.to_table('observed_keys').as_('o')).where(
                exp.In(this=column(key, 'o'), query=ticks_keys.subquery())).subquery()
            spine = spine.where(exp.GTE(this=column(key, 'calendar_ticks'), expression=inside))
            unplaced = exp.select(exp.Literal.number(1)).from_(exp.to_table('observed_keys').as_('o')).where(conjunction([
                exp.Not(this=exp.Is(this=column(key, 'o'), expression=exp.Null())),
                exp.Not(this=exp.Exists(this=exp.select(exp.Literal.number(1)).from_(exp.to_table('calendar_ticks').as_('t')).where(
                    exp.EQ(this=column(key, 't'), expression=column(key, 'o')))))]))
            self.outside_calendar = exp.Exists(this=unplaced)
        return spine

    def build(self):
        plan, vocab = self.plan, self.vocab
        selected = branches(plan)
        built = [(b, *self.aggregate(b, i)) for i, b in enumerate(selected)]
        main = [(b, name, keys) for b, name, keys in built if not b.remove_dimensions and not b.remove_filters]
        key_queries = [exp.select(*(column(k) for k in self.keys)).from_(name) for _, name, _ in main]
        spine = union(key_queries, distinct=True)
        if not self.dimension_keys:
            spine = self.constant_keys()
        time_dims = [d for d in plan.dimensions if d.kind == 'time']
        if time_dims:
            grain_order = GRAIN_ORDER
            d = min(time_dims, key=lambda d: grain_order[d.grain])
            window = aggregate_window(AggregateSpec('', plan.measures[0], plan.time, 'current'), vocab, plan, self.latest)
            ticks = periods(window, d.grain, vocab.calendars[d.calendar.id])
            declared_spine = self.calendar_spine(spine, time_dims, window)
            if declared_spine is not None:
                spine = declared_spine
            elif ticks:
                time_keys = {self.dimension_keys[d.id] for d in time_dims}
                other_keys = [k for k in self.keys if k not in time_keys]
                self.cte('observed_keys', spine)
                partitions = exp.select(*(column(k) for k in other_keys)).distinct().from_('observed_keys')
                if other_keys == ['k0', *(['panel'] if plan.layout == 'separate' else [])]:
                    partitions = self.constant_keys()
                self.cte('partitions', partitions)
                queries = []
                spine_ticks = (periods(window, 'day', vocab.calendars[d.calendar.id])
                               if any(t.grain == 'week' for t in time_dims)
                               and any(t.grain not in ('day', 'week') for t in time_dims) else ticks)
                for tick in spine_ticks:
                    values = {self.dimension_keys[d.id]: periods((tick, tick + datetime.timedelta(days=1)), d.grain, vocab.calendars[d.calendar.id])[0] for d in time_dims}
                    queries.append(exp.select(*(label(date(values[k]), k) if k in values else column(k) for k in self.keys)).from_('partitions'))
                spine = union(queries, distinct=True)
            elif window is None and any(node.kind == 'moving_average' for node in plan.derived):
                self.cte('observed_keys', spine)
                other_keys = [k for k in self.keys if k != self.dimension_keys[d.id]]
                bounds = exp.select(*(column(k) for k in other_keys),
                    label(exp.Min(this=column(self.dimension_keys[d.id])), 'first_tick'),
                    label(exp.Max(this=column(self.dimension_keys[d.id])), 'last_tick')).from_('observed_keys').group_by(
                        *(column(k) for k in other_keys))
                self.cte('partitions', bounds)
                step = 1 if d.grain in ('day', 'month', 'week') else 3 if d.grain.endswith('quarter') else 12
                unit = 'DAY' if d.grain == 'day' else 'WEEK' if d.grain == 'week' else 'MONTH'
                series = exp.GenerateDateArray(start=column('first_tick', 'partitions'), end=column('last_tick', 'partitions'),
                    step=exp.Interval(this=exp.Literal.string(str(step)), unit=exp.Var(this=unit)))
                unnest = exp.Unnest(expressions=[series], alias=exp.TableAlias(this=exp.to_identifier('ticks'),
                    columns=[exp.to_identifier('tick')]))
                spine = exp.select(*(column(k, 'partitions') for k in other_keys),
                    label(exp.column('tick', 'ticks'), self.dimension_keys[d.id])).from_('partitions').join(unnest, join_type='CROSS')
                self.date_series['spine'] = canonical(parse(series.sql(AUTHORED)), {})
        self.cte('spine', spine)
        q = exp.select(*(column(k, 's') for k in self.keys)).from_(exp.to_table('spine').as_('s'))
        for b, name, keys in built:
            on = conjunction([key_match(k, 's', name) for k in keys])
            self.alignments[name] = set(self.keys) - {self.dimension_keys[d] for d in b.remove_dimensions}
            q = q.join(name, on=on, join_type='LEFT')
            value = column('value', name)
            ctx = vocab.contexts[b.measure.context.id]
            released_additive = (b.measure.kind == 'measure' and output_spec(b.measure, ctx, vocab).additive
                                 and policy_for(vocab, ctx) is not None and aggregate_window(b, vocab, plan, self.latest) is not None)
            empty_policy = output_spec(b.measure, ctx, vocab).empty if b.measure.kind == 'measure' else None
            if empty_policy == 'zero' or (empty_policy is None and released_additive) or b.measure.kind == 'distinct_count':
                empty = exp.Is(this=column('k0', name), expression=exp.Null())
                if plan.layout == 'separate':
                    empty = exp.and_(empty, exp.EQ(this=column('panel', 's'), expression=exp.Literal.string(ctx.id)))
                value = exp.Case(ifs=[exp.If(this=empty, true=exp.Literal.number(0))], default=value)
            missing_reason = exp.Literal.string('missing_observation')
            if b.shift == -1 and plan.time.kind in ('range', 'ytd'):
                dims = [d for d in plan.dimensions if d.kind == 'time']
                if dims:
                    first = time_bounds(plan.time, vocab.calendars)[0]
                    after = first - datetime.timedelta(days=shift_size(b, plan)) if shift_unit(b, plan) == 'DAY' else add_months(first, -shift_size(b, plan))
                    dim = min(dims, key=lambda d: GRAIN_ORDER[d.grain])
                    after = periods((after, after + datetime.timedelta(days=1)), dim.grain, vocab.calendars[dim.calendar.id])[0]
                    outside = exp.LT(this=column(self.dimension_keys[dim.id], 's'), expression=date(after))
                    previous_window = aggregate_window(b, vocab, plan, self.latest)
                    if previous_window is not None and previous_window[0] >= previous_window[1]:
                        outside = exp.true()
                    value = exp.Case(ifs=[exp.If(this=outside.copy(), true=exp.Null())], default=value)
                    missing_reason = exp.Case(ifs=[exp.If(this=outside, true=exp.Literal.string('outside_requested_range'))], default=missing_reason)
            q = q.select(label(value.copy(), self.outputs[b.id]),
                         label(exp.Case(ifs=[exp.If(this=exp.Is(this=value, expression=exp.Null()),
                                                  true=missing_reason)], default=exp.Null()), self.reasons[b.id]))
        self.cte('aligned', q)
        current = 'aligned'
        for i, d in enumerate(plan.derived):
            expressions = [column(k) for k in self.keys]
            expressions += [column(c) for c in self.outputs.values()] + [column(c) for c in self.reasons.values()]
            if d.kind in ('ratio', 'percent_change', 'difference'):
                n, den = column(self.outputs[d.numerator]), column(self.outputs[d.denominator])
                numerator = exp.Paren(this=exp.Sub(this=n.copy(), expression=den.copy())) if d.kind in ('percent_change', 'difference') else n
                divisor = exp.Abs(this=den.copy()) if d.kind in ('ratio', 'percent_change') else den.copy()
                value = exp.Div(this=numerator, expression=exp.Nullif(this=divisor, expression=exp.Literal.number(0)))
                reason = exp.Case(ifs=[exp.If(this=exp.EQ(this=den.copy(), expression=exp.Literal.number(0)), true=exp.Literal.string('zero_denominator'))],
                                  default=exp.Coalesce(this=column(self.reasons[d.numerator]), expressions=[column(self.reasons[d.denominator])]))
                if d.kind == 'difference':
                    value = numerator
                    reason = exp.Coalesce(this=column(self.reasons[d.numerator]), expressions=[column(self.reasons[d.denominator])])
            elif d.kind == 'share':
                n, den = column(self.outputs[d.input]), column(self.outputs[d.id + '/denominator'])
                value = exp.Div(this=n, expression=exp.Nullif(this=den.copy(), expression=exp.Literal.number(0)))
                reason = exp.Case(ifs=[exp.If(this=exp.EQ(this=den, expression=exp.Literal.number(0)), true=exp.Literal.string('zero_denominator'))],
                                  default=exp.Coalesce(this=column(self.reasons[d.input]), expressions=[column(self.reasons[d.id + '/denominator'])]))
            else:
                part = [column(self.dimension_keys[k]) for k in d.partition_by]
                tick = column(self.dimension_keys[d.order_by])
                null_tick = exp.Case(ifs=[exp.If(this=exp.Is(this=tick.copy(), expression=exp.Null()),
                    true=exp.Literal.number(1))], default=exp.Literal.number(0))
                order = exp.Order(expressions=[exp.Ordered(this=null_tick, nulls_first=True),
                                              exp.Ordered(this=tick, nulls_first=True)])
                spec = exp.WindowSpec(kind='ROWS', start='UNBOUNDED', start_side='PRECEDING', end='CURRENT ROW')
                if d.kind == 'moving_average':
                    spec.set('start', exp.Literal.number(d.length - 1))
                def window(expression):
                    return exp.Window(this=expression, partition_by=[p.copy() for p in part], order=order.copy(), spec=spec.copy())
                unknowns = window(exp.Sum(this=exp.Case(ifs=[exp.If(this=exp.Is(this=column(self.outputs[d.input]), expression=exp.Null()), true=exp.Literal.number(1))], default=exp.Literal.number(0))))
                unknown = exp.GT(this=unknowns, expression=exp.Literal.number(0))
                aggregate = exp.Avg if d.kind == 'moving_average' else exp.Sum
                value = exp.Case(ifs=[exp.If(this=unknown.copy(), true=exp.Null())], default=window(aggregate(this=column(self.outputs[d.input]))))
                reason = exp.Case(ifs=[exp.If(this=unknown, true=exp.Literal.string('missing_contributing_observation'))], default=exp.Null())
            output, why = 'v' + str(i), 'w' + str(i)
            self.outputs[d.id], self.reasons[d.id] = output, why
            next_name = 'derived' + str(i)
            self.cte(next_name, exp.select(*expressions, label(value, output), label(reason, why)).from_(current))
            current = next_name
        aliases = comparison_aliases(plan)
        visible = [b.id for b in selected if not b.id.endswith('/denominator') and b.id not in aliases] + [d.id for d in plan.derived]
        final = exp.select(*([label(column('panel'), '_panel')] if plan.layout == 'separate' else []),
                           *(label(column(k), identifier) for identifier, k in self.dimension_keys.items()),
                           *(label(column(self.outputs[i]), i) for i in visible),
                           *(label(column(self.reasons[i]), i + '/reason') for i in visible)).from_(current)
        predicates = [self.predicate(p) for p in plan.filters if phase(p) == 'output']
        if predicates:
            final = final.where(conjunction(predicates))
            required = [self.predicate(node) for node in plan.filters if phase(node) == 'output']
            for node in required:
                for col in node.find_all(exp.Column):
                    col.set('table', exp.to_identifier(current))
            self.selections['/'] = {canonical(c, {}) for node in required for c in conjuncts(node)}
        has_summary_join = warehouse_totals(self.checked) and any(
            b.measure.kind == 'distinct_count' or b.id.endswith('/denominator') for b in selected)
        if warehouse_totals(self.checked) and any(b.measure.kind == 'distinct_count' for b in selected):
            scope_query = exp.select(*(column(k) for k in self.keys),
                *(column(c) for c in self.outputs.values()), *(column(c) for c in self.reasons.values())).from_(current)
            if predicates:
                scope_query = scope_query.where(conjunction([p.copy() for p in predicates]))
            self.cte('answer_scope', scope_query)
        if has_summary_join:
            for expression in final.expressions:
                for reference in expression.find_all(exp.Column):
                    reference.set('table', exp.to_identifier(current))
        total_source = current if has_summary_join else None
        if warehouse_totals(self.checked):
            source_outputs, source_reasons = dict(self.outputs), dict(self.reasons)
            for scope_index, scope in enumerate(total_scopes(plan)):
                part = [column('panel', total_source)] if plan.layout == 'separate' else []
                part += [column(self.dimension_keys[d], total_source) for d in scope]
                for i, identifier in enumerate(total_outputs(self.checked)):
                    branch = next(b for b in selected if b.id == identifier)
                    if branch.measure.kind == 'distinct_count' or identifier.endswith('/denominator'):
                        summary = replace(branch, id='summary_' + str(scope_index) + '_' + str(i),
                            remove_dimensions=tuple(d.id for d in plan.dimensions if d.id not in scope or d.id in branch.remove_dimensions),
                            answer_scope=not identifier.endswith('/denominator'))
                        name, keys = self.aggregate(summary, 'summary_' + str(scope_index) + '_' + str(i))
                        on = conjunction([key_match(k, current, name) for k in keys])
                        final = final.join(name, on=on, join_type='LEFT')
                        self.alignments[name] = set(keys)
                        value, reason = column('value', name), exp.Null()
                    else:
                        def window(value):
                            return exp.Window(this=value, partition_by=[p.copy() for p in part])
                        value = column(source_outputs[identifier], total_source)
                        missing = exp.NEQ(this=window(exp.Count(this=value.copy())), expression=window(exp.Count(this=exp.Star())))
                        value = exp.Case(ifs=[exp.If(this=missing.copy(), true=exp.Null())], default=window(exp.Sum(this=value)))
                        reason = exp.Case(ifs=[exp.If(this=missing, true=exp.Literal.string('missing_contributing_observation'))], default=exp.Null())
                    suffix = str(i) if len(total_scopes(plan)) == 1 else str(scope_index) + '_' + str(i)
                    final = final.select(label(value, '/total' + suffix), label(reason, '/total_reason' + suffix))
            self.outputs, self.reasons = source_outputs, source_reasons
        ordering = [exp.Ordered(this=self.dimension_order(o.output, total_source) if o.output in self.dimension_keys else column(self.outputs[o.output], total_source), desc=o.direction == 'desc', nulls_first=False) for o in plan.order]
        ordering += [exp.Ordered(this=self.dimension_order(identifier, total_source), nulls_first=False) for identifier in self.dimension_keys]
        partitioned = bool(plan.order and plan.order[0].partition_by)
        if any(o.direction == 'both' for o in plan.order) or partitioned:
            names = final.named_selects
            scoped = {name: 'shape' + str(i) for i, name in enumerate(names)}
            for expression in final.expressions:
                expression.set('alias', exp.to_identifier(scoped[expression.alias], quoted=True))
            self.cte('answer_rows', final)
            if '/' in self.selections:
                self.selections['answer_rows'] = self.selections.pop('/')
            ends = []
            direction = plan.order[0].direction
            ranked_ends = (('top', True), ('bottom', False)) if direction == 'both' else (
                ('top' if direction == 'desc' else 'bottom', direction == 'desc'),)
            partitions = [column(scoped[k]) for k in plan.order[0].partition_by]
            if partitioned and plan.layout == 'separate':
                partitions.insert(0, column(scoped['_panel']))

            def ranking(descending):
                ordered = [exp.Ordered(this=column(scoped[plan.order[0].output]), desc=descending, nulls_first=False)]
                ordered += [exp.Ordered(this=column(scoped[d.id]), nulls_first=False) for d in plan.dimensions]
                return exp.Window(this=exp.RowNumber(), partition_by=[p.copy() for p in partitions], order=exp.Order(expressions=ordered))
            for end, descending in ranked_ends:
                query = exp.select(*(column(n) for n in scoped.values()), label(exp.Literal.string(end), 'shape_end'),
                                   label(ranking(descending), 'shape_rank')).from_('answer_rows')
                kept = query.named_selects
                below_top = direction == 'both' and end == 'bottom'
                if below_top:
                    query = query.select(label(ranking(True), 'shape_top_rank'))
                if partitioned or below_top:
                    self.cte(end + '_ranked', query)
                    cutoffs = [('shape_rank', exp.LTE)] + ([('shape_top_rank', exp.GT)] if below_top else [])
                    query = exp.select(*(column(n) for n in kept)).from_(end + '_ranked').where(
                        conjunction([kind(this=column(n), expression=exp.Literal.number(plan.limit)) for n, kind in cutoffs]))
                    required = []
                    for n, kind in cutoffs:
                        reference = column(n)
                        reference.set('table', exp.to_identifier(end + '_ranked'))
                        required.append(canonical(kind(this=reference, expression=exp.Literal.number(plan.limit)), {}))
                    self.selections[end + '_rows'] = set(required)
                    if not partitioned:
                        query = query.order_by(column('shape_rank'))
                else:
                    query = query.order_by(column('shape_rank')).limit(plan.limit)
                self.cte(end + '_rows', query)
                ends.append(exp.select(*(column(n) for n in query.named_selects)).from_(end + '_rows'))
            self.cte('ends', union(ends))
            final = exp.select(*(label(column(scoped[n], 'ends'), n) for n in names),
                               label(column('shape_end', 'ends'), '/end'), label(column('shape_rank', 'ends'), '/rank')).from_('ends').order_by(
                exp.Ordered(this=column('shape_end', 'ends'), desc=True), column('shape_rank', 'ends'))
            if partitioned:
                final.set('order', exp.Order(expressions=[
                    *(exp.Ordered(this=column(scoped[k], 'ends'), nulls_first=False) for k in plan.order[0].partition_by),
                    *final.args['order'].expressions]))
        else:
            if ordering:
                final = final.order_by(*ordering)
            if plan.limit is not None:
                final = final.limit(plan.limit)
        if self.outside_calendar is not None:
            final = final.select(label(self.outside_calendar, '/outside_calendar'))
        final.set('with_', exp.With(expressions=self.ctes))
        allow = merge_allowlists(self.allows, self.requirements, set(self.keys))
        allow.selections = self.selections
        allow.alignments = self.alignments
        normalized = resolved(warehouse_names(parse(final.sql(AUTHORED)), allow), allow)
        allow.projections = {physical_scope_name(scope): {i: projection_signature(e, scope) for i, e in enumerate(scope.expression.expressions)}
                             for scope in traverse_scope(normalized) if isinstance(scope.expression, exp.Select)}
        allow._checked_sql = {'sql': normalized.sql(AUTHORED), 'dialect': AUTHORED}
        allow.date_series = self.date_series
        if (partitioned or any(o.direction == 'both' for o in plan.order) or self.calendar_scopes or any(d.kind == 'moving_average' for d in plan.derived)
                or any(d.kind == 'band' for d in plan.dimensions)
                or any(getattr(p, 'op', None) in TEXT_MATCHES for p in plan.nodes())):
            guarded = resolved(warehouse_names(parse(final.sql(AUTHORED)), allow, AUTHORED), allow, AUTHORED)
            for scope in traverse_scope(guarded):
                scope_name = physical_scope_name(scope)
                if scope_name in allow.date_series:
                    node = next(j.this for j in scope.expression.args.get('joins') or [] if isinstance(j.this, exp.Unnest))
                    allow.date_series[scope_name] = canonical(node.expressions[0], {})
                allow.output_shapes[scope_name] = checked_output_shape(scope)
        lineage, currencies = {}, {}
        for d in plan.dimensions:
            dtype = 'date' if d.kind == 'time' else 'text'
            if d.kind not in ('time', 'band'):
                entry = vocab.entries[d.bindings[0].column.id]
                table, name = entry.column.rsplit('.', 1)
                declared = vocab.tables[table][name].upper()
                dtype = ('boolean' if declared == 'BOOLEAN' else 'integer' if 'INT' in declared else
                         'decimal' if any(t in declared for t in ('DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE', 'REAL')) else 'date' if declared == 'DATE' else 'text')
            self.columns.append(ResultColumn(id=d.id, kind='dimension', type=dtype, currency=None, lineage=(d.id,)))
        for b in selected:
            ctx = vocab.contexts[b.measure.context.id]
            window = aggregate_window(b, vocab, plan, self.latest)
            lineage[b.id] = (ctx.id, b.measure.entity.id if b.measure.kind == 'distinct_count' else ctx.measure, *(d.isoformat() for d in window or ()))
            currencies[b.id] = vocab.measures[ctx.measure].currency if b.measure.kind != 'distinct_count' and aggregation_of(b.measure, ctx) != 'count' else None
        for derived in plan.derived:
            inputs = (derived.numerator, derived.denominator) if derived.kind in ('ratio', 'percent_change', 'difference') else (derived.input,)
            lineage[derived.id] = tuple(dict.fromkeys(source for i in inputs for source in lineage[i]))
            if getattr(derived, 'declaration', None) is not None:
                lineage[derived.id] = (*lineage[derived.id], derived.declaration.id)
            currencies[derived.id] = (currencies[derived.input] if derived.kind in ('running_total', 'moving_average') else
                currencies[derived.numerator] if derived.kind == 'difference' or (derived.kind == 'ratio' and currencies[derived.denominator] is None) else None)
        for identifier in visible:
            b = next((b for b in selected if b.id == identifier), None)
            ctx = vocab.contexts[b.measure.context.id] if b else None
            count = b is not None and (b.measure.kind == 'distinct_count' or aggregation_of(b.measure, ctx) == 'count')
            self.columns.append(ResultColumn(id=identifier, kind='count' if count else 'measure' if b and b.id == b.measure.id else 'comparison' if b else 'derived',
                                             type='integer' if count else 'decimal', currency=currencies[identifier], lineage=lineage[identifier]))
        return (Statement(final.sql(AUTHORED), allow, self.checked.units, self.outside_calendar is not None),
                tuple(self.columns))


def merge_allowlists(allows, requirements, keys):
    result = CheckedAllowlist(' '.join(dict.fromkeys(a.unit for a in allows)), set(), {}, branches=requirements, alignment_keys=keys)
    for a in allows:
        for name in ('tables', 'joins', 'restricted', 'measures', 'predicates', 'declared', 'links'):
            getattr(result, name).update(getattr(a, name))
        for name in ('columns', 'schema'):
            for table, columns in getattr(a, name).items():
                getattr(result, name).setdefault(table, set()).update(columns)
        for name in ('required', 'cites', 'composite_joins'):
            getattr(result, name).update(getattr(a, name))
        result._join_contracts.update(a._join_contracts)
        result.attached = a.attached
    return result


def key_match(key, left, right):
    """Two rows' alignment keys match: k0 and panel, constants never null, by equality; a dimension's, null-safe."""
    cls = exp.EQ if key in ('k0', 'panel') else exp.NullSafeEQ
    return cls(this=column(key, left), expression=column(key, right))


def compile_plan(checked, release_rows=None):
    if not isinstance(checked, CheckedPlan):
        raise TypeError('compiler requires a checked plan')
    needs = requirements(checked)
    release = release_statement(checked, needs)
    pending = Compiled(None, release, needs, (), checked)
    if needs and release_rows is None:
        preflight, _ = Builder(checked, None).build()
        problems = check(preflight.sql, preflight.allow)
        if problems:
            raise ValueError('SQL gate: ' + '; '.join(problems))
        return pending
    if (anchored := reanchored(checked, needs, release_rows)) is not checked:
        return compile_plan(anchored, release_rows)
    latest = resolve_releases(pending, release_rows) if needs else None
    for comparison in checked.plan.comparisons:
        if comparison.kind in ('previous_year', 'previous_period') and checked.plan.time.kind == 'latest_released' and latest is not None:
            shifted_mapping(checked.plan, comparison.kind, checked.vocabulary.calendars, (latest.replace(day=1), add_months(latest.replace(day=1), 1)))
    answer, columns = Builder(checked, latest).build()
    return replace(pending, answer=answer, columns=columns)
