"""The lanes are the folders, with named integration overrides."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from pytest_split.algorithms import LeastDurationAlgorithm

from tests import lanes
from tests.conftest import pytest_xdist_auto_num_workers


def test_worker_cap_uses_physical_memory_and_falls_back_safely(monkeypatch):
    import os

    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.setattr(os, "sysconf", lambda name: {"SC_PHYS_PAGES": 3 * 1024 ** 3, "SC_PAGE_SIZE": 4}[name])
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert pytest_xdist_auto_num_workers(None) == 3
    monkeypatch.setattr(os, "sysconf", lambda name: -1)
    assert pytest_xdist_auto_num_workers(None) == 1


@pytest.mark.parametrize("lane", ["unit", "integration"])
@pytest.mark.parametrize("count", [1, 2, 4, 6, 8])
def test_pytest_split_groups_cover_each_test_once(lane, count):
    durations = json.loads(Path(".test_durations").read_text())
    files = set(lanes.files_in(lane))
    whole = [name for name in durations if name.split("::", 1)[0] in files]
    items = [SimpleNamespace(nodeid=name) for name in whole]
    groups = LeastDurationAlgorithm()(count, items, durations)
    dealt = [item.nodeid for group in groups for item in group.selected]
    assert whole and all(group.selected for group in groups)
    assert len(whole) == len(set(whole))
    assert sorted(dealt) == sorted(whole)


def test_every_collected_unit_test_is_dealt_to_exactly_one_leg():
    """Tests the durations file does not yet name still land in one leg each, timed at the average."""
    command = [sys.executable, "-m", "pytest", *lanes.files_in("unit"), "-m", "unit", "--collect-only", "-q"]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    collected = [line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    durations = json.loads(Path(".test_durations").read_text())
    assert collected and len(collected) == len(set(collected))
    known = [durations[name] for name in collected if name in durations]
    average = sum(known) / len(known)
    timed = {name: durations.get(name, average) for name in collected}
    groups = LeastDurationAlgorithm()(6, [SimpleNamespace(nodeid=name) for name in collected], timed)
    assert sorted(item.nodeid for group in groups for item in group.selected) == sorted(collected)


def test_refreshing_durations_keeps_the_other_lane(tmp_path):
    demo = Path(lanes.__file__).resolve().parents[1]
    for lane in ("unit", "integration"):
        (tmp_path / f"test_{lane}.py").write_text(f"def test_{lane}():\n    pass\n")
    wrapper = tmp_path / "pytest-wrapper"
    wrapper.write_text(f"""#!{sys.executable}
import subprocess
import sys
from pathlib import Path
args = sys.argv[1:]
if args == ['tests/lanes.py', 'unit']:
    print('test_unit.py')
    sys.exit(0)
lane = args[max(i for i, arg in enumerate(args) if arg == '-m') + 1]
durations = next(arg for arg in args if arg.startswith('--durations-path='))
root = Path(__file__).parent
sys.exit(subprocess.call([sys.executable, '-m', 'pytest', str(root / ('test_' + lane + '.py')),
    '-c', '/dev/null', '-q', '--store-durations', durations, '--basetemp', str(root / ('temp-' + lane))]))
""")
    wrapper.chmod(0o755)
    done = subprocess.run(["make", "--no-print-directory", "-f", str(demo / "Makefile"), "test-durations",
                           f"PY={wrapper}", f"BUILD={tmp_path / 'build'}"], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    durations = json.loads((tmp_path / ".test_durations").read_text())
    assert any("test_unit.py::test_unit" in name for name in durations)
    assert any("test_integration.py::test_integration" in name for name in durations)
    integration = {name: seconds for name, seconds in durations.items() if "test_integration.py::" in name}
    again = subprocess.run([str(wrapper), "-m", "unit", f"--durations-path={tmp_path / '.test_durations'}"],
                           cwd=tmp_path, capture_output=True, text=True)
    assert again.returncode == 0, again.stdout + again.stderr
    refreshed = json.loads((tmp_path / ".test_durations").read_text())
    assert {name: refreshed[name] for name in integration} == integration


def tree(root: Path, *files: str) -> Path:
    for name in files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    return root


def test_the_committed_tests_are_each_in_one_lane_folder_or_frozen_in_tests():
    assert lanes.misplaced() == []
    held = lanes.placed()
    assert {Path(at).parent.name for at in held.values()} == {*lanes.LANES, "tests"}
    assert [at for at in held.values() if Path(at).parent.name == "tests"] == ["tests/test_context.py", "tests/test_agent_loop.py"]


def test_a_test_file_outside_the_lane_folders_or_two_deep_or_in_two_lanes_is_refused(tmp_path):
    root = tree(tmp_path, "test_context.py", "test_agent_loop.py", "unit/test_a.py", "integration/test_b.py",
                "test_loose.py", "unit/deeper/test_c.py", "e2e/test_a.py", "eval/deeper/test_d.py", "fixtures/test_data.py")
    for name in lanes.OVERRIDES["integration"]:
        tree(root, f"unit/{name}")
    assert lanes.misplaced(root) == ["tests/eval/deeper/test_d.py is in no lane: move it to tests/unit/, tests/integration/, tests/e2e/ or tests/eval/",
                                     "tests/test_loose.py is in no lane: move it to tests/unit/, tests/integration/, tests/e2e/ or tests/eval/",
                                     "tests/unit/deeper/test_c.py is in no lane: move it to tests/unit/, tests/integration/, tests/e2e/ or tests/eval/",
                                     "test_a.py is in more than one lane"]


def test_a_frozen_file_missing_or_an_override_without_its_file_is_named(tmp_path):
    problems = lanes.misplaced(tree(tmp_path, "test_context.py", "unit/test_a.py"))
    assert "tests/test_agent_loop.py is frozen in tests/ but not there" in problems
    assert all(f"{name} has overrides but is in no lane" in problems for name in lanes.OVERRIDES["integration"])


def test_a_lane_s_files_are_its_folder_s_with_the_overrides_and_the_frozen_two_in_integration(tmp_path):
    root = tree(tmp_path, "test_context.py", "test_agent_loop.py", "unit/test_a.py", "unit/test_bigquery.py",
                "integration/test_b.py", "e2e/test_c.py", "eval/test_d.py")
    assert lanes.files_in("unit", root) == ["tests/unit/test_a.py", "tests/unit/test_bigquery.py"]
    assert lanes.files_in("integration", root) == ["tests/integration/test_b.py", "tests/test_agent_loop.py",
                                                   "tests/test_context.py", "tests/unit/test_bigquery.py"]
    assert lanes.files_in("e2e", root) == ["tests/e2e/test_c.py"]
    assert lanes.files_in("eval", root) == ["tests/eval/test_d.py"]
    assert lanes.lane_of(root / "eval/test_d.py", "test_example") == "eval"


def test_worker_build_copies_data_and_relocates_every_profile(tmp_path, monkeypatch):
    import yaml
    from tests import scratch

    source = tmp_path / "build"
    source.mkdir()
    (source / "graph").write_text("original")
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": "a", "profiles": {
        "a": {"store": "build", "clients": "clients"},
        "b": {"store": "build", "baseline": None},
    }}))
    monkeypatch.setattr(scratch, "THE_BUILD", source)
    monkeypatch.setattr(scratch, "REGISTRY", registry)
    copy = scratch.worker_build(tmp_path / "worker")
    (copy / "graph").write_text("changed")
    data = yaml.safe_load((copy / scratch.OWN).read_text())
    assert (source / "graph").read_text() == "original"
    assert all(entry["store"] == str(copy) for entry in data["profiles"].values())
    assert data["profiles"]["a"]["clients"] == str(tmp_path / "clients")
    assert data["profiles"]["b"]["baseline"] is None
