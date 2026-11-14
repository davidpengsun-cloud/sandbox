"""The existing numerical adversaries keep their assertions after evidence grounding."""
import pytest

from kge.semantics.coverage import ground_plan
from tests.integration import test_ask_gates as gates
from tests.integration.test_ask_gates import comparison_engine as comparison_engine


@pytest.fixture(autouse=True)
def grounded_model_replies(monkeypatch):
    original = gates.read
    def read(provider, text, vocabulary, history=(), turn='turn'):
        reply = original(provider, text, vocabulary, history, turn)
        return (reply.model_copy(update={'plan': ground_plan(reply.plan, text, turn, vocabulary)})
                if reply.kind == 'propose' else reply)
    monkeypatch.setattr(gates, 'read', read)


for _name in dir(gates):
    if _name.startswith('test_'):
        globals()['test_grounding_' + _name.removeprefix('test_')] = getattr(gates, _name)
