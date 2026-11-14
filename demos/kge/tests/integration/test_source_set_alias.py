"""Source-set membership loads into the release snapshot without extra request reads."""

from kge.semantics.plan import Binding
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary


def test_compiled_source_sets_reach_the_cached_domain(eng, monkeypatch):
    graph = eng.g
    semantics = eng.semantics
    graph._domain = None
    reads = []
    query = graph.q

    def counted(text, params=None):
        reads.append(text)
        return query(text, params)

    monkeypatch.setattr(graph, 'q', counted)
    domain = graph.domain()
    for phrase in ('bank', 'group', 'enterprise'):
        assert domain['unit_words'][phrase] == ('retail', 'sme', 'corporate', 'wealth')
        assert phrase not in eng.UNIT_WORDS
    assert domain['unit_words']['retail'] == 'retail'
    cold_reads = len(reads)
    vocabulary = build_vocabulary(semantics,
                                  Binding(release_identity='release', access_fingerprint='access', products=(), purpose='analysis'),
                                  domain['units'], domain['units'],
                                  [MeasureSpec(ctx.measure, None, '', True) for ctx in semantics.contexts.values()],
                                  aliases=domain['unit_words'].items())
    for phrase in ('bank', 'group', 'enterprise'):
        [entry] = vocabulary.candidates(phrase)
        assert entry.kind == 'source_set' and set(entry.value) == set(domain['units'])
    assert len(reads) == cold_reads
    assert cold_reads == 17
    assert graph.domain() is domain and graph.domain() is domain
    assert len(reads) == cold_reads
