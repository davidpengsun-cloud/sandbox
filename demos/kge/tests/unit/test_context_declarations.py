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


def test_golden_wealth_holds_its_items_alone_and_is_the_set_the_current_baseline_ran():
    import json
    from kge.eval.sets import item_hashes, read

    folder = ROOT / 'clients/meridian'
    data = read(folder / 'fixture/golden_wealth_v1.json')
    assert data['version'] == 'v5' and set(data) == {'version', 'items'}
    v2 = json.loads((folder / 'baselines/issue-316.json').read_text())['sets']['golden_wealth_v1']
    retired = set(v2['item_sha256']) - {i['id'] for i in data['items']}
    assert sorted(retired) == ['W08', 'XB3', 'XB3-W'], "#402 retired these three; git history holds why"
    v5 = json.loads((folder / 'baselines/issue-474.json').read_text())['sets']['golden_wealth_v1']
    assert v5['version'] == 'v5' and v5['item_sha256'] == item_hashes(data['items']), \
        "the issue-474 baseline ran the set as the fixture holds it"
