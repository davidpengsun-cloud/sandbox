"""Shared fixtures. The demo's packages import as the editable install (pip install -e .) puts them; tests/ is a package
under the demo folder, which pytest puts on sys.path, so its helpers import as tests.<module>."""
from pathlib import Path

import pytest

from tests import lanes
from tests.scratch import compile_pack, open_engine


def pytest_configure(config):
    """The test lanes, tests/lanes.py: a test file outside the lanes' folders stops the run before anything is collected."""
    problems = lanes.misplaced(Path(__file__).resolve().parent)
    if problems:
        raise pytest.UsageError("tests/lanes.py:\n" + "\n".join(problems))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Marks every test with its lane, its folder's or its override's, before -m selects on it. An override naming no
    test of its file stops the run, when the whole file was collected."""
    seen: dict[str, set[str]] = {}
    for item in items:
        name = item.path.name
        test = getattr(item, "originalname", None) or item.name
        item.add_marker(getattr(pytest.mark, lanes.lane_of(item.path, test)))
        seen.setdefault(name, set()).add(test)
    if config.option.keyword or any("::" in arg for arg in config.args):
        return
    stale = [f"{name}::{test}" for tests in lanes.OVERRIDES.values() for name, held in tests.items() if name in seen
             for test in sorted(held - seen[name])]
    if stale:
        raise pytest.UsageError("tests/lanes.py names tests that are not there:\n" + "\n".join(stale))


@pytest.fixture(scope="session", autouse=True)
def the_caller_s_profile_steers_no_test():
    """The suite tests the demo's own build, the registry's default profile, run as make test or as bare pytest alike.
    make exports PROFILE (the registry's default) to every step, make test included, so without this a test that builds
    a registry of its own and names no profile would look the caller's id up in it."""
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("PROFILE", raising=False)
        mp.delenv("PROFILES", raising=False)
        yield


@pytest.fixture(scope="session", autouse=True)
def no_ambient_google_credentials(tmp_path_factory):
    """No test reaches Google with the machine's own credentials: application default credentials point at a file that
    is not there, in this process and every one it starts, so a test that would sign in fails here as it does on a
    runner with none (a review of #97: one passed on a Mac with gcloud's credentials and failed in CI). A test that
    needs an account stands one in (bq_account, mock_account)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path_factory.mktemp("no-credentials") / "none.json"))
        mp.setenv("CLOUDSDK_CONFIG", str(tmp_path_factory.mktemp("no-gcloud")))
        yield


@pytest.fixture(scope="module")
def eng():
    """The demo's own graph, opened once per module. The API holding it fails the module's tests, naming the API's
    process (tests/unit/test_lock.py)."""
    e = open_engine()
    yield e
    e.close()


@pytest.fixture(scope="session")
def compiled(tmp_path_factory) -> Path:
    """The committed pack compiled once for the session, as make compile does: the artifact a test's graph load reads."""
    return compile_pack(tmp_path_factory.mktemp("artifacts"))
