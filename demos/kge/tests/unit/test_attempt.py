"""Every contracted writer refuses task retries before its caller performs any work."""
import os
import subprocess
import sys
from dataclasses import fields

import pytest

from kge.pipeline import attempt
from kge.pipeline.settings import ENVIRONMENT_NAMES, ROOT, Settings, settings

WRITERS = ("onboard", "release", "sign-off", "passwords", "seed", "estate", "teardown", "onboard-request")


@pytest.mark.parametrize("command", WRITERS)
def test_each_writer_refuses_retries_with_the_exact_message(monkeypatch, command, capsys):
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "1")
    expected = f"an automatic retry cannot fix a failed {command}: fix the cause and run it again"
    assert attempt.retry_refusal(command) == expected
    assert attempt.main([command]) == 2
    assert capsys.readouterr().err.strip() == expected


@pytest.mark.parametrize("value", [None, "", " ", "\t\r\n", " \t\n ", "\u2003", "0", "00"])
def test_first_attempts_are_allowed(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CLOUD_RUN_TASK_ATTEMPT", raising=False)
    else:
        monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", value)
    assert attempt.WRITERS == WRITERS
    assert all(attempt.retry_refusal(command) is None for command in WRITERS)
    assert all(attempt.main([command]) == 0 for command in WRITERS)


@pytest.mark.parametrize("value", ["one", "1.0", "-1", "+1", " 0", "0 ", "1_0", "١", "NaN"])
def test_bad_attempt_names_the_setting(monkeypatch, value):
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", value)
    assert attempt.retry_refusal("onboard") == "CLOUD_RUN_TASK_ATTEMPT must be a whole number at least 0"


@pytest.mark.parametrize("value", ["1", "bad"])
def test_readers_are_unaffected(monkeypatch, value):
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", value)
    assert attempt.retry_refusal("serve") is None
    assert attempt.main(["serve"]) == 0


def test_module_exit_status_and_no_work(tmp_path):
    done = subprocess.run([sys.executable, "-m", "kge.pipeline.attempt", "onboard"], cwd=tmp_path,
                          env={**os.environ, "CLOUD_RUN_TASK_ATTEMPT": "1"}, capture_output=True, text=True)
    assert done.returncode == 2
    assert done.stderr.strip() == "an automatic retry cannot fix a failed onboard: fix the cause and run it again"
    assert list(tmp_path.iterdir()) == []


def test_missing_command_returns_usage(capsys):
    assert attempt.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_long_whole_number_is_a_refusal_not_a_conversion_error(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "9" * 5000)
    assert attempt.retry_refusal("seed") == "an automatic retry cannot fix a failed seed: fix the cause and run it again"


def test_new_settings_are_documented_and_keep_unset_distinct_from_empty(monkeypatch):
    names = ("ASKER", "BASELINE", "CLOUD_RUN_TASK_ATTEMPT", "CONFIRM", "PEOPLE", "PREVIEW", "PREVIOUS", "ROTATE")
    documented = (ROOT / ".env.example").read_text()
    assert len(ENVIRONMENT_NAMES) == len(set(ENVIRONMENT_NAMES))
    assert set(ENVIRONMENT_NAMES) == {field.name for field in fields(Settings)}
    for name in names:
        assert name + "=" in documented
        monkeypatch.delenv(name, raising=False)
        assert getattr(settings(), name) is None
        monkeypatch.setenv(name, "")
        assert getattr(settings(), name) == ""
