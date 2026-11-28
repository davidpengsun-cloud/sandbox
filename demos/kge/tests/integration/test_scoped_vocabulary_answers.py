"""Equivalent planning inputs share builds without changing complete answers or their access scope."""
import json

import pytest

from kge.services.ask.engine import planrules, runtrace
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.scope import ProductScope


def words(engine, question, principal='analyst_enterprise', purpose='analysis', products=None, config='graph'):
    request = parse(question, engine.term_names, engine)
    return planrules.vocabulary(engine, principal, purpose, ProductScope(engine, products), PRESETS[config], request)


def test_questions_reuse_scoped_builds_without_sharing_principals_or_purposes(eng, monkeypatch):
    eng.g._scoped_vocabularies.clear()
    built = []
    build = planrules.build_vocabulary
    def counted(*args, **kwargs):
        built.append(True)
        return build(*args, **kwargs)
    monkeypatch.setattr(planrules, 'build_vocabulary', counted)
    first = words(eng, 'Total retail revenue for June 2025')
    second = words(eng, 'Total retail revenue for May 2025')
    assert first is second
    assert len(built) == 1
    other = words(eng, 'Total retail revenue for May 2025', principal='analyst_retail')
    assert other is not first
    assert all(e.unit in (None, 'retail') for e in other.entries.values())
    assert words(eng, 'Total retail revenue for June 2025', purpose='audit') is not first
    assert words(eng, 'Total retail revenue for June 2025', products=['retail.monthly_revenue']) is not first
    assert words(eng, 'Total retail revenue for June 2025', config='schema') is not first


def test_people_with_the_same_access_and_live_access_changes_have_distinct_bindings(eng, monkeypatch):
    monkeypatch.setattr(eng.g, 'readable_units', lambda _: {'retail', 'sme'})
    monkeypatch.setattr(eng.g, 'may', lambda *_: True)
    first = words(eng, 'Total retail revenue for June 2025', principal='one@example.com')
    second = words(eng, 'Total retail revenue for June 2025', principal='two@example.com')
    assert first is not second and first.binding != second.binding
    monkeypatch.setattr(eng.g, 'readable_units', lambda _: {'retail'})
    narrowed = words(eng, 'Total retail revenue for June 2025', principal='one@example.com')
    assert narrowed is not first and narrowed.binding != first.binding
    assert all(entry.unit in (None, 'retail') for entry in narrowed.entries.values())


def test_schema_planning_reuses_setup_but_keeps_aggregation_variants(eng):
    scope = ProductScope(eng, None)
    def control(question):
        req = parse(question, eng.term_names, eng)
        return planrules.control_vocabulary(eng, 'analyst_enterprise', 'analysis', scope, req)
    first = control('Total retail revenue for June 2025')
    assert control('Total retail revenue for May 2025') is first
    assert control('Average retail revenue for June 2025') is not first


@pytest.mark.parametrize('question,principal,products', [
    ('Total retail revenue for June 2025', 'analyst_enterprise', None),
    ('Total retail revenue for May 2025', 'analyst_enterprise', None),
    ('Average retail revenue for June 2025', 'analyst_enterprise', None),
    ('What is NNA?', 'analyst_enterprise', None),
    ('What was certified NNA for April 2025?', 'analyst_enterprise', None),
    ('How many active customers have a complaint in retail for June 2025?', 'analyst_enterprise', None),
    ('Total retail revenue by customer name for June 2025', 'analyst_enterprise', None),
    ('Total retail revenue for June 2025', 'analyst_retail', ['retail.monthly_revenue']),
    ('Total wealth revenue for June 2025', 'analyst_retail', None),
])
def test_cached_answers_traces_paths_labels_and_refusals_are_byte_identical(eng, monkeypatch, question, principal, products):
    init = runtrace.Trace.__init__
    def fixed_id(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.run_id = 'fixed-scoped-vocabulary-comparison'
        self.started_at = '2026-11-11T00:00:00+00:00'
        self.clock = lambda: 0
        self.t0 = 0
    monkeypatch.setattr(runtrace.Trace, '__init__', fixed_id)
    caller = Combined(eng, llm=None)
    eng.g._scoped_vocabularies.clear()
    def answer():
        return caller.ask(question, principal, config=PRESETS['graph'], products=products)
    before = answer()
    after = answer()
    with monkeypatch.context() as patch:
        patch.setattr(eng.g, 'scoped_vocabulary', lambda binding, variant, writes, read: read())
        uncached = answer()
    encoded = [json.dumps(result, ensure_ascii=False).encode() for result in (before, after, uncached)]
    assert encoded[0] == encoded[1] == encoded[2]
