"""Movable mode qualifiers come from declared phrase and noun pairs."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Rule
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse, qualified_phrases
from tests.plan_fixtures import semantics, metric_vocabulary
from tests.unit.test_wording_concepts import without_evidence
from tests.unit.test_measure_capability_figures import ROWS
from tests.unit.test_plan_compiler import connection


@pytest.mark.parametrize('prefix,noun', [('Certified', 'NNA'), ('Stored', 'Metric'), ('Ledger approved', 'Metric')])
@pytest.mark.parametrize('between', ['June 2025', 'Group A'])
def test_only_declared_mode_prefixes_move_over_a_period_or_member(prefix, noun, between):
    phrase = prefix + ' ' + noun
    question = prefix + ' ' + between + ' ' + noun
    assert qualified_phrases(question, (phrase, noun), (prefix,)) == ((0, len(prefix), phrase),)
    assert qualified_phrases(question, (phrase, noun)) == ()
    assert qualified_phrases(question, (phrase,), (prefix,)) == ()


def test_source_prefixes_do_not_become_movable_mode_qualifiers():
    phrases = ('Retail NNA', 'Wealth NNA', 'NNA', 'Stored NNA')
    assert qualified_phrases('Retail and Wealth NNA', phrases, ('Stored',)) == ()


@pytest.mark.parametrize('question', ['Metric, Stored, for June 2025', 'Metric for June 2025 Stored',
                                      'Metric by Group A for June 2025, Stored'])
def test_a_mode_qualifier_after_its_noun_qualifies_it(question):
    phrases = ('Stored Metric', 'Metric')
    start = question.index('Stored')
    assert qualified_phrases(question, phrases, ('Stored',)) == ((start, start + len('Stored'), 'Stored Metric'),)
    assert qualified_phrases(question, phrases, ('Stored',), nouns=True) == (
        (start, start + len('Stored'), 'Stored Metric', 0, len('Metric')),)
    assert qualified_phrases(question, phrases) == ()


@pytest.mark.parametrize('question', ['Metric current versus Stored for June 2025', 'current Metric versus Stored',
                                      'Scoped Metric for June 2025, Stored'])
def test_a_mode_qualifier_after_its_noun_holds_back_beside_another_mode_or_a_longer_measure(question):
    assert qualified_phrases(question, ('Stored Metric', 'Scoped Metric', 'Metric'), ('Stored', 'current')) == ()


@pytest.mark.parametrize('between', ['', 'April 2025 '])
def test_a_qualifier_never_reads_a_noun_inside_a_longer_declared_measure(between):
    assert qualified_phrases('Stored ' + between + 'Scoped Metric',
        ('Stored Metric', 'Scoped Metric', 'Metric'), ('Stored',)) == ()


def test_mode_wordings_preserve_the_longer_measure_plan_and_figures(monkeypatch):
    sem = semantics(release=True)
    sem.contexts['stored_context'] = replace(sem.contexts['ctx'], grain='day', id='stored_context', label='Stored Metric', term='Stored Metric')
    sem.contexts['scoped'] = replace(sem.contexts['ctx'], grain='day', id='scoped', label='Scoped Metric', term='Scoped Metric',
        filters=(*sem.contexts['ctx'].filters, Rule('category', '', "events.category='A'", 'u.events')))
    sem.d.TIME_MODES = {'stored': 'as_reported', 'published': 'as_reported'}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {'metric': 'Metric', 'scoped metric': 'Scoped Metric'}, {}, {}
    sem.measure_declarations = {}
    vocabulary = metric_vocabulary(sem, aliases=[('Metric', 'ctx'), ('Stored Metric', 'stored_context'), ('Scoped Metric', 'scoped')])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric', 'Stored Metric', 'Scoped Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    with connection(ROWS) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-04-30', '2025-04-15', '2025-04-15', TRUE)")
        def answer(text):
            reply = planrules.read(engine, parse(text, engine.term_names, engine), vocabulary,
                                   SimpleNamespace(graph=True, glossary=True))
            assert reply.kind == 'propose', reply
            checked = check_plan(reply.plan, vocabulary, text)
            assert isinstance(checked, CheckedPlan), checked
            compiled = compile_plan(checked)
            if compiled.release is not None:
                assert check(compiled.release.sql, compiled.release.allow) == []
                compiled = compile_plan(checked, con.execute(compiled.release.sql).fetchall())
            assert compiled.answer is not None and check(compiled.answer.sql, compiled.answer.allow) == []
            return reply.plan, compiled.answer.sql, con.execute(compiled.answer.sql).fetchone()[0]
        answers = []
        for mode in ('Stored', 'Published'):
            for question in (mode + ' Scoped Metric for April 2025', mode + ' April 2025 Scoped Metric'):
                plan, statement, figure = answer(question)
                measure, = plan.measures
                assert measure.context.id == 'scoped' and measure.mode == 'as_reported'
                assert figure == 6
                answers.append((without_evidence(plan.model_dump(mode='json', by_alias=True)), statement, figure))
        assert all(item == answers[0] for item in answers)
        plan, _, figure = answer('Stored Metric for April 2025')
        measure, = plan.measures
        assert measure.context.id == 'stored_context' and measure.mode == 'current' and figure == 16


@pytest.mark.parametrize('boundary', ['live basis', 'compared with', 'versus', 'against'])
def test_a_declared_counterpart_or_comparison_stops_qualifying_the_later_noun(boundary):
    sem = semantics()
    sem.contexts['stored'] = replace(sem.contexts['ctx'], grain='day', id='stored', label='Stored Metric', term='Stored Metric')
    sem.d.TIME_MODES = {'stored': 'as_reported', 'live basis': 'restated'}
    vocabulary = metric_vocabulary(sem, aliases=[('Metric', 'ctx'), ('Stored Metric', 'stored')])
    pieces = ledger('Stored ' + boundary + ' Metric', 'turn', vocabulary)
    noun = next(piece for piece in pieces if piece.text == 'Metric')
    assert 'stored' not in noun.candidates and 'ctx' in noun.candidates


@pytest.mark.parametrize('prefix', ['Certified', 'Stored', 'Ledger approved'])
@pytest.mark.parametrize('alias', [False, True])
def test_declared_mode_qualifiers_keep_the_canonical_plan_across_a_period(monkeypatch, prefix, alias):
    sem = semantics()
    phrase = prefix + ' Metric'
    sem.contexts['qualified'] = replace(sem.contexts['ctx'], grain='day', id='qualified', label=phrase, term=phrase)
    sem.d.ALIASES = {'metric': 'Metric', **({phrase.casefold(): phrase} if alias else {})}
    sem.d.UNIT_WORDS, sem.d.GROUP_BY = {}, {}
    sem.d.TIME_MODES = {prefix.casefold(): 'as_reported'}
    sem.measure_declarations = {}
    vocabulary = metric_vocabulary(sem, aliases=[(phrase, 'qualified')])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric', phrase])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    plans, statements = [], []
    for text in (phrase + ' for June 2025', prefix + ' for June 2025 Metric'):
        assert not [p.text for p in ledger(text, 'turn', vocabulary) if p.kind == 'unknown']
        request = parse(text, engine.term_names, engine)
        assert phrase in request.terms_found
        reply = planrules.read(engine, request, vocabulary, SimpleNamespace(graph=True, glossary=True))
        assert reply.kind == 'propose', reply
        measure, = reply.plan.measures
        assert measure.context.id == 'qualified' and measure.mode == 'current'
        checked = check_plan(reply.plan, vocabulary, text)
        assert isinstance(checked, CheckedPlan), checked
        compiled = compile_plan(checked)
        assert compiled.answer is not None and check(compiled.answer.sql, compiled.answer.allow) == []
        plans.append(without_evidence(reply.plan.model_dump(mode='json', by_alias=True)))
        statements.append(compiled.answer.sql)
    assert plans[0] == plans[1] and statements[0] == statements[1]
