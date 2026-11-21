"""Pack declarations for aggregation choices and SQL-free arithmetic measures."""
from copy import deepcopy
import json
from pathlib import Path
from typing import get_args

import pytest

from kge.compiler import checks
from kge.compiler.emit import NODES
from kge.compiler.pack import Pack
from kge.compiler.vocabulary import AGGREGATION_CHOICES, ARITHMETIC, entries, measure_entries
from kge.semantics.plan import Arithmetic


def pack():
    """Small physical declarations have no dependency on generated warehouses or a graph."""
    measures = [dict(id=identifier, term=identifier, column='u.events.' + column,
                     expression='sum(events.' + column + ')', aggregation='sum', grain='day',
                     currency='USD', calendar='cal', conversion='none')
                for identifier, column in (('amount', 'value'), ('cost', 'cost'))]
    return Pack('fixture', Path('.'), data={
        'glossary': [dict(id='amount', name='Amount'), dict(id='cost', name='Cost'), dict(id='margin', name='Margin')],
        'measures': measures, 'terminology': {'aliases': []}},
        lock={'sources': {'u': {'tables': [{'name': 'events', 'columns': [
            {'name': 'value', 'type': 'DECIMAL'}, {'name': 'cost', 'type': 'DECIMAL'}]}]}}})


def derived(kind='ratio', **formula):
    return dict(id='margin', term='margin', formula=dict(kind=kind, numerator='amount', denominator='cost', **formula))


def test_pack_formula_operators_are_exactly_the_plan_arithmetic_operators():
    assert set(ARITHMETIC) == set(get_args(Arithmetic.model_fields['kind'].annotation)) | {'share'}


def section_findings(p, section):
    return [f for f in checks.sections(p) if f.path.startswith('pack.yaml:' + section)]


@pytest.mark.parametrize('aggregation', AGGREGATION_CHOICES)
def test_each_physical_aggregation_is_a_valid_default(aggregation):
    p = pack()
    p.data['measures'][0].update(aggregation=aggregation, expression=aggregation + '(events.value)')
    assert section_findings(p, 'measures') == []
    assert checks.measures(p) == []


def test_a_measure_without_options_keeps_its_single_default():
    p = pack()
    assert checks.measures(p) == []
    assert measure_entries(p.data) == []


@pytest.mark.parametrize('expression', ['sum(events.value)',
                                     'sum(CASE WHEN events.cost > 0 THEN events.value ELSE 0 END)',
                                     'sum(events.value) FILTER (WHERE events.cost > 0)'])
def test_alternatives_retain_one_declared_row_expression(expression):
    p = pack()
    p.data['measures'][0].update(expression=expression, aggregations=list(AGGREGATION_CHOICES))
    assert checks.measures(p) == []


@pytest.mark.parametrize('choices, message', [
    ([], 'at least one'), (['sum', 'sum'], 'once'), (['avg'], 'default aggregation')])
def test_invalid_aggregation_lists_name_the_problem(choices, message):
    p = pack()
    p.data['measures'][0]['aggregations'] = choices
    assert any(message in f.message for f in checks.measures(p))


@pytest.mark.parametrize('choices', [None, 'sum', ['variance'], ['average'], [True], [1]])
def test_aggregation_schema_refuses_values_outside_the_closed_set(choices):
    p = pack()
    p.data['measures'][0]['aggregations'] = choices
    assert section_findings(p, 'measures')


@pytest.mark.parametrize('expression', ['sum(events.value) + sum(events.cost)',
                                     'sum(events.value) / nullif(sum(events.cost), 0)',
                                     'sum(events.value) + 1', 'count(*)', 'count(DISTINCT events.value)'])
def test_alternatives_refuse_compound_aggregates_or_changed_distinct_semantics(expression):
    p = pack()
    p.data['measures'][0].update(expression=expression, aggregations=['sum', 'avg'])
    assert any('alternate aggregations require' in f.message for f in checks.measures(p))


@pytest.mark.parametrize('kind', ARITHMETIC)
def test_each_supported_arithmetic_formula_names_declared_measures(kind):
    p = pack()
    p.data['derived_measures'] = [derived(kind)]
    if kind == 'share':
        p.data['derived_measures'][0]['formula']['denominator'] = 'amount'
    assert section_findings(p, 'derived_measures') == []
    assert checks.derived_measures(p) == []
    assert checks.ids(p, ('derived_measures',)) == []


@pytest.mark.parametrize('kind', ['sum', 'add', 'multiply', 'divide', 'sql'])
def test_formula_operators_outside_arithmetic_are_schema_findings(kind):
    p = pack()
    p.data['derived_measures'] = [derived(kind)]
    assert section_findings(p, 'derived_measures')


@pytest.mark.parametrize('formula', ['amount / cost', {'kind': 'ratio'},
                                     dict(kind='ratio', numerator='amount', denominator='cost', sql='SELECT 1')])
def test_formula_is_a_closed_record_not_executable_text(formula):
    p = pack()
    p.data['derived_measures'] = [dict(id='margin', term='margin', formula=formula)]
    assert section_findings(p, 'derived_measures')


@pytest.mark.parametrize('operand', ['numerator', 'denominator'])
def test_a_formula_cannot_read_an_undeclared_measure_or_itself(operand):
    for identifier in ('missing', 'margin', 'u.events.value'):
        p = pack()
        measure = derived()
        measure['formula'][operand] = identifier
        p.data['derived_measures'] = [measure]
        finding, = checks.derived_measures(p)
        assert finding.path.endswith('.formula.' + operand)
        assert 'declared physical measure' in finding.message


def test_derived_names_are_unique_slugs_and_do_not_shadow_physical_measures():
    p = pack()
    p.data['derived_measures'] = [derived(), derived()]
    assert any('already' in f.message for f in checks.ids(p, ('derived_measures',)))
    p.data['derived_measures'] = [dict(id='amount', term='margin', formula=derived()['formula'])]
    assert any('physical measure' in f.message for f in checks.derived_measures(p))
    p.data['derived_measures'][0]['id'] = 'Margin name'
    assert any('not an id' in f.message for f in checks.ids(p, ('derived_measures',)))


def test_a_derived_measure_names_a_declared_glossary_term():
    p = pack()
    p.data['derived_measures'] = [dict(id='margin', term='missing', formula=derived()['formula'])]
    assert any('glossary' in f.message for f in checks.derived_measures(p))


@pytest.mark.parametrize('section', ['sources', 'curated_sources', 'contexts', 'entities', 'calendars', 'joins'])
def test_a_derived_id_cannot_shadow_an_existing_vocabulary_name(section):
    p = pack()
    p.data[section] = [dict(id='occupied')]
    p.data['derived_measures'] = [dict(derived(), id='occupied')]
    finding, = checks.derived_measures(p)
    assert finding.rule == 'S08'
    assert finding.path.endswith('derived_measures[0].id')
    assert section in finding.message


@pytest.mark.parametrize('identifier', ['u.events.value', 'u.events.cost'])
def test_a_derived_id_cannot_shadow_a_qualified_warehouse_column(identifier):
    p = pack()
    p.data['derived_measures'] = [dict(derived(), id=identifier)]
    finding, = checks.derived_measures(p)
    assert finding.path.endswith('derived_measures[0].id')
    assert 'warehouse column' in finding.message


def test_a_derived_id_can_share_its_glossary_term_id():
    p = pack()
    p.data['derived_measures'] = [derived()]
    assert p.data['derived_measures'][0]['id'] == p.data['derived_measures'][0]['term']
    assert checks.derived_measures(p) == []


@pytest.mark.parametrize('section', ['sources', 'curated_sources', 'contexts', 'entities', 'calendars', 'joins'])
@pytest.mark.parametrize('malformed', [None, 7, 'wrong', [None, 1, 'wrong', dict(id=[]), dict(name='missing id')]])
def test_malformed_other_sections_do_not_crash_derived_name_validation(section, malformed):
    p = pack()
    p.data[section] = malformed
    p.data['derived_measures'] = [derived()]
    assert checks.derived_measures(p) == []


def test_the_pack_check_skips_derived_column_name_validation_when_the_lock_is_malformed(tmp_path, monkeypatch):
    import yaml

    from kge.compiler.compile import VERSION
    from kge.compiler.emit import FILLS
    from tests.unit.test_compiler import META_MODEL, committed

    data, _ = committed()
    data['derived_measures'] = [dict(id='formula', term=data['glossary'][0]['id'], formula=dict(
        kind='ratio', numerator=data['measures'][0]['id'], denominator=data['measures'][1]['id']))]
    p = Pack(data['manifest']['client'], tmp_path, data=data, lock={'sources': []})
    monkeypatch.setattr(checks, 'derived_measures', lambda p: pytest.fail('a malformed lock stops every reader of its columns'))
    findings = checks.check(p, yaml.safe_load(META_MODEL.read_text()), VERSION, FILLS)
    assert any(f.rule == 'S01' and f.path.startswith('pack.lock') for f in findings)


def test_optional_declarations_use_stable_ids_and_preserve_every_existing_vocabulary_row():
    p = pack()
    p.data['terminology'] = {'predicates': [dict(phrases=['positive'], column='u.events.value', op='gt', value=0)]}
    baseline = deepcopy(NODES['Vocabulary'](p.data, {}))
    assert baseline == [{**v, 'params': json.dumps(v['params'], sort_keys=True, separators=(',', ':'))}
                        for v in entries(p.data['terminology'])]
    p.data['measures'][0]['aggregations'] = list(AGGREGATION_CHOICES)
    p.data['derived_measures'] = [derived()]
    rows = NODES['Vocabulary'](p.data, {})
    assert rows[:len(baseline)] == baseline
    options, formula = rows[len(baseline):]
    assert options['id'] == 'measure-options:amount'
    assert json.loads(options['params']) == dict(measure='amount', aggregations=list(AGGREGATION_CHOICES))
    assert formula['id'] == 'derived-measure:margin'
    assert json.loads(formula['params']) == {**derived(), 'label': 'Margin'}
    p.data['measures'].insert(0, dict(p.data['measures'][1], id='new'))
    assert [r['id'] for r in NODES['Vocabulary'](p.data, {})] == [r['id'] for r in rows]
