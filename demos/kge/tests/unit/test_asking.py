"""The runner every job that asks a set of questions goes through (kge/eval/asking.py each): EVAL_CONCURRENCY at a
time on a bounded pool, the results in the items' order, each call in a context of its own, and a failure that may pass
if made again (kge/adapters/model/errors.py transient) asked again after a back-off, never returned.

    python -m pytest tests/unit/test_asking.py -q
"""
from __future__ import annotations

import threading
import time
import urllib.error

import pytest

from kge.adapters.model.errors import ExternalCallError, NoFallback, transient
from kge.eval import asking
from kge.eval.asking import Unanswered, each
from kge.pipeline.settings import eval_concurrency
from kge.pipeline.trace import transient_failure
from kge.services.ask.engine import runtrace


@pytest.fixture(autouse=True)
def no_back_off(monkeypatch):
    """No test waits out a back-off; each one asked for is counted."""
    waited: list[float] = []
    monkeypatch.setattr(asking, "pause", waited.append)
    monkeypatch.delenv("EVAL_CONCURRENCY", raising=False)
    return waited


def busy() -> RuntimeError:
    """BigQuery's own rate limit, as kge/adapters/bigquery.py raises it: HTTP 403, reason rateLimitExceeded."""
    return RuntimeError("BigQuery error (HTTP 403): Exceeded rate limits: too many api requests per user per method for this "
                        "user_method. For more information, see https://cloud.google.com/bigquery/docs/troubleshoot-quotas")


def test_eight_unset_one_serial_and_a_mistype_refused(monkeypatch):
    assert eval_concurrency() == 8
    monkeypatch.setenv("EVAL_CONCURRENCY", "1")
    assert eval_concurrency() == 1
    for bad in ("0", "-2", "eight", "2.5"):
        monkeypatch.setenv("EVAL_CONCURRENCY", bad)
        with pytest.raises(ValueError, match="EVAL_CONCURRENCY"):
            eval_concurrency()
        with pytest.raises(ValueError, match="EVAL_CONCURRENCY"):
            each(str, [1, 2])


def test_results_come_back_in_the_items_order_whatever_order_they_finish_in():
    def slow_first(n: int) -> int:
        time.sleep(0.02 * (10 - n))
        return n * n
    assert each(slow_first, range(10)) == [n * n for n in range(10)]


def test_the_pool_is_bounded_and_really_runs_side_by_side(monkeypatch):
    """A stand-in model that waits 0.1 s, as a question waits on BigQuery and Vertex AI: 24 of them take 2.4 s one at a
    time and about 0.3 s eight at a time, and never more than eight run at once."""
    lock, running, peak = threading.Lock(), [0], [0]

    def model(n: int) -> int:
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.1)
        with lock:
            running[0] -= 1
        return n

    monkeypatch.setenv("EVAL_CONCURRENCY", "1")
    started = time.perf_counter()
    assert each(model, range(24)) == list(range(24))
    serial = time.perf_counter() - started
    assert peak[0] == 1
    monkeypatch.setenv("EVAL_CONCURRENCY", "8")
    started = time.perf_counter()
    assert each(model, range(24)) == list(range(24))
    pooled = time.perf_counter() - started
    assert peak[0] == 8
    assert serial >= 2.4 and pooled < serial / 3, (serial, pooled)


def test_one_at_a_time_runs_in_the_callers_thread_as_before(monkeypatch):
    monkeypatch.setenv("EVAL_CONCURRENCY", "1")
    assert set(each(lambda _: threading.get_ident(), range(5))) == {threading.get_ident()}
    monkeypatch.setenv("EVAL_CONCURRENCY", "4")
    assert threading.get_ident() not in set(each(lambda _: threading.get_ident(), range(5)))


def test_each_questions_run_trace_is_whole_and_its_own():
    """Eight questions in flight at once, each recording its stages between barriers: every trace holds its own
    question's marks and no other's."""
    barrier = threading.Barrier(8)

    def ask(n: int) -> dict:
        def answer() -> dict:
            for stage in ("understand", "plan", "execute"):
                runtrace.stage(stage)
                runtrace.retrieved([{"doc": f"Q{n}", "chunk": f"Q{n}-{stage}", "score": n}], "graph")
                barrier.wait(timeout=5)
            return {"question": f"Q{n}"}
        return runtrace.run("answer", answer)

    out = each(ask, range(8))
    for n, ans in enumerate(out):
        rt = ans["run_trace"]
        assert ans["question"] == f"Q{n}"
        assert [r["chunk"] for r in rt["retrieved"]] == [f"Q{n}-understand", f"Q{n}-plan", f"Q{n}-execute"]
        assert [s["stage"] for s in rt["stages"]] == ["understand", "plan", "execute"]
    assert len({a["run_trace"]["run_id"] for a in out}) == 8
    assert runtrace.current() is None


@pytest.mark.parametrize("failure", [
    busy(),
    RuntimeError("BigQuery error (HTTP 503): backendError"),
    ExternalCallError("Vertex AI error (HTTP 429): Resource exhausted", transient=True),
    TimeoutError("BigQuery job j1 did not finish within 60 seconds"),
    urllib.error.URLError("connection reset"),
])
def test_a_transient_failure_is_asked_again_after_a_back_off_and_the_question_still_scores(no_back_off, failure):
    tries: dict[int, int] = {}

    def ask(n: int) -> str:
        tries[n] = tries.get(n, 0) + 1
        if n == 3 and tries[n] <= 2:
            raise failure
        return f"answer {n}"

    assert each(ask, range(6)) == [f"answer {n}" for n in range(6)]
    assert tries == {0: 1, 1: 1, 2: 1, 3: 3, 4: 1, 5: 1}
    assert len(no_back_off) == 2 and no_back_off[1] > no_back_off[0] >= asking.BACKOFF_SECONDS


def test_a_failure_the_answer_stepped_around_is_asked_again_never_scored():
    """The engine answers a warehouse query BigQuery turned away as a warehouse problem: recorded (transient_failure),
    the answer is not the one returned, and the question is asked again."""
    tries = [0]

    def ask(_: int) -> str:
        tries[0] += 1
        if tries[0] == 1:
            transient_failure(busy())
            return "a warehouse problem"
        transient_failure(ValueError("not transient: never recorded"))
        return "the figure"

    assert each(ask, [1]) == ["the figure"] and tries[0] == 2


def test_no_fallback_from_a_rate_limit_is_asked_again():
    """On the pilot's cloud path a model call that fails stops the step with NoFallback, raised from the call's own
    failure: a rate limit under it is still a rate limit."""
    tries = [0]

    def ask(_: int) -> str:
        tries[0] += 1
        if tries[0] == 1:
            try:
                raise ExternalCallError("Vertex AI error (HTTP 429)", transient=True)
            except ExternalCallError as exc:
                raise NoFallback("the agent's plan step: the model call failed") from exc
        return "answered"

    assert not transient(NoFallback("x", transient=True))
    assert each(ask, [1]) == ["answered"] and tries[0] == 2


def test_any_other_failure_is_not_asked_again(no_back_off):
    tries = [0]

    def ask(_: int) -> str:
        tries[0] += 1
        raise RuntimeError("BigQuery error (HTTP 400): Syntax error")

    with pytest.raises(RuntimeError, match="HTTP 400"):
        each(ask, [1])
    assert tries[0] == 1 and no_back_off == []


def test_a_question_that_meets_it_every_time_stops_the_job_and_is_never_counted_wrong(no_back_off):
    def ask(n: int) -> str:
        if n == 2:
            raise busy()
        return "ok"

    with pytest.raises(Unanswered, match=r"question 2: .* each of 5 tries .*HTTP 403"):
        each(ask, range(4), lambda n: f"question {n}")
    assert len(no_back_off) == asking.RETRIES


def test_transient_reads_bigquery_s_own_error_line():
    assert transient(busy())
    assert transient(RuntimeError("BigQuery error (HTTP 403): Exceeded rate limits: too many concurrent queries for this project_and_region"))
    assert transient(RuntimeError("BigQuery error (HTTP 429): Too many requests")) and transient(RuntimeError("BigQuery load job error (HTTP 500): x"))
    assert not transient(RuntimeError("BigQuery error (HTTP 404): Not found: Table t"))
    assert not transient(RuntimeError("planner fell over (HTTP 429)"))
    assert transient(urllib.error.HTTPError("u", 429, "Too Many Requests", None, None))
    assert not transient(urllib.error.HTTPError("u", 403, "Forbidden", None, None))
    assert not transient(ValueError("EVAL_CONCURRENCY must be a positive whole number"))


def test_make_eval_asks_the_api_no_more_at_once_than_it_lets_one_person():
    from kge.eval.run_golden import API_IN_FLIGHT
    from kge.services.http import MAX_IN_FLIGHT
    assert API_IN_FLIGHT == MAX_IN_FLIGHT


def test_a_spent_quota_is_not_asked_again(no_back_off):
    """BigQuery's quotaExceeded (HTTP 403, "Quota exceeded") lasts until the quota resets: it fails the question at once."""
    spent = RuntimeError("BigQuery error (HTTP 403): Quota exceeded: Your project exceeded quota for free query bytes scanned")
    assert not transient(spent)
    tries = [0]

    def ask(_: int) -> str:
        tries[0] += 1
        raise spent

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        each(ask, [1])
    assert tries[0] == 1 and no_back_off == []


def test_test_drive_says_it_could_not_check_rather_than_name_a_fault():
    """A step whose margin check stopped on a question turned away on every try is reported as not checked, never as a
    fault in the configuration to fix."""
    from kge.services.onboard.test_drive_run import timed

    def onboard() -> str:
        raise RuntimeError("exit 2: margin: cannot check: Unanswered: G001 on control, repeat 1: a failure that may pass "
                           "if asked again, on each of 5 tries (last: RuntimeError: BigQuery error (HTTP 403))")

    got = timed("onboard", onboard)
    assert got.detail.startswith("could not check: G001 on control") and "run the test drive again later" in got.what_to_do
    assert "fix its cause" not in got.what_to_do
