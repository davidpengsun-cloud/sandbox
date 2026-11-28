"""Neutral pack names and structural comparison families have the same governed meaning."""
import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary, value_entries
from kge.semantics.query import Rule
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import connection


FAMILIES = [
    ('2025-06-10', '2025-06-10', '2025-06-09', 'previous_period', word)
    for word in ('DoD', 'day over day', 'day-over-day', 'vs previous day')
] + [
    ('2025-W24', '2025-06-10', '2025-06-03', 'previous_period', word)
    for word in ('WoW', 'week over week', 'week-over-week', 'vs previous week')
] + [
    ('2025-06', '2025-06-10', '2025-05-10', 'previous_period', word)
    for word in ('MoM', 'month over month', 'month-over-month', 'vs previous month')
] + [
    ('2025-Q2', '2025-06-10', '2025-03-10', 'previous_period', word)
    for word in ('QoQ', 'quarter over quarter', 'quarter-over-quarter', 'vs previous quarter')
] + [
    ('2025-06', '2025-06-10', '2024-06-10', 'previous_year', word)
    for word in ('YoY', 'year over year', 'year-over-year', 'vs last year')
]


def declared(monkeypatch, ambiguous=False):
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], label='Metric', term='Metric', grain='day')
    sem.tables['u.archive'] = dict(sem.tables['u.events'])
    sem.d.PERIODS['u.archive'] = dict(sem.d.PERIODS['u.events'])
    sem.contexts['archived'] = replace(sem.contexts['ctx'], id='archived', label='Archived Metric',
        term='Archived Metric', measure='u.archived_metric', fact='u.archive', grain='day',
        expression='sum(archive.value)', values=('u.archive.value',),
        filters=(Rule('archive-positive', '', 'archive.value >= 0', 'u.archive'),))
    sem.d.GROUP_BY = {'by category': 'u.events.category', 'by archived category': 'u.events.category'}
    sem.d.ALIASES = {'metric': 'Metric', 'archived metric': 'Archived Metric'}
    sem.d.UNIT_WORDS = {}
    sem.measure_declarations = {'u.metric': {}, 'u.archived_metric': {}}
    sem.output_metadata = {}
    specs = [MeasureSpec(identifier, 'USD', 'shared', True) for identifier in sem.measure_declarations]
    aliases = [('Metric', 'ctx'), ('Archived Metric', 'archived'), *sem.d.GROUP_BY.items()]
    if ambiguous:
        sem.contexts['alternative'] = replace(sem.contexts['ctx'], id='alternative', label='Alternative metric',
                                              term='Metric', measure='u.alternative_metric', grain='day')
        specs.append(MeasureSpec('u.alternative_metric', 'USD', 'shared', True))
        sem.measure_declarations['u.alternative_metric'] = {}
        aliases.append(('Metric', 'alternative'))
    vocab = build_vocabulary(sem, vocabulary().binding, ['u'], ['u'], specs, aliases=aliases)
    eng = SimpleNamespace(semantics=sem, FISCAL_START=4, **vars(sem.d))
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return eng, vocab


def interpreted(eng, vocab, text):
    reply = planrules.read(eng, parse(text, ['Metric', 'Archived Metric'], eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked

    class Supplied:
        def json(self, prompt):
            return Propose(kind='propose', base_turn=None,
                           plan=ground_plan(reply.plan, text, 'turn', vocab)).model_dump(mode='json', by_alias=True)

    replay = planmodel.read(Supplied(), text, vocab)
    assert replay.kind == 'propose', replay
    assert isinstance(check_plan(replay.plan, vocab, text), CheckedPlan)
    return reply.plan, compile_plan(checked)


def rows(compiled, observed, archived=()):
    assert compiled.answer is not None
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(observed) as con:
        con.execute('CREATE TABLE archive(occurred DATE, value DECIMAL(18,2), category VARCHAR, entity_id INT, source VARCHAR, private VARCHAR)')
        for row in archived:
            con.execute('INSERT INTO archive VALUES (?, ?, ?, ?, ?, NULL)', row)
        cursor = con.execute(compiled.answer.sql)
        return [dict(zip([column[0] for column in cursor.description], row, strict=True)) for row in cursor.fetchall()]


@pytest.mark.parametrize('period,current,other,kind,word', FAMILIES)
def test_structural_offset_families_with_dollar_and_percent_changes(monkeypatch, period, current, other, kind, word):
    eng, vocab = declared(monkeypatch)
    text = f'Metric for {period} {word} in dollars and in percent'
    plan, compiled = interpreted(eng, vocab, text)
    comparison, = plan.comparisons
    assert comparison.kind == kind
    derived = {node.kind: node.id for node in plan.derived}
    assert set(derived) == {'difference', 'percent_change'}
    observed = [(dt.date.fromisoformat(day), value, 'A', 1, 'x') for day, value in ((current, 34), (other, 25))]
    result, = rows(compiled, observed)
    assert result[derived['difference']] == 9
    assert result[derived['percent_change']] == pytest.approx(.36)


@pytest.mark.parametrize('text', [
    'Archived Metric for June 2025',
    'Archived for June 2025 Metric',
    'Archived June 2025 Metric',
])
def test_a_declared_compound_name_crosses_period_words_generically(monkeypatch, text):
    eng, vocab = declared(monkeypatch)
    plan, compiled = interpreted(eng, vocab, text)
    output, = plan.measures
    assert output.context.id == 'archived'
    result, = rows(compiled, [(dt.date(2025, 6, 10), 34, 'A', 1, 'x')],
                   [(dt.date(2025, 6, 10), 25, 'A', 1, 'x')])
    assert result[output.id] == 25


@pytest.mark.parametrize('operands,current_first', [
    ('current against Archived Metric for June 2025', True),
    ('current against Archived June 2025 Metric', True),
    ('Archived Metric against current for June 2025', False),
    ('Archived June 2025 Metric against current', False),
])
def test_a_bare_current_operand_resolves_the_declared_unqualified_noun(monkeypatch, operands, current_first):
    eng, vocab = declared(monkeypatch)
    plan, compiled = interpreted(eng, vocab, operands + ' in dollars and in percent')
    by_context = {output.context.id: output.id for output in plan.measures}
    assert set(by_context) == {'ctx', 'archived'}
    comparison, = plan.comparisons
    assert comparison.kind == 'contexts'
    assert (comparison.left, comparison.right) == tuple(by_context[context] for context in (
        ('ctx', 'archived') if current_first else ('archived', 'ctx')))
    result, = rows(compiled, [(dt.date(2025, 6, 10), 34, 'A', 1, 'x')],
                   [(dt.date(2025, 6, 10), 25, 'A', 1, 'x')])
    derived = {node.kind: node.id for node in plan.derived}
    assert (result[by_context['ctx']], result[by_context['archived']]) == (34, 25)
    assert result[derived['difference']] == (9 if current_first else -9)
    assert result[derived['percent_change']] == pytest.approx(.36 if current_first else -9 / 34)


def test_a_grouping_name_does_not_qualify_a_measure_elsewhere(monkeypatch):
    eng, vocab = declared(monkeypatch)
    text = 'Metric for June 2025 by archived category'
    plan, compiled = interpreted(eng, vocab, text)
    output, = plan.measures
    assert output.context.id == 'ctx'
    assert not plan.comparisons
    dimension, = plan.dimensions
    assert dimension.bindings[0].column.id == 'u.events.category'
    assert not any('archived' in piece.candidates for piece in ledger(text, 'turn', vocab))
    result, = rows(compiled, [(dt.date(2025, 6, 10), 34, 'A', 1, 'x')])
    assert result[output.id] == 34


def test_a_bare_current_operand_does_not_choose_between_ambiguous_nouns(monkeypatch):
    eng, vocab = declared(monkeypatch, ambiguous=True)
    text = 'current against Archived Metric for June 2025 in dollars and in percent'
    reply = planrules.read(eng, parse(text, ['Metric', 'Archived Metric'], eng), vocab, PRESETS['graph'])
    if reply.kind == 'propose':
        assert not isinstance(check_plan(reply.plan, vocab, text), CheckedPlan)
    else:
        assert reply.kind in ('abstain', 'clarify')


def filter_declarations(monkeypatch, declarations):
    eng, original = declared(monkeypatch)
    sem = eng.semantics
    sem.d.VOCABULARY = declarations
    words = lexical_vocabulary(declarations)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    eng = SimpleNamespace(semantics=sem, FISCAL_START=4, **vars(sem.d))
    vocab = build_vocabulary(sem, original.binding, ['u'], ['u'], list(original.measures.values()),
                             aliases=[('Metric', 'ctx'), ('Archived Metric', 'archived'), *sem.d.GROUP_BY.items()],
                             values=value_entries(declarations))
    return eng, vocab


def test_a_longer_declared_predicate_displaces_only_the_overlapping_value_name(monkeypatch):
    eng, vocab = filter_declarations(monkeypatch, [
        dict(kind='value', params=dict(column='u.events.category', values=['Amber'])),
        dict(kind='predicate', params=dict(column='u.events.source', phrases=['Amber category'], op='eq', value='x')),
    ])
    text = 'Metric for June 2025 for Amber category'
    request = parse(text, ['Metric', 'Archived Metric'], eng)
    assert set(request.filtered) == {'u.events.category', 'u.events.source'}
    plan, compiled = interpreted(eng, vocab, text)
    predicates = [node for node in plan.nodes() if node.kind == 'compare']
    predicate, = predicates
    assert (predicate.left.name.id, predicate.value.value) == ('u.events.source', 'x')
    observed = [(dt.date(2025, 6, 10), value, category, 1, source) for value, category, source in (
        (99, 'Amber', 'y'), (34, 'Other', 'x'), (5, 'Amber', 'x'))]
    result, = rows(compiled, observed)
    assert result[plan.measures[0].id] == 39


def test_a_declared_code_name_identity_retains_the_canonical_predicate(monkeypatch):
    eng, vocab = filter_declarations(monkeypatch, [
        dict(kind='value', params=dict(column='u.events.category', values=['Amber'])),
        dict(kind='value', params=dict(column='u.events.source', values=['AMBER'])),
        dict(kind='predicate', params=dict(column='u.events.source', phrases=['Amber'], op='eq', value='AMBER')),
        dict(kind='code_name_pair', params=dict(code='u.events.source', name='u.events.category',
                                              members=[dict(code='AMBER', name='Amber')])),
    ])
    text = 'Metric for June 2025 for Amber'
    request = parse(text, ['Metric', 'Archived Metric'], eng)
    assert set(request.filtered) == {'u.events.category', 'u.events.source'}
    plan, compiled = interpreted(eng, vocab, text)
    predicate, = [node for node in plan.nodes() if node.kind == 'compare']
    assert (predicate.left.name.id, predicate.value.value) == ('u.events.source', 'AMBER')
    result, = rows(compiled, [(dt.date(2025, 6, 10), value, category, 1, source)
                             for value, category, source in ((34, 'Amber', 'AMBER'), (99, 'Other', 'OTHER'))])
    assert result[plan.measures[0].id] == 34


@pytest.mark.parametrize('second_value', ('x', 'A'))
def test_overlapping_predicates_without_a_unique_longer_meaning_remain_ambiguous(monkeypatch, second_value):
    eng, vocab = filter_declarations(monkeypatch, [
        dict(kind='predicate', params=dict(column='u.events.source', phrases=['Amber group'], op='eq', value='x')),
        dict(kind='predicate', params=dict(column='u.events.category', phrases=['Amber group'], op='eq', value=second_value)),
    ])
    text = 'Metric for June 2025 for Amber group'
    request = parse(text, ['Metric', 'Archived Metric'], eng)
    assert set(request.filtered) == {'u.events.category', 'u.events.source'}
    reply = planrules.read(eng, request, vocab, PRESETS['graph'])
    if reply.kind == 'propose':
        assert not isinstance(check_plan(reply.plan, vocab, text), CheckedPlan)
    else:
        assert reply.kind in ('abstain', 'clarify')


def test_a_period_preposition_does_not_turn_the_quarter_into_a_value_list(monkeypatch):
    eng, vocab = declared(monkeypatch)
    plan, compiled = interpreted(eng, vocab, 'Metric by month in the third quarter of 2025')
    assert (plan.time.kind, plan.time.period.kind, plan.time.period.value) == ('single', 'calendar_quarter', '2025-Q3')
    assert not plan.filters
    observed = [(dt.date(2025, month, 10), value, 'A', 1, 'x')
                for month, value in ((6, 99), (7, 10), (8, 20), (9, 30))]
    result = rows(compiled, observed)
    dimension, = plan.dimensions
    assert [(row[dimension.id], row[plan.measures[0].id]) for row in result] == [
        (dt.date(2025, 7, 1), 10), (dt.date(2025, 8, 1), 20), (dt.date(2025, 9, 1), 30)]


def test_a_longer_grouping_alias_does_not_add_its_shorter_prefix_dimension(monkeypatch):
    eng, original = declared(monkeypatch)
    sem = eng.semantics
    sem.d.GROUP_BY = {'by category': 'u.events.category', 'by category code': 'u.events.source'}
    eng.GROUP_BY = sem.d.GROUP_BY
    vocab = build_vocabulary(sem, original.binding, ['u'], ['u'], list(original.measures.values()),
                             aliases=[('Metric', 'ctx'), ('Archived Metric', 'archived'), *sem.d.GROUP_BY.items()])
    text = 'Metric for June 2025 by category code'
    assert parse(text, ['Metric', 'Archived Metric'], eng).group_by == ['u.events.source']
    plan, compiled = interpreted(eng, vocab, text)
    dimension, = plan.dimensions
    assert dimension.bindings[0].column.id == 'u.events.source'
    observed = [(dt.date(2025, 6, 10), value, category, 1, source)
                for value, category, source in ((10, 'A', 'x'), (20, 'B', 'x'), (7, 'A', 'y'))]
    result = rows(compiled, observed)
    assert {row[dimension.id]: row[plan.measures[0].id] for row in result} == {'x': 30, 'y': 7}


@pytest.mark.parametrize('connector', ('compare with', 'compare to', 'compared with', 'compared to'))
def test_explicit_period_connectors_preserve_order_and_figures(monkeypatch, connector):
    eng, vocab = declared(monkeypatch)
    text = f'Metric for June 2025 {connector} May 2025 in dollars and in percent'
    plan, compiled = interpreted(eng, vocab, text)
    comparison, = plan.comparisons
    assert comparison.kind == 'explicit_period'
    assert plan.time.period.value == '2025-06'
    assert comparison.other.period.value == '2025-05'
    result, = rows(compiled, [(dt.date(2025, month, 10), value, 'A', 1, 'x') for month, value in ((5, 25), (6, 34))])
    derived = {node.kind: node.id for node in plan.derived}
    assert result[derived['difference']] == 9
    assert result[derived['percent_change']] == pytest.approx(.36)


def test_a_named_context_disambiguates_one_predicate_across_physical_facts(monkeypatch):
    _, vocab = filter_declarations(monkeypatch, [
        dict(kind='predicate', params=dict(column=column, phrases=['credits'], op='eq', value='A'))
        for column in ('u.events.category', 'u.archive.category')
    ])
    text = 'Archived Metric for June 2025 for credits'
    predicate, = [piece for piece in ledger(text, 'turn', vocab) if piece.kind == 'predicate']
    assert set(predicate.candidates) == {'u.events.category', 'u.archive.category'}
    proposed = model(measures=[dict(kind='measure', id='m', measure=name('u.archived_metric'),
                     context=name('archived'), mode='current', filters=[])],
                     time=dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('cal'))),
                     filters=[dict(kind='compare', id='credits', left=dict(kind='column',
                         name=name('u.archive.category'), path=[]), op='eq',
                         value=dict(kind='text', value='A'), evidence=[])])
    proposed = ground_plan(proposed, text, 'turn', vocab)

    class Supplied:
        def __init__(self, plan):
            self.plan = plan

        def json(self, prompt):
            return Propose(kind='propose', base_turn=None, plan=self.plan).model_dump(mode='json', by_alias=True)

    reply = planmodel.read(Supplied(proposed), text, vocab)
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    result, = rows(compile_plan(checked), [(dt.date(2025, 6, 10), 34, 'A', 1, 'x')],
                   [(dt.date(2025, 6, 10), 25, 'A', 1, 'x'), (dt.date(2025, 6, 10), 99, 'B', 1, 'x')])
    assert result['m'] == 25
    for move_measure in (False, True):
        data = proposed.model_dump(mode='json', by_alias=True)
        data['filters'][0]['left']['name']['id'] = 'u.events.category'
        if move_measure:
            data['measures'][0]['measure']['id'] = 'u.metric'
            data['measures'][0]['context']['id'] = 'ctx'
        wrong = planmodel.read(Supplied(type(proposed).model_validate(data)), text, vocab)
        if wrong.kind == 'propose':
            assert not isinstance(check_plan(wrong.plan, vocab, text), CheckedPlan)
        else:
            assert wrong.kind in ('abstain', 'clarify', 'refuse')
