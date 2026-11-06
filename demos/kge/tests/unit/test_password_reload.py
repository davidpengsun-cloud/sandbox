"""The pointer reloads successful reads within a minute and never caches a refusal."""
import base64
import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from kge.adapters.deadline import ExternalCallError
from kge.adapters.objstore import open_store
from kge.adapters.signin import ReloadingPasswords, for_profile
from kge.adapters.signin.manager import SecretManagerReader

RESOURCE = "projects/test-project/secrets/test-passwords"


def test_pointer_appears_and_secret_changes_at_exact_cache_boundary(tmp_path):
    store = open_store(str(tmp_path))
    now, calls = [0.0], []
    data = [{"ann@example.com": "old"}]

    def latest(resource):
        calls.append(resource)
        return json.dumps(data[0]).encode()

    source = ReloadingPasswords(store, "deploy/acme/passwords-secret", SimpleNamespace(latest=latest),
                                lambda: {"legacy@example.com": "startup"}, clock=lambda: now[0])
    assert source.hashes() == {"legacy@example.com": "startup"}
    store.put("deploy/acme/passwords-secret", RESOURCE.encode())
    now[0] = 59.999
    assert source.hashes() == {"legacy@example.com": "startup"} and calls == []
    now[0] = 60
    assert source.hashes() == {"ann@example.com": "old"} and calls == [RESOURCE]
    data[0] = {"ann@example.com": "new"}
    now[0] = 119.999
    assert source.hashes() == {"ann@example.com": "old"}
    now[0] = 120
    assert source.hashes() == data[0] and calls == [RESOURCE, RESOURCE]
    returned = source.hashes()
    returned.clear()
    assert source.hashes() == data[0]


@pytest.mark.parametrize("failure", [ExternalCallError("Secret Manager HTTP 403"), b"{", b"[]", b'{"ann@example.com": null}'])
def test_failed_refresh_never_serves_stale_hashes_or_caches_failure(tmp_path, failure):
    store = open_store(str(tmp_path))
    store.put("pointer", RESOURCE.encode())
    now, data, calls = [0], [b'{"ann@example.com": "old"}'], []

    def latest(resource):
        calls.append(resource)
        if isinstance(data[0], Exception):
            raise data[0]
        return data[0]

    source = ReloadingPasswords(store, "pointer", SimpleNamespace(latest=latest), lambda: {}, clock=lambda: now[0])
    assert source.hashes() == {"ann@example.com": "old"}
    now[0], data[0] = 60, failure
    with pytest.raises(ExternalCallError):
        source.hashes()
    data[0] = b'{"ann@example.com": "new"}'
    assert source.hashes() == {"ann@example.com": "new"}
    assert len(calls) == 3


def test_factory_preserves_signin_passwords_and_local_file_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    prof = SimpleNamespace(client="acme", registry=tmp_path / "deploy/profiles.yaml", run_dir=lambda: tmp_path / "run")
    prof.run_dir().mkdir()
    (prof.run_dir() / "passwords.json").write_text('{"ann@example.com": "file"}')
    assert for_profile(prof, '{"ann@example.com": "env"}').hashes() == {"ann@example.com": "env"}
    assert for_profile(prof).hashes() == {"ann@example.com": "file"}


@pytest.mark.parametrize("code", [403, 404, 500])
def test_rest_failures_are_one_safe_line(monkeypatch, code):
    def denied(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "token-leak\nsecret-leak", {}, io.BytesIO(b"secret-leak"))

    monkeypatch.setattr("urllib.request.urlopen", denied)
    with pytest.raises(ExternalCallError, match=f"^Sign-in passwords unavailable: Secret Manager HTTP {code}$"):
        SecretManagerReader(token=lambda: "token-leak").latest(RESOURCE)


def test_rest_request_and_adc_refresh_without_network(monkeypatch):
    credentials = SimpleNamespace(valid=False, token="old-token")
    refreshed = []

    def refresh(request):
        refreshed.append(request)
        credentials.valid, credentials.token = True, "test-token"

    credentials.refresh = refresh
    monkeypatch.setattr("kge.adapters.signin.manager.bq_account", lambda: (credentials, "test-project"))
    requests = []

    def respond(request, timeout):
        requests.append(request)
        assert timeout == 15
        return io.BytesIO(json.dumps({"payload": {"data": base64.b64encode(b'{"ann@example.com":"hash"}').decode()}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    reader = SecretManagerReader()
    assert reader.latest(RESOURCE) == b'{"ann@example.com":"hash"}'
    assert reader.latest(RESOURCE) == b'{"ann@example.com":"hash"}'
    assert len(refreshed) == 1
    assert requests[0].full_url == f"https://secretmanager.googleapis.com/v1/{RESOURCE}/versions/latest:access"
    assert requests[0].get_header("Authorization") == "Bearer test-token"
    assert requests[0].get_method() == "GET"


@pytest.mark.parametrize("resource", ["", "http://evil.example", "projects/x/secrets/y/versions/1", "projects/x/secrets/../y"])
def test_pointer_must_be_full_resource_and_cannot_redirect(resource):
    def forbidden():
        pytest.fail("invalid pointer must not authenticate")

    with pytest.raises(ExternalCallError, match="invalid passwords-secret pointer"):
        SecretManagerReader(token=forbidden).latest(resource)


def test_secret_manager_rest_with_stand_in_http_server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"payload": {"data": base64.b64encode(b'{"ann@example.com":"hash"}').decode()}}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reader = SecretManagerReader(endpoint=f"http://127.0.0.1:{server.server_port}/v1", token=lambda: "test-token")
        assert reader.latest(RESOURCE) == b'{"ann@example.com":"hash"}'
        assert seen == [(f"/v1/{RESOURCE}/versions/latest:access", "Bearer test-token")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
