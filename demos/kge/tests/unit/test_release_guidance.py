"""A successful sign-off names the release command available to its operator."""
from types import SimpleNamespace

import pytest

from kge.graph import release
from kge.pipeline.settings import ROOT


@pytest.mark.parametrize("version,command", [(None, "make"), ("dev", "kge"), ("0.9.20", "kge")])
def test_sign_off_names_the_demo_or_kit_release_command(monkeypatch, tmp_path, capsys, version, command):
    if version is None:
        monkeypatch.delenv("KGE_VERSION", raising=False)
    else:
        monkeypatch.setenv("KGE_VERSION", version)
    model_hash = "ab" * 32
    principal = "steward_glossary.meridian@example.com"
    prof = SimpleNamespace(at=lambda value: SimpleNamespace(graph_path=lambda: tmp_path / value / "graph.ladybug"))
    monkeypatch.setattr(release, "resolve", lambda value: prof)
    monkeypatch.setattr(release, "sign_off", lambda *args, **kwargs: {"principal": principal, "run_id": "check-1"})
    assert release.main(["--hash", model_hash, "--sign-off", "--as", principal]) == 0
    output = capsys.readouterr().out
    assert f"signed off by {principal}" in output
    assert f". {command} release HASH={model_hash} swaps current to it" in output


@pytest.mark.parametrize("version,expected", [
    (None, "make margin HASH={h} checks it, make sign-off PRINCIPAL=<steward-email> HASH={h} signs it off, then make release HASH={h}"),
    ("0.9.20", "kge eval pre checks it, kge sign-off signs it off, then kge release HASH={h}")])
def test_a_loaded_candidate_names_the_demo_or_kit_commands(monkeypatch, tmp_path, capsys, version, expected):
    if version is None:
        monkeypatch.delenv("KGE_VERSION", raising=False)
    else:
        monkeypatch.setenv("KGE_VERSION", version)
    model_hash = "cd" * 32
    target = SimpleNamespace(graph_path=lambda: tmp_path / "graph.ladybug")
    prof = SimpleNamespace(model_hash=lambda: "ef" * 32, at=lambda value: target)
    monkeypatch.setattr(release, "resolve", lambda value: prof)
    monkeypatch.setattr(release, "mark", lambda value: {"release": "r1", "chunks": 3})
    release.main(["--hash", model_hash, "--mark-loaded"])
    output = capsys.readouterr().out
    assert expected.format(h=model_hash) in output
    assert ("make " in output) == (version is None)


def test_release_explains_stored_page_polling_and_other_process_restarts(monkeypatch, tmp_path, capsys):
    model_hash = "ab" * 32
    prof = SimpleNamespace(id="meridian", client="meridian", artifacts=tmp_path,
                           model_hash=lambda: "cd" * 32)
    monkeypatch.setattr(release, "resolve", lambda value: prof)
    monkeypatch.setattr(release, "release", lambda *args, **kwargs: ("cd" * 32, "signed off"))
    assert release.main(["--hash", model_hash]) == 0
    output = capsys.readouterr().out
    assert "A page release switches processes with STORE_URL, including Compose and Cloud Run, as they poll" in output
    assert "after this direct release, restart the API (docker compose restart app, or a new revision on Cloud Run)" in output


def test_guides_require_compose_restart_for_passwords_not_page_releases():
    steward = (ROOT / "docs/playbooks/steward.md").read_text()
    operator = (ROOT / "docs/playbooks/operator.md").read_text()
    install = (ROOT / "kit/INSTALL.md").read_text()
    assert "kge restart" not in steward
    assert "kge restart" in operator
    assert "The playbook restarts Compose after password changes only" in install
    assert "Compose and Cloud Run pages follow a completed page/API release on the next status poll" in install
