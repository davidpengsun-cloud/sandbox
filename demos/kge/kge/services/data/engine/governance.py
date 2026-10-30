"""Constrain every physical table before caller SQL can filter or aggregate it."""
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from kge.pipeline.sqlguard import DataAllowlist, check, warehouse_names
from kge.pipeline.landed_tables import PREDATES, landed_tables
from kge.ports import AUTHORED
from kge.semantics.query import Asked, Context, Plan, holds


def table_plan(sem, unit: str, table: str) -> Plan:
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
    version = plan.version(plan.fact, full)
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
    allow = DataAllowlist(**vars(allow))
    joins = tuple(j for j in sem.declared.values()
                  if j.source.startswith(unit + ".") and j.target.startswith(unit + ".") and not j.through)
    if joins:
        context = Context("data", "data", "", "", "", "", "", "", joins[0].source, (), (), (), joins)
        plan = Plan(sem, context, Asked())
        policy = sem.allowlist(plan, data_read=True)
        allow.joins |= policy.joins
        allow.composite_joins.update(policy.composite_joins)
        allow.predicates |= policy.predicates
        allow.declared |= policy.declared
        allow.links |= policy.links
        allow.required.update(policy.required)
    return allow


def constrain(engine, unit: str, sql: str, allow, dialect: str, tables: set[str]) -> tuple[str, object]:
    """Original SQL passes the gate first; generated CTEs cannot be named or shadowed by it."""
    allow = DataAllowlist(**vars(allow))
    statement = warehouse_names(parse_one(sql, read=dialect), allow, dialect)
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
    for table in sorted(tables):
        plan = table_plan(engine.semantics, unit, table)
        policy = engine.semantics.allowlist(plan, data_read=True)
        allow.joins |= policy.joins
        allow.composite_joins.update(policy.composite_joins)
        allow.predicates |= policy.predicates
        allow.declared |= policy.declared
        allow.links |= policy.links
        allow.required.update(policy.required)
        plan.select = ", ".join(exp.column(c, table=table).sql(AUTHORED) for c in sorted(allow.columns[table]))
        if not plan.select:
            raise PermissionError("this product has no columns available to read")
        prefix = f"_kge_read_{len(wrappers)}"
        while prefix.lower() in names:
            prefix += "_"
        names.add(prefix.lower())
        governed[table] = prefix
        body = parse_one(plan.query(None), read=AUTHORED)
        wrappers.append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(prefix))))
    for source in sources.values():
        name = source.name
        if not source.alias:
            source.set("alias", exp.TableAlias(this=source.this.copy()))
        source.set("this", exp.to_identifier(governed[name]))
    held = statement.args.get("with_")
    statement.set("with_", exp.With(expressions=[*wrappers, *(held.expressions if held else [])]))
    rewritten = statement.sql(dialect)
    problems = check(rewritten, allow, dialect=dialect)
    if problems:
        raise ValueError("The query cannot be safely constrained by the pack's governance: " + "; ".join(problems))
    return rewritten, allow
