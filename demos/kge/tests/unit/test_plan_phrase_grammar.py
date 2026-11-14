import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import grounded, model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import connection


class Reply:
    def __init__(self, plan):
        self.plan = plan

    def json(self, prompt):
        return Propose(kind='propose', base_turn=None, plan=self.plan).model_dump(mode='json', by_alias=True)


def compile_read(plan, text, vocab, assignments):
    proposed = read(Reply(grounded(plan, text, vocab, assignments)), text, vocab)
    assert proposed.kind == 'propose', proposed
    checked = check_plan(proposed.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    return compiled


@pytest.mark.parametrize('wording,grain,first,last,grouped', [
    ('April through June 2025', 'month', '2025-04', '2025-06', False),
    ('from April to June 2025', 'month', '2025-04', '2025-06', False),
    ('Q2 through Q3 2025', 'calendar_quarter', '2025-Q2', '2025-Q3', False),
    ('Q2 to Q3 2025', 'calendar_quarter', '2025-Q2', '2025-Q3', False),
    ('from Q2 to Q3 2025', 'calendar_quarter', '2025-Q2', '2025-Q3', False),
    ('each quarter from Q2 to Q3 2025', 'calendar_quarter', '2025-Q2', '2025-Q3', True),
    ('each month from April to June 2025', 'month', '2025-04', '2025-06', True),
])
def test_shared_year_endpoints_survive_read_check_and_gate(wording, grain, first, last, grouped):
    text = 'Give Metric for ' + wording
    v = vocabulary()
    time = dict(kind='range', first=dict(kind=grain, value=first, calendar=name('cal')),
                last=dict(kind=grain, value=last, calendar=name('cal')))
    dims = [dict(kind='time', id='d', grain=grain, calendar=name('cal'))] if grouped else []
    assignments = {p.text: 'time' for p in ledger(text, 'turn', v) if p.kind == 'time' or p.operation == 'range'}
    assignments.update({p.text: 'd' for p in ledger(text, 'turn', v) if p.operation in ('dimension', grain)})
    compiled = compile_read(model(time=time, dimensions=dims), text, v, assignments)
    with connection() as con:
        actual = con.execute(compiled.answer.sql).fetchall()
        assert sum(r[1 if grouped else 0] or 0 for r in actual) == con.execute(
            "SELECT sum(value) FROM events WHERE occurred BETWEEN DATE '2025-04-01' AND DATE '2025-09-30'").fetchone()[0]
    short = model(time=dict(kind='single', period=time['last']), dimensions=dims)
    assert not isinstance(check_plan(grounded(short, text, v, assignments), v, text), CheckedPlan)


@pytest.mark.parametrize('amount', ['$20K', '$20,000', '$20,000.00', '$0.02M'])
def test_currency_amount_is_one_literal_and_cannot_lose_its_scale(amount):
    text = f'Sum Metric above {amount} for April 2025'
    v = vocabulary()
    pred = dict(kind='compare', id='f', left=dict(kind='column', name=name('u.events.value'), path=[]),
                op='gt', value=dict(kind='integer', value=20000), evidence=[])
    p = model(filters=[pred], time=dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('cal'))))
    compiled = compile_read(p, text, v, {'above': 'f', amount: 'f', 'April 2025': 'time'})
    with connection([(dt.date(2025, 4, 10), 30000, 'A', 1, 'x'), (dt.date(2025, 4, 11), 100, 'B', 2, 'x')]) as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] == con.execute('SELECT sum(value) FROM events WHERE value > 20000').fetchone()[0]
    pred['value']['value'] = 20
    wrong = model(filters=[pred], time=p.time.model_dump())
    assert check_plan(grounded(wrong, text, v, {'above': 'f', amount: 'f', 'April 2025': 'time'}), v, text).kind == 'abstain'
    piece, = (p for p in ledger(text, 'turn', v) if p.kind == 'literal')
    assert piece.text == amount and Decimal(piece.value) == 20000


@pytest.mark.parametrize('opening', ['Break out', 'Break down', 'Show me', 'List', 'Give'])
def test_connectives_do_not_create_unknown_words_or_drop_content(opening):
    v = vocabulary()
    text = opening + ' Metric based on April 2025'
    p = model(time=dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('cal'))))
    compile_read(p, text, v, {'April 2025': 'time'})
    assert check_plan(grounded(p, text + ' unapproved', v, {'April 2025': 'time'}), v, text + ' unapproved').kind == 'abstain'


def test_separated_qualifier_uses_the_pack_phrase_and_nearest_measure():
    domain = SimpleNamespace(ALIASES={'certified mass': 'Audited Mass', 'mass': 'Mass', 'certified energy': 'Audited Energy'}, UNIT_WORDS={})
    req = parse('Give certified Q3 2025 mass and energy', ['Mass', 'Energy', 'Audited Mass', 'Audited Energy'], domain)
    assert 'Audited Mass' in req.terms_found and 'Audited Energy' not in req.terms_found
    assert parse('Give certified Q3 2025 balance', ['Mass'], domain).terms_found == []
    sem = semantics()
    object.__setattr__(sem.contexts['ctx'], 'term', 'Audited Mass')
    v = vocabulary(sem, aliases=[('certified mass', 'ctx'), ('mass', 'ctx')])
    text = 'Give certified May 2025 mass'
    compiled = compile_read(model(time=dict(kind='single', period=dict(kind='month', value='2025-05', calendar=name('cal')))),
                            text, v, {'May 2025': 'time'})
    with connection() as con:
        assert con.execute(compiled.answer.sql).fetchone()[0] is None
    assert next(p for p in ledger(text, 'turn', v) if p.text == 'certified').candidates == ('ctx',)


def test_longest_declared_type_phrase_owns_its_own_column():
    from kge.semantics.vocabulary import compiled
    sem = semantics()
    sem.d.VOCABULARY = [dict(kind='value', params=dict(column='u.events.category', values=['A'])),
                        dict(kind='predicate', params=dict(column='u.events.category', phrases=['A category'], op='eq', value='A'))]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem)
    text = 'Metric for the A category'
    pred = dict(kind='compare', id='f', left=dict(kind='column', name=name('u.events.category'), path=[]),
                op='eq', value=dict(kind='text', value='A'), evidence=[])
    compiled_plan = compile_read(model(filters=[pred]), text, v, {'A category': 'f'})
    with connection() as con:
        assert con.execute(compiled_plan.answer.sql).fetchone()[0] == con.execute("SELECT sum(value) FROM events WHERE category = 'A'").fetchone()[0]


@pytest.mark.parametrize('word,operation', [('highest', 'desc'), ('most', 'desc'), ('lowest', 'asc'), ('cumulative', 'running_total'),
                                          ('contribution', 'share'), ('contribute', 'share'), ('percent difference', 'percent_change'),
                                          ('month-over-month', 'previous_period'), ('%', 'percent_change')])
def test_operation_phrases_remain_meaningful(word, operation):
    piece, = ledger(word, 'turn', vocabulary())
    assert piece.operation == operation


def test_cumulative_repeated_measure_cannot_select_another_requested_input():
    from tests.unit.test_plan_relational_matrix import replace_context
    sem = semantics()
    sem.contexts['extra'] = replace_context(sem.contexts['ctx'], id='extra', label='Extra', term='Extra')
    v = vocabulary(sem, aliases=[('certified metric', 'ctx')])
    text = 'Certified Metric and Extra by month for April through June 2025 with cumulative Metric'
    time = dict(kind='range', first=dict(kind='month', value='2025-04', calendar=name('cal')),
                last=dict(kind='month', value='2025-06', calendar=name('cal')))
    extra = dict(kind='measure', id='e', measure=name('u.metric'), context=name('extra'), mode='current', filters=[])
    derived = dict(kind='running_total', id='r', input='e', partition_by=[], order_by='d', evidence=[])
    assignments = {'Certified Metric': 'm', 'Extra': 'e', 'by': 'd', 'month': 'd', 'April': 'time',
                   'through': 'time', 'June 2025': 'time', 'cumulative': 'r', 'Metric': 'r'}
    p = model(measures=[model().measures[0].model_dump(), extra], time=time,
              dimensions=[dict(kind='time', id='d', grain='month', calendar=name('cal'))], derived=[derived])
    assert check_plan(grounded(p, text, v, assignments), v, text).kind == 'abstain'
    derived['input'] = 'm'
    p = model(measures=[model().measures[0].model_dump(), extra], time=time,
              dimensions=[dict(kind='time', id='d', grain='month', calendar=name('cal'))], derived=[derived])
    compile_read(p, text, v, assignments)


def test_only_the_released_title_can_supply_the_clients_possessive():
    sem = semantics()
    sem.d.TITLE = 'Lumen Cooperative governance graph'
    v = vocabulary(sem)
    text = "Give Lumen's Metric during April 2025"
    p = model(time=dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('cal'))))
    compile_read(p, text, v, {'April 2025': 'time'})
    other = text.replace('Lumen', 'Elsewhere')
    assert check_plan(grounded(p, other, v, {'April 2025': 'time'}), v, other).kind == 'abstain'
