"""Transient unbound contexts never crash matching of declared metric qualifiers."""
import pytest

from kge.semantics.coverage import Piece, declared_part
from kge.semantics.plan import Span
from tests.plan_fixtures import model, vocabulary


@pytest.mark.parametrize('context', ['context:temporary', 'unbound'])
def test_declared_part_refuses_an_unbound_context_before_later_scope_validation(context):
    vocab = vocabulary()
    output, = model().measures
    output = output.model_copy(update={'context': output.context.model_copy(update={'id': context})})
    piece = Piece(Span(turn='turn', start=0, end=7), 'Group A', 'predicate', ('u.events.category',), 'eq', 'A')
    assert not declared_part(piece, output, vocab)
