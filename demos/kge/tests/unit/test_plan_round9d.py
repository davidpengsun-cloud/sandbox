from dataclasses import replace

import pytest
import sqlglot
from sqlglot import exp

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.compiler.drafts import _qualified_aliases
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_plan_relational_matrix import replace_context


def test_qualified_alias_requires_the_whole_population():
    recipes = [{'table': 'booking', 'column': 'status', 'op': '=', 'value': 'settled'},
               {'table': 'booking', 'column': 'channel', 'op': '=', 'value': 'online'}]
    assert _qualified_aliases(recipes, 'Turnover') == []


def test_qualified_union_alias_says_or_and_its_and_wording():
    assert _qualified_aliases([{'table': 'booking', 'column': 'status', 'op': 'IN', 'value': ['a', 'b']}], 'Turnover') == ['a or b booking Turnover', 'a and b booking Turnover']


def test_measure_divided_by_declared_population_count_executes():
    sem = semantics()
    v = vocabulary(sem)
    v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], population='entity')})
    p = model(measures=[model().measures[0], {'kind': 'distinct_count', 'id': 'c', 'entity': name('entity'),
              'context': name('ctx'), 'mode': 'current', 'filters': []}],
              derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'c', 'evidence': []}])
    text = 'ratio of Metric to count Entities'
    checked = check_plan(grounded(p, text, v, {'count': 'c', 'Entities': 'c', 'ratio': 'r'}), v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked)
    with connection() as con:
        assert con.execute(c.answer.sql).fetchone()[:3] == (15, 1, 15)
    assert next(col for col in c.columns if col.id == 'r').currency == 'USD'


@pytest.mark.parametrize('aliases', [False, True])
def test_flow_and_snapshot_align_through_distinct_declared_paths(aliases):
    sem = semantics(joined=True)
    first = sem.contexts['ctx']
    if aliases:
        first = replace_context(first, joins=(replace(first.joins[0], alias='flow_role',
            condition=first.joins[0].condition.replace('members.', 'flow_role.')),))
        sem.contexts['ctx'] = first
    sem.tables['u.snapshots'] = dict(sem.tables['u.events'])
    other = replace(first.joins[0], id='snapshot_member', source='u.snapshots',
                    condition=first.joins[0].condition.replace('events.', 'snapshots.'))
    if aliases:
        other = replace(other, alias='snapshot_role', condition=other.condition.replace('flow_role.', 'snapshot_role.'))
    sem.contexts['snapshot'] = replace_context(first, id='snapshot', label='Snapshot', term='Balance', measure='u.balance',
        fact='u.snapshots', expression='sum(snapshots.value)', values=('u.snapshots.value',), filters=(), joins=(other,))
    sem.d.PERIODS['u.snapshots'] = dict(sem.d.PERIODS['u.events'])
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'],
        [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('u.balance', 'USD', 'entities', False)])
    p = model(measures=[model().measures[0], {'kind': 'measure', 'id': 's', 'measure': name('u.balance'), 'context': name('snapshot'), 'mode': 'current', 'filters': []}],
        dimensions=[{'kind': 'column', 'id': 'd', 'bindings': [
            {'context': name(ctx), 'column': name('u.members.grouping'), 'path': [name(path)], 'level': None}
            for ctx, path in [('ctx', 'member_join'), ('snapshot', 'snapshot_member')]]}])
    text = 'Metric Balance by grouping'
    checked = check_plan(grounded(p, text, v, {'Balance': 's', 'by': 'd', 'grouping': 'd'}), v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked)
    with connection() as con:
        con.execute('CREATE TABLE snapshots AS SELECT * FROM events')
        con.execute("CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)")
        con.execute("INSERT INTO members VALUES (1, 'x', 'North')")
        assert con.execute(c.answer.sql).fetchone()[:3] == ('North', 15, 5)


@pytest.mark.parametrize('kind', ['entity', 'context', 'value'])
def test_hidden_nonunit_collision_keeps_the_readable_meaning(kind):
    sem = semantics()
    sem.tables['v.hidden'] = {'key': 'INTEGER'}
    values = []
    if kind == 'entity':
        sem.entities['hidden_entity'] = {'label': 'Metric', 'table': 'v.hidden', 'key': ['key']}
    elif kind == 'context':
        sem.contexts['hidden_context'] = replace_context(sem.contexts['ctx'], id='hidden_context', label='Metric',
            fact='v.hidden', measure='v.metric')
    else:
        values = [Entry('hidden_value', 'value', 'Metric', 'v', column='v.hidden.key', value=1)]
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'],
        [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('v.metric', 'USD', 'entities', True)], values=values)
    assert not v.denial('Metric')
    assert isinstance(check_plan(grounded(model(), 'Metric', v), v, 'Metric'), CheckedPlan)


def test_bigquery_conditional_composite_count_has_no_tuple_and_no_encoding_collisions():
    sql = 'SELECT COUNT(DISTINCT CASE WHEN k1 IS NOT NULL AND k2 IS NOT NULL THEN (k1, k2) END) FROM items'
    rewritten = rewrite_sql(sql, lambda unit: 'project.dataset.' + unit, 'u')
    tree = sqlglot.parse_one(rewritten, read='bigquery')
    assert not list(tree.find_all(exp.Tuple)), rewritten
    assert list(tree.find_all(exp.Struct)), rewritten
    assert 'TO_JSON_STRING' in rewritten


def test_percent_change_compiles_and_executes_against_independent_values():
    import datetime as dt
    from tests.plan_fixtures import monthly_time
    v = vocabulary()
    p = model(time=monthly_time('2025-06', '2025-06'),
        comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}],
        derived=[{'kind': 'percent_change', 'id': 'p', 'numerator': 'c/m/base', 'denominator': 'c/m/other', 'evidence': []}])
    text = 'Metric 2025-06 through 2025-06 previous period percent change'
    checked = check_plan(grounded(p, text, v, {'2025-06': 'time', 'through': 'time', 'previous period': 'c', 'percent change': 'p'}), v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked)
    with connection([(dt.date(2025, 5, 1), 20, 'A', 1, 'x'), (dt.date(2025, 6, 1), 30, 'A', 1, 'x')]) as con:
        assert con.execute(c.answer.sql).fetchone()[:4] == (30, 30, 20, .5)


@pytest.mark.parametrize('phrase', ['hold both', 'line up', 'raised'])
def test_relationship_grammar_has_no_dependency_on_pack_nouns(phrase):
    from kge.semantics.coverage import ledger
    for label in ('Entities', 'Novel population'):
        sem = semantics()
        sem.entities['entity']['label'] = label
        pieces = ledger(f'Metric {phrase} {label}', 'turn', vocabulary(sem))
        assert any(piece.text == phrase and piece.operation == 'membership' for piece in pieces)
        assert not any(piece.kind == 'unknown' for piece in pieces)


@pytest.mark.parametrize('conditional', [False, True])
def test_bigquery_composite_encoding_preserves_component_boundaries_and_nulls(conditional):
    from tests.unit.test_plan_compiler import duckdb
    value = '(a, b)'
    if conditional:
        value = 'CASE WHEN a IS NOT NULL AND b IS NOT NULL THEN (a, b) END'
    sql = f'SELECT COUNT(DISTINCT {value}) FROM keys'
    translated = sqlglot.transpile(rewrite_sql(sql, lambda unit: unit), read='bigquery', write='duckdb')[0]
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE keys(a VARCHAR, b VARCHAR)')
        con.execute("INSERT INTO keys VALUES ('a||b', 'c'), ('a', 'b||c'), ('\\N', 'x'), (NULL, 'x')")
        assert con.execute(translated).fetchone()[0] == (3 if conditional else 4)


@pytest.mark.parametrize('operation', ['ratio', 'percent_change'])
def test_new_arithmetic_keeps_one_native_answer_statement(monkeypatch, tmp_path, operation):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import monthly_time
    from tests.unit.test_planner_jobs import jobs, setup
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    v = vocabulary()
    v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], population='entity')})
    text = 'ratio of Metric to count Entities'
    p = model(measures=[model().measures[0], {'kind': 'distinct_count', 'id': 'c', 'entity': name('entity'),
        'context': name('ctx'), 'mode': 'current', 'filters': []}],
        derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'c', 'evidence': []}])
    assignments = {'count': 'c', 'Entities': 'c', 'ratio': 'r'}
    if operation == 'percent_change':
        fake.con.execute("INSERT INTO u_events VALUES ('2025-05-10', 20, 'A', 2, 'x', NULL)")
        p = model(time=monthly_time('2025-06', '2025-06'),
            comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}],
            derived=[{'kind': 'percent_change', 'id': 'r', 'numerator': 'c/m/base', 'denominator': 'c/m/other', 'evidence': []}])
        text = 'Metric 2025-06 through 2025-06 previous period percent change'
        assignments = {'2025-06': 'time', 'through': 'time', 'previous period': 'c', 'percent change': 'r'}
    p = grounded(p, text, v, assignments)
    for _ in range(2):
        fake.requests.clear()
        out = Planner(v, warehouses, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
        assert out['kind'] == 'result', out
        assert float(out['result']['rows'][0]['cells'][-1]['value']['value']) == (15 if operation == 'ratio' else -.75)
        assert jobs(fake) == (1, 1)
    fake.con.close()


@pytest.mark.parametrize('phrase', ['without the filter', 'drop Metric', 'just the total', 'drop Metric sparkly'])
def test_deletion_cannot_select_an_ambiguous_or_unrelated_antecedent(phrase):
    from kge.semantics.coverage import removal_spans
    from kge.semantics.planhistory import nodes
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
        'op': 'gt', 'value': {'kind': 'integer', 'value': 1}, 'evidence': []}
    first = model().measures[0].model_dump(mode='json')
    base = model(measures=[first, dict(first, id='n')], filters=[predicate, dict(predicate, id='g')])
    target = 'f' if phrase == 'without the filter' else 'n'
    data = base.model_dump(mode='json', by_alias=True)
    data['filters'] = [p for p in data['filters'] if p['id'] != target]
    data['measures'] = [m for m in data['measures'] if m['id'] != target]
    data['obligations'] = [{'id': 'o', 'spans': [{'turn': 'turn', 'start': 0, 'end': len(phrase)}], 'kind': 'name', 'targets': [target], 'state': 'removed'}]
    p = type(base).model_validate(data)
    with pytest.raises(ValueError):
        removal_spans(p, phrase, 'turn', vocabulary(), {target: nodes(base)[target]}, nodes(base))


def test_removed_obligation_without_a_checked_base_never_executes():
    data = model().model_dump(mode='json', by_alias=True)
    data['obligations'] = [{'id': 'o', 'spans': [{'turn': 'turn', 'start': 0, 'end': 18}], 'kind': 'name', 'targets': ['missing'], 'state': 'removed'}]
    assert check_plan(type(model()).model_validate(data), vocabulary(), 'without the filter').kind == 'abstain'


def test_shared_hidden_context_label_preserves_only_the_readable_meaning():
    sem = semantics()
    sem.tables['v.hidden'] = {'value': 'DECIMAL'}
    sem.contexts['hidden'] = replace_context(sem.contexts['ctx'], id='hidden', label='Metric', fact='v.hidden', measure='v.metric')
    specs = [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('v.metric', 'USD', 'entities', True)]
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'], specs)
    assert not v.denial('Metric') and not v.denial('u Metric') and v.denial('u hidden')


def test_aggregate_conversion_preserves_a_declared_hidden_context_label(monkeypatch):
    from types import SimpleNamespace
    from kge.services.ask.engine import planrules
    sem = semantics()
    sem.tables['v.hidden'] = {'value': 'DECIMAL'}
    sem.contexts['hidden'] = replace_context(sem.contexts['ctx'], id='hidden', fact='v.hidden',
        label='Metric', term='Metric', measure='v.metric', aggregation='count')
    sem.context_declarations = frozenset(sem.contexts)
    sem.measure_specs = {i: MeasureSpec(i, None, 'entities', False) for i in ('u.metric', 'v.metric')}
    sem.output_metadata = {'hidden': {'dtype': 'DECIMAL', 'col': 'value', 'qualified': 'v.hidden.value', 'tbl': 'hidden'}}
    sem.no_join_declarations = []
    sem.plan_writes = (0, ())
    engine = SimpleNamespace(g=SimpleNamespace(readable_units=lambda _: ['u'], may=lambda *_: True,
        domain=lambda: {'vocabulary': []}, writes=0, scoped_vocabulary=lambda binding, variant, writes, read: read()),
        identity={}, ALIASES={}, UNIT_WORDS={}, SOURCE_SETS={},
        ENTITY_WORDS={}, UNIT_LABEL={}, GROUP_BY={}, ACRONYMS={}, RESTRICTED_PHRASES={})
    req = SimpleNamespace(agg='sum', terms_found=['Metric'], filters=[], restricted=[], question='Metric')
    monkeypatch.setattr(planrules, 'snapshot', lambda *_: sem)
    monkeypatch.setattr(planrules, 'cohort_snapshot', lambda _, snapshot, __: snapshot)
    v = planrules.vocabulary(engine, 'reader', 'analysis', SimpleNamespace(products=[]), SimpleNamespace(graph=True), req)
    assert not v.denial('Metric') and v.denial('v')


@pytest.mark.parametrize('slot,followup', [('filters', 'without the filter'), ('dimensions', 'just the total')])
def test_native_history_deletion_keeps_one_answer_job(monkeypatch, tmp_path, slot, followup):
    from kge.semantics.plan import HistoryEntry, Propose, QueryPlan
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import time_dimension
    from tests.unit.test_planner_jobs import jobs, setup
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    v = vocabulary()
    if slot == 'filters':
        p = model(filters=[{'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
            'op': 'gt', 'value': {'kind': 'integer', 'value': 6}, 'evidence': []}])
        text, assignments = 'Metric value above 6', {'value': 'f', 'above': 'f', '6': 'f'}
    else:
        p = model(dimensions=[time_dimension()])
        text, assignments = 'Metric by month', {'by': 't', 'month': 't'}
    p = grounded(p, text, v, assignments)
    history = [HistoryEntry(turn_id='turn', question=text, plan=p, clarification=None, binding=v.binding)]
    data = p.model_dump(mode='json', by_alias=True)
    removed = {n['id'] for n in data[slot]}
    data[slot] = []
    for obligation in data['obligations']:
        obligation['targets'] = [target for target in obligation['targets'] if target not in removed]
    data['obligations'] = [o for o in data['obligations'] if o['targets']]
    data['obligations'].append({'id': 'remove', 'kind': 'name', 'targets': sorted(removed), 'state': 'removed',
        'spans': [{'turn': 'next', 'start': 0, 'end': len(followup)}]})
    for _ in range(2):
        fake.requests.clear()
        out = Planner(v, warehouses, Hidden([], set(), [])).answer(followup,
            Propose(kind='propose', base_turn='turn', plan=QueryPlan.model_validate(data)), turn='next', history=history)
        assert out['kind'] == 'result', out
        assert float(out['result']['rows'][0]['cells'][0]['value']['value']) == 15
        assert jobs(fake) == (1, 1)
    fake.con.close()


def test_deletion_evidence_cannot_overlap():
    from kge.semantics.coverage import removal_spans
    from kge.semantics.planhistory import nodes
    from tests.plan_fixtures import time_dimension
    base = model(dimensions=[time_dimension()])
    data = model().model_dump(mode='json', by_alias=True)
    obligation = {'id': 'o', 'kind': 'name', 'targets': ['t'], 'state': 'removed',
        'spans': [{'turn': 'turn', 'start': 0, 'end': 14}]}
    data['obligations'] = [obligation, dict(obligation, id='other')]
    with pytest.raises(ValueError, match='overlaps'):
        removal_spans(type(base).model_validate(data), 'just the total', 'turn', vocabulary(), {'t': nodes(base)['t']})
