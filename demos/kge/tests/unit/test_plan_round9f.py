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


@pytest.mark.parametrize('kind', ['value', 'entity'])
def test_hidden_labels_on_restricted_fields_keep_the_readable_meaning(kind):
    sem = semantics()
    sem.tables['v.hidden'] = {'key': 'INTEGER'}
    sem.d.RESTRICTED_COLUMNS.append(('v', 'hidden', 'key'))
    values = []
    if kind == 'entity':
        sem.entities['hidden_entity'] = {'label': 'Metric', 'table': 'v.hidden', 'key': ['key']}
    else:
        values = [Entry('hidden_value', 'value', 'Metric', 'v', column='v.hidden.key', value=1)]
    v = vocabulary(sem, aliases=[('Metric', 'hidden_' + kind)], values=values)
    assert not v.denial('Metric')
    assert isinstance(check_plan(grounded(model(), 'Metric', v), v, 'Metric'), CheckedPlan)
    assert v.denial('v.hidden.key') and 'v.hidden.key' not in v.entries


@pytest.mark.parametrize('removed', [[], ['d']])
def test_share_of_total_checks_denominator_scope_and_executes(removed):
    v = vocabulary()
    p = model(dimensions=[{'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}],
        derived=[{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': removed, 'remove_filters': [], 'evidence': []}])
    text = 'Metric by category share of total'
    checked = check_plan(grounded(p, text, v, {'by': 'd', 'category': 'd', 'share': 's'}), v, text)
    if not removed:
        assert not isinstance(checked, CheckedPlan), checked
        assert checked.kind == 'abstain' and 'denominator' in ' '.join(checked.missing)
        return
    assert isinstance(checked, CheckedPlan), checked
    with connection() as con:
        con.execute("INSERT INTO events VALUES ('2025-06-10', 5, 'B', 2, 'x', NULL)")
        assert [(r[0], r[2]) for r in con.execute(compile_plan(checked).answer.sql).fetchall()] == [('A', .75), ('B', .25)]


@pytest.mark.parametrize('secondary', [False, True])
@pytest.mark.parametrize('uppercase', [False, True])
def test_alignment_retains_the_declared_role_keys(secondary, uppercase):
    sem = semantics(joined=True)
    first = sem.contexts['ctx']
    sem.tables['u.snapshots'] = {**sem.tables['u.events'], 'secondary_entity_id': 'INTEGER'}
    other = replace(first.joins[0], id='snapshot_member', source='u.snapshots', alias='snapshot_role',
        condition=first.joins[0].condition.replace('events.', 'snapshots.').replace('members.', 'snapshot_role.'))
    if secondary:
        other = replace(other, condition=other.condition.replace('snapshots.entity_id', 'snapshots.secondary_entity_id'))
    if uppercase:
        other = replace(other, condition=other.condition.upper())
    sem.contexts['snapshot'] = replace_context(first, id='snapshot', label='Balance', term='Balance', measure='u.balance',
        fact='u.snapshots', expression='sum(snapshots.value)', values=('u.snapshots.value',), filters=(), joins=(other,))
    sem.d.PERIODS['u.snapshots'] = dict(sem.d.PERIODS['u.events'])
    v = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'],
        [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('u.balance', 'USD', 'entities', False)])
    p = model(measures=[model().measures[0], {'kind': 'measure', 'id': 's', 'measure': name('u.balance'),
        'context': name('snapshot'), 'mode': 'current', 'filters': []}], dimensions=[{'kind': 'column', 'id': 'd', 'bindings': [
            {'context': name(ctx), 'column': name('u.members.grouping'), 'path': [name(path)], 'level': None}
            for ctx, path in [('ctx', 'member_join'), ('snapshot', 'snapshot_member')]]}])
    text = 'Metric Balance by grouping'
    checked = check_plan(grounded(p, text, v, {'Balance': 's', 'by': 'd', 'grouping': 'd'}), v, text)
    assert isinstance(checked, CheckedPlan) is not secondary, checked
    if secondary:
        assert checked.kind == 'abstain' and 'role' in ' '.join(checked.missing)


@pytest.mark.parametrize('kind,label', [('fiscal_quarter', 'FY26-Q1'), ('calendar_quarter', '2025-Q2')])
def test_quarter_labels_are_dates_before_grouping_in_both_dialects(monkeypatch, tmp_path, kind, label):
    sem = semantics()
    sem.tables['u.events']['occurred'] = 'VARCHAR'
    sem.d.PERIODS['u.events']['kind'] = kind
    if kind == 'fiscal_quarter':
        sem.d.CALENDARS['cal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    v = vocabulary(sem)
    p = model(time={'kind': 'single', 'period': {'kind': kind, 'value': label, 'calendar': name('cal')}},
        dimensions=[{'kind': 'time', 'id': 't', 'calendar': name('cal'), 'grain': kind}])
    text = f'Metric by {kind.replace("_", " ")} {label}'
    checked = check_plan(grounded(p, text, v, {'by': 't', kind.replace('_', ' '): 't', label: 'time'}), v, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    with connection() as con:
        con.execute('ALTER TABLE events ALTER occurred TYPE VARCHAR')
        con.execute('UPDATE events SET occurred = ?', [label])
        row = con.execute(compiled.answer.sql).fetchone()
        assert str(row[0]) == '2025-04-01' and row[1] == 15
    translated = rewrite_sql(compiled.answer.sql, lambda unit: 'project.dataset.' + unit, 'u')
    tree = sqlglot.parse_one(translated, read='bigquery')
    assert list(tree.find_all(exp.DateFromParts)), translated
    with connection() as con:
        con.execute('ALTER TABLE events ALTER occurred TYPE VARCHAR')
        con.execute('UPDATE events SET occurred = ?', [label])
        roundtrip = sqlglot.transpile(translated, read='bigquery', write='duckdb')[0]
        roundtrip = roundtrip.replace('"project"."dataset"."u_events"', 'events')
        assert con.execute(roundtrip).fetchone()[:2] == row[:2]
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        fake.con.execute('ALTER TABLE u_events ALTER occurred TYPE VARCHAR')
        fake.con.execute('UPDATE u_events SET occurred = ?', [label])
        for _ in range(2):
            fake.requests.clear()
            answer = Planner(v, warehouses, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=checked.plan))
            assert answer['kind'] == 'result', answer
            assert answer['result']['rows'][0]['dimensions'][0]['value'] == '2025-04-01'
            assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()


def test_recipe_alias_includes_the_business_field_when_it_is_not_status():
    recipe = {'table': 'policy', 'column': 'previous_status', 'op': '=', 'value': 'active'}
    assert _qualified_aliases([recipe], 'Written premium') == ['previous status active-policy Written premium']
