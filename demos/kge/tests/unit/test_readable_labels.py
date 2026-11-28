"""Issue 456: periods in words, a breakdown's rows in time order, and a plan check's decline in plain words."""
from __future__ import annotations

import pytest

from kge.services.ask.engine.question import period_words
from kge.services.ask.engine.planpublic import PLAIN, gate_decline, plain_decline, plain_reason
from kge.services.ask.engine.reading import group_phrase, grouped

FISCAL = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}


@pytest.mark.parametrize('kind,value,calendar,words', [
    ('month', '2025-01-01', None, 'January 2025'),
    ('month', '2025-03', None, 'March 2025'),
    ('day', '2025-03-05', None, '5 March 2025'),
    ('calendar_quarter', '2025-04-01', None, 'Q2 2025'),
    ('calendar_quarter', '2025-Q1', None, 'Q1 2025'),
    ('year', '2025-01-01', None, '2025'),
    ('year', '2025', None, '2025'),
    ('fiscal_quarter', '2025-04-01', FISCAL, 'FY26-Q1'),
    ('fiscal_quarter', 'FY26-Q1', None, 'FY26-Q1'),
    ('fiscal_year', '2025-04-01', FISCAL, 'FY26'),
    ('month', 'MIDWEST', None, 'MIDWEST'),
])
def test_a_period_reads_as_people_write_it(kind, value, calendar, words):
    assert period_words(kind, value, calendar) == words


def test_a_breakdown_by_month_lists_its_months_in_time_order_and_in_words():
    rows = [{'dimensions': [{'kind': 'date', 'value': iso}], 'dimension_text': [words], 'cells': []}
            for iso, words in (('2025-01-01', 'January 2025'), ('2025-02-01', 'February 2025'), ('2025-03-01', 'March 2025'))]
    a = {'group_by': 'month', 'rows': {'2025-02-01': 6.0, '2025-03-01': 5.5, '2025-01-01': 5.0}, 'result': {'rows': rows}}
    assert grouped(a) == [('January 2025', 5.0), ('February 2025', 6.0), ('March 2025', 5.5)]
    assert group_phrase({'group_by': 'calendar_quarter', 'question': 'NNA by quarter'}, type('E', (), {'GROUP_BY': {}})()) == 'by quarter'


def test_any_other_breakdown_stays_largest_first_under_its_key():
    a = {'group_by': 'office.region_code', 'rows': {'MIDWEST': 1.0, 'CEO': 3.0, 'NER': None}}
    assert grouped(a) == [('CEO', 3.0), ('MIDWEST', 1.0), ('NER', None)]


@pytest.mark.parametrize('why,plain', [
    ('incompatible dimension calendar', 'This figure is not recorded on that calendar, so it cannot be broken down by it.'),
    ('missing compatible period binding', 'This figure is not recorded by period, so it cannot be given for one.'),
    ('dimension must bind every context', 'The breakdown asked for is not declared for every figure asked.'),
    ('planner cannot build this shape', 'The question asks for a combination of figures that cannot be computed as asked.'),
    ('missing readable context', 'This figure is not one you may read. It needs data outside what you may read or outside '
                                 'the products in scope, so no warehouse was queried.'),
    ('no-join policy blocks membership', 'The no-join policy keeps the data these figures come from apart, so they cannot be combined.'),
    ('The requested read is not permitted. Contact the access administrator.',
     'The requested read is not permitted. Contact the access administrator.'),
    ('A name has unrelated evidence.', 'A name in the question was matched to the wrong figure.'),
])
def test_a_plan_check_declines_in_words_a_business_user_reads(why, plain):
    assert plain_decline(why, None, None, '', 'turn') == plain


def test_the_question_s_own_unmatched_words_are_named_as_its_words():
    question = 'Retail revenue for June 2025 sparkly shiny'
    assert plain_decline('sparkly', None, None, question, 'turn') == (
        '“sparkly” in the question matches no certified definition, so it cannot be applied.')
    assert plain_decline('sparkly; shiny', None, None, question, 'turn') == (
        '“sparkly”, “shiny” in the question match no certified definition, so they cannot be applied.')


def test_a_query_the_sql_gate_stopped_keeps_its_governance_reason():
    assert gate_decline(['column wealth.client.name is restricted personal data']) == (
        'column wealth.client.name is restricted personal data')
    assert gate_decline(['SELECT * is not allowed: name each column']) == (
        'The query this question needs did not pass the SQL gate, so it did not run.')


def test_every_plan_check_the_code_raises_reads_as_a_sentence():
    import re
    from kge.pipeline.settings import ROOT
    sources = [ROOT / 'kge/semantics' / name for name in ('plancheck.py', 'compile.py', 'planperiods.py', 'coverage.py',
                                                          'planhistory.py', 'plan.py')]
    sources += [ROOT / 'kge/services/ask/engine' / name for name in ('planner.py', 'planmodel.py', 'planrules.py')]
    found = re.compile(r"(?:ValueError|PlanDenied|Decision\('(?:abstain|refuse)',)\s*\(?'([^'{]+)'")
    raised = {m for path in sources for m in found.findall(path.read_text()) if m.strip(' ;')}
    assert len(raised) > 60, len(raised)
    unmapped = sorted(m for m in raised if m[:1].islower() and m not in PLAIN and not m.startswith('SQL gate'))
    assert not unmapped, unmapped
    unread = {m: plain_reason(m, '') for m in raised if not re.fullmatch(r'[A-Z“].*[.?]', plain_reason(m, ''))}
    assert not unread, unread


def labelled(plan, text, assignments, rows):
    from kge.services.ask.engine.planner import display_outputs, labelled_result, result_of
    from tests.unit.test_plan_compiler import compiled, connection
    c = compiled(plan, text, assignments)
    with connection(rows) as con:
        data = con.execute(c.answer.sql).fetchall()
    result = labelled_result(result_of(c, data, {}), c.checked.plan, c.checked.vocabulary, text, 'turn')
    public = result.model_dump(mode='json', exclude_unset=True)
    return public, [column for column, _ in display_outputs(public)]


def test_two_comparisons_on_one_figure_show_that_figure_once():
    import datetime as dt
    from tests.plan_fixtures import model, name
    plan = model(time=dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('cal'))),
                 comparisons=[dict(kind='previous_period', id='p', inputs=['m'], evidence=[]),
                              dict(kind='previous_year', id='y', inputs=['m'], evidence=[])])
    text = 'Metric 2025-04 previous period and previous year'
    rows = [(dt.date(2025, 4, 10), 30, 'A', 1, 'x'), (dt.date(2025, 3, 10), 20, 'A', 2, 'x'), (dt.date(2024, 4, 10), 10, 'A', 3, 'x')]
    public, shown = labelled(plan, text, {'2025-04': 'time', 'previous period': 'p', 'previous year': 'y'}, rows)
    cells = public['rows'][0]['cells']
    outputs = [c for c in public['columns'] if c['kind'] != 'dimension']
    values = [cells[outputs.index(column)]['value']['value'] for column in shown]
    assert sorted(float(v) for v in values) == [10.0, 20.0, 30.0], (values, [c['id'] for c in shown])
    assert len({column['label'] for column in shown}) == len(shown)


@pytest.mark.parametrize('op,words', [('ge', 'at least'), ('eq', 'equal to')])
def test_a_numeric_condition_on_a_measure_keeps_the_measure_s_name(op, words):
    import datetime as dt
    from tests.plan_fixtures import model, name
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
                 'op': op, 'value': {'kind': 'decimal', 'value': '20.00'}, 'evidence': []}
    plan = model(measures=[{'kind': 'measure', 'id': 'm', 'measure': name('u.metric'), 'context': name('ctx'), 'mode': 'current',
                            'filters': [predicate]}])
    text = f'Metric with value {words} 20.00'
    rows = [(dt.date(2025, 4, 10), 30, 'A', 1, 'x'), (dt.date(2025, 4, 11), 20, 'A', 2, 'x')]
    public, shown = labelled(plan, text, {'Metric': 'm', 'value': 'f', words: 'f', '20.00': 'f'}, rows)
    assert [column['label'] for column in shown] == [f'Context one · value {words} 20.00'], shown


@pytest.mark.parametrize('values,label', [(('t.RVW_ID',), 'Suitability reviews'),
                                          (('t.AMOUNT',), 'Suitability reviews with suitability review')])
def test_a_count_by_its_entity_s_own_key_is_named_for_the_entity_alone(values, label):
    from types import SimpleNamespace as N
    from kge.services.ask.engine.planner import count_label
    vocabulary = N(contexts={'c': N(id='context:mapped:t', label='Suitability Review', values=values, measure='x')},
                   entries={'x': None}, entities={'e': {'label': 'suitability_review', 'table': 't', 'key': ('RVW_ID',)}},
                   candidates=lambda text: [])
    measure = N(entity=N(id='e'), context=N(id='c'))
    assert count_label(measure, vocabulary, 'Number of reviews escalated in 2025', 'turn') == label


def test_a_time_mode_comparison_names_each_side_by_the_metric_and_its_own_basis(monkeypatch):
    from kge.semantics.plan import Result
    from kge.services.ask.engine import planner
    monkeypatch.setattr(planner, 'measure_label', lambda *args, as_asked=True: 'Current Approved Metric' if as_asked else 'Approved Metric')
    from kge.services.ask.engine.planner import display_outputs, labelled_result
    from tests.plan_fixtures import semantics, vocabulary
    from tests.unit.test_answer_shape_fixes import checked_read, comparison_plan, execute
    from tests.unit.test_plan_compiler import connection
    text = 'current Metric June 2025 versus as reported difference percent change'
    assignments = {'Metric': 'm', 'June 2025': 'time', 'current': 'c', 'versus': 'c', 'as reported': 'c',
                   'difference': 'delta', 'percent change': 'pct'}
    checked = checked_read(comparison_plan('time_mode'), text, assignments, vocabulary(semantics(release=True)))
    with connection() as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-30', '2025-06-30', TRUE)")
        result = execute(checked, con)
    public = labelled_result(Result.model_validate(result), checked.plan, checked.vocabulary, text, 'turn').model_dump(
        mode='json', exclude_unset=True)
    labels = [column['label'] for column, _ in display_outputs(public)]
    assert 'Approved Metric · current' in labels and 'Approved Metric · as reported' in labels, labels
    assert not any(label.casefold().startswith('current') and 'as reported' in label for label in labels), labels


def test_a_time_mode_comparison_of_a_count_keeps_what_it_counts():
    from kge.semantics.plan import Result
    from kge.services.ask.engine.planner import display_outputs, labelled_result
    from tests.plan_fixtures import metric_vocabulary as vocabulary, model, name, semantics
    from tests.unit.test_answer_shape_fixes import arithmetic, checked_read, count, execute
    from tests.unit.test_plan_compiler import connection
    plan = model(measures=[count()], time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}},
                 comparisons=[{'kind': 'time_mode', 'id': 'c', 'inputs': ['n'], 'evidence': [], 'base': 'current',
                               'other': 'as_reported'}], derived=arithmetic('c/n/base', 'c/n/other'))
    text = 'count of Entities June 2025 current versus as reported difference percent change'
    assignments = {'count of': 'n', 'Entities': 'n', 'June 2025': 'time', 'current': 'c', 'versus': 'c', 'as reported': 'c',
                   'difference': 'delta', 'percent change': 'pct'}
    checked = checked_read(plan, text, assignments, vocabulary(semantics(release=True)))
    with connection() as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-30', '2025-06-30', TRUE)")
        result = execute(checked, con)
    public = labelled_result(Result.model_validate(result), checked.plan, checked.vocabulary, text, 'turn').model_dump(
        mode='json', exclude_unset=True)
    labels = [column['label'] for column, _ in display_outputs(public)]
    assert labels[:2] == ['Entities with context one · current', 'Entities with context one · as reported'], labels


def test_a_metric_named_without_its_basis_keeps_its_declared_modifier():
    from dataclasses import replace
    from kge.semantics.query import Rule
    from kge.semantics.vocabulary import compiled
    from kge.services.ask.engine.planner import measure_label
    from tests.plan_fixtures import grounded, model, name, semantics, vocabulary
    sem = semantics()
    sem.contexts['directed'] = replace(sem.contexts['ctx'], id='directed', term='Directed Metric', label='Directed Metric', grain='day')
    sem.contexts['approved'] = replace(sem.contexts['ctx'], id='approved', term='Approved Metric', label='Approved Metric', grain='day',
        filters=(*sem.contexts['ctx'].filters, Rule('archived', 'Archived rows', "events.source = 'x'", 'u.events')))
    del sem.contexts['ctx']
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(label='Approved (archived)', modifier=True, phrases=['approved'],
                                                           column='u.events.source', op='eq', value='x', **{'with': ['Directed Metric']}))]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem)
    text = 'approved June 2025 Directed Metric'
    predicate = dict(kind='compare', id='archive', left=dict(kind='column', name=name('u.events.source'), path=[]),
                     op='eq', value=dict(kind='text', value='x'), evidence=[])
    output = model().measures[0].model_dump()
    output['context'] = name('directed')
    plan = grounded(model(measures=[output], filters=[predicate],
                          time=dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('cal')))),
                    text, v, {'approved': 'archive', 'Directed Metric': 'm', 'June 2025': 'time'})
    labels = {as_asked: measure_label(plan.measures[0], plan, v, text, 'turn', as_asked=as_asked) for as_asked in (True, False)}
    assert labels == {True: 'Approved (archived) Directed Metric', False: 'Approved (archived) Directed Metric'}


def test_a_client_reads_each_compared_figure_once_and_the_engine_keeps_its_repeats():
    from kge.services.ask.service import client_result

    def cell(value):
        return {'value': {'kind': 'decimal', 'value': value}, 'citations': [], 'null_reasons': []}
    result = {'columns': [{'id': 't0', 'kind': 'dimension', 'label': 'Month'},
                          {'id': 'm0', 'kind': 'measure', 'label': 'Certified NNA'},
                          {'id': 'c/base', 'kind': 'comparison', 'label': 'Certified NNA', 'repeats': 'm0'},
                          {'id': 'x0', 'kind': 'derived', 'label': 'Difference'}],
              'rows': [{'dimensions': [{'kind': 'text', 'value': '2025-03'}], 'cells': [cell('5'), cell('5'), cell('-1')]}]}
    engine = {'behaviour': 'combined', 'result': result}
    client = client_result(engine)
    assert [c['id'] for c in client['result']['columns']] == ['t0', 'm0', 'x0']
    assert not any('repeats' in c for c in client['result']['columns'])
    assert [c['value']['value'] for c in client['result']['rows'][0]['cells']] == ['5', '-1']
    assert [c['id'] for c in engine['result']['columns']] == ['t0', 'm0', 'c/base', 'x0']
    assert len(engine['result']['rows'][0]['cells']) == 3
    plain = {'behaviour': 'figure', 'result': {'columns': [{'id': 'm0', 'kind': 'measure'}], 'rows': []}}
    assert client_result(plain) is plain


def test_every_plan_grain_has_a_name():
    """A time breakdown the plan allows is named in an answer: week as much as month ('NNA by week')."""
    from typing import get_args

    from kge.semantics.plan import Grain
    from kge.services.ask.engine.question import GRAIN_NAMES
    assert set(GRAIN_NAMES) == set(get_args(Grain))
    assert GRAIN_NAMES['week'] == 'Week' and GRAIN_NAMES['calendar_quarter'] == 'Quarter'
