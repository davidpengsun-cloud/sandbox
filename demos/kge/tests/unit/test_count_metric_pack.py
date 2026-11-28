"""Count metric declarations bind a complete entity key and one checked population."""
from copy import deepcopy

import pytest

from kge.compiler import checks
from kge.compiler.pack import Pack
from kge.compiler.vocabulary import measure_entries


def declaration(tmp_path):
    return Pack('example', tmp_path, data={
        'glossary': [{'id': 'accounts-count', 'name': 'Account count'}],
        'entities': [{'id': 'accounts', 'table': 'u.accounts', 'key': ['source', 'id']}],
        'contexts': [{'id': 'flows', 'fact': 'u.flows', 'reaches': ['account']}],
        'joins': [{'id': 'account', 'from': 'u.flows', 'to': 'u.accounts'}],
        'count_metrics': [{'id': 'u-account-count', 'term': 'accounts-count', 'entity': 'accounts', 'context': 'flows',
                           'filters': [{'column': 'u.accounts.id', 'op': 'gt', 'value': 0}]}],
    }, lock={'sources': {'u': {'tables': [
        {'name': 'accounts', 'columns': [{'name': 'source', 'type': 'VARCHAR'}, {'name': 'id', 'type': 'INTEGER'}]},
        {'name': 'flows', 'columns': [{'name': 'account', 'type': 'INTEGER'}]},
    ]}}})


def test_count_metric_compiles_once_with_its_entity_population_and_local_filter(tmp_path):
    p = declaration(tmp_path)
    before = deepcopy(p.data)
    assert checks.count_metrics(p) == []
    assert not [f for f in checks.sections(p) if f.path.startswith('pack.yaml:count_metrics')]
    assert measure_entries(p.data) == [{'id': 'count-metric:u-account-count', 'kind': 'count_metric', 'ordinal': 1,
                                      'params': {**p.data['count_metrics'][0], 'label': 'Account count'}}]
    assert p.data == before


@pytest.mark.parametrize('change,message', [
    ({'entity': 'unknown'}, 'declared entity'),
    ({'context': 'unknown'}, 'declared population'),
    ({'term': 'unknown'}, 'glossary'),
    ({'id': 'accounts'}, 'distinct vocabulary id'),
    ({'filters': [{'column': 'u.flows.account', 'op': 'gt', 'value': 0}]}, "entity's table"),
    ({'filters': [{'column': 'u.accounts.missing', 'op': 'eq', 'value': 'A'}]}, 'pack.lock'),
    ({'filters': [{'column': 'u.accounts.id', 'op': 'gt'}]}, 'requires text or a number'),
    ({'filters': [{'column': 'u.accounts.id', 'op': 'is_null', 'value': 0}]}, 'takes no value'),
])
def test_check_pack_refuses_unbound_or_incomplete_count_declarations(tmp_path, change, message):
    p = declaration(tmp_path)
    p.data['count_metrics'][0].update(change)
    assert any(message in f.message for f in checks.count_metrics(p))


def test_count_metric_needs_a_declared_path_and_cannot_filter_restricted_data(tmp_path):
    p = declaration(tmp_path)
    p.data['contexts'][0]['reaches'] = []
    assert any('no declared path' in f.message for f in checks.count_metrics(p))
    p.data['contexts'][0]['reaches'] = ['account']
    p.data['policies'] = {'restricted_attributes': [{'columns': ['u.accounts.id']}]}
    assert any('restricted' in f.message for f in checks.count_metrics(p))


def test_count_metric_ids_are_unique(tmp_path):
    p = declaration(tmp_path)
    p.data['count_metrics'].append(deepcopy(p.data['count_metrics'][0]))
    finding, = checks.ids(p, held=('count_metrics',))
    assert finding.rule == 'S02' and 'already' in finding.message


def test_share_formula_names_the_same_declared_metric_at_total_scope(tmp_path):
    p = declaration(tmp_path)
    p.data['measures'] = [{'id': 'u.amount'}, {'id': 'u.units'}]
    p.data['sources'] = []
    p.data['derived_measures'] = [{'id': 'u-share', 'term': 'accounts-count',
                                  'formula': {'kind': 'share', 'numerator': 'u.amount', 'denominator': 'u.amount'}}]
    assert checks.derived_measures(p) == []
    p.data['derived_measures'][0]['formula']['denominator'] = 'u.units'
    assert any('total scope' in f.message for f in checks.derived_measures(p))


@pytest.mark.parametrize('identifier,section', [('bridge', 'curated_sources'), ('context:count:accounts', None),
                                              ('count:accounts', None), ('standing', 'filters'), ('account-bands', 'banded_dimensions')])
def test_count_metric_cannot_shadow_other_or_generated_vocabulary(tmp_path, identifier, section):
    p = declaration(tmp_path)
    p.data['count_metrics'][0]['id'] = identifier
    if section:
        p.data[section] = [{'id': identifier}]
    assert any('distinct vocabulary id' in f.message for f in checks.count_metrics(p))


@pytest.mark.parametrize('dtype,value,valid', [('INTEGER', 'wrong', False), ('INTEGER', 3, True),
                                            ('INTEGER', '3', True), ('VARCHAR', 3, False), ('VARCHAR', 'A', True),
                                            ('BOOLEAN', True, True), ('BOOLEAN', 'false', True), ('BOOLEAN', 1, False),
                                            ('DATE', '2025-02-30', False), ('DATE', '2025-02-28', True)])
def test_count_filter_values_are_checked_against_the_declared_column_type(tmp_path, dtype, value, valid):
    p = declaration(tmp_path)
    p.lock['sources']['u']['tables'][0]['columns'][1]['type'] = dtype
    p.data['count_metrics'][0]['filters'][0].update(op='eq', value=value)
    assert (checks.count_metrics(p) == []) == valid


def test_formula_context_bindings_name_the_declared_input_populations(tmp_path):
    p = declaration(tmp_path)
    p.data['measures'] = [{'id': 'u.amount'}, {'id': 'u.units'}]
    p.data['contexts'] = [{'id': 'amounts', 'measure': 'u.amount'}, {'id': 'units', 'measure': 'u.units'}]
    metric = {'id': 'u-ratio', 'term': 'accounts-count',
              'formula': {'kind': 'ratio', 'numerator': 'u.amount', 'denominator': 'u.units'},
              'contexts': {'numerator': 'amounts', 'denominator': 'units'}}
    p.data['derived_measures'] = [metric]
    assert checks.derived_measures(p) == []
    metric['contexts']['numerator'] = 'units'
    assert any('input measure' in f.message for f in checks.derived_measures(p))
    metric['contexts']['numerator'] = 'unknown'
    assert any('declared input context' in f.message for f in checks.derived_measures(p))


def test_a_share_cannot_switch_its_denominator_population(tmp_path):
    p = declaration(tmp_path)
    p.data['measures'] = [{'id': 'u.amount'}]
    p.data['contexts'] = [{'id': 'one', 'measure': 'u.amount'}, {'id': 'two', 'measure': 'u.amount'}]
    p.data['derived_measures'] = [{'id': 'u-share', 'term': 'accounts-count',
                                 'formula': {'kind': 'share', 'numerator': 'u.amount', 'denominator': 'u.amount'},
                                 'contexts': {'numerator': 'one', 'denominator': 'two'}}]
    assert any('same input population' in f.message for f in checks.derived_measures(p))


def test_numeric_band_cannot_shadow_a_declared_count_metric(tmp_path):
    p = declaration(tmp_path)
    band = {'id': 'account-band', 'label': 'Account band', 'column': 'u.accounts.id',
            'ranges': [{'label': 'All accounts', 'min': None, 'max': None}]}
    p.data['banded_dimensions'] = [band]
    assert checks.banded_dimensions(p) == []
    band['id'] = p.data['count_metrics'][0]['id']
    finding, = checks.banded_dimensions(p)
    assert finding.rule == 'S29' and 'existing vocabulary name' in finding.message


@pytest.mark.parametrize('column,operation,value,message', [
    ('u.accounts.id', 'contains', 3, 'text column'),
    ('u.accounts.source', 'contains', '', 'nonempty'),
    ('u.accounts.source', 'starts_with', 'A%', 'wildcard'),
])
def test_count_metric_text_filters_refuse_invalid_column_and_literals(tmp_path, column, operation, value, message):
    p = declaration(tmp_path)
    p.data['count_metrics'][0]['filters'] = [{'column': column, 'op': operation, 'value': value}]
    assert not [finding for finding in checks.sections(p) if ':count_metrics' in finding.path]
    findings = checks.count_metrics(p)
    assert any(finding.rule == 'S08' and message in finding.message for finding in findings), findings


@pytest.mark.parametrize('operation,value', [('contains', 'A'), ('starts_with', 'B'), ('contains', "O'Brien")])
def test_count_metric_text_filters_accept_valid_text_operations(tmp_path, operation, value):
    p = declaration(tmp_path)
    p.data['count_metrics'][0]['filters'] = [{'column': 'u.accounts.source', 'op': operation, 'value': value}]
    assert not [finding for finding in checks.sections(p) if ':count_metrics' in finding.path]
    assert checks.count_metrics(p) == []
