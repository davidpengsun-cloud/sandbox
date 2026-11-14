"""Explicit test-only replay injection; runtime mocks never discover files."""
import copy
import hashlib
import json

from kge.adapters.model import LLM
from kge.adapters.model.mock import ChatMock
from tests.scratch import DEMO


def load():
    return {row['key']: row['reply'] for path in sorted((DEMO / 'tests/fixtures/query-plan-v1').glob('*.json'))
            for row in json.loads(path.read_text())}


def recorded(mapping=None):
    llm = LLM('mock', 'mock')
    llm.chat = ChatMock(recordings=load() if mapping is None else mapping)
    return llm


def public_recorded(engine, question, principal, products=None, config=None):
    """Bind the recorded current-turn evidence to this request's release identity."""
    from kge.services.ask import Combined, ProductScope, parse
    from kge.services.ask.engine import planmodel, planrules

    cfg = config or Combined(engine, llm=recorded()).default_config()
    req = parse(question, engine.term_names, engine)
    vocab = planrules.vocabulary(engine, principal, "analysis", ProductScope(engine, products), cfg, req)
    data = planmodel.payload(question, vocab)
    mapping = copy.deepcopy(load())
    reply = mapping[data["recording_key"]]
    turn = hashlib.sha256(json.dumps([question, cfg.label, vocab.binding.model_dump(mode="json"), []],
                                    sort_keys=True).encode()).hexdigest()[:32]

    def bind(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "turn" and child == "turn":
                    value[key] = turn
                else:
                    bind(child)
        elif isinstance(value, list):
            for child in value:
                bind(child)

    bind(reply)
    return recorded(mapping)
