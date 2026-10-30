"""The lanes are the folders: tests/lanes.py finds a test file out of place, and deals a lane's files into shards."""

from pathlib import Path

from tests import lanes


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
                "test_loose.py", "unit/deeper/test_c.py", "e2e/test_a.py", "fixtures/test_data.py")
    for name in lanes.OVERRIDES["integration"]:
        tree(root, f"unit/{name}")
    assert lanes.misplaced(root) == ["tests/test_loose.py is in no lane: move it to tests/unit/, tests/integration/ or tests/e2e/",
                                     "tests/unit/deeper/test_c.py is in no lane: move it to tests/unit/, tests/integration/ or tests/e2e/",
                                     "test_a.py is in more than one lane"]


def test_a_frozen_file_missing_or_an_override_without_its_file_is_named(tmp_path):
    problems = lanes.misplaced(tree(tmp_path, "test_context.py", "unit/test_a.py"))
    assert "tests/test_agent_loop.py is frozen in tests/ but not there" in problems
    assert all(f"{name} has overrides but is in no lane" in problems for name in lanes.OVERRIDES["integration"])


def test_a_lane_s_files_are_its_folder_s_with_the_overrides_and_the_frozen_two_in_integration(tmp_path):
    root = tree(tmp_path, "test_context.py", "test_agent_loop.py", "unit/test_a.py", "unit/test_bigquery.py",
                "integration/test_b.py", "e2e/test_c.py")
    assert lanes.files_in("unit", root) == ["tests/unit/test_a.py", "tests/unit/test_bigquery.py"]
    assert lanes.files_in("integration", root) == ["tests/integration/test_b.py", "tests/test_agent_loop.py",
                                                   "tests/test_context.py", "tests/unit/test_bigquery.py"]
    assert lanes.files_in("e2e", root) == ["tests/e2e/test_c.py"]


def test_the_shards_of_a_lane_hold_every_file_once():
    files = lanes.files_in("unit")
    for count in (1, 2, 4):
        dealt = lanes.shards(files, count)
        assert all(dealt) and sorted(f for shard in dealt for f in shard) == files


def test_lane_times_sum_a_junit_report_by_file_and_rewrite_only_that_lane_s_table(tmp_path):
    report = tmp_path / "integration.xml"
    report.write_text('<testsuites><testsuite name="pytest">'
                      '<testcase classname="tests.integration.test_b" name="test_one" time="2.4"/>'
                      '<testcase classname="tests.integration.test_b.TestGroup" name="test_two[x]" time="1.3"/>'
                      '<testcase classname="tests.test_context" name="test_three" time="0.2"/>'
                      '<testcase classname="tests.unit.test_bigquery" name="test_four" time="9.6"/>'
                      '</testsuite></testsuites>')
    times = lanes.measured([report])
    assert times == {"test_b.py": 4, "test_context.py": 1, "test_bigquery.py": 10}
    source = tmp_path / "lanes.py"
    source.write_text(Path(lanes.__file__).read_text())
    lanes.rewrite("integration", times, source)
    text = source.read_text()
    assert 'INTEGRATION_SECONDS = {\n    "test_bigquery.py": 10, "test_b.py": 4, "test_context.py": 1,\n}' in text
    namespace: dict = {"__file__": str(source)}
    exec(compile(text, str(source), "exec"), namespace)
    assert namespace["INTEGRATION_SECONDS"] == times and namespace["UNIT_SECONDS"] == lanes.UNIT_SECONDS
    assert lanes.shards(sorted(times), 2, times) == [["test_bigquery.py"], ["test_b.py", "test_context.py"]]
