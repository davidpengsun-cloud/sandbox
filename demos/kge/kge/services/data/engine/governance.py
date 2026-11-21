"""Constrain every physical table before caller SQL can filter or aggregate it."""
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from kge.pipeline.sqlguard import (DataAllowlist, _source, check, grouped_source, matching_join_contracts,
                                   physical_key, reach_of, resolved, scope_conditions, warehouse_names, conjuncts)
from kge.pipeline.landed_tables import PREDATES, landed_tables
from kge.ports import AUTHORED
from kge.semantics.query import Asked, Context, Plan, holds


def table_plan(sem, unit: str, table: str, *, declared_window: bool = False) -> Plan:
    full = f"{unit}.{table}"
    selectors = {}
    for context in sem.contexts.values():
        if context.unit != unit:
            continue
        for rule in context.selectors:
            if rule.table.split(".")[0] != unit:
                continue
            node = parse_one(rule.predicate, read=AUTHORED)
            named = {f"{c.db or unit}.{c.table}" for c in node.find_all(exp.Column)}
            outer = named - {rule.table}
            if full not in outer and context.fact != full:
                continue
            if outer - {full}:
                raise ValueError("this table's row selectors cannot be safely applied to a data read")
            selectors[rule.id] = rule
    joins = tuple(j for j in sem.declared.values()
                  if j.source == full and j.kind == "calendar_rollup" and j.target in sem.releases)
    context = Context(id="data", label=table, description="", term="", measure="", expression="", aggregation="",
                      grain="", fact=full, values=(), filters=(), selectors=tuple(selectors.values()), joins=joins)
    plan = Plan(sem, context, Asked())
    plan.fact = plan.name(full)
    version = None if declared_window else plan.version(plan.fact, full)
    if version:
        plan.where.append(version)
    for join in joins:
        if join.target.split(".")[0] != unit or join.through:
            raise ValueError("this table's time policy cannot be safely applied within this unit")
        alias = plan.add_join(join)
        policy = sem.releases[join.target]
        plan.where.append(holds(f"{alias}.{policy['released']}", sem.tables.get(join.target, {}).get(policy["released"], "")))
        plan.release = {**policy, "alias": alias}
    if full in sem.releases:
        released = sem.releases[full]["released"]
        plan.where.append(holds(f"{plan.fact}.{released}", sem.tables.get(full, {}).get(released, "")))
    plan.where.extend(plan.selector(rule, plan.outer()) for rule in selectors.values())
    return plan


def declared_allowlist(engine, unit, allow):
    if allow is None:
        return allow
    sem = getattr(engine, "semantics", None)
    if sem is None:
        return allow
    return sem.declared_allowlist(unit, allow)


def constrain(engine, unit: str, sql: str, allow, dialect: str, tables: set[str]) -> tuple[str, object]:
    """Original SQL passes the gate first; generated CTEs cannot be named or shadowed by it."""
    allow = DataAllowlist(**vars(allow))
    allow._governed_sql = None
    problems = check(sql, allow, dialect=dialect, inner_only=True)
    if problems:
        raise ValueError("The query cannot be safely constrained by the pack's governance: " + "; ".join(problems))
    statement = warehouse_names(parse_one(sql, read=dialect), allow, dialect)
    for source in statement.find_all(exp.Table):
        source.meta['_kge_source'] = id(source)
    windowed = set()
    derived_uses, dated_uses = {}, {}
    needed = {}
    for scope in traverse_scope(resolved(statement.copy(), allow, dialect)):
        if not isinstance(scope.expression, exp.Select):
            continue
        for column in scope.columns:
            source = _source(scope, column.table)
            if isinstance(source, exp.Table):
                needed.setdefault(source.meta.get('_kge_source'), set()).add(column.name)
        for alias, (_, source) in scope.selected_sources.items():
            if grouped_source(source, allow):
                physical = next(iter(source.selected_sources.values()))[1]
                marker = physical.meta.get('_kge_source')
                derived_uses.setdefault(marker, set()).add((id(scope), alias))
        names = reach_of(scope, allow)
        first = scope.expression.args.get('from_')
        prior = ({first.this.alias_or_name} if first else set()) | (set(names) - set(scope.sources))
        for join in scope.expression.args.get('joins') or []:
            _, matches = matching_join_contracts(scope, join, prior, allow)
            for contract in matches:
                window = contract.get('validity')
                policy = engine.semantics.scd2.get(unit + '.' + window['table']) if window else None
                if policy and (policy['valid_from'], policy['valid_to']) == (window['effective'], window['expiry']):
                    conditions = scope_conditions(scope) if not join.side else conjuncts(join.args.get('on'))
                    for condition in conditions:
                        if not isinstance(condition, exp.Between):
                            continue
                        low, high = condition.args.get('low'), condition.args.get('high')
                        if (not isinstance(low, exp.Column) or not isinstance(high, exp.Column) or low.table != high.table
                                or names.get(low.table) != window['table'] or physical_key(scope, low, allow) != window['effective']
                                or physical_key(scope, high, allow) != window['expiry']):
                            continue
                        source = _source(scope, low.table)
                        if grouped_source(source, allow):
                            source = next(iter(source.selected_sources.values()))[1]
                            dated_uses.setdefault(source.meta.get('_kge_source'), set()).add((id(scope), low.table))
                        if isinstance(source, exp.Table):
                            marker = source.meta.get('_kge_source')
                            if marker is None:
                                raise ValueError("The query's declared window cannot be bound to its source occurrence")
                            windowed.add(marker)
            prior.add(join.this.alias_or_name)
    if any(derived_uses.get(marker, set()) - uses for marker, uses in dated_uses.items()):
        raise ValueError("The query cannot be safely constrained: a shared pre-aggregation mixes dated and current version policies")
    sources = {id(source): source for scope in traverse_scope(statement) for source in scope.sources.values()
               if isinstance(source, exp.Table)}
    tables.update(source.name for source in sources.values())
    landed = landed_tables(engine.g.profile, engine.g.release())
    if landed is None:
        raise PermissionError(PREDATES)
    missing = sorted(table for table in tables if f"{unit}.{table}" not in landed)
    if missing:
        raise PermissionError(f"The current release has not landed table(s): {', '.join(missing)}.")
    names = {node.name.lower() for node in statement.find_all(exp.Identifier)} | set(allow.tables)
    wrappers = []
    governed = {}
    for table, declared_window in sorted({(source.name, id(source) in windowed) for source in sources.values()}):
        plan = table_plan(engine.semantics, unit, table, declared_window=declared_window)
        if any(join.cardinality == 'one-to-many' for join in plan.context.joins):
            raise ValueError("The query cannot be safely constrained: a source policy's one-to-many join repeats its grain")
        policy = engine.semantics.allowlist(plan, data_read=True)
        allow.joins |= policy.joins
        allow.composite_joins.update(policy.composite_joins)
        allow.predicates |= policy.predicates
        allow.declared |= policy.declared
        allow.links |= policy.links
        allow.required.update(policy.required)
        allow._join_contracts.update(policy._join_contracts)
        columns = set().union(*(needed.get(id(source), set()) for source in sources.values()
                              if source.name == table and (id(source) in windowed) == declared_window))
        if not columns and allow.columns[table]:
            columns.add(min(allow.columns[table]))
        plan.select = ", ".join(exp.column(c, table=table).sql(AUTHORED) for c in sorted(columns))
        if not plan.select:
            raise PermissionError("this product has no columns available to read")
        prefix = f"_kge_read_{len(wrappers)}"
        while prefix.lower() in names:
            prefix += "_"
        names.add(prefix.lower())
        governed[table, declared_window] = prefix
        body = parse_one(plan.query(None), read=AUTHORED)
        problems = check(body.sql(AUTHORED), allow)
        if problems:
            raise ValueError("The table's governance cannot be safely applied: " + "; ".join(problems))
        wrappers.append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(prefix))))
    for source in sources.values():
        name = source.name
        if not source.alias:
            source.set("alias", exp.TableAlias(this=source.this.copy()))
        source.set("this", exp.to_identifier(governed[name, id(source) in windowed]))
    held = statement.args.get("with_")
    statement.set("with_", exp.With(expressions=[*wrappers, *(held.expressions if held else [])]))
    rewritten = statement.sql(dialect)
    allow._governed_sql = {'sql': rewritten, 'dialect': dialect}
    problems = check(rewritten, allow, dialect=dialect)
    if problems:
        raise ValueError("The query cannot be safely constrained by the pack's governance: " + "; ".join(problems))
    return rewritten, allow
