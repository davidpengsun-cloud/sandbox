"""A count whose count context the vocabulary does not offer (a contract vocabulary, such as the control arm's) is the
rules' decline, never a KeyError: on the two-entity membership path, a declared count and a single membership."""
from dataclasses import replace
from types import SimpleNamespace

from kge.semantics.coverage import ledger
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, vocabulary


def two_entities(monkeypatch):
    sem = semantics(joined=True)
    sem.entities['member'] = {'label': 'Members', 'table': 'u.members', 'key': ['entity_id', 'source']}
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    return sem, engine


def test_a_two_entity_membership_whose_count_contexts_are_not_offered_is_a_decline_never_a_key_error(monkeypatch):
    sem, engine = two_entities(monkeypatch)
    full = vocabulary(sem)
    text = 'Entities also Members'
    pieces = ledger(text, 'turn', full)
    assert {i for p in pieces for i in p.candidates} >= {'entity', 'member'}
    assert any(p.operation == 'membership' for p in pieces)
    offered = replace(full, contexts={k: v for k, v in full.contexts.items() if not k.startswith('context:count:')})
    reply = planrules.read(engine, parse(text, ['Metric'], sem.d), offered, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'abstain'
