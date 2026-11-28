"""A failed seed never publishes its staged files, and relinquishes the client lease."""
import pytest

from kge.adapters.objstore import open_store
from tests.unit.test_objstore import image as image


@pytest.mark.parametrize("code", [1, 137])
def test_failed_seed_keeps_store_and_landing_and_releases_lease(image, tmp_path, code):
    store = open_store(str(tmp_path / "store"))
    result, _ = image("setup-client", "CLIENT=acme", "ASKER=analyst")
    assert result.returncode == 0, result.stderr
    store.put("landing/acme/kept.txt", b"earlier landing")
    before = {key: store.get(key) for key in store.list()}
    result, calls = image("seed", env={"MAKE_EXIT": str(code), "MAKE_FAIL_AFTER": "1"})
    assert result.returncode == code, result.stderr
    assert [call["args"] for call in calls] == [["estate"]]
    assert (tmp_path / "app/build/landing/acme/current").is_symlink()
    assert {key: store.get(key) for key in store.list() if key != "jobs/acme/lease.json"} == before
    assert store.get("jobs/acme/lease.json") == b"{}"
    result, _ = image("seed", app="retry")
    assert result.returncode == 0, result.stderr
    assert store.get("work/acme.tar.gz") is not None
    assert store.get("landing/acme/current") is not None
