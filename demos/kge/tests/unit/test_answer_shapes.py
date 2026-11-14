import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlglot

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import check, gate
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.planner import Planner, result_of
from kge.services.ask.engine.planpublic import render
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, vocabulary
from tests.unit.test_plan_compiler import compiled, connection
from tests.unit.test_planner_jobs import jobs, setup


def dimension():
    return {'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}


def shape(direction=None, limit=None, **updates):
    return model(dimensions=[dimension()], limit=limit,
                 order=[{'output': 'm', 'direction': direction, 'nulls': 'last', 'evidence': []}] if direction else [], **updates)


def request(direction=None, limit=None):
    text = 'Metric by category'
    assignments = {'by': 'd', 'category': 'd'}
    if direction:
        word = {'desc': 'top', 'asc': 'bottom', 'both': 'top'}[direction]
        text += f' {word} {limit}'
        assignments.update({word: 'm', str(limit): 'limit'})
        if direction == 'both':
            text += f' and bottom {limit}'
            assignments['bottom'] = 'm'
    return text, assignments


def values():
    return [(dt.date(2025, 6, 10), value, category, key, 'x')
            for key, (category, value) in enumerate([('A', 10), ('B', 30), ('C', 20), ('D', 5), ('E', 30)], 1)]


def decoded(compilation, rows):
    return result_of(compilation, rows, {'ctx': ('policy',)}).model_dump(mode='json', exclude_unset=True)


@pytest.mark.parametrize('limit', [1, 2, 5])
def test_both_ends_are_one_gated_statement_with_labels_ranks_and_full_total(limit):
    text, assignments = request('both', limit)
    c = compiled(shape('both', limit), text, assignments)
    assert check(c.answer.sql, c.answer.allow) == []
    assert 'UNION ALL' in c.answer.sql
    with connection(values()) as con:
        assert gate(con, c.answer.sql, c.answer.allow)['passed']
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    details = [r for r in result['rows'] if not r.get('total')]
    assert len(details) == 2 * limit
    assert [(r['end'], r['rank']) for r in details] == [(end, rank) for end in ('top', 'bottom') for rank in range(1, limit + 1)]
    assert [r['dimensions'][0]['value'] for r in details[:min(limit, 2)]] == ['B', 'E'][:min(limit, 2)]
    assert details[limit]['dimensions'][0]['value'] == 'D'
    assert Decimal(result['rows'][-1]['cells'][0]['value']['value']) == 95
    assert result['rows'][-1]['total']
    assert 'top 1:' in render(result) and 'bottom 1:' in render(result) and 'Total: m: USD 95' in render(result)


@pytest.mark.parametrize('direction,limit', [(None, None), ('desc', 1), ('asc', 2), ('both', 2)])
def test_breakdown_total_matches_full_scope_without_an_extra_bigquery_job(monkeypatch, tmp_path, direction, limit):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        fake.con.execute('DELETE FROM u_events')
        fake.con.executemany('INSERT INTO u_events VALUES (?, ?, ?, ?, ?, NULL)', values())
        v = vocabulary()
        text, assignments = request(direction, limit)
        p = grounded(shape(direction, limit), text, v, assignments)
        for _ in range(2):
            fake.requests.clear()
            answer = Planner(v, warehouses, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
            assert answer['kind'] == 'result', answer
            assert jobs(fake) == (1, 1)
            result = answer['result']
            assert Decimal(result['rows'][-1]['cells'][0]['value']['value']) == 95
            if direction == 'both':
                assert len(result['rows']) == 2 * limit + 1
                assert [r['end'] for r in result['rows'][:-1]] == ['top'] * limit + ['bottom'] * limit
    finally:
        fake.con.close()


@pytest.mark.parametrize('text,direction,limit', [
    ('Metric by category top 2 and bottom 2', 'desc', 2),
    ('Metric by category top 2', 'both', 2),
    ('Metric by category top 2 and bottom 3', 'both', 2),
    ('Metric by category top 2 and bottom 2', 'both', None),
])
def test_partial_ends_unrequested_end_and_mismatched_limits_abstain(text, direction, limit):
    v = vocabulary()
    assignments = {'by': 'd', 'category': 'd', 'top': 'm', 'bottom': 'm', '2': 'limit', '3': 'limit'}
    checked = check_plan(grounded(shape(direction, limit), text, v, assignments), v, text)
    assert not isinstance(checked, CheckedPlan)
    assert checked.kind == 'abstain'


@pytest.mark.parametrize('limited', [False, True])
def test_totals_recompute_counts_and_shares_with_citations(limited):
    count = {'kind': 'distinct_count', 'id': 'n', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'current', 'filters': []}
    share = {'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': ['d'], 'remove_filters': [], 'evidence': []}
    ratio = {'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'm', 'evidence': []}
    text, assignments = request('desc' if limited else None, 1 if limited else None)
    text += ' count Entities share ratio of Metric to Metric'
    assignments.update({'count': 'n', 'Entities': 'n', 'share': 's', 'ratio': 'r'})
    c = compiled(shape('desc' if limited else None, 1 if limited else None,
                       measures=[model().measures[0], count], derived=[share, ratio]), text, assignments)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    total = result['rows'][-1]
    assert total['total'] and total['dimensions'] == [{'kind': 'null'}]
    assert Decimal(total['cells'][0]['value']['value']) == 95
    assert total['cells'][0]['citations'] == ['policy']
    assert [Decimal(str(c['value']['value'])) for c in total['cells'][1:3]] == [5, 1]
    assert all(c['citations'] == ['policy'] and not c['null_reasons'] for c in total['cells'][1:3])
    assert total['cells'][3]['value'] == {'kind': 'decimal', 'value': '1'}
    assert total['cells'][3]['citations'] == ['policy']
    assert render(result).splitlines()[-1] == 'Total: m: USD 95.00; n: 5; s: 1; r: 1'


@pytest.mark.parametrize('kind', ['distinct', 'snapshot'])
def test_distinct_count_reaggregates_while_snapshot_has_no_total(kind):
    v = vocabulary()
    p = shape()
    text, assignments = request()
    if kind == 'distinct':
        p = shape(measures=[{'kind': 'distinct_count', 'id': 'm', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'current', 'filters': []}])
        text += ' count Entities'
    else:
        v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=False)})
    c = compiled(p, text, assignments, v)
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert any(r.get('total') for r in result['rows']) == (kind == 'distinct')


def test_totals_never_replace_missing_observations_with_a_partial_sum():
    from tests.plan_fixtures import monthly_time, time_dimension
    text = 'Metric by month 2025-04 through 2025-06'
    assignments = {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time'}
    c = compiled(model(time=monthly_time(), dimensions=[time_dimension()]), text, assignments)
    with connection() as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert result['rows'][-1]['cells'][0]['value'] == {'kind': 'null'}
    assert result['rows'][-1]['cells'][0]['null_reasons'] == ['missing_contributing_observation']


def test_both_ends_preserve_output_threshold_before_limiting_and_totaling():
    text, assignments = request('both', 1)
    text += ' above 10'
    assignments.update({'above': 'f', '10': 'f'})
    pred = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'output', 'id': 'm'}, 'op': 'gt', 'value': {'kind': 'integer', 'value': 10}, 'evidence': []}
    c = compiled(shape('both', 1, filters=[pred]), text, assignments)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert [r['dimensions'][0].get('value') for r in result['rows'][:-1]] == ['B', 'C']
    assert Decimal(result['rows'][-1]['cells'][0]['value']['value']) == 80
    assert check(c.answer.sql.replace('"x0" > 10', 'TRUE'), c.answer.allow)


@pytest.mark.parametrize('direction,limit', [(None, None), ('desc', 2), ('both', 2)])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_answer_shapes_pass_reference_and_runtime_allowlists(direction, limit, dialect):
    text, assignments = request(direction, limit)
    c = compiled(shape(direction, limit), text, assignments)
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    columns = {'value': 'amount', 'occurred': 'day', 'category': 'label', 'entity_id': 'key', 'source': 'system', 'private': 'private'}
    manager.ref_table_units = {'events': 'u'}
    manager.ref_columns = {('events', c): t for c, t in vocabulary().tables['u.events'].items()}
    manager.rt_tables = {('live', 'events'): {'table': 'events_live'}}
    manager.rt_columns = {('live', 'events'): {c: {'column': p} for c, p in columns.items()}}
    sql, allow, _ = manager._rename_sql_for_runtime(c.answer.sql, 'u', 'live', c.answer.allow)
    reference = rewrite_sql(c.answer.sql, lambda _: '', 'u') if dialect == 'bigquery' else c.answer.sql
    physical = rewrite_sql(sql, lambda _: '', 'u') if dialect == 'bigquery' else sql
    assert check(reference, c.answer.allow, dialect=dialect) == []
    assert check(physical, allow, dialect=dialect) == []
    with connection(values()) as con:
        con.execute('CREATE VIEW events_live AS SELECT value AS amount, occurred AS day, category AS label, entity_id AS key, source AS system, private FROM events')
        roundtrip = sqlglot.transpile(physical, read=dialect, write='duckdb')[0]
        actual = con.execute(roundtrip).fetchall()
        assert len(actual) == (2 * limit if direction == 'both' else limit or 5)


def test_rich_display_preserves_ends_ranks_total_and_nonadditive_blanks():
    from kge.services.ask.engine.present import present
    text, assignments = request('both', 1)
    c = compiled(shape('both', 1), text, assignments)
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    answer = {'behaviour': 'figure', 'result': result, 'figure': None, 'rows': {'B': 30, 'D': 5},
              'answer_text': render(result)}
    display = present(answer, None)
    assert 'error' not in display, display
    assert [col['label'] for col in display['table']['columns'][:3]] == ['End', 'Rank', 'd']
    assert [r[:3] for r in display['table']['rows']] == [['top', 1, 'B'], ['bottom', 1, 'D'], [None, None, 'Total']]
    assert display['chart'] is None
    assert display['headline']['text'] == render(result)
    assert display['table']['total_rows'] == [2]


def test_model_schema_and_normalization_preserve_both_requested_ends():
    from kge.services.ask.engine.planmodel import read
    v = vocabulary()
    text, assignments = request('both', 2)
    p = grounded(shape('both', 2), text, v, assignments)

    class Scripted:
        def json(self, prompt):
            payload = json.loads(prompt.split('\n', 1)[1])
            assert 'both' in payload['schema']['$defs']['Order']['properties']['direction']['enum']
            return {'kind': 'propose', 'base_turn': None, 'plan': p.model_dump(mode='json', by_alias=True)}

    reply = read(Scripted(), text, v)
    assert reply.plan.order[0].direction == 'both' and reply.plan.limit == 2
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)


def test_limited_total_does_not_hide_an_unreturned_missing_observation():
    from tests.plan_fixtures import monthly_time, time_dimension
    text = 'Metric by month 2025-04 through 2025-06 top 1'
    assignments = {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'top': 'm', '1': 'limit'}
    c = compiled(model(time=monthly_time(), dimensions=[time_dimension()], limit=1,
                       order=[{'output': 'm', 'direction': 'desc', 'nulls': 'last', 'evidence': []}]), text, assignments)
    with connection() as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert Decimal(result['rows'][0]['cells'][0]['value']['value']) == 10
    assert result['rows'][-1]['cells'][0]['value'] == {'kind': 'null'}
    assert result['rows'][-1]['cells'][0]['null_reasons'] == ['missing_contributing_observation']


def test_unlimited_totals_keep_warehouse_decimal_precision():
    text, assignments = request()
    c = compiled(shape(), text, assignments)
    result = decoded(c, [('A', Decimal('123456789012345678901234567890.12345'), None), ('B', Decimal('0.00001'), None)])
    assert result['rows'][-1]['cells'][0]['value']['value'] == '123456789012345678901234567890.12346'


@pytest.mark.parametrize('expression,aggregation', [('count(DISTINCT events.entity_id)', 'count'),
                                                  ('sum(events.value) / count(*)', 'sum')])
def test_context_distinct_counts_and_ratios_never_receive_an_additive_total(expression, aggregation):
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain=sem.contexts['ctx'].grain, expression=expression, aggregation=aggregation)
    v = vocabulary(sem)
    text, assignments = request()
    c = compiled(shape(), text, assignments, v)
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert not any(r.get('total') for r in result['rows'])


def test_output_identifiers_cannot_capture_the_internal_end_or_rank_order():
    text, assignments = request('both', 2)
    p = shape('both', 2).model_dump(mode='json', by_alias=True)
    p['measures'][0]['id'], p['dimensions'][0]['id'] = 'shape_end', 'shape_rank'
    p['order'][0]['output'] = 'shape_end'
    assignments = {k: {'m': 'shape_end', 'd': 'shape_rank'}.get(v, v) for k, v in assignments.items()}
    assignments['Metric'] = 'shape_end'
    c = compiled(model(**{k: v for k, v in p.items() if k != 'schema'}), text, assignments)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert [(r['end'], r['rank']) for r in result['rows'][:-1]] == [('top', 1), ('top', 2), ('bottom', 1), ('bottom', 2)]


def test_grouped_rich_total_keeps_the_existing_detail_chart():
    from kge.services.ask.engine.present import present
    text, assignments = request()
    c = compiled(shape(), text, assignments)
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    answer = {'behaviour': 'figure', 'result': result, 'figure': None, 'group_by': 'events.category',
              'rows': {row[2]: row[1] for row in values()}, 'answer_text': render(result)}
    display = present(answer, SimpleNamespace(GROUP_BY={'by category': 'events.category'}))
    assert 'error' not in display, display
    assert len(display['table']['rows']) == 5
    assert display['table']['total'] == {'label': 'Total', 'value': 95}
    assert display['chart']['x'] == 'group' and display['chart']['y'] == 'value'


@pytest.mark.parametrize('expression', ['sum(events.value) / 1000', 'sum(events.value / 1000)'])
def test_additive_unit_scaling_still_receives_a_total(expression):
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain=sem.contexts['ctx'].grain, expression=expression)
    text, assignments = request()
    c = compiled(shape(), text, assignments, vocabulary(sem))
    with connection(values()) as con:
        result = decoded(c, con.execute(c.answer.sql).fetchall())
    assert result['rows'][-1]['total']
    assert Decimal(result['rows'][-1]['cells'][0]['value']['value']) == Decimal('0.095')
