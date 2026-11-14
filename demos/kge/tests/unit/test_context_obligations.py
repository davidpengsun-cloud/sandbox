import json
import re
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.adapters.warehouse import open_warehouses
from kge.pipeline.sqlguard import gate
from kge.semantics.compile import compile_plan
from kge.semantics.plan import HistoryEntry, Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planhistory import replacement
from kge.semantics.query import Rule
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.question import Request
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, time_dimension, vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_plan_relational_matrix import replace_context
from tests.unit.test_plan_ytd_abstention import public
from tests.unit.test_planner_jobs import jobs, setup


class Scripted:
    name = 'test:scripted'

    def __init__(self, plan, base=None):
        self.plan, self.base = plan, base

    def json(self, prompt):
        turn = json.loads(prompt.split('\n', 1)[1])['turn_id']
        def remap(value):
            if isinstance(value, dict):
                return {k: turn if k == 'turn' and v == 'turn' and self.base is None else remap(v) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v) for v in value]
            return value
        return {'kind': 'propose', 'base_turn': self.base,
                'plan': remap(self.plan.model_dump(mode='json', by_alias=True))}


def shared_vocabulary(joined=False):
    sem = semantics(joined=joined)
    sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], filters=())
    sem.contexts['scoped'] = replace_context(sem.contexts['ctx'], id='scoped', label='Scoped metric',
        filters=(Rule('scope', '', "events.category = 'A'", 'u.events'),))
    v = vocabulary(sem)
    return replace(v, phrases={**v.phrases, 'whole metric': ('ctx',), 'scoped metric': ('scoped',), 'metric': ('u.metric',)})


def predicate(identifier, op, number):
    return {'kind': 'compare', 'id': identifier, 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
            'op': op, 'value': {'kind': 'integer', 'value': number}, 'evidence': []}


def parts_plan(v, wrong=None):
    measures = []
    for i, filters in [('m', []), ('n', [predicate('positive', 'gt', 0)]), ('o', [predicate('negative', 'lt', 0)])]:
        measures.append({'kind': 'measure', 'id': i, 'measure': name('u.metric'),
                         'context': name('ctx' if wrong == i else 'scoped'), 'mode': 'current', 'filters': filters})
    text = 'Scoped metric above 0 and below 0'
    plan = grounded(model(measures=measures), text, v,
                    {'Scoped metric': ['m', 'n', 'o'], 'above': 'positive', 'below': 'negative', '0': ['positive', 'negative']})
    return plan, text


@pytest.mark.parametrize('wrong', [None, 'm', 'n', 'o'])
def test_every_target_keeps_the_named_context_through_model_read_and_gate(wrong):
    v = shared_vocabulary()
    plan, text = parts_plan(v, wrong)
    reply = read(Scripted(plan), text, v)
    assert isinstance(reply, Propose), reply
    checked = check_plan(reply.plan, v, text)
    if wrong:
        assert checked.kind == 'abstain', checked
        return
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    with connection() as con:
        con.execute('DELETE FROM events')
        con.execute("INSERT INTO events VALUES ('2025-04-10', 19, 'A', 1, 'x', NULL), ('2025-06-10', -7, 'A', 2, 'x', NULL), ('2025-06-10', 101, 'B', 3, 'x', NULL)")
        assert gate(con, compiled.answer.sql, compiled.answer.allow)['passed']
        actual = con.execute(compiled.answer.sql).fetchone()[:3]
        expected = con.execute("SELECT SUM(value), SUM(CASE WHEN value > 0 THEN value END), SUM(CASE WHEN value < 0 THEN value END) FROM events WHERE category = 'A'").fetchone()
        assert actual == expected


def test_measure_name_evidence_cannot_borrow_an_unnamed_context():
    v = shared_vocabulary()
    plan, text = parts_plan(v, 'n')
    data = plan.model_dump(mode='json', by_alias=True)
    span = data['obligations'][0]['spans'][0]
    data['measures'][1]['measure']['evidence'] = [span]
    data['obligations'][0]['targets'] = ['m', 'o']
    checked = check_plan(QueryPlan.model_validate(data), v, text)
    assert checked.kind == 'abstain' and checked.missing == ('A name has unrelated evidence.',)


def test_another_explicitly_named_context_can_supply_a_target():
    v = shared_vocabulary()
    plan, text = parts_plan(v, 'n')
    text += ' and Whole metric'
    plan = grounded(plan, text, v, {'Scoped metric': ['m', 'n', 'o'], 'Whole metric': 'n',
        'above': 'positive', 'below': 'negative', '0': ['positive', 'negative']})
    reply = read(Scripted(plan), text, v)
    assert isinstance(check_plan(reply.plan, v, text), CheckedPlan)


def test_public_repeated_context_parts_return_separate_figures(monkeypatch, tmp_path):
    v = shared_vocabulary()
    plan, text = parts_plan(v)
    with connection() as con:
        con.execute('DELETE FROM events')
        con.execute("INSERT INTO events VALUES ('2025-04-10', 19, 'A', 1, 'x', NULL), ('2025-06-10', -7, 'A', 2, 'x', NULL), ('2025-06-10', 101, 'B', 3, 'x', NULL)")
        expected = con.execute("SELECT SUM(value), SUM(CASE WHEN value > 0 THEN value END), SUM(CASE WHEN value < 0 THEN value END) FROM events WHERE category = 'A'").fetchone()
        rows = con.execute('SELECT * FROM events').fetchall()
    from kge.adapters.warehouse import open_estate
    with open_estate(tmp_path, 'test').open('u', write=True) as session:
        session.execute('CREATE TABLE events(occurred DATE, value DECIMAL(18,2), category VARCHAR, entity_id INT, source VARCHAR, private VARCHAR)')
        for row in rows:
            session.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)', row)
    warehouses = open_warehouses(SimpleNamespace(warehouses=tmp_path), ['u'])
    try:
        answer = public(monkeypatch, v, Request(text), Scripted(plan), warehouses)
        assert answer['behaviour'] == 'combined', answer
        assert answer['guard']['passed']
        assert [Decimal(c['value']['value']) for c in answer['result']['rows'][0]['cells']] == list(expected)
        assert not answer.get('figure') and not answer.get('parts')
        assert all(str(n) in answer['answer_text'] for n in expected)
    finally:
        warehouses.close()


def followup_fixture():
    sem = semantics(joined=True)
    sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], filters=())
    sem.contexts['scoped'] = replace_context(sem.contexts['ctx'], id='scoped', label='Scoped metric')
    sem.tables['u.members']['eligible'] = 'BOOLEAN'
    sem.tables['u.snapshots'] = dict(sem.tables['u.events'])
    path = replace(sem.contexts['ctx'].joins[0], id='snapshot_member', source='u.snapshots',
                   condition=sem.contexts['ctx'].joins[0].condition.replace('events.', 'snapshots.'))
    sem.contexts['scoped'] = replace_context(sem.contexts['scoped'], fact='u.snapshots', values=('u.snapshots.value',),
        expression='sum(snapshots.value)', filters=(Rule('scope', '', "snapshots.category = 'A'", 'u.snapshots'),), joins=(path,))
    sem.d.PERIODS['u.snapshots'] = dict(sem.d.PERIODS['u.events'])
    sem.d.FILTERS = [(re.compile('eligible accounts', re.I), 'members.eligible', '= TRUE', None, True)]
    sem.d.FILTER_COLUMNS = ['u.members.eligible']
    sem.declared[path.id] = path
    v = vocabulary(sem)
    v = replace(v, phrases={**v.phrases, 'whole metric': ('ctx',), 'scoped metric': ('scoped',), 'metric': ('u.metric',)})
    base = model().measures[0].model_dump(mode='json')
    text = 'Whole metric and Scoped metric by month 2025-04 through 2025-06'
    plan = grounded(model(measures=[base, dict(base, id='n', context=name('scoped'))],
        dimensions=[time_dimension()], time=monthly_time()), text, v,
        {'Whole metric': 'm', 'Scoped metric': 'n', 'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time'})
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    data = deepcopy(plan.model_dump(mode='json', by_alias=True))
    for m, path in zip(data['measures'], ('member_join', 'snapshot_member')):
        m['filters'] = [{'kind': 'compare', 'id': 'f' + m['id'],
            'left': {'kind': 'column', 'name': name('u.members.eligible'), 'path': [name(path)]},
            'op': 'eq', 'value': {'kind': 'boolean', 'value': True}, 'evidence': []}]
    followup = 'Now just eligible accounts'
    data['obligations'].append({'id': 'flag', 'spans': [{'turn': 'next', 'start': 9, 'end': len(followup)}],
                                'kind': 'name', 'targets': ['fm', 'fn'], 'state': 'applied'})
    return v, QueryPlan.model_validate(data), followup, history


def test_followup_filters_both_context_paths_and_matches_independent_sql():
    v, plan, text, history = followup_fixture()
    reply = read(Scripted(plan, 'turn'), text, v, history, turn='next')
    assert isinstance(reply, Propose), reply
    problem, pieces = replacement(reply, history, v, text, 'next')
    assert problem is None, problem
    checked = check_plan(reply.plan, v, text, 'next', pieces)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    with connection() as con:
        con.execute('CREATE TABLE snapshots AS SELECT * FROM events')
        con.execute("INSERT INTO events VALUES ('2025-06-11', 90, 'B', 2, 'x', NULL)")
        con.execute("INSERT INTO snapshots VALUES ('2025-06-11', 80, 'A', 2, 'x', NULL)")
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR, eligible BOOLEAN)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'A', TRUE), (2, 'x', 'A', FALSE)")
        assert gate(con, compiled.answer.sql, compiled.answer.allow)['passed']
        actual = con.execute(compiled.answer.sql).fetchall()
        for n, table in enumerate(('events', 'snapshots'), 1):
            scope = "AND f.category = 'A'" if table == 'snapshots' else ''
            expected = dict(con.execute(f"SELECT CAST(date_trunc('month', f.occurred) AS DATE), SUM(f.value) FROM {table} f JOIN members a ON f.entity_id = a.entity_id AND f.source = a.source WHERE a.eligible {scope} GROUP BY 1").fetchall())
            assert {r[0]: r[n] for r in actual if r[n] is not None} == expected


@pytest.mark.parametrize('change', ['context', 'mode', 'value', 'remove', 'unevidenced'])
def test_filter_addition_exception_preserves_genuine_followup_change_evidence(change):
    v, plan, text, history = followup_fixture()
    data = plan.model_dump(mode='json', by_alias=True)
    if change == 'context':
        data['measures'][0]['context'] = name('scoped')
    elif change == 'mode':
        data['measures'][0]['mode'] = 'as_reported'
    elif change == 'value':
        old = history[0].plan.model_dump(mode='json', by_alias=True)
        old['measures'][0]['filters'] = [deepcopy(data['measures'][0]['filters'][0])]
        old['measures'][0]['filters'][0]['value']['value'] = False
        history = (history[0].model_copy(update={'plan': QueryPlan.model_validate(old)}),)
    elif change == 'remove':
        old = history[0].plan.model_dump(mode='json', by_alias=True)
        old['measures'][0]['filters'] = [predicate('old', 'gt', 1)]
        history = (history[0].model_copy(update={'plan': QueryPlan.model_validate(old)}),)
    else:
        data['obligations'].pop()
    reply = Propose(kind='propose', base_turn='turn', plan=QueryPlan.model_validate(data))
    problem, _ = replacement(reply, history, v, text, 'next')
    if change == 'value':
        assert problem is not None
    else:
        assert problem is not None and 'explicit follow-up evidence' in problem.missing[0]


@pytest.mark.parametrize('wrong', [False, True])
def test_context_mistake_has_no_native_jobs_and_valid_parts_keep_one_job(monkeypatch, tmp_path, wrong):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        v = shared_vocabulary()
        plan, text = parts_plan(v, 'n' if wrong else None)
        reply = read(Scripted(plan), text, v)
        for _ in range(2):
            fake.requests.clear()
            answer = Planner(v, warehouses, Hidden([], set(), [])).answer(text, reply)
            assert answer['kind'] == ('abstain' if wrong else 'result'), answer
            assert jobs(fake) == ((0, 0) if wrong else (1, 1))
    finally:
        fake.con.close()


def test_two_context_followup_keeps_native_jobs_constant_and_validates_history(monkeypatch, tmp_path):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        fake.con.execute("INSERT INTO u_events VALUES ('2025-05-11', 3, 'A', 1, 'x', NULL)")
        fake.con.execute('CREATE TABLE u_snapshots AS SELECT * FROM u_events')
        fake.con.execute('CREATE TABLE u_members(entity_id INT, source VARCHAR, grouping VARCHAR, eligible BOOLEAN)')
        fake.con.execute("INSERT INTO u_members VALUES (1, 'x', 'A', TRUE), (2, 'x', 'A', FALSE)")
        fake.con.execute("INSERT INTO u_events VALUES ('2025-06-11', 90, 'B', 2, 'x', NULL)")
        fake.con.execute("INSERT INTO u_snapshots VALUES ('2025-06-11', 80, 'A', 2, 'x', NULL)")
        v, plan, text, history = followup_fixture()
        reply = read(Scripted(plan, 'turn'), text, v, history, turn='next')
        planner = Planner(v, warehouses, Hidden([], set(), []))
        for _ in range(2):
            fake.requests.clear()
            answer = planner.answer(text, reply, turn='next', history=history)
            assert answer['kind'] == 'result', answer
            assert jobs(fake) == (1, 1)
            totals = [r for r in answer['result']['rows'] if r.get('total')]
            assert len(totals) == 1
            assert [Decimal(c['value']['value']) for c in totals[0]['cells']] == [Decimal(18), Decimal(18)]
    finally:
        fake.con.close()


@pytest.mark.parametrize('wrong', ['value', 'path'])
def test_followup_filter_evidence_must_hold_on_each_context(wrong):
    v, plan, text, history = followup_fixture()
    data = plan.model_dump(mode='json', by_alias=True)
    target = data['measures'][1]['filters'][0]
    if wrong == 'value':
        target['value']['value'] = False
    else:
        target['left']['path'] = [name('member_join')]
    reply = read(Scripted(QueryPlan.model_validate(data), 'turn'), text, v, history, turn='next')
    assert isinstance(reply, Propose), reply
    problem, pieces = replacement(reply, history, v, text, 'next')
    assert problem is None, problem
    checked = check_plan(reply.plan, v, text, 'next', pieces)
    assert checked.kind == 'abstain', checked


def independently_grounded_followup(grounding, scope, qualified=False):
    from kge.semantics.coverage import ledger
    from kge.semantics.planvocabulary import Entry
    sem = semantics()
    sem.contexts['second'] = replace_context(sem.contexts['ctx'], id='second', label='Second')
    values = [Entry('member-b', 'value', 'Selected', 'u', column='u.events.category', value='B')]
    v = vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'second')], values=values)
    measure = model().measures[0].model_dump(mode='json')
    question = 'First and Second'
    base = grounded(model(measures=[measure, dict(measure, id='n', context=name('second'))]), question, v,
                    {'First': 'm', 'Second': 'n'})
    assert isinstance(check_plan(base, v, question), CheckedPlan)
    data = base.model_dump(mode='json', by_alias=True)
    targets = []
    for measure in data['measures'] if scope == 'all' else data['measures'][:1] if scope == 'first' else []:
        identifier = 'f' + measure['id']
        measure['filters'] = [predicate(identifier, 'ge', 10) if grounding == 'numeric' else
            dict(kind='compare', id=identifier, left=dict(kind='column', name=name('u.events.category'), path=[]),
                 op='eq', value=dict(kind='text', value='B'), evidence=[])]
        targets.append(identifier)
    if scope == 'global':
        data['filters'] = [predicate('f', 'ge', 10) if grounding == 'numeric' else
            dict(kind='compare', id='f', left=dict(kind='column', name=name('u.events.category'), path=[]),
                 op='eq', value=dict(kind='text', value='B'), evidence=[])]
        targets = ['f']
    text = 'Now just ' + ('First ' if qualified else '') + ('value at least 10' if grounding == 'numeric' else 'category equal to Selected')
    pieces = ledger(text, 'next', v)
    assert [p.kind for p in pieces if p.text != 'First'] == (['name', 'operator', 'literal'] if grounding == 'numeric' else ['name', 'operator', 'name'])
    for index, piece in enumerate(pieces):
        data['obligations'].append(dict(id='follow' + str(index), state='applied', kind='name',
            spans=[piece.span.model_dump()], targets=['m'] if piece.text == 'First' else targets))
    history = (HistoryEntry(turn_id='turn', question=question, plan=base, clarification=None, binding=v.binding),)
    return v, QueryPlan.model_validate(data), text, history


@pytest.mark.parametrize('grounding', ['numeric', 'member'])
@pytest.mark.parametrize('scope', ['first', 'all', 'global'])
@pytest.mark.parametrize('qualified', [False, True])
def test_followup_filter_coverage_survives_independent_grounding_and_model_read(grounding, scope, qualified):
    v, plan, text, history = independently_grounded_followup(grounding, scope, qualified)
    reply = read(Scripted(plan, 'turn'), text, v, history, turn='next')
    assert isinstance(reply, Propose), reply
    problem, pieces = replacement(reply, history, v, text, 'next')
    assert problem is None, problem
    checked = check_plan(reply.plan, v, text, 'next', pieces)
    with connection() as con:
        con.execute('DELETE FROM events')
        con.execute("INSERT INTO events VALUES ('2025-06-01', 5, 'A', 1, 'x', NULL), ('2025-06-01', 80, 'B', 2, 'x', NULL)")
        base = check_plan(history[0].plan, v, history[0].question)
        assert con.execute(compile_plan(base).answer.sql).fetchone()[:2] == (85, 85)
        if scope == 'first' and not qualified:
            assert checked.kind == 'abstain', checked
            assert 'every applicable retained measure' in str(checked.missing)
        else:
            assert isinstance(checked, CheckedPlan), checked
            compiled = compile_plan(checked)
            assert gate(con, compiled.answer.sql, compiled.answer.allow)['passed']
            assert con.execute(compiled.answer.sql).fetchone()[:2] == ((80, 85) if scope == 'first' else (80, 80))


@pytest.mark.parametrize('grounding', ['numeric', 'member'])
@pytest.mark.parametrize('scope', ['first', 'all', 'global'])
def test_independently_grounded_followup_keeps_native_bigquery_job_budget(monkeypatch, tmp_path, grounding, scope):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        fake.con.execute('DELETE FROM u_events')
        fake.con.execute("INSERT INTO u_events VALUES ('2025-06-01', 5, 'A', 1, 'x', NULL), ('2025-06-01', 80, 'B', 2, 'x', NULL)")
        v, plan, text, history = independently_grounded_followup(grounding, scope)
        reply = read(Scripted(plan, 'turn'), text, v, history, turn='next')
        for _ in range(2):
            fake.requests.clear()
            answer = Planner(v, warehouses, Hidden([], set(), [])).answer(text, reply, 'next', history)
            assert answer['kind'] == ('abstain' if scope == 'first' else 'result'), answer
            assert jobs(fake) == ((0, 0) if scope == 'first' else (1, 1))
            if scope != 'first':
                assert [Decimal(c['value']['value']) for c in answer['result']['rows'][0]['cells']] == [80, 80]
    finally:
        fake.con.close()
