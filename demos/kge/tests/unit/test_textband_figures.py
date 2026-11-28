"""Declared text operations and numeric bands on hand-computed small inputs."""
import copy
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import exp

from kge.compiler.checks import banded_dimensions, vocabulary as check_vocabulary
from kge.compiler.pack import Pack
from kge.compiler.vocabulary import band_entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import connection


BAND = {'id': 'value_bands', 'label': 'size band', 'column': 'u.events.value', 'ranges': [
    {'label': 'Small', 'min': None, 'max': 10}, {'label': 'Medium', 'min': 10, 'max': 20},
    {'label': 'Large', 'min': 20, 'max': None}]}
PREDICATES = [dict(kind='predicate', params=dict(column='u.events.category', phrases=[phrase], op=op, value='Al'))
              for phrase, op in (('matching labels', 'contains'), ('initial labels', 'starts_with'))]
ROWS = [(dt.date(2025, 4, 10), value, category, i, 'x') for i, (value, category) in enumerate(
    ((0, 'Alpha'), (9, 'Alpine'), (10, 'Alpha'), (19, 'ZAlpha'), (20, 'Beta'), (30, 'Alpine'), (-5, 'Alpha')), 1)]


class SuppliedPlan:
    def __init__(self, plan):
        self.plan = plan

    def json(self, prompt):
        return Propose(kind='propose', base_turn=None, plan=self.plan).model_dump(mode='json', by_alias=True)


def declarations(monkeypatch=None, bands=True, predicates=PREDICATES):
    sem = semantics()
    sem.d.GROUP_BY = {'by size band': BAND['id']} if bands else {}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.INTENTS = {'metric': 'Metric'}, {}, {}
    sem.d.VOCABULARY = [*band_entries({'banded_dimensions': [copy.deepcopy(BAND)]} if bands else {}), *copy.deepcopy(predicates)]
    words = lexical_vocabulary(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    sem.measure_declarations, sem.output_metadata = {'u.metric': {}}, {}
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    eng = SimpleNamespace(semantics=sem, term_names=['Metric'], FISCAL_START=4, **vars(sem.d))
    if monkeypatch is not None:
        monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    return eng, vocab


def band_dimension(column='u.events.value', identifier='value_bands'):
    return dict(kind='band', id='band', band=name(identifier), bindings=[
        dict(context=name('ctx'), column=name(column), path=[], level=None)])


def text_filter(operation='contains', value='Al', column='u.events.category'):
    return dict(kind='compare', id='text', left=dict(kind='column', name=name(column), path=[]),
                op=operation, value=dict(kind='text', value=value), evidence=[])


def compiled(plan, text, vocab):
    grounded = ground_plan(plan, text, 'turn', vocab)
    replay = planmodel.read(SuppliedPlan(grounded), text, vocab)
    assert replay.kind == 'propose', replay
    checked = check_plan(replay.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert result.answer is not None, result
    assert check(result.answer.sql, result.answer.allow) == []
    return result


def declaration_pack():
    data = {'terminology': {'predicates': [copy.deepcopy(p['params']) for p in PREDICATES]},
            'banded_dimensions': [copy.deepcopy(BAND)]}
    lock = {'sources': {'u': {'tables': [{'name': 'events', 'primary_key': ['entity_id'], 'columns': [
        {'name': 'value', 'type': 'DECIMAL(18,2)'}, {'name': 'category', 'type': 'VARCHAR'},
        {'name': 'entity_id', 'type': 'INTEGER'}]}]}}}
    return Pack('example', Path('.'), data=data, lock=lock)


@pytest.mark.parametrize('operation,phrase,expected', [('contains', 'matching labels', 68), ('starts_with', 'initial labels', 49)])
def test_text_filter_rules_and_model_have_hand_computed_figures(monkeypatch, operation, phrase, expected):
    eng, vocab = declarations(monkeypatch)
    text = 'Metric for ' + phrase
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    predicate, = [p for p in reply.plan.nodes() if p.kind == 'compare']
    assert (predicate.op, predicate.value.kind, predicate.value.value) == (operation, 'text', 'Al')
    assert any(p.kind == 'predicate' and p.operation == operation for p in ledger(text, 'turn', vocab))
    for plan in (reply.plan, model(filters=[text_filter(operation)])):
        result = compiled(plan, text, vocab)
        with connection(ROWS) as con:
            assert con.execute(result.answer.sql).fetchone()[0] == expected


@pytest.mark.parametrize('value', ['', '   ', 5, True, None, 'A%', 'A_', '%', '_'])
@pytest.mark.parametrize('operation', ['contains', 'starts_with'])
def test_check_pack_refuses_invalid_text_values(value, operation):
    pack = declaration_pack()
    pack.data['terminology']['predicates'][0].update(op=operation, value=value)
    assert any(f.rule == 'S29' for f in check_vocabulary(pack))


def test_check_pack_refuses_text_operations_on_numeric_columns():
    pack = declaration_pack()
    pack.data['terminology']['predicates'][0]['column'] = 'u.events.value'
    assert any(f.rule == 'S29' for f in check_vocabulary(pack))


@pytest.mark.parametrize('operation', ['contains', 'starts_with'])
@pytest.mark.parametrize('literal', ["O'Brien", r'a\b', 'A!B'])
def test_text_literals_keep_quotes_and_escape_characters(operation, literal):
    phrase = 'selected labels'
    predicates = [dict(kind='predicate', params=dict(column='u.events.category', phrases=[phrase], op=operation, value=literal))]
    _, vocab = declarations(predicates=predicates)
    result = compiled(model(filters=[text_filter(operation, literal)]), 'Metric for ' + phrase, vocab)
    rows = [(dt.date(2025, 4, 10), value, category, i, 'x') for i, (value, category) in enumerate(
        ((3, literal), (5, literal + ' tail'), (7, 'head ' + literal), (11, 'elsewhere'), (13, None)), 1)]
    with connection(rows) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == (15 if operation == 'contains' else 8)


@pytest.mark.parametrize('operation,value,column', [('starts_with', 'Al', 'u.events.category'),
    ('contains', 'Beta', 'u.events.category'), ('contains', 'Al', 'u.events.source')])
def test_model_cannot_borrow_text_authority_from_another_operator_value_or_column(operation, value, column):
    _, vocab = declarations(predicates=PREDICATES[:1])
    text = 'Metric for matching labels'
    plan = ground_plan(model(filters=[text_filter(operation, value, column)]), text, 'turn', vocab)
    assert not isinstance(check_plan(plan, vocab, text), CheckedPlan)


def test_numeric_bands_rules_and_model_have_boundary_figures(monkeypatch):
    eng, vocab = declarations(monkeypatch)
    text = 'Metric by size band'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    dimension, = reply.plan.dimensions
    assert dimension.kind == 'band' and dimension.band.id == BAND['id']
    for plan in (reply.plan, model(dimensions=[band_dimension()])):
        result = compiled(plan, text, vocab)
        with connection(ROWS) as con:
            actual = con.execute(result.answer.sql).fetchall()
        assert {r[0]: r[1] for r in actual if r[0] is not None} == {'Small': 9, 'Medium': 29, 'Large': 50}


@pytest.mark.parametrize('change', [
    lambda b: b.update(ranges=[]),
    lambda b: b['ranges'][0].update(min=0),
    lambda b: b['ranges'][-1].update(max=30),
    lambda b: b['ranges'][1].update(min=11),
    lambda b: b['ranges'][1].update(min=9),
    lambda b: b['ranges'][1].update(max=10),
    lambda b: b['ranges'][1].update(min=True),
    lambda b: b['ranges'][1].update(max=float('inf')),
    lambda b: b['ranges'][1].update(max=float('nan')),
    lambda b: b['ranges'][1].update(label='small'),
    lambda b: b['ranges'][1].update(label='  '),
    lambda b: b['ranges'].reverse(),
    lambda b: b.update(column='u.events.category'),
    lambda b: b.update(column='u.events.absent'),
])
def test_check_pack_refuses_invalid_numeric_bands(change):
    pack = declaration_pack()
    change(pack.data['banded_dimensions'][0])
    assert banded_dimensions(pack)


def test_check_pack_accepts_contiguous_numeric_bands_and_text_filters():
    pack = declaration_pack()
    assert banded_dimensions(pack) == []
    assert check_vocabulary(pack) == []


@pytest.mark.parametrize('identifier,column', [('missing', 'u.events.value'), ('value_bands', 'u.events.entity_id')])
def test_model_cannot_invent_a_band_or_rebind_its_column(identifier, column):
    _, vocab = declarations()
    text = 'Metric by size band'
    plan = ground_plan(model(dimensions=[band_dimension(column, identifier)]), text, 'turn', vocab)
    assert not isinstance(check_plan(plan, vocab, text), CheckedPlan)


@pytest.mark.parametrize('operation,phrase,expected', [('contains', 'matching labels', {'Small': 9, 'Medium': 29, 'Large': 30}),
    ('starts_with', 'initial labels', {'Small': 9, 'Medium': 10, 'Large': 30})])
def test_text_filters_combine_with_bands(operation, phrase, expected):
    _, vocab = declarations()
    result = compiled(model(filters=[text_filter(operation)], dimensions=[band_dimension()]),
                      'Metric by size band for ' + phrase, vocab)
    with connection(ROWS) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert {r[0]: r[1] for r in actual if r[0] is not None} == expected


@pytest.mark.parametrize('mutate', ['text_value', 'text_column', 'band_boundary', 'band_label'])
def test_gate_rejects_mutating_text_or_band_declarations(mutate):
    _, vocab = declarations()
    result = compiled(model(filters=[text_filter()], dimensions=[band_dimension()]),
                      'Metric by size band for matching labels', vocab)
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    if mutate == 'text_column':
        column = next(c for c in tree.find_all(exp.Column) if c.name == 'category')
        column.set('this', exp.to_identifier('source'))
    else:
        original, changed = {'text_value': ('Al', 'Beta'), 'band_boundary': ('10', '11'),
                             'band_label': ('Small', 'Changed')}[mutate]
        literal = next(n for n in tree.find_all(exp.Literal) if original in str(n.this))
        literal.set('this', str(literal.this).replace(original, changed))
    sql = tree.sql('duckdb')
    assert sql != result.answer.sql
    assert check(sql, result.answer.allow)


def test_band_and_text_match_combine_with_per_output_aggregation():
    eng, _ = declarations()
    sem = eng.semantics
    sem.d.VOCABULARY.append(dict(kind='measure_options', params=dict(measure='u.metric', aggregations=['sum', 'count'])))
    sem.contexts['count'] = replace(sem.contexts['ctx'], grain=sem.contexts['ctx'].grain, id='count', label='Item count', term='Item count',
                                    measure='u.count', expression='count(events.value)', aggregation='count')
    from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
    from kge.semantics.plan import Binding
    vocab = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                             ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True),
                                           MeasureSpec('u.count', None, 'entities', True)],
                             aliases=[('Metric', 'ctx'), ('Item count', 'count'), *sem.d.GROUP_BY.items()])
    dims = band_dimension()
    dims['bindings'].append(dict(context=name('count'), column=name('u.events.value'), path=[], level=None))
    data = model(dimensions=[dims], filters=[text_filter()]).model_dump(mode='json', by_alias=True)
    data['measures'].append(dict(kind='measure', id='n', measure=name('u.count'), context=name('count'), mode='current', filters=[]))
    result = compiled(QueryPlan.model_validate(data), 'Metric and Item count by size band for matching labels', vocab)
    with connection(ROWS) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert {r[0]: tuple(r[1:3]) for r in actual if r[0] is not None} == {
        'Small': (9, 2), 'Medium': (29, 2), 'Large': (30, 1)}


def test_numeric_band_pairs_with_ranking_on_rules_and_model_paths(monkeypatch):
    eng, vocab = declarations(monkeypatch)
    text = 'top 2 size bands by Metric'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    supplied = model(dimensions=[band_dimension()], order=[dict(output='m', direction='desc', nulls='last', evidence=[])], limit=2)
    for plan in (reply.plan, supplied):
        result = compiled(plan, text, vocab)
        with connection(ROWS) as con:
            actual = con.execute(result.answer.sql).fetchall()
        assert {r[0]: r[1] for r in actual if r[0] is not None} == {'Large': 50, 'Medium': 29}


def test_numeric_band_pairs_with_time_on_rules_and_model_paths(monkeypatch):
    eng, vocab = declarations(monkeypatch)
    text = 'Metric by size band and month for April 2025'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    supplied = model(dimensions=[band_dimension(), dict(kind='time', id='month', calendar=name('cal'), grain='month')],
                     time=dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('cal'))))
    for plan in (reply.plan, supplied):
        result = compiled(plan, text, vocab)
        with connection(ROWS) as con:
            actual = con.execute(result.answer.sql).fetchall()
        dimensions = [d.kind for d in plan.dimensions]
        band_at, time_at = dimensions.index('band'), dimensions.index('time')
        assert {r[band_at]: r[2] for r in actual if r[band_at] is not None} == {'Small': 9, 'Medium': 29, 'Large': 50}
        assert {r[time_at] for r in actual if r[band_at] is not None} == {dt.date(2025, 4, 1)}


def test_text_matching_pairs_with_or_across_columns_without_repeating_rows(monkeypatch):
    predicates = [*PREDICATES[:1], dict(kind='predicate', params=dict(
        column='u.events.source', phrases=['initial sources'], op='starts_with', value='x'))]
    eng, vocab = declarations(monkeypatch, predicates=predicates)
    text = 'Metric for matching labels or initial sources'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    supplied = model(filters=[dict(kind='any', id='either', children=[text_filter(),
        {**text_filter('starts_with', 'x', 'u.events.source'), 'id': 'prefix'}], evidence=[])])
    rows = [(*row[:4], 'x' if i in (2, 4) else 'q') for i, row in enumerate(ROWS)]
    for plan in (reply.plan, supplied):
        result = compiled(plan, text, vocab)
        with connection(rows) as con:
            assert con.execute(result.answer.sql).fetchone()[0] == 88


def test_band_label_groups_automatically_without_a_manual_alias(monkeypatch):
    eng, _ = declarations(monkeypatch)
    eng.semantics.d.GROUP_BY = {}
    eng.GROUP_BY = {}
    vocab = vocabulary(eng.semantics, aliases=[('Metric', 'ctx')])
    text = 'Metric by size band'
    assert parse(text, eng.term_names, eng).group_by == []
    reply = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    assert reply.plan.dimensions[0].band.id == BAND['id']
    result = compiled(reply.plan, text, vocab)
    with connection(ROWS) as con:
        assert {r[0]: r[1] for r in con.execute(result.answer.sql).fetchall() if r[0] is not None} == {
            'Small': 9, 'Medium': 29, 'Large': 50}


def test_band_public_payload_is_json_data_and_the_scoped_declaration_stays_immutable():
    _, vocab = declarations()
    payload = json.loads(json.dumps(vocab.public()))
    band, = [entry for entry in payload if entry['kind'] == 'band']
    assert band['id'] == BAND['id'] and band['value'] == BAND
    with pytest.raises(TypeError):
        vocab.entries[BAND['id']].value['ranges'][0]['min'] = 0


def test_bands_keep_null_grouping_and_include_negative_values():
    eng, _ = declarations()
    sem = eng.semantics
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain=sem.contexts['ctx'].grain, filters=())
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    result = compiled(model(dimensions=[band_dimension()]), 'Metric by size band', vocab)
    rows = [*ROWS, (dt.date(2025, 4, 10), None, 'Alpha', 8, 'x')]
    with connection(rows) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert {r[0]: r[1] for r in actual} == {'Small': 4, 'Medium': 29, 'Large': 50, None: None}
