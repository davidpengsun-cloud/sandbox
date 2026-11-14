import datetime as dt

import pytest

from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.semantics.plan import Clarify, Name, Propose, Span
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests import bigquery_fake
from tests.plan_fixtures import ROWS, grounded, model, monthly_time, name, semantics, time_dimension, vocabulary


def setup(monkeypatch, tmp_path, released):
    fake = bigquery_fake.install(monkeypatch)
    w = BigQueryWarehouses(tmp_path, ['u'], client='test', project=bigquery_fake.PROJECT)
    target = bigquery_fake.PROJECT + '.' + w.dataset_id
    fake.run(None, f'CREATE TABLE \u0060{target}.u_events\u0060 (occurred DATE, value NUMERIC, category STRING, entity_id INT64, source STRING, private STRING)')
    for d, v, category, key, source in ROWS:
        fake.run(None, f"INSERT \u0060{target}.u_events\u0060 VALUES (DATE '{d}', {v}, '{category}', {key}, '{source}', NULL)")
    if released:
        fake.run(None, f'CREATE TABLE \u0060{target}.u_calendar_rows\u0060 (period DATE, freeze DATE, release_date DATE, released BOOL)')
        for month in range(1, 8):
            d = dt.date(2025, month + 1, 1) - dt.timedelta(days=1)
            fake.run(None, f"INSERT \u0060{target}.u_calendar_rows\u0060 VALUES (DATE '{d}', DATE '{d}', DATE '{d}', TRUE)")
    fake.requests.clear()
    return fake, w


def jobs(fake):
    submitted = [r['body'] for r in fake.requests if 'body' in r and ('query' in r['body'] or 'configuration' in r['body'])]
    dry = [b for b in submitted if b.get('dryRun') or b.get('configuration', {}).get('dryRun') or b.get('configuration', {}).get('query', {}).get('dryRun')]
    return len(submitted) - len(dry), len(dry)


@pytest.mark.parametrize('release,bounded,latest,width,expected', [
    (False, False, False, 1, (1, 1)),
    (True, True, False, 1, (2, 2)),
    (True, False, True, 1, (2, 2)),
    (True, False, False, 1, (1, 1)),
    (True, True, False, 8, (2, 2)),
])
def test_exact_table_budgets_cold_and_warm(monkeypatch, tmp_path, release, bounded, latest, width, expected):
    fake, warehouses = setup(monkeypatch, tmp_path, release)
    v = vocabulary(semantics(release=release))
    base = model().measures[0].model_dump(mode='json')
    measures = [dict(base, id='m' + str(i)) for i in range(width)]
    time = monthly_time() if bounded else {'kind': 'latest_released', 'calendar': {'id': 'cal', 'evidence': []}} if latest else model().time.model_dump()
    plan = model(measures=measures, time=time)
    text = 'Metric 2025-04 through 2025-06' if bounded else 'Metric latest released' if latest else 'Metric'
    assignments = {'Metric': [m['id'] for m in measures], '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'latest released': 'time'}
    p = grounded(plan, text, v, assignments)
    planner = Planner(v, warehouses, Hidden([], set(), []))
    for _ in range(2):
        fake.requests.clear()
        result = planner.answer(text, Propose(kind='propose', base_turn=None, plan=p))
        assert result['kind'] == 'result', result
        assert jobs(fake) == expected


def test_invalid_coverage_never_opens_warehouse(monkeypatch, tmp_path):
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    v = vocabulary(semantics(release=True))
    plan = grounded(model(), 'Metric sparkle', v)
    result = Planner(v, warehouses, Hidden([], set(), [])).answer('Metric sparkle', Propose(kind='propose', base_turn=None, plan=plan))
    assert result['kind'] == 'abstain'
    assert jobs(fake) == (0, 0)


@pytest.mark.parametrize('text', ['Metric June 2025', 'Metric above 5', 'Metric sparkly'])
def test_clarification_span_cannot_replace_other_constraints(monkeypatch, tmp_path, text):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    v = vocabulary()
    reply = Clarify(kind='clarify', spans=(Span(turn='turn', start=0, end=len(text)),),
                    candidates=(Name(id='ctx', evidence=()),))
    result = Planner(v, warehouses, Hidden([], set(), [])).answer(text, reply)
    assert result['kind'] == 'abstain'
    assert jobs(fake) == (0, 0)


def test_comparisons_stay_inside_one_answer_job(monkeypatch, tmp_path):
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    v = vocabulary(semantics(release=True))
    plan = model(time=monthly_time(), dimensions=[time_dimension()],
                 comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by month 2025-04 through 2025-06 previous period'
    p = grounded(plan, text, v, {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'previous period': 'c'})
    result = Planner(v, warehouses, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
    assert result['kind'] == 'result', result
    assert jobs(fake) == (2, 2)


@pytest.mark.parametrize('kind', ['range', 'ytd'])
def test_undeclared_previous_period_window_has_no_warehouse_jobs(monkeypatch, tmp_path, kind):
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    try:
        v = vocabulary(semantics(release=True))
        time = monthly_time('2025-01', '2025-03') if kind == 'range' else {
            'kind': 'ytd', 'through': {'kind': 'month', 'value': '2025-03', 'calendar': name('cal')}, 'calendar': name('cal')}
        plan = model(time=time, comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
        text = 'Metric 2025-01 through 2025-03 previous period' if kind == 'range' else 'Metric year to date calendar Q1 2025 previous period'
        p = grounded(plan, text, v, {'2025-01': 'time', 'through': 'time', '2025-03': 'time',
                                    'year to date': 'time', 'calendar Q1 2025': 'time', 'previous period': 'c'})
        planner = Planner(v, warehouses, Hidden([], set(), []))
        for _ in range(2):
            fake.requests.clear()
            result = planner.answer(text, Propose(kind='propose', base_turn=None, plan=p))
            assert result['kind'] == 'abstain'
            assert result['missing'] == ["The previous period of a multi-period range isn't declared."]
            assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()
