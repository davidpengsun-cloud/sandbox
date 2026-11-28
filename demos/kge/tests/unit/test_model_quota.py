import concurrent.futures
import hashlib
import io
import json
import threading
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from kge.adapters.model.quota import Caller, QuotaExceeded, QuotaUnavailable, admit, ceiling, limits, quota_caller, reserve
from kge.adapters.objstore import open_store
from kge.ports import StoreConflict, StoreError


PERSON = Caller("meridian", "person", "analyst@example.com", ("analyst", "steward"))


def test_per_caller_role_and_service_ceilings_choose_the_strictest():
    policy = limits('{"default":50,"persons":{"analyst@example.com":12},"roles":{"analyst":9,"steward":7},"services":{"portal":5}}')
    assert ceiling(policy, PERSON) == 7
    assert ceiling(policy, Caller("meridian", "person", "other", ("analyst",))) == 9
    assert ceiling(policy, Caller("meridian", "service", "portal")) == 5
    assert ceiling(policy, Caller("meridian", "service", "else")) == 50


@pytest.mark.parametrize("raw", ['{}', '{"default":0}', '{"default":true}', '{"default":2,"default":3}',
                                 '{"default":2,"roles":{"analyst":-1}}', '{"default":2,"typo":1}', 'not json'])
def test_malformed_policy_is_refused(raw):
    with pytest.raises(ValueError, match="MODEL_RATE_LIMITS"):
        limits(raw)


def test_limits_off_need_no_identity_or_store(monkeypatch):
    monkeypatch.delenv("MODEL_RATE_LIMITS", raising=False)
    monkeypatch.delenv("MODEL_QUOTA_STORE_URL", raising=False)
    monkeypatch.delenv("STORE_URL", raising=False)
    admit("mock:mock", "agent")


def test_scoped_identity_is_required_and_refusal_is_traced_without_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    calls = []
    monkeypatch.setattr("kge.adapters.model.quota.trace.llm_call", lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(QuotaUnavailable):
        admit("test:model", "agent")
    with quota_caller(PERSON):
        admit("test:model", "agent")
        with pytest.raises(QuotaExceeded) as refusal:
            admit("test:model", "agent")
        assert 1 <= refusal.value.retry_after <= 60
    with pytest.raises(QuotaUnavailable):
        admit("test:model", "agent")
    assert [call[1]["error"] for call in calls] == ["QuotaUnavailable", "QuotaExceeded", "QuotaUnavailable"]
    assert "analyst@example.com" not in repr(calls)


def test_counters_are_isolated_by_client_identity_and_kind(tmp_path):
    store = open_store(str(tmp_path))
    reserve(store, PERSON, 1, clock=lambda: 120)
    for other in (Caller("other", "person", PERSON.identifier), Caller("meridian", "service", PERSON.identifier),
                  Caller("meridian", "person", "different")):
        reserve(store, other, 1, clock=lambda: 120)
    with pytest.raises(QuotaExceeded):
        reserve(store, PERSON, 1, clock=lambda: 120)
    assert PERSON.identifier not in repr(store.list())


def test_rollover_and_backwards_clock_never_reset_the_later_window(tmp_path):
    store = open_store(str(tmp_path))
    reserve(store, PERSON, 1, clock=lambda: 119.0)
    with pytest.raises(QuotaExceeded) as refusal:
        reserve(store, PERSON, 1, clock=lambda: 119.0)
    assert refusal.value.retry_after == 1
    reserve(store, PERSON, 1, clock=lambda: 120.0)
    with pytest.raises(QuotaUnavailable):
        reserve(store, PERSON, 1, clock=lambda: 119.0)
    with pytest.raises(QuotaExceeded):
        reserve(store, PERSON, 1, clock=lambda: 120.0)


def test_concurrent_local_processes_cannot_exceed_the_limit(tmp_path):
    stores = [open_store(str(tmp_path)), open_store(str(tmp_path))]
    barrier = threading.Barrier(24)

    def one(n):
        barrier.wait()
        try:
            reserve(stores[n % 2], PERSON, 7, clock=lambda: 121)
            return "accepted"
        except QuotaExceeded:
            return "refused"
        except QuotaUnavailable:
            return "unavailable"

    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
        results = list(executor.map(one, range(24)))
    assert results.count("accepted") == 7
    assert results.count("refused") + results.count("unavailable") == 17
    data = json.loads(stores[0].get(stores[0].list()[0]))
    assert data == {"window": 2, "count": 7}


class VersionedStore:
    """A GCS-style generation precondition with independently racing readers."""
    def __init__(self):
        self.lock = threading.Lock()
        self.value = None
        self.version = 0

    def get_version(self, key):
        with self.lock:
            return self.value, str(self.version)

    def put_version(self, key, data, version, *, retry_ambiguous=True):
        with self.lock:
            if version != str(self.version):
                raise StoreConflict("generation changed")
            self.value = data
            self.version += 1
            return str(self.version)


def test_generation_condition_never_overspends_in_concurrent_clients():
    store = VersionedStore()
    barrier = threading.Barrier(16)

    def one(_):
        barrier.wait()
        try:
            reserve(store, PERSON, 5, clock=lambda: 121)
            return True
        except (QuotaExceeded, QuotaUnavailable):
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(one, range(16)))
    assert sum(results) == 5
    assert json.loads(store.value) == {"window": 2, "count": 5}


def test_gcs_generation_read_race_retries_without_losing_the_other_reservation(monkeypatch):
    from kge.adapters.objstore.gcs import GcsStore
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:1")
    store = GcsStore("gs://test-bucket/model-quotas", "test-bucket", "model-quotas")
    state = {"generation": 1, "body": b'{"window":2,"count":0}', "race": True}

    def call(method, url, key, data=None, json_body=False, *, attempts=4):
        query = parse_qs(urlparse(url).query)
        if method == "GET" and query.get("alt") == ["media"]:
            if state["race"]:
                state.update(generation=2, body=b'{"window":2,"count":1}', race=False)
                return 404, b""
            assert query["generation"] == [str(state["generation"])]
            return 200, state["body"]
        if method == "GET":
            return 200, json.dumps({"generation": str(state["generation"])}).encode()
        assert method == "POST"
        assert query["ifGenerationMatch"] == [str(state["generation"])]
        state.update(generation=state["generation"] + 1, body=data)
        return 200, json.dumps({"generation": str(state["generation"])}).encode()

    monkeypatch.setattr(store, "call", call)
    reserve(store, PERSON, 3, clock=lambda: 121)
    assert json.loads(state["body"]) == {"window": 2, "count": 2}
    assert state["generation"] == 3


def test_gcs_denial_does_not_look_like_a_generation_race(monkeypatch):
    from kge.adapters.objstore.gcs import GcsStore
    store = GcsStore("gs://test-bucket/model-quotas", "test-bucket", "model-quotas")
    seen = []

    def call(method, url, key, data=None, json_body=False, *, attempts=4):
        seen.append(url)
        return (200, b'{"generation":"1"}') if len(seen) == 1 else (403, b"denied")

    monkeypatch.setattr(store, "call", call)
    with pytest.raises(QuotaUnavailable):
        reserve(store, PERSON, 3, clock=lambda: 121)
    assert len(seen) == 2


def test_successful_gcs_write_followed_by_generation_race_is_not_charged_twice(monkeypatch):
    from kge.adapters.objstore.gcs import GcsStore
    store = GcsStore("gs://test-bucket/model-quotas", "test-bucket", "model-quotas")
    requests = []

    def call(method, url, key, data=None, json_body=False, *, attempts=4):
        requests.append((method, url))
        if method == "POST":
            return 200, b"{}"
        if len(requests) == 1:
            return 404, b""
        if parse_qs(urlparse(url).query).get("alt") == ["media"]:
            return 404, b""
        return 200, b'{"generation":"1"}'

    monkeypatch.setattr(store, "call", call)
    with pytest.raises(QuotaUnavailable):
        reserve(store, PERSON, 3, clock=lambda: 121)
    assert sum(method == "POST" for method, _ in requests) == 1


@pytest.mark.parametrize("failure", ["lost-response", "server-error", "timeout"])
def test_gcs_conditional_write_never_retries_an_ambiguous_response(monkeypatch, failure):
    from kge.adapters.model import quota
    from kge.adapters.objstore.gcs import GcsStore
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:1")
    store = GcsStore("gs://test-bucket/model-quotas", "test-bucket", "model-quotas")
    monkeypatch.setattr(store, "get_version", lambda key: (None, "0"))
    writes = []

    def write_then_lose_response(request, timeout):
        writes.append(request.data)
        if failure == "server-error":
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO())
        if failure == "timeout":
            raise TimeoutError("response deadline")
        raise urllib.error.URLError("response lost after commit")

    monkeypatch.setattr("kge.adapters.objstore.gcs.http.open_http", write_then_lose_response)
    with pytest.raises(quota.QuotaUnavailable):
        reserve(store, PERSON, 3, clock=lambda: 121)
    assert len(writes) == 1
    assert json.loads(writes[0]) == {"window": 2, "count": 1}


class ConflictStore:
    def get_version(self, key):
        return None, "0"

    def put_version(self, key, data, version, *, retry_ambiguous=True):
        raise StoreConflict("sensitive storage address")


def test_conflicts_and_store_failures_are_closed_and_sanitized(tmp_path):
    with pytest.raises(QuotaUnavailable) as unavailable:
        reserve(ConflictStore(), PERSON, 1, clock=lambda: 120)
    assert "sensitive" not in str(unavailable.value)
    store = open_store(str(tmp_path))
    class Broken:
        def get_version(self, key):
            raise StoreError("secret")
    with pytest.raises(QuotaUnavailable, match="temporarily unavailable"):
        reserve(Broken(), PERSON, 1, clock=lambda: 120)
    store.put("meridian/" + hashlib.sha256(f"person\0{PERSON.identifier}".encode()).hexdigest() + ".json", b"not json")
    with pytest.raises(QuotaUnavailable):
        reserve(store, PERSON, 1, clock=lambda: 120)


def test_model_port_refuses_before_provider_work(monkeypatch, tmp_path):
    from kge.adapters.model.providers import LLM
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    llm = LLM("mock", "mock")
    with quota_caller(PERSON):
        with pytest.raises(RuntimeError, match="mock model answers only"):
            llm.complete("first")
        with pytest.raises(QuotaExceeded):
            llm.complete("second")


def test_stream_admission_spends_before_provider_work_and_abandonment(monkeypatch, tmp_path):
    from kge.adapters.model.providers import LLM
    from kge.ports import ChatRequest
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    llm = LLM("mock", "mock")
    request = ChatRequest(instructions="", messages=(), tools=(), input_tokens=200, output_tokens=50)
    with quota_caller(PERSON):
        unused = llm.stream_chat(request)
        with pytest.raises(QuotaExceeded):
            llm.stream_chat(request)
        unused.close()


def test_quota_refusal_cannot_fall_back_to_local_rules():
    from kge.adapters.model.errors import no_fallback
    for error in (QuotaExceeded(3), QuotaUnavailable("The model request limit is temporarily unavailable.")):
        with pytest.raises(type(error)) as caught:
            no_fallback("model", error)
        assert caught.value is error


def test_provider_retry_spends_a_second_admission(monkeypatch, tmp_path):
    from kge.adapters.model.errors import ExternalCallError, post_json
    from kge.adapters.model.quota import admit_retry, retry_scope
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    sent = []

    def post(*args):
        sent.append(True)
        raise ExternalCallError("upstream unavailable", transient=True)

    monkeypatch.setattr("kge.adapters.model.errors._post_json", post)
    with quota_caller(PERSON), retry_scope("test:model", "plan"):
        admit("test:model", "plan")
        with pytest.raises(QuotaExceeded):
            post_json("https://example.invalid", {}, {}, on_retry=admit_retry)
    assert len(sent) == 1


def test_count_tokens_retry_does_not_spend_inference_admission(monkeypatch, tmp_path):
    from kge.adapters.model.errors import ExternalCallError, post_json
    from kge.adapters.model.quota import admit_retry, retry_scope, without_retry_admission
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    sent = []

    def post(*args):
        sent.append(True)
        if len(sent) == 1:
            raise ExternalCallError("upstream unavailable", transient=True)
        return {"totalTokens": 10}

    monkeypatch.setattr("kge.adapters.model.errors._post_json", post)
    with quota_caller(PERSON), retry_scope("test:model", "agent"):
        admit("test:model", "agent")
        with without_retry_admission():
            assert post_json("https://example.invalid", {}, {}, on_retry=admit_retry) == {"totalTokens": 10}
        with pytest.raises(QuotaExceeded):
            admit("test:model", "agent")
    assert len(sent) == 2


def test_stream_retry_is_refused_before_second_provider_request(monkeypatch, tmp_path):
    from kge.adapters.model.chat import _SSEStream
    from kge.adapters.model.quota import retry_scope
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    sent = []

    def unavailable(request, timeout):
        sent.append(True)
        raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO())

    monkeypatch.setattr("kge.adapters.model.chat.http.open_http", unavailable)
    with quota_caller(PERSON), retry_scope("test:model", "agent"):
        admit("test:model", "agent")
        with pytest.raises(QuotaExceeded):
            next(_SSEStream("https://example.invalid", {}, {}))
    assert len(sent) == 1
