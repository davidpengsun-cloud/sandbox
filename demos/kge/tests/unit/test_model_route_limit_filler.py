"""The model route's limit and filler corrections (#593), on synthetic plans with a stub vocabulary and stub models.

A limit no word of the question asks for goes back to the model as a correction: a plural ranking ("rank the ...")
keeps every group, while "top 3" and a singular "which ... had the highest" keep their limits; a rules plan is not read
by that check. A reading refused for an invented id is corrected naming the phrase and offering filler; a model that
then reads it as filler answers, and one that leaves it unresolved still fails as unresolved (E5)."""
from __future__ import annotations

import pytest

from kge.semantics.plan import HistoryEntry, Propose
from kge.services.ask.engine import planmodel
from kge.services.ask.engine.modelcall import FILLER, correction_text
from kge.services.ask.engine.planner import Planner, unasked_limit
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, metric_vocabulary
from tests.unit.test_model_route_choices import MEASURE, TIME, Scripted, asked, floor_reply
from tests.unit.test_planner_jobs import setup


@pytest.fixture
def warehouses(monkeypatch, tmp_path):
    fake, w = setup(monkeypatch, tmp_path, False)
    yield w
    fake.con.close()


@pytest.fixture
def categories(monkeypatch, tmp_path):
    """The fixture's rows (category A, 15 in all) and two categories more: B, 20, and C, 3."""
    from tests import bigquery_fake
    fake, w = setup(monkeypatch, tmp_path, False)
    target = bigquery_fake.PROJECT + '.' + w.dataset_id
    for day, value, category in (('2025-05-10', 20, 'B'), ('2025-05-11', 3, 'C')):
        fake.run(None, f"INSERT `{target}.u_events` VALUES (DATE '{day}', {value}, '{category}', 2, 'x', NULL)")
    fake.requests.clear()
    yield w
    fake.con.close()


def category_breakdown():
    return {'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}


def ranked(limit):
    return model(dimensions=[category_breakdown()], limit=limit,
                 order=[{'output': 'm', 'direction': 'desc', 'nulls': 'last', 'evidence': []}])


def proposal(plan):
    return {'reply': {'kind': 'propose', 'base_turn': None, 'plan': plan.model_dump(mode='json', by_alias=True)}, 'readings': []}


@pytest.mark.parametrize('question', [
    'Rank the categories by metric', 'Rank the categories by metric from highest to lowest',
    'Rank the categories by highest metric', 'Categories ranked by metric', 'Metric by category, largest first',
    'Ranking of categories by metric', 'Which categories, highest to lowest', 'What is the ranking of categories by metric',
    'Which categories ranked by metric', 'What is the rank of each category by metric',
    'Rank categories by metric for the last 3 months'])
def test_a_plural_ranking_does_not_own_a_limit_of_one(question):
    vocab = metric_vocabulary()
    problem = unasked_limit(question, Propose(kind='propose', base_turn=None, plan=ranked(1)), 'turn', vocab)
    assert problem is not None and problem.startswith('unrequested limit') and 'every group in rank order' in problem
    assert unasked_limit(question, Propose(kind='propose', base_turn=None, plan=ranked(None)), 'turn', vocab) is None


@pytest.mark.parametrize('question,limit', [
    ('Top 3 categories by metric', 3), ('The 5 largest categories by metric', 5), ('Top three categories by metric', 3),
    ('Which category had the highest metric', 1), ('Which category had the lowest metric', 1),
    ('Rank the top 3 categories by metric', 3), ('What is the top category by metric', 1), ('Top category by metric', 1),
    ('Which 3 categories had the highest metric', 3), ('Show 5 categories with the highest metric', 5),
    ('Show me the three biggest categories by metric', 3), ('First 3 categories by metric', 3),
    ('Which category ranked highest by metric', 1), ('Which category is ranked first by metric', 1),
    ('top-3 categories by metric', 3), ('Top 3 and bottom 3 categories', 3), ('Highest and lowest category by metric', 1),
    ('Highest category in each month by metric', 1)])
def test_top_n_and_a_singular_highest_keep_their_limits(question, limit):
    vocab = metric_vocabulary()
    assert unasked_limit(question, Propose(kind='propose', base_turn=None, plan=ranked(limit)), 'turn', vocab) is None


@pytest.mark.parametrize('question,limit', [('Rank the top 3 categories by metric', 5), ('Rank the categories by metric', 5),
                                            ('Rank categories by metric for the last 3 months', 3)])
def test_a_plural_ranking_owns_no_count_it_does_not_state(question, limit):
    vocab = metric_vocabulary()
    assert unasked_limit(question, Propose(kind='propose', base_turn=None, plan=ranked(limit)), 'turn', vocab) is not None


def test_a_follow_up_keeps_the_limit_of_the_turn_it_continues():
    vocab = metric_vocabulary()
    base = HistoryEntry(turn_id='turn', question='Top 3 categories by metric', plan=ranked(3), clarification=None, binding=vocab.binding)
    reply = Propose(kind='propose', base_turn='turn', plan=ranked(3))
    assert unasked_limit('Rank them for June', reply, 'next', vocab, (base,)) is None
    assert unasked_limit('Rank them for June', Propose(kind='propose', base_turn='turn', plan=ranked(1)), 'next', vocab, (base,))


def test_a_plural_ranking_with_a_limit_of_one_is_corrected_and_returns_every_group(categories):
    vocab = metric_vocabulary()
    planner = Planner(vocab, categories, Hidden([], set(), []))
    question = 'Rank the categories by metric'
    llm = Scripted(proposal(ranked(1)), proposal(ranked(None)))
    result = asked(planner, vocab, question, llm, floor_reply)
    assert result['kind'] == 'result' and 'model_refusal' not in result, result
    assert len(llm.prompts) == 2 and llm.errors(1)[0]['problem'].startswith('unrequested limit')
    assert result['plan']['limit'] is None
    groups = [(row['dimensions'][0]['value'], row['cells'][0]['value']['value'])
              for row in result['result']['rows'] if not row.get('total')]
    assert groups == [('B', '20.0'), ('A', '15.0'), ('C', '3.0')], 'every category, in rank order'


@pytest.mark.parametrize('question,limit,groups', [
    ('Top 2 categories by metric', 2, ['B', 'A']), ('Which category had the highest metric', 1, ['B'])])
def test_top_n_and_a_singular_highest_keep_their_limits_on_the_model_route_with_no_correction(categories, question, limit, groups):
    vocab = metric_vocabulary()
    planner = Planner(vocab, categories, Hidden([], set(), []))
    llm = Scripted(proposal(ranked(limit)))
    result = asked(planner, vocab, question, llm, floor_reply)
    assert result['kind'] == 'result' and len(llm.prompts) == 1 and result['plan']['limit'] == limit
    assert [row['dimensions'][0]['value'] for row in result['result']['rows'] if not row.get('total')] == groups


def test_a_rules_plan_is_not_read_by_the_model_routes_limit_check(warehouses, monkeypatch):
    import kge.services.ask.engine.planner as planner_module
    vocab = metric_vocabulary()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))

    def watched(*args, **kwargs):
        raise AssertionError("the rules route never asks the model route's limit check")
    monkeypatch.setattr(planner_module, 'unasked_limit', watched)

    def rules(question, vocabulary):
        return Propose(kind='propose', base_turn=None, plan=grounded(model(), question, vocab))
    assert planner.answer('Metric', rules)['kind'] == 'result'


def contract(readings=(), unread=()):
    return {'decision': 'propose', 'plan': {'measures': [MEASURE], 'time': TIME},
            'readings': list(readings), 'unread': list(unread)}


def test_a_refused_invented_id_is_corrected_naming_the_phrase_and_offering_filler():
    vocab = metric_vocabulary()
    reading = planmodel.read_on(Scripted(contract([{'phrase': 'we book', 'id': 'u.booked'}])), 'Metric we book', vocab)
    assert reading.reply is None
    correction = planmodel.correction(reading, (), vocab)
    [error] = correction.errors
    assert error.field == '/readings/0/id' and error.allowed[0] == FILLER
    assert "'we book'" in error.error and "'u.booked'" in error.error and 'not a declared id' in error.error
    assert FILLER in error.error
    text = correction_text(correction)
    assert "'we book'" in text and 'Allowed: filler' in text
    again = Scripted(contract([{'phrase': 'we book', 'id': FILLER}]))
    planmodel.read_on(again, 'Metric we book', vocab, corrections=(correction,))
    assert "'we book'" in again.errors(0)[0]['problem'], 'the single-text form names the phrase too'


def test_any_other_id_error_is_corrected_as_before():
    vocab = metric_vocabulary()
    bad = contract()
    bad['plan']['measures'] = [{**MEASURE, 'context': 'undeclared'}]
    reading = planmodel.read_on(Scripted(bad), 'Metric', vocab)
    assert reading.errors == ({'field': '/plan/measures/0/context', 'error': 'Value error, not a declared id', 'allowed': ['ctx']},)


@pytest.mark.parametrize('second,answers', [
    (contract([{'phrase': 'we book', 'id': FILLER}], ['we book']), True),
    (contract(unread=['we book']), False)])
def test_after_the_correction_filler_passes_e5_and_a_phrase_left_unresolved_still_fails(warehouses, second, answers):
    vocab = metric_vocabulary()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    question = 'Metric we book'
    llm = Scripted(contract([{'phrase': 'we book', 'id': 'u.booked'}], ['we book']), second, second)
    result = asked(planner, vocab, question, llm, floor_reply)
    assert len(llm.prompts) == (2 if answers else 3) and "'we book'" in llm.errors(1)[0]['problem']
    if answers:
        assert result['kind'] == 'result' and 'model_refusal' not in result, result
    else:
        assert (result['kind'], result['phase']) == ('abstain', 'readings') and "'we book'" in result['missing'][0]


def test_a_phrase_left_unread_is_corrected_naming_it_and_offering_filler_then_answers(warehouses):
    """#656: a reply that gives the phrase no reading at all goes back to the model once, naming the phrase and
    offering filler first, then the declared ids; a model that then reads it as filler answers."""
    vocab = metric_vocabulary()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    question = 'Metric we book'
    llm = Scripted(contract(unread=['we book']), contract([{'phrase': 'we book', 'id': FILLER}], ['we book']))
    result = asked(planner, vocab, question, llm, floor_reply)
    assert len(llm.prompts) == 2 and result['kind'] == 'result' and 'model_refusal' not in result, result
    [error] = llm.errors(1)
    assert "'we book'" in error['problem'] and FILLER in error['problem']


def test_the_unread_correction_names_each_phrase_with_filler_first_and_the_nearest_ids():
    vocab = metric_vocabulary()
    reading = planmodel.read_on(Scripted(contract(unread=['we book'])), 'Metric we book', vocab)
    correction = planmodel.unread_phrases(reading, ['we book', 'lately'])
    assert [e.field for e in correction.errors] == ['/readings', '/readings']
    assert ["'we book'" in correction.errors[0].error, "'lately'" in correction.errors[1].error] == [True, True]
    assert all(e.allowed[0] == FILLER and FILLER not in e.allowed[1:] and set(e.allowed[1:]) <= set(reading.ids)
               for e in correction.errors)
    assert correction.previous == reading.contract and not correction.truncated
    assert "Allowed: filler, " in correction_text(correction) and "u.metric" in correction_text(correction)


@pytest.mark.parametrize('corrections,prompts', [(0, 1), (1, 2), (2, 3)])
def test_a_phrase_still_unread_on_the_last_attempt_declines_with_no_floor(warehouses, monkeypatch, corrections, prompts):
    monkeypatch.setenv('ASK_MODEL_CORRECTIONS', str(corrections))
    vocab = metric_vocabulary()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    question = 'Metric we book'
    llm = Scripted(*[contract(unread=['we book'])] * 3)
    floor = []
    def rules(text, vocabulary):
        floor.append(text)
        return floor_reply(text, vocabulary)
    result = asked(planner, vocab, question, llm, rules)
    assert len(llm.prompts) == prompts and floor == []
    assert (result['kind'], result['phase'], result['model_refusal']['fallback']) == ('abstain', 'readings', None)
    assert "'we book'" in result['missing'][0]


def test_no_correction_for_a_phrase_left_unread_starts_without_the_time_for_one(warehouses, monkeypatch):
    import kge.services.ask.engine.planner as planner_module
    monkeypatch.setattr(planner_module, 'request_remaining', lambda: 0.0)
    vocab = metric_vocabulary()
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    llm = Scripted(*[contract(unread=['we book'])] * 3)
    result = asked(planner, vocab, 'Metric we book', llm, floor_reply)
    assert len(llm.prompts) == 1
    assert (result['kind'], result['phase'], result['model_refusal']['fallback']) == ('abstain', 'readings', None)
