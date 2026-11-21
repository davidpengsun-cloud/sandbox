"""Column wordings resolve a checked column without becoming its preferred display name."""
from copy import deepcopy

import pytest

from kge.compiler import checks, emit
from kge.compiler.vocabulary import entries
from tests.unit.test_wording_concepts import pack


COLUMN = 'u.events.category'


def test_a_column_wording_emits_its_own_kind_and_the_existing_column_edge(tmp_path):
    p = pack(tmp_path, aliases=[{'phrase': 'selected rows', 'column_wording': COLUMN}])
    before = deepcopy(p.data)
    assert checks.aliases(p) == []
    assert not [finding for finding in checks.sections(p) if 'terminology.aliases' in finding.path]
    assert emit.NODES['Alias'](p.data, p.lock) == [
        {'phrase': 'selected rows', 'kind': 'column_wording', 'target': COLUMN, 'ordinal': 1, 'sources': []}]
    assert emit.alias_edges(p.data, p.lock) == [('Alias', 'Column', 'selected rows', COLUMN, {})]
    assert p.data == before


def test_a_column_wording_requires_a_column_declared_in_the_lock(tmp_path):
    p = pack(tmp_path, aliases=[{'phrase': 'selected rows', 'column_wording': 'u.events.missing'}])
    finding, = checks.aliases(p)
    assert finding.path.endswith('terminology.aliases[0].column_wording')
    assert 'not a column in pack.lock' in finding.message


@pytest.mark.parametrize('kind', ['column_wording', 'column', 'group_by'])
def test_duplicate_column_wordings_are_refused_across_target_kinds(tmp_path, kind):
    p = pack(tmp_path, aliases=[{'phrase': 'selected rows', 'column_wording': COLUMN},
                               {'phrase': 'SELECTED ROWS', kind: COLUMN}])
    finding, = checks.aliases(p)
    assert finding.path.endswith('terminology.aliases[1].phrase')
    assert ('is listed already' if kind == 'column_wording' else 'names ' + kind) in finding.message


def test_a_column_wording_stays_exclusive_with_other_alias_targets(tmp_path):
    p = pack(tmp_path, aliases=[{'phrase': 'selected rows', 'column_wording': COLUMN, 'column': COLUMN}])
    assert any(finding.path.endswith('terminology.aliases[0]') and 'names one of' in finding.message
               for finding in checks.sections(p))


def test_switching_to_a_column_wording_preserves_alias_counts_edges_and_window_policy(tmp_path):
    p = pack(tmp_path, aliases=[{'phrase': 'selected rows', 'column': COLUMN}])
    policy = [{'id': 'u.metric', 'moving_average_lengths': [2, 4]}]
    original_nodes = emit.NODES['Alias'](p.data, p.lock)
    original_edges = emit.alias_edges(p.data, p.lock)
    original_vocabulary = entries(p.data['terminology'], policy)
    p.data['terminology']['aliases'] = [{'phrase': 'selected rows', 'column_wording': COLUMN}]
    nodes = emit.NODES['Alias'](p.data, p.lock)
    assert len(nodes) == len(original_nodes) == 1 and nodes[0]['kind'] == 'column_wording'
    assert {key: value for key, value in nodes[0].items() if key != 'kind'} == {
        key: value for key, value in original_nodes[0].items() if key != 'kind'}
    assert emit.alias_edges(p.data, p.lock) == original_edges
    assert entries(p.data['terminology'], policy) == original_vocabulary == [
        {'id': 'moving-average:u.metric', 'kind': 'moving_average', 'params': {'measure': 'u.metric', 'lengths': [2, 4]}, 'ordinal': 1}]
