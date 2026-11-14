"""Test the per-test and per-leg timing reports."""
import json
from types import SimpleNamespace

import pytest

from tests import timing


def test_report_lists_over_budget_tests_and_named_exception(tmp_path, monkeypatch, capsys):
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(json.dumps({"tests.integration.test_flow::test_slow": "A complete release."}))
    monkeypatch.setattr(timing, "EXCEPTIONS", exceptions)
    report = tmp_path / "junit.xml"
    report.write_text('<testsuite><testcase classname="tests.integration.test_flow" name="test_slow" time="11"/>'
                      '<testcase classname="tests.integration.test_flow" name="test_fast" time="1"/></testsuite>')
    timing.report("integration", report)
    output = capsys.readouterr().out
    assert "test_slow" in output and "A complete release." in output and "test_fast" not in output
    timing.report("unit", report)
    assert "test_fast" in capsys.readouterr().out


@pytest.mark.parametrize("name,seconds,over", [("unit-1/6", 601, True), ("integration-1/10", 899, False),
                                               ("integration-1/10", 901, True), ("e2e-1/4", 601, True),
                                               ("parity-margin", 2000, False)])
@pytest.mark.parametrize("tests_exit", [0, 3])
def test_leg_caps_warn_over_cap_and_keep_the_tests_exit_code(monkeypatch, capsys, name, seconds, over, tests_exit):
    readings = iter((0, seconds))
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(readings))
    monkeypatch.setattr(timing.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=tests_exit))
    assert timing.leg(name, ["make"]) == tests_exit
    output = capsys.readouterr().out
    assert ("::warning title=KGE full check leg over its cap::" in output) is over
    assert "::error::" not in output


def test_over_cap_lists_slowest_tests_from_current_report(tmp_path, monkeypatch, capsys):
    report = tmp_path / "unit.xml"
    report.write_text("stale")
    monkeypatch.setenv("KGE_TIMING_REPORT", str(report))
    readings = iter((0, 601))
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(readings))

    def run(*args, **kwargs):
        assert not report.exists()
        report.write_text('<testsuite><testcase classname="suite" name="fast" time="1"/>'
                          '<testcase classname="suite" name="slow" time="9"/></testsuite>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(timing.subprocess, "run", run)
    assert timing.leg("unit-1/6", ["make"]) == 0
    output = capsys.readouterr().out
    assert "Refresh the durations (make test-durations) or add a leg" in output
    assert "Its slowest tests: suite::slow (9.000s), suite::fast (1.000s)" in output
