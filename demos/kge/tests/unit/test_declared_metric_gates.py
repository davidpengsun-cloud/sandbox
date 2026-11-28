"""Metric declarations bind entity counts, shares and repeated output aggregations."""
from decimal import Decimal
import json
from dataclasses import replace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import build_vocabulary, selected_count_metrics, formula_metrics, count_metrics, MeasureSpec
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name
from tests.unit.test_measure_capability_figures import declarations, checked, ROWS
from tests.unit.test_plan_compiler import connection


def declared(monkeypatch, counts=True, shares=True):
    engine, base = declarations(monkeypatch)
    sem = engine.semantics
    sem.d.VOCABULARY = [row for row in sem.d.VOCABULARY if row['kind'] != 'derived_measure']
    if counts:
        sem.d.VOCABULARY.append({'kind': 'count_metric', 'params': {'id': 'entity_count', 'term': 'entity-count',
            'label': 'Entity count', 'entity': 'entity', 'context': 'ctx', 'filters': []}})
    if shares:
        sem.d.VOCABULARY.append({'kind': 'derived_measure', 'params': {'id': 'u.contribution', 'term': 'contribution',
            'label': 'Metric contribution', 'formula': {'kind': 'share', 'numerator': 'u.metric', 'denominator': 'u.metric'}}})
    if counts and shares:
        sem.d.VOCABULARY.append({'kind': 'derived_measure', 'params': {'id': 'entity_contribution', 'term': 'entity-contribution',
            'label': 'Entity contribution', 'formula': {'kind': 'share', 'numerator': 'entity_count', 'denominator': 'entity_count'}}})
    engine.term_names.extend(['Entity count', 'Metric contribution', 'Entity contribution'])
    vocab = build_vocabulary(sem, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('Units', 'units'), *sem.d.GROUP_BY.items()])
    return engine, vocab


def rules(engine, vocabulary, text):
    return planrules.read(engine, parse(text, engine.term_names, engine), vocabulary, PRESETS['graph'])


def execute(plan, text, vocabulary):
    result = compile_plan(checked(plan, text, vocabulary))
    assert result.answer is not None
    assert check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        cursor = con.execute(result.answer.sql)
        return [dict(zip([column[0] for column in cursor.description], row, strict=True)) for row in cursor.fetchall()]


@pytest.mark.parametrize('text', ['sum Metric and count Metric', 'count Metric and sum Metric',
                                  'average Metric and maximum Metric'])
def test_two_aggregations_of_one_context_are_two_outputs_on_both_paths(monkeypatch, text):
    engine, vocabulary = declared(monkeypatch)
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    assert len(proposal.plan.measures) == 2
    values = execute(proposal.plan, text, vocabulary)[0]
    expected = {'sum': Decimal(16), 'count': Decimal(3), 'avg': Decimal(16) / 3, 'max': Decimal(10)}
    for measure in proposal.plan.measures:
        assert Decimal(str(values[measure.id])) == pytest.approx(expected[measure.aggregation])
    missing = proposal.plan.model_copy(update={'measures': proposal.plan.measures[:1]})
    assert not isinstance(check_plan(ground_plan(missing, text, 'turn', vocabulary), vocabulary, text), CheckedPlan)


def test_explicit_model_plan_accepts_sum_and_count_of_one_context(monkeypatch):
    _, vocabulary = declared(monkeypatch)
    data = model().model_dump(mode='json', by_alias=True)
    data['measures'][0]['aggregation'] = 'sum'
    data['measures'].append(dict(kind='measure', id='count', measure=name('u.metric'), context=name('ctx'),
                                 mode='current', filters=[], aggregation='count'))
    values = execute(QueryPlan.model_validate(data), 'sum Metric and count Metric', vocabulary)[0]
    assert {key: value for key, value in values.items() if not key.endswith('/reason')} == {'m': Decimal(16), 'count': 3}


@pytest.mark.parametrize('text', ['Metric with number of Entities', 'share of Metric by category'])
def test_missing_metric_declaration_refuses_the_review_probes(monkeypatch, text):
    engine, vocabulary = declared(monkeypatch, counts=False, shares=False)
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'abstain', reply
    assert 'metric is declared' in ' '.join(reply.missing), reply


def test_declared_entity_count_matches_hand_counted_complete_keys(monkeypatch):
    engine, vocabulary = declared(monkeypatch)
    text = 'Metric with number of Entities'
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'propose', reply
    count, = [m for m in reply.plan.measures if m.kind == 'distinct_count']
    assert count.declaration is None
    assert selected_count_metrics(vocabulary, vocabulary.contexts[count.context.id], count.entity.id)[0]['id'] == 'entity_count'
    values = execute(reply.plan, text, vocabulary)[0]
    assert values[reply.plan.measures[0].id] == 16
    assert values[count.id] == 2


def test_named_count_metric_plans_the_declared_metric(monkeypatch):
    engine, vocabulary = declared(monkeypatch)
    text = 'Entity count'
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'propose', reply
    count, = reply.plan.measures
    assert count.kind == 'distinct_count' and count.declaration.id == 'entity_count'
    assert execute(reply.plan, text, vocabulary)[0][count.id] == 2


@pytest.mark.parametrize('text,outputs', [('Entity count', 1), ('Entity count and number of Entities', 2)])
def test_entity_words_inside_a_named_count_do_not_add_a_default_population(monkeypatch, text, outputs):
    engine, base = declared(monkeypatch)
    sem = engine.semantics
    sem.contexts['context:count:entity'] = replace(sem.contexts['ctx'], grain='day', id='context:count:entity',
        label='Entity population', term='count:entity', measure='count:entity', aggregation='count',
        expression='count(DISTINCT (events.entity_id, events.source))', filters=(),
        values=('u.events.entity_id', 'u.events.source'))
    engine.ENTITY_WORDS = {'entity': 'entity', 'entities': 'entity'}
    vocabulary = build_vocabulary(sem, base.binding, ['u'], ['u'], [*base.measures.values(),
        MeasureSpec('count:entity', None, 'entity', False)], aliases=[('Metric', 'ctx'), ('Units', 'units')])
    request = parse(text, engine.term_names, engine)
    assert request.counted == 'entity' and request.agg == 'count'
    reply = planrules.read(engine, request, vocabulary, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert len(reply.plan.measures) == outputs
    assert all(count.context.id == 'ctx' for count in reply.plan.measures)
    assert sum(count.declaration is not None and count.declaration.id == 'entity_count'
               for count in reply.plan.measures) == 1
    values = execute(reply.plan, text, vocabulary)[0]
    assert all(values[count.id] == 2 for count in reply.plan.measures)


def test_a_named_count_uses_its_population_bindings_for_filters_and_dimensions(monkeypatch):
    from types import SimpleNamespace
    from kge.semantics.vocabulary import compiled
    engine, _ = declared(monkeypatch, shares=False)
    sem = engine.semantics
    sem.tables['u.canonical'] = {'category': 'VARCHAR'}
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', bindings={
        'u.canonical.category': 'u.events.category'})
    sem.d.GROUP_BY = {'by category': 'u.canonical.category'}
    sem.d.VOCABULARY.extend({'kind': 'predicate', 'params': {'phrases': ['Group A'],
        'column': column, 'op': 'eq', 'value': 'A'}} for column in ('u.canonical.category', 'u.events.category'))
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    sem.context_declarations = frozenset(sem.contexts)
    sem.no_join_declarations, sem.mapping_declarations = [], []
    sem.plan_writes = (0, ())
    for key, value in vars(sem.d).items():
        setattr(engine, key, value)
    engine.identity = {}
    engine.UNIT_LABEL, engine.SOURCE_SETS, engine.ENTITY_WORDS, engine.RESTRICTED_PHRASES = {}, {}, {}, {}
    engine.g = SimpleNamespace(writes=0, readable_units=lambda _: ['u'], may=lambda *_: True,
        domain=lambda: {'vocabulary': sem.d.VOCABULARY, 'business': {}},
        scoped_vocabulary=lambda binding, variant, writes, read: read())
    monkeypatch.setattr(planrules, 'cohort_snapshot', lambda _, snapshot, __: snapshot)
    text = 'Entity count for Group A by category'
    req = parse(text, engine.term_names, engine)
    vocabulary = planrules.vocabulary(engine, 'reader', 'analysis', SimpleNamespace(products=[]), PRESETS['graph'], req)
    assert vocabulary.candidates('by category')[0].column == 'u.events.category'
    assert 'u.canonical.category' not in vocabulary.entries
    proposal = planrules.read(engine, req, vocabulary, PRESETS['graph'])
    assert proposal.kind == 'propose', proposal
    count, = proposal.plan.measures
    assert count.kind == 'distinct_count' and count.declaration.id == 'entity_count'
    assert proposal.plan.dimensions[0].bindings[0].column.id == 'u.events.category'
    assert execute(proposal.plan, text, vocabulary)[0][count.id] == 2


@pytest.mark.parametrize('text', ['share of Metric by category', 'Metric contribution by category'])
def test_declared_share_equals_hand_computed_filtered_total(monkeypatch, text):
    engine, vocabulary = declared(monkeypatch)
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'propose', reply
    share, = reply.plan.derived
    assert (share.declaration.id if share.declaration else None) == (None if text.startswith('share of') else 'u.contribution')
    assert formula_metrics(vocabulary, 'share', 'u.metric', 'u.metric', contexts={'numerator': 'ctx', 'denominator': 'ctx'}) == ('u.contribution',)
    rows = execute(reply.plan, text, vocabulary)
    assert {row['d0']: Decimal(str(row[share.id])) for row in rows} == {'A': Decimal('0.375'), 'B': Decimal('0.625')}


def test_count_local_filters_are_required_and_cannot_filter_the_sum(monkeypatch):
    engine, _ = declared(monkeypatch)
    sem = engine.semantics
    row = next(row['params'] for row in sem.d.VOCABULARY if row['kind'] == 'count_metric')
    row['filters'] = [{'column': 'u.events.entity_id', 'op': 'lt', 'value': 4}]
    base = declarations()[1]
    vocabulary = build_vocabulary(sem, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('Units', 'units'), *sem.d.GROUP_BY.items()])
    text = 'Metric with number of Entities'
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'propose', reply
    count, = [m for m in reply.plan.measures if m.kind == 'distinct_count']
    assert execute(reply.plan, text, vocabulary)[0][count.id] == 1
    sum_output, = [m for m in reply.plan.measures if m.kind == 'measure']
    assert execute(reply.plan, text, vocabulary)[0][sum_output.id] == 16
    wrong = reply.plan.model_copy(update={'measures': tuple(m.model_copy(update={'filters': ()}) if m.id == count.id else m
                                                          for m in reply.plan.measures)})
    result = check_plan(ground_plan(wrong, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(result, CheckedPlan)
    assert 'declared local filters' in ' '.join(result.missing)


def test_model_cannot_introduce_an_undeclared_entity_count(monkeypatch):
    _, vocabulary = declared(monkeypatch, counts=False)
    data = model().model_dump(mode='json', by_alias=True)
    data['measures'].append(dict(kind='distinct_count', id='n', entity=name('entity'), context=name('ctx'),
                                 mode='current', filters=[]))
    text = 'Metric with number of Entities'
    result = check_plan(ground_plan(QueryPlan.model_validate(data), text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(result, CheckedPlan)
    assert 'distinct-count metric' in ' '.join(result.missing)


@pytest.mark.parametrize('text', ['share of Entity count by category', 'Entity contribution by category'])
def test_count_metric_is_a_declared_share_operand(monkeypatch, text):
    engine, vocabulary = declared(monkeypatch)
    reply = rules(engine, vocabulary, text)
    assert reply.kind == 'propose', reply
    measure, = reply.plan.measures
    share, = reply.plan.derived
    assert measure.declaration.id == 'entity_count'
    assert (share.declaration.id if share.declaration else None) == (None if text.startswith('share of') else 'entity_contribution')
    rows = execute(reply.plan, text, vocabulary)
    assert {row['d0']: Decimal(str(row[share.id])) for row in rows} == {'A': Decimal(1), 'B': Decimal('0.5')}


def test_model_cannot_introduce_an_undeclared_share(monkeypatch):
    _, vocabulary = declared(monkeypatch, shares=False)
    data = model().model_dump(mode='json', by_alias=True)
    data['dimensions'] = [{'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}]
    data['derived'] = [dict(kind='share', id='share', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])]
    text = 'share of Metric by category'
    decision = check_plan(ground_plan(QueryPlan.model_validate(data), text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'share metric' in ' '.join(decision.missing)


def test_source_entity_count_applies_only_to_an_equivalent_population(monkeypatch):
    engine, base = declared(monkeypatch)
    sem = engine.semantics
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=())
    context = sem.contexts['ctx']
    sem.contexts['context:count:entity'] = replace(context, grain='day', id='context:count:entity', label='Entity population',
        term='Entity population', measure='count:entity', aggregation='count',
        expression='count(DISTINCT (events.entity_id, events.source))', values=('u.events.entity_id', 'u.events.source'))
    row = next(row['params'] for row in sem.d.VOCABULARY if row['kind'] == 'count_metric')
    del row['context']
    from kge.semantics.planvocabulary import MeasureSpec
    def vocabulary():
        return build_vocabulary(sem, base.binding, ['u'], ['u'], [*base.measures.values(),
            MeasureSpec('count:entity', None, 'entity', False)], aliases=[('Metric', 'ctx')])
    text = 'Metric with number of Entities'
    scoped = vocabulary()
    reply = rules(engine, scoped, text)
    assert reply.kind == 'propose', reply
    count, = [m for m in reply.plan.measures if m.kind == 'distinct_count']
    assert count.declaration is None
    assert selected_count_metrics(scoped, scoped.contexts[count.context.id], count.entity.id)[0]['id'] == 'entity_count'
    assert execute(reply.plan, text, scoped)[0][count.id] == 3
    sem.contexts['ctx'] = replace(context, grain='day', filters=declarations()[0].semantics.contexts['ctx'].filters)
    reply = rules(engine, vocabulary(), text)
    assert reply.kind == 'abstain' and 'metric is declared' in ' '.join(reply.missing)


@pytest.mark.parametrize('text', ['Metric and count Metric', 'Metric with count Metric', 'count Metric and Metric'])
def test_an_unqualified_occurrence_keeps_its_default_aggregation(monkeypatch, text):
    engine, vocabulary = declared(monkeypatch)
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    assert len(proposal.plan.measures) == 2
    values = execute(proposal.plan, text, vocabulary)[0]
    for measure in proposal.plan.measures:
        assert values[measure.id] == (3 if measure.aggregation == 'count' else 16)
    first, second = proposal.plan.measures
    assert (first.aggregation == 'count') == text.startswith('count')
    only_count = proposal.plan.model_copy(update={'measures': tuple(m for m in proposal.plan.measures if m.aggregation == 'count')})
    decision = check_plan(ground_plan(only_count, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'own output' in ' '.join(decision.missing)


def test_a_named_formula_dependency_keeps_its_default_beside_a_count_of_the_same_metric(monkeypatch):
    engine, vocabulary = declarations(monkeypatch)
    text = 'Margin and count Metric'
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    formula, = proposal.plan.derived
    inputs = {m.id: m for m in proposal.plan.measures}
    assert inputs[formula.numerator].aggregation is None
    count, = [m for m in proposal.plan.measures if m.aggregation == 'count']
    values = execute(proposal.plan, text, vocabulary)[0]
    assert values[formula.numerator] == 16 and values[formula.denominator] == 8
    assert values[formula.id] == 2 and values[count.id] == 3


def rebuilt(engine, base, aliases=(), populations=None):
    specs = [replace(spec, population=(populations or {}).get(spec.id, spec.population)) for spec in base.measures.values()]
    return build_vocabulary(engine.semantics, base.binding, ['u'], ['u'], specs,
        aliases=[('Metric', 'ctx'), ('Units', 'units'), *engine.semantics.d.GROUP_BY.items(), *aliases])


@pytest.mark.parametrize('different_population', [False, True])
def test_amount_over_a_contextual_count_uses_the_same_declared_population(monkeypatch, different_population):
    engine, base = declared(monkeypatch, shares=False)
    sem = engine.semantics
    count_context = 'units' if different_population else 'ctx'
    row = next(row['params'] for row in sem.d.VOCABULARY if row['kind'] == 'count_metric')
    row['context'] = count_context
    sem.d.VOCABULARY.append({'kind': 'derived_measure', 'params': {'id': 'per_entity', 'label': 'Population quotient',
        'formula': {'kind': 'ratio', 'numerator': 'u.metric', 'denominator': 'entity_count'},
        'contexts': {'numerator': 'ctx', 'denominator': count_context}}})
    engine.term_names.append('Population quotient')
    vocabulary = rebuilt(engine, base, populations={'u.units': 'other_population'} if different_population else {})
    text = 'Population quotient'
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    if different_population:
        decision = check_plan(proposal.plan, vocabulary, text)
        assert not isinstance(decision, CheckedPlan)
        assert 'populations' in ' '.join(decision.missing)
    else:
        formula, = proposal.plan.derived
        assert execute(proposal.plan, text, vocabulary)[0][formula.id] == 8


@pytest.mark.parametrize('text', ['Other contribution by category', 'share of Other metric by category'])
def test_formula_population_bindings_disambiguate_contexts_of_one_physical_measure(monkeypatch, text):
    from kge.semantics.query import Rule
    engine, base = declared(monkeypatch)
    sem = engine.semantics
    sem.contexts['other'] = replace(sem.contexts['ctx'], grain='day', id='other', label='Other metric', term='Other metric',
        filters=(*sem.contexts['ctx'].filters, Rule('category', '', "events.category='B'", 'u.events')))
    sem.d.VOCABULARY.append({'kind': 'derived_measure', 'params': {'id': 'other_share', 'label': 'Other contribution',
        'formula': {'kind': 'share', 'numerator': 'u.metric', 'denominator': 'u.metric'},
        'contexts': {'numerator': 'other', 'denominator': 'other'}}})
    engine.term_names.extend(['Other metric', 'Other contribution'])
    vocabulary = rebuilt(engine, base, aliases=[('Other metric', 'other')])
    json.loads(json.dumps(vocabulary.public()))
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    measure, = proposal.plan.measures
    share, = proposal.plan.derived
    assert measure.context.id == 'other'
    rows = execute(proposal.plan, text, vocabulary)
    assert rows[0][measure.id] == 10 and rows[0][share.id] == 1
    wrong = proposal.plan.model_copy(update={'measures': (measure.model_copy(update={'context': measure.context.model_copy(update={'id': 'ctx'})}),)})
    assert not isinstance(check_plan(ground_plan(wrong, text, 'turn', vocabulary), vocabulary, text), CheckedPlan)
    sem.contexts.pop('other')
    assert not rebuilt(engine, base).candidates('Other contribution')


def test_a_named_share_cannot_let_the_model_choose_an_ambiguous_input_population(monkeypatch):
    from kge.semantics.query import Rule
    engine, base = declared(monkeypatch)
    sem = engine.semantics
    sem.contexts['other'] = replace(sem.contexts['ctx'], grain='day', id='other', label='Other metric', term='Other metric',
        filters=(*sem.contexts['ctx'].filters, Rule('category', '', "events.category='B'", 'u.events')))
    engine.term_names.append('Other metric')
    vocabulary = rebuilt(engine, base, aliases=[('Other metric', 'other')])
    data = model().model_dump(mode='json', by_alias=True)
    data['dimensions'] = [{'kind': 'column', 'id': 'd', 'bindings': [
        {'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}]
    data['derived'] = [{'kind': 'share', 'id': 'share', 'input': 'm', 'remove_dimensions': ['d'],
                        'remove_filters': [], 'declaration': name('u.contribution'), 'evidence': []}]
    text = 'Metric contribution by category'
    assert rules(engine, vocabulary, text).kind == 'clarify'
    decision = check_plan(ground_plan(QueryPlan.model_validate(data), text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'explicit context' in ' '.join(decision.missing)
    text = 'Metric contribution of Other metric by category'
    data['measures'][0]['context'] = name('other')
    data['dimensions'][0]['bindings'][0]['context'] = name('other')
    rows = execute(QueryPlan.model_validate(data), text, vocabulary)
    assert len(rows) == 1 and rows[0]['d'] == 'B' and rows[0]['share'] == 1


@pytest.mark.parametrize('connector', ['AND', 'OR'])
def test_a_physical_count_does_not_implicitly_declare_an_entity_count(monkeypatch, connector):
    from kge.semantics.query import Join
    engine, base = declarations(monkeypatch)
    sem = engine.semantics
    sem.tables['u.members'] = {'entity_id': 'INTEGER', 'source': 'VARCHAR'}
    sem.entities['members'] = {'label': 'Members', 'table': 'u.members', 'key': ['entity_id', 'source']}
    join = Join('members', 'key', 'events.entity_id=members.entity_id ' + connector + ' events.source=members.source',
                None, None, 'u.events', 'u.members', False)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(join,))
    sem.contexts['count_members'] = replace(sem.contexts['ctx'], grain='day', id='count_members', label='Member count',
        term='Member count', measure='u.member_count', aggregation='count',
        expression='count(DISTINCT (events.entity_id, events.source))')
    vocabulary = build_vocabulary(sem, base.binding, ['u'], ['u'], [*base.measures.values(),
        MeasureSpec('u.member_count', None, 'members', False)])
    choices = count_metrics(vocabulary, vocabulary.contexts['ctx'], 'members')
    assert not choices
    sem.d.VOCABULARY.append({'kind': 'count_metric', 'params': {'id': 'members_count', 'label': 'Declared member count',
        'entity': 'members', 'context': 'ctx', 'filters': []}})
    vocabulary = build_vocabulary(sem, base.binding, ['u'], ['u'], [*base.measures.values(),
        MeasureSpec('u.member_count', None, 'members', False)])
    assert count_metrics(vocabulary, vocabulary.contexts['ctx'], 'members')[0]['id'] == 'members_count'


def test_entity_count_requires_its_own_declaration_and_drops_incomplete_keys(monkeypatch):
    import datetime as dt
    engine, base = declared(monkeypatch, counts=False, shares=False)
    sem = engine.semantics
    sem.contexts['count_entities'] = replace(sem.contexts['ctx'], grain='day', id='count_entities', label='Member count',
        term='Member count', measure='u.member_count', aggregation='count',
        expression='count(DISTINCT (events.entity_id, events.source))')
    engine.term_names.append('Member count')
    def vocabulary():
        return build_vocabulary(sem, base.binding, ['u'], ['u'], [*base.measures.values(),
            MeasureSpec('u.member_count', None, 'entity', False)], aliases=[('Metric', 'ctx'), ('Member count', 'count_entities')])
    def figures(proposal, text, scoped):
        result = compile_plan(checked(proposal.plan, text, scoped))
        assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
        with connection([*ROWS, (dt.date(2025, 4, 10), 5, 'A', None, 'x')]) as con:
            return con.execute(result.answer.sql).fetchone()
    scoped = vocabulary()
    text = 'Member count'
    assert figures(rules(engine, scoped, text), text, scoped)[0] == 3
    text = 'Metric with number of Entities'
    reply = rules(engine, scoped, text)
    assert reply.kind == 'abstain' and 'distinct-count metric' in ' '.join(reply.missing)
    sem.d.VOCABULARY.append({'kind': 'count_metric', 'params': {'id': 'entity_count', 'label': 'Entity count',
        'entity': 'entity', 'context': 'ctx', 'filters': []}})
    scoped = vocabulary()
    assert figures(rules(engine, scoped, text), text, scoped)[1] == 2
    text = 'Member count'
    assert figures(rules(engine, scoped, text), text, scoped)[0] == 3


def test_a_declared_count_filter_cannot_switch_to_another_join_role(monkeypatch):
    from kge.semantics.plan import Name
    from kge.semantics.query import Join
    engine, base = declared(monkeypatch, counts=False, shares=False)
    sem = engine.semantics
    sem.tables['u.members'] = {'entity_id': 'INTEGER', 'source': 'VARCHAR'}
    sem.entities['members'] = {'label': 'Members', 'table': 'u.members', 'key': ['entity_id', 'source']}
    primary = Join('primary_member', 'key', 'events.entity_id=members.entity_id AND events.source=members.source',
                   None, None, 'u.events', 'u.members', False)
    alternate = Join('alternate_member', 'key', 'events.value=alternate.entity_id AND events.source=alternate.source',
                     'alternate', None, 'u.events', 'u.members', False)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(primary, alternate))
    sem.d.VOCABULARY.append({'kind': 'count_metric', 'params': {'id': 'members_count', 'label': 'Declared member count',
        'entity': 'members', 'context': 'ctx', 'filters': [{'column': 'u.members.entity_id', 'op': 'gt', 'value': 3}]}})
    engine.term_names.append('Declared member count')
    vocabulary = rebuilt(engine, base)
    text = 'Declared member count'
    proposal = rules(engine, vocabulary, text)
    result = compile_plan(checked(proposal.plan, text, vocabulary))
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR)')
        con.execute("INSERT INTO members VALUES (2, 'x'), (4, 'x'), (10, 'x')")
        assert con.execute(result.answer.sql).fetchone()[0] == 1
    assert len({(row[3], row[4]) for row in ROWS if row[1] is not None and row[1] > 3}) == 2
    measure, = proposal.plan.measures
    predicate, = measure.filters
    wrong = predicate.model_copy(update={'left': predicate.left.model_copy(update={
        'path': (Name(id='alternate_member', evidence=()),)})})
    changed = proposal.plan.model_copy(update={'measures': (measure.model_copy(update={'filters': (wrong,)}),)})
    decision = check_plan(ground_plan(changed, text, 'turn', vocabulary), vocabulary, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'declared local filters' in ' '.join(decision.missing)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(primary, replace(alternate, alias=None,
        condition='events.value=members.entity_id AND events.source=members.source')))
    ambiguous = rebuilt(engine, base)
    decision = check_plan(ground_plan(proposal.plan, text, 'turn', ambiguous), ambiguous, text)
    assert not isinstance(decision, CheckedPlan)
    assert 'declared local filters' in ' '.join(decision.missing)


def test_part_evidence_resolves_the_full_source_and_declared_role():
    from kge.semantics.coverage import Piece, declared_part
    from kge.semantics.plan import Span
    from kge.semantics.query import Rule
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.tables['v.events'] = dict(sem.tables['u.events'])
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=(Rule('category', '', "events.category='A'", 'u.events'),))
    base = declarations()[1]
    vocabulary = build_vocabulary(sem, base.binding, ['u', 'v'], ['u', 'v'], [MeasureSpec('u.metric', 'USD', 'entities', True)])
    def piece(column):
        return Piece(Span(turn='turn', start=0, end=7), 'Group A', 'predicate', (column,), 'eq', 'A')
    assert declared_part(piece('u.events.category'), model().measures[0], vocabulary)
    assert not declared_part(piece('v.events.category'), model().measures[0], vocabulary)


def test_unfiltered_default_and_named_conditional_count_are_both_declared(monkeypatch):
    engine, base = declared(monkeypatch)
    sem = engine.semantics
    row = next(row['params'] for row in sem.d.VOCABULARY if row['kind'] == 'count_metric')
    row['filters'] = [{'column': 'u.events.entity_id', 'op': 'lt', 'value': 4}]
    sem.d.VOCABULARY.append({'kind': 'count_metric', 'params': {**row, 'id': 'all_entities', 'label': 'Complete entity count', 'filters': []}})
    vocabulary = rebuilt(engine, base)
    text = 'Metric with number of Entities'
    proposal = rules(engine, vocabulary, text)
    count, = [m for m in proposal.plan.measures if m.kind == 'distinct_count']
    assert count.declaration is None and count.filters == ()
    assert execute(proposal.plan, text, vocabulary)[0][count.id] == 2
    engine.term_names.append('Entity count')
    named = rules(engine, vocabulary, 'Entity count')
    count, = named.plan.measures
    assert count.declaration.id == 'entity_count'
    assert execute(named.plan, 'Entity count', vocabulary)[0][count.id] == 1
    wrong = named.plan.model_copy(update={'measures': (count.model_copy(update={'filters': ()}),)})
    assert not isinstance(check_plan(ground_plan(wrong, 'Entity count', 'turn', vocabulary), vocabulary, 'Entity count'), CheckedPlan)


@pytest.mark.parametrize('text', ['Metric with count Metric and Units', 'count Metric and Metric with Units'])
def test_a_repeated_count_does_not_change_another_outputs_default(monkeypatch, text):
    engine, vocabulary = declared(monkeypatch)
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    values = execute(proposal.plan, text, vocabulary)[0]
    actual = {(measure.context.id, measure.aggregation or vocabulary.contexts[measure.context.id].aggregation):
              values[measure.id] for measure in proposal.plan.measures}
    assert actual == {('ctx', 'sum'): 16, ('ctx', 'count'): 3, ('units', 'sum'): 8}


def test_sum_and_count_of_one_metric_keep_their_figures_per_group(monkeypatch):
    engine, vocabulary = declared(monkeypatch)
    text = 'sum Metric and count Metric by category'
    proposal = rules(engine, vocabulary, text)
    assert proposal.kind == 'propose', proposal
    outputs = {measure.aggregation: measure.id for measure in proposal.plan.measures}
    rows = execute(proposal.plan, text, vocabulary)
    assert {row['d0']: (row[outputs['sum']], row[outputs['count']]) for row in rows} == {
        'A': (6, 2), 'B': (10, 1)}
