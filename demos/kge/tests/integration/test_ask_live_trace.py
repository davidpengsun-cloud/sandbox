"""The Graph and Trace tabs follow the pipeline live, each event once (#442), on Meridian's demo with no model.

For a fixed set of questions (a figure, a by-month breakdown over two statements, a decline, a definition), on a direct
stream and on a background run (the page's path), started with and without the start wait and followed by its live
polls: every node, edge, plan and context is sent once (a later change is its own event); the streamed graph is the
final graph_path; the streamed stages are the final run_trace.stages, in order, with their totals; the read-but-not-used
count never runs above the final one. A held warehouse job shows the run, the stages and the running Runtime arriving
before the job ends, the start reply's run line first.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import json
import threading
import time

import anyio
import pytest

from tests.integration.test_answer_text import client as client
from tests.unit.test_ask_stream_events import assembled

WHO = "analyst_enterprise.meridian@example.com"
QUESTIONS = {
    "figure": ("Total retail revenue for June 2025", "figure"),
    "by_month": ("certified NNA for Q1 2025 by month", "figure"),
    "decline": ("Join retail customers to corporate contacts on customer_id and count the matches", "refuse"),
    "definition": ("What is the definition of an active customer?", "cite_certified"),
}
NDJSON = {"accept": "application/x-ndjson"}


def lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def background(client, question, headers):
    """The page's path: the start reply, then live polls from its cursor until the answer."""
    events = lines(client.post("/ask?background=true", json={"question": question}, headers={**headers, **NDJSON}).text)
    run = events[0]
    assert run["event"] == "run" and run["cursor"] == 0
    cursor = max([e["cursor"] for e in events if "cursor" in e], default=0)
    for _ in range(400):
        if any(e["event"] in ("answer", "error") for e in events):
            break
        time.sleep(0.02)
        polled = lines(client.get(f"/ask/runs/{run['id']}?after={cursor}", headers={**headers, **NDJSON}).text)
        assert polled[0]["event"] == "run"
        events += polled[1:]
        cursor = max([e["cursor"] for e in polled if "cursor" in e], default=cursor)
    cursors = [e["cursor"] for e in events[1:] if "cursor" in e]
    assert cursors == list(range(1, len(cursors) + 1))
    return events


def checked(events, behaviour):
    """The once, graph, summary and stage contract of one answer's events; its answer."""
    kinds = [e["event"] for e in events]
    assert kinds.count("answer") == 1 and kinds[-1] == "answer"
    answer = events[-1]["answer"]
    assert answer["behaviour"] == behaviour, answer.get("reason")
    nodes = [e["node"]["id"] for e in events if e["event"] == "node_added"]
    edges = [(e["edge"]["from"], e["edge"]["to"], e["edge"]["type"], e["edge"]["label"]) for e in events if e["event"] == "edge_added"]
    assert len(nodes) == len(set(nodes)) and len(edges) == len(set(edges))
    assert kinds.count("plan") <= 1 and kinds.count("context") <= 1
    graph = answer["graph_path"]
    assert assembled(events) == {key: graph[key] for key in ("nodes", "edges")}
    assert {e["id"] for e in events if e["event"] == "state_changed"} <= set(nodes)
    summaries = [e["unused"] for e in events if e["event"] == "graph_summary"]
    assert summaries and summaries[-1] == graph["unused"] and max(summaries) <= graph["unused"]
    stages = [e for e in events if e["event"] == "stage"]
    order = list(dict.fromkeys(e["stage"] for e in stages))
    final = answer["run_trace"]["stages"]
    assert order == [s["stage"] for s in final]
    for stage in final:
        sent = [e for e in stages if e["stage"] == stage["stage"]]
        assert sent[0]["state"] == "running" and sent[-1]["state"] == "done"
        assert {k: sent[-1][k] for k in ("stage", "ms", "llm_calls", "tokens_in", "tokens_out")} == stage
    return answer


@pytest.mark.parametrize("name", list(QUESTIONS))
def test_direct_stream_sends_each_event_once_and_agrees_with_the_answer(client, name):
    question, behaviour = QUESTIONS[name]
    response = client.post("/ask", json={"question": question}, headers={**client.as_(WHO), **NDJSON})
    answer = checked(lines(response.text), behaviour)
    whole = client.post("/ask", json={"question": question}, headers=client.as_(WHO)).json()
    timed = ("run_trace", "path")
    assert {k: v for k, v in answer.items() if k not in timed} == {k: v for k, v in whole.items() if k not in timed}


@pytest.mark.parametrize("wait", ["5", "0"])
@pytest.mark.parametrize("name", list(QUESTIONS))
def test_background_run_sends_each_event_once_across_its_passes(client, monkeypatch, name, wait):
    monkeypatch.setenv("QUESTION_START_WAIT", wait)
    question, behaviour = QUESTIONS[name]
    events = background(client, question, client.as_(WHO))
    answer = checked(events, behaviour)
    if name == "by_month" and wait == "0":
        assert answer["run_trace"]["warehouse_jobs"] and len(answer["sql_run"]) == 2
    replay = lines(client.get(f"/ask/runs/{events[0]['id']}", headers={**client.as_(WHO), **NDJSON}).text)
    assert [e for e in replay[1:] if e["event"] != "answer"] == [e for e in events[1:] if e["event"] not in ("answer", "run")]


def test_a_compact_background_run_sends_its_context_once_across_its_passes(client, monkeypatch):
    monkeypatch.setenv("QUESTION_START_WAIT", "0")
    headers = {**client.as_(WHO), **NDJSON}
    started = lines(client.post("/ask?background=true", json={"question": QUESTIONS["by_month"][0], "compact": True}, headers=headers).text)
    events, cursor = started, max(e.get("cursor", 0) for e in started)
    for _ in range(400):
        if any(e["event"] == "answer" for e in events):
            break
        time.sleep(0.02)
        polled = lines(client.get(f"/ask/runs/{started[0]['id']}?after={cursor}", headers=headers).text)
        events += polled[1:]
        cursor = max([e["cursor"] for e in polled if "cursor" in e], default=cursor)
    assert events[-1]["event"] == "answer" and events[-1]["answer"]["behaviour"] == "figure"
    assert [e["event"] for e in events].count("context") == 1
    assert not any(e["event"] in ("plan", "stage", "node_added", "edge_added", "edge_changed", "state_changed") for e in events)


def test_a_refusal_for_a_narrow_reader_streams_once_on_both_paths(client):
    who = "analyst_retail.meridian@example.com"
    question = QUESTIONS["by_month"][0]
    checked(lines(client.post("/ask", json={"question": question}, headers={**client.as_(who), **NDJSON}).text), "refuse")
    checked(background(client, question, client.as_(who)), "refuse")


def test_a_held_job_streams_its_stages_and_running_step_before_it_ends(client, monkeypatch):
    from kge import app as appmod
    from kge.adapters.warehouse import localjobs

    released = threading.Event()
    submit = localjobs.submit

    def held(job_id, compute):
        def wait(register_cancel):
            assert released.wait(30)
            return compute(register_cancel)
        return submit(job_id, wait)

    monkeypatch.setattr(localjobs, "submit", held)
    monkeypatch.setenv("QUESTION_START_WAIT", "20")
    seen, at_release = [], {}
    payload = json.dumps({"question": QUESTIONS["figure"][0]}).encode()
    headers = [(b"content-type", b"application/json"), (b"accept", b"application/x-ndjson"),
               (b"content-length", str(len(payload)).encode()), (b"host", b"testserver")]
    headers += [(k.encode(), v.encode()) for k, v in client.as_(WHO).items()]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/ask", "raw_path": b"/ask", "query_string": b"background=true", "root_path": "",
             "headers": headers, "client": ("testclient", 50000), "server": ("testserver", 80), "state": {}}
    sent = {"body": False}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await anyio.sleep_forever()

    async def send(message):
        if message["type"] == "http.response.start":
            seen.append(("start", message["status"]))
        for line in message.get("body", b"").decode().splitlines():
            if line.strip():
                seen.append(("event", json.loads(line)))
        events = [e for kind, e in seen if kind == "event"]
        running = any(e["event"] in ("node_added", "state_changed") and e["node"]["type"] == "Runtime"
                      and e["node"]["state"] == "running" for e in events)
        if not released.is_set() and running and any(e["event"] == "stage" and e["stage"] == "execute" for e in events):
            at_release["events"] = list(events)
            released.set()

    async def run():
        with anyio.fail_after(60):
            await appmod.app(scope, receive, send)

    try:
        client.portal.call(run)
    finally:
        released.set()
    assert seen[0] == ("start", 202)
    before = at_release["events"]
    assert before[0]["event"] == "run" and before[0]["state"] == "running"
    assert [e["stage"] for e in before if e["event"] == "stage" and e["state"] == "running"][:2] == ["understand", "access"]
    assert any(e["event"] == "stage" and e["stage"] == "plan" and e["state"] == "done" for e in before)
    assert not any(e["event"] == "answer" for e in before)
    events = [e for kind, e in seen if kind == "event"]
    answer = checked(events, "figure")
    assert answer["figure"] is not None


def test_a_reader_leaving_the_start_reply_ends_its_wait_and_frees_the_run(client, monkeypatch):
    from kge import app as appmod
    from kge.adapters.questionstate import for_profile
    from kge.adapters.warehouse import localjobs

    released, started = threading.Event(), {}
    submit = localjobs.submit

    def held(job_id, compute):
        def wait(register_cancel):
            assert released.wait(30)
            return compute(register_cancel)
        return submit(job_id, wait)

    monkeypatch.setattr(localjobs, "submit", held)
    monkeypatch.setenv("QUESTION_START_WAIT", "50")
    payload = json.dumps({"question": QUESTIONS["figure"][0]}).encode()
    headers = [(b"content-type", b"application/json"), (b"accept", b"application/x-ndjson"),
               (b"content-length", str(len(payload)).encode()), (b"host", b"testserver")]
    headers += [(k.encode(), v.encode()) for k, v in client.as_(WHO).items()]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/ask", "raw_path": b"/ask", "query_string": b"background=true", "root_path": "",
             "headers": headers, "client": ("testclient", 50000), "server": ("testserver", 80), "state": {}}
    sent = {"body": False}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": payload, "more_body": False}
        while "id" not in started:
            await anyio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message):
        for line in message.get("body", b"").decode().splitlines():
            event = json.loads(line)
            if event["event"] == "run":
                started["id"] = event["id"]

    async def run():
        with anyio.fail_after(30):
            await appmod.app(scope, receive, send)

    began = time.monotonic()
    try:
        client.portal.call(run)
        state = for_profile(appmod.PROFILE)
        for _ in range(200):
            record, _ = state.get(started["id"])
            if record["lease"] == 0:
                break
            time.sleep(0.05)
        assert record["lease"] == 0 and record["state"] == "running" and not released.is_set()
        assert time.monotonic() - began < 20
    finally:
        released.set()
    answer = checked(background_follow(client, started["id"], client.as_(WHO)), "figure")
    assert answer["figure"] is not None


def background_follow(client, run_id, headers):
    """A run's events from its start, by live polls, until its answer."""
    events, cursor = [], 0
    for _ in range(400):
        polled = lines(client.get(f"/ask/runs/{run_id}?after={cursor}", headers={**headers, **NDJSON}).text)
        events += polled[1:]
        cursor = max([e["cursor"] for e in polled if "cursor" in e], default=cursor)
        if any(e["event"] in ("answer", "error") for e in polled):
            break
        time.sleep(0.05)
    return events


def test_a_reader_leaving_during_planning_keeps_the_engine_until_the_work_ends_through_an_engine_switch(client, monkeypatch):
    from kge import app as appmod
    from kge.adapters.questionstate import for_profile
    from kge.services.ask.engine.path import PathStreamRecorder

    planning, release, started = threading.Event(), threading.Event(), {}
    on_stage = PathStreamRecorder.on_stage

    def held(self, stage_name):
        if stage_name == "plan" and not release.is_set():
            planning.set()
            assert release.wait(30)
        return on_stage(self, stage_name)

    monkeypatch.setattr(PathStreamRecorder, "on_stage", held)
    payload = json.dumps({"question": QUESTIONS["figure"][0]}).encode()
    headers = [(b"content-type", b"application/json"), (b"accept", b"application/x-ndjson"),
               (b"content-length", str(len(payload)).encode()), (b"host", b"testserver")]
    headers += [(k.encode(), v.encode()) for k, v in client.as_(WHO).items()]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/ask", "raw_path": b"/ask", "query_string": b"background=true", "root_path": "",
             "headers": headers, "client": ("testclient", 50000), "server": ("testserver", 80), "state": {}}
    sent = {"body": False}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": payload, "more_body": False}
        while not planning.is_set():
            await anyio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message):
        for line in message.get("body", b"").decode().splitlines():
            event = json.loads(line)
            if event["event"] == "run":
                started["id"] = event["id"]

    async def run():
        with anyio.fail_after(60):
            await appmod.app(scope, receive, send)

    old = appmod.engine()
    finished = []
    request = threading.Thread(target=lambda: finished.append(client.portal.call(run)))
    try:
        request.start()
        assert planning.wait(30)
        time.sleep(0.3)
        assert request.is_alive() and appmod._references.get(id(old), 0) >= 1
        appmod.switch()
        assert appmod.engine() is not old and id(old) in appmod._retired
    finally:
        release.set()
        request.join(60)
    assert not request.is_alive() and finished == [None]
    assert id(old) not in appmod._retired and id(old) not in appmod._references
    record, _ = for_profile(appmod.PROFILE).get(started["id"])
    assert record["state"] in ("running", "done") and "error" not in record
    events = background_follow(client, started["id"], client.as_(WHO))
    assert checked(events, "figure")["figure"] is not None
