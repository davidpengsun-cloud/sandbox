"""Capstone regressions for pending review, untrusted labels and stored requests."""
import json
from types import SimpleNamespace

import pytest
import yaml

from kge import packstore
from kge.estate import onboard
from kge.improve import abtest, flow, gather
from kge.pipeline import objstore
from kge.services.onboard import jobs
from tests.unit.test_improve_flow import prof
from tests.unit.test_knowledge_engineer import boundary, call, ENGINEER
from tests.unit.test_objstore import released_role_store

__all__ = ["prof", "boundary"]
DETAIL = "A proposal is awaiting review: accept, certify, run checks, sign-off and release it first."
START_JOB = jobs.start


def pending(prof, state="drafted", pid="open"):
    record = {"id": pid, "kept": True, "state": state, "after_run_id": "checked",
              "candidate_hash": "b" * 64, "items": [{"id": "alias-1", "unit": "sme", "state": state}]}
    abtest.write_proposal_record(prof, pid, record)
    return record


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("state", ["drafted", "accepted", "certified"])
def test_pending_proposal_refuses_improve_before_job_on_both_transports(boundary, transport, state):
    profile = boundary[1]
    pending(profile, state)
    path = profile.clients / profile.client / "proposals/open/proposal.yaml"
    before = path.read_bytes()
    code, body = call(boundary, transport, ENGINEER, "improve", {"unit": "retail"})
    assert code == (409 if transport == "http" else 400)
    assert body == {"detail": DETAIL} if transport == "http" else body.endswith(f"409: {DETAIL}")
    assert boundary[2] == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("state", ["drafted", "accepted", "certified"])
@pytest.mark.parametrize("outcome", ["done", "failed"])
def test_pending_proposal_survives_done_or_failed_improvement(prof, monkeypatch, state, outcome):
    pending(prof, state)
    path = prof.clients / prof.client / "proposals/open/proposal.yaml"
    before = path.read_bytes()
    entered = []

    def read(profile, **kwargs):
        entered.append(True)
        if outcome == "failed":
            raise RuntimeError("failed checks")
        return {"ab_misses": [], "page_misses": [], "evidence": {
            "before_run_id": "before", "served_hash": "a" * 64, "base_pack_hash": "a" * 64,
            "base_version": "1.0.0", "certified_terms": {}, "existing_aliases": {}, "columns": {}}}

    monkeypatch.setattr(gather, "gather", read)
    with pytest.raises(onboard.OnboardError, match=DETAIL) as error:
        flow.run_improve(prof, unit="retail", as_=ENGINEER, llm=None)
    assert error.value.status == 409
    assert not entered
    assert path.read_bytes() == before


@pytest.mark.parametrize("outcome", ["done", "failed"])
@pytest.mark.parametrize("pid", ["open", "other"])
@pytest.mark.parametrize("state", ["drafted", "certified"])
def test_proposal_arriving_during_run_survives_cleanup(prof, monkeypatch, outcome, pid, state):
    before = []

    def read(profile, **kwargs):
        pending(prof, state=state, pid=pid)
        before.append(packstore.for_profile(prof).read(f"proposals/{pid}/proposal.yaml"))
        if outcome == "failed":
            raise RuntimeError("failed checks")
        return {"ab_misses": [], "page_misses": [], "evidence": {
            "before_run_id": "before", "served_hash": "a" * 64, "base_pack_hash": "a" * 64,
            "base_version": "1.0.0", "certified_terms": {}, "existing_aliases": {}, "columns": {}}}

    monkeypatch.setattr(gather, "gather", read)
    if outcome == "failed":
        with pytest.raises(RuntimeError, match="failed checks"):
            flow.run_improve(prof, llm=None)
    else:
        assert flow.run_improve(prof, llm=None)["proposal"] is None
    assert packstore.for_profile(prof).read(f"proposals/{pid}/proposal.yaml") == before[0]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("label", ["", " ", "x" * 81, "x\ny", "x\ry", "x\ty", "x\x00y", "x\x7fy", "x\u0085y", "x\u2028y", "x\u202ey"])
def test_bad_labels_are_refused_before_source_creation(boundary, transport, label):
    code, body = call(boundary, transport, ENGINEER, "source", {
        "unit": "sample", "label": label, "ddl": "CREATE TABLE t (id INTEGER)", "rows": "none"})
    assert code == (422 if transport == "http" else 400)
    assert "label" in str(body)
    if transport == "mcp":
        assert "422:" in body
    assert not onboard.show(boundary[1])["units"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("value,status", [("x" * 64, 422), ("../other", 422), ("b" * 64, 404)])
def test_unknown_checks_hash_does_not_start_job(boundary, monkeypatch, transport, value, status):
    monkeypatch.setattr(jobs, "start", START_JOB)
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "save", lambda *args: pytest.fail("job saved before hash validation"))
    code, body = call(boundary, transport, ENGINEER, "checks", {"hash": value})
    assert code == (status if transport == "http" else 400)
    if transport == "mcp":
        assert f"{status}:" in body


@pytest.fixture
def stored(tmp_path, monkeypatch):
    store = released_role_store(tmp_path, [{"id": "engineer", "reads": ["retail"], "processes": ["improving"]}])
    identities = tmp_path / "identities.yaml"
    identities.write_text("principals:\n  - {principal: operator, role: engineer}\n")
    profile = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda *args: profile)
    calls = []
    monkeypatch.setattr(objstore.subprocess, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    request = {"id": "20261024T120000Z-abcdef", "command": "improve", "principal": "operator",
               "unit": "retail", "timestamp": "2026-10-24T12:00:00Z"}
    return store, profile, request, calls


@pytest.mark.parametrize("state", ["drafted", "accepted", "certified"])
def test_stored_improvement_refuses_pending_review_before_execution(stored, tmp_path, state):
    store, profile, request, calls = stored
    pending(profile, state)
    path = profile.clients / profile.client / "proposals/open/proposal.yaml"
    before = path.read_bytes()
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    with pytest.raises(objstore.Refused, match=f"409: {DETAIL}"):
        objstore.onboard_request(tmp_path)
    assert not calls
    assert path.read_bytes() == before
    assert json.loads(store.get("clients/meridian/onboard/job.json"))["state"] == "failed"


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", {}), ("id", "forged"), ("id", "20261024T120000Z-abcdef\n"),
    ("checked_hash", "g" * 64), ("checked_hash", []), ("checks_id", {}), ("checks_id", "forged"),
    ("hash", {}), ("hash", "../../other"), ("checked_hash", "a" * 64),
])
def test_stored_identifiers_are_validated_before_running(stored, tmp_path, field, value):
    store, _, request, calls = stored
    request[field] = value
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    with pytest.raises(objstore.Refused, match=field):
        objstore.onboard_request(tmp_path)
    assert not calls
    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["state"] == "failed" and job["checked_hash"] is None and job["checks_id"] is None
    if field == "id":
        assert job["id"] is None


def test_stored_improve_forwards_principal_and_clears_unearned_checks(stored, tmp_path):
    store, _, request, calls = stored
    request.update(checked_hash="b" * 64, checks_id="20261024T110000Z-abcdef")
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    objstore.onboard_request(tmp_path)
    assert calls[0][-2:] == ["UNIT=retail", "PRINCIPAL=operator"]
    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["checked_hash"] is None and job["checks_id"] is None


@pytest.mark.parametrize("label", ['A "quoted": [unit] <b> & *text*', "x" * 80])
def test_dictionary_label_round_trips_yaml_and_cannot_inject_markdown(prof, label):
    from kge.pipeline.settings import ROOT
    from kge.services.onboard.routes import SourceBody

    assert SourceBody(unit="leasing", label=label, ddl="schema").label == label
    onboard.create(prof, "leasing", ROOT / "examples/leasing/schema.sql", label, as_=ENGINEER)
    onboard.draft(prof, "leasing", ENGINEER, "2026-10-17")
    onboard.build(prof)
    text = (prof.estate() / "docs/uncertified/DD-LEASING.md").read_text()
    front, body = text[4:].split("\n---\n", 1)
    assert yaml.safe_load(front) == {"title": f"{label} data dictionary (drafted)", "kind": "data_dictionary",
                                     "unit": "leasing", "owner": f"{label} Data Steward", "version": "draft"}
    heading = body.strip().splitlines()[0]
    assert heading.startswith("# ") and "<b>" not in heading
    if len(label) == 80:
        assert heading == "# " + label + r" data dictionary \(drafted\)"
    else:
        assert heading == r'# A "quoted": \[unit\] &lt;b&gt; &amp; \*text\* data dictionary \(drafted\)'


def test_unscoped_improvement_rechecks_the_principals_process(prof):
    with pytest.raises(onboard.OnboardError, match="not an improving identity") as error:
        flow.run_improve(prof, as_="analyst_retail", llm=None)
    assert error.value.status == 403


def test_job_start_rechecks_pending_review_inside_its_lock(prof, monkeypatch):
    pending(prof)
    monkeypatch.setattr(jobs, "load", lambda app: None)
    monkeypatch.setattr(jobs, "save", lambda *args: pytest.fail("must refuse before saving"))
    with pytest.raises(onboard.OnboardError, match=DETAIL):
        jobs.start(SimpleNamespace(PROFILE=prof), "improve", None, ENGINEER, {})


def test_cleanup_preserves_kept_remote_proposals_and_only_removes_its_own(prof, monkeypatch, tmp_path):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    pending(prof)
    pending(prof, pid="other")
    store = packstore.for_profile(prof)
    before = {key: store.read(key) for key in store.list("proposals")}
    flow.clear_open_proposals(prof)
    assert {key: store.read(key) for key in store.list("proposals")} == before
    with pytest.raises(onboard.OnboardError, match=DETAIL):
        onboard.require_no_pending_proposal(prof)
    abtest.write_proposal_record(prof, "open", {"kept": False, "state": "drafted"})
    flow.clear_open_proposals(prof)
    assert not store.exists("proposals/open/proposal.yaml")
    assert store.read("proposals/other/proposal.yaml") == before["proposals/other/proposal.yaml"]


@pytest.mark.parametrize("source", ["current", "job", "proposal"])
def test_checks_accept_a_current_or_known_candidate(prof, monkeypatch, source):
    model_hash = prof.model_hash() if source == "current" else "b" * 64
    monkeypatch.setattr(jobs, "load", lambda app: {"hash": model_hash} if source == "job" else None)
    monkeypatch.setattr(jobs, "state", lambda *args: {"loaded": False})
    monkeypatch.setattr(jobs, "save", lambda *args: None)
    starts = []
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: starts.append(True)))
    shown = {"proposal": {"candidate_hash": model_hash}} if source == "proposal" else {}
    result = jobs.start(SimpleNamespace(PROFILE=prof), "checks", model_hash, ENGINEER, shown)
    jobs.active.discard(result["job"]["id"])
    assert starts == [True]


@pytest.mark.parametrize("label", ["", "x" * 81, "x\ny", "x\x00y"])
def test_command_onboarding_rejects_bad_labels_before_landing(prof, label):
    with pytest.raises(onboard.OnboardError, match="label") as error:
        onboard.create(prof, "sample", label=label, ddl_text="CREATE TABLE t (id INTEGER)", as_=ENGINEER)
    assert error.value.status == 422
    assert not packstore.for_profile(prof).list("units")


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_released_proposal_allows_improve_on_both_transports(boundary, transport):
    profile = boundary[1]
    released = profile.artifacts / profile.client / ("a" * 64)
    released.mkdir(parents=True)
    (released.parent / "current").symlink_to(released.name)
    record = pending(profile, "certified")
    record["candidate_hash"] = profile.model_hash()
    abtest.write_proposal_record(profile, "open", record)
    code, body = call(boundary, transport, ENGINEER, "improve")
    assert code == (202 if transport == "http" else 200), body
    assert len(boundary[2]) == 1


def test_released_proposal_allows_flow_and_cleanup(prof, monkeypatch):
    record = pending(prof, "certified")
    record["candidate_hash"] = prof.model_hash()
    abtest.write_proposal_record(prof, "open", record)
    monkeypatch.setattr(flow, "build_workflow", lambda *args, **kwargs: SimpleNamespace(invoke=lambda state: state))
    assert flow.run_improve(prof, llm=None)["proposal_id"] == "open"
    flow.clear_open_proposals(prof)
    assert not packstore.for_profile(prof).exists("proposals/open/proposal.yaml")


def test_stored_released_proposal_allows_improve(stored, tmp_path, monkeypatch):
    from kge.graph import release

    monkeypatch.setattr(release, "state", lambda *args: {})
    store, profile, request, calls = stored
    record = pending(profile, "certified")
    record["candidate_hash"] = "a" * 64
    abtest.write_proposal_record(profile, "open", record)
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    objstore.onboard_request(tmp_path)
    assert len(calls) == 1
    assert store.get("clients/meridian/onboard/request.json") == b""


BAD_PROPOSALS = [b": not yaml: [", b"[]", b"scalar", b"", b"items: [broken]", b"\xff", b"items: [{}]",
                 b'kept: true\nitems: [{id: alias, kind: term, pack: broken}]',
                 b'kept: true\nitems: [{id: alias, kind: term, pack: {glossary: [broken]}}]',
                 b'kept: true\nitems: [{id: alias, expect: broken}]',
                 b'kept: true\ntitle: "\\ud83d"', b'kept: true\nloop: &loop [*loop]', b"{}", b'kept: "true"']


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("data", BAD_PROPOSALS)
def test_unreadable_proposal_status_and_improve_are_plain(boundary, transport, data):
    profile = boundary[1]
    store = packstore.for_profile(profile)
    store.write("proposals/open/proposal.yaml", data)
    code, body = call(boundary, transport, ENGINEER, "status")
    assert code == 200, body
    assert body["proposal"]["state"] == "unreadable"
    assert "could not be read" in body["proposal"]["status"]
    code, body = call(boundary, transport, ENGINEER, "improve")
    assert code == (409 if transport == "http" else 400), body
    assert "could not be read" in str(body)
    assert not boundary[2]
    assert store.read("proposals/open/proposal.yaml") == data


@pytest.mark.parametrize("data", BAD_PROPOSALS)
def test_stored_unreadable_proposal_consumes_request(stored, tmp_path, data):
    store, profile, request, calls = stored
    packstore.for_profile(profile).write("proposals/open/proposal.yaml", data)
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    with pytest.raises(objstore.Refused, match="409: A proposal could not be read"):
        objstore.onboard_request(tmp_path)
    assert not calls
    assert store.get("clients/meridian/onboard/request.json") == b""
    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["state"] == "failed" and "could not be read" in job["message"]


@pytest.mark.parametrize("data", BAD_PROPOSALS)
def test_flow_preserves_unreadable_proposals(prof, data):
    store = packstore.for_profile(prof)
    store.write("proposals/open/proposal.yaml", data)
    with pytest.raises(onboard.OnboardError, match="could not be read"):
        flow.run_improve(prof, llm=None)
    flow.clear_open_proposals(prof)
    assert store.read("proposals/open/proposal.yaml") == data


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("label", ["می\u200cخواهم", "क\u200dष"])
def test_letter_joiners_are_valid_labels(boundary, transport, label):
    code, body = call(boundary, transport, ENGINEER, "source", {
        "unit": "sample", "label": label, "ddl": "CREATE TABLE t (id INTEGER)", "rows": "none"})
    assert code == 200, body
    assert onboard.show(boundary[1])["units"][0]["label"] == label


@pytest.mark.parametrize("char", ["\u200b", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
                                  "\u2066", "\u2067", "\u2068", "\u2069", "\ufeff", "\ud83d", "\x00"])
def test_other_control_format_and_surrogate_labels_raise_value_error(char):
    from kge.services.onboard.routes import _unit_label

    with pytest.raises(ValueError, match="label"):
        _unit_label(f"a{char}b")


@pytest.mark.parametrize("label", ["\u200ca", "a\u200d", "1\u200cb", "a\u200d1", "a\u200c\u200db", "👨\u200d👩"])
def test_joiners_need_letters_on_both_sides(label):
    with pytest.raises(onboard.OnboardError, match="label"):
        onboard.validate_unit_label(label)


def test_source_surrogate_is_a_value_error():
    from kge.services.onboard import service
    from kge.services.onboard.routes import SourceBody

    body = SourceBody(unit="sample", label="Sample", ddl="\ud83d", rows="none")
    with pytest.raises(ValueError):
        service._ddl(body)


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_source_surrogate_is_plain_422(boundary, transport, monkeypatch):
    original = boundary[0].post

    def ascii_post(path, **kwargs):
        kwargs["content"] = json.dumps(kwargs.pop("json")).encode()
        kwargs["headers"]["content-type"] = "application/json"
        return original(path, **kwargs)

    monkeypatch.setattr(boundary[0], "post", ascii_post)
    code, body = call(boundary, transport, ENGINEER, "source", {
        "unit": "sample", "label": "Sample", "ddl": "\ud83d", "rows": "none"})
    assert code == (422 if transport == "http" else 400), body
    if transport == "mcp":
        assert "422:" in body


def test_mcp_upload_refusal_names_all_http_limits():
    from kge.services.onboard.mcp import _small
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as error:
        _small("x" * (256 * 1024 + 1), None, None, None)
    assert all(limit in str(error.value) for limit in ("4 MiB", "1 MiB", "256 KiB"))


def test_label_surrogate_is_a_pydantic_value_error():
    from kge.services.onboard.routes import SourceBody

    with pytest.raises(ValueError):
        SourceBody(unit="sample", label="Re\ud83d", ddl="CREATE TABLE t (id INTEGER)", rows="none")


@pytest.mark.parametrize("failure", [OSError("secret/path"), objstore.StoreError("secret/store")])
def test_unreadable_store_proposal_is_preserved(prof, monkeypatch, failure):
    pending(prof, "certified")
    store = packstore.for_profile(prof)
    original = store.read

    def unreadable(name):
        if name.endswith("/proposal.yaml"):
            raise failure
        return original(name)

    monkeypatch.setattr(store, "read", unreadable)
    monkeypatch.setattr(packstore, "for_profile", lambda prof: store)
    assert onboard.show(prof)["proposal"]["state"] == "unreadable"
    assert "could not be read" in onboard.status_lines(prof)["said"]
    with pytest.raises(onboard.OnboardError, match="could not be read"):
        onboard.require_no_pending_proposal(prof)
    flow.clear_open_proposals(prof)
    assert store.exists("proposals/open/proposal.yaml")


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_status_reports_unreadable_older_proposal(boundary, transport):
    profile = boundary[1]
    pending(profile, pid="zz-latest")
    packstore.for_profile(profile).write("proposals/older/proposal.yaml", b"[")
    code, body = call(boundary, transport, ENGINEER, "status")
    assert code == 200
    assert body["proposal"]["id"] == "zz-latest"
    assert body["proposal_errors"][0]["id"] == "older"
    assert "could not be read" in body["proposal_errors"][0]["status"]
