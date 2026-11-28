"""Bare display names use uniquely declared code members; explicit name filters stay literal."""
from copy import deepcopy
import datetime as dt
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger, member_value
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_plan_compiler import connection


CODE = 'u.events.category'
DISPLAY = 'u.events.source'
MEMBERS = [{'code': 'A', 'name': 'Prime Circle'}, {'code': 'B', 'name': 'Broad Circle'}]
ROWS = [(dt.date(2025, 4, 10), 10, 'A', 1, 'Earlier name'),
        (dt.date(2025, 4, 10), 20, 'A', 2, 'Prime Circle'),
        (dt.date(2025, 4, 10), 7, 'B', 3, 'Prime Circle')]


def pair(code=CODE, display=DISPLAY, members=MEMBERS):
    return {'kind': 'code_name_pair', 'params': {'code': code, 'name': display, 'members': deepcopy(members)}}


def values():
    return [Entry('code-a', 'value', 'A', 'u', column=CODE, value='A'),
            Entry('code-b', 'value', 'B', 'u', column=CODE, value='B'),
            Entry('display', 'value', 'Prime Circle', 'u', column=DISPLAY, value='Prime Circle')]


def prepared(monkeypatch):
    sem = semantics()
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {'metric': 'Metric'}, {}
    sem.d.VOCABULARY = [pair(),
        {'kind': 'value', 'params': {'column': CODE, 'values': ['A', 'B']}},
        {'kind': 'value', 'params': {'column': DISPLAY, 'values': ['Prime Circle', 'Broad Circle']}},
        {'kind': 'predicate', 'params': {'phrases': ['segment name Prime Circle'], 'column': DISPLAY,
                                       'op': 'eq', 'value': 'Prime Circle'}}]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    sem.d.INTENTS = {}
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    scoped = vocabulary(sem, values=values(), aliases=[('Metric', 'ctx')])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return engine, scoped


def ask(engine, scoped, text):
    reply = planrules.read(engine, parse(text, engine.term_names, engine), scoped,
                           SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, scoped, text)
    assert isinstance(checked, CheckedPlan), checked
    statement = compile_plan(checked).answer
    assert statement is not None and check(statement.sql, statement.allow) == []
    with connection(ROWS) as con:
        figure = con.execute(statement.sql).fetchone()[0]
    return reply.plan, figure


@pytest.mark.parametrize('spelling', ['Prime Circle', 'prime circle', 'PRIME CIRCLE'])
def test_bare_display_spelling_uses_its_declared_code_and_hand_computed_figures(monkeypatch, spelling):
    engine, scoped = prepared(monkeypatch)
    text = 'Metric for ' + spelling
    member, = [piece for piece in ledger(text, 'turn', scoped) if piece.kind == 'predicate']
    assert member.candidates == (CODE,) and member.value == 'A'
    plan, figure = ask(engine, scoped, text)
    assert figure == 30
    predicate, = plan.filters
    assert predicate.left.name.id == CODE and predicate.value.value == 'A'
    wrong = plan.model_copy(update={'filters': (predicate.model_copy(update={
        'value': predicate.value.model_copy(update={'value': 'B'})}),)})
    assert not isinstance(check_plan(ground_plan(wrong, text, 'turn', scoped), scoped, text), CheckedPlan)


def test_explicit_name_column_selector_keeps_literal_name_figures(monkeypatch):
    engine, scoped = prepared(monkeypatch)
    text = 'Metric for segment name Prime Circle'
    member, = [piece for piece in ledger(text, 'turn', scoped) if piece.kind == 'predicate']
    assert member.candidates == (DISPLAY,) and member.value == 'Prime Circle'
    plan, figure = ask(engine, scoped, text)
    assert figure == 27
    predicate, = plan.filters
    assert predicate.left.name.id == DISPLAY and predicate.value.value == 'Prime Circle'


@pytest.mark.parametrize('change', ['undeclared', 'unknown-name', 'missing-code', 'wrong-code-role',
                                   'wrong-name-role', 'duplicate-members', 'duplicate-code-values', 'two-code-roles',
                                   'restricted-code'])
def test_name_only_resolution_grants_no_undeclared_ambiguous_or_other_role_code(change):
    sem = semantics()
    sem.tables['u.other'] = {'category': 'VARCHAR', 'source': 'VARCHAR'}
    sem.tables['u.events']['alternate'] = 'VARCHAR'
    rows, entries = [pair()], values()
    if change == 'undeclared':
        rows = []
    elif change == 'unknown-name':
        rows[0]['params']['members'] = [{'code': 'A', 'name': 'Another Circle'}]
    elif change in ('missing-code', 'wrong-code-role'):
        entries = [entry for entry in entries if entry.id != 'code-a']
        if change == 'wrong-code-role':
            entries.append(Entry('elsewhere', 'value', 'A', 'u', column='u.other.category', value='A'))
    elif change == 'wrong-name-role':
        rows[0]['params']['name'] = 'u.other.source'
    elif change == 'duplicate-members':
        rows[0]['params']['members'].append({'code': 'B', 'name': 'PRIME CIRCLE'})
    elif change == 'duplicate-code-values':
        entries.append(Entry('duplicate', 'value', 'A', 'u', column=CODE, value='A'))
    elif change == 'two-code-roles':
        rows.append(pair(code='u.events.alternate'))
        entries.append(Entry('alternate', 'value', 'A', 'u', column='u.events.alternate', value='A'))
    elif change == 'restricted-code':
        sem.d.RESTRICTED_COLUMNS.append(('u', 'events', 'category'))
    sem.d.VOCABULARY = rows
    scoped = vocabulary(sem, values=entries)
    assert member_value(('display',), scoped) is None


def test_two_existing_candidates_cannot_coalesce_to_a_different_declared_code():
    sem = semantics()
    sem.d.VOCABULARY = [pair()]
    scoped = vocabulary(sem, values=values())
    assert member_value(('display',), scoped).id == 'code-a'
    assert member_value(('code-a', 'display'), scoped).id == 'code-a'
    assert member_value(('code-b', 'display'), scoped) is None
