"""Copilot on #97 (kge/pipeline/objstore.py unpack, kge/services/ask/engine/graphview.py): a release record is written
whole, and it is downloaded whole too. A status poll pulls the job's work into build/ while another poll's switch reads
the record: unpack puts each file in place by temporary name and rename, so a reader sees the old record or the new,
never part of one; a record that cannot be read is refused, never taken for none and the unreleased pointer opened;
and a status poll pulls the work under the guard the switch holds. On stand-ins: no BigQuery is called."""
from __future__ import annotations

import io
import json
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters import released
from kge.adapters.graphstore import bigquery as bq
from kge.adapters.graphstore.gql import Schema
from kge.pipeline.objstore import unpack
from kge.pipeline.profile import Profile
from kge.ports import StoreError

HASH = "cd" * 32
RELEASED = ("1a1a1a1a", "2b2b2b2b")
UNRELEASED = "9f9f9f9f"
PAD = 2_000_000


def profile(app: Path) -> Profile:
    build = app / "build"
    return Profile(id="p", client="meridian", clients=app / "clients", artifacts=build / "artifacts",
                   store=build / "stores", landing=build / "landing", warehouses=build / "warehouses",
                   identities=app / "identities.yaml", baseline=None, registry=app / "profiles.yaml")


def pointer_at(folder: Path, generation: str) -> dict:
    g = bq.Graph(folder / "graph.bigquery")
    g.ns.prefix = bq.prefix_of(HASH, generation)
    g.write(Schema.build([], []))
    return bq.BigQueryStore().release_entry(folder / "graph.bigquery")


def tarball_naming(scratch: Path, generation: str) -> bytes:
    """The job's work as a release of HASH at generation leaves it: a release record, padded so it is written in many
    chunks, naming generation, and the hash's graph pointer naming a later load nothing released."""
    folder = scratch / generation / "stores" / "meridian" / HASH
    folder.mkdir(parents=True)
    entry = pointer_at(folder, generation)
    pointer_at(folder, UNRELEASED)
    record = {"hash": HASH, "graph": entry, "chunks": None, "pad": "x" * PAD}
    (folder / released.RECORD).write_text(json.dumps(record))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in (released.RECORD, "graph.bigquery"):
            tar.add(folder / name, arcname=f"build/stores/meridian/{HASH}/{name}")
    return buf.getvalue()


@pytest.fixture
def on_bigquery(monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")


def test_a_reader_never_sees_part_of_a_record_while_the_work_is_unpacked_over_it(tmp_path, on_bigquery):
    app = tmp_path / "app"
    tarballs = [tarball_naming(tmp_path / "src", g) for g in RELEASED]
    unpack(tarballs[0], app)
    served = profile(app).serve().at(HASH)
    wanted = {bq.prefix_of(HASH, g) for g in RELEASED}
    seen: list[str] = []
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            try:
                record = served.released()
            except Exception as exc:
                seen.append(f"raised {type(exc).__name__}")
                continue
            if record is None:
                seen.append("none")
                continue
            graph = bq.pointer_in(record.get("graph"))
            seen.append(graph["prefix"] if graph else "no graph")

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for i in range(40):
            unpack(tarballs[i % 2], app)
    finally:
        stop.set()
        reader.join()
    assert seen, "the reader read the record"
    assert set(seen) <= wanted, sorted(set(seen) - wanted)


def test_a_record_that_cannot_be_read_is_refused_and_a_missing_one_is_none(tmp_path):
    folder = tmp_path / HASH
    folder.mkdir()
    assert released.read(folder) is None
    (folder / released.RECORD).write_text('{"hash": "' + HASH)
    with pytest.raises(released.Unreadable) as raised:
        released.read(folder)
    assert isinstance(raised.value, StoreError)
    (folder / released.RECORD).write_text("[]")
    with pytest.raises(released.Unreadable):
        released.read(folder)
    (folder / released.RECORD).unlink()
    (folder / released.LEGACY["graph"]).write_text("{not json")
    with pytest.raises(released.Unreadable):
        released.read(folder)


def test_a_served_profile_with_an_unreadable_record_never_opens_the_unreleased_pointer(tmp_path, on_bigquery):
    app = tmp_path / "app"
    folder = app / "build" / "stores" / "meridian" / HASH
    folder.mkdir(parents=True)
    pointer_at(folder, UNRELEASED)
    (folder / released.RECORD).write_text('{"hash": "' + HASH + '", "graph": {')
    served = profile(app).serve().at(HASH)
    with pytest.raises(released.Unreadable):
        served.released()
    with pytest.raises(released.Unreadable):
        bq.read_pointer(folder / released.RECORD)


def test_an_unreadable_record_met_while_following_a_release_is_the_store_s_503(monkeypatch):
    from kge.services.onboard import jobs
    store = SimpleNamespace(get=lambda key: HASH.encode())
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: store)
    monkeypatch.setattr(jobs, "released_identity", lambda store_, prof_, hash_: {"hash": HASH, "graph": "g2"})
    monkeypatch.setattr(jobs, "fetch", lambda store_, root, prof_: None)
    prof = SimpleNamespace(client="meridian", model_hash=lambda: HASH)

    class App:
        def engine(self):
            return SimpleNamespace(identity={"hash": HASH, "graph": "g1"}, profile=prof)

        def switch(self):
            raise released.Unreadable("release.json cannot be read")

    with pytest.raises(jobs.JobRefused) as refused:
        jobs.follow_release(App(), prof, {"id": "job-1", "step": "release", "state": "done"})
    assert refused.value.status == 503


def test_a_status_poll_pulls_the_work_under_the_guard_the_switch_holds(monkeypatch):
    from kge.services.onboard import jobs
    prof = SimpleNamespace(client="meridian")
    held = []
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: object())
    monkeypatch.setattr(jobs, "pull_config", lambda store, root: (held.append(jobs.guard.locked()), prof)[1])
    monkeypatch.setattr(jobs, "pull_work", lambda store, root, prof_: held.append(jobs.guard.locked()))
    assert jobs.current_profile(SimpleNamespace(PROFILE=prof)) is prof
    assert held == [True, True]
    assert not jobs.guard.locked()
