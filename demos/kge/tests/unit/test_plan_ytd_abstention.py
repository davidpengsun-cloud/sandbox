import json
from types import SimpleNamespace

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary
from kge.services.ask.engine import planpublic, planrules
from kge.services.ask.engine.core import Engine, PRESETS
from kge.services.ask.engine.question import Request, UNPLACED_RANGE
from kge.services.ask.engine.wording import Hidden
from kge.services.ask.engine.planner import Planner
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary
from tests.unit.test_plan_resolution_matrix import NoWarehouse
from tests.unit.test_planner_jobs import jobs, setup
from tests.unit.test_plan_relational_matrix import replace_context


def ytd(month='2025-06', calendar='cal'):
    return {'kind': 'ytd', 'through': {'kind': 'month', 'value': month, 'calendar': name(calendar)}, 'calendar': name('cal')}


@pytest.mark.parametrize('phrase', ['year to date through', 'YTD through', 'year-to-date to'])
@pytest.mark.parametrize('end,expected', [('2025-06', 'checked'), ('2025-05', 'abstain')])
def test_ytd_requires_the_written_endpoint(phrase, end, expected):
    v = vocabulary()
    text = f'Metric {phrase} June 2025'
    assignments = {p.text: 'time' for p in ledger(text, 'turn', v) if p.kind in ('operator', 'time')}
    p = grounded(model(time=ytd(end)), text, v, assignments)
    decision = check_plan(p, v, text)
    if expected == 'checked':
        assert isinstance(decision, CheckedPlan), decision
    else:
        assert decision.kind == expected and 'June 2025' in decision.missing


@pytest.mark.parametrize('month', ['2025-01', '2025-06'])
def test_plain_month_cannot_authorize_a_ytd_window(month):
    v = vocabulary()
    text = 'Metric ' + month
    p = grounded(model(time=ytd(month)), text, v, {month: 'time'})
    assert check_plan(p, v, text).kind == 'abstain'


def test_ytd_endpoint_cannot_use_a_different_calendar():
    sem = semantics()
    sem.d.CALENDARS['other'] = dict(sem.d.CALENDARS['cal'])
    v = vocabulary(sem)
    text = 'Metric YTD through June 2025'
    with pytest.raises(ValueError, match='YTD calendar mismatch'):
        grounded(model(time=ytd(calendar='other')), text, v, {'YTD': 'time', 'through': 'time', 'June 2025': 'time'})


@pytest.mark.parametrize('end,expected', [('2025-06', (2, 2)), ('2025-05', (0, 0))])
def test_ytd_model_plan_keeps_the_bigquery_job_budget(monkeypatch, tmp_path, end, expected):
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    try:
        v = vocabulary(semantics(release=True))
        text = 'Metric YTD through June 2025'
        p = grounded(model(time=ytd(end)), text, v, {'YTD': 'time', 'through': 'time', 'June 2025': 'time'})
        result = Planner(v, warehouses, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
        if end == '2025-06':
            assert result['kind'] == 'result', result
            assert float(result['result']['rows'][0]['cells'][0]['value']['value']) == 15
        else:
            assert result['kind'] == 'abstain' and 'June 2025' in result['missing']
            assert 'result' not in result
        assert jobs(fake) == expected
    finally:
        fake.con.close()


def public(monkeypatch, v, req, recording=None, warehouses=None, cfg=None):
    sem = v.semantics
    runtime = SimpleNamespace(contexts=v.contexts, mapping_declarations=[], output_metadata={})
    engine = SimpleNamespace(semantics=sem, w=warehouses or NoWarehouse(), UNIT_ORDER=['u'], UNIT_LABEL={'u': 'Unit'},
                             ALIASES={}, PERIODS=sem.d.PERIODS, CALENDARS=sem.d.CALENDARS, ACRONYMS={})
    engine.g = SimpleNamespace(readable_units=lambda principal: {'u'})
    engine.request_abstention = lambda request, reason: Engine.request_abstention(engine, request, reason)
    monkeypatch.setattr(planrules, 'vocabulary', lambda *args: v)
    monkeypatch.setattr(planrules, 'snapshot', lambda *args: runtime)
    monkeypatch.setattr(planrules, 'cohort_terms', lambda *args: [])
    monkeypatch.setattr(Hidden, 'of', lambda *args: Hidden([], set(), []))
    combined = SimpleNamespace(llm=recording) if recording else None
    cfg = cfg or (PRESETS['graph'].with_stages(resolution='glossary+model') if recording else PRESETS['graph'])
    return planpublic.selected_answer(engine, req, 'reader', 'analysis', cfg, SimpleNamespace(products=None),
                                      {'versions': {}, 'trace': []}, combined=combined)


class Recording:
    name = 'test:recording'

    def __init__(self, reply=None):
        self.reply, self.calls = reply, 0

    def json(self, prompt):
        self.calls += 1
        if self.reply is None:
            return {'kind': 'propose', 'plan': {}}
        data = json.loads(prompt.split('\n', 1)[1])
        reply = self.reply(data['turn_id'])
        return reply.model_dump(mode='json', by_alias=True)


def assert_no_figure(answer):
    assert answer['behaviour'] in ('clarify', 'abstain', 'abstain_with_reason'), answer
    assert answer.get('figure') is None and not answer.get('figures') and not answer.get('result')
    assert not answer.get('rows') and not answer.get('parts') and not answer.get('sql')


@pytest.mark.parametrize('model_period_allowed', [False, True])
def test_failed_model_validation_keeps_its_real_reason(monkeypatch, model_period_allowed):
    req = Request('Metric since June 2025', term='Metric', terms_found=['Metric'], problem=UNPLACED_RANGE,
                  model_period_allowed=model_period_allowed)
    recording = Recording()
    answer = public(monkeypatch, vocabulary(), req, recording)
    assert_no_figure(answer)
    assert 'valid query plan' in answer['reason'] and 'parser' not in answer['reason']
    assert recording.calls == 2


def test_rules_only_keeps_the_parser_reason(monkeypatch):
    req = Request('Metric Juni 2025', problem='Please give a valid period: Juni 2025.')
    answer = public(monkeypatch, vocabulary(), req)
    assert_no_figure(answer)
    assert answer['reason'] == req.problem


def test_model_plancheck_failure_keeps_its_reason_when_the_parser_has_a_problem(monkeypatch):
    req = Request('Metric since June 2025', term='Metric', terms_found=['Metric'], problem=UNPLACED_RANGE)
    def invalid_name(turn):
        data = model().model_dump(mode='json', by_alias=True)
        data['measures'][0]['measure'] = name('missing')
        return Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(data))
    recording = Recording(invalid_name)
    answer = public(monkeypatch, vocabulary(), req, recording)
    assert_no_figure(answer)
    assert 'absent from the readable vocabulary' in answer['reason'] and 'parser' not in answer['reason']
    assert recording.calls == 1


@pytest.mark.parametrize('configured', [False, True])
def test_ambiguous_term_names_both_declared_meanings_without_a_figure(monkeypatch, configured):
    v = vocabulary(values=[Entry('one', 'value', 'First grouping', 'u', column='u.events.category', value='A'),
                           Entry('two', 'value', 'Second grouping', 'u', column='u.events.source', value='x')],
                   aliases=[('Office', 'one'), ('Office', 'two')])
    recording = Recording(lambda turn: Propose(kind='propose', base_turn=None, plan=model())) if configured else None
    answer = public(monkeypatch, v, Request('Office'), recording)
    assert_no_figure(answer)
    assert answer['behaviour'] == 'clarify'
    assert {c['id'] for c in answer['clarification']['choices']} == {'one', 'two'}
    assert {c['label'] for c in answer['clarification']['choices']} == {'First grouping', 'Second grouping'}
    assert answer['reason'] == 'Which meaning do you intend?'


@pytest.mark.parametrize('phrase', ['plan', 'budget', 'target', 'total client assets', 'total category'])
@pytest.mark.parametrize('configured', [False, True])
def test_undeclared_measure_offers_readable_measures_without_a_figure(monkeypatch, phrase, configured):
    sem = semantics()
    sem.tables['secret.rows'] = {'value': 'DECIMAL'}
    sem.contexts['private'] = replace_context(sem.contexts['ctx'], id='private', fact='secret.rows', measure='secret.metric', term='Private measure')
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'],
                         [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('secret.metric', 'USD', 'entities', True)],
                         aliases=[('Metric', 'ctx')])
    recording = Recording(lambda turn: Propose(kind='propose', base_turn=None, plan=model())) if configured else None
    answer = public(monkeypatch, v, Request('What is the ' + phrase + '?'), recording)
    assert_no_figure(answer)
    assert 'Declared measures here are Metric' in answer['reason'] and 'Which would you like?' in answer['reason']
    assert 'Private measure' not in str(answer) and 'secret' not in str(answer)


def test_schema_numeric_columns_are_not_offered_as_declared_measures(monkeypatch):
    answer = public(monkeypatch, vocabulary(), Request('Show plan'), cfg=PRESETS['schema'])
    assert_no_figure(answer)
    assert 'Declared measures' not in answer['reason']


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('released', [True, False])
def test_unreleased_month_offers_latest_without_an_answer_job(monkeypatch, tmp_path, configured, released):
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    try:
        fake.con.execute("UPDATE u_calendar_rows SET released = FALSE WHERE period >= DATE '2025-06-01'")
        if not released:
            fake.con.execute('UPDATE u_calendar_rows SET released = FALSE')
        v = vocabulary(semantics(release=True), aliases=[('Metric', 'ctx')])
        req = Request('Metric June 2025', term='Metric', terms_found=['Metric'])
        from kge.services.ask.engine.question import Period
        req.period = Period('month', '2025-06-30')
        def reply(turn):
            p = grounded(model(time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}}),
                         req.question, v, {'June 2025': 'time'})
            data = p.model_dump(mode='json', by_alias=True)
            for obligation in data['obligations']:
                for span in obligation['spans']:
                    span['turn'] = turn
            return Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(data))
        fake.requests.clear()
        answer = public(monkeypatch, v, req, Recording(reply) if configured else None, warehouses)
        assert_no_figure(answer)
        assert answer['reason'].startswith('June 2025 is not released yet.')
        if released:
            assert answer['reason'].endswith('Would you like that month instead?')
        assert ('latest released month is May 2025' in answer['reason']) is released
        assert ('Would you like that month instead?' in answer['reason']) is released
        assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()


@pytest.mark.parametrize('released', [False, True])
@pytest.mark.parametrize('runtime,month', [('reference', 'May'), ('external', 'April'), ('uncached', None)])
def test_release_offer_reads_only_its_cached_calendar_without_jobs(monkeypatch, tmp_path, released, runtime, month):
    from kge.services.ask.engine.graphview import Graph
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    try:
        v = vocabulary(semantics(release=True), aliases=[('Metric', 'ctx')])
        text = 'Metric June 2025'
        plan = grounded(model(time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}}),
                        text, v, {'June 2025': 'time'})
        graph = Graph.__new__(Graph)
        graph._release_periods = {
            'reference': (('u.calendar_rows', '2025-05-31', released, None),
                          ('private.calendar_rows', '2026-12-31', True, None)),
            'external': (('u.calendar_rows', '2025-04-30', released, None),)}
        monkeypatch.setattr(warehouses, 'select_runtime', lambda *a, **kw: {'ok': True, 'runtime_id': runtime})
        planner = Planner(v, warehouses, Hidden([], set(), []), runtimes=[runtime])
        fake.requests.clear()
        offer = planner.release_offer(text, plan, 'turn', graph.release_periods)
        assert offer == (f'The latest released month is {month} 2025. Would you like that month instead?' if released and month else '')
        assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()


def test_release_calendars_are_pinned_from_each_runtime_at_open(monkeypatch):
    from kge.services.ask.engine import planner
    from kge.services.ask.engine.graphview import Graph

    calls = []
    def execute(warehouses, statement, runtime, measures):
        calls.append(runtime)
        return {}, [('u.calendar_rows', '2025-05-31' if runtime == 'reference' else '2025-04-30', True, None)], {}
    monkeypatch.setattr(planner, 'execute_on_runtime', execute)
    warehouses = SimpleNamespace(
        list_runtimes=lambda: [{'id': runtime} for runtime in ('reference', 'external', 'unavailable')],
        select_runtime=lambda statements, runtimes: {'ok': runtimes != ['unavailable'], 'runtime_id': runtimes[0]})
    graph = Graph.__new__(Graph)
    sem = semantics(release=True)
    graph.warm_release_periods(sem, warehouses)
    assert calls == ['reference', 'external']
    assert graph._release_periods == {
        'reference': (('u.calendar_rows', '2025-05-31', True, None),),
        'external': (('u.calendar_rows', '2025-04-30', True, None),)}


@pytest.mark.parametrize('fiscal', [False, True])
@pytest.mark.parametrize('calendar', ['cal', 'fiscal'])
def test_context_ytd_calendar_is_checked_independently_of_endpoint(fiscal, calendar):
    from kge.semantics.coverage import coverage
    from kge.semantics.planperiods import time_bounds
    import datetime as dt

    sem = semantics()
    sem.d.CALENDARS['fiscal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], ytd_calendar='cal')
    v = vocabulary(sem)
    text = 'Metric ' + ('fiscal ' if fiscal else '') + 'year to date through June 2025'
    time = {'kind': 'ytd', 'through': {'kind': 'month', 'value': '2025-06', 'calendar': name(calendar)}, 'calendar': name(calendar)}
    assignments = {p.text: 'time' for p in ledger(text, 'turn', v) if p.kind in ('operator', 'time')}
    plan = grounded(model(time=time), text, v, assignments)
    decision = check_plan(plan, v, text)
    if fiscal == (calendar == 'fiscal'):
        assert isinstance(decision, CheckedPlan), decision
        assert not coverage(plan, text, 'turn', v)
        assert time_bounds(plan.time, v.calendars) == (dt.date(2025, 4 if fiscal else 1, 1), dt.date(2025, 7, 1))
    else:
        assert decision.kind == 'abstain'
        assert any('calendar' in p for p in coverage(plan, text, 'turn', v))
