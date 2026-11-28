"""Pack-selected aggregates and formulas checked against hand-computed small inputs."""
import datetime as dt
import re
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.compiler.vocabulary import AGGREGATION_CHOICES, measure_entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import Binding, Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name, semantics
from tests.unit.test_plan_compiler import connection


ROWS = [(dt.date(2025, 4, 10), value, category, units, 'x')
        for value, category, units in ((2, 'A', 2), (4, 'A', 4), (10, 'B', 2), (None, 'B', None), (-7, 'B', 100))]
AGGREGATES = [('sum', 'sum', Decimal(16)), ('count', 'count', Decimal(3)),
              ('average', 'avg', Decimal(16) / 3), ('mean', 'avg', Decimal(16) / 3),
              ('minimum', 'min', Decimal(2)), ('smallest', 'min', Decimal(2)), ('lowest', 'min', Decimal(2)),
              ('maximum', 'max', Decimal(10)), ('largest', 'max', Decimal(10)), ('highest', 'max', Decimal(10)),
              ('median', 'median', Decimal(4))]


class SuppliedPlan:
    def __init__(self, plan):
        self.plan = plan

    def json(self, prompt):
        return Propose(kind='propose', base_turn=None, plan=self.plan).model_dump(mode='json', by_alias=True)


def declarations(monkeypatch=None, choices=AGGREGATION_CHOICES, formula='ratio'):
    sem = semantics()
    sem.contexts['units'] = replace(sem.contexts['ctx'], id='units', label='Unit total', term='Units', grain='day',
                                    measure='u.units', expression='sum(events.entity_id)', values=('u.events.entity_id',))
    sem.d.GROUP_BY = {'by category': 'u.events.category'}
    sem.d.ALIASES = {'metric': 'Metric', 'units': 'Units', 'margin': 'Margin'}
    sem.d.UNIT_WORDS = {}
    sem.d.INTENTS = {'count': re.compile(r'\bcount\b', re.I), 'avg': re.compile(r'\b(?:average|mean)\b', re.I)}
    pack = {'glossary': [{'id': 'margin', 'name': 'Margin'}],
            'measures': [{'id': 'u.metric', 'aggregations': list(choices)}, {'id': 'u.units'}],
            'derived_measures': [{'id': 'u.margin', 'term': 'margin',
                                  'formula': {'kind': formula, 'numerator': 'u.metric', 'denominator': 'u.units'}}]}
    sem.d.VOCABULARY = measure_entries(pack)
    words = lexical_vocabulary(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    specs = [MeasureSpec(i, 'USD', 'entities', True) for i in ('u.metric', 'u.units')]
    sem.measure_specs = {s.id: s for s in specs}
    sem.measure_declarations = {'u.metric': {}, 'u.units': {}}
    sem.output_metadata = {}
    vocab = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                             ['u'], ['u'], specs, aliases=[('Metric', 'ctx'), ('Units', 'units'), *sem.d.GROUP_BY.items()])
    eng = SimpleNamespace(semantics=sem, term_names=['Metric', 'Units', 'Margin'], FISCAL_START=4, **vars(sem.d))
    if monkeypatch is not None:
        monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    return eng, vocab


def checked(plan, text, vocab):
    proposed = ground_plan(plan, text, 'turn', vocab)
    replay = planmodel.read(SuppliedPlan(proposed), text, vocab)
    assert replay.kind == 'propose', replay
    result = check_plan(replay.plan, vocab, text)
    assert isinstance(result, CheckedPlan), result
    return result


def compiled(plan, text, vocab):
    result = compile_plan(checked(plan, text, vocab))
    assert result.answer is not None
    assert check(result.answer.sql, result.answer.allow) == []
    return result


def measured(aggregation):
    data = model().model_dump(mode='json', by_alias=True)
    data['measures'][0]['aggregation'] = aggregation
    return QueryPlan.model_validate(data)


def formula_plan(kind='ratio'):
    data = model().model_dump(mode='json', by_alias=True)
    data['measures'].append(dict(kind='measure', id='units', measure=name('u.units'), context=name('units'), mode='current', filters=[]))
    data['derived'] = [dict(kind=kind, id='margin', numerator='m', denominator='units', declaration=name('u.margin'), evidence=[])]
    return QueryPlan.model_validate(data)


@pytest.mark.parametrize('word,aggregation,expected', AGGREGATES)
def test_pack_aggregations_have_hand_computed_figures(word, aggregation, expected):
    _, vocab = declarations()
    result = compiled(measured(aggregation), word + ' Metric', vocab)
    with connection(ROWS) as con:
        actual = con.execute(result.answer.sql).fetchone()[0]
    assert Decimal(str(actual)) == pytest.approx(expected)
    assert result.columns[0].type == ('integer' if aggregation == 'count' else 'decimal')
    assert result.columns[0].currency == (None if aggregation == 'count' else 'USD')


@pytest.mark.parametrize('word,aggregation,expected', AGGREGATES)
def test_rules_select_declared_aggregation_and_model_accepts_it(monkeypatch, word, aggregation, expected):
    eng, vocab = declarations(monkeypatch)
    text = word + ' Metric'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    output = reply.plan.measures[0]
    assert (output.aggregation or vocab.contexts[output.context.id].aggregation) == aggregation
    result = compiled(reply.plan, text, vocab)
    with connection(ROWS) as con:
        assert Decimal(str(con.execute(result.answer.sql).fetchone()[0])) == pytest.approx(expected)


@pytest.mark.parametrize('text,operation,expected', [
    ('count Metric and sum Units', 'count', 3),
    ('sum Units and count Metric', 'count', 3),
    ('average Metric and sum Units', 'avg', Decimal(16) / 3),
    ('sum Units and median Metric', 'median', 4),
])
def test_each_output_owns_its_aggregation(monkeypatch, text, operation, expected):
    eng, vocab = declarations(monkeypatch)
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    outputs = {m.context.id: m for m in reply.plan.measures}
    assert outputs['ctx'].aggregation == operation
    assert (outputs['units'].aggregation or vocab.contexts['units'].aggregation) == 'sum'
    result = compiled(reply.plan, text, vocab)
    with connection(ROWS) as con:
        cursor = con.execute(result.answer.sql)
        actual = dict(zip([c[0] for c in cursor.description], cursor.fetchone(), strict=True))
    assert Decimal(str(actual[outputs['ctx'].id])) == pytest.approx(Decimal(expected))
    assert actual[outputs['units'].id] == 8
    wrong = reply.plan.model_copy(update={'measures': tuple(m.model_copy(update={'aggregation': 'sum'})
                                                           if m.context.id == 'ctx' else m for m in reply.plan.measures)})
    assert not isinstance(check_plan(ground_plan(wrong, text, 'turn', vocab), vocab, text), CheckedPlan)


def test_aggregation_ownership_distinguishes_contexts_of_the_same_measure(monkeypatch):
    eng, base = declarations(monkeypatch)
    sem = eng.semantics
    sem.contexts['other'] = replace(sem.contexts['ctx'], id='other', label='Other metric', term='Other metric', grain='day')
    eng.term_names.append('Other metric')
    vocab = build_vocabulary(sem, base.binding, ['u'], ['u'], list(base.measures.values()),
                             aliases=[('Metric', 'ctx'), ('Other metric', 'other')])
    text = 'average Metric and count Other metric'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    outputs = {m.context.id: m for m in reply.plan.measures}
    assert outputs['ctx'].aggregation == 'avg' and outputs['other'].aggregation == 'count'
    result = compiled(reply.plan, text, vocab)
    with connection(ROWS) as con:
        cursor = con.execute(result.answer.sql)
        actual = dict(zip([c[0] for c in cursor.description], cursor.fetchone(), strict=True))
    assert actual[outputs['ctx'].id] == pytest.approx(16 / 3)
    assert actual[outputs['other'].id] == 3


@pytest.mark.parametrize('aggregation', ('avg', 'min', 'max', 'median', 'count'))
def test_model_cannot_select_an_undeclared_aggregation(aggregation):
    _, vocab = declarations(choices=('sum',))
    word = {'avg': 'average', 'min': 'minimum', 'max': 'maximum'}.get(aggregation, aggregation)
    result = check_plan(ground_plan(measured(aggregation), word + ' Metric', 'turn', vocab), vocab, word + ' Metric')
    assert not isinstance(result, CheckedPlan)
    assert 'does not declare' in ' '.join(result.missing)


@pytest.mark.parametrize('word', ('average', 'mean', 'minimum', 'maximum', 'median'))
def test_rules_decline_an_undeclared_aggregation_plainly(monkeypatch, word):
    eng, vocab = declarations(monkeypatch, choices=('sum',))
    text = word + ' Metric'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'abstain', reply
    assert 'does not declare' in ' '.join(reply.missing)


@pytest.mark.parametrize('aggregation,word', [('avg', 'average'), ('min', 'minimum'), ('max', 'maximum'), ('median', 'median')])
def test_model_cannot_change_aggregation_without_question_evidence(aggregation, word):
    _, vocab = declarations()
    result = check_plan(ground_plan(measured(aggregation), 'Metric', 'turn', vocab), vocab, 'Metric')
    assert not isinstance(result, CheckedPlan)
    assert 'request' in ' '.join(result.missing)
    assert any(p.operation == aggregation for p in ledger(word + ' Metric', 'turn', vocab))


def test_sql_gate_refuses_replacing_a_declared_aggregate():
    _, vocab = declarations()
    result = compiled(measured('median'), 'median Metric', vocab)
    sql = result.answer.sql.replace('MEDIAN(', 'MAX(')
    assert sql != result.answer.sql
    assert check(sql, result.answer.allow)


@pytest.mark.parametrize('aggregation,word,first', [('sum', 'sum', 6), ('count', 'count', 2), ('avg', 'average', 3),
                                                   ('min', 'minimum', 2), ('max', 'maximum', 4), ('median', 'median', 3)])
def test_each_declared_aggregation_combines_with_a_breakdown(aggregation, word, first):
    _, vocab = declarations()
    data = measured(aggregation).model_dump(mode='json', by_alias=True)
    data['dimensions'] = [dict(kind='column', id='category', bindings=[
        dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])]
    result = compiled(QueryPlan.model_validate(data), word + ' Metric by category', vocab)
    with connection(ROWS) as con:
        rows = con.execute(result.answer.sql).fetchall()
    actual = {row[0]: row[1] for row in rows if row[0] is not None}
    assert actual == {'A': first, 'B': 1 if aggregation == 'count' else 10}


def test_changing_an_aggregate_preserves_the_declared_filter_expression():
    eng, _ = declarations()
    sem = eng.semantics
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day',
                                  expression="sum(events.value) FILTER (WHERE events.category = 'A')")
    vocab = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                             ['u'], ['u'], list(sem.measure_specs.values()), aliases=[('Metric', 'ctx')])
    result = compiled(measured('avg'), 'average Metric', vocab)
    with connection(ROWS) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 3


@pytest.mark.parametrize('kind,expected', [('ratio', Decimal(2)), ('difference', Decimal(8)), ('percent_change', Decimal(1))])
def test_pack_formulas_have_hand_computed_figures(kind, expected):
    _, vocab = declarations(formula=kind)
    result = compiled(formula_plan(kind), 'Margin', vocab)
    with connection(ROWS) as con:
        actual = con.execute(result.answer.sql).fetchone()
    assert actual[:3] == (Decimal(16), Decimal(8), expected)
    assert result.columns[2].kind == 'derived'
    assert {'ctx', 'units', 'u.metric', 'u.units'} <= set(result.columns[2].lineage)


@pytest.mark.parametrize('kind,expected', [('ratio', Decimal(2)), ('difference', Decimal(8)), ('percent_change', Decimal(1))])
def test_rules_expand_a_pack_formula_and_model_accepts_it(monkeypatch, kind, expected):
    eng, vocab = declarations(monkeypatch, formula=kind)
    text = 'Margin'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    output, = reply.plan.derived
    assert output.declaration.id == 'u.margin' and output.kind == kind
    result = compiled(reply.plan, text, vocab)
    with connection(ROWS) as con:
        assert con.execute(result.answer.sql).fetchone()[2] == expected


@pytest.mark.parametrize('change', ('operator', 'operands', 'declaration', 'aggregation'))
def test_model_cannot_replace_any_part_of_the_declared_formula(change):
    _, vocab = declarations()
    data = formula_plan().model_dump(mode='json', by_alias=True)
    if change == 'operator':
        data['derived'][0]['kind'] = 'difference'
    elif change == 'operands':
        data['derived'][0].update(numerator='units', denominator='m')
    elif change == 'declaration':
        data['derived'][0]['declaration'] = name('u.unknown')
    else:
        data['measures'][0]['aggregation'] = 'avg'
    plan = ground_plan(QueryPlan.model_validate(data), 'Margin', 'turn', vocab)
    result = check_plan(plan, vocab, 'Margin')
    assert not isinstance(result, CheckedPlan)


@pytest.mark.parametrize('kind', ('ratio', 'percent_change'))
def test_a_zero_denominator_is_null_with_a_plain_reason(kind):
    _, vocab = declarations(formula=kind)
    result = compiled(formula_plan(kind), 'Margin', vocab)
    rows = [(day, value, category, 0, source) for day, value, category, units, source in ROWS]
    with connection(rows) as con:
        actual = con.execute(result.answer.sql).fetchone()
    assert actual[2] is None
    assert actual[-1] == 'zero_denominator'


@pytest.mark.parametrize('kind', ('ratio', 'difference', 'percent_change'))
def test_no_input_rows_leave_a_declared_formula_null(kind):
    _, vocab = declarations(formula=kind)
    result = compiled(formula_plan(kind), 'Margin', vocab)
    with connection([]) as con:
        actual = con.execute(result.answer.sql).fetchone()
    assert actual[2] is None


def test_sql_gate_refuses_rewriting_the_declared_formula():
    _, vocab = declarations()
    result = compiled(formula_plan(), 'Margin', vocab)
    sql = result.answer.sql.replace('ABS(', 'SQRT(')
    assert sql != result.answer.sql
    assert check(sql, result.answer.allow)


def test_formula_name_is_hidden_when_an_input_measure_is_outside_scope():
    eng, _ = declarations()
    sem = eng.semantics
    sem.contexts.pop('units')
    specs = [MeasureSpec('u.metric', 'USD', 'entities', True)]
    vocab = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'), ['u'], ['u'], specs)
    assert not vocab.candidates('Margin')
    assert 'u.margin' not in vocab.derived_measures
