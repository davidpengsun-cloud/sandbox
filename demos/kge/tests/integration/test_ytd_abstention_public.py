from dataclasses import replace

import pytest

from kge.semantics.planvocabulary import Entry
from kge.services.ask.engine import planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.wording import template
from tests.unit.test_plan_ytd_abstention import Recording, assert_no_figure


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('phrase', ['plan', 'budget', 'target', 'total client assets'])
def test_public_ask_offers_declared_measures_for_undeclared_figures(eng, configured, phrase):
    answer = Combined(eng, llm=Recording() if configured else None).ask('Show ' + phrase, 'analyst_retail')
    assert_no_figure(answer)
    assert 'Declared measures here are' in answer['reason'] and 'Which would you like?' in answer['reason']
    assert 'Revenue' in template(answer, eng)
    assert 'NNA' not in answer['reason']


@pytest.mark.parametrize('configured', [False, True])
def test_public_ask_asks_which_declared_meaning(eng, monkeypatch, configured):
    original = planrules.vocabulary
    def ambiguous(*args, **kwargs):
        v = original(*args, **kwargs)
        column = next(e for e in v.entries.values() if e.kind == 'column')
        entries = {i: Entry(i, 'column', label, column.unit, column=column.column)
                   for i, label in [('first', 'First grouping'), ('second', 'Second grouping')]}
        return replace(v, entries={**v.entries, **entries}, phrases={**v.phrases, 'shared meaning': ('first', 'second')})
    monkeypatch.setattr(planrules, 'vocabulary', ambiguous)
    monkeypatch.setattr(planrules, 'declared_vocabulary', ambiguous)
    def reply(turn):
        from kge.semantics.plan import Clarify, Name, Span
        return Clarify(kind='clarify', spans=(Span(turn=turn, start=0, end=14),),
                       candidates=(Name(id='first', evidence=()), Name(id='second', evidence=())))
    answer = Combined(eng, llm=Recording(reply) if configured else None).ask('Shared meaning', 'analyst_retail')
    assert_no_figure(answer)
    assert answer['behaviour'] == 'clarify'
    assert all(label in template(answer, eng) for label in ('First grouping', 'Second grouping'))


def test_public_rules_and_model_keep_their_own_period_failure_reason(eng):
    question = 'Retail revenue since June 2025'
    rules = Combined(eng, llm=None).ask(question, 'analyst_retail')
    recording = Recording()
    model = Combined(eng, llm=recording).ask(question, 'analyst_retail',
        config=PRESETS['graph'].with_stages(resolution='glossary+model', renderer='template'))
    for answer in (rules, model):
        assert_no_figure(answer)
    assert 'parser can place' in rules['reason']
    assert 'valid query plan' in model['reason'] and 'parser can place' not in model['reason']
    assert recording.calls == 2


@pytest.mark.parametrize('question,expected', [
    ('Show plan', [(2, 0), (0, 0)]),
    ('Explain how to bake sourdough bread.', [(0, 3), (0, 2)]),
])
def test_metadata_guidance_does_not_add_native_bigquery_jobs(monkeypatch, tmp_path, question, expected):
    from tests.integration.test_planner_request_jobs import pilot, budgets
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        measured = []
        for _ in range(2):
            fake.requests.clear()
            answer = Combined(engine, llm=None).ask(question, 'analyst_retail')
            assert_no_figure(answer)
            count = budgets(fake)
            assert count['warehouse'] == count['dry'] == count['catalogue'] == 0
            measured.append((count['graph'], count['retrieval']))
        assert measured == expected
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
