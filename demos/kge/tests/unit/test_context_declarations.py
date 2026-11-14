"""Context defaults and terminology bindings fail closed at pack compilation."""
import pytest

from kge.compiler.checks import contexts
from kge.compiler.pack import read
from kge.pipeline.settings import ROOT


@pytest.mark.parametrize('field,value,reason', [
    ('ytd_calendar', 'missing', 'declared calendar'),
    ('bindings', [], 'declared columns or entities'),
    ('bindings', {'wealth.RGN_DIM_1.RGN_CD': 'missing'}, 'declared columns or entities'),
    ('bindings', {'wealth.RGN_DIM_1.RGN_CD': 'wealth.HSHLD_DIM_1.HSHLD_DK'}, 'context reach'),
    ('bindings', {'wealth_household': 'wealth.ACCT_DIM2_1.LAST_RCV_HSHLD_DK'}, 'column/entity kind'),
    ('bindings', {'wealth.ACCT_DIM2_1.ACCT_NM': 'wealth.ACCT_DIM2_1.LAST_RCV_HSHLD_DK'}, 'unrestricted names'),
])
def test_invalid_context_declaration_is_rejected(field, value, reason):
    pack = read(ROOT / 'clients', 'meridian')
    ctx = next(c for c in pack.data['contexts'] if c['id'] == 'nna')
    ctx[field] = value
    assert any(reason in f.message for f in contexts(pack))


def test_declared_context_defaults_and_bindings_are_valid():
    assert contexts(read(ROOT / 'clients', 'meridian')) == []


@pytest.mark.parametrize('extra', [[], ['ytd_calendar', 'bindings']])
def test_context_property_detection_reads_the_pinned_compiled_schema(tmp_path, extra):
    from types import SimpleNamespace
    from kge.services.ask.engine.graphview import Graph

    folder = tmp_path / 'invented' / 'pinned' / 'data/nodes'
    folder.mkdir(parents=True)
    columns = ['id', 'label', 'description', *extra]
    (folder / 'Context.csv').write_text(','.join(columns) + '\n')
    graph = Graph.__new__(Graph)
    graph.profile = SimpleNamespace(artifacts=tmp_path, client='invented', model_hash=lambda: 'pinned')
    assert graph.context_properties() == set(columns)


def test_golden_retirement_declares_exact_prior_version_without_changing_other_items():
    import json
    from kge.eval.sets import read, undeclared_change

    folder = ROOT / 'clients/meridian'
    path = folder / 'fixture/golden_wealth_v1.json'
    baseline = json.loads((folder / 'baselines/issue-316.json').read_text())['sets']['golden_wealth_v1']
    data = read(path)
    assert data['version'] == 'v3'
    assert data['changes_from']['version'] == 'v2'
    assert data['changes_from']['items'] == ['W01', 'W02', 'W03', 'W04', 'W05', 'W06', 'W07', 'W09', 'W10', 'W12', 'W14', 'W15', 'W18'] and data['changes_from']['removed'] == ['W08', 'XB3', 'XB3-W']
    assert undeclared_change(path, baseline) is None
