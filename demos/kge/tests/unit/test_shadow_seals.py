"""Sealed per-event counters are durable and separate from raw observation reads."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog import shadow_log, shadow_seals
from kge.adapters.shadowlog.folder import FolderShadowLog
from kge.adapters.shadowlog.objstore import StoreShadowLog
from kge.adapters.shadowlog.record import line_of
from kge.ports import ShadowLog, ShadowSeals, StoreConflict
from tests.unit.test_shadow_log import observation


def record(release="rel-1", event="0001-rel-1", digests=None):
    counters = json.dumps({"version": 1, "event": {"release": release}, "live": {"compared": 1}})
    return {"version": 1, "release": release, "event": event, "observation_digests": digests or {"ab" * 16: "ab" * 32},
            "rows": [{"id": f"shape.a@{event}", "shape": "shape.a", "release_event": event, "counters": counters}],
            "unattributed": 0}


@pytest.mark.parametrize("store_backed", [False, True])
def test_seals_are_durable_per_event_and_never_observations(tmp_path, monkeypatch, store_backed):
    if store_backed:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    else:
        monkeypatch.delenv("STORE_URL", raising=False)
    state = tmp_path / "state"
    log, seals = shadow_log(state, "client-a"), shadow_seals(state, "client-a")
    assert isinstance(log, ShadowLog) and isinstance(seals, ShadowSeals)
    assert isinstance(log, StoreShadowLog if store_backed else FolderShadowLog)
    log.append(observation())
    data = record()
    missing, version = seals.read("rel-1", "0001-rel-1")
    assert missing is None and version == "0"
    next_version = seals.write("rel-1", "0001-rel-1", data, version)
    seals.write("rel-1", "0003-rel-1", record(event="0003-rel-1"), "0")
    seals.write("rel-2", "0002-rel-2", record(release="rel-2", event="0002-rel-2"), "0")
    assert shadow_seals(state, "client-a").read("rel-1", "0001-rel-1") == (data, next_version)
    assert seals.events() == [("rel-1", "0001-rel-1"), ("rel-1", "0003-rel-1"), ("rel-2", "0002-rel-2")]
    assert log.releases() == ["rel-1"]
    assert log.read("rel-1") == [json.loads(line_of(observation()))]
    assert log.read("rel-2") == []
    with pytest.raises(StoreConflict):
        seals.write("rel-1", "0001-rel-1", data, "0")
    updated = {**data, "unattributed": 1}
    seals.write("rel-1", "0001-rel-1", updated, next_version)
    assert seals.read("rel-1", "0001-rel-1")[0] == updated


def test_store_reads_do_not_silently_hide_malformed_nonseal_objects(tmp_path):
    store = open_store(str(tmp_path / "store"))
    log = StoreShadowLog(store, "client-a")
    store.put("shadow/client-a/rel-1/other/broken.json", b"not json")
    with pytest.raises(ValueError):
        log.read("rel-1")


def test_invalid_snapshot_and_path_fail_closed_without_replacing_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    seals = shadow_seals(tmp_path, "client-a")
    data = record()
    version = seals.write("rel-1", "0001-rel-1", data, "0")
    bad = [
        {**data, "release": "other"},
        {**data, "event": "other"},
        {**data, "observation_digests": {"ab" * 16: "bad hash"}},
        {**data, "observation_digests": {"a private question": "ab" * 32}},
        {**data, "unattributed": True},
        {**data, "rows": [{**data["rows"][0], "counters": '{"question":"secret"}'}]},
        {**data, "rows": [{**data["rows"][0], "counters": '{"value":NaN}'}]},
        {**data, "question": "secret"},
    ]
    for candidate in bad:
        with pytest.raises(ValueError):
            seals.write("rel-1", "0001-rel-1", candidate, version)
    assert seals.read("rel-1", "0001-rel-1")[0] == data
    for release, event in [("../escape", "0001"), ("rel-1", "../escape")]:
        with pytest.raises(ValueError):
            seals.read(release, event)


def test_corrupt_seal_is_never_accepted(tmp_path):
    store = open_store(str(tmp_path / "store"))
    from kge.adapters.shadowlog.seals import StoreShadowSeals
    seals = StoreShadowSeals(store, "client-a")
    store.put("shadow/client-a/rel-1/_sealed/0001-rel-1.json", b"not json")
    with pytest.raises(ValueError):
        seals.read("rel-1", "0001-rel-1")
    store.put("shadow/client-a/rel-1/_sealed/bad/nested.json", b"{}")
    with pytest.raises(ValueError):
        seals.events()


def test_concurrent_folds_cannot_overwrite_one_anothers_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    seals = shadow_seals(tmp_path, "client-a")
    initial = record()
    version = seals.write("rel-1", "0001-rel-1", initial, "0")

    def update(n):
        try:
            return seals.write("rel-1", "0001-rel-1", {**initial, "unattributed": n}, version)
        except StoreConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert seals.read("rel-1", "0001-rel-1")[0]["unattributed"] in (1, 2)


def test_event_ids_include_a_release_id_at_its_maximum_length(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    release = "r" * 128
    event = f"0001-{release}"
    seals = shadow_seals(tmp_path, "client-a")
    data = record(release=release, event=event)
    seals.write(release, event, data, "0")
    assert seals.read(release, event)[0] == data
    assert seals.events() == [(release, event)]
