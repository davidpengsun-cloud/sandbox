"""Numeric per-question accounting is private, bounded, durable, and safe across instances."""
from __future__ import annotations

import uuid
import base64
import json
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from kge.adapters.modelusage import Actor, UsageConflict, UsageUnavailable, current_usage, for_actor, for_profile, usage_scope
from kge.adapters.objstore.folder import LocalStore
from kge.ports import ModelUsage
from kge.ports import StoreConflict, StoreError


KEY = b"prototype-only-random-key-for-tests"
PERSON = Actor("meridian", "person", "alice@example.test", "cerebro")


def uid():
    return str(uuid.uuid4())


def scoped(tmp_path, actor=PERSON, key=KEY):
    return for_actor(LocalStore(str(tmp_path), tmp_path), actor, key=key)


def _process_attempt(args):
    path, question, attempt = args
    ledger = scoped(path)
    ledger.start_attempt(question, attempt)
    ledger.finish_attempt(question, attempt, usage())


def usage(**changes):
    return {**{"outcome": "ok", "input_tokens": 100, "output_tokens": 20, "thinking_tokens": 5,
               "input_price": 1.0, "output_price": 2.0, "cache_mode": "none", "cache_read_tokens": None,
               "cache_write_tokens": None, "cache_read_price": None, "cache_write_price": None}, **changes}


def test_scope_and_privacy_reject_other_users_tenants_services_and_conversations(tmp_path):
    owner = scoped(tmp_path)
    assert isinstance(owner, ModelUsage)
    question, attempt = uid(), uid()
    owner.open_question(question)
    owner.start_attempt(question, attempt)
    for actor in (replace(PERSON, who="bob@example.test"), replace(PERSON, client="other"),
                  replace(PERSON, kind="service"), replace(PERSON, app="discover")):
        other = scoped(tmp_path, actor)
        with pytest.raises(UsageUnavailable):
            other.report(question)
        with pytest.raises(UsageUnavailable):
            other.finish_attempt(question, attempt, usage())
    with pytest.raises(UsageUnavailable):
        scoped(tmp_path).report(uid())
    assert "alice" not in repr(PERSON)
    files = list(tmp_path.glob("model-usage/*.json"))
    assert len(files) == 1 and "alice" not in str(files[0]) and question not in str(files[0])
    assert b"alice" not in files[0].read_bytes() and b"question" not in files[0].read_bytes()
    with pytest.raises(ValueError):
        owner.finish_attempt(question, attempt, {**usage(), "answer": "sensitive"})


def test_pending_hitl_and_background_do_not_return_misleading_zero(tmp_path):
    owner, other_instance = scoped(tmp_path), scoped(tmp_path)
    question, first, followup = uid(), uid(), uid()
    owner.open_question(question)
    assert owner.report(question) == {"calls": 0, "input_tokens": None, "output_tokens": None,
                                      "thinking_tokens": None, "cost_usd": None, "complete": False}
    owner.start_attempt(question, first)
    owner.finish_attempt(question, first, usage())
    other_instance.open_question(question)
    other_instance.start_attempt(question, followup)
    with pytest.raises(UsageConflict):
        owner.finish_question(question)
    other_instance.finish_attempt(question, followup, usage(input_tokens=20, output_tokens=10, thinking_tokens=2))
    owner.finish_question(question)
    assert other_instance.report(question) == {"calls": 2, "input_tokens": 120, "output_tokens": 30,
                                                "thinking_tokens": 7, "cost_usd": 0.00018, "complete": True}


def test_question_model_first_writer_wins_across_instances(tmp_path):
    question = uid()
    def open_for(model):
        try:
            scoped(tmp_path).bind_question(question, ("provider", model), legacy_binding=lambda: ("provider", model))
            return model
        except UsageConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(open_for, ("model-a", "model-b")))
    assert len([result for result in results if result]) == 1
    winner = next(result for result in results if result)
    scoped(tmp_path).bind_question(question, ("provider", winner), legacy_binding=lambda: ("provider", winner))
    raw = next(tmp_path.glob('model-usage/*.model.json')).read_bytes()
    assert b'provider' not in raw and b'model-a' not in raw and b'model-b' not in raw


def test_binding_survives_partial_question_creation_and_fails_closed(tmp_path):
    question = uid()
    ledger = scoped(tmp_path)
    ledger.bind_question(question, ("provider", "model-a"), legacy_binding=lambda: ("provider", "model-a"))
    assert not (tmp_path / ledger._path(question)).exists()
    with pytest.raises(UsageConflict):
        scoped(tmp_path).bind_question(question, ("provider", "model-b"), legacy_binding=lambda: ("provider", "model-b"))
    scoped(tmp_path).bind_question(question, ("provider", "model-a"), legacy_binding=lambda: ("provider", "model-a"))
    scoped(tmp_path).open_question(question)
    assert scoped(tmp_path).report(question)["calls"] == 0


def test_legacy_record_adopts_only_the_existing_selection(tmp_path):
    question = uid()
    ledger = scoped(tmp_path)
    ledger.open_question(question)
    record = json.loads((tmp_path / ledger._path(question)).read_bytes())
    assert set(record) == {"version", "closed", "attempts", "invocations"} and record["version"] == 2
    with pytest.raises(UsageConflict):
        ledger.bind_question(question, ("provider", "new"), legacy_binding=lambda: ("provider", "old"))
    ledger.bind_question(question, ("provider", "old"), legacy_binding=lambda: ("provider", "old"))
    assert json.loads((tmp_path / ledger._path(question)).read_bytes()) == record


def test_physical_attempts_are_counted_once_and_conflicting_replays_fail(tmp_path):
    owner, second = scoped(tmp_path), scoped(tmp_path)
    question, attempt, retry = uid(), uid(), uid()
    owner.open_question(question)
    assert owner.start_attempt(question, attempt) is True
    assert second.start_attempt(question, attempt) is False
    owner.finish_attempt(question, attempt, usage(outcome="failed", input_tokens=None, output_tokens=None))
    second.finish_attempt(question, attempt, usage(outcome="failed", input_tokens=None, output_tokens=None))
    with pytest.raises(UsageConflict):
        owner.finish_attempt(question, attempt, usage())
    owner.start_attempt(question, retry)
    owner.finish_attempt(question, retry, usage())
    owner.finish_question(question)
    report = owner.report(question)
    assert report["calls"] == 2 and report["input_tokens"] is None and report["output_tokens"] is None
    assert report["cost_usd"] is None and report["complete"] is True


def test_mixed_rates_and_inclusive_reasoning_are_not_double_charged(tmp_path):
    owner, question, a, b = scoped(tmp_path), uid(), uid(), uid()
    owner.open_question(question)
    owner.start_attempt(question, a)
    owner.finish_attempt(question, a, usage(input_tokens=1000, output_tokens=100, thinking_tokens=80, input_price=1, output_price=2))
    owner.start_attempt(question, b)
    owner.finish_attempt(question, b, usage(input_tokens=500, output_tokens=200, thinking_tokens=0, input_price=10, output_price=20))
    owner.finish_question(question)
    report = owner.report(question)
    assert report["input_tokens"] == 1500 and report["output_tokens"] == 300 and report["thinking_tokens"] == 80
    assert report["cost_usd"] == 0.0102


def test_anthropic_cache_is_priced_separately_and_missing_rates_are_unknown(tmp_path):
    owner, question, a = scoped(tmp_path), uid(), uid()
    owner.open_question(question)
    owner.start_attempt(question, a)
    owner.finish_attempt(question, a, usage(input_tokens=100, output_tokens=10, cache_mode="separate",
                        cache_read_tokens=40, cache_write_tokens=20, cache_read_price=0.1, cache_write_price=1.25))
    owner.finish_question(question)
    assert owner.report(question)["cost_usd"] == 0.000089
    question2, a2 = uid(), uid()
    owner.open_question(question2)
    owner.start_attempt(question2, a2)
    owner.finish_attempt(question2, a2, usage(cache_mode="separate", cache_read_tokens=40, cache_write_tokens=20,
                                              cache_read_price=None, cache_write_price=1.25))
    owner.finish_question(question2)
    assert owner.report(question2)["cost_usd"] is None


@pytest.mark.parametrize("changes", [
    {"input_price": None}, {"output_price": None}, {"input_tokens": None}, {"output_tokens": None},
    {"cache_mode": "unknown"},
])
def test_unknown_price_or_usage_does_not_become_free(tmp_path, changes):
    owner, question, attempt = scoped(tmp_path), uid(), uid()
    owner.open_question(question)
    owner.start_attempt(question, attempt)
    owner.finish_attempt(question, attempt, usage(**changes))
    owner.finish_question(question)
    assert owner.report(question)["cost_usd"] is None


@pytest.mark.parametrize("changes", [
    {"input_price": -1}, {"input_price": float("inf")}, {"input_price": float("nan")},
    {"output_price": Decimal("NaN")}, {"input_price": True}, {"input_tokens": -1},
    {"output_tokens": True}, {"thinking_tokens": 21},
    {"cache_mode": "separate", "cache_read_tokens": 80, "cache_write_tokens": 21},
])
def test_invalid_prices_and_token_counts_are_rejected(tmp_path, changes):
    owner, question, attempt = scoped(tmp_path), uid(), uid()
    owner.open_question(question)
    owner.start_attempt(question, attempt)
    with pytest.raises(ValueError):
        owner.finish_attempt(question, attempt, usage(**changes))
    assert owner.report(question)["cost_usd"] is None


def test_concurrent_instances_preserve_every_attempt_and_duplicate_delivery(tmp_path):
    owner, question = scoped(tmp_path), uid()
    owner.open_question(question)
    attempts = [uid() for _ in range(30)]
    def run(attempt):
        ledger = scoped(tmp_path)
        ledger.start_attempt(question, attempt)
        ledger.finish_attempt(question, attempt, usage())
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(run, attempts * 2))
    owner.finish_question(question)
    result = owner.report(question)
    assert result["calls"] == 30 and result["input_tokens"] == 3000 and result["output_tokens"] == 600
    assert result["cost_usd"] == 0.0042


def test_empty_question_cannot_be_closed_as_zero_without_verified_outcome(tmp_path):
    owner, question = scoped(tmp_path), uid()
    owner.open_question(question)
    assert owner.report(question)["cost_usd"] is None
    with pytest.raises(UsageConflict):
        owner.finish_question(question)
    assert owner.report(question)["cost_usd"] is None


def test_server_generated_ids_and_shared_secret_required(tmp_path):
    owner = scoped(tmp_path)
    for value in ("alice@example.test", "../../secret", str(uuid.UUID(int=0)), uid().upper()):
        with pytest.raises(ValueError):
            owner.open_question(value)
    with pytest.raises(ValueError):
        scoped(tmp_path, key=b"short")
    with pytest.raises(ValueError):
        for_actor(LocalStore(str(tmp_path), tmp_path), {"who": "alice"}, key=KEY)


def test_local_store_serializes_separate_processes(tmp_path):
    owner, question = scoped(tmp_path), uid()
    owner.open_question(question)
    attempts = [uid() for _ in range(12)]
    with ProcessPoolExecutor(max_workers=4) as workers:
        list(workers.map(_process_attempt, [(tmp_path, question, attempt) for attempt in attempts]))
    owner.finish_question(question)
    assert owner.report(question)["calls"] == 12


def test_store_conflict_retries_and_exhaustion_are_safe(tmp_path):
    class ConflictingStore:
        def __init__(self, store, failures):
            self.store, self.failures = store, failures

        def get_version(self, key):
            return self.store.get_version(key)

        def put_version(self, key, data, version):
            if self.failures:
                self.failures -= 1
                raise StoreConflict("simulated generation conflict")
            return self.store.put_version(key, data, version)

    store = LocalStore(str(tmp_path), tmp_path)
    owner = for_actor(ConflictingStore(store, 3), PERSON, key=KEY)
    question = uid()
    owner.open_question(question)
    assert owner.report(question)["calls"] == 0
    blocked = for_actor(ConflictingStore(store, 100), PERSON, key=KEY)
    with pytest.raises(UsageConflict):
        blocked.start_attempt(question, uid())
    assert owner.report(question)["calls"] == 0


def test_contention_retries_are_bounded_and_fail_closed(tmp_path, monkeypatch):
    import kge.adapters.modelusage as ledger_module

    class ConflictingStore:
        def __init__(self, store, failures):
            self.store, self.failures, self.calls = store, failures, 0

        def get_version(self, key):
            return self.store.get_version(key)

        def put_version(self, key, data, version):
            self.calls += 1
            if self.calls <= self.failures:
                raise StoreConflict("simulated conflict")
            return self.store.put_version(key, data, version)

    pauses = []
    monkeypatch.setattr(ledger_module.time, "sleep", pauses.append)
    monkeypatch.setattr(ledger_module.secrets, "randbelow", lambda _: 999)
    store = LocalStore(str(tmp_path), tmp_path)
    owner, question = for_actor(store, PERSON, key=KEY), uid()
    owner.open_question(question)
    retrying = ConflictingStore(store, 40)
    attempt = uid()
    assert for_actor(retrying, PERSON, key=KEY).start_attempt(question, attempt) is True
    assert retrying.calls == 41
    assert for_actor(store, PERSON, key=KEY).start_attempt(question, attempt) is False
    pauses.clear()
    blocked = ConflictingStore(store, 1000)
    with pytest.raises(UsageConflict):
        for_actor(blocked, PERSON, key=KEY).start_attempt(question, uid())
    assert blocked.calls == ledger_module._MAX_RETRIES
    assert len(pauses) == ledger_module._MAX_RETRIES - 1
    assert max(pauses) <= 0.02 and sum(pauses) < 1.3
    assert owner.report(question)["calls"] == 1


def test_contention_pause_respects_request_time_remaining(monkeypatch):
    import kge.adapters.deadline as deadline
    import kge.adapters.modelusage as ledger_module

    pauses = []
    monkeypatch.setattr(ledger_module.time, "sleep", pauses.append)
    monkeypatch.setattr(ledger_module.secrets, "randbelow", lambda _: 999)
    monkeypatch.setattr(deadline, "request_remaining", lambda: 0.003)
    ledger_module._pause_conflict(10)
    assert pauses == [0.003]


def test_expired_deadline_after_backoff_prevents_another_read_or_write(tmp_path, monkeypatch):
    import kge.adapters.deadline as deadline
    import kge.adapters.modelusage as ledger_module

    original = LocalStore(str(tmp_path), tmp_path)
    owner, question, attempt = for_actor(original, PERSON, key=KEY), uid(), uid()
    owner.open_question(question)
    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ledger_module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds + 0.001))
    monkeypatch.setattr(ledger_module.secrets, "randbelow", lambda _: 999)

    class ConflictingStore:
        def __init__(self):
            self.reads, self.writes = 0, 0

        def get_version(self, key):
            self.reads += 1
            return original.get_version(key)

        def put_version(self, key, data, version):
            self.writes += 1
            clock[0] += 0.0029
            raise StoreConflict("simulated conflict")

    store = ConflictingStore()
    contender = for_actor(store, PERSON, key=KEY)
    with pytest.raises(deadline.RequestDeadlineExceeded), deadline.request_window(0.003):
        contender.start_attempt(question, attempt)
    assert (store.reads, store.writes) == (1, 1)
    assert owner.report(question)["calls"] == 0


def test_corrupted_state_fails_closed_without_exposing_contents(tmp_path):
    owner, question, attempt = scoped(tmp_path), uid(), uid()
    owner.open_question(question)
    owner.start_attempt(question, attempt)
    path = next(tmp_path.glob("model-usage/*.json"))
    path.write_text('{"version":1,"closed":true,"attempts":{"secret text": null}}')
    with pytest.raises(UsageUnavailable) as caught:
        owner.report(question)
    assert "secret text" not in str(caught.value)


def test_store_outage_never_creates_a_false_zero(tmp_path):
    class FailedStore:
        def get_version(self, _key):
            raise StoreError("offline")

        def put_version(self, _key, _data, _version):
            raise AssertionError("must not write after a failed read")

    owner = for_actor(FailedStore(), PERSON, key=KEY)
    with pytest.raises(StoreError):
        owner.report(uid())
    with pytest.raises(StoreError):
        owner.open_question(uid())


def test_profile_local_key_is_stable_private_and_shared_across_processes(tmp_path, monkeypatch):
    class Profile:
        client = "meridian"

        def run_dir(self):
            return tmp_path / "run"

    for name in ("MODEL_USAGE_KEY", "STORE_URL", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    profile = Profile()
    question, attempt = uid(), uid()
    with ThreadPoolExecutor(max_workers=8) as workers:
        ledgers = list(workers.map(lambda _: for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef"), range(20)))
    ledgers[0].open_question(question)
    assert ledgers[-1].start_attempt(question, attempt) is True
    ledgers[1].finish_attempt(question, attempt, usage())
    ledgers[2].finish_question(question)
    assert ledgers[3].report(question)["calls"] == 1
    path = Path(profile.run_dir()) / "model_usage_key"
    assert path.is_file() and path.stat().st_mode & 0o077 == 0


def test_cloud_requires_stable_secret_shared_store_and_matching_key(tmp_path, monkeypatch):
    import kge.adapters.objstore as objstore

    class Profile:
        client = "meridian"

        def run_dir(self):
            return tmp_path / "run"

    profile = Profile()
    monkeypatch.setenv("K_SERVICE", "kge-demo")
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.delenv("MODEL_USAGE_KEY", raising=False)
    with pytest.raises(ValueError, match="STORE_URL"):
        for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef")
    monkeypatch.setenv("STORE_URL", "gs://example-bucket/client")
    with pytest.raises(ValueError, match="MODEL_USAGE_KEY"):
        for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef")
    secret = base64.urlsafe_b64encode(b"a" * 32).rstrip(b"=").decode()
    monkeypatch.setenv("MODEL_USAGE_KEY", secret)
    paths = []
    def open_stub(path, *, private=False):
        paths.append((path, private))
        return LocalStore(str(tmp_path / "cloud"), tmp_path / "cloud", private=True)
    monkeypatch.setattr(objstore, "open_store", open_stub)
    first = for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef")
    question = uid()
    first.open_question(question)
    assert paths == [("gs://example-bucket/client/model-usage/meridian", True)]
    assert for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef").report(question)["calls"] == 0
    monkeypatch.setenv("MODEL_USAGE_KEY", base64.urlsafe_b64encode(b"b" * 32).rstrip(b"=").decode())
    with pytest.raises(UsageUnavailable, match="does not match"):
        for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef")
    monkeypatch.setenv("MODEL_USAGE_KEY", "short-secret-dont-print")
    with pytest.raises(ValueError, match="MODEL_USAGE_KEY") as caught:
        for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef")
    assert "short-secret-dont-print" not in str(caught.value)
    with pytest.raises(ValueError, match="scope"):
        for_profile(profile, replace(PERSON, client="other"), "00000000-0000-4000-8000-abcdefabcdef")


def test_profile_key_file_mode_and_scope_context_are_enforced(tmp_path, monkeypatch):
    class Profile:
        client = "meridian"

        def run_dir(self):
            return tmp_path / "run"

    for name in ("MODEL_USAGE_KEY", "STORE_URL", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    profile = Profile()
    ledger, question = for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef"), uid()
    ledger.open_question(question)
    assert current_usage() is None
    with usage_scope(ledger, question):
        assert current_usage() == (ledger, question, None)
        assert copy_context().run(current_usage) == (ledger, question, None)
    assert current_usage() is None
    with pytest.raises(ValueError):
        with usage_scope(ledger, "bad-id"):
            pass
    key_file = Path(profile.run_dir()) / "model_usage_key"
    key_file.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        for_profile(profile, PERSON, "00000000-0000-4000-8000-abcdefabcdef")


def test_missing_instrumentation_never_finishes_invocation_or_reports_zero(tmp_path):
    ledger, question, invocation, attempt = scoped(tmp_path), uid(), uid(), uid()
    ledger.open_question(question)
    assert ledger.start_invocation(question, invocation) is True
    assert ledger.start_invocation(question, invocation) is False
    with usage_scope(ledger, question, invocation):
        assert current_usage() == (ledger, question, invocation)
    assert ledger.report(question)["cost_usd"] is None
    with pytest.raises(UsageConflict, match="not fully accounted"):
        ledger.finish_invocation(question, invocation)
    with pytest.raises(UsageConflict):
        ledger.finish_question(question)
    assert ledger.start_attempt(question, attempt, invocation) is True
    with pytest.raises(UsageConflict, match="not fully accounted"):
        ledger.finish_invocation(question, invocation)
    ledger.finish_attempt(question, attempt, usage())
    ledger.finish_invocation(question, invocation)
    ledger.finish_question(question)
    assert ledger.report(question)["calls"] == 1


@pytest.mark.parametrize("reason", ["quota_refused", "quota_unavailable"])
def test_known_pre_provider_quota_refusal_can_finish_as_zero_but_not_after_attempt(tmp_path, reason):
    ledger, question, invocation = scoped(tmp_path), uid(), uid()
    ledger.open_question(question)
    ledger.start_invocation(question, invocation)
    with pytest.raises(ValueError):
        ledger.finish_invocation_no_inference(question, invocation, reason="untrusted_input")
    ledger.finish_invocation_no_inference(question, invocation, reason=reason)
    ledger.finish_invocation_no_inference(question, invocation, reason=reason)
    ledger.finish_question(question)
    assert ledger.report(question)["cost_usd"] == 0
    question2, invocation2, attempt = uid(), uid(), uid()
    ledger.open_question(question2)
    ledger.start_invocation(question2, invocation2)
    ledger.start_attempt(question2, attempt, invocation2)
    with pytest.raises(UsageConflict):
        ledger.finish_invocation_no_inference(question2, invocation2, reason=reason)
    assert ledger.report(question2)["cost_usd"] is None


def test_model_free_cancellation_is_atomic_idempotent_and_refuses_prior_work(tmp_path):
    ledger, question, earlier, cancelled = scoped(tmp_path), uid(), uid(), uid()
    ledger.open_question(question)
    ledger.finish_question_no_inference(question, cancelled, reason="deterministic_cancelled")
    ledger.finish_question_no_inference(question, cancelled, reason="deterministic_cancelled")
    assert ledger.report(question)["cost_usd"] == 0
    question = uid()
    ledger.open_question(question)
    ledger.start_invocation(question, earlier)
    with pytest.raises(UsageConflict, match="not verified"):
        ledger.finish_question_no_inference(question, cancelled, reason="deterministic_cancelled")
    assert ledger.report(question)["cost_usd"] is None
    assert ledger.start_invocation(question, cancelled) is True


def test_expected_invocation_claim_is_atomic_and_prevents_false_complete(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    ledger, question, invocation = scoped(tmp_path), uid(), uid()
    ledger.open_question(question)
    ledger.start_invocation(question, invocation)
    with pytest.raises(UsageConflict):
        ledger.finish_question(question)
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: ledger.claim_invocation(question, invocation), range(2)))
    assert sorted(outcomes) == [False, True]
    with pytest.raises(UsageConflict):
        ledger.finish_question(question)
    ledger.finish_invocation_no_inference(question, invocation, reason='idle_poll')
    ledger.finish_question(question)
    assert ledger.report(question)['complete'] is True


def test_same_actor_question_in_different_conversations_is_isolated(tmp_path):
    actor = PERSON
    store = LocalStore(str(tmp_path), tmp_path)
    question, one, two = uid(), uid(), uid()
    a = for_actor(store, actor, key=KEY, conversation_id=one)
    b = for_actor(store, actor, key=KEY, conversation_id=two)
    a.open_question(question)
    with pytest.raises(UsageUnavailable):
        b.report(question)
    b.open_question(question)
    inv = uid()
    a.start_invocation(question, inv)
    with pytest.raises(UsageUnavailable):
        b.finish_invocation(question, inv)
    with pytest.raises(UsageConflict):
        a.finish_question(question)
    assert b.report(question)['complete'] is False
