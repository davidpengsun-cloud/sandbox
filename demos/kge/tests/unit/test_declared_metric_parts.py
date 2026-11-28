"""Declared formula inputs retain their totals beside a named group selection."""
from decimal import Decimal

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import build_vocabulary
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS, parse
from tests.unit.test_measure_capability_figures import declarations, ROWS
from tests.unit.test_plan_compiler import connection


def test_net_and_named_ratio_keep_the_declared_input_totals(monkeypatch):
    engine, base = declarations(monkeypatch)
    sem = engine.semantics
    sem.d.VOCABULARY.extend({'kind': 'predicate', 'params': {
        'column': 'u.events.category', 'phrases': ['Group ' + value], 'op': 'eq', 'value': value}} for value in 'AB')
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    engine.FILTERS, engine.FILTER_COLUMNS = words.filters, words.columns
    vocabulary = build_vocabulary(sem, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), ('Units', 'units'), *sem.d.GROUP_BY.items()])
    question = 'Metric Group A, Group B and net with Margin'
    reply = planrules.read(engine, parse(question, engine.term_names, engine), vocabulary, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, vocabulary, question)
    assert isinstance(checked, CheckedPlan), checked
    statement = compile_plan(checked).answer
    assert check(statement.sql, statement.allow) == []
    with connection(ROWS) as warehouse:
        cursor = warehouse.execute(statement.sql)
        values = dict(zip((column[0] for column in cursor.description), cursor.fetchone(), strict=True))
    assert {values[measure.id] for measure in reply.plan.measures if measure.context.id == 'ctx'} == {Decimal(16)}
    assert {values[measure.id] for measure in reply.plan.measures if measure.context.id == 'units'} == {8}
    assert values[reply.plan.derived[0].id] == 2
