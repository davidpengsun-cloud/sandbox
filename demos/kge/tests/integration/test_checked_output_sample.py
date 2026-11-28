"""Sample figures retain their checked output bindings and independent SQL result."""
from decimal import Decimal

import pytest
import sqlglot
from sqlglot import exp

from kge.ports import AUTHORED
from kge.semantics.compile import compile_plan
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_derived_shapes_meridian import reference


@pytest.mark.parametrize('mutation', ['redirect value', 'extra aggregate', 'extra window'])
def test_sample_output_binding_refuses_unchecked_projections(eng, mutation):
    text = 'NNA for Jan 2025'
    answer = eng.answer(text, 'analyst_enterprise')
    assert answer['behaviour'] == 'figure', answer.get('reason')
    figure = Decimal(answer['result']['rows'][0]['cells'][0]['value']['value'])
    assert figure == reference(eng, first='2025-01-01', last='2025-02-01')[()]
    turn = answer['history'][-1]
    checked = check_plan(QueryPlan.model_validate(turn['plan']), vocabulary(eng, text), text, turn['turn_id'])
    assert isinstance(checked, CheckedPlan), checked
    pending = compile_plan(checked)
    if pending.release is not None:
        release = pending.release
        assert eng.w.gate(release.units[0], release.sql, release.allow, dialect=AUTHORED)['passed']
        pending = compile_plan(checked, eng.w.rows(release.units[0], release.sql, AUTHORED))
    statement = pending.answer
    assert eng.w.gate(statement.units[0], statement.sql, statement.allow, dialect=AUTHORED)['passed']
    tree = sqlglot.parse_one(statement.sql, read=AUTHORED)
    base = next(cte.this for cte in tree.find_all(exp.CTE) if cte.alias == 'b0')
    value = next(output for output in base.expressions if output.alias == 'value')
    if mutation == 'redirect value':
        value.set('alias', exp.to_identifier('unused'))
        extra = exp.alias_(exp.Literal.number(0), 'value')
    elif mutation == 'extra aggregate':
        extra = exp.alias_(exp.Max(this=next(value.find_all(exp.Column)).copy()), 'extra')
    else:
        extra = exp.alias_(exp.Window(this=exp.Max(this=exp.Literal.number(1))), 'extra')
    base.select(extra, append=True, copy=False)
    guard = eng.w.gate(statement.units[0], tree.sql(AUTHORED), statement.allow, dialect=AUTHORED)
    assert not guard['passed'], guard
    assert any('different expressions' in problem for problem in guard['problems']), guard
