import json

import pytest
from pydantic import ValidationError

from kge.semantics.plan import DecimalValue, Period, QueryPlan, Propose, Result, reply_adapter
from tests.plan_fixtures import model, name


def test_strict_roundtrip_and_schema():
    p = model()
    assert QueryPlan.model_validate_json(p.model_dump_json(by_alias=True)) == p
    schema = QueryPlan.model_json_schema()
    assert set(schema['required']) == set(schema['properties'])
    assert schema['additionalProperties'] is False
    proposal = Propose(kind='propose', base_turn='prior', plan=p)
    assert reply_adapter.validate_json(proposal.model_dump_json(by_alias=True)) == proposal
    assert Propose(kind='propose', base_turn=None, plan=p).base_turn is None
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(p.model_dump() | {'sql': 'select 1'})
    with pytest.raises(ValidationError):
        p.layout = 'separate'
    assert isinstance(p.measures, tuple)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '1e3', '01', 1.5, True])
def test_decimal_rejects_noncanonical_or_nontext(value):
    with pytest.raises(ValidationError):
        DecimalValue(kind='decimal', value=value)


@pytest.mark.parametrize('kind,value', [('day', '2025-02-30'), ('month', '2025-13'), ('year', '0'), ('calendar_quarter', '2025-Q5')])
def test_invalid_period(kind, value):
    with pytest.raises(ValidationError):
        Period(kind=kind, value=value, calendar=name('cal'))


def test_outputs_cycles_and_missing_references():
    with pytest.raises(ValidationError, match='cycle'):
        model(derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'r', 'denominator': 'm', 'evidence': []}])
    with pytest.raises(ValidationError, match='missing'):
        model(order=[{'output': 'missing', 'direction': 'desc', 'nulls': 'last', 'evidence': []}])
    p = model(comparisons=[{'kind': 'previous_year', 'id': 'c', 'inputs': ['m'], 'evidence': []}],
              derived=[{'kind': 'percent_change', 'id': 'r', 'numerator': 'c/m/base', 'denominator': 'c/m/other', 'evidence': []}])
    assert {'c/m/base', 'c/m/other', 'r'} <= p.outputs()


def test_result_roundtrips_without_untyped_cells():
    result = Result.model_validate({'columns': [], 'rows': [{'panel': None, 'dimensions': [], 'cells': [
        {'value': {'kind': 'null'}, 'citations': ['citation'], 'null_reasons': ['missing_observation']}]}]})
    assert Result.model_validate_json(result.model_dump_json()) == result
    assert json.loads(result.model_dump_json())['rows'][0]['cells'][0]['value'] == {'kind': 'null'}


@pytest.mark.parametrize('kind,value', [('fiscal_quarter', 'FY26-Q1'), ('fiscal_year', 'FY2026'), ('year', '2025'), ('calendar_quarter', '2025-Q2'), ('day', '2025-02-28')])
def test_every_valid_period_form_roundtrips(kind, value):
    p = Period(kind=kind, value=value, calendar=name('cal'))
    assert Period.model_validate_json(p.model_dump_json()) == p


def test_every_time_form_and_full_history_roundtrip():
    from kge.semantics.plan import HistoryEntry
    from tests.plan_fixtures import monthly_time, vocabulary
    for time in [monthly_time(), {'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}},
                 {'kind': 'ytd', 'through': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}, 'calendar': name('cal')},
                 {'kind': 'latest_released', 'calendar': name('cal')}]:
        plan = model(time=time)
        entry = HistoryEntry(turn_id='prior', question='Metric', plan=plan, clarification=None, binding=vocabulary().binding)
        assert HistoryEntry.model_validate_json(entry.model_dump_json(by_alias=True)) == entry
