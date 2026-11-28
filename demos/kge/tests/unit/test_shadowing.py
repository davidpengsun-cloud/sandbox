"""The two rules planners are read once and compared on their checked replies, without executing or storing them."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from kge.semantics.plan import Abstain, Clarify, Name
from kge.services.ask.engine.shadowing import Shadowing


def decline(text='not known'):
    return Abstain(kind='abstain', spans=(), missing=(text,))


class Coded:
    name = 'coded'

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def plan(self, question):
        self.calls.append(question)
        return self.reply


class Graph:
    def __init__(self, reply, shapes=('shape.a',)):
        self.reply, self.shapes, self.calls = reply, shapes, []

    def read(self, question):
        self.calls.append(question)
        return self.reply, self.shapes


def test_each_adapter_is_read_once_and_a_supplied_coded_reply_is_reused():
    question = object()
    first, second = decline('coded'), decline('graph')
    coded, graph = Coded(first), Graph(second, ('shape.a', 'shape.b', 'shape.a'))
    comparison = Shadowing(coded, graph).read(question, check=lambda reply: reply)
    assert coded.calls == [question] and graph.calls == [question]
    assert comparison.coded is first and comparison.graph is second
    assert comparison.graph_shapes == ('shape.a', 'shape.b')
    assert comparison.outcome == 'agree'
    again = Shadowing(coded, graph).read(question, coded=first, check=lambda reply: reply)
    assert again.coded is first and coded.calls == [question] and graph.calls == [question, question]


def test_comparison_uses_checked_replies_without_changing_the_served_coded_reply():
    coded_reply, graph_reply = decline('coded'), decline('graph')
    clarification = Clarify(kind='clarify', spans=(), candidates=(Name(id='meaning:a', evidence=()),))
    calls = []

    def check(reply):
        calls.append(reply)
        return clarification if reply is graph_reply else reply

    compared = Shadowing(Coded(coded_reply), Graph(graph_reply)).read(object(), check=check)
    assert calls == [coded_reply, graph_reply]
    assert compared.coded is coded_reply and compared.graph is graph_reply
    assert compared.checked_graph is clarification and compared.checked_coded is coded_reply
    assert compared.outcome == 'declines_differ'


def test_one_redacted_observation_records_both_sides_and_the_matched_shapes():
    coded, graph = decline('private coded text'), decline('private graph text')
    compared = Shadowing(Coded(coded), Graph(graph)).read(object(), check=lambda reply: reply)
    observation = compared.observe(release='rel-1', served='coded', pieces=(),
                                   vocab=SimpleNamespace(entries={}), new_phrase=True,
                                   new_id=lambda: 'ab' * 16)
    assert observation.comparison == 'agree'
    assert observation.graph_shapes == ('shape.a',)
    assert observation.shapes == ('shape.a',)
    assert observation.new_phrase is True and observation.evidence is None
    assert 'private' not in str(asdict(observation))


@pytest.mark.parametrize('bad_side', ('coded', 'graph', 'check'))
def test_invalid_reply_is_refused_instead_of_becoming_evidence(bad_side):
    coded = Coded(object() if bad_side == 'coded' else decline())
    graph = Graph(object() if bad_side == 'graph' else decline())
    check = (lambda reply: object()) if bad_side == 'check' else (lambda reply: reply)
    with pytest.raises(TypeError):
        Shadowing(coded, graph).read(object(), check=check)
