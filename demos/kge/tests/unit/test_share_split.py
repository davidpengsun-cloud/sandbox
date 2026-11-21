"""A share of the total split by the column of one named value: the value before a share word, or before 'the rest',
groups by its column instead of filtering on it, so the answer shows that value beside the rest of the total."""
import datetime as dt

import pytest

from kge.semantics.coverage import ground_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.plan_fixtures import metric_vocabulary as vocabulary
from tests.plan_fixtures import model, name
from tests.unit.test_plan_compiler import connection
from tests.unit import test_structural_vocabulary as structural

reader = structural.reader
ROWS = [(dt.date(2025, 4, 1), 15, 'A', 1, 'x'), (dt.date(2025, 5, 1), 3, 'B', 2, 'x'),
        (dt.date(2025, 6, 1), 2, 'C', 3, 'y'), (dt.date(2024, 4, 1), 99, 'A', 4, 'x')]


def split(plan, result):
    """The plan's dimensions and filters, and its rows as (category, share)."""
    assert [d.kind for d in plan.derived] == ['share']
    with connection(ROWS) as con:
        rows = con.execute(result.answer.sql).fetchall()
    return {r[0]: r[2] for r in rows}


@pytest.mark.parametrize('text', ['A category share of Metric for Q2 2025',
                                  'What is the A category share of Metric for Q2 2025?'])
def test_value_before_share_word_splits_by_its_column(reader, text):
    plan, result = reader(text)
    assert len(plan.dimensions) == 1 and not plan.filters
    assert split(plan, result) == pytest.approx({'A': .75, 'B': .15, 'C': .10})


@pytest.mark.parametrize('word', ['rest', 'remainder'])
def test_value_then_the_rest_splits_by_its_column(reader, word):
    plan, result = reader(f'For Q2 2025, how much Metric is from A category and how much from the {word}?')
    assert len(plan.dimensions) == 1 and not plan.filters and len(plan.measures) == 1
    shares = split(plan, result)
    assert shares == pytest.approx({'A': .75, 'B': .15, 'C': .10}) and sum(shares.values()) == pytest.approx(1)


def test_unanchored_rest_still_refuses(reader):
    with pytest.raises(AssertionError, match='remaining share'):
        reader('Metric by category rest of total for Q2 2025')


def test_a_value_of_an_ungroupable_column_stays_a_filter(reader):
    plan, result = reader('For x source, A category share of Metric for Q2 2025')
    assert len(plan.dimensions) == 1 and len(plan.filters) == 1
    assert split(plan, result) == pytest.approx({'A': 15 / 18, 'B': 3 / 18})


def test_the_rest_of_an_ungroupable_column_still_refuses(reader):
    with pytest.raises(AssertionError, match='remaining share'):
        reader('For Q2 2025, how much Metric is from x source and how much from the rest?')


def test_a_share_beside_a_named_breakdown_keeps_its_value_as_a_filter(reader):
    plan, _ = reader('A category share of Metric by category for Q2 2025')
    assert len(plan.dimensions) == 1 and len(plan.filters) == 1


def test_a_model_plan_cannot_keep_the_split_value_as_its_filter():
    """The split value is a breakdown, so a supplied plan that filters on it and shares over its column is refused."""
    text = 'For Q2 2025, how much Metric is from A category and how much from the rest?'
    vocab = vocabulary()
    plan = model(dimensions=[dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])],
                 filters=[dict(kind='compare', id='f', left=dict(kind='column', name=name('u.events.category'), path=[]), op='eq',
                               value=dict(kind='text', value='A'), evidence=[])],
                 derived=[dict(kind='share', id='s', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])])
    assert not isinstance(check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text), CheckedPlan)


@pytest.mark.parametrize('text', ['Metric rest of total for Q2 2025',
                                  'For Q2 2025, how much Metric is from x source for A category and how much from the rest?',
                                  'For Q2 2025, how much Metric is from A category and x source and how much from the rest?'])
def test_a_rest_with_no_single_value_to_split_still_refuses(reader, text):
    with pytest.raises(AssertionError, match='remaining share'):
        reader(text)


@pytest.mark.parametrize('text', ['A category Metric share of total for Q2 2025', 'A category and B category share of Metric for Q2 2025'])
def test_a_share_word_away_from_its_value_or_after_two_values_does_not_split(reader, text):
    with pytest.raises(AssertionError):
        reader(text)


def test_a_model_plan_must_still_build_the_share_the_rest_asks_for():
    text = 'For Q2 2025, how much Metric is from A category and how much from the rest?'
    vocab = vocabulary()
    plan = model(dimensions=[dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])])
    assert not isinstance(check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text), CheckedPlan)
