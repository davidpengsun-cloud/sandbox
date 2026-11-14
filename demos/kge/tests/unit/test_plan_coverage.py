import pytest

from kge.semantics.plancheck import check_plan
from tests.plan_fixtures import grounded, model, vocabulary


@pytest.mark.parametrize('declared,member,expected', [
    (False, 'alpha', None),
    (True, 'alpha', 'code'),
    (True, 'another', None),
])
def test_code_and_name_coalesce_only_for_one_declared_member(declared, member, expected):
    from kge.semantics.coverage import member_value
    from kge.semantics.planvocabulary import Entry
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.tables['u.events'].update(identifier='VARCHAR', description='VARCHAR')
    sem.d.VOCABULARY = ([dict(kind='code_name_pair', params=dict(
        code='u.events.identifier', name='u.events.description',
        members=[dict(code='alpha', name=member)]))] if declared else [])
    entries = [Entry('code', 'value', 'alpha', 'u', column='u.events.identifier', value='alpha'),
               Entry('name', 'value', 'ALPHA', 'u', column='u.events.description', value='ALPHA')]
    v = vocabulary(sem, values=entries)
    result = member_value(('code', 'name'), v)
    assert (result.id if result else None) == expected


def test_two_members_with_a_shared_code_and_name_still_clarify():
    from kge.semantics.coverage import ledger, one_meaning
    from kge.semantics.planvocabulary import Entry
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.tables['u.events'].update(region_code='VARCHAR', region_name='VARCHAR')
    sem.d.VOCABULARY = [dict(kind='code_name_pair', params=dict(
        code='u.events.region_code', name='u.events.region_name',
        members=[dict(code='EAST', name='Coast'), dict(code='WEST', name='EAST')]))]
    v = vocabulary(sem, values=[
        Entry('east-code', 'value', 'EAST', 'u', column='u.events.region_code', value='EAST'),
        Entry('west-name', 'value', 'EAST', 'u', column='u.events.region_name', value='EAST')])
    assert not one_meaning(('east-code', 'west-name'), v)
    piece, = ledger('EAST', 'turn', v)
    assert piece.kind == 'name' and len(piece.candidates) == 2
    assert check_plan(grounded(model(), 'Metric EAST', v), v, 'Metric EAST').kind == 'clarify'


@pytest.mark.parametrize('question', ['What did the unit bring in as Metric?', 'Metric in total', 'Metric booked by u'])
def test_generic_measure_scaffolding_does_not_become_a_predicate_or_dimension(question):
    from kge.semantics.plancheck import CheckedPlan, check_plan
    v = vocabulary()
    question = question.replace('the unit', 'u')
    assert isinstance(check_plan(grounded(model(), question, v), v, question), CheckedPlan)


@pytest.mark.parametrize('suffix', ['restated', 'using current versions', 'using the current version'])
def test_declared_restated_alias_keeps_its_existing_current_time_mode(suffix):
    from kge.semantics.plancheck import CheckedPlan, check_plan
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.d.TIME_MODES = {'restated': 'restated'}
    v = vocabulary(sem)
    text = 'Metric ' + suffix
    assert isinstance(check_plan(grounded(model(), text, v), v, text), CheckedPlan)


def test_a_written_single_key_entity_selector_grounds_exactly_its_key_predicate():
    from kge.semantics.plancheck import CheckedPlan
    from tests.plan_fixtures import semantics, name
    sem = semantics()
    sem.entities = {'member': {'label': 'Member', 'table': 'u.events', 'key': ['entity_id']}}
    v = vocabulary(sem)
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.entity_id'), 'path': []},
                 'op': 'eq', 'value': {'kind': 'integer', 'value': 12}, 'evidence': []}
    text = 'Metric Member 12'
    p = grounded(model(filters=[predicate]), text, v, {'Member 12': 'f'})
    assert isinstance(check_plan(p, v, text), CheckedPlan)
    data = p.model_dump(mode='json', by_alias=True)
    data['filters'][0]['value']['value'] = 13
    assert check_plan(type(p).model_validate(data), v, text).kind == 'abstain'


@pytest.mark.parametrize('suffix', ['sparkly', 'UNKNOWN', 'not', 'by month', 'ratio', 'share', 'count', 'versus plan', 'above 50', 'current versus certified'])
def test_no_silent_drop_or_falsely_claimed_coverage(suffix):
    v = vocabulary()
    text = 'Metric ' + suffix
    p = grounded(model(), text, v)
    assert check_plan(p, v, text).kind == 'abstain'


def test_blank_duplicate_and_unrequested_nodes():
    v = vocabulary()
    p = grounded(model(), 'Metric', v)
    data = p.model_dump(mode='json', by_alias=True)
    data['obligations'] *= 2
    assert 'overlapping' in str(check_plan(type(p).model_validate(data), v, 'Metric'))
    data['obligations'] = []
    assert check_plan(type(p).model_validate(data), v, 'Metric').kind == 'abstain'


def test_unrequested_node_refusal_is_identical_across_process_hash_seeds():
    import os
    import subprocess
    import sys
    script = '''from kge.semantics.plancheck import check_plan
from tests.plan_fixtures import model, vocabulary
p = model()
data = p.model_dump(mode='json', by_alias=True)
data['measures'] = [{**data['measures'][0], 'id': identifier} for identifier in ('m1', 'm0')]
print('; '.join(check_plan(type(p).model_validate(data), vocabulary(), 'Metric').missing))
'''
    reasons = []
    for seed in ('1', '2', '3', '4'):
        result = subprocess.run([sys.executable, '-c', script], env={**os.environ, 'PYTHONHASHSEED': seed},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        reasons.append(result.stdout.strip())
    assert len(set(reasons)) == 1, reasons
    assert 'unrequested node m0; unrequested node m1' in reasons[0]


@pytest.mark.parametrize('question_value', ['20000', '20000.00', '-20000'])
@pytest.mark.parametrize('scalar_kind', ['decimal', 'integer'])
@pytest.mark.parametrize('matches', [True, False])
def test_bare_numeric_predicate_literals_compare_by_value(question_value, scalar_kind, matches):
    from decimal import Decimal
    from kge.semantics.plancheck import CheckedPlan
    from tests.plan_fixtures import name
    v = vocabulary()
    numeric = Decimal(question_value) + (0 if matches else 1)
    scalar = {'kind': scalar_kind, 'value': format(numeric, '.2f') if scalar_kind == 'decimal' else int(numeric)}
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
                 'op': 'ge', 'value': scalar, 'evidence': []}
    question = 'Metric value at least ' + question_value
    plan = grounded(model(filters=[predicate]), question, v, {'value': 'f', 'at least': 'f', question_value: 'f'})
    result = check_plan(plan, v, question)
    assert isinstance(result, CheckedPlan) is matches, result


@pytest.mark.parametrize('text,expected', [
    ('Metric in US dollars', [('US dollars', 'name', ('USD',), None)]),
    ('Metric US', [('US', 'predicate', ('u.events.source',), 'US')]),
    ('Metric in US', [('in US', 'predicate', ('u.events.source',), 'US')]),
])
def test_currency_phrase_keeps_a_shorter_country_value_as_a_country_only_when_standalone(text, expected):
    import re
    from kge.semantics.coverage import ledger
    from kge.semantics.planvocabulary import Entry
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.d.FILTERS = [(re.compile(r'(?<!\w)(?:in )?US(?!\w)', re.I), 'source', '=', None, 'US')]
    sem.d.FILTER_COLUMNS = ['u.events.source']
    v = vocabulary(sem, values=[Entry('USD', 'currency', 'USD', None)], aliases=[('US dollars', 'USD')])
    assert [(p.text, p.kind, p.candidates, p.value) for p in ledger(text, 'turn', v)[1:]] == expected


@pytest.mark.parametrize('matches', [True, False])
def test_bare_numeric_decimal_coverage_keeps_native_request_jobs(monkeypatch, tmp_path, matches):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.plan_fixtures import name
    from tests.unit.test_planner_jobs import jobs, setup
    fake, warehouse = setup(monkeypatch, tmp_path, False)
    v = vocabulary()
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.value'), 'path': []},
                 'op': 'ge', 'value': {'kind': 'decimal', 'value': '20000.00' if matches else '20001.00'}, 'evidence': []}
    question = 'Metric value at least 20000'
    plan = grounded(model(filters=[predicate]), question, v, {'value': 'f', 'at least': 'f', '20000': 'f'})
    try:
        for _ in range(2):
            fake.requests.clear()
            result = Planner(v, warehouse, Hidden([], set(), [])).answer(question, Propose(kind='propose', base_turn=None, plan=plan))
            assert result['kind'] == ('result' if matches else 'abstain'), result
            assert jobs(fake) == ((1, 1) if matches else (0, 0))
    finally:
        fake.con.close()


def test_an_unsupported_possessive_relationship_is_never_silently_scaffolded():
    from kge.semantics.coverage import ledger
    v = vocabulary()
    text = 'Metric and their Metric'
    assert any(piece.operation == 'membership' and piece.text == 'their' for piece in ledger(text, 'turn', v))
    assert check_plan(grounded(model(), text, v), v, text).kind == 'abstain'


@pytest.mark.parametrize('question_value,accepted', [('20000', False), ('20000.00', True)])
def test_numeric_looking_text_codes_keep_exact_text_semantics(question_value, accepted):
    from kge.semantics.plancheck import CheckedPlan
    from kge.semantics.planvocabulary import Entry
    from tests.plan_fixtures import name, semantics
    sem = semantics()
    sem.tables['u.events']['code'] = 'VARCHAR'
    v = vocabulary(sem, values=[Entry('code-value', 'value', 'named code', 'u', column='u.events.code', value='20000.00')])
    predicate = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'column', 'name': name('u.events.code'), 'path': []},
                 'op': 'eq', 'value': {'kind': 'text', 'value': '20000.00'}, 'evidence': []}
    question = 'Metric code equal to ' + question_value
    plan = grounded(model(filters=[predicate]), question, v, {'code': 'f', 'equal to': 'f', question_value: 'f'})
    decision = check_plan(plan, v, question)
    assert isinstance(decision, CheckedPlan) is accepted, decision


@pytest.mark.parametrize('text,accepted', [
    ('drop the category label breakdown', True),
    ('drop the another label breakdown', False),
])
def test_dimension_deletion_uses_its_exact_declared_grouping_phrase(text, accepted):
    from kge.semantics.coverage import removal_spans
    from kge.semantics.plan import Span
    from tests.unit.test_plan_relational_matrix import dimension
    v = vocabulary(aliases=[('by category label', 'u.events.category')])
    base = model(dimensions=[dimension('d', 'u.events.category')])
    current = model(obligations=[dict(id='delete', spans=[dict(turn='turn', start=0, end=len(text))],
        kind='name', targets=['d'], state='removed')])
    if accepted:
        assert removal_spans(current, text, 'turn', v, {'d': base.dimensions[0]}) == (Span(turn='turn', start=0, end=len(text)),)
    else:
        with pytest.raises(ValueError, match='does not identify'):
            removal_spans(current, text, 'turn', v, {'d': base.dimensions[0]})


def shared_fact_count_and_measure():
    from tests.plan_fixtures import name
    v = vocabulary()
    measured = model().measures[0].model_dump(mode='json')
    counted = dict(kind='distinct_count', id='n', entity=name('entity'), context=name('ctx'), mode='current', filters=[])
    text = 'Count Entities and their Metric'
    plan = grounded(model(measures=[measured, counted]), text, v, {'Count': 'n', 'Entities': 'n', 'their': 'm', 'Metric': 'm'})
    return text, v, plan


def test_a_count_and_amount_of_the_same_declared_fact_cover_their_shared_population():
    from kge.semantics.plancheck import CheckedPlan
    text, v, plan = shared_fact_count_and_measure()
    assert isinstance(check_plan(plan, v, text), CheckedPlan)


@pytest.mark.parametrize('difference', ['context', 'mode', 'filters', 'entity_fact'])
def test_their_requires_matching_declared_context_mode_filters_and_fact(difference):
    from dataclasses import replace
    from kge.semantics.coverage import operations
    from tests.plan_fixtures import name
    text, v, plan = shared_fact_count_and_measure()
    data = plan.model_dump(mode='json', by_alias=True)
    if difference == 'context':
        data['measures'][1]['context'] = name('another-context')
    elif difference == 'mode':
        data['measures'][1]['mode'] = 'as_reported'
    elif difference == 'filters':
        data['measures'][1]['filters'] = [dict(kind='null', id='f', is_null=False,
            left=dict(kind='column', name=name('u.events.category'), path=[]), evidence=[])]
    else:
        v = replace(v, entities={**v.entities, 'entity': {**v.entities['entity'], 'table': 'u.members'}})
    changed = type(plan).model_validate(data)
    assert 'membership' not in operations(changed.measures[0], v, changed)
    assert check_plan(changed, v, text).kind == 'abstain'


def test_same_fact_count_and_amount_keep_one_native_answer_job(monkeypatch, tmp_path):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    from tests.unit.test_planner_jobs import jobs, setup
    text, v, plan = shared_fact_count_and_measure()
    fake, warehouse = setup(monkeypatch, tmp_path, False)
    try:
        for _ in range(2):
            fake.requests.clear()
            result = Planner(v, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=plan))
            assert result['kind'] == 'result', result
            assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()
