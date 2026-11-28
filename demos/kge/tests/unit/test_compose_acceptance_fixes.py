"""Compose acceptance regressions, with in-process GCS and command doubles only."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from kge.adapters.objstore.gcs import GcsStore
from kge.ports import StoreError
from tests.gcs_stub import Stub
from tests.unit.test_kge_helper import Helper, HELPER


@pytest.fixture
def memory_gcs(monkeypatch):
    """Exercise the existing JSON API stub without binding a listening socket."""
    server = SimpleNamespace(buckets={}, generations={}, fail=[], seen=[], delete_status=200)
    def call(self, method, url, key, data=None, json_body=False):
        request = object.__new__(Stub)
        request.server = server
        request.path = urlsplit(url).path + ('?' + urlsplit(url).query if urlsplit(url).query else '')
        request.headers = {'Content-Length': str(len(data or b''))}
        request.rfile = BytesIO(data or b'')
        request.answer = lambda code, body=b'', kind='application/json': (code, body)
        return request.handle_one(method)
    monkeypatch.setattr(GcsStore, 'call', call)
    monkeypatch.setenv('STORAGE_EMULATOR_HOST', 'http://127.0.0.1:4443')
    return server, GcsStore('gs://kge-store', 'kge-store')


@pytest.mark.parametrize('conditional', [False, True])
def test_every_write_creates_absent_emulator_bucket(memory_gcs, conditional):
    server, store = memory_gcs
    key, data = 'deploy/profiles.yaml', b'profiles: {}\n'
    if conditional:
        assert store.put_version(key, data, '0') == '1'
    else:
        store.put(key, data)
    assert store.get_version(key) == (data, '1')
    assert sum(path.startswith('/storage/v1/b?') for method, path, _ in server.seen if method == 'POST') == 1
    with pytest.raises(StoreError, match='concurrently'):
        store.put_version(key, b'wrong', '0')
    assert store.get(key) == data
    assert store.put_version(key, b'updated', '1') == '2'


def test_nonzero_generation_is_not_lost_when_bucket_is_created(memory_gcs):
    server, store = memory_gcs
    with pytest.raises(StoreError, match='concurrently'):
        store.put_version('deploy/profiles.yaml', b'wrong', '9')
    assert server.buckets == {'kge-store': {}}


@pytest.mark.parametrize('conditional', [False, True])
def test_real_gcs_never_creates_a_bucket(memory_gcs, conditional):
    server, store = memory_gcs
    store.emulator = False
    with pytest.raises(StoreError, match='HTTP 404'):
        store.put_version('a', b'data', '0') if conditional else store.put('a', b'data')
    assert not server.buckets
    assert len(server.seen) == 1


@pytest.mark.parametrize('target', ['local', 'compose', 'gcp'])
def test_seed_uses_selected_job_runner(tmp_path, target):
    helper = Helper(tmp_path)
    helper.write(target)
    helper.run('seed')
    call = helper.calls()[0]
    assert call['tool'] == {'local': 'make', 'compose': 'docker', 'gcp': 'gcloud'}[target]
    assert 'seed' in ' '.join(call['args'])


@pytest.mark.parametrize('target', ['local', 'compose', 'gcp'])
def test_restart_runs_only_compose_in_its_folder(tmp_path, target):
    helper = Helper(tmp_path)
    helper.write(target)
    (helper.root / 'proxy-8000').touch()
    done = helper.run('restart')
    if target == 'compose':
        call = helper.calls('docker')[0]
        assert call['args'] == ['compose', 'restart', 'app']
        assert call['cwd'] == str(helper.folder)
        assert 'compose app ready' in done.stdout
    else:
        assert not helper.calls()
        assert 'nothing needed' in done.stdout


def test_source_and_draft_send_shipped_files_and_personal_token(tmp_path):
    helper = Helper(tmp_path)
    helper.write('local')
    helper.token()
    example = HELPER.parents[2] / 'examples/leasing'
    helper.run('source', 'UNIT=leasing', 'LABEL=Asset Finance', f'DDL={example}/schema.sql', f'CSV={example}/data')
    call = helper.calls('curl')[-1]
    assert call['url'].endswith('/onboard/source') and call['bearer']
    body = call['body']
    assert body['ddl'] == (example / 'schema.sql').read_text()
    assert body['csv'] == {p.name: p.read_text() for p in (example / 'data').glob('*.csv')}
    assert body['rows'] == 'csv' and body['unit'] == 'leasing'
    assert 'principal' not in body
    helper.run('draft', 'UNIT=leasing')
    call = helper.calls('curl')[-1]
    assert call['url'].endswith('/onboard/draft') and call['bearer']
    assert call['body'] == {'unit': 'leasing'}
    for cmd in ('source', 'draft'):
        helper.clear()
        helper.run(cmd, 'UNIT=leasing', 'PRINCIPAL=steward.meridian@example.com', ok=False)
        assert not helper.calls()


@pytest.mark.parametrize('command,args', [('accept', ['UNIT=missing']), ('draft', ['UNIT=missing']),
                                         ('checks', []), ('list', []), ('review', [])])
def test_all_api_commands_print_error_detail(tmp_path, command, args):
    helper = Helper(tmp_path)
    helper.write('local')
    helper.token()
    curl = helper.bin / 'curl'
    curl.write_text(curl.read_text().replace('content = value if', 'if path != "/whoami":\n        status, value = 409, {"detail": "unit missing: prepare the source first"}\n    content = value if'))
    done = helper.run(command, *args, ok=False)
    assert 'HTTP 409' in done.stderr and 'unit missing: prepare the source first' in done.stderr


@pytest.mark.parametrize('target', ['local', 'compose', 'gcp'])
def test_password_latest_pointer_updates_without_printing_passwords(tmp_path, target):
    helper = Helper(tmp_path)
    helper.write(target)
    first = helper.run('passwords').stdout.splitlines()[-1]
    latest = helper.state.parent / 'passwords' / target / 'meridian-latest'
    assert latest.resolve() == Path(first)
    done = helper.run('passwords')
    second = done.stdout.splitlines()[-1]
    assert first != second and latest.resolve() == Path(second)
    assert Path(first).is_file() and Path(second).stat().st_mode & 0o777 == 0o600
    assert '<test-password>' not in done.stdout + done.stderr


def test_served_hash_checks_the_service_not_the_job(tmp_path):
    helper = Helper(tmp_path)
    helper.write('local')
    helper.token()
    curl = helper.bin / 'curl'
    curl.write_text(curl.read_text().replace('value = {"ok": True}', 'value = {"serving": "' + 'a' * 64 + '"}'))
    assert 'served: ' + 'a' * 64 in helper.run('served').stdout
    helper.run('served', 'HASH=' + 'a' * 64)
    assert 'has not switched' in helper.run('served', 'HASH=' + 'b' * 64, ok=False).stderr


def test_connect_switches_to_named_person_using_password_signin(tmp_path):
    helper = Helper(tmp_path)
    helper.write('local')
    helper.token()
    (helper.root / 'whoami.json').write_text(json.dumps({'id': 'steward.meridian@example.com', 'roles': ['steward']}))
    helper.run('connect', 'knowledge_engineer.meridian@example.com', input='<test-password>\n')
    posted = next(c for c in helper.calls('curl') if c['url'].endswith('/session/token'))
    assert posted['body']['username'] == 'knowledge_engineer.meridian@example.com'
