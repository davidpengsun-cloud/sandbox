"""Lexical entity selectors reuse the legacy entity node without dropping distinct predicates."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import exp

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Join
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_plan_compiler import connection


ROWS = [(date(2025, 4, 10), 10, 'A', 1, 'x'), (date(2025, 4, 11), 5, 'B', 1, 'y'),
        (date(2025, 4, 12), 20, 'A', 2, 'x')]


def declared(monkeypatch, raw=False, member=False, composite=False):
    sem = semantics()
    sem.entities['entity']['key'] = ['entity_id', 'source'] if composite else ['entity_id']
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {'metric': 'Metric'}, {}, {}
    sem.d.ENTITY_WORDS = {'entity': 'entity'}
    sem.d.VOCABULARY = ([{'kind': 'predicate', 'params': {'phrases': ['entity 1'],
        'column': 'u.events.entity_id', 'op': 'eq', 'value': 1}}] if raw else [])
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    aliases = [('Metric', 'ctx'), ('entity', 'entity'), ('key', 'u.events.entity_id'), ('amount', 'u.events.value')]
    if member:
        sem.tables['u.members'] = {'entity_id': 'INTEGER'}
        join = Join('member', 'key', 'events.entity_id=members.entity_id', None, None, 'u.events', 'u.members', False)
        sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=(join,))
        sem.declared['member'] = join
        aliases.append(('member key', 'u.members.entity_id'))
    scoped = vocabulary(sem, aliases=aliases)
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return engine, scoped


def ask(monkeypatch, text, **options):
    engine, scoped = declared(monkeypatch, **options)
    request = parse(text, engine.term_names, engine)
    assert request.entity == ('entity', '1')
    reply = planrules.read(engine, request, scoped, SimpleNamespace(graph=True, glossary=True))
    return reply, scoped


def execute(plan, scoped, text, member=False):
    checked = check_plan(plan, scoped, text)
    assert isinstance(checked, CheckedPlan), checked
    statement = compile_plan(checked).answer
    assert statement is not None and check(statement.sql, statement.allow) == []
    with connection(ROWS) as con:
        if member:
            con.execute('CREATE TABLE members(entity_id INT)')
            con.execute('INSERT INTO members VALUES (1)')
        figure = con.execute(statement.sql).fetchone()[0]
    return figure, statement.sql


@pytest.mark.parametrize('raw', [False, True])
def test_a_single_key_entity_keeps_one_legacy_node_and_one_sql_predicate(monkeypatch, raw):
    text = 'Metric for entity 1'
    reply, scoped = ask(monkeypatch, text, raw=raw)
    assert reply.kind == 'propose', reply
    predicate, = reply.plan.filters
    assert predicate.id == 'entity' and predicate.left.name.id == 'u.events.entity_id'
    assert not predicate.evidence and any('entity' in obligation.targets for obligation in reply.plan.obligations)
    figure, statement = execute(reply.plan, scoped, text)
    assert figure == 15
    equalities = [node for node in sqlglot.parse_one(statement, read='duckdb').find_all(exp.EQ)
                  if isinstance(node.this, exp.Column) and node.this.name == 'entity_id']
    assert len(equalities) == 1


@pytest.mark.parametrize('text,expected', [('Metric for entity 1 with amount equal to 5', 5),
                                          ('Metric for entity 1 with key equal to 2', None)])
def test_different_columns_and_values_are_not_coalesced_with_an_entity(monkeypatch, text, expected):
    reply, scoped = ask(monkeypatch, text)
    assert reply.kind == 'propose', reply
    assert len(reply.plan.filters) == 2
    assert sum(predicate.id == 'entity' for predicate in reply.plan.filters) == 1
    assert execute(reply.plan, scoped, text)[0] == expected


def test_another_declared_role_keeps_its_own_predicate(monkeypatch):
    text = 'Metric for entity 1 with member key equal to 1'
    reply, scoped = ask(monkeypatch, text, member=True)
    assert reply.kind == 'propose', reply
    predicates = {predicate.left.name.id: predicate for predicate in reply.plan.filters}
    assert set(predicates) == {'u.events.entity_id', 'u.members.entity_id'}
    assert predicates['u.events.entity_id'].id == 'entity' and not predicates['u.events.entity_id'].left.path
    assert [role.id for role in predicates['u.members.entity_id'].left.path] == ['member']
    assert execute(reply.plan, scoped, text, member=True)[0] == 15


def test_a_composite_entity_selector_is_still_refused(monkeypatch):
    reply, _ = ask(monkeypatch, 'Metric for entity 1', composite=True)
    assert reply.kind == 'abstain' and 'complete declared key' in ' '.join(reply.missing)
