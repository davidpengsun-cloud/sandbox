import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.pipeline import questionrun
from kge.semantics.compile import Builder, compile_plan, release_calendar_statement, resolve_releases
from kge.semantics.plan import Clarify, Name, Propose, Span
from kge.semantics.plancheck import check_plan
from kge.services.ask.engine.graphview import Graph
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests import bigquery_fake
from tests.plan_fixtures import ROWS, grounded, model, monthly_time, name, semantics, time_dimension, metric_vocabulary as vocabulary


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


def calendar_answer(warehouse, semantics, answer_release_periods=None):
    text = 'Metric 2025-04 through 2025-06'
    vocab = vocabulary(semantics)
    plan = grounded(model(time=monthly_time()), text, vocab, {'2025-04': 'time', 'through': 'time', '2025-06': 'time'})
    planner = Planner(vocab, warehouse, Hidden([], set(), []), answer_release_periods=answer_release_periods)
    return planner.answer(text, Propose(kind='propose', base_turn=None, plan=plan))


def calendar_snapshot(monkeypatch, warehouse, semantics, versions=None):
    monkeypatch.setattr(warehouse, 'list_runtimes', lambda: [{'id': 'reference'}])
    graph = Graph.__new__(Graph)
    graph.warm_release_periods(semantics, warehouse, versions)
    return graph


def pinned_warehouse(fake, tmp_path, version):
    for table in ('events', 'calendar_rows'):
        fake.con.execute(f'CREATE TABLE u_v{version}_{table} AS SELECT * FROM u_{table}')
    return BigQueryWarehouses(tmp_path, ['u'], client='test', project=bigquery_fake.PROJECT, versions={'u': version})


def test_pinned_releases_reuse_one_governed_calendar_and_keep_answer_sql_gate(monkeypatch, tmp_path):
    fake, _ = setup(monkeypatch, tmp_path, True)
    sem = semantics(release=True)
    first = pinned_warehouse(fake, tmp_path, 1)
    baseline = calendar_answer(first, sem)
    assert jobs(fake) == (2, 2) and baseline['kind'] == 'result'
    fake.requests.clear()
    warm = calendar_snapshot(monkeypatch, first, sem, first.versions)
    assert jobs(fake) == (1, 1)
    fake.requests.clear()
    for _ in range(2):
        answer = calendar_answer(first, sem, warm.answer_release_periods)
        assert all(answer[key] == baseline[key] for key in ('result', 'sql', 'guard', 'months'))
        assert answer['sql_run'] == [answer['sql']]
    assert jobs(fake) == (2, 2)

    second = pinned_warehouse(fake, tmp_path, 2)
    fake.con.execute("UPDATE u_v2_calendar_rows SET released = FALSE WHERE period = DATE '2025-06-30'")
    fake.requests.clear()
    latest = calendar_snapshot(monkeypatch, second, sem, second.versions)
    assert jobs(fake) == (1, 1)
    refused = calendar_answer(second, sem, latest.answer_release_periods)
    assert refused['kind'] == 'abstain' and 'June 2025 is not yet released' in str(refused)
    assert jobs(fake) == (1, 1)
    assert calendar_answer(first, sem, warm.answer_release_periods)['result'] == baseline['result']


def test_unpinned_calendar_changes_are_seen_with_cached_preview(monkeypatch, tmp_path):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    sem = semantics(release=True)
    warm = calendar_snapshot(monkeypatch, warehouse, sem)
    fake.requests.clear()
    assert calendar_answer(warehouse, sem, warm.answer_release_periods)['kind'] == 'result'
    assert jobs(fake) == (2, 2)
    fake.con.execute("UPDATE u_calendar_rows SET released = FALSE WHERE period = DATE '2025-06-30'")
    fake.requests.clear()
    assert calendar_answer(warehouse, sem, warm.answer_release_periods)['kind'] == 'abstain'
    assert jobs(fake) == (1, 1)


@pytest.mark.parametrize('old_calendar_job', [False, True])
def test_pinned_calendar_answer_resumes_from_durable_job_checkpoints(monkeypatch, tmp_path, old_calendar_job):
    fake, _ = setup(monkeypatch, tmp_path, True)
    get = fake.get

    def status(url):
        data = get(url)
        if '/jobs/' in url and data.get('jobComplete') and 'status' not in data:
            return {**data, 'status': {'state': 'DONE'}}
        return data

    monkeypatch.setattr(fake, 'get', status)
    sem = semantics(release=True)
    warehouse = pinned_warehouse(fake, tmp_path, 1)
    warm = calendar_snapshot(monkeypatch, warehouse, sem, warehouse.versions)

    def new_record():
        return {'jobs': [], 'expires_at': dt.datetime.now(dt.timezone.utc).timestamp() + 60}

    def completed(record):
        job = record['jobs'][-1]
        handle = job['handle']
        polled = warehouse.poll(handle)
        assert polled['state'] == 'done'
        job['result'] = {'guard': handle['guard'], 'rows': polled['rows'], 'meta': handle['meta']}
        return questionrun.loads(questionrun.dumps(record))

    def unused_snapshot(*args):
        pytest.fail('resume required the new engine to warm the calendar')

    def submit(record, snapshot):
        with questionrun.checkpoints(record, lambda: None), pytest.raises(questionrun.RunPending):
            calendar_answer(warehouse, sem, snapshot)

    before = new_record()
    for _ in range(2):
        submit(before, None)
        before = completed(before)
    with questionrun.checkpoints(before, lambda: None):
        baseline = calendar_answer(warehouse, sem)
    fake.requests.clear()

    record = new_record()
    submit(record, None if old_calendar_job else warm.answer_release_periods)
    if old_calendar_job:
        record.pop('warehouse_reads', None)
        record['jobs'][0].pop('units')
    record = completed(record)
    if old_calendar_job:
        submit(record, unused_snapshot)
        record = completed(record)
    with questionrun.checkpoints(record, lambda: None):
        result = calendar_answer(warehouse, sem, unused_snapshot)
    assert result['kind'] == 'result'
    for key in ('result', 'sql', 'guard', 'months'):
        assert result[key] == baseline[key], key
    assert jobs(fake) == ((2, 2) if old_calendar_job else (1, 1))


@pytest.mark.parametrize('second_calendar', ['snapshot', 'live', 'legacy-job'])
def test_same_calendar_sql_for_different_units_keeps_saved_decisions_on_resume(monkeypatch, second_calendar):
    second_live = second_calendar != 'snapshot'
    text = 'Metric 2025-04 through 2025-06'
    vocab = vocabulary(semantics(release=True))
    plan = grounded(model(time=monthly_time()), text, vocab,
                    {'2025-04': 'time', 'through': 'time', '2025-06': 'time'})
    checked = check_plan(plan, vocab, text)
    original = compile_plan(checked)
    intent = {unit: SimpleNamespace(plan=checked.plan, vocabulary=vocab, unit=unit) for unit in ('u', 'v')}
    compiled = {}
    for unit in intent:
        table = unit + '.calendar_rows'
        need = replace(original.needs[0], unit=unit, policy={**original.needs[0].policy, 'table': table})
        calendar = release_calendar_statement({table: vocab.tables['u.calendar_rows']}, (need,), (unit,))
        compiled[unit] = replace(original, release=calendar, needs=(need,), answer=Builder(checked, None).build()[0])
    assert compiled['u'].release.sql == compiled['v'].release.sql
    assert compiled['u'].release.units != compiled['v'].release.units

    def compile_for_unit(checked, rows=None):
        result = compiled[checked.unit]
        if rows is not None:
            resolve_releases(result, rows)
            pytest.fail('expected an unreleased period')
        return result

    monkeypatch.setattr('kge.services.ask.engine.planner.compile_plan', compile_for_unit)
    submitted = []

    def submit(statement, **options):
        submitted.append(tuple(statement['units']))
        return {'id': options['job_id']}

    warehouse = SimpleNamespace(select_runtime=lambda *a, **kw: {'ok': True, 'runtime_id': 'reference'},
                                submit=submit)

    def calendar_for(unit):
        return [('0', dt.date(2025, 4, 30), True, None), ('0', dt.date(2025, 5, 31), True, None),
                ('0', dt.date(2025, 6, 30), False, dt.date(2025, 7, 1 if unit == 'u' else 2))]

    def snapshot(needs, runtime):
        unit = needs[0].unit
        return None if second_live and unit == 'v' else calendar_for(unit)

    def answer(unit, read):
        planner = Planner(vocab, warehouse, Hidden([], set(), []), answer_release_periods=read)
        reply = planner.execute_checked(text, intent[unit], 'turn')
        assert reply['kind'] == 'abstain'
        return '; '.join(reply['missing'])

    record = {'jobs': []}
    with questionrun.checkpoints(record, lambda: None):
        assert '2025-07-01' in answer('u', snapshot)
        if second_live:
            with pytest.raises(questionrun.RunPending):
                answer('v', snapshot)
        else:
            assert '2025-07-02' in answer('v', snapshot)
    assert submitted == ([('v',)] if second_live else [])
    if second_live:
        job, = record['jobs']
        assert job['units'] == ['v']
        if second_calendar == 'legacy-job':
            job.pop('units')
        job['result'] = {'guard': {'passed': True}, 'rows': calendar_for('v'), 'meta': {}}

    def already_saved(*args):
        pytest.fail('resume must use the saved calendar decision')

    with questionrun.checkpoints(questionrun.loads(questionrun.dumps(record)), lambda: None):
        assert '2025-07-01' in answer('u', already_saved)
        assert '2025-07-02' in answer('v', already_saved)
    assert submitted == ([('v',)] if second_live else [])


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
