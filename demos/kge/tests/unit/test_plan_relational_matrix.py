import datetime as dt
from dataclasses import replace

import duckdb
import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.plan import Binding
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Context, Join, Rule
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, time_dimension, vocabulary
from tests.unit.test_plan_compiler import compiled, connection


def dimension(identifier, column, path=()):
    return {'kind': 'column', 'id': identifier, 'bindings': [{'context': name('ctx'), 'column': name(column), 'path': [name(j) for j in path], 'level': None}]}


@pytest.mark.parametrize('other_first,other_last,expected', [
    ('2025-02', '2025-04', [2, 3, 4]), ('2025-04', '2025-06', [4, 5, 6]),
    ('2024-05', '2024-07', [105, 106, 107]),
])
def test_explicit_comparison_rekeys_each_source_once(other_first, other_last, expected):
    plan = model(time=monthly_time('2025-05', '2025-07'), dimensions=[time_dimension()],
                 comparisons=[{'kind': 'explicit_period', 'id': 'c', 'inputs': ['m'], 'other': monthly_time(other_first, other_last), 'evidence': []}])
    text = f'Metric by month 2025-05 through 2025-07 compare {other_first} through {other_last}'
    c = compiled(plan, text, {'by': 't', 'month': 't', '2025-05': 'time', '2025-07': 'time',
                              'through': ['time', 'c'], 'compare': 'c', other_first: 'c', other_last: 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(y, m, 10), m + (100 if y == 2024 else 0), 'A', 1, 'x') for y in (2024, 2025) for m in range(1, 9)]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [(r[0], r[1], r[3]) for r in actual] == [(dt.date(2025, m, 1), m, v) for m, v in zip((5, 6, 7), expected)]


def test_explicit_comparison_with_different_period_counts_abstains_before_compiling():
    v = vocabulary()
    p = model(time=monthly_time(), dimensions=[time_dimension()], comparisons=[{'kind': 'explicit_period', 'id': 'c', 'inputs': ['m'], 'other': monthly_time('2024-04', '2024-05'), 'evidence': []}])
    text = 'Metric by month 2025-04 through 2025-06 compare 2024-04 through 2024-05'
    p = grounded(p, text, v, {'by': 't', 'month': 't', '2025-04': 'time', '2025-06': 'time', 'through': ['time', 'c'], 'compare': 'c', '2024-04': 'c', '2024-05': 'c'})
    assert 'one-to-one' in str(check_plan(p, v, text))


@pytest.mark.parametrize('grain,expected', [('month', [10, 20, 30, 40, 50, 60]), ('calendar_quarter', [30, 50, 60]), ('year', [60]), (None, [60])])
def test_snapshot_rollups_select_last_observation_in_each_output_period(grain, expected):
    v = vocabulary()
    v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=False)})
    dims = [dict(time_dimension(), grain=grain)] if grain else []
    word = {'calendar_quarter': 'quarter'}.get(grain, grain)
    text = 'Metric' + (f' by {word}' if grain else '')
    c = compiled(model(dimensions=dims), text, {'by': 't', word: 't'} if grain else None, v)
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(2025, month, 10), value, 'A', 1, 'x') for month, value in [(4, 10), (5, 20), (6, 30), (7, 40), (8, 50), (12, 60)]]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [r[1 if grain else 0] for r in actual] == expected
    assert check(c.answer.sql.replace('"observed_at" = "latest_at"', 'TRUE'), c.answer.allow)


def membership_vocabulary(certified=True, no_join=()):
    sem = semantics()
    sem.tables.update({'v.people': {'id': 'INTEGER', 'score': 'INTEGER'}, 'u.links': {'near_id': 'INTEGER', 'source': 'VARCHAR', 'far_id': 'INTEGER', 'certified': 'BOOLEAN'},
                       'v.exclusions': {'id': 'INTEGER'}})
    relation = Join('rel', 'cross_reference', 'u.events.entity_id = u.links.near_id AND u.events.source = u.links.source AND u.links.far_id = v.people.id AND u.links.certified = TRUE', None, 'u.links', 'u.events', 'v.people', False)
    original = sem.contexts['ctx']
    sem.contexts['ctx'] = replace_context(original, joins=(relation,))
    sem.contexts['far'] = Context('far', 'People context', '', 'Score', 'v.score', 'sum(people.score)', 'sum', 'day', 'v.people', ('v.people.score',),
                                  (Rule('eligible', '', 'people.score >= 0', 'v.people'),),
                                  (Rule('excluded', '', 'people.id = exclusions.id', 'v.exclusions', 'exclude'),), ())
    sem.entities['person'] = {'label': 'People', 'table': 'v.people', 'key': ['id']}
    sem.declared['rel'] = relation
    sem.certified = [{'between': ('u', 'v'), 'declared_join': 'rel'}] if certified else []
    sem.types = {t + '.' + c: dtype for t, cols in sem.tables.items() for c, dtype in cols.items()}
    return build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'), ['u', 'v'], ['u', 'v'],
                            [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('v.score', 'USD', 'entities', True)], no_join=no_join)


def replace_context(c, **updates):
    data = {k: getattr(c, k) for k in ('id', 'label', 'description', 'term', 'measure', 'expression', 'aggregation', 'grain', 'fact', 'values', 'filters', 'selectors', 'joins')}
    return Context(**(data | updates))


def membership_plan(v):
    predicate = {'kind': 'membership', 'id': 'f', 'entity': name('person'), 'relation': name('rel'), 'context': name('far'),
                 'filters': [{'kind': 'compare', 'id': 'n', 'left': {'kind': 'column', 'name': name('v.people.score'), 'path': []}, 'op': 'gt', 'value': {'kind': 'integer', 'value': 5}, 'evidence': []}], 'evidence': []}
    text = 'Metric membership People above 5'
    p = grounded(model(filters=[predicate]), text, v, {'membership': 'f', 'People': 'f', 'above': 'n', '5': 'n'})
    return p, text


def test_membership_uses_certified_exists_without_fact_fanout_and_keeps_far_policy():
    v = membership_vocabulary()
    p, text = membership_plan(v)
    checked = check_plan(p, v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked)
    assert check(c.answer.sql, c.answer.allow) == []
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE SCHEMA u; CREATE SCHEMA v')
        con.execute('CREATE TABLE u.events(occurred DATE, value INT, entity_id INT, source VARCHAR, category VARCHAR, private VARCHAR)')
        con.execute("INSERT INTO u.events VALUES ('2025-06-01', 10, 1, 'x', 'A', NULL), ('2025-06-01', 20, 2, 'x', 'B', NULL), ('2025-06-01', 30, 3, 'x', 'C', NULL)")
        con.execute('CREATE TABLE v.people(id INT, score INT); INSERT INTO v.people VALUES (11, 10), (12, 15), (13, 10)')
        con.execute('CREATE TABLE u.links(near_id INT, source VARCHAR, far_id INT, certified BOOL)')
        con.execute("INSERT INTO u.links VALUES (1, 'x', 11, TRUE), (1, 'x', 12, TRUE), (2, 'x', 13, TRUE), (3, 'x', 11, FALSE)")
        con.execute('CREATE TABLE v.exclusions(id INT); INSERT INTO v.exclusions VALUES (13)')
        assert con.execute(c.answer.sql).fetchall()[0][0] == 10
    assert check(c.answer.sql.replace('links.certified = TRUE', 'TRUE'), c.answer.allow)
    assert check(c.answer.sql.replace('member_0.score >= 0', 'TRUE'), c.answer.allow)


@pytest.mark.parametrize('certified,no_join', [(False, ()), (True, [('u', 'v')])])
def test_membership_cannot_bypass_certification_or_no_join(certified, no_join):
    v = membership_vocabulary(certified, no_join)
    p, text = membership_plan(v)
    decision = check_plan(p, v, text)
    assert not isinstance(decision, CheckedPlan)
    assert decision.kind == ('refuse' if no_join else 'abstain')


@pytest.mark.parametrize('comparison', [False, True])
def test_mixed_dating_oracle_fact_at_release_dimension_at_month_end_and_office_current(comparison):
    sem = semantics(release=True)
    policy = {'valid_from': 'since', 'valid_to': 'until', 'is_current': 'active', 'valid_to_inclusive': False}
    sem.tables['u.events'].update({'since': 'DATE', 'until': 'DATE', 'active': 'BOOLEAN', 'office_id': 'INTEGER'})
    sem.tables['u.members'] = {'entity_id': 'INTEGER', 'grouping': 'VARCHAR', 'since': 'DATE', 'until': 'DATE', 'active': 'BOOLEAN'}
    sem.tables['u.offices'] = {'office_id': 'INTEGER', 'label': 'VARCHAR', 'since': 'DATE', 'until': 'DATE', 'active': 'BOOLEAN'}
    ctx = sem.contexts['ctx']
    joins = (*ctx.joins,
             Join('member', 'key', 'events.entity_id = members.entity_id AND members.since <= last_day(events.occurred) AND (members.until IS NULL OR members.until > last_day(events.occurred))', None, None, 'u.events', 'u.members', True),
             Join('office', 'key', 'events.office_id = offices.office_id AND offices.active', None, None, 'u.events', 'u.offices', True))
    sem.contexts['ctx'] = replace_context(ctx, joins=joins)
    sem.scd2 = {t: policy for t in ('u.events', 'u.members', 'u.offices')}
    sem.declared = {j.id: j for j in joins}
    v = vocabulary(sem)
    measure = model().measures[0].model_dump(mode='json') | {'mode': 'as_reported'}
    p = model(measures=[measure], time=monthly_time('2025-06', '2025-06'),
              dimensions=[dimension('d', 'u.members.grouping', ['member']), dimension('o', 'u.offices.label', ['office'])])
    if comparison:
        p = model(measures=[measure], time=monthly_time('2025-06', '2025-06'), dimensions=p.dimensions,
                  comparisons=[{'kind': 'time_mode', 'id': 'c', 'inputs': ['m'], 'base': 'as_reported', 'other': 'current', 'evidence': []}])
    text = 'Metric as reported by grouping and label 2025-06 through 2025-06' + (' vs current' if comparison else '')
    assignments = {'as reported': ['m/mode', 'c'] if comparison else 'm/mode', 'by': ['d', 'o'], 'grouping': 'd', 'label': 'o', '2025-06': 'time', 'through': 'time'}
    if comparison:
        assignments.update({'vs': 'c', 'current': 'c'})
    p = grounded(p, text, v, assignments)
    checked = check_plan(p, v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked, [('0', dt.date(2025, 6, 30), True, dt.date(2025, 6, 15))])
    assert check(c.answer.sql, c.answer.allow) == []
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE events(occurred DATE, value INT, entity_id INT, source VARCHAR, category VARCHAR, private VARCHAR, since DATE, until DATE, active BOOL, office_id INT)')
        con.execute("INSERT INTO events VALUES ('2025-06-10', 10, 1, 'x', 'A', NULL, '2025-06-01', '2025-06-20', FALSE, 1), ('2025-06-10', 100, 1, 'x', 'A', NULL, '2025-06-20', NULL, TRUE, 1)")
        con.execute("CREATE TABLE calendar_rows(period DATE, \"freeze\" DATE, release_date DATE, released BOOL); INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-15', '2025-06-15', TRUE)")
        con.execute("CREATE TABLE members(entity_id INT, grouping VARCHAR, since DATE, until DATE, active BOOL); INSERT INTO members VALUES (1, 'at freeze', '2025-01-01', '2025-06-20', FALSE), (1, 'at month end', '2025-06-20', NULL, TRUE)")
        con.execute("CREATE TABLE offices(office_id INT, label VARCHAR, since DATE, until DATE, active BOOL); INSERT INTO offices VALUES (1, 'at freeze', '2025-01-01', '2025-07-01', FALSE), (1, 'current', '2025-07-01', NULL, TRUE)")
        actual = con.execute(c.answer.sql).fetchall()[0]
        assert actual[:3] == ('at month end', 'current', 10)
        if comparison:
            assert actual[3:5] == (10, 100)


@pytest.mark.parametrize('kind', ['in', 'not', 'any', 'all', 'null'])
def test_row_predicate_variants_execute_with_declared_values(kind):
    from kge.semantics.planvocabulary import Entry
    operand = {'kind': 'column', 'name': name('u.events.category'), 'path': []}
    leaf = {'kind': 'in', 'id': 'a', 'left': operand, 'values': [{'kind': 'text', 'value': 'A'}], 'evidence': []}
    v = vocabulary(values=[Entry('member_a', 'value', 'A', 'u', column='u.events.category', value='A')])
    assignments = {'category': 'a', 'A': 'a', 'in': 'a'}
    text = 'Metric category in A'
    pred = leaf
    expected = 15
    if kind == 'not':
        pred = {'kind': 'not', 'id': 'f', 'child': leaf, 'evidence': []}
        text += ' not'
        assignments['not'] = 'f'
        expected = 7
    elif kind in ('all', 'any'):
        pred = {'kind': kind, 'id': 'f', 'children': [leaf], 'evidence': []}
        text += ' ' + kind
        assignments[kind] = 'f'
    elif kind == 'null':
        pred = {'kind': 'null', 'id': 'a', 'left': operand, 'is_null': True, 'evidence': []}
        text, assignments = 'Metric category is null', {'category': 'a', 'is null': 'a'}
        expected = 3
    p = model(filters=[pred])
    c = compiled(p, text, assignments, v)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([(dt.date(2025, 6, 1), value, category, 1, 'x') for value, category in [(15, 'A'), (7, 'B'), (3, None)]]) as con:
        assert con.execute(c.answer.sql).fetchall()[0][0] == expected


@pytest.mark.parametrize('fiscal', [False, True])
def test_previous_year_and_fiscal_predecessor_numerical_oracles(fiscal):
    sem = semantics()
    if fiscal:
        sem.d.CALENDARS['cal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    v = vocabulary(sem)
    p = model(time=monthly_time('2025-04', '2025-06'), dimensions=[dict(time_dimension(), grain='fiscal_quarter' if fiscal else 'month')],
              comparisons=[{'kind': 'previous_period' if fiscal else 'previous_year', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by month 2025-04 through 2025-06 previous year'
    if fiscal:
        p = model(time={'kind': 'single', 'period': {'kind': 'fiscal_quarter', 'value': 'FY26-Q1', 'calendar': name('cal')}}, dimensions=[dict(time_dimension(), grain='fiscal_quarter')],
                  comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
        text = 'Metric by fiscal quarter FY26-Q1 previous period'
        c = compiled(p, text, {'by': 't', 'fiscal quarter': 't', 'FY26-Q1': 'time', 'previous period': 'c'}, v)
    else:
        c = compiled(p, text, {'by': 't', 'month': 't', '2025-04': 'time', '2025-06': 'time', 'through': 'time', 'previous year': 'c'}, v)
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(y, m, 10), m, 'A', 1, 'x') for y in (2024, 2025) for m in range(1, 7)]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [(r[1], r[3]) for r in actual] == ([(15, 6)] if fiscal else [(4, 4), (5, 5), (6, 6)])


def test_day_window_with_release_driver_reads_fact_date():
    sem = semantics(release=True)
    v = vocabulary(sem)
    p = model(time={'kind': 'single', 'period': {'kind': 'day', 'value': '2025-06-10', 'calendar': name('cal')}})
    text = 'Metric 2025-06-10'
    checked = check_plan(grounded(p, text, v, {'2025-06-10': 'time'}), v, text)
    assert isinstance(checked, CheckedPlan)
    c = compile_plan(checked, [('0', dt.date(2025, 6, 30), True, dt.date(2025, 6, 30))])
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([(dt.date(2025, 6, day), day, 'A', 1, 'x') for day in (10, 11)]) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-30', '2025-06-30', TRUE)")
        assert con.execute(c.answer.sql).fetchall()[0][0] == 10


def test_previous_day_comparison_uses_days_and_preserves_source_period_lineage():
    p = model(time={'kind': 'single', 'period': {'kind': 'day', 'value': '2025-06-10', 'calendar': name('cal')}},
              dimensions=[dict(time_dimension(), grain='day')], comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by day 2025-06-10 previous period'
    c = compiled(p, text, {'by': 't', 'day': 't', '2025-06-10': 'time', 'previous period': 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([(dt.date(2025, 6, day), day, 'A', 1, 'x') for day in (9, 10)]) as con:
        assert con.execute(c.answer.sql).fetchall()[0][:4] == (dt.date(2025, 6, 10), 10, 10, 9)
    other = next(column for column in c.columns if column.id == 'c/m/other')
    assert '2025-06-09' in other.lineage


def test_derived_lineage_keeps_both_inputs_and_integer_dimension_type():
    p = model(dimensions=[dimension('d', 'u.events.entity_id')],
              derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'm', 'evidence': []}])
    c = compiled(p, 'ratio of Metric to Metric by entity_id', {'by': 'd', 'entity_id': 'd', 'ratio': 'r'})
    assert c.columns[0].type == 'integer'
    assert {'ctx', 'u.metric'} <= set(next(col for col in c.columns if col.id == 'r').lineage)
    assert next(col for col in c.columns if col.id == 'r').currency is None


def test_gate_rejects_bypassing_a_snapshot_selection_scope():
    import sqlglot
    from sqlglot import exp
    v = vocabulary()
    v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=False)})
    c = compiled(vocab=v)
    tree = sqlglot.parse_one(c.answer.sql, read='duckdb')
    tree.args['with_'].set('expressions', [cte for cte in tree.args['with_'].expressions if cte.alias != 'b0'])
    for table in tree.find_all(exp.Table):
        if table.name == 'b0':
            table.set('this', exp.to_identifier('snapshots0'))
            table.set('alias', exp.TableAlias(this=exp.to_identifier('b0')))
    assert 'missing checked selection scope' in check(tree.sql('duckdb'), c.answer.allow)


def test_multiple_time_grains_rekey_observations_before_grouping():
    p = model(time=monthly_time('2025-01', '2025-02'), dimensions=[dict(time_dimension(), id='y', grain='year'), time_dimension()],
              comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by year and month 2025-01 through 2025-02 previous period'
    c = compiled(p, text, {'by': ['y', 't'], 'year': 'y', 'month': 't', '2025-01': 'time', '2025-02': 'time', 'through': 'time', 'previous period': 'c'})
    with connection([(dt.date(2024, 12, 10), 12, 'A', 1, 'x'), (dt.date(2025, 1, 10), 1, 'A', 1, 'x'), (dt.date(2025, 2, 10), 2, 'A', 1, 'x')]) as con:
        rows = con.execute(c.answer.sql).fetchall()
    assert [(r[0], r[1], r[2], r[4]) for r in rows] == [(dt.date(2025, 1, 1), dt.date(2025, month, 1), value, prior) for month, value, prior in [(1, 1, None), (2, 2, 1)]]
    assert check(c.answer.sql, c.answer.allow) == []


def test_empty_bounded_time_spine_returns_missing_observations():
    p = model(time=monthly_time(), dimensions=[time_dimension()])
    c = compiled(p, 'Metric by month 2025-04 through 2025-06', {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time'})
    with connection([]) as con:
        rows = con.execute(c.answer.sql).fetchall()
    assert len(rows) == 3
    assert all(r[1:] == (None, 'missing_observation') for r in rows)
    assert check(c.answer.sql, c.answer.allow) == []


def test_required_membership_cannot_be_omitted_from_a_context():
    v = membership_vocabulary()
    ctx = v.contexts['ctx']
    v = replace(v, contexts={**v.contexts, 'ctx': replace_context(ctx, joins=tuple(replace(j, required=True) for j in ctx.joins))})
    p = grounded(model(), 'Metric', v)
    assert 'required membership' in str(check_plan(p, v, 'Metric'))


def test_declared_self_join_hierarchy_keeps_each_alias_role():
    sem = semantics()
    sem.tables['u.nodes'] = {'id': 'INTEGER', 'parent_id': 'INTEGER', 'label': 'VARCHAR'}
    joins = (Join('leaf', 'key', 'events.entity_id = nodes.id', None, None, 'u.events', 'u.nodes', False),
             Join('parent', 'hierarchy', 'nodes.parent_id = parent_role.id', 'parent_role', None, 'u.nodes', 'u.nodes', False),
             Join('ancestor', 'hierarchy', 'parent_role.parent_id = ancestor_role.id', 'ancestor_role', None, 'u.nodes', 'u.nodes', False))
    sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], joins=joins)
    v = vocabulary(sem)
    d = dimension('d', 'u.nodes.label', ['leaf', 'parent', 'ancestor'])
    d['kind'], d['bindings'][0]['level'] = 'hierarchy', 2
    c = compiled(model(dimensions=[d]), 'Metric by label', {'by': 'd', 'label': 'd'}, v)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([(dt.date(2025, 6, 1), 10, 'A', 1, 'x')]) as con:
        con.execute("CREATE TABLE nodes(id INT, parent_id INT, label VARCHAR); INSERT INTO nodes VALUES (1, 2, 'leaf'), (2, 3, 'parent'), (3, NULL, 'ancestor')")
        assert con.execute(c.answer.sql).fetchall()[0][:2] == ('ancestor', 10)


@pytest.mark.parametrize('additive,expected', [(True, 15), (False, 6)])
def test_fiscal_ytd_flow_and_snapshot_have_independent_numerical_oracles(additive, expected):
    sem = semantics()
    sem.d.CALENDARS['cal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    v = vocabulary(sem)
    v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=additive)})
    p = model(time={'kind': 'ytd', 'through': {'kind': 'fiscal_quarter', 'value': 'FY26-Q1', 'calendar': name('cal')}, 'calendar': name('cal')})
    text = 'Metric year to date FY26-Q1'
    c = compiled(p, text, {'year to date': 'time', 'FY26-Q1': 'time'}, v)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([(dt.date(2025, month, 10), month, 'A', 1, 'x') for month in range(1, 7)]) as con:
        assert con.execute(c.answer.sql).fetchall()[0][0] == expected
