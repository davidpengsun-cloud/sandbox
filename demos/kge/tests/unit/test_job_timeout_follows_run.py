"""A background warehouse job's time limit follows its question's remaining time, and the run reply names what ended it
(#629), on stubbed BigQuery clients."""
import time
from types import SimpleNamespace

import pytest

from kge.adapters import bigquery
from kge.adapters.bigquery import BigQueryLoader
from kge.adapters.warehouse import jobs, localjobs
from kge.pipeline import questionrun
from kge.services.ask import service
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.models import Ask, QuestionRunResponse

WAREHOUSE_LIMIT = "The warehouse exceeded its time limit; ask a narrower question."
WAREHOUSE_FAILED = "The warehouse could not finish this question within its limits."


def loader(monkeypatch, status=None):
    stub = BigQueryLoader(project="billing-project")
    stub.sent, stub.cancelled = [], []
    monkeypatch.setattr(stub, "token", lambda: ("offline", "billing-project"))
    monkeypatch.setattr(stub, "get", lambda url, *args, **kwargs: status if "/jobs/" in url else {"location": "EU"})
    monkeypatch.setattr(stub, "post", lambda url, headers, body: stub.sent.append(body) or {"jobReference": body["jobReference"]})
    monkeypatch.setattr(stub, "cancel_job", lambda ref, project, token: stub.cancelled.append(ref["jobId"]))
    return stub


def job_seconds(monkeypatch, left, setting=None):
    if setting is None:
        monkeypatch.delenv("BIGQUERY_JOB_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("BIGQUERY_JOB_TIMEOUT", setting)
    stub = loader(monkeypatch)
    with questionrun.checkpoints({"expires_at": time.time() + left}, lambda: None):
        handle = jobs.submit(stub, "SELECT 1", "data", "ask_job")
    return int(stub.sent[0]["configuration"]["jobTimeoutMs"]) / 1000, handle


def test_unset_the_job_runs_for_the_time_its_run_has_left(monkeypatch):
    seconds, handle = job_seconds(monkeypatch, 850)
    assert 849 <= seconds <= 850
    assert handle["limit"] == questionrun.RUN_TIME_LIMIT


def test_unset_a_full_run_gets_the_whole_question_run_timeout_not_ten_minutes(monkeypatch):
    monkeypatch.delenv("QUESTION_RUN_TIMEOUT", raising=False)
    seconds, _ = job_seconds(monkeypatch, questionrun.timeout())
    assert 899 <= seconds <= 900


def test_set_lower_it_caps_the_job(monkeypatch):
    seconds, handle = job_seconds(monkeypatch, 850, "120")
    assert seconds == 120
    assert handle["limit"] == questionrun.WAREHOUSE_TIME_LIMIT


def test_set_higher_the_runs_remaining_time_still_caps_it(monkeypatch):
    seconds, handle = job_seconds(monkeypatch, 850, "1100")
    assert 849 <= seconds <= 850
    assert handle["limit"] == questionrun.RUN_TIME_LIMIT


def test_an_ended_run_submits_no_job(monkeypatch):
    monkeypatch.delenv("BIGQUERY_JOB_TIMEOUT", raising=False)
    stub = loader(monkeypatch)
    with questionrun.checkpoints({"expires_at": time.time() - 1}, lambda: None):
        with pytest.raises(Exception, match="time limit"):
            jobs.submit(stub, "SELECT 1", "data", "ask_late")
    assert not stub.sent


class Capped(Exception):
    pass


@pytest.mark.parametrize("setting,expected", [(None, 300.0), ("45", 45.0)])
def test_direct_queries_keep_their_own_timeout_even_inside_a_run(monkeypatch, setting, expected):
    if setting is None:
        monkeypatch.delenv("BIGQUERY_JOB_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("BIGQUERY_JOB_TIMEOUT", setting)
    seen = []

    def cap(seconds):
        seen.append(seconds)
        raise Capped()
    monkeypatch.setattr(bigquery, "request_cap", cap)
    stub = loader(monkeypatch)
    with questionrun.checkpoints({"expires_at": time.time() + 850}, lambda: None):
        with pytest.raises(Capped):
            stub.run("SELECT 1")
    assert seen == [expected]
    assert not stub.sent


def poll(monkeypatch, status, limit):
    stub = loader(monkeypatch, status)
    handle = {"reference": {"projectId": "billing-project", "jobId": "ask_job", "location": "EU"},
              "started": time.time() - 10, "expires": time.time() - 1}
    if limit is not None:
        handle["limit"] = limit
    return jobs.poll(stub, handle), stub


@pytest.mark.parametrize("limit,reason", [(questionrun.WAREHOUSE_TIME_LIMIT, "warehouse_time_limit"),
                                          (questionrun.RUN_TIME_LIMIT, "run_time_limit"),
                                          (None, "warehouse_time_limit")])
def test_a_job_past_its_limit_names_whose_limit_it_was(monkeypatch, limit, reason):
    result, stub = poll(monkeypatch, {"status": {"state": "RUNNING"}, "statistics": {}}, limit)
    assert (result["state"], result["error"], result["reason"]) == ("failed", WAREHOUSE_LIMIT, reason)
    assert stub.cancelled == ["ask_job"]


def test_a_failed_job_is_a_warehouse_failure(monkeypatch):
    status = {"status": {"state": "DONE", "errorResult": {"reason": "bytesBilledLimitExceeded", "message": "cap"}},
              "statistics": {"endTime": str(int(time.time() * 1000) - 5000)}}
    stub = loader(monkeypatch, status)
    handle = {"reference": {"projectId": "billing-project", "jobId": "ask_job", "location": "EU"},
              "started": time.time() - 10, "expires": time.time() + 100, "limit": questionrun.RUN_TIME_LIMIT}
    result = jobs.poll(stub, handle)
    assert (result["state"], result["error"], result["reason"]) == ("failed", WAREHOUSE_FAILED, "warehouse_failure")


def test_local_jobs_name_their_endings(monkeypatch):
    with questionrun.checkpoints({"expires_at": time.time() + 30}, lambda: None):
        broken = localjobs.submit("local-broken", lambda register: 1 / 0)
    for _ in range(200):
        if (result := localjobs.poll(broken))["state"] != "running":
            break
        time.sleep(.01)
    assert result["reason"] == "warehouse_failure"
    assert localjobs.poll({"id": "gone", "expires": time.time() - 1})["reason"] == "run_time_limit"
    assert localjobs.poll({"id": "gone", "expires": time.time() + 30})["reason"] == "warehouse_failure"


class Ending:
    def __init__(self, result):
        self.result = result

    def submit(self, *args, **kwargs):
        return {"id": kwargs["job_id"], "guard": {"passed": True}, "meta": {}, "job": {}}

    def poll(self, handle):
        return self.result

    def cancel(self, handle):
        pass

    def release(self, handle):
        pass


def ended(monkeypatch, tmp_path, result):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("QUESTION_STATE_BACKEND", "store")
    w = Ending(result)
    c = SimpleNamespace(e=SimpleNamespace(w=w, identity="release-1"), g=SimpleNamespace(readable_units=lambda who: {"u"}))

    def compute(c, profile, question, who, purpose, arm, cfg, **kwargs):
        questionrun.execute(w, {"sql": "SELECT 1", "unit": "u", "units": ["u"]}, "reference", ())
        pytest.fail("an ended run must not answer")
    monkeypatch.setattr(service, "ask", compute)
    profile = SimpleNamespace(client="sample")
    run = service.start_run(c, profile, Ask(question="Metric"), "analyst", PRESETS["graph"])
    if run["state"] == "running":
        run = service.get_run(c, profile, run["id"], "analyst", clock=lambda: time.time() + 3)
    QuestionRunResponse.model_validate(run)
    return run


@pytest.mark.parametrize("result,state,error,reason", [
    ({"state": "failed", "error": WAREHOUSE_LIMIT, "reason": "warehouse_time_limit"},
     "failed", WAREHOUSE_LIMIT, "warehouse_time_limit"),
    ({"state": "failed", "error": WAREHOUSE_LIMIT, "reason": "run_time_limit"},
     "expired", questionrun.TIME_LIMIT, "run_time_limit"),
    ({"state": "failed", "error": WAREHOUSE_FAILED, "reason": "warehouse_failure"},
     "failed", WAREHOUSE_FAILED, "warehouse_failure"),
    ({"state": "failed", "error": WAREHOUSE_FAILED}, "failed", WAREHOUSE_FAILED, "warehouse_failure"),
])
def test_each_ending_returns_its_reason_beside_its_plain_message(monkeypatch, tmp_path, result, state, error, reason):
    run = ended(monkeypatch, tmp_path, result)
    assert (run["state"], run["error"], run["reason"]) == (state, error, reason)
    assert service.run_events(run)[-1] == {"event": "error", "error": error, "reason": reason}


def test_the_runs_own_expiry_says_run_time_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTION_RUN_TIMEOUT", "5")
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("QUESTION_STATE_BACKEND", "store")
    w = Ending({"state": "running"})
    c = SimpleNamespace(e=SimpleNamespace(w=w, identity="release-1"), g=SimpleNamespace(readable_units=lambda who: {"u"}))
    monkeypatch.setattr(service, "ask", lambda *a, **k: questionrun.execute(w, {"sql": "SELECT 1", "unit": "u", "units": ["u"]},
                                                                             "reference", ()))
    profile = SimpleNamespace(client="sample")
    now = [time.time()]
    run = service.start_run(c, profile, Ask(question="Metric"), "analyst", PRESETS["graph"], clock=lambda: now[0])
    now[0] += 6
    expired = service.get_run(c, profile, run["id"], "analyst", clock=lambda: now[0])
    assert (expired["state"], expired["error"], expired["reason"]) == ("expired", questionrun.TIME_LIMIT, "run_time_limit")


def test_a_run_still_running_carries_no_reason(monkeypatch, tmp_path):
    run = ended(monkeypatch, tmp_path, {"state": "running"})
    assert "reason" not in run
    assert QuestionRunResponse.model_validate({**run, "state": "failed", "error": "x"}).reason is None


def test_the_old_reason_is_not_kept_once_access_or_the_release_changes_and_the_run_says_access_changed(monkeypatch, tmp_path):
    run = ended(monkeypatch, tmp_path, {"state": "failed", "error": WAREHOUSE_FAILED, "reason": "warehouse_failure"})
    assert run["reason"] == "warehouse_failure"
    c = SimpleNamespace(e=SimpleNamespace(w=None, identity="release-2"), g=SimpleNamespace(readable_units=lambda who: {"u"}))
    changed = service.get_run(c, SimpleNamespace(client="sample"), run["id"], "analyst")
    assert (changed["state"], changed["reason"]) == ("failed", "access_changed")
    assert changed["error"] != WAREHOUSE_FAILED
    QuestionRunResponse.model_validate(changed)


def test_a_late_answer_read_as_expired_says_run_time_limit():
    record = {"state": "done", "expires_at": 100.0, "completed_at": 101.0, "events": [], "answer": {}}
    read = questionrun.read_record(record)
    assert (read["state"], read["error"], read["reason"]) == ("expired", questionrun.TIME_LIMIT, "run_time_limit")


def test_a_failure_after_the_poll_does_not_keep_the_polls_reason(monkeypatch, tmp_path):
    from kge.adapters.questionstate import for_profile
    from kge.adapters.deadline import ExternalCallError
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("QUESTION_STATE_BACKEND", "store")
    state = for_profile(SimpleNamespace(client="sample"))
    original, calls = state.put, []

    def put(run_id, record, revision):
        calls.append(record.get("reason"))
        if record.get("reason") and len(calls) > 1 and record.get("lease") == 0:
            raise ExternalCallError("The run could not be saved.")
        return original(run_id, record, revision)
    monkeypatch.setattr(state, "put", put)
    w = Ending({"state": "failed", "error": WAREHOUSE_LIMIT, "reason": "warehouse_time_limit"})
    c = SimpleNamespace(e=SimpleNamespace(w=w, identity="release-1"), g=SimpleNamespace(readable_units=lambda who: {"u"}))
    monkeypatch.setattr(service, "ask", lambda *a, **k: questionrun.execute(w, {"sql": "SELECT 1", "unit": "u", "units": ["u"]},
                                                                             "reference", ()))
    run = service.start_run(c, SimpleNamespace(client="sample"), Ask(question="Metric"), "analyst", PRESETS["graph"],
                            state=state, wait=1)
    assert run["state"] == "failed" and run["error"] == "The run could not be saved." and "reason" not in run
