"""When the model sees two declared meanings, Ask asks the person to choose (#595), on a synthetic pack with stub models.

Ask answers clarify with the declared meanings as choices (each a declared id and its label), never a decline; picking
one asks again through the follow-up path and is checked as any plan; a choice that names an undeclared id is refused;
the rules route's clarify is unchanged."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plan import Choice, Clarification, HistoryEntry, Propose
from kge.semantics.planhistory import validate
from kge.services.ask.engine import planmodel
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, semantics, metric_vocabulary
from tests.unit.test_planner_jobs import setup

TOTAL = 'Give Total'
TIME = {'kind': 'all_available', 'calendar': 'cal'}
MEASURE = {'kind': 'measure', 'id': 'm1', 'measure': 'u.metric', 'context': 'ctx'}


class Scripted:
    """A stub model that gives its replies in order and keeps every prompt it was sent."""
    name = 'mock:scripted'

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def json(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)

    def errors(self, index):
        return json.loads(self.prompts[index].split('\n', 1)[1])['errors']


def two_meanings():
    """A pack where 'Total' names two declared measures' contexts on one table: the plain one and a second."""
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Context two', term='Second', grain='day')
    vocab = metric_vocabulary(sem, aliases=[('Total', 'ctx'), ('Total', 'ctx2')])
    assert set(vocab.phrases['total']) == {'ctx', 'ctx2'}
    return vocab


@pytest.fixture
def warehouses(monkeypatch, tmp_path):
    fake, w = setup(monkeypatch, tmp_path, False)
    yield w
    fake.con.close()


def asked(planner, vocab, question, llm, rules, turn='turn', history=()):
    def read(corrections):
        return planmodel.read_on(llm, question, vocab, history, turn, corrections=corrections)
    return planner.answer_model(question, read, rules, turn, history)


def no_floor(question, vocabulary):
    return {'kind': 'abstain', 'spans': [], 'missing': ['The rules could not read the question.']}


def floor_reply(question, vocabulary):
    from kge.semantics.plan import reply_adapter
    return reply_adapter.validate_python(no_floor(question, vocabulary))


def clarify(vocab, *candidates, text=TOTAL):
    span = next(p.span for p in ledger(text, 'turn', vocab) if p.text == 'Total')
    return {'reply': {'kind': 'clarify', 'spans': [span.model_dump()], 'candidates': [name(c) for c in candidates]}, 'readings': []}


def test_a_model_clarify_between_two_declared_meanings_is_clarify_with_both_choices_never_a_decline(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    result = asked(planner, vocab, TOTAL, Scripted(clarify(vocab, 'ctx', 'ctx2')), floor_reply)
    assert result['kind'] == 'clarify', result
    assert result['choices'] == [{'id': 'ctx', 'label': vocab.entries['ctx'].label},
                                 {'id': 'ctx2', 'label': vocab.entries['ctx2'].label}]
    assert result['model_refusal']['kind'] == 'clarify' and result['model_refusal']['fallback'] is None
    assert not any('can be read as' in m for m in result.get('missing', ()))


def test_the_contract_clarify_is_clarify_too_and_names_only_ids_it_was_shown(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    reply = {'decision': 'clarify', 'clarify': {'phrase': 'Total', 'candidates': ['ctx', 'ctx2']}}
    result = asked(planner, vocab, TOTAL, Scripted(reply), floor_reply)
    assert result['kind'] == 'clarify' and {c['id'] for c in result['choices']} == {'ctx', 'ctx2'}


def test_picking_a_choice_asks_again_through_the_follow_up_path_and_gives_a_checked_answer(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    first = asked(planner, vocab, TOTAL, Scripted(clarify(vocab, 'ctx', 'ctx2')), floor_reply)
    span = next(p.span for p in ledger(TOTAL, 'turn', vocab) if p.text == 'Total')
    choices = tuple(Choice.model_validate(c) for c in first['choices'])
    entry = HistoryEntry(turn_id='turn', question=TOTAL, plan=None, binding=vocab.binding,
                         clarification=Clarification(spans=(span,), slot='meaning', choices=choices))
    picked = TOTAL.replace('Total', vocab.entries['ctx2'].label)
    assert validate((entry,), vocab, picked, 'next', words=False) is None
    plan = model(measures=[{'kind': 'measure', 'id': 'm', 'measure': name('u.metric'), 'context': name('ctx2'),
                            'mode': 'current', 'filters': []}]).model_dump(mode='json', by_alias=True)
    llm = Scripted({'reply': {'kind': 'propose', 'base_turn': 'turn', 'plan': plan}, 'readings': []})
    result = asked(planner, vocab, picked, llm, floor_reply, 'next', (entry,))
    assert result['kind'] == 'result' and 'model_refusal' not in result, result
    assert result['guard']['passed'] and result['plan']['measures'][0]['context']['id'] == 'ctx2'


@pytest.mark.parametrize('question', ['Give Total we booked yesterdayish', 'Sparkly Total please'])
def test_a_model_clarify_on_a_question_with_unknown_words_can_be_picked_and_answers(warehouses, question):
    """The model route is reached mostly where the question holds a word the vocabulary does not list: the choice the
    clarification offers is still one the follow-up accepts, since only its own phrase must be read."""
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    first = asked(planner, vocab, question, Scripted(clarify(vocab, 'ctx', 'ctx2', text=question)), floor_reply)
    assert first['kind'] == 'clarify' and {c['id'] for c in first['choices']} == {'ctx', 'ctx2'}, first
    span = next(p.span for p in ledger(question, 'turn', vocab) if p.text == 'Total')
    entry = HistoryEntry(turn_id='turn', question=question, plan=None, binding=vocab.binding, clarification=Clarification(
        spans=(span,), slot='meaning', choices=tuple(Choice.model_validate(c) for c in first['choices'])))
    picked = question.replace('Total', vocab.entries['ctx'].label)
    assert validate((entry,), vocab, picked, 'next', words=False) is None
    llm = Scripted({'reply': {'kind': 'propose', 'base_turn': 'turn', 'plan': model().model_dump(mode='json', by_alias=True)}, 'readings': []})
    result = asked(planner, vocab, picked, llm, floor_reply, 'next', (entry,))
    assert result['kind'] == 'result' and 'model_refusal' not in result, result
    assert validate((entry,), vocab, 'Something else entirely', 'next', words=False).missing == (
        'Conversation alternatives could not be validated.',), 'its unread words reach no model unless this is its pick'
    later = HistoryEntry(turn_id='later', question=picked, plan=model(), clarification=None, binding=vocab.binding)
    assert validate((entry, later), vocab, 'Give Total again', 'third', words=False) is not None, 'only the last turn'
    unread = HistoryEntry(turn_id='turn', question='Give yesterdayish', plan=None, binding=vocab.binding, clarification=Clarification(
        spans=(next(p.span for p in ledger('Give yesterdayish', 'turn', vocab) if p.kind == 'unknown'),), slot='meaning',
        choices=(Choice(id='ctx', label=vocab.entries['ctx'].label),)))
    assert validate((unread,), vocab, 'Give ' + vocab.entries['ctx'].label, 'next', words=False).missing == (
        'Conversation alternatives could not be validated.',), 'the clarified phrase itself must still be read'


def test_a_model_clarify_with_no_choices_is_not_offered_as_one(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    prepare = planner.prepare

    def model_clarifies_with_no_choices(question, proposal, *args, **kwargs):
        if callable(proposal):
            return prepare(question, proposal, *args, **kwargs)
        return {'kind': 'clarify', 'missing': ['Group the filters.'], 'choices': []}
    planner.prepare = model_clarifies_with_no_choices
    result = asked(planner, vocab, TOTAL, Scripted(clarify(vocab, 'ctx', 'ctx2')), floor_reply)
    assert result['kind'] == 'abstain' and result['model_refusal']['kind'] == 'clarify'


def test_a_choice_that_names_an_undeclared_id_is_refused(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    result = asked(planner, vocab, TOTAL, Scripted(clarify(vocab, 'ctx', 'nowhere')), floor_reply)
    assert result['kind'] == 'abstain' and result['choices'] == [], result
    assert result['model_refusal']['kind'] == 'abstain'
    assert result['model_refusal']['missing'] == ['A proposed alternative is unavailable.']
    span = next(p.span for p in ledger(TOTAL, 'turn', vocab) if p.text == 'Total')
    forged = HistoryEntry(turn_id='turn', question=TOTAL, plan=None, binding=vocab.binding, clarification=Clarification(
        spans=(span,), slot='meaning', choices=(Choice(id='ctx', label=vocab.entries['ctx'].label), Choice(id='nowhere', label='Nowhere'))))
    assert validate((forged,), vocab, 'Give Nowhere', 'next', words=False).missing == ('Conversation alternatives are unavailable.',)


def test_the_rules_routes_clarify_is_unchanged(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))

    def rules(question, vocabulary):
        return Propose(kind='propose', base_turn=None, plan=grounded(model(), question, vocab))
    result = planner.answer(TOTAL, rules)
    assert result['kind'] == 'clarify' and {c['id'] for c in result['choices']} == {'ctx', 'ctx2'}
    assert 'model_refusal' not in result


def test_a_rules_floors_clarify_on_the_model_route_is_clarify_with_its_choices(warehouses):
    vocab = two_meanings()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))

    def rules(question, vocabulary):
        return Propose(kind='propose', base_turn=None, plan=grounded(model(), question, vocab))
    result = asked(planner, vocab, TOTAL, Scripted({'kind': 'nonsense'}, {'kind': 'nonsense'}, {'kind': 'nonsense'}), rules)
    assert result['kind'] == 'clarify' and {c['id'] for c in result['choices']} == {'ctx', 'ctx2'}
