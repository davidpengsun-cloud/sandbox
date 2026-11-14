"""Retention follows successful publication and durable work save."""
import json
from types import SimpleNamespace

import pytest

from kge.adapters.objstore.folder import LocalStore
from kge.graph import release
from kge.pipeline import objstore
from kge import cli


@pytest.fixture
def publication(tmp_path, monkeypatch):
    h = 'a' * 64
    root = tmp_path / 'build'
    run = root / 'run'
    run.mkdir(parents=True)
    graph = root / 'stores' / 'acme' / h / 'graph.bigquery'
    graph.parent.mkdir(parents=True)
    identity = {'hash': h, 'release': 'new', 'graph': 'g2', 'chunks': 'c2', 'warehouses': {'sales': 2}}
    pinned = SimpleNamespace(serve=lambda: SimpleNamespace(identity=lambda: identity), graph_path=lambda: graph)
    prof = SimpleNamespace(client='acme', store=root / 'stores', artifacts=root / 'artifacts', warehouses=root / 'warehouses',
                           model_hash=lambda: h, at=lambda _: pinned, run_dir=lambda: run)
    prefix = 'releases/acme/'
    archive = prefix + h + '/new.tar.gz'
    (prof.store / 'acme' / 'kept.json').write_text(json.dumps([{'identity': identity, 'tarball': archive}]))
    (prof.artifacts / 'acme').mkdir(parents=True)
    (prof.artifacts / 'acme' / 'versions.jsonl').write_text('new history\n')
    store = LocalStore(str(tmp_path / 'objects'), tmp_path / 'objects')
    keys = [prefix + 'current', prefix + 'identity.json', prefix + 'ledger.json',
            prefix + 'versions.jsonl', prefix + h + '.tar.gz', prefix + 'evaluations/new.json', 'work/acme.tar.gz']
    before = {key: ('old ' + key).encode() for key in keys}
    for key, data in before.items():
        store.put(key, data)
    events = []
    monkeypatch.setattr(objstore, 'the_store', lambda: store)
    monkeypatch.setattr(objstore, 'resolve', lambda **_: prof)
    monkeypatch.setattr(cli.profile, 'resolve', lambda **_: prof)
    monkeypatch.setattr(cli, 'profile_words', lambda: {})
    monkeypatch.setattr(release, 'prepare_retention', lambda _: events.append('prepare'))
    monkeypatch.setattr(objstore, 'local_files', lambda _: False)
    monkeypatch.setattr(objstore, 'release_paths', lambda *_: ['release'])
    monkeypatch.setattr(objstore, 'work_paths', lambda *_: ['work'])
    monkeypatch.setattr(objstore, 'tarball', lambda _, paths: paths[0].encode())
    monkeypatch.setattr(objstore, 'release_lease', lambda *_: events.append('lease released'))
    monkeypatch.setattr(objstore.released, 'evaluation', lambda *_: {'passed': True})
    monkeypatch.setattr(release, 'finish', lambda _: events.append('retire'))
    monkeypatch.delenv('PREVIOUS', raising=False)
    put = store.put

    def recorded(key, data):
        put(key, data)
        events.append(key)

    monkeypatch.setattr(store, 'put', recorded)
    return SimpleNamespace(store=store, prof=prof, before=before, events=events, app=tmp_path,
                           archive=archive, prefix=prefix)


def test_retention_follows_publication_and_durable_work(publication):
    p = publication
    cli.Commands({'COMMAND': 'release'})._stored_after()
    assert p.events[0] == 'prepare'
    assert p.events.index('work/acme.tar.gz') < p.events.index(p.prefix + 'ledger.json') < p.events.index(p.prefix + 'current')
    assert p.events[-2:] == ['retire', 'lease released']
    assert json.loads(p.store.get(p.prefix + 'ledger.json'))[0]['identity']['release'] == 'new'


@pytest.mark.parametrize('key', ['current', 'identity.json', 'ledger.json', 'versions.jsonl', 'evaluations/new.json', 'work'])
@pytest.mark.parametrize('rollback', [False, True])
def test_partial_save_failure_does_not_retire_resources(publication, monkeypatch, key, rollback):
    p = publication
    if rollback:
        restored = p.prof.run_dir() / 'restored.yaml'
        restored.write_text('restored input')
        p.before.update({'clients/acme/units/sales/draft.yaml': b'current input',
                         'clients/acme/units/removed/draft.yaml': b'keep until committed'})
        for name, data in p.before.items():
            p.store.put(name, data)
        p.store.put(p.archive, b'kept immutable archive')
        (p.prof.run_dir() / 'rollback.json').write_text(json.dumps({
            'hash': p.prof.model_hash(), 'release': 'new',
            'restored': [{'key': 'clients/acme/units/sales/draft.yaml', 'file': str(restored)}],
            'removed': ['clients/acme/units/removed/draft.yaml']}))
    target = 'work/acme.tar.gz' if key == 'work' else p.prefix + key
    put = p.store.put
    failed = False

    def fail_after_write(name, data):
        nonlocal failed
        put(name, data)
        if name == target and not failed:
            failed = True
            raise RuntimeError('injected save failure')

    monkeypatch.setattr(p.store, 'put', fail_after_write)
    with pytest.raises(RuntimeError, match='injected save failure'):
        cli.Commands({'COMMAND': 'release'})._stored_after()
    assert failed
    assert 'retire' not in p.events


def test_retention_failure_does_not_unpublish_saved_release(monkeypatch, tmp_path, caplog):
    def fail(*_):
        raise RuntimeError('cleanup unavailable')
    monkeypatch.setattr(release, 'retire', fail)
    release.finish(SimpleNamespace(model_hash=lambda: 'a', run_dir=lambda: tmp_path))
    assert 'release saved; retention failed' in caplog.text




def test_legacy_versions_only_cover_units_declared_by_the_held_artifact(tmp_path):
    prof = SimpleNamespace(artifacts=tmp_path, client='acme')
    nodes = tmp_path / 'acme' / 'old' / 'data' / 'nodes'
    nodes.mkdir(parents=True)
    (nodes / 'Unit.csv').write_text('id\nsample\nretail\n')
    (nodes / 'CuratedSource.csv').write_text('id\nsource\n')
    record = {'hash': 'old', 'warehouses': {'retail': 2}}
    assert release.record_versions(prof, record) == {'sample': 0, 'retail': 2, 'source': 0}
    assert record['warehouses'] == {'retail': 2}
    assert release.record_versions(prof, {'hash': 'absent'}) == {}


@pytest.mark.parametrize('failure', [None, 'work', 'ledger'])
def test_real_stored_cli_saves_ledger_before_advertisement_and_keeps_lease(tmp_path, monkeypatch, failure, caplog):
    import io
    import tarfile
    from dataclasses import asdict
    import yaml
    from kge.adapters import released
    from kge.graph import leftovers
    from kge.pipeline import lease
    from tests.unit.test_release_record_unpack import profile
    app = tmp_path / 'app'
    app.mkdir()
    prof = profile(app)
    for name, value in {'STORE_URL': str(tmp_path / 'objects'), 'GRAPH_STORE': 'ladybug',
                        'RETRIEVAL_BACKEND': 'graph', 'WAREHOUSE_BACKEND': 'duckdb',
                        'APP_STATE_BACKEND': 'files', 'PROFILES': str(prof.registry), 'PROFILE': 'p'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(app)
    conf = {key: str(getattr(prof, key)) for key in ('clients', 'artifacts', 'store', 'landing', 'warehouses', 'identities')}
    prof.registry.write_text(yaml.safe_dump({'default': 'p', 'profiles': {'p': {**conf, 'client': prof.client, 'baseline': None}}}))
    (app / 'deploy').mkdir()
    (app / 'deploy' / 'profiles.yaml').write_bytes(prof.registry.read_bytes())
    pack = prof.clients / prof.client
    pack.mkdir(parents=True)
    (pack / 'pack.yaml').write_text('manifest: {}\n')
    h, old_hash = 'a' * 64, 'b' * 64
    folder = prof.artifacts / prof.client / h
    folder.mkdir(parents=True)
    (folder / 'manifest.json').write_text(json.dumps({'client': prof.client}))
    (folder.parent / 'current').symlink_to(h)
    graph = prof.at(h).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b'local graph')
    released.write(graph.parent, h, None, None, {}, 'new')
    current = prof.serve().identity()
    old = {**current, 'hash': old_hash, 'release': 'old'}
    entries = [{'identity': item, 'snapshot': f"versions/{item['hash']}/{item['release']}/",
                'tarball': f"releases/{prof.client}/{item['hash']}/{item['release']}.tar.gz"} for item in (current, old)]
    (prof.store / prof.client / release.KEPT).write_text(json.dumps(entries))
    release.served(prof.store / prof.client, old)
    release.served(prof.store / prof.client, current)
    store = objstore.the_store()
    prefix = f'releases/{prof.client}/'
    store.put(prefix + 'current', old_hash.encode())
    store.put(prefix + 'identity.json', json.dumps(old).encode())
    store.put(prefix + 'ledger.json', json.dumps([entries[1]]).encode())
    obsolete = f'clients/{prof.client}/versions/' + 'c' * 64 + '/obsolete/snapshot.json'
    store.put(obsolete, b'{}')
    held = lease.take(store, prof.client, 'release')
    objstore.write_private(prof.run_dir() / lease.LEASE_FILE, json.dumps(asdict(held)).encode())
    put = LocalStore.put
    events = []

    def write(target, key, data):
        if key in (objstore.work_key(prof), prefix + 'ledger.json', prefix + 'current'):
            assert json.loads(store.get(f'jobs/{prof.client}/lease.json'))['token'] == held.token
            events.append(key)
        if key == (objstore.work_key(prof) if failure == 'work' else prefix + 'ledger.json' if failure == 'ledger' else None):
            raise OSError('injected publication boundary failure')
        put(target, key, data)

    monkeypatch.setattr(LocalStore, 'put', write)
    assert cli.main(['_stored-after', 'COMMAND=release']) == (2 if failure else 0)
    assert store.get(f'jobs/{prof.client}/lease.json') == b'{}'
    assert 'retention failed' not in caplog.text
    if failure:
        assert store.get(prefix + 'current') == old_hash.encode()
        assert store.get(obsolete) is not None
    else:
        assert store.get(prefix + 'current') == (h + '\n').encode()
        assert store.get(obsolete) is None
        assert events == [objstore.work_key(prof), prefix + 'ledger.json', prefix + 'current']
    if failure != 'work':
        with tarfile.open(fileobj=io.BytesIO(store.get(objstore.work_key(prof)))) as archive:
            ledger = json.load(archive.extractfile(f'build/stores/{prof.client}/served.json'))
        assert {item['release'] for item in ledger} == {'old', 'new'}
        fresh = profile(tmp_path / 'fresh')
        objstore.pull_work(store, tmp_path / 'fresh', fresh)
        protected = leftovers.protected_records(fresh)
        assert any(item['release'] == 'old' for item in protected)
        if failure == 'ledger':
            newer = {**current, 'release': 'newer'}
            released.write(graph.parent, h, None, None, {}, 'newer')
            release.served(prof.store / prof.client, newer)
            newer_entry = {**entries[0], 'identity': newer, 'snapshot': f'versions/{h}/newer/',
                           'tarball': f'releases/{prof.client}/{h}/newer.tar.gz'}
            (prof.store / prof.client / release.KEPT).write_text(json.dumps([newer_entry, entries[0]]))
            held = lease.take(store, prof.client, 'release')
            objstore.write_private(prof.run_dir() / lease.LEASE_FILE, json.dumps(asdict(held)).encode())
            assert cli.main(['_stored-after', 'COMMAND=release']) == 2
            again = profile(tmp_path / 'again')
            objstore.pull_work(store, tmp_path / 'again', again)
            assert all(item['release'] != 'old' for item in release.served(again.store / again.client))
            assert any(item['release'] == 'old' for item in leftovers.protected_records(again))
            release.finish_onboard(again)
            assert store.get(prefix + 'current') == old_hash.encode()
            assert 'retention failed' not in caplog.text


@pytest.mark.parametrize('legacy_parts', [False, True])
def test_published_floor_reads_legacy_archive_without_identity_or_ledger(publication, monkeypatch, legacy_parts):
    import io
    import tarfile
    from kge.adapters import released
    p = publication
    monkeypatch.setenv('STORE_URL', p.store.url)
    h = p.prof.model_hash()
    p.store.put(p.prefix + 'current', h.encode())
    p.store.delete(p.prefix + 'identity.json')
    p.store.delete(p.prefix + 'ledger.json')
    record = {'hash': h, 'graph': {'prefix': 'old_graph', 'dataset': 'graph'},
              'chunks': {'table': 'old_chunks', 'dataset': 'graph'}, 'warehouses': {'sales': 0}, 'release': 'original'}
    files = ({name: record[part] for part, name in released.LEGACY.items()} if legacy_parts else {released.RECORD: record})
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for name, value in files.items():
            payload = json.dumps(value).encode()
            member = tarfile.TarInfo(f'build/stores/acme/{h}/{name}')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    p.store.put(p.prefix + h + '.tar.gz', data.getvalue())
    protected = objstore.published_records(p.prof)
    assert len(protected) == 1
    assert protected[0]['graph'] == record['graph']
    assert protected[0]['chunks'] == record['chunks']
    assert protected[0]['snapshot'] == (f'versions/{h}/' if legacy_parts else f'versions/{h}/original/')


def test_object_store_after_does_not_import_the_graph_layer():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(objstore.after))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert not any(name == 'kge.graph' or name.startswith('kge.graph.') for name in imports if name)


def test_stored_onboard_finishes_after_run_and_work_save(publication, monkeypatch):
    p = publication
    p.prof.clients = p.app / 'clients'
    (p.prof.run_dir() / objstore.ONBOARD_RUN).write_text(json.dumps({
        'outcome': 'passed', 'started_at': '2026-10-31T12:00:00Z', 'gate': None}))
    monkeypatch.setattr(release, 'finish_onboard', lambda _: p.events.append('onboard retire'))
    cli.Commands({'COMMAND': 'onboard'})._stored_after()
    assert p.events[0] == 'prepare'
    assert p.events.index('work/acme.tar.gz') < p.events.index('onboard retire')
    assert any(event.startswith('runs/acme/onboard/') for event in p.events)
    assert p.events[-2:] == ['onboard retire', 'lease released']
