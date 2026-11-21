"""The forms BigQuery refuses that DuckDB runs, read with sqlglot from the SQL BigQuery is given (rewrite_sql's): no
null-safe equality (BigQuery has none for collated text), every outer join on at least one equality of the two sides,
and every correlated subquery correlated by plain equalities alone, which BigQuery can turn into a join. No BigQuery
call is made."""
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.pipeline.sqlguard import conjuncts


def for_bigquery(sql: str, unit: str = 'wealth') -> str:
    """sql, the code's DuckDB SQL, as the BigQuery warehouses would send it (to_bigquery's rewrite)."""
    return rewrite_sql(sql, lambda name: name, unit)


def refusals(bigquery_sql: str) -> list[str]:
    tree = parse_one(bigquery_sql, read='bigquery')
    problems = []
    if tree.find(exp.NullSafeEQ, exp.NullSafeNEQ):
        problems.append('null-safe equality')
    for scope in traverse_scope(tree):
        query = scope.expression
        if not isinstance(query, exp.Select):
            continue
        for join in query.args.get('joins') or []:
            if join.side not in ('LEFT', 'RIGHT', 'FULL') or join.args.get('on') is None:
                continue
            right = join.this.alias_or_name
            if not any(isinstance(c, exp.EQ) and {x.table for x in c.find_all(exp.Column)} - {right}
                       and right in {x.table for x in c.find_all(exp.Column)} for c in conjuncts(join.args['on'])):
                problems.append(f'outer join of {right} without an equality of both sides')
        outer = {c.table for c in scope.external_columns}
        if not outer or scope.is_cte or scope.is_root:
            continue
        conditions = conjuncts(query.args['where'].this if query.args.get('where') else None)
        conditions += [c for j in query.args.get('joins') or [] for c in conjuncts(j.args.get('on'))]
        for condition in conditions:
            if {x.table for x in condition.find_all(exp.Column)} & outer and not isinstance(condition, exp.EQ):
                problems.append('correlated subquery on ' + condition.sql('bigquery'))
    return problems
