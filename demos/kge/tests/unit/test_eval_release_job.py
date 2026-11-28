"""A failed automatic check publishes its release but cannot record a new baseline."""
from kge.adapters.objstore import open_store
from tests.unit.test_objstore import image as image


def test_post_failure_publishes_release_and_skips_baseline(image, tmp_path):
    result, _ = image("setup-client", "CLIENT=acme", "ASKER=analyst")
    assert result.returncode == 0, result.stderr
    result, calls = image("release", env={"MAKE_EXIT": "3", "MAKE_FAIL_AFTER": "1", "MAKE_EXIT_TARGET": "release"})
    assert result.returncode == 3, result.stderr
    assert result.stderr.rstrip().endswith("run kge rollback")
    assert [call["args"] for call in calls] == [["release"], ["_stored-after", "COMMAND=release"]]
    store = open_store(str(tmp_path / "store"))
    assert store.get("releases/acme/current") == b"a1\n"
    assert store.get("releases/acme/a1.tar.gz") is not None
    assert store.get("work/acme.tar.gz") is not None
    assert store.list("clients/acme/baselines/") == []
    assert store.get("jobs/acme/lease.json") == b"{}"


def test_job_alias_runs_pre_and_refuses_post_before_work(image):
    result, _ = image("setup-client", "CLIENT=acme", "ASKER=analyst")
    assert result.returncode == 0, result.stderr
    result, calls = image("eval-pre")
    assert result.returncode == 0, result.stderr
    assert [call["args"][0] for call in calls] == ["eval-pre"]
    result, calls = image("eval-post")
    assert result.returncode == 2
    assert not calls
