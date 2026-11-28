"""Sample fan-out figures are preserved by aggregation before each declared join type."""
from copy import deepcopy
from decimal import Decimal

import pytest
import sqlglot

from kge.pipeline.sqlguard import canonical, conjuncts, schema_allowlist
from kge.ports import AUTHORED
from kge.services.data.engine.governance import declared_allowlist


KINDS = ('inner', 'left', 'right', 'full')
PROBES = 'DW_TRAN_ID BETWEEN 8800001 AND 8800003'
CONDITION = 'F_NNA_FIN.DW_ACCT_ID = ACCT_DIM2_1.DW_ACCT_ID AND F_NNA_FIN.ACCT_SRCE_SYS_ID = ACCT_DIM2_1.SRCE_SYS_ID'


def fanout_allowlist(eng, kind):
    """A scratch declaration deliberately reaches both account versions of the isolated sample cohort."""
    allow = deepcopy(declared_allowlist(eng, 'wealth', schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS)))
    allow.required = {identifier: requirement for identifier, requirement in allow.required.items()
                      if (requirement.get('join_contract', {}).get('source'), requirement.get('join_contract', {}).get('target'))
                      != ('F_NNA_FIN', 'ACCT_DIM2_1')}
    condition = frozenset(canonical(c, {}) for c in conjuncts(sqlglot.parse_one(CONDITION)))
    allow.required['sample_fanout'] = {'join_contract': {'source': 'F_NNA_FIN', 'target': 'ACCT_DIM2_1',
                                     'type': kind, 'cardinality': 'one-to-many', 'condition': condition}}
    return allow


def joined(kind, source):
    return (f'FROM {source} {kind.upper()} JOIN ACCT_DIM2_1 a '
            'ON t.DW_ACCT_ID = a.DW_ACCT_ID AND t.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID')


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('expression', ['SUM(t.NNA_AM)', 'COUNT(t.NNA_AM)', 'COUNT(*)'])
def test_sample_direct_fanout_aggregate_is_refused(eng, kind, expression):
    sql = f'SELECT {expression} ' + joined(kind, 'F_NNA_FIN t') + f' WHERE t.{PROBES}'
    verdict = eng.w.gate('wealth', sql, fanout_allowlist(eng, kind), dialect=AUTHORED)
    assert not verdict['passed']
    assert any('one-to-many' in problem for problem in verdict['problems']), verdict


@pytest.mark.parametrize('kind', KINDS)
def test_sample_preaggregation_equals_independent_source_figures(eng, kind):
    allow = fanout_allowlist(eng, kind)
    source = f'SELECT DW_ACCT_ID, ACCT_SRCE_SYS_ID, SUM(NNA_AM) AS total FROM F_NNA_FIN WHERE {PROBES} GROUP BY DW_ACCT_ID, ACCT_SRCE_SYS_ID'
    sql = ('WITH totals AS (' + source + ') SELECT DISTINCT t.DW_ACCT_ID, t.total '
           + joined(kind, 'totals t') + ' WHERE t.DW_ACCT_ID IS NOT NULL')
    verdict = eng.w.gate('wealth', sql, allow, dialect=AUTHORED)
    assert verdict['passed'], verdict['problems']
    actual = eng.w.rows('wealth', sql, AUTHORED)
    independent = source + ('' if kind in ('left', 'full') else ' HAVING DW_ACCT_ID <> 8800009')
    verdict = eng.w.gate('wealth', independent, allow, dialect=AUTHORED)
    assert verdict['passed'], verdict['problems']
    expected = {key: value for key, _, value in eng.w.rows('wealth', independent, AUTHORED)}
    assert expected == ({8800001: Decimal('23'), 8800002: Decimal('17'), 8800009: Decimal('11')}
                        if kind in ('left', 'full') else {8800001: Decimal('23'), 8800002: Decimal('17')})
    assert sorted(actual) == sorted(expected.items())
    assert sum(value for _, value in actual) == sum(expected.values())
