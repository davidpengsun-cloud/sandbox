"""Snapshot ties never make a row permutation observable in answers or their evidence."""
import datetime
import json
import random
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from kge.eval.stages import compare
from kge.pipeline import trace
from kge.semantics.query import Semantics
from kge.services.ask.engine.graphview import CALENDAR_SNAPSHOT, VOCABULARY
from kge.services.ask.engine.planner import measure_label
from tests.unit.test_graph_path import engine, read_path, returned_context, vocabulary_graph


def permutations(rows):
    shuffled = list(rows)
    random.Random(407).shuffle(shuffled)
    return [list(rows), list(reversed(rows)), shuffled]


def encoded(value):
    return json.dumps(value, default=lambda value: sorted(value))


@pytest.mark.parametrize('startup', [False, True])
def test_vocabulary_and_case_insensitive_paths_ignore_row_permutations(tmp_path, startup):
    rows = [dict(id=calendar, start=start, label='FY{yy}-Q{n}', term=name, term_id=tid,
                 acronym=acronym, measure='wealth.' + tid, calendar=calendar, context='flows',
                 golden='golden_x_v2:' + item, item=item, kind='CERTIFIED_BY')
            for calendar, start in [('fiscal_april', 4), ('calendar', 1)]
            for name, tid in [('net flows', 'lower'), ('Net Flows', 'upper-z'), ('Net Flows', 'upper-a')]
            for acronym in ['nf', 'NF'] for item in ['X02', 'X01']]
    evidence, snapshots = [], []
    eng = engine(tmp_path)
    eng.ALIASES['nf'] = 'Net Flows'
    for permuted in permutations(rows):
        calls = []
        def q(cypher):
            calls.append(cypher)
            if cypher == CALENDAR_SNAPSHOT:
                return permuted
            projections = {
                'terms': lambda r: {'id': r['term_id'], 'name': r['term']},
                'acronyms': lambda r: {'term': r['term_id'], 'name': r['term'], 'acronym': r['acronym']},
                'calendars': lambda r: {'measure': r['measure'], 'calendar': r['calendar']},
                'golden': lambda r: {'context': r['context'], 'id': r['golden'], 'item': r['item'], 'kind': r['kind']}}
            name = next(name for name, query in VOCABULARY.items() if cypher == query)
            unique = {encoded(projections[name](r)): projections[name](r) for r in permuted}
            return list(unique.values())
        graph = vocabulary_graph(q)
        calendars = graph.calendar_snapshot() if startup else {}
        vocab = graph.vocabulary()
        assert list(vocab.term_ids.items()) == [('Net Flows', 'upper-z'), ('net flows', 'lower')]
        assert vocab.acronyms['NF'] == tuple(sorted(vocab.acronyms['NF']))
        snapshots.append(encoded((asdict(vocab), calendars)))
        eng.g.vocabulary = graph.vocabulary
        rec = read_path(eng, question='Outside the golden set')
        with trace.graph_recording(rec):
            for text in ['NET FLOWS', 'nF', 'unmatched label']:
                trace.graph_read('match', (SimpleNamespace(id='flows', kind='context', label='NET FLOWS'), text, vocab))
            returned_context(eng)
        path = rec.path()
        assert not path.get('error')
        assert [n['id'] for n in path['nodes'] if n['type'] == 'Term'] == ['Term:upper-z']
        assert [n['id'] for n in path['nodes'] if n['type'] == 'Acronym'] == ['Acronym:NF']
        evidence.append({'set': 'heldout', 'id': 'case-tie', 'arm': 'treatment', 'principal': 'analyst',
                         'repeat': 1, 'ok': True, 'deterministic': {'answer': {'graph_path': path}, 'retrieved': []}})
        assert len(calls) == (1 if startup else len(VOCABULARY))
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert compare({'records': evidence[:1], 'must_pass': []}, evidence[1:2])['passed']
    assert compare({'records': evidence[:1], 'must_pass': []}, evidence[2:])['passed']


@pytest.mark.parametrize('text', ['NET FLOWS', 'nF', 'unmatched label'])
def test_case_insensitive_picks_are_stable_even_for_unsorted_vocabulary(tmp_path, text):
    eng = engine(tmp_path)
    eng.ALIASES['nf'] = 'Net Flows'
    paths = []
    for terms, acronyms in zip(permutations([('net flows', 'lower'), ('Net Flows', 'upper'), ('Region', 'region')]),
                              permutations(['nf', 'NF', 'Nf'])):
        vocab = eng.g.vocabulary()
        vocab.term_ids = dict(terms)
        vocab.acronyms = dict((acronym, ('upper',)) for acronym in acronyms)
        rec = read_path(eng)
        rec.record('match', (SimpleNamespace(id='flows', kind='context', label='NET FLOWS'), text, vocab))
        paths.append(rec.path())
    assert paths[0] == paths[1] == paths[2]
    assert any(n['id'] == 'Term:upper' for n in paths[0]['nodes'])
    if text == 'nF':
        assert any(n['id'] == 'Acronym:NF' for n in paths[0]['nodes'])


def test_golden_first_matching_question_uses_stored_id_order(tmp_path):
    eng = engine(tmp_path)
    (tmp_path / 'golden_x_v1.json').write_text(json.dumps({'version': 'v2', 'items': [
        {'id': item, 'question': 'Same question'} for item in ['X01', 'X02', 'X03']]}))
    paths = []
    golden = [dict(id='golden_x_v2:' + item, set='golden_x', version='v2', item=item, kind='CERTIFIED_BY')
              for item in ['X03', 'X02', 'X01']]
    for rows in permutations(golden):
        rec = read_path(eng, question='Same question')
        rec.g.node('Context', 'flows')
        rec.record('golden', ('flows', rows))
        paths.append(rec.path())
    assert paths[0] == paths[1] == paths[2]
    assert [n['id'] for n in paths[0]['nodes'] if n['type'] == 'Golden'] == ['Golden:golden_x_v2:X01']


DOMAIN_ROWS = {
    'aliases': ('MATCH (a:Alias)-[:ALIAS_OF]->(t:Term)', [dict(n=1, phrase='flows', name=name) for name in ['Net Flows', 'net flows', 'Flows']]),
    'semantic_aliases': ("WHERE a.kind IN ['time_mode'", [dict(n=1, kind='period', phrase='latest', name=name) for name in ['latest', 'recent', 'current']]),
    'business': ("WHERE a.kind = 'column'", [dict(col='wealth.FLOW.AMT_USD', phrase=name) for name in ['Amount', 'Flow', 'Net']]),
    'vocabulary': ('MATCH (v:Vocabulary)', [dict(kind='predicate', n=1, params=json.dumps(dict(
        modifier=True, label=label, column='wealth.FLOW.KIND', value='in', **{'with': ['flows']}))) for label in ['Inflows', 'Gross', 'Net']]),
    'not_joinable': ('NOT_JOINABLE_TO', [dict(col='wealth.FLOW.ACCT', unit='wealth', name='ACCT',
        tbl='retail.ACCOUNT', to_unit='retail', note=note) for note in ['Third', 'First', 'Second']]),
    'cites': ("p.id AS policy, p.params AS params", [dict(col='wealth.FLOW.SECRET', policy=name,
        params=json.dumps({'cite': name, 'phrases': {'wealth.FLOW.SECRET': [name]}})) for name in ['P3', 'P1', 'P2']])}


@pytest.mark.parametrize('collection', DOMAIN_ROWS)
def test_domain_ties_and_planner_labels_ignore_row_permutations(collection, monkeypatch):
    from kge.services.ask.engine import planner
    marker, rows = DOMAIN_ROWS[collection]
    results, labels = [], []
    monkeypatch.setattr(planner, 'ledger', lambda *args: [])
    for permuted in permutations(rows):
        calls = []
        def q(cypher):
            calls.append(cypher)
            return permuted if marker in cypher else []
        graph = vocabulary_graph(q)
        graph.onboarded_units = lambda: set()
        domain = graph.read_domain()
        results.append(encoded(domain))
        vocabulary = SimpleNamespace(semantics=SimpleNamespace(d=SimpleNamespace(VOCABULARY=domain['vocabulary'])),
            contexts={'flows': SimpleNamespace(label='Flows')},
            candidates=lambda noun: [SimpleNamespace(kind='context', id='flows')])
        predicate = SimpleNamespace(kind='compare', op='eq', left=SimpleNamespace(kind='column', name=SimpleNamespace(id='wealth.FLOW.KIND')),
                                    value=SimpleNamespace(value='in'))
        labels.append(measure_label(SimpleNamespace(context=SimpleNamespace(id='flows'), filters=[predicate]),
                                    SimpleNamespace(filters=[]), vocabulary, 'flows', 'turn'))
        assert len(calls) == 17
    assert results[0] == results[1] == results[2]
    assert labels[0] == labels[1] == labels[2]
    if collection == 'vocabulary':
        assert labels[0] == 'Gross flows'


def test_catalogue_collections_ignore_row_permutations():
    results = []
    rows = [dict(unit='wealth', name='T' + str(i), tbl='wealth.FLOW', col='C' + str(i),
                 ft='FLOW', fc='C' + str(i), tt='T' + str(i), tc='ID') for i in [2, 0, 1]]
    for permuted in permutations(rows):
        calls = []
        def q(cypher):
            calls.append(cypher)
            if 'MATCH (r:Release)' in cypher:
                return [{'id': 'release', 'client': 'meridian', 'policy': 'calendar', 'as_of': '2026-01-01'}]
            if 'MATCH (d:Document)' in cypher:
                return [dict(id=r['name'], title=r['col']) for r in permuted]
            keys = ('unit', 'name') if 'MATCH (t:Tbl) RETURN' in cypher else (
                ('tbl', 'col') if 'HAS_COLUMN' in cypher else ('unit', 'ft', 'fc', 'tt', 'tc'))
            return [{k: r[k] for k in keys} for r in permuted]
        graph = vocabulary_graph(q)
        graph._catalogue = None
        graph.catalogue_figures = lambda tables: {}
        results.append(encoded(asdict(graph.catalogue())))
        for _ in range(3):
            graph.document_metadata()
            graph.unit_joins('wealth')
        assert len(calls) == 5
    assert results[0] == results[1] == results[2]


def test_runtime_collections_ignore_row_permutations(tmp_path):
    rows = [dict(unit=unit, tbl='T' + str(i), col='C' + str(i), dtype='DECIMAL',
                 id='r' + str(i), label='Runtime ' + str(i), kind='external', dialect='duckdb', location='',
                 rt_id='r' + str(i), ref_tbl='T' + str(i), ref_col='C' + str(i), measure='wealth.flows', term='Net Flows')
            for i, unit in enumerate(['wealth', 'retail', 'wealth'])]
    results = []
    for permuted in permutations(rows):
        calls = []
        def q(cypher):
            calls.append(cypher)
            return [dict(r) for r in permuted]
        graph = vocabulary_graph(q)
        graph._catalogue = None
        graph.profile = SimpleNamespace(artifacts=tmp_path, client='meridian', model_hash=lambda: 'hash', warehouses=tmp_path)
        results.append(repr(graph.runtime_subgraph()))
        assert len(calls) == 7
    assert results[0] == results[1] == results[2]


def test_release_calendar_snapshot_and_b3_offer_ignore_row_permutations(monkeypatch):
    from kge.semantics.compile import latest_released_offer
    from kge.services.ask.engine import planner
    policy = dict(table='wealth.MONTH', period='EOM_DT', released='READY', release_date='RELEASE_DT')
    semantics = SimpleNamespace(releases={'wealth.FLOW': policy}, tables={
        'wealth.MONTH': {'EOM_DT': 'DATE', 'READY': 'BOOLEAN', 'RELEASE_DT': 'DATE'}})
    rows = [('wealth.MONTH', datetime.date(2026, month, 28), ready, None) for month, ready in [(1, True), (2, True), (3, False)]]
    warehouses = SimpleNamespace(list_runtimes=lambda: [{'id': 'reference'}], select_runtime=lambda *args, **kwargs: {'ok': True, 'runtime_id': 'reference'})
    held, offers, calls = [], [], []
    for permuted in permutations(rows):
        def execute(*args):
            calls.append(True)
            return {}, permuted, {}
        monkeypatch.setattr(planner, 'execute_on_runtime', execute)
        graph = vocabulary_graph(lambda cypher: pytest.fail('unexpected graph job'))
        graph.warm_release_periods(semantics, warehouses)
        needs = [SimpleNamespace(tag='wealth.FLOW', policy=policy)]
        held.append(graph.release_periods(needs, 'reference'))
        offers.append(latest_released_offer(needs, held[-1]))
        graph.release_periods(needs, 'reference')
    assert held[0] == held[1] == held[2]
    assert offers == ['The latest released month is February 2026. Would you like that month instead?'] * 3
    assert len(calls) == 3


def test_cached_term_names_and_implementations_ignore_row_permutations():
    from tests.unit.test_principal_ids import graph
    names = [{'t.name': name} for name in ['net flows', 'Net Flows', 'Region']]
    mappings = [dict(unit='wealth', tbl=table, col=column, certified_by=steward)
                for table, column, steward in [('FLOW', 'NET', 'b'), ('FLOW', 'NET', 'a'), ('FLOW', 'AMT', 'a')]]
    results = []
    for terms, implementations in zip(permutations(names), permutations(mappings)):
        g, store = graph([])
        store.rows = lambda cypher: terms if cypher == 'MATCH (t:Term) RETURN t.name' else implementations
        for _ in range(3):
            result = g.terms(), g.implementations('Net Flows')
        results.append(result)
        assert len(store.sent) == 2
    assert results[0] == results[1] == results[2]
    assert results[0][0] == ['Net Flows', 'Region', 'net flows']
    assert [(r['col'], r['certified_by']) for r in results[0][1]] == [('AMT', 'a'), ('NET', 'a'), ('NET', 'b')]


def test_equal_length_restricted_names_have_stable_redaction_precedence():
    from kge.services.ask.engine.contextpath import Graphed
    texts = []
    for names in permutations(['alpha_beta', 'beta_gamma', 'other_code']):
        graph = Graphed(lambda text: text)
        graph.restricted = names
        texts.append(graph.redact('alpha_beta_gamma'))
    assert texts == ['withheld_gamma'] * 3


@pytest.mark.parametrize('rows,expected', [
    ([dict(col='wealth.FLOW.SECRET', policy=policy, params='{}') for policy in ['P1', 'P2']], 'P2'),
    ([dict(col='wealth.FLOW.SECRET', policy='P2', params=json.dumps({'cite': cite}))
      for cite in ['Alpha', 'Zulu']], 'Zulu'),
])
def test_semantics_refusal_citations_ignore_row_permutations(rows, expected):
    snapshots, decisions = [], []
    for permuted in permutations(rows):
        calls = []
        def q(cypher):
            calls.append(cypher)
            return permuted if 'p.id AS policy, p.params AS params' in cypher else []
        sem = Semantics(SimpleNamespace(q=q), SimpleNamespace())
        snapshots.append(list(sem.cites.items()))
        decisions.append(sem.restricted(['wealth.FLOW.SECRET'], ['wealth', 'retail']))
        assert len(calls) == 11
    assert snapshots == [[('wealth.FLOW.SECRET', expected)]] * 3
    assert decisions[0] == decisions[1] == decisions[2]
    assert decisions[0]['behaviour'] == 'refuse'
    assert f' ({expected})' in decisions[0]['reason']


SEMANTICS_CONTEXT = dict(id='flows', label='Flows', description='', term='Net Flows', measure='wealth.flows',
                         expression='sum(FLOW.AMT)', aggregation='sum', grain='day', fact='wealth.FLOW')
SEMANTICS_JOIN = dict(id='join', kind='dimension', condition='FLOW.ID = DIM.ID', alias=None, through=None,
                      source='wealth.FLOW', target='wealth.DIM')
SEMANTICS_ROWS = {
    'contexts': ('MATCH (t:Term)-[:RESOLVES_TO]', [dict(SEMANTICS_CONTEXT, term=term, grain=grain)
        for term, grain in [('Net Flows', 'day'), ('Flows', 'day'), ('Net Flows', 'month')]]),
    'values': ("WHERE r.role = 'value'", [dict(measure='wealth.flows', col='wealth.FLOW.' + col,
        expression='sum(FLOW.AMT)', aggregation='sum', grain='day', currency='USD', population='', snapshot=snapshot)
        for col, snapshot in [('Z', None), ('A', None), ('A', 'month')]]),
    'filters': ('REQUIRES_FILTER', [dict(ctx='flows', id=identifier, label=None, predicate=f'FLOW.ID > {n or 0}', tbl='wealth.FLOW', n=n)
        for identifier, n in [('z', 2), ('a', 2), ('tenth', 10), ('first', 1), ('null', None)]]),
    'selectors': ('SELECTS_ROWS_WITH', [dict(ctx='flows', id=identifier, label=None, predicate='DIM.ID = FLOW.ID',
        mode=mode, tbl='wealth.DIM', n=n) for identifier, n, mode in
        [('z', 2, 'exclude'), ('a', 2, None), ('a', 2, 'include'), ('tenth', 10, 'exclude'), ('first', 1, 'exclude')]]),
    'joins': ('REQUIRES_JOIN', [dict(SEMANTICS_JOIN, ctx='flows', id=identifier, required=required, n=n)
        for identifier, n, required in [('z', 2, False), ('a', 2, False), ('a', 2, True), ('tenth', 10, True), ('first', 1, True)]]),
    'entities': ('REPRESENTS', [dict(id='entity', label=label, tbl=table, col=col, n=n)
        for label, table, col, n in [('Zulu', 'wealth.OTHER', 'Z', 2), ('Alpha', 'wealth.FLOW', 'A', 2),
                                    ('First', 'wealth.FLOW', 'B', 1), ('Last', 'wealth.FLOW', 'C', 10)]]),
    'types': ('MATCH (c:Col) RETURN', [dict(id=col, t=kind) for col, kind in
        [('wealth.FLOW.Z', 'INTEGER'), ('wealth.FLOW.A', None), ('wealth.FLOW.A', 'VARCHAR')]]),
    'certified': ('CERTIFIED_JOIN', [dict(a=a, b=b, asserted_by='Steward', asserted_on='2026-01-01', note=note, j=join)
        for a, b, note, join in [('wealth', 'retail', 'Zulu', 'z'), ('retail', 'wealth', 'Zulu', 'z'),
                                  ('retail', 'wealth', 'Alpha', 'a'), ('retail', 'wealth', 'Alpha', 'z')]]),
    'declared': ('MATCH (j:JoinRule)', [dict(SEMANTICS_JOIN, alias=alias, target=target)
        for alias, target in [(None, 'wealth.DIM'), ('d', 'wealth.DIM'), ('d', 'wealth.OTHER')]]),
    **{kind: ("WHERE p.kind IN ['scd2'", [dict(id=identifier, kind=kind, params=json.dumps(dict(table='wealth.FLOW', marker=marker)))
        for identifier, marker in [('P1', 'Zulu'), ('P2', 'Alpha'), ('P2', 'Beta')]])
       for kind in ['scd2', 'release_driver', 'partitioned']},
}


@pytest.mark.parametrize('collection', SEMANTICS_ROWS)
def test_semantics_collections_ignore_row_permutations(collection):
    marker, rows = SEMANTICS_ROWS[collection]
    snapshots = []
    for permuted in permutations(rows):
        calls = []
        def q(cypher):
            calls.append(cypher)
            if marker in cypher:
                return permuted
            return [SEMANTICS_CONTEXT] if 'MATCH (t:Term)-[:RESOLVES_TO]' in cypher else []
        sem = Semantics(SimpleNamespace(q=q), SimpleNamespace())
        snapshots.append(encoded({
            'contexts': [(key, {**asdict(context), 'grain': context.grain, 'ytd_calendar': context.ytd_calendar,
                               'bindings': context.bindings, 'version_dates': context.version_dates})
                         for key, context in sem.contexts.items()],
            'measures': list(sem.measure_declarations.items()), 'entities': sem.entities, 'types': sem.types,
            'tables': sem.tables, 'certified': sem.certified, 'declared': [(key, asdict(join)) for key, join in sem.declared.items()],
            'scd2': sem.scd2, 'releases': sem.releases, 'partitioned': sem.partitioned,
        }))
        if collection in ('filters', 'selectors', 'joins'):
            rules = getattr(sem.contexts['flows'], collection)
            expected = ['null', 'first', 'a', 'z', 'tenth'] if collection == 'filters' else ['first', 'a', 'a', 'z', 'tenth']
            assert [rule.id for rule in rules] == expected
        elif collection == 'values':
            assert sem.contexts['flows'].values == ('wealth.FLOW.A', 'wealth.FLOW.A', 'wealth.FLOW.Z')
        elif collection == 'entities':
            assert sem.entities['entity']['label'] == 'First'
            assert sem.entities['entity']['key'] == ['B', 'A', 'Z', 'C']
        elif collection in ('scd2', 'release_driver', 'partitioned'):
            policies = getattr(sem, 'releases' if collection == 'release_driver' else collection)
            assert policies['wealth.FLOW'] == dict(id='P2', table='wealth.FLOW', marker='Beta')
        elif collection == 'certified':
            assert sem.certified_join('wealth', 'retail')['declared_join'] == 'a'
        assert len(calls) == 11
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_tied_mapping_rows_keep_the_same_synthetic_context():
    from kge.services.ask.engine.planrules import snapshot
    from tests.plan_fixtures import semantics
    rows = [dict(term=term, qualified='u.events.amount', unit='u', tbl='events', col='amount', dtype='DECIMAL',
                 grain='day', currency='USD', calendar=None, conversion=None, certified_by='s', certified_on='2025-01-01')
            for term in ['Net Flows', 'Amount']]
    results = []
    for permuted in permutations(rows):
        sem = semantics()
        sem.contexts = {}
        sem.measure_declarations = {}
        sem.entities = {}
        graph = SimpleNamespace(writes=0, q=lambda query: permuted if 'MAPS_TO' in query else [])
        eng = SimpleNamespace(semantics=sem, g=graph, INTENTS={})
        runtime = snapshot(eng)
        results.append((runtime.mapping_declarations, [(c.id, c.term) for c in runtime.contexts.values()]))
    assert results[0] == results[1] == results[2]
    assert results[0][1] == [('context:mapped:u.events.amount:sum', 'Net Flows')]


def test_certified_definer_ids_ignore_row_permutations(monkeypatch):
    from kge.services.ask.engine import wording
    monkeypatch.setattr(wording, 'parse', lambda *args: SimpleNamespace(terms_found=['Flows']))
    rows = [dict(term='Flows', definition='A definition', doc=doc, unit='wealth', shelf='certified',
                 ai_ready=True, tier='gold', superseded_by=None) for doc in ['Z', 'A', 'M']]
    results = []
    for permuted in permutations(rows):
        graph = SimpleNamespace(q=lambda query: permuted, readable_units=lambda principal: {'wealth'},
                                definition_documents=lambda purpose: {'A', 'M', 'Z'})
        eng = SimpleNamespace(g=graph, term_names=['Flows'])
        results.append(wording.certified_definitions({'behaviour': 'figure', 'question': '', 'principal': 'p'}, eng))
    assert results[0] == results[1] == results[2]
    assert results[0][0]['documents'] == ['A', 'M', 'Z']


def test_column_owners_and_shortest_route_ignore_row_permutations():
    joins = [dict(ft='A', fc='id', tt='C', tc='id'), dict(ft='C', fc='id', tt='D', tc='id'),
             dict(ft='A', fc='id', tt='B', tc='id'), dict(ft='B', fc='id', tt='D', tc='id')]
    owners = [{'c.tbl': name} for name in ['C', 'B', 'A']]
    results = []
    for js, os in zip(permutations(joins), permutations(owners)):
        graph = vocabulary_graph(lambda query, params=None: os)
        graph.unit_joins = lambda unit: js
        results.append((graph.column_owners('u', 'id'), graph.join_path('u', 'A', 'D')))
    assert results[0] == results[1] == results[2]
    assert results[0][0] == ['A', 'B', 'C']
    assert [edge['to_table'] for edge in results[0][1]] == ['B', 'D']
