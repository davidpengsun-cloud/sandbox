from dataclasses import replace
from decimal import Decimal

import pytest

from kge.semantics.plan import Propose
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, time_dimension, vocabulary
from tests.unit.test_planner_jobs import jobs, setup


@pytest.mark.parametrize('operation', ['snapshot', 'explicit_period', 'distinct_count'])
def test_bigquery_translates_general_ctes_with_fixed_statement_budget(monkeypatch, tmp_path, operation):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    v = vocabulary(semantics(release=True))
    options = dict(time=monthly_time(), dimensions=[time_dimension()])
    text = 'Metric by month 2025-04 through 2025-06'
    assignments = {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time'}
    if operation == 'snapshot':
        v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=False)})
    elif operation == 'explicit_period':
        options['comparisons'] = [{'kind': 'explicit_period', 'id': 'c', 'inputs': ['m'], 'other': monthly_time('2025-01', '2025-03'), 'evidence': []}]
        text += ' compare 2025-01 through 2025-03'
        assignments.update({'compare': 'c', '2025-01': 'c', '2025-03': 'c', 'through': ['time', 'c']})
    else:
        options['measures'] = [model().measures[0], {'kind': 'distinct_count', 'id': 'n', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'current', 'filters': []}]
        text += ' count Entities'
        assignments.update({'count': 'n', 'Entities': 'n'})
    p = grounded(model(**options), text, v, assignments)
    fake.requests.clear()
    result = Planner(v, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
    assert result['kind'] == 'result', result
    assert jobs(fake) == (2, 2)
    rows = [r for r in result['result']['rows'] if not r.get('total')]
    totals = [r for r in result['result']['rows'] if r.get('total')]
    assert len(totals) == (0 if operation == 'snapshot' else 1)
    if totals:
        assert Decimal(totals[0]['cells'][0]['value']['value']) == 15
        if operation == 'distinct_count':
            assert totals[0]['cells'][1]['value'] == {'kind': 'integer', 'value': 1}
    assert [Decimal(row['cells'][0]['value']['value']) if row['cells'][0]['value']['kind'] != 'null' else None for row in rows] == [Decimal(10), None if operation == 'snapshot' else Decimal(0), Decimal(5)]
    assert rows[1]['cells'][0]['null_reasons'] == (['missing_observation'] if operation == 'snapshot' else [])


def test_latest_comparison_checks_the_predecessor_release_in_the_same_lookup(monkeypatch, tmp_path):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    fake.con.execute("UPDATE u_calendar_rows SET released = FALSE WHERE period = DATE '2025-06-30'")
    v = vocabulary(semantics(release=True))
    p = model(time={'kind': 'latest_released', 'calendar': name('cal')}, comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric latest released previous period'
    p = grounded(p, text, v, {'latest released': 'time', 'previous period': 'c'})
    fake.requests.clear()
    result = Planner(v, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
    assert result['kind'] == 'abstain'
    assert 'not yet released' in str(result['missing'])
    assert jobs(fake) == (1, 1)


def test_malformed_join_dating_abstains_before_any_release_job(monkeypatch, tmp_path):
    from tests.unit.test_plan_relational_matrix import replace_context
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    sem = semantics(release=True, joined=True)
    sem.tables['u.members'].update({'since': 'DATE', 'until': 'DATE', 'active': 'BOOLEAN'})
    sem.scd2['u.members'] = {'valid_from': 'since', 'valid_to': 'until', 'is_current': 'active'}
    ctx = sem.contexts['ctx']
    sem.contexts['ctx'] = replace_context(ctx, joins=tuple(replace(j, required=True, condition=j.condition + ' AND members.since <= last_day(events.occurred)') if j.id == 'member_join' else j for j in ctx.joins))
    v = vocabulary(sem)
    p = model(time=monthly_time())
    text = 'Metric 2025-04 through 2025-06'
    p = grounded(p, text, v, {'2025-04': 'time', 'through': 'time', '2025-06': 'time'})
    fake.requests.clear()
    result = Planner(v, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
    assert result['kind'] == 'abstain'
    assert jobs(fake) == (0, 0)


@pytest.mark.parametrize('layout', ['aligned', 'separate'])
def test_code_name_breakdown_and_share_keep_the_fixed_job_budget(monkeypatch, tmp_path, layout):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    try:
        sem = semantics(release=True)
        sem.d.VOCABULARY = [dict(kind='code_name_pair', params=dict(code='u.events.category', name='u.events.source',
            members=[dict(code='A', name='x')]))]
        v = vocabulary(sem)
        dimension = dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])
        p = model(layout=layout, time=monthly_time(), dimensions=[time_dimension(), dimension],
                  derived=[dict(kind='share', id='s', input='m', remove_dimensions=['t'], remove_filters=[], evidence=[])])
        text = 'Metric by month by category 2025-04 through 2025-06 share' + (' separate' if layout == 'separate' else '')
        p = grounded(p, text, v, {'by': ['t', 'd'], 'month': 't', 'category': 'd', '2025-04': 'time',
                                 'through': 'time', '2025-06': 'time', 'share': 's', 'separate': 'm'})
        planner = Planner(v, warehouse, Hidden([], set(), []))
        for _ in range(2):
            fake.requests.clear()
            result = planner.answer(text, Propose(kind='propose', base_turn=None, plan=p))
            assert result['kind'] == 'result', result
            assert jobs(fake) == (2, 2)
            rows = [row for row in result['result']['rows'] if not row.get('total')]
            assert [Decimal(row['cells'][0]['value']['value']) for row in rows] == [10, 0, 5]
            assert all(row['dimensions'][1]['value'] == 'A' and row['dimension_labels'][1] == 'x' for row in rows)
            assert [float(row['cells'][1]['value']['value']) for row in rows] == pytest.approx([2 / 3, 0, 1 / 3])
    finally:
        fake.con.close()
