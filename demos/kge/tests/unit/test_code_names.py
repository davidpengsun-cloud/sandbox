"""People read a declared code's name, not its code (#603): a synthetic pack's code/name pair, its rows, text, table,
grouped card, filter notes and choice labels. The rows keep both code and name."""
from decimal import Decimal
from types import SimpleNamespace

from kge.semantics.planvocabulary import Entry, member_names, named_members, value_id
from kge.services.ask.engine.planner import scalar
from kge.services.ask.engine.planpublic import question_filter_notes, render
from kge.services.ask.engine.present import result_table
from kge.services.ask.engine.reading import dimension_value, grouped

CODE, NAME = 'unit.segment.segment_code', 'unit.segment.segment_name'
PAIR = {'kind': 'code_name_pair', 'params': {'code': CODE, 'name': NAME, 'members': [
    {'code': 'S4100', 'name': 'Affluent'}, {'code': 'S4200', 'name': 'Affluent'}, {'code': 'P1', 'name': 'Private Clients'}]}}


def row(code, amount, name=None, total=False):
    cell = {'value': scalar(Decimal(amount) if amount is not None else None), 'citations': [], 'null_reasons': []}
    return {'panel': None, 'dimensions': [scalar(code)], 'cells': [cell], 'total': total,
            **({'dimension_labels': [name]} if name else {})}


def result(*rows):
    return {'columns': [{'id': 'segment', 'kind': 'dimension', 'label': 'Segment'},
                        {'id': 'nna', 'kind': 'measure', 'label': 'NNA', 'currency': 'USD', 'lineage': []}],
            'rows': list(rows)}


ROWS = (row('S4100', '10', 'Affluent'), row('S4200', '20', 'Affluent'), row('P1', '30', 'Private Clients'), row('US', '40'))


def test_a_named_code_reads_as_its_name_and_a_shared_name_adds_the_code():
    rows = list(ROWS)
    assert [dimension_value(r, 0, rows) for r in rows] == ['Affluent (S4100)', 'Affluent (S4200)', 'Private Clients', 'US']
    assert dimension_value(rows[0], 0) == 'Affluent'


def test_a_period_still_reads_in_words():
    assert dimension_value({**row('2024-01', '1'), 'dimension_text': ['January 2024']}, 0) == 'January 2024'


def test_answer_text_and_the_rich_table_show_names_while_the_rows_keep_codes():
    data = result(*ROWS, row(None, '100', total=True))
    text = render(data)
    assert text.splitlines()[:4] == ['Affluent (S4100): NNA: USD 10', 'Affluent (S4200): NNA: USD 20',
                                     'Private Clients: NNA: USD 30', 'US: NNA: USD 40']
    assert 'S4100 ·' not in text and 'P1' not in text
    assert [r[0] for r in result_table(data)['rows']] == ['Affluent (S4100)', 'Affluent (S4200)', 'Private Clients', 'US', 'Total']
    assert [r['dimensions'][0]['value'] for r in data['rows'][:4]] == ['S4100', 'S4200', 'P1', 'US']
    assert data['rows'][0]['dimension_labels'] == ['Affluent']


def test_the_grouped_rows_read_as_names_and_keep_their_codes():
    answer = {'rows': {'P1': 30, 'S4100': 10, 'US': 40}, 'group_by': 'segment_code',
              'result': result(ROWS[0], ROWS[2], ROWS[3], row(None, '80', total=True))}
    assert grouped(answer) == [('US', 40), ('Private Clients', 30), ('Affluent', 10)]
    ranked = {**answer, 'rows': {'top 1: P1': 30}}
    assert grouped(ranked) == [('top 1: Private Clients', 30)]
    assert set(answer['rows']) == {'P1', 'S4100', 'US'}


def test_a_filter_note_reads_the_declared_name_and_an_unnamed_value_as_it_is():
    span = SimpleNamespace(start=0, end=8, turn='t')

    def compare(value):
        return SimpleNamespace(id='f', kind='compare', op='eq', magnitude=False, evidence=(span,),
                               left=SimpleNamespace(kind='column', name=SimpleNamespace(id=CODE)), value=SimpleNamespace(value=value))
    names = member_names([PAIR])
    for value, shown in (('P1', 'Private Clients'), ('S4100', 'Affluent'), ('ZZ', 'ZZ')):
        plan = SimpleNamespace(filters=(compare(value),), measures=(), obligations=())
        assert question_filter_notes(plan, 'Privates', 't', names) == [f'filter read from "Privates": segment.segment_code = {shown}']
    plan = SimpleNamespace(filters=(compare('P1'),), measures=(), obligations=())
    assert question_filter_notes(plan, 'Privates', 't') == ['filter read from "Privates": segment.segment_code = P1']


def test_a_coded_value_entry_takes_its_declared_name_the_code_added_where_the_name_would_read_as_another():
    def value(column, v):
        return Entry(value_id(column, v), 'value', str(v), 'unit', column=column, value=v)
    pair = {**PAIR, 'params': {**PAIR['params'], 'members': [*PAIR['params']['members'], {'code': 'G1', 'name': 'Gold'},
                                                            {'code': 'X9', 'name': 'X9'}]}}
    entries = {e.id: e for e in (value(CODE, 'P1'), value(CODE, 'S4100'), value(CODE, 'S4200'), value(CODE, 'G1'),
                                 value(NAME, 'Private Clients'), value(CODE, 'X9'), value(CODE, 'US'))}
    phrases = {e.label.casefold(): {i} for i, e in entries.items()}
    labelled = named_members(entries, [pair], phrases)
    assert {e.value: e.label for e in labelled.values()} == {
        'P1': 'Private Clients (P1)', 'S4100': 'Affluent (S4100)', 'S4200': 'Affluent (S4200)', 'G1': 'Gold',
        'Private Clients': 'Private Clients', 'X9': 'X9', 'US': 'US'}
    assert len({e.label for e in labelled.values()}) == len(labelled)
    assert all(phrases[e.label.casefold()] >= {e.id} for e in labelled.values())
    assert phrases['private clients'] == {value(NAME, 'Private Clients').id}
    assert all(labelled[i].id == i and labelled[i].column == entries[i].column for i in entries)
