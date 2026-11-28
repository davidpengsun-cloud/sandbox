"""Scoped planning snapshots remain isolated and bounded without sending graph queries."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from kge.semantics.plan import Binding
from tests.unit.test_principal_ids import graph


def binding(**changes):
    return Binding(**{**dict(release_identity='release', access_fingerprint='ana', products=(), purpose='analysis'), **changes})


def test_simultaneous_questions_build_a_scoped_vocabulary_once():
    g, store = graph(['ana'])
    start = threading.Barrier(8)
    built = []
    def read():
        built.append(True)
        return object()
    def question(_):
        start.wait()
        return g.scoped_vocabulary(binding(), ('graph',), 0, read)
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(question, range(8)))
    assert all(answer is answers[0] for answer in answers)
    assert len(built) == 1
    assert store.sent == []


@pytest.mark.parametrize('changes', [dict(release_identity='next'), dict(access_fingerprint='bo'),
                                   dict(products=('retail.monthly_revenue',)), dict(purpose='audit')])
def test_release_principal_access_product_and_purpose_do_not_share_a_scope(changes):
    g, store = graph(['ana', 'bo'])
    original = g.scoped_vocabulary(binding(), (), 0, object)
    other = g.scoped_vocabulary(binding(**changes), (), 0, object)
    assert other is not original
    assert g.scoped_vocabulary(binding(), (), 0, object) is original
    assert store.sent == []


def test_term_writes_drop_scoped_reads_and_stale_builds_cannot_refill_them():
    g, store = graph(['ana'])
    original = g.scoped_vocabulary(binding(), (), 0, object)
    g.q('MERGE (t:Term {name: $n})', {'n': 'new term'})
    assert not g._scoped_vocabularies
    stale = g.scoped_vocabulary(binding(), (), 0, object)
    assert not g._scoped_vocabularies
    current = g.scoped_vocabulary(binding(), (), 1, object)
    assert current is not original and current is not stale
    assert g.scoped_vocabulary(binding(), (), 1, object) is current
    assert store.sent == ['MERGE (t:Term {name: $n})']


def test_scoped_vocabulary_storage_is_bounded():
    g, _ = graph(['ana'])
    original = g.scoped_vocabulary(binding(), ('question', 0), 0, object)
    for variant in range(1, 65):
        g.scoped_vocabulary(binding(), ('question', variant), 0, object)
    assert len(g._scoped_vocabularies) == 64
    assert g.scoped_vocabulary(binding(), ('question', 0), 0, object) is not original
