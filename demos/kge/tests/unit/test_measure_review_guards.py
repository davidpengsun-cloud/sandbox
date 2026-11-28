"""Independent numerical and grounding regressions for declared measure variants."""
import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
import sqlglot
from sqlglot import exp

from kge.compiler.vocabulary import AGGREGATION_CHOICES, measure_entries
from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import build_vocabulary
from kge.services.ask.engine.planner import result_of
from kge.services.ask.engine.planmodel import payload
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import declared_metrics, model, monthly_time, name, semantics, time_dimension, metric_vocabulary as vocabulary
from tests.unit.test_measure_capability_figures import ROWS, declarations, formula_plan, measured
from tests.unit.test_plan_compiler import connection


NONADDITIVE = [('avg', 'average'), ('min', 'minimum'), ('max', 'maximum'), ('median', 'median')]


def test_prompt_schema_exposes_only_the_optional_measure_capabilities_the_pack_declares():
    from tests.plan_fixtures import vocabulary as minimal_vocabulary
    legacy = payload('Metric', minimal_vocabulary())['schema']['$defs']
    assert 'aggregation' not in legacy['PlanMeasure']['properties']
    assert 'declaration' not in legacy['Arithmetic']['properties']
    _, vocab = declarations()
    declared = payload('Metric', vocab)['schema']['$defs']
    assert 'aggregation' in declared['PlanMeasure']['properties']
    assert 'declaration' in declared['Arithmetic']['properties']


def test_schema_only_inference_cannot_certify_new_aggregation_words():
    sem = semantics()
    sem.measure_ids = frozenset()
    vocab = vocabulary(sem)
    assert any(piece.kind == 'unknown' and piece.text == 'average' for piece in ledger('average Metric', 'turn', vocab))
    _, declared = declarations()
    assert any(piece.operation == 'avg' for piece in ledger('average Metric', 'turn', declared))


def test_projection_gate_keeps_composite_count_encoding_and_refuses_a_changed_key():
    plan = model(measures=[model().measures[0].model_dump(), dict(kind='distinct_count', id='c', entity=name('entity'),
                  context=name('ctx'), mode='current', filters=[])])
    checked = validate(plan, 'Metric and count Entities', vocabulary())
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    written = rewrite_sql(compiled.answer.sql, lambda unit: '')
    assert check(written, compiled.answer.allow, 'bigquery') == []
    tree = sqlglot.parse_one(written, read='bigquery')
    changed = next(c for c in tree.find_all(exp.Column) if c.name == 'entity_id')
    changed.set('this', exp.to_identifier('value'))
    assert check(tree.sql('bigquery'), compiled.answer.allow, 'bigquery')


def validate(plan, text, vocab):
    return check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text)


@pytest.mark.parametrize('aggregation,word', NONADDITIVE)
def test_nonadditive_variants_never_sum_their_group_values_into_a_public_total(aggregation, word):
    _, vocab = declarations()
    data = measured(aggregation).model_dump(mode='json', by_alias=True)
    data['dimensions'] = [dict(kind='column', id='category', bindings=[
        dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])]
    checked = validate(QueryPlan.model_validate(data), word + ' Metric by category', vocab)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(ROWS) as con:
        rows = con.execute(compiled.answer.sql).fetchall()
    result = result_of(compiled, rows, {})
    assert len(result.rows) == 2
    assert not any(r.total for r in result.rows)


def released_vocabulary():
    sem = semantics(release=True)
    sem.d.VOCABULARY = measure_entries({'measures': [dict(id='u.metric', aggregations=list(AGGREGATION_CHOICES))]})
    return vocabulary(sem, aliases=[('Metric', 'ctx')])


def test_a_default_average_with_declared_choices_keeps_empty_released_months_null():
    sem = semantics(release=True)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], aggregation='avg', expression='avg(events.value)', grain='day')
    sem.d.VOCABULARY = measure_entries({'measures': [dict(id='u.metric', aggregations=['avg', 'sum'])]})
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    plan = model(time=monthly_time(), dimensions=[time_dimension()])
    text = 'Metric by month for 2025-04 through 2025-06'
    checked = validate(plan, text, vocab)
    assert isinstance(checked, CheckedPlan), checked
    pending = compile_plan(checked)
    with connection(ROWS) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        for month, day in ((4, 30), (5, 31), (6, 30)):
            date = dt.date(2025, month, day)
            con.execute('INSERT INTO calendar_rows VALUES (?, ?, ?, TRUE)', [date, date, date])
        assert check(pending.release.sql, pending.release.allow) == []
        compiled = compile_plan(checked, con.execute(pending.release.sql).fetchall())
        assert check(compiled.answer.sql, compiled.answer.allow) == []
        rows = con.execute(compiled.answer.sql).fetchall()
    assert [row[1] for row in rows] == [pytest.approx(16 / 3), None, None]


def test_a_declared_difference_can_combine_two_declared_statistics_with_the_same_units():
    eng, previous = declarations(formula='difference')
    sem = eng.semantics
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], aggregation='avg', expression='avg(events.value)', grain='day')
    vocab = build_vocabulary(declared_metrics(sem), previous.binding, ['u'], ['u'], list(sem.measure_specs.values()),
                             aliases=[('Metric', 'ctx'), ('Units', 'units')])
    checked = validate(formula_plan('difference'), 'Margin', vocab)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(ROWS) as con:
        actual = con.execute(compiled.answer.sql).fetchone()[2]
    assert actual == pytest.approx(16 / 3 - 8)


@pytest.mark.parametrize('aggregation,word', NONADDITIVE)
@pytest.mark.parametrize('grouped', [False, True])
def test_empty_released_observations_leave_nonadditive_aggregates_null(aggregation, word, grouped):
    vocab = released_vocabulary()
    data = measured(aggregation).model_dump(mode='json', by_alias=True)
    data['time'] = monthly_time()
    if grouped:
        data['dimensions'] = [time_dimension()]
    text = word + ' Metric' + (' by month' if grouped else '') + ' for 2025-04 through 2025-06'
    checked = validate(QueryPlan.model_validate(data), text, vocab)
    assert isinstance(checked, CheckedPlan), checked
    pending = compile_plan(checked)
    rows = [(dt.date(2025, month, 10), value, 'A', 1, 'x') for month, value in ((4, 2), (6, 10))] if grouped else []
    with connection(rows) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        for month, day in ((4, 30), (5, 31), (6, 30)):
            date = dt.date(2025, month, day)
            con.execute('INSERT INTO calendar_rows VALUES (?, ?, ?, TRUE)', [date, date, date])
        assert check(pending.release.sql, pending.release.allow) == []
        release_rows = con.execute(pending.release.sql).fetchall()
        compiled = compile_plan(checked, release_rows)
        assert check(compiled.answer.sql, compiled.answer.allow) == []
        actual = con.execute(compiled.answer.sql).fetchall()
    if grouped:
        assert [r[1] for r in actual] == [2, None, 10]
    else:
        assert actual[0][0] is None


@pytest.mark.parametrize('aggregation,word', NONADDITIVE)
def test_a_running_total_cannot_sum_a_nonadditive_variant(aggregation, word):
    _, vocab = declarations()
    data = measured(aggregation).model_dump(mode='json', by_alias=True)
    data.update(time=monthly_time(), dimensions=[time_dimension()],
                derived=[dict(kind='running_total', id='running', input='m', partition_by=[], order_by='t', evidence=[])])
    checked = validate(QueryPlan.model_validate(data), word + ' Metric by month for 2025-04 through 2025-06 running total', vocab)
    assert not isinstance(checked, CheckedPlan)
    assert 'additive' in ' '.join(checked.missing)


def test_count_variant_cannot_be_used_as_a_currency_amount_in_a_ratio():
    _, vocab = declarations()
    data = formula_plan().model_dump(mode='json', by_alias=True)
    del data['derived'][0]['declaration']
    data['measures'][0]['aggregation'] = 'count'
    checked = validate(QueryPlan.model_validate(data), 'ratio of count Metric to Units', vocab)
    assert not isinstance(checked, CheckedPlan)
    assert 'declared input aggregations' in ' '.join(checked.missing)


def test_a_named_formula_cannot_be_answered_by_only_one_raw_operand():
    _, vocab = declarations()
    checked = validate(model(), 'Margin', vocab)
    assert not isinstance(checked, CheckedPlan)
    assert 'formula output' in ' '.join(checked.missing)


def ambiguous_vocabulary():
    eng, previous = declarations()
    sem = eng.semantics
    sem.contexts['alternate'] = replace(sem.contexts['units'], id='alternate', label='Alternative units',
                                         term='Alternative units', grain=sem.contexts['units'].grain)
    vocab = build_vocabulary(declared_metrics(sem), previous.binding, ['u'], ['u'], list(sem.measure_specs.values()),
                             aliases=[('Metric', 'ctx'), ('Unit total', 'units'), ('Alternative units', 'alternate')])
    return vocab


def test_a_model_cannot_silently_choose_between_contexts_of_a_formula_operand():
    checked = validate(formula_plan(), 'Margin', ambiguous_vocabulary())
    assert not isinstance(checked, CheckedPlan)
    assert 'explicit context' in ' '.join(checked.missing)


def test_a_formula_operand_context_can_be_selected_explicitly():
    checked = validate(formula_plan(), 'Margin for Unit total', ambiguous_vocabulary())
    assert isinstance(checked, CheckedPlan), checked


@pytest.mark.parametrize('aggregation,replacement', [('min', 'max'), ('max', 'min'), ('median', 'max')])
def test_gate_protects_a_new_default_aggregation_even_without_an_explicit_question_operator(aggregation, replacement):
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], expression=aggregation + '(events.value)',
                                   aggregation=aggregation, grain='day')
    vocab = vocabulary(sem)
    checked = validate(model(), 'Metric', vocab)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    tampered = compiled.answer.sql.replace(aggregation.upper() + '(', replacement.upper() + '(')
    assert tampered != compiled.answer.sql
    assert check(tampered, compiled.answer.allow)


def test_a_declared_amount_per_count_formula_uses_a_unitless_default_count():
    eng, previous = declarations()
    sem = eng.semantics
    sem.contexts['units'] = replace(sem.contexts['units'], expression='count(events.entity_id)', aggregation='count', grain='day')
    specs = [replace(spec, currency='XXX') if spec.id == 'u.units' else spec for spec in sem.measure_specs.values()]
    vocab = build_vocabulary(declared_metrics(sem), previous.binding, ['u'], ['u'], specs, aliases=[('Metric', 'ctx'), ('Units', 'units')])
    checked = validate(formula_plan(), 'Margin', vocab)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(ROWS) as con:
        actual = con.execute(compiled.answer.sql).fetchone()
    assert Decimal(str(actual[2])) == pytest.approx(Decimal(16) / 3)
    assert compiled.columns[1].currency is None
    assert compiled.columns[2].currency == 'USD'


@pytest.mark.parametrize('aggregation', ['min', 'max', 'median'])
def test_sum_does_not_silently_use_a_different_default_only_aggregation(monkeypatch, aggregation):
    eng, previous = declarations(monkeypatch)
    sem = eng.semantics
    sem.d.VOCABULARY = [r for r in sem.d.VOCABULARY if r['kind'] != 'measure_options']
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], aggregation=aggregation,
                                   expression=aggregation + '(events.value)', grain='day')
    vocab = build_vocabulary(declared_metrics(sem), previous.binding, ['u'], ['u'], list(sem.measure_specs.values()),
                             aliases=[('Metric', 'ctx'), ('Units', 'units')])
    text = 'sum Metric'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'abstain', reply
    checked = validate(model(), text, vocab)
    assert not isinstance(checked, CheckedPlan)
