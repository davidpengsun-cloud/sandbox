"""Round-three onboarding regressions across the CLI, HTTP and MCP boundaries."""
import json

import pytest

from kge import packstore
from kge.estate import onboard
from kge.estate.onboard import cli
from kge.improve import abtest
from kge.pipeline import objstore
from tests.unit.test_improve_flow import prof
from tests.unit.test_knowledge_engineer import boundary, call, ENGINEER
from tests.unit.test_onboard_safety import pending, stored

__all__ = ["prof", "boundary", "stored"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("label", ["क्\u200dष", "ශ්\u200dරී ලංකා", "க்\u200cஷ", "র্\u200dয", "ರ್\u200dಯ", "ന്\u200dന", "بّ\u200cب"])
def test_script_joiner_labels_round_trip(boundary, transport, label):
    code, body = call(boundary, transport, ENGINEER, "source", {
        "unit": "sample", "label": label, "ddl": "CREATE TABLE t (id INTEGER)", "rows": "none"})
    assert code == 200, body
    assert onboard.show(boundary[1])["units"][0]["label"] == label


@pytest.mark.parametrize("joiner", ["\u200c", "\u200d"])
@pytest.mark.parametrize("left", ["a", "\u094d", "\u093e"])
@pytest.mark.parametrize("right", ["b", "\u094d", "\u093e"])
def test_joiners_allow_letter_and_mark_categories(joiner, left, right):
    label = left + joiner + right
    assert onboard.validate_unit_label(label) == label


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("label", ["👨\u200d👩\u200d👧", "Team 👨\u200d💻", "ന്\u200d", "a\u2060b", "a\u202eb"])
def test_non_name_joiners_and_other_format_characters_stay_refused(boundary, transport, label):
    code, body = call(boundary, transport, ENGINEER, "source", {
        "unit": "sample", "label": label, "ddl": "CREATE TABLE t (id INTEGER)", "rows": "none"})
    assert code == (422 if transport == "http" else 400), body
    if transport == "mcp":
        assert "422:" in body
    assert not onboard.show(boundary[1])["units"]


@pytest.mark.parametrize("state", ["drafted", "accepted", "certified"])
@pytest.mark.parametrize("remote", [False, True])
def test_reset_cli_preserves_pending_proposal_until_explicit_force(prof, monkeypatch, capsys, tmp_path, state, remote):
    if remote:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
        store = packstore.for_profile(prof)
        for name in ("pack.yaml", "pack.lock"):
            store.write(name, (prof.clients / prof.client / name).read_bytes())
    onboard.create(prof, "sample", ddl_text="CREATE TABLE t (id INTEGER)", rows="none", as_=ENGINEER)
    pending(prof, state, "review-me")
    store = packstore.for_profile(prof)
    before = {name: store.read(name) for name in store.list("")}
    current = prof.model_hash()
    monkeypatch.setattr(cli, "resolve", lambda _: prof)
    assert cli.main(["reset", "--unit", "sample", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == 409 and "proposal review-me" in result["error"] and "--force" in result["error"]
    assert {name: store.read(name) for name in store.list("")} == before
    assert prof.model_hash() == current
    assert cli.main(["reset", "--unit", "sample", "--force", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reset"] == "unit"
    assert not store.exists("proposals/review-me/proposal.yaml")
    assert not store.list("units/sample")


def test_reset_cli_documents_force(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["reset", "--help"])
    assert exited.value.code == 0
    help_text = capsys.readouterr().out
    assert "--force" in help_text and "discard pending" in help_text


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_heldout_regression_is_not_pending_and_does_not_block_improve(boundary, transport):
    profile = boundary[1]
    record = {**pending(profile), "heldout_fell": True}
    abtest.write_proposal_record(profile, "open", record)
    assert not objstore.proposal_pending(record, profile.model_hash())
    code, body = call(boundary, transport, ENGINEER, "status")
    assert code == 200 and body["proposal"] is None
    code, body = call(boundary, transport, ENGINEER, "improve")
    assert code == (202 if transport == "http" else 200), body
    assert len(boundary[2]) == 1


@pytest.mark.parametrize("after_run_id", ["checked", None])
@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_older_pending_proposal_cannot_be_hidden_by_newer_unkept_record(boundary, transport, after_run_id):
    profile = boundary[1]
    record = {**pending(profile, pid="older"), "after_run_id": after_run_id}
    abtest.write_proposal_record(profile, "older", record)
    abtest.write_proposal_record(profile, "zz-newer", {"id": "zz-newer", "kept": False})
    code, body = call(boundary, transport, ENGINEER, "status")
    assert code == 200 and body["proposal"]["id"] == "older"
    code, body = call(boundary, transport, ENGINEER, "improve")
    assert code == (409 if transport == "http" else 400), body
    assert not boundary[2]


def test_stored_heldout_regression_allows_improve(stored, tmp_path, monkeypatch):
    from kge.graph import release

    monkeypatch.setattr(release, "state", lambda *args: {})
    store, profile, request, calls = stored
    abtest.write_proposal_record(profile, "open", {**pending(profile), "heldout_fell": True})
    key = f"clients/{profile.client}/onboard/request.json"
    store.put(key, json.dumps(request).encode())
    objstore.onboard_request(tmp_path)
    assert len(calls) == 1
    assert store.get(key) == b""


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("step", ["checks", "improve"])
@pytest.mark.parametrize("proposal", ["../x", "open", "", None])
def test_run_refuses_unused_proposal_before_starting_job(boundary, transport, step, proposal):
    code, body = call(boundary, transport, ENGINEER, step, {"proposal": proposal})
    assert code == (422 if transport == "http" else 400), body
    assert "proposal" in str(body)
    if transport == "mcp":
        assert "422:" in body
    assert not boundary[2]


@pytest.mark.parametrize("step", ["sign-off", "release"])
def test_governance_runs_also_refuse_unused_proposal(boundary, step):
    code, body = call(boundary, "http", "steward_glossary", step, {"proposal": "../x"})
    assert code == 422 and "proposal" in str(body)
    assert not boundary[2]


def test_reset_preserves_an_unreadable_proposal(prof):
    onboard.create(prof, "sample", ddl_text="CREATE TABLE t (id INTEGER)", rows="none", as_=ENGINEER)
    store = packstore.for_profile(prof)
    store.write("proposals/broken/proposal.yaml", b"[")
    before = {name: store.read(name) for name in store.list("")}
    with pytest.raises(onboard.OnboardError, match="proposal broken could not be read") as refused:
        onboard.reset(prof, "sample")
    assert refused.value.status == 409
    assert {name: store.read(name) for name in store.list("")} == before


def test_reset_allows_a_released_certified_proposal_without_force(prof):
    onboard.create(prof, "sample", ddl_text="CREATE TABLE t (id INTEGER)", rows="none", as_=ENGINEER)
    record = {**pending(prof, "certified"), "candidate_hash": prof.model_hash()}
    abtest.write_proposal_record(prof, "open", record)
    assert onboard.reset(prof, "sample")["reset"] == "unit"
    assert not packstore.for_profile(prof).list("proposals")
