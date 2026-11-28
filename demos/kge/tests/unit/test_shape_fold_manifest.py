"""Atomic checkpoint publication and late release-window reconciliation."""
from copy import deepcopy

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog.seals import StoreShadowSeals
from kge.eval import shapefold
from tests.unit.test_shape_fold import by_id, observation


def history(*releases):
    return shapefold.events([{"release": release, "model_hash": "h", "released_at": at} for release, at in releases])


def apply(seals, history_rows, rows):
    stages = {event.index: {"s.a": "shadow"} for event in history_rows}
    return shapefold._durable(history_rows, shapefold.assign(rows, history_rows), stages, seals)


def test_late_history_boundary_reassigns_complete_raw_without_double_count(tmp_path):
    seals = StoreShadowSeals(open_store(str(tmp_path)), "client")
    before = history(("r1", "2026-01-01T00:00:00Z"))
    moved = observation(id="2" * 32, at="2026-01-02T01:00:00.000+00:00")
    stable = observation(id="1" * 32)
    first, _ = apply(seals, before, [stable, moved])
    assert by_id(first)[("s.a", before[0].id)]["live"]["compared"] == 2
    after = history(("r1", "2026-01-01T00:00:00Z"), ("r2", "2026-01-02T00:00:00Z"))
    rows, _ = apply(seals, after, [stable, moved])
    counts = by_id(rows)
    assert counts[("s.a", after[0].id)]["live"]["compared"] == 1
    assert counts[("s.a", after[1].id)]["live"]["compared"] == 0
    assert counts[("s.a", after[1].id)]["release_mismatch"] == 1
    assert apply(seals, after, [stable, moved])[0] == rows
    assert apply(seals, after, [])[0] == rows


def test_boundary_change_with_missing_affected_raw_fails_before_publication(tmp_path):
    seals = StoreShadowSeals(open_store(str(tmp_path)), "client")
    before = history(("r1", "2026-01-01T00:00:00Z"))
    moved = observation(id="2" * 32, at="2026-01-02T01:00:00.000+00:00")
    apply(seals, before, [moved])
    old = deepcopy(seals.snapshot())
    after = history(("r1", "2026-01-01T00:00:00Z"), ("r2", "2026-01-02T00:00:00Z"))
    with pytest.raises(ValueError, match="complete retained"):
        apply(seals, after, [])
    assert seals.snapshot() == old


def test_pruned_checkpoint_can_close_when_its_timestamp_bounds_prove_safety(tmp_path):
    seals = StoreShadowSeals(open_store(str(tmp_path)), "client")
    before = history(("r1", "2026-01-01T00:00:00Z"))
    first, _ = apply(seals, before, [observation()])
    after = history(("r1", "2026-01-01T00:00:00Z"), ("r2", "2026-01-02T00:00:00Z"))
    rows, _ = apply(seals, after, [])
    assert by_id(rows)[("s.a", after[0].id)]["live"]["compared"] == 1
    assert by_id(rows)[("s.a", after[0].id)]["event"]["open"] is False
    assert first[0].id == rows[0].id


class InterruptedStore:
    def __init__(self, store, stop):
        self.store = store
        self.stop = stop
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    def put_version(self, key, data, version):
        if '/_sealed/_tx/' in key:
            self.writes += 1
            if self.stop == 'stage' and self.writes == 2:
                raise OSError('injected staged write failure')
        if key.endswith('_fold-manifest.json'):
            if self.stop == 'before':
                raise OSError('injected manifest failure')
            if self.stop == 'after':
                self.store.put_version(key, data, version)
                raise OSError('injected lost manifest acknowledgement')
        return self.store.put_version(key, data, version)


@pytest.mark.parametrize('stop', ['stage', 'before', 'after'])
def test_interrupted_multi_event_commit_has_exact_old_or_new_snapshot(tmp_path, stop):
    base = open_store(str(tmp_path))
    seals = StoreShadowSeals(base, 'client')
    before = history(('r1', '2026-01-01T00:00:00Z'))
    moved = observation(id='2' * 32, at='2026-01-02T01:00:00.000+00:00')
    apply(seals, before, [moved])
    old, _, old_version, _ = seals.snapshot()
    after = history(('r1', '2026-01-01T00:00:00Z'), ('r2', '2026-01-02T00:00:00Z'))
    failing = StoreShadowSeals(InterruptedStore(base, stop), 'client')
    with pytest.raises(OSError):
        apply(failing, after, [moved])
    result, _, version, _ = seals.snapshot()
    if stop == 'after':
        assert version != old_version
        assert set(result) == {('r1', '0000-r1'), ('r2', '0001-r2')}
    else:
        assert version == old_version
        assert result == old
    rows, _ = apply(seals, after, [moved])
    assert by_id(rows)[('s.a', '0001-r2')]['release_mismatch'] == 1
    assert apply(seals, after, [])[0] == rows


def test_manifest_commit_then_graph_crash_can_delete_previously_committed_row(tmp_path):
    seals = StoreShadowSeals(open_store(str(tmp_path)), 'client')
    before = history(('r1', '2026-01-01T00:00:00Z'))
    moved = observation(id='2' * 32, at='2026-01-02T01:00:00.000+00:00')
    initial, _ = shapefold._durable(before, shapefold.assign([moved], before), {0: {'s.a': 'specify'}}, seals)
    old_id = initial[0].id
    after = history(('r1', '2026-01-01T00:00:00Z'), ('r2', '2026-01-02T00:00:00Z'))
    stages = {0: {'s.a': 'specify'}, 1: {'s.a': 'shadow'}}
    moved_rows, _ = shapefold._durable(after, shapefold.assign([moved], after), stages, seals, frozenset({old_id}))
    assert old_id not in {row.id for row in moved_rows}
    repaired, _ = shapefold._durable(after, shapefold.assign([moved], after), stages, seals, frozenset({old_id}))
    assert repaired == moved_rows


def test_legacy_snapshot_requires_raw_evidence_before_window_migration(tmp_path):
    from dataclasses import asdict

    seals = StoreShadowSeals(open_store(str(tmp_path)), 'client')
    old_history = history(('r1', '2026-01-01T00:00:00Z'))
    raw = observation(id='3' * 32)
    rows, unattributed = shapefold.fold(old_history, shapefold.assign([raw], old_history), {0: {'s.a': 'shadow'}})
    old = {'version': 1, 'release': 'r1', 'event': '0000-r1',
           'observation_digests': {raw['id']: shapefold._observation_digest(raw)},
           'rows': [asdict(row) for row in rows], 'unattributed': unattributed}
    seals.write('r1', '0000-r1', old, '0')
    with pytest.raises(ValueError, match='complete retained'):
        apply(seals, old_history, [])
    assert seals.read('r1', '0000-r1')[0] == old
    restored, _ = apply(seals, old_history, [raw])
    assert by_id(restored)[('s.a', '0000-r1')]['live']['compared'] == 1
    assert apply(seals, old_history, [])[0] == restored


def test_manifest_snapshots_never_appear_as_observations(tmp_path):
    from kge.adapters.shadowlog.objstore import StoreShadowLog
    from kge.ports import ShadowObservation
    from tests.unit.test_shadow_log import observation as shadow_observation

    store = open_store(str(tmp_path))
    log = StoreShadowLog(store, 'client')
    seals = StoreShadowSeals(store, 'client')
    obs = shadow_observation()
    assert isinstance(obs, ShadowObservation)
    log.append(obs)
    before = log.read(obs.release)
    released = history((obs.release, '2000-01-01T00:00:00Z'))
    apply(seals, released, before)
    assert log.releases() == [obs.release]
    assert log.read(obs.release) == before
    assert seals.events() == [(obs.release, released[0].id)]


def test_gcs_manifest_uses_generation_precondition(monkeypatch):
    from kge.ports import StoreConflict
    from tests.gcs_stub import serve

    with serve() as server:
        monkeypatch.setenv('STORAGE_EMULATOR_HOST', f'127.0.0.1:{server.server_address[1]}')
        monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
        seals = StoreShadowSeals(open_store('gs://fold-manifest-test'), 'client')
        h = history(('r1', '2026-01-01T00:00:00Z'))
        _, _, absent, known = seals.snapshot()
        assert absent == '0'
        apply(seals, h, [observation()])
        records, windows, version, known = seals.snapshot()
        assert version != absent
        with pytest.raises(StoreConflict):
            seals.commit(records, windows, absent, known)
        assert seals.snapshot()[2] == version
