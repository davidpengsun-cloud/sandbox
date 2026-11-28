"""Fold wiring keeps the job and candidate gate distinct from the served graph."""

from types import SimpleNamespace

import pytest

from kge import cli
from kge.eval import shapefold
from kge.pipeline import objstore
from kge.services.onboard import routes, service
from tests.unit.test_onboard_atomic import HASH

pytest_plugins = ["tests.unit.test_onboard_atomic", "tests.unit.test_image"]


def test_fold_command_targets_served_or_explicit_candidate(monkeypatch, capsys):
    monkeypatch.setattr(cli, "profile_words", lambda *args: {})
    prof = object()
    monkeypatch.setattr(cli.profile, "resolve", lambda: prof)
    calls = []
    monkeypatch.setattr(shapefold, "run", lambda p, *, model_hash: (
        calls.append((p, model_hash)) or SimpleNamespace(line=lambda: "fold complete")
    ))
    cli.Commands({}, {}).fold()
    cli.Commands({"HASH": "candidate"}, {}).fold()
    assert calls == [(prof, None), (prof, "candidate")]
    assert capsys.readouterr().out.count("fold: fold complete") == 2
    assert cli.parse(["fold"]) == (["fold"], {})
    assert "fold" in objstore.JOBS


def test_fold_refusal_is_a_command_failure_and_candidate_fold_does_not_change_gate(monkeypatch, capsys):
    monkeypatch.setattr(cli, "profile_words", lambda *args: {})
    monkeypatch.setattr(cli.profile, "resolve", lambda: object())

    def refuse(*args, **kwargs):
        raise shapefold.NotFolded("graph is busy")

    monkeypatch.setattr(shapefold, "run", refuse)
    commands = cli.Commands({}, {})
    with pytest.raises(cli.Failed) as exc:
        commands.fold()
    assert exc.value.returncode == 2
    assert "fold: graph is busy" in capsys.readouterr().err
    commands.fold_candidate(object(), HASH)
    assert "onboard: fold: skipped: graph is busy" in capsys.readouterr().out


def test_onboard_folds_once_after_passed_margin(atomic, monkeypatch, capsys):
    commands, prof, _, scenario, *_ = atomic
    calls = []
    monkeypatch.setattr(commands, "fold_candidate", lambda p, h: calls.append((p, h, list(scenario["steps"]))))
    commands.onboard()
    assert len(calls) == 1
    assert calls[0][0] is prof and calls[0][1] == HASH
    assert calls[0][2][-1] == "margin"
    assert "onboard: gate passed" in capsys.readouterr().out


def test_onboard_failed_margin_does_not_fold(atomic, monkeypatch):
    commands, _, _, scenario, *_ = atomic
    scenario["failure"] = "margin"
    monkeypatch.setattr(commands, "fold_candidate", lambda *args: pytest.fail("fold ran after a failed gate"))
    with pytest.raises(cli.Failed):
        commands.onboard()


def test_fold_report_is_only_returned_for_its_candidate(atomic, monkeypatch):
    commands, prof, _, _, *_ = atomic
    monkeypatch.setattr(commands, "fold_candidate", lambda *args: None)
    commands.onboard()
    report = {"target": "candidate", "hash": HASH, "observations": 2, "written": True}
    monkeypatch.setattr(shapefold, "candidate_report", lambda p, h: report)
    current = service.margin_report(prof, HASH)
    assert current["fold"] == report
    assert routes.MarginResponse(**current).model_dump()["fold"] == report
    monkeypatch.setattr(shapefold, "candidate_report", lambda p, h: {**report, "hash": "b" * 64})
    assert service.margin_report(prof, HASH)["fold"] is None
    monkeypatch.setattr(shapefold, "candidate_report", lambda p, h: None)
    monkeypatch.setattr(shapefold, "last_report", lambda p: None)
    assert service.margin_report(prof, HASH)["fold"] is None


def test_local_margin_fallback_rejects_served_report_for_same_hash(atomic, monkeypatch):
    commands, prof, _, _, *_ = atomic
    monkeypatch.setenv("STORE_URL", "")
    monkeypatch.setattr(commands, "fold_candidate", lambda *args: None)
    commands.onboard()
    monkeypatch.setattr(shapefold, "candidate_report", lambda p, h: None)
    monkeypatch.setattr(shapefold, "last_report", lambda p: {"target": "served", "hash": HASH, "written": True})
    assert service.margin_report(prof, HASH)["fold"] is None


def test_image_entrypoint_dispatches_fold_as_one_job(entry):
    result, calls = entry("fold", "HASH=candidate")
    assert result.returncode == 0, result.stderr
    assert [call["args"] for call in calls] == [["fold", "HASH=candidate"]]
    result, calls = entry("unknown")
    assert result.returncode == 2 and calls == [] and "fold" in result.stderr
