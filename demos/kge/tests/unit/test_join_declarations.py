"""Declared join types and inclusive windows have observable, hand-computed figures."""
import datetime as dt
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from kge.compiler.checks import SECTIONS, join_window_problems, joins, shape
from kge.compiler.pack import Pack
from kge.compiler.vocabulary import join_entries
from kge.pipeline.sqlguard import check
from kge.semantics.query import Join
from tests.plan_fixtures import model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import compiled, connection


WINDOW = {'date': 'u.events.occurred', 'effective': 'u.members.effective', 'expiry': 'u.members.expiry'}


def declaration(**updates):
    return {'id': 'member_join', 'kind': 'key', 'from': 'u.events', 'to': 'u.members',
            'cardinality': 'one-to-one',
            'condition': 'events.entity_id = members.entity_id AND events.source = members.source', **updates}


def pack(*declared):
    sem = semantics(joined=True)
    sem.tables['u.members'].update(effective='DATE', expiry='DATE')
    lock = {'sources': {'u': {'tables': [
        {'name': table.split('.')[1], 'columns': [{'name': name, 'type': kind} for name, kind in columns.items()]}
        for table, columns in sem.tables.items()]}}}
    return Pack('sample', Path('.'), data={'joins': list(declared)}, lock=lock)


def fixture(join_type=None, window=False, versioned=False):
    sem = semantics(joined=True)
    sem.tables['u.members'].update(effective='DATE', expiry='DATE')
    if versioned:
        sem.tables['u.members']['current'] = 'BOOLEAN'
        sem.scd2['u.members'] = {'is_current': 'current', 'valid_from': 'effective', 'valid_to': 'expiry',
                               'valid_to_inclusive': True}
    sem.d.GROUP_BY = {'by grouping': 'u.members.grouping'}
    options = {**({'type': join_type} if join_type is not None else {}), **({'validity_window': WINDOW} if window else {})}
    original = sem.contexts['ctx'].joins[0]
    join = Join(original.id, original.kind, original.condition, None, None, original.source, original.target, True, options)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=(), joins=(join,))
    sem.declared = {join.id: join}
    sem.types = {table + '.' + name: kind for table, columns in sem.tables.items() for name, kind in columns.items()}
    v = vocabulary(sem)
    dimension = {'kind': 'column', 'id': 'group', 'bindings': [
        {'context': name('ctx'), 'column': name('u.members.grouping'), 'path': [name('member_join')], 'level': None}]}
    return compiled(model(dimensions=[dimension]), 'Metric by grouping', {'by': 'group', 'grouping': 'group'}, v)


def rows(compilation, historical=False):
    assert check(compilation.answer.sql, compilation.answer.allow) == []
    observations = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'), (dt.date(2025, 4, 10), 20, 'B', 2, 'x')]
    with connection(observations) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR, effective DATE, expiry DATE)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'matched', DATE '2025-01-01', DATE '2025-12-31'), "
                    "(3, 'x', 'right only', DATE '2025-01-01', DATE '2025-12-31')")
        if historical:
            con.execute("INSERT INTO members VALUES (1, 'x', 'old version', DATE '2024-01-01', DATE '2024-12-31')")
        return {row[0]: row[1] for row in con.execute(compilation.answer.sql).fetchall()}


@pytest.mark.parametrize('join_type,expected', [
    ('inner', {'matched': 10}),
    ('left', {'matched': 10, None: 20}),
    ('right', {'matched': 10, 'right only': None}),
    ('full', {'matched': 10, None: 20, 'right only': None}),
])
def test_each_declared_join_type_keeps_its_hand_computed_rows(join_type, expected):
    assert rows(fixture(join_type)) == expected


@pytest.mark.parametrize('join_type', ['inner', 'left', 'right', 'full'])
def test_declared_window_excludes_an_extra_version(join_type):
    without = rows(fixture(join_type), historical=True)
    with_window = rows(fixture(join_type, window=True), historical=True)
    assert without['old version'] == 10
    assert with_window.get('old version') is None
    assert with_window['matched'] == 10
    assert with_window != without


@pytest.mark.parametrize('join_type', ['inner', 'left', 'right', 'full'])
def test_declared_between_window_already_satisfies_the_version_policy(join_type):
    assert rows(fixture(join_type, window=True, versioned=True), historical=True) == rows(fixture(join_type, window=True), historical=True)


def test_window_between_includes_both_boundary_dates():
    result = fixture('inner', window=True)
    assert check(result.answer.sql, result.answer.allow) == []
    observations = [(dt.date(2025, 1, 1), 7, 'A', 1, 'x'), (dt.date(2025, 12, 31), 11, 'A', 1, 'x'),
                    (dt.date(2024, 12, 31), 19, 'A', 1, 'x'), (dt.date(2026, 1, 1), 23, 'A', 1, 'x')]
    with connection(observations) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR, effective DATE, expiry DATE)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'matched', DATE '2025-01-01', DATE '2025-12-31')")
        assert con.execute(result.answer.sql).fetchone()[1] == 7 + 11


def test_default_inner_keeps_existing_sql_and_serialised_join_fields():
    assert fixture().answer.sql == fixture('inner').answer.sql
    plain = Join('j', 'key', 'events.entity_id = members.entity_id', None, None, 'u.events', 'u.members', False)
    assert set(asdict(plain)) == {'id', 'kind', 'condition', 'alias', 'through', 'source', 'target', 'required'}
    assert plain.type == 'inner' and plain.predicate == plain.condition
    assert join_entries({'joins': [declaration()]}) == [
        {'id': 'join-options:member_join', 'kind': 'join_options', 'ordinal': 1,
         'params': {'join': 'member_join', 'cardinality': 'one-to-one'}}]


def test_explicit_join_metadata_uses_the_existing_vocabulary_shape():
    entry, = join_entries({'joins': [declaration(type='left', validity_window=WINDOW)]})
    assert entry == {'id': 'join-options:member_join', 'kind': 'join_options', 'ordinal': 1,
                     'params': {'join': 'member_join', 'type': 'left', 'validity_window': WINDOW, 'cardinality': 'one-to-one'}}


def test_check_pack_reports_missing_join_cardinality():
    subject = declaration()
    subject.pop('cardinality')
    findings = []
    shape([subject], SECTIONS['joins'], 'joins', findings)
    assert findings == [('joins[0].cardinality', 'is required')]


@pytest.mark.parametrize('cardinality', ['many-to-many', 'many-to-one', '', None])
def test_check_pack_reports_unknown_join_cardinality(cardinality):
    findings = []
    shape([declaration(cardinality=cardinality)], SECTIONS['joins'], 'joins', findings)
    assert findings == [('joins[0].cardinality', f'is one of one-to-one, one-to-many, not {cardinality!r}')]


@pytest.mark.parametrize('cardinality', ['one-to-one', 'one-to-many'])
def test_check_pack_accepts_and_emits_declared_join_cardinality(cardinality):
    findings = []
    subject = declaration(cardinality=cardinality)
    shape([subject], SECTIONS['joins'], 'joins', findings)
    assert findings == []
    assert join_entries({'joins': [subject]})[0]['params']['cardinality'] == cardinality


def test_check_pack_reports_unknown_join_type():
    findings = []
    shape([declaration(type='semi')], SECTIONS['joins'], 'joins', findings)
    assert findings == [('joins[0].type', "is one of inner, left, right, full, not 'semi'")]


@pytest.mark.parametrize('window,problem', [
    ({**WINDOW, 'date': 'u.events.missing'}, 'not a column'),
    ({**WINDOW, 'date': 'u.events.value'}, 'not a date column'),
    ({**WINDOW, 'effective': 'u.members.entity_id'}, 'not a date column'),
    ({**WINDOW, 'expiry': 'u.members.entity_id'}, 'not a date column'),
    ({**WINDOW, 'expiry': WINDOW['effective']}, 'each date column once'),
    ({**WINDOW, 'expiry': 'u.events.occurred'}, 'same joined side'),
    ({'date': WINDOW['date'], 'effective': WINDOW['effective']}, 'exactly date, effective and expiry'),
])
def test_check_pack_reports_each_invalid_window(window, problem):
    subject = declaration(validity_window=window)
    assert any(problem in finding for finding in join_window_problems(subject, pack(subject).lock))


def test_check_pack_rejects_identical_declarations_but_allows_type_variants():
    assert any('duplicates join' in finding.message for finding in joins(pack(declaration(), declaration(id='duplicate'))))
    assert joins(pack(declaration(), declaration(id='left_variant', type='left'))) == []


@pytest.mark.parametrize('condition', [
    'events.entity_id=members.entity_id AND events.source=members.source',
    'members.source = events.source AND members.entity_id = events.entity_id',
    'events.entity_id = members.entity_id /* same key */ AND events.source = members.source',
    'u.events.entity_id = u.members.entity_id AND u.events.source = u.members.source',
    'u.events.entity_id = members.entity_id AND events.source = u.members.source',
])
def test_duplicate_join_authority_cannot_conflict_on_cardinality(condition):
    first = declaration(cardinality='one-to-many')
    duplicate = declaration(id='duplicate', cardinality='one-to-one', condition=condition)
    assert any('duplicates join' in finding.message for finding in joins(pack(first, duplicate)))


def test_duplicate_detection_preserves_the_case_of_literal_values():
    first = declaration(condition="events.entity_id = members.entity_id AND members.grouping = 'Group'")
    second = declaration(id='second', condition="events.entity_id = members.entity_id AND members.grouping = 'group'")
    assert joins(pack(first, second)) == []


def test_a_window_in_the_condition_cannot_hide_conflicting_duplicate_authority():
    first = declaration(cardinality='one-to-many', validity_window=WINDOW)
    second = declaration(id='duplicate', cardinality='one-to-one', condition=first['condition'] +
                         ' AND events.occurred BETWEEN members.effective AND members.expiry')
    assert any('duplicates join' in finding.message for finding in joins(pack(first, second)))


def test_check_pack_reports_a_restricted_window_column():
    subject = declaration(validity_window=WINDOW)
    assert any('restricted personal data' in problem for problem in join_window_problems(subject, pack(subject).lock, {WINDOW['date']}))
