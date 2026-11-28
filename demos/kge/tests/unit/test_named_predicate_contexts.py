"""Trusted metric names own predicate scope; unbound predicates offer metric contexts."""
from dataclasses import replace
from copy import copy
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger, one_meaning
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.plan import Clarification, Choice, Clarify, HistoryEntry, Name, Span
from kge.semantics.planhistory import validate
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary
from kge.semantics.query import Join, Rule
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import semantics, vocabulary as base_vocabulary
from tests.unit.test_measure_capability_figures import ROWS
from tests.unit.test_plan_compiler import connection


def routing(monkeypatch, source_alias=False):
    sem = semantics()
    sem.tables['u.ledger'] = dict(sem.tables['u.events'])
    sem.d.PERIODS['u.ledger'] = dict(sem.d.PERIODS['u.events'])
    sem.contexts['audited'] = replace(sem.contexts['ctx'], grain='day', id='audited', label='Audited Metric', term='Audited Metric',
        fact='u.ledger', measure='u.audited_metric', expression='sum(ledger.value)', values=('u.ledger.value',),
        filters=(Rule('positive', '', 'ledger.value >= 0', 'u.ledger'),))
    sem.d.VOCABULARY = [{'kind': 'predicate', 'params': {'phrases': ['accepted entries'], 'column': column, 'op': 'eq', 'value': 'A'}}
                        for column in ('u.events.category', 'u.ledger.category')]
    sem.d.ALIASES = {'metric': 'Metric', 'audited metric': 'Audited Metric'}
    sem.d.UNIT_WORDS, sem.d.GROUP_BY = {}, {}
    sem.measure_declarations = {}
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    aliases = [('Metric', 'ctx'), ('Audited Metric', 'audited')]
    if source_alias:
        sem.tables['v.other'] = {'marker': 'INTEGER'}
        sem.d.ALIASES['side metric'] = 'Audited Metric'
        sem.d.UNIT_WORDS['side'] = 'v'
        aliases.extend([('Side Metric', 'audited'), ('Side Metric', 'v')])
    units = ['u', 'v'] if source_alias else ['u']
    vocabulary = build_vocabulary(sem, base_vocabulary().binding, units, units, [
        MeasureSpec('u.metric', 'USD', 'entities', True), MeasureSpec('u.audited_metric', 'USD', 'entities', True)], aliases=aliases)
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric', 'Audited Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return engine, vocabulary


@pytest.mark.parametrize('text', ['gross accepted entries of Audited Metric for April 2025',
                                  'Audited Metric for April 2025 for accepted entries'])
def test_a_full_metric_name_owns_a_multi_fact_predicate_over_its_embedded_noun(monkeypatch, text):
    engine, vocabulary = routing(monkeypatch)
    request = parse(text, engine.term_names, engine)
    assert set(request.terms_found) == {'Metric', 'Audited Metric'}
    piece, = [piece for piece in ledger(text, 'turn', vocabulary) if piece.text == 'Audited Metric']
    assert len(piece.candidates) == 2 and one_meaning(piece.candidates, vocabulary)
    reply = planrules.read(engine, request, vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'propose', reply
    assert {measure.context.id for measure in reply.plan.measures} == {'audited'}
    checked = check_plan(reply.plan, vocabulary, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        con.execute('CREATE TABLE ledger(occurred DATE, value DECIMAL(18,2), category VARCHAR, entity_id INT, source VARCHAR, private VARCHAR)')
        for day, value, category, entity, source in ROWS:
            con.execute('INSERT INTO ledger VALUES (?, ?, ?, ?, ?, NULL)',
                        (day, value * 10 if value is not None else None, category, entity, source))
        assert con.execute(result.answer.sql).fetchone()[0] == 60


def test_an_unbound_predicate_offers_only_declared_reachable_metric_contexts(monkeypatch):
    engine, vocabulary = routing(monkeypatch)
    text = 'gross accepted entries for April 2025'
    reply = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'clarify', reply
    assert {choice.id for choice in reply.candidates} == {'ctx', 'audited'}
    assert all(vocabulary.entries[choice.id].kind == 'context' for choice in reply.candidates)
    assert reply.spans and all(text[span.start:span.end] == 'accepted entries' for span in reply.spans)
    answer = Planner(vocabulary, None, Hidden([], set(), [])).prepare(text, reply)
    assert answer['kind'] == 'clarify' and {choice['id'] for choice in answer['choices']} == {'ctx', 'audited'}


def clarification_history(vocabulary, text, reply):
    entry = HistoryEntry(turn_id='turn', question=text, plan=None, binding=vocabulary.binding,
        clarification=Clarification(spans=reply.spans, slot='meaning', choices=tuple(
            Choice(id=choice.id, label=vocabulary.entries[choice.id].label) for choice in reply.candidates)))
    return HistoryEntry.model_validate_json(entry.model_dump_json(by_alias=True))


def test_serialized_predicate_context_choices_allow_a_followup_without_losing_the_filter(monkeypatch):
    engine, vocabulary = routing(monkeypatch)
    question = 'gross accepted entries for April 2025'
    reply = planrules.read(engine, parse(question, engine.term_names, engine), vocabulary,
                           SimpleNamespace(graph=True, glossary=True))
    entry = clarification_history(vocabulary, question, reply)
    followup = question.replace('gross', vocabulary.contexts['ctx'].label)
    assert validate([entry], vocabulary, followup, 'next') is None
    proposal = planrules.read(engine, parse(followup, engine.term_names, engine), vocabulary,
                              SimpleNamespace(graph=True, glossary=True), turn='next')
    checked = Planner(vocabulary, None, Hidden([], set(), [])).prepare(followup, proposal, turn='next', history=[entry])
    assert isinstance(checked, CheckedPlan), checked
    assert {measure.context.id for measure in checked.plan.measures} == {'ctx'}
    result = compile_plan(checked)
    assert result.answer is not None and check(result.answer.sql, result.answer.allow) == []
    with connection(ROWS) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 6


@pytest.mark.parametrize('mutation', ['unrelated-choice', 'partial-predicates', 'broad-span'])
def test_context_clarification_history_refuses_forged_choices_or_evidence(monkeypatch, mutation):
    engine, vocabulary = routing(monkeypatch)
    question = 'gross accepted entries and accepted entries for April 2025'
    reply = planrules.read(engine, parse(question, engine.term_names, engine), vocabulary,
                           SimpleNamespace(graph=True, glossary=True))
    entry = clarification_history(vocabulary, question, reply)
    assert validate([entry], vocabulary, 'Metric', 'next') is None
    if mutation == 'unrelated-choice':
        identifier = 'u.events.value'
        changed = entry.clarification.model_copy(update={'choices': (
            Choice(id=identifier, label=vocabulary.entries[identifier].label),)})
    elif mutation == 'partial-predicates':
        changed = entry.clarification.model_copy(update={'spans': entry.clarification.spans[:1]})
    else:
        changed = entry.clarification.model_copy(update={'spans': (Span(turn='turn', start=0, end=len(question)),)})
    entry = entry.model_copy(update={'clarification': changed})
    decision = validate([entry], vocabulary, 'Metric', 'next')
    assert decision is not None and decision.kind == 'abstain'
    assert 'unrelated evidence' in ' '.join(decision.missing)


@pytest.mark.parametrize('kind', ['unrelated', 'undeclared', 'unreadable'])
def test_predicate_clarification_refuses_unrelated_undeclared_or_unreadable_contexts(monkeypatch, kind):
    _, vocabulary = routing(monkeypatch)
    base = vocabulary.contexts['ctx']
    context = replace(base, grain='day', id=kind, label=kind.title() + ' Metric')
    tables = dict(vocabulary.tables)
    runtime = copy(vocabulary.semantics)
    if kind != 'undeclared':
        runtime.context_ids = runtime.context_ids | {kind}
    if kind == 'unrelated':
        tables['u.unrelated'] = dict(tables['u.events'])
        context = replace(context, grain='day', fact='u.unrelated', values=('u.unrelated.value',),
                          expression='sum(unrelated.value)', filters=())
    if kind == 'unreadable':
        context = replace(context, grain='day', joins=(Join('hidden', 'key', 'events.entity_id=hidden.entity_id',
                          None, None, 'u.events', 'u.hidden', True),))
    vocabulary = replace(vocabulary, semantics=runtime, tables=tables, contexts={**vocabulary.contexts, kind: context},
        entries={**vocabulary.entries, kind: Entry(kind, 'context', context.label, 'u')})
    text = 'gross accepted entries for April 2025'
    predicate, = [piece for piece in ledger(text, 'turn', vocabulary) if piece.kind == 'predicate']
    reply = Clarify(kind='clarify', spans=(predicate.span,), candidates=(Name(id=kind, evidence=(predicate.span,)),))
    answer = Planner(vocabulary, None, Hidden([], set(), [])).prepare(text, reply)
    assert answer['kind'] == 'abstain' and 'no grounded alternatives' in ' '.join(answer['missing'])


def test_physical_column_clarification_remains_available_outside_context_inference(monkeypatch):
    _, vocabulary = routing(monkeypatch)
    columns = ('u.events.value', 'u.events.entity_id')
    vocabulary = replace(vocabulary, phrases={**vocabulary.phrases, 'value column': columns})
    text = 'value column'
    piece, = ledger(text, 'turn', vocabulary)
    reply = Clarify(kind='clarify', spans=(piece.span,), candidates=tuple(Name(id=column, evidence=(piece.span,)) for column in columns))
    answer = Planner(vocabulary, None, Hidden([], set(), [])).prepare(text, reply)
    assert answer['kind'] == 'clarify' and {choice['id'] for choice in answer['choices']} == set(columns)


def test_a_source_and_context_alias_stays_ambiguous(monkeypatch):
    engine, vocabulary = routing(monkeypatch, source_alias=True)
    text = 'Side Metric for April 2025'
    piece, = [piece for piece in ledger(text, 'turn', vocabulary) if piece.text == 'Side Metric']
    assert not one_meaning(piece.candidates, vocabulary)
    reply = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind in ('clarify', 'abstain'), reply
