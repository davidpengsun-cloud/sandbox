from __future__ import annotations

import os
import stat

import pytest

from kge import packstore
from kge.adapters.objstore import open_store


def write_with_umask(store, name, data, mask):
    previous = os.umask(mask)
    try:
        store.write(name, data)
    finally:
        os.umask(previous)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_write_over_a_0644_file_keeps_0644(tmp_path):
    target = tmp_path / "pack.yaml"
    target.write_bytes(b"old")
    target.chmod(0o644)
    store = packstore.Store(tmp_path)

    write_with_umask(store, "pack.yaml", b"first", 0o022)
    assert mode(target) == 0o644 and target.read_bytes() == b"first"

    write_with_umask(store, "pack.yaml", b"second", 0o077)
    assert mode(target) == 0o644 and target.read_bytes() == b"second"


def test_write_over_a_0640_file_keeps_0640(tmp_path):
    target = tmp_path / "pack.yaml"
    target.write_bytes(b"old")
    target.chmod(0o640)

    write_with_umask(packstore.Store(tmp_path), "pack.yaml", b"new", 0o022)

    assert mode(target) == 0o640 and target.read_bytes() == b"new"


def test_write_of_a_new_file_takes_the_umask(tmp_path):
    store = packstore.Store(tmp_path)

    write_with_umask(store, "units/u1/draft.yaml", b"first", 0o022)
    write_with_umask(store, "units/u2/draft.yaml", b"second", 0o002)

    assert mode(tmp_path / "units/u1/draft.yaml") == 0o644
    assert mode(tmp_path / "units/u2/draft.yaml") == 0o664


def test_mode_is_set_before_the_rename(monkeypatch, tmp_path):
    target = tmp_path / "pack.yaml"
    target.write_bytes(b"old")
    target.chmod(0o644)
    recorded = []
    replace = os.replace

    def recording_replace(source, destination):
        recorded.append(mode(source))
        replace(source, destination)

    monkeypatch.setattr(packstore.os, "replace", recording_replace)
    store = packstore.Store(tmp_path)

    write_with_umask(store, "pack.yaml", b"existing", 0o077)
    write_with_umask(store, "new.yaml", b"new", 0o022)

    assert recorded == [0o644, 0o644]


def test_write_does_not_touch_the_umask(monkeypatch, tmp_path):
    target = tmp_path / "pack.yaml"
    target.write_bytes(b"old")
    target.chmod(0o640)
    store = packstore.Store(tmp_path)
    real_umask = os.umask
    previous = real_umask(0o022)
    try:
        monkeypatch.setattr(packstore.os, "umask", lambda _mask: pytest.fail("os.umask was called"))
        store.write("new.yaml", b"new")
        store.write("pack.yaml", b"changed")
    finally:
        real_umask(previous)

    assert mode(tmp_path / "new.yaml") == 0o644
    assert mode(target) == 0o640


def test_failed_write_leaves_the_file_as_it_was(monkeypatch, tmp_path):
    target = tmp_path / "pack.yaml"
    target.write_bytes(b"old")
    target.chmod(0o640)

    def fail_fsync(_fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(packstore.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        packstore.Store(tmp_path).write("pack.yaml", b"new")

    assert target.read_bytes() == b"old" and mode(target) == 0o640
    assert list(tmp_path.glob(".pack.yaml.*")) == []


def test_mirror_keeps_modes(tmp_path):
    remote_root = tmp_path / "remote"
    mirror_root = tmp_path / "mirror"
    remote = open_store(f"file://{remote_root}")
    store = packstore.Store(mirror_root, remote=remote)

    existing = mirror_root / "pack.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")
    existing.chmod(0o644)
    write_with_umask(store, "pack.yaml", b"written", 0o077)
    assert mode(existing) == 0o644 and existing.read_bytes() == b"written"

    write_with_umask(store, "units/u1/draft.yaml", b"new", 0o022)
    created = mirror_root / "units/u1/draft.yaml"
    assert mode(created) == 0o644 and created.read_bytes() == b"new"

    existing.chmod(0o644)
    remote.put("pack.yaml", b"refreshed")
    previous = os.umask(0o077)
    try:
        assert store.read("pack.yaml") == b"refreshed"
    finally:
        os.umask(previous)
    assert mode(existing) == 0o644 and existing.read_bytes() == b"refreshed"
