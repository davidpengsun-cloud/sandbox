from decimal import Decimal

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine.planpublic import declared_measure_labels, release_decline, render
from kge.services.ask.engine.planner import scalar
from kge.services.ask.engine.question import Request, restricted_named
from tests.plan_fixtures import grounded, model, semantics, vocabulary
from tests.unit.test_plan_relational_matrix import replace_context


@pytest.mark.parametrize('prefix', ['Now just', 'now only', 'just'])
def test_followup_narrowing_words_add_no_lexical_obligation(prefix):
    v = vocabulary()
    text = prefix + ' Metric'
    assert [p.text for p in ledger(text, 'turn', v)] == ['Metric']
    assert isinstance(check_plan(grounded(model(), text, v), v, text), CheckedPlan)


def test_shared_predicate_phrase_keeps_candidate_columns_without_dropping_other_conditions():
    sem = semantics()
    rules = [{'kind': 'predicate', 'params': dict(phrases=['received'], column=column, op='eq', value='x')}
             for column in ('u.events.source', 'u.events.category')]
    sem.d.VOCABULARY = rules
    sem.d.FILTERS, _, _, sem.d.FILTER_COLUMNS = compiled(rules)
    v = vocabulary(sem)
    p, = ledger('received', 'turn', v)
    assert p.candidates == ('u.events.source', 'u.events.category')
    assert p.value == 'x' and p.operation == 'eq'


@pytest.mark.parametrize('text', ['customer names', 'client names', 'their names'])
def test_named_denial_phrases_apply_only_to_the_requested_unit(text):
    sem = semantics()
    sem.d.RESTRICTED_PHRASES = {'u.events.private': ['customer name', 'client names', 'their name']}
    assert restricted_named(text, sem.d, 'u') == ['u.events.private']
    assert restricted_named(text, sem.d, 'other') == []


def test_declared_measure_offers_infer_context_from_predicate_evidence():
    sem = semantics()
    sem.tables['other.rows'] = {'amount': 'DECIMAL'}
    sem.contexts['other'] = replace_context(sem.contexts['ctx'], id='other', term='Other measure', measure='other.amount', fact='other.rows')
    from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
    v = build_vocabulary(sem, vocabulary().binding, ['u', 'other'], ['u', 'other'],
        [MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('other.amount', 'USD', 'entities', True)])
    assert declared_measure_labels(Request('What total assets by category?', terms_found=[]), v) == ['Metric']


@pytest.mark.parametrize('reason', ['July 2027 is not booked in the release calendar',
                                   'July 2027 is not yet released: its figures are released on 2027-08-10'])
def test_release_offer_ends_with_the_question_after_its_source(reason):
    reason += '; The latest released month is June 2027. Would you like that month instead?'
    assert release_decline(reason, ['u.calendar']) == ('July 2027 is not released yet. The latest released month is June 2027. '
                                                       'Would you like that month instead?')


def test_cross_unit_alias_keeps_its_written_source_as_a_grounded_alternative():
    from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
    sem = semantics()
    sem.tables['other.rows'] = {'amount': 'DECIMAL'}
    v = build_vocabulary(sem, vocabulary().binding, ['u', 'other'], ['u', 'other'],
                         [MeasureSpec('u.metric', 'USD', 'entities', True)], aliases=[('other metric', 'ctx')])
    assert {e.id for e in v.candidates('other metric')} == {'ctx', 'other'}
    assert v.entries['ctx'].label == 'Context one'
    assert {e.id for e in v.candidates('other')} == {'other'}


CASES = [
    ('12790595.590000', None, 'USD', 'USD 12790595.590000'),
    ('12790595.00', None, 'USD', 'USD 12790595.00'),
    ('95', None, 'USD', 'USD 95'),
    ('-12790595.590000', None, 'USD', 'USD -12790595.590000'),
    ('0.125000', 'percent', None, '12.50%'),
    ('-2.5000', 'ratio', None, '-2.50 : 1'),
]


@pytest.mark.parametrize('raw,fmt,currency,expected', [
    ('12790595.590000', None, 'USD', 'USD 12,790,595.59'),
    ('12790595.00', None, 'USD', 'USD 12,790,595'),
    ('-12790595.590000', None, 'USD', 'USD -12,790,595.59'),
    ('-12790595.00', None, 'USD', 'USD -12,790,595'),
    ('0.125000', 'percent', None, '12.50%'),
    ('-2.5000', 'ratio', None, '-2.50 : 1'),
])
def test_labelled_breakdown_matches_sentence_format(raw, fmt, currency, expected):
    cell = {'value': scalar(Decimal(raw)), 'null_reasons': []}
    column = {'id': 'amount', 'kind': 'measure', 'format': fmt, 'currency': currency}
    row = {'dimensions': [{'kind': 'text', 'value': 'BR'}], 'dimension_labels': ['Branch'], 'cells': [cell]}
    result = {'columns': [{'id': 'division', 'kind': 'dimension'}, column], 'rows': [row]}
    assert render(result, labelled_breakdown=True) == 'BR · Branch: amount: ' + expected


@pytest.mark.parametrize('raw,fmt,currency,expected', CASES)
def test_unlabelled_render_matches_develop(raw, fmt, currency, expected):
    cell = {'value': scalar(Decimal(raw)), 'null_reasons': []}
    column = {'id': 'amount', 'kind': 'measure', 'format': fmt, 'currency': currency}
    row = {'dimensions': [{'kind': 'text', 'value': 'BR'}], 'cells': [cell]}
    result = {'columns': [{'id': 'division', 'kind': 'dimension'}, column], 'rows': [row]}
    assert render(result) == 'BR: amount: ' + expected
