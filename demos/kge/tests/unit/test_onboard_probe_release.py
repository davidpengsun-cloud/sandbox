"""Release capture excludes transient onboarding probes without hiding missing public files."""
import io
from pathlib import Path
import tarfile

import pytest

from kge.estate.onboard import common
from kge.graph import history
from kge.pipeline import objstore
from kge.pipeline.profile import ONBOARD_PROBE_PREFIX
from tests.unit.test_release_record_unpack import HASH, profile


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("STORE_URL", raising=False)
    prof = profile(tmp_path)
    prof.run_dir().mkdir(parents=True)
    return prof


def capture(prof, mode):
    if mode == "snapshot":
        return history.capture_runtime(prof, HASH)
    if mode == "local-archive":
        history.archive_runtime(prof, HASH, "probe-test")
        data = (prof.store / prof.client / "releases" / HASH / "probe-test.tar.gz").read_bytes()
    else:
        data = objstore.tarball(prof.store.parent.parent, objstore.release_paths(prof, HASH))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers() if member.isfile()}


@pytest.mark.parametrize("mode", ["snapshot", "local-archive", "published-archive"])
def test_probe_removed_after_listing_cannot_break_release(prof, monkeypatch, mode):
    probe = prof.run_dir() / f"{ONBOARD_PROBE_PREFIX}race"
    probe.write_bytes(b"temporary")
    public = prof.run_dir() / "principals.json"
    public.write_bytes(b"[]")
    original = objstore.release_paths

    def listed_then_removed(*args):
        assert probe.is_file()
        paths = original(*args)
        probe.unlink()
        return paths

    monkeypatch.setattr(objstore, "release_paths", listed_then_removed)
    captured = capture(prof, mode)
    assert list(captured.values()) == [b"[]"]
    assert not any(ONBOARD_PROBE_PREFIX in name for name in captured)


@pytest.mark.parametrize("mode", ["snapshot", "local-archive", "published-archive"])
def test_release_keeps_public_files_and_excludes_probes_and_private_files(prof, mode):
    run = prof.run_dir()
    for name in objstore.LOCAL_ONLY:
        (run / name).write_bytes(b"private")
    (run / f"{ONBOARD_PROBE_PREFIX}present").write_bytes(b"temporary")
    (run / "principals.json").write_bytes(b"[]")
    (run / "pipeline_stats.json").write_bytes(b"{}")
    captured = capture(prof, mode)
    assert {Path(name).name: data for name, data in captured.items()} == {
        "principals.json": b"[]", "pipeline_stats.json": b"{}"}


@pytest.mark.parametrize("mode", ["snapshot", "local-archive", "published-archive"])
def test_missing_public_file_still_fails_release(prof, monkeypatch, mode):
    public = prof.run_dir() / "principals.json"
    public.write_bytes(b"[]")
    original = objstore.release_paths

    def listed_then_removed(*args):
        paths = original(*args)
        assert public in paths
        public.unlink()
        return paths

    monkeypatch.setattr(objstore, "release_paths", listed_then_removed)
    with pytest.raises(FileNotFoundError):
        capture(prof, mode)


def test_writability_probe_uses_shared_prefix_and_is_removed(prof, monkeypatch):
    original = common.tempfile.mkstemp
    probes = []

    def make_probe(*args, **kwargs):
        assert kwargs == {"prefix": ONBOARD_PROBE_PREFIX, "dir": prof.run_dir()}
        fd, name = original(*args, **kwargs)
        probes.append(Path(name))
        return fd, name

    monkeypatch.setattr(common.tempfile, "mkstemp", make_probe)
    assert common.writable(prof) == (True, "")
    assert len(probes) == 1 and not probes[0].exists()


@pytest.mark.parametrize("error", [PermissionError("read-only"), OSError("probe failed")])
def test_writability_probe_errors_are_still_reported(prof, monkeypatch, error):
    def refused(*args, **kwargs):
        raise error

    monkeypatch.setattr(common.tempfile, "mkstemp", refused)
    ok, why = common.writable(prof)
    assert not ok
    assert "read-only" in why and str(error) in why
