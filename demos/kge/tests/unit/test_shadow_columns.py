"""Schema used by a CPU-only shadow plan comes from the serving request's existing reads."""
from types import SimpleNamespace

from kge.services.ask.engine import shadowcolumns
from kge.services.ask.engine.core import Engine


def test_schema_guess_records_existing_reads_without_extra_warehouse_calls():
    calls = []
    data = {'a': [('income', 'amount', 'DECIMAL'), ('income', 'amount_alt', 'DECIMAL')],
            'b': [('expense', 'value', 'INTEGER')]}

    def columns(unit):
        calls.append(unit)
        return data[unit]

    engine = Engine.__new__(Engine)
    engine.UNIT_ORDER = ['a', 'b']
    engine.w = SimpleNamespace(columns=columns)
    with shadowcolumns.capture() as held:
        result = engine.guess(['income', 'amount'])
    assert calls == ['a', 'b']
    assert result == [{'unit': 'a', 'tbl': 'income', 'col': 'amount', 'dtype': 'DECIMAL', 'grain': '',
                       'currency': '', 'calendar': '', 'conversion': '', 'certified_by': '', 'certified_on': ''}]
    assert held == {unit: tuple(rows) for unit, rows in data.items()}
    data['a'].append(('later', 'change', 'INTEGER'))
    assert len(held['a']) == 2


def test_column_capture_is_request_scoped_and_restores_an_outer_capture():
    with shadowcolumns.capture() as outer:
        shadowcolumns.note('a', [('t', 'a', 'INT')])
        with shadowcolumns.capture() as inner:
            shadowcolumns.note('b', [('t', 'b', 'INT')])
        shadowcolumns.note('c', [('t', 'c', 'INT')])
    shadowcolumns.note('d', [('t', 'd', 'INT')])
    assert set(outer) == {'a', 'c'}
    assert set(inner) == {'b'}
