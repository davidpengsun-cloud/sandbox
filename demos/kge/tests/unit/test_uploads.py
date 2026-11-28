"""An upload mirrors people-owned bytes, and a failed run restores exactly the prior inputs."""
import hashlib
import json
import os
from dataclasses import asdict

import pytest

from kge.adapters.objstore import open_store
from kge.pipeline import uploads


def put(folder, name, data):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def contents(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def record(plan):
    return {k: v for k, v in asdict(plan).items() if k != "files"}


@pytest.mark.parametrize("with_store", [False, True])
def test_upload_removes_stale_inputs_applies_same_size_same_time_and_preserves_owned(tmp_path, with_store):
    pack = tmp_path / "client"
    root = tmp_path / "store"
    source = root / "uploads/acme/one"
    put(pack, "pack.yaml", b"manifest: {includes: [old.yaml]}\n")
    old = put(pack, "fixture/data/unit/table.csv", b"old\n")
    put(pack, "catalogue.yaml", b"stale\n")
    put(pack, "old.yaml", b"old include\n")
    put(source, "pack.yaml", b"manifest: {includes: [new.yaml]}\n")
    changed = put(source, "fixture/data/unit/table.csv", b"new\n")
    os.utime(changed, ns=(old.stat().st_atime_ns, old.stat().st_mtime_ns))
    put(source, "new.yaml", b"new include\n")
    protected = ["pack.lock", "baselines/old.json", "versions/hash/pack.yaml", "proposals/one.yaml",
                 "units/one/draft.yaml", "onboard/job.json"]
    for name in protected:
        put(pack, name, b"owned before")
        put(source, name, b"must not apply")
    put(pack, ".keep", b"kept")
    if not with_store:
        put(source, ".DS_Store", b"ignored")
        put(source, "fixture/.hidden/file", b"ignored")
    before = contents(pack)
    plan = uploads.plan(source.as_uri() if with_store else str(source), pack, "acme",
                        root.as_uri() if with_store else None)
    assert contents(pack) == before
    assert plan.remove == ["catalogue.yaml", "old.yaml"]
    assert plan.write == ["fixture/data/unit/table.csv", "new.yaml", "pack.yaml"]
    assert set(plan.ignored) == set(protected + ([] if with_store else [".DS_Store", "fixture/.hidden/file"]))
    assert set(plan.files) == {"pack.yaml", "new.yaml", "fixture/data/unit/table.csv"}
    backup = tmp_path / "run/onboard-inputs-before"
    uploads.apply(plan, pack, backup)
    assert not (pack / "catalogue.yaml").exists() and not (pack / "old.yaml").exists()
    assert (pack / "fixture/data/unit/table.csv").read_bytes() == b"new\n"
    assert all((pack / name).read_bytes() == b"owned before" for name in protected)
    uploads.restore(pack, backup)
    assert contents(pack) == before


def test_publish_orders_inputs_then_deletions_and_never_touches_deployment(tmp_path):
    pack, source = tmp_path / "pack", tmp_path / "upload"
    inputs = {"pack.yaml": b"manifest:\n  includes: [glossary.yaml]\n  inputs:\n    estate: {fixture: source}\n    catalogue: terms.yaml\n",
              "source/data/a.csv": b"csv", "glossary.yaml": b"include", "terms.yaml": b"catalogue"}
    for name, data in inputs.items():
        put(source, name, data)
    put(pack, "stale.yaml", b"stale")
    identities = put(tmp_path, "deploy/acme/identities.yaml", b"old people")
    store = open_store((tmp_path / "store").as_uri())
    protected = ["clients/acme/pack.lock", "clients/acme/baselines/a.json", "clients/acme/versions/a/pack.yaml",
                 "clients/acme/proposals/a.yaml", "clients/acme/units/a/draft.yaml", "clients/acme/onboard/job.json",
                 "clients/acme/.hidden", "clients/other/pack.yaml", "deploy/other/identities.yaml"]
    for name in protected:
        store.put(name, b"owned")
    store.put("clients/acme/stale.yaml", b"stale")
    store.put("clients/acme/remote-only.yaml", b"also stale")
    plan = uploads.plan(str(source), pack, "acme", None)
    uploads.apply(plan, pack, tmp_path / "backup")
    calls = []
    real_put, real_delete = store.put, store.delete

    def tracked_put(key, data):
        calls.append(("put", key))
        real_put(key, data)

    def tracked_delete(key):
        calls.append(("delete", key))
        real_delete(key)

    store.put, store.delete = tracked_put, tracked_delete
    saved = json.loads(json.dumps(record(plan)))
    assert set(saved) == {"source", "sha256", "write", "remove", "ignored"}
    assert uploads.UPLOAD_RECORD == "onboard-upload.json"
    assert uploads.publish(pack, store, "acme", saved) == 6
    assert calls == [("put", "clients/acme/source/data/a.csv"), ("put", "clients/acme/glossary.yaml"),
                     ("put", "clients/acme/terms.yaml"),
                     ("put", "clients/acme/pack.yaml"), ("delete", "clients/acme/remote-only.yaml"),
                     ("delete", "clients/acme/stale.yaml")]
    assert all(store.get(name) == b"owned" for name in protected)
    assert all(store.get("clients/acme/" + name) == data for name, data in inputs.items())
    assert store.get("deploy/acme/identities.yaml") is None
    assert identities.read_bytes() == b"old people"


@pytest.mark.parametrize("with_store", [False, True])
def test_people_file_in_upload_is_refused_without_changing_access(tmp_path, with_store):
    pack = tmp_path / "pack"
    root = tmp_path / "store"
    source = root / "uploads/acme/run"
    put(source, "pack.yaml", b"{}")
    put(source, "people.yaml", b"principals: []")
    identities = put(tmp_path, "deploy/acme/identities.yaml", b"existing seed")
    with pytest.raises(uploads.Refused, match="people.yaml.*access"):
        uploads.plan(source.as_uri() if with_store else str(source), pack, "acme",
                     root.as_uri() if with_store else None)
    assert identities.read_bytes() == b"existing seed"
    assert not pack.exists()


def test_no_people_upload_keeps_deployment_and_same_inputs_still_publish(tmp_path):
    pack, source = tmp_path / "pack", tmp_path / "source"
    for folder in (pack, source):
        put(folder, "pack.yaml", b"{}")
    identities = put(tmp_path, "identities.yaml", b"keep people")
    plan = uploads.plan(str(source), pack, "acme", None)
    assert plan.write == []
    uploads.apply(plan, pack, tmp_path / "backup")
    store = open_store((tmp_path / "store").as_uri())
    store.put("deploy/acme/identities.yaml", b"keep people")
    assert uploads.publish(pack, store, "acme", record(plan)) == 1
    assert store.get("clients/acme/pack.yaml") == b"{}"
    assert store.get("deploy/acme/identities.yaml") == identities.read_bytes() == b"keep people"


def test_apply_failure_is_restorable_without_any_store_write(tmp_path, monkeypatch):
    pack, source = tmp_path / "pack", tmp_path / "source"
    put(pack, "pack.yaml", b"old")
    put(pack, "stale.yaml", b"stale")
    put(source, "pack.yaml", b"new")
    put(source, "a.yaml", b"new include")
    before = contents(pack)
    plan = uploads.plan(str(source), pack, "acme", None)
    write = uploads._write

    def fail(path, data):
        if path == pack / "pack.yaml":
            raise OSError("injected failure")
        write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(uploads, "_write", fail)
        with pytest.raises(OSError, match="injected"):
            uploads.apply(plan, pack, tmp_path / "backup")
    uploads.restore(pack, tmp_path / "backup")
    assert contents(pack) == before


@pytest.mark.parametrize("source", ["relative", "file:///elsewhere", "gs://other/uploads/acme/one/",
                                    "gs://store/uploads/other/one/", "gs://store/uploads/acme-other/one/",
                                    "gs://store/uploads/acme/../other/", "gs://store/uploads/acme/a/?query=1"])
def test_store_source_must_be_in_this_clients_upload_area_without_io(tmp_path, source, monkeypatch):
    monkeypatch.setattr(uploads, "open_store_if_url", lambda _: pytest.fail("invalid source opened"))
    with pytest.raises(uploads.Refused, match="FROM must be under"):
        uploads.plan(source, tmp_path / "pack", "acme", "gs://store")
    assert not (tmp_path / "pack").exists()


@pytest.mark.parametrize("source", ["relative", "file:///upload", "gs://store/uploads/acme/one/"])
def test_without_store_an_absolute_folder_is_required(tmp_path, source):
    with pytest.raises(uploads.Refused, match="FROM must be under"):
        uploads.plan(source, tmp_path / "pack", "acme", None)


def test_missing_pack_changes_nothing(tmp_path):
    source, pack = tmp_path / "source", tmp_path / "pack"
    put(source, "glossary.yaml", b"{}")
    put(pack, "pack.yaml", b"old")
    with pytest.raises(uploads.Refused, match="holds no pack.yaml: nothing was applied"):
        uploads.plan(str(source), pack, "acme", None)
    assert contents(pack) == {"pack.yaml": b"old"}


def test_absolute_store_path_accepts_file_url_in_its_upload_area(tmp_path):
    source = tmp_path / "store/uploads/acme"
    put(source, "pack.yaml", b"{}")
    plan = uploads.plan(source.as_uri(), tmp_path / "pack", "acme", str(tmp_path / "store"))
    assert plan.files == {"pack.yaml": b"{}"}


def test_digest_uses_sorted_names_and_bytes(tmp_path):
    assert uploads.digest({"b": b"two", "a": b"one"}) == hashlib.sha256(b"a\0one\0b\0two\0").hexdigest()
    assert uploads.digest({"a": b"one"}) != uploads.digest({"a": b"two"})
    assert uploads.digest({"a": b"one"}) != uploads.digest({"b": b"one"})


def test_inputs_cannot_escape_through_a_symlink(tmp_path):
    source = tmp_path / "source"
    put(source, "pack.yaml", b"{}")
    outside = put(tmp_path, "outside", b"outside")
    (source / "input.yaml").symlink_to(outside)
    with pytest.raises(uploads.Refused, match="symbolic link"):
        uploads.plan(str(source), tmp_path / "pack", "acme", None)


def test_file_and_directory_replacements_restore(tmp_path):
    source, pack = tmp_path / "source", tmp_path / "pack"
    put(pack, "pack.yaml", b"{}")
    put(pack, "one", b"file")
    put(pack, "two/child", b"nested")
    put(source, "pack.yaml", b"{}")
    put(source, "one/child", b"nested now")
    put(source, "two", b"file now")
    before = contents(pack)
    plan = uploads.plan(str(source), pack, "acme", None)
    uploads.apply(plan, pack, tmp_path / "backup")
    assert contents(pack) == contents(source)
    uploads.restore(pack, tmp_path / "backup")
    assert contents(pack) == before
