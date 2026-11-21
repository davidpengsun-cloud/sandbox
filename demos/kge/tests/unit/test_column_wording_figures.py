"""Lookup wordings reference declared columns without choosing another metric."""
from decimal import Decimal

from kge.compiler.vocabulary import alias_entries
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import build_vocabulary
from kge.services.ask.engine.core import parse, PRESETS
from kge.services.ask.engine import planrules
from tests.unit.test_declared_metric_gates import declared, execute


def wording_vocabulary(engine, base, column):
    rows = alias_entries({'aliases': [{'phrase': 'produced', 'column_wording': column}]})
    return build_vocabulary(engine.semantics, base.binding, ['u'], ['u'], list(base.measures.values()),
        aliases=[('Metric', 'ctx'), *((row['phrase'], row['column_wording']) for row in rows)])


def test_a_column_wording_keeps_the_named_metric_and_its_hand_computed_total(monkeypatch):
    engine, base = declared(monkeypatch)
    vocabulary = wording_vocabulary(engine, base, 'u.events.value')
    question = 'Metric produced'
    assert parse(question, engine.term_names, engine).terms_found == ['Metric']
    reply = planrules.read(engine, parse(question, engine.term_names, engine), vocabulary, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert {measure.context.id for measure in reply.plan.measures} == {'ctx'}
    result, = execute(reply.plan, question, vocabulary)
    assert result[reply.plan.measures[0].id] == Decimal(16)


def test_a_column_wording_grants_no_restricted_column_read(monkeypatch):
    engine, base = declared(monkeypatch)
    vocabulary = wording_vocabulary(engine, base, 'u.events.private')
    question = 'Metric produced'
    reply = planrules.read(engine, parse(question, engine.term_names, engine), vocabulary, PRESETS['graph'])
    assert reply.kind != 'propose' or not isinstance(check_plan(reply.plan, vocabulary, question), CheckedPlan)
