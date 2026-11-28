"""B2.1 (#514): the served graph holds baseline v1 of the question lexer's phrase table, and every phrasing of the golden
20 (sage_v1, originals and variants) lexes from it exactly as from the coded constant, piece for piece. The held-out
rewordings are not read here: the lane's ledger mirror (tests/integration/conftest.py) checks them where they are asked."""
import pytest

from kge.estate.golden_sage import GOLDEN_20
from kge.eval import sets
from kge.semantics import coverage
from kge.services.ask.engine import planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.scope import ProductScope
from tests.scratch import BUILD

PHRASINGS = 112


def golden_phrasings() -> list[str]:
    """Every phrasing the set holds for the golden 20: each item's question, then its variants'."""
    held = sets.items(BUILD / 'sage_v1.json')
    out = {i['id']: [i['question']] for i in held if i.get('category') == GOLDEN_20 and not i.get('variant_of')}
    for i in held:
        if i.get('variant_of') in out:
            out[i['variant_of']].append(i['question'])
    assert len(out) == 20
    return [phrasing for phrasings in out.values() for phrasing in phrasings]


@pytest.fixture(scope='module')
def vocab(eng):
    """The rules path's vocabulary with no request: the default principal, an analysis question, no page scope, and the
    graph configuration."""
    cfg = Combined(eng, llm=None).default_config('graph')
    return planrules.vocabulary(eng, eng.profile.asking(None), 'analysis', ProductScope(eng, None), cfg)


def test_the_served_graph_holds_baseline_v1(eng):
    phrases = eng.semantics.graph_vocabulary.phrases
    assert phrases == coverage.BASELINE_PHRASES
    assert phrases.source == 'graph'
    assert not phrases.extended


def test_every_golden20_phrasing_lexes_the_same_from_the_graph_and_the_constant(vocab):
    phrasings = golden_phrasings()
    assert vocab.phrase_table.source == 'graph'
    differ = []
    for index, phrasing in enumerate(phrasings):
        graph, constant = coverage.mirror_ledgers(phrasing, 'turn', vocab)
        if graph != constant:
            differ.append(index)
    assert not differ, f'graph and constant ledgers differ on golden-20 phrasings at positions {differ}'
    print(f'{len(phrasings)} golden-20 phrasings: graph and constant ledgers identical')
    assert len(phrasings) == PHRASINGS


def test_a_baseline_graph_adds_no_lexing(vocab):
    assert vocab.baseline_ledger is vocab.lexical_ledger
