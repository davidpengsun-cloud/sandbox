"""Request deadlines, without a network, credentials or built graph."""
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from kge.adapters.model import LLM, ModelSettingError, check_settings
from kge.adapters.model.budget import MIN_CALL_SECONDS, request_budget, request_remaining, request_timeout
from kge.adapters.model.errors import CallDeadline, ExternalCallError, RequestDeadlineExceeded, no_fallback, post_json
from kge.adapters.model.providers import result


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "many", "300", "360"])
def test_invalid_request_budget_refused_at_startup(monkeypatch, raw):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", raw)
    with pytest.raises(ModelSettingError, match="MODEL_REQUEST_TIMEOUT"):
        check_settings()


@pytest.mark.parametrize("raw", [None, "", "  ", "12.5"])
def test_request_budget_default_and_override(monkeypatch, raw):
    monkeypatch.delenv("MODEL_REQUEST_TIMEOUT", raising=False)
    if raw is not None:
        monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", raw)
    assert request_timeout() == (12.5 if raw == "12.5" else 270)


def test_nested_requests_count_non_model_work_and_restore_context(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("kge.adapters.model.budget.time.monotonic", lambda: clock[0])
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "10")

    @request_budget
    def nested():
        assert request_remaining() == 7
        raise ValueError("test failure")

    @request_budget
    def outer():
        clock[0] += 3
        nested()

    with pytest.raises(ValueError, match="test failure"):
        outer()
    assert request_remaining() is None


@pytest.mark.parametrize("transport", ["rest", "vertex"])
@pytest.mark.parametrize("request_seconds,elapsed,expected", [(10, 2, [4, 4]), (5, 2, [4, 3]),
                                                             (5, 4.98, [4]), (5, 5, [4])])
def test_attempt_and_retry_take_request_time_left(monkeypatch, transport, request_seconds, elapsed, expected):
    clock = [1000.0]
    calls = []
    monkeypatch.setenv("MODEL_TIMEOUT", "4")
    monkeypatch.setenv("MODEL_TOTAL_TIMEOUT", "9")
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(request_seconds))
    monkeypatch.setattr("kge.adapters.model.budget.time.monotonic", lambda: clock[0])

    def send(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            clock[0] += elapsed
            raise TimeoutError("slow model")
        return {}

    if transport == "rest":
        def rest(*args):
            try:
                return send(args[3])
            except TimeoutError:
                raise ExternalCallError("slow model", transient=True) from None
        monkeypatch.setattr("kge.adapters.model.errors._post_json", rest)

        def invoke():
            return post_json("https://models.example.com", {}, {})
    else:
        model = LLM("vertex-claude", "stand-in")
        def create(**kwargs):
            send(kwargs["timeout"])
            return SimpleNamespace(model_dump=lambda: {})
        monkeypatch.setattr(type(model.chat), "anthropic_vertex", lambda self: SimpleNamespace(messages=SimpleNamespace(create=create)))

        def invoke():
            return model.chat.vertex_reply("probe", 10, {})
    if len(expected) == 1:
        with pytest.raises(RequestDeadlineExceeded):
            request_budget(invoke)()
    else:
        request_budget(invoke)()
    assert calls == expected
    assert request_remaining() is None


def test_floor_does_not_start_a_model_call(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(MIN_CALL_SECONDS / 2))
    model = LLM("mock", "mock")
    monkeypatch.setattr(type(model.chat), "_generate", lambda *a, **kw: pytest.fail("model started below floor"))
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(lambda: model.complete("probe"))()


def test_concurrent_requests_have_independent_clocks(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "1")
    barrier = Barrier(2)

    @request_budget
    def invoke(delay):
        time.sleep(delay)
        barrier.wait(timeout=2)
        return request_remaining()

    with ThreadPoolExecutor(2) as pool:
        older = pool.submit(invoke, .15)
        time.sleep(.1)
        newer = pool.submit(invoke, 0)
        assert newer.result() - older.result() > .06
    assert request_remaining() is None


def test_sleeping_model_is_bounded_and_its_late_reply_cannot_start_another_call(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".15")
    model = LLM("mock", "mock")
    finished = Event()
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        time.sleep(.4)
        finished.set()
        return result("late", "mock", 1, 1)

    monkeypatch.setattr(type(model.chat), "_generate", generate)

    @request_budget
    def ask():
        model.complete("first")
        model.complete("must not start")

    started = time.monotonic()
    with pytest.raises(RequestDeadlineExceeded):
        ask()
    assert time.monotonic() - started < .3
    assert finished.wait(1)
    assert calls == [1]
    assert request_remaining() is None


@pytest.mark.parametrize("cloud", [False, True])
def test_request_exhaustion_never_falls_back(monkeypatch, cloud):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery" if cloud else "duckdb")
    error = RequestDeadlineExceeded()
    with pytest.raises(RequestDeadlineExceeded) as caught:
        no_fallback("phrasing the answer", error)
    assert caught.value is error


def test_call_total_still_wins_when_shorter(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "10")
    monkeypatch.setenv("MODEL_TOTAL_TIMEOUT", "2")
    monkeypatch.setenv("MODEL_TIMEOUT", "4")
    assert 1.9 < request_budget(lambda: CallDeadline().remaining_timeout())() <= 2


@pytest.mark.parametrize("transport", ["rest", "vertex"])
def test_one_slow_call_keeps_its_attempt_and_retry(monkeypatch, transport):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "2")
    monkeypatch.setenv("MODEL_TOTAL_TIMEOUT", "1")
    monkeypatch.setenv("MODEL_TIMEOUT", ".1")
    calls = []

    def send(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            time.sleep(timeout)
            raise TimeoutError("slow model")

    if transport == "rest":
        model = LLM("openai", "stand-in")
        def rest(*args):
            try:
                send(args[3])
            except TimeoutError:
                raise ExternalCallError("slow model", transient=True) from None
            return {"choices": [{"message": {"content": "OK"}}]}
        monkeypatch.setattr("kge.adapters.model.errors._post_json", rest)
    else:
        model = LLM("vertex-claude", "stand-in")
        def create(**kwargs):
            send(kwargs["timeout"])
            return SimpleNamespace(model_dump=lambda: {"content": [{"type": "text", "text": "OK"}]})
        monkeypatch.setattr(type(model.chat), "anthropic_vertex", lambda self: SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert request_budget(lambda: model.complete("probe"))() == "OK"
    assert calls == [.1, .1]


def test_service_cannot_return_a_figure_after_non_model_work_exhausts_budget(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("kge.adapters.model.budget.time.monotonic", lambda: clock[0])
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "1")

    @request_budget
    def ask():
        clock[0] += 2
        return {"figure": 42}

    with pytest.raises(RequestDeadlineExceeded):
        ask()
    assert request_remaining() is None
