"""Share denominators keep the numerator's joined and filtered population."""
import datetime as dt
from decimal import Decimal

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.services.ask.engine.planner import result_of
from tests.plan_fixtures import model, name, semantics, metric_vocabulary as vocabulary
from tests.unit.test_plan_compiler import compiled, connection


def joined_share(filtered=False, partitioned=False):
    sem = semantics(joined=True)
    sem.d.GROUP_BY = {'by grouping': 'u.members.grouping', 'by source': 'u.events.source'}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(column='u.events.source', phrases=['x source'], op='eq', value='x'))]
    words = lexical_vocabulary(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem, aliases=list(sem.d.GROUP_BY.items()))
    dimensions = [dict(kind='column', id='d', bindings=[dict(context=name('ctx'),
        column=name('u.members.grouping'), path=[name('member_join')], level=None)])]
    if partitioned:
        dimensions.append(dict(kind='column', id='p', bindings=[dict(context=name('ctx'),
            column=name('u.events.source'), path=[], level=None)]))
    filters = [dict(kind='compare', id='f', left=dict(kind='column', name=name('u.events.source'), path=[]),
        op='eq', value=dict(kind='text', value='x'), evidence=[])] if filtered else []
    plan = model(dimensions=dimensions, filters=filters, derived=[dict(kind='share', id='s', input='m',
        remove_dimensions=['d'], remove_filters=[], evidence=[])])
    text = 'Metric by grouping' + (' by source' if partitioned else '') + ' share' + (' for x source' if filtered else '')
    return compiled(plan, text, {'by grouping': 'd', 'by source': 'p', 'share': 's', 'x source': 'f'}, v)


@pytest.mark.parametrize('filtered', [False, True])
@pytest.mark.parametrize('partitioned', [False, True])
def test_joined_share_retains_duplicate_matches_and_excludes_unmatched_rows(filtered, partitioned):
    """The breakdown counts every declared match, and its denominator does too.

    One x event has two matching groups, one has one, and an orphan has none.
    The y event also matches twice. Thus the breakdown total is 100, or 40
    after the x filter, rather than the raw event total of 159 or 129.
    """
    c = joined_share(filtered, partitioned)
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(2025, 4, 10), value, 'A', entity, source)
            for value, entity, source in [(10, 1, 'x'), (20, 2, 'x'), (99, 3, 'x'), (30, 1, 'y')]]
    with connection(rows) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
        con.executemany('INSERT INTO members VALUES (?, ?, ?)',
            [(1, 'x', 'East'), (1, 'x', 'West'), (2, 'x', 'East'), (1, 'y', 'East'), (1, 'y', 'West')])
        result = result_of(c, con.execute(c.answer.sql).fetchall(), {})
    details = [r for r in result.rows if not r.total]
    if partitioned:
        expected = {('East', 'x'): (30, .75), ('West', 'x'): (10, .25)}
        if not filtered:
            expected.update({('East', 'y'): (30, .5), ('West', 'y'): (30, .5)})
    else:
        expected = {('East',): (30, .75), ('West',): (10, .25)} if filtered else {('East',): (60, .6), ('West',): (40, .4)}
    actual = {tuple(d.value for d in r.dimensions): tuple(float(cell.value.value) for cell in r.cells) for r in details}
    assert actual == expected
    for source in ('x',) if filtered else ('x', 'y') if partitioned else (None,):
        shares = [r.cells[1].value.value for r in details if not partitioned or r.dimensions[1].value == source]
        assert sum(Decimal(str(s)) for s in shares) == 1
    total, = (r for r in result.rows if r.total)
    assert Decimal(total.cells[0].value.value) == (40 if filtered else 100)
    assert Decimal(total.cells[1].value.value) == 1
