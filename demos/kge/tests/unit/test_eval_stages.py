"""Automatic release comparison keeps exact evidence and never gates on model grades."""
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters import released
from kge.eval import stages
from kge.graph import release
from kge.services.onboard import routes, service, jobs
from tests.unit.test_faithful_rollback import client as client, HASH
from tests.unit.test_cli import runner as runner, shop as shop


@pytest.mark.parametrize("explicit", [False, True])
def test_eval_pre_selects_candidate_instead_of_current(runner, shop, explicit):
    commands, calls = runner
    old = shop.artifacts / shop.client / "old-current"
    old.mkdir(parents=True)
    (old.parent / "current").symlink_to(old.name)
    assert shop.model_hash() == "old-current"
    if explicit:
        commands.explicit["HASH"] = "different-candidate"
    commands.run("eval-pre")
    assert calls[-1][-2:] == ["--hash", "different-candidate" if explicit else "abc"]
    assert calls[-1][0] == "kge.eval.margin"
    assert [row for row in calls if row[0] == "kge.compiler.compile"] == (
        [] if explicit else [["kge.compiler.compile", "--check"]])
    assert Path(commands.get("MARGIN_STATUS")).read_text() == "0\n"


def test_eval_pre_preserves_gate_failure(runner, monkeypatch):
    commands, _ = runner
    monkeypatch.setattr(commands, "margin_run", lambda model_hash: (1, 0))
    with pytest.raises(subprocess.CalledProcessError) as exc:
        commands.run("eval-pre")
    assert exc.value.returncode == 1


def evidence():
    row = {"set": "golden", "id": "G1", "arm": "treatment", "principal": "analyst", "repeat": 1,
           "ok": True, "why": "", "deterministic": {"answer": {"figure": 5, "sql": "SELECT 5"},
           "citations": [{"doc": "D1"}], "retrieved": [{"doc": "D1", "chunk": "C1"}],
           "withheld": ["D2"], "rights": {"reads": ["retail"]}}}
    return {"records": [row], "must_pass": [stages.question_key(row)],
            "model_graded": {"scores": {stages.question_key(row): [1, 1, 1]}}}


def test_identical_evidence_passes_and_a_model_grade_fall_never_fails():
    pre = evidence()
    result = stages.compare(pre, copy.deepcopy(pre["records"]), {
        "scores": {stages.question_key(pre["records"][0]): [0, 0, 0]}})
    assert result["passed"]
    assert result["model_graded"]["difference"] == -1
    assert result["model_graded"]["interval_95"] == [-1, -1]


def retrieved_evidence():
    pre = evidence()
    pre["records"][0]["deterministic"]["retrieved"] = [
        {"doc": "D1", "chunk": "C1", "via": "vector", "tier": "gold", "ai_ready": True, "score": .8123},
        {"doc": "D2", "chunk": "C2", "via": "vector", "tier": "silver", "ai_ready": True, "score": .7123},
    ]
    return pre


def test_retrieval_score_drift_does_not_fail_or_mutate_saved_evidence():
    pre = retrieved_evidence()
    original = copy.deepcopy(pre)
    rows = copy.deepcopy(pre["records"])
    rows[0]["deterministic"]["retrieved"][0]["score"] += .0001
    assert stages.compare(pre, rows)["passed"]
    assert pre == original
    assert rows[0]["deterministic"]["retrieved"][0]["score"] != .8123


@pytest.mark.parametrize("field,value", [("doc", "D3"), ("chunk", "C3"), ("via", "graph"),
    ("tier", "bronze"), ("ai_ready", False), ("order", 2), ("future_field", "changed")])
def test_every_other_retrieved_field_still_fails_comparison(field, value):
    pre = retrieved_evidence()
    rows = copy.deepcopy(pre["records"])
    rows[0]["deterministic"]["retrieved"][0][field] = value
    assert not stages.compare(pre, rows)["passed"]


def test_retrieved_order_and_nonretrieval_score_remain_deterministic():
    pre = retrieved_evidence()
    rows = copy.deepcopy(pre["records"])
    rows[0]["deterministic"]["retrieved"].reverse()
    assert not stages.compare(pre, rows)["passed"]
    pre["records"][0]["deterministic"]["answer"]["score"] = 1
    rows = copy.deepcopy(pre["records"])
    rows[0]["deterministic"]["answer"]["score"] = 2
    assert not stages.compare(pre, rows)["passed"]


def test_new_request_turn_ids_do_not_change_evidence_but_plans_and_release_bindings_do():
    original = {'retrieved': [], 'answer': {'history': [{'turn_id': 'first-request', 'binding': {'release_identity': 'release'},
        'plan': {'measures': ['metric'], 'obligations': [{'spans': [{'turn': 'first-request', 'start': 0, 'end': 6}]}]}}]}}
    repeated = copy.deepcopy(original)
    repeated['answer']['history'][0]['turn_id'] = 'another-request'
    repeated['answer']['history'][0]['plan']['obligations'][0]['spans'][0]['turn'] = 'another-request'
    assert stages.deterministic(original) == stages.deterministic(repeated)
    for field, changed in [('binding', {'release_identity': 'another-release'}), ('plan', {'measures': ['another-metric']})]:
        modified = copy.deepcopy(repeated)
        modified['answer']['history'][0][field] = changed
        assert stages.deterministic(original) != stages.deterministic(modified)


@pytest.mark.parametrize("field", ["id", "kind", "label", "state", "detail", "future_field"])
def test_path_node_time_drift_passes_but_other_path_changes_fail(field):
    pre = evidence()
    pre["records"][0]["deterministic"]["answer"]["path"] = {
        "nodes": [{"id": "n", "kind": "query", "label": "Query", "state": "passed",
                   "detail": "SELECT 5", "time": 1}], "edges": [], "time": 3}
    original = copy.deepcopy(pre)
    rows = copy.deepcopy(pre["records"])
    path = rows[0]["deterministic"]["answer"]["path"]
    path["nodes"][0]["time"] = 2
    assert stages.compare(pre, rows)["passed"]
    assert pre == original
    assert path["nodes"][0]["time"] == 2
    path["nodes"][0][field] = "changed"
    assert not stages.compare(pre, rows)["passed"]
    for changed in ({"edges": [{"from": "n", "to": "m"}]}, {"time": 4}):
        rows = copy.deepcopy(pre["records"])
        rows[0]["deterministic"]["answer"]["path"].update(changed)
        assert not stages.compare(pre, rows)["passed"]


def test_recorded_retrieval_evidence_omits_only_score():
    arms = object.__new__(stages.EvidenceArms)
    arms.e = SimpleNamespace(g=SimpleNamespace(principal=lambda _: {"reads": ["retail"]}))
    retrieved = retrieved_evidence()["records"][0]["deterministic"]["retrieved"]
    ans = {"run_trace": {"retrieved": retrieved}, "score": 3}
    result = arms.evidence(ans, "analyst")
    assert result["retrieved"] == [{k: v for k, v in row.items() if k != "score"} for row in retrieved]
    assert result["answer"]["score"] == 3
    assert "score" in retrieved[0]


@pytest.mark.parametrize("field,value", [("answer", {"figure": 6}), ("answer", {"sql": "SELECT 6"}),
    ("citations", [{"doc": "D2"}]), ("withheld", []), ("rights", {"reads": ["wealth"]}),
    ("retrieved", [{"doc": "D2"}])])
def test_one_changed_deterministic_value_fails(field, value):
    pre = evidence()
    rows = copy.deepcopy(pre["records"])
    rows[0]["deterministic"][field] = value
    result = stages.compare(pre, rows)
    assert result["passed"] is False
    assert result["message"].endswith("run kge rollback")
    assert result["differences"][0]["question"][:2] == ["golden", "G1"]


def test_must_pass_question_is_checked_even_if_the_saved_verdict_was_corrupted():
    pre = evidence()
    pre["records"][0]["ok"] = False
    assert not stages.compare(pre, copy.deepcopy(pre["records"]))["passed"]


def test_missing_extra_and_duplicate_records_fail():
    pre = evidence()
    assert not stages.compare(pre, [])["passed"]
    assert not stages.compare(pre, pre["records"] * 2)["passed"]


def test_bootstrap_resamples_questions_with_repeats_kept_together():
    before = {"a": [1, 1, 1], "b": [.3, .4, .5], "c": [0, 0, 0]}
    after = {"a": [.5, .5, .5], "b": [.3, .4, .5], "c": [.5, .5, .5]}
    first = stages.bootstrap(before, after)
    assert first == stages.bootstrap(dict(reversed(list(before.items()))), after)
    assert first["difference"] == 0
    assert first["interval_95"] == [-.5, .5]
    assert first["questions"] == 3 and first["repeats"] == 3


@pytest.mark.parametrize("provider", ["none", "mock"])
def test_offline_model_grading_is_not_applicable(monkeypatch, provider):
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("ANSWER_MODEL_TIER", "default")
    monkeypatch.setattr("kge.adapters.model.providers.LLM.json", lambda *a: pytest.fail("offline must not call a model"))
    assert stages.grades([], {})["status"] == "not applicable"


@pytest.mark.parametrize("default", ["none", "mock"])
@pytest.mark.parametrize("tier", ["", "medium"])
def test_grading_uses_effective_answer_tier_without_calling_it(monkeypatch, default, tier):
    from types import SimpleNamespace
    from kge.adapters.model import providers

    monkeypatch.setenv("MODEL_PROVIDER", default)
    monkeypatch.setenv("MODEL_MEDIUM_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_MEDIUM_NAME", "answer-stub")
    monkeypatch.setenv("ANSWER_MODEL_TIER", tier)
    made = []

    def model(provider, name, *args, **kwargs):
        stub = SimpleNamespace(provider=provider, name=name, tier=kwargs["tier"])
        made.append(stub)
        return stub

    monkeypatch.setattr(providers, "LLM", model)
    resolved = providers.grading_model()
    assert resolved is not None
    assert resolved is made[0]
    assert (resolved.provider, resolved.name, resolved.tier) == ("openai", "answer-stub", "medium")


def test_both_release_and_rollback_call_the_hook_after_the_swap(client, monkeypatch):
    seen = []
    monkeypatch.setattr(release, "post_release", lambda prof: seen.append(prof.identity()))
    for n in (1, 2):
        client.stage(n)
        release.release(client.prof, HASH)
    release.release(client.prof, previous=True)
    assert len(seen) == 3
    assert seen[0] != seen[1] and seen[0] == seen[2]


def test_post_failure_keeps_the_new_release_and_records_the_diagnostic(client, monkeypatch):
    client.stage(1)
    released.write_evaluation(client.graph.parent, "pre", {})
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=3))
    with pytest.raises(release.PostReleaseFailed, match="run kge rollback$"):
        release.release(client.prof, HASH)
    assert client.prof.model_hash() == HASH
    assert released.evaluation(client.graph.parent, "post")["passed"] is False
    assert release.kept(client.prof)[0]["identity"] == client.prof.serve().identity()


def test_old_release_without_pre_is_a_reported_non_failure(client, capsys):
    client.stage(1)
    release.release(client.prof, HASH)
    result = released.evaluation(client.graph.parent, "post")
    assert result["passed"] is True
    assert result["compared"] is False
    assert result["status"] == "not compared"
    assert result["message"] == "eval post not compared: no pre-release results to compare"
    assert result["message"] in capsys.readouterr().out
    from kge.services.tools.service import versions
    assert versions(client.prof)["eval_post"]["status"] == "not compared"
    assert stages.evaluate(client.prof)["status"] == "not compared"


def test_evaluation_sidecar_uses_the_store_and_leaves_archive_unchanged(client, monkeypatch, tmp_path):
    from kge.adapters.objstore import open_store
    client.stage(1)
    release.release(client.prof, HASH)
    identity = client.prof.serve().identity()
    path = client.prof.store / client.prof.client / "releases" / HASH / (identity["release"] + ".tar.gz")
    before = path.read_bytes()
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    value = {"passed": False, "message": stages.RECOVERY, "identity": identity}
    open_store(str(tmp_path / "objects")).put(released.evaluation_key(client.prof, identity), json.dumps(value).encode())
    assert released.post_evaluation(client.prof) == value
    assert path.read_bytes() == before


def test_status_exposes_failure_to_a_release_reader(client, monkeypatch):
    client.stage(1)
    release.release(client.prof, HASH)
    released.write_evaluation(client.graph.parent, "post", {"passed": False, "message": stages.RECOVERY})
    monkeypatch.setattr(jobs, "shown_candidate", lambda *a: (None, None))
    monkeypatch.setattr(jobs, "access_reader", lambda *a: None)
    monkeypatch.setattr(service.core, "permissions", lambda *a, **k: {"certify": False})
    monkeypatch.setattr("kge.graph.history.list_versions", lambda *a: {})
    app = SimpleNamespace(engine=lambda: SimpleNamespace(profile=client.prof))
    result = service.page_status(app, client.prof, "analyst.acme@example.com", (False, "reader"), None, {})
    assert result["eval_post"]["passed"] is False
    assert routes.StatusResponse(**result).model_dump()["eval_post"]["message"].endswith("run kge rollback")
    from kge.services.tools.service import versions
    assert versions(client.prof)["eval_post"]["passed"] is False


@pytest.mark.parametrize("old_local_gate", [False, True])
def test_pre_report_is_readable_from_stored_work_without_matching_local_evidence(client, monkeypatch, tmp_path, old_local_gate):
    from kge.adapters.objstore import open_store
    from kge.pipeline.objstore import tarball
    import importlib
    monkeypatch.setattr(importlib.import_module("kge.pipeline.settings"), "ROOT", client.app)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    pre = {"passed": True, "manifest": {"run_id": "pre-candidate"}, "report": "Candidate margin passed",
           "model_graded": {"status": "not applicable"}, "must_pass": ["golden/G1"],
           "summary": {"sets": {"golden": {"checked": 1}}}}
    released.write_evaluation(client.graph.parent, "pre", pre)
    data = tarball(client.app, [client.graph.parent / released.PRE_EVAL])
    open_store(str(tmp_path / "objects")).put(f"work/{client.prof.client}.tar.gz", data)
    (client.graph.parent / released.PRE_EVAL).unlink()
    client.graph.parent.rmdir()
    if old_local_gate:
        client.graph.parent.mkdir()
        release.mark_gate(client.graph, {"passed": False, "run_id": "old-run", "model_hash": HASH})
        old_summary = client.prof.run_dir() / "runs" / "old-run" / "summary.json"
        old_summary.parent.mkdir(parents=True)
        old_summary.write_text(json.dumps({"sets": {"old": {}}}))
    report = service.margin_report(client.prof, HASH)
    assert report["result"] == "passed"
    assert report["run_id"] == "pre-candidate"
    assert report["eval_pre"]["report"] == "Candidate margin passed"
    assert report["sets"][0]["name"] == "golden"


def test_reloading_invalidates_candidate_pre_but_keeps_the_release_result(client):
    released.write_evaluation(client.graph.parent, "pre", {"passed": True})
    released.write_evaluation(client.graph.parent, "post", {"passed": True})
    release.unmark(client.graph)
    assert released.evaluation(client.graph.parent, "pre") is None
    assert released.evaluation(client.graph.parent, "post") == {"passed": True}


def test_no_route_mcp_or_job_command_can_trigger_post():
    from kge.cli import COMMANDS
    from kge.pipeline.objstore import JOBS
    assert "eval-pre" in COMMANDS and "eval-pre" in JOBS
    assert not {"post", "eval-post"} & set(COMMANDS + JOBS)
    root = Path(__file__).resolve().parents[2]
    for file in (root / "kge/services").rglob("*.py"):
        if file.name in {"routes.py", "mcp.py"}:
            assert "eval.stages" not in file.read_text()
            assert "post_release(" not in file.read_text()
    page = (root / "portal/ui/src/pages/Context/Onboard.js").read_text()
    assert 'id="ob-eval-post"' in page
    assert "{ob.eval_post?.message}" in page


def test_serialized_pre_compares_equal_to_live_tuple_values():
    pre = evidence()
    pre["records"][0]["deterministic"]["trace"] = {"pairs": [["a", "b"]]}
    live = copy.deepcopy(pre["records"])
    live[0]["deterministic"]["trace"]["pairs"] = [("a", "b")]
    assert stages.compare(pre, live)["passed"]


def test_local_page_switches_on_post_failure_but_keeps_the_job_failed(client, monkeypatch, tmp_path):
    import sys
    switched = []
    saved = []
    monkeypatch.setattr(jobs, "job_dir", lambda app: tmp_path)
    monkeypatch.setattr(jobs, "save", lambda app, job: saved.append(dict(job)))
    app = SimpleNamespace(PROFILE=client.prof, switch=lambda: switched.append(True))
    job = {"id": "evaluation", "step": "release"}
    jobs.run(app, job, [sys.executable, "-c", "print('run kge rollback'); raise SystemExit(3)"])
    assert switched == [True]
    assert saved[-1]["state"] == "failed"
    assert saved[-1]["message"].endswith("run kge rollback")


@pytest.mark.parametrize('changed', ['release', 'hash', 'graph', 'chunks', 'warehouses', 'forged_binding', 'plan'])
def test_post_release_binding_allows_only_the_nonce_of_the_same_candidate_data(changed):
    import hashlib
    before = {'hash': 'artifact', 'graph': 'generation', 'chunks': 'table', 'warehouses': {'u': 1}, 'release': None}
    after = {**before, 'release': 'released'}
    if changed in ('hash', 'graph', 'chunks'):
        after[changed] = 'different'
    elif changed == 'warehouses':
        after['warehouses'] = {'u': 2}
    def bound(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    pre = evidence()
    pre['manifest'] = {'identity': before}
    pre['records'][0]['deterministic']['answer']['history'] = [
        {'turn_id': 'pre', 'binding': {'release_identity': bound(before), 'access_fingerprint': 'scope'}, 'plan': {'measure': 'metric'}}]
    records = copy.deepcopy(pre['records'])
    entry = records[0]['deterministic']['answer']['history'][0]
    entry['turn_id'] = 'post'
    entry['binding']['release_identity'] = 'forged' if changed == 'forged_binding' else bound(after)
    if changed == 'plan':
        entry['plan'] = {'measure': 'different'}
    original = copy.deepcopy(records)
    assert stages.compare(pre, records, identity=after)['passed'] is (changed == 'release')
    assert records == original
