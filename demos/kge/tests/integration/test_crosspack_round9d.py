import csv
from dataclasses import replace
from decimal import Decimal

import pytest

from kge.semantics.compile import compile_plan
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import Engine, PRESETS, parse
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_specials import toy as toy
from tests.plan_fixtures import grounded, model, name
from tests.scratch import DEMO


@pytest.fixture
def thornbury(toy):
    engine = Engine.candidate(toy[0].at(toy[1]))
    yield engine
    engine.close()


def test_undeclared_group_word_does_not_widen_one_unit_reader(thornbury, monkeypatch):
    engine = thornbury
    assert not engine.SOURCE_SETS
    monkeypatch.setattr(engine.g, 'readable_units', lambda principal: {'underwriting'})
    requested = []
    original = engine.g.join_access
    def checked(principal, units):
        requested.extend(units)
        return original(principal, units)
    monkeypatch.setattr(engine.g, 'join_access', checked)
    answer = engine.answer('How many clients in the group were active in June 2025', 'actuary')
    assert answer['behaviour'] == 'abstain', answer
    assert requested == []
    assert not answer.get('sql_run') and not answer.get('figure')


def test_declared_premium_per_policy_variant_executes_with_currency(thornbury, monkeypatch):
    engine = thornbury
    monkeypatch.setitem(engine.semantics.entities, 'policy_entity', {'label': 'Policies', 'table': 'underwriting.policy', 'key': ['policy_no']})
    monkeypatch.delattr(engine.semantics, 'plan_snapshot', raising=False)
    text = 'Ratio of Gross written premium to count Policies for June 2025'
    req = parse(text, engine.term_names, engine)
    v = planrules.vocabulary(engine, 'actuary', 'analysis', ProductScope(engine, None), PRESETS['graph'], req)
    v = replace(v, measures={k: replace(spec, population='policy_entity') if k == 'gross-written-premium' else spec for k, spec in v.measures.items()})
    p = model(measures=[
        {'kind': 'measure', 'id': 'm', 'measure': name('gross-written-premium'), 'context': name('gross_written_premium'), 'mode': 'current', 'filters': []},
        {'kind': 'distinct_count', 'id': 'c', 'entity': name('policy_entity'), 'context': name('gross_written_premium'), 'mode': 'current', 'filters': []}],
        time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('calendar')}},
        derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'c', 'evidence': []}])
    p = grounded(p, text, v, {'Gross written premium': 'm', 'count': 'c', 'Policies': 'c', 'Ratio': 'r', 'June 2025': 'time'})
    checked = check_plan(p, v, text)
    assert isinstance(checked, CheckedPlan), checked
    assert next(col for col in compile_plan(checked).columns if col.id == 'r').currency == 'USD'
    result = Planner(v, engine.w, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
    assert result['kind'] == 'result', result
    rows = list(csv.DictReader((DEMO / 'clients/thornbury/fixture/data/underwriting/premium.csv').open()))
    selected = [r for r in rows if r['booked_on'].startswith('2025-06')]
    expected = sum(Decimal(r['written_premium_usd']) for r in selected) / len({r['policy_no'] for r in selected})
    assert Decimal(result['result']['rows'][0]['cells'][-1]['value']['value']) == expected
