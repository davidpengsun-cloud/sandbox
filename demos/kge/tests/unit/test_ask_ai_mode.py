"""AI-Generated mode (#464, J5; #561): where the rules do not fully match, the model reads the question into a typed plan
and its phrases' readings; the engine checks the plan alone (names, rights, check_plan without the question's words, the
compiler, the SQL gate), sends a check_plan or compiler error back as a correction up to twice (ASK_MODEL_CORRECTIONS),
so at most three calls, then takes the rules' best reading, then is not sure. The answer is labelled AI-Generated and
shows its readings; a decline carries no label."""
import json
from types import SimpleNamespace

import pytest

from kge.adapters.model import UnavailablePlan
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planmodel, planpublic
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import AI_GENERATED_LABEL, CERTIFIED_LABEL, Hidden, worded
from kge.services.ask.engine import wording
from tests.plan_fixtures import grounded, model, metric_vocabulary as vocabulary
from tests.unit.test_planner_jobs import setup


class Scripted:
    """A mock model that gives its replies in order and keeps every prompt it was sent."""
    name = 'mock:scripted'

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def json(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def enum_values(node):
    """Every value any enum in a schema lists."""
    if isinstance(node, dict):
        return set(node.get('enum') or ()) | {v for child in node.values() for v in enum_values(child)}
    if isinstance(node, list):
        return {v for child in node for v in enum_values(child)}
    return set()


def proposal(plan=None, readings=()):
    plan = plan if plan is not None else model().model_dump(mode='json', by_alias=True)
    return {'reply': {'kind': 'propose', 'base_turn': None, 'plan': plan}, 'readings': list(readings)}


def body(prompt):
    assert prompt.startswith(planmodel.ON_VERSION + '\n')
    return json.loads(prompt.split('\n', 1)[1])


def rules_of(vocab, text='Metric'):
    def rules(question, vocabulary):
        return Propose(kind='propose', base_turn=None, plan=grounded(model(), text, vocab))
    return rules


@pytest.fixture
def bench(monkeypatch, tmp_path):
    vocab = vocabulary()
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    yield vocab, Planner(vocab, warehouses, Hidden([], set(), []))
    fake.con.close()


def asked(planner, vocab, question, llm, rules):
    def read(corrections):
        return planmodel.read_on(llm, question, vocab, examples=({'question': 'Metric', 'plan': {}},), corrections=corrections)
    return planner.answer_model(question, read, rules)


def test_check_plan_without_words_checks_the_plan_alone_never_the_questions_words():
    vocab = vocabulary()
    bare = model()
    assert not isinstance(check_plan(bare, vocab, 'Metric we book sparkly'), CheckedPlan), 'the rules path keeps the word check'
    assert isinstance(check_plan(bare, vocab, 'Metric we book sparkly', words=False), CheckedPlan)
    absent = model(measures=[{'kind': 'measure', 'id': 'm', 'measure': {'id': 'u.metric', 'evidence': []},
                              'context': {'id': 'nowhere', 'evidence': []}, 'mode': 'current', 'filters': []}])
    refused = check_plan(absent, vocab, 'Metric', words=False)
    assert not isinstance(refused, CheckedPlan) and refused.missing == ('A requested name is absent from the readable vocabulary.',)


def test_the_model_plan_answers_ai_generated_with_one_call_and_its_readings(bench):
    vocab, planner = bench
    llm = Scripted(proposal(readings=[{'phrase': 'we book', 'id': 'u.metric'}, {'phrase': 'sparkly', 'id': 'filler'},
                                      {'phrase': 'made up', 'id': 'not-an-id'}]))
    result = asked(planner, vocab, 'Metric we book sparkly', llm, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result
    assert result['result']['rows'][0]['cells'][0]['value']['value'] == '15.0'
    assert result['readings'] == [{'phrase': 'we book', 'id': 'u.metric'}, {'phrase': 'sparkly', 'id': 'filler'}]
    assert len(llm.prompts) == 1
    assert vocab.entries['cal'].kind == 'calendar'
    notes = planpublic.reading_notes([*result['readings'], {'phrase': 'last quarter', 'id': 'cal'}], vocab, planner)
    assert notes == [f"read 'we book' as {vocab.entries['u.metric'].label}"]


def test_a_check_plan_error_goes_back_to_the_model_up_to_twice_then_the_rules_floor_answers(bench):
    vocab, planner = bench
    wrong = model(measures=[{'kind': 'measure', 'id': 'm', 'measure': {'id': 'u.metric', 'evidence': []},
                             'context': {'id': 'nowhere', 'evidence': []}, 'mode': 'current', 'filters': []}])
    plan = wrong.model_dump(mode='json', by_alias=True)
    llm = Scripted(proposal(plan), proposal())
    result = asked(planner, vocab, 'Metric', llm, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result, 'the repaired plan answers'
    assert len(llm.prompts) == 2
    assert body(llm.prompts[0])['errors'] == []
    assert body(llm.prompts[1])['errors'] == [{'problem': 'A requested name is absent from the readable vocabulary.'}]
    llm = Scripted(proposal(plan), proposal(plan), proposal(plan))
    result = asked(planner, vocab, 'Metric', llm, rules_of(vocab))
    assert len(llm.prompts) == 3, 'two corrections, never a fourth call'
    assert body(llm.prompts[2])['errors'] == [{'problem': 'A requested name is absent from the readable vocabulary.'}]
    assert result['kind'] == 'result' and result['model_refusal']['fallback'] == 'rules'


def test_a_reply_that_cannot_be_read_is_corrected_up_to_twice_and_then_the_rules_are_not_sure(bench):
    vocab, planner = bench
    llm = Scripted({'kind': 'nonsense'}, {'kind': 'nonsense'}, {'kind': 'nonsense'})
    result = asked(planner, vocab, 'Metric sparkly', llm, rules_of(vocab, 'Metric sparkly'))
    assert len(llm.prompts) == 3 and body(llm.prompts[1])['errors'] and body(llm.prompts[2])['errors']
    assert result['kind'] != 'result' and result['model_refusal']['fallback'] is None


def test_a_model_that_declines_to_plan_is_a_decline_with_no_repair_and_no_floor(bench):
    vocab, planner = bench
    llm = Scripted(UnavailablePlan('The model gave no plan.'))
    result = asked(planner, vocab, 'Metric', llm, rules_of(vocab))
    assert len(llm.prompts) == 1
    assert result['kind'] == 'abstain' and result['model_refusal']['fallback'] is None


def test_a_phrase_the_models_own_reading_marks_unresolved_is_not_sure_naming_it_with_no_floor(bench):
    vocab, planner = bench
    question = 'Metric plus sparkly'
    unresolved = {'id': 'o1', 'kind': 'name', 'spans': [{'start': 12, 'end': 19, 'turn': 'turn'}], 'targets': [], 'state': 'unresolved'}
    applied = {'id': 'o0', 'kind': 'name', 'spans': [{'start': 0, 'end': 6, 'turn': 'turn'}], 'targets': ['m'], 'state': 'applied'}
    plan = model(obligations=[applied, unresolved]).model_dump(mode='json', by_alias=True)
    llm = Scripted(proposal(plan))
    floor = []
    def rules(text, vocabulary):
        floor.append(text)
        return rules_of(vocab, question)(text, vocabulary)
    result = asked(planner, vocab, question, llm, rules)
    assert len(llm.prompts) == 1 and floor == []
    assert (result['kind'], result['phase'], result['model_refusal']['fallback']) == ('abstain', 'readings', None)
    assert "'sparkly'" in result['missing'][0] and 'Metric' not in result['missing'][0]
    assert 'readings' in planpublic.STATED_PHASES


def test_an_unresolved_obligation_the_models_own_readings_map_to_an_id_or_filler_still_answers(bench):
    vocab, planner = bench
    question = 'Metric we book'
    unresolved = {'id': 'o1', 'kind': 'name', 'spans': [{'start': 7, 'end': 14, 'turn': 'turn'}], 'targets': [], 'state': 'unresolved'}
    plan = model(obligations=[unresolved]).model_dump(mode='json', by_alias=True)
    llm = Scripted(proposal(plan, readings=[{'phrase': 'we book', 'id': 'u.metric'}]))
    result = asked(planner, vocab, question, llm, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result
    llm = Scripted(proposal(plan, readings=[{'phrase': 'we book', 'id': 'filler'}]))
    result = asked(planner, vocab, question, llm, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result, 'a phrase the model reads as filler is filler'
    llm = Scripted(proposal(plan, readings=[{'phrase': 'we', 'id': 'filler'}]))
    result = asked(planner, vocab, question, llm, rules_of(vocab))
    assert result['kind'] == 'abstain' and "'we book'" in result['missing'][0], 'filler covers only the phrase it names'


def test_a_model_plan_with_every_phrase_resolved_still_answers(bench):
    vocab, planner = bench
    applied = {'id': 'o0', 'kind': 'name', 'spans': [{'start': 0, 'end': 6, 'turn': 'turn'}], 'targets': ['m'], 'state': 'applied'}
    llm = Scripted(proposal(model(obligations=[applied]).model_dump(mode='json', by_alias=True)))
    result = asked(planner, vocab, 'Metric we book', llm, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result


def test_the_receipts_plan_keeps_declared_names_and_members_and_types_any_other_value():
    from kge.semantics.planvocabulary import Entry
    vocab = vocabulary(values=[Entry('one', 'value', 'First grouping', 'u', column='u.events.category', value='A')])
    def column(name):
        return {'kind': 'column', 'name': {'id': name, 'evidence': [{'start': 0, 'end': 6, 'turn': 'turn'}]}, 'path': []}
    plan = {'measures': [{'id': 'm', 'measure': {'id': 'u.metric', 'evidence': [{'start': 0, 'end': 6, 'turn': 'turn'}]}}],
            'filters': [{'kind': 'compare', 'id': 'f0', 'left': column('u.events.category'), 'op': 'eq',
                         'value': {'kind': 'text', 'value': 'A'}, 'evidence': []},
                        {'kind': 'in', 'id': 'f1', 'left': column('u.events.entity_id'),
                         'values': [{'kind': 'integer', 'value': 4711}, {'kind': 'text', 'value': 'Acme Holdings'}], 'evidence': []}],
            'obligations': [{'id': 'o0', 'spans': [{'start': 0, 'end': 6, 'turn': 'turn'}], 'state': 'applied'}]}
    kept = planpublic.receipt_plan(plan, vocab)
    assert 'obligations' not in kept and 'evidence' not in json.dumps(kept)
    assert kept['measures'][0]['measure'] == {'id': 'u.metric'}
    assert kept['filters'][0]['value'] == {'kind': 'text', 'value': 'A'}
    assert kept['filters'][1]['values'] == [{'kind': 'integer'}, {'kind': 'text'}]
    assert '4711' not in json.dumps(kept) and 'Acme' not in json.dumps(kept)


def test_the_receipts_plan_renames_the_node_ids_by_position_and_every_reference_to_them():
    from kge.semantics.plan import QueryPlan
    from tests.plan_fixtures import name, time_dimension
    vocab = vocabulary()
    above = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
             'op': 'gt', 'value': {'kind': 'integer', 'value': 6}, 'evidence': []}
    plain = model(filters=[above], dimensions=[time_dimension()], limit=1,
                  order=[{'output': 'm', 'direction': 'desc', 'nulls': 'last', 'evidence': []}])
    text = json.dumps(plain.model_dump(mode='json', by_alias=True))
    for old, new in (('"m"', '"customer_4711"'), ('"f"', '"acme_holdings_filter"'), ('"t"', '"acme_months"')):
        text = text.replace(old, new)
    named = QueryPlan.model_validate(json.loads(text))
    def kept(plan):
        return planpublic.receipt_plan(plan.model_dump(mode='json', by_alias=True), vocab, [n.id for n in plan.nodes()])
    assert kept(named) == kept(plain)
    assert not {'4711', 'customer', 'acme'} & set(json.dumps(kept(named)).replace('"', ' ').replace('_', ' ').split())
    assert kept(plain)['order'][0]['output'] == kept(plain)['measures'][0]['id'] != 'm'


@pytest.mark.parametrize('full,model_asked,follow_up,mode', [
    (True, False, False, 'certified'), (True, False, True, 'certified'), (True, True, False, 'certified'),
    (True, True, True, 'ai_generated'), (False, True, False, 'ai_generated'), (False, False, False, 'not_sure')])
def test_a_rules_floor_figure_served_by_the_model_path_is_ai_generated_never_not_sure(full, model_asked, follow_up, mode):
    assert planpublic.rules_floor_mode(full, model_asked, follow_up) == mode


def test_an_ambiguous_question_is_clarify_with_its_declared_choices_never_a_decline(bench, monkeypatch):
    vocab, planner = bench
    choices = [{'id': 'a', 'label': 'Alpha'}, {'id': 'b', 'label': 'Beta'}]
    monkeypatch.setattr(planner, 'answer', lambda *a, **k: {'kind': 'clarify', 'missing': ['Metric'], 'choices': choices})
    llm = Scripted(UnavailablePlan('no plan'))
    llm.replies = [{'kind': 'nonsense'}, {'kind': 'nonsense'}, {'kind': 'nonsense'}]
    result = asked(planner, vocab, 'Metric', llm, rules_of(vocab))
    assert result['kind'] == 'clarify' and result['choices'] == choices, '#595: the person is asked to choose'
    assert not any('can be read as' in m for m in result['missing'])


def test_the_prompt_is_built_from_the_pack_with_ids_in_the_data_examples_and_the_v1_recording_key():
    vocab = vocabulary()
    llm = Scripted(proposal())
    examples = ({'question': 'What was Metric?', 'plan': {'schema': 'query-plan/v1'}},)
    reading = planmodel.read_on(llm, 'Metric by category', vocab, examples=examples)
    assert reading.reply.kind == 'propose' and reading.schema
    data = body(llm.prompts[0])
    assert data['examples'] == [dict(e) for e in examples]
    assert data['recording_key'] == planmodel.payload('Metric by category', vocab)['recording_key']
    assert data['obligations'] == json.loads(planmodel.encoded(planmodel.payload('Metric by category', vocab)['obligations']))
    ids = [n['id'] for n in data['vocabulary']['names']]
    assert 'VocabularyId' not in data['schema']['$defs'] and 'ReadingId' not in data['schema']['$defs']
    assert not enum_values(data['schema']) & set(ids)
    assert data['schema']['required'] == ['decision']
    full = planmodel.payload('Metric by category', vocab)['vocabulary']['names']
    columns = {n['id'] for n in full if vocab.entries[n['id']].kind == 'column'}
    shown = {i for i in ids if i in columns}
    assert shown < columns and all(planmodel.stems(vocab.entries[i].label) & {'metric', 'category'} for i in shown)


def test_a_name_or_member_written_as_words_is_bound_to_its_declared_code_by_the_rules_lookup():
    member = SimpleNamespace(id='v:retail', kind='value', column='u.events.category', value='S4100', label='Retail Investor')
    column = SimpleNamespace(id='u.events.category', kind='column', column='u.events.category', label='Category')
    entries = {e.id: e for e in (member, column)}
    vocab = SimpleNamespace(entries=entries, candidates=lambda words: (member,) if words == 'retail investors' else ())
    def filtered(value):
        return {'kind': 'compare', 'op': 'eq', 'left': {'kind': 'column', 'name': {'id': 'u.events.category', 'evidence': []}},
                'value': {'kind': 'text', 'value': value}}
    assert planmodel.bound_names(filtered('Retail Investor'), vocab)['value']['value'] == 'S4100'
    assert planmodel.bound_names(filtered('retail investors'), vocab)['value']['value'] == 'S4100'
    assert planmodel.bound_names(filtered('S4100'), vocab)['value']['value'] == 'S4100'
    assert planmodel.bound_names(filtered('Somebody else'), vocab)['value']['value'] == 'Somebody else'
    assert planmodel.bound_names({'id': 'Retail Investors', 'evidence': []}, SimpleNamespace(
        entries=entries, candidates=lambda words: (member,)))['id'] == 'v:retail'


@pytest.mark.parametrize('mode,behaviour,label', [('certified', 'figure', CERTIFIED_LABEL), ('ai_generated', 'figure', AI_GENERATED_LABEL),
                                                  ('not_sure', 'figure', ''), ('certified', 'abstain', ''), ('certified', 'refuse', ''),
                                                  ('not_sure', 'abstain', ''), ('ai_generated', 'clarify', '')])
def test_only_an_answer_carries_a_label_and_a_decline_begins_with_neither(monkeypatch, mode, behaviour, label):
    monkeypatch.setattr(wording, '_worded', lambda a, e: dict(a))
    text = worded({'answer_text': 'Metric: 15.', 'behaviour': behaviour, 'answer_mode': mode}, None)['answer_text']
    assert text == label + 'Metric: 15.'
    assert worded({'answer_text': text, 'behaviour': behaviour, 'answer_mode': mode}, None)['answer_text'] == text


def test_the_examples_come_through_the_pack_store_once_per_release_and_only_readable_ones_reach_the_prompt(monkeypatch):
    reads = []
    held = [{'question': 'Metric', 'plan': {'measures': [{'context': {'id': 'ctx', 'evidence': []}}]}},
            {'question': 'Other', 'plan': {'measures': [{'context': {'id': 'unreadable-ctx', 'evidence': []}}]}}]

    class Store:
        def read(self, name):
            reads.append(name)
            return json.dumps(held).encode()
    monkeypatch.setattr(planmodel.packstore, 'for_profile', lambda profile: Store())
    planmodel._examples.clear()
    engine = SimpleNamespace(profile=SimpleNamespace(client='c'), identity={'model_hash': 'h1'})
    assert planmodel.verified_examples(engine) == tuple(held)
    assert planmodel.verified_examples(engine) == tuple(held) and reads == [planmodel.EXAMPLES]
    vocab = vocabulary()
    llm = Scripted(proposal())
    planmodel.read_on(llm, 'Metric', vocab, examples=planmodel.verified_examples(engine))
    assert body(llm.prompts[0])['examples'] == [held[0]]
    planmodel._examples.clear()


def category_breakdown():
    return {'kind': 'column', 'id': 'd', 'bindings': [
        {'context': {'id': 'ctx', 'evidence': []}, 'column': {'id': 'u.events.category', 'evidence': []}, 'path': [], 'level': None}]}


def test_a_breakdown_the_models_plan_asks_for_is_never_dropped_by_the_rules_normalisation(bench):
    vocab, planner = bench
    plan = model(dimensions=[category_breakdown()]).model_dump(mode='json', by_alias=True)
    llm = Scripted(proposal(plan, readings=[{'phrase': 'stratified according to', 'id': 'filler'},
                                            {'phrase': 'category', 'id': 'u.events.category'}]))
    result = asked(planner, vocab, 'Metric stratified according to category', llm, rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result
    assert [d['id'] for d in result['plan']['dimensions']] == ['d']
    assert len([r for r in result['result']['rows'] if not r.get('total')]) == 1
    time = model(dimensions=[{'kind': 'time', 'id': 't', 'calendar': {'id': 'cal', 'evidence': []}, 'grain': 'month'}])
    reading = planmodel.read_on(Scripted(proposal(time.model_dump(mode='json', by_alias=True))), 'Metric monthwise', vocab)
    assert [d.id for d in reading.reply.plan.dimensions] == ['t']


def test_the_models_time_mode_and_layout_are_never_reset_by_the_rules_normalisation():
    vocab = vocabulary()
    plan = model().model_dump(mode='json', by_alias=True)
    plan['measures'][0]['mode'] = 'as_reported'
    plan['layout'] = 'separate'
    reading = planmodel.read_on(Scripted(proposal(plan)), 'Metric as first published, each on its own', vocab)
    assert reading.reply.kind == 'propose', reading
    assert [m.mode for m in reading.reply.plan.measures] == ['as_reported']
    assert reading.reply.plan.layout == 'separate'


@pytest.mark.parametrize('question', ['Metric excluding where value is above 6', 'Metric other than value is above 6'])
def test_the_models_comparison_is_never_rewritten_from_the_questions_words(bench, question):
    from tests.plan_fixtures import name
    vocab, planner = bench
    kept = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
            'op': 'le', 'value': {'kind': 'integer', 'value': 6}, 'evidence': []}
    plan = model(filters=[kept]).model_dump(mode='json', by_alias=True)
    result = asked(planner, vocab, question, Scripted(proposal(plan)), rules_of(vocab))
    assert result['kind'] == 'result' and 'model_refusal' not in result, result
    assert [(f['op'], f['value']['value']) for f in result['plan']['filters']] == [('le', 6)]
    assert [r['cells'][0]['value']['value'] for r in result['result']['rows'] if not r.get('total')] == ['5.0']


def test_the_models_grouping_column_is_never_rewritten_from_the_questions_words(monkeypatch, tmp_path):
    import datetime as dt
    from tests.plan_fixtures import ROWS, semantics
    sem = semantics()
    sem.d.GROUP_BY = {'category': 'u.events.category'}
    vocab = vocabulary(sem)
    monkeypatch.setattr('tests.unit.test_planner_jobs.ROWS', [*ROWS, (dt.date(2025, 5, 10), 20, 'A', 2, 'y')])
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    breakdown = category_breakdown()
    breakdown['bindings'][0]['column'] = {'id': 'u.events.source', 'evidence': []}
    plan = model(dimensions=[breakdown]).model_dump(mode='json', by_alias=True)
    try:
        result = asked(planner, vocab, 'Metric by source rather than by category', Scripted(proposal(plan)), rules_of(vocab))
    finally:
        fake.con.close()
    assert result['kind'] == 'result' and 'model_refusal' not in result, result
    assert [b['column']['id'] for d in result['plan']['dimensions'] for b in d['bindings']] == ['u.events.source']
    rows = [r for r in result['result']['rows'] if not r.get('total')]
    assert sorted((r['dimensions'][0]['value'], r['cells'][0]['value']['value']) for r in rows) == [('x', '15.0'), ('y', '20.0')]


def test_a_member_the_prompt_keeps_keeps_the_column_it_filters():
    from kge.semantics.planvocabulary import Entry
    vocab = vocabulary(values=[Entry('one', 'value', 'First grouping', 'u', column='u.events.category', value='A')])
    names = {n['id'] for n in planmodel.on_payload('Metric for First grouping', vocab)['vocabulary']['names']}
    assert {'one', 'u.events.category'} <= names
    assert 'u.events.entity_id' not in names
    enum = planmodel.on_payload('Metric for First grouping', vocab)['schema']['$defs']['Name']['properties']['id']['enum']
    assert 'u.events.category' in enum


def test_an_answer_the_model_read_in_novel_words_supports_the_next_turn_on_the_model_path(bench):
    from kge.semantics.plan import HistoryEntry
    vocab, planner = bench
    first = HistoryEntry(turn_id='t1', question='Metric we book sparkly', plan=model(), clarification=None, binding=vocab.binding)
    assert not isinstance(check_plan(model(), vocab, first.question, 't1'), CheckedPlan), 'the rules would not certify it'
    llm = Scripted(proposal())
    def read(corrections):
        return planmodel.read_on(llm, 'Metric again', vocab, (first,), 't2', corrections=corrections)
    result = planner.answer_model('Metric again', read, rules_of(vocab, 'Metric again'), 't2', (first,))
    assert result['kind'] == 'result' and 'model_refusal' not in result, result


def test_a_partitioned_ranking_is_checked_as_a_plan_without_the_questions_words():
    vocab = vocabulary()
    time = {'kind': 'time', 'id': 't', 'calendar': {'id': 'cal', 'evidence': []}, 'grain': 'month'}
    ranked = model(dimensions=[time, category_breakdown()],
                   order=[{'output': 'm', 'direction': 'desc', 'nulls': 'last', 'evidence': [], 'partition_by': ['t']}], limit=1)
    assert isinstance(check_plan(ranked, vocab, 'Metric categories ranked within each month', words=False), CheckedPlan)


def test_a_deletion_follow_up_is_checked_as_a_plan_with_its_deletion_evidence_in_the_follow_up():
    from kge.semantics.plan import HistoryEntry, QueryPlan
    from kge.semantics.planhistory import removed_nodes, validate
    from tests.plan_fixtures import time_dimension
    vocab = vocabulary()
    base = grounded(model(dimensions=[time_dimension()]), 'Metric by month', vocab, {'by': 't', 'month': 't'})
    data = base.model_dump(mode='json', by_alias=True)
    data['dimensions'] = []
    data['obligations'] = [dict(o, targets=[t for t in o['targets'] if t != 't']) for o in data['obligations']]
    data['obligations'] = [o for o in data['obligations'] if o['targets']]
    data['obligations'].append({'id': 'remove', 'kind': 'name', 'targets': ['t'], 'state': 'removed',
                                'spans': [{'turn': 'next', 'start': 0, 'end': len('just the total')}]})
    total = QueryPlan.model_validate(data)
    removed = removed_nodes(total, base)
    assert isinstance(check_plan(total, vocab, 'just the total', 'next', removed=removed, words=False), CheckedPlan)
    refused = check_plan(total, vocab, 'just the extra', 'next', removed=removed, words=False)
    assert not isinstance(refused, CheckedPlan) and refused.missing == ('Deletion evidence does not identify the removed nodes.',)
    history = (HistoryEntry(turn_id='turn', question='Metric by month', plan=base, clarification=None, binding=vocab.binding),
               HistoryEntry(turn_id='next', question='just the total', plan=total, clarification=None, binding=vocab.binding))
    assert validate(history, vocab, 'Metric again', 'third', words=False) is None


def test_a_second_unreadable_reply_on_the_cloud_path_falls_to_the_rules_floor_never_a_503(bench, monkeypatch):
    import kge.pipeline.settings as settings
    monkeypatch.setattr(settings, 'gcp_native', lambda: True)
    vocab, planner = bench
    llm = Scripted({'kind': 'nonsense'}, {'kind': 'nonsense'}, {'kind': 'nonsense'})
    result = asked(planner, vocab, 'Metric', llm, rules_of(vocab))
    assert len(llm.prompts) == 3
    assert result['kind'] == 'result' and result['model_refusal']['fallback'] == 'rules'


def declining_floor(question, vocabulary):
    from kge.semantics.plan import Abstain
    return Abstain(kind='abstain', spans=(), missing=('The rules have no reading.',))


def test_a_model_plan_naming_a_unit_the_asker_cannot_read_is_refused(bench):
    vocab, served = bench
    planner = Planner(vocab, served.warehouses, Hidden([], set(), []), readable_units={'elsewhere'})
    llm = Scripted(proposal())
    result = asked(planner, vocab, 'Metric', llm, declining_floor)
    assert result['kind'] != 'result' and 'sql' not in result and 'result' not in result
    assert result['model_refusal']['kind'] == 'refuse' and result['model_refusal']['fallback'] is None


@pytest.mark.parametrize('failed_at, problem', [
    ('static', 'column t.c is restricted personal data'),
    ('partition', 'The statement reads no partition the gate allows.')])
def test_a_model_plan_the_sql_gate_refuses_for_restricted_data_or_a_partition_is_refused_with_no_figure_or_correction(
        bench, monkeypatch, failed_at, problem):
    from kge.services.ask.engine import planner as planning
    vocab, planner = bench
    monkeypatch.setattr(planning, 'execute_on_runtime', lambda *a, **k: (
        {'failed_at': failed_at, 'problems': [problem]}, None, {}))
    llm = Scripted(proposal())
    result = asked(planner, vocab, 'Metric', llm, declining_floor)
    assert len(llm.prompts) == 1
    assert result['kind'] != 'result' and 'result' not in result
    assert result['model_refusal']['kind'] == 'refuse' and result['model_refusal']['guard']['failed_at'] == failed_at
    assert result['model_refusal']['fallback'] is None


def test_any_other_static_gate_failure_goes_back_to_the_model_with_the_gates_problem(bench, monkeypatch):
    from kge.services.ask.engine import planner as planning
    vocab, planner = bench
    monkeypatch.setattr(planning, 'execute_on_runtime', lambda *a, **k: (
        {'failed_at': 'static', 'problems': ['The statement reads a table outside the gate.']}, None, {}))
    llm = Scripted(proposal(), proposal(), proposal())
    result = asked(planner, vocab, 'Metric', llm, declining_floor)
    assert len(llm.prompts) == 3
    assert body(llm.prompts[1])['errors'] == [{'problem': 'The statement reads a table outside the gate.'}]
    assert result['kind'] != 'result' and result['model_refusal']['guard']['failed_at'] == 'static'


def test_a_missing_examples_file_is_read_again_on_the_next_question_never_pinned_for_the_release(monkeypatch):
    held, reads = [{'question': 'Metric', 'plan': {}}], []

    class Store:
        written = False

        def read(self, name):
            reads.append(name)
            if not Store.written:
                raise FileNotFoundError(name)
            return json.dumps(held).encode()
    monkeypatch.setattr(planmodel.packstore, 'for_profile', lambda profile: Store())
    planmodel._examples.clear()
    engine = SimpleNamespace(profile=SimpleNamespace(client='c'), identity={'model_hash': 'h1'})
    assert planmodel.verified_examples(engine) == ()
    Store.written = True
    assert planmodel.verified_examples(engine) == tuple(held)
    assert planmodel.verified_examples(engine) == tuple(held) and len(reads) == 2
    planmodel._examples.clear()


def member_vocabulary():
    """Five declared members named as the pilot's rewordings name them, codes as their labels; branch names a column,
    so it is a generic word that names no member alone, and Heartland region is a declared group of two codes."""
    from kge.semantics.planvocabulary import Entry, value_id
    from tests.plan_fixtures import semantics
    sem = semantics()
    rows = [('u.events.category', 'eq', ('S4100',), ('segment AFFLUENT', 'Retail Investor')),
            ('u.events.category', 'eq', ('S3200',), ('segment WLTH', 'International')),
            ('u.events.category', 'eq', ('CHI',), ('branch CHI',)),
            ('u.events.source', 'eq', ('S5100',), ('segment CORE', 'Investor Services')),
            ('u.events.source', 'eq', ('5H',), ('branch BOS', 'Fort Collins branch', 'branch Fort Collins')),
            ('u.events.source', 'in', ('CEO', 'NER'), ('Heartland region',))]
    sem.d.VOCABULARY = [{'kind': 'predicate', 'params': {'phrases': phrases, 'column': column, 'op': op,
                         **({'values': codes} if op == 'in' else {'value': codes[0]})}} for column, op, codes, phrases in rows]
    values = [Entry(value_id(column, code), 'value', code, 'u', column=column, value=code)
              for column, _, codes, _ in rows for code in codes]
    return vocabulary(sem, values=values, aliases=[('by branch', 'u.events.source')])


@pytest.mark.parametrize('question, column, op, codes', [
    ("How much of Retail Investor's Metric came in?", 'u.events.category', 'eq', ['S4100']),
    ('Metric for Retail Investors', 'u.events.category', 'eq', ['S4100']),
    ('Metric for CHI', 'u.events.category', 'eq', ['CHI']),
    ('Metric by month in the Heartland region', 'u.events.source', 'in', ['CEO', 'NER']),
    ('Metric for Heartland', 'u.events.source', 'in', ['CEO', 'NER']),
    ('Count the Metric in Fort Collins', 'u.events.source', 'eq', ['5H']),
    ('Metric for INTERNATIONAL', 'u.events.category', 'eq', ['S3200']),
    ('Metric of each investor services sub-segment', 'u.events.source', 'eq', ['S5100']),
])
def test_the_prompt_carries_every_declared_member_the_question_names_with_its_phrases_and_codes(question, column, op, codes):
    from kge.semantics.planvocabulary import value_id
    data = planmodel.on_payload(question, member_vocabulary())
    names = {n['id'] for n in data['vocabulary']['names']}
    enum = data['schema']['$defs']['Name']['properties']['id']['enum']
    assert all(value_id(column, code) in names and value_id(column, code) in enum for code in codes)
    assert column in names
    assert {'column': column, 'op': op, 'values': codes} in [
        {k: m[k] for k in ('column', 'op', 'values')} for m in data['vocabulary']['members']]


def test_a_generic_word_names_no_member_alone_and_the_declared_groups_are_always_there():
    data = planmodel.on_payload('Metric by month', member_vocabulary())
    assert {tuple(m['values']) for m in data['vocabulary']['members']} == {('CEO', 'NER')}
    data = planmodel.on_payload('Metric for each branch', member_vocabulary())
    names = {n['id'] for n in data['vocabulary']['names']}
    assert 'value:u.events.source:5H' in names
    assert not {'value:u.events.category:CHI', 'value:u.events.category:S4100'} & names


def test_a_dimension_the_question_names_brings_its_members_and_their_phrases():
    data = planmodel.on_payload('Metric by category', member_vocabulary())
    names = {n['id'] for n in data['vocabulary']['names']}
    assert {'value:u.events.category:S4100', 'value:u.events.category:S3200', 'value:u.events.category:CHI'} <= names
    assert {'Retail Investor', 'International', 'branch CHI'} <= {p for m in data['vocabulary']['members'] for p in m['phrases']}
    assert 'value:u.events.source:5H' not in names
