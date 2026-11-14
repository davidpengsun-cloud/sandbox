from copy import deepcopy
from types import SimpleNamespace

import pytest

from kge.compiler import checks, emit
from tests.unit.test_compiler import committed


def variants():
    data, lock = deepcopy(committed())
    original = next(m for m in data['measures'] if m['id'] == 'wealth.net-new-assets')
    data['measures'] = [original, dict(original, id='wealth.gross', expression='sum(TRAN_FACT_1.TRAN_AM)')]
    return SimpleNamespace(data=data, lock=lock, at=lambda path: path)


def test_signed_and_gross_measures_share_a_column_with_consistent_metadata():
    pack = variants()
    assert checks.measures(pack) == []
    before = emit.columns(pack.data, pack.lock)
    pack.data['measures'].reverse()
    assert emit.columns(pack.data, pack.lock) == before


@pytest.mark.parametrize('field,value', [('grain', 'month'), ('currency', 'EUR'),
                                        ('calendar', 'calendar'), ('conversion', 'period_end_rate_from_EUR')])
def test_shared_column_rejects_conflicting_measure_metadata(field, value):
    pack = variants()
    pack.data['measures'][1][field] = value
    findings = checks.measures(pack)
    assert len(findings) == 1
    assert findings[0].rule == 'S08'
    assert 'must agree' in findings[0].message
