import ast
import re

import pytest
from pydantic import TypeAdapter, ValidationError

from kge.pipeline.settings import ROOT
from kge.semantics.plan import (
    Clarification, HistoryEntry, Predicate, Result, Scalar, reply_adapter,
)
from tests.plan_fixtures import model, monthly_time, name, time_dimension, vocabulary


@pytest.mark.parametrize('scalar', [
    {'kind': 'text', 'value': 'x'}, {'kind': 'boolean', 'value': True},
    {'kind': 'integer', 'value': 5}, {'kind': 'decimal', 'value': '-12.50'},
    {'kind': 'date', 'value': '2024-02-29'}, {'kind': 'null'},
])
def test_scalar_and_result_variants_roundtrip(scalar):
    adapter = TypeAdapter(Scalar)
    value = adapter.validate_python(scalar)
    assert adapter.validate_json(value.model_dump_json()) == value
    result = Result.model_validate({'columns': [{'id': 'x', 'kind': 'measure', 'type': 'decimal', 'currency': None, 'lineage': ['ctx']}],
                                    'rows': [{'panel': 'p', 'dimensions': [scalar], 'cells': [{'value': scalar, 'citations': ['c'], 'null_reasons': []}]}]})
    assert Result.model_validate_json(result.model_dump_json()) == result


def comparison_predicate(identifier='f', operand=None):
    return {'kind': 'compare', 'id': identifier, 'left': operand or {'kind': 'column', 'name': name('u.events.value'), 'path': []},
            'op': 'gt', 'value': {'kind': 'integer', 'value': 3}, 'evidence': []}


@pytest.mark.parametrize('kind', ['compare', 'in', 'null', 'all', 'any', 'not', 'membership'])
@pytest.mark.parametrize('operand', ['column', 'output'])
def test_predicate_tagged_variants_and_both_operands(kind, operand):
    compare = comparison_predicate(operand={'kind': 'output', 'id': 'm'} if operand == 'output' else None)
    data = compare
    if kind == 'in':
        data = {k: v for k, v in compare.items() if k not in ('op', 'value')}
        data.update(kind=kind, values=[{'kind': 'text', 'value': 'x'}, {'kind': 'null'}])
    elif kind == 'null':
        data = {k: v for k, v in compare.items() if k not in ('op', 'value')}
        data.update(kind=kind, is_null=False)
    elif kind in ('all', 'any', 'not'):
        data = {'kind': kind, 'id': 'outer', 'evidence': [], **({'child': compare} if kind == 'not' else {'children': [compare]})}
    elif kind == 'membership':
        data = {'kind': kind, 'id': 'outer', 'evidence': [], 'entity': name('entity'), 'relation': name('rel'), 'context': name('ctx'), 'filters': [compare]}
    adapter = TypeAdapter(Predicate)
    value = adapter.validate_python(data)
    assert adapter.validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        adapter.validate_python(data | {'sql': 'x'})


@pytest.mark.parametrize('kind', ['time_mode', 'previous_period', 'previous_year', 'explicit_period', 'contexts'])
def test_every_comparison_roundtrips_and_exposes_stable_outputs(kind):
    c = {'kind': kind, 'id': 'c', 'evidence': []}
    if kind == 'contexts':
        c.update(left='m', right='m')
    else:
        c['inputs'] = ['m']
    if kind == 'time_mode':
        c.update(base='current', other='as_reported')
    if kind == 'explicit_period':
        c['other'] = monthly_time('2024-04', '2024-06')
    plan = model(comparisons=[c])
    assert type(plan).model_validate_json(plan.model_dump_json(by_alias=True)) == plan
    assert ('c/base' if kind == 'contexts' else 'c/m/base') in plan.outputs()


@pytest.mark.parametrize('kind', ['ratio', 'percent_change', 'share', 'running_total'])
def test_every_derived_variant_roundtrips(kind):
    d = {'kind': kind, 'id': 'd', 'evidence': []}
    if kind in ('ratio', 'percent_change'):
        d.update(numerator='m', denominator='m')
    elif kind == 'share':
        d.update(input='m', remove_dimensions=['t'], remove_filters=[])
    else:
        d.update(input='m', partition_by=[], order_by='t')
    p = model(dimensions=[time_dimension()], derived=[d])
    assert type(p).model_validate_json(p.model_dump_json(by_alias=True)) == p


@pytest.mark.parametrize('kind,level', [('column', None), ('hierarchy', 2)])
def test_dimension_bindings_levels_and_complete_counts(kind, level):
    p = model(dimensions=[{'kind': kind, 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'), 'path': [name('j')], 'level': level}]}],
              measures=[{'kind': 'distinct_count', 'id': 'c', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'as_reported', 'filters': []}])
    assert type(p).model_validate_json(p.model_dump_json(by_alias=True)) == p
    data = p.model_dump(mode='json', by_alias=True)
    data['dimensions'][0]['bindings'][0]['level'] = 0
    with pytest.raises(ValidationError):
        type(p).model_validate(data)


@pytest.mark.parametrize('reply', [{'kind': 'clarify', 'spans': [], 'candidates': [name('x')]},
                                   {'kind': 'abstain', 'spans': [], 'missing': ['a declared path']}])
def test_nonproposal_replies_and_clarification_history(reply):
    parsed = reply_adapter.validate_python(reply)
    assert reply_adapter.validate_json(parsed.model_dump_json()) == parsed
    clarification = Clarification(spans=[], slot='dimension', choices=[{'id': 'x', 'label': 'Readable'}])
    history = HistoryEntry(turn_id='t', question='Which?', plan=None, clarification=clarification, binding=vocabulary().binding)
    assert HistoryEntry.model_validate_json(history.model_dump_json()) == history


@pytest.mark.parametrize('update', [
    {'limit': 0}, {'limit': True}, {'measures': []},
    {'time': monthly_time('2025-06', '2025-04')},
    {'comparisons': [{'kind': 'previous_period', 'id': 'c', 'inputs': ['missing'], 'evidence': []}]},
    {'filters': [comparison_predicate(operand={'kind': 'output', 'id': 'missing'})]},
])
def test_invalid_full_plan_variants_are_rejected(update):
    with pytest.raises(ValidationError):
        model(**update)


def test_new_class_names_do_not_shadow_cited_demo_classes():
    docs = ROOT.parents[1] / 'docs/use-cases/kge/platform'
    cited = {name for p in docs.glob('*.md') for name in re.findall(r'`([A-Z]\w+)\.', p.read_text())}
    paths = [ROOT / 'kge/semantics' / f for f in ('plan.py', 'plancheck.py', 'planvocabulary.py', 'planperiods.py', 'coverage.py', 'compile.py')]
    paths.append(ROOT / 'kge/services/ask/engine/planner.py')
    assert not {node.name for p in paths for node in ast.parse(p.read_text()).body if isinstance(node, ast.ClassDef)} & cited
