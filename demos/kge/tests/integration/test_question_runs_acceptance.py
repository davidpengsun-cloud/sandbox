"""Real governed Ask resumes its checked planner on both warehouse paths."""
import time

import pytest

from kge.adapters.questionstate.store import StoreQuestionRuns
from kge.adapters.objstore import open_store
from kge.services.ask import service
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.models import Ask
from tests.bigquery_fake import pilot, budgets
from tests.scratch import open_engine, scratch_build
from tests.integration.test_answer_text import client as client


@pytest.mark.parametrize('backend', ['local', 'pilot'])
@pytest.mark.parametrize('question', ['Total retail revenue for June 2025?', 'What was certified NNA for June 2025?'])
def test_background_matches_sync_and_submits_each_gated_statement_once(monkeypatch, tmp_path, backend, question):
    fake = twin = None
    engines = []
    if backend == 'pilot':
        fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
        get = fake.get
        def read(url):
            data = get(url)
            if '/jobs/' in url:
                data.setdefault('status', {'state': 'DONE' if data.get('jobComplete') else 'RUNNING'})
            return data
        monkeypatch.setattr(fake, 'get', read)
        engine = opened()
    else:
        engine = open_engine(scratch_build(tmp_path / 'build'))
        engines.append(engine)
    try:
        combined = Combined(engine, llm=None)
        cfg = PRESETS['graph']
        expected = service.ask(combined, engine.profile, question, 'analyst_enterprise', 'analysis', 'graph', cfg)
        assert expected['behaviour'] == 'figure', expected
        if fake:
            fake.requests.clear()
            fake.pending_polls = 2
        state = StoreQuestionRuns(open_store(str(tmp_path / 'runs')))
        now = [time.time()]
        run = service.start_run(combined, engine.profile, Ask(question=question), 'analyst_enterprise', cfg,
                                state=state, clock=lambda: now[0])
        assert run['state'] == 'running', run
        for _ in range(100):
            now[0] += 3
            run = service.get_run(combined, engine.profile, run['id'], 'analyst_enterprise', state=state, clock=lambda: now[0])
            if run['state'] != 'running':
                break
            time.sleep(.001)
        assert run['state'] == 'done', run
        assert run['answer']['figure'] == expected['figure']
        assert run['answer']['answer_text'] == expected['answer_text']
        assert run['answer']['history'] == expected['history']
        assert run['answer']['run_trace']['warehouse_jobs']
        assert [s['stage'] for s in run['answer']['run_trace']['stages']] == [s['stage'] for s in expected['run_trace']['stages']]
        assert {'gate', 'execute'} <= {s['stage'] for s in run['answer']['run_trace']['stages']}
        assert any(e.get('event') == 'node_added' and e.get('node', {}).get('type') == 'Runtime' for e in run['events'])
        assert any(e.get('event') == 'plan' for e in run['events'])
        held = state.get(run['id'])[0]
        assert held['plan'] and all('result' in job for job in held['jobs'])
        if fake:
            counts = budgets(fake)
            assert counts['warehouse'] == counts['dry'] == len(held['jobs'])
            assert counts['catalogue'] == 0
            assert counts['graph'] == counts['retrieval'] == 0
        assert service.get_run(combined, engine.profile, run['id'], 'analyst_retail', state=state) is None
    finally:
        for engine in engines:
            engine.close()
        if twin and twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        if fake:
            fake.con.close()


def test_background_access_refusal_reads_no_warehouse(monkeypatch, tmp_path):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        state = StoreQuestionRuns(open_store(str(tmp_path / 'runs')))
        fake.requests.clear()
        run = service.start_run(Combined(engine, llm=None), engine.profile,
                Ask(question='Total wealth revenue for June 2025?', products=['wealth.client_holding']),
                'analyst_retail', PRESETS['graph'], state=state)
        run = service.get_run(Combined(engine, llm=None), engine.profile, run['id'], 'analyst_retail', state=state)
        assert run['state'] == 'done' and run['answer']['behaviour'] == 'refuse'
        assert not state.get(run['id'])[0]['jobs']
        assert budgets(fake)['warehouse'] == budgets(fake)['dry'] == 0
        assert 'client_holding' not in str(run['events'])
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_api_start_poll_reconnect_and_existing_sync_contract(client, monkeypatch):
    import json
    monkeypatch.setenv("QUESTION_POLL_SECONDS", ".001")
    monkeypatch.setenv("QUESTION_START_WAIT", "0")
    who = "analyst_enterprise.meridian@example.com"
    question = "Total retail revenue for June 2025?"
    headers = client.as_(who)
    started = client.post("/ask?background=true&display=1", json={"question": question}, headers=headers)
    assert started.status_code == 202, started.text
    run = started.json()
    assert run["state"] == "running" and len(run["id"]) == 32
    path = "/ask/runs/" + run["id"]
    assert client.get(path).status_code == 401
    assert client.get(path, headers=client.as_("analyst_retail.meridian@example.com")).status_code == 404
    for _ in range(100):
        polled = client.get(path, headers=headers)
        assert polled.status_code in (200, 202), polled.text
        run = polled.json()
        if run["state"] != "running":
            break
        time.sleep(.001)
    assert run["state"] == "done", run
    assert run["answer"]["figure"] == 13287.67
    assert run["answer"]["display"]
    replay = client.get(path + "?after=" + str(run["cursor"]), headers={**headers, "accept": "application/x-ndjson"})
    events = [json.loads(line) for line in replay.text.splitlines()]
    assert events[0]["event"] == "run" and events[0]["id"] == run["id"]
    assert events[-1]["event"] == "answer" and events[-1]["answer"] == run["answer"]
    sync = client.post("/ask", json={"question": question}, headers=headers)
    assert sync.status_code == 200 and sync.json()["figure"] == run["answer"]["figure"]
    assert "id" not in sync.json() and "state" not in sync.json()
    assert client.post("/ask?background=true", json={"question": question, "principal": who}, headers=headers).status_code == 422


def test_a_short_background_question_answers_in_its_first_response(client, monkeypatch):
    import json
    monkeypatch.setenv("QUESTION_POLL_SECONDS", "30")
    monkeypatch.setenv("QUESTION_START_WAIT", "20")
    headers = client.as_("analyst_enterprise.meridian@example.com")
    question = "Total retail revenue for June 2025?"
    sync = client.post("/ask", json={"question": question}, headers=headers).json()
    began = time.monotonic()
    started = client.post("/ask?background=true", json={"question": question}, headers=headers)
    assert time.monotonic() - began < 10
    run = started.json()
    assert started.status_code == 202 and run["state"] == "done" and len(run["id"]) == 32
    assert run["answer"]["figure"] == sync["figure"] == 13287.67
    assert [s["stage"] for s in run["answer"]["run_trace"]["stages"]] == [s["stage"] for s in sync["run_trace"]["stages"]]
    streamed = client.post("/ask?background=true", json={"question": question},
                           headers={**headers, "accept": "application/x-ndjson"})
    events = [json.loads(line) for line in streamed.text.splitlines()]
    assert events[0]["event"] == "run" and events[0]["state"] == "running" and events[0]["cursor"] == 0
    assert events[-1]["event"] == "answer" and events[-1]["answer"]["figure"] == sync["figure"]


@pytest.mark.parametrize("limit", [".1", ".3"])
def test_an_over_limit_question_says_the_same_through_the_api(client, monkeypatch, limit):
    from kge.pipeline.questionrun import TIME_LIMIT
    monkeypatch.setenv("QUESTION_RUN_TIMEOUT", limit)
    monkeypatch.setenv("QUESTION_POLL_SECONDS", ".001")
    headers = client.as_("analyst_enterprise.meridian@example.com")
    started = client.post("/ask?background=true", json={"question": "certified vs current NNA for Q1 2025 by month"}, headers=headers)
    assert started.status_code == 202, started.text
    run = started.json()
    seen = [(run["state"], run.get("error"))]
    time.sleep(float(limit) + .1)
    for _ in range(2):
        polled = client.get("/ask/runs/" + run["id"], headers=headers)
        assert polled.status_code == 200, polled.text
        seen.append((polled.json()["state"], polled.json().get("error")))
        assert "answer" not in polled.json()
    assert all(outcome in (("running", None), ("expired", TIME_LIMIT)) for outcome in seen[:1]), seen
    assert seen[1:] == [("expired", TIME_LIMIT)] * 2, seen
