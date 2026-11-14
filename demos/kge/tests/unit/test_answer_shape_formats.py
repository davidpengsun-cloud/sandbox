from decimal import Decimal
from dataclasses import replace

import pytest

from kge.semantics.plan import Binding, Result
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine.planner import display_value, labelled_result, result_of
from kge.services.ask.engine.planpublic import render
from kge.services.ask.engine.present import result_table
from tests.plan_fixtures import model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import compiled, connection
from tests.unit.test_plan_relational_matrix import dimension


def arithmetic(kind='ratio', denominator=-10):
    import datetime as dt
    sem = semantics()
    object.__setattr__(sem.contexts['ctx'], 'filters', ())
    if kind == 'ratio':
        base = sem.contexts['ctx']
        sem.contexts = {identifier: replace(base, id=identifier, label=label, term=label, measure='u.' + identifier, grain=base.grain,
            expression=f'sum(CASE WHEN EXTRACT(YEAR FROM events.occurred) = {year} THEN events.value ELSE 0 END)')
            for identifier, label, year in [('first', 'First', 2025), ('second', 'Second', 2024)]}
        v = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                             ['u'], ['u'], [MeasureSpec('u.' + identifier, 'USD', 'entities', True) for identifier in sem.contexts])
        grouping = dimension('d', 'u.events.category')
        grouping['bindings'] = [dict(grouping['bindings'][0], context=name(identifier)) for identifier in sem.contexts]
        p = model(measures=[dict(kind='measure', id=identifier, measure=name('u.' + identifier), context=name(identifier),
                                 mode='current', filters=[]) for identifier in sem.contexts],
                  dimensions=[grouping], derived=[dict(kind='ratio', id='r', numerator='first', denominator='second', evidence=[])])
        text = 'ratio of First to Second by category'
        assignments = {'ratio': 'r', 'First': 'first', 'Second': 'second', 'by': 'd', 'category': 'd'}
    else:
        v = vocabulary(sem)
        p = model(time=dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('cal'))),
                  dimensions=[dimension('d', 'u.events.category')],
                  comparisons=[dict(kind='previous_year', id='c', inputs=['m'], evidence=[])],
                  derived=[dict(kind=kind, id='r', numerator='c/m/base', denominator='c/m/other', evidence=[])])
        text = 'Metric by category 2025-04 previous year ' + kind.replace('_', ' ')
        assignments = {'by': 'd', 'category': 'd', '2025-04': 'time', 'previous year': 'c', kind.replace('_', ' '): 'r'}
    c = compiled(p, text, assignments, v)
    rows = [(dt.date(year, 4, 10), value, group, index, 'x') for index, (year, group, value) in enumerate([
        (2025, 'A', 30), (2024, 'A', denominator), (2025, 'B', 90), (2024, 'B', -20)])]
    with connection(rows) as con:
        data = con.execute(c.answer.sql).fetchall()
    return c, result_of(c, data, {}), text


def test_ratio_uses_absolute_denominator_and_recomputes_total_from_inputs():
    c, result, _ = arithmetic()
    index = next(i for i, col in enumerate(result.columns[1:]) if col.id == 'r')
    assert [Decimal(r.cells[index].value.value) for r in result.rows] == [Decimal(3), Decimal('4.5'), Decimal(4)]
    assert result.rows[-1].total
    assert 'ABS(' in c.answer.sql


@pytest.mark.parametrize('kind,expected', [('difference', Decimal(150)), ('percent_change', Decimal(5))])
def test_comparison_totals_use_aggregate_inputs(kind, expected):
    _, result, _ = arithmetic(kind)
    assert Decimal(result.rows[-1].cells[-1].value.value) == expected


def test_zero_denominator_keeps_a_null_reason():
    _, result, _ = arithmetic(denominator=0)
    assert result.rows[0].cells[-1].value.kind == 'null'
    assert result.rows[0].cells[-1].null_reasons == ('zero_denominator',)


def test_labels_and_formats_are_shared_by_text_and_rich_tables():
    c, result, text = arithmetic('percent_change')
    decorated = labelled_result(result, c.checked.plan, c.checked.vocabulary, text, 'turn')
    public = decorated.model_dump(mode='json', exclude_unset=True)
    assert Result.model_validate(public) == decorated
    table = result_table(public)
    assert table['columns'][0]['label'] == c.checked.vocabulary.entries['u.events.category'].label
    assert table['columns'][-1]['label'] == '% change'
    assert table['columns'][-1]['format'] == 'percent'
    assert table['rows'][-1][-1] == '5'
    text = render(public)
    assert '% change' in text and '500.00%' in text and 'Total' in text


@pytest.mark.parametrize('value,fmt,expected', [('2.34567', 'ratio', '2.35 : 1'), ('0.03456', 'percent', '3.46%')])
def test_display_format_keeps_raw_values(value, fmt, expected):
    cell = dict(value=dict(kind='decimal', value=value))
    assert display_value(dict(format=fmt), cell) == expected
    assert cell['value']['value'] == value


def test_ratio_and_total_format_keep_one_native_answer_job(monkeypatch, tmp_path):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup
    c, _, text = arithmetic()
    fake, warehouse = setup(monkeypatch, tmp_path, False)
    table = f'`{warehouse.project}.{warehouse.dataset_id}.u_events`'
    fake.run(None, 'DELETE FROM ' + table + ' WHERE TRUE')
    fake.run(None, 'INSERT INTO ' + table + " VALUES (DATE '2025-04-10', 30, 'A', 1, 'x', NULL), "
             "(DATE '2024-04-10', -10, 'A', 2, 'x', NULL), (DATE '2025-04-10', 90, 'B', 3, 'x', NULL), "
             "(DATE '2024-04-10', -20, 'B', 4, 'x', NULL)")
    fake.requests.clear()
    out = Planner(c.checked.vocabulary, warehouse, Hidden([], set(), [])).answer(
        text, Propose(kind='propose', base_turn=None, plan=c.checked.plan))
    assert out['kind'] == 'result', out
    assert jobs(fake) == (1, 1)
    assert Decimal(out['result']['rows'][-1]['cells'][-1]['value']['value']) == 4
    assert '4.00 : 1' in render(out['result'])


def test_question_order_keeps_each_value_citation_and_null_reason_together():
    _, result, _ = arithmetic()
    public = result.model_dump(mode='json', exclude_unset=True)
    outputs = [c for c in public['columns'] if c['kind'] != 'dimension']
    for i, column in enumerate(outputs):
        column.update(label='Output ' + str(i), display_order=float(-i))
    for row in public['rows']:
        for i, cell in enumerate(row['cells']):
            cell['citations'] = ['source-' + str(i)]
            cell['null_reasons'] = ['reason-' + str(i)]
    table = result_table(public)
    assert [c['key'] for c in table['columns'][1:1 + len(outputs)]] == [c['id'] for c in reversed(outputs)]
    assert table['rows'][0][1:1 + len(outputs)] == [c['value'].get('value') for c in reversed(public['rows'][0]['cells'])]
    assert table['cell_citations'][0] == [['source-' + str(i)] for i in reversed(range(len(outputs)))]
    assert table['null_reasons'][0] == [['reason-' + str(i)] for i in reversed(range(len(outputs)))]
