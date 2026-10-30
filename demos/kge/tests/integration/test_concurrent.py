"""Requests served at once: each gets its own answer.

    .venv/bin/python -m pytest tests/integration/test_concurrent.py -q

The API answers requests on several threads at once (FastAPI's threadpool),
all on one Engine. A DuckDB connection is not safe to share between threads:
one request could read another's result and give it as its own figure. Here the
same questions are asked one at a time and from 16 threads at once, and every
answer must be the same both ways: the rule presets, the reference agent's arms
on rules and on the mock, and /ask. The questions are chosen to read the
warehouses every way the planner, the gate's dry run, the schema arm's column
guess and the agent's catalogue do: a figure, a filter through a join, a
snapshot, a fiscal quarter, a sum and parts across units.

The graph is read and written from those threads too: a steward's decision
writes while other requests read. On a throwaway copy of the build, the graph's
own reads, the answers traced as the API traces them, and writes landing while
they run must give what they give one at a time, and leave the same graph. And
no graph query waits for another's read: each read runs on a connection of its
own from the one database, Ladybug's way to use it from many threads.

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import Engine
from kge.eval import sets
from kge.adapters.model import LLM
from tests.scratch import BUILD, profile_of, scratch_build

THREADS = 16
GOLDEN = {i["id"]: i for i in sets.items(BUILD / "golden.json")}
ASKED = [GOLDEN[g]["question"] for g in ("G001", "G004", "G012", "G014", "G017", "G022", "G026", "G027")]
GROUP = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
KEPT = ("behaviour", "figure", "parts", "plan", "figures", "cite", "citations", "reason", "answer_text", "phrased_by")
TERMS = ("Revenue", "Fee Income", "Active Customer", "Average Balance")
STEWARD = "steward_glossary"
NOTES = 8
SLOW_FOR = 1.0


def slow_read(g) -> str:
    """A read that takes SLOW_FOR seconds or more on the machine running the test: the narrower chain of three or four
    columns that does, as a fixed query is over before the test's quick calls start on a fast machine. Each column more
    multiplies the work by the number of columns, so the chain stops at four. A machine on which even four columns are
    quicker fails here, plainly, rather than testing the CPU's speed."""
    for width in (3, 4):
        cols = [f"c{i}" for i in range(width)]
        query = (f"MATCH {', '.join(f'({c}:Col)' for c in cols)} "
                 f"WHERE {' AND '.join(f'{a}.name < {b}.name' for a, b in zip(cols, cols[1:]))} RETURN count(*) AS n")
        started = time.perf_counter()
        g.q(query)
        took = time.perf_counter() - started
        if took >= SLOW_FOR:
            return query
    pytest.fail(f"no read here takes {SLOW_FOR} s: four columns took {took:.2f} s, so the test cannot tell a wait apart")


def jobs(eng) -> list[tuple[str, object]]:
    """Each question on each path, as a call that answers it. The agents are new, so their catalogues fill under load."""
    rules, mock = Agent(eng, llm=None), Agent(eng, llm=LLM("mock", "mock"))
    comb = Combined(eng, llm=None)
    out = []
    for q in ASKED:
        for arm in ("schema", "graph"):
            out.append((f"{arm}: {q}", lambda q=q, arm=arm: eng.answer(q, arm_name=arm)))
        for name, agent in (("rules", rules), ("mock", mock)):
            for arm in ("control", "treatment"):
                out.append((f"agent {arm} on {name}: {q}", lambda q=q, agent=agent, arm=arm: agent.ask(q, "analyst_enterprise", arm=arm)))
    out.append((f"ask: {GROUP}", lambda: comb.ask(GROUP, "analyst_retail")))
    return out


def answered(call, graph=None) -> str:
    """What an answer says, in a traced run as the API serves it, or the error it raised. With graph, the trace reads
    the graph's fingerprint, as the API's does."""
    try:
        a = runtrace.run("answer", call, graph=graph)
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}"
    return json.dumps({k: a.get(k) for k in KEPT}, sort_keys=True, default=str)


def said(call) -> str:
    """What a graph call returned, without the id of a steward's new tag (random), or the error it raised."""
    try:
        out = call()
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}"
    if isinstance(out, dict):
        out = {k: v for k, v in out.items() if k != "by"}
    return json.dumps(out, sort_keys=True, default=str)


def under_writes(eng) -> list[tuple[str, object]]:
    """The answers jobs() asks, traced with the graph's fingerprint as the API traces them; the graph's own reads
    through Graph.q, three times over; and writes among them: a steward asking to reject each likely-personal privacy
    tag (it waits for a second steward) and rejecting the candidate term, which no answer here reads, spread through
    the list, and notes written through Graph.q at its head, so they land at once."""
    g = eng.g
    reads = [("terms", lambda: sorted(g.terms())), ("principals", g.principals),
             ("join path", lambda: g.join_path("retail", "complaint", "branch")),
             ("retrieve", lambda: g.retrieve("fee income by quarter", "analyst_retail", "analysis"))]
    for t in TERMS:
        reads += [(f"{t} certified", lambda t=t: g.implementations(t)), (f"{t} defined by", lambda t=t: g.defining_docs(t)),
                  (f"{t} uncertified", lambda t=t: g.uncertified_mentions(t))]
    out = [(name, lambda call=call: answered(call, g)) for name, call in jobs(eng)]
    out += [(f"{name} #{k}", lambda call=call: said(call)) for k in range(3) for name, call in reads]
    decide = [r["tag"] for r in g.steward_queue() if r.get("assessment") == "likely_personal" or r["family"] == "candidate_term"]
    writes = [(f"steward rejects {t}", lambda t=t: said(lambda: g.steward_decide(t, STEWARD, "reject"))) for t in decide]
    for i, w in enumerate(writes):
        out.insert((i + 1) * len(out) // (len(writes) + 1), w)
    notes = [(f"note {i}", lambda i=i: said(lambda: g.q("CREATE (:Tag {id: $id, family: 'concurrency_note'})", {"id": f"note/{i}"})))
             for i in range(NOTES)]
    return notes + out


def left(eng) -> dict:
    """What the writes left: the write count, the steward queue and the notes."""
    g = eng.g
    return {"writes": g.writes, "queue": [(r["tag"], r.get("reject_requested_by")) for r in g.steward_queue()],
            "notes": g.q("MATCH (t:Tag {family: 'concurrency_note'}) RETURN count(*) AS n")[0]["n"]}


def test_answers_given_at_once_are_the_answers_given_alone(eng):
    alone = {name: answered(call) for name, call in jobs(eng)}
    assert not [n for n, a in alone.items() if a.startswith("raised")], "every question answers on its own"
    with ThreadPoolExecutor(THREADS) as pool:
        together = list(pool.map(lambda job: (job[0], answered(job[1])), jobs(eng)))
    differ = [(name, alone[name][:160], got[:160]) for name, got in together if got != alone[name]]
    assert differ == [], f"{len(differ)} of {len(together)} answers changed when asked at once: {differ[:3]}"


def test_graph_reads_and_writes_at_once_are_as_alone(tmp_path):
    """Each way runs on a fresh copy of the build, since the steward's decisions can be made only once."""
    runs = {}
    for how in ("alone", "together"):
        eng = Engine(profile_of(scratch_build(tmp_path / how)))
        try:
            todo = under_writes(eng)
            if how == "alone":
                got = [(name, job()) for name, job in todo]
            else:
                with ThreadPoolExecutor(THREADS) as pool:
                    got = list(pool.map(lambda job: (job[0], job[1]()), todo))
            runs[how] = (dict(got), left(eng))
        finally:
            eng.close()
    (alone, alone_left), (together, together_left) = runs["alone"], runs["together"]
    assert len(together) == len(alone) == len(todo), "every job has a name of its own"
    assert sum(n.startswith("steward") for n in alone) == 3, "two reject requests and a rejected candidate term"
    assert not [n for n, a in alone.items() if a.startswith("raised")], "every call answers on its own"
    differ = [(name, alone[name][:160], got[:160]) for name, got in together.items() if got != alone[name]]
    assert differ == [], f"{len(differ)} of {len(together)} calls changed when made at once: {differ[:3]}"
    assert together_left == alone_left, "the same writes landed, each counted once"
    assert alone_left["notes"] == NOTES


def test_a_query_that_may_write_runs_on_the_write_connection():
    """Routing errs towards writes: any Cypher that may change the graph or its state goes to the one write
    connection, and plain reads go to connections of their own."""
    from kge.services.ask.engine.core import WRITES
    may_write = ["MATCH (t:Tag) DETACH DELETE t", "MATCH (n:Doc) REMOVE n.tier", "DROP TABLE Tag",
                 "ALTER TABLE Doc ADD x STRING", "COPY Doc FROM 'x.csv'", "CHECKPOINT", "BEGIN TRANSACTION",
                 "COMMIT", "ROLLBACK", "INSTALL json", "LOAD json", "ATTACH 'x' AS y", "CREATE (n:Tag {id: 1})",
                 "MERGE (n:Tag {id: 1})", "MATCH (n:Tag) SET n.x = 1"]
    reads = ["MATCH (t:Term) RETURN t.name", "MATCH (c:Col)-[m:MAPS_TO]->(t:Term {name: $t}) RETURN c.name",
             "CALL show_tables() RETURN *"]
    assert [q for q in may_write if not WRITES.search(q)] == []
    assert [q for q in reads if WRITES.search(q)] == []


def test_no_graph_query_waits_for_a_slow_read(tmp_path):
    """Started while a slow read runs, a read, the run trace's fingerprint and a write are each done before it: reads
    run side by side on connections of their own, and a write does not queue behind a read. A write goes first, so
    the fingerprint reads the graph afresh."""
    eng = Engine(profile_of(scratch_build(tmp_path / "build")))
    g, done = eng.g, {}

    def timed(name, call):
        out = call()
        done[name] = time.perf_counter()
        return out

    try:
        g.q("CREATE (:Tag {id: 'note/first', family: 'concurrency_note'})")
        slow_q = slow_read(g)
        with ThreadPoolExecutor(4) as pool:
            slow = pool.submit(timed, "slow read", lambda: g.q(slow_q))
            time.sleep(0.3)
            quick = {name: pool.submit(timed, name, call) for name, call in (
                ("read", lambda: g.implementations("Revenue")),
                ("fingerprint", lambda: runtrace.fingerprint(g)),
                ("write", lambda: g.q("CREATE (:Tag {id: 'note/second', family: 'concurrency_note'})")))}
            assert slow.result()[0]["n"] > 0 and quick["read"].result() and quick["fingerprint"].result()["overlay"]
            quick["write"].result()
        late = [name for name in quick if done[name] > done["slow read"]]
        assert late == [], f"{late} waited for the slow read"
        assert g.q("MATCH (t:Tag {family: 'concurrency_note'}) RETURN count(*) AS n")[0]["n"] == 2
    finally:
        eng.close()
