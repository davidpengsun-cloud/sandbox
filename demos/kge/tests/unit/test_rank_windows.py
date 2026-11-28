"""Partitioned rankings and bounded time averages on hand-computed populations."""
import datetime as dt
from collections import Counter
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import exp

from kge.compiler import checks
from kge.compiler.vocabulary import entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import Order
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Context
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_rules_derived_shapes import ROWS, declared as base_declared, execute, read_checked


def declared(monkeypatch, sem=None, aliases=(), lengths=(2, 3)):
    eng, _ = base_declared(monkeypatch, sem, aliases)
    sem = eng.semantics
    sem.d.VOCABULARY += entries({}, [{'id': 'u.metric', 'moving_average_lengths': list(lengths)}]) if lengths else []
    return eng, vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items(), *aliases])


@pytest.mark.parametrize('cue', ['in each source', 'within each source', 'per source'])
@pytest.mark.parametrize('ranking', ['top 2', 'bottom 2', 'top 2 and bottom 2'])
def test_rank_limit_restarts_in_every_declared_dimension(monkeypatch, cue, ranking):
    eng, vocab = declared(monkeypatch)
    checked = read_checked(eng, vocab, f'{ranking} categories by Metric {cue} for Q2 2025')
    order, = checked.plan.order
    category, source = checked.plan.dimensions
    assert order.partition_by == (source.id,)
    rows = [*ROWS, (dt.date(2025, 4, 10), 60, 'D', 4, 'x'),
            (dt.date(2025, 4, 10), 5, 'E', 5, 'y'), (dt.date(2025, 4, 10), 1, 'F', 6, 'y')]
    expected = {'top': {'x': [('B', 150), ('A', 60)], 'y': [('C', 240), ('E', 5)]},
                'bottom': {'x': [('A', 60), ('D', 60)], 'y': [('F', 1), ('E', 5)]}}
    with connection(rows) as con:
        actual = execute(checked, con)
    ends = ['top', 'bottom'] if order.direction == 'both' else ['top' if order.direction == 'desc' else 'bottom']
    wanted = {(s, end, rank): item for end in ends for s, items in expected[end].items() for rank, item in enumerate(items, 1)}
    if order.direction == 'both':
        wanted = {key: item for key, item in wanted.items() if key not in {('x', 'bottom', 1), ('y', 'bottom', 2)}}
    assert Counter((r[source.id], r['/end']) for r in actual) == Counter((s, end) for s, end, _ in wanted)
    assert {(r[source.id], r['/end'], r['/rank']): (r[category.id], r[order.output]) for r in actual} == wanted


@pytest.mark.parametrize('partition', ['month', 'source and customer', 'source and month', 'month and source'])
def test_partition_lists_include_declared_columns_and_time_grains(monkeypatch, partition):
    eng, vocab = declared(monkeypatch)
    checked = read_checked(eng, vocab, f'top 2 categories by Metric in each {partition} for Q2 2025')
    order, = checked.plan.order
    assert len(order.partition_by) == (1 if partition == 'month' else 2)
    with connection(ROWS) as con:
        actual = execute(checked, con)
    category = checked.plan.dimensions[0]
    assert all(r['/rank'] <= 2 for r in actual)
    if partition == 'month':
        month = next(d for d in checked.plan.dimensions if d.kind == 'time')
        assert {(r[month.id].month, r[category.id]): r[order.output] for r in actual} == {
            (4, 'B'): 40, (4, 'C'): 70, (5, 'B'): 50, (5, 'C'): 80, (6, 'B'): 60, (6, 'C'): 90}
    elif partition == 'source and customer':
        assert {r[category.id]: r[order.output] for r in actual} == {'A': 60, 'B': 150, 'C': 240}
    else:
        assert len(actual) == 9


@pytest.mark.parametrize('partitioned', [False, True])
@pytest.mark.parametrize('length', [2, 3])
def test_moving_average_uses_the_current_and_preceding_periods(monkeypatch, length, partitioned):
    eng, vocab = declared(monkeypatch)
    text = f'{length}-month moving average of Metric by month' + (' and category' if partitioned else '') + ' for Q2 2025'
    checked = read_checked(eng, vocab, text)
    moving, = checked.plan.derived
    time, = (d for d in checked.plan.dimensions if d.kind == 'time')
    group = next((d for d in checked.plan.dimensions if d.kind != 'time'), None)
    assert moving.length == length and moving.order_by == time.id
    assert moving.partition_by == ((group.id,) if group else ())
    with connection(ROWS) as con:
        actual = execute(checked, con)
    expected = {'A': [10, 15, 20 if length == 3 else 25], 'B': [40, 45, 50 if length == 3 else 55],
                'C': [70, 75, 80 if length == 3 else 85]} if partitioned else {'all': [120, 135, 150 if length == 3 else 165]}
    assert {(r[group.id] if group else 'all', r[time.id].month): float(r[moving.id]) for r in actual} == {
        (key, month): value for key, values in expected.items() for month, value in zip((4, 5, 6), values)}


@pytest.mark.parametrize('period', ['', ' for all available', ' for April through July 2025'])
def test_a_missing_period_poisons_only_windows_that_include_it(monkeypatch, period):
    eng, vocab = declared(monkeypatch)
    checked = read_checked(eng, vocab, '2-month moving average of Metric by month' + period)
    moving, = checked.plan.derived
    time, = checked.plan.dimensions
    rows = [(dt.date(2025, m, 10), value, 'A', 1, 'x') for m, value in ((4, 10), (6, 30), (7, 50))]
    with connection(rows) as con:
        actual = execute(checked, con)
    assert {r[time.id].month: r[moving.id] for r in actual} == {4: 10, 5: None, 6: None, 7: 40}


@pytest.mark.parametrize('change', ['drop partition', 'reverse', 'length', 'cutoff', 'rank alias', 'average alias'])
def test_sql_gate_refuses_changed_windows_and_partition_limits(monkeypatch, change):
    eng, vocab = declared(monkeypatch)
    text = ('3-month moving average of Metric by month for Q2 2025' if change in ('length', 'average alias')
            else 'top 2 categories by Metric in each source for Q2 2025')
    compiled = compile_plan(read_checked(eng, vocab, text))
    tree = sqlglot.parse_one(compiled.answer.sql, read='duckdb')
    if change == 'length':
        for window in tree.find_all(exp.Window):
            if window.args.get('spec'):
                window.args['spec'].set('start', exp.Literal.number(1))
    elif change in ('drop partition', 'reverse'):
        window = next(w for w in tree.find_all(exp.Window) if isinstance(w.this, exp.RowNumber))
        if change == 'drop partition':
            window.set('partition_by', [])
        else:
            window.args['order'].expressions[0].set('desc', False)
    elif change == 'cutoff':
        cutoff = next(p for p in tree.find_all(exp.LTE) if getattr(p.this, 'name', '') == 'shape_rank')
        cutoff.set('expression', exp.Literal.number(1))
    else:
        identifier = 'shape_rank' if change == 'rank alias' else 'v0'
        projection = next(a for a in tree.find_all(exp.Alias) if a.alias == identifier)
        select = projection.parent
        projection.set('alias', exp.to_identifier('unused'))
        select.select(exp.alias_(exp.Literal.number(1), identifier, quoted=True), append=True, copy=False)
    assert check(tree.sql('duckdb'), compiled.answer.allow)


@pytest.mark.parametrize('change', ['series step', 'tick alias', 'bounds', 'observed keys', 'physical projection'])
def test_sql_gate_keeps_the_calendar_spine_and_its_bounds(monkeypatch, change):
    eng, vocab = declared(monkeypatch)
    compiled = compile_plan(read_checked(eng, vocab, '3-month moving average of Metric by month'))
    tree = sqlglot.parse_one(compiled.answer.sql, read='duckdb')
    if change == 'series step':
        series = next(tree.find_all(exp.GenerateSeries))
        series.args['step'].set('this', exp.Literal.string('2'))
    elif change == 'tick alias':
        spine = next(c.this for c in tree.find_all(exp.CTE) if c.alias == 'spine')
        spine.expressions[-1].set('this', exp.cast(exp.Literal.string('2025-04-01'), 'DATE'))
    elif change == 'bounds':
        bounds = next(c.this for c in tree.find_all(exp.CTE) if c.alias == 'partitions')
        bounds.expressions[-1].set('this', exp.cast(exp.Literal.string('9999-12-31'), 'DATE'))
    elif change == 'observed keys':
        observed = next(c for c in tree.find_all(exp.CTE) if c.alias == 'observed_keys')
        observed.set('this', sqlglot.parse_one("SELECT k0, DATE '1900-01-01' AS d0 FROM b0 UNION ALL SELECT k0, DATE '2100-01-01' AS d0 FROM b0", read='duckdb'))
    else:
        physical = next(c.this for c in tree.find_all(exp.CTE) if c.alias == 'b0')
        projection = next(a for a in physical.find_all(exp.Alias) if a.alias == 'd0')
        projection.set('this', exp.cast(exp.Literal.string('1900-01-01'), 'DATE'))
    assert check(tree.sql('duckdb'), compiled.answer.allow)


def test_nullable_time_order_stays_last_when_a_running_total_is_ranked(monkeypatch):
    from kge.adapters.warehouse.bigquery import rewrite_sql
    eng, vocab = declared(monkeypatch)
    checked = read_checked(eng, vocab, 'running total of Metric by month, top 2 by Metric in each category')
    compiled = compile_plan(checked)
    moving, = checked.plan.derived
    month = next(d for d in checked.plan.dimensions if d.kind == 'time')
    rows = [(None, 100, 'A', 1, 'x'), (dt.date(2025, 4, 10), 10, 'A', 1, 'x'), (dt.date(2025, 5, 10), 20, 'A', 1, 'x')]
    expected = {None: 130, dt.date(2025, 5, 1): 30}
    with connection(rows) as con:
        assert {r[month.id]: r[moving.id] for r in execute(checked, con)} == expected
        written = rewrite_sql(compiled.answer.sql, lambda _: '', unit='u')
        assert check(written, compiled.answer.allow, 'bigquery') == []
        translated = sqlglot.parse_one(written, read='bigquery').sql('duckdb')
        cursor = con.execute(translated)
        columns = [c[0].replace('__', '/') for c in cursor.description]
        read = [dict(zip(columns, row)) for row in cursor.fetchall()]
        assert {r[month.id]: r[moving.id] for r in read} == expected
    tree = sqlglot.parse_one(compiled.answer.sql, read='duckdb')
    for window in tree.find_all(exp.Window):
        if window.args.get('spec') and window.args.get('order'):
            window.args['order'].set('expressions', window.args['order'].expressions[1:])
    assert check(tree.sql('duckdb'), compiled.answer.allow)


@pytest.mark.parametrize('change', ['omit partition', 'wrong partition', 'wrong length', 'wrong order', 'unapproved length'])
def test_model_cannot_change_requested_windows(monkeypatch, change):
    eng, vocab = declared(monkeypatch)
    text = ('3-month moving average of Metric by month and category for Q2 2025' if 'partition' not in change
            else 'top 2 categories by Metric in each source for Q2 2025')
    plan = read_checked(eng, vocab, text).plan
    if change == 'omit partition':
        plan = plan.model_copy(update={'order': (plan.order[0].model_copy(update={'partition_by': ()}),)})
    elif change == 'wrong partition':
        plan = plan.model_copy(update={'order': (plan.order[0].model_copy(update={'partition_by': (plan.dimensions[0].id,)}),)})
    else:
        moving, = plan.derived
        updates = {'length': 2 if change == 'wrong length' else 4} if change != 'wrong order' else {'order_by': plan.dimensions[0].id}
        plan = plan.model_copy(update={'derived': (moving.model_copy(update=updates),)})
    assert not isinstance(check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text), CheckedPlan)


def test_measure_lengths_are_onboarded_without_changing_other_vocabulary_ids(monkeypatch):
    sem = semantics()
    eng, vocab = declared(monkeypatch, sem, lengths=())
    sem.d.VOCABULARY += entries({}, [{'id': 'u.metric', 'moving_average_lengths': [4]}])
    from tests.plan_fixtures import vocabulary
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    checked = read_checked(eng, vocab, '4-month moving average of Metric by month for Q2 2025')
    assert checked.plan.derived[0].length == 4
    with connection(ROWS) as con:
        assert [r[checked.plan.derived[0].id] for r in execute(checked, con)] == [120, 135, 150]
    text = '3-month moving average of Metric by month for Q2 2025'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose'
    assert not isinstance(check_plan(reply.plan, vocab, text), CheckedPlan)
    rows = entries({'predicates': [{'phrases': ['A category'], 'column': 'u.events.category', 'op': 'eq', 'value': 'A'}]})
    assert entries({'predicates': [r['params'] for r in rows]}, [{'id': 'u.metric', 'moving_average_lengths': [4]}])[:1] == rows


@pytest.mark.parametrize('lengths', [[], [0], [-1], ['3'], [3, 3], [True], [2.5]])
def test_pack_refuses_invalid_moving_average_lengths(lengths):
    measure = dict(id='u.metric', term='Metric', column='u.events.value', expression='sum(events.value)', aggregation='sum',
                   grain='day', currency='USD', calendar='cal', conversion='none', moving_average_lengths=lengths)
    pack = SimpleNamespace(data={'glossary': [{'id': 'Metric'}], 'measures': [measure]},
                           lock={'sources': {'u': {'tables': [{'name': 'events', 'columns': [{'name': 'value'}]}]}}}, at=lambda p: p)
    findings = checks.measures(pack)
    assert any('window lengths' in f.message for f in findings)


def test_existing_order_serialization_is_unchanged():
    data = dict(output='m', direction='desc', nulls='last', evidence=[])
    assert Order.model_validate(data).model_dump(mode='json') == data


def test_partitioned_ranking_composes_with_a_moving_average(monkeypatch):
    eng, vocab = declared(monkeypatch)
    text = '3-month moving average of Metric by month, top 2 by Metric in each category for Q2 2025'
    checked = read_checked(eng, vocab, text)
    moving, = checked.plan.derived
    category = next(d for d in checked.plan.dimensions if d.kind != 'time')
    month = next(d for d in checked.plan.dimensions if d.kind == 'time')
    with connection(ROWS) as con:
        actual = execute(checked, con)
    assert {(r[category.id], r[month.id].month): r[moving.id] for r in actual} == {
        ('A', 5): 15, ('A', 6): 20, ('B', 5): 45, ('B', 6): 50, ('C', 5): 75, ('C', 6): 80}


def test_window_words_do_not_consume_other_unknown_constraints(monkeypatch):
    _, vocab = declared(monkeypatch)
    assert any(p.kind == 'unknown' for p in ledger('3-month moving average of Metric by month unapproved', 'turn', vocab))


@pytest.mark.parametrize('alias', ['3-month moving average of Metric', 'moving average'])
def test_structural_words_inside_a_declared_name_remain_part_of_the_name(monkeypatch, alias):
    eng, vocab = declared(monkeypatch, aliases=[(alias, 'ctx')])
    checked = read_checked(eng, vocab, alias)
    assert not checked.plan.derived
    with connection(ROWS) as con:
        assert execute(checked, con)[0][checked.plan.measures[0].id] == 450


@pytest.mark.parametrize('prefix', ['per ', 'in each ', 'within each '])
@pytest.mark.parametrize('noun', ['source', 'month'])
def test_partition_words_inside_a_declared_measure_name_keep_its_meaning(monkeypatch, prefix, noun):
    alias = prefix + noun
    eng, vocab = declared(monkeypatch, aliases=[(alias, 'ctx')])
    checked = read_checked(eng, vocab, f'top 2 categories by {alias} for Q2 2025')
    assert len(checked.plan.dimensions) == 1 and not checked.plan.order[0].partition_by
    with connection(ROWS) as con:
        actual = execute(checked, con)
    assert {r[checked.plan.dimensions[0].id]: r[checked.plan.measures[0].id] for r in actual} == {'B': 150, 'C': 240}


def test_an_explicit_partition_phrase_owns_its_declared_column(monkeypatch):
    sem = semantics()
    sem.d.GROUP_BY = {'per source': 'u.events.entity_id'}
    eng, vocab = declared(monkeypatch, sem)
    checked = read_checked(eng, vocab, 'top 1 categories by Metric per source for Q2 2025')
    partition = next(d for d in checked.plan.dimensions if d.id in checked.plan.order[0].partition_by)
    assert partition.bindings[0].column.id == 'u.events.entity_id'
    category = next(d for d in checked.plan.dimensions if d.id not in checked.plan.order[0].partition_by)
    rows = [(dt.date(2025, 4, 10), value, key, entity, source) for value, key, entity, source in
            [(10, 'A', 1, 'x'), (20, 'B', 1, 'y'), (30, 'C', 2, 'x'), (40, 'D', 2, 'y')]]
    with connection(rows) as con:
        actual = execute(checked, con)
    assert {r[category.id]: r[checked.plan.measures[0].id] for r in actual} == {'B': 20, 'D': 40}


def test_a_declared_mode_wording_is_not_a_time_partition(monkeypatch):
    eng, _ = declared(monkeypatch)
    sem = eng.semantics
    sem.d.TIME_MODES['per month'] = 'current'
    from tests.plan_fixtures import vocabulary
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    checked = read_checked(eng, vocab, 'top 2 categories by Metric per month for Q2 2025')
    assert len(checked.plan.dimensions) == 1 and not checked.plan.order[0].partition_by


@pytest.mark.parametrize('shape', ['moving', 'ranking', 'unbounded moving'])
def test_new_windows_run_through_the_warehouse_port_in_a_second_dialect(monkeypatch, tmp_path, shape):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import setup
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    try:
        eng, vocab = declared(monkeypatch, semantics(release=True))
        text = ('3-month moving average of Metric by month' + (' for Q2 2025' if shape == 'moving' else '') if 'moving' in shape else
                'top 2 categories by Metric in each source for Q2 2025')
        checked = read_checked(eng, vocab, text)
        result = Planner(vocab, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=checked.plan))
        assert result['kind'] == 'result', result
        assert result['guard']['passed']
        if shape == 'ranking':
            rows = [r for r in result['result']['rows'] if not r.get('total')]
            assert rows and all(r['end'] == 'top' and r['rank'] >= 1 for r in rows)
        if shape == 'unbounded moving':
            from kge.adapters.warehouse.bigquery import rewrite_sql
            sql = compile_plan(checked).answer.sql
            written = rewrite_sql(sql, lambda _: '', unit='u')
            assert 'GENERATE_DATE_ARRAY(' in written and 'GENERATE_ARRAY(' not in written
            assert check(written, compile_plan(checked).answer.allow, 'bigquery') == []
    finally:
        fake.con.close()


def counted(monkeypatch, table):
    """Metric with a declared count of observations, an entity keyed on the fact itself or on a table joined to it."""
    sem = semantics(joined=True)
    sem.entities = {'observation': {'label': 'Observations', 'table': table, 'key': ['entity_id', 'source']}}
    alias = table.rsplit('.', 1)[1]
    sem.contexts['context:count:observation'] = Context('context:count:observation', 'Observation count', '', 'Observation count',
        'count:observation', f'count(DISTINCT ({alias}.entity_id, {alias}.source))', 'count', 'day', table,
        (table + '.entity_id', table + '.source'), (), (), ())
    sem.d.ENTITY_WORDS = {'observations': 'observation'}
    eng, base = base_declared(monkeypatch, sem)
    return eng, build_vocabulary(sem, base.binding, ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True),
        MeasureSpec('count:observation', None, 'observation', False)], aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])


@pytest.mark.parametrize('table', ['u.events', 'u.members'])
@pytest.mark.parametrize('text', [
    'top 2 categories by Metric for April through June 2025 with observation counts',
    'top 2 categories by Metric for April through June 2025, with the number of observations',
    'for April through June 2025, top 2 categories by Metric with observation counts',
    'Which 2 categories had the most Metric from April to June 2025, and how many observations did each have?',
    'Rank categories by Metric for April through June 2025 and show the top 2 with their observation counts',
])
def test_a_ranked_amount_keeps_its_accompanying_count_and_ranks_on_the_amount(monkeypatch, table, text):
    """A has the most observations and the least Metric, so a ranking on the count would put it first."""
    eng, vocab = counted(monkeypatch, table)
    checked = read_checked(eng, vocab, text)
    amount, count = sorted(checked.plan.measures, key=lambda m: m.kind != 'measure')
    assert (amount.kind, count.kind, count.entity.id) == ('measure', 'distinct_count', 'observation')
    order, = checked.plan.order
    assert (order.output, order.direction, checked.plan.limit) == (amount.id, 'desc', 2)
    category, = checked.plan.dimensions
    rows = [*ROWS, (dt.date(2025, 5, 11), 5, 'A', 4, 'x'), (dt.date(2025, 5, 12), 1, 'A', 5, 'y'), (dt.date(2025, 7, 1), 900, 'A', 6, 'y')]
    with connection(rows) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'g'), (2, 'x', 'g'), (3, 'y', 'g'), (4, 'x', 'g'), (5, 'y', 'g'), (6, 'y', 'g')")
        actual = execute(checked, con)
    assert [(r[category.id], r[amount.id], r[count.id]) for r in actual] == [('C', 240, 1), ('B', 150, 1)]
    by_count = checked.plan.model_copy(update={'order': (order.model_copy(update={'output': count.id}),)})
    refused = check_plan(ground_plan(by_count, text, 'turn', vocab), vocab, text)
    assert not isinstance(refused, CheckedPlan)
    assert any('ordering must use the requested measure' in m for m in refused.missing), refused


@pytest.mark.parametrize('text', [
    'how many observations of 100 or more by category for April through June 2025, top 2 by Metric',
    'Number of observations by category for April through June 2025 and the top 2 categories by Metric',
    'How many observations of 100 or more did each category have from April to June 2025, '
    'and which 2 categories had the most Metric?',
])
def test_a_count_asked_before_the_ranking_leaves_the_ranking_on_the_amount(monkeypatch, text):
    eng, vocab = counted(monkeypatch, 'u.events')
    checked = read_checked(eng, vocab, text)
    amount, count = sorted(checked.plan.measures, key=lambda m: m.kind != 'measure')
    order, = checked.plan.order
    assert (order.output, order.direction, checked.plan.limit) == (amount.id, 'desc', 2)
    by_count = checked.plan.model_copy(update={'order': (order.model_copy(update={'output': count.id}),)})
    assert not isinstance(check_plan(ground_plan(by_count, text, 'turn', vocab), vocab, text), CheckedPlan)


@pytest.mark.parametrize('table', ['u.events', 'u.members'])
@pytest.mark.parametrize('text', [
    'top 1 categories by number of observations for April through June 2025, with Metric',
    'rank categories by number of observations for April through June 2025 and show the top 1 with Metric',
])
def test_a_count_named_before_the_amount_is_the_ranked_output(monkeypatch, table, text):
    eng, vocab = counted(monkeypatch, table)
    checked = read_checked(eng, vocab, text)
    amount, count = sorted(checked.plan.measures, key=lambda m: m.kind != 'measure')
    order, = checked.plan.order
    assert (order.output, order.direction, checked.plan.limit) == (count.id, 'desc', 1)
    category, = checked.plan.dimensions
    rows = [*ROWS, (dt.date(2025, 5, 11), 5, 'A', 4, 'x'), (dt.date(2025, 5, 12), 1, 'A', 5, 'y')]
    with connection(rows) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'g'), (2, 'x', 'g'), (3, 'y', 'g'), (4, 'x', 'g'), (5, 'y', 'g')")
        actual = execute(checked, con)
    assert [(r[category.id], r[amount.id], r[count.id]) for r in actual] == [('A', 66, 3)]
    by_amount = checked.plan.model_copy(update={'order': (order.model_copy(update={'output': amount.id}),)})
    refused = check_plan(ground_plan(by_amount, text, 'turn', vocab), vocab, text)
    assert not isinstance(refused, CheckedPlan)
    assert any('ordering must use the count the ranking names, not the amount' in m for m in refused.missing), refused


def test_which_after_the_ranking_is_not_a_count_that_takes_the_ranking(monkeypatch):
    """'which' reads as a count operator; after the ranking word, before the amount, it still names no count."""
    from kge.semantics.coverage import Piece, ranked_outputs
    from kge.semantics.plan import Span
    eng, vocab = counted(monkeypatch, 'u.events')
    text = 'top 2 categories by Metric for April through June 2025 with observation counts'
    checked = read_checked(eng, vocab, text)
    amount, count = sorted(checked.plan.measures, key=lambda m: m.kind != 'measure')
    outputs = [(m.id, m.context.id, m.kind) for m in checked.plan.measures]
    pieces = ledger(text, 'turn', vocab)
    at = text.index(' by ')
    which = Piece(Span(turn='turn', start=at + 1, end=at + 3), 'which', 'operator', operation='count')
    assert ranked_outputs({'desc'}, [*pieces, which], text, vocab, outputs) == {amount.id}
    counted_instead = Piece(Span(turn='turn', start=at + 1, end=at + 3), 'how many', 'operator', operation='count')
    assert ranked_outputs({'desc'}, [*pieces, counted_instead], text, vocab, outputs) == {count.id}
