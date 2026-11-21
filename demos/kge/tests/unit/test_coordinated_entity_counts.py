"""Structural lists give each declared entity its own count and complete key."""
import datetime as dt
import re
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import counted_names, ground_plan, ledger
from kge.semantics.plan import Binding
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics
from tests.unit.test_plan_compiler import connection


ROWS = [(dt.date(2025, 4, day), value, category, account, source)
        for day, value, category, account, source in (
            (1, 2, 'A', 1, 'x'), (2, 4, 'A', 1, 'x'), (3, 10, 'B', 2, 'x'),
            (4, 5, 'B', 1, 'y'), (5, 7, 'A', 1, 'x'))]


@pytest.fixture
def declared(monkeypatch):
    sem = semantics()
    sem.entities = {
        'account': {'label': 'Accounts', 'table': 'u.events', 'key': ['entity_id', 'source']},
        'transaction': {'label': 'Transactions', 'table': 'u.events', 'key': ['entity_id', 'source', 'occurred']},
        'customer': {'label': 'Customers', 'table': 'u.events', 'key': ['entity_id']},
    }
    sem.d.ENTITY_WORDS = {form: entity for entity in sem.entities for form in (entity, entity + 's')}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {'metric': 'Metric'}, {}
    sem.d.GROUP_BY = {'by account': 'u.events.entity_id', 'by transaction': 'u.events.occurred'}
    sem.d.INTENTS = {'count': re.compile(r'\b(?:counts?|number of)\b', re.I)}
    sem.d.VOCABULARY = [{'kind': 'count_metric', 'params': {
        'id': entity + '_count', 'label': entity.title() + ' cardinality',
        'entity': entity, 'context': 'ctx', 'filters': []}} for entity in sem.entities]
    specs = [MeasureSpec('u.metric', 'USD', 'entity', True)]
    sem.measure_specs = {spec.id: spec for spec in specs}
    sem.measure_declarations, sem.output_metadata = {'u.metric': {}}, {}
    vocabulary = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
        ['u'], ['u'], specs, aliases=[('Metric', 'ctx'), *sem.d.ENTITY_WORDS.items(), *sem.d.GROUP_BY.items()])
    engine = SimpleNamespace(semantics=sem, term_names=['Metric'], FISCAL_START=4, **vars(sem.d))
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return engine, vocabulary


@pytest.mark.parametrize('entities', ['account and transaction', 'account or transaction',
                                    'account & transaction', 'account, transaction'])
@pytest.mark.parametrize('template', ['Metric with {entities} counts', 'Metric with number of {entities}'])
def test_coordinated_declared_counts_keep_independent_composite_keys_and_figures(declared, entities, template):
    engine, vocabulary = declared
    text = template.format(entities=entities)
    pieces = ledger(text, 'turn', vocabulary)
    assert counted_names(pieces, text, vocabulary) == {'account', 'transaction'}
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary, PRESETS['graph'])
    assert proposal.kind == 'propose', proposal
    checked = check_plan(proposal.plan, vocabulary, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert compiled.answer is not None
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(ROWS) as con:
        cursor = con.execute(compiled.answer.sql)
        columns = [column[0] for column in cursor.description]
        values = dict(zip(columns, cursor.fetchone(), strict=True))
    counts = {measure.entity.id: values[measure.id] for measure in proposal.plan.measures if measure.kind == 'distinct_count'}
    assert counts == {'account': 3, 'transaction': 5}
    amount, = [measure for measure in proposal.plan.measures if measure.kind == 'measure']
    assert values[amount.id] == Decimal(28)


@pytest.mark.parametrize('text,expected', [
    ('Metric with account counts by transaction', {'account'}),
    ('Metric with transaction counts by account', {'transaction'}),
    ('Metric by account with transaction counts', {'transaction'}),
    ('Metric by account and transaction counts', set()),
    ('Metric with account in A and transaction counts', {'transaction'}),
    ('Metric with account and transaction', set()),
    ('which account has Metric with transaction counts', {'transaction'}),
])
def test_count_ownership_does_not_cross_a_grouping_selection_or_unrequested_entity(declared, text, expected):
    _, vocabulary = declared
    assert counted_names(ledger(text, 'turn', vocabulary), text, vocabulary) == expected


def test_three_nouns_and_independent_count_operators_each_keep_their_ownership(declared):
    _, vocabulary = declared
    for text in ('Metric with account, transaction and customer counts',
                 'Metric with account count and transaction count and customer count'):
        assert counted_names(ledger(text, 'turn', vocabulary), text, vocabulary) == {'account', 'transaction', 'customer'}


def test_coordinated_counts_refuse_another_declared_entity_or_an_incomplete_key(declared):
    engine, vocabulary = declared
    text = 'Metric with account and transaction counts'
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary, PRESETS['graph'])
    assert proposal.kind == 'propose', proposal
    transaction, = [measure for measure in proposal.plan.measures if measure.kind == 'distinct_count'
                    and measure.entity.id == 'transaction']
    wrong = transaction.model_copy(update={'entity': transaction.entity.model_copy(update={'id': 'customer'})})
    changed = proposal.plan.model_copy(update={'measures': tuple(wrong if measure.id == transaction.id else measure
        for measure in proposal.plan.measures)})
    decision = check_plan(ground_plan(changed, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'requested entity' in ' '.join(decision.missing)
    incomplete = replace(vocabulary, entities={**vocabulary.entities,
        'transaction': {**vocabulary.entities['transaction'], 'key': []}})
    decision = check_plan(proposal.plan, incomplete, text)
    assert not isinstance(decision, CheckedPlan)
    assert any('complete' in reason or 'declared' in reason for reason in decision.missing)


@pytest.mark.parametrize('text,connector', [
    ('Metric with account or transaction', 'or'),
    ('Metric with account & transaction', '&'),
    ('Metric by account or transaction counts', 'or'),
    ('Metric by account & transaction counts', '&'),
    ('Metric with account counts or customer 1', 'or'),
    ('Metric with account counts & customer 1', '&'),
])
def test_list_scaffolding_does_not_swallow_uncounted_entities_groupings_or_selectors(declared, text, connector):
    engine, vocabulary = declared
    pieces = ledger(text, 'turn', vocabulary)
    assert any(piece.text == connector for piece in pieces)
    proposal = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary, PRESETS['graph'])
    if proposal.kind == 'propose':
        assert not isinstance(check_plan(proposal.plan, vocabulary, text), CheckedPlan)
