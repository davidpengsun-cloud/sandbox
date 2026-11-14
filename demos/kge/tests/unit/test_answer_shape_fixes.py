"""Answer shapes reconciled against independent raw-row SQL, without model calls."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlglot

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.adapters.warehouse.runtimes import RuntimeManager

from kge.pipeline.sqlguard import check, gate
from kge.semantics.compile import compile_plan
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.planner import Planner, result_of
from kge.services.ask.engine.present import result_table
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, time_dimension, vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_plan_relational_matrix import replace_context
from tests.unit.test_planner_jobs import jobs, setup


def category():
    return {'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}


def count():
    return {'kind': 'distinct_count', 'id': 'n', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'current', 'filters': []}


def checked_read(plan, text, assignments, vocab=None):
    v = vocab or vocabulary()
    p = grounded(plan, text, v, assignments)

    class Scripted:
        def json(self, prompt):
            return Propose(kind='propose', base_turn=None, plan=p).model_dump(mode='json', by_alias=True)

    reply = read(Scripted(), text, v)
    assert isinstance(reply, Propose), reply
    checked = check_plan(reply.plan, v, text)
    assert isinstance(checked, CheckedPlan), checked
    return checked


def execute(checked, con):
    compiled = compile_plan(checked)
    if compiled.release:
        guard = gate(con, compiled.release.sql, compiled.release.allow)
        assert guard['passed'], guard
        compiled = compile_plan(checked, con.execute(compiled.release.sql).fetchall())
    guard = gate(con, compiled.answer.sql, compiled.answer.allow)
    assert guard['passed'], guard
    return result_of(compiled, con.execute(compiled.answer.sql).fetchall(), {}).model_dump(mode='json')


def values(row):
    return [None if c['value']['kind'] == 'null' else Decimal(str(c['value']['value'])) for c in row['cells']]


def arithmetic(base='c/m/base', other='c/m/other'):
    return [{'kind': k, 'id': i, 'numerator': base, 'denominator': other, 'evidence': []}
            for k, i in [('difference', 'delta'), ('percent_change', 'pct')]]


def comparison_plan(kind='previous_period', dimensions=(), time=None):
    c = {'kind': kind, 'id': 'c', 'inputs': ['m'], 'evidence': []}
    if kind == 'time_mode':
        c.update(base='current', other='as_reported')
    return model(time=time or {'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}},
                 dimensions=dimensions, comparisons=[c], derived=arithmetic())


@pytest.mark.parametrize('word', ['difference', 'change', '$ change'])
@pytest.mark.parametrize('kind', ['previous_period', 'previous_year', 'time_mode'])
def test_comparison_difference_and_percentage_match_sql(kind, word):
    phrase = {'previous_period': 'previous period', 'previous_year': 'previous year', 'time_mode': 'current versus as reported'}[kind]
    text = f'Metric by category June 2025 {phrase} {word} percent change'
    assignments = {'by': 'd', 'category': 'd', 'June 2025': 'time', 'previous period': 'c', 'previous year': 'c',
                   'current': 'c', 'versus': 'c', 'as reported': 'c', word: 'delta', 'percent change': 'pct'}
    checked = checked_read(comparison_plan(kind, [category()]), text, assignments, vocabulary(semantics(release=kind == 'time_mode')))
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-05-10', 4, 'A', 1, 'x', NULL), ('2024-06-10', 2, 'A', 1, 'x', NULL)")
        if kind == 'time_mode':
            con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
            con.execute("INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-30', '2025-06-30', TRUE)")
        result = execute(checked, con)
        other = '2025-06' if kind == 'time_mode' else '2024-06' if kind == 'previous_year' else '2025-05'
        expected = con.execute("WITH operands AS (SELECT sum(value) FILTER (WHERE strftime(occurred, '%Y-%m') = '2025-06') AS base, sum(value) FILTER (WHERE strftime(occurred, '%Y-%m') = ?) AS other FROM events) SELECT base - other, (base - other) / other FROM operands", [other]).fetchone()
    for row in result['rows']:
        assert values(row)[-2] == expected[0]
        assert float(values(row)[-1]) == pytest.approx(expected[1])
    assert next(c for c in result['columns'] if c['id'] == 'delta')['currency'] == 'USD'


@pytest.mark.parametrize('kind', ['difference', 'percent_change'])
def test_inverted_changes_are_refused(kind):
    v = vocabulary()
    p = comparison_plan()
    derived = [d.model_copy(update={'numerator': 'c/m/other', 'denominator': 'c/m/base'}) for d in p.derived if d.kind == kind]
    text = 'Metric June 2025 previous period ' + kind.replace('_', ' ')
    p = grounded(p.model_copy(update={'derived': tuple(derived)}), text, v,
                 {'June 2025': 'time', 'previous period': 'c', kind.replace('_', ' '): derived[0].id})
    decision = check_plan(p, v, text)
    assert decision.kind == 'abstain' and 'comparison base' in str(decision.missing)


def test_cross_context_population_declaration_controls_arithmetic():
    sem = semantics()
    sem.tables['u.frozen'] = dict(sem.tables['u.events'])
    sem.d.PERIODS['u.frozen'] = dict(sem.d.PERIODS['u.events'])
    sem.contexts['frozen'] = replace_context(sem.contexts['ctx'], id='frozen', label='Frozen', term='Frozen', measure='u.frozen_metric',
        fact='u.frozen', expression='sum(frozen.value)', values=('u.frozen.value',), filters=(), joins=())
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'], [
        MeasureSpec('u.metric', 'USD', 'shared', True), MeasureSpec('u.frozen_metric', 'USD', 'shared', True)])
    measures = [model().measures[0], {'kind': 'measure', 'id': 'f', 'measure': name('u.frozen_metric'), 'context': name('frozen'), 'mode': 'current', 'filters': []}]
    p = model(time=monthly_time(), dimensions=[time_dimension()], measures=measures,
        comparisons=[{'kind': 'contexts', 'id': 'c', 'left': 'm', 'right': 'f', 'evidence': []}], derived=arithmetic('c/base', 'c/other'))
    text = 'Metric versus Frozen by month 2025-04 through 2025-06 difference percent change'
    assignments = {'Frozen': 'f', 'versus': 'c', 'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'difference': 'delta', 'percent change': 'pct'}
    checked = checked_read(p, text, assignments, v)
    with connection() as con:
        con.execute('CREATE TABLE frozen AS SELECT * REPLACE (value * 2 AS value) FROM events')
        result = execute(checked, con)
        sql = con.execute('SELECT e.occurred, sum(e.value) - sum(f.value), (sum(e.value) - sum(f.value)) / sum(f.value) FROM events e JOIN frozen f USING (occurred) GROUP BY e.occurred ORDER BY e.occurred').fetchall()
    observed = [r for r in result['rows'] if not r.get('total') and values(r)[0] is not None]
    assert [values(r)[-2] for r in observed] == [r[1] for r in sql]
    assert [float(values(r)[-1]) for r in observed] == pytest.approx([r[2] for r in sql])
    bad = replace(v, measures={**v.measures, 'u.frozen_metric': replace(v.measures['u.frozen_metric'], population='unrelated')})
    refused = check_plan(checked.plan, bad, text)
    assert refused.kind == 'abstain' and 'incompatible currencies or populations' in str(refused.missing)


@pytest.mark.parametrize('grain', ['month', 'calendar_quarter'])
def test_period_subtotals_distinct_counts_and_share_reconcile_to_sql(grain):
    dimension = {**time_dimension(), 'grain': grain}
    word = 'month' if grain == 'month' else 'quarter'
    p = model(time=monthly_time(), dimensions=[dimension, category()], measures=[model().measures[0], count()],
        derived=[{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': ['t', 'd'], 'remove_filters': [], 'evidence': []}])
    text = f'Metric by {word} by category 2025-04 through 2025-06 count Entities share'
    checked = checked_read(p, text, {'by': ['t', 'd'], word: 't', 'category': 'd', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'count': 'n', 'Entities': 'n', 'share': 's'})
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-04-11', 7, 'B', 1, 'x', NULL), ('2025-05-10', 3, 'A', 2, 'x', NULL), ('2025-05-11', 2, 'B', 1, 'x', NULL), ('2025-06-11', 2, 'B', 1, 'x', NULL)")
        result = execute(checked, con)
        expected = con.execute(f"SELECT date_trunc('{word}', occurred)::DATE, sum(value), count(DISTINCT (entity_id, source)) FROM events GROUP BY 1 ORDER BY 1").fetchall()
        total = con.execute('SELECT sum(value), count(DISTINCT (entity_id, source)) FROM events').fetchone()
    subtotals = [r for r in result['rows'] if r.get('total') and r['dimensions'][0]['kind'] != 'null']
    assert [(r['dimensions'][0]['value'], *values(r)[:2]) for r in subtotals] == [(str(d), s, n) for d, s, n in expected]
    grand = [r for r in result['rows'] if r.get('total') and r['dimensions'][0]['kind'] == 'null'][0]
    assert values(grand) == [*total, 1]
    assert [float(values(r)[2]) for r in subtotals] == pytest.approx([float(s / total[0]) for _, s, _ in expected])
    table = result_table(result)
    assert ['2025-04-01', 'Total'] == table['rows'][table['total_rows'][0]][:2]


@pytest.mark.parametrize('limit', [None, 1])
def test_count_only_totals_are_distinct_across_selected_groups(limit):
    p = model(dimensions=[category()], measures=[count()],
        filters=[{'kind': 'compare', 'id': 'f', 'left': {'kind': 'output', 'id': 'n'}, 'op': 'gt', 'value': {'kind': 'integer', 'value': 1}, 'evidence': []}],
        order=[{'output': 'n', 'direction': 'desc', 'nulls': 'last', 'evidence': []}] if limit else [], limit=limit)
    text = 'count Entities by category above 1' + (' top 1' if limit else '')
    checked = checked_read(p, text, {'count': 'n', 'Entities': 'n', 'by': 'd', 'category': 'd', 'above': 'f', '1': ['f', 'limit'] if limit else 'f', 'top': 'n'})
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-05-01', 1, 'A', 2, 'x', NULL), ('2025-06-10', 1, 'B', 1, 'x', NULL), ('2025-06-11', 1, 'C', 3, 'x', NULL)")
        result = execute(checked, con)
        expected = con.execute('SELECT count(DISTINCT (entity_id, source)) FROM events WHERE category IN (SELECT category FROM events GROUP BY category HAVING count(DISTINCT (entity_id, source)) > 1)').fetchone()[0]
    assert values(result['rows'][-1]) == [expected]


def test_first_month_changes_are_blank_and_explicit_previous_range_is_allowed():
    text = 'Metric by month 2025-04 through 2025-06 previous period difference percent change'
    assignments = {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'previous period': 'c', 'difference': 'delta', 'percent change': 'pct'}
    checked = checked_read(comparison_plan(dimensions=[time_dimension()], time=monthly_time()), text, assignments)
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-03-10', 8, 'A', 1, 'x', NULL), ('2025-05-10', 6, 'A', 1, 'x', NULL)")
        result = execute(checked, con)
        sql = con.execute("WITH m AS (SELECT date_trunc('month', occurred)::DATE AS period, sum(value) AS amount FROM events WHERE occurred >= DATE '2025-04-01' GROUP BY 1) SELECT amount - lag(amount) OVER (ORDER BY period), (amount - lag(amount) OVER (ORDER BY period)) / lag(amount) OVER (ORDER BY period) FROM m ORDER BY period").fetchall()
        assert [values(r)[-2] for r in result['rows'][:-1]] == [r[0] for r in sql]
        assert result['rows'][0]['cells'][-1]['null_reasons'] == ['outside_requested_range']
        p = comparison_plan(dimensions=[time_dimension()], time=monthly_time())
        explicit = {'kind': 'explicit_period', 'id': 'c', 'inputs': ['m'], 'other': monthly_time('2025-03', '2025-05'), 'evidence': []}
        p = p.model_copy(update={'comparisons': model(comparisons=[explicit]).comparisons})
        text = 'Metric by month 2025-04 through 2025-06 versus 2025-03 through 2025-05 difference percent change'
        checked = checked_read(p, text, {**assignments, 'versus': 'c', '2025-03': 'c', '2025-05': 'c', 'through': ['time', 'c']})
        explicit_result = execute(checked, con)
        assert values(explicit_result['rows'][0])[-2] == 2


@pytest.mark.parametrize('share', [False, True])
def test_subtotals_counts_and_changes_keep_bigquery_job_budget(monkeypatch, tmp_path, share):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    try:
        p = comparison_plan(dimensions=[time_dimension(), category()], time=monthly_time())
        p = p.model_copy(update={'measures': (*p.measures, model(measures=[count()]).measures[0])})
        text = 'Metric by month by category 2025-04 through 2025-06 previous period difference percent change count Entities'
        if share:
            part = model(dimensions=[time_dimension(), category()], derived=[{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': ['t', 'd'], 'remove_filters': [], 'evidence': []}]).derived[0]
            p = p.model_copy(update={'derived': (*p.derived, part)})
            text += ' share'
        checked = checked_read(p, text, {'by': ['t', 'd'], 'month': 't', 'category': 'd', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'previous period': 'c', 'difference': 'delta', 'percent change': 'pct', 'count': 'n', 'Entities': 'n', 'share': 's'}, vocabulary(semantics(release=True)))
        for _ in range(2):
            fake.requests.clear()
            answer = Planner(checked.vocabulary, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=checked.plan))
            assert answer['kind'] == 'result', answer
            assert jobs(fake) == (2, 2)
            assert values(answer['result']['rows'][-1])[:2] == [15, 1]
            if share:
                assert values(answer['result']['rows'][-1])[-1] == 1
    finally:
        fake.con.close()


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_count_subtotals_and_arithmetic_pass_reference_and_runtime_gates(dialect):
    text = 'Metric by month by category 2025-04 through 2025-06 previous period difference percent change count Entities'
    p = comparison_plan(dimensions=[time_dimension(), category()], time=monthly_time())
    p = p.model_copy(update={'measures': (*p.measures, model(measures=[count()]).measures[0])})
    checked = checked_read(p, text, {'by': ['t', 'd'], 'month': 't', 'category': 'd', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'previous period': 'c', 'difference': 'delta', 'percent change': 'pct', 'count': 'n', 'Entities': 'n'})
    c = compile_plan(checked)
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    fields = {'value': 'amount', 'occurred': 'day', 'category': 'label', 'entity_id': 'key', 'source': 'system', 'private': 'private'}
    manager.ref_table_units = {'events': 'u'}
    manager.ref_columns = {('events', field): dtype for field, dtype in checked.vocabulary.tables['u.events'].items()}
    manager.rt_tables = {('live', 'events'): {'table': 'events_live'}}
    manager.rt_columns = {('live', 'events'): {field: {'column': physical} for field, physical in fields.items()}}
    physical, allow, _ = manager._rename_sql_for_runtime(c.answer.sql, 'u', 'live', c.answer.allow)
    reference = rewrite_sql(c.answer.sql, lambda _: '', 'u') if dialect == 'bigquery' else c.answer.sql
    runtime = rewrite_sql(physical, lambda _: '', 'u') if dialect == 'bigquery' else physical
    assert check(reference, c.answer.allow, dialect=dialect) == []
    assert check(runtime, allow, dialect=dialect) == []
    with connection() as con:
        con.execute('CREATE VIEW events_live AS SELECT value AS amount, occurred AS day, category AS label, entity_id AS key, source AS system, private FROM events')
        reference_rows = con.execute(sqlglot.transpile(reference, read=dialect, write='duckdb')[0]).fetchall()
        runtime_rows = con.execute(sqlglot.transpile(runtime, read=dialect, write='duckdb')[0]).fetchall()
    assert reference_rows == runtime_rows


def test_zero_comparison_base_keeps_dollar_difference_and_blanks_percentage():
    text = 'Metric by category June 2025 previous period difference percent change'
    checked = checked_read(comparison_plan(dimensions=[category()]), text,
        {'by': 'd', 'category': 'd', 'June 2025': 'time', 'previous period': 'c', 'difference': 'delta', 'percent change': 'pct'})
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-05-10', 0, 'A', 1, 'x', NULL)")
        expected = con.execute("SELECT sum(value) FILTER (WHERE occurred >= DATE '2025-06-01') - sum(value) FILTER (WHERE occurred < DATE '2025-06-01') FROM events WHERE occurred >= DATE '2025-05-01'").fetchone()[0]
        result = execute(checked, con)
    assert all(values(r)[-2:] == [expected, None] for r in result['rows'])
    assert all(r['cells'][-1]['null_reasons'] == ['zero_denominator'] for r in result['rows'])


def test_total_change_uses_total_operands_instead_of_adding_group_percentages():
    text = 'Metric by category June 2025 previous period difference percent change'
    checked = checked_read(comparison_plan(dimensions=[category()]), text,
        {'by': 'd', 'category': 'd', 'June 2025': 'time', 'previous period': 'c', 'difference': 'delta', 'percent change': 'pct'})
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-05-10', 4, 'A', 1, 'x', NULL), ('2025-05-10', 8, 'B', 2, 'x', NULL), ('2025-06-10', 2, 'B', 2, 'x', NULL)")
        expected = con.execute("WITH operands AS (SELECT sum(value) FILTER (WHERE occurred >= DATE '2025-06-01') AS base, sum(value) FILTER (WHERE occurred < DATE '2025-06-01') AS other FROM events WHERE occurred >= DATE '2025-05-01') SELECT base - other, (base - other) / other FROM operands").fetchone()
        result = execute(checked, con)
    assert result['rows'][-1]['total']
    assert values(result['rows'][-1])[-2] == expected[0]
    assert float(values(result['rows'][-1])[-1]) == pytest.approx(expected[1])
