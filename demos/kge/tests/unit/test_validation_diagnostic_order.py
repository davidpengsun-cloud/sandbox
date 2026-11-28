"""New declaration and lexical checks retain existing coverage, role and period diagnostics."""
from copy import copy
from types import SimpleNamespace

import pytest

from kge.semantics.coverage import ground_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name, semantics, vocabulary


def count_plan():
    return model(measures=[{'kind': 'distinct_count', 'id': 'm', 'entity': name('entity'),
                           'context': name('ctx'), 'mode': 'current', 'filters': []}])


@pytest.mark.parametrize('text,missing', [('number of Entities active', ('active',)),
                                       ('Metric active', ('active', 'unrequested count m'))])
def test_unresolved_coverage_precedes_the_missing_count_declaration(text, missing):
    scoped = vocabulary(aliases=[('Metric', 'ctx')])
    plan = ground_plan(count_plan(), text, 'turn', scoped)
    decision = check_plan(plan, scoped, text)
    assert not isinstance(decision, CheckedPlan)
    assert decision.missing == missing


def test_an_otherwise_complete_undeclared_count_still_has_its_explicit_refusal():
    scoped = vocabulary(aliases=[('Metric', 'ctx')])
    text = 'number of Entities'
    decision = check_plan(ground_plan(count_plan(), text, 'turn', scoped), scoped, text)
    assert not isinstance(decision, CheckedPlan)
    assert decision.missing == ('No unambiguous distinct-count metric is declared for this entity and population.',)


@pytest.mark.parametrize('raw,period_binding,expected', [
    (True, False, 'A predicate on u.archive.category has no declared role for every selected context.'),
    (True, True, 'A predicate on u.archive.category has no declared role for every selected context.'),
    (False, False, 'missing compatible period binding'),
    (False, True, 'A requested predicate needs one declared readable column and an unambiguous role.'),
])
def test_raw_roles_and_new_lexical_period_failures_keep_their_precedence(monkeypatch, raw, period_binding, expected):
    sem = semantics()
    sem.tables['u.archive'] = {'category': 'VARCHAR'}
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {'metric': 'Metric'}, {}, {}
    row = {'kind': 'predicate', 'params': {'phrases': ['archive entries'], 'column': 'u.archive.category', 'op': 'eq', 'value': 'A'}}
    sem.d.VOCABULARY = [row]
    words = compiled([row])
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    if not period_binding:
        sem.d.PERIODS = {}
    scoped = vocabulary(sem, aliases=[('Metric', 'ctx')])
    domain = copy(sem.d)
    if not raw:
        domain.FILTERS, domain.FILTER_COLUMNS = (), ()
    engine = SimpleNamespace(**vars(domain), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    text = 'Metric for archive entries in April 2025'
    request = parse(text, engine.term_names, engine)
    assert bool(request.filters) == raw and request.period is not None
    reply = planrules.read(engine, request, scoped, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'abstain' and reply.missing == (expected,)
