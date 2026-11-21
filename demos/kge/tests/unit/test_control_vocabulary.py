"""Graph wordings do not change the schema arm's filter or count ownership."""
from types import SimpleNamespace

import pytest

from kge.semantics.coverage import counted_names, ledger
from kge.semantics.planvocabulary import Entry
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary


@pytest.mark.parametrize('graph', [False, True], ids=['schema', 'graph'])
def test_bare_member_names_shadow_raw_filters_only_on_the_graph_arm(monkeypatch, graph):
    sem = semantics()
    sem.tables['u.archive'] = {'category': 'VARCHAR'}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.INTENTS = {'metric': 'Metric'}, {}, {}
    columns = ('u.events.category', 'u.archive.category')
    sem.d.VOCABULARY = [{'kind': 'value', 'params': {'column': column, 'values': ['Harbor Circle']}}
                        for column in columns]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    scoped = vocabulary(sem, aliases=[('Metric', 'ctx')], values=[
        Entry('member-' + str(index), 'value', 'Harbor Circle', 'u', column=column, value='Harbor Circle')
        for index, column in enumerate(columns)])
    sem.measure_declarations, sem.measure_specs = {}, scoped.measures
    sem.output_metadata = {'ctx': {'tbl': 'events', 'col': 'value', 'dtype': 'DECIMAL'}}
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'],
                             guess=lambda _: [{'unit': 'u', 'tbl': 'events', 'col': 'value'}])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    question = 'Metric for Harbor Circle'
    member, = (piece for piece in ledger(question, 'turn', scoped) if piece.text == 'Harbor Circle')
    assert member.kind == 'name' and len(member.candidates) == 2
    request = parse(question, engine.term_names if graph else [], engine, aliases=graph)
    assert set(request.filtered) == set(columns)
    config = SimpleNamespace(graph=graph, glossary=graph, resolution='glossary' if graph else 'words')
    reply = planrules.read(engine, request, scoped, config)
    if graph:
        assert reply.kind == 'propose'
        assert reply.plan.filters == ()
    else:
        assert reply.kind == 'abstain'
        assert reply.missing == ('A predicate on u.archive.category has no declared role for every selected context.',)


def test_schema_count_ownership_keeps_the_nearest_noun_while_graph_ownership_extends_its_list():
    sem = semantics()
    sem.entities = {
        'account': {'label': 'Accounts', 'table': 'u.events', 'key': ['entity_id', 'source']},
        'transaction': {'label': 'Transactions', 'table': 'u.events', 'key': ['entity_id', 'source', 'occurred']},
    }
    scoped = vocabulary(sem, aliases=[('accounts', 'account'), ('transactions', 'transaction')])
    question = 'Transactions and accounts counts'
    pieces = ledger(question, 'turn', scoped)
    assert counted_names(pieces, question, scoped, coordinated=False) == {'account'}
    assert counted_names(pieces, question, scoped, coordinated=True) == {'account', 'transaction'}
    assert counted_names(pieces, question, scoped) == {'account', 'transaction'}
