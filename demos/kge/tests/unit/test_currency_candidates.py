"""Currency and period constraints bound ambiguity before any warehouse can open."""
from dataclasses import replace

import pytest

from kge.semantics.coverage import currency_candidates, ledger, period_candidates
from kge.semantics.plan import Binding, Clarify, HistoryEntry, Name, Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, semantics


class NoWarehouse:
    def __getattr__(self, operation):
        pytest.fail(f'an incomplete currency interpretation requested warehouse operation {operation}')


def revenues(codes=('USD', 'EUR'), *, declared=False, bound=True):
    sem = semantics()
    sem.contexts = {}
    sem.d.PERIODS = sem.d.PERIODS if bound else {}
    specs, aliases = [], []
    for index, code in enumerate(codes):
        column = 'revenue_' + code.lower() + '_' + str(index)
        identifier = ('context:' if declared else 'schema:') + 'u.events.' + column + ':sum'
        measure = identifier + '/measure'
        sem.tables['u.events'][column] = 'DECIMAL'
        sem.contexts[identifier] = replace(semantics().contexts['ctx'], id=identifier, label=column.replace('_', ' '),
            term=column.replace('_', ' '), measure=measure, expression='sum(events.' + column + ')',
            values=('u.events.' + column,), filters=(), grain='day')
        specs.append(MeasureSpec(measure, code if declared else None, 'u.events', True))
        aliases.extend([('Revenue', identifier), (code, identifier)])
    vocabulary = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                                  ['u'], ['u'], specs, aliases=aliases)
    return vocabulary, tuple(sem.contexts)


def revenue_plan(vocabulary, context, question, *, period=False):
    selected = vocabulary.contexts[context]
    updates = {'measures': [{'kind': 'measure', 'id': 'm', 'measure': name(selected.measure),
                            'context': name(context), 'mode': 'current', 'filters': []}]}
    if period:
        updates['time'] = {'kind': 'single', 'period': {'kind': 'month', 'value': '2025-04', 'calendar': name('cal')}}
    return grounded(model(**updates), question, vocabulary)


def clarify(vocabulary, question, candidates):
    spans = tuple(piece.span for piece in ledger(question, 'turn', vocabulary) if piece.text == 'Revenue')
    return Clarify(kind='clarify', spans=spans, candidates=tuple(Name(id=identifier, evidence=spans) for identifier in candidates))


def coordinator(vocabulary):
    return Planner(vocabulary, NoWarehouse(), Hidden([], set(), []))


@pytest.mark.parametrize('wording', ['USD', 'usd', 'US dollars', '$'])
@pytest.mark.parametrize('declared', [False, True], ids=['schema', 'declared'])
def test_a_named_currency_removes_other_currency_meanings_without_changing_metadata(wording, declared):
    vocabulary, (dollars, euros) = revenues(declared=declared)
    before = vocabulary.public()
    question = 'Revenue in ' + wording
    revenue, = (piece for piece in ledger(question, 'turn', vocabulary) if piece.text == 'Revenue')
    assert revenue.candidates == (dollars,)
    reply = clarify(vocabulary, question, (dollars, euros))
    result = coordinator(vocabulary).prepare(question, reply)
    assert result['kind'] == 'clarify'
    assert [choice['id'] for choice in result['choices']] == [dollars]
    assert vocabulary.public() == before
    assert vocabulary.measures[vocabulary.contexts[dollars].measure].currency == ('USD' if declared else None)


def test_the_schema_period_refusal_survives_an_other_currency_alias():
    vocabulary, (dollars, _) = revenues(bound=False)
    question = 'Revenue in USD for April 2025'
    proposal = Propose(kind='propose', base_turn=None, plan=revenue_plan(vocabulary, dollars, question, period=True))
    result = coordinator(vocabulary).prepare(question, proposal)
    assert result['kind'] == 'abstain'
    assert result['missing'] == ['missing compatible period binding']
    assert result['choices'] == []


def test_a_wrong_currency_proposal_cannot_drop_the_currency_obligation():
    vocabulary, (_, euros) = revenues()
    question = 'Revenue in USD'
    plan = revenue_plan(vocabulary, euros, question)
    result = check_plan(plan, vocabulary, question)
    assert not isinstance(result, CheckedPlan)
    assert result.kind == 'abstain'
    assert result.missing


def test_no_currency_keeps_every_meaning_and_same_currency_meanings_still_clarify():
    vocabulary, candidates = revenues(('USD', 'USD'))
    question = 'Revenue'
    piece, = ledger(question, 'turn', vocabulary)
    assert piece.candidates == tuple(sorted(candidates))
    assert currency_candidates(piece.candidates, vocabulary, set()) == piece.candidates
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, candidates))
    assert result['kind'] == 'clarify'
    assert {choice['id'] for choice in result['choices']} == set(candidates)
    dollars = coordinator(vocabulary).prepare('Revenue in USD', clarify(vocabulary, 'Revenue in USD', candidates))
    assert dollars['kind'] == 'clarify'
    assert {choice['id'] for choice in dollars['choices']} == set(candidates)


def test_an_all_wrong_currency_clarification_cannot_restore_the_original_candidates():
    vocabulary, (euros,) = revenues(('EUR',))
    question = 'Revenue in USD'
    piece, = (piece for piece in ledger(question, 'turn', vocabulary) if piece.text == 'Revenue')
    assert piece.candidates == ()
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, (euros,)))
    assert result['kind'] == 'abstain'
    assert result['choices'] == []


def test_declared_currency_wins_over_a_conflicting_physical_column_suffix():
    vocabulary, (euros,) = revenues(('EUR',), declared=True)
    measure = vocabulary.contexts[euros].measure
    vocabulary = replace(vocabulary, measures={**vocabulary.measures, measure: replace(vocabulary.measures[measure], currency='USD')})
    assert currency_candidates((euros, measure), vocabulary, {'USD'}) == (euros, measure)
    assert currency_candidates((euros, measure), vocabulary, {'EUR'}) == ()


def test_currency_narrowing_retains_nonmonetary_names():
    vocabulary, (dollars, euros) = revenues()
    identifiers = ('u', 'entity', 'u.events.entity_id', dollars, euros)
    assert currency_candidates(identifiers, vocabulary, {'USD'}) == identifiers[:-1]


def test_unavailable_periods_are_not_clarification_choices():
    vocabulary, candidates = revenues(('USD', 'USD'), bound=False)
    question = 'Revenue in USD for April 2025'
    assert period_candidates(candidates, vocabulary, True) == ()
    assert period_candidates(candidates, vocabulary, False) == candidates
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, candidates))
    assert result['kind'] == 'abstain'
    assert result['missing'] == ['missing compatible period binding']
    assert result['choices'] == []


def test_a_valid_period_alternative_does_not_resurrect_an_unbound_one():
    vocabulary, (valid, unbound) = revenues(('USD', 'USD'))
    contexts = dict(vocabulary.contexts)
    contexts[unbound] = replace(contexts[unbound], fact='u.archive', grain='day')
    vocabulary = replace(vocabulary, contexts=contexts)
    question = 'Revenue in USD for April 2025'
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, (valid, unbound)))
    assert result['kind'] == 'clarify'
    assert [choice['id'] for choice in result['choices']] == [valid]


def test_history_cannot_reoffer_a_meaning_absent_from_its_selected_phrase():
    vocabulary, (primary, alternate) = revenues(('USD', 'USD'))
    original = 'Revenue'
    question = 'Primary revenue'
    vocabulary = replace(vocabulary, entries={**vocabulary.entries,
        primary: replace(vocabulary.entries[primary], label=question),
        alternate: replace(vocabulary.entries[alternate], label='Alternate revenue')})
    history = (HistoryEntry.model_validate({'turn_id': 'previous', 'question': original, 'plan': None,
        'clarification': {'spans': [{'turn': 'previous', 'start': 0, 'end': len(original)}], 'slot': 'meaning',
                          'choices': [{'id': primary, 'label': question}, {'id': alternate, 'label': 'Alternate revenue'}]},
        'binding': vocabulary.binding.model_dump()}),)
    reply = Clarify.model_validate({'kind': 'clarify', 'spans': [{'turn': 'turn', 'start': 0, 'end': len(question)}],
                                  'candidates': [name(primary), name(alternate)]})
    result = coordinator(vocabulary).prepare(question, reply, history=history)
    assert result['kind'] == 'abstain'
    assert result['choices'] == []


def test_clarification_cannot_offer_a_context_from_another_arm():
    vocabulary, (dollars, _) = revenues()
    question = 'Revenue in USD'
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, (dollars, 'other-arm-revenue')))
    assert result['kind'] == 'abstain'
    assert result['missing'] == ['A proposed alternative is unavailable.']
    assert result['choices'] == []


@pytest.mark.parametrize('required', [False, True], ids=['unbounded', 'period-required'])
@pytest.mark.parametrize('kind', ['context', 'measure'])
def test_clarification_requires_an_active_context_even_when_the_entry_remains_readable(required, kind):
    vocabulary, (active, removed) = revenues(('USD', 'USD'))
    alternative = removed if kind == 'context' else vocabulary.contexts[removed].measure
    vocabulary = replace(vocabulary, contexts={active: vocabulary.contexts[active]})
    assert alternative in vocabulary.entries
    assert period_candidates((active, alternative), vocabulary, required) == (active,)
    question = 'Revenue for April 2025' if required else 'Revenue'
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, (active, alternative)))
    assert result['kind'] == 'clarify'
    assert [choice['id'] for choice in result['choices']] == [active]
    alone = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, (alternative,)))
    assert alone['kind'] == 'abstain'
    assert alone['choices'] == []


@pytest.mark.parametrize('one_bound', [False, True], ids=['no-period-binding', 'one-period-binding'])
def test_a_time_breakdown_requires_bound_clarification_choices_without_an_explicit_date(one_bound):
    vocabulary, (first, second) = revenues(('USD', 'USD'), bound=one_bound)
    if one_bound:
        vocabulary = replace(vocabulary, contexts={**vocabulary.contexts,
            second: replace(vocabulary.contexts[second], fact='u.archive', grain='day')})
    question = 'Revenue by month'
    result = coordinator(vocabulary).prepare(question, clarify(vocabulary, question, (first, second)))
    if one_bound:
        assert result['kind'] == 'clarify'
        assert [choice['id'] for choice in result['choices']] == [first]
    else:
        assert result['kind'] == 'abstain'
        assert result['missing'] == ['missing compatible period binding']
        assert result['choices'] == []
