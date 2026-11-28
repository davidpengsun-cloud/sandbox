"""Governed data reads preserve declared joins and cannot reuse a changed policy proof."""
from dataclasses import asdict, replace
from types import SimpleNamespace

import duckdb
import pytest
from sqlglot import exp, parse_one

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import Allowlist, check
from kge.ports import AUTHORED, QueryRefused
from kge.semantics.query import Join, Rule
from kge.services.data.engine import governance
from tests.plan_fixtures import semantics


WINDOW = {'date': 'u.events.occurred', 'effective': 'u.members.effective', 'expiry': 'u.members.expiry'}
KEY = 'e.entity_id = m.entity_id AND e.source = m.source'
BETWEEN = 'e.occurred BETWEEN m.effective AND m.expiry'


def fixture(monkeypatch, kind='left', window=True, selector=False, release=False):
    sem = semantics(release=release, joined=True)
    sem.tables['u.members'].update(effective='DATE', expiry='DATE', current='BOOLEAN')
    sem.scd2['u.members'] = {'is_current': 'current', 'valid_from': 'effective', 'valid_to': 'expiry',
                            'valid_to_inclusive': True}
    original = sem.declared['member_join']
    join = Join(original.id, original.kind, original.condition, None, None, original.source, original.target, False,
                {'type': kind, 'cardinality': 'one-to-one', **({'validity_window': WINDOW} if window else {})})
    selectors = ()
    if selector:
        sem.tables['u.blocklist'] = {'entity_id': 'INTEGER'}
        selectors = (Rule('blocked', '', 'events.entity_id = blocklist.entity_id', 'u.blocklist', 'exclude'),)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(join,), filters=(), selectors=selectors)
    sem.declared = {join.id: join}
    if release:
        sem.declared['release_join'] = Join('release_join', 'calendar_rollup',
                                           'last_day(events.occurred) = calendar_rows.period', None, None,
                                           'u.events', 'u.calendar_rows', True, {'cardinality': 'one-to-one'})
    sem.types = {table + '.' + column: dtype for table, columns in sem.tables.items() for column, dtype in columns.items()}
    schema = {table.split('.')[1]: set(columns) for table, columns in sem.tables.items()}
    allow = Allowlist('u', set(schema), {table: columns - {'private'} for table, columns in schema.items()},
                      schema=schema, restricted={('events', 'private')}, windows={('members', 'effective', 'expiry')})
    allow = sem.declared_allowlist('u', allow)
    engine = SimpleNamespace(semantics=sem, g=SimpleNamespace(profile=object(), release=lambda: object()))
    monkeypatch.setattr(governance, 'landed_tables', lambda profile, release: set(sem.tables))
    query = (f'SELECT m.grouping, SUM(e.value) AS total FROM events e {kind.upper()} JOIN members m '
             f'ON {KEY}' + (f' AND {BETWEEN}' if window else '') + ' GROUP BY m.grouping ORDER BY m.grouping')
    return engine, allow, query


def database(blocked=False):
    con = duckdb.connect(':memory:')
    con.execute('CREATE TABLE events(occurred DATE, value DECIMAL(18,2), category VARCHAR, '
                'entity_id INTEGER, source VARCHAR, private VARCHAR)')
    con.execute("INSERT INTO events VALUES (DATE '2024-06-30', 10, 'A', 1, 'x', NULL), "
                "(DATE '2024-06-30', 20, 'B', 2, 'x', NULL)")
    con.execute('CREATE TABLE members(entity_id INTEGER, source VARCHAR, grouping VARCHAR, '
                'effective DATE, expiry DATE, current BOOLEAN)')
    con.execute("INSERT INTO members VALUES (1, 'x', 'historical', DATE '2024-01-01', DATE '2024-12-31', FALSE), "
                "(1, 'x', 'current', DATE '2025-01-01', DATE '2025-12-31', TRUE), "
                "(3, 'x', 'right only', DATE '2024-01-01', DATE '2025-12-31', TRUE)")
    if blocked:
        con.execute('CREATE TABLE blocklist(entity_id INTEGER)')
        con.execute('INSERT INTO blocklist VALUES (4)')
        con.execute("INSERT INTO events VALUES (DATE '2024-06-30', 100, 'C', 4, 'x', NULL)")
    return con


def governed(engine, allow, query):
    assert check(query, allow, inner_only=True) == []
    sql, policy = governance.constrain(engine, 'u', query, allow, AUTHORED, set())
    assert check(sql, policy, inner_only=True) == []
    return sql, policy


@pytest.mark.parametrize('kind,expected', [
    ('inner', {'historical': 10}),
    ('left', {'historical': 10, None: 20}),
    ('right', {'historical': 10, 'current': None, 'right only': None}),
    ('full', {'historical': 10, 'current': None, 'right only': None, None: 20}),
])
def test_governed_declared_type_and_window_keep_historical_hand_computed_figures(monkeypatch, kind, expected):
    engine, allow, original = fixture(monkeypatch, kind)
    sql, policy = governed(engine, allow, original)
    with database() as con:
        assert dict(con.execute(original).fetchall()) == expected
        assert dict(con.execute(sql).fetchall()) == expected
    assert '_governed_sql' not in asdict(policy)
    assert check(sql, replace(policy)) == []


def test_current_policy_is_preserved_when_no_declared_window_replaces_it(monkeypatch):
    engine, allow, original = fixture(monkeypatch, window=False)
    sql, policy = governed(engine, allow, original)
    with database() as con:
        assert dict(con.execute(sql).fetchall()) == {'current': 10, None: 20}
        assert dict(con.execute(original).fetchall()) == {'historical': 10, 'current': 10, None: 20}
    tree = parse_one(sql, read=AUTHORED)
    body = next(cte.this for cte in tree.find_all(exp.CTE) if 'members' in {t.name for t in cte.find_all(exp.Table)})
    body.set('where', None)
    assert check(tree.sql(AUTHORED), policy)


def test_policy_wrappers_project_only_the_named_physical_columns(monkeypatch):
    engine, allow, original = fixture(monkeypatch)
    sql, _ = governed(engine, allow, original)
    tree = parse_one(sql, read=AUTHORED)
    bodies = {cte.this.args['from_'].this.name: cte.this for cte in tree.find_all(exp.CTE)}
    assert {column.name for column in bodies['events'].selects} == {'entity_id', 'source', 'occurred', 'value'}
    assert {column.name for column in bodies['members'].selects} == {'entity_id', 'source', 'effective', 'expiry', 'grouping'}
    named = original.replace('SELECT m.grouping,', 'SELECT e.category, m.current, m.grouping,').replace(
        'GROUP BY m.grouping', 'GROUP BY e.category, m.current, m.grouping')
    named_sql, _ = governed(engine, allow, named)
    with database() as con:
        assert con.execute(sql).fetchall() == con.execute(original).fetchall()
        assert con.execute(named_sql).fetchall() == con.execute(named).fetchall()


def test_count_star_keeps_one_safe_projection_and_all_row_selectors(monkeypatch):
    engine, allow, _ = fixture(monkeypatch, selector=True)
    sql, _ = governed(engine, allow, 'SELECT COUNT(*) AS total FROM events e')
    tree = parse_one(sql, read=AUTHORED)
    assert len(tree.args['with_'].expressions[0].this.selects) == 1
    with database(blocked=True) as con:
        assert con.execute(sql).fetchone() == (2,)


def test_mixed_historical_and_current_roles_cannot_bypass_the_current_policy(monkeypatch):
    engine, allow, _ = fixture(monkeypatch)
    original = engine.semantics.declared['member_join']
    engine.semantics.declared['member_current'] = Join('member_current', original.kind, original.condition,
                                                      None, None, original.source, original.target, False,
                                                      {'type': 'left', 'cardinality': 'one-to-one'})
    allow = engine.semantics.declared_allowlist('u', allow)
    original = (f'SELECT SUM(e.value) AS total FROM events e LEFT JOIN members m ON {KEY} AND {BETWEEN} '
                'LEFT JOIN members c ON e.entity_id = c.entity_id AND e.source = c.source')
    reference = original + ' AND c.current'
    with database() as con:
        assert con.execute(reference).fetchone() == (30,)
        assert con.execute(original).fetchone() == (40,)
    assert check(original, allow, inner_only=True)
    with pytest.raises(ValueError, match='cannot be safely constrained'):
        governance.constrain(engine, 'u', original, allow, AUTHORED, set())


def test_separate_scopes_keep_windowed_and_current_roles_of_the_same_table(monkeypatch):
    engine, allow, _ = fixture(monkeypatch)
    original = engine.semantics.declared['member_join']
    engine.semantics.declared['member_current'] = Join('member_current', original.kind, original.condition,
                                                      None, None, original.source, original.target, False,
                                                      {'type': 'left', 'cardinality': 'one-to-one'})
    allow = engine.semantics.declared_allowlist('u', allow)
    dated = (f"SELECT 'dated' AS basis, SUM(e.value) AS total FROM events e LEFT JOIN members m "
             f'ON {KEY} AND {BETWEEN}')
    current = ("SELECT 'current' AS basis, SUM(e.value) AS total FROM events e LEFT JOIN members m "
               f'ON {KEY}')
    original = dated + ' UNION ALL ' + current
    reference = original + ' AND m.current'
    assert check(reference, allow, inner_only=True) == []
    sql, policy = governed(engine, allow, original)
    with database() as con:
        assert dict(con.execute(original).fetchall()) == {'dated': 30, 'current': 40}
        assert dict(con.execute(reference).fetchall()) == {'dated': 30, 'current': 30}
        assert dict(con.execute(sql).fetchall()) == {'dated': 30, 'current': 30}
    manager = runtime(engine)
    manager.runtimes['live'] = {'dialect': 'bigquery'}
    translated, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', policy)
    translated = manager._render_for_dialect(translated, 'bigquery')
    assert check(translated, physical, dialect='bigquery') == []


def test_a_shared_grouped_source_cannot_waive_current_policy_for_another_consumer(monkeypatch):
    engine, allow, _ = fixture(monkeypatch, 'inner')
    join = engine.semantics.declared['member_join']
    engine.semantics.declared['member_current'] = Join('member_current', join.kind, join.condition, None, None,
                                                      join.source, join.target, False,
                                                      {'type': 'inner', 'cardinality': 'one-to-one'})
    allow = engine.semantics.declared_allowlist('u', allow)
    original = ('WITH grouped AS (SELECT entity_id, source, effective, expiry, current, SUM(entity_id) AS copies '
                'FROM members GROUP BY entity_id, source, effective, expiry, current) '
                f"SELECT 'dated' AS basis, SUM(e.value) AS total FROM events e INNER JOIN grouped m ON {KEY} WHERE {BETWEEN} "
                f"UNION ALL SELECT 'current' AS basis, SUM(e.value) AS total FROM events e INNER JOIN grouped m ON {KEY}")
    assert check(original, allow, inner_only=True) == []
    with database() as con:
        assert dict(con.execute(original).fetchall()) == {'dated': 10, 'current': 20}
        assert dict(con.execute(original + ' WHERE m.current').fetchall()) == {'dated': 10, 'current': 10}
    with pytest.raises(ValueError, match='cannot be safely constrained'):
        governance.constrain(engine, 'u', original, allow, AUTHORED, set())


def mutations(sql):
    tree = parse_one(sql, read=AUTHORED)
    join = tree.args['joins'][0]
    changed = tree.copy()
    changed.args['joins'][0].set('side', 'LEFT' if join.side == 'RIGHT' else 'RIGHT')
    changed.args['joins'][0].set('kind', None)
    yield changed.sql(AUTHORED)
    changed = tree.copy()
    changed.args['joins'][0].set('on', exp.true())
    yield changed.sql(AUTHORED)
    changed = tree.copy()
    on = changed.args['joins'][0].args['on']
    changed.args['joins'][0].set('on', on.transform(lambda node: exp.true() if isinstance(node, exp.Between) else node))
    yield changed.sql(AUTHORED)
    changed = tree.copy()
    next(changed.args['joins'][0].find_all(exp.Between)).set('high', exp.column('effective', table='m'))
    yield changed.sql(AUTHORED)
    changed = tree.copy()
    next(c for c in changed.args['joins'][0].find_all(exp.Column) if c.name == 'entity_id').set('this', exp.to_identifier('value'))
    yield changed.sql(AUTHORED)
    changed = tree.copy()
    target = join.this.alias_or_name
    subquery = parse_one('SELECT entity_id, source, grouping, effective, expiry, current FROM members', read=AUTHORED).subquery(target)
    changed.args['joins'][0].set('this', subquery)
    yield changed.sql(AUTHORED)


def test_governed_proof_refuses_each_join_type_condition_and_window_mutation(monkeypatch):
    engine, allow, original = fixture(monkeypatch)
    sql, policy = governed(engine, allow, original)
    altered = list(mutations(sql))
    assert len(altered) == 6 and all(changed != sql for changed in altered)
    for changed in altered:
        assert check(changed, policy), changed


def test_raw_mutations_cannot_gain_a_governed_proof(monkeypatch):
    engine, allow, original = fixture(monkeypatch)
    for changed in mutations(original):
        assert check(changed, allow, inner_only=True), changed
        with pytest.raises(ValueError, match='cannot be safely constrained'):
            governance.constrain(engine, 'u', changed, allow, AUTHORED, set())


def test_selector_remains_inside_the_physical_wrapper_and_cannot_be_dropped(monkeypatch):
    engine, allow, original = fixture(monkeypatch, selector=True)
    sql, policy = governed(engine, allow, original)
    with database(blocked=True) as con:
        assert dict(con.execute(sql).fetchall()) == {'historical': 10, None: 20}
        assert dict(con.execute(original).fetchall()) == {'historical': 10, None: 120}
    tree = parse_one(sql, read=AUTHORED)
    body = next(cte.this for cte in tree.find_all(exp.CTE) if list(cte.find_all(exp.Exists)))
    body.set('where', None)
    assert check(tree.sql(AUTHORED), policy)
    tree = parse_one(sql, read=AUTHORED)
    exists = next(tree.find_all(exp.Exists))
    exists.this.set('where', exp.Where(this=exp.false()))
    assert check(tree.sql(AUTHORED), policy)


def test_release_policy_remains_inside_the_wrapper_and_cannot_be_dropped(monkeypatch):
    engine, allow, _ = fixture(monkeypatch, release=True)
    original = 'SELECT SUM(e.value) AS total FROM events e'
    sql, policy = governed(engine, allow, original)
    with database() as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
        con.execute("INSERT INTO calendar_rows VALUES (DATE '2024-06-30', DATE '2024-07-01', DATE '2024-07-02', TRUE), "
                    "(DATE '2024-07-31', DATE '2024-08-01', DATE '2024-08-02', FALSE)")
        con.execute("INSERT INTO events VALUES (DATE '2024-07-31', 50, 'A', 1, 'x', NULL)")
        assert con.execute(original).fetchone() == (80,)
        assert con.execute(sql).fetchone() == (30,)
    tree = parse_one(sql, read=AUTHORED)
    body = next(cte.this for cte in tree.find_all(exp.CTE) if cte.this.args.get('joins'))
    body.set('where', None)
    assert check(tree.sql(AUTHORED), policy)
    tree = parse_one(sql, read=AUTHORED)
    body = next(cte.this for cte in tree.find_all(exp.CTE) if cte.this.args.get('joins'))
    body.set('joins', [])
    assert check(tree.sql(AUTHORED), policy)


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
def test_governance_preserves_preaggregated_grain_and_refuses_raw_fanout(monkeypatch, kind):
    engine, allow, _ = fixture(monkeypatch, kind)
    join = engine.semantics.declared['member_join']
    engine.semantics.declared[join.id] = Join(join.id, join.kind, join.condition, None, None,
                                            join.source, join.target, False,
                                            {**join.options, 'cardinality': 'one-to-many'})
    allow = engine.semantics.declared_allowlist('u', allow)
    tail = f'{kind.upper()} JOIN members m ON {KEY} AND {BETWEEN}'
    for expression in ('SUM(e.value)', 'COUNT(e.entity_id)'):
        raw = f'SELECT {expression} FROM events e {tail}'
        assert check(raw, allow, inner_only=True)
        with pytest.raises(ValueError, match='cannot be safely constrained'):
            governance.constrain(engine, 'u', raw, allow, AUTHORED, set())
    original = ('SELECT DISTINCT e.entity_id, e.source, e.occurred, e.total FROM '
                '(SELECT entity_id, source, occurred, SUM(value) AS total FROM events '
                f'GROUP BY entity_id, source, occurred) e {tail}')
    sql, _ = governed(engine, allow, original)
    with database() as con:
        con.execute("INSERT INTO members VALUES (1, 'x', 'second match', DATE '2024-01-01', DATE '2024-12-31', FALSE)")
        expected = [(1, 'x', 10)] if kind == 'inner' else [(1, 'x', 10), (2, 'x', 20)] if kind == 'left' else None
        figures = {(row[0], row[1], row[3]) for row in con.execute(sql).fetchall()}
        independent = {(row[0], row[1], row[3]) for row in con.execute(original).fetchall()}
        assert figures == independent
        if expected is not None:
            assert figures == set(expected)
        else:
            assert (1, 'x', 10) in figures and (None, None, None) in figures
            assert ((2, 'x', 20) in figures) == (kind == 'full')


def runtime(engine):
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    tables = {table.split('.')[1]: columns for table, columns in engine.semantics.tables.items()}
    manager.ref_table_units = dict.fromkeys(tables, 'u')
    manager.ref_columns = {(table, column): kind for table, columns in tables.items() for column, kind in columns.items()}
    manager.rt_tables = {('live', table): {'table': 'live_data.' + table.upper()} for table in tables}
    manager.rt_columns = {('live', table): {column: {'column': column.upper()} for column in columns}
                          for table, columns in tables.items()}
    return manager


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_runtime_maps_the_governed_proof_and_still_refuses_mutations(monkeypatch, kind, dialect):
    engine, allow, original = fixture(monkeypatch, kind)
    sql, policy = governed(engine, allow, original)
    manager = runtime(engine)
    manager.runtimes['live'] = {'dialect': dialect}
    translated, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', policy)
    translated = manager._render_for_dialect(translated, dialect)
    assert check(translated, physical, dialect=dialect) == []
    changed = parse_one(translated, read=dialect)
    changed.args['joins'][0].set('on', exp.true())
    assert check(changed.sql(dialect), physical, dialect=dialect)
    for altered in mutations(sql):
        if altered == sql:
            continue
        with pytest.raises(QueryRefused, match='failed its gate'):
            manager._rename_sql_for_runtime(altered, 'u', 'live', policy)
