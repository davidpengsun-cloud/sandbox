"""Every session-state adapter must preserve the same durable, client-scoped contract."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from kge.adapters.appstate import bigquery
from kge.adapters.deadline import ExternalCallError
from kge.adapters.objstore import open_store
from kge.adapters.objstore.gcs import GcsStore
from kge.adapters.sessionstate import for_profile, local_identity
from kge.adapters.sessionstate.store import StoreSessionState
from kge.pipeline.objstore import LOCAL_ONLY, files_under, work_paths
from kge.ports import SessionState, StoreConflict, StoreError
from tests import bigquery_fake


@pytest.fixture(params=["folder", "gcs", "bigquery"])
def sessions(request, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_STATE_BACKEND", "bigquery" if request.param == "bigquery" else "store")
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    monkeypatch.setenv("STORE_URL", "gs://session-test/shared" if request.param == "gcs" else str(tmp_path / "shared"))
    monkeypatch.setattr(bigquery, "_IDENTITY_ENSURED", set())
    fake = bigquery_fake.FakeBigQuery()
    monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: fake.state_post(body))
    objects, generations, lock = {}, {}, threading.Lock()

    def call(self, method, url, key, data=None, json_body=False):
        parsed = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        name = (self.bucket, query.get("name") or unquote(parsed.path.rsplit("/", 1)[-1]))
        with lock:
            current, content = objects.get(name, (0, None))
            if method == "GET":
                if content is None:
                    return 404, b""
                if query.get("alt") == "media":
                    return 200, generations[(name, query["generation"])]
                return 200, json.dumps({"generation": str(current)}).encode()
            assert method == "POST"
            if query["ifGenerationMatch"] != str(current):
                return 412, b""
            objects[name] = current + 1, data
            generations[(name, str(current + 1))] = data
            return 200, json.dumps({"generation": str(current + 1)}).encode()

    monkeypatch.setattr(GcsStore, "call", call)

    def new(client="sample"):
        return for_profile(SimpleNamespace(client=client, run_dir=lambda: tmp_path / client))

    def fail(state):
        def unavailable(*args, **kwargs):
            raise StoreError("private diagnostic")
        if request.param == "bigquery":
            monkeypatch.setattr(state._records, "_post", unavailable)
        else:
            monkeypatch.setattr(state.store, "get_version", unavailable)

    yield new, fail
    fake.con.close()


def test_put_get_and_merge_preserve_tokens_and_sign_outs(sessions):
    new, _ = sessions
    first, second = new(), new()
    assert isinstance(first, SessionState)
    assert first.identity_snapshot() == {"tokens": {}, "signed_out": {}}
    assert first.identity_token("reader@example.com", "first") == "first"
    assert second.identity_token("reader@example.com", "second") == "first"
    expiry = int(time.time()) + 100
    second.identity_sign_out("one", expiry)
    first.identity_sign_out("two", expiry)
    second.identity_token("writer@example.com", "writer")
    expected = {"tokens": {"reader@example.com": "first", "writer@example.com": "writer"},
                "signed_out": {"one": expiry, "two": expiry}}
    first.refresh_identity()
    assert first.identity_snapshot() == expected
    assert new().identity_snapshot() == expected
    first.identity_snapshot()["tokens"].clear()
    assert first.identity_snapshot() == expected
    assert new("other").identity_snapshot() == {"tokens": {}, "signed_out": {}}


def test_concurrent_sign_outs_and_token_creation_survive(sessions):
    new, _ = sessions
    states, start = [new(), new()], threading.Barrier(2)
    expiry = int(time.time()) + 100

    def write(index):
        start.wait(timeout=5)
        token = states[index].identity_token("reader@example.com", str(index))
        states[index].identity_sign_out(str(index), expiry)
        return token

    with ThreadPoolExecutor(max_workers=2) as workers:
        tokens = list(workers.map(write, range(2)))
    assert tokens[0] == tokens[1]
    assert new().identity_snapshot() == {"tokens": {"reader@example.com": tokens[0]},
                                        "signed_out": {"0": expiry, "1": expiry}}


def test_expired_sign_outs_are_not_returned(sessions):
    new, _ = sessions
    state = new()
    state.identity_sign_out("expired", int(time.time()) - 1)
    assert new().identity_snapshot() == {"tokens": {}, "signed_out": {}}


def test_read_failure_surfaces_and_idle_requests_fail_closed(sessions):
    new, fail = sessions
    state = new()
    now = [100.0]
    state.identity_clock = lambda: now[0]
    state.refresh_identity()
    fail(state)
    with pytest.raises(ExternalCallError, match="^Sign-in state unavailable; retry shortly$"):
        state.refresh_identity()
    now[0] += 60
    with pytest.raises(ExternalCallError, match="snapshot is missing or at least 30 seconds old"):
        state.identity_snapshot()


@pytest.mark.parametrize("sessions", ["folder", "gcs"], indirect=True)
def test_generation_conflict_retries_and_merges_both_sign_outs(sessions, monkeypatch):
    new, _ = sessions
    first, second = new(), new()
    assert isinstance(first, StoreSessionState)
    start, conflicts = threading.Barrier(2), []
    for state in (first, second):
        put = state.store.put_version

        def racing(key, data, version, put=put, waited=[]):
            if not waited:
                waited.append(True)
                start.wait(timeout=5)
            try:
                return put(key, data, version)
            except StoreConflict:
                conflicts.append(True)
                raise

        monkeypatch.setattr(state.store, "put_version", racing)
    expiry = int(time.time()) + 100
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda pair: pair[0].identity_sign_out(pair[1], expiry), ((first, "one"), (second, "two"))))
    assert conflicts == [True]
    assert new().identity_snapshot()["signed_out"] == {"one": expiry, "two": expiry}


def test_session_state_is_excluded_from_work_and_release_archives(tmp_path):
    folder = tmp_path / "build" / "run"
    state = StoreSessionState(open_store(str(folder), private=True))
    state.identity_token("reader@example.com", "candidate")
    assert state.KEY in LOCAL_ONLY
    assert folder / state.KEY not in work_paths(tmp_path)
    assert folder / state.KEY not in files_under(folder)


def test_local_agents_share_tokens_in_the_profile_run_folder(tmp_path):
    first, second = local_identity(tmp_path), local_identity(tmp_path)
    start = threading.Barrier(2)

    def mint(index):
        start.wait(timeout=5)
        return (first, second)[index].identity_token("reader@example.com", str(index))

    with ThreadPoolExecutor(max_workers=2) as workers:
        tokens = list(workers.map(mint, range(2)))
    assert tokens[0] == tokens[1]
    second.identity_token("writer@example.com", "writer")
    first.identity_sign_out("session", int(time.time()) + 100)
    expected = {"reader@example.com": tokens[0], "writer@example.com": "writer"}
    assert json.loads((tmp_path / "tokens.json").read_text()) == expected
    assert first.identity_snapshot()["tokens"] == second.identity_snapshot()["tokens"] == expected
    assert (tmp_path / "tokens.json").stat().st_mode & 0o777 == 0o600


def test_local_token_projection_keeps_newer_peer_tokens_and_recovers_a_missing_file(tmp_path):
    state = local_identity(tmp_path)
    state.identity_token("reader@example.com", "reader")
    stale = state.identity_snapshot()["tokens"]
    local_identity(tmp_path).identity_token("writer@example.com", "writer")
    state._keep_local_tokens(stale)
    expected = {"reader@example.com": "reader", "writer@example.com": "writer"}
    assert json.loads((tmp_path / "tokens.json").read_text()) == expected
    (tmp_path / "tokens.json").unlink()
    assert state.identity_snapshot()["tokens"] == expected
    assert json.loads((tmp_path / "tokens.json").read_text()) == expected


def test_folder_version_belongs_to_the_bytes_even_if_a_peer_replaces_the_path(tmp_path, monkeypatch):
    from pathlib import Path

    store = open_store(str(tmp_path), private=True)
    store.put("state.json", b"first")
    first = store.get_version("state.json")
    opened = Path.open

    class ReplacedAfterReading:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self):
            data = self.stream.read()
            store.put("state.json", b"second")
            return data

        def fileno(self):
            return self.stream.fileno()

    def racing(path, *args, **kwargs):
        stream = opened(path, *args, **kwargs)
        return ReplacedAfterReading(stream) if path == tmp_path / "state.json" else stream

    with monkeypatch.context() as race:
        race.setattr(Path, "open", racing)
        assert store.get_version("state.json") == first
    with pytest.raises(StoreConflict):
        store.put_version("state.json", b"lost peer write", first[1])
    assert store.get("state.json") == b"second"
