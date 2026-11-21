"""Lease ownership, expiry and races through the local object-store port, with no sockets."""
import json
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from kge.adapters.objstore import open_store
from kge.pipeline import lease
from kge.ports import StoreError


@pytest.fixture
def store(tmp_path):
    return open_store((tmp_path / "store").as_uri())


@pytest.fixture
def now(monkeypatch):
    clock = [datetime(2026, 10, 31, tzinfo=timezone.utc)]
    monkeypatch.setattr(lease, "_now", lambda: clock[0])
    return clock


def test_take_refuses_another_writer_and_names_its_times(store, now):
    held = lease.take(store, "acme", "onboard")
    before = store.get(lease.key("acme"))
    assert held.started_at == "2026-10-31T00:00:00Z"
    assert held.expires_at == "2026-10-31T00:10:00Z"
    assert lease.key("acme") == "jobs/acme/lease.json"
    assert lease.LEASE_FILE == "lease.json"
    assert lease.LEASE_SECONDS == 600 and lease.RENEW_SECONDS == 120
    with pytest.raises(lease.Refused) as exc:
        lease.take(store, "acme", "release")
    assert str(exc.value) == ("another job is running for acme: onboard since 2026-10-31T00:00:00Z; "
                              "its lease ends at 2026-10-31T00:10:00Z unless it renews")
    assert store.get(lease.key("acme")) == before
    assert lease.take(store, "other", "seed").client == "other"


def test_creation_uses_zero_and_renewal_extends_from_now(store, now, monkeypatch):
    versions = []
    put = store.put_version

    def track(key, data, version):
        versions.append(version)
        return put(key, data, version)

    monkeypatch.setattr(store, "put_version", track)
    held = lease.take(store, "acme", "seed")
    first = held.version
    now[0] += timedelta(seconds=120)
    assert lease.renew(store, held) is held
    assert held.expires_at == "2026-10-31T00:12:00Z"
    assert lease.holder(store, "acme")["started_at"] == "2026-10-31T00:00:00Z"
    assert versions == ["0", first]


def test_expiry_takeover_and_old_holder_cannot_renew_or_release_successor(store, now):
    old = lease.take(store, "acme", "onboard")
    now[0] += timedelta(seconds=600)
    assert lease.holder(store, "acme") is None
    with pytest.raises(lease.Refused, match="no longer held"):
        lease.renew(store, old)
    new = lease.take(store, "acme", "release")
    before = store.get(lease.key("acme"))
    assert new.token != old.token
    lease.release(store, old)
    with pytest.raises(lease.Refused, match="no longer held"):
        lease.renew(store, old)
    assert store.get(lease.key("acme")) == before


def test_release_from_saved_run_record_survives_a_heartbeat_and_is_repeatable(store, now):
    held = lease.take(store, "acme", "onboard")
    saved = lease.Lease(**json.loads(json.dumps(asdict(held))))
    now[0] += timedelta(seconds=120)
    lease.renew(store, held)
    lease.release(store, saved)
    lease.release(store, saved)
    assert lease.holder(store, "acme") is None
    assert store.get(lease.key("acme")) == b"{}"
    assert lease.take(store, "acme", "release").token != held.token


def test_take_losing_conditional_write_reports_winner(store, now, monkeypatch):
    put = store.put_version

    def race(key, data, version):
        winner = json.loads(data)
        winner.update(command="seed", token="winner")
        put(key, json.dumps(winner).encode(), version)
        return put(key, data, version)

    monkeypatch.setattr(store, "put_version", race)
    with pytest.raises(lease.Refused, match="another job is running for acme: seed"):
        lease.take(store, "acme", "onboard")
    assert lease.holder(store, "acme")["token"] == "winner"


@pytest.mark.parametrize("operation", [lease.release, lease.renew])
def test_successor_wins_race_between_ownership_read_and_write(store, now, monkeypatch, operation):
    old = lease.take(store, "acme", "onboard")
    put = store.put_version

    def race(key, data, version):
        winner = asdict(old)
        winner.update(command="release", token="winner")
        put(key, json.dumps(winner).encode(), version)
        return put(key, data, version)

    monkeypatch.setattr(store, "put_version", race)
    with pytest.raises(StoreError):
        operation(store, old)
    assert lease.holder(store, "acme")["token"] == "winner"


@pytest.mark.parametrize("data", [b"broken", b"null", b"[]", b'{"expires_at":"tomorrow"}',
                                  b'{"client":"acme","command":"seed","token":"x","started_at":"now","expires_at":"2026-10-31"}'])
def test_unreadable_lease_is_refused_without_overwrite(store, data):
    store.put(lease.key("acme"), data)
    with pytest.raises(lease.Refused, match="cannot be read"):
        lease.take(store, "acme", "onboard")
    assert store.get(lease.key("acme")) == data


def test_heartbeat_renews_and_can_be_stopped(store, now, monkeypatch):
    held = lease.take(store, "acme", "onboard")
    renewed = threading.Event()
    actual_renew = lease.renew

    def renew(s, own):
        now[0] += timedelta(seconds=120)
        actual_renew(s, own)
        renewed.set()

    monkeypatch.setattr(lease, "RENEW_SECONDS", .01)
    monkeypatch.setattr(lease, "renew", renew)
    stop = lease.heartbeat(store, held)
    try:
        assert renewed.wait(2)
    finally:
        stop.set()
    assert lease.holder(store, "acme")["expires_at"] != "2026-10-31T00:10:00Z"


def test_heartbeat_stops_and_reports_lost_lease(store, now, monkeypatch, caplog):
    held = lease.take(store, "acme", "onboard")
    now[0] += timedelta(seconds=600)
    successor = lease.take(store, "acme", "release")
    monkeypatch.setattr(lease, "RENEW_SECONDS", .01)
    stop = lease.heartbeat(store, held)
    assert stop.wait(2)
    assert "no longer held" in caplog.text
    assert lease.holder(store, "acme")["token"] == successor.token


def test_a_store_failure_is_not_reported_as_contention(store, monkeypatch):
    def fail(*args):
        raise StoreError("unavailable")

    monkeypatch.setattr(store, "put_version", fail)
    with pytest.raises(StoreError, match="unavailable"):
        lease.take(store, "acme", "onboard")
    assert lease.holder(store, "acme") is None
